#!/bin/bash
clear

_service=/etc/systemd/system/@SITE@.service
_env=${1:-}

if [[ ! -f ${_service} ]]; then
	echo "Service file not found: ${_service}" >&2
	exit 1
fi

echo "Current environment:"
grep "_ENVIRONMENT" "${_service}"
echo

if [[ -z ${_env} ]]; then
	echo "1) Development"
	echo "2) Test"
	echo "3) Staging"
	echo "4) Production"
	read -r -p "Choose environment: " _env
fi

case "${_env,,}" in
	1 | d | dev | development) _env=Development ;;
	2 | t | test) _env=Test ;;
	3 | s | stage | staging) _env=Staging ;;
	4 | p | prod | production) _env=Production ;;
	*) echo "Unknown environment: ${_env}" >&2; exit 1 ;;
esac

sudo sed -i -E "s|^(Environment=(ASPNETCORE\|DOTNET)_ENVIRONMENT=).*|\1${_env}|" "${_service}"

echo "Environment is now:"
grep "_ENVIRONMENT" "${_service}"

sudo systemctl daemon-reload
sudo systemctl restart @SITE@
sudo systemctl status @SITE@ --no-pager
