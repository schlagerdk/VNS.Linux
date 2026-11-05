"""File operations confined to one remote production directory; run over SSH."""
import base64
import json
import os
from pathlib import Path
import shutil
import sys


def handle(root, data):
    action = data["action"]
    relative = data.get("path", "")
    path = root / relative
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Path outside the remote site")
    if action in ("delete", "rename") and resolved == root:
        raise ValueError("Use Delete production site to remove the site")
    if action == "list":
        items = []
        for entry in path.iterdir():
            info = entry.lstat()
            is_dir = entry.is_dir() and not entry.is_symlink()
            items.append({"name": entry.name, "dir": is_dir, "size": 0 if is_dir else info.st_size,
                          "modified": int(info.st_mtime)})
        return {"path": relative, "items": sorted(items, key=lambda item: (not item["dir"], item["name"].lower()))}
    if action in ("read", "download"):
        if action == "read" and path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("The file is too large to edit here. Download it instead.")
        content = path.read_bytes()
        if action == "download":
            return {"content": base64.b64encode(content).decode("ascii")}
        if b"\0" in content:
            raise ValueError("Binary file. Download it instead.")
        return {"content": content.decode("utf-8", errors="replace")}
    if action in ("mkdir", "newfile", "rename"):
        name = data.get("name", "")
        if not name or name in (".", "..") or "/" in name or "\0" in name:
            raise ValueError("Invalid name")
        target = (path.parent if action == "rename" else path) / name
        if target.exists() or target.is_symlink():
            raise ValueError("It already exists")
        if action == "rename":
            path.rename(target)
        else:
            if action == "mkdir":
                target.mkdir()
            else:
                target.touch(exist_ok=False)
            parent = path.stat()
            os.chown(target, parent.st_uid, parent.st_gid)
    elif action == "save":
        existed = path.exists()
        path.write_text(data["content"], encoding="utf-8")
        if not existed:
            parent = path.parent.stat()
            os.chown(path, parent.st_uid, parent.st_gid)
    elif action == "upload":
        for upload in data["files"]:
            name = upload["name"]
            if not name or name in (".", "..") or "/" in name or "\0" in name:
                raise ValueError("Invalid filename")
            target = path / name
            if not target.resolve().is_relative_to(root):
                raise ValueError("Path outside the remote site")
            existed = target.exists()
            target.write_bytes(base64.b64decode(upload["content"], validate=True))
            if not existed:
                parent = path.stat()
                os.chown(target, parent.st_uid, parent.st_gid)
        return {"ok": True, "count": len(data["files"])}
    elif action == "delete":
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            raise ValueError("Not found")
    else:
        raise ValueError("Unknown file action")
    return {"ok": True}


if __name__ == "__main__":
    try:
        root = Path("/var/www", os.environ["SITE"])
        if root.is_symlink() or root.resolve() != root:
            raise ValueError("The remote site root must not be a symbolic link")
        result = handle(root, json.load(sys.stdin))
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
    print(json.dumps(result))
