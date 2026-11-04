#!/bin/bash
cd ${0%/*}

TZ=":Europe/Copenhagen"

echo "----- `date '+%d-%m-%Y %H:%M:%S'` -------------------------------------------------------------------------------"
/opt/mssql-tools18/bin/sqlcmd -C -S LOCALHOST -U sa -P Serverskrue22 -i ../scripts/delete_old_2dm_data.sql
echo "Old 2DM data is deleted! ------------------------------------------------------- `date '+%d-%m-%Y %H:%M:%S'` -----"