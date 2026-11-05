#!/bin/bash
# Usage: delete_site.sh [site] [y|n (delete certificates)] [all|test|prod]
#   all  = the site and its production site (default)
#   test = only the site itself (a production folder inside the site folder is kept)
#   prod = only the production site, including a remote production target
cd "$(dirname "$0")"
source lib/common.sh
clear

_site=${1:-}
_deleteCertificate=${2:-}
_scope=${3:-all}

ask _site "Enter site name"
validate_site_name "${_site}" || exit 1
[[ ${_scope} =~ ^(all|test|prod)$ ]] || { echo "Scope must be all, test or prod." >&2; exit 1; }
set_site_vars
_binding=${_site}
if [[ -f ${_configPath} ]]; then
	_binding=$(awk '$1 == "ServerName" { print $2; exit }' "${_configPath}")
	_binding=${_binding:-${_site}}
fi

# Settings of the production site, read (never executed) from publish_prod.sh
_prodScript="${_siteRoot}/scripts/publish_prod.sh"
_prodVar() { sed -n "s/^$1=\([^ #	]*\).*/\1/p" "${_prodScript}" 2>/dev/null | head -n 1 | tr -d "'\\\\\""; }
_hasProd=0
_prodLocalPath=""
_prodFolder=""
_prodBinding=""
if [[ -f ${_prodScript} ]]; then
	_hasProd=1
	_prodLocalPath=$(_prodVar PROD_LOCAL_PATH)
	_prodFolder=$(_prodVar PROD_FOLDER)
	_prodBinding=$(_prodVar PROD_BINDING)
fi
if [[ ${_scope} == prod && ${_hasProd} -eq 0 ]]; then
	echo "This site has no production site." >&2
	exit 1
fi

remove_service_and_config() {
	local _name=$1
	if [[ -f /etc/systemd/system/${_name}.service ]]; then
		echo "Stop, disable and delete service ${_name}"
		sudo systemctl stop "${_name}" || true
		sudo systemctl disable "${_name}" || true
		sudo rm -f "/etc/systemd/system/${_name}.service"
	fi
	echo "Delete apache config and logs for ${_name}"
	sudo rm -f "/etc/apache2/sites-available/${_name}.conf" "/etc/apache2/sites-enabled/${_name}.conf"
	sudo rm -f "/var/log/apache2/${_name}-80-error.log" "/var/log/apache2/${_name}-443-error.log" "/var/log/apache2/${_name}-rewrite-error.log"
	sudo rm -rf "/var/lib/vns-roots/${_name:?}"
}

remove_certificate() {
	local _domain=$1 _directory=$2
	[[ ${_deleteCertificate} =~ ^([yY]|[yY][eE][sS])$ ]] || return 0
	echo "Revoke and remove certificate for ${_domain}"
	sudo "${_acme}" --revoke -d "${_domain}" || true
	sudo "${_acme}" --remove -d "${_domain}" || true
	sudo rm -rf "${_directory:?}"
	# acme.sh keeps its files in a folder named after the binding, and --remove does not delete them
	clean_stale_acme "${_domain}"
}

if [[ -z ${_deleteCertificate} ]]; then
	read -r -p "Delete certificates? [y/N]: " _deleteCertificate
fi

if [[ ${_scope} == test && -n ${_prodLocalPath} && ${_prodLocalPath} == "${_siteRoot}"/*/* ]]; then
	echo "The production folder is nested too deep inside the site folder to keep it. Delete all instead." >&2
	exit 1
fi

# ---- production site
if [[ ${_scope} != test && ${_hasProd} -eq 1 ]]; then
	if [[ -n ${_prodLocalPath} ]]; then
		_prodLocalPath=${_prodLocalPath%/}
		[[ ${_prodFolder} =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ && ${_prodFolder} != "${_site}" ]] || { echo "Invalid production site name: ${_prodFolder}" >&2; exit 1; }
		case "${_prodLocalPath}" in
			"${_rootPath}"/?*) ;;
			*) echo "The production folder must be inside ${_rootPath}: ${_prodLocalPath}" >&2; exit 1 ;;
		esac
		[[ ${_prodLocalPath} != *..* ]] || { echo "Invalid production folder: ${_prodLocalPath}" >&2; exit 1; }
		remove_service_and_config "${_prodFolder}"
		echo "Delete production files ${_prodLocalPath}"
		sudo rm -rf "${_prodLocalPath:?}"
		[[ -z ${_prodBinding} ]] || remove_certificate "${_prodBinding}" "/etc/ssl/certs/${_prodBinding}_ecc"
	else
		echo "Delete the remote production site (${_prodFolder})"
		# Use the saved production connection settings. Do not execute publish_prod.sh.
		source lib/prod.sh
		load_prod_settings
		[[ -z ${PROD_LOCAL_PATH} ]] || { echo "Production target settings are inconsistent." >&2; exit 1; }
		validate_site_name "${PROD_FOLDER}" || exit 1
		validate_binding "${PROD_BINDING}" || exit 1
		[[ ${PROD_IP} =~ ^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$ ]] || { echo "Invalid production host." >&2; exit 1; }
		[[ ${PROD_USER} =~ ^[A-Za-z_][A-Za-z0-9_.-]{0,63}$ ]] || { echo "Invalid production SSH user." >&2; exit 1; }
		[[ ${PROD_SSH_PORT} =~ ^[0-9]+$ && ${PROD_SSH_PORT} -ge 1 && ${PROD_SSH_PORT} -le 65535 ]] || { echo "Invalid production SSH port." >&2; exit 1; }
		printf -v _remoteVars 'SITE=%q BINDING=%q DELETE_CERTIFICATE=%q' "${PROD_FOLDER}" "${PROD_BINDING}" "${_deleteCertificate}"
		prod_run "${_remoteVars}" <<'REMOTE'
set -euo pipefail
[[ ${SITE} =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*$ ]] || { echo "Invalid remote site name." >&2; exit 1; }
[[ ${BINDING} =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$ ]] || { echo "Invalid remote binding." >&2; exit 1; }
root="/var/www/${SITE}"
[[ ${root} == "/var/www/${SITE}" && ${root} != /var/www ]] || { echo "Unsafe remote site path." >&2; exit 1; }
if [[ -f /etc/systemd/system/${SITE}.service ]]; then
	systemctl stop "${SITE}.service"
	systemctl disable "${SITE}.service"
fi
rm -f -- "/etc/systemd/system/${SITE}.service"
rm -f -- "/etc/apache2/sites-available/${SITE}.conf" "/etc/apache2/sites-enabled/${SITE}.conf"
rm -f -- "/var/log/apache2/${SITE}-80-error.log" "/var/log/apache2/${SITE}-443-error.log" "/var/log/apache2/${SITE}-rewrite-error.log"
rm -rf -- "/var/lib/vns-roots/${SITE}"
rm -rf -- "${root:?}"
if [[ ${DELETE_CERTIFICATE} =~ ^([yY]|[yY][eE][sS])$ ]]; then
	domain_in_use=0
	for conf in /etc/apache2/sites-enabled/*.conf; do
		[[ -e ${conf} ]] || continue
		if awk -v d="${BINDING}" '($1 == "ServerName" || $1 == "ServerAlias") { for (i = 2; i <= NF; i++) if (tolower($i) == tolower(d)) f = 1 } END { exit !f }' "${conf}"; then
			domain_in_use=1
			break
		fi
	done
	if ((domain_in_use)); then
		echo "Keep certificate for ${BINDING}; another enabled site uses it."
	else
		if [[ -x /etc/acmesh/acme.sh ]]; then
			/etc/acmesh/acme.sh --revoke -d "${BINDING}" || true
			/etc/acmesh/acme.sh --remove -d "${BINDING}" || true
		fi
		rm -rf -- "/etc/ssl/certs/${BINDING}" "/etc/ssl/certs/${BINDING}_ecc"
	fi
fi
systemctl daemon-reload
apache2ctl configtest
systemctl reload apache2
echo "Remote production site ${SITE} removed."
REMOTE
	fi
	if [[ ${_scope} == prod ]]; then
		# Put the site back to a test-only site so create_prod.sh can be used again
		sudo rm -f "${_siteRoot}/scripts/publish_prod.sh" "${_siteRoot}/scripts/sync_prod.sh" "${_siteRoot}/scripts/clear_logs_prod.sh"
		for _name in publish clear_logs; do
			if [[ -f ${_siteRoot}/scripts/${_name}_local.sh && ! -e ${_siteRoot}/scripts/${_name}.sh ]]; then
				sudo mv "${_siteRoot}/scripts/${_name}_local.sh" "${_siteRoot}/scripts/${_name}.sh"
			fi
		done
	fi
fi

# ---- the site itself
if [[ ${_scope} != prod ]]; then
	remove_service_and_config "${_site}"
	if [[ ${_scope} == test && -n ${_prodLocalPath} && ${_prodLocalPath} == "${_siteRoot}"/* ]]; then
		_keep=${_prodLocalPath#"${_siteRoot}"/}
		echo "Delete the site files (production folder ${_keep} and scripts are kept)"
		sudo find "${_siteRoot}" -mindepth 1 -maxdepth 1 ! -name "${_keep}" ! -name scripts -exec rm -rf {} +
	else
		echo "Delete site and files"
		sudo rm -rf "${_rootPath:?}/${_site:?}"
	fi
	remove_certificate "${_binding}" "${_certificateDirectory}"
fi

echo "Reload daemon"
sudo systemctl daemon-reload
echo "Restart apache2"
sudo systemctl restart apache2
echo "All is removed!"
