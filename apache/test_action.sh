#!/bin/bash
# Usage: test_action.sh <site> <issue_certificate|change_env> [environment]
# Actions on the test (main) environment of a site, using the Apache config and service of the site.
set -euo pipefail
cd "$(dirname "$0")"
source lib/common.sh

_site=${1:-}
_action=${2:-}
_env=${3:-}
ask _site "Enter folder name"
validate_site_name "${_site}"
set_site_vars

case "${_action}" in
	issue_certificate)
		[[ -f ${_configPath} ]] || { echo "Apache config not found: ${_configPath}" >&2; exit 1; }
		_binding=$(awk '$1 == "ServerName" { print $2; exit }' "${_configPath}")
		[[ -n ${_binding} ]] || { echo "Could not determine ServerName in ${_configPath}" >&2; exit 1; }
		_binding=${_binding,,}
		read -r -p "Issue a new certificate for ${_binding}? [y/N]: " _confirm
		[[ ${_confirm,,} == y || ${_confirm,,} == yes ]] || { echo "Aborted"; exit 1; }
		require_acme
		echo "Issue certificate for ${_binding}"
		_status=0
		sudo "${_acme}" --issue --apache --force --server "${_acmeServer}" -d "${_binding}" || _status=$?
		if [[ ${_status} -ne 0 && ${_status} -ne 2 ]]; then
			echo "Certificate issue failed. Check that DNS for ${_binding} points to this server." >&2
			exit 1
		fi
		sudo mkdir -p "${_certificateDirectory}"
		sudo "${_acme}" --install-cert -d "${_binding}" --key-file "${_keyFile}" \
			--fullchain-file "${_certificateFile}" --reloadcmd "systemctl reload apache2"
		echo "Certificate installed in ${_certificateDirectory}"
		;;
	change_env)
		[[ -f ${_servicePath} ]] || { echo "Service file not found: ${_servicePath}" >&2; exit 1; }
		echo "Current:"
		grep "_ENVIRONMENT" "${_servicePath}" || true
		if [[ -z ${_env} ]]; then
			echo "1) Development"
			echo "2) Test"
			echo "3) Staging"
			echo "4) Production"
			read -r -p "Choose environment: " _env
		fi
		case "${_env,,}" in
			1 | d | dev | development) _env=Development ;;
			2 | t | test) _env=Test ;;
			3 | s | stage | staging) _env=Staging ;;
			4 | p | prod | production) _env=Production ;;
			*) echo "Unknown environment: ${_env}" >&2; exit 1 ;;
		esac
		sudo sed -i -E "s/^(Environment=(ASPNETCORE|DOTNET)_ENVIRONMENT=).*/\1${_env}/" "${_servicePath}"
		echo "Now:"
		grep "_ENVIRONMENT" "${_servicePath}"
		sudo systemctl daemon-reload
		sudo systemctl restart "${_site}"
		sudo systemctl status "${_site}" --no-pager
		;;
	*)
		echo "Unknown action: ${_action}" >&2
		exit 1
		;;
esac
