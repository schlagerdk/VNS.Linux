#!/bin/bash
# Shared helpers for Microsoft's apt repositories.

# Microsoft signs newer repositories (e.g. Ubuntu 26.04) with a different key than older ones,
# so both keys are added to the same keyring.
install_microsoft_keys() {
	local _sudo=$1 _tmp _key
	_tmp=$(mktemp -d)
	for _key in microsoft.asc microsoft-2025.asc; do
		curl -fsSL "https://packages.microsoft.com/keys/${_key}" | gpg --dearmor >"${_tmp}/${_key}.gpg"
	done
	cat "${_tmp}"/*.gpg | ${_sudo} tee /usr/share/keyrings/microsoft-prod.gpg >/dev/null
	rm -rf "${_tmp}"
}
