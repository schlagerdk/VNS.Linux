import ast
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from flask import Flask, abort, jsonify, request, send_file
from werkzeug.exceptions import HTTPException


APACHE = Path(__file__).resolve().parents[1]
APP_SOURCE = APACHE / "solutionfiles/admin/app/app.py"
spec = importlib.util.spec_from_file_location("remote_files", APACHE / "lib/remote_files.py")
remote_files = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remote_files)


class RemoteFilesTests(unittest.TestCase):
    def test_file_operations_and_confinement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            handle = lambda **data: remote_files.handle(root, data)
            handle(action="mkdir", name="content")
            handle(action="newfile", path="content", name="test.txt")
            handle(action="save", path="content/test.txt", content="hello")
            self.assertEqual(handle(action="read", path="content/test.txt")["content"], "hello")
            handle(action="rename", path="content/test.txt", name="renamed.txt")
            self.assertEqual(handle(action="list", path="content")["items"][0]["name"], "renamed.txt")
            handle(action="upload", path="content", files=[{"name": "binary", "content": "AAEC"}])
            self.assertEqual(handle(action="download", path="content/binary")["content"], "AAEC")
            with self.assertRaises(ValueError):
                handle(action="read", path="content/binary")
            for action in ("read", "save", "list", "delete"):
                with self.assertRaises(ValueError):
                    handle(action=action, path="../outside", content="bad")
            for action in ("delete", "rename"):
                with self.assertRaises(ValueError):
                    handle(action=action, path="", name="root")
            (root / "escape").symlink_to(root.parent, target_is_directory=True)
            with self.assertRaises(ValueError):
                handle(action="save", path="escape/outside", content="bad")
            with self.assertRaises(ValueError):
                handle(action="upload", files=[{"name": "escape", "content": "AAEC"}])
            handle(action="delete", path="content")
            self.assertFalse((root / "content").exists())


class RemoteApiTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["TESTING"] = True
        self.ns = dict(
            app=self.app, os=os, json=json, subprocess=subprocess, time=time,
            base64=base64, abort=abort, jsonify=jsonify, request=request,
            io=io, send_file=send_file,
            HTTPException=HTTPException, APACHE_DIR=str(APACHE), WEB_ROOT="/var/www",
            REMOTE_STATUS={}, site_dirs=lambda: ["example.test"],
            parse_prod=lambda root: {"PROD_LOCAL_PATH": "", "PROD_FOLDER": "production.test"},
            site_arg=lambda data: "example.test",
        )
        functions = {"error", "remote_command", "remote_status", "remote_site", "api_service", "api_logs", "build_command", "remote_fs"}
        nodes = [node for node in ast.parse(APP_SOURCE.read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name in functions]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(APP_SOURCE), "exec"), self.ns)
        self.client = self.app.test_client()

    def test_service_and_logs_use_remote_target(self):
        command = Mock(return_value=Mock(stdout="remote journal"))
        self.ns["remote_command"] = command
        response = self.client.post("/api/sites/example.test/service",
                                    json={"scope": "prod", "action": "stop", "unit": "local.test"})
        self.assertEqual(response.status_code, 200)
        command.assert_called_with("example.test", "stop", text=True)
        response = self.client.get("/api/sites/example.test/logs?scope=prod")
        self.assertEqual(response.json["text"], "remote journal")
        command.assert_called_with("example.test", "logs", text=True)
        self.assertEqual(self.client.post("/api/sites/example.test/service",
                         json={"scope": "prod", "action": "delete"}).status_code, 400)

    def test_unknown_or_local_target_rejected(self):
        self.assertEqual(self.client.get("/api/sites/unknown.test/logs?scope=prod").status_code, 400)
        self.ns["parse_prod"] = lambda root: {"PROD_LOCAL_PATH": "/var/www/local"}
        self.assertEqual(self.client.get("/api/sites/example.test/logs?scope=prod").status_code, 400)

    def test_status_uses_remote_metadata_and_cache(self):
        status = {"unit": "production.test", "state": "active", "scheme": "https", "https": True, "exists": True}
        command = Mock(return_value=Mock(stdout=json.dumps(status)))
        self.ns["remote_command"] = command
        self.assertEqual(self.ns["remote_status"]("example.test"), status)
        self.ns["remote_status"]("example.test")
        self.assertEqual(command.call_count, 1)

    def test_unreachable_status_is_unknown_not_insecure(self):
        self.ns["remote_command"] = Mock(side_effect=HTTPException("SSH unavailable"))
        status = self.ns["remote_status"]("example.test")
        self.assertIsNone(status["https"])
        self.assertIn("remote_error", status)

    def test_remote_tools_are_allowlisted(self):
        for tool in ("shell", "check_service", "edit_service", "edit_conf"):
            _, argv, _ = self.ns["build_command"]({"kind": "remote_tool", "tool": tool})
            self.assertEqual(argv, ["/bin/bash", str(APACHE / "remote_site.sh"), "example.test", tool])
        with self.assertRaises(HTTPException):
            self.ns["build_command"]({"kind": "remote_tool", "tool": "anything"})

    def test_command_failures_are_reported(self):
        with patch("subprocess.run", return_value=Mock(returncode=1, stderr="SSH failed", stdout="")):
            with self.assertRaises(HTTPException) as error:
                self.ns["remote_command"]("example.test", "status")
            self.assertEqual(error.exception.code, 502)

    def test_remote_file_routes_and_binary_transfer(self):
        command = Mock(return_value=Mock(stdout=b'{"ok": true}'))
        self.ns["remote_command"] = command
        response = self.client.post("/api/remote-fs/example.test/save", json={"path": "content.txt", "content": "hello"})
        self.assertEqual(response.status_code, 200)
        payload = json.loads(command.call_args.kwargs["input"])
        self.assertEqual(payload, {"action": "save", "path": "content.txt", "content": "hello"})
        self.assertEqual(self.client.get("/api/remote-fs/example.test/delete").status_code, 405)
        self.assertEqual(self.client.post("/api/remote-fs/example.test/save", json=["bad"]).status_code, 400)
        response = self.client.post("/api/remote-fs/example.test/upload",
                                    data={"path": "", "files": (io.BytesIO(b"\0\1\2"), "binary")})
        self.assertEqual(response.status_code, 200)
        payload = json.loads(command.call_args.kwargs["input"])
        self.assertEqual(payload["files"], [{"name": "binary", "content": "AAEC"}])
        command.return_value.stdout = b'{"content": "AAEC"}'
        response = self.client.get("/api/remote-fs/example.test/download?path=binary")
        self.assertEqual(response.data, b"\0\1\2")


class RemoteStatusTests(unittest.TestCase):
    def test_https_is_read_from_remote_configuration(self):
        source = (APACHE / "remote_site.sh").read_text().split("python3 - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        tree = ast.parse(source)
        tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "site").mkdir()
            (root / "service").write_text("Environment=ASPNETCORE_ENVIRONMENT=Production\nExecStart=dotnet --urls http://localhost:4100")
            certificate = root / "certificate"
            certificate.touch()
            conf = root / "conf"
            conf.write_text('<VirtualHost *:443>\nServerName production.test\nSSLCertificateFile "' + str(certificate) + '"\n</VirtualHost>')
            def remote_path(*parts):
                return {"/etc/systemd/system": root / "service", "/etc/apache2/sites-available": conf,
                        "/var/www": root / "site"}.get(parts[0], Path(*parts))
            for has_certificate in (True, False):
                if not has_certificate:
                    certificate.unlink()
                output = io.StringIO()
                with patch.dict(os.environ, SITE="production.test"), redirect_stdout(output), \
                        patch("subprocess.run", side_effect=[
                            Mock(returncode=0, stdout="active\n"),
                            Mock(stdout='"ASPNETCORE_ENVIRONMENT=Production" "DOTNET_ENVIRONMENT=Production"\n')]):
                    exec(compile(tree, "remote status", "exec"), {"Path": remote_path})
                status = json.loads(output.getvalue())
                self.assertEqual(status["https"], has_certificate)
                self.assertEqual(status["binding"], "production.test")
                self.assertEqual(status["environment"], "Production")
                self.assertEqual(status["port"], "4100")
                self.assertEqual(status["state"], "active")

            for expected, variables in (
                ("Development", '"ASPNETCORE_ENVIRONMENT=Development"'),
                ("Test", 'ASPNETCORE_ENVIRONMENT=Test'),
                ("Staging", '"ASPNETCORE_ENVIRONMENT=Production" "DOTNET_ENVIRONMENT=Staging"'),
                ("Production", 'DOTNET_ENVIRONMENT=Production'),
            ):
                with self.subTest(environment=expected):
                    output = io.StringIO()
                    with patch.dict(os.environ, SITE="production.test"), redirect_stdout(output), \
                            patch("subprocess.run", side_effect=[
                                Mock(returncode=0, stdout="active\n"), Mock(stdout=variables)]) as run:
                        exec(compile(tree, "remote status", "exec"), {"Path": remote_path})
                    self.assertEqual(json.loads(output.getvalue())["environment"], expected)
                    self.assertEqual(run.call_args.args[0],
                                     ["systemctl", "show", "production.test.service", "--property=Environment", "--value"])


class RemoteTransportTests(unittest.TestCase):
    def test_transport_actions_and_interactive_tty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "lib").mkdir()
            (root / "bin").mkdir()
            (root / "sites/example.test/scripts").mkdir(parents=True)
            (root / "remote_site.sh").write_text((APACHE / "remote_site.sh").read_text())
            (root / "lib/common.sh").write_text((APACHE / "lib/common.sh").read_text()
                                                   + '\n_rootPath="' + str(root / "sites") + '"\n')
            for name in ("prod.sh", "remote_files.py"):
                (root / "lib" / name).write_text((APACHE / "lib" / name).read_text())
            # macOS Bash cannot source a process substitution; test transport with trusted fixture settings.
            with (root / "lib/prod.sh").open("a") as source:
                source.write('\nload_prod_settings() { PROD_LOCAL_PATH=""; source "${_siteRoot}/scripts/publish_prod.sh"; }\n')
            (root / "sites/example.test/scripts/publish_prod.sh").write_text(
                "PROD_FOLDER=production.test\nPROD_BINDING=production.test\n"
                "PROD_IP=remote.test\nPROD_USER=operator\nPROD_PASSWORD=''\nPROD_SSH_PORT=2222\n")
            ssh = root / "bin/ssh"
            ssh.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$TEST_ARGS"\ncat > "$TEST_INPUT"\n')
            ssh.chmod(0o700)
            env = dict(os.environ, PATH=str(root / "bin") + ":" + os.environ["PATH"],
                       TEST_ARGS=str(root / "args"), TEST_INPUT=str(root / "input"))
            for action in ("status", "start", "stop", "restart", "logs", "check_service",
                           "edit_service", "edit_conf", "shell", "files"):
                result = subprocess.run(["/bin/bash", str(root / "remote_site.sh"), "example.test", action],
                                        input="{}", capture_output=True, text=True, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                args = (root / "args").read_text()
                self.assertIn("operator@remote.test", args)
                self.assertIn("2222", args)
                self.assertIn("sudo -n", args)
                self.assertEqual("-tt\n" in args, action in ("shell", "edit_service", "edit_conf"))
                self.assertIn("SITE=production.test", args)
            ssh.write_text("#!/bin/bash\nexit 7\n")
            result = subprocess.run(["/bin/bash", str(root / "remote_site.sh"), "example.test", "status"],
                                    capture_output=True, env=env)
            self.assertEqual(result.returncode, 7)
            (root / "delete_site.sh").write_text((APACHE / "delete_site.sh").read_text())
            for name in ("sudo", "clear"):
                mock = root / "bin" / name
                mock.write_text('#!/bin/bash\nprintf "%s\\n" "$@" >> "$TEST_LOCAL_ACTIONS"\n')
                mock.chmod(0o700)
            env["TEST_LOCAL_ACTIONS"] = str(root / "local-actions")
            for scope in ("prod", "all"):
                result = subprocess.run(["/bin/bash", str(root / "delete_site.sh"), "example.test", "n", scope],
                                        capture_output=True, text=True, env=env)
                self.assertEqual(result.returncode, 7, result.stderr)
                self.assertTrue((root / "sites/example.test/scripts/publish_prod.sh").exists())
                self.assertEqual((root / "local-actions").read_text(), "\n" * (1 if scope == "prod" else 2))
                self.assertIn("local files and production settings have been kept", result.stderr)


if __name__ == "__main__":
    unittest.main()
