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

    def test_explicit_providers_and_no_connection_details_in_result(self):
        for provider, connection, expected in (
            ("Microsoft.Data.Sqlite", "Data Source=|DataDirectory|/Umbraco.sqlite.db;Cache=Shared", "SQLite"),
            ("Microsoft.Data.SqlClient", "Server=private;Database=app;User Id=example", "MS SQL"),
            ("System.Data.SqlClient", "Data Source=private;Initial Catalog=app", "MS SQL"),
        ):
            self.settings(connection, provider)
            self.assertEqual(database.database_type(self.root), expected)

    def test_environment_file_and_service_overrides(self):
        self.settings("Data Source=app.db", "Microsoft.Data.Sqlite")
        self.settings("Server=sql;Database=app", "Microsoft.Data.SqlClient", "Production")
        self.assertEqual(database.database_type(self.root, "Test"), "SQLite")
        self.assertEqual(database.database_type(self.root, "Production"), "MS SQL")
        self.assertEqual(database.database_type(self.root, "Production", {
            "ConnectionStrings__umbracoDbDSN": "Data Source=override.db",
            "ConnectionStrings__umbracoDbDSN_ProviderName": "Microsoft.Data.Sqlite",
        }), "SQLite")
        self.assertEqual(database.database_type(self.root), "MS SQL")

    def test_inference_without_provider_and_unsupported_database(self):
        for value, expected in (
            ("Data Source=app.sqlite", "SQLite"),
            ("Data Source=:memory:", "SQLite"),
            ("Server=example;Database=test", "MS SQL"),
            ("Data Source=example;Initial Catalog=test", "MS SQL"),
            ("Host=postgres;Username=example", "Unknown"),
        ):
            self.settings(value)
            self.assertEqual(database.database_type(self.root), expected)
        self.settings("Server=example;Database=test", "Npgsql")
        self.assertEqual(database.database_type(self.root), "Unknown")

    def test_comments_trailing_commas_and_string_literals(self):
        text = '''{
            // app settings
            "ConnectionStrings": {
                "umbracoDbDSN": "Server=example;Application Name=a//b/*c*/,};Database=test",
                /* provider */ "umbracoDbDSN_ProviderName": "Microsoft.Data.SqlClient",
            },
        }'''
        (self.root / "appsettings.json").write_text(text)
        self.assertEqual(database.database_type(self.root), "MS SQL")
        parsed = database.configuration_json(text)
        self.assertIn("a//b/*c*/,}", parsed["ConnectionStrings"]["umbracoDbDSN"])

    def test_missing_empty_and_invalid_config(self):
        self.assertEqual(database.database_type(self.root), "Unknown")
        self.settings("", "Microsoft.Data.Sqlite")
        self.assertEqual(database.database_type(self.root), "Unknown")
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
        for connection, expected in (
            ("Server=remote;Database=prod", "MS SQL"),
            (r"Server=remote\SQLEXPRESS;Database=prod", "SQL Express"),
            ("Server=tcp:example.database.windows.net,1433;Database=prod", "Azure SQL"),
        ):
            self.settings(connection, "Microsoft.Data.SqlClient")
            code = ("ROOT=" + repr(str(self.root)) + "\nHELPER=" + repr((APACHE / "lib/database_info.py").read_text())
                    + "\nSCRIPT=" + repr(script) + "\n" + harness)
            result = subprocess.run(["python3", "-c", code], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout)["database"], expected)
            self.assertEqual(json.loads(result.stdout)["database_name"], "prod")
            self.assertNotIn(connection, result.stdout)

    def test_sql_express_and_azure_hostnames(self):
        for connection, provider, expected in (
            (r"Server=localhost\SQLEXPRESS;Database=app", None, "SQL Express"),
            (r"Data Source=HOST\SqlExpress,1433;Initial Catalog=app", "Microsoft.Data.SqlClient", "SQL Express"),
            ("Server=tcp:example.database.windows.net,1433;Database=app", None, "Azure SQL"),
            ('Data Source="example.database.windows.net";Initial Catalog=app', "Microsoft.Data.SqlClient", "Azure SQL"),
            ("Server=example.database.usgovcloudapi.net;Database=app", None, "Azure SQL"),
            ("Server=example.database.windows.net.evil.test;Database=app", None, "MS SQL"),
            ("Server=private;Application Name='value;Server=example.database.windows.net';Database=app", None, "MS SQL"),
            (r"Server=private;Application Name=SQLEXPRESS;Database=app", None, "MS SQL"),
            ("Server=private;Database=app", "Npgsql", "Unknown"),
        ):
            with self.subTest(connection=connection):
                self.settings(connection, provider)
                self.assertEqual(database.database_type(self.root), expected)

    def test_mixed_datasources_are_custom(self):
        (self.root / "appsettings.json").write_text(json.dumps({"ConnectionStrings": {
            "first": "Server=example;Database=app",
            "second": "Data Source=app.db",
        }}))
        self.assertEqual(database.database_type(self.root), "Unknown")

    def test_database_names_and_sqlite_paths_preserve_case(self):
        for connection, provider, expected in (
            ("Server=example;Database=MyApplication", None, "MyApplication"),
            ('Server=example;Initial Catalog="App;Production"', None, "App;Production"),
            ('Server=example;Database="My ""Application"""', None, 'My "Application"'),
            ("Host=example;Database=CustomApp", "Npgsql", "CustomApp"),
            ("Data Source=App_Data/MyApp.db", None, str(self.root / "App_Data/MyApp.db")),
            ("DataSource=App_Data/MyApp.db", "Microsoft.Data.Sqlite", str(self.root / "App_Data/MyApp.db")),
            ("Filename=/var/data/MyApp.sqlite", "Microsoft.Data.Sqlite", "/var/data/MyApp.sqlite"),
            ("Data Source=|DataDirectory|/Umbraco.sqlite.db", "Microsoft.Data.Sqlite", "|DataDirectory|/Umbraco.sqlite.db"),
            ("Data Source=file:App.db?mode=ro", "Microsoft.Data.Sqlite", "file:App.db?mode=ro"),
            ("Data Source=:memory:", "Microsoft.Data.Sqlite", ":memory:"),
        ):
            with self.subTest(connection=connection):
                self.settings(connection, provider)
                self.assertEqual(database.database_details(self.root)["database_name"], expected)
        self.settings("Server=example;User ID=Example", "Microsoft.Data.SqlClient")
        self.assertEqual(database.database_details(self.root), {
            "database": "MS SQL", "database_name": "", "database_server": "example"})

    def test_server_aliases_instances_ports_and_sqlite(self):
        for connection, provider, expected in (
            ("Server=172.23.3.21;Database=App;User ID=Example", None, "172.23.3.21"),
            (r"Data Source=Host\SQLEXPRESS,1433;Database=App", None, r"Host\SQLEXPRESS,1433"),
            ("Address=tcp:Example.database.windows.net,1433;Database=App", "Microsoft.Data.SqlClient",
             "tcp:Example.database.windows.net,1433"),
            ("Data Source=App.db", "Microsoft.Data.Sqlite", ""),
            ("Host=custom;Database=App", "Npgsql", ""),
        ):
            with self.subTest(connection=connection):
                self.settings(connection, provider)
                self.assertEqual(database.database_details(self.root)["database_server"], expected)

    def test_names_respect_environment_and_service_overrides(self):
        self.settings("Server=example;Database=BaseApp")
        self.settings("Server=example;Database=ProductionApp", environment="Production")
        self.assertEqual(database.database_details(self.root)["database_name"], "ProductionApp")
        self.assertEqual(database.database_details(self.root, variables={
            "ConnectionStrings__umbracoDbDSN": "Server=override;Database=OverrideApp",
        })["database_name"], "OverrideApp")

    def test_detail_datasource_row_and_no_overview_badge(self):
        source = (APACHE / "solutionfiles/admin/app/static/app.js").read_text()
        overview = source[source.index("function siteCard("):source.index("function renderSite(")]
        self.assertNotIn("env.database", overview)
        detail = source[source.index("function envPanel("):]
        self.assertNotIn('className: "badge database"', detail)
        self.assertIn('if (env.aliases && env.aliases.length)', detail)
        styles = (APACHE / "solutionfiles/admin/app/static/style.css").read_text()
        self.assertIn(".meta .aliases a { font-style: italic; }", styles)
        alias_row = detail[detail.index('if (env.aliases && env.aliases.length)'):
                           detail.index('\n\tif (site.kind === "umbraco"', detail.index('if (env.aliases && env.aliases.length)'))]
        self.assertIn('openLink(alias, scheme + "://" + alias + "/")', alias_row)
        start = detail.index('\tif (site.kind === "csharp" || site.kind === "umbraco")')
        row = detail[start:detail.index('\n\tif (env.port)', start)]
        harness = r"""
const assert = require("node:assert/strict");
const el = (tag, properties, ...children) => ({tag, ...properties, children});
const openLink = (text, url) => ({tag: "a", textContent: text, href: url});
const scheme = "https";
const env = {aliases: ["www.example.com", "shop.example.com"]};
const meta = {children: [], append(...items) {this.children.push(...items);}};
ALIASES
assert.equal(meta.children[1].className, "database-name aliases");
assert.deepEqual(meta.children[1].children, [
    {tag: "a", textContent: "www.example.com", href: "https://www.example.com/"},
    {tag: "a", textContent: "shop.example.com", href: "https://shop.example.com/"},
]);
for (const kind of ["csharp", "umbraco", "python", "html"]) {
    for (const database of ["MS SQL", "SQL Express", "SQLite", "Azure SQL", "Unknown", undefined]) {
        const site = {kind}, env = {database, database_name: "MyApplication", database_server: database === "SQLite" ? "" : "172.23.3.21"};
        const meta = {children: [], append(...items) {this.children.push(...items);}};
        ROW
        if (kind === "csharp" || kind === "umbraco") {
            assert.equal(meta.children[0].tag, "dt");
            assert.equal(meta.children[0].textContent, "Datasource");
            assert.equal(meta.children[1].tag, "dd");
            assert.equal(meta.children[1].textContent, (database || "Unknown") + (env.database_server ? " (172.23.3.21)" : ""));
            assert.equal(meta.children[2].textContent, "Database");
            assert.equal(meta.children[3].textContent, "MyApplication");
        } else assert.equal(meta.children.length, 0);
    }
}
"""
        result = subprocess.run(["node", "-e", harness.replace("ALIASES", alias_row).replace("ROW", row)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
