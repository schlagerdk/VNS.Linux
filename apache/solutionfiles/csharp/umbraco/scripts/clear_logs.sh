#!/bin/bash
clear
systemctl stop @SITE@
sudo rm -rf @SITE_ROOT@/@PUBLISH_FOLDER@/umbraco/Logs
systemctl start @SITE@
systemctl status @SITE@
