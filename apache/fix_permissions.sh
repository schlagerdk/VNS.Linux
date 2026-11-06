#!/bin/bash
# Usage: fix_permissions.sh <site> [test|prod]
set -euo pipefail
cd "$(dirname "$0")"
source lib/common.sh
source lib/prod.sh
_site=${1:-}
_scope=${2:-test}
validate_site_name "${_site}"
set_site_vars
if [[ ${_scope} == test ]]; then
	sudo env SITE="${_site}" ROOT="${_siteRoot}" python3 lib/site_permissions.py
elif [[ ${_scope} == prod ]]; then
	load_prod_settings
	validate_site_name "${PROD_FOLDER}"
	printf -v _vars 'SITE=%q ROOT=%q' "${PROD_FOLDER}" "${_root}"
	prod_exec "env ${_vars} python3 -" < lib/site_permissions.py
else
	echo "Scope must be test or prod." >&2
	exit 1
fi
