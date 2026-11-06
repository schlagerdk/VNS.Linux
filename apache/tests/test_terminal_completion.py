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
        names = {"terminal_auto_close", "terminal_output_problem", "TermSession", "describe"}
        nodes = [node for node in ast.parse((APP / "app.py").read_text()).body
                 if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
        self.ns = {"re": re, "codecs": codecs, "BUFFER_MAX": 1024, "REMOTE_STATUS": {},
                   "os": SimpleNamespace(read=Mock())}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "terminal completion", "exec"), self.ns)

    def test_mutations_close_and_diagnostics_remain(self):
        auto_close = self.ns["terminal_auto_close"]
        for kind in ("create_site", "delete_site", "create_prod", "change_binding", "prod_action",
                     "shell", "dotnet_package"):
            self.assertTrue(auto_close({"kind": kind}), kind)
        for script in ("publish.sh", "publish_local.sh", "publish_prod.sh", "sync_prod.sh",
                       "change_env.sh", "clear_logs.sh"):
            self.assertTrue(auto_close({"kind": "script", "script": script}), script)
        for script in ("check_service.sh", "outdated.sh", "custom.sh", "edit_conf.sh"):
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
                session.output_tail, session.output_problem = "", False
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


if __name__ == "__main__":
    unittest.main()
