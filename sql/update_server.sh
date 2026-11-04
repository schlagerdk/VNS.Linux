#!/bin/bash
# Updates SQL Server (and sqlcmd tools) to the latest version in the configured repository.
# Switching to a new major version (e.g. 2022 -> 2025) requires a new installation with install_server.sh.
set -euo pipefail
clear

if [[ $(id -u) -eq 0 ]]; then _sudo=""; else _sudo="sudo"; fi

if ! dpkg -s mssql-server >/dev/null 2>&1; then
	echo "mssql-server is not installed - run install_server.sh first" >&2
	exit 1
fi

_before=$(dpkg-query -W -f='${Version}' mssql-server)
echo "Installed version: ${_before}"

echo "Check for updates"
${_sudo} apt-get update
if ! apt list --upgradable 2>/dev/null | grep -q '^mssql-server/'; then
	echo "SQL Server is already up to date"
else
	echo "Update available. Take a backup first - e.g. /vns/sql/cron/backup_everything.sh"
	read -r -p "Update now? The service is restarted. [y/N]: " _answer
	[[ ${_answer,,} == y || ${_answer,,} == yes ]] || { echo "Aborted"; exit 1; }

	${_sudo} apt-get install --only-upgrade -y mssql-server
	echo "Restart service"
	${_sudo} systemctl restart mssql-server
fi

if dpkg -s mssql-tools18 >/dev/null 2>&1; then
	${_sudo} apt-get install --only-upgrade -y mssql-tools18
fi

echo "Version now: $(dpkg-query -W -f='${Version}' mssql-server)"
${_sudo} systemctl status mssql-server --no-pager
