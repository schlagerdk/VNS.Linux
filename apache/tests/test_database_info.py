import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


APACHE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("database_info", APACHE / "lib/database_info.py")
database = importlib.util.module_from_spec(spec)
spec.loader.exec_module(database)


class DatabaseInfoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def settings(self, connection, provider=None, environment=""):
        values = {"umbracoDbDSN": connection}
        if provider is not None:
            values["umbracoDbDSN_ProviderName"] = provider
        name = "appsettings" + ("." + environment if environment else "") + ".json"
        (self.root / name).write_text(json.dumps({"ConnectionStrings": values}))

    def test_explicit_providers_and_no_credentials_in_result(self):
        for provider, connection, expected in (
            ("Microsoft.Data.Sqlite", "Data Source=|DataDirectory|/Umbraco.sqlite.db;Cache=Shared", "SQLite"),
            ("Microsoft.Data.SqlClient", "Server=private;Database=app;User Id=example;Password=secret", "SQL Server"),
            ("System.Data.SqlClient", "Data Source=private;Initial Catalog=app", "SQL Server"),
        ):
            self.settings(connection, provider)
            self.assertEqual(database.database_type(self.root), expected)

    def test_environment_file_and_service_overrides(self):
        self.settings("Data Source=app.db", "Microsoft.Data.Sqlite")
        self.settings("Server=sql;Database=app", "Microsoft.Data.SqlClient", "Production")
        self.assertEqual(database.database_type(self.root, "Test"), "SQLite")
        self.assertEqual(database.database_type(self.root, "Production"), "SQL Server")
        self.assertEqual(database.database_type(self.root, "Production", {
            "ConnectionStrings__umbracoDbDSN": "Data Source=override.db",
            "ConnectionStrings__umbracoDbDSN_ProviderName": "Microsoft.Data.Sqlite",
        }), "SQLite")

    def test_inference_without_provider_and_unsupported_database(self):
        for value, expected in (
            ("Data Source=app.sqlite", "SQLite"),
            ("Data Source=:memory:", "SQLite"),
            ("Server=example;Database=test", "SQL Server"),
            ("Data Source=example;Initial Catalog=test", "SQL Server"),
            ("Host=postgres;Username=example", ""),
        ):
            self.settings(value)
            self.assertEqual(database.database_type(self.root), expected)
        self.settings("Server=example;Database=test", "Npgsql")
        self.assertEqual(database.database_type(self.root), "")

    def test_comments_trailing_commas_and_string_literals(self):
        text = '''{
            // app settings
            "ConnectionStrings": {
                "umbracoDbDSN": "Server=example;Password=a//b/*c*/,};Database=test",
                /* provider */ "umbracoDbDSN_ProviderName": "Microsoft.Data.SqlClient",
            },
        }'''
        (self.root / "appsettings.json").write_text(text)
        self.assertEqual(database.database_type(self.root), "SQL Server")
        parsed = database.configuration_json(text)
        self.assertIn("a//b/*c*/,}", parsed["ConnectionStrings"]["umbracoDbDSN"])

    def test_missing_empty_and_invalid_config(self):
        self.assertEqual(database.database_type(self.root), "")
        self.settings("", "Microsoft.Data.Sqlite")
        self.assertEqual(database.database_type(self.root), "")
        for text in ("{", "[]"):
            (self.root / "appsettings.json").write_text(text)
            with self.assertRaises(ValueError):
                database.database_type(self.root)

    def test_remote_status_uses_same_helper_at_site_root(self):
        source = (APACHE / "remote_site.sh").read_text()
        script = source.split("python3 - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        script = script.replace('Path("/var/www", site)', 'Path(ROOT)')
        script = script.replace('Path("/etc/systemd/system", site + ".service")', 'Path(ROOT) / "service"')
        script = script.replace('Path("/etc/apache2/sites-available", site + ".conf")', 'Path(ROOT) / "conf"')
        self.settings("Server=remote;Database=prod", "Microsoft.Data.SqlClient")
        # Execute the actual transmitted status program with local service/config fixtures.
        harness = '''
import os
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
root = Path(ROOT)
(root / "service").write_text("WorkingDirectory=/var/www/prod.test")
(root / "conf").write_text("ServerName prod.test")
def run(argv, **kwargs):
    return SimpleNamespace(returncode=0, stdout='DOTNET_ENVIRONMENT=Production' if argv[1] == "show" else "active", stderr="")
with patch.dict(os.environ, {"SITE":"prod.test", "DATABASE_CODE":HELPER}), patch("subprocess.run", run):
    exec(SCRIPT)
'''
        code = ("ROOT=" + repr(str(self.root)) + "\nHELPER=" + repr((APACHE / "lib/database_info.py").read_text())
                + "\nSCRIPT=" + repr(script) + "\n" + harness)
        result = subprocess.run(["python3", "-c", code], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["database"], "SQL Server")
        self.assertNotIn("Server=remote", result.stdout)


if __name__ == "__main__":
    unittest.main()
