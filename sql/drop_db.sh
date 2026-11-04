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

_removeBackup=$2
if [[ -z ${_removeBackup} ]]; then
        read -p "Remove backup? (y/N): " _removeBackup
fi

sed -i "s|NEW_NAME|${_dbname}|g" scripts/drop_db.sql
/opt/mssql-tools18/bin/sqlcmd -C -S LOCALHOST -U sa -P '@SA_PASSWORD@' -i scripts/drop_db.sql
sed -i "s|${_dbname}|NEW_NAME|g" scripts/drop_db.sql

while true; do
	case $_removeBackup in
		[Yy]* ) rm /var/opt/mssql/backup/${_dbname}.bak; break;;
		* ) break;;
	esac
done
echo ${_dbname} is now deleted!