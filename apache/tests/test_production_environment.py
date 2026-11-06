import ast
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from flask import abort
from werkzeug.exceptions import BadRequest


APACHE = Path(__file__).resolve().parents[1]


class ProductionEnvironmentTests(unittest.TestCase):
    def test_modal_environment_for_local_and_remote_targets(self):
        source = (APACHE / "solutionfiles/admin/app/app.py").read_text()
        nodes = [node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef) and node.name == "production_setup_env"]
        with tempfile.TemporaryDirectory() as directory:
            scripts = Path(directory) / "example.test/scripts"
            scripts.mkdir(parents=True)
            (scripts / "create_prod.sh").touch()
            ns = {"os": os, "re": re, "abort": abort, "WEB_ROOT": directory,
                  "NAME_RE": re.compile(r"^[A-Za-z0-9.-]+$"),
                  "BINDING_RE": re.compile(r"^[A-Za-z0-9.-]+$"),
                  "conf_binding": lambda site: "example.test"}
            exec(compile(ast.Module(body=nodes, type_ignores=[]), "production env", "exec"), ns)
            for target in ("local", "remote"):
                data = dict(target=target, folder="prod.test", binding="prod.test",
                            local_path=str(scripts.parent / "_prod"),
                            host="server.test", user="root", ssh_port="22")
                for environment in ("Development", "Test", "Staging", "Production"):
                    result = ns["production_setup_env"](data | {"environment": environment}, "example.test")
                    self.assertEqual(result["VNS_PROD_ENVIRONMENT"], environment)
                self.assertEqual(ns["production_setup_env"](data, "example.test")["VNS_PROD_ENVIRONMENT"],
                                 "Production")
                with self.assertRaises(BadRequest):
                    ns["production_setup_env"](data | {"environment": "invalid"}, "example.test")

    def test_rendered_publish_script_preserves_selected_environment(self):
        create = (APACHE / "solutionfiles/csharp/scripts/create_prod.sh").read_text()
        self.assertIn("R_PROD_ENVIRONMENT=\"${_prodEnvironment}\"", create)
        template = APACHE / "solutionfiles/csharp/prod-scripts/publish_prod.sh"
        for environment in ("Development", "Test", "Staging", "Production"):
            result = subprocess.run(
                ["perl", "-pe", 's/\\@([A-Z_]+)\\@/exists $ENV{"R_$1"} ? $ENV{"R_$1"} : "\\@$1\\@"/ge',
                 str(template)], env=os.environ | {"R_PROD_ENVIRONMENT": environment},
                capture_output=True, text=True, check=True)
            self.assertIn('PROD_ENVIRONMENT="' + environment + '"', result.stdout)
            self.assertIn("ENVIRONMENT='${PROD_ENVIRONMENT}'", result.stdout)
            self.assertIn("Environment=ASPNETCORE_ENVIRONMENT=${ENVIRONMENT}", result.stdout)
            self.assertIn("Environment=DOTNET_ENVIRONMENT=${ENVIRONMENT}", result.stdout)


if __name__ == "__main__":
    unittest.main()
