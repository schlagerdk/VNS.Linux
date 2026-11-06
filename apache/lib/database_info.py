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


def database_type(root, environment="", variables=None):
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
    for key, value in connections.items():
        if not value.strip():
            continue
        lower = value.lower()
        if key.endswith("_providername"):
            continue
        provider = connections.get(key + "_providername", "").lower()
        if "sqlite" in provider:
            providers.add("SQLite")
        elif "sqlclient" in provider or "sqlserver" in provider:
            providers.add("SQL Server")
        elif provider:
            continue
        elif re.search(r"(?:^|;)\s*(?:server|initial catalog|database|integrated security|trusted_connection)\s*=", lower):
            providers.add("SQL Server")
        elif re.search(r"(?:^|;)\s*data source\s*=\s*[^;]*(?:\.db|\.sqlite|\.sqlite3)(?:[;]|$)", lower) or ":memory:" in lower:
            providers.add("SQLite")
    return " / ".join(sorted(providers))
