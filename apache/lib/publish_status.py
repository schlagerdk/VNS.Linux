"""Track .NET project configuration for publishes and package update checks."""
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


def record(root, scope, value, **metadata):
    folder = Path(root) / ".vns-publish"
    folder.mkdir(mode=0o700, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=folder, prefix=scope + "-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump({"fingerprint": value, **metadata}, stream)
            stream.write("\n")
        os.replace(temporary, folder / (scope + ".json"))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def updates_status(root, current):
    try:
        data = json.loads((Path(root) / ".vns-publish/updates.json").read_text())
    except FileNotFoundError:
        return ""
    if not isinstance(data, dict) or not isinstance(data.get("fingerprint"), str) or data.get("status") not in ("current", "available", "error"):
        raise ValueError("Invalid package update status")
    return data["status"] if data.get("fingerprint") == current else ""


def check_updates(root):
    value = fingerprint(root)
    available = False
    try:
        for project in sorted(Path(root).glob("*.csproj")):
            result = subprocess.run(
                ["dotnet", "list", str(project), "package", "--outdated"],
                cwd=root, env=os.environ | {"DOTNET_CLI_UI_LANGUAGE": "en-US", "VSLANG": "1033"},
                capture_output=True, text=True, timeout=180, check=True)
            if not result.stdout.strip():
                raise ValueError("Package update check returned no output")
            available |= "has no updates given the current sources" not in result.stdout.lower()
        state = "available" if available else "current"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        record(root, "updates", value, status="error")
        raise RuntimeError("Package update check failed for " + Path(root).name) from exc
    record(root, "updates", value, status=state)
    return state


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
