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

    def test_all_bulk_actions_require_project_file_in_site_root(self):
        project = self.root / "two/Project.csproj"
        project.unlink()
        nested = project.parent / "nested"
        nested.mkdir()
        (nested / "Project.csproj").touch()
        project.mkdir()
        for action in ("check", "add", "remove", "local", "prod", "both", "outdated"):
            with self.subTest(action=action), self.assertRaises(BadRequest):
                self.command(action=action, package="Example")

    def test_checked_package_update_commands_and_validation(self):
        self.ns["site_info"] = lambda *_: {
            "kind": "csharp", "updates": "available",
            "package_updates": [{"name": "Example", "current": "1.0", "latest": "2.0"}],
        }
        for package in (None, "Example"):
            payload = {"kind": "update_package", "site": "one"}
            if package:
                payload["package"] = package
            _, argv, _ = self.ns["build_command"](payload)
            self.assertEqual(argv[2:4], ["upgrade", str(self.root / "one")])
            self.assertEqual(argv[4:], [package] if package else [])
        with self.assertRaises(BadRequest):
            self.ns["build_command"]({"kind": "update_package", "site": "one", "package": "Unknown"})

    def test_outdated_upgrades_each_project_without_interactive_script(self):
        _, argv, _ = self.command(action="outdated")
        for task in json.loads(argv[-1]):
            self.assertEqual(task["argv"], ["dotnet", "outdated", "--upgrade"])
            self.assertTrue(Path(task["cwd"], "Project.csproj").is_file())

    def test_check_lists_outdated_packages_without_upgrading(self):
        _, argv, _ = self.command(action="check")
        for task in json.loads(argv[-1]):
            self.assertEqual(task["argv"], [sys.executable, str(APACHE / "lib/publish_status.py"),
                                            "check", task["cwd"]])
            self.assertTrue(Path(task["cwd"], "Project.csproj").is_file())

    def test_bulk_check_persists_update_badges_and_clears_them(self):
        tools = self.root / "tools"
        tools.mkdir()
        dotnet = tools / "dotnet"
        dotnet.write_text('#!/bin/bash\nprintf "%s\\n" "$CHECK_OUTPUT"\nexit "${CHECK_EXIT:-0}"\n')
        dotnet.chmod(0o700)
        _, argv, cwd = self.command(action="check")
        available = json.dumps({"version": 1, "projects": [{"frameworks": [{"topLevelPackages": [
            {"id": "Example", "resolvedVersion": "1.0", "latestVersion": "2.0"}]}]}]})
        current = json.dumps({"version": 1, "projects": [{"frameworks": []}]})
        for output, code, expected in (
            (available, "0", "available"),
            (current, "0", "current"),
            ("error: feed unavailable", "1", "error"),
        ):
            with self.subTest(expected=expected):
                result = subprocess.run(
                    argv, cwd=cwd, capture_output=True, text=True,
                    env=os.environ | {"PATH": str(tools) + os.pathsep + os.environ["PATH"],
                                      "CHECK_OUTPUT": output, "CHECK_EXIT": code})
                self.assertEqual(result.returncode, int(code != "0"), result.stderr)
                self.assertIn("Example: 1.0 -> 2.0" if expected == "available" else
                              "No updates available" if expected == "current" else output, result.stdout)
                for site in ("one", "two"):
                    state = json.loads((self.root / site / ".vns-publish/updates.json").read_text())
                    self.assertEqual(state["status"], expected)

    def test_sites_start_concurrently_and_output_is_labelled(self):
        script = (
            "import pathlib, sys, time\n"
            "root = pathlib.Path(sys.argv[1])\n"
            "name, other = sys.argv[2:]\n"
            "(root / name).touch()\n"
            "deadline = time.monotonic() + 5\n"
            "while not (root / other).exists():\n"
            "    if time.monotonic() >= deadline: sys.exit(9)\n"
            "    time.sleep(0.01)\n"
            "print('concurrent')\n"
        )
        tasks = [
            {"site": name, "argv": [sys.executable, "-c", script, str(self.root), name, other],
             "cwd": str(self.root)}
            for name, other in (("started-one", "started-two"), ("started-two", "started-one"))
        ]
        result = subprocess.run(
            [sys.executable, str(APACHE / "lib/bulk_tasks.py"), json.dumps(tasks)],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        for task in tasks:
            self.assertIn(f'[{task["site"]}] concurrent', result.stdout)
            self.assertIn(f'{task["site"]}: OK', result.stdout)

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
