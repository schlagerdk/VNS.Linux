#!/bin/bash
# Publish this C# site (always a Release build) and upload it to a production server over SSH,
# or into a local folder when PROD_LOCAL_PATH is set (e.g. a second copy of the site on this server).
# The Release build goes to a temporary folder, so the local publish folder is left untouched.
# Creates the folder, systemd service, Apache config and certificate on the target if they are missing.
# Requirements on prod: VNS.Linux apache/init.sh has been run, and the login is root or has passwordless sudo.
# DNS for PROD_BINDING must point to the target server before the certificate can be issued.
# With PROD_LOCAL_PATH use a PROD_FOLDER and PROD_BINDING that differ from the site it is published from.
if [[ ${VNS_PUBLISH_TRACKED:-} != prod ]]; then
	exec python3 "@APACHE_DIR@/lib/publish_status.py" run "$(cd "$(dirname "$0")/.." && pwd)" prod /bin/bash "$(realpath "$0")" "$@"
fi
cd "$(dirname "$0")"
cd ..
set -euo pipefail
source "@APACHE_DIR@/lib/dotnet.sh"

# ---- Production settings (filled in by create_prod.sh, edit if needed) -----
PROD_LOCAL_PATH=@PROD_LOCAL_PATH@   # If set: publish to this local folder on this server instead of a remote server
PROD_IP=@PROD_IP@                   # IP or hostname of the production server
PROD_USER=@PROD_USER@               # SSH login
PROD_PASSWORD=@PROD_PASSWORD@       # SSH password (needs sshpass). Empty = use an SSH key
PROD_SSH_PORT=@PROD_SSH_PORT@
PROD_FOLDER=@PROD_FOLDER@           # Site name on prod: folder /var/www/<folder>, service and Apache config
PROD_BINDING=@PROD_BINDING@         # Public domain on prod, e.g. account.vns.dk
PROD_ENVIRONMENT="@PROD_ENVIRONMENT@"
PROD_ADMIN_MAIL="@ADMIN_MAIL@"
# ---------------------------------------------------------------------------

_projectName=@PROJECT_NAME@
_localPublish=@PUBLISH_FOLDER@   # The site's local publish folder: source of appsettings*.json and web.config on the first publish
_publishFolder=$(mktemp -d)
_cleanup=("${_publishFolder}")
trap 'rm -rf "${_cleanup[@]}"' EXIT
_local=0
[[ -n ${PROD_LOCAL_PATH} ]] && _local=1
if ((_local)); then
	_root=${PROD_LOCAL_PATH%/}
	_sudo=""
	[[ $EUID -eq 0 ]] || _sudo="sudo"
else
	_root=/var/www/${PROD_FOLDER}
fi

_rsyncShell=""
_rsyncPath=""
if [[ -z ${PROD_FOLDER} || -z ${PROD_BINDING} ]]; then
	echo "Set PROD_FOLDER and PROD_BINDING at the top of this script" >&2
	exit 1
fi
if ((! _local)); then
	if [[ -z ${PROD_IP} || -z ${PROD_USER} ]]; then
		echo "Set PROD_IP and PROD_USER (or PROD_LOCAL_PATH) at the top of this script" >&2
		exit 1
	fi

	_ssh=(ssh -p "${PROD_SSH_PORT}" -o StrictHostKeyChecking=accept-new)
	if [[ -n ${PROD_PASSWORD} ]]; then
		command -v sshpass >/dev/null || { echo "sshpass is required for password login: sudo apt install sshpass" >&2; exit 1; }
		export SSHPASS=${PROD_PASSWORD}
		_ssh=(sshpass -e "${_ssh[@]}")
	fi
	_target=${PROD_USER}@${PROD_IP}
	_sudo=""
	[[ ${PROD_USER} == root ]] || _sudo="sudo -n"
	_rsyncShell="${_ssh[*]}"
	_rsyncPath="${_sudo:+${_sudo} }rsync"
fi

remote() { "${_ssh[@]}" "${_target}" "$@"; }
if ((_local)); then
	_runtimeIdentifier=$(linux_runtime_identifier)
else
	_runtimeIdentifier=$(linux_runtime_identifier "$(remote uname -m)")
fi

# Run the script from stdin as root on the target (this machine in local mode)
run_script() {
	if ((_local)); then
		${_sudo} bash -c "env ${_vars} bash -s"
	else
		remote "${_sudo} env ${_vars} bash -s"
	fi
}

# A local target inside the project folder must be excluded from the build, or its files are compiled/included
if ((_local)); then
	_projectDir=$(pwd -P)
	_target_abs=$(realpath -m "${PROD_LOCAL_PATH}")
	if [[ ${_target_abs} == "${_projectDir}"/* ]]; then
		_rel=${_target_abs#"${_projectDir}"/}
		_csproj=${_projectDir}/${_projectName}.csproj
		[[ -f ${_csproj} ]] || _csproj=$(ls "${_projectDir}"/*.csproj | head -n 1)
		if ! grep -qF "Compile Remove=\"${_rel}/**\"" "${_csproj}"; then
			echo "Exclude ${_rel} from the build in $(basename "${_csproj}")"
			perl -0pi -e 's|</Project>\s*$|  <!-- Exclude local prod publish folder from build -->\n  <ItemGroup>\n    <Content Remove="'"${_rel}"'/**" />\n    <Compile Remove="'"${_rel}"'/**" />\n    <None Remove="'"${_rel}"'/**" />\n  </ItemGroup>\n</Project>\n|' "${_csproj}"
		fi
	fi
fi

echo "Publish (Release)"
dotnet publish "${_projectName}.csproj" -c Release -p:PublishDir="${_publishFolder}" -p:CustomAfterMicrosoftCommonTargets="@APACHE_DIR@/lib/publish.targets" --runtime "${_runtimeIdentifier}" --self-contained false --nologo
if [[ ! -f ${_publishFolder}/${_projectName}.dll ]]; then
	echo "Publish failed: ${_projectName}.dll not found in the output" >&2
	exit 1
fi

_umbraco=0
grep -qs "Umbraco.Cms" ./*.csproj && _umbraco=1

_vars="SITE='${PROD_FOLDER}' BINDING='${PROD_BINDING}' ENVIRONMENT='${PROD_ENVIRONMENT}' MAIL='${PROD_ADMIN_MAIL}' PROJECT='${_projectName}' UMBRACO='${_umbraco}' ROOT='${_root}'"

echo "Prepare ${PROD_FOLDER} on ${PROD_IP:-this server}"
run_script <<'REMOTE'
set -euo pipefail
root=${ROOT}
docroot=${root}/wwwroot
conf=/etc/apache2/sites-available/${SITE}.conf
service=/etc/systemd/system/${SITE}.service
certdir=/etc/ssl/certs/${BINDING}_ecc
acme=/etc/acmesh/acme.sh
source /vns/apache/lib/common.sh

if [[ -e ${service} ]] && ! grep -qx "WorkingDirectory=${root}" "${service}"; then
	echo "Service ${SITE} already exists and runs from a different folder. Use another PROD_FOLDER (and PROD_BINDING)." >&2
	exit 1
fi

PORT=""
if [[ -e ${service} ]]; then
	PORT=$(grep '^ExecStart=' "${service}" | grep -oE '(localhost|127\.0\.0\.1|\*):[0-9]+' | head -n 1 | cut -d: -f2 || true)
	[[ -n ${PORT} ]] || { echo "Could not determine the existing port from ${service}." >&2; exit 1; }
elif [[ -e ${conf} ]]; then
	PORT=$(grep -hEo '(127\.0\.0\.1|localhost|\*):[0-9]{1,5}' "${conf}" | head -n 1 | cut -d: -f2)
fi
if [[ -z ${PORT} ]]; then
	_site=${SITE}
	allocate_ports
	PORT=${_portHttp}
fi
echo "Using production port ${PORT}"

mkdir -p "${docroot}" "/var/lib/vns-roots/${SITE}"
if [[ -e ${service} ]]; then
	echo "Stop service"
	systemctl stop "${SITE}" || true
fi

if [[ ! -e ${service} ]]; then
	echo "Create service"
	cat >"${service}" <<EOF
[Unit]
Description=Running .NET Core [${SITE}]

[Service]
User=www-data
Group=www-data
WorkingDirectory=${root}
ExecStart=/usr/lib/dotnet/dotnet ${root}/${PROJECT}.dll --urls "http://localhost:${PORT}"
RestartSec=10
Restart=always
KillSignal=SIGINT
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SITE}
Environment=ASPNETCORE_ENVIRONMENT=${ENVIRONMENT}
Environment=DOTNET_ENVIRONMENT=${ENVIRONMENT}
Environment=ASPNETCORE_FORWARDEDHEADERS_ENABLED=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
NoNewPrivileges=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ProtectProc=invisible
ProcSubset=pid
RootDirectory=/var/lib/vns-roots/${SITE}
BindReadOnlyPaths=/usr/lib/dotnet /usr/lib/x86_64-linux-gnu /usr/lib/x86_64-linux-gnu:/lib/x86_64-linux-gnu /lib64 /usr/lib/ssl -/usr/share/zoneinfo -/usr/share/ca-certificates /etc/ssl /etc/ld.so.cache /etc/resolv.conf /etc/hosts /etc/nsswitch.conf /etc/passwd /etc/group /etc/localtime /etc/os-release
BindPaths=${root}
MountAPIVFS=yes
ReadWritePaths=${root}

[Install]
WantedBy=multi-user.target
EOF
	systemctl daemon-reload
	systemctl enable "${SITE}"
fi

server_aliases=()
if [[ -f ${conf} ]]; then
	while IFS= read -r alias; do
		[[ -n ${alias} ]] || continue
		alias=${alias,,}
		[[ ${alias} =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ && ${#alias} -le 253 ]] || {
			echo "Invalid ServerAlias '${alias}' in ${conf}; certificate issuance stopped." >&2
			exit 1
		}
		found=0
		for domain in "${BINDING}" "${server_aliases[@]}"; do [[ ${domain} == "${alias}" ]] && found=1; done
		((found)) || server_aliases+=("${alias}")
	done < <(awk '$1 == "ServerAlias" { for (i = 2; i <= NF && $i !~ /^#/; i++) print $i }' "${conf}")
fi
server_alias_line=""
if ((${#server_aliases[@]})); then
	printf -v server_alias_line '  ServerAlias %s' "${server_aliases[*]}"
fi

if [[ ! -e ${certdir}/fullchain.cer ]]; then
	[[ -x ${acme} ]] || { echo "acme.sh not found on prod (${acme}). Run apache/init.sh there first." >&2; exit 1; }
	certificate_domains=("${BINDING}" "${server_aliases[@]}")
	certificate_args=()
	for domain in "${certificate_domains[@]}"; do certificate_args+=(-d "${domain}"); done
	printf 'Issue certificate for: %s\n' "${certificate_domains[*]}"
	cat >"${conf}" <<EOF
<VirtualHost *:80>
  ServerAdmin ${MAIL}
  ServerName ${BINDING}
${server_alias_line}
  DocumentRoot ${docroot}
  ErrorLog /var/log/apache2/${SITE}-80-error.log
  <Directory ${docroot}>
    AllowOverride All
    Require all granted
  </Directory>
</VirtualHost>
EOF
	a2enmod ssl proxy proxy_http proxy_wstunnel rewrite headers >/dev/null
	[[ -e /etc/apache2/sites-enabled/${SITE}.conf ]] || a2ensite "${SITE}.conf" >/dev/null
	apache2ctl configtest
	systemctl reload apache2
	# Leftover acme data for this domain (e.g. from a deleted site) makes --issue fail with "Domain key exists"
	used=0
	for c in /etc/apache2/sites-enabled/*.conf; do
		[[ -e ${c} && ${c} != "/etc/apache2/sites-enabled/${SITE}.conf" ]] || continue
		awk -v d="${BINDING}" '($1 == "ServerName" || $1 == "ServerAlias") { for (i = 2; i <= NF; i++) if (tolower($i) == tolower(d)) f = 1 } END { exit !f }' "${c}" && used=1
	done
	if [[ ${used} == 0 ]]; then
		"${acme}" --remove -d "${BINDING}" >/dev/null 2>&1 || true
		rm -rf "/etc/ssl/certs/${BINDING}" "/etc/ssl/certs/${BINDING}_ecc"
	fi
	rc=0
	"${acme}" --issue --apache --force --server letsencrypt "${certificate_args[@]}" || rc=$?
	if [[ ${rc} -ne 0 && ${rc} -ne 2 ]]; then
		echo "Certificate issue failed. Check that DNS for ${BINDING} points to this server." >&2
		exit 1
	fi
	mkdir -p "${certdir}"
	"${acme}" --install-cert -d "${BINDING}" --key-file "${certdir}/${BINDING}.key" \
		--fullchain-file "${certdir}/fullchain.cer" --reloadcmd "systemctl reload apache2"
fi

if ! grep -q "SSLEngine" "${conf}" 2>/dev/null; then
	echo "Create Apache config"
	extra=""
	if [[ ${UMBRACO} == 1 ]]; then
		extra="
  ProxyPass /robots.txt http://localhost:${PORT}/seo_robots_txt
  ProxyPassReverse /robots.txt http://localhost:${PORT}/seo_robots_txt

  ProxyPass /sitemap.xml http://localhost:${PORT}/seo_sitemap_xml
  ProxyPassReverse /sitemap.xml http://localhost:${PORT}/seo_sitemap_xml
"
	fi
	cat >"${conf}" <<EOF
<VirtualHost *:80>
  ServerAdmin ${MAIL}
  ServerName ${BINDING}
${server_alias_line}
  DocumentRoot ${docroot}

  RewriteEngine On
  RewriteCond %{REQUEST_URI} !^/.well-known/acme-challenge/
  RewriteRule ^(.*)\$ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]

  ErrorLog /var/log/apache2/${SITE}-rewrite-error.log
</VirtualHost>

<VirtualHost *:443>
  ServerAdmin ${MAIL}
  ServerName ${BINDING}
${server_alias_line}
  DocumentRoot ${docroot}

  AllowEncodedSlashes NoDecode

  RewriteEngine On
  RewriteCond %{HTTP:Upgrade} =websocket [NC]
  RewriteCond %{HTTP:Connection} upgrade [NC]
  RewriteRule /(.*) ws://localhost:${PORT}/\$1 [P,L]

  ProxyPreserveHost On
  ProxyRequests Off
  RequestHeader set X-Forwarded-Proto "https"
${extra}
  ProxyPass / http://localhost:${PORT}/ nocanon
  ProxyPassReverse / http://localhost:${PORT}/

  SSLEngine On

  SSLCertificateFile ${certdir}/fullchain.cer
  SSLCertificateKeyFile ${certdir}/${BINDING}.key

  ErrorLog /var/log/apache2/${SITE}-443-error.log
</VirtualHost>
EOF
fi

# The site must be enabled whenever the config exists, also if the certificate or config was created earlier
a2enmod ssl proxy proxy_http proxy_wstunnel rewrite headers >/dev/null
[[ -e /etc/apache2/sites-enabled/${SITE}.conf ]] || a2ensite "${SITE}.conf" >/dev/null
apache2ctl configtest
systemctl reload apache2
release_port_reservation
REMOTE

if ((_local)); then
	echo "Publish to local folder ${_root}"
	${_sudo} mkdir -p "${_root}"
	_dest="${_root}/"
	_rsyncRemote=()
else
	echo "Upload to ${_target}:${_root}"
	_dest="${_target}:${_root}/"
	_rsyncRemote=(-e "${_rsyncShell}" --rsync-path="${_rsyncPath}")
fi

# Is this the first publish? Decided before anything is copied.
_exists=0
if { ((_local)) && [[ -f ${_root}/appsettings.json ]]; } || { ((! _local)) && remote "${_sudo} test -f '${_root}/appsettings.json'"; }; then
	_exists=1
fi

# Schema files are never uploaded. Once the site exists on the target, its content, settings and data are never
# overwritten or deleted by a publish (rsync keeps excluded paths when using --delete).
_keep=(--exclude 'appsettings-schema*.json' --exclude 'umbraco-package-schema.json')
if ((_exists)); then
	_keep+=(--exclude 'appsettings*.json' --exclude 'web.config'
		--exclude '/umbraco/' --exclude '/uSync/' --exclude '/Views/' --exclude '/wwwroot/'
		--exclude '/App_Data/' --exclude '/app_data/')
fi
rsync -az --delete "${_keep[@]}" ${_rsyncRemote[@]+"${_rsyncRemote[@]}"} "${_publishFolder}/" "${_dest}"

if ((_exists)); then
	echo "appsettings.json and web.config already exist in the destination - not touched"
else
	echo "First publish: copy appsettings*.json (without ConnectionStrings) and web.config"
	_first=$(mktemp -d)
	_cleanup+=("${_first}")
	# Prefer the files from the local publish folder (created by create_site.sh); fall back to the fresh build
	for _src in "${_localPublish}" "${_publishFolder}"; do
		cp "${_src}"/appsettings*.json "${_first}/" 2>/dev/null || true
		[[ -f ${_src}/web.config && ! -f ${_first}/web.config ]] && cp "${_src}/web.config" "${_first}/"
		[[ -f ${_first}/appsettings.json ]] && break
	done
	rm -f "${_first}"/appsettings-schema*.json
	for _file in "${_first}"/appsettings*.json; do
		[[ -f ${_file} ]] || continue
		if command -v python3 >/dev/null; then
			python3 - "${_file}" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8-sig") as f:
    data = json.load(f)
data.pop("ConnectionStrings", None)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
PY
		elif command -v jq >/dev/null; then
			jq 'del(.ConnectionStrings)' "${_file}" >"${_file}.new" && mv "${_file}.new" "${_file}"
		else
			echo "python3 or jq is required to remove ConnectionStrings" >&2
			exit 1
		fi
	done
	rsync -az ${_rsyncRemote[@]+"${_rsyncRemote[@]}"} "${_first}/" "${_dest}"
fi

echo "Start service"
run_script <<'REMOTE'
set -euo pipefail
dir=${ROOT}
file=${dir}/appsettings.json
if [[ -f ${file} ]] && grep -q "UmbracoApplicationUrl" "${file}"; then
	echo "Set UmbracoApplicationUrl to https://${BINDING}"
	sed -i -E "s|(\"UmbracoApplicationUrl\"[[:space:]]*:[[:space:]]*\")[^\"]*|\1https://${BINDING}|" "${file}"
	sed -i -E 's/("UseHttps"[[:space:]]*:[[:space:]]*)false/\1true/' "${file}"
fi
chown -R www-data:www-data "${dir}"
systemctl start "${SITE}"
sleep 2
systemctl status "${SITE}" --no-pager
REMOTE

echo "Delete schema files and bin"
rm -f appsettings-schema.json
rm -f appsettings-schema.usync.json
rm -f appsettings-schema.Umbraco.Cms.json
rm -f umbraco-package-schema.json
rm -rf App_Plugins
rm -rf bin
