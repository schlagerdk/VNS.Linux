"""VNS admin site: manage the sites in /var/www, run the VNS scripts, open shells and edit files.

Runs as root behind Apache (HTTPS). Everything except /login and /static requires a logged-in session.
"""
import codecs
import base64
import glob
import hmac
import io
import importlib.util
import json
import os
import socket
import queue
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import tempfile
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import ptyprocess
from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session
from flask_sock import Sock
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("VNS_ADMIN_CONFIG", os.path.join(BASE, "config.json"))
with open(CONFIG_PATH) as _f:
    cfg = json.load(_f)

WEB_ROOT = os.path.realpath(cfg.get("web_root", "/var/www"))
APACHE_DIR = cfg["apache_dir"]
_files_spec = importlib.util.spec_from_file_location("vns_remote_files", os.path.join(APACHE_DIR, "lib", "remote_files.py"))
_files_module = importlib.util.module_from_spec(_files_spec)
_files_spec.loader.exec_module(_files_module)
editable_file = _files_module.editable_file
contains_editable_files = _files_module.contains_editable_files
_publish_spec = importlib.util.spec_from_file_location("vns_publish_status", os.path.join(APACHE_DIR, "lib", "publish_status.py"))
_publish_module = importlib.util.module_from_spec(_publish_spec)
_publish_spec.loader.exec_module(_publish_module)
_database_spec = importlib.util.spec_from_file_location("vns_database_info", os.path.join(APACHE_DIR, "lib", "database_info.py"))
_database_module = importlib.util.module_from_spec(_database_spec)
_database_spec.loader.exec_module(_database_module)
ADMIN_SITE = cfg.get("admin_site", "")
APACHE_SITES = "/etc/apache2/sites-available"
APACHE_ENABLED = "/etc/apache2/sites-enabled"
SERVICE_DIR = "/etc/systemd/system"

app = Flask(__name__)
app.config.update(
    SECRET_KEY=cfg["secret_key"],
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=not cfg.get("insecure_cookies", False),
    PERMANENT_SESSION_LIFETIME=2 * 60 * 60,  # idle timeout: the session cookie is refreshed on every request
    MAX_CONTENT_LENGTH=500 * 1024 * 1024,
)
sock = Sock(app)

NAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*$")
BINDING_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$")
PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
FOLDER_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")
VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+-]*$")
PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SCRIPT_RE = re.compile(r"^[A-Za-z0-9_.-]+\.sh$")


# ---------------------------------------------------------------- security

FAILS = {}
GLOBAL_FAILS = []
FAIL_LOCK = threading.Lock()
WINDOW = 15 * 60
MAX_FAILS_PER_IP = 5
MAX_FAILS_GLOBAL = 60


def client_ip():
    ip = request.remote_addr or ""
    if ip in ("127.0.0.1", "::1"):
        # Apache appends the real client address as the last X-Forwarded-For entry
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            ip = forwarded.split(",")[-1].strip()
    return ip


def login_locked(ip):
    now = time.time()
    with FAIL_LOCK:
        recent = [t for t in FAILS.get(ip, []) if now - t < WINDOW]
        FAILS[ip] = recent
        GLOBAL_FAILS[:] = [t for t in GLOBAL_FAILS if now - t < WINDOW]
        return len(recent) >= MAX_FAILS_PER_IP or len(GLOBAL_FAILS) >= MAX_FAILS_GLOBAL


def record_failure(ip):
    now = time.time()
    with FAIL_LOCK:
        FAILS.setdefault(ip, []).append(now)
        GLOBAL_FAILS.append(now)


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.before_request
def protect():
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf", "")
        if not hmac.compare_digest(sent, session.get("csrf", "")):
            return jsonify(error="Invalid CSRF token"), 403
    if request.path.startswith("/ws/") and urlparse(request.headers.get("Origin", "")).netloc != request.headers.get("Host", ""):
        return jsonify(error="Bad origin"), 403
    public = request.path == "/login" or request.path.startswith("/static/")
    if not public and not session.get("user"):
        if request.path.startswith("/api/") or request.path.startswith("/ws/"):
            return jsonify(error="Not logged in"), 401
        return redirect("/login")


@app.after_request
def headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; "
        "worker-src 'self' blob:; connect-src 'self' ws: wss:; frame-ancestors 'none'"
    )
    if not request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        ip = client_ip()
        if login_locked(ip):
            error = "Too many failed attempts. Try again later."
        else:
            user_ok = hmac.compare_digest(request.form.get("username", ""), cfg["user"])
            password_ok = check_password_hash(cfg["password_hash"], request.form.get("password", ""))
            if user_ok and password_ok:
                session.clear()
                session.permanent = True
                session["user"] = cfg["user"]
                csrf_token()
                with FAIL_LOCK:
                    FAILS.pop(ip, None)
                return redirect("/")
            record_failure(ip)
            time.sleep(1)
            error = "Wrong username or password."
    return render_template("login.html", csrf=csrf_token(), error=error), (429 if "Too many" in error else 200)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect("/login")


@app.route("/")
def index():
    return render_template("index.html", csrf=csrf_token(), admin_site=ADMIN_SITE, hostname=socket.gethostname(), user=session["user"])


# ------------------------------------------------------------------ helpers

@app.errorhandler(HTTPException)
def http_error(exc):
    if request.path.startswith("/api/"):
        return jsonify(error=exc.description), exc.code
    return exc


def error(message, status=400):
    return jsonify(error=message), status


def site_dirs():
    names = []
    for name in sorted(os.listdir(WEB_ROOT)):
        if name.startswith(".") or not NAME_RE.match(name):
            continue
        if os.path.isdir(os.path.join(WEB_ROOT, name)) and not os.path.islink(os.path.join(WEB_ROOT, name)):
            names.append(name)
    return names


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


PROD_KEYS = ("PROD_LOCAL_PATH", "PROD_IP", "PROD_USER", "PROD_SSH_PORT", "PROD_FOLDER", "PROD_BINDING", "PROD_PORT_HTTP")


def parse_prod(root):
    """A site has a production site when scripts/publish_prod.sh exists; its settings block has the details.
    The password is never read."""
    text = read_text(os.path.join(root, "scripts", "publish_prod.sh"))
    if not text:
        return None
    values = {}
    for key in PROD_KEYS:
        match = re.search(r"^%s=(\S*)" % key, text, re.M)
        value = match.group(1).strip("'\"") if match else ""
        values[key] = "" if value.startswith("@") else value
    return values


def conf_binding(unit):
    match = re.search(r"^\s*ServerName\s+(\S+)", read_text(os.path.join(APACHE_SITES, unit + ".conf")), re.M)
    return match.group(1) if match else ""


def service_port(unit):
    match = re.search(r"(?:localhost|127\.0\.0\.1):(\d+)", read_text(os.path.join(SERVICE_DIR, unit + ".service")))
    return match.group(1) if match else ""


def unit_of(name):
    return os.path.isfile(os.path.join(SERVICE_DIR, name + ".service"))


def https_status(unit):
    text = read_text(os.path.join(APACHE_SITES, unit + ".conf"))
    secure = bool(re.search(r"<VirtualHost\s+[^>]*:443>", text))
    certificate = re.search(r'^\s*SSLCertificateFile\s+"?([^"\s]+)', text, re.M)
    return {"scheme": "https" if secure else "http",
            "https": secure and bool(certificate and os.path.isfile(certificate.group(1)))}


REMOTE_STATUS = {}


def remote_command(site, action, timeout=60, **kwargs):
    try:
        result = subprocess.run(
            ["/bin/bash", os.path.join(APACHE_DIR, "remote_site.sh"), site, action],
            capture_output=True, timeout=timeout, **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        app.logger.error("Remote %s failed for %s: %s", action, site, exc)
        abort(502, "Could not connect to the remote server")
    if result.returncode:
        message = result.stderr or result.stdout
        if isinstance(message, bytes):
            message = message.decode("utf-8", errors="replace")
        if action == "config" and result.stdout:
            try:
                details = json.loads(result.stdout)
                if isinstance(details, dict) and isinstance(details.get("error"), str):
                    message = details["error"]
            except ValueError:
                pass
        app.logger.error("Remote %s failed for %s: %s", action, site, message)
        abort(502, message.strip() or "Remote action failed")
    return result


def remote_status(site):
    cached = REMOTE_STATUS.get(site)
    if cached and time.time() - cached[0] < 15:
        return cached[1]
    try:
        result = remote_command(site, "status", timeout=20, text=True)
        status = json.loads(result.stdout)
        if not isinstance(status, dict) or not {"unit", "state", "scheme", "https", "exists"} <= status.keys():
            raise ValueError("Invalid remote status response")
        if not status.get("binding"):
            status.pop("binding", None)
    except HTTPException as exc:
        status = {"https": None, "state": "unknown", "remote_error": exc.description}
    except (ValueError, TypeError) as exc:
        app.logger.error("Invalid remote status for %s: %s", site, exc)
        status = {"https": None, "state": "unknown", "remote_error": "Invalid remote status response"}
    REMOTE_STATUS[site] = (time.time(), status)
    return status


def service_environment(unit):
    try:
        result = subprocess.run(
            ["systemctl", "show", unit + ".service", "--property=Environment", "--value"],
            capture_output=True, text=True, timeout=10, check=True)
        variables = dict(item.split("=", 1) for item in shlex.split(result.stdout) if "=" in item)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        app.logger.error("Could not read environment for %s: %s", unit, exc)
        return "Unknown"
    return variables.get("DOTNET_ENVIRONMENT") or variables.get("ASPNETCORE_ENVIRONMENT", "")


def server_aliases(config_text, binding=""):
    aliases = []
    seen = {binding.lower()} if binding else set()
    for line in config_text.splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) < 2 or fields[0].lower() != "serveralias":
            continue
        for alias in fields[1:]:
            normalized = alias.lower()
            if normalized not in seen:
                seen.add(normalized)
                aliases.append(alias)
    return aliases


def environment_info(key, label, unit, binding, port, path, states, **extra):
    service = bool(unit) and unit_of(unit)
    info = {"key": key, "label": label, "unit": unit if service else "", "binding": binding, "port": port, "path": path,
            "state": states.get(unit, "") if service else ""}
    info.update(extra)
    if unit:
        config_text = read_text(os.path.join(APACHE_SITES, unit + ".conf"))
        info["aliases"] = server_aliases(config_text, binding)
    info["environment"] = service_environment(unit) if service else ""
    info.update(https_status(unit=unit))
    if service:
        service_text = read_text(os.path.join(SERVICE_DIR, unit + ".service"))
        match = re.search(r'^WorkingDirectory=(.+)$', service_text, re.M)
        if match:
            working = os.path.realpath(match.group(1).strip().strip('"'))
            if working.startswith(WEB_ROOT + os.sep) and os.path.isdir(working):
                info["publish_path"] = rel(working)
                info["editable_configs"] = [filename for filename in ("appsettings.json", "web.config")
                                            if os.path.isfile(os.path.join(working, filename))]
                try:
                    if info["environment"] == "Unknown":
                        raise ValueError("Service environment unavailable")
                    settings = subprocess.run(
                        ["systemctl", "show", unit + ".service", "--property=Environment", "--value"],
                        capture_output=True, text=True, timeout=10, check=True)
                    variables = dict(item.split("=", 1) for item in shlex.split(settings.stdout) if "=" in item)
                    info.update(_database_module.database_details(working, info["environment"], variables))
                except (OSError, subprocess.SubprocessError, ValueError):
                    app.logger.warning("Could not read database configuration for %s", unit)
                    info["database"] = "Unknown"
    return info


def site_units(name):
    """Unit names the admin may control for a site: its own service and a local production service."""
    units = [name]
    prod = parse_prod(os.path.join(WEB_ROOT, name))
    if prod and prod["PROD_LOCAL_PATH"] and NAME_RE.match(prod["PROD_FOLDER"] or ""):
        units.append(prod["PROD_FOLDER"])
    return units


def umbraco_version(root, projects):
    """Version of the Umbraco.Cms package from the csproj (or Directory.Packages.props)."""
    files = projects + [os.path.join(root, "Directory.Packages.props"), os.path.join(root, "Directory.Build.props")]
    for path in files:
        text = read_text(path)
        for tag in re.findall(r"<PackageReference\b[^>]*>|<PackageVersion\b[^>]*>", text):
            if not re.search(r'(?:Include|Update)\s*=\s*"Umbraco\.Cms"', tag):
                continue
            match = re.search(r'Version\s*=\s*"([^"]+)"', tag)
            if match:
                return match.group(1)
        match = re.search(r'Include\s*=\s*"Umbraco\.Cms"[^>]*>\s*<Version>([^<]+)</Version>', text)
        if match:
            return match.group(1).strip()
    return ""


def package_references(projects):
    packages = {}
    errors = []
    for path in projects:
        try:
            project = ET.parse(path).getroot()
        except ET.ParseError as exc:
            errors.append("%s: %s" % (os.path.basename(path), exc))
            continue
        for element in project.iter():
            if element.tag.rsplit("}", 1)[-1] != "PackageReference":
                continue
            name = element.attrib.get("Include") or element.attrib.get("Update")
            if not name:
                continue
            version = element.attrib.get("Version", "")
            if not version:
                version_node = next((child for child in element if child.tag.rsplit("}", 1)[-1] == "Version"), None)
                version = (version_node.text or "").strip() if version_node is not None else ""
            if name not in packages or version:
                packages[name] = version
    return [{"name": name, "version": version} for name, version in sorted(packages.items(), key=lambda item: item[0].lower())], errors


def published_project_info(root, service_text):
    candidates = []
    match = re.search(r"^ExecStart=(.+)$", service_text, re.M)
    if match:
        for argument in shlex.split(match.group(1)):
            if argument.endswith(".dll") and os.path.isabs(argument):
                candidates.append(argument[:-4] + ".deps.json")
    if not candidates:
        candidates = sorted(glob.glob(os.path.join(root, "*.deps.json")))
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as stream:
                data = json.load(stream)
            libraries = data.get("libraries", {})
            version = next((name.split("/", 1)[1] for name in libraries
                            if name.startswith("Umbraco.Cms/")), "")
            return {"kind": "umbraco" if version else "csharp", "umbraco_version": version}
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            app.logger.warning("Could not read published project metadata %s: %s", path, exc)
    return None


def site_info(name, states):
    root = os.path.join(WEB_ROOT, name)
    conf = os.path.join(APACHE_SITES, name + ".conf")
    service_text = read_text(os.path.join(SERVICE_DIR, name + ".service"))
    conf_text = read_text(conf)

    binding = ""
    match = re.search(r"^\s*ServerName\s+(\S+)", conf_text, re.M)
    if match:
        binding = match.group(1)
    port = ""
    match = re.search(r"(?:localhost|127\.0\.0\.1):(\d+)", service_text)
    if match:
        port = match.group(1)
    aspnet = re.search(r"ASPNETCORE_ENVIRONMENT=(\S+)", service_text)

    projects = [path for path in glob.glob(os.path.join(root, "*.csproj")) if os.path.isfile(path)]
    umbraco = ""
    packages = []
    package_errors = []
    if projects:
        kind = "umbraco" if any("Umbraco.Cms" in read_text(p) for p in projects) else "csharp"
        packages, package_errors = package_references(projects)
        if kind == "umbraco":
            umbraco = umbraco_version(root, projects)
    elif os.path.isfile(os.path.join(root, "app", "app.py")):
        kind = "python"
    else:
        published = published_project_info(root, service_text)
        kind = published["kind"] if published else "html"
        umbraco = published["umbraco_version"] if published else ""

    scripts = []
    scripts_dir = os.path.join(root, "scripts")
    if os.path.isdir(scripts_dir):
        scripts = sorted(f for f in os.listdir(scripts_dir) if SCRIPT_RE.match(f) and os.path.isfile(os.path.join(scripts_dir, f)))

    first_label = "Test" if not aspnet or aspnet.group(1) != "Production" else "Production"
    if name == ADMIN_SITE:
        first_label = "Admin"
    environments = [environment_info("main", first_label if kind in ("csharp", "umbraco") else "Live", name, binding, port, name, states,
                                     environment=aspnet.group(1) if aspnet else "", apache=os.path.exists(os.path.join(APACHE_ENABLED, name + ".conf")))]

    prod = parse_prod(root)
    if prod:
        local = prod["PROD_LOCAL_PATH"]
        if local:
            full = os.path.realpath(local)
            inside = full == WEB_ROOT or full.startswith(WEB_ROOT + os.sep)
            unit = prod["PROD_FOLDER"]
            environments.append(environment_info("prod", "Production", unit, prod["PROD_BINDING"] or conf_binding(unit), prod["PROD_PORT_HTTP"] or service_port(unit),
                                                 rel(full) if inside else "", states, mode="local", exists=os.path.isdir(full)))
        else:
            environments.append({"key": "prod", "label": "Production", "mode": "remote", "unit": "", "state": "", "binding": prod["PROD_BINDING"],
                                 "port": prod["PROD_PORT_HTTP"], "path": "", "host": prod["PROD_IP"], "user": prod["PROD_USER"],
                                 "ssh_port": prod["PROD_SSH_PORT"], "folder": prod["PROD_FOLDER"], "environment": "Unknown",
                                 **remote_status(name)})

    updates = ""
    package_updates = []
    if projects:
        try:
            current = _publish_module.fingerprint(root)
        except (OSError, ValueError) as exc:
            app.logger.warning("Could not read publish status for %s: %s", name, exc)
            current = None
        if current is not None:
            try:
                details = _publish_module.updates_details(root, current)
                updates = details["status"]
                package_updates = details["packages"]
            except (OSError, ValueError):
                app.logger.warning("Could not read package update status for %s", name)
                updates = "error"
        for environment in environments:
            scope = "prod" if environment["key"] == "prod" else "test"
            environment["publish_status"] = "unknown"
            if current is not None:
                try:
                    environment["publish_status"] = _publish_module.status(root, scope, current)
                except (OSError, ValueError) as exc:
                    app.logger.warning("Could not read %s publish status for %s: %s", scope, name, exc)

    return {
        "name": name,
        "kind": kind,
        "has_project": bool(projects),
        "updates": updates,
        "package_updates": package_updates,
        "umbraco_version": umbraco,
        "packages": packages,
        "package_errors": package_errors,
        "scripts": scripts,
        "admin": name == ADMIN_SITE,
        "environments": environments,
    }


def service_states(names):
    units = [n + ".service" for n in names if os.path.isfile(os.path.join(SERVICE_DIR, n + ".service"))]
    if not units:
        return {}
    try:
        out = subprocess.run(["systemctl", "is-active", *units], capture_output=True, text=True, timeout=10).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return {}
    return {u[: -len(".service")]: s for u, s in zip(units, out)}


def safe_path(relative):
    full = os.path.realpath(os.path.join(WEB_ROOT, (relative or "").lstrip("/")))
    if full != WEB_ROOT and not full.startswith(WEB_ROOT + os.sep):
        abort(400, "Path outside the web root")
    return full


def rel(full):
    return os.path.relpath(full, WEB_ROOT).replace(os.sep, "/") if full != WEB_ROOT else ""


def server_files():
    return request.path.startswith("/api/server-fs/")


def fs_path(relative):
    if server_files():
        path = os.path.join("/", (relative or "").lstrip("/"))
        if request.endpoint in ("fs_rename", "fs_delete") and os.path.basename(path) not in ("", ".", ".."):
            return os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
        return os.path.realpath(path)
    return safe_path(relative)


def fs_relative(full):
    return full.lstrip("/") if server_files() else rel(full)


def is_site_root(full):
    return full == "/" or full == WEB_ROOT or os.path.dirname(full) == WEB_ROOT


def own_like_parent(path):
    parent = os.stat(os.path.dirname(path))
    os.chown(path, parent.st_uid, parent.st_gid)


# ----------------------------------------------------------------- sites API

UPDATE_CHECK_LOCK = threading.Lock()
UPDATE_CHECK = {"running": False, "completed": 0, "total": 0, "errors": []}


def check_site_updates(name):
    try:
        _publish_module.check_updates(os.path.join(WEB_ROOT, name))
    except (OSError, ValueError, RuntimeError) as exc:
        app.logger.error("Package update check failed for %s: %s", name, exc, exc_info=True)
        with UPDATE_CHECK_LOCK:
            UPDATE_CHECK["errors"].append(name)
    finally:
        with UPDATE_CHECK_LOCK:
            UPDATE_CHECK["completed"] += 1


def check_all_updates(names):
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(check_site_updates, names))
    finally:
        with UPDATE_CHECK_LOCK:
            UPDATE_CHECK["running"] = False


@app.route("/api/package-updates", methods=["GET", "POST"])
def api_package_updates():
    with UPDATE_CHECK_LOCK:
        if request.method == "POST" and not UPDATE_CHECK["running"]:
            names = [name for name in site_dirs() if name != ADMIN_SITE
                     and any(os.path.isfile(path) for path in glob.glob(os.path.join(WEB_ROOT, name, "*.csproj")))]
            UPDATE_CHECK.update(running=True, completed=0, total=len(names), errors=[])
            try:
                threading.Thread(target=check_all_updates, args=(names,), daemon=True).start()
            except RuntimeError:
                UPDATE_CHECK["running"] = False
                app.logger.exception("Could not start package update checks")
                return error("Could not start package update checks", 500)
        return jsonify(**UPDATE_CHECK)

def default_umbraco_version():
    """The Umbraco version create_site.sh installs when none is given."""
    match = re.search(r"^_defaultUmbracoVersion=([0-9][0-9A-Za-z.\-]*)", read_text(os.path.join(APACHE_DIR, "create_site.sh")) or "", re.M)
    return match.group(1) if match else ""


@app.route("/api/sites")
def api_sites():
    names = site_dirs()
    units = []
    for n in names:
        units.extend(site_units(n))
    states = service_states(units)
    with ThreadPoolExecutor(max_workers=8) as executor:
        sites = list(executor.map(lambda name: site_info(name, states), names))
    with UPDATE_CHECK_LOCK:
        update_check = dict(UPDATE_CHECK)
    return jsonify(sites=sites, defaults={"umbraco_version": default_umbraco_version()}, update_check=update_check)


@app.route("/api/sites/<name>/service", methods=["POST"])
def api_service(name):
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    unit = data.get("unit") or name
    if data.get("scope") == "prod":
        remote_site(name)
        if action not in ("start", "stop", "restart"):
            return error("Unknown service action")
        remote_command(name, action, text=True)
        REMOTE_STATUS.pop(name, None)
        return jsonify(ok=True)
    if name not in site_dirs() or action not in ("start", "stop", "restart") or unit not in site_units(name):
        return error("Unknown site, service or action")
    if not unit_of(unit):
        return error("The site has no service")
    if unit == ADMIN_SITE and action != "restart":
        return error("The admin site cannot stop itself")
    result = subprocess.run(["systemctl", action, unit], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return error((result.stderr or result.stdout).strip() or "systemctl failed", 500)
    return jsonify(ok=True)


@app.route("/api/sites/<name>/logs")
def api_logs(name):
    if request.args.get("scope") == "prod":
        remote_site(name)
        return jsonify(text=remote_command(name, "logs", text=True).stdout)
    unit = request.args.get("unit") or name
    if name not in site_dirs() or unit not in site_units(name):
        return error("Unknown site or service")
    result = subprocess.run(["journalctl", "-u", unit, "-n", "300", "--no-pager", "-o", "short-iso"], capture_output=True, text=True, timeout=20)
    return jsonify(text=result.stdout or result.stderr)


def remote_site(name):
    if name not in site_dirs():
        abort(400, "Unknown site")
    prod = parse_prod(os.path.join(WEB_ROOT, name))
    if not prod or prod["PROD_LOCAL_PATH"]:
        abort(400, "This site has no remote production target")
    return prod


# ------------------------------------------------------------------- profile

CONFIG_LOCK = threading.Lock()


def save_config():
    fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(CONFIG_PATH, 0o600)


@app.route("/api/profile", methods=["GET", "POST"])
def api_profile():
    if request.method == "GET":
        return jsonify(user=cfg["user"])
    data = request.get_json(silent=True) or {}
    ip = client_ip()
    if login_locked(ip):
        return error("Too many failed attempts. Try again later.", 429)
    if not check_password_hash(cfg["password_hash"], data.get("current_password", "")):
        record_failure(ip)
        time.sleep(1)
        return error("The current password is wrong.", 403)
    user = (data.get("username") or "").strip() or cfg["user"]
    new_password = data.get("new_password") or ""
    if not re.match(r"^[A-Za-z0-9._@-]{1,64}$", user):
        return error("Invalid username. Use letters, numbers and . _ @ - (max 64).")
    if new_password and len(new_password) < 12:
        return error("The new password must be at least 12 characters.")
    if user == cfg["user"] and not new_password:
        return error("Nothing to change.")
    with CONFIG_LOCK:
        cfg["user"] = user
        if new_password:
            cfg["password_hash"] = generate_password_hash(new_password)
            # A new secret key signs out every other session; this one is re-signed below
            cfg["secret_key"] = secrets.token_hex(32)
            app.secret_key = cfg["secret_key"]
        save_config()
    session["user"] = user
    return jsonify(ok=True, user=user)


# ------------------------------------------------------------- terminal API

SESSIONS = {}
SESSIONS_LOCK = threading.Lock()
BUFFER_MAX = 400 * 1024
MAX_SESSIONS = 12
def dotnet_root_dir():
    """Where .NET is installed. Login shells get DOTNET_ROOT from /etc/profile.d, tasks started by the service do not."""
    if os.environ.get("DOTNET_ROOT"):
        return os.environ["DOTNET_ROOT"]
    exe = shutil.which("dotnet")
    return os.path.dirname(os.path.realpath(exe)) if exe else ""


DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def terminal_scope(payload):
    kind = payload.get("kind")
    if kind == "publish_both" or (kind == "bulk" and payload.get("action") == "both"):
        return "all"
    if kind == "bulk":
        return "prod" if payload.get("action") == "prod" else "main"
    if payload.get("scope") in ("all", "prod", "test", "main"):
        return "main" if payload["scope"] == "test" else payload["scope"]
    if kind in ("remote_tool", "create_prod"):
        return "prod"
    if kind == "script":
        script = payload.get("script", "")
        if script.endswith("_prod.sh") or script == "create_prod.sh":
            return "prod"
    return "main"


def terminal_auto_close(payload):
    kind = payload.get("kind")
    if kind in ("shell", "create_site", "create_prod", "delete_site", "change_binding", "prod_action", "dotnet_package", "fix_permissions", "publish_both", "bulk", "update_package"):
        return True
    if kind == "remote_tool":
        return payload.get("tool") == "shell"
    if kind == "apache":
        return payload.get("tool") == "restart"
    if kind == "script":
        return payload.get("script") in {
            "publish.sh", "publish_local.sh", "publish_prod.sh", "create_prod.sh",
            "sync_prod.sh", "clear_logs.sh", "clear_logs_local.sh", "clear_logs_prod.sh",
            "issue_certificat.sh", "change_env.sh", "restart_service.sh", "restart_apache.sh",
            "fix_permissions.sh", "update_packages.sh", "set_password.sh", "outdated.sh",
        }
    return False


def terminal_output_problem(text):
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(
        r"^(?:\[[^\]\r\n]+\] )?(?:"
        r"Checking if there is an error in the Apache config file before starting\."
        r"|In case an error causes it to not be restored automatically, you can restore it yourself\."
        r")\r?$", "", text, flags=re.M)
    text = re.sub(r"\b(?:0|no)\s+(?:errors?|warnings?|failures?)(?:\(s\))?\b", "", text, flags=re.I)
    return bool(re.search(r"\b(?:errors?|failed|failure|fatal|\w*exception|traceback|warnings?)\b(?![/_-]|\.[A-Za-z0-9])", text, re.I))


class TermSession:
    def __init__(self, title, argv, cwd, extra_env=None, auto_close=False, update_root=None, sites=None, scope="main"):
        self.id = uuid.uuid4().hex[:12]
        self.title = title
        self.sites = sites or []
        self.scope = scope
        self.created = time.time()
        self.exit = None
        self.auto_close = auto_close
        self.output_problem = False
        self.output_tail = ""
        self.update_root = update_root
        self.update_fingerprint = _publish_module.fingerprint(update_root) if update_root else None
        self.no_outdated = False
        self.buffer = bytearray()
        self.subscribers = set()
        self.lock = threading.Lock()
        env = {
            "TERM": "xterm-256color",
            "LANG": "C.UTF-8",
            "HOME": "/root",
            "USER": "root",
            "SHELL": "/bin/bash",
            "PATH": os.environ.get("PATH", DEFAULT_PATH),
        }
        dotnet_root = dotnet_root_dir()
        if dotnet_root:
            env["DOTNET_ROOT"] = dotnet_root
        env.update(extra_env or {})
        self.proc = ptyprocess.PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=(30, 120))
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        while True:
            try:
                data = os.read(self.proc.fd, 4096)
            except OSError:
                break
            if not data:
                break
            lines = (self.output_tail + decoder.decode(data)).split("\n")
            self.output_tail = lines.pop()
            if self.update_root:
                self.no_outdated |= "No outdated dependencies were detected" in "\n".join(lines + [self.output_tail])
            self.output_problem |= any(terminal_output_problem(line) for line in lines)
            if len(self.output_tail) > BUFFER_MAX:
                self.output_problem |= terminal_output_problem(self.output_tail)
                self.output_tail = self.output_tail[-4096:]
            with self.lock:
                self.buffer += data
                if len(self.buffer) > BUFFER_MAX:
                    del self.buffer[: len(self.buffer) - BUFFER_MAX]
                for q in list(self.subscribers):
                    q.put(data)
        try:
            self.proc.wait()
        except Exception:
            pass
        self.output_problem |= terminal_output_problem(self.output_tail + decoder.decode(b"", final=True))
        status = self.proc.exitstatus
        self.exit = status if status is not None else -(self.proc.signalstatus or 1)
        if getattr(self, "update_root", None):
            update_status = "error" if self.exit != 0 or self.output_problem else "current" if self.no_outdated else "available"
            try:
                _publish_module.record(self.update_root, "updates", self.update_fingerprint, status=update_status)
            except (OSError, ValueError):
                app.logger.exception("Could not save package update status for %s", self.update_root)
                self.output_problem = True
        REMOTE_STATUS.clear()
        with self.lock:
            for q in list(self.subscribers):
                q.put(None)

    @property
    def running(self):
        return self.exit is None

    def write(self, text):
        if self.running:
            os.write(self.proc.fd, text.encode("utf-8"))

    def resize(self, rows, cols):
        if self.running:
            self.proc.setwinsize(max(2, min(rows, 500)), max(2, min(cols, 500)))

    def kill(self):
        if not self.running:
            return
        for sig in (signal.SIGHUP, signal.SIGKILL):
            try:
                os.killpg(self.proc.pid, sig)
            except OSError:
                break
            for _ in range(20):
                if not self.running:
                    return
                time.sleep(0.1)


def prune_sessions():
    now = time.time()
    with SESSIONS_LOCK:
        for sid, s in list(SESSIONS.items()):
            if not s.running and not s.subscribers and now - s.created > 3600:
                del SESSIONS[sid]


def site_arg(data):
    site = data.get("site", "")
    if site not in site_dirs():
        abort(400, "Unknown site")
    return site


def production_setup_env(data, site):
    """Validate modal input for create_prod.sh and keep credentials out of argv."""
    folder = data.get("folder", "")
    binding = data.get("binding", "")
    target = data.get("target", "")
    if not isinstance(folder, str) or not isinstance(binding, str):
        abort(400, "Invalid production site name or binding")
    binding = binding.lower()
    if not NAME_RE.match(folder) or not BINDING_RE.match(binding):
        abort(400, "Invalid production site name or binding")
    if target not in ("local", "remote"):
        abort(400, "Invalid production target")
    environment = data.get("environment", "Production")
    if environment not in ("Development", "Test", "Staging", "Production"):
        abort(400, "Invalid production environment")
    if os.path.isfile(os.path.join(WEB_ROOT, site, "scripts", "publish_prod.sh")):
        abort(400, "This site already has a production target")
    if not os.path.isfile(os.path.join(WEB_ROOT, site, "scripts", "create_prod.sh")):
        abort(400, "This site cannot create a production target")

    values = {
        "VNS_PROD_MODAL": "1",
        "VNS_PROD_TARGET": target,
        "VNS_PROD_FOLDER": folder,
        "VNS_PROD_BINDING": binding,
        "VNS_PROD_ENVIRONMENT": environment,
        "VNS_PROD_LOCAL_PATH": "",
        "VNS_PROD_IP": "",
        "VNS_PROD_USER": "",
        "VNS_PROD_PASSWORD": "",
        "VNS_PROD_SSH_PORT": "",
    }
    test_binding = conf_binding(site).lower()
    if target == "local":
        local_path = data.get("local_path", "")
        if not isinstance(local_path, str):
            abort(400, "Invalid local production folder")
        site_root = os.path.realpath(os.path.join(WEB_ROOT, site))
        target_path = os.path.realpath(local_path) if os.path.isabs(local_path) else ""
        try:
            within_site = os.path.commonpath((site_root, target_path)) == site_root
        except ValueError:
            within_site = False
        if not within_site or target_path == site_root:
            abort(400, "Local production files must be stored in a subfolder of this site's folder")
        if folder == site or binding == test_binding:
            abort(400, "A local production site needs a different name and binding than the test site")
        values["VNS_PROD_LOCAL_PATH"] = target_path
    else:
        host = data.get("host", "")
        user = data.get("user", "")
        password = data.get("password", "")
        port = str(data.get("ssh_port", ""))
        if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host):
            abort(400, "Invalid remote host")
        if not isinstance(user, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", user):
            abort(400, "Invalid SSH username")
        if not isinstance(password, str) or len(password) > 4096:
            abort(400, "Invalid SSH password")
        if not port.isdecimal() or not 1 <= int(port) <= 65535:
            abort(400, "Invalid SSH port")
        values.update({
            "VNS_PROD_IP": host,
            "VNS_PROD_USER": user,
            "VNS_PROD_PASSWORD": password,
            "VNS_PROD_SSH_PORT": port,
        })
    return values


def render_create_prod_script(site):
    """Render the current create_prod template from this site's installed metadata."""
    root = os.path.realpath(os.path.join(WEB_ROOT, site))
    projects = sorted(glob.glob(os.path.join(root, "*.csproj")))
    if not projects:
        abort(400, "Could not find this site's C# project")
    binding = conf_binding(site)
    if not binding:
        abort(400, "Could not find this site's Apache binding")
    service_text = read_text(os.path.join(SERVICE_DIR, site + ".service"))
    working = re.search(r"^WorkingDirectory=(\S+)", service_text, re.M)
    publish_folder = os.path.basename(working.group(1).rstrip("/")) if working else ""
    if not FOLDER_RE.match(publish_folder):
        abort(400, "Could not determine this site's publish folder")
    project_text = read_text(projects[0])
    admin_match = re.search(r"^\s*ServerAdmin\s+(\S+)", read_text(os.path.join(APACHE_SITES, site + ".conf")), re.M)
    admin_mail = admin_match.group(1) if admin_match else ""
    if not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", admin_mail):
        abort(400, "Could not determine this site's administrator email")
    umbraco = "umbraco" if "Umbraco.Cms" in project_text else "empty"

    template = os.path.join(APACHE_DIR, "solutionfiles", "csharp", "scripts", "create_prod.sh")
    text = read_text(template)
    if not text:
        abort(500, "Production setup template is missing")
    replacements = {
        "@APACHE_DIR@": APACHE_DIR,
        "@SITE@": site,
        "@BINDING@": binding,
        "@PROJECT_NAME@": os.path.splitext(os.path.basename(projects[0]))[0],
        "@SITE_ROOT@": root,
        "@PUBLISH_FOLDER@": publish_folder,
        "@ADMIN_MAIL@": admin_mail,
        "@CSHARP_KIND@": umbraco,
    }
    for token, value in replacements.items():
        text = text.replace(token, value)
    fd, path = tempfile.mkstemp(prefix="vns-create-prod-", suffix=".sh")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as script:
            script.write(text)
        os.chmod(path, 0o700)
    except Exception:
        os.unlink(path)
        raise
    return path


def build_command(data):
    """Validate a terminal request and return (title, argv, cwd). User input is never shell code."""
    kind = data.get("kind")
    bash = "/bin/bash"
    if kind == "update_package":
        site = site_arg(data)
        info = site_info(site, {})
        if info["kind"] not in ("csharp", "umbraco") or info.get("admin"):
            abort(400, "Package updates require a C# site")
        package = data.get("package")
        if package is not None and (not isinstance(package, str) or not PACKAGE_RE.fullmatch(package)):
            abort(400, "Invalid package name")
        available = info.get("package_updates", [])
        if info["updates"] != "available" or not available or (
                package is not None and not any(item["name"].lower() == package.lower() for item in available)):
            abort(400, "Run Check before updating this package")
        root = os.path.join(WEB_ROOT, site)
        return "Update packages: " + site, [
            sys.executable, os.path.join(APACHE_DIR, "lib", "publish_status.py"),
            "upgrade", root, *([package] if package else []),
        ], root
    if kind == "bulk":
        sites = data.get("sites")
        action = data.get("action")
        if (not isinstance(sites, list) or not sites or len(sites) > 100
                or any(not isinstance(site, str) for site in sites)
                or len(set(sites)) != len(sites)
                or action not in ("check", "add", "remove", "local", "prod", "both", "outdated")):
            abort(400, "Invalid multi action selection")
        tasks = []
        for name in sites:
            site = site_arg({"site": name})
            info = site_info(site, {})
            if info["kind"] not in ("csharp", "umbraco") or info.get("admin"):
                abort(400, "Multi actions require non-admin C# sites")
            if not any(os.path.isfile(path) for path in glob.glob(os.path.join(WEB_ROOT, site, "*.csproj"))):
                abort(400, "Multi actions require a C# project file in the site root")
            scripts = info["scripts"]
            if action in ("prod", "both") and "publish_prod.sh" not in scripts:
                tasks.append({"site": site, "skip": "No production publish script"})
                continue
            if action in ("add", "remove"):
                payload = {"kind": "dotnet_package", "site": site, "action": action,
                           "package": data.get("package"), "version": data.get("version", "")}
            elif action in ("check", "outdated"):
                projects = sorted(glob.glob(os.path.join(WEB_ROOT, site, "*.csproj")))
                if len(projects) != 1:
                    abort(400, "Expected exactly one C# project in this site")
                argv = [sys.executable, os.path.join(APACHE_DIR, "lib", "publish_status.py"),
                        "check", os.path.dirname(projects[0])] if action == "check" else ["dotnet", "outdated", "--upgrade"]
                tasks.append({"site": site, "argv": argv,
                              "cwd": os.path.dirname(projects[0])})
                continue
            elif action == "both":
                payload = {"kind": "publish_both", "site": site}
            else:
                script = "publish_prod.sh" if action == "prod" else next(
                    (name for name in ("publish_local.sh", "publish.sh") if name in scripts), "")
                payload = {"kind": "script", "site": site, "script": script}
            _, argv, cwd = build_command(payload)
            tasks.append({"site": site, "argv": argv, "cwd": cwd})
        return "Multi action: " + action, [
            sys.executable, os.path.join(APACHE_DIR, "lib", "bulk_tasks.py"), json.dumps(tasks),
        ], WEB_ROOT
    if kind == "remote_tool":
        site = site_arg(data)
        remote_site(site)
        tool = data.get("tool")
        if tool not in ("check_service", "edit_service", "edit_conf", "shell"):
            abort(400, "Unknown remote tool")
        REMOTE_STATUS.pop(site, None)
        return "Remote %s: %s" % (tool.replace("_", " "), site), [bash, os.path.join(APACHE_DIR, "remote_site.sh"), site, tool], WEB_ROOT
    if kind == "shell":
        site = data.get("site") or ""
        if site:
            site = site_arg(data)
            cwd = os.path.join(WEB_ROOT, site)
            title = "Shell: " + site
            if data.get("path"):
                cwd = safe_path(data["path"])
                if not os.path.isdir(cwd):
                    abort(400, "Unknown folder")
                title = "Shell: " + (data.get("label") or rel(cwd))[:60]
            return title, [bash, "-l"], cwd
        return "Shell: server", [bash, "-l"], "/"
    if kind == "publish_both":
        site = site_arg(data)
        folder = os.path.join(WEB_ROOT, site, "scripts")
        local = next((name for name in ("publish_local.sh", "publish.sh")
                      if os.path.isfile(os.path.join(folder, name))), None)
        if not local:
            abort(400, "This site has no test publish script")
        build_command({"kind": "script", "site": site, "script": local})
        build_command({"kind": "script", "site": site, "script": "publish_prod.sh"})
        root = os.path.join(WEB_ROOT, site)
        return "Publish both: " + site, [
            sys.executable, os.path.join(APACHE_DIR, "lib", "publish_status.py"),
            "both", root, os.path.realpath(os.path.join(folder, local)),
            os.path.realpath(os.path.join(folder, "publish_prod.sh")),
        ], root
    if kind == "script":
        site = site_arg(data)
        script = data.get("script", "")
        folder = os.path.join(WEB_ROOT, site, "scripts")
        path = os.path.realpath(os.path.join(folder, script))
        if not SCRIPT_RE.match(script) or os.path.dirname(path) != os.path.realpath(folder) or not os.path.isfile(path):
            abort(400, "Unknown script")
        argv = [bash, path]
        root = os.path.join(WEB_ROOT, site)
        if script in ("publish.sh", "publish_local.sh", "publish_prod.sh") and glob.glob(os.path.join(root, "*.csproj")):
            argv = [sys.executable, os.path.join(APACHE_DIR, "lib", "publish_status.py"),
                    "run", root, "prod" if script == "publish_prod.sh" else "test", *argv]
        return "%s: %s" % (site, script), argv, folder
    if kind == "dotnet_package":
        site = site_arg(data)
        if site_info(site, {})["kind"] not in ("csharp", "umbraco"):
            abort(400, "NuGet packages can only be managed for C# sites")
        action = data.get("action", "")
        package = data.get("package", "")
        if action not in ("add", "remove") or not isinstance(package, str) or not PACKAGE_RE.fullmatch(package):
            abort(400, "Invalid package action or name")
        projects = sorted(glob.glob(os.path.join(WEB_ROOT, site, "*.csproj")))
        if len(projects) != 1:
            abort(400, "Expected exactly one C# project in this site")
        version = data.get("version", "")
        if action == "add":
            if not isinstance(version, str) or len(version) > 128 or (version and not VERSION_RE.fullmatch(version)):
                abort(400, "Invalid package version")
            argv = ["dotnet", "add", "package", package]
            if version:
                argv.extend(["--version", version])
            title = "Add NuGet package: " + package
        else:
            argv = ["dotnet", "remove", "package", package]
            title = "Remove NuGet package: " + package
        return title + " (" + site + ")", argv, os.path.dirname(projects[0])
    if kind == "create_prod":
        site = site_arg(data)
        path = os.path.join(WEB_ROOT, site, "scripts", "create_prod.sh")
        if not os.path.isfile(path):
            abort(400, "This site cannot create a production target")
        return "Create production site: " + site, [bash, path], os.path.dirname(path)
    if kind == "unit_tool":
        site = site_arg(data)
        unit = data.get("unit", "")
        tool = data.get("tool", "")
        if unit not in site_units(site):
            abort(400, "Unknown service")
        service = "/etc/systemd/system/%s.service" % unit
        conf = "/etc/apache2/sites-available/%s.conf" % unit
        if tool == "check_service":
            return "Service: " + unit, [bash, "-c", 'clear; systemctl status "$1" --no-pager -l', "x", unit], WEB_ROOT
        if tool == "edit_service":
            code = ('clear; nano "$1"; read -r -p "Reload daemon and restart service? [Y/n]: " a; '
                    'if [[ ! $a =~ ^[nN] ]]; then systemctl daemon-reload && systemctl restart "$2"; systemctl status "$2" --no-pager; fi')
            return "Edit service: " + unit, [bash, "-c", code, "x", service, unit], WEB_ROOT
        if tool == "edit_conf":
            code = ('clear; nano "$1"; read -r -p "Test config and reload apache? [Y/n]: " a; '
                    'if [[ ! $a =~ ^[nN] ]]; then apache2ctl configtest && systemctl reload apache2; fi')
            return "Edit config: " + unit, [bash, "-c", code, "x", conf], WEB_ROOT
        abort(400, "Unknown tool")
    if kind == "apache":
        tool = data.get("tool", "")
        if tool == "restart":
            return "Restart Apache", [bash, "-c", 'clear; apache2ctl configtest && systemctl restart apache2 && echo "Apache restarted" ; systemctl status apache2 --no-pager | head -n 12'], WEB_ROOT
        if tool == "configtest":
            return "Apache config test", [bash, "-c", "clear; apache2ctl configtest"], WEB_ROOT
        abort(400, "Unknown tool")
    if kind == "create_site":
        site, site_type, binding = data.get("site", ""), data.get("type", ""), (data.get("binding") or "").lower()
        if not NAME_RE.match(site) or site_type not in ("html", "python", "csharp") or not BINDING_RE.match(binding):
            abort(400, "Invalid site name, type or binding")
        if site in site_dirs():
            abort(400, "The site already exists")
        certificate = data.get("certificate", "y")
        if certificate not in ("y", "n"):
            abort(400, "Invalid certificate option")
        args = [site, site_type, "", "", "", binding, "", "", certificate]
        if site_type == "csharp":
            csharp = data.get("csharp") or "umbraco"
            project = data.get("project") or site
            publish = data.get("publish") or "_publish"
            build = data.get("build") or "Debug"
            environment = data.get("environment") or ("Production" if build == "Release" else "Test")
            if environment not in ("Development", "Test", "Staging", "Production"):
                abort(400, "Invalid environment")
            version = data.get("version") or ""
            template = (data.get("template") or "").strip() if csharp == "empty" else ""
            if csharp not in ("umbraco", "empty") or build not in ("Debug", "Release"):
                abort(400, "Invalid C# options")
            if not PROJECT_RE.match(project) or not FOLDER_RE.match(publish):
                abort(400, "Invalid project name or publish folder")
            if csharp == "umbraco" and version and not VERSION_RE.match(version):
                abort(400, "Invalid Umbraco version")
            if template and not PROJECT_RE.fullmatch(template):
                abort(400, "Invalid template name")
            args = [site, site_type, csharp, version if csharp == "umbraco" else "", project, binding, publish, build, certificate, template, environment]
        return "Create: " + site, [bash, os.path.join(APACHE_DIR, "create_site.sh"), *args], APACHE_DIR
    if kind == "fix_permissions":
        site = site_arg(data)
        scope = data.get("scope", "test")
        if scope not in ("test", "prod") or site == ADMIN_SITE:
            abort(400, "Invalid permissions target")
        if scope == "prod" and not os.path.isfile(os.path.join(WEB_ROOT, site, "scripts", "publish_prod.sh")):
            abort(400, "This site has no production site")
        return "Fix permissions: " + site, [bash, os.path.join(APACHE_DIR, "fix_permissions.sh"), site, scope], APACHE_DIR
    if kind == "change_binding":
        site = site_arg(data)
        binding = (data.get("binding") or "").lower()
        if not BINDING_RE.match(binding):
            abort(400, "Invalid binding")
        argv = [bash, os.path.join(APACHE_DIR, "change_binding.sh"), site, binding]
        if "aliases" in data:
            aliases = data["aliases"]
            if not isinstance(aliases, str) or len(aliases) > 4096:
                abort(400, "Invalid aliases")
            alias_names = []
            for alias in re.split(r"[\s,]+", aliases.strip()):
                if not alias:
                    continue
                alias = alias.lower()
                if (len(alias) > 253 or not BINDING_RE.fullmatch(alias)
                        or any(len(label) > 63 for label in alias.split("."))):
                    abort(400, "Invalid alias: " + alias)
                if alias != binding and alias not in alias_names:
                    alias_names.append(alias)
            if len(alias_names) > 99:
                abort(400, "A certificate can include at most 99 aliases")
            aliases = " ".join(alias_names)
            argv.append(aliases)
            argv.append("--yes")
        if data.get("scope") == "prod":
            if not os.path.isfile(os.path.join(WEB_ROOT, site, "scripts", "publish_prod.sh")):
                abort(400, "This site has no production site")
            argv[1] = os.path.join(APACHE_DIR, "change_binding_prod.sh")
            return "Prod bindings: " + site, argv, APACHE_DIR
        return "Bindings: " + site, argv, APACHE_DIR
    if kind == "prod_action":
        site = site_arg(data)
        action = data.get("action", "")
        if action not in ("issue_certificate", "change_env"):
            abort(400, "Unknown action")
        args = []
        if action == "change_env":
            environment = data.get("environment", "")
            if environment not in ("Development", "Test", "Staging", "Production"):
                abort(400, "Invalid environment")
            args.append(environment)
        else:
            args.append("--yes")
        if data.get("scope") == "test":
            if not os.path.isdir(os.path.join(WEB_ROOT, site)):
                abort(404, "Unknown site")
            return "Test %s: %s" % (action.replace("_", " "), site), [bash, os.path.join(APACHE_DIR, "test_action.sh"), site, action, *args], APACHE_DIR
        if not os.path.isfile(os.path.join(WEB_ROOT, site, "scripts", "publish_prod.sh")):
            abort(400, "This site has no production site")
        return "Prod %s: %s" % (action.replace("_", " "), site), [bash, os.path.join(APACHE_DIR, "prod_action.sh"), site, action, *args], APACHE_DIR
    if kind == "delete_site":
        site = site_arg(data)
        if site == ADMIN_SITE:
            abort(400, "The admin site cannot delete itself")
        certificates = "y" if data.get("certificates") else "n"
        scope = data.get("scope") or "all"
        if scope not in ("all", "test", "prod"):
            abort(400, "Invalid scope")
        return "Delete " + scope + ": " + site, [bash, os.path.join(APACHE_DIR, "delete_site.sh"), site, certificates, scope], APACHE_DIR
    abort(400, "Unknown task")


def describe(s):
    return {"id": s.id, "title": s.title, "running": s.running, "exit": s.exit,
            "auto_close": s.auto_close, "output_problem": s.output_problem, "sites": s.sites,
            "scope": s.scope}


@app.route("/api/term", methods=["GET", "POST"])
def api_term():
    if request.method == "GET":
        prune_sessions()
        with SESSIONS_LOCK:
            return jsonify(sessions=[describe(s) for s in SESSIONS.values()])
    payload = request.get_json(silent=True) or {}
    title, argv, cwd = build_command(payload)
    extra_env = production_setup_env(payload, payload.get("site", "")) if payload.get("kind") == "create_prod" else None
    if extra_env is not None:
        argv = ["/bin/bash", render_create_prod_script(payload["site"])]
    with SESSIONS_LOCK:
        if sum(1 for s in SESSIONS.values() if s.running) >= MAX_SESSIONS:
            if extra_env is not None:
                os.unlink(argv[1])
            return error("Too many running terminals. Close some first.")
        try:
            update_root = os.path.join(WEB_ROOT, payload["site"]) if payload.get("kind") == "script" and payload.get("script") == "outdated.sh" and glob.glob(os.path.join(WEB_ROOT, payload["site"], "*.csproj")) else None
            sites = payload.get("sites", []) if payload.get("kind") == "bulk" else [payload["site"]] if payload.get("site") else []
            session_obj = TermSession(title, argv, cwd, extra_env, auto_close=terminal_auto_close(payload), update_root=update_root, sites=sites, scope=terminal_scope(payload))
        except Exception:
            if extra_env is not None:
                os.unlink(argv[1])
            raise
        SESSIONS[session_obj.id] = session_obj
    return jsonify(describe(session_obj))


@app.route("/api/term/<sid>/close", methods=["POST"])
def api_term_close(sid):
    with SESSIONS_LOCK:
        s = SESSIONS.pop(sid, None)
    if s:
        s.kill()
    return jsonify(ok=True)


@sock.route("/ws/term/<sid>")
def ws_term(ws, sid):
    origin = request.headers.get("Origin", "")
    if not session.get("user") or urlparse(origin).netloc != request.headers.get("Host", ""):
        return
    s = SESSIONS.get(sid)
    if s is None:
        return
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    q = queue.Queue()
    with s.lock:
        replay = bytes(s.buffer)
        s.subscribers.add(q)
        already_exited = not s.running
    try:
        if replay:
            ws.send(json.dumps({"o": decoder.decode(replay)}))
        if already_exited:
            ws.send(json.dumps({"x": s.exit, "output_problem": s.output_problem}))

        def pump():
            while True:
                data = q.get()
                if data is None:
                    ws.send(json.dumps({"x": s.exit, "output_problem": s.output_problem}))
                    return
                text = decoder.decode(data)
                if text:
                    ws.send(json.dumps({"o": text}))

        threading.Thread(target=pump, daemon=True).start()
        while True:
            message = json.loads(ws.receive())
            if message.get("t") == "i":
                s.write(str(message.get("d", "")))
            elif message.get("t") == "r":
                s.resize(int(message.get("rows", 30)), int(message.get("cols", 120)))
    except Exception:
        pass
    finally:
        with s.lock:
            s.subscribers.discard(q)


# ---------------------------------------------------------------- files API

MAX_EDIT_BYTES = 2 * 1024 * 1024


@app.route("/api/sites/<site>/config/<kind>", methods=["GET", "POST"])
def managed_config(site, kind):
    if site not in site_dirs() or kind not in ("service", "conf"):
        abort(400, "Unknown site or configuration type")
    data = (request.get_json(silent=True) or {}) if request.method == "POST" else {}
    if not isinstance(data, dict):
        abort(400, "Expected a configuration object")
    action = data.get("action") if request.method == "POST" else "read"
    if action not in ("read", "save", "apply"):
        abort(400, "Unknown configuration action")
    payload = {"kind": kind, "action": action, "content": data.get("content")}
    if action == "read" and "encoding" in request.args:
        payload["encoding"] = request.args["encoding"]
    scope = request.args.get("scope", "local")
    if scope not in ("local", "remote"):
        abort(400, "Unknown configuration scope")
    if scope == "remote":
        remote_site(site)
        result = remote_command(site, "config", timeout=150, text=True, input=json.dumps(payload))
    else:
        unit = request.args.get("unit") or site
        if unit not in site_units(site):
            abort(400, "Unknown service")
        try:
            result = subprocess.run(
                [sys.executable, os.path.join(APACHE_DIR, "lib", "managed_config.py")],
                input=json.dumps(payload), text=True, capture_output=True, timeout=150,
                env={**os.environ, "SITE": unit})
        except (OSError, subprocess.TimeoutExpired) as exc:
            app.logger.error("Configuration %s failed for %s: %s", action, unit, exc)
            abort(502, "Configuration operation failed")
    try:
        response = json.loads(result.stdout)
    except ValueError:
        app.logger.error("Invalid configuration response for %s: %s", site, result.stderr)
        abort(502, "Invalid configuration response")
    if result.returncode:
        app.logger.error("Configuration %s failed for %s: %s", action, site, response)
        abort(502, response.get("error", "Configuration operation failed"))
    if response.get("encoding_required"):
        app.logger.warning("Configuration for %s is not UTF-8; requesting an explicit encoding", site)
    if action == "apply":
        REMOTE_STATUS.pop(site, None)
    return jsonify(response)


@app.route("/api/remote-fs/<site>/<action>", methods=["GET", "POST"])
def remote_fs(site, action):
    remote_site(site)
    if action not in ("list", "read", "download", "save", "mkdir", "newfile", "rename", "delete", "upload"):
        abort(400, "Unknown file action")
    if request.method == "GET":
        if action not in ("list", "read", "download"):
            abort(405)
        data = {"path": request.args.get("path", "")}
    elif action == "upload":
        data = {"path": request.form.get("path", ""), "files": [
            {"name": upload.filename or "", "content": base64.b64encode(upload.read()).decode("ascii")}
            for upload in request.files.getlist("files")]}
    else:
        data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        abort(400, "Expected a file operation object")
    data["action"] = action
    result = remote_command(site, "files", timeout=120, input=json.dumps(data).encode("utf-8"))
    try:
        response = json.loads(result.stdout)
    except ValueError:
        app.logger.error("Invalid remote file response for %s", site)
        abort(502, "Invalid remote file response")
    if action == "download":
        return send_file(io.BytesIO(base64.b64decode(response["content"])), as_attachment=True,
                         download_name=os.path.basename(data["path"]))
    return jsonify(response)


@app.route("/api/fs/list")
@app.route("/api/server-fs/list")
def fs_list():
    full = fs_path(request.args.get("path", ""))
    if not os.path.isdir(full):
        return error("Not a folder", 404)
    items = []
    for entry in os.scandir(full):
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        is_dir = entry.is_dir(follow_symlinks=server_files())
        try:
            editable = not is_dir and not entry.is_symlink() and entry.is_file() and editable_file(Path(entry.path), info.st_size)
            has_editable_files = (True if server_files() else contains_editable_files(Path(entry.path))) if is_dir else False
        except OSError as exc:
            app.logger.warning("Could not check file editability for %s: %s", entry.path, exc)
            editable = False
            has_editable_files = is_dir
        items.append({"name": entry.name, "dir": is_dir, "size": 0 if is_dir else info.st_size,
                      "modified": int(info.st_mtime), "editable": editable, "has_editable_files": has_editable_files})
    items.sort(key=lambda i: (not i["dir"], i["name"].lower()))
    return jsonify(path=fs_relative(full), items=items)


@app.route("/api/fs/read")
@app.route("/api/server-fs/read")
def fs_read():
    full = fs_path(request.args.get("path", ""))
    if not os.path.isfile(full):
        return error("Not a file", 404)
    if os.path.getsize(full) > MAX_EDIT_BYTES:
        return error("The file is too large to edit here. Download it instead.")
    with open(full, "rb") as f:
        data = f.read()
    if b"\0" in data:
        return error("Binary file. Download it instead.")
    return jsonify(content=data.decode("utf-8", errors="replace"))


@app.route("/api/fs/save", methods=["POST"])
@app.route("/api/server-fs/save", methods=["POST"])
def fs_save():
    data = request.get_json(silent=True) or {}
    full = fs_path(data.get("path", ""))
    if os.path.isdir(full) or not os.path.isdir(os.path.dirname(full)):
        return error("Invalid path")
    existed = os.path.exists(full)
    # Written in place, so owner and permissions of an existing file are kept
    with open(full, "w", encoding="utf-8", newline="") as f:
        f.write(data.get("content", ""))
    if not existed:
        own_like_parent(full)
    return jsonify(ok=True)


def valid_name(name):
    return bool(name) and name not in (".", "..") and "/" not in name and "\0" not in name


@app.route("/api/fs/mkdir", methods=["POST"])
@app.route("/api/fs/newfile", methods=["POST"])
@app.route("/api/server-fs/mkdir", methods=["POST"])
@app.route("/api/server-fs/newfile", methods=["POST"])
def fs_create():
    data = request.get_json(silent=True) or {}
    parent = fs_path(data.get("path", ""))
    name = data.get("name", "")
    if not valid_name(name) or not os.path.isdir(parent):
        return error("Invalid name")
    full = os.path.join(parent, name)
    if os.path.lexists(full):
        return error("It already exists")
    if request.path.endswith("mkdir"):
        os.mkdir(full)
    else:
        open(full, "x").close()
    own_like_parent(full)
    return jsonify(ok=True)


@app.route("/api/fs/rename", methods=["POST"])
@app.route("/api/server-fs/rename", methods=["POST"])
def fs_rename():
    data = request.get_json(silent=True) or {}
    full = fs_path(data.get("path", ""))
    name = data.get("name", "")
    if full == "/":
        return error("The filesystem root cannot be renamed.")
    if is_site_root(full):
        return error("Sites cannot be renamed here. Use change binding or create a new site.")
    if not valid_name(name) or not os.path.lexists(full):
        return error("Invalid name")
    target = os.path.join(os.path.dirname(full), name)
    if os.path.lexists(target):
        return error("It already exists")
    os.rename(full, target)
    return jsonify(ok=True)


@app.route("/api/fs/delete", methods=["POST"])
@app.route("/api/server-fs/delete", methods=["POST"])
def fs_delete():
    full = fs_path((request.get_json(silent=True) or {}).get("path", ""))
    if full == "/":
        return error("The filesystem root cannot be deleted.")
    if is_site_root(full):
        return error("Sites cannot be deleted here. Use Delete site.")
    if os.path.islink(full) or os.path.isfile(full):
        os.remove(full)
    elif os.path.isdir(full):
        shutil.rmtree(full)
    else:
        return error("Not found", 404)
    return jsonify(ok=True)


@app.route("/api/fs/upload", methods=["POST"])
@app.route("/api/server-fs/upload", methods=["POST"])
def fs_upload():
    parent = fs_path(request.form.get("path", ""))
    if not os.path.isdir(parent):
        return error("Not a folder")
    count = 0
    for upload in request.files.getlist("files"):
        name = os.path.basename(upload.filename or "")
        if not valid_name(name):
            continue
        target = os.path.join(parent, name)
        existed = os.path.exists(target)
        upload.save(target)
        if not existed:
            own_like_parent(target)
        count += 1
    return jsonify(ok=True, count=count)


@app.route("/api/fs/download")
@app.route("/api/server-fs/download")
def fs_download():
    full = fs_path(request.args.get("path", ""))
    if not os.path.isfile(full):
        return error("Not a file", 404)
    return send_file(full, as_attachment=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), threaded=True)
