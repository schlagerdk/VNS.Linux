#!/bin/bash
cd "$(dirname "$0")"
cd ..

_site=@SITE@
_publishFolder=@PUBLISH_FOLDER@

systemctl stop "${_site}"
#dotnet publish -c Release -p:PublishDir="${_publishFolder}" /p:EnvironmentName=Production --self-contained false --nologo
dotnet publish -c Debug -p:PublishDir="${_publishFolder}" /p:EnvironmentName=Test --self-contained false --nologo
rm -rf bin
chown -R www-data:www-data "${_publishFolder}"
systemctl start "${_site}"
systemctl status "${_site}" --no-pager
