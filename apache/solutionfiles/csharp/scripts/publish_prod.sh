#!/bin/bash
# Upload the already published output (run publish.sh first) of this C# site to a production server over SSH.
# Creates the folder, systemd service, Apache config and certificate on prod if they are missing.
# Requirements on prod: VNS.Linux apache/init.sh has been run, and the login is root or has passwordless sudo.
# DNS for PROD_BINDING must point to the prod server before the certificate can be issued.
cd "$(dirname "$0")"
cd ..
set -euo pipefail

# ---- Production settings --------------------------------------------------
PROD_IP=""                      # IP or hostname of the production server
PROD_USER="root"                # SSH login
PROD_PASSWORD=""                # SSH password (needs sshpass). Leave empty to use an SSH key
PROD_SSH_PORT=22
PROD_FOLDER="@SITE@"            # Folder name on prod (/var/www/<folder>)
PROD_BINDING="@BINDING@"        # Public domain on prod, e.g. account.vns.dk
PROD_PORT_HTTP=$((@PORT_HTTP@ + 2000))   # Local Kestrel port on prod: test port + 2000, so it cannot clash with sites created locally on prod
PROD_ENVIRONMENT="Production"
PROD_ADMIN_MAIL="@ADMIN_MAIL@"
# ---------------------------------------------------------------------------

_projectName=@PROJECT_NAME@
_publishFolder=@PUBLISH_FOLDER@
_root=/var/www/${PROD_FOLDER}

if [[ -z ${PROD_IP} || -z ${PROD_USER} || -z ${PROD_FOLDER} || -z ${PROD_BINDING} ]]; then
	echo "Set PROD_IP, PROD_USER, PROD_FOLDER and PROD_BINDING at the top of this script" >&2
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

remote() { "${_ssh[@]}" "${_target}" "$@"; }

if [[ ! -f ${_publishFolder}/${_projectName}.dll ]]; then
	echo "Nothing to upload: ${_publishFolder}/${_projectName}.dll not found. Run publish.sh first." >&2
	exit 1
fi

_umbraco=0
[[ -f ${_publishFolder}/Umbraco.Cms.Core.dll ]] && _umbraco=1

_vars="SITE='${PROD_FOLDER}' BINDING='${PROD_BINDING}' PORT='${PROD_PORT_HTTP}' ENVIRONMENT='${PROD_ENVIRONMENT}' MAIL='${PROD_ADMIN_MAIL}' PROJECT='${_projectName}' UMBRACO='${_umbraco}'"

echo "Prepare ${PROD_FOLDER} on ${PROD_IP}"
remote "${_sudo} env ${_vars} bash -s" <<'REMOTE'
set -euo pipefail
root=/var/www/${SITE}
docroot=${root}/wwwroot
conf=/etc/apache2/sites-available/${SITE}.conf
service=/etc/systemd/system/${SITE}.service
certdir=/etc/ssl/certs/${SITE}_ecc
acme=/etc/acmesh/acme.sh

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

echo "Upload to ${_target}:${_root}"
# appsettings*.json and web.config are prod-specific: only copied the first time, never overwritten later
_keep=(--exclude 'appsettings-schema*.json' --exclude 'umbraco-package-schema.json' --exclude 'appsettings*.json' --exclude 'web.config' --exclude 'wwwroot/media/' --exclude 'umbraco/Data/' --exclude 'umbraco/Logs/' --exclude 'App_Data/')
rsync -az --delete "${_keep[@]}" -e "${_rsyncShell}" --rsync-path="${_rsyncPath}" "${_publishFolder}/" "${_target}:${_root}/"

if remote "${_sudo} test -f '${_root}/appsettings.json'"; then
	echo "appsettings.json and web.config already exist on prod - not touched"
else
	echo "First publish: copy appsettings*.json (without ConnectionStrings) and web.config"
	_first=$(mktemp -d)
	trap 'rm -rf "${_first}"' EXIT
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
	rsync -az -e "${_rsyncShell}" --rsync-path="${_rsyncPath}" "${_first}/" "${_target}:${_root}/"
fi

echo "Start service"
remote "${_sudo} env ${_vars} bash -s" <<'REMOTE'
set -euo pipefail
dir=/var/www/${SITE}
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
