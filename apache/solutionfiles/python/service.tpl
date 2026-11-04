[Unit]
# Author: Martin Schläger (VNS)
Description=Running Python site [@SITE@]
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=@SITE_ROOT@/app
ExecStart=@SITE_ROOT@/venv/bin/gunicorn --bind 127.0.0.1:@PORT_HTTP@ app:app

RestartSec=10
Restart=always

StandardOutput=journal
StandardError=journal
SyslogIdentifier=@SITE@

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
BindReadOnlyPaths=/usr -/bin -/sbin -/lib -/lib64 /etc/ssl -/etc/ca-certificates /etc/ld.so.cache /etc/resolv.conf /etc/hosts /etc/nsswitch.conf /etc/passwd /etc/group /etc/localtime -/etc/os-release -/etc/host.conf
BindPaths=@SITE_ROOT@
MountAPIVFS=yes

[Install]
WantedBy=multi-user.target
