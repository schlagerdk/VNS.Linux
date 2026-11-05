#!/bin/bash
# Change the login password of this admin site. Signs out all current sessions.
set -euo pipefail
cd "@SITE_ROOT@/app"
sudo "@SITE_ROOT@/venv/bin/python" manage.py set-password
sudo systemctl restart "@SITE@"
echo "Password changed. Log in again."
