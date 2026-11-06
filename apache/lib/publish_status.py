"""Track the project configuration used by each successful .NET publish."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def fingerprint(root):
    root = Path(root).resolve()
    projects = sorted(root.glob("*.csproj"))
    if not projects:
        raise ValueError("Publish tracking requires a C# project")
    files = list(projects)
    for folder in (root, *root.parents):
        for name in ("Directory.Packages.props", "Directory.Build.props", "Directory.Build.targets"):
            path = folder / name
            if path.is_file():
                files.append(path)
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root) if path.is_relative_to(root) else path).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def status(root, scope, current):
    path = Path(root) / ".vns-publish" / (scope + ".json")
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return "unknown"
    if not isinstance(data, dict) or not isinstance(data.get("fingerprint"), str):
        raise ValueError("Invalid publish status: " + str(path))
    return "current" if data["fingerprint"] == current else "pending"


def record(root, scope, value):
    folder = Path(root) / ".vns-publish"
    folder.mkdir(mode=0o700, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=folder, prefix=scope + "-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump({"fingerprint": value}, stream)
            stream.write("\n")
        os.replace(temporary, folder / (scope + ".json"))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_publish(root, scope, command):
    value = fingerprint(root)
    result = subprocess.run(command, cwd=root, env=os.environ | {"VNS_PUBLISH_TRACKED": scope})
    if result.returncode == 0:
        record(root, scope, value)
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


def main(args):
    if len(args) < 4 or args[0] not in ("run", "both"):
        raise ValueError("Usage: publish_status.py run ROOT test|prod COMMAND... | both ROOT LOCAL_SCRIPT PROD_SCRIPT")
    action, root = args[:2]
    if action == "both":
        if len(args) != 4:
            raise ValueError("Expected test and production publish scripts")
        result = run_publish(root, "test", ["/bin/bash", args[2]])
        return result or run_publish(root, "prod", ["/bin/bash", args[3]])
    scope = args[2]
    if scope not in ("test", "prod"):
        raise ValueError("Invalid publish scope")
    return run_publish(root, scope, args[3:])


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, ValueError) as exc:
        print("ERROR: Publish tracking failed: " + str(exc), file=sys.stderr)
        sys.exit(1)
