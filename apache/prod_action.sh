#!/bin/bash
# Usage: prod_action.sh <site> <issue_certificate|change_env> [environment]
# Actions on the production environment of a site (settings in <site>/scripts/publish_prod.sh),
# on this server or over SSH on the remote one.
set -euo pipefail
cd "$(dirname "$0")"
source lib/common.sh
source lib/prod.sh

_site=${1:-}
_action=${2:-}
_env=${3:-}
ask _site "Enter folder name"
validate_site_name "${_site}"
set_site_vars
load_prod_settings

case "${_action}" in
	issue_certificate)
		read -r -p "Issue a new certificate for ${PROD_BINDING} on ${_target}? [y/N]: " _confirm
		[[ ${_confirm,,} == y || ${_confirm,,} == yes ]] || { echo "Aborted"; exit 1; }
		prod_run "SITE='${PROD_FOLDER}' BINDING='${PROD_BINDING,,}' ACME_SERVER='${_acmeServer}'" <<'REMOTE'
set -euo pipefail
acme=/etc/acmesh/acme.sh
certdir=/etc/ssl/certs/${BINDING}_ecc
[[ -x ${acme} ]] || { echo "acme.sh not found (${acme}). Run apache/init.sh there first." >&2; exit 1; }
[[ -f /etc/apache2/sites-available/${SITE}.conf ]] || { echo "Apache config for ${SITE} not found. Publish to production first." >&2; exit 1; }
echo "Issue certificate for ${BINDING}"
rc=0
"${acme}" --issue --apache --force --server "${ACME_SERVER}" -d "${BINDING}" || rc=$?
if [[ ${rc} -ne 0 && ${rc} -ne 2 ]]; then
	echo "Certificate issue failed. Check that DNS for ${BINDING} points to this server." >&2
	exit 1
fi
mkdir -p "${certdir}"
"${acme}" --install-cert -d "${BINDING}" --key-file "${certdir}/${BINDING}.key" \
	--fullchain-file "${certdir}/fullchain.cer" --reloadcmd "systemctl reload apache2"
echo "Certificate installed in ${certdir}"
REMOTE
		;;
	change_env)
		echo "Production environment of ${PROD_FOLDER} (${_target})"
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
		prod_run "SITE='${PROD_FOLDER}' ENVIRONMENT='${_env}'" <<'REMOTE'
set -euo pipefail
service=/etc/systemd/system/${SITE}.service
[[ -f ${service} ]] || { echo "Service file not found: ${service}. Publish to production first." >&2; exit 1; }
echo "Current:"
grep "_ENVIRONMENT" "${service}" || true
sed -i -E "s/^(Environment=(ASPNETCORE|DOTNET)_ENVIRONMENT=).*/\1${ENVIRONMENT}/" "${service}"
echo "Now:"
grep "_ENVIRONMENT" "${service}"
systemctl daemon-reload
systemctl restart "${SITE}"
systemctl status "${SITE}" --no-pager
REMOTE
		# publish_prod.sh only reads PROD_ENVIRONMENT when it creates a new service; keep it in sync
		sudo env NEW_ENV="${_env}" perl -pi -e 's/^(PROD_ENVIRONMENT=)(["\x27]?)[^"\x27\s]*\2/$1$2$ENV{NEW_ENV}$2/' "${_prodScript}"
		;;
	*)
		echo "Unknown action: ${_action}" >&2
		exit 1
		;;
esac
