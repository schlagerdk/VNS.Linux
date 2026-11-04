[Unit]
# Author: Martin Schläger (VNS)
Description=Running .NET Core [@SITE@]

[Service]
User=www-data
Group=www-data
WorkingDirectory=@SITE_ROOT@/@PUBLISH_FOLDER@
ExecStart=/usr/lib/dotnet/dotnet @SITE_ROOT@/@PUBLISH_FOLDER@/@SITE@.dll --urls "http://127.0.0.1:@PORT_HTTP@"

# Restart service after 10 seconds if the dotnet service crashes:
RestartSec=10
Restart=always
KillSignal=SIGINT

# Output to journal
StandardOutput=journal
StandardError=journal
SyslogIdentifier=@SITE@

# Variables
Environment=ASPNETCORE_ENVIRONMENT=Test
Environment=DOTNET_ENVIRONMENT=Test
Environment=ASPNETCORE_FORWARDEDHEADERS_ENABLED=true

# Protect: chroot - the site only sees system libraries and its own folder, not /root, /home, /vns or other sites
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
RootDirectory=/var/lib/vns-empty-root
BindReadOnlyPaths=/usr/lib/dotnet /usr/lib/x86_64-linux-gnu /usr/lib/x86_64-linux-gnu:/lib/x86_64-linux-gnu /lib64 /usr/lib/ssl -/usr/share/zoneinfo -/usr/share/ca-certificates /etc/ssl /etc/ld.so.cache /etc/resolv.conf /etc/hosts /etc/nsswitch.conf /etc/passwd /etc/group /etc/localtime /etc/os-release
BindPaths=@SITE_ROOT@
MountAPIVFS=yes
ReadWritePaths=@SITE_ROOT@/@PUBLISH_FOLDER@

[Install]
WantedBy=multi-user.target
