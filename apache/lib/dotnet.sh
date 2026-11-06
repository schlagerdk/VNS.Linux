#!/bin/bash

# Keep SDKs and ASP.NET Core runtimes in the directory used by the site services.
ensure_dotnet_channel() (
	set -euo pipefail
	local _channel=$1 _dir=/usr/lib/dotnet _installer=""
	trap '[[ -z ${_installer} ]] || rm -f "${_installer}"' EXIT
	local _sdks="" _runtimes=""
	if [[ -x ${_dir}/dotnet ]]; then
		_sdks=$("${_dir}/dotnet" --list-sdks)
		_runtimes=$("${_dir}/dotnet" --list-runtimes)
	fi
	if grep -qE "^${_channel//./\\.}\.[0-9]+ \[" <<<"${_sdks}" &&
		grep -qE "^Microsoft.AspNetCore.App ${_channel//./\\.}\.[0-9]+ \[" <<<"${_runtimes}"; then
		return
	fi
	echo "Install .NET ${_channel} SDK and ASP.NET Core runtime"
	_installer=$(mktemp)
	curl -fsSL https://dot.net/v1/dotnet-install.sh -o "${_installer}"
	sudo mkdir -p "${_dir}"
	set --
	# Adding .NET 8 must not replace an existing, newer dotnet host.
	if [[ ${_channel} == 8.0 && -x ${_dir}/dotnet ]]; then
		set -- --skip-non-versioned-files
	fi
	sudo bash "${_installer}" --channel "${_channel}" --install-dir "${_dir}" "$@"
	sudo bash "${_installer}" --channel "${_channel}" --runtime aspnetcore --install-dir "${_dir}" "$@"
)

configure_project_dotnet() {
	local _project=$1 _framework
	_framework=$(python3 - "${_project}" <<'PY'
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()
frameworks = [
    (node.text or "").strip()
    for node in root.iter()
    if node.tag.rsplit("}", 1)[-1] == "TargetFramework"
]
print(frameworks[0] if len(frameworks) == 1 else "")
PY
	)
	if [[ ${_framework} == net8.0 ]]; then
		ensure_dotnet_channel 8.0
		printf '%s\n' '{"sdk":{"version":"8.0.100","rollForward":"latestFeature","allowPrerelease":false}}' |
			sudo tee "$(dirname "${_project}")/global.json" >/dev/null
	fi
}
