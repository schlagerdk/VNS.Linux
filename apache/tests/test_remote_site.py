import ast
import base64
import importlib.util
import io
import json
import os
import re
import shlex
import sys
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
database_spec = importlib.util.spec_from_file_location("database_info", APACHE / "lib/database_info.py")
database_info = importlib.util.module_from_spec(database_spec)
database_spec.loader.exec_module(database_info)


class RemoteFilesTests(unittest.TestCase):
    def test_editability_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "folder").mkdir()
            (root / "nested/deeper").mkdir(parents=True)
            (root / "nested/deeper/config.json").write_text("{}")
            (root / "binaries").mkdir()
            (root / "binaries/app.dll").write_bytes(b"compiled")
            (root / "folder/link").symlink_to(root / "nested", target_is_directory=True)
            (root / "app.dll").write_bytes(b"compiled")
            (root / "settings.json").write_text('{"enabled":true}')
            (root / "binary").write_bytes(b"\0binary")
            (root / "large.log").write_bytes(b"x" * (2 * 1024 * 1024 + 1))
            items = {item["name"]: item for item in remote_files.handle(root, {"action": "list"})["items"]}
            self.assertTrue(items["settings.json"]["editable"])
            for name in ("app.dll", "binary", "large.log", "folder"):
                self.assertFalse(items[name]["editable"])
            self.assertTrue(items["nested"]["has_editable_files"])
            self.assertFalse(items["folder"]["has_editable_files"])
            self.assertFalse(items["binaries"]["has_editable_files"])

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
            app=self.app, os=os, json=json, subprocess=subprocess, time=time, shlex=shlex,
            _database_module=database_info,
            re=re, SERVICE_DIR="/etc/systemd/system", APACHE_SITES="/etc/apache2/sites-available",
            read_text=lambda path: "",
            base64=base64, abort=abort, jsonify=jsonify, request=request,
            io=io, send_file=send_file, sys=sys, site_units=lambda site: [site, "local-prod.test"],
            Path=Path, editable_file=remote_files.editable_file,
            contains_editable_files=remote_files.contains_editable_files,
            safe_path=lambda path: path, rel=lambda path: path,
            HTTPException=HTTPException, APACHE_DIR=str(APACHE), WEB_ROOT="/var/www",
            REMOTE_STATUS={}, site_dirs=lambda: ["example.test"],
            parse_prod=lambda root: {"PROD_LOCAL_PATH": "", "PROD_FOLDER": "production.test"},
            site_arg=lambda data: "example.test",
        )
        functions = {"error", "remote_command", "remote_status", "remote_site", "api_service", "api_logs", "build_command", "remote_fs",
                     "service_environment", "server_aliases", "environment_info", "managed_config", "fs_list",
                     "server_files", "fs_path", "fs_relative"}
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

    def test_server_aliases_parsing_is_case_insensitive_and_skips_binding_comments(self):
        aliases = self.ns["server_aliases"](
            "ServerName main.example.test\n"
            "ServerAlias WWW.EXAMPLE.TEST api.example.test www.example.test # ignored.example.test\n"
            "ServerAlias api.example.test cdn.example.test\n",
            "main.example.test")
        self.assertEqual(aliases, ["WWW.EXAMPLE.TEST", "api.example.test", "cdn.example.test"])

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

    def test_local_production_badge_uses_effective_environment(self):
        self.ns["unit_of"] = lambda unit: True
        self.ns["https_status"] = lambda unit: {"https": True, "scheme": "https"}
        with patch("subprocess.run", return_value=Mock(
                stdout='"ASPNETCORE_ENVIRONMENT=Production" "DOTNET_ENVIRONMENT=Staging"')) as run:
            info = self.ns["environment_info"](
                "prod", "Production", "prod.test", "prod.test", "", "prod.test",
                {"prod.test": "active"}, mode="local", environment="Production")
        self.assertEqual(info["environment"], "Staging")
        self.assertEqual(run.call_args.args[0],
                         ["systemctl", "show", "prod.test.service", "--property=Environment", "--value"])

    def test_config_shortcuts_use_service_publish_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            publish = root / "example.test/_custom"
            publish.mkdir(parents=True)
            (publish / "appsettings.json").write_text("{}")
            (publish / "appsettings.Test.json").write_text(
                '{"ConnectionStrings":{"umbracoDbDSN":"Data Source=app.db","umbracoDbDSN_ProviderName":"Microsoft.Data.Sqlite"}}')
            self.ns.update(WEB_ROOT=str(root), unit_of=lambda unit: True,
                           service_environment=lambda unit: "Test",
                           https_status=lambda unit: {},
                           read_text=lambda path: 'WorkingDirectory="%s"' % publish)
            with patch("subprocess.run", return_value=Mock(stdout="")):
                info = self.ns["environment_info"]("main", "Test", "example.test",
                                                  "example.test", "", "example.test", {})
            self.assertEqual(info["publish_path"], str(publish))
            self.assertEqual(info["editable_configs"], ["appsettings.json"])
            self.assertEqual(info["database"], "SQLite")
            self.assertEqual(info["database_name"], str(publish / "app.db"))

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

    def test_config_api_scopes_local_and_remote(self):
        command = Mock(return_value=Mock(returncode=0, stdout='{"content":"remote config","path":"/etc/test"}'))
        self.ns["remote_command"] = command
        response = self.client.get("/api/sites/example.test/config/conf?scope=remote&unit=example.test")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["content"], "remote config")
        self.assertEqual(command.call_args.args, ("example.test", "config"))
        response = self.client.get("/api/sites/example.test/config/service?scope=remote&encoding=cp850")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(command.call_args.kwargs["input"])["encoding"], "cp850")
        with patch("subprocess.run", return_value=Mock(returncode=0, stdout='{"encoding_required":true,"path":"/etc/test"}')) as run:
            response = self.client.get("/api/sites/example.test/config/service?encoding=cp1252")
        self.assertTrue(response.json["encoding_required"])
        self.assertEqual(json.loads(run.call_args.kwargs["input"])["encoding"], "cp1252")
        with patch("subprocess.run", return_value=Mock(returncode=0, stdout='{"ok":true}')) as run:
            response = self.client.post("/api/sites/example.test/config/service?unit=local-prod.test",
                                        json={"action": "save", "content": "[Service]\n"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(run.call_args.kwargs["env"]["SITE"], "local-prod.test")
        self.assertEqual(json.loads(run.call_args.kwargs["input"])["content"], "[Service]\n")
        with patch("subprocess.run") as run:
            self.assertEqual(self.client.get("/api/sites/example.test/config/service?unit=other.test").status_code, 400)
            self.assertEqual(self.client.get("/api/sites/example.test/config/other").status_code, 400)
            run.assert_not_called()

    def test_local_file_editability_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.dll").write_bytes(b"compiled")
            (root / "config.json").write_text("{}")
            (root / "empty").mkdir()
            (root / "nested/deeper").mkdir(parents=True)
            (root / "nested/deeper/config.json").write_text("{}")
            response = self.client.get("/api/fs/list", query_string={"path": directory})
            self.assertEqual(response.status_code, 200)
            items = {item["name"]: item for item in response.json["items"]}
            self.assertFalse(items["app.dll"]["editable"])
            self.assertTrue(items["config.json"]["editable"])
            self.assertFalse(items["empty"]["has_editable_files"])
            self.assertTrue(items["nested"]["has_editable_files"])


class RemoteStatusTests(unittest.TestCase):
    def test_https_is_read_from_remote_configuration(self):
        helper = (APACHE / "lib/database_info.py").read_text().replace("from pathlib import Path", "")
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
            conf.write_text('<VirtualHost *:443>\nServerName production.test\nServerAlias www.production.test api.production.test\nServerAlias WWW.PRODUCTION.TEST # ignored.test\nSSLCertificateFile "' + str(certificate) + '"\n</VirtualHost>')
            def remote_path(*parts):
                return {"/etc/systemd/system": root / "service", "/etc/apache2/sites-available": conf,
                        "/var/www": root / "site"}.get(parts[0], Path(*parts))
            for has_certificate in (True, False):
                if not has_certificate:
                    certificate.unlink()
                output = io.StringIO()
                with patch.dict(os.environ, SITE="production.test", DATABASE_CODE=helper), redirect_stdout(output), \
                        patch("subprocess.run", side_effect=[
                            Mock(returncode=0, stdout="active\n"),
                            Mock(stdout='"ASPNETCORE_ENVIRONMENT=Production" "DOTNET_ENVIRONMENT=Production"\n')]):
                    exec(compile(tree, "remote status", "exec"), {"Path": remote_path})
                status = json.loads(output.getvalue())
                self.assertEqual(status["https"], has_certificate)
                self.assertEqual(status["binding"], "production.test")
                self.assertEqual(status["aliases"], ["www.production.test", "api.production.test"])
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
                    with patch.dict(os.environ, SITE="production.test", DATABASE_CODE=helper), redirect_stdout(output), \
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
            for name in ("prod.sh", "remote_files.py", "managed_config.py", "database_info.py"):
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
                           "edit_service", "edit_conf", "shell", "files", "config"):
                result = subprocess.run(["/bin/bash", str(root / "remote_site.sh"), "example.test", action],
                                        input="{}", capture_output=True, text=True, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                args = (root / "args").read_text()
                self.assertIn("operator@remote.test", args)
                self.assertIn("2222", args)
                self.assertIn("sudo -n", args)
                self.assertEqual("-tt\n" in args, action in ("shell", "edit_service", "edit_conf"))
                self.assertIn("SITE=production.test", args)
                if action == "check_service":
                    self.assertIn("TERM=xterm-256color SYSTEMD_COLORS=1 systemctl status",
                                  (root / "input").read_text())
                if action in ("shell", "edit_service", "edit_conf"):
                    self.assertIn("TERM=xterm-256color", args)
                    self.assertIn("COLORTERM=truecolor", args)
                if action == "shell":
                    self.assertIn("force_color_prompt=yes", args)
                    self.assertIn("bash\\ -il", args)
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
