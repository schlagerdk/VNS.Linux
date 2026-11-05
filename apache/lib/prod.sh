#!/bin/bash
# Helpers for the production environment of a site (settings in <site>/scripts/publish_prod.sh).
# Needs _siteRoot (set_site_vars). Source after lib/common.sh.

# load_prod_settings: read the PROD_* assignments (the rest of publish_prod.sh is not executed)
# and set _target (description) and _root (site folder on the production target)
load_prod_settings() {
	local _script="${_siteRoot}/scripts/publish_prod.sh"
	if [[ ! -f ${_script} ]]; then
		echo "This site has no production site (missing ${_script})." >&2
		exit 1
	fi
	PROD_LOCAL_PATH="" PROD_IP="" PROD_USER="" PROD_PASSWORD="" PROD_SSH_PORT=22
	PROD_FOLDER="" PROD_BINDING="" PROD_PORT_HTTP="" PROD_ADMIN_MAIL=""
	# shellcheck disable=SC1090
	source <(grep -E '^PROD_[A-Z_]+=' "${_script}")
	if [[ -z ${PROD_FOLDER} || -z ${PROD_BINDING} ]]; then
		echo "PROD_FOLDER and PROD_BINDING must be set in ${_script}" >&2
		exit 1
	fi
	_prodScript=${_script}
	if [[ -n ${PROD_LOCAL_PATH} ]]; then
		_target="this server"
		_root=${PROD_LOCAL_PATH%/}
	else
		_target="${PROD_USER}@${PROD_IP}:${PROD_SSH_PORT}"
		_root=/var/www/${PROD_FOLDER}
	fi
}

# prod_run "<VAR='x' ...>"  runs the script on stdin as root on the production target
prod_run() {
	local _vars=$1
	if [[ -n ${PROD_LOCAL_PATH} ]]; then
		sudo bash -c "env ${_vars} bash -s"
		return
	fi
	local _ssh=(ssh -p "${PROD_SSH_PORT}" -o StrictHostKeyChecking=accept-new)
	if [[ -n ${PROD_PASSWORD} ]]; then
		command -v sshpass >/dev/null || { echo "sshpass is required for password login: sudo apt install sshpass" >&2; exit 1; }
		export SSHPASS=${PROD_PASSWORD}
		_ssh=(sshpass -e "${_ssh[@]}")
	fi
	local _sudo=""
	[[ ${PROD_USER} == root ]] || _sudo="sudo -n"
	"${_ssh[@]}" "${PROD_USER}@${PROD_IP}" "${_sudo} env ${_vars} bash -s"
}
