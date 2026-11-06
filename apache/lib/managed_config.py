"""Read, save and apply allowlisted site configuration files."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SERVICE_DIR = Path("/etc/systemd/system")
APACHE_DIR = Path("/etc/apache2/sites-available")


def handle(unit, data):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", unit):
        raise ValueError("Invalid site unit")
    kind = data.get("kind")
    if kind not in ("service", "conf"):
        raise ValueError("Unknown configuration type")
    path = SERVICE_DIR / (unit + ".service") if kind == "service" else APACHE_DIR / (unit + ".conf")
    if not path.is_file():
        raise ValueError("Configuration file not found: " + str(path))
    action = data.get("action")
    if action == "read":
        encoding = data.get("encoding", "utf-8")
        if encoding not in ("utf-8", "cp1252", "cp850", "iso-8859-1"):
            raise ValueError("Unsupported configuration encoding")
        try:
            content = path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            if encoding != "utf-8":
                raise ValueError(f"Cannot decode this file as {encoding}. Select another encoding.") from exc
            return {"path": str(path), "encoding_required": True,
                    "message": f"This file is not valid UTF-8 (byte {exc.start}). Select its original encoding; no changes have been made."}
        return {"path": str(path), "content": content, "encoding": encoding}
    if action == "save":
        content = data.get("content")
        if not isinstance(content, str) or "\0" in content:
            raise ValueError("Expected text configuration content")
        # Write in place to preserve ownership and permissions.
        path.write_text(content, encoding="utf-8")
        return {"ok": True}
    if action == "apply":
        commands = ([["systemctl", "daemon-reload"], ["systemctl", "restart", unit + ".service"]]
                    if kind == "service" else [["apache2ctl", "configtest"], ["systemctl", "reload", "apache2"]])
        output = []
        for command in commands:
            result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=True)
            output.append(result.stdout + result.stderr)
        return {"ok": True, "output": "\n".join(output)}
    raise ValueError("Unknown configuration action")


if __name__ == "__main__":
    try:
        result = handle(os.environ["SITE"], json.load(sys.stdin))
    except subprocess.CalledProcessError as exc:
        print(json.dumps({"error": (exc.stderr or exc.stdout or str(exc)).strip()}))
        sys.exit(1)
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
    print(json.dumps(result))
