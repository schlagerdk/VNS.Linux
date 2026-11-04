#!/bin/bash
cd "$(dirname "$0")"
cd ..
clear
sudo -u www-data ./venv/bin/pip install --upgrade -r app/requirements.txt
sudo systemctl restart @SITE@
sudo systemctl status @SITE@
