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
    local revision=""

    if [ -z "$src" ]; then
        tmp="$(mktemp -d)"
        trap "rm -rf '$tmp'" EXIT
        git clone --depth 1 "$REPO_URL" "$tmp/VNS.Linux"
        src="$tmp/VNS.Linux"
    fi
    if git -C "$src" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        revision="$(git -C "$src" rev-parse HEAD)"
        if [ -n "$(git -C "$src" status --porcelain --untracked-files=normal)" ]; then
            echo "WARNING: Source has local changes; installed revision will be unknown." >&2
            revision=""
        fi
    else
        echo "WARNING: Source has no Git metadata; installed revision will be unknown." >&2
    fi

    # If the repository has a vns subdirectory, deploy that directory
    [ -d "$src/vns" ] && src="$src/vns"

    sudo mkdir -p "$TARGET"
    sudo chown "$(id -un):$(id -gn)" "$TARGET"
    # A failed synchronization must not retain a success-shaped version marker.
    printf '\n' > "$TARGET/.vns-version"

    # Save the sa password from the installed copy so it can be restored after synchronization
    local sa_quoted=""
    if [ -f "$TARGET/sql/drop_db.sh" ]; then
        sa_quoted="$(sed -n 's/.* -U sa -P \(.*\) -i scripts\/drop_db.sql$/\1/p' "$TARGET/sql/drop_db.sh")"
        [ "$sa_quoted" = "'@SA_PASSWORD@'" ] && sa_quoted=""
    fi

    # Do not use --delete, so local files (e.g. SQL data) are preserved.
    rsync -a \
        --exclude '.git' \
        --exclude '.DS_Store' \
        --exclude '.gitignore' \
        --exclude '.vns-version' \
        "$src"/ "$TARGET"/

    # Site ports are discovered from active configs now; remove the obsolete counter.
    rm -f "$TARGET/apache/_portnumber.txt"

    # Templates hold no local data, so remove templates that no longer exist in the repository
    if [ -d "$src/apache/solutionfiles" ]; then
        rsync -a --delete "$src/apache/solutionfiles"/ "$TARGET/apache/solutionfiles"/
    fi

    if [ -n "$sa_quoted" ]; then
        source "$TARGET/sql/lib/sa_password.sh"
        sa_apply_quoted "$TARGET/sql" "$sa_quoted"
    fi

    find "$TARGET" -name '*.sh' -exec chmod +x {} +
    printf '%s\n' "$revision" > "$TARGET/.vns-version"
    echo "VNS updated in $TARGET"
}

main "$@"
exit $?
