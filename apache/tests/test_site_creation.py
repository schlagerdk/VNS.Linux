import ast
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest

from flask import abort
from werkzeug.exceptions import BadRequest


APACHE = Path(__file__).resolve().parents[1]
APP = APACHE / "solutionfiles/admin/app"
CREATE = (APACHE / "create_site.sh").read_text()


def shell_function(name):
    return re.search(r"^" + name + r"\(\) \{.*?^\}", CREATE, re.M | re.S).group()


class CreateCommandTests(unittest.TestCase):
    def setUp(self):
        source = ast.parse((APP / "app.py").read_text())
        nodes = [node for node in source.body if
                 isinstance(node, ast.FunctionDef) and node.name == "build_command" or
                 isinstance(node, ast.Assign) and any(
                     isinstance(target, ast.Name) and target.id in
                     ("NAME_RE", "BINDING_RE", "PROJECT_RE", "FOLDER_RE", "VERSION_RE")
                     for target in node.targets)]
        self.ns = {"re": re, "os": os, "abort": abort, "APACHE_DIR": str(APACHE),
                   "site_dirs": lambda: []}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "create command", "exec"), self.ns)
        self.data = dict(kind="create_site", site="example.test", binding="example.test",
                         type="csharp", csharp="empty", certificate="n")

    def command(self, **changes):
        return self.ns["build_command"](self.data | changes)[1]

    def test_custom_template_is_a_separate_argument(self):
        args = self.command(template="webapi", version="18.3.1")
        self.assertEqual(args[2:], ["example.test", "csharp", "empty", "",
                                   "example.test", "example.test", "_publish", "Debug", "n", "webapi", "Test"])

    def test_blank_template_preserves_default(self):
        self.assertEqual(self.command(template="  ")[-2], "")

    def test_umbraco_ignores_hidden_template(self):
        self.assertEqual(self.command(csharp="umbraco", version="13.3.2", template="bad name")[-2], "")

    def test_environment_is_independent_of_build(self):
        for build in ("Debug", "Release"):
            for environment in ("Development", "Test", "Staging", "Production"):
                with self.subTest(build=build, environment=environment):
                    self.assertEqual(self.command(build=build, environment=environment)[-1], environment)
        self.assertEqual(self.command(build="Release")[-1], "Production")
        with self.assertRaises(BadRequest):
            self.command(environment="invalid")

    def test_invalid_templates_are_rejected(self):
        for value in ("--help", "../web", "web api", "web;echo", "web\napi"):
            with self.subTest(value=value), self.assertRaises(BadRequest):
                self.command(template=value)

    def test_html_command_is_unchanged(self):
        self.assertEqual(self.command(type="html")[-1], "n")


class CreateScriptTests(unittest.TestCase):
    def run_creation(self, kind="empty", version="18.3.1", template="", extra=""):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "_publish").mkdir()
            script = """
set -euo pipefail
_site=example.test
_siteRoot=ROOT
_apacheDir=ROOT
_solutionFolder=/templates
_projectName=Example
_publishFolder=_publish
_publishArg=_publish
_buildConfig=Debug
_createCertificate=n
_templateProvided=y
_dotNetVersion=net10.0
_defaultUmbracoVersion=18.3.1
_defaultUmbraco13Version=13.16.4
ask() { :; }
validate_publish_folder() { :; }
allocate_ports() { :; }
install_scripts() { :; }
exclude_from_build() { :; }
configure_project_dotnet() { echo "configure:$1"; }
install_apache_config() { :; }
install_service() { :; }
ensure_dotnet_channel() { echo "ensure:$1"; }
create_umbraco_project() { echo "umbraco:${_dotNetVersion}:${_umbracoVersion}"; }
sudo() {
    printf 'sudo:'
    printf ' <%s>' "$@"
    printf '\\n'
    if [[ $1 == tee ]]; then
        cat > "$2"
    elif [[ $1 == dotnet && $2 == new ]]; then
        touch "${_siteRoot}/${_projectName}.csproj"
    fi
}
""".replace("ROOT", shlex.quote(directory))
            script += "\n_csharpKind=" + shlex.quote(kind)
            script += "\n_umbracoVersion=" + shlex.quote(version)
            script += "\n_templateName=" + shlex.quote(template)
            script += "\n" + shell_function("create_csharp") + "\n" + extra + "\ncreate_csharp\n"
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            global_file = root / "global.json"
            sdk = json.loads(global_file.read_text()) if global_file.exists() else None
            return result, sdk

    def test_empty_defaults_to_web(self):
        result, sdk = self.run_creation()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<dotnet> <new> <web> <--name> <Example> <--output> <.>", result.stdout)
        self.assertIsNone(sdk)

    def test_custom_template_runs_in_site_folder(self):
        result, _ = self.run_creation(template="webapi",
                                      extra='exclude_from_build() { [[ $PWD == "$_siteRoot" ]]; }')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<dotnet> <new> <webapi>", result.stdout)
        self.assertIn("<dotnet> <publish> <", result.stdout)
        self.assertIn("/Example.csproj> <-c>", result.stdout)
        self.assertIn("configure:", result.stdout)

    def test_invalid_template_fails_before_creating_site(self):
        result, _ = self.run_creation(template="--help")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid template name", result.stderr)
        self.assertNotIn("sudo:", result.stdout)

    def test_umbraco13_uses_sdk_and_target_net8(self):
        for version in ("13", "13.3.2", "13.12.0"):
            with self.subTest(version=version):
                result, sdk = self.run_creation(kind="umbraco", version=version)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("ensure:8.0", result.stdout)
                self.assertIn("umbraco:net8.0:", result.stdout)
                self.assertEqual(sdk["sdk"]["version"], "8.0.100")
                self.assertEqual(sdk["sdk"]["rollForward"], "latestFeature")

    def test_unsupported_umbraco13_fails_explicitly(self):
        for version in ("13.0.0", "13.3.1", "13.invalid"):
            with self.subTest(version=version):
                result, _ = self.run_creation(kind="umbraco", version=version)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("13.3.2 or newer", result.stderr)

    def test_modern_umbraco_keeps_net10(self):
        result, sdk = self.run_creation(kind="umbraco")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("umbraco:net10.0:18.3.1", result.stdout)
        self.assertNotIn("ensure:8.0", result.stdout)
        self.assertIsNone(sdk)

    def test_environment_reaches_the_service_template(self):
        for environment in ("Development", "Test", "Staging", "Production"):
            with self.subTest(environment=environment):
                result, _ = self.run_creation(extra=(
                    "_environmentArg=" + environment +
                    '\ninstall_service() { echo "service-environment:${_environment}"; }'))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("service-environment:" + environment, result.stdout)
        result, _ = self.run_creation(extra="_environmentArg=invalid")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Environment must be", result.stderr)

    def test_umbraco_packages_match_the_runtime(self):
        for version, target in (("13.3.2", "net8.0"), ("18.3.1", "net10.0")):
            with self.subTest(version=version):
                script = """
set -euo pipefail
_solutionFolder=/templates
_siteRoot=/site
_project=/site/Example.csproj
_projectName=Example
_publishFolder=_publish
_buildConfig=Debug
_binding=example.test
_createCertificate=y
sudo() { printf '<%s> ' "$@"; printf '\\n'; }
uuidgen() { echo test; }
exclude_from_build() { :; }
"""
                script += "\n_umbracoVersion=" + version + "\n_dotNetVersion=" + target
                script += "\n" + shell_function("create_umbraco_project") + "\ncreate_umbraco_project\n"
                result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("s|DotNetVersion|" + target + "|g", result.stdout)
                if target == "net8.0":
                    self.assertIn("<Umbraco.Templates::13.3.2>", result.stdout)
                    self.assertNotIn("Umbraco.Cms.DevelopmentMode.Backoffice", result.stdout)
                    for package in ("VNS.Umbraco", "uSync"):
                        self.assertIn("<package> <" + package + "> <-v> <13.0.0>", result.stdout)
                else:
                    self.assertIn("<Umbraco.Templates@18.3.1>", result.stdout)
                    self.assertIn("<Umbraco.Cms.DevelopmentMode.Backoffice> <-v> <18.3.1>", result.stdout)


class DotnetInstallationTests(unittest.TestCase):
    def run_installer(self, runtime):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = root / "dotnet"
            host.write_text('#!/bin/bash\ncase "$1" in\n'
                            '--list-sdks) echo "8.0.100 [/sdk]" ;;\n'
                            '--list-runtimes) echo ' + shlex.quote(runtime) + ' ;;\nesac\n')
            host.chmod(0o700)
            helper = (APACHE / "lib/dotnet.sh").read_text().replace(
                "_dir=/usr/lib/dotnet", "_dir=" + shlex.quote(directory))
            script = helper + """
sudo() { printf 'sudo:'; printf ' <%s>' "$@"; printf '\\n'; }
curl() { echo download; }
ensure_dotnet_channel 8.0
"""
            return subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    def test_existing_sdk_and_runtime_are_kept(self):
        result = self.run_installer("Microsoft.AspNetCore.App 8.0.0 [/shared]")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("download", result.stdout)

    def test_missing_aspnet_runtime_installs_both_components(self):
        result = self.run_installer("Microsoft.NETCore.App 8.0.0 [/shared]")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("download", result.stdout)
        self.assertIn("<--channel> <8.0> <--install-dir>", result.stdout)
        self.assertIn("<--channel> <8.0> <--runtime> <aspnetcore>", result.stdout)


class ProjectRuntimeTests(unittest.TestCase):
    def test_net8_templates_install_and_select_net8(self):
        helper = (APACHE / "lib/dotnet.sh").read_text()
        for framework in ("net8.0", "net10.0"):
            with self.subTest(framework=framework), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project = root / "example-site.csproj"
                project.write_text("<Project><PropertyGroup><TargetFramework>" + framework +
                                   "</TargetFramework></PropertyGroup></Project>")
                script = helper + """
set -euo pipefail
ensure_dotnet_channel() { echo "ensure:$1"; }
sudo() { "$@"; }
configure_project_dotnet "$1"
"""
                result = subprocess.run(["bash", "-c", script, "test", str(project)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                if framework == "net8.0":
                    self.assertIn("ensure:8.0", result.stdout)
                    self.assertEqual(json.loads((root / "global.json").read_text())["sdk"]["version"],
                                     "8.0.100")
                else:
                    self.assertNotIn("ensure:", result.stdout)
                    self.assertFalse((root / "global.json").exists())

    def test_all_publish_paths_select_the_project(self):
        for path in ("scripts/publish.sh", "prod-scripts/publish_prod.sh"):
            text = (APACHE / "solutionfiles/csharp" / path).read_text()
            self.assertIn('dotnet publish "${_projectName}.csproj"', text)


if __name__ == "__main__":
    unittest.main()
