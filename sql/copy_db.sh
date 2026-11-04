#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
clear

_olddbname=${1:-}
if [[ -z ${_olddbname} ]]; then
	read -r -p "Enter old db name: " _olddbname
	if [[ -z ${_olddbname} ]]; then
		echo "No source database name given" >&2
		exit 1
	fi
fi

_newdbname=${2:-}
if [[ -z ${_newdbname} ]]; then
	read -r -p "Enter new db name: " _newdbname
	if [[ -z ${_newdbname} ]]; then
		echo "No destination database name given" >&2
		exit 1
	fi
fi

source lib/new_login.sh
prompt_new_login_password

_sqlFile=$(mktemp)
trap 'rm -f "$_sqlFile"' EXIT
render_sql_template scripts/copy_db.sql "${_sqlFile}" \
	-e "s|OLD_NAME|${_olddbname}|g" \
	-e "s|NEW_NAME|${_newdbname}|g"
/opt/mssql-tools18/bin/sqlcmd -b -C -S LOCALHOST -U sa -P '@SA_PASSWORD@' -i "${_sqlFile}"
echo "${_olddbname} was copied to ${_newdbname}."