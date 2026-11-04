#!/bin/bash
clear

/etc/acmesh/acme.sh --issue --apache --server letsencrypt --force -d @SITE@ -w @DOC_ROOT@

echo  Reload apache
sudo systemctl reload apache2
