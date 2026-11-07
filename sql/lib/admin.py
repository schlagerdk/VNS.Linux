"""SQL Server admin operations using sqlcmd and server-side backup paths."""
import argparse
import hashlib
import importlib.util
import shlex
import socket
import stat
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

_file_spec = importlib.util.spec_from_file_location("vns_backup_files", Path(__file__).with_name("backup_files.py"))
_file_module = importlib.util.module_from_spec(_file_spec)
_file_spec.loader.exec_module(_file_module)


def ssh_settings(data, existing=None):
    enabled = data.get("ssh_enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("Invalid SSH setting")
    if not enabled:
        return {"ssh_enabled": False}
    host = data.get("ssh_host", "")
    user = data.get("ssh_username", "")
    if not isinstance(host, str) or not isinstance(user, str):
        raise ValueError("Invalid SSH host or username")
    host, user = host.strip(), user.strip()
    port = data.get("ssh_port", 22)
    password = data.get("ssh_password", "")
    if existing and password == "":
        password = existing.get("ssh_password", "")
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host):
        raise ValueError("Invalid SSH host")
    if not isinstance(user, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", user):
        raise ValueError("Invalid SSH username")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Invalid SSH port")
    if not isinstance(password, str) or len(password) > 4096 or "\0" in password:
        raise ValueError("Invalid SSH password")
    return dict(ssh_enabled=True, ssh_host=host, ssh_username=user, ssh_port=port, ssh_password=password)


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
    for item in data:
        ssh_settings(item)
    return data


def public_connections(path):
    return [{key: item[key] for key in (
        "id", "name", "host", "port", "username", "backup_directory", "trust_certificate",
        "ssh_enabled", "ssh_host", "ssh_username", "ssh_port"
    ) if key in item} for item in connections(path)]


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
    return edit_connection(path, data)


def edit_connection(path, data, connection_id=None):
    items = connections(path)
    existing = next((item for item in items if item["id"] == connection_id), None)
    if connection_id is not None and existing is None:
        raise ValueError("Unknown SQL connection")
    if existing and data.get("password", "") == "":
        data = data | {"password": existing["password"]}
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
    fields.update(id=connection_id or uuid.uuid4().hex, port=port, trust_certificate=trust)
    fields.update(ssh_settings(data | {"ssh_host": fields["host"] if fields["host"] != "." else "localhost"}, existing))
    if fields["ssh_enabled"] and (not fields["backup_directory"].startswith("/") or "\\" in fields["backup_directory"]):
        raise ValueError("Linux SSH backup access requires an absolute Linux backup directory")
    host = fields["host"].strip("[]").rstrip(".").casefold()
    if any(item["id"] != connection_id and item["host"].strip("[]").rstrip(".").casefold() == host and item["port"] == port for item in items):
        raise ValueError("A connection to this SQL host and port already exists")
    if existing:
        items = [fields if item["id"] == connection_id else item for item in items]
    else:
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


def query_json(settings, query, *, preamble=""):
    output = execute(settings, preamble + "\nSELECT COALESCE((" + query + " FOR JSON PATH), N'[]') AS vns_json;", timeout=30)
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
    history = query_json(settings, """
SELECT TOP (200) b.backup_set_id AS id, b.database_name AS database_name,
 CONVERT(varchar(30),b.backup_finish_date,126) AS finished,
 CAST(b.backup_size/1048576.0 AS decimal(18,1)) AS size_mb,
 m.physical_device_name AS path, b.position,
 (SELECT COUNT(*) FROM msdb.dbo.backupmediafamily x WHERE x.media_set_id=b.media_set_id) AS media_count
FROM msdb.dbo.backupset b JOIN msdb.dbo.backupmediafamily m ON m.media_set_id=b.media_set_id
WHERE b.type='D' AND m.family_sequence_number=1 AND m.device_type=2
ORDER BY b.backup_finish_date DESC""")
    if local_backup_access(settings):
        directory = Path(settings["backup_directory"]).resolve()
        history = [item for item in history if not (
            Path(item["path"]).parent.resolve() == directory and not Path(item["path"]).exists()
        )]
    elif settings.get("ssh_enabled"):
        paths = {item["path"]: None for item in history
                 if str(Path(item["path"]).parent) == settings["backup_directory"].rstrip("/")
                 and Path(item["path"]).suffix.casefold() == ".bak"}
        if paths:
            present = file_operation(settings, "inspect", paths, skip_missing=True)
            history = [item for item in history if item["path"] not in paths or item["path"] in present]
    files = query_json(settings,
        "SELECT subdirectory AS name FROM @files WHERE is_file=1 AND LOWER(RIGHT(subdirectory,4))='.bak'",
        preamble=f"""
DECLARE @files TABLE (subdirectory nvarchar(512), depth int, is_file bit);
INSERT INTO @files EXEC master.sys.xp_dirtree {literal(settings["backup_directory"])}, 1, 1;""")
    known_paths = {item["path"] for item in history}
    separator = "\\" if "\\" in settings["backup_directory"] else "/"
    for entry in files:
        filename = entry["name"]
        if not filename or any(char in filename for char in ("/", "\\", "\0")):
            raise ValueError("SQL Server returned an invalid backup filename")
        path = settings["backup_directory"].rstrip("/\\") + separator + filename
        if path in known_paths:
            continue
        try:
            media = backup_table(settings, f"RESTORE LABELONLY FROM DISK={literal(path)};")
            if len(media) != 1:
                raise ValueError("Cannot read backup media information")
            family_count = int(media[0]["FamilyCount"])
            headers = backup_table(settings, f"RESTORE HEADERONLY FROM DISK={literal(path)};")
            full = [header for header in headers if header["BackupType"] == "1"]
            if not full:
                raise ValueError("File contains no full database backup")
            for header in full:
                position = int(header["Position"])
                if position < 1:
                    raise ValueError("Invalid backup position")
                key = path + "\0" + str(position) + "\0" + header["BackupSetGUID"]
                history.append({
                    "id": "file-" + hashlib.sha256(key.encode("utf-8")).hexdigest(),
                    "database_name": header["DatabaseName"], "finished": header["BackupFinishDate"].replace(" ", "T", 1),
                    "size_mb": round(int(header["BackupSize"]) / 1048576, 1),
                    "path": path, "position": position,
                    "media_count": family_count if header.get("IsDamaged") == "0" else 0,
                    "source": "file",
                })
        except (ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f"Cannot read backup file {path}: {exc}", file=sys.stderr, flush=True)
            history.append({"id": "file-" + hashlib.sha256(path.encode("utf-8")).hexdigest(),
                            "database_name": filename, "finished": "", "size_mb": None, "path": path,
                            "position": 0, "media_count": 0, "source": "file", "error": str(exc)})
    return history


def backup_table(settings, query):
    output = execute(settings, query, table=True, timeout=30)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) < 3:
        raise ValueError("SQL Server returned no backup metadata")
    columns = [column.strip() for column in lines[0].split("|")]
    rows = []
    for line in lines[2:]:
        values = [value.strip() for value in line.split("|")]
        if len(values) != len(columns):
            raise ValueError("Invalid backup metadata columns")
        rows.append(dict(zip(columns, values)))
    return rows


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


def validate_restore_login(data):
    username = data.get("login")
    identifier(username)
    if username.casefold() in {"dbo", "guest", "sys", "information_schema"}:
        raise ValueError("Choose a username other than a built-in database user")
    password = data.get("login_password")
    if not isinstance(password, str) or not 8 <= len(password) <= 128 or any(char in password for char in ("\0", "\r", "\n")):
        raise ValueError("The new SQL login password must be 8-128 characters without line breaks")
    return username, password


def local_backup_access(settings):
    if settings["host"].strip() == ".":
        return True
    host = settings["host"].strip("[]").rstrip(".").casefold()
    return host in {"localhost", "127.0.0.1", "::1", socket.gethostname().casefold(), socket.getfqdn().casefold()}


def backup_file_access(settings):
    return local_backup_access(settings) or settings.get("ssh_enabled", False)


def file_operation(settings, operation, files, skip_missing=False):
    data = dict(directory=settings["backup_directory"], operation=operation, files=files, skip_missing=skip_missing)
    if local_backup_access(settings):
        return _file_module.operate(data)
    config = ssh_settings(settings)
    if not config["ssh_enabled"]:
        raise ValueError("Configure Linux SSH access to delete remote backup files")
    ssh = shutil.which("ssh")
    if not ssh:
        raise ValueError("ssh is not installed on the admin server")
    args = [ssh, "-T", "-p", str(config["ssh_port"]), "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2"]
    env = os.environ.copy()
    if config["ssh_password"]:
        sshpass = shutil.which("sshpass")
        if not sshpass:
            raise ValueError("sshpass is required for password login: sudo apt install sshpass")
        args = [sshpass, "-e", *args]
        env["SSHPASS"] = config["ssh_password"]
    else:
        args += ["-o", "BatchMode=yes"]
    source = Path(__file__).with_name("backup_files.py").read_text()
    args += [config["ssh_username"] + "@" + config["ssh_host"], "python3 -c " + shlex.quote(source)]
    result = subprocess.run(args, input=json.dumps(data), capture_output=True, text=True,
                            encoding="utf-8", env=env, timeout=60)
    if result.returncode:
        raise RuntimeError((result.stderr or "Remote backup file operation failed").strip())
    if result.stderr.strip():
        print(result.stderr, file=sys.stderr, end="", flush=True)
    response = json.loads(result.stdout)
    if not isinstance(response, dict) or any(
        not isinstance(path, str) or path not in files or not isinstance(identity, list)
        or len(identity) != 2 or any(type(value) is not int for value in identity)
        for path, identity in response.items()
    ):
        raise ValueError("Invalid remote backup file response")
    if (operation == "delete" or not skip_missing) and set(response) != set(files):
        raise ValueError("Remote backup file operation did not confirm every file")
    for filename in set(files) - set(response):
        print("Backup file no longer exists; skipping: " + filename, flush=True)
    return response


def backup_files_for_deletion(settings, name, backup_id=None):
    if not backup_file_access(settings):
        raise ValueError("Backup file deletion requires a local connection or Linux SSH access")
    records = backups(settings)
    if backup_id is not None:
        records = [item for item in records if item["id"] == backup_id and item["database_name"] == name]
        if not records:
            raise ValueError("Backup no longer exists")
    else:
        records = [item for item in records if item["database_name"].casefold() == name.casefold()]
    paths = {}
    for item in records:
        path = Path(item["path"])
        if path.suffix.casefold() != ".bak" or str(path.parent) != settings["backup_directory"].rstrip("/"):
            raise ValueError("Backup deletion is restricted to regular .bak files in the backup directory")
        paths[str(path)] = None
    checked = file_operation(settings, "inspect", paths, skip_missing=backup_id is None)
    for filename in checked:
        path = Path(filename)
        headers = backup_table(settings, f"RESTORE HEADERONLY FROM DISK={literal(str(path))};")
        if any(header["DatabaseName"].casefold() != name.casefold() for header in headers):
            raise ValueError("Backup file contains backups of another database; deletion refused")
    return checked


def delete_backup_files(paths, settings=None):
    if not paths:
        return
    if settings is not None:
        file_operation(settings, "delete", paths)
        for filename in paths:
            print("Deleted backup file: " + filename, flush=True)
        return
    for filename, identity in paths.items():
        path = Path(filename)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != tuple(identity):
            raise ValueError("Backup file changed before deletion")
        path.unlink()
        print("Deleted backup file: " + filename, flush=True)


def create_restore_login(settings, database, username, password):
    execute(settings, f"""
CREATE LOGIN {identifier(username)} WITH PASSWORD={literal(password)}, CHECK_POLICY=OFF,
 CHECK_EXPIRATION=OFF, DEFAULT_DATABASE={identifier(database)};
USE {identifier(database)};
IF EXISTS (SELECT 1 FROM sys.database_principals WHERE name={literal(username)})
 ALTER USER {identifier(username)} WITH LOGIN={identifier(username)};
ELSE
 CREATE USER {identifier(username)} FOR LOGIN {identifier(username)};
ALTER ROLE [db_owner] ADD MEMBER {identifier(username)};""")
    print("SQL login and db_owner mapping created: " + username, flush=True)


def exclusive_database_logins(settings, database):
    candidates = query_json(settings, f"""
SELECT DISTINCT sp.name
FROM {identifier(database)}.sys.database_principals dp
JOIN sys.server_principals sp ON sp.sid=dp.sid
WHERE dp.type='S' AND dp.principal_id>4 AND sp.type='S' AND sp.principal_id>1
AND sp.name<>{literal(settings["username"])}
AND NOT EXISTS (SELECT 1 FROM sys.server_role_members r WHERE r.member_principal_id=sp.principal_id)
AND NOT EXISTS (SELECT 1 FROM sys.server_permissions p WHERE p.grantee_principal_id=sp.principal_id
 AND p.permission_name<>'CONNECT SQL')""")
    if not candidates:
        return []
    # Inaccessible databases prevent proving that a login is exclusive.
    others = query_json(settings, "SELECT name, state_desc AS state FROM sys.databases WHERE name<>" + literal(database))
    if any(row["state"] != "ONLINE" for row in others):
        print("Keeping SQL logins: another database is offline; shared usage cannot be verified.", flush=True)
        return []
    result = []
    for login in candidates:
        usage = query_json(settings, f"SELECT name FROM sys.databases WHERE name<>{literal(database)} AND owner_sid=SUSER_SID({literal(login['name'])})")
        for row in others:
            usage.extend(query_json(settings, f"""
SELECT name FROM {identifier(row["name"])}.sys.database_principals
WHERE sid=SUSER_SID({literal(login["name"])})"""))
        if usage:
            print("Keeping shared SQL login: " + login["name"], flush=True)
        else:
            result.append(login["name"])
    return result


def run_action(settings, data):
    if not isinstance(data, dict):
        raise ValueError("Expected a SQL action object")
    action = data.get("action")
    if action not in ("backup_all", "create", "backup", "drop", "copy", "restore", "delete_backup"):
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
    if action == "delete_backup":
        if data.get("confirmation") != name or not isinstance(data.get("backup_id"), (int, str)) or isinstance(data.get("backup_id"), bool):
            raise ValueError("Confirm the database name and select a backup")
        delete_backup_files(backup_files_for_deletion(settings, name, data["backup_id"]), settings)
        return
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
        if not isinstance(data.get("delete_backups", False), bool):
            raise ValueError("Invalid backup deletion option")
        files_to_delete = backup_files_for_deletion(settings, name) if data.get("delete_backups") else {}
        if not isinstance(data.get("delete_login", True), bool):
            raise ValueError("Invalid login deletion option")
        logins_to_delete = exclusive_database_logins(settings, name) if data.get("delete_login", True) else []
        execute(settings, f"""
ALTER DATABASE {identifier(name)} SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
BEGIN TRY
 DROP DATABASE {identifier(name)};
END TRY
BEGIN CATCH
 ALTER DATABASE {identifier(name)} SET MULTI_USER;
 THROW;
END CATCH;""")
        for login in logins_to_delete:
            execute(settings, "DROP LOGIN " + identifier(login) + ";")
            print("Deleted SQL login: " + login, flush=True)
        delete_backup_files(files_to_delete, settings)
    elif action in ("copy", "restore"):
        target = data.get("target", "")
        identifier(target)
        existing = next((row for row in rows if row["name"].casefold() == target.casefold()), None)
        if existing and existing["id"] <= 4:
            raise ValueError("Cannot overwrite a system database")
        overwrite = existing is not None
        if overwrite and (action == "copy" or data.get("confirmation") != target or data.get("overwrite") is not True):
            raise ValueError("Destination exists. Explicit overwrite confirmation is required for restore.")
        login = None
        if action == "copy":
            login = validate_restore_login(data)
            if query_json(settings, "SELECT name FROM sys.server_principals WHERE name=" + literal(login[0])):
                raise ValueError("SQL login already exists. Choose another username before copying/restoring.")
        if action == "copy":
            path, position = backup(settings, name), 1
        else:
            backup_id = data.get("backup_id")
            if isinstance(backup_id, bool) or not (
                isinstance(backup_id, int) or isinstance(backup_id, str) and re.fullmatch(r"file-[0-9a-f]{64}", backup_id)
            ):
                raise ValueError("Select a valid backup")
            selected = next((item for item in backups(settings) if item["id"] == data.get("backup_id")), None)
            if not selected or selected["media_count"] != 1:
                raise ValueError("Backup not found or striped backups are unsupported")
            path, position = selected["path"], selected["position"]
        if action == "restore" and not overwrite:
            login = validate_restore_login(data)
            if query_json(settings, "SELECT name FROM sys.server_principals WHERE name=" + literal(login[0])):
                raise ValueError("SQL login already exists. Choose another username before restoring.")
        restore(settings, target, path, position, overwrite)
        if login:
            create_restore_login(settings, target, *login)
    print("OK: " + action, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("store")
    parser.add_argument("connection")
    parser.add_argument("payload")
    args = parser.parse_args()
    try:
        payload = json.loads(args.payload)
        password = os.environ.pop("VNS_SQL_LOGIN_PASSWORD", None)
        if password is not None:
            payload["login_password"] = password
        run_action(connection(args.store, args.connection), payload)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
