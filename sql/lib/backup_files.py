"""Restricted backup-file operations, also run on Linux over SSH."""
import json
import os
from pathlib import Path
import stat
import sys


def operate(data):
    root = Path(data["directory"]).resolve(strict=True)
    checked = {}
    for filename, identity in data["files"].items():
        path = Path(filename)
        if not path.is_absolute() or path.suffix.casefold() != ".bak" or path.parent.resolve(strict=True) != root:
            raise ValueError("Backup deletion is restricted to .bak files in the backup directory")
        try:
            info = path.lstat()
        except FileNotFoundError:
            if data["operation"] == "inspect" and data.get("skip_missing"):
                print("Backup file no longer exists; skipping: " + filename, file=sys.stderr)
                continue
            raise
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Backup is not a regular file (symlinks are not allowed)")
        current = [info.st_dev, info.st_ino]
        if data["operation"] == "delete" and list(identity) != current:
            raise ValueError("Backup file changed before deletion")
        checked[str(path)] = current
    if data["operation"] == "delete":
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for filename, identity in checked.items():
                name = Path(filename).name
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or [info.st_dev, info.st_ino] != identity:
                    raise ValueError("Backup file changed before deletion")
                os.unlink(name, dir_fd=descriptor)
        finally:
            os.close(descriptor)
    elif data["operation"] != "inspect":
        raise ValueError("Unknown backup file operation")
    return checked


if __name__ == "__main__":
    try:
        if sys.platform != "linux":
            raise ValueError("Remote backup file access requires a Linux server")
        print(json.dumps(operate(json.load(sys.stdin))))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("Backup file operation failed: " + str(exc), file=sys.stderr)
        sys.exit(1)
