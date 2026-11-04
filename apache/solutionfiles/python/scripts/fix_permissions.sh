#!/bin/bash
clear

_app=@SITE_ROOT@/app

if [[ ! -d ${_app} ]]; then
	echo "Folder not found: ${_app}" >&2
	exit 1
fi

sudo chown -R www-data:www-data "${_app}"
sudo find "${_app}" -type d -exec chmod 755 {} \;
sudo find "${_app}" -type f -exec chmod 644 {} \;

echo "Permissions fixed on ${_app}"
