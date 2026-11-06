from pathlib import Path
import subprocess
import unittest


class EditorModelTests(unittest.TestCase):
    def test_configuration_encoding_choice_and_conversion_confirmation(self):
        source = (Path(__file__).resolve().parents[1] /
                  "solutionfiles/admin/app/static/app.js").read_text()
        start = source.index("async function editConfiguration(")
        functions = source[start:source.index("\nasync function createItem(", start)]
        script = """
const assert = require("node:assert/strict");
let selection = "cp1252", confirmation = true, configurationEditor = null, openFile = null;
let savingFile = false, calls = [], notices = [], shown = null, prompts = [], dirty = false;
const nodes = new Map();
const $ = key => {
    if (!nodes.has(key)) nodes.set(key, {});
    return nodes.get(key);
};
$("#config-encoding").value = "cp1252";
$("#dlg-encoding").showModal = function() {this.returnValue = selection ? "open" : "cancel"; this.onclose();};
const editorDialog = {open: false, showModal() {this.open = true;}};
const discardOk = async () => true;
const showInEditor = async (path, content) => {shown = content;};
const setDirty = value => {dirty = value;};
const editorValue = () => shown;
const ask = async options => {prompts.push(options); return confirmation;};
const toast = message => notices.push(message);
const loadSites = async () => {};
const api = async (url, options) => {
    calls.push({url, options});
    if (!options) return url.includes("encoding=")
        ? {path: "/etc/example.service", content: "Decoded text", encoding: "cp1252"}
        : {encoding_required: true, message: "Not UTF-8"};
    return {ok: true};
};
""" + functions + """
(async () => {
    const site = {name:"example.test"}, env = {unit:"example.test",mode:"local"};
    await editConfiguration(site, env, "service");
    assert.equal(calls.length, 2);
    assert.ok(calls[1].url.includes("encoding=cp1252"));
    assert.equal(shown, "Decoded text");
    assert.ok($("#editor-status").textContent.includes("Save converts"));
    assert.equal(configurationEditor.encoding, "cp1252");
    confirmation = false;
    await saveFile();
    assert.equal(calls.length, 2);
    assert.equal(savingFile, false);
    assert.equal(configurationEditor.encoding, "cp1252");
    confirmation = true;
    await saveFile();
    assert.equal(calls[2].options.json.content, "Decoded text");
    assert.equal(configurationEditor.encoding, "utf-8");
    assert.equal(prompts[1].ok, "Save as UTF-8");
    calls = []; shown = null; selection = null;
    await editConfiguration(site, env, "service");
    assert.equal(calls.length, 1);
    assert.equal(shown, null);
    assert.equal(notices.length, 0);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reopening_files_disposes_previous_model_before_creation(self):
        source = (Path(__file__).resolve().parents[1] /
                  "solutionfiles/admin/app/static/app.js").read_text()
        start = source.index("async function showInEditor(")
        function = source[start:source.index("\nconst editorValue", start)]
        script = """
const assert = require("node:assert/strict");
const models = new Map();
let current = null, loadingContent = false, dirty = false, monacoEditor = null;
const nodes = {"#editor-box": {}, "#editor-text": {}};
const $ = key => nodes[key];
const languageFor = () => "json";
const setDirty = value => {dirty = value;};
const run = () => {};
const saveFile = () => {};
const editor = {
    getModel: () => current,
    setModel: model => {current = model;},
    addCommand() {},
    focus() {}
};
const monaco = {
    KeyMod: {CtrlCmd: 1}, KeyCode: {KeyS: 2},
    Uri: {file: path => path},
    editor: {
        create() {
            current = {dispose() {this.disposed = true;}};
            return editor;
        },
        createModel(content, language, uri) {
            if (models.has(uri)) throw Error("ModelService: Cannot add model because it already exists!");
            const model = {
                content, language, uri,
                dispose() {models.delete(uri); this.disposed = true;},
                onDidChangeContent(callback) {this.change = callback;}
            };
            models.set(uri, model);
            return model;
        }
    }
};
const loadMonaco = async () => monaco;
""" + function + """
(async () => {
    for (const [path, content] of [
        ["site/appsettings.json", "first"],
        ["site/appsettings.json", "updated"],
        ["other/Program.cs", "other"],
        ["site/appsettings.json", "remote content"]
    ]) {
        const previous = current;
        await showInEditor(path, content);
        if (previous) assert.equal(previous.disposed, true);
        assert.equal(current.content, content);
        assert.equal(models.size, 1);
        assert.equal(loadingContent, false);
    }
    await Promise.all([
        showInEditor("site/appsettings.json", "one"),
        showInEditor("site/appsettings.json", "two")
    ]);
    assert.equal(current.content, "two");
    assert.equal(models.size, 1);
    current.change();
    assert.equal(dirty, true);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
