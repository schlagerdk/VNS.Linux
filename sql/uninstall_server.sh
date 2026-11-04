#!/bin/bash
# Removes SQL Server and the sqlcmd tools. Databases in /var/opt/mssql are preserved unless you explicitly choose to delete them.
set -euo pipefail
clear

if [[ $(id -u) -eq 0 ]]; then _sudo=""; else _sudo="sudo"; fi

_hasServer=0; _hasTools=0
dpkg -s mssql-server >/dev/null 2>&1 && _hasServer=1
dpkg -s mssql-tools18 >/dev/null 2>&1 && _hasTools=1

if ((_hasServer == 0 && _hasTools == 0)); then
	echo "Neither mssql-server nor mssql-tools18 is installed"
	exit 0
fi

echo "This will remove SQL Server (mssql-server) and the sqlcmd tools (mssql-tools18)."
read -r -p "Continue? [y/N]: " _answer
[[ ${_answer,,} == y || ${_answer,,} == yes ]] || { echo "Aborted"; exit 1; }

if ((_hasServer)); then
	echo "Stop and disable service"
	${_sudo} systemctl stop mssql-server || true
	${_sudo} systemctl disable mssql-server || true

	echo "Remove mssql-server"
	${_sudo} apt-get remove --purge -y mssql-server
fi

if ((_hasTools)); then
	echo "Remove mssql-tools18"
	${_sudo} apt-get remove --purge -y mssql-tools18 msodbcsql18 unixodbc-dev || true
fi

echo "Remove Microsoft apt repositories, PATH entry and keys"
${_sudo} rm -f /etc/apt/sources.list.d/mssql-server-*.list /etc/apt/sources.list.d/mssql-release.list
${_sudo} rm -f /etc/profile.d/mssql-tools.sh
# Remove the keys only if no other repository uses them
if ! grep -qs 'microsoft-prod.gpg' /etc/apt/sources.list /etc/apt/sources.list.d/* 2>/dev/null; then
	${_sudo} rm -f /usr/share/keyrings/microsoft-prod.gpg
fi
${_sudo} apt-get update
${_sudo} apt-get autoremove -y

echo
echo "Databases, logs and backups are stored in /var/opt/mssql"
echo "Type DELETE to permanently delete this folder, or press Enter to keep it."
read -r -p "> " _answer
if [[ ${_answer} == DELETE ]]; then
	${_sudo} rm -rf /var/opt/mssql
	echo "/var/opt/mssql deleted"
else
	echo "/var/opt/mssql kept"
fi

echo "SQL Server removed"
