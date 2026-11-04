#! /bin/bash
clear

_sqlDir="$(cd "$(dirname "$0")" && pwd)"
source "${_sqlDir}/lib/sa_password.sh"
sa_prompt_and_apply "${_sqlDir}"
echo "Use the same password in the setup below."

# Install sql server
curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | sudo gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg
curl https://packages.microsoft.com/keys/microsoft.asc | sudo tee /etc/apt/trusted.gpg.d/microsoft.asc
curl -fsSL https://packages.microsoft.com/config/ubuntu/22.04/mssql-server-2022.list | sudo tee /etc/apt/sources.list.d/mssql-server-2022.list
sudo apt update
sudo apt install -y mssql-server
sudo /opt/mssql/bin/mssql-conf setup

# Create backup folder
mkdir /var/opt/mssql/backup
chmod -R 757 /var/opt/mssql/backup

# Get status for service
systemctl status mssql-server --no-pager