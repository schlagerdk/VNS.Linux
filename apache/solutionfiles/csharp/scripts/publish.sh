#!/bin/bash
# Publish this site into its local publish folder and restart the service.
# Usage: publish.sh [Debug|Release]   (default: @BUILD_CONFIG@)
# After create_prod.sh has run this script is called publish_local.sh.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
if [[ ${VNS_PUBLISH_TRACKED:-} != test ]]; then
	exec python3 "@APACHE_DIR@/lib/publish_status.py" run "$(cd "$(dirname "$0")/.." && pwd)" test /bin/bash "$(realpath "$0")" "$@"
fi
cd "$(dirname "$0")"
cd ..

_site=@SITE@
_projectName=@PROJECT_NAME@
_publishFolder=@PUBLISH_FOLDER@
_configuration=${1:-@BUILD_CONFIG@}

case "${_configuration}" in
	Debug | Release) ;;
	*) echo "Usage: $0 [Debug|Release]" >&2; exit 1 ;;
esac

_stopped=0
start_service() {
	if ((_stopped)); then
		_stopped=0
		systemctl start "${_site}"
	fi
}
# Always bring the site back up, also if the publish fails
trap start_service EXIT

echo "Stop ${_site}"
systemctl stop "${_site}"
_stopped=1

echo "Publish (${_configuration})"
dotnet publish "${_projectName}.csproj" -c "${_configuration}" -p:PublishDir="${_publishFolder}" --self-contained false --nologo

echo "Delete bin"
rm -rf bin

if grep -qs "Umbraco.Cms" ./*.csproj; then
	echo "Delete Umbraco schema files and App_Plugins"
	rm -f appsettings-schema.json appsettings-schema.usync.json appsettings-schema.Umbraco.Cms.json umbraco-package-schema.json
	rm -f "${_publishFolder}"/{appsettings-schema.json,appsettings-schema.usync.json,appsettings-schema.Umbraco.Cms.json,umbraco-package-schema.json}
	rm -rf App_Plugins
fi

chown -R www-data:www-data "${_publishFolder}"
start_service

sleep 2
systemctl status "${_site}" --no-pager || true
if systemctl is-active --quiet "${_site}"; then
	echo "OK: ${_site} is running"
else
	echo "FAILED: ${_site} is not running. Last log lines:"
	journalctl -u "${_site}" -n 20 --no-pager
	exit 1
fi
