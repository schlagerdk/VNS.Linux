#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
clear

_dbname=${1:-}
if [[ -z ${_dbname} ]]; then
	read -r -p "Enter db name: " _dbname
	if [[ -z ${_dbname} ]]; then
		echo "No database name given" >&2
		exit 1
	fi
fi

source lib/new_login.sh
prompt_new_login_password

_sqlFile=$(mktemp)
trap 'rm -f "$_sqlFile"' EXIT
render_sql_template scripts/create_db.sql "${_sqlFile}" \
	-e "s|NEW_NAME|${_dbname}|g"
/opt/mssql-tools18/bin/sqlcmd -b -C -S LOCALHOST -U sa -P '@SA_PASSWORD@' -i "${_sqlFile}"
echo "${_dbname} is now created!"