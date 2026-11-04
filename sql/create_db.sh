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
sed -i "s|NEW_NAME|${_dbname}|g" scripts/create_db.sql
/opt/mssql-tools18/bin/sqlcmd -C -S LOCALHOST -U sa -P Serverskrue22 -i scripts/create_db.sql
sed -i "s|${_dbname}|NEW_NAME|g" scripts/create_db.sql
echo ${_dbname} is now created!