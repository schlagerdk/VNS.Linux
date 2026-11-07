import ast
import hmac
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, redirect, request, send_file, session
from werkzeug.exceptions import BadRequest

from apache.lib.remote_files import contains_editable_files, editable_file


APP = Path(__file__).resolve().parents[1] / "solutionfiles/admin/app"


class ServerFilesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.web = self.root / "www"
        self.web.mkdir()
        self.outside = self.root / "etc"
        self.outside.mkdir()
        self.file = self.outside / "example.conf"
        self.file.write_text("original\n")
        self.file.chmod(0o640)
        self.app = Flask(__name__)
        self.app.secret_key = "test-only"
        self.app.config["TESTING"] = True
        self.ns = dict(app=self.app, os=os, Path=Path, io=io, shutil=shutil, re=re,
                       abort=abort, jsonify=jsonify, request=request, send_file=send_file,
                       session=session, hmac=hmac, urlparse=urlparse, redirect=redirect,
                       WEB_ROOT=str(self.web), MAX_EDIT_BYTES=2 * 1024 * 1024,
                       editable_file=editable_file, contains_editable_files=contains_editable_files)
        names = {"protect", "error", "safe_path", "rel", "server_files", "fs_path", "fs_relative",
                 "is_site_root", "own_like_parent", "valid_name", "build_command",
                 "fs_list", "fs_read", "fs_save", "fs_create", "fs_rename", "fs_delete",
                 "fs_upload", "fs_download"}
        nodes = [node for node in ast.parse((APP / "app.py").read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(APP / "app.py"), "exec"), self.ns)
        self.client = self.app.test_client()
        with self.client.session_transaction() as state:
            state.update(user="test-admin", csrf="test-csrf")

    def post(self, action, **data):
        return self.client.post("/api/server-fs/" + action, json=data,
                                headers={"X-CSRF-Token": "test-csrf"})

    def test_root_listing_and_no_recursive_system_scan(self):
        with patch("os.scandir", return_value=[]) as scan:
            response = self.client.get("/api/server-fs/list")
        self.assertEqual(response.json["path"], "")
        scan.assert_called_once_with("/")
        empty = self.outside / "empty"
        empty.mkdir()
        with patch.dict(self.ns, contains_editable_files=Mock(side_effect=AssertionError("recursive scan"))):
            response = self.client.get("/api/server-fs/list", query_string={"path": str(self.outside)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["path"], str(self.outside).lstrip("/"))
        entries = {item["name"]: item for item in response.json["items"]}
        self.assertTrue(entries["empty"]["has_editable_files"])
        self.assertTrue(entries["example.conf"]["editable"])

    def test_read_save_download_outside_web_root(self):
        response = self.client.get("/api/server-fs/read", query_string={"path": str(self.file)})
        self.assertEqual(response.json["content"], "original\n")
        response = self.post("save", path=str(self.file), content="updated\n")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.file.read_text(), "updated\n")
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o640)
        response = self.client.get("/api/server-fs/download", query_string={"path": str(self.file)})
        self.assertEqual(response.data, b"updated\n")
        response.close()

    def test_create_upload_rename_delete_outside_web_root(self):
        self.assertEqual(self.post("mkdir", path=str(self.outside), name="new").status_code, 200)
        folder = self.outside / "new"
        self.assertEqual(self.post("newfile", path=str(folder), name="file.txt").status_code, 200)
        file = folder / "file.txt"
        self.assertEqual(file.stat().st_uid, folder.stat().st_uid)
        response = self.client.post("/api/server-fs/upload",
                                    data={"path": str(folder), "files": (io.BytesIO(b"upload"), "upload.txt")},
                                    headers={"X-CSRF-Token": "test-csrf"})
        self.assertEqual(response.json["count"], 1)
        self.assertEqual(self.post("rename", path=str(file), name="renamed.txt").status_code, 200)
        self.assertEqual(self.post("delete", path=str(folder / "renamed.txt")).status_code, 200)
        self.assertEqual(self.post("delete", path=str(folder)).status_code, 200)
        self.assertFalse(folder.exists())

    def test_web_root_confinement_and_protected_roots_are_preserved(self):
        for action in ("list", "read", "download"):
            response = self.client.get("/api/fs/" + action, query_string={"path": "../etc/example.conf"})
            self.assertEqual(response.status_code, 400)
        link = self.web / "link"
        link.symlink_to(self.outside, target_is_directory=True)
        self.assertEqual(self.client.get("/api/fs/list?path=link").status_code, 400)
        for action in ("rename", "delete"):
            with self.subTest(action=action):
                self.assertEqual(self.post(action, path="/", name="renamed").status_code, 400)
                self.assertEqual(self.post(action, path=str(self.web), name="renamed").status_code, 400)

    def test_server_file_routes_require_login_and_csrf(self):
        self.assertEqual(self.client.post("/api/server-fs/save", json={
            "path": str(self.file), "content": "bad"}).status_code, 403)
        with self.client.session_transaction() as state:
            state.clear()
        self.assertEqual(self.client.get("/api/server-fs/list").status_code, 401)
        self.assertEqual(self.file.read_text(), "original\n")

    def test_server_directory_links_are_browsable_and_mutations_change_link_only(self):
        link = self.outside / "linked-directory"
        target = self.outside / "target"
        target.mkdir()
        (target / "keep.txt").write_text("keep")
        link.symlink_to(target, target_is_directory=True)
        listing = self.client.get("/api/server-fs/list", query_string={"path": str(self.outside)})
        item = next(item for item in listing.json["items"] if item["name"] == link.name)
        self.assertTrue(item["dir"])
        response = self.client.get("/api/server-fs/list", query_string={"path": str(link)})
        self.assertEqual(response.json["path"], str(target).lstrip("/"))
        self.assertEqual(self.post("rename", path=str(link), name="renamed-link").status_code, 200)
        renamed = self.outside / "renamed-link"
        self.assertTrue(renamed.is_symlink())
        self.assertEqual(self.post("delete", path=str(renamed)).status_code, 200)
        self.assertTrue((target / "keep.txt").is_file())

    def test_server_shell_starts_at_root_and_site_shell_is_unchanged(self):
        self.ns["site_arg"] = lambda data: data["site"]
        self.assertEqual(self.ns["build_command"]({"kind": "shell"})[2], "/")
        self.assertEqual(self.ns["build_command"]({"kind": "shell", "site": "example.test"})[2],
                         str(self.web / "example.test"))

    def test_settings_update_runs_scripts_in_order_and_stops_after_failure(self):
        apache = self.root / "vns/apache"
        apache.mkdir(parents=True)
        calls = self.root / "update-calls"
        updater = apache.parent / "update.sh"
        admin_updater = apache / "update_admin_site.sh"
        admin_updater.write_text(f'printf "admin:%s\\n" "$1" >> "{calls}"\n')
        self.ns.update(APACHE_DIR=str(apache), ADMIN_SITE="admin.example.test")
        for code in (0, 7):
            with self.subTest(code=code):
                updater.write_text(f'echo vns >> "{calls}"\nexit {code}\n')
                if calls.exists():
                    calls.unlink()
                title, argv, cwd = self.ns["build_command"]({
                    "kind": "update_system", "site": "ignored.test",
                })
                self.assertEqual(cwd, "/")
                result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(calls.read_text().splitlines(),
                                 ["vns", "admin:admin.example.test"] if code == 0 else ["vns"])
        updater.unlink()
        with self.app.test_request_context(), self.assertRaises(BadRequest):
            self.ns["build_command"]({"kind": "update_system"})


if __name__ == "__main__":
    unittest.main()
