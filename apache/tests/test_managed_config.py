import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "managed_config", Path(__file__).resolve().parents[1] / "lib/managed_config.py")
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


class ManagedConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.patchers = [patch.object(config, "SERVICE_DIR", root), patch.object(config, "APACHE_DIR", root)]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.service = root / "example.test.service"
        self.conf = root / "example.test.conf"
        self.service.write_text("[Service]\n")
        self.conf.write_text("<VirtualHost *:80>\n</VirtualHost>\n")
        self.conf.chmod(0o640)

    def test_read_and_save_preserve_permissions(self):
        self.assertEqual(config.handle("example.test", {"kind": "service", "action": "read"})["content"], "[Service]\n")
        config.handle("example.test", {"kind": "conf", "action": "save", "content": "updated\n"})
        self.assertEqual(self.conf.read_text(), "updated\n")
        self.assertEqual(self.conf.stat().st_mode & 0o777, 0o640)

    def test_apache_test_precedes_reload(self):
        with patch.object(config.subprocess, "run", return_value=Mock(stdout="", stderr="")) as run:
            config.handle("example.test", {"kind": "conf", "action": "apply"})
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         [["apache2ctl", "configtest"], ["systemctl", "reload", "apache2"]])

    def test_failed_config_test_never_reloads(self):
        with patch.object(config.subprocess, "run", side_effect=subprocess.CalledProcessError(
                1, ["apache2ctl", "configtest"], stderr="Syntax error")) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                config.handle("example.test", {"kind": "conf", "action": "apply"})
        self.assertEqual(run.call_count, 1)

    def test_daemon_reload_precedes_restart(self):
        with patch.object(config.subprocess, "run", return_value=Mock(stdout="", stderr="")) as run:
            config.handle("example.test", {"kind": "service", "action": "apply"})
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         [["systemctl", "daemon-reload"], ["systemctl", "restart", "example.test.service"]])

    def test_invalid_targets_are_rejected(self):
        for unit, kind in (("../outside", "service"), ("example.test", "other")):
            with self.assertRaises(ValueError):
                config.handle(unit, {"kind": kind, "action": "read"})


if __name__ == "__main__":
    unittest.main()
