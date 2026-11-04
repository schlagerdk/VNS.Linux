#!/bin/bash
# Opdaterer /vns fra https://github.com/schlagerdk/VNS.Linux
# Brug: update.sh            (kloner repo til en midlertidig mappe)
#       update.sh <kildemappe> (bruger en eksisterende kopi, bruges af init.sh)
set -euo pipefail

REPO_URL="https://github.com/schlagerdk/VNS.Linux.git"
TARGET="/vns"

main() {
    local src="${1:-}"
    local tmp=""

    if [ -z "$src" ]; then
        tmp="$(mktemp -d)"
        trap '[ -n "$tmp" ] && rm -rf "$tmp"' EXIT
        git clone --depth 1 "$REPO_URL" "$tmp/VNS.Linux"
        src="$tmp/VNS.Linux"
    fi

    # Hvis repoet har en vns-undermappe, er det den der skal deployes
    [ -d "$src/vns" ] && src="$src/vns"

    sudo mkdir -p "$TARGET"
    sudo chown "$(id -un):$(id -gn)" "$TARGET"

    # Ingen --delete, så lokale filer (fx sql-data) bevares. _portnumber.txt rører vi aldrig.
    rsync -a \
        --exclude '.git' \
        --exclude '.DS_Store' \
        --exclude '_portnumber.txt' \
        "$src"/ "$TARGET"/

    find "$TARGET" -name '*.sh' -exec chmod +x {} +
    echo "VNS opdateret i $TARGET"
}

main "$@"
exit $?
