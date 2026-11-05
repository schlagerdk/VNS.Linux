#!/bin/bash
# Usage: change_binding.sh [folder] [new-domain]
set -euo pipefail
cd "$(dirname "$0")"
source lib/common.sh

_site=${1:-}
_newBinding=${2:-}
ask _site "Enter folder name"
validate_site_name "${_site}"
set_site_vars

if [[ ! -f ${_configPath} ]]; then
	echo "Apache config not found: ${_configPath}" >&2
	exit 1
fi
if [[ ! -e /etc/apache2/sites-enabled/${_site}.conf ]]; then
	echo "Apache site is not enabled: ${_site}" >&2
	exit 1
fi

_oldBinding=$(awk '$1 == "ServerName" { print $2; exit }' "${_configPath}")
if [[ -z ${_oldBinding} ]]; then
	echo "Could not determine the current ServerName in ${_configPath}" >&2
	exit 1
fi
_oldBinding=${_oldBinding,,}

ask _newBinding "Enter new site URL binding" "${_oldBinding}"
validate_binding "${_newBinding}"
_newBinding=${_newBinding,,}
_binding=${_newBinding}

if [[ ${_newBinding} == "${_oldBinding}" ]]; then
	echo "The binding is unchanged."
	exit 0
fi

for _enabledConfig in /etc/apache2/sites-enabled/*.conf; do
	[[ -e ${_enabledConfig} ]] || continue
	[[ ${_enabledConfig} -ef ${_configPath} ]] && continue
	if awk -v binding="${_newBinding}" '($1 == "ServerName" || $1 == "ServerAlias") { for (i = 2; i <= NF; i++) if (tolower($i) == tolower(binding)) found = 1 } END { exit !found }' "${_enabledConfig}"; then
		echo "Binding ${_newBinding} is already used by ${_enabledConfig}" >&2
		exit 1
	fi
done

echo "The new domain must resolve to this server and be reachable on HTTP/port 80."
read -r -p "Change ${_oldBinding} to ${_newBinding}, issue a new certificate, and revoke the old one if unused? [y/N]: " _confirm
[[ ${_confirm,,} == y || ${_confirm,,} == yes ]] || { echo "Aborted"; exit 1; }

require_acme
if [[ ! -f ${_certificateFile} || ! -f ${_keyFile} ]]; then
	echo "Current certificate files are missing; refusing to replace the binding." >&2
	exit 1
fi
_appSettings="${_siteRoot}/${_publishFolder}/appsettings.json"

_backupDir=$(mktemp -d)
trap 'rm -rf "${_backupDir}"' EXIT
sudo cp "${_configPath}" "${_backupDir}/site.conf"
sudo cp "${_certificateFile}" "${_backupDir}/fullchain.cer"
sudo cp "${_keyFile}" "${_backupDir}/site.key"
if [[ -f ${_appSettings} ]]; then
	sudo cp "${_appSettings}" "${_backupDir}/appsettings.json"
fi

_rollback() {
	echo "Restoring previous Apache config and certificate" >&2
	sudo cp "${_backupDir}/site.conf" "${_configPath}"
	sudo cp "${_backupDir}/fullchain.cer" "${_certificateFile}"
	sudo cp "${_backupDir}/site.key" "${_keyFile}"
	if [[ -f ${_backupDir}/appsettings.json ]]; then
		sudo cp "${_backupDir}/appsettings.json" "${_appSettings}"
	fi
	sudo apache2ctl configtest
	sudo systemctl reload apache2
	if [[ -f ${_servicePath} ]]; then
		sudo systemctl restart "${_site}" || true
	fi
}

if ! sudo env OLD_BINDING="${_oldBinding}" NEW_BINDING="${_newBinding}" perl -pi -e 'if (/^\s*(?:ServerName|Redirect)\b/) { $count += s/\Q$ENV{OLD_BINDING}\E/$ENV{NEW_BINDING}/gi } END { die "No binding directive updated\n" unless $count }' "${_configPath}"; then
	_rollback
	exit 1
fi
if [[ -f ${_appSettings} ]] &&
	! sudo env NEW_BINDING="${_newBinding}" perl -0pi -e 's/("UmbracoApplicationUrl"\s*:\s*")[^"]*(")/${1}https:\/\/$ENV{NEW_BINDING}$2/ or die "UmbracoApplicationUrl not found\n"' "${_appSettings}"; then
	_rollback
	exit 1
fi

if ! sudo cp "${_configPath}" "${_backupDir}/new-site.conf"; then
	_rollback
	exit 1
fi
_docRoot=${_siteRoot}
if ! render_to "${_solutionFolder}/common/apache-temp.conf.tpl" "${_configPath}"; then
	_rollback
	exit 1
fi
if ! sudo apache2ctl configtest || ! sudo systemctl reload apache2; then
	_rollback
	exit 1
fi

echo "Issue certificate for ${_newBinding}"
if sudo "${_acme}" --issue --apache --server "${_acmeServer}" -d "${_newBinding}"; then
	:
else
	_status=$?
	if [[ ${_status} -ne 2 ]]; then
		_rollback
		exit "${_status}"
	fi
fi

if ! sudo "${_acme}" --install-cert -d "${_newBinding}" \
	--key-file "${_keyFile}" \
	--fullchain-file "${_certificateFile}" \
	--reloadcmd "systemctl reload apache2"; then
	_rollback
	exit 1
fi

if ! sudo cp "${_backupDir}/new-site.conf" "${_configPath}"; then
	_rollback
	exit 1
fi
if ! sudo apache2ctl configtest || ! sudo systemctl reload apache2; then
	_rollback
	exit 1
fi
if [[ -f ${_appSettings} ]] && ! sudo systemctl restart "${_site}"; then
	_rollback
	exit 1
fi

_oldBindingInUse=0
for _enabledConfig in /etc/apache2/sites-enabled/*.conf; do
	[[ -e ${_enabledConfig} ]] || continue
	[[ ${_enabledConfig} -ef ${_configPath} ]] && continue
	if awk -v binding="${_oldBinding}" '($1 == "ServerName" || $1 == "ServerAlias") { for (i = 2; i <= NF; i++) if (tolower($i) == tolower(binding)) found = 1 } END { exit !found }' "${_enabledConfig}"; then
		_oldBindingInUse=1
		break
	fi
done

if ((_oldBindingInUse)); then
	echo "Previous binding ${_oldBinding} is still used by another site; keeping its certificate."
else
	echo "Revoke and remove certificate for ${_oldBinding}"
	sudo "${_acme}" --revoke -d "${_oldBinding}"
	sudo "${_acme}" --remove -d "${_oldBinding}"
fi

echo "Binding updated: https://${_newBinding}/ (folder: ${_site})"
