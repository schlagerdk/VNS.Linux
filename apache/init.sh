#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
source lib/dotnet.sh
clear

_adminMail=mhs@vns.dk
_dotnetDir=/usr/lib/dotnet
_dotnetChannel=10.0

echo Install prerequisites
sudo apt-get update
sudo apt-get install -y curl tar git ca-certificates uuid-runtime python3 python3-venv python3-pip

echo Install apache2
sudo apt-get install -y apache2

echo Create directory for the per-site empty roots used to isolate the site services
sudo mkdir -p /var/lib/vns-roots

echo Enable apache modules
sudo a2enmod ssl rewrite headers proxy proxy_http proxy_wstunnel

echo Install sshpass
sudo apt install -y sshpass

echo "Test Apache config and reload (start if stopped)"
sudo apache2ctl configtest
if sudo systemctl is-active --quiet apache2; then
	sudo systemctl reload apache2
else
	sudo systemctl start apache2
fi

if [[ ! -x /etc/acmesh/acme.sh ]]; then
	echo Install acme.sh
	_acmeTmp=$(mktemp -d)
	git clone https://github.com/acmesh-official/acme.sh.git "${_acmeTmp}"
	(cd "${_acmeTmp}" && sudo ./acme.sh --install --home /etc/acmesh --config-home /etc/ssl/data --cert-home /etc/ssl/certs --accountemail "${_adminMail}")
	rm -rf "${_acmeTmp}"
fi

echo Set letsencrypt to default
sudo /etc/acmesh/acme.sh --set-default-ca --server letsencrypt

echo Add cron job for acme
sudo /etc/acmesh/acme.sh --install-cronjob

# Install the default SDK and runtime, plus .NET 8 for Umbraco 13.
echo "Install .NET ${_dotnetChannel} SDK and ASP.NET Core runtime"
sudo mkdir -p "${_dotnetDir}"
curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh
sudo bash /tmp/dotnet-install.sh --channel "${_dotnetChannel}" --install-dir "${_dotnetDir}"
sudo bash /tmp/dotnet-install.sh --channel "${_dotnetChannel}" --runtime aspnetcore --install-dir "${_dotnetDir}"
rm -f /tmp/dotnet-install.sh
ensure_dotnet_channel 8.0

# The generated systemd services use /usr/bin/dotnet
sudo ln -sf "${_dotnetDir}/dotnet" /usr/bin/dotnet

echo Set DOTNET_ROOT for all users
echo "export DOTNET_ROOT=${_dotnetDir}" | sudo tee /etc/profile.d/dotnet.sh >/dev/null

echo Install dotnet-outdated
sudo DOTNET_ROOT="${_dotnetDir}" \
	dotnet tool install --tool-path /usr/local/bin dotnet-outdated-tool 2>/dev/null ||
	sudo DOTNET_ROOT="${_dotnetDir}" dotnet tool update --tool-path /usr/local/bin dotnet-outdated-tool

echo "Installed runtimes:"
dotnet --list-runtimes
echo "Installed SDKs:"
dotnet --list-sdks

echo
read -r -p "Set up the admin site now (web UI for managing all sites)? [y/N]: " _setupAdmin
if [[ ${_setupAdmin,,} == y* ]]; then
	sudo "$(pwd)/install_admin_site.sh"
fi
