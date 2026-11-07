import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import Mock

from flask import Flask, abort, jsonify, request


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "apache/solutionfiles/admin/app"


class SystemUpdateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.marker = self.root / ".vns-version"
        self.installed = "a" * 40
        self.latest = "b" * 40
        self.app = Flask(__name__)
        self.run = Mock(return_value=Mock(stdout=self.latest + "\tHEAD\n"))
        self.clock = Mock(return_value=1000)
        self.ns = dict(app=self.app, jsonify=jsonify, request=request, Path=Path, BASE=str(self.root), re=re,
                       subprocess=Mock(run=self.run, SubprocessError=subprocess.SubprocessError),
                       SYSTEM_UPDATE_LOCK=threading.Lock(), SYSTEM_UPDATE_CACHE={},
                       time=Mock(monotonic=self.clock))
        nodes = [node for node in ast.parse((APP / "app.py").read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name == "api_system_update"]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "system updates", "exec"), self.ns)
        self.client = self.app.test_client()

    def test_changed_and_matching_revisions_and_cache(self):
        self.marker.write_text(self.installed)
        response = self.client.get("/api/system-update")
        self.assertEqual(response.json["status"], "available")
        self.assertEqual(response.json["installed"], self.installed)
        self.assertEqual(response.json["latest"], self.latest)
        self.assertEqual(self.run.call_args.kwargs["timeout"], 15)
        self.client.get("/api/system-update")
        self.assertEqual(self.run.call_count, 1)
        self.clock.return_value = 1061
        self.run.return_value.stdout = self.installed + "\tHEAD\n"
        self.assertEqual(self.client.get("/api/system-update").json["status"], "current")
        self.assertEqual(self.run.call_count, 2)

    def test_missing_and_empty_markers_are_unknown(self):
        for marker in (None, ""):
            with self.subTest(marker=marker):
                self.ns["SYSTEM_UPDATE_CACHE"].clear()
                if marker is not None:
                    self.marker.write_text(marker)
                self.assertEqual(self.client.get("/api/system-update").json["status"], "unknown")

    def test_explicit_refresh_bypasses_cached_revision(self):
        self.marker.write_text(self.installed)
        self.run.return_value.stdout = self.installed + "\tHEAD\n"
        self.assertEqual(self.client.get("/api/system-update").json["status"], "current")
        self.run.return_value.stdout = self.latest + "\tHEAD\n"
        self.assertEqual(self.client.get("/api/system-update").json["status"], "current")
        self.assertEqual(self.client.get("/api/system-update?refresh=1").json["status"], "available")
        self.assertEqual(self.run.call_count, 2)

    def test_malformed_markers_remote_responses_and_network_errors_are_explicit(self):
        for marker, output, failure in (
                ("invalid", self.latest + "\tHEAD", None),
                (self.installed, "", None),
                (self.installed, self.latest + "\trefs/heads/main", None),
                (self.installed, "", subprocess.TimeoutExpired("git", 15)),
                (self.installed, "", subprocess.CalledProcessError(128, "git"))):
            with self.subTest(marker=marker, output=output, failure=failure):
                self.ns["SYSTEM_UPDATE_CACHE"].clear()
                self.marker.write_text(marker)
                self.run.side_effect = failure
                self.run.return_value.stdout = output
                with self.assertLogs(self.app.logger, level="ERROR"):
                    response = self.client.get("/api/system-update")
                self.assertEqual(response.json["status"], "error")
                self.assertIn("error", response.json)

    def test_update_job_survives_new_app_and_reports_failures(self):
        helper = ROOT / "apache/lib/system_update.py"
        self.ns.update(abort=abort, json=json, uuid=uuid, os=os, sys=__import__("sys"),
                       APACHE_DIR=str(ROOT / "apache"), time=time,
                       build_command=Mock(return_value=("Update", [sys.executable, "-c", "pass"], "/")))
        self.ns["subprocess"] = subprocess
        nodes = [node for node in ast.parse((APP / "app.py").read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name == "api_run_system_update"]
        self.app = Flask("update jobs")
        self.ns["app"] = self.app
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "update jobs", "exec"), self.ns)
        client = self.app.test_client()
        self.assertEqual(client.get("/api/system-update/run").json["status"], "idle")
        from unittest.mock import patch
        with patch.object(subprocess, "Popen") as launch:
            response = client.post("/api/system-update/run")
        job = response.json["id"]
        self.assertEqual(response.json["status"], "running")
        self.assertTrue(launch.call_args.kwargs["start_new_session"])
        status = self.root / "system-update.json"
        self.assertEqual(status.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / "system-update.log").stat().st_mode & 0o777, 0o600)
        self.assertEqual(client.post("/api/system-update/run").status_code, 409)
        for code, expected in ((0, "success"), (1, "error")):
            subprocess.run([sys.executable, str(helper), str(status), job,
                            sys.executable, "-c", f"raise SystemExit({code})"], check=True)
            state = client.get("/api/system-update/run").json
            self.assertEqual(state["id"], job)
            self.assertEqual(state["status"], expected)
        self.assertIn("code 1", state["error"])
        updater = (ROOT / "apache/update_admin_site.sh").read_text()
        for name in ("system-update.json", "system-update.log"):
            self.assertIn("--exclude /" + name, updater)

    def test_deployment_records_commit_only_on_success_and_for_clean_sources(self):
        target = self.root / "deployed"
        source = self.root / "source"
        source.mkdir()
        binary = self.root / "bin"
        binary.mkdir()
        updater = self.root / "update.sh"
        updater.write_text((ROOT / "update.sh").read_text().replace('TARGET="/vns"', f'TARGET="{target}"'))
        for name, body in (
                ("sudo", 'exec "$@"'),
                ("git", 'case "$*" in\n'
                 '*rev-parse\\ --is-inside-work-tree) echo true;;\n'
                 f'*rev-parse\\ HEAD) echo {self.installed};;\n'
                 '*status\\ --porcelain*) printf "%s" "${DIRTY:-}";;\n'
                 'esac'),
                ("rsync", 'exit "${FAIL_RSYNC:-0}"')):
            path = binary / name
            path.write_text("#!/bin/bash\n" + body + "\n")
            path.chmod(0o700)
        for dirty, failure, expected in (("", "0", self.installed), (" M file", "0", ""), ("", "7", "")):
            with self.subTest(dirty=dirty, failure=failure):
                result = subprocess.run(["bash", str(updater), str(source)], capture_output=True, text=True,
                                        env=os.environ | {"PATH": str(binary) + ":" + os.environ["PATH"],
                                                          "DIRTY": dirty, "FAIL_RSYNC": failure})
                self.assertEqual(result.returncode, int(failure), result.stderr)
                self.assertEqual((target / ".vns-version").read_text().strip(), expected)


if __name__ == "__main__":
    unittest.main()
