#!/bin/bash
# Shared helpers for create_site.sh / delete_site.sh
# Templates live in solutionfiles/ and use @PLACEHOLDERS@ that are replaced by render().

_apacheDir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
_solutionFolder="${_apacheDir}/solutionfiles"
_portNumberFile="${_apacheDir}/_portnumber.txt"
_rootPath=/var/www
_publishFolder=_Publish
_acme=/etc/acmesh/acme.sh
_acmeServer=letsencrypt
_adminMail=mhs@vns.dk

# Ask for a value unless it is already set. Usage: ask VARNAME "Prompt" [default]
ask() {
	local _name=$1 _prompt=$2 _default=${3:-}
	if [[ -z ${!_name:-} ]]; then
		if [[ -n ${_default} ]]; then
			read -r -p "${_prompt} [${_default}]: " "${_name}"
		else
			read -r -p "${_prompt}: " "${_name}"
		fi
		printf -v "${_name}" '%s' "${!_name:-${_default}}"
	fi
}

validate_site_name() {
	local _name=$1
	if [[ -z ${_name} ]]; then
		echo "No site given." >&2
		return 1
	fi
	if [[ ! ${_name} =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*$ ]]; then
		echo "Invalid site name. Use a DNS name containing only letters, numbers, dots and hyphens." >&2
		return 1
	fi
}

validate_binding() {
	local _binding=$1 _label
	if [[ ! ${_binding} =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$ ]]; then
		echo "Invalid binding. Enter a fully qualified DNS name, without scheme or path." >&2
		return 1
	fi
	if ((${#_binding} > 253)); then
		echo "Binding is too long." >&2
		return 1
	fi
	IFS=. read -r -a _labels <<<"${_binding}"
	for _label in "${_labels[@]}"; do
		if ((${#_label} > 63)); then
			echo "DNS labels cannot exceed 63 characters." >&2
			return 1
		fi
	done
}

validate_project_name() {
	if [[ ! $1 =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
		echo "Invalid C# project name. Use letters, numbers, dots, hyphens or underscores." >&2
		return 1
	fi
}

validate_publish_folder() {
	if [[ ! $1 =~ ^[A-Za-z0-9_][A-Za-z0-9._-]*$ ]]; then
		echo "Invalid publish folder. Use a single folder name with letters, numbers, dots, hyphens or underscores." >&2
		return 1
	fi
	case "${1,,}" in
		bin | obj | wwwroot | views | usync | scripts | properties | umbraco | app_plugins | .)
			echo "Publish folder name '$1' is reserved." >&2
			return 1
			;;
	esac
}

# Exclude a publish folder from the project's build globs. Usage: exclude_from_build <csproj> <folder>. Safe to repeat.
exclude_from_build() {
	local _csproj=$1 _folder=$2
	if grep -qF "Compile Remove=\"${_folder}/**\"" "${_csproj}"; then
		return 0
	fi
	sudo perl -0pi -e 's|</Project>\s*$|  <!-- Exclude publish folder from build -->\n  <ItemGroup>\n    <Content Remove="'"${_folder}"'/**" />\n    <Compile Remove="'"${_folder}"'/**" />\n    <None Remove="'"${_folder}"'/**" />\n  </ItemGroup>\n</Project>\n|' "${_csproj}"
}

# Placeholders available in every template. Call set_site_vars after _site, _binding and ports are known.
set_site_vars() {
	_siteRoot="${_rootPath}/${_site}"
	_certificateDirectory="/etc/ssl/certs/${_site}_ecc"
	_certificateFile="${_certificateDirectory}/fullchain.cer"
	_keyFile="${_certificateDirectory}/${_site}.key"
	_configPath="/etc/apache2/sites-available/${_site}.conf"
	_servicePath="/etc/systemd/system/${_site}.service"
}

# Print a template with placeholders replaced
render() {
	sed \
		-e "s|@SITE@|${_site}|g" \
		-e "s|@BINDING@|${_binding:-${_site}}|g" \
		-e "s|@PROJECT_NAME@|${_projectName:-${_site}}|g" \
		-e "s|@ROOT_PATH@|${_rootPath}|g" \
		-e "s|@SITE_ROOT@|${_siteRoot}|g" \
		-e "s|@PUBLISH_FOLDER@|${_publishFolder}|g" \
		-e "s|@DOC_ROOT@|${_docRoot:-}|g" \
		-e "s|@PORT_HTTP@|${_portHttp:-}|g" \
		-e "s|@PORT_HTTPS@|${_portHttps:-}|g" \
		-e "s|@DOTNET_VERSION@|${_dotNetVersion:-}|g" \
		-e "s|@UMBRACO_VERSION@|${_umbracoVersion:-}|g" \
		-e "s|@CERT_FILE@|${_certificateFile}|g" \
		-e "s|@KEY_FILE@|${_keyFile}|g" \
		-e "s|@ADMIN_MAIL@|${_adminMail}|g" \
		-e "s|@BUILD_CONFIG@|${_buildConfig:-Debug}|g" \
		-e "s|@ENVIRONMENT@|${_environment:-Test}|g" \
		-e "s|@APACHE_DIR@|${_apacheDir}|g" \
		-e "s|@CSHARP_KIND@|${_csharpKind:-}|g" \
		"$1"
}

# render_to <template> <destination>  (destination written with sudo)
render_to() {
	render "$1" | sudo tee "$2" >/dev/null
}

# Copy script templates into "<siteRoot>/scripts" (later calls overwrite earlier ones)
install_scripts() {
	local _src=$1 _dest="${_siteRoot}/scripts" _file
	[[ -d ${_src} ]] || return 0
	sudo mkdir -p "${_dest}"
	for _file in "${_src}"/*; do
		[[ -f ${_file} ]] || continue
		render_to "${_file}" "${_dest}/$(basename "${_file}")"
		sudo chmod +x "${_dest}/$(basename "${_file}")"
	done
}

# Reserve the next port number and set _portHttp/_portHttps
allocate_ports() {
	local _old _new
	_old=$(cat "${_portNumberFile}")
	_new=$((_old + 1))
	echo "${_new}" >"${_portNumberFile}"
	_portHttp=$((_new + 4000))
	_portHttps=$((_new + 6000))
	echo "Ports: http=${_portHttp} https=${_portHttps}"
}

require_acme() {
	if [[ ! -x ${_acme} ]]; then
		echo "acme.sh not found or not executable: ${_acme}" >&2
		exit 1
	fi
}

# Remove leftover acme.sh state (e.g. a domain key from an earlier site) so a fresh certificate can be issued.
# Skipped when another enabled site still uses the domain.
clean_stale_acme() {
	local _domain=$1 _conf
	for _conf in /etc/apache2/sites-enabled/*.conf; do
		[[ -e ${_conf} && ${_conf} != "/etc/apache2/sites-enabled/${_site}.conf" ]] || continue
		if awk -v d="${_domain}" '($1 == "ServerName" || $1 == "ServerAlias") { for (i = 2; i <= NF; i++) if (tolower($i) == tolower(d)) f = 1 } END { exit !f }' "${_conf}"; then
			return 0
		fi
	done
	if [[ -d /etc/ssl/certs/${_domain} || -d /etc/ssl/certs/${_domain}_ecc ]]; then
		echo "Remove leftover certificate data for ${_domain}"
		sudo "${_acme}" --remove -d "${_domain}" >/dev/null 2>&1 || true
		sudo rm -rf "/etc/ssl/certs/${_domain}" "/etc/ssl/certs/${_domain}_ecc"
	fi
}

# Temporary HTTP-only vhost, then issue and install the certificate. Requires _docRoot to exist.
issue_certificate() {
	require_acme
	echo "Create temporary HTTP-only config for certificate validation"
	sudo mkdir -p "${_docRoot}"
	render_to "${_solutionFolder}/common/apache-temp.conf.tpl" "${_configPath}"

	sudo a2enmod ssl
	sudo apache2ctl configtest
	[[ -e "/etc/apache2/sites-enabled/${_site}.conf" ]] || sudo a2ensite "${_site}.conf"
	sudo systemctl reload apache2

	clean_stale_acme "${_binding}"
	echo "Issue certificate"
	# acme.sh returns 2 when a still-valid certificate is skipped
	sudo "${_acme}" --issue --apache --force --server "${_acmeServer}" -d "${_binding}" || {
		local _status=$?
		[[ ${_status} -eq 2 ]] || return "${_status}"
	}

	echo "Install certificate for Apache"
	sudo mkdir -p "${_certificateDirectory}"
	sudo "${_acme}" --install-cert -d "${_binding}" \
		--key-file "${_keyFile}" \
		--fullchain-file "${_certificateFile}" \
		--reloadcmd "systemctl reload apache2"
}

install_apache_config() {
	echo "Create Apache config"
	render_to "$1" "${_configPath}"
	sudo apache2ctl configtest
	[[ -e "/etc/apache2/sites-enabled/${_site}.conf" ]] || sudo a2ensite "${_site}.conf"
	sudo systemctl reload apache2
}

install_service() {
	echo "Create service"
	render_to "$1" "${_servicePath}"
	sudo systemctl daemon-reload
	sudo systemctl enable "${_site}"
	sudo systemctl restart "${_site}"
	sudo systemctl status "${_site}" --no-pager || true
}
