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


def updates_details(root, current):
    try:
        data = json.loads((Path(root) / ".vns-publish/updates.json").read_text())
    except FileNotFoundError:
        return {"status": "", "packages": []}
    if not isinstance(data, dict) or not isinstance(data.get("fingerprint"), str) or data.get("status") not in ("current", "available", "error"):
        raise ValueError("Invalid package update status")
    packages = data.get("packages", [])
    if not isinstance(packages, list) or any(
        not isinstance(item, dict) or any(not isinstance(item.get(key), str) or not item[key]
                                         for key in ("name", "current", "latest"))
        for item in packages
    ):
        raise ValueError("Invalid package update details")
    return {"status": data["status"], "packages": packages} if data["fingerprint"] == current else {"status": "", "packages": []}


def updates_status(root, current):
    return updates_details(root, current)["status"]


def parse_updates(output):
    data = json.loads(output)
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("projects"), list) or not data["projects"]:
        raise ValueError("Invalid package update response")
    packages = {}
    for project in data["projects"]:
        if not isinstance(project, dict) or not isinstance(project.get("frameworks", []), list):
            raise ValueError("Invalid package update project")
        for framework in project.get("frameworks", []):
            if not isinstance(framework, dict) or not isinstance(framework.get("topLevelPackages", []), list):
                raise ValueError("Invalid package update framework")
            for item in framework.get("topLevelPackages", []):
                if not isinstance(item, dict) or any(
                    not isinstance(item.get(key), str) or not item[key]
                    for key in ("id", "resolvedVersion", "latestVersion")
                ):
                    raise ValueError("Invalid package update entry")
                name = item["id"]
                entry = {"name": name, "current": item["resolvedVersion"], "latest": item["latestVersion"]}
                if name.lower() in packages and packages[name.lower()] != entry:
                    raise ValueError("Conflicting package versions across target frameworks: " + name)
                packages[name.lower()] = entry
    return sorted(packages.values(), key=lambda item: item["name"].lower())


def check_updates(root, show_output=False):
    value = fingerprint(root)
    available = False
    packages = {}
    try:
        for project in sorted(Path(root).glob("*.csproj")):
            result = subprocess.run(
                ["dotnet", "list", str(project), "package", "--outdated", "--format", "json", "--output-version", "1"],
                cwd=root, env=os.environ | {"DOTNET_CLI_UI_LANGUAGE": "en-US", "VSLANG": "1033"},
                capture_output=True, text=True, timeout=180, check=True)
            found = parse_updates(result.stdout)
            for item in found:
                key = item["name"].lower()
                if key in packages and packages[key] != item:
                    raise ValueError("Conflicting package versions across projects: " + item["name"])
                packages[key] = item
            available |= bool(found)
            if show_output:
                print(f"{project.name}: " + ("Updates available" if found else "No updates available"), flush=True)
                for item in found:
                    print(f'{item["name"]}: {item["current"]} -> {item["latest"]}', flush=True)
                if result.stderr:
                    print(result.stderr, end="", file=sys.stderr, flush=True)
        state = "available" if available else "current"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        if show_output and isinstance(exc, subprocess.CalledProcessError):
            if exc.stdout:
                print(exc.stdout, end="", flush=True)
            if exc.stderr:
                print(exc.stderr, end="", file=sys.stderr, flush=True)
        record(root, "updates", value, status="error")
        raise RuntimeError("Package update check failed for " + Path(root).name + ": " + str(exc)) from exc
    record(root, "updates", value, status=state, packages=list(packages.values()))
    return state


def upgrade_packages(root, name=None):
    projects = sorted(Path(root).glob("*.csproj"))
    if len(projects) != 1:
        raise ValueError("Expected exactly one C# project")
    details = updates_details(root, fingerprint(root))
    packages = details["packages"]
    if details["status"] != "available" or not packages:
        raise ValueError("Run Check before updating packages")
    if name:
        packages = [item for item in packages if item["name"].lower() == name.lower()]
        if not packages:
            raise ValueError("Package has no checked update: " + name)
    for item in packages:
        print(f'Update {item["name"]}: {item["current"]} -> {item["latest"]}', flush=True)
        result = subprocess.run(
            ["dotnet", "add", str(projects[0]), "package", item["name"], "--version", item["latest"]],
            cwd=root)
        if result.returncode:
            return result.returncode if result.returncode > 0 else 128 - result.returncode
    check_updates(root, show_output=True)
    return 0


def run_publish(root, scope, command):
    value = fingerprint(root)
    result = subprocess.run(command, cwd=root, env=os.environ | {"VNS_PUBLISH_TRACKED": scope})
    if result.returncode == 0:
        record(root, scope, value)
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


def main(args):
    if len(args) in (2, 3) and args[0] == "upgrade":
        return upgrade_packages(args[1], args[2] if len(args) == 3 else None)
    if len(args) == 2 and args[0] == "check":
        check_updates(args[1], show_output=True)
        return 0
    if len(args) < 4 or args[0] not in ("run", "both"):
        raise ValueError("Usage: publish_status.py check ROOT | upgrade ROOT [PACKAGE] | run ROOT test|prod COMMAND... | both ROOT LOCAL_SCRIPT PROD_SCRIPT")
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
    except (OSError, ValueError, RuntimeError) as exc:
        print("ERROR: Publish tracking failed: " + str(exc), file=sys.stderr)
        sys.exit(1)
