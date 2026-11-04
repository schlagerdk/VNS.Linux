#!/bin/bash
cd "$(dirname "$0")"
cd ..

_site=@SITE@
_publishFolder=@PUBLISH_FOLDER@

systemctl stop "${_site}"
#dotnet publish -c Release -p:PublishDir="${_publishFolder}" /p:EnvironmentName=Production --self-contained false --nologo
dotnet publish -c Debug -p:PublishDir="${_publishFolder}" /p:EnvironmentName=Test --self-contained false --nologo
echo Delete schema files and bin
rm -f appsettings-schema.json
rm -f appsettings-schema.usync.json
rm -f appsettings-schema.Umbraco.Cms.json
rm -f umbraco-package-schema.json
rm -rf App_Plugins
rm -rf bin
systemctl start "${_site}"
systemctl status "${_site}" --no-pager
