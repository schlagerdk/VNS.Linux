#!/bin/bash
# Publish this C# site (always a Release build) and upload it to a production server over SSH,
# or into a local folder when PROD_LOCAL_PATH is set (e.g. a second copy of the site on this server).
# The Release build goes to a temporary folder, so the local publish folder is left untouched.
# Creates the folder, systemd service, Apache config and certificate on the target if they are missing.
# Requirements on prod: VNS.Linux apache/init.sh has been run, and the login is root or has passwordless sudo.
# DNS for PROD_BINDING must point to the target server before the certificate can be issued.
# With PROD_LOCAL_PATH use a PROD_FOLDER, PROD_BINDING and port that differ from the site it is published from.
cd "$(dirname "$0")"
cd ..
set -euo pipefail

# ---- Production settings (filled in by create_prod.sh, edit if needed) -----
PROD_LOCAL_PATH=@PROD_LOCAL_PATH@   # If set: publish to this local folder on this server instead of a remote server
PROD_IP=@PROD_IP@                   # IP or hostname of the production server
PROD_USER=@PROD_USER@               # SSH login
PROD_PASSWORD=@PROD_PASSWORD@       # SSH password (needs sshpass). Empty = use an SSH key
PROD_SSH_PORT=@PROD_SSH_PORT@
PROD_FOLDER=@PROD_FOLDER@           # Site name on prod: folder /var/www/<folder>, service and Apache config
PROD_BINDING=@PROD_BINDING@         # Public domain on prod, e.g. account.vns.dk
PROD_PORT_HTTP=@PROD_PORT_HTTP@     # Local Kestrel port on prod
PROD_ENVIRONMENT="Production"
PROD_ADMIN_MAIL="@ADMIN_MAIL@"
# ---------------------------------------------------------------------------

_projectName=@PROJECT_NAME@
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

# Run the script from stdin as root on the target (this machine in local mode)
run_script() {
	if ((_local)); then
		${_sudo} env ${_vars} bash -s
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
dotnet publish -c Release -p:PublishDir="${_publishFolder}" --self-contained false --nologo
if [[ ! -f ${_publishFolder}/${_projectName}.dll ]]; then
	echo "Publish failed: ${_projectName}.dll not found in the output" >&2
	exit 1
fi

_umbraco=0
[[ -f ${_publishFolder}/Umbraco.Cms.Core.dll ]] && _umbraco=1

_vars="SITE='${PROD_FOLDER}' BINDING='${PROD_BINDING}' PORT='${PROD_PORT_HTTP}' ENVIRONMENT='${PROD_ENVIRONMENT}' MAIL='${PROD_ADMIN_MAIL}' PROJECT='${_projectName}' UMBRACO='${_umbraco}' ROOT='${_root}'"

echo "Prepare ${PROD_FOLDER} on ${PROD_IP:-this server}"
run_script <<'REMOTE'
set -euo pipefail
root=${ROOT}
docroot=${root}/wwwroot
conf=/etc/apache2/sites-available/${SITE}.conf
service=/etc/systemd/system/${SITE}.service
certdir=/etc/ssl/certs/${SITE}_ecc
acme=/etc/acmesh/acme.sh

if [[ -e ${service} ]] && ! grep -qx "WorkingDirectory=${root}" "${service}"; then
	echo "Service ${SITE} already exists and runs from a different folder. Use another PROD_FOLDER (and PROD_BINDING)." >&2
	exit 1
fi

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

if [[ ! -e ${certdir}/fullchain.cer ]]; then
	echo "Issue certificate for ${BINDING}"
	[[ -x ${acme} ]] || { echo "acme.sh not found on prod (${acme}). Run apache/init.sh there first." >&2; exit 1; }
	cat >"${conf}" <<EOF
<VirtualHost *:80>
  ServerAdmin ${MAIL}
  ServerName ${BINDING}
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
	rc=0
	"${acme}" --issue --apache --server letsencrypt -d "${BINDING}" || rc=$?
	if [[ ${rc} -ne 0 && ${rc} -ne 2 ]]; then
		echo "Certificate issue failed. Check that DNS for ${BINDING} points to this server." >&2
		exit 1
	fi
	mkdir -p "${certdir}"
	"${acme}" --install-cert -d "${BINDING}" --key-file "${certdir}/${SITE}.key" \
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
  DocumentRoot ${docroot}

  RewriteEngine On
  RewriteCond %{REQUEST_URI} !^/.well-known/acme-challenge/
  RewriteRule ^(.*)\$ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]

  ErrorLog /var/log/apache2/${SITE}-rewrite-error.log
</VirtualHost>

<VirtualHost *:443>
  ServerAdmin ${MAIL}
  ServerName ${BINDING}
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
  SSLCertificateKeyFile ${certdir}/${SITE}.key

  ErrorLog /var/log/apache2/${SITE}-443-error.log
</VirtualHost>
EOF
	apache2ctl configtest
	systemctl reload apache2
fi
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

# appsettings*.json and web.config are prod-specific: only copied the first time, never overwritten later
_keep=(--exclude 'appsettings-schema*.json' --exclude 'umbraco-package-schema.json' --exclude 'appsettings*.json' --exclude 'web.config' --exclude 'wwwroot/media/' --exclude 'umbraco/Data/' --exclude 'umbraco/Logs/' --exclude 'App_Data/')
rsync -az --delete "${_keep[@]}" ${_rsyncRemote[@]+"${_rsyncRemote[@]}"} "${_publishFolder}/" "${_dest}"

if { ((_local)) && [[ -f ${_root}/appsettings.json ]]; } || { ((! _local)) && remote "${_sudo} test -f '${_root}/appsettings.json'"; }; then
	echo "appsettings.json and web.config already exist in the destination - not touched"
else
	echo "First publish: copy appsettings*.json (without ConnectionStrings) and web.config"
	_first=$(mktemp -d)
	_cleanup+=("${_first}")
	cp "${_publishFolder}"/appsettings*.json "${_first}/" 2>/dev/null || true
	rm -f "${_first}"/appsettings-schema*.json
	[[ -f ${_publishFolder}/web.config ]] && cp "${_publishFolder}/web.config" "${_first}/"
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
