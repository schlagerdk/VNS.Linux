import ast
import glob
import importlib.util
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest


APACHE = Path(__file__).resolve().parents[1]
APP = APACHE / "solutionfiles/admin/app"
HELPER = APACHE / "lib/publish_status.py"
spec = importlib.util.spec_from_file_location("publish_status", HELPER)
tracking = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tracking)


class PublishStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "example.test"
        self.root.mkdir()
        self.project = self.root / "Example.csproj"
        self.project.write_text('<Project><ItemGroup><PackageReference Include="Example" Version="1.0" /></ItemGroup></Project>')

    def publish(self, scope="test", code="pass"):
        return tracking.run_publish(self.root, scope, [sys.executable, "-c", code])

    def status(self, scope="test"):
        return tracking.status(self.root, scope, tracking.fingerprint(self.root))

    def test_unknown_then_independent_test_and_prod_baselines(self):
        self.assertEqual(self.status(), "unknown")
        self.assertEqual(self.publish(), 0)
        self.assertEqual(self.status(), "current")
        self.assertEqual(self.status("prod"), "unknown")
        self.assertEqual(self.publish("prod"), 0)
        self.project.write_text(self.project.read_text().replace("1.0", "2.0"))
        self.assertEqual(self.status(), "pending")
        self.assertEqual(self.status("prod"), "pending")
        self.publish()
        self.assertEqual(self.status(), "current")
        self.assertEqual(self.status("prod"), "pending")
        self.publish("prod")
        self.assertEqual(self.status("prod"), "current")

    def test_add_remove_and_manual_version_changes(self):
        original = self.project.read_text()
        self.publish()
        for changed in (
            original.replace("</ItemGroup>", '<PackageReference Include="Other" Version="1" /></ItemGroup>'),
            "<Project />",
            original.replace("1.0", "3.0"),
        ):
            self.project.write_text(changed)
            self.assertEqual(self.status(), "pending")
        self.project.write_text(original)
        self.assertEqual(self.status(), "current")

    def test_ancestor_package_and_build_configuration_changes(self):
        for name in ("Directory.Packages.props", "Directory.Build.props", "Directory.Build.targets"):
            path = self.root.parent / name
            path.write_text("<Project />")
            self.publish()
            path.write_text("<Project><PropertyGroup /></Project>")
            self.assertEqual(self.status(), "pending", name)
            path.unlink()

    def test_failed_publish_preserves_previous_baseline(self):
        self.publish()
        baseline = (self.root / ".vns-publish/test.json").read_text()
        self.project.write_text("<Project />")
        self.assertEqual(self.publish(code="import sys; sys.exit(7)"), 7)
        self.assertEqual((self.root / ".vns-publish/test.json").read_text(), baseline)
        self.assertEqual(self.status(), "pending")
        self.assertEqual(self.publish("prod", "import sys; sys.exit(9)"), 9)
        self.assertEqual(self.status("prod"), "unknown")

    def test_changes_during_publish_remain_pending(self):
        code = 'from pathlib import Path; Path("Example.csproj").write_text("<Project />")'
        self.assertEqual(self.publish(code=code), 0)
        self.assertEqual(self.status(), "pending")

    def test_invalid_baseline_is_not_treated_as_current(self):
        self.publish()
        path = self.root / ".vns-publish/test.json"
        for content in ("{", "[]", '{"fingerprint":null}'):
            path.write_text(content)
            with self.assertRaises(ValueError):
                self.status()

    def test_cli_persists_baseline_without_project_contents_or_temporary_files(self):
        command = [sys.executable, str(HELPER), "run", str(self.root), "test",
                   sys.executable, "-c", "pass"]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.status(), "current")
        self.assertEqual(list((self.root / ".vns-publish").glob("*.tmp")), [])
        self.assertNotIn("PackageReference", (self.root / ".vns-publish/test.json").read_text())

    def test_missing_project_reports_error_without_running_command(self):
        self.project.unlink()
        result = subprocess.run(
            [sys.executable, str(HELPER), "run", str(self.root), "test",
             sys.executable, "-c", 'raise RuntimeError("should not run")'],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR: Publish tracking failed", result.stderr)
        self.assertNotIn("should not run", result.stderr)

    def test_initial_site_creation_publishes_record_only_success(self):
        source = (APACHE / "create_site.sh").read_text()
        commands = [line.strip() for line in source.splitlines() if "dotnet publish " in line]
        self.assertEqual(len(commands), 2)
        bin_dir = self.root / "tools"
        bin_dir.mkdir()
        dotnet = bin_dir / "dotnet"
        dotnet.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\nexit "${PUBLISH_EXIT}"\n')
        dotnet.chmod(0o700)
        baseline = self.root / ".vns-publish/test.json"
        for command in commands:
            for exit_code in (7, 0):
                with self.subTest(command=command, exit_code=exit_code):
                    if baseline.exists():
                        baseline.unlink()
                    result = subprocess.run(
                        ["/bin/bash", "-c",
                         f'source "{APACHE / "lib/dotnet.sh"}"\nsudo() {{ "$@"; }}\n' + command],
                        cwd=self.root, capture_output=True, text=True,
                        env=os.environ | {
                            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                            "_apacheDir": str(APACHE), "_siteRoot": str(self.root),
                            "_project": str(self.project), "_buildConfig": "Debug",
                            "_publishFolder": "_publish", "PUBLISH_EXIT": str(exit_code),
                        })
                    self.assertEqual(result.returncode, exit_code, result.stderr)
                    self.assertIn(str(self.project), result.stdout)
                    self.assertRegex(result.stdout, r"--runtime\nlinux-(x64|arm64)\n")
                    self.assertEqual(self.status(), "current" if exit_code == 0 else "unknown")

    def test_site_api_exposes_per_environment_state_and_logs_corruption(self):
        nodes = [node for node in ast.parse((APP / "app.py").read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name == "site_info"]
        ns = dict(os=os, glob=glob, re=re, _publish_module=tracking, WEB_ROOT=str(self.root.parent),
                  APACHE_SITES=str(self.root), APACHE_ENABLED=str(self.root), SERVICE_DIR=str(self.root),
                  SCRIPT_RE=re.compile(r".*\.sh$"), ADMIN_SITE="admin.test",
                  read_text=lambda path: Path(path).read_text() if Path(path).is_file() else "",
                  package_references=lambda projects: ([], []),
                  environment_info=lambda key, *args, **kwargs: {"key": key},
                  parse_prod=lambda root: dict(PROD_LOCAL_PATH="", PROD_BINDING="prod.test",
                                              PROD_PORT_HTTP="4001", PROD_IP="remote.test",
                                              PROD_USER="root", PROD_SSH_PORT="22", PROD_FOLDER="prod.test"),
                  remote_status=lambda name: {}, app=SimpleNamespace(logger=logging.getLogger(__name__)))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "site publish status", "exec"), ns)
        site = ns["site_info"]("example.test", {})
        self.assertEqual([env["publish_status"] for env in site["environments"]], ["unknown", "unknown"])
        self.publish()
        site = ns["site_info"]("example.test", {})
        self.assertEqual([env["publish_status"] for env in site["environments"]], ["current", "unknown"])
        (self.root / ".vns-publish/test.json").write_text("{")
        with self.assertLogs(__name__, level="WARNING"):
            site = ns["site_info"]("example.test", {})
        self.assertTrue(all(env["publish_status"] == "unknown" for env in site["environments"]))
        self.publish("prod")
        with self.assertLogs(__name__, level="WARNING"):
            site = ns["site_info"]("example.test", {})
        self.assertEqual([env["publish_status"] for env in site["environments"]], ["unknown", "current"])

    def test_badges_and_template_tracking_hooks(self):
        source = (APP / "static/app.js").read_text()
        badge = source[source.index("function publishBadge("):source.index("function siteCard(")]
        harness = """
const assert = require("node:assert/strict");
const el = (tag, properties) => properties;
assert.equal(publishBadge({publish_status: "pending"}).textContent, "Publish pending");
assert.equal(publishBadge({publish_status: "pending"}).className, "badge publish-pending");
assert.equal(publishBadge({publish_status: "unknown"}).textContent, "Publish status unknown");
assert.equal(publishBadge({publish_status: "current"}), null);
assert.equal(publishBadge({}), null);
"""
        subprocess.run(["node", "-e", badge + harness], check=True, capture_output=True)
        self.assertIn("const pending = publishBadge(env);", source[source.index("function siteCard("):source.index("function renderSite(")])
        self.assertIn("const pending = publishBadge(env);", source[source.index("function envPanel("):])
        for scope, template in (
            ("test", APACHE / "solutionfiles/csharp/scripts/publish.sh"),
            ("prod", APACHE / "solutionfiles/csharp/prod-scripts/publish_prod.sh"),
        ):
            text = template.read_text()
            self.assertIn("VNS_PUBLISH_TRACKED:-} != " + scope, text)
            self.assertIn('"@APACHE_DIR@/lib/publish_status.py" run', text)

    def test_existing_create_prod_renderer_gets_tracking_helper_path(self):
        result = subprocess.run(
            ["/bin/bash", "-c",
             'source "$1"; perl -pe \'s/\\@([A-Z_]+)\\@/exists $ENV{"R_$1"} ? $ENV{"R_$1"} : "\\@$1\\@"/ge\' "$2"',
             "render-prod", str(APACHE / "lib/common.sh"),
             str(APACHE / "solutionfiles/csharp/prod-scripts/publish_prod.sh")],
            check=True, capture_output=True, text=True)
        self.assertNotIn("@APACHE_DIR@", result.stdout)
        self.assertIn('"' + str(HELPER) + '" run', result.stdout)


if __name__ == "__main__":
    unittest.main()
