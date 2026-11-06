#!/bin/bash
# Usage: remote_site.sh <site> <status|start|stop|restart|logs|check_service|edit_service|edit_conf|shell|files|config>
set -euo pipefail
cd "$(dirname "$0")"
source lib/common.sh
source lib/prod.sh
_site=${1:-}
_action=${2:-}
validate_site_name "${_site}"
set_site_vars
load_prod_settings
[[ -z ${PROD_LOCAL_PATH} ]] || { echo "This is not a remote production target." >&2; exit 1; }
validate_site_name "${PROD_FOLDER}"
[[ ${PROD_IP} =~ ^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$ ]] || { echo "Invalid remote host." >&2; exit 1; }
[[ ${PROD_USER} =~ ^[A-Za-z_][A-Za-z0-9_.-]{0,63}$ ]] || { echo "Invalid SSH username." >&2; exit 1; }
[[ ${PROD_SSH_PORT} =~ ^[0-9]+$ && ${PROD_SSH_PORT} -ge 1 && ${PROD_SSH_PORT} -le 65535 ]] || { echo "Invalid SSH port." >&2; exit 1; }
printf -v _vars 'SITE=%q' "${PROD_FOLDER}"
case "${_action}" in
	status)
		_database_code=$(cat lib/database_info.py)
		printf -v _vars '%s DATABASE_CODE=%q' "${_vars}" "${_database_code}"
		prod_run "${_vars}" <<'REMOTE'
set -euo pipefail
python3 - <<'PY'
import json
import os
import re
import shlex
import subprocess
from pathlib import Path
site = os.environ["SITE"]
service = Path("/etc/systemd/system", site + ".service")
conf = Path("/etc/apache2/sites-available", site + ".conf")
text = conf.read_text() if conf.is_file() else ""
service_text = service.read_text() if service.is_file() else ""
state = subprocess.run(["systemctl", "is-active", site + ".service"], capture_output=True, text=True)
if state.returncode not in (0, 3, 4):
    raise RuntimeError(state.stderr or "Could not read remote service status")
secure = bool(re.search(r"<VirtualHost\s+[^>]*:443>", text))
certificate = re.search(r'^\s*SSLCertificateFile\s+"?([^"\s]+)', text, re.M)
environment = ""
variables = {}
if service.is_file():
    settings = subprocess.run(["systemctl", "show", site + ".service", "--property=Environment", "--value"],
                              capture_output=True, text=True, check=True)
    variables = dict(item.split("=", 1) for item in shlex.split(settings.stdout) if "=" in item)
    environment = variables.get("DOTNET_ENVIRONMENT") or variables.get("ASPNETCORE_ENVIRONMENT", "")
port = re.search(r"(?:localhost|127\.0\.0\.1):(\d+)", service_text)
binding = re.search(r"^\s*ServerName\s+(\S+)", text, re.M)
aliases = []
seen = {binding.group(1).lower()} if binding else set()
for line in text.splitlines():
    fields = line.split("#", 1)[0].split()
    if len(fields) > 1 and fields[0].lower() == "serveralias":
        for alias in fields[1:]:
            if alias.lower() not in seen:
                aliases.append(alias)
                seen.add(alias.lower())
exec(os.environ.pop("DATABASE_CODE"))
database = {"database": "Unknown", "database_name": ""}
try:
    database = database_details(Path("/var/www", site), environment, variables)
except (OSError, ValueError):
    print("Could not read remote database configuration", file=__import__("sys").stderr)
print(json.dumps({
    "unit": site if service.is_file() else "",
    "state": state.stdout.strip() if service.is_file() else "",
    "exists": Path("/var/www", site).is_dir(),
    "scheme": "https" if secure else "http",
    "https": secure and bool(certificate and Path(certificate.group(1)).is_file()),
    "environment": environment,
    **database,
    "binding": binding.group(1) if binding else "",
    "aliases": aliases,
    "port": port.group(1) if port else "",
}))
PY
REMOTE
		;;
	start|stop|restart)
		prod_run "${_vars} ACTION=${_action}" <<'REMOTE'
set -euo pipefail
systemctl "${ACTION}" "${SITE}.service"
REMOTE
		;;
	logs|check_service)
		prod_run "${_vars} ACTION=${_action}" <<'REMOTE'
set -euo pipefail
if [[ ${ACTION} == logs ]]; then
	journalctl -u "${SITE}.service" -n 300 --no-pager -o short-iso
else
	rc=0
	TERM=xterm-256color SYSTEMD_COLORS=1 systemctl status "${SITE}.service" --no-pager -l || rc=$?
	[[ ${rc} -eq 0 || ${rc} -eq 3 ]]
fi
REMOTE
		;;
	edit_service|edit_conf|shell)
		case "${_action}" in
			edit_service)
				_code='set -e; nano "/etc/systemd/system/$SITE.service"; read -r -p "Reload daemon and restart service? [Y/n]: " answer; if [[ ! $answer =~ ^[nN] ]]; then systemctl daemon-reload; systemctl restart "$SITE.service"; systemctl status "$SITE.service" --no-pager; fi'
				;;
			edit_conf)
				_code='set -e; nano "/etc/apache2/sites-available/$SITE.conf"; read -r -p "Test config and reload Apache? [Y/n]: " answer; if [[ ! $answer =~ ^[nN] ]]; then apache2ctl configtest; systemctl reload apache2; fi'
				;;
			shell) _code='cd "/var/www/$SITE" && export force_color_prompt=yes && exec bash -il' ;;
		esac
		printf -v _command 'env TERM=xterm-256color COLORTERM=truecolor %s bash -c %q' "${_vars}" "${_code}"
		prod_exec "${_command}" tty
		;;
	files|config)
		if [[ ${_action} == config ]]; then
			_code=$(cat lib/managed_config.py)
		else
			_code=$(cat lib/remote_files.py)
		fi
		printf -v _command 'env %s python3 -c %q' "${_vars}" "${_code}"
		prod_exec "${_command}"
		;;
	*)
		echo "Unknown remote action: ${_action}" >&2
		exit 1
		;;
esac
