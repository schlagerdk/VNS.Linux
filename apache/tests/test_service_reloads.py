import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


APACHE = Path(__file__).resolve().parents[1]


def run_shell(code):
    return subprocess.run(["bash", "-c", "set -euo pipefail\n" + code],
                          capture_output=True, text=True)


class ApacheReloadTests(unittest.TestCase):
    def test_init_reloads_running_apache_and_starts_stopped_apache(self):
        source = (APACHE / "init.sh").read_text()
        block = source[source.index('echo "Test Apache config'):source.index("\nif [[ ! -x /etc/acmesh")]
        for running in (True, False):
            with self.subTest(running=running):
                mock = """
sudo() {
    echo "CALL:$*"
    if [[ $1 == systemctl && $2 == is-active ]]; then return ACTIVE; fi
}
""".replace("ACTIVE", "0" if running else "3")
                result = run_shell(mock + block)
                self.assertEqual(result.returncode, 0, result.stderr)
                action = "reload" if running else "start"
                self.assertIn("CALL:systemctl " + action + " apache2", result.stdout)
                self.assertNotIn("CALL:systemctl restart", result.stdout)
                self.assertLess(result.stdout.index("CALL:apache2ctl configtest"),
                                result.stdout.index("CALL:systemctl " + action))

    def test_failed_configtest_stops_init_and_delete(self):
        init = (APACHE / "init.sh").read_text()
        delete = (APACHE / "delete_site.sh").read_text()
        blocks = [init[init.index('echo "Test Apache config'):init.index("\nif [[ ! -x /etc/acmesh")],
                  delete[delete.index('echo "Reload daemon"'):]]
        for block in blocks:
            result = run_shell("""
sudo() {
    echo "CALL:$*"
    if [[ $1 == apache2ctl ]]; then return 1; fi
}
""" + block)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("CALL:systemctl reload apache2", result.stdout)
            self.assertNotIn("CALL:systemctl start apache2", result.stdout)


class ApplicationReloadTests(unittest.TestCase):
    def test_https_settings_report_only_real_changes(self):
        source = (APACHE / "lib/common.sh").read_text()
        start = source.index("configure_umbraco_https() {")
        helper = source[start:source.index("\n}", start) + 2]
        for settings in ({}, {"Other": True}, {"Umbraco": {"CMS": {}}}):
            with self.subTest(settings=settings), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "appsettings.json"
                original = json.dumps(settings)
                path.write_text(original)
                code = ('sudo() { "$@"; }\n' + helper +
                        "\nconfigure_umbraco_https " + shlex.quote(str(path)) + " example.test y\n")
                first = run_shell(code)
                self.assertEqual(first.returncode, 0, first.stderr)
                updated = "Umbraco" in settings
                self.assertEqual(first.stdout.strip(), "changed" if updated else "")
                if not updated:
                    self.assertEqual(path.read_text(), original)
                before = path.read_bytes()
                second = run_shell(code)
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertEqual(second.stdout, "")
                self.assertEqual(path.read_bytes(), before)
                if updated:
                    cms = json.loads(path.read_text())["Umbraco"]["CMS"]
                    self.assertTrue(cms["Global"]["UseHttps"])
                    self.assertEqual(cms["WebRouting"]["UmbracoApplicationUrl"], "https://example.test")

    def test_certificate_action_restarts_only_after_settings_change(self):
        source = (APACHE / "test_action.sh").read_text()
        start = source.index("\t\t\t_settingsChanged=")
        block = source[start:source.index("\n\t\tfi", start)]
        for changed in (True, False):
            mock = """
_working=/fixture
_binding=example.test
_site=example.test
configure_umbraco_https() { OUTPUT; }
sudo() { echo "CALL:$*"; }
""".replace("OUTPUT", "echo changed" if changed else ":")
            result = run_shell(mock + block)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual("CALL:systemctl restart example.test" in result.stdout, changed)

    def test_binding_changes_restart_only_when_appsettings_changed(self):
        for remote in (False, True):
            source = (APACHE / ("change_binding_prod.sh" if remote else "change_binding.sh")).read_text()
            marker = "if [[ -f ${backup}/appsettings.json ]]" if remote else "if [[ -f ${_backupDir}/appsettings.json ]]"
            start = source.rindex(marker)
            end_marker = "\n# 4." if remote else "\nif [[ ${_newBinding} != \"${_oldBinding}\" ]]"
            block = source[start:source.index(end_marker, start)]
            for state in ("absent", "unchanged", "changed"):
                with self.subTest(remote=remote, state=state), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    backup = root / "backup"
                    backup.mkdir()
                    settings = root / "appsettings.json"
                    if state != "absent":
                        (backup / "appsettings.json").write_text("old")
                        settings.write_text("new" if state == "changed" else "old")
                    prefix = """
sudo() { "$@"; }
systemctl() { echo "CALL:$*"; }
rollback() { echo rollback; }
_rollback() { echo rollback; }
SITE=example.test
_site=example.test
app_restart_attempted=n
_appRestartAttempted=n
"""
                    prefix += "\nbackup=" + shlex.quote(str(backup))
                    prefix += "\n_backupDir=" + shlex.quote(str(backup))
                    prefix += "\nsettings=" + shlex.quote(str(settings))
                    prefix += "\n_appSettings=" + shlex.quote(str(settings)) + "\n"
                    result = run_shell(prefix + block)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual("CALL:restart example.test" in result.stdout, state == "changed")


if __name__ == "__main__":
    unittest.main()
