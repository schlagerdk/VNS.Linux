[Unit]
# Author: Martin Schläger (VNS)
Description=Running .NET Core [@SITE@]

[Service]
WorkingDirectory=@SITE_ROOT@/@PUBLISH_FOLDER@
ExecStart=/usr/bin/dotnet @SITE_ROOT@/@PUBLISH_FOLDER@/@SITE@.dll --urls "http://*:@PORT_HTTP@;https://*:@PORT_HTTPS@"

# Restart service after 10 seconds if the dotnet service crashes:
RestartSec=10
Restart=always
KillSignal=SIGINT

# Output to syslog
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=@SITE@

# Variables
Environment=ASPNETCORE_ENVIRONMENT=Test
Environment=DOTNET_ENVIRONMENT=Test

# Protect
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true
NoNewPrivileges=true
ReadWritePaths=@SITE_ROOT@/@PUBLISH_FOLDER@

[Install]
WantedBy=multi-user.target
