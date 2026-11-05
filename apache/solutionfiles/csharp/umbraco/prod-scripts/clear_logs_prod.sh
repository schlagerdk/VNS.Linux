#!/bin/bash
# Clear the Umbraco logs on the production server over SSH.
set -euo pipefail

# ---- Production settings (filled in by create_prod.sh, edit if needed) -----
PROD_LOCAL_PATH=@PROD_LOCAL_PATH@   # If set: clear logs in this local folder instead of on a remote server
PROD_IP=@PROD_IP@                   # IP or hostname of the production server
PROD_USER=@PROD_USER@               # SSH login
PROD_PASSWORD=@PROD_PASSWORD@       # SSH password (needs sshpass). Empty = use an SSH key
PROD_SSH_PORT=@PROD_SSH_PORT@
PROD_FOLDER=@PROD_FOLDER@           # Site name on prod (/var/www/<folder>)
# ---------------------------------------------------------------------------

if [[ -n ${PROD_LOCAL_PATH} ]]; then
	# Local folder: no service is managed, only the log folder is deleted
	_root=${PROD_LOCAL_PATH%/}
	_sudo=""
	[[ $EUID -eq 0 ]] || _sudo="sudo"
	_dir="${_root}/umbraco/Logs"
	if [[ -d ${_dir} ]]; then
		${_sudo} rm -rf "${_dir}"
		echo "Deleted ${_dir}"
	else
		echo "Nothing to delete: ${_dir} not found"
	fi
	exit 0
fi

if [[ -z ${PROD_IP} || -z ${PROD_USER} || -z ${PROD_FOLDER} ]]; then
	echo "Set PROD_IP, PROD_USER and PROD_FOLDER (or PROD_LOCAL_PATH) at the top of this script" >&2
	exit 1
fi

_ssh=(ssh -p "${PROD_SSH_PORT}" -o StrictHostKeyChecking=accept-new)
if [[ -n ${PROD_PASSWORD} ]]; then
	command -v sshpass >/dev/null || { echo "sshpass is required for password login: sudo apt install sshpass" >&2; exit 1; }
	export SSHPASS=${PROD_PASSWORD}
	_ssh=(sshpass -e "${_ssh[@]}")
fi
_sudo=""
[[ ${PROD_USER} == root ]] || _sudo="sudo -n"

"${_ssh[@]}" "${PROD_USER}@${PROD_IP}" "${_sudo} bash -s" <<REMOTE
set -e
systemctl stop '${PROD_FOLDER}'
_dir='/var/www/${PROD_FOLDER}/umbraco/Logs'
if [[ -d "\${_dir}" ]]; then
	rm -rf "\${_dir}"
	echo "Deleted \${_dir}"
else
	echo "Nothing to delete: \${_dir} not found"
fi
systemctl start '${PROD_FOLDER}'
systemctl status '${PROD_FOLDER}' --no-pager
REMOTE
