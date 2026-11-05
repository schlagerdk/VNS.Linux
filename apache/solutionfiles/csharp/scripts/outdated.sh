#!/bin/bash
cd "$(dirname "$0")"
cd ..
clear

# Check if dotnet is installed
if ! command -v dotnet &> /dev/null
then
    echo "dotnet SDK not found. Please install it first."
    exit 1
fi

# dotnet-outdated is an apphost that needs DOTNET_ROOT when .NET is not in /usr/share/dotnet
if [[ -z ${DOTNET_ROOT:-} ]]; then
    DOTNET_ROOT=$(dirname "$(readlink -f "$(command -v dotnet)")")
    export DOTNET_ROOT
fi

echo "Running dotnet outdated..."
dotnet outdated

echo
read -p "Do you want to upgrade the packages? (y/n): " answer

if [[ "$answer" == "y" || "$answer" == "Y" ]]; then
    echo "Running dotnet outdated --upgrade..."
    dotnet outdated --upgrade
    echo "Upgrade completed."
else
    echo "No upgrade performed."
fi

echo
echo "Done."
