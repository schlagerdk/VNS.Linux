"""Identify configured .NET database providers without exposing connection strings."""
import json
from pathlib import Path
import re


def configuration_json(text):
    # ASP.NET configuration permits comments and trailing commas outside strings.
    tokens = r'"(?:\\.|[^"\\])*"|//[^\r\n]*|/\*[\s\S]*?\*/'
    text = re.sub(tokens, lambda match: match[0] if match[0].startswith('"') else "", text)
    text = re.sub(r'"(?:\\.|[^"\\])*"|,\s*(?=[}\]])',
                  lambda match: match[0] if match[0].startswith('"') else "", text)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Expected an appsettings object")
    return data


def connection_fields(connection):
    fields = {}
    pattern = r'''(?:^|;)\s*([^=;]+)\s*=\s*("(?:[^"]|"")*"|'(?:[^']|'')*'|[^;]*)'''
    for match in re.finditer(pattern, connection):
        value = match[2].strip()
        if value[:1] in ('"', "'") and value[-1:] == value[:1]:
            quote = value[0]
            value = value[1:-1]
            value = value.replace(quote * 2, quote)
        fields[match[1].strip().lower()] = value
    return fields


def connection_type(connection, provider):
    if "sqlite" in provider:
        return "SQLite"
    if provider and not any(name in provider for name in ("sqlclient", "sqlserver")):
        return "Unknown"
    fields = {key: value.lower() for key, value in connection_fields(connection).items()}
    server = next((fields[key] for key in ("server", "data source", "datasource", "filename", "address", "addr", "network address")
                   if key in fields), "")
    if not provider and (server.endswith((".db", ".sqlite", ".sqlite3")) or server == ":memory:"):
        return "SQLite"
    sql = bool(provider) or any(key in fields for key in (
        "server", "initial catalog", "database", "integrated security", "trusted_connection"))
    if not sql:
        return "Unknown"
    host = re.sub(r"^(?:tcp|np|lpc):", "", server).split(",", 1)[0].rstrip(".")
    if any(host.endswith(suffix) for suffix in (
            ".database.windows.net", ".database.usgovcloudapi.net",
            ".database.chinacloudapi.cn", ".database.cloudapi.de")):
        return "Azure SQL"
    if re.search(r"\\sqlexpress(?:$|\\)", host):
        return "SQL Express"
    return "MS SQL"


def database_details(root, environment="", variables=None):
    values = {}
    environment = environment or "Production"

    def flatten(data, prefix=""):
        for key, value in data.items():
            name = (prefix + "__" + key if prefix else key).lower()
            if isinstance(value, dict):
                flatten(value, name)
            else:
                values[name] = value

    paths = [Path(root) / "appsettings.json"]
    if environment and "/" not in environment and "\\" not in environment:
        paths.append(Path(root) / ("appsettings." + environment + ".json"))
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            continue
        flatten(configuration_json(text))
    for key, value in (variables or {}).items():
        values[key.replace(":", "__").lower()] = value
    connections = {key: value for key, value in values.items()
                   if key.startswith("connectionstrings__") and isinstance(value, str)}
    providers = set()
    names = set()
    servers = set()
    for key, value in connections.items():
        if not value.strip():
            continue
        if key.endswith("_providername"):
            continue
        provider = connections.get(key + "_providername", "").lower()
        kind = connection_type(value, provider)
        providers.add(kind)
        fields = connection_fields(value)
        if kind == "SQLite":
            name = fields.get("data source") or fields.get("datasource") or fields.get("filename", "")
            # Keep runtime substitutions and URI sources literal rather than guessing their location.
            if name and name != ":memory:" and "|" not in name and not name.startswith("file:"):
                name = str(Path(root) / name)
        else:
            name = fields.get("initial catalog") or fields.get("database", "")
            if kind in ("MS SQL", "SQL Express", "Azure SQL"):
                server = next((fields[key] for key in ("server", "data source", "address", "addr", "network address")
                               if fields.get(key)), "")
                if server:
                    servers.add(server)
        if name:
            names.add(name)
    return {"database": next(iter(providers)) if len(providers) == 1 else "Unknown",
            "database_name": " / ".join(sorted(names)),
            "database_server": " / ".join(sorted(servers))}


def database_type(root, environment="", variables=None):
    return database_details(root, environment, variables)["database"]
