#!/bin/bash
# Usage: change_binding_prod.sh [site] [new-domain]
# Changes the binding of a site's production environment (settings in <site>/scripts/publish_prod.sh).
# Works for a production site on this server and for a remote one (over SSH): Apache config, certificate,
# UmbracoApplicationUrl and the PROD_BINDING setting are updated. The old certificate is removed if unused.
set -euo pipefail
cd "$(dirname "$0")"
source lib/common.sh
source lib/prod.sh

_site=${1:-}
_newBinding=${2:-}
ask _site "Enter folder name"
validate_site_name "${_site}"
set_site_vars

load_prod_settings

_oldBinding=${PROD_BINDING,,}
validate_binding "${_oldBinding}"
ask _newBinding "Enter new production binding" "${_oldBinding}"
validate_binding "${_newBinding}"
_newBinding=${_newBinding,,}
_remoteConf="/etc/apache2/sites-available/${PROD_FOLDER}.conf"
_oldAliases=$(prod_exec "bash -c 'source /vns/apache/lib/common.sh; server_aliases_from_config ${_remoteConf} ${_oldBinding}'")
echo "Current aliases: ${_oldAliases:-none}"
_aliasInput=""
if (($# >= 3)); then
	_aliasInput=${3}
else
	read -r -p "New aliases, comma-separated (Enter keeps current; - removes all): " _aliasInput
	if [[ -z ${_aliasInput} ]]; then
		_aliasInput=${_oldAliases}
	elif [[ ${_aliasInput} == "-" ]]; then
		_aliasInput=""
	fi
fi
normalize_server_aliases "${_newBinding}" "${_aliasInput}"
_newAliases=${_normalizedAliases}
normalize_server_aliases "${_oldBinding}" "${_oldAliases}"
_oldAliasesForCompare=${_normalizedAliases}
if [[ ${_newBinding} == "${_oldBinding}" && ${_newAliases} == "${_oldAliasesForCompare}" ]]; then
	echo "The binding and aliases are unchanged."
	exit 0
fi

echo "HTTP-only sites remain HTTP-only. For HTTPS sites the certificate will be updated; all names must resolve to ${_target} and be reachable on HTTP/port 80."
_confirm=y
if [[ ${4:-} != --yes ]]; then
	read -r -p "Change production bindings to ${_newBinding} (aliases: ${_newAliases:-none}) on ${_target}? [y/N]: " _confirm
fi
[[ ${_confirm,,} == y || ${_confirm,,} == yes ]] || { echo "Aborted"; exit 1; }

_umbraco=n
[[ -f ${_siteRoot}/scripts/sync_prod.sh ]] && _umbraco=1
_vars="SITE='${PROD_FOLDER}' OLD='${_oldBinding}' NEW='${_newBinding}' ALIASES='${_newAliases}' ROOT='${_root}' ACME_SERVER='${_acmeServer}' MAIL='${PROD_ADMIN_MAIL:-${_adminMail}}' UMBRACO='${_umbraco}'"

prod_run "${_vars}" <<'REMOTE'
set -euo pipefail
source /vns/apache/lib/common.sh
conf=/etc/apache2/sites-available/${SITE}.conf
acme=/etc/acmesh/acme.sh
oldcert=/etc/ssl/certs/${OLD}_ecc
newcert=/etc/ssl/certs/${NEW}_ecc
settings=${ROOT}/appsettings.json

[[ -f ${conf} ]] || { echo "Apache config not found: ${conf}" >&2; exit 1; }
scheme=http
if grep -Eq '^[[:space:]]*SSLEngine[[:space:]]+On([[:space:]]|$)' "${conf}"; then
	scheme=https
	[[ -x ${acme} ]] || { echo "acme.sh not found (${acme}). Run apache/init.sh there first." >&2; exit 1; }
fi
grep -qi "^[[:space:]]*ServerName[[:space:]]\+${OLD}[[:space:]]*$" "${conf}" || { echo "ServerName ${OLD} not found in ${conf}" >&2; exit 1; }

used_by_other() {
	local c
	for c in /etc/apache2/sites-enabled/*.conf; do
		[[ -e ${c} && ! ${c} -ef ${conf} ]] || continue
		awk -v d="$1" '($1 == "ServerName" || $1 == "ServerAlias") { for (i = 2; i <= NF; i++) if (tolower($i) == tolower(d)) f = 1 } END { exit !f }' "${c}" && return 0
	done
	return 1
}
if used_by_other "${NEW}"; then
	echo "Binding ${NEW} is already used by another Apache site" >&2
	exit 1
fi
for alias in ${ALIASES}; do
	if used_by_other "${alias}"; then
		echo "Alias ${alias} is already used by another Apache site" >&2
		exit 1
	fi
done

backup=$(mktemp -d)
trap 'rm -rf "${backup}"' EXIT
cp "${conf}" "${backup}/site.conf"
[[ -f ${settings} ]] && cp "${settings}" "${backup}/appsettings.json"
app_restart_attempted=n
rollback() {
	echo "Restoring the previous Apache config" >&2
	cp "${backup}/site.conf" "${conf}"
	[[ -f ${backup}/appsettings.json ]] && cp "${backup}/appsettings.json" "${settings}"
	apache2ctl configtest && systemctl reload apache2 || true
	if [[ ${app_restart_attempted} == y ]]; then
		systemctl restart "${SITE}" 2>/dev/null || true
	fi
}

# 1. Serve the new name on port 80 (the certificate still points to the old one)
sed -i -E "s/^([[:space:]]*ServerName[[:space:]]+)${OLD//./\\.}[[:space:]]*$/\1${NEW}/I" "${conf}"
replace_server_aliases "${conf}" "${ALIASES}"
apache2ctl configtest || { rollback; exit 1; }
systemctl reload apache2

# 2. Issue the certificate for the new name
if [[ ${scheme} == https ]]; then
	domains=("${NEW}")
	while IFS= read -r alias; do
		[[ -n ${alias} ]] || continue
		alias=${alias,,}
		[[ ${alias} =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ && ${#alias} -le 253 ]] || {
			echo "Invalid ServerAlias '${alias}' in ${conf}; certificate issuance stopped." >&2
			rollback
			exit 1
		}
		found=0
		for domain in "${domains[@]}"; do [[ ${domain} == "${alias}" ]] && found=1; done
		((found)) || domains+=("${alias}")
	done < <(awk '$1 == "ServerAlias" { for (i = 2; i <= NF && $i !~ /^#/; i++) print $i }' "${conf}")
	domain_args=()
	for domain in "${domains[@]}"; do domain_args+=(-d "${domain}"); done
	printf 'Issue certificate for: %s\n' "${domains[*]}"
	if [[ ${NEW} != "${OLD}" ]] && ! used_by_other "${NEW}"; then
		"${acme}" --remove -d "${NEW}" >/dev/null 2>&1 || true
		rm -rf "/etc/ssl/certs/${NEW}" "${newcert}"
	fi
	rc=0
	"${acme}" --issue --apache --force --server "${ACME_SERVER}" "${domain_args[@]}" || rc=$?
	if [[ ${rc} -ne 0 && ${rc} -ne 2 ]]; then
		echo "Certificate issue failed. Check that DNS for ${NEW} points to this server." >&2
		rollback
		exit 1
	fi
	mkdir -p "${newcert}"
	"${acme}" --install-cert -d "${NEW}" --key-file "${newcert}/${NEW}.key" \
		--fullchain-file "${newcert}/fullchain.cer" --reloadcmd "systemctl reload apache2" || { rollback; exit 1; }

	# 3. Point the config to the new certificate
	sed -i "s|${oldcert}/fullchain.cer|${newcert}/fullchain.cer|g; s|${oldcert}/${OLD}.key|${newcert}/${NEW}.key|g" "${conf}"
fi
if [[ -f ${settings} ]] && grep -q "UmbracoApplicationUrl" "${settings}"; then
	sed -i -E "s|(\"UmbracoApplicationUrl\"[[:space:]]*:[[:space:]]*\")[^\"]*|\1${scheme}://${NEW}|" "${settings}"
fi
apache2ctl configtest || { rollback; exit 1; }
systemctl reload apache2
if [[ -f ${backup}/appsettings.json ]] && ! cmp -s "${backup}/appsettings.json" "${settings}"; then
	app_restart_attempted=y
	systemctl restart "${SITE}" || { rollback; exit 1; }
fi

# 4. Remove the old certificate unless another site still uses that name
if [[ ${OLD} != "${NEW}" && ${scheme} == https ]]; then
	old_binding_in_use=n
	for alias in ${ALIASES}; do [[ ${alias} == "${OLD}" ]] && old_binding_in_use=y; done
	if [[ ${old_binding_in_use} == y ]] || used_by_other "${OLD}"; then
		echo "Previous binding ${OLD} is still used; keeping its certificate."
	else
		echo "Revoke and remove certificate for ${OLD}"
		"${acme}" --revoke -d "${OLD}" || true
		"${acme}" --remove -d "${OLD}" || true
		rm -rf "/etc/ssl/certs/${OLD}" "${oldcert}"
	fi
fi
echo "Bindings updated: ${scheme}://${NEW}/ (aliases: ${ALIASES:-none})"
REMOTE

# Keep the settings of this site in sync (quotes and the trailing comment are preserved)
sudo env NEW_BINDING="${_newBinding}" perl -pi -e 's/^(PROD_BINDING=)(["\x27]?)[^"\x27\s]*\2/$1$2$ENV{NEW_BINDING}$2/' "${_prodScript}"
echo "PROD_BINDING in ${_prodScript} is now ${_newBinding}"
