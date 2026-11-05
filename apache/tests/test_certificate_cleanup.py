from pathlib import Path
import subprocess
import tempfile
import unittest


APACHE = Path(__file__).resolve().parents[1]


def shell_function(source, name):
    start = source.index(name + "() {")
    return source[start:source.index("\n}", start) + 2]


class CertificateCleanupTests(unittest.TestCase):
    def run_cleanup(self, remote, selected="y", shared=False, domain="example.test"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            mappings = {
                "/root/.acme.sh": str(root / "acme"),
                "/etc/ssl/certs": str(root / "certs"),
                "/etc/apache2/sites-enabled": str(root / "enabled"),
                "/etc/acmesh/acme.sh": str(root / "not-installed-acme.sh"),
            }
            for name in ("acme", "certs", "enabled"):
                (root / name).mkdir()
            for name in ("example.test", "example.test_ecc", "other.test", "other.test_ecc", "ca"):
                (root / "acme" / name).mkdir()
                (root / "acme" / name / "keep").write_text("fixture")
            (root / "acme/account.conf").write_text("account fixture")
            for name in ("example.test", "example.test_ecc"):
                (root / "certs" / name).mkdir()
            if shared:
                (root / "enabled/other.conf").write_text(
                    "ServerName other.test\nServerAlias EXAMPLE.TEST\n")
            source = (APACHE / "delete_site.sh").read_text()
            if remote:
                start = source.index('if [[ ${DELETE_CERTIFICATE} =~')
                code = source[start:source.index("\nsystemctl daemon-reload", start)]
                prefix = 'BINDING="$1"; DELETE_CERTIFICATE="$2"\n'
            else:
                common = (APACHE / "lib/common.sh").read_text()
                code = "\n".join(shell_function(common, name) for name in ("validate_binding", "clean_stale_acme"))
                code += "\n" + shell_function(source, "remove_certificate")
                prefix = '_deleteCertificate="$2"; _acme=/fixture/acme.sh; _site=deleted.test\n'
                code += '\nremove_certificate "$1" "/etc/ssl/certs/${1}_ecc"\n'
            for old, new in mappings.items():
                code = code.replace(old, new)
            # Only fixture paths can reach rm; ACME commands are mocked.
            prefix += 'sudo() { if [[ $1 == /fixture/acme.sh ]]; then return 0; fi; "$@"; }\n'
            result = subprocess.run(
                ["/bin/bash", "-c", prefix + code, "test", domain, selected],
                capture_output=True, text=True)
            should_remove = selected == "y" and not shared and domain == "example.test"
            for name in ("example.test", "example.test_ecc"):
                self.assertEqual((root / "acme" / name).exists(), not should_remove, result.stderr)
            for name in ("other.test", "other.test_ecc", "ca", "account.conf"):
                self.assertTrue((root / "acme" / name).exists())
            if domain == "example.test":
                self.assertEqual(result.returncode, 0, result.stderr)
            else:
                self.assertNotEqual(result.returncode, 0)
            if shared:
                self.assertIn("another enabled site uses it", result.stdout)
                self.assertTrue((root / "certs/example.test_ecc").exists())

    def test_domain_only_cleanup(self):
        for remote in (False, True):
            with self.subTest(remote=remote):
                self.run_cleanup(remote)

    def test_declined_certificate_removal_keeps_data(self):
        for remote in (False, True):
            with self.subTest(remote=remote):
                self.run_cleanup(remote, selected="n")

    def test_shared_certificate_keeps_data(self):
        for remote in (False, True):
            with self.subTest(remote=remote):
                self.run_cleanup(remote, shared=True)

    def test_invalid_local_domain_rejected(self):
        self.run_cleanup(False, domain="../outside")


if __name__ == "__main__":
    unittest.main()
