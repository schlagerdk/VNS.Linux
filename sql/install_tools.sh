#!/bin/bash
# Installerer sqlcmd (mssql-tools18) til den Ubuntu-version, serveren kører.
set -euo pipefail
clear

if [[ $(id -u) -eq 0 ]]; then _sudo=""; else _sudo="sudo"; fi

source /etc/os-release
if [[ ${ID} != ubuntu ]]; then
	echo "This script supports Ubuntu only (found: ${ID})" >&2
	exit 1
fi

echo "Add Microsoft repository"
curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | ${_sudo} gpg --dearmor --yes -o /usr/share/keyrings/microsoft-prod.gpg
curl -fsSL "https://packages.microsoft.com/config/ubuntu/${VERSION_ID}/prod.list" | ${_sudo} tee /etc/apt/sources.list.d/mssql-release.list >/dev/null

echo "Install mssql-tools18"
${_sudo} apt-get update
${_sudo} env ACCEPT_EULA=Y apt-get install -y mssql-tools18 unixodbc-dev

echo "Add sqlcmd to PATH for all users"
echo 'export PATH="$PATH:/opt/mssql-tools18/bin"' | ${_sudo} tee /etc/profile.d/mssql-tools.sh >/dev/null

echo "Installed: /opt/mssql-tools18/bin/sqlcmd (open a new shell to get it on PATH)"
