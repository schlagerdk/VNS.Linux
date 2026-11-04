#!/bin/bash
clear

_site=@SITE@

sudo systemctl daemon-reload
sudo systemctl restart "${_site}"

sleep 2
sudo systemctl status "${_site}" --no-pager

if systemctl is-active --quiet "${_site}"; then
	echo "OK: ${_site} is running"
else
	echo "FAILED: ${_site} is not running. Last log lines:"
	sudo journalctl -u "${_site}" -n 20 --no-pager
	exit 1
fi
