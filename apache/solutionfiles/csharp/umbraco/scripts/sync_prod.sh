#!/bin/bash
# Mirror Umbraco content folders (uSync, Views, css, scripts, media) from this site's publish folder to production.
# Each folder is mirrored (files missing here are deleted on prod), like robocopy /mir.
cd "$(dirname "$0")"
cd ..
set -euo pipefail

# ---- Production settings --------------------------------------------------
PROD_IP=""                      # IP or hostname of the production server
PROD_USER="root"                # SSH login
PROD_PASSWORD=""                # SSH password (needs sshpass). Leave empty to use an SSH key
PROD_SSH_PORT=22
PROD_FOLDER="@SITE@"            # Folder name on prod (/var/www/<folder>)
# ---------------------------------------------------------------------------

_publishFolder=@PUBLISH_FOLDER@
_destination=/var/www/${PROD_FOLDER}

if [[ -z ${PROD_IP} || -z ${PROD_USER} || -z ${PROD_FOLDER} ]]; then
	echo "Destination not found: set PROD_IP, PROD_USER and PROD_FOLDER at the top of this script" >&2
	exit 1
fi

_ssh=(ssh -p "${PROD_SSH_PORT}" -o StrictHostKeyChecking=accept-new)
if [[ -n ${PROD_PASSWORD} ]]; then
	command -v sshpass >/dev/null || { echo "sshpass is required for password login: sudo apt install sshpass" >&2; exit 1; }
	export SSHPASS=${PROD_PASSWORD}
	_ssh=(sshpass -e "${_ssh[@]}")
fi
_target=${PROD_USER}@${PROD_IP}
_sudo=""
[[ ${PROD_USER} == root ]] || _sudo="sudo -n"

cd "${_publishFolder}"

sync_folder() {
	local _label=$1 _path=$2
	if [[ ! -d ${_path} ]]; then
		echo "${_label}: SKIPPED (${_path} not found)"
		echo
		return
	fi
	"${_ssh[@]}" "${_target}" "${_sudo} mkdir -p '${_destination}/${_path}'"
	rsync -az --delete -e "${_ssh[*]}" --rsync-path="${_sudo:+${_sudo} }rsync" "${_path}/" "${_target}:${_destination}/${_path}/"
	echo "${_label}: OK"
	echo
}

sync_folder uSync uSync
sync_folder Views Views
sync_folder CSS wwwroot/css
sync_folder Scripts wwwroot/scripts
sync_folder Media wwwroot/media

"${_ssh[@]}" "${_target}" "${_sudo} chown -R www-data:www-data '${_destination}'"
