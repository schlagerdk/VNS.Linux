#!/bin/bash
cd ${0%/*}

TZ=":Europe/Copenhagen"

echo "----- `date '+%d-%m-%Y %H:%M:%S'` -------------------------------------------------------------------------------"
/opt/mssql-tools18/bin/sqlcmd -C -S LOCALHOST -U sa -P Serverskrue22 -i ../scripts/cprlaas_auto_lock.sql
echo "All open CPRs with autolock enabled is now locked! ---------------------------- `date '+%d-%m-%Y %H:%M:%S'` -----"
echo ""
