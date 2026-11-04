#!/bin/bash
clear
/opt/mssql-tools18/bin/sqlcmd -C -S LOCALHOST -U sa -P Serverskrue22 -Q "sp_databases"