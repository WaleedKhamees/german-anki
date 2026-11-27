#!/usr/bin/env bash
# Remove what install.sh set up. Keeps your config and data unless --purge is given.
#   ./uninstall.sh [--purge]
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
CONFIG_FILE="${DANKI_CONFIG:-$CONFIG_HOME/danki/.env}"
PURGE=0; [[ "${1:-}" == "--purge" ]] && PURGE=1

conf_get() {
    local v=""
    [[ -f "$CONFIG_FILE" ]] && v="$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$CONFIG_FILE" | tail -n1 | cut -d= -f2- | xargs)" || true
    [[ -z "$v" ]] && v="$2"
    echo "${v/#\~/$HOME}"
}
DATA_DIR="$(conf_get DANKI_DATA_DIR "$HOME/.local/share/danki")"
CLI_LINK="$(conf_get DANKI_BIN "$HOME/.local/bin/danki")"

# stop the background worker of `danki add-row` (it runs from the venv removed below)
pkill -TERM -f "danki.cli serve" 2>/dev/null && echo "stopped the background worker" || true

for src in "$REPO_DIR"/mpv/*/; do
    link="$CONFIG_HOME/mpv/scripts/$(basename "$src")"
    if [[ -L "$link" && "$(readlink "$link")" == "${src%/}" ]]; then rm "$link"; echo "removed $link"; fi
done
completion="${XDG_DATA_HOME:-$HOME/.local/share}/bash-completion/completions/danki"
if [[ -L "$completion" && "$(readlink "$completion")" == "$REPO_DIR/completions/danki.bash" ]]; then
    rm "$completion"; echo "removed $completion"
fi
if [[ -L "$CLI_LINK" ]]; then rm "$CLI_LINK"; echo "removed $CLI_LINK"; fi
if [[ -d "$DATA_DIR/venv" ]]; then rm -rf "$DATA_DIR/venv"; echo "removed $DATA_DIR/venv"; fi

if [[ $PURGE -eq 1 ]]; then
    echo
    echo "This deletes your settings and ALL captured data (queued captures, screenshots, audio):"
    echo "  $(dirname "$CONFIG_FILE")"
    echo "  $DATA_DIR"
    read -r -p "Type 'delete' to confirm: " answer
    if [[ "$answer" == "delete" ]]; then
        config_dir="$(dirname "$CONFIG_FILE")"
        if [[ -L "$config_dir" ]]; then
            # a link into your dotfiles: remove only the link, never the files in the repo
            echo "removed the link $config_dir (kept $(readlink -f "$config_dir"))"
            rm "$config_dir"
        else
            rm -rf "$config_dir"
        fi
        rm -rf "$DATA_DIR" "${XDG_CACHE_HOME:-$HOME/.cache}/danki"
        echo "deleted"
    else
        echo "kept"
    fi
else
    echo "kept settings ($(dirname "$CONFIG_FILE")) and data ($DATA_DIR) — use --purge to delete them"
fi
