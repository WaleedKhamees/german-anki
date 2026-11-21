#!/usr/bin/env bash
# Remove what install.sh set up. Keeps your config and data unless --purge is given.
#   ./uninstall.sh [--purge]
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
CONFIG_FILE="${GERMAN_ANKI_CONFIG:-$CONFIG_HOME/german-anki/.env}"
PURGE=0; [[ "${1:-}" == "--purge" ]] && PURGE=1

conf_get() {
    local v=""
    [[ -f "$CONFIG_FILE" ]] && v="$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$CONFIG_FILE" | tail -n1 | cut -d= -f2- | xargs)" || true
    [[ -z "$v" ]] && v="$2"
    echo "${v/#\~/$HOME}"
}
DATA_DIR="$(conf_get GERMAN_ANKI_DATA_DIR "$HOME/.local/share/german-anki")"
CLI_LINK="$(conf_get GERMAN_ANKI_BIN "$HOME/.local/bin/german-anki")"

for src in "$REPO_DIR"/mpv/*/; do
    link="$CONFIG_HOME/mpv/scripts/$(basename "$src")"
    if [[ -L "$link" && "$(readlink "$link")" == "${src%/}" ]]; then rm "$link"; echo "removed $link"; fi
done
if [[ -L "$CLI_LINK" ]]; then rm "$CLI_LINK"; echo "removed $CLI_LINK"; fi
if [[ -d "$DATA_DIR/venv" ]]; then rm -rf "$DATA_DIR/venv"; echo "removed $DATA_DIR/venv"; fi

if [[ $PURGE -eq 1 ]]; then
    echo
    echo "This deletes your settings and ALL captured data (to-do/done lists, screenshots, audio):"
    echo "  $(dirname "$CONFIG_FILE")"
    echo "  $DATA_DIR"
    read -r -p "Type 'delete' to confirm: " answer
    if [[ "$answer" == "delete" ]]; then
        rm -rf "$(dirname "$CONFIG_FILE")" "$DATA_DIR" "${XDG_CACHE_HOME:-$HOME/.cache}/german-anki"
        echo "deleted"
    else
        echo "kept"
    fi
else
    echo "kept settings ($(dirname "$CONFIG_FILE")) and data ($DATA_DIR) — use --purge to delete them"
fi
