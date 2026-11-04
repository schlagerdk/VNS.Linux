#!/bin/bash
# Usage: delete_site.sh [site] [y|n (delete certificates)]
cd "$(dirname "$0")"
source lib/common.sh
clear

_site=${1:-}
_deleteCertificate=${2:-}

ask _site "Enter site name"
validate_site_name "${_site}" || exit 1
set_site_vars

if [[ -f ${_servicePath} ]]; then
	echo "Stop, disable and delete service"
	sudo systemctl stop "${_site}"
	sudo systemctl disable "${_site}"
	sudo rm -f "${_servicePath}"
fi

echo "Delete site.conf"
sudo rm -f "${_configPath}"
sudo rm -f "/etc/apache2/sites-enabled/${_site}.conf"

echo "Delete apache log"
sudo rm -f "/var/log/apache2/${_site}-80-error.log"
sudo rm -f "/var/log/apache2/${_site}-443-error.log"
sudo rm -f "/var/log/apache2/${_site}-rewrite-error.log"

echo "Delete site and files"
sudo rm -rf "${_rootPath:?}/${_site:?}"

echo "Reload daemon"
sudo systemctl daemon-reload

echo "Restart apache2"
sudo systemctl restart apache2

if [[ -z ${_deleteCertificate} ]]; then
	read -r -p "Delete certificates? [y/N]: " _deleteCertificate
fi

if [[ ${_deleteCertificate} =~ ^([yY]|[yY][eE][sS])$ ]]; then
	echo "Revoke and remove certificate"
	sudo "${_acme}" --revoke -d "${_site}"
	sudo "${_acme}" --remove -d "${_site}"
	sudo rm -rf "${_certificateDirectory}"
	echo "All is removed!"
fi
