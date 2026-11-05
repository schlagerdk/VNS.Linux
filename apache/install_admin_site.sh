#!/bin/bash
# Create the admin site: a web page to manage every site in /var/www.
# It can create, delete and change the binding of sites (the scripts in /vns/apache), run each site's scripts,
# open a shell in a site, and browse/edit/upload/download files.
# Usage: install_admin_site.sh [folder] [binding] [username]
# The site runs as root. Use a long password and, if you can, restrict it to your own IP addresses.
set -euo pipefail
cd "$(dirname "$0")"
source lib/common.sh
source lib/admin_vendor.sh

trap 'echo "FAILED at ${BASH_SOURCE[0]}:${LINENO} (exit $?): ${BASH_COMMAND}" >&2; echo "Admin site is incomplete - run ./delete_site.sh ${_site:-<site>} n to clean up" >&2' ERR
set -o errtrace


_site=${1:-}
_binding=${2:-}
_adminUser=${3:-}

ask _site "Enter admin site folder name"
validate_site_name "${_site}"

if [[ -z ${_binding} && ${_site} == *.* ]]; then
	ask _binding "Enter admin site URL binding" "${_site}"
else
	ask _binding "Enter admin site URL binding"
fi
validate_binding "${_binding}"
_binding=${_binding,,}

ask _adminUser "Admin username" admin
if [[ ! ${_adminUser} =~ ^[A-Za-z0-9._@-]{1,64}$ ]]; then
	echo "Invalid username. Use letters, numbers and . _ @ - (max 64)." >&2
	exit 1
fi

_adminPassword=${VNS_ADMIN_PASSWORD:-}
if [[ -z ${_adminPassword} ]]; then
	read -r -s -p "Admin password (min 12 characters): " _adminPassword
	echo
	read -r -s -p "Repeat password: " _repeat
	echo
	[[ ${_adminPassword} == "${_repeat}" ]] || { echo "The passwords do not match." >&2; exit 1; }
fi
if ((${#_adminPassword} < 12)); then
	echo "The password must be at least 12 characters." >&2
	exit 1
fi

echo "The admin site gives root access to this server. Restricting it to your own IP addresses is strongly recommended."
read -r -p "Allowed IP addresses or CIDR ranges, separated by spaces (empty = everyone): " _allowedIps
_accessRules="Require all granted"
if [[ -n ${_allowedIps// /} ]]; then
	for _ip in ${_allowedIps}; do
		if [[ ! ${_ip} =~ ^[0-9A-Fa-f:.]+(/[0-9]{1,3})?$ ]]; then
			echo "Invalid IP address or range: ${_ip}" >&2
			exit 1
		fi
	done
	_accessRules="Require ip ${_allowedIps}"
fi

set_site_vars
if [[ -e ${_siteRoot} ]]; then
	echo "${_siteRoot} already exists." >&2
	exit 1
fi

_docRoot="${_siteRoot}/wwwroot"
allocate_ports
sudo mkdir -p "${_siteRoot}/app/static/vendor" "${_docRoot}"
sudo a2enmod ssl proxy proxy_http proxy_wstunnel rewrite headers >/dev/null
issue_certificate

echo "Copy the admin app"
sudo cp -R "${_solutionFolder}/admin/app/." "${_siteRoot}/app/"

download_admin_vendor "${_siteRoot}/app"

echo "Create virtual environment"
sudo python3 -m venv "${_siteRoot}/venv"
sudo "${_siteRoot}/venv/bin/pip" install --quiet -r "${_siteRoot}/app/requirements.txt"

echo "Create login"
sudo env VNS_ADMIN_PASSWORD="${_adminPassword}" "${_siteRoot}/venv/bin/python" "${_siteRoot}/app/manage.py" init \
	--user "${_adminUser}" --admin-site "${_site}" --web-root "${_rootPath}" --apache-dir "${_apacheDir}"
unset _adminPassword _repeat
sudo chown -R root:root "${_siteRoot}/app" "${_siteRoot}/venv"
sudo chmod 700 "${_siteRoot}/app"

install_scripts "${_solutionFolder}/common/scripts"
install_scripts "${_solutionFolder}/common/service-scripts"
install_scripts "${_solutionFolder}/admin/scripts"

install_apache_config "${_solutionFolder}/admin/apache.conf.tpl"
install_service "${_solutionFolder}/admin/service.tpl"

echo "Admin site created: https://${_binding}/ (user: ${_adminUser}, folder: ${_site})"
echo "Change the password later with ${_siteRoot}/scripts/set_password.sh"
