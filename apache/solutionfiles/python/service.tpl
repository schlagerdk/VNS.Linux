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

# Protect
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
