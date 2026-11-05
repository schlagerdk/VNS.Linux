#!/bin/bash
# Update an existing admin site to the code in /vns (run update.sh first to fetch the latest version).
# The login (config.json), the virtual environment and the downloaded terminal library are kept.
# Usage: update_admin_site.sh [folder]
set -euo pipefail
cd "$(dirname "$0")"
source lib/common.sh
source lib/admin_vendor.sh

_site=${1:-}

_found=()
for _dir in "${_rootPath}"/*/; do
	_name=$(basename "${_dir}")
	if sudo test -f "${_dir}app/manage.py" && sudo test -f "${_dir}app/config.json"; then
		_found+=("${_name}")
	fi
done

if [[ -z ${_site} ]]; then
	if ((${#_found[@]} == 0)); then
		echo "No admin site found in ${_rootPath}. Use install_admin_site.sh." >&2
		exit 1
	elif ((${#_found[@]} == 1)); then
		_site=${_found[0]}
	else
		echo "Admin sites: ${_found[*]}"
		ask _site "Enter admin site folder name"
	fi
fi

_isAdmin=0
for _name in "${_found[@]}"; do [[ ${_name} == "${_site}" ]] && _isAdmin=1; done
if ((_isAdmin == 0)); then
	echo "${_site} is not an admin site created by install_admin_site.sh." >&2
	exit 1
fi

set_site_vars
echo "Update the app files"
sudo rsync -a --delete \
	--exclude /config.json --exclude /static/vendor/ --exclude /__pycache__/ \
	"${_solutionFolder}/admin/app/" "${_siteRoot}/app/"
sudo chown -R root:root "${_siteRoot}/app"
sudo chmod 700 "${_siteRoot}/app"

download_admin_vendor "${_siteRoot}/app"

echo "Update the Python packages"
sudo "${_siteRoot}/venv/bin/pip" install --quiet -r "${_siteRoot}/app/requirements.txt"

echo "Update the scripts"
install_scripts "${_solutionFolder}/admin/scripts"

echo "Restart the admin site"
# The service uses KillMode=process, so terminals opened in the admin site keep running
sudo systemctl restart "${_site}"
echo "Admin site '${_site}' updated."
