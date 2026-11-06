import ast
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flask import abort
from werkzeug.exceptions import BadRequest


APACHE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("site_permissions", APACHE / "lib/site_permissions.py")
permissions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(permissions)


class SitePermissionsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name).resolve()
        self.root = self.base / "example.test"
        self.root.mkdir()
        self.service = self.base / "example.service"

    def repair(self):
        with patch.object(permissions.os, "chown") as chown:
            permissions.repair(self.root, self.service, 33, 33, self.base)
            return [Path(call.args[0]) for call in chown.call_args_list]

    def test_static_content_skips_scripts_secrets_and_symlinks(self):
        (self.root / "media").mkdir()
        image = self.root / "media/image.jpg"
        image.write_bytes(b"fixture")
        for folder in ("scripts", ".git", "venv"):
            (self.root / folder).mkdir()
            (self.root / folder / "keep").touch()
        (self.root / ".env").touch()
        outside = self.base / "outside"
        outside.write_text("unchanged")
        outside.chmod(0o600)
        (self.root / "link").symlink_to(outside)
        (self.root / "folder-link").symlink_to(self.base, target_is_directory=True)
        changed = self.repair()
        self.assertEqual(set(changed), {self.root, self.root / "media", image})
        self.assertEqual(image.stat().st_mode & 0o777, 0o640)
        self.assertEqual((self.root / "media").stat().st_mode & 0o777, 0o750)
        self.assertEqual(outside.stat().st_mode & 0o777, 0o600)

    def test_service_targets_publish_or_python_app_only(self):
        for folder in ("_publish", "app"):
            with self.subTest(folder=folder):
                target = self.root / folder
                target.mkdir()
                binary = target / "runner"
                binary.touch()
                binary.chmod(0o700)
                self.service.write_text("[Service]\nWorkingDirectory=" + str(target) + "\n")
                self.assertEqual(set(self.repair()), {target, binary})
                self.assertEqual(binary.stat().st_mode & 0o777, 0o750)

    def test_outside_working_directory_is_rejected(self):
        self.service.write_text("WorkingDirectory=" + str(self.base))
        with self.assertRaisesRegex(ValueError, "outside"):
            self.repair()

    def test_admin_site_is_rejected(self):
        app = self.root / "app"
        app.mkdir()
        (app / "manage.py").touch()
        (app / "config.json").touch()
        with self.assertRaisesRegex(ValueError, "root-owned"):
            self.repair()

    def test_content_symlink_is_rejected(self):
        (self.root / "_publish").symlink_to(self.base, target_is_directory=True)
        self.service.write_text("WorkingDirectory=" + str(self.root / "_publish"))
        with self.assertRaisesRegex(ValueError, "WorkingDirectory"):
            self.repair()

    def test_api_routes_existing_sites_and_production_to_shared_script(self):
        source = APACHE / "solutionfiles/admin/app/app.py"
        nodes = [node for node in ast.parse(source.read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name == "build_command"]
        namespace = {"os": os, "re": __import__("re"), "abort": abort, "site_arg": lambda data: data["site"],
                     "BINDING_RE": __import__("re").compile(
                         r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$"),
                     "ADMIN_SITE": "admin.test", "APACHE_DIR": str(APACHE), "WEB_ROOT": str(self.base)}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
        command = namespace["build_command"]
        for scope in ("test", "prod"):
            if scope == "prod":
                (self.root / "scripts").mkdir()
                (self.root / "scripts/publish_prod.sh").touch()
            _, argv, _ = command({"kind": "fix_permissions", "site": "example.test", "scope": scope})
            self.assertEqual(argv, ["/bin/bash", str(APACHE / "fix_permissions.sh"), "example.test", scope])
        for site, scope in (("admin.test", "test"), ("example.test", "all")):
            with self.assertRaises(BadRequest):
                command({"kind": "fix_permissions", "site": site, "scope": scope})

    def test_change_bindings_passes_validated_aliases_to_both_scopes(self):
        source = APACHE / "solutionfiles/admin/app/app.py"
        nodes = [node for node in ast.parse(source.read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name == "build_command"]
        namespace = {"os": os, "re": __import__("re"), "abort": abort, "site_arg": lambda data: data["site"],
                     "BINDING_RE": __import__("re").compile(
                         r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$"),
                     "ADMIN_SITE": "admin.test", "APACHE_DIR": str(APACHE), "WEB_ROOT": str(self.base)}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
        (self.root / "scripts").mkdir()
        (self.root / "scripts/publish_prod.sh").touch()
        command = namespace["build_command"]
        for scope, script in (("test", "change_binding.sh"), ("prod", "change_binding_prod.sh")):
            title, argv, cwd = command({
                "kind": "change_binding", "site": "example.test", "binding": "example.test",
                "aliases": "WWW.example.test, api.example.test www.example.test", "scope": scope,
            })
            self.assertEqual(argv, ["/bin/bash", str(APACHE / script), "example.test",
                                    "example.test", "www.example.test api.example.test", "--yes"])
            self.assertIn("bindings", title.lower())
            self.assertEqual(cwd, str(APACHE))
            _, legacy_argv, _ = command({
                "kind": "change_binding", "site": "example.test", "binding": "example.test",
                "scope": scope,
            })
            self.assertEqual(legacy_argv, argv[:4])
        for aliases in ("not-a-domain", "*.example.test", "https://example.test", "a" * 254 + ".test"):
            with self.subTest(aliases=aliases), self.assertRaises(BadRequest):
                command({"kind": "change_binding", "site": "example.test",
                         "binding": "example.test", "aliases": aliases, "scope": "test"})


if __name__ == "__main__":
    unittest.main()
