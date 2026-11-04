#!/bin/bash
cd ${0%/*}

TZ=":Europe/Copenhagen"

echo "----- `date '+%d-%m-%Y %H:%M:%S'` -------------------------------------------------------------------------------"
/opt/mssql-tools18/bin/sqlcmd -C -S LOCALHOST -U sa -P Serverskrue22 -i ../scripts/shrink_logs.sql
echo "All logs now shrinked! -------------------------------------------------------- `date '+%d-%m-%Y %H:%M:%S'` -----"
echo ""
