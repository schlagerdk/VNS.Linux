#!/bin/bash
cd "$(dirname "$0")"
clear
# -W trims padding, -s sets a column separator; sed drops the dashed header line and column re-aligns the output
/opt/mssql-tools18/bin/sqlcmd -C -S LOCALHOST -U sa -P '@SA_PASSWORD@' -W -s '|' -i scripts/list_dbs.sql |
	sed '2d' | column -t -s '|'
