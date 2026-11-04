#!/bin/bash
clear
sudo nano /etc/apache2/sites-available/@SITE@.conf

read -r -p "Test config and reload apache? [Y/n]: " _answer
if [[ ! ${_answer} =~ ^[nN] ]]; then
	sudo apache2ctl configtest && sudo systemctl reload apache2
fi
