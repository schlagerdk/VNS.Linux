#!/bin/bash
cd ${0%/*}
clear

_olddbname=$1
if [[ -z ${_olddbname} ]]; then
	read -p "Enter old db name: " _olddbname
	if [[ -z ${_olddbname} ]]; then
		read -p "No name given..."
		exit 1
	fi
fi

_newdbname=$2
if [[ -z ${_newdbname} ]]; then
	read -p "Enter new db name: " _newdbname
	if [[ -z ${_newdbname} ]]; then
		read -p "No name given..."
		exit 1
	fi
fi

sed -i "s|OLD_NAME|${_olddbname}|g" scripts/copy_db.sql
sed -i "s|NEW_NAME|${_newdbname}|g" scripts/copy_db.sql
/opt/mssql-tools18/bin/sqlcmd -C -S LOCALHOST -U sa -P '@SA_PASSWORD@' -i scripts/copy_db.sql
sed -i "s|${_olddbname}|OLD_NAME|g" scripts/copy_db.sql
sed -i "s|${_newdbname}|NEW_NAME|g" scripts/copy_db.sql
echo ${_olddbname} is now copyed to ${_newdbname}!