#!/bin/bash
cd ${0%/*}
clear

_dbname=$1
if [[ -z ${_dbname} ]]; then
	read -p "Enter db name: " _dbname
	if [[ -z ${_dbname} ]]; then
		read -p "No name given..."
		exit 1
	fi
fi
sed -i "s|DB_NAME|${_dbname}|g" scripts/clean_umbraco7_db.sql
/opt/mssql-tools18/bin/sqlcmd -C -S LOCALHOST -U sa -P Serverskrue22 -i scripts/clean_umbraco7_db.sql
sed -i "s|${_dbname}|DB_NAME|g" scripts/clean_umbraco7_db.sql
echo "${_dbname} now clean!"