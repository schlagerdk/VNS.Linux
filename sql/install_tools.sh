#!/bin/bash
# Installerer sqlcmd (mssql-tools18) til den Ubuntu-version, serveren kører.
set -euo pipefail

_sqlDir="$(cd "$(dirname "$0")" && pwd)"
source "${_sqlDir}/lib/microsoft_repo.sh"

if [[ $(id -u) -eq 0 ]]; then _sudo=""; else _sudo="sudo"; fi

source /etc/os-release
if [[ ${ID} != ubuntu ]]; then
	echo "This script supports Ubuntu only (found: ${ID})" >&2
	exit 1
fi

if ! command -v gpg >/dev/null 2>&1; then
	${_sudo} apt-get install -y gnupg
fi

echo "Add Microsoft repository"
install_microsoft_keys "${_sudo}"
curl -fsSL "https://packages.microsoft.com/config/ubuntu/${VERSION_ID}/prod.list" | ${_sudo} tee /etc/apt/sources.list.d/mssql-release.list >/dev/null

echo "Install mssql-tools18"
${_sudo} apt-get update
${_sudo} env ACCEPT_EULA=Y apt-get install -y mssql-tools18 unixodbc-dev

echo "Add sqlcmd to PATH for all users"
echo 'export PATH="$PATH:/opt/mssql-tools18/bin"' | ${_sudo} tee /etc/profile.d/mssql-tools.sh >/dev/null

if [[ ! -x /opt/mssql-tools18/bin/sqlcmd ]]; then
	echo "Installation finished, but /opt/mssql-tools18/bin/sqlcmd was not found." >&2
	echo "Candidate version:" >&2
	apt-cache policy mssql-tools18 >&2 || true
	exit 1
fi

echo "Installed: $(/opt/mssql-tools18/bin/sqlcmd '-?' | sed -n 1p)"
echo "Open a new shell to get sqlcmd on PATH"
