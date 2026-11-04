#! /bin/bash
clear

# Install cmd tools
curl https://packages.microsoft.com/keys/microsoft.asc | sudo tee /etc/apt/trusted.gpg.d/microsoft.asc
curl https://packages.microsoft.com/config/ubuntu/22.04/prod.list | tee /etc/apt/sources.list.d/mssql-release.list
sudo apt-get update
sudo apt-get install mssql-tools18 unixodbc-dev
sudo apt-get update
sudo apt-get install mssql-tools18

# Add paths
echo 'export PATH="$PATH:/opt/mssql-tools18/bin"' >> ~/.bash_profile
source ~/.bash_profile

# Add paths
echo 'export PATH="$PATH:/opt/mssql-tools18/bin"' >> ~/.bashrc
source ~/.bashrc