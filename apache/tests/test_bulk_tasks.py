import ast
import glob
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from flask import abort
from werkzeug.exceptions import BadRequest


APACHE = Path(__file__).resolve().parents[1]
APP = APACHE / "solutionfiles/admin/app"
spec = importlib.util.spec_from_file_location("bulk_tasks", APACHE / "lib/bulk_tasks.py")
bulk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bulk)


class BulkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("one", "two"):
            scripts = self.root / name / "scripts"
            scripts.mkdir(parents=True)
            (scripts.parent / "Project.csproj").write_text("<Project />")
            (scripts / "publish.sh").write_text("exit 0\n")
        (self.root / "one/scripts/publish_prod.sh").write_text("exit 0\n")
        nodes = [node for node in ast.parse((APP / "app.py").read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name == "build_command"]
        self.ns = dict(os=os, glob=glob, sys=sys, json=json, abort=abort,
                       WEB_ROOT=str(self.root), APACHE_DIR=str(APACHE),
                       SCRIPT_RE=re.compile(r"^[A-Za-z0-9_.-]+\.sh$"),
                       PACKAGE_RE=re.compile(r"^[A-Za-z0-9_.-]+$"),
                       VERSION_RE=re.compile(r"^[A-Za-z0-9_.-]+$"),
                       site_arg=lambda data: data["site"], site_info=self.info)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "bulk API", "exec"), self.ns)

    def info(self, name, _):
        return {"kind": "csharp", "scripts": [
            path.name for path in (self.root / name / "scripts").glob("*.sh")]}

    def command(self, **kwargs):
        return self.ns["build_command"]({"kind": "bulk", "sites": ["one", "two"], **kwargs})

    def test_scoped_publish_and_missing_production(self):
        for action in ("local", "prod", "both"):
            _, argv, cwd = self.command(action=action)
            tasks = json.loads(argv[-1])
            self.assertEqual(len(tasks), 2)
            self.assertEqual(bool(tasks[1].get("skip")), action != "local")
            result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Multi action summary", result.stdout)
            self.assertIn("one: OK", result.stdout)
            if action != "local":
                self.assertIn("two: SKIPPED", result.stdout)

    def test_package_commands_reuse_validation(self):
        for action in ("add", "remove"):
            _, argv, _ = self.command(action=action, package="Example.Package", version="1.2.3")
            tasks = json.loads(argv[-1])
            for task in tasks:
                self.assertEqual(task["argv"][:4], ["dotnet", action, "package", "Example.Package"])
                self.assertEqual("--version" in task["argv"], action == "add")
        with self.assertRaises(BadRequest):
            self.command(action="add", package="bad;command")

    def test_invalid_selection_and_non_csharp_are_rejected(self):
        for sites in ([], ["one", "one"], ["one"] * 101, [1]):
            with self.assertRaises(BadRequest):
                self.command(action="local", sites=sites)
        self.ns["site_info"] = lambda *_: {"kind": "python"}
        with self.assertRaises(BadRequest):
            self.command(action="local")

    def test_continues_after_failure_and_reports_all_sites(self):
        (self.root / "one/scripts/publish.sh").write_text("exit 7\n")
        _, argv, cwd = self.command(action="local")
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("one: FAILED: exit 7", result.stdout)
        self.assertIn("two: OK", result.stdout)

    def test_spawn_failure_does_not_stop_next_site(self):
        tasks = [
            {"site": "one", "argv": ["/nonexistent/vns-command"], "cwd": str(self.root)},
            {"site": "two", "argv": [sys.executable, "-c", "pass"], "cwd": str(self.root)},
        ]
        self.assertEqual(bulk.run(tasks), 1)

    def test_both_skips_failed_sites_production_but_continues_next_site(self):
        (self.root / "one/scripts/publish.sh").write_text("exit 7\n")
        (self.root / "one/scripts/publish_prod.sh").write_text("touch should-not-run\n")
        (self.root / "two/scripts/publish_prod.sh").write_text("exit 0\n")
        _, argv, cwd = self.command(action="both")
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.root / "one/should-not-run").exists())
        self.assertIn("two: OK", result.stdout)

    def test_script_symlink_outside_site_is_rejected(self):
        (self.root / "one/scripts/publish.sh").unlink()
        outside = self.root / "outside.sh"
        outside.write_text("exit 0\n")
        (self.root / "one/scripts/publish.sh").symlink_to(outside)
        with self.assertRaises(BadRequest):
            self.command(action="local")


if __name__ == "__main__":
    unittest.main()
