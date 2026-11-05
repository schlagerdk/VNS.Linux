"""Create or update the admin site configuration (login, secret key, paths).

  manage.py init --user NAME --admin-site SITE --web-root /var/www --apache-dir DIR [--insecure-cookies]
      The password is read from the VNS_ADMIN_PASSWORD environment variable (never from the command line).
  manage.py set-password       Prompts for a new password. Restart the service afterwards.
"""
import argparse
import getpass
import json
import os
import secrets
import sys

from werkzeug.security import generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("VNS_ADMIN_CONFIG", os.path.join(BASE, "config.json"))
MIN_PASSWORD = 12


def save(cfg):
    fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(CONFIG_PATH, 0o600)


def check_password(password):
    if len(password) < MIN_PASSWORD:
        sys.exit("The password must be at least %d characters." % MIN_PASSWORD)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--user", required=True)
    init.add_argument("--admin-site", required=True)
    init.add_argument("--web-root", default="/var/www")
    init.add_argument("--apache-dir", required=True)
    init.add_argument("--insecure-cookies", action="store_true")
    sub.add_parser("set-password")
    args = parser.parse_args()

    if args.command == "init":
        password = os.environ.get("VNS_ADMIN_PASSWORD", "")
        check_password(password)
        save({
            "user": args.user,
            "password_hash": generate_password_hash(password),
            "secret_key": secrets.token_hex(32),
            "admin_site": args.admin_site,
            "web_root": args.web_root,
            "apache_dir": args.apache_dir,
            "insecure_cookies": args.insecure_cookies,
        })
        return

    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    password = getpass.getpass("New password: ")
    if password != getpass.getpass("Repeat password: "):
        sys.exit("The passwords do not match.")
    check_password(password)
    cfg["password_hash"] = generate_password_hash(password)
    # A new secret key also signs out every existing session
    cfg["secret_key"] = secrets.token_hex(32)
    save(cfg)
    print("Password updated.")


if __name__ == "__main__":
    main()
