import ast
import codecs
from pathlib import Path
import re
import subprocess
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


APP = Path(__file__).resolve().parents[1] / "solutionfiles/admin/app"


class TerminalCompletionTests(unittest.TestCase):
    def setUp(self):
        names = {"terminal_scope", "terminal_auto_close", "terminal_output_problem", "TermSession", "describe"}
        nodes = [node for node in ast.parse((APP / "app.py").read_text()).body
                 if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
        self.ns = {"re": re, "codecs": codecs, "BUFFER_MAX": 1024, "REMOTE_STATUS": {},
                   "os": SimpleNamespace(read=Mock())}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "terminal completion", "exec"), self.ns)

    def test_activity_scope_matches_target_environment(self):
        scope = self.ns["terminal_scope"]
        for payload, expected in (
            ({"kind": "script", "script": "publish.sh"}, "main"),
            ({"kind": "script", "script": "publish_local.sh"}, "main"),
            ({"kind": "script", "script": "publish_prod.sh"}, "prod"),
            ({"kind": "remote_tool"}, "prod"),
            ({"kind": "publish_both"}, "all"),
            ({"kind": "bulk", "action": "local"}, "main"),
            ({"kind": "bulk", "action": "prod"}, "prod"),
            ({"kind": "bulk", "action": "both"}, "all"),
            ({"kind": "bulk", "action": "add"}, "main"),
            ({"kind": "unit_tool", "scope": "prod"}, "prod"),
            ({"kind": "shell", "scope": "prod"}, "prod"),
            ({"kind": "delete_site", "scope": "test"}, "main"),
            ({"kind": "delete_site", "scope": "all"}, "all"),
        ):
            with self.subTest(payload=payload):
                self.assertEqual(scope(payload), expected)

    def test_mutations_close_and_diagnostics_remain(self):
        auto_close = self.ns["terminal_auto_close"]
        for kind in ("create_site", "delete_site", "create_prod", "change_binding", "prod_action",
                     "shell", "dotnet_package", "publish_both", "bulk", "sql_action"):
            self.assertTrue(auto_close({"kind": kind}), kind)
        for script in ("publish.sh", "publish_local.sh", "publish_prod.sh", "sync_prod.sh",
                       "change_env.sh", "clear_logs.sh", "outdated.sh"):
            self.assertTrue(auto_close({"kind": "script", "script": script}), script)
        for script in ("check_service.sh", "custom.sh", "edit_conf.sh"):
            self.assertFalse(auto_close({"kind": "script", "script": script}), script)
        for kind in ("unit_tool", "remote_tool"):
            self.assertFalse(auto_close({"kind": kind, "tool": "check_service"}))
        self.assertTrue(auto_close({"kind": "remote_tool", "tool": "shell"}))
        self.assertFalse(auto_close({"kind": "apache", "tool": "configtest"}))
        self.assertFalse(auto_close({"kind": "unknown"}))

    def test_output_errors_and_warnings(self):
        problem = self.ns["terminal_output_problem"]
        for text in ("error MSB3202", "FAILED at script.sh:42", "fatal: unable to access",
                     "Unhandled exception.", "System.InvalidOperationException: failure", "Traceback (most recent call last):",
                     "warning NU1903:", "[ERROR] failed", "\x1b[31merror\x1b[0m"):
            self.assertTrue(problem(text), text)
        for text in ("Build succeeded.", "0 Warning(s)", "0 Error(s)", "0 Errors",
                     "No errors detected", "No outdated dependencies were detected",
                     "/var/log/apache2/site-error.log", "ErrorLog /var/log/apache2/example.log"):
            self.assertFalse(problem(text), text)

    def test_outdated_closes_after_declining_upgrade_without_errors(self):
        self.assertTrue(self.ns["terminal_auto_close"]({"kind": "script", "script": "outdated.sh"}))
        output = """Running dotnet outdated...
Discovering projects...
Analyzing dependencies...
Umbraco.Cms 13.13.0 -> 14.3.4
Version color legend:
<red> : Major version update or pre-release version. Possible breaking changes.
<yellow>: Minor version update. Backwards-compatible features added.
<green> : Patch version update. Backwards-compatible bug fixes.
Do you want to upgrade the packages? (y/n): n
No upgrade performed.
Done.
"""
        self.assertFalse(self.ns["terminal_output_problem"](output))
        self.assertTrue(self.ns["terminal_output_problem"](output + "warning NU1903: vulnerable package"))

    def test_issue_certificate_auto_closes_for_test_and_production(self):
        auto_close = self.ns["terminal_auto_close"]
        for scope in ("test", "prod"):
            self.assertTrue(auto_close({"kind": "prod_action", "action": "issue_certificate",
                                        "scope": scope}))
        self.assertTrue(auto_close({"kind": "script", "script": "issue_certificat.sh"}))
        problem = self.ns["terminal_output_problem"]
        self.assertFalse(problem("Cert success.\nCertificate installed in /etc/ssl/certs/example.test_ecc"))
        self.assertTrue(problem("Warning: certificate renewal needs attention"))
        self.assertTrue(problem("Error: certificate issue failed"))

    def test_acme_informational_error_mentions_are_not_failures(self):
        problem = self.ns["terminal_output_problem"]
        output = (
            "[Fri Nov  6 09:51:45 CET 2026] Checking if there is an error in the Apache config file before starting.\r\n"
            "Syntax OK\r\n"
            "[Fri Nov  6 09:51:45 CET 2026] In case an error causes it to not be restored automatically, you can restore it yourself.\r\n"
            "[Fri Nov  6 09:51:49 CET 2026] Cert success.\r\n"
            "[Fri Nov  6 09:51:50 CET 2026] Reload successful\r\n"
            "Certificate installed in /etc/ssl/certs/example.test_ecc\r\n"
        )
        self.assertFalse(problem(output))
        for line in output.splitlines():
            self.assertFalse(problem(line), line)
        for failure in ("[Fri Nov  6 09:51:45 CET 2026] Error in Apache config",
                        "Syntax error on line 10", "Warning: certificate installation incomplete",
                        "Certificate reload failed"):
            self.assertTrue(problem(output + failure), failure)
        self.assertTrue(problem(output.replace(
            "Checking if there is an error in the Apache config file before starting.",
            "Checking if there is an error in the Apache config file before starting. ERROR: invalid config")))

    def test_problem_survives_replay_buffer_truncation_and_split_chunks(self):
        cls = self.ns["TermSession"]
        for output, expected in (
            ([b"\x1b[3", b"1merr", b"or\x1b[0m: failed\n", b"ok\n" * 2048], True),
            ([b"Build succeeded.\n0 War", b"ning(s)\n0 Error(s)\n"], False),
            ([b"Done\nFatal exception"], True),
        ):
            with self.subTest(expected=expected):
                session = cls.__new__(cls)
                session.id, session.title, session.auto_close = "test", "Create site", True
                session.sites = ["example.test"]
                session.scope = "main"
                session.output_tail, session.output_problem = "", False
                session.update_root = None
                session.buffer, session.lock, session.subscribers = bytearray(), threading.Lock(), set()
                session.proc = SimpleNamespace(fd=1, wait=Mock(), exitstatus=0)
                self.ns["os"].read.side_effect = output + [b""]
                session._read()
                self.assertEqual(session.output_problem, expected)
                self.assertLessEqual(len(session.buffer), self.ns["BUFFER_MAX"])
                metadata = self.ns["describe"](session)
                self.assertTrue(metadata["auto_close"])
                self.assertEqual(metadata["output_problem"], expected)
                self.assertEqual(metadata["exit"], 0)
                self.assertEqual(metadata["sites"], ["example.test"])
                self.assertEqual(metadata["scope"], "main")

    def test_outdated_output_updates_shared_flag_after_completion(self):
        record = Mock()
        self.ns["_publish_module"] = SimpleNamespace(record=record)
        for output, exit_code, expected in (
            ([b"No outdated depen", b"dencies were detected\n"], 0, "current"),
            ([b"Example 1.0 -> 2.0\n", b"No upgrade performed.\n"], 0, "available"),
            ([b"error: feed unavailable\n"], 1, "error"),
        ):
            with self.subTest(expected=expected):
                session = self.ns["TermSession"].__new__(self.ns["TermSession"])
                session.output_tail, session.output_problem = "", False
                session.update_root, session.update_fingerprint = "/var/www/example.test", "before-upgrade"
                session.no_outdated = False
                session.buffer, session.lock, session.subscribers = bytearray(), threading.Lock(), set()
                session.proc = SimpleNamespace(fd=1, wait=Mock(), exitstatus=exit_code)
                self.ns["os"].read.side_effect = output + [b""]
                session._read()
                record.assert_called_with("/var/www/example.test", "updates", "before-upgrade", status=expected)

    def test_browser_closes_only_clean_successful_mutations(self):
        source = (APP / "static/app.js").read_text()
        start = source.index("function connectTerminal(t)")
        function = source[start:source.index("\nfunction openTerminal(", start)]
        test = r"""
const assert = require("node:assert/strict");
const location = {protocol: "https:", host: "admin.test"};
class WebSocket { constructor() { this.send = () => {}; } }
const terminals = {};
const activeTerminal = null;
const run = async () => {};
const loadSites = () => {};
const updateSiteActivity = () => {};
"""
        test += function + r"""
for (const [autoClose, exit, problem, expected] of [
    [true, 0, false, true], [true, 1, false, false],
    [true, 0, true, false], [false, 0, false, false],
    [true, 0, undefined, false]
]) {
    let removed = false;
    const writes = [];
    const t = {id: "test", autoClose, title: {textContent: "Create site"},
        term: {write: value => writes.push(value)}, remove: () => {removed = true;}};
    connectTerminal(t);
    t.ws.onmessage({data: JSON.stringify({x: exit, output_problem: problem})});
    assert.equal(removed, expected);
    assert.equal(t.exited, true);
    assert.equal(writes.some(text => text.includes("Process exited")), !expected);
}
"""
        result = subprocess.run(["node", "-e", test], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_keyboard_closes_any_finished_terminal_only(self):
        source = (APP / "static/app.js").read_text()
        start = source.index("term.attachCustomKeyEventHandler(")
        handler = source[start:source.index("\n\tterm.onData", start)]
        test = """
const assert = require("node:assert/strict");
let callback, closed;
const term = {attachCustomKeyEventHandler: fn => {callback = fn;}};
const closeDock = () => {closed++;};
const t = {};
""" + handler + """
for (const exited of [false, true]) for (const exitCode of [0, 1, -9]) {
    Object.assign(t, {exited, exitCode});
    for (const key of ["Escape", "Enter", "a"]) {
        closed = 0;
        const handled = exited && key !== "a";
        assert.equal(callback({type:"keydown", key}), !handled);
        assert.equal(closed, handled ? 1 : 0);
        callback({type:"keyup", key});
        assert.equal(closed, handled ? 1 : 0);
    }
}
"""
        result = subprocess.run(["node", "-e", test], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
