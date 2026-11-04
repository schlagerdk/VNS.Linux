#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
clear

export DEBIAN_FRONTEND=noninteractive

echo Set your timezone
sudo timedatectl set-timezone Europe/Copenhagen

echo Enable timesyncd
sudo timedatectl set-ntp on

echo Update APT and upgrade all packages
sudo apt-get update
sudo apt-get full-upgrade -y
sudo apt-get autoremove -y
sudo apt-get autoclean

echo Install base packages
sudo apt-get install -y curl git net-tools nano unzip ca-certificates rsync

echo Deploy to /vns
SRC="$(pwd)"
bash ./update.sh "$SRC"
rm -f /vns/.gitignore

# Remove the cloned directory, but only if it is the VNS.Linux clone and not /vns
if [ "$(basename "$SRC")" = "VNS.Linux" ] && [ "$SRC" != "/vns" ]; then
    cd /
    rm -rf "$SRC"
fi

echo "Done. Next steps: /vns/apache/init.sh (apache, acme.sh, .NET 10, python) and /vns/install_nodejs.sh (optional). Update later with /vns/update.sh"
