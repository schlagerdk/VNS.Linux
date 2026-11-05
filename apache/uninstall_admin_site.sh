#!/bin/bash
# Remove the admin site created by install_admin_site.sh: service, Apache config, certificate (optional),
# logs, virtual environment, login and all files. Other sites in /var/www are not touched.
# Usage: uninstall_admin_site.sh [folder] [y|n (delete certificates)]
set -euo pipefail
cd "$(dirname "$0")"
source lib/common.sh

_site=${1:-}
_deleteCertificate=${2:-}

# An admin site is recognised by its app folder (manage.py + config.json), not by its name
_found=()
for _dir in "${_rootPath}"/*/; do
	_name=$(basename "${_dir}")
	if sudo test -f "${_dir}app/manage.py" && sudo test -f "${_dir}app/config.json"; then
		_found+=("${_name}")
	fi
done

if [[ -z ${_site} ]]; then
	if ((${#_found[@]} == 0)); then
		echo "No admin site found in ${_rootPath}."
		exit 0
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

echo "This removes the admin site '${_site}' completely (files, login, service and Apache config)."
echo "If you run this from the admin site's own terminal, the terminal will end when the site is removed."
read -r -p "Type the folder name to confirm: " _confirm
[[ ${_confirm} == "${_site}" ]] || { echo "Cancelled."; exit 1; }

if [[ -z ${_deleteCertificate} ]]; then
	read -r -p "Also revoke and remove the certificate? [y/N]: " _deleteCertificate
fi
[[ ${_deleteCertificate} =~ ^([yY]|[yY][eE][sS])$ ]] && _deleteCertificate=y || _deleteCertificate=n

# delete_site.sh does the removal (service, Apache config, logs, folder, optional certificate)
exec ./delete_site.sh "${_site}" "${_deleteCertificate}"
