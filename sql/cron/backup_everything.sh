#!/bin/bash
cd ${0%/*}

TZ=":Europe/Copenhagen"

echo "----- `date '+%d-%m-%Y %H:%M:%S'` -------------------------------------------------------------------------------"
/opt/mssql-tools18/bin/sqlcmd -C -S LOCALHOST -U sa -P Serverskrue22 -i ../scripts/backup_everything.sql
echo "Backup of everything done! ----------------------------------------------------- `date '+%d-%m-%Y %H:%M:%S'` -----"