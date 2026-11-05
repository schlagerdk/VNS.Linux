[Unit]
# Author: Martin Schläger (VNS)
Description=Running admin site [@SITE@]
After=network.target

[Service]
# Runs as root on purpose: the site creates, changes and deletes sites and opens shells. It is not sandboxed.
User=root
WorkingDirectory=@SITE_ROOT@/app
ExecStart=@SITE_ROOT@/venv/bin/gunicorn --worker-class gthread --workers 1 --threads 32 --timeout 120 --bind 127.0.0.1:@PORT_HTTP@ app:app

RestartSec=10
Restart=always
# Keep running shells and scripts alive if only this service is restarted
KillMode=process

StandardOutput=journal
StandardError=journal
SyslogIdentifier=@SITE@

[Install]
WantedBy=multi-user.target
