import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from flask import Flask, abort, jsonify, request
from werkzeug.exceptions import BadRequest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("sql_admin", ROOT / "sql/lib/admin.py")
sql = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sql)


class SqlAdminTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Path(self.directory.name) / "connections.json"
        self.settings = dict(name="SQL", host="localhost", port=1433, username="sa",
                             password="test-only-password", backup_directory="/var/opt/mssql/backup",
                             trust_certificate=False)
        self.rows = [{"id": 1, "name": "master", "state": "ONLINE"},
                     {"id": 5, "name": "Example", "state": "ONLINE"},
                     {"id": 6, "name": "Offline", "state": "OFFLINE"}]

    def test_connections_permissions_and_redaction(self):
        identifier = sql.add_connection(self.store, self.settings)
        self.assertEqual(self.store.stat().st_mode & 0o777, 0o600)
        public = sql.public_connections(self.store)
        self.assertNotIn("password", public[0])
        self.assertEqual(sql.connection(self.store, identifier)["password"], self.settings["password"])
        sql.remove_connection(self.store, identifier)
        self.assertEqual(sql.public_connections(self.store), [])
        with self.assertRaises(ValueError):
            sql.connection(self.store, identifier)

    def test_connection_validation(self):
        for change in ({"host": "https://localhost"}, {"port": True}, {"port": 70000},
                       {"password": ""}, {"trust_certificate": "yes"}, {"backup_directory": "\0"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                sql.add_connection(self.store, self.settings | change)

    def test_sqlcmd_uses_private_query_file_and_password_environment(self):
        filenames = []
        def run(args, **kwargs):
            filename = args[args.index("-i") + 1]
            filenames.append(filename)
            self.assertEqual(os.stat(filename).st_mode & 0o777, 0o600)
            self.assertNotIn(self.settings["password"], json.dumps(args))
            self.assertEqual(kwargs["env"]["SQLCMDPASSWORD"], self.settings["password"])
            self.assertIn("-b", args)
            self.assertIn("-x", args)
            self.assertNotIn("-C", args)
            self.assertNotIn("-h", args)
            self.assertEqual(args[args.index("-y") + 1], "0")
            self.assertIn("SET NOCOUNT ON;", Path(filename).read_text())
            self.assertIn("FOR JSON PATH), N'[]') AS vns_json;", Path(filename).read_text())
            return Mock(returncode=0, stdout="[]\n", stderr="")
        with patch.object(sql.shutil, "which", return_value="/test/sqlcmd"), patch.object(sql.subprocess, "run", side_effect=run):
            self.assertEqual(sql.query_json(self.settings, "SELECT 1 AS value"), [])
        self.assertFalse(Path(filenames[0]).exists())

    def test_long_json_chunks_preserve_spaces_at_chunk_boundaries(self):
        expected = [{"name": "a" * 3985 + " middle " + "b" * 5000}]
        encoded = json.dumps(expected)
        boundary = encoded.index(" middle") + 1
        output = "vns_json\n--------\n" + encoded[:boundary] + "\n" + encoded[boundary:] + "\n"
        with patch.object(sql, "execute", return_value=output):
            self.assertEqual(sql.query_json(self.settings, "SELECT name"), expected)

    def test_json_uses_result_set_instead_of_print_messages(self):
        expected = [{"name": "Example", "id": 5}]
        output = "vns_json\n--------\n" + json.dumps(expected) + "\n"

        def run(args, **kwargs):
            query = Path(args[args.index("-i") + 1]).read_text()
            self.assertNotIn("PRINT", query)
            return Mock(returncode=0, stdout=output, stderr="")

        with patch.object(sql.shutil, "which", return_value="/test/sqlcmd"), \
                patch.object(sql.subprocess, "run", side_effect=run):
            self.assertEqual(sql.query_json(self.settings, "SELECT name, database_id AS id FROM sys.databases"), expected)

    def test_empty_json_response_has_actionable_error(self):
        with patch.object(sql, "execute", return_value=" \r\n"):
            with self.assertRaisesRegex(ValueError, "sqlcmd returned no JSON output"):
                sql.query_json(self.settings, "SELECT 1")

    def test_actual_tools18_headerless_database_response(self):
        output = '[{"name":"master"},{"name":"tempdb"},{"name":"model"},{"name":"msdb"}]\n'
        with patch.object(sql, "execute", return_value=output):
            self.assertEqual(sql.query_json(self.settings, "SELECT name FROM sys.databases"),
                             [{"name": name} for name in ("master", "tempdb", "model", "msdb")])

    def test_empty_result_is_coalesced_to_json_array_in_sql(self):
        def run(args, **kwargs):
            query = Path(args[args.index("-i") + 1]).read_text()
            output = "[]\n" if "COALESCE((" in query and "), N'[]')" in query else "NULL\n"
            return Mock(returncode=0, stdout=output, stderr="")

        with patch.object(sql.shutil, "which", return_value="/test/sqlcmd"), \
                patch.object(sql.subprocess, "run", side_effect=run):
            self.assertEqual(sql.query_json(self.settings, "SELECT 1 AS value WHERE 1=0"), [])
            self.assertEqual(sql.backups(self.settings), [])

    def test_sql_errors_never_become_success(self):
        with patch.object(sql.shutil, "which", return_value="/test/sqlcmd"), patch.object(
                sql.subprocess, "run", return_value=Mock(returncode=1, stdout="", stderr="Access denied")):
            with self.assertRaisesRegex(RuntimeError, "Access denied"):
                sql.execute(self.settings, "SELECT 1")
        with patch.object(sql, "execute", return_value="not JSON"):
            with self.assertRaises(ValueError):
                sql.query_json(self.settings, "SELECT 1")

    def test_create_and_drop_escape_names_and_require_confirmation(self):
        name = "odd]name';--"
        with patch.object(sql, "databases", return_value=self.rows), patch.object(sql, "execute") as execute:
            sql.run_action(self.settings, {"action": "create", "database": name})
        self.assertIn("[odd]]name';--]", execute.call_args.args[1])
        with patch.object(sql, "databases", return_value=self.rows), patch.object(sql, "execute") as execute:
            for data in ({"action": "drop", "database": "master", "confirmation": "master"},
                         {"action": "drop", "database": "Example", "confirmation": "wrong"}):
                with self.assertRaises(ValueError):
                    sql.run_action(self.settings, data)
            execute.assert_not_called()
            sql.run_action(self.settings, {"action": "drop", "database": "Example", "confirmation": "Example"})
            self.assertIn("SET MULTI_USER", execute.call_args.args[1])

    def test_backup_all_skips_system_and_offline_databases(self):
        with patch.object(sql, "databases", return_value=self.rows), patch.object(sql, "backup") as backup:
            sql.run_action(self.settings, {"action": "backup_all"})
        backup.assert_called_once_with(self.settings, "Example")

    def test_restore_rechecks_destination_backup_and_confirmation(self):
        record = dict(id=10, path="/backup/example.bak", position=2, media_count=1)
        data = dict(action="restore", database="Example", target="Example", backup_id=10)
        with patch.object(sql, "databases", return_value=self.rows), patch.object(sql, "backups", return_value=[record]), \
                patch.object(sql, "restore") as restore:
            with self.assertRaises(ValueError):
                sql.run_action(self.settings, data)
            restore.assert_not_called()
            sql.run_action(self.settings, data | {"overwrite": True, "confirmation": "Example"})
            restore.assert_called_once_with(self.settings, "Example", record["path"], 2, True)
            record["media_count"] = 2
            with self.assertRaises(ValueError):
                sql.run_action(self.settings, data | {"overwrite": True, "confirmation": "Example"})

    def test_restore_uses_logical_files_and_unique_server_paths(self):
        output = "LogicalName|PhysicalName|Type|Other\n-----------|------------|----|-----\nData|C:\\old.mdf|D|0\nLog|C:\\old.ldf|L|0\n"
        with patch.object(sql, "execute", side_effect=[output, ""]) as execute, patch.object(sql, "query_json", return_value=[
                {"data_path": "C:\\Data\\", "log_path": "D:\\Logs\\"}]):
            sql.restore(self.settings, "New", "/backup/file.bak", 3, True)
        query = execute.call_args.args[1]
        self.assertIn("MOVE N'Data' TO N'C:\\Data\\vns-", query)
        self.assertIn("MOVE N'Log' TO N'D:\\Logs\\vns-", query)
        self.assertIn("FILE=3", query)
        self.assertIn("REPLACE", query)
        self.assertIn("BEGIN CATCH", query)
        self.assertIn("SET MULTI_USER", query)

    def test_restore_rejects_unsupported_logical_files_before_changing_database(self):
        output = "LogicalName|PhysicalName|Type|Other\n-----------|------------|----|-----\nData|/old.mdf|S|0\n"
        with patch.object(sql, "execute", return_value=output) as execute:
            with self.assertRaises(ValueError):
                sql.restore(self.settings, "New", "/backup/file.bak", 1, True)
        self.assertEqual(execute.call_count, 1)

    def test_copy_only_to_new_database(self):
        with patch.object(sql, "databases", return_value=self.rows), patch.object(sql, "backup", return_value="/backup/copy.bak"), \
                patch.object(sql, "restore") as restore:
            sql.run_action(self.settings, {"action": "copy", "database": "Example", "target": "New"})
            restore.assert_called_once_with(self.settings, "New", "/backup/copy.bak", 1, False)
            with self.assertRaises(ValueError):
                sql.run_action(self.settings, {"action": "copy", "database": "Example", "target": "master"})


class SqlApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = str(Path(self.directory.name) / "connections.json")
        self.app = Flask(__name__)
        self.ns = dict(app=self.app, request=request, jsonify=jsonify, abort=abort, subprocess=subprocess,
                       SQL_LOCK=threading.Lock(), SQL_CONNECTIONS=self.store, SQL_HELPER=str(ROOT / "sql/lib/admin.py"),
                       _sql_module=sql, sys=sys, json=json,
                       error=lambda message, status=400: (jsonify(error=message), status))
        source = ROOT / "apache/solutionfiles/admin/app/app.py"
        names = {"api_sql_connections", "api_sql_status", "build_command"}
        nodes = [node for node in ast.parse(source.read_text()).body if isinstance(node, ast.FunctionDef) and node.name in names]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "sql API", "exec"), self.ns)
        self.client = self.app.test_client()

    def test_connection_api_redacts_credentials_and_builds_allowlisted_task(self):
        response = self.client.post("/api/sql/connections", json={
            "name": "Local", "host": "localhost", "port": 1433, "username": "sa",
            "password": "test-only-secret", "backup_directory": "/backup", "trust_certificate": True})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("test-only-secret", response.get_data(as_text=True))
        connection = response.json["connections"][0]["id"]
        argv = self.ns["build_command"]({"kind": "sql_action", "connection": connection, "action": "backup_all"})[1]
        self.assertNotIn("test-only-secret", json.dumps(argv))
        self.assertEqual(json.loads(argv[-1]), {"action": "backup_all"})
        with self.app.test_request_context(), self.assertRaises(BadRequest):
            self.ns["build_command"]({"kind": "sql_action", "connection": connection, "action": "arbitrary"})
        response = self.client.delete("/api/sql/connections", json={"id": connection})
        self.assertEqual(response.json["connections"], [])

    def test_sql_status_errors_are_reported_and_unknown_connections_rejected(self):
        with self.assertLogs(self.app.logger, level="ERROR"):
            response = self.client.get("/api/sql/missing")
        self.assertEqual(response.status_code, 502)
        self.assertIn("Unknown SQL connection", response.json["error"])

    def test_admin_updater_preserves_connection_store(self):
        updater = (ROOT / "apache/update_admin_site.sh").read_text()
        self.assertIn("--exclude /sql-connections.json", updater)


if __name__ == "__main__":
    unittest.main()
