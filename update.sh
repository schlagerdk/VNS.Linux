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
        trap "rm -rf '$tmp'" EXIT
        git clone --depth 1 "$REPO_URL" "$tmp/VNS.Linux"
        src="$tmp/VNS.Linux"
    fi

    # Hvis repoet har en vns-undermappe, er det den der skal deployes
    [ -d "$src/vns" ] && src="$src/vns"

    sudo mkdir -p "$TARGET"
    sudo chown "$(id -un):$(id -gn)" "$TARGET"

    # Gem sa-adgangskoden fra den installerede kopi, så den kan sættes på igen efter synkronisering
    local sa_quoted=""
    if [ -f "$TARGET/sql/list_dbs.sh" ]; then
        sa_quoted="$(sed -n 's/.* -U sa -P \(.*\) -Q "sp_databases"$/\1/p' "$TARGET/sql/list_dbs.sh")"
        [ "$sa_quoted" = "'@SA_PASSWORD@'" ] && sa_quoted=""
    fi

    # Ingen --delete, så lokale filer (fx sql-data) bevares. _portnumber.txt overskrives aldrig.
    rsync -a \
        --exclude '.git' \
        --exclude '.DS_Store' \
        --exclude '.gitignore' \
        --exclude '_portnumber.txt' \
        "$src"/ "$TARGET"/

    if [ -n "$sa_quoted" ]; then
        source "$TARGET/sql/lib/sa_password.sh"
        sa_apply_quoted "$TARGET/sql" "$sa_quoted"
    fi

    # Startværdi kopieres kun hvis filen ikke findes i forvejen, så eksisterende portnumre aldrig overskrives
    if [ -f "$src/apache/_portnumber.txt" ] && [ ! -e "$TARGET/apache/_portnumber.txt" ]; then
        mkdir -p "$TARGET/apache"
        cp "$src/apache/_portnumber.txt" "$TARGET/apache/_portnumber.txt"
    fi

    find "$TARGET" -name '*.sh' -exec chmod +x {} +
    echo "VNS opdateret i $TARGET"
}

main "$@"
exit $?
