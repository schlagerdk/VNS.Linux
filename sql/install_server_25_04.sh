#! /bin/bash
clear

_sqlDir="$(cd "$(dirname "$0")" && pwd)"
source "${_sqlDir}/lib/sa_password.sh"
sa_prompt_and_apply "${_sqlDir}"
echo "Use the same password in the setup below."

# download and install libldap-2.5-0
curl -O http://debian.mirror.ac.za/debian/pool/main/o/openldap/libldap-2.5-0_2.5.13+dfsg-5_amd64.deb
dpkg -i libldap-2.5-0_2.5.13+dfsg-5_amd64.deb

# download and install libldap-dev
curl -O http://debian.mirror.ac.za/debian/pool/main/o/openldap/libldap-dev_2.5.13+dfsg-5_amd64.deb
dpkg -i libldap-dev_2.5.13+dfsg-5_amd64.deb

curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | sudo gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg
curl -fsSL https://packages.microsoft.com/config/ubuntu/24.04/mssql-server-2025.list | sudo tee /etc/apt/sources.list.d/mssql-server-2025.list

# wipe out the rebuild apt lists
rm -rf /var/lib/apt/lists/* && apt update

# install mssql-server
apt install -y mssql-server

#run the config tool
/opt/mssql/bin/mssql-conf setup

# Create backup folder
mkdir /var/opt/mssql/backup
chmod -R 757 /var/opt/mssql/backup

# Get status for service
systemctl status mssql-server --no-pager
