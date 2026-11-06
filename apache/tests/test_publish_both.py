import ast
import glob
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from flask import abort
from werkzeug.exceptions import BadRequest


APP = Path(__file__).resolve().parents[1] / "solutionfiles/admin/app"


class PublishBothTests(unittest.TestCase):
    def setUp(self):
        source = ast.parse((APP / "app.py").read_text())
        nodes = [node for node in source.body
                 if isinstance(node, ast.FunctionDef) and node.name == "build_command"]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.scripts = self.root / "example.test/scripts"
        self.scripts.mkdir(parents=True)
        (self.scripts.parent / "Example.csproj").write_text("<Project />")
        self.ns = {"os": os, "abort": abort, "WEB_ROOT": str(self.root),
                   "glob": glob, "sys": sys, "APACHE_DIR": str(APP.parents[2]),
                   "SCRIPT_RE": re.compile(r"^[A-Za-z0-9_.-]+\.sh$"),
                   "site_arg": lambda data: "example.test"}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "publish both", "exec"), self.ns)

    def command(self):
        return self.ns["build_command"]({"kind": "publish_both", "site": "example.test"})

    def test_runs_test_then_production(self):
        for local in ("publish_local.sh", "publish.sh"):
            with self.subTest(local=local):
                (self.scripts / local).write_text('echo test >> order\n')
                (self.scripts / "publish_prod.sh").write_text('echo production >> order\n')
                title, argv, cwd = self.command()
                self.assertEqual(title, "Publish both: example.test")
                subprocess.run(argv, cwd=cwd, check=True, capture_output=True)
                self.assertEqual((self.scripts.parent / "order").read_text(), "test\nproduction\n")
                (self.scripts / local).unlink()
                (self.scripts.parent / "order").unlink()

    def test_test_failure_skips_production_and_preserves_exit_code(self):
        (self.scripts / "publish_local.sh").write_text("exit 7\n")
        (self.scripts / "publish_prod.sh").write_text("touch production-ran\n")
        _, argv, cwd = self.command()
        result = subprocess.run(argv, cwd=cwd, capture_output=True)
        self.assertEqual(result.returncode, 7)
        self.assertFalse((self.scripts.parent / "production-ran").exists())

    def test_production_failure_preserves_exit_code(self):
        (self.scripts / "publish_local.sh").write_text("exit 0\n")
        (self.scripts / "publish_prod.sh").write_text("exit 9\n")
        _, argv, cwd = self.command()
        self.assertEqual(subprocess.run(argv, cwd=cwd, capture_output=True).returncode, 9)

    def test_existing_individual_scripts_are_tracked_through_admin(self):
        for script, scope in (("publish.sh", "test"), ("publish_local.sh", "test"), ("publish_prod.sh", "prod")):
            with self.subTest(script=script):
                (self.scripts / script).write_text("exit 0\n")
                _, argv, cwd = self.ns["build_command"](
                    {"kind": "script", "site": "example.test", "script": script})
                subprocess.run(argv, cwd=cwd, check=True, capture_output=True)
                self.assertTrue((self.scripts.parent / ".vns-publish" / (scope + ".json")).is_file())

    def test_missing_or_escaping_scripts_are_rejected(self):
        with self.assertRaises(BadRequest):
            self.command()
        (self.scripts / "publish_local.sh").write_text("exit 0\n")
        with self.assertRaises(BadRequest):
            self.command()
        outside = self.root / "outside.sh"
        outside.write_text("exit 0\n")
        (self.scripts / "publish_prod.sh").symlink_to(outside)
        with self.assertRaises(BadRequest):
            self.command()

    def test_button_visibility_confirmation_and_task(self):
        source = (APP / "static/app.js").read_text()
        render = source[source.index("function renderSite(name) {"):source.index("// Scripts that have their own button")]
        harness = r"""
const assert = require("node:assert/strict");
const nodes = {};
function el(tag, props = {}, ...children) {
    return Object.assign({children, append(...items) {this.children.push(...items);},
        replaceChildren() {this.children = [];}}, props);
}
const $ = selector => nodes[selector] ||= el("div");
let allSites = [], sitesLoaded = true, confirmed = false, tasks = [], prompts = [];
const buttonScripts = new Set(["publish_local.sh", "publish.sh", "publish_prod.sh"]);
const hasProd = site => site.prod;
const kindBadges = () => el("span");
const decorate = () => {};
const envPanel = () => el("div");
const run = handler => handler();
const ask = async options => {prompts.push(options); return confirmed;};
const openTask = async payload => tasks.push(payload);
(async () => {
    for (const mode of ["local", "remote"]) {
        for (const local of ["publish_local.sh", "publish.sh"]) {
            const site = {name: "example.test", kind: "csharp", prod: true,
                scripts: [local, "publish_prod.sh"], environments: [{key: "prod", mode}]};
            allSites = [site];
            renderSite(site.name);
            const actions = $("#site-head").children[1].children;
            const button = actions.find(item => item.textContent === "Publish both");
            assert.ok(button);
            tasks = [];
            confirmed = false;
            await button.onclick();
            assert.equal(tasks.length, 0);
            assert.equal(prompts.at(-1).title, "Publish both?");
            confirmed = true;
            await button.onclick();
            assert.deepEqual(tasks, [{kind: "publish_both", site: site.name}]);
        }
    }
    for (const [prod, scripts] of [
        [false, ["publish.sh"]], [true, ["publish_prod.sh"]],
        [true, ["publish_local.sh"]]
    ]) {
        allSites = [{name: "example.test", kind: "csharp", prod, scripts, environments: []}];
        renderSite("example.test");
        assert.ok(!$("#site-head").children[1].children.some(item => item.textContent === "Publish both"));
    }
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
        subprocess.run(["node", "-e", render + harness], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
