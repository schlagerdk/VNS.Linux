"""Repair application content permissions without following symbolic links."""
import os
from pathlib import Path
import pwd
import grp
import re
import stat


def repair(root, service, uid, gid, web_root=Path("/var/www")):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Site folder is missing or is a symbolic link")
    root = root.resolve()
    web_root = web_root.resolve()
    if root == web_root or not root.is_relative_to(web_root):
        raise ValueError("Expected a site folder inside /var/www")
    if (root / "app/manage.py").exists() and (root / "app/config.json").exists():
        raise ValueError("Admin site permissions must remain root-owned")
    target = root
    if service.is_file():
        text = service.read_text()
        match = re.search(r"^WorkingDirectory=(.+)$", text, re.M)
        if not match:
            raise ValueError("Service WorkingDirectory is missing")
        target = Path(match.group(1).strip().strip('"'))
        if not target.is_absolute() or target.is_symlink():
            raise ValueError("Invalid service WorkingDirectory")
        target = target.resolve(strict=True)
        if not target.is_relative_to(root):
            raise ValueError("Service WorkingDirectory is outside the site")
        if (target / "manage.py").exists() and (target / "config.json").exists():
            raise ValueError("Admin site permissions must remain root-owned")
    elif (root / "app").is_dir():
        raise ValueError("App folder found without a service; cannot safely select content")
    if not target.is_dir():
        raise ValueError("Content folder is not a directory")
    changed = 0

    def update(path):
        nonlocal changed
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            return
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            return
        mode = 0o750 if stat.S_ISDIR(info.st_mode) or info.st_mode & 0o111 else 0o640
        os.chown(path, uid, gid, follow_symlinks=False)
        os.chmod(path, mode)
        changed += 1

    update(target)
    def walk_error(error):
        raise error

    for directory, folders, files in os.walk(target, followlinks=False, onerror=walk_error):
        folders[:] = [name for name in folders if name not in ("scripts", ".git", "venv")
                      and not (Path(directory) / name).is_symlink()]
        for name in folders + files:
            if name in ("scripts", ".git", ".env") or name.startswith(".env."):
                continue
            update(Path(directory) / name)
    print("Permissions fixed on %s (%d entries). No service restart required." % (target, changed))


if __name__ == "__main__":
    site = os.environ["SITE"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", site):
        raise ValueError("Invalid site name")
    repair(Path(os.environ["ROOT"]), Path("/etc/systemd/system") / (site + ".service"),
           pwd.getpwnam("www-data").pw_uid, grp.getgrnam("www-data").gr_gid)
