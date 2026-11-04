#!/bin/bash
clear
/opt/mssql-tools18/bin/sqlcmd -C -S LOCALHOST -U sa -P '@SA_PASSWORD@' -Q "sp_databases"