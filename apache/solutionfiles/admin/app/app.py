"""VNS admin site: manage the sites in /var/www, run the VNS scripts, open shells and edit files.

Runs as root behind Apache (HTTPS). Everything except /login and /static requires a logged-in session.
"""
import codecs
import glob
import hmac
import json
import os
import socket
import queue
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time
import uuid
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


def environment_info(key, label, unit, binding, port, path, states, **extra):
    service = bool(unit) and unit_of(unit)
    info = {"key": key, "label": label, "unit": unit if service else "", "binding": binding, "port": port, "path": path,
            "state": states.get(unit, "") if service else ""}
    info.update(extra)
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

    projects = glob.glob(os.path.join(root, "*.csproj"))
    umbraco = ""
    if projects:
        kind = "umbraco" if any("Umbraco.Cms" in read_text(p) for p in projects) else "csharp"
        if kind == "umbraco":
            umbraco = umbraco_version(root, projects)
    elif os.path.isfile(os.path.join(root, "app", "app.py")):
        kind = "python"
    else:
        kind = "html"

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
                                                 rel(full) if inside else "", states, mode="local", exists=os.path.isdir(full), environment="Production"))
        else:
            environments.append({"key": "prod", "label": "Production", "mode": "remote", "unit": "", "state": "", "binding": prod["PROD_BINDING"],
                                 "port": prod["PROD_PORT_HTTP"], "path": "", "host": prod["PROD_IP"], "user": prod["PROD_USER"],
                                 "ssh_port": prod["PROD_SSH_PORT"], "folder": prod["PROD_FOLDER"], "environment": "Production"})

    return {
        "name": name,
        "kind": kind,
        "umbraco_version": umbraco,
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


def is_site_root(full):
    return full == WEB_ROOT or os.path.dirname(full) == WEB_ROOT


def own_like_parent(path):
    parent = os.stat(os.path.dirname(path))
    os.chown(path, parent.st_uid, parent.st_gid)


# ----------------------------------------------------------------- sites API

@app.route("/api/sites")
def api_sites():
    names = site_dirs()
    units = []
    for n in names:
        units.extend(site_units(n))
    states = service_states(units)
    return jsonify(sites=[site_info(n, states) for n in names])


@app.route("/api/sites/<name>/service", methods=["POST"])
def api_service(name):
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    unit = data.get("unit") or name
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
    unit = request.args.get("unit") or name
    if name not in site_dirs() or unit not in site_units(name):
        return error("Unknown site or service")
    result = subprocess.run(["journalctl", "-u", unit, "-n", "300", "--no-pager", "-o", "short-iso"], capture_output=True, text=True, timeout=20)
    return jsonify(text=result.stdout or result.stderr)


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
DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


class TermSession:
    def __init__(self, title, argv, cwd):
        self.id = uuid.uuid4().hex[:12]
        self.title = title
        self.created = time.time()
        self.exit = None
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
        self.proc = ptyprocess.PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=(30, 120))
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while True:
            try:
                data = os.read(self.proc.fd, 4096)
            except OSError:
                break
            if not data:
                break
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
        status = self.proc.exitstatus
        self.exit = status if status is not None else -(self.proc.signalstatus or 1)
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


def build_command(data):
    """Validate a terminal request and return (title, argv, cwd). Nothing is passed through a shell."""
    kind = data.get("kind")
    bash = "/bin/bash"
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
        return "Shell: server", [bash, "-l"], WEB_ROOT
    if kind == "script":
        site = site_arg(data)
        script = data.get("script", "")
        folder = os.path.join(WEB_ROOT, site, "scripts")
        path = os.path.realpath(os.path.join(folder, script))
        if not SCRIPT_RE.match(script) or os.path.dirname(path) != os.path.realpath(folder) or not os.path.isfile(path):
            abort(400, "Unknown script")
        return "%s: %s" % (site, script), [bash, path], folder
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
        args = [site, site_type, "", "", "", binding]
        if site_type == "csharp":
            csharp = data.get("csharp") or "umbraco"
            project = data.get("project") or site
            publish = data.get("publish") or "_publish"
            build = data.get("build") or "Debug"
            version = data.get("version") or ""
            if csharp not in ("umbraco", "empty") or build not in ("Debug", "Release"):
                abort(400, "Invalid C# options")
            if not PROJECT_RE.match(project) or not FOLDER_RE.match(publish):
                abort(400, "Invalid project name or publish folder")
            if csharp == "umbraco" and version and not VERSION_RE.match(version):
                abort(400, "Invalid Umbraco version")
            args = [site, site_type, csharp, version if csharp == "umbraco" else "", project, binding, publish, build]
        return "Create: " + site, [bash, os.path.join(APACHE_DIR, "create_site.sh"), *args], APACHE_DIR
    if kind == "change_binding":
        site = site_arg(data)
        binding = (data.get("binding") or "").lower()
        if not BINDING_RE.match(binding):
            abort(400, "Invalid binding")
        if data.get("scope") == "prod":
            if not os.path.isfile(os.path.join(WEB_ROOT, site, "scripts", "publish_prod.sh")):
                abort(400, "This site has no production site")
            return "Prod binding: " + site, [bash, os.path.join(APACHE_DIR, "change_binding_prod.sh"), site, binding], APACHE_DIR
        return "Binding: " + site, [bash, os.path.join(APACHE_DIR, "change_binding.sh"), site, binding], APACHE_DIR
    if kind == "prod_action":
        site = site_arg(data)
        action = data.get("action", "")
        if action not in ("issue_certificate", "change_env"):
            abort(400, "Unknown action")
        if data.get("scope") == "test":
            if not os.path.isdir(os.path.join(WEB_ROOT, site)):
                abort(404, "Unknown site")
            return "Test %s: %s" % (action.replace("_", " "), site), [bash, os.path.join(APACHE_DIR, "test_action.sh"), site, action], APACHE_DIR
        if not os.path.isfile(os.path.join(WEB_ROOT, site, "scripts", "publish_prod.sh")):
            abort(400, "This site has no production site")
        return "Prod %s: %s" % (action.replace("_", " "), site), [bash, os.path.join(APACHE_DIR, "prod_action.sh"), site, action], APACHE_DIR
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
    return {"id": s.id, "title": s.title, "running": s.running, "exit": s.exit}


@app.route("/api/term", methods=["GET", "POST"])
def api_term():
    if request.method == "GET":
        prune_sessions()
        with SESSIONS_LOCK:
            return jsonify(sessions=[describe(s) for s in SESSIONS.values()])
    title, argv, cwd = build_command(request.get_json(silent=True) or {})
    with SESSIONS_LOCK:
        if sum(1 for s in SESSIONS.values() if s.running) >= MAX_SESSIONS:
            return error("Too many running terminals. Close some first.")
        session_obj = TermSession(title, argv, cwd)
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
            ws.send(json.dumps({"x": s.exit}))

        def pump():
            while True:
                data = q.get()
                if data is None:
                    ws.send(json.dumps({"x": s.exit}))
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


@app.route("/api/fs/list")
def fs_list():
    full = safe_path(request.args.get("path", ""))
    if not os.path.isdir(full):
        return error("Not a folder", 404)
    items = []
    for entry in os.scandir(full):
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        is_dir = entry.is_dir(follow_symlinks=False)
        items.append({"name": entry.name, "dir": is_dir, "size": 0 if is_dir else info.st_size, "modified": int(info.st_mtime)})
    items.sort(key=lambda i: (not i["dir"], i["name"].lower()))
    return jsonify(path=rel(full), items=items)


@app.route("/api/fs/read")
def fs_read():
    full = safe_path(request.args.get("path", ""))
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
def fs_save():
    data = request.get_json(silent=True) or {}
    full = safe_path(data.get("path", ""))
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
def fs_create():
    data = request.get_json(silent=True) or {}
    parent = safe_path(data.get("path", ""))
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
def fs_rename():
    data = request.get_json(silent=True) or {}
    full = safe_path(data.get("path", ""))
    name = data.get("name", "")
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
def fs_delete():
    full = safe_path((request.get_json(silent=True) or {}).get("path", ""))
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
def fs_upload():
    parent = safe_path(request.form.get("path", ""))
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
def fs_download():
    full = safe_path(request.args.get("path", ""))
    if not os.path.isfile(full):
        return error("Not a file", 404)
    return send_file(full, as_attachment=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), threaded=True)
