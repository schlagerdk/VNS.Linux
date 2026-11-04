#!/bin/bash
clear

_site=@SITE@

sudo nano "/etc/systemd/system/${_site}.service"

read -r -p "Reload daemon and restart service? [Y/n]: " _answer
if [[ ! ${_answer} =~ ^[nN] ]]; then
	sudo systemctl daemon-reload
	sudo systemctl restart "${_site}"
	sudo systemctl status "${_site}" --no-pager
fi
