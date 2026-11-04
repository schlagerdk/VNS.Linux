#!/bin/bash
# Fjerner SQL Server. Databaser i /var/opt/mssql bevares, medmindre du eksplicit vælger at slette dem.
set -euo pipefail
clear

if [[ $(id -u) -eq 0 ]]; then _sudo=""; else _sudo="sudo"; fi

if ! dpkg -s mssql-server >/dev/null 2>&1; then
	echo "mssql-server is not installed"
	exit 0
fi

echo "This will remove SQL Server (mssql-server)."
read -r -p "Continue? [y/N]: " _answer
[[ ${_answer,,} == y || ${_answer,,} == yes ]] || { echo "Aborted"; exit 1; }

echo "Stop and disable service"
${_sudo} systemctl stop mssql-server || true
${_sudo} systemctl disable mssql-server || true

echo "Remove mssql-server"
${_sudo} apt-get remove --purge -y mssql-server

echo "Remove SQL Server apt repositories"
${_sudo} rm -f /etc/apt/sources.list.d/mssql-server-*.list
${_sudo} apt-get update
${_sudo} apt-get autoremove -y

read -r -p "Also remove sqlcmd tools (mssql-tools18)? [y/N]: " _answer
if [[ ${_answer,,} == y || ${_answer,,} == yes ]]; then
	${_sudo} apt-get remove --purge -y mssql-tools18 unixodbc-dev || true
fi

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
