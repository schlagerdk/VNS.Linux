from pathlib import Path
import subprocess
import unittest


class EditorModelTests(unittest.TestCase):
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
