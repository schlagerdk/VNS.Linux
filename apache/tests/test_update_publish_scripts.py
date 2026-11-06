import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


APACHE = Path(__file__).resolve().parents[1]
UPDATER = APACHE / "update_admin_site.sh"


def updater_python():
    source = UPDATER.read_text()
    match = re.search(r"<<'PY'\n(.*?)\nPY\n\texit", source, re.S)
    if not match:
        raise AssertionError("Could not find publish-script updater block")
    return match.group(1)


class UpdatePublishScriptsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "www"
        self.apache = Path(self.temp.name) / "apache"
        self.apache.mkdir()
        site = self.root / "example.test"
        (site / "scripts").mkdir(parents=True)
        (site / "Example.csproj").write_text("<Project />\n")
        self.local = site / "scripts/publish.sh"
        self.local.write_text(
            'cd "$(dirname "$0")"\ncd ..\n'
            'dotnet publish "Example.csproj" -c Debug -p:PublishDir="_publish" '
            '--self-contained false --nologo\n'
        )
        self.local.chmod(0o700)
        self.local_prod = site / "scripts/publish_local.sh"
        self.local_prod.write_text(self.local.read_text())
        self.remote = site / "scripts/publish_prod.sh"
        self.remote.write_text(
            'set -euo pipefail\n'
            'PROD_PASSWORD=keep-this-setting\n'
            'remote() { "${_ssh[@]}" "${_target}" "$@"; }\n'
            'dotnet publish "Example.csproj" -c Release -p:PublishDir="${_publishFolder}" '
            '--self-contained false --nologo\n'
        )
        self.non_dotnet = self.root / "static.test"
        (self.non_dotnet / "scripts").mkdir(parents=True)
        (self.non_dotnet / "scripts/publish.sh").write_text("echo unchanged\n")

    def tearDown(self):
        self.temp.cleanup()

    def run_updater(self):
        return subprocess.run(
            [sys.executable, "-c", updater_python(), str(self.root), str(self.apache)],
            capture_output=True, text=True)

    def test_updates_local_and_remote_scripts_without_overwriting_settings(self):
        result = self.run_updater()
        self.assertEqual(result.returncode, 0, result.stderr)
        local = self.local.read_text()
        self.assertIn("source " + shlex.quote(str(self.apache / "lib/dotnet.sh")), local)
        self.assertIn('rm -rf "${_publishFolder}/runtimes"', local)
        self.assertIn('--runtime "$(linux_runtime_identifier)" --self-contained false', local)
        self.assertIn("CustomAfterMicrosoftCommonTargets=" + str(self.apache / "lib/publish.targets"), local)
        self.assertEqual(self.local.stat().st_mode & 0o777, 0o700)
        self.assertIn('--runtime "$(linux_runtime_identifier)"', self.local_prod.read_text())

        production = self.remote.read_text()
        self.assertIn("PROD_PASSWORD=keep-this-setting", production)
        self.assertIn('--runtime "${_runtimeIdentifier}" --self-contained false', production)
        self.assertIn('_runtimeIdentifier=$(linux_runtime_identifier "$(remote uname -m)")', production)
        self.assertLess(production.index("source " + shlex.quote(str(self.apache / "lib/dotnet.sh"))), production.index('remote()'))
        self.assertLess(production.index('remote()'), production.index('_runtimeIdentifier='))
        self.assertEqual((self.non_dotnet / "scripts/publish.sh").read_text(), "echo unchanged\n")
        self.assertEqual(production.count("_runtimeIdentifier=$(linux_runtime_identifier)"), 1)
        self.assertEqual(production.count('_runtimeIdentifier=$(linux_runtime_identifier "$(remote uname -m)")'), 1)

    def test_is_idempotent(self):
        first = self.run_updater()
        self.assertEqual(first.returncode, 0, first.stderr)
        contents = [path.read_text() for path in (self.local, self.local_prod, self.remote)]
        second = self.run_updater()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("Already current", second.stdout)
        self.assertEqual(contents, [path.read_text() for path in (self.local, self.local_prod, self.remote)])

    def test_already_migrated_script_gets_assembly_refresh_without_duplicate_source(self):
        for path in (self.local, self.local_prod):
            text = path.read_text().replace(
                "dotnet publish", f'source "{self.apache}/lib/dotnet.sh"\ndotnet publish'
            ).replace("--self-contained false", '--runtime "$(linux_runtime_identifier)" --self-contained false')
            path.write_text(text)
        result = self.run_updater()
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (self.local, self.local_prod):
            text = path.read_text()
            self.assertEqual(text.count("/lib/dotnet.sh"), 1)
            self.assertEqual(text.count("CustomAfterMicrosoftCommonTargets="), 1)
        contents = self.local.read_text()
        result = self.run_updater()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(contents, self.local.read_text())


if __name__ == "__main__":
    unittest.main()
