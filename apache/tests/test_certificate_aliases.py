from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


APACHE = Path(__file__).resolve().parents[1]


def shell_function(source, name):
    start = source.index(name + "() {")
    return source[start:source.index("\n}", start) + 2]


class CertificateAliasTests(unittest.TestCase):
    def test_binding_changes_preserve_http_and_https_modes(self):
        for production in (False, True):
            for https in (False, True):
                for primary_changed in (False, True):
                    with self.subTest(production=production, https=https, primary_changed=primary_changed), tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        for folder in ("lib", "available", "enabled", "services", "certs", "bin", "sites/example.test/_publish"):
                            (root / folder).mkdir(parents=True, exist_ok=True)

                        def fixture_source(text):
                            for original, replacement in (
                                ("/etc/apache2/sites-available", root / "available"),
                                ("/etc/apache2/sites-enabled", root / "enabled"),
                                ("/etc/systemd/system", root / "services"),
                                ("/etc/ssl/certs", root / "certs"),
                                ("/etc/acmesh/acme.sh", root / "bin/acme"),
                                ("/var/www", root / "sites"),
                                ("/vns/apache/lib/common.sh", root / "lib/common.sh"),
                            ):
                                text = text.replace(original, str(replacement))
                            if sys.platform == "darwin":
                                text = re.sub(r"\$\{(\w+),,\}", r"""$(printf '%s' "${\1}" | tr '[:upper:]' '[:lower:]')""", text)
                                text = text.replace("sed -i ", "sed -i '' ")
                            return text

                        (root / "lib/common.sh").write_text(fixture_source((APACHE / "lib/common.sh").read_text()))
                        templates = root / "solutionfiles/common"
                        templates.mkdir(parents=True)
                        shutil.copy(APACHE / "solutionfiles/common/apache-temp.conf.tpl", templates)
                        settings = root / "sites/example.test/_publish/appsettings.json"
                        scheme = "https" if https else "http"
                        settings.write_text('{"UmbracoApplicationUrl":"' + scheme + '://example.test"}')
                        certificate = root / "certs/example.test_ecc"
                        if https:
                            certificate.mkdir()
                            (certificate / "fullchain.cer").write_text("old cert")
                            (certificate / "example.test.key").write_text("old key")
                        config = root / "available/example.test.conf"
                        config.write_text(
                            "<VirtualHost *:" + ("443" if https else "80") + ">\n"
                            "ServerName example.test\nServerAlias old.example.test\n"
                            "ProxyPass / http://localhost:4003/\n"
                            + (f"SSLEngine On\nSSLCertificateFile {certificate}/fullchain.cer\n"
                               f"SSLCertificateKeyFile {certificate}/example.test.key\n" if https else "")
                            + "</VirtualHost>\n")
                        (root / "enabled/example.test.conf").symlink_to(config)
                        (root / "services/example.test.service").write_text(
                            "WorkingDirectory=" + str(settings.parent) + "\n")
                        for name, body in (
                            ("sudo", 'exec "$@"'),
                            ("apache2ctl", 'echo "configtest" >> "$CALLS"'),
                            ("systemctl", 'echo "systemctl:$*" >> "$CALLS"'),
                            ("acme", 'echo "acme:$*" >> "$CALLS"\n'
                             '[[ "$1" != --issue || "${FAIL_CERT:-0}" != 1 ]] || exit 1\n'
                             'while (($#)); do\n'
                             'case "$1" in --key-file|--fullchain-file) shift; mkdir -p "$(dirname "$1")"; echo installed > "$1";; esac\n'
                             'shift\ndone'),
                        ):
                            command = root / "bin" / name
                            command.write_text("#!/bin/bash\n" + body + "\n")
                            command.chmod(0o700)
                        new = "new.example.test" if primary_changed else "example.test"
                        if production:
                            source = (APACHE / "change_binding_prod.sh").read_text().split("<<'REMOTE'\n", 1)[1].split("\nREMOTE", 1)[0]
                            args = []
                        else:
                            source = (APACHE / "change_binding.sh").read_text()
                            args = ["example.test", new, "api.example.test"]
                        script = root / "change.sh"
                        script.write_text(fixture_source(source))
                        calls = root / "calls"
                        env = os.environ | {
                            "PATH": str(root / "bin") + ":" + os.environ["PATH"], "CALLS": str(calls),
                            "SITE": "example.test", "OLD": "example.test", "NEW": new,
                            "ALIASES": "api.example.test", "ROOT": str(settings.parent),
                            "ACME_SERVER": "letsencrypt", "MAIL": "admin@example.test", "UMBRACO": "1",
                        }
                        if https:
                            original_config = config.read_text()
                            original_settings = settings.read_text()
                            result = subprocess.run(
                                ["bash", str(script), *args], input="y\n", env=env | {"FAIL_CERT": "1"},
                                capture_output=True, text=True)
                            self.assertNotEqual(result.returncode, 0)
                            self.assertEqual(config.read_text(), original_config)
                            self.assertEqual(settings.read_text(), original_settings)
                            self.assertEqual((certificate / "fullchain.cer").read_text(), "old cert")
                            self.assertEqual((certificate / "example.test.key").read_text(), "old key")
                            calls.unlink()
                        result = subprocess.run(["bash", str(script), *args], input="y\n", env=env,
                                                capture_output=True, text=True)
                        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                        self.assertIn("ServerName " + new, config.read_text())
                        self.assertIn("ServerAlias api.example.test", config.read_text())
                        self.assertNotIn("old.example.test", config.read_text())
                        self.assertIn(scheme + "://" + new, settings.read_text())
                        self.assertEqual("SSLEngine On" in config.read_text(), https)
                        actions = calls.read_text()
                        self.assertEqual("acme:--issue" in actions, https)
                        self.assertEqual("acme:--install-cert" in actions, https)
                        if not https:
                            self.assertNotIn("acme:", actions)
                            self.assertEqual(list((root / "certs").iterdir()), [])

    def run_domains(self, config, primary="Example.test"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "site.conf"
            path.write_text(config)
            common = (APACHE / "lib/common.sh").read_text()
            code = "\n".join(shell_function(common, name) for name in ("validate_binding", "set_certificate_domains"))
            code += '\nset -e\nset_certificate_domains "$1" "$2"\nprintf "names=%s\\n" "$_certificateDomainNames"\nprintf "args=%s\\n" "${_certificateDomainArgs[*]}"\nprintf "aliases=%s\\n" "${_certificateDomainAliases[*]}"\n'
            return subprocess.run(["bash", "-c", code, "test", str(path), primary],
                                  capture_output=True, text=True)

    def test_primary_and_all_aliases_are_normalized_deduplicated_and_comment_safe(self):
        result = self.run_domains(
            "ServerName example.test\n"
            "ServerAlias WWW.EXAMPLE.TEST api.example.test www.example.test # ignored.test\n"
            "<VirtualHost *:443>\nServerAlias api.example.test cdn.example.test\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("names=example.test www.example.test api.example.test cdn.example.test", result.stdout)
        self.assertIn("args=-d example.test -d www.example.test -d api.example.test -d cdn.example.test", result.stdout)
        self.assertIn("aliases=www.example.test api.example.test cdn.example.test", result.stdout)

    def test_invalid_and_wildcard_aliases_fail_before_acme(self):
        for alias in ("*.example.test", "https://example.test", "localhost", "-bad.example.test",
                      "bad..example.test", "a" * 64 + ".example.test"):
            with self.subTest(alias=alias):
                result = self.run_domains("ServerAlias " + alias + "\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("certificate issuance stopped", result.stderr)
                self.assertNotIn("args=", result.stdout)

    def test_http_only_production_config_is_upgraded_after_certificate_issue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "site.conf"
            config.write_text(
                "<VirtualHost *:80>\n"
                "  ServerName old.example.test\n"
                "  ServerAlias WWW.EXAMPLE.TEST api.example.test www.example.test\n"
                "</VirtualHost>\n")
            service = root / "site.service"
            service.write_text('ExecStart=/usr/bin/dotnet app.dll --urls "http://localhost:4003"\n')
            common = (APACHE / "lib/common.sh").read_text()
            functions = "\n".join(
                shell_function(common, name) for name in ("validate_binding", "ensure_production_tls_config"))
            command = (
                "set -euo pipefail\n" + functions
                + '\nensure_production_tls_config "$1" "$2" example.test example.test /srv/site '
                  '/etc/ssl/certs/example.test_ecc admin@example.test 1\n')
            result = subprocess.run(
                ["bash", "-c", command, "test", str(config), str(service)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            rewritten = config.read_text()
            self.assertEqual(rewritten.count("ServerAlias www.example.test api.example.test"), 2)
            self.assertIn("RewriteRule ^(.*)$ https://%{HTTP_HOST}%{REQUEST_URI}", rewritten)
            self.assertIn("ws://localhost:4003/$1", rewritten)
            self.assertIn("ProxyPass / http://localhost:4003/ nocanon", rewritten)
            self.assertIn("SSLCertificateFile /etc/ssl/certs/example.test_ecc/fullchain.cer", rewritten)
            self.assertIn("SSLCertificateKeyFile /etc/ssl/certs/example.test_ecc/example.test.key", rewritten)
            self.assertIn("ProxyPass /robots.txt http://localhost:4003/seo_robots_txt", rewritten)
            self.assertIn("ProxyPass /sitemap.xml http://localhost:4003/seo_sitemap_xml", rewritten)

    def test_tls_config_recovery_preserves_existing_custom_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "site.conf"
            config.write_text("<VirtualHost *:443>\n  SSLEngine On\n  CustomSetting kept\n</VirtualHost>\n")
            original = config.read_text()
            service = root / "site.service"
            service.write_text("ExecStart=dotnet app.dll --urls http://localhost:4003\n")
            common = (APACHE / "lib/common.sh").read_text()
            functions = "\n".join(
                shell_function(common, name) for name in ("validate_binding", "ensure_production_tls_config"))
            command = (
                "set -euo pipefail\n" + functions
                + '\nensure_production_tls_config "$1" "$2" example.test example.test /srv/site '
                  '/etc/ssl/certs/example.test_ecc admin@example.test 0\n')
            result = subprocess.run(
                ["bash", "-c", command, "test", str(config), str(service)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(config.read_text(), original)

    def test_alias_list_normalization_and_config_replacement(self):
        common = (APACHE / "lib/common.sh").read_text()
        functions = "\n".join(shell_function(common, name) for name in (
            "validate_binding", "normalize_server_aliases", "replace_server_aliases"))
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "site.conf"
            config.write_text(
                "<VirtualHost *:80>\n"
                "  ServerName example.test\n"
                "  ServerAlias OLD.example.test\n"
                "</VirtualHost>\n"
                "<VirtualHost *:443>\n"
                "  ServerName example.test\n"
                "  ServerAlias OLD.example.test\n"
                "</VirtualHost>\n")
            command = (
                "set -euo pipefail\n" + functions
                + '\nnormalize_server_aliases example.test "WWW.example.test, api.example.test www.example.test"\n'
                  'printf "aliases=%s\\n" "${_normalizedAliases}"\n'
                  'replace_server_aliases "$1" "${_normalizedAliases}"\n')
            result = subprocess.run(
                ["bash", "-c", command, "test", str(config)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("aliases=www.example.test api.example.test", result.stdout)
            self.assertEqual(config.read_text().count("ServerAlias www.example.test api.example.test"), 2)

            command = (
                "set -euo pipefail\n" + functions
                + '\nnormalize_server_aliases example.test ""\n'
                  'replace_server_aliases "$1" "${_normalizedAliases}"\n')
            result = subprocess.run(
                ["bash", "-c", command, "test", str(config)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("ServerAlias", config.read_text())

    def test_alias_normalization_rejects_invalid_domains(self):
        common = (APACHE / "lib/common.sh").read_text()
        functions = "\n".join(shell_function(common, name) for name in ("validate_binding", "normalize_server_aliases"))
        for alias in ("*.example.test", "https://example.test", "localhost", "-bad.example.test"):
            with self.subTest(alias=alias):
                result = subprocess.run(
                    ["bash", "-c", functions + '\nnormalize_server_aliases example.test "$1"', "test", alias],
                    capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Invalid alias", result.stderr)

    def test_aliases_are_used_by_every_supported_certificate_flow(self):
        common = (APACHE / "lib/common.sh").read_text()
        test_action = (APACHE / "test_action.sh").read_text()
        prod_action = (APACHE / "prod_action.sh").read_text()
        change_local = (APACHE / "change_binding.sh").read_text()
        change_remote = (APACHE / "change_binding_prod.sh").read_text()
        publish = (APACHE / "solutionfiles/csharp/prod-scripts/publish_prod.sh").read_text()
        legacy = (APACHE / "solutionfiles/common/scripts/issue_certificat.sh").read_text()
        self.assertIn('"${_certificateDomainArgs[@]}"', common)
        self.assertIn('set_certificate_domains "${_configPath}" "${_binding}"', test_action)
        self.assertIn('"${_certificateDomainArgs[@]}"', test_action)
        self.assertIn('awk \'$1 == "ServerAlias"', prod_action)
        self.assertIn('"${domain_args[@]}"', prod_action)
        self.assertIn('set_certificate_domains "${_configPath}" "${_newBinding}"', change_local)
        self.assertIn('"${_certificateDomainArgs[@]}"', change_local)
        self.assertIn('awk \'$1 == "ServerAlias"', change_remote)
        self.assertIn('"${domain_args[@]}"', change_remote)
        self.assertIn('awk \'$1 == "ServerAlias"', publish)
        self.assertIn('"${certificate_args[@]}"', publish)
        self.assertIn('${server_alias_line}', publish)
        self.assertLess(publish.index('server_alias_line=""'), publish.index('if [[ ! -e ${certdir}/fullchain.cer ]]'))
        self.assertIn("ensure_production_tls_config", prod_action)
        self.assertNotIn("ensure_production_tls_config", change_remote)
        for change in (change_local, change_remote):
            self.assertIn("New aliases, comma-separated", change)
            self.assertIn("normalize_server_aliases", change)
            self.assertIn("replace_server_aliases", change)
        self.assertIn('if [[ ${NEW} != "${OLD}" ]]', change_remote)
        self.assertIn('if [[ ${_newBinding} != "${_oldBinding}" && ${_scheme} == https ]]', change_local)
        self.assertIn('set_certificate_domains "${_configPath}" "${_binding}"', legacy)
        self.assertIn('"${_certificateDomainArgs[@]}"', legacy)


if __name__ == "__main__":
    unittest.main()
