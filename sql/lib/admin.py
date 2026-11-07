"""SQL Server admin operations using sqlcmd and server-side backup paths."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid


def connections(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(data, list) or any(
        not isinstance(item, dict) or any(not isinstance(item.get(key), str) or not item[key]
                                         for key in ("id", "name", "host", "username", "password", "backup_directory"))
        or isinstance(item.get("port"), bool) or not isinstance(item.get("port"), int)
        or not 1 <= item["port"] <= 65535 or not isinstance(item.get("trust_certificate"), bool)
        for item in data
    ):
        raise ValueError("Invalid SQL connection store")
    return data


def public_connections(path):
    return [{key: item[key] for key in ("id", "name", "host", "port", "username", "backup_directory", "trust_certificate")}
            for item in connections(path)]


def save_connections(path, items):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".sql-connections-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(items, stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def add_connection(path, data):
    fields = {}
    for key in ("name", "host", "username", "password", "backup_directory"):
        value = data.get(key)
        if not isinstance(value, str) or not value or "\0" in value or len(value) > 1024:
            raise ValueError("Invalid connection field: " + key)
        fields[key] = value if key == "password" else value.strip()
        if not fields[key]:
            raise ValueError("Missing connection field: " + key)
    if not re.fullmatch(r"[A-Za-z0-9_.:\[\]-]+", fields["host"]):
        raise ValueError("Enter a hostname or IP address without scheme or port")
    port = data.get("port", 1433)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Invalid SQL port")
    trust = data.get("trust_certificate", False)
    if not isinstance(trust, bool):
        raise ValueError("Invalid certificate setting")
    fields.update(id=uuid.uuid4().hex, port=port, trust_certificate=trust)
    items = connections(path)
    host = fields["host"].strip("[]").rstrip(".").casefold()
    if any(item["host"].strip("[]").rstrip(".").casefold() == host and item["port"] == port for item in items):
        raise ValueError("A connection to this SQL host and port already exists")
    items.append(fields)
    save_connections(path, items)
    return fields["id"]


def remove_connection(path, identifier):
    items = connections(path)
    if not any(item["id"] == identifier for item in items):
        raise ValueError("Unknown SQL connection")
    save_connections(path, [item for item in items if item["id"] != identifier])


def connection(path, identifier):
    for item in connections(path):
        if item["id"] == identifier:
            return item
    raise ValueError("Unknown SQL connection")


def literal(value):
    return "N'" + value.replace("'", "''") + "'"


def identifier(value):
    if not isinstance(value, str) or not value or len(value) > 128 or any(ord(char) < 32 for char in value):
        raise ValueError("Invalid database name")
    return "[" + value.replace("]", "]]") + "]"


def execute(settings, query, table=False, timeout=3600):
    tool = next((str(path) for path in (Path("/opt/mssql-tools18/bin/sqlcmd"), Path("/opt/mssql-tools/bin/sqlcmd"))
                 if path.is_file()), None) or shutil.which("sqlcmd")
    if not tool:
        raise ValueError("sqlcmd is not installed on the admin server. Install the Microsoft SQL command-line tools.")
    descriptor, filename = tempfile.mkstemp(prefix="vns-sql-", suffix=".sql")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("SET NOCOUNT ON;\n" + query + "\n")
        args = [tool, "-S", f'tcp:{settings["host"]},{settings["port"]}', "-U", settings["username"],
                "-d", "master", "-b", "-x", "-r", "0", "-l", "15", "-t", "0", "-w", "65535", "-f", "65001", "-i", filename]
        args += ["-W", "-s", "|"] if table else ["-y", "0"]
        if settings["trust_certificate"]:
            args.append("-C")
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                env=os.environ | {"SQLCMDPASSWORD": settings["password"]}, timeout=timeout)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or "SQL command failed").strip())
        if result.stderr.strip():
            print(result.stderr, file=sys.stderr, end="", flush=True)
        return result.stdout
    finally:
        os.unlink(filename)


def query_json(settings, query):
    output = execute(settings, "SELECT COALESCE((" + query + " FOR JSON PATH), N'[]') AS vns_json;", timeout=30)
    lines = output.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        raise ValueError("sqlcmd returned no JSON output. Check the SQL command-line tools and server logs.")
    if lines[0].strip() == "vns_json":
        if len(lines) < 3 or not re.fullmatch(r"-+", lines[1].strip()):
            raise ValueError("Invalid sqlcmd JSON result header")
        lines = lines[2:]
    payload = "".join(lines)
    data = json.loads(payload)
    if not isinstance(data, list):
        raise ValueError("Invalid SQL Server response")
    return data


def databases(settings):
    return query_json(settings, """
SELECT d.name, d.database_id AS id, d.state_desc AS state, d.recovery_model_desc AS recovery,
 CAST(COALESCE(SUM(CAST(f.size AS bigint)),0)*8.0/1024 AS decimal(18,1)) AS size_mb
FROM sys.databases d LEFT JOIN sys.master_files f ON f.database_id=d.database_id
GROUP BY d.name,d.database_id,d.state_desc,d.recovery_model_desc ORDER BY d.name""")


def backups(settings):
    return query_json(settings, """
SELECT TOP (200) b.backup_set_id AS id, b.database_name AS database_name,
 CONVERT(varchar(30),b.backup_finish_date,126) AS finished,
 CAST(b.backup_size/1048576.0 AS decimal(18,1)) AS size_mb,
 m.physical_device_name AS path, b.position,
 (SELECT COUNT(*) FROM msdb.dbo.backupmediafamily x WHERE x.media_set_id=b.media_set_id) AS media_count
FROM msdb.dbo.backupset b JOIN msdb.dbo.backupmediafamily m ON m.media_set_id=b.media_set_id
WHERE b.type='D' AND m.family_sequence_number=1 AND m.device_type=2
ORDER BY b.backup_finish_date DESC""")


def backup(settings, name):
    identifier(name)
    separator = "\\" if "\\" in settings["backup_directory"] else "/"
    path = settings["backup_directory"].rstrip("/\\") + separator + "vns-" + uuid.uuid4().hex + ".bak"
    execute(settings, f"BACKUP DATABASE {identifier(name)} TO DISK={literal(path)} WITH COPY_ONLY, CHECKSUM;")
    print(f"Backup complete: {name} -> {path}", flush=True)
    return path


def restore(settings, name, path, position, overwrite):
    files = execute(settings, f"RESTORE FILELISTONLY FROM DISK={literal(path)} WITH FILE={position};", table=True)
    lines = [line.strip() for line in files.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("LogicalName|PhysicalName|Type|"):
        raise ValueError("Cannot read backup logical files")
    entries = []
    for line in lines[2:]:
        fields = line.split("|")
        if len(fields) < 3 or fields[2] not in ("D", "L"):
            raise ValueError("Unsupported backup file type or invalid backup metadata")
        entries.append((fields[0], fields[2]))
    if not entries:
        raise ValueError("Backup contains no database files")
    paths = query_json(settings, """
SELECT CAST(SERVERPROPERTY('InstanceDefaultDataPath') AS nvarchar(4000)) AS data_path,
 CAST(SERVERPROPERTY('InstanceDefaultLogPath') AS nvarchar(4000)) AS log_path""")
    if not paths or not paths[0].get("data_path") or not paths[0].get("log_path"):
        raise ValueError("SQL Server default data/log paths are unavailable")
    # Unique physical filenames avoid collisions with other databases.
    prefix = "vns-" + uuid.uuid4().hex
    moves = []
    for index, (logical, kind) in enumerate(entries):
        directory = paths[0]["log_path" if kind == "L" else "data_path"]
        separator = "\\" if "\\" in directory else "/"
        physical = directory.rstrip("/\\") + separator + prefix + "-" + str(index) + (".ldf" if kind == "L" else ".mdf")
        moves.append(f"MOVE {literal(logical)} TO {literal(physical)}")
    sql = f"RESTORE DATABASE {identifier(name)} FROM DISK={literal(path)} WITH FILE={position}, " + ", ".join(moves)
    if overwrite:
        sql = f"""
ALTER DATABASE {identifier(name)} SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
BEGIN TRY
 {sql}, REPLACE;
 ALTER DATABASE {identifier(name)} SET MULTI_USER;
END TRY
BEGIN CATCH
 ALTER DATABASE {identifier(name)} SET MULTI_USER;
 THROW;
END CATCH;"""
    execute(settings, sql)
    print("Restore complete: " + name, flush=True)


def run_action(settings, data):
    if not isinstance(data, dict):
        raise ValueError("Expected a SQL action object")
    action = data.get("action")
    if action not in ("backup_all", "create", "backup", "drop", "copy", "restore"):
        raise ValueError("Unknown SQL action")
    print("SQL operation: " + action, flush=True)
    rows = databases(settings)
    name = data.get("database", "")
    if action == "backup_all":
        count = 0
        for row in rows:
            if row["id"] > 4 and row["state"] == "ONLINE":
                backup(settings, row["name"])
                count += 1
        print(f"Backup complete: {count} online user database(s).", flush=True)
        return
    identifier(name)
    current = next((row for row in rows if row["name"].casefold() == name.casefold()), None)
    if current and current["id"] <= 4:
        raise ValueError("System databases cannot be changed here")
    if action in ("backup", "drop", "copy") and not current:
        raise ValueError("Database no longer exists")
    if action == "create":
        if current:
            raise ValueError("Database already exists")
        execute(settings, f"CREATE DATABASE {identifier(name)};")
    elif action == "backup":
        backup(settings, name)
    elif action == "drop":
        if data.get("confirmation") != name:
            raise ValueError("Type the exact database name to confirm deletion")
        execute(settings, f"""
ALTER DATABASE {identifier(name)} SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
BEGIN TRY
 DROP DATABASE {identifier(name)};
END TRY
BEGIN CATCH
 ALTER DATABASE {identifier(name)} SET MULTI_USER;
 THROW;
END CATCH;""")
    elif action in ("copy", "restore"):
        target = data.get("target", "")
        identifier(target)
        existing = next((row for row in rows if row["name"].casefold() == target.casefold()), None)
        if existing and existing["id"] <= 4:
            raise ValueError("Cannot overwrite a system database")
        overwrite = existing is not None
        if overwrite and (action == "copy" or data.get("confirmation") != target or data.get("overwrite") is not True):
            raise ValueError("Destination exists. Explicit overwrite confirmation is required for restore.")
        if action == "copy":
            path, position = backup(settings, name), 1
        else:
            if isinstance(data.get("backup_id"), bool) or not isinstance(data.get("backup_id"), int):
                raise ValueError("Select a valid backup")
            selected = next((item for item in backups(settings) if item["id"] == data.get("backup_id")), None)
            if not selected or selected["media_count"] != 1:
                raise ValueError("Backup not found or striped backups are unsupported")
            path, position = selected["path"], selected["position"]
        restore(settings, target, path, position, overwrite)
    print("OK: " + action, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("store")
    parser.add_argument("connection")
    parser.add_argument("payload")
    args = parser.parse_args()
    try:
        run_action(connection(args.store, args.connection), json.loads(args.payload))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
