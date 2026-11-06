import ast
import glob
import json
import logging
import os
from pathlib import Path
import re
import shlex
import tempfile
import unittest
from types import SimpleNamespace


APP = Path(__file__).resolve().parents[1] / "solutionfiles/admin/app/app.py"


class PublishedMetadataTests(unittest.TestCase):
    def setUp(self):
        nodes = [node for node in ast.parse(APP.read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name == "published_project_info"]
        namespace = dict(glob=glob, json=json, os=os, re=re, shlex=shlex,
                         app=SimpleNamespace(logger=logging.getLogger(__name__)))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(APP), "exec"), namespace)
        self.read = namespace["published_project_info"]

    def test_published_umbraco_without_csproj(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "Example.deps.json"
            path.write_text(json.dumps({"runtimeTarget": {"name": ".NETCoreApp,Version=v8.0"},
                                        "libraries": {"Example/1.0.0": {}, "Umbraco.Cms/13.13.0": {}}}))
            self.assertEqual(self.read(folder, ""), {"kind": "umbraco", "umbraco_version": "13.13.0"})

    def test_service_selects_application_not_other_dependencies(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "Tool.deps.json").write_text('{"libraries":{"Umbraco.Cms/18.0.0":{}}}')
            publish = root / "_publish"
            publish.mkdir()
            (publish / "Example.deps.json").write_text('{"libraries":{"Example/1.0.0":{}}}')
            service = 'ExecStart=/usr/lib/dotnet/dotnet "%s" --urls http://localhost:4001' % (publish / "Example.dll")
            self.assertEqual(self.read(folder, service), {"kind": "csharp", "umbraco_version": ""})

    def test_html_has_no_published_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(self.read(folder, ""))

    def test_invalid_metadata_is_logged(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "Example.deps.json").write_text("{")
            with self.assertLogs(__name__, level="WARNING"):
                self.assertIsNone(self.read(folder, ""))


if __name__ == "__main__":
    unittest.main()
