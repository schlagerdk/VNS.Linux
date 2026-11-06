#!/bin/bash
# Shared helpers for create_site.sh / delete_site.sh
# Templates live in solutionfiles/ and use @PLACEHOLDERS@ that are replaced by render().

_apacheDir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Installed create_prod.sh scripts render current templates using exported R_* values.
export R_APACHE_DIR="${_apacheDir}"
_solutionFolder="${_apacheDir}/solutionfiles"
_portReservationDir=/run/lock/vns-apache-port-reservations
_portAllocationLock=/run/lock/vns-apache-port-allocation.lock
_portReservationFile=""
_rootPath=/var/www
_publishFolder=_publish
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

ensure_production_tls_config() {
	local _config=$1 _service=$2 _site=$3 _binding=$4 _root=$5 _certdir=$6 _mail=$7 _umbraco=$8
	local _port _alias _candidate _found _normalizedBinding _aliases="" _aliasLine="" _extra=""

	[[ -f ${_config} ]] || { echo "Apache config not found: ${_config}" >&2; return 1; }
	if grep -Eq '^[[:space:]]*SSLEngine[[:space:]]+On([[:space:]]|$)' "${_config}"; then
		return 0
	fi
	[[ -f ${_service} ]] || { echo "Production service not found: ${_service}" >&2; return 1; }
	_port=$(grep '^ExecStart=' "${_service}" | grep -oE '(localhost|127\.0\.0\.1|\*):[0-9]+' | head -n 1 | cut -d: -f2 || true)
	[[ ${_port} =~ ^[0-9]+$ ]] || { echo "Could not determine the production port from ${_service}." >&2; return 1; }
	_normalizedBinding=$(printf '%s' "${_binding}" | tr '[:upper:]' '[:lower:]')

	while IFS= read -r _alias; do
		[[ -n ${_alias} ]] || continue
		_alias=$(printf '%s' "${_alias}" | tr '[:upper:]' '[:lower:]')
		validate_binding "${_alias}" || {
			echo "Invalid ServerAlias '${_alias}' in ${_config}; refusing to build the HTTPS config." >&2
			return 1
		}
		_found=0
		[[ ${_alias} == "${_normalizedBinding}" ]] && _found=1
		for _candidate in ${_aliases}; do [[ ${_candidate} == "${_alias}" ]] && _found=1; done
		if ((!_found)); then
			_aliases="${_aliases:+${_aliases} }${_alias}"
		fi
	done < <(awk '$1 == "ServerAlias" { for (i = 2; i <= NF && $i !~ /^#/; i++) print $i }' "${_config}")
	if [[ -n ${_aliases} ]]; then
		_aliasLine="  ServerAlias ${_aliases}"
	fi
	if [[ ${_umbraco} == 1 ]]; then
		_extra="
  ProxyPass /robots.txt http://localhost:${_port}/seo_robots_txt
  ProxyPassReverse /robots.txt http://localhost:${_port}/seo_robots_txt

  ProxyPass /sitemap.xml http://localhost:${_port}/seo_sitemap_xml
  ProxyPassReverse /sitemap.xml http://localhost:${_port}/seo_sitemap_xml
"
	fi

	cat >"${_config}" <<EOF
<VirtualHost *:80>
  ServerAdmin ${_mail}
  ServerName ${_binding}
${_aliasLine}
  DocumentRoot ${_root}/wwwroot

  RewriteEngine On
  RewriteCond %{REQUEST_URI} !^/.well-known/acme-challenge/
  RewriteRule ^(.*)\$ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]

  ErrorLog /var/log/apache2/${_site}-rewrite-error.log
</VirtualHost>

<VirtualHost *:443>
  ServerAdmin ${_mail}
  ServerName ${_binding}
${_aliasLine}
  DocumentRoot ${_root}/wwwroot

  AllowEncodedSlashes NoDecode

  RewriteEngine On
  RewriteCond %{HTTP:Upgrade} =websocket [NC]
  RewriteCond %{HTTP:Connection} upgrade [NC]
  RewriteRule /(.*) ws://localhost:${_port}/\$1 [P,L]

  ProxyPreserveHost On
  ProxyRequests Off
  RequestHeader set X-Forwarded-Proto "https"
${_extra}
  ProxyPass / http://localhost:${_port}/ nocanon
  ProxyPassReverse / http://localhost:${_port}/

  SSLEngine On
  SSLCertificateFile ${_certdir}/fullchain.cer
  SSLCertificateKeyFile ${_certdir}/${_binding}.key

  ErrorLog /var/log/apache2/${_site}-443-error.log
</VirtualHost>
EOF
}

set_certificate_domains() {
	local _config=$1 _primary _alias _domain _found _candidate
	local -a _seen
	_primary=$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')
	_certificateDomainArgs=(-d "${_primary}")
	_certificateDomainAliases=()
	_certificateDomains=("${_primary}")
	_seen=("${_primary}")
	_certificateDomainNames="${_primary}"
	[[ -f ${_config} ]] || return 0
	while IFS= read -r _alias; do
		[[ -n ${_alias} ]] || continue
		_domain=$(printf '%s' "${_alias}" | tr '[:upper:]' '[:lower:]')
		if ! validate_binding "${_domain}"; then
			echo "Invalid ServerAlias '${_alias}' in ${_config}; certificate issuance stopped." >&2
			return 1
		fi
		_found=0
		for _candidate in "${_seen[@]}"; do
			[[ ${_candidate} == "${_domain}" ]] && _found=1
		done
		((_found)) && continue
		_seen+=("${_domain}")
		_certificateDomainArgs+=(-d "${_domain}")
		_certificateDomainAliases+=("${_domain}")
		_certificateDomains+=("${_domain}")
		_certificateDomainNames+=" ${_domain}"
	done < <(awk '$1 == "ServerAlias" { for (i = 2; i <= NF && $i !~ /^#/; i++) print $i }' "${_config}")
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
		-e "s|@DOTNET_VERSION@|${_dotNetVersion:-}|g" \
		-e "s|@UMBRACO_VERSION@|${_umbracoVersion:-}|g" \
		-e "s|@CERT_FILE@|${_certificateFile}|g" \
		-e "s|@KEY_FILE@|${_keyFile}|g" \
		-e "s|@ADMIN_MAIL@|${_adminMail}|g" \
		-e "s|@BUILD_CONFIG@|${_buildConfig:-Debug}|g" \
		-e "s|@ENVIRONMENT@|${_environment:-Test}|g" \
		-e "s|@APACHE_DIR@|${_apacheDir}|g" \
		-e "s|@CSHARP_KIND@|${_csharpKind:-}|g" \
		-e "s|@ACCESS_RULES@|${_accessRules:-Require all granted}|g" \
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

# Reserve the first available port starting at 4000 and set _portHttp.
# Existing Apache/systemd configurations, listening sockets and concurrent creations are respected.
allocate_ports() {
	local _port _siteName=${_site:-${SITE:-unknown}}
	sudo mkdir -p "${_portReservationDir}"
	_port=$(sudo flock -x "${_portAllocationLock}" bash -c '
		set -euo pipefail
		dir=$1
		owner_pid=$2
		site_name=$3
		shopt -s nullglob
		declare -a used=()

		for reservation in "$dir"/*.reserve; do
			port=${reservation##*/}
			port=${port%.reserve}
			read -r pid _site <"$reservation" || true
			if [[ ! ${pid:-} =~ ^[0-9]+$ ]] || ! kill -0 "$pid" 2>/dev/null; then
				rm -f "$reservation"
			else
				used[$port]=1
			fi
		done

		command -v ss >/dev/null || { echo "ss is required to detect listening ports." >&2; exit 1; }
		while read -r _state _recv _send address _peer; do
			port=${address##*:}
			[[ $port =~ ^[0-9]+$ ]] && used[$port]=1
		done < <(ss -H -ltn)

		for config in /etc/apache2/sites-available/*.conf /etc/systemd/system/*.service; do
			[[ -f $config ]] || continue
			while IFS= read -r port; do
				[[ $port =~ ^[0-9]+$ ]] && used[$port]=1
			done < <(grep -hEo "(127\\.0\\.0\\.1|localhost|\\*):[0-9]{1,5}" "$config" 2>/dev/null | cut -d: -f2 || true)
		done

		for ((port = 4000; port <= 65535; port++)); do
			if [[ ! ${used[$port]+yes} && ! -e $dir/$port.reserve ]]; then
				printf "%s %s\\n" "$owner_pid" "$site_name" >"$dir/$port.reserve"
				printf "%s\\n" "$port"
				exit 0
			fi
		done
		echo "No available site ports in the range 4000-65535." >&2
		exit 1
	' _ "${_portReservationDir}" "$$" "${_siteName}") || return 1
	_portHttp=${_port}
	_portReservationFile="${_portReservationDir}/${_port}.reserve"
	echo "Port: ${_portHttp}"
}

release_port_reservation() {
	if [[ -n ${_portReservationFile:-} ]]; then
		sudo rm -f "${_portReservationFile}" || true
		_portReservationFile=""
	fi
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
	set_certificate_domains "${_configPath}" "${_binding}"
	echo "Create temporary HTTP-only config for certificate validation"
	sudo mkdir -p "${_docRoot}"
	render_to "${_solutionFolder}/common/apache-temp.conf.tpl" "${_configPath}"
	if ((${#_certificateDomainAliases[@]})); then
		printf '  ServerAlias %s\n' "${_certificateDomainAliases[*]}" | sudo tee -a "${_configPath}" >/dev/null
	fi

	sudo a2enmod ssl
	sudo apache2ctl configtest
	[[ -e "/etc/apache2/sites-enabled/${_site}.conf" ]] || sudo a2ensite "${_site}.conf"
	sudo systemctl reload apache2

	for _domain in "${_certificateDomains[@]}"; do clean_stale_acme "${_domain}"; done
	echo "Issue certificate"
	echo "Certificate domains: ${_certificateDomainNames}"
	# acme.sh returns 2 when a still-valid certificate is skipped
	sudo "${_acme}" --issue --apache --force --server "${_acmeServer}" "${_certificateDomainArgs[@]}" || {
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
	if [[ ${_createCertificate:-y} == n ]]; then
		render "$1" | awk '
			/<VirtualHost \*:443>/ { active=1; sub(":443", ":80") }
			active && !/^[[:space:]]*SSL/ {
				if (/^[[:space:]]*ProxyPass \/.well-known\/acme-challenge\/ !/) next
				gsub("X-Forwarded-Proto \"https\"", "X-Forwarded-Proto \"http\"")
				gsub("-443-error.log", "-80-error.log")
				print
				if (/ProxyRequests Off/) print "  ProxyPass /.well-known/acme-challenge/ !"
			}
			/<\/VirtualHost>/ { active=0 }
		' | sudo tee "${_configPath}" >/dev/null
	else
		render_to "$1" "${_configPath}"
	fi
	sudo apache2ctl configtest
	[[ -e "/etc/apache2/sites-enabled/${_site}.conf" ]] || sudo a2ensite "${_site}.conf"
	sudo systemctl reload apache2
}

# Prints "changed" only when application settings were updated.
configure_umbraco_https() {
	sudo python3 - "$1" "$2" "$3" <<'PY'
import json
import sys
path, binding, enabled = sys.argv[1:]
with open(path) as stream:
    settings = json.load(stream)
cms = settings.get("Umbraco", {}).get("CMS")
if cms is not None:
    secure = enabled == "y"
    url = ("https://" if secure else "http://") + binding
    if (cms.get("Global", {}).get("UseHttps") == secure
            and cms.get("WebRouting", {}).get("UmbracoApplicationUrl") == url):
        sys.exit(0)
    cms.setdefault("Global", {})["UseHttps"] = secure
    cms.setdefault("WebRouting", {})["UmbracoApplicationUrl"] = url
    with open(path, "w") as stream:
        json.dump(settings, stream, indent=2)
        stream.write("\n")
    print("changed")
PY
}

enable_https_config() {
	if grep -q '<VirtualHost \*:443>' "${_configPath}"; then
		return
	fi
	sudo a2enmod ssl
	local _updated
	_updated=$(mktemp)
	{
		printf '<VirtualHost *:80>\n  ServerName %s\n  RewriteEngine On\n  RewriteCond %%{REQUEST_URI} !^/.well-known/acme-challenge/\n  RewriteRule ^ https://%%{HTTP_HOST}%%{REQUEST_URI} [R=301,L]\n</VirtualHost>\n\n' "${_binding}"
		awk -v cert="${_certificateFile}" -v key="${_keyFile}" '
			{
				gsub("<VirtualHost \\*:80>", "<VirtualHost *:443>")
				gsub("X-Forwarded-Proto \"http\"", "X-Forwarded-Proto \"https\"")
				if (/<\/VirtualHost>/) {
					print "  SSLEngine On"
					print "  SSLCertificateFile " cert
					print "  SSLCertificateKeyFile " key
				}
				print
			}
		' "${_configPath}"
	} >"${_updated}"
	sudo cp "${_configPath}" "${_updated}.backup"
	sudo cp "${_updated}" "${_configPath}"
	if ! sudo apache2ctl configtest; then
		sudo cp "${_updated}.backup" "${_configPath}"
		sudo rm -f "${_updated}" "${_updated}.backup"
		return 1
	fi
	sudo rm -f "${_updated}" "${_updated}.backup"
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
