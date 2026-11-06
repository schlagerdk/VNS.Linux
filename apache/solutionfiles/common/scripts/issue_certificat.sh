#!/bin/bash
set -euo pipefail
clear

source "@APACHE_DIR@/lib/common.sh"
_site=@SITE@
set_site_vars
_binding=$(awk '$1 == "ServerName" { print $2; exit }' "${_configPath}")
[[ -n ${_binding} ]] || { echo "Could not determine ServerName in ${_configPath}" >&2; exit 1; }
set_certificate_domains "${_configPath}" "${_binding}"
require_acme
echo "Issue certificate for ${_certificateDomainNames}"
"${_acme}" --issue --apache --server "${_acmeServer}" --force "${_certificateDomainArgs[@]}"

echo  Reload apache
systemctl reload apache2
