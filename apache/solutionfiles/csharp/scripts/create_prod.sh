#!/bin/bash
# Add a production target to this site, either on a remote server (SSH) or in a local folder.
# Asks for the production settings, then:
#   publish.sh -> publish_local.sh, clear_logs.sh -> clear_logs_local.sh
#   creates publish_prod.sh, clear_logs_prod.sh and sync_prod.sh (Umbraco) with the answers filled in
# Publishes production, then syncs content when available. Production is always a Release build.
# This script deletes itself after the production scripts have been created.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
	exec sudo "$0" "$@"
fi

source "@APACHE_DIR@/lib/common.sh"

# Values baked in when the site was created
_site=@SITE@
_binding=@BINDING@
_projectName=@PROJECT_NAME@
_siteRoot=@SITE_ROOT@
_publishFolder=@PUBLISH_FOLDER@
_adminMail=@ADMIN_MAIL@
_kind=@CSHARP_KIND@

_scripts="${_siteRoot}/scripts"
_self=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")
_tmp=""
cleanup() {
	[[ -z ${_tmp} ]] || rm -rf "${_tmp}"
	if [[ ${VNS_PROD_MODAL:-} == 1 ]]; then
		rm -f -- "${_self}"
	fi
}
trap cleanup EXIT
_templates=("${_solutionFolder}/csharp/prod-scripts")
[[ ${_kind} == umbraco ]] && _templates+=("${_solutionFolder}/csharp/umbraco/prod-scripts")

if [[ ! -f ${_scripts}/publish.sh || -e ${_scripts}/publish_local.sh || -e ${_scripts}/publish_prod.sh ]]; then
	echo "A production target has already been created for this site (or publish.sh is missing)." >&2
	exit 1
fi

_localPath=""
_ip=""
_user=""
_password=""
_sshPort=""
if [[ ${VNS_PROD_MODAL:-} == 1 ]]; then
	_target=${VNS_PROD_TARGET:-}
	_localPath=${VNS_PROD_LOCAL_PATH:-}
	_ip=${VNS_PROD_IP:-}
	_user=${VNS_PROD_USER:-}
	_password=${VNS_PROD_PASSWORD:-}
	_sshPort=${VNS_PROD_SSH_PORT:-22}
	_prodFolder=${VNS_PROD_FOLDER:-}
	_prodBinding=${VNS_PROD_BINDING:-}
else
	_target=${1:-}
	ask _target "Production target (remote/local)" remote
	case "${_target}" in
		remote)
			ask _ip "Production server IP or hostname"
			[[ -n ${_ip} ]] || { echo "IP is required." >&2; exit 1; }
			ask _user "SSH login" root
			read -r -s -p "SSH password (empty = use an SSH key): " _password
			echo
			ask _sshPort "SSH port" 22
			[[ ${_sshPort} =~ ^[0-9]+$ ]] || { echo "SSH port must be a number." >&2; exit 1; }
			_defaultFolder=${_site}
			;;
		local)
			ask _localPath "Local production folder (absolute path)" "${_siteRoot}/_prod"
			[[ ${_localPath} == /* ]] || { echo "The path must be absolute." >&2; exit 1; }
			_defaultFolder=prod-${_site}
			;;
		*)
			echo "Target must be remote or local." >&2
			exit 1
			;;
	esac
	ask _prodFolder "Production site name (folder /var/www/<name>, service and Apache config)" "${_defaultFolder}"
	ask _prodBinding "Production binding (domain)" "${_prodFolder}"
fi

[[ ${_target} == remote || ${_target} == local ]] || { echo "Target must be remote or local." >&2; exit 1; }
validate_site_name "${_prodFolder}"
validate_binding "${_prodBinding}"
if [[ ${_target} == remote ]]; then
	[[ -n ${_ip} && -n ${_user} && ${_sshPort} =~ ^[0-9]+$ && ${_sshPort} -ge 1 && ${_sshPort} -le 65535 ]] || {
		echo "Remote target requires a host, SSH user and valid SSH port." >&2
		exit 1
	}
else
	[[ ${_localPath} == /* ]] || { echo "The local production folder must be an absolute path." >&2; exit 1; }
	if [[ ${_prodFolder} == "${_site}" || ${_prodBinding} == "${_binding}" ]]; then
		echo "A local production site needs a different name and binding than this site." >&2
		exit 1
	fi
fi
echo
echo "Target:   ${_target}${_ip:+ (${_user}@${_ip}:${_sshPort})}${_localPath:+ (${_localPath})}"
echo "Site:     ${_prodFolder}"
echo "Binding:  ${_prodBinding}"
if [[ ${VNS_PROD_MODAL:-} != 1 ]]; then
	read -r -p "Create the production scripts? [Y/n]: " _confirm
	[[ ${_confirm:-y} =~ ^[Yy] ]] || { echo "Cancelled."; exit 1; }
fi

_q() { printf '%q' "$1"; }
export R_PROD_LOCAL_PATH="$(_q "${_localPath}")" R_PROD_IP="$(_q "${_ip}")" R_PROD_USER="$(_q "${_user}")" \
	R_PROD_PASSWORD="$(_q "${_password}")" R_PROD_SSH_PORT="$(_q "${_sshPort}")" R_PROD_FOLDER="$(_q "${_prodFolder}")" \
	R_PROD_BINDING="$(_q "${_prodBinding}")" R_ADMIN_MAIL="${_adminMail}" \
	R_PROJECT_NAME="${_projectName}" R_PUBLISH_FOLDER="${_publishFolder}" R_SITE="${_site}"
unset _password VNS_PROD_PASSWORD

# Render everything first, so a missing template fails before anything is renamed
_tmp=$(mktemp -d)
for _dir in "${_templates[@]}"; do
	for _file in "${_dir}"/*.sh; do
		[[ -f ${_file} ]] || continue
		perl -pe 's/\@([A-Z_]+)\@/exists $ENV{"R_$1"} ? $ENV{"R_$1"} : "\@$1\@"/ge' "${_file}" >"${_tmp}/$(basename "${_file}")"
	done
done
[[ -f ${_tmp}/publish_prod.sh ]] || { echo "Template publish_prod.sh not found." >&2; exit 1; }

mv "${_scripts}/publish.sh" "${_scripts}/publish_local.sh"
[[ ! -f ${_scripts}/clear_logs.sh ]] || mv "${_scripts}/clear_logs.sh" "${_scripts}/clear_logs_local.sh"
for _file in "${_tmp}"/*.sh; do
	install -m 700 "${_file}" "${_scripts}/$(basename "${_file}")"
done

echo "Created in ${_scripts}:"
ls -1 "${_scripts}" | grep -E '_(local|prod)\.sh$' | sed 's/^/  /'
rm -f -- "${_self}"

echo "Publish to production"
bash "${_scripts}/publish_prod.sh"
if [[ -f ${_scripts}/sync_prod.sh ]]; then
	echo "Sync content to production"
	bash "${_scripts}/sync_prod.sh"
fi
echo "Production setup complete."
