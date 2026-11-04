#!/bin/bash
# Updates /vns from https://github.com/schlagerdk/VNS.Linux
# Usage: update.sh              (clones the repository to a temporary directory)
#        update.sh <source-dir>  (uses an existing copy, used by init.sh)
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

    # If the repository has a vns subdirectory, deploy that directory
    [ -d "$src/vns" ] && src="$src/vns"

    sudo mkdir -p "$TARGET"
    sudo chown "$(id -un):$(id -gn)" "$TARGET"

    # Save the sa password from the installed copy so it can be restored after synchronization
    local sa_quoted=""
    if [ -f "$TARGET/sql/drop_db.sh" ]; then
        sa_quoted="$(sed -n 's/.* -U sa -P \(.*\) -i scripts\/drop_db.sql$/\1/p' "$TARGET/sql/drop_db.sh")"
        [ "$sa_quoted" = "'@SA_PASSWORD@'" ] && sa_quoted=""
    fi

    # Do not use --delete, so local files (e.g. SQL data) are preserved. _portnumber.txt is never overwritten.
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

    # Copy the initial value only if the file does not already exist, so existing port numbers are never overwritten
    if [ -f "$src/apache/_portnumber.txt" ] && [ ! -e "$TARGET/apache/_portnumber.txt" ]; then
        mkdir -p "$TARGET/apache"
        cp "$src/apache/_portnumber.txt" "$TARGET/apache/_portnumber.txt"
    fi

    find "$TARGET" -name '*.sh' -exec chmod +x {} +
    echo "VNS updated in $TARGET"
}

main "$@"
exit $?
