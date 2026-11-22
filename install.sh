#!/usr/bin/env bash
# german-anki installer — sets the whole setup up on a fresh Linux machine. Safe to re-run
# (it never overwrites your config or data; re-running updates packages and links).
#
#   ./install.sh [options]
#     --no-packages        don't install system packages (only report what is missing)
#     --dict-zip FILE      use a local dict-de-de.zip instead of downloading it
#     --skip-browser       don't download Playwright's Chromium (conjugation tables)
#     --migrate-from DIR   copy assets/, the to-do/done and sentence lists from an older setup
#     --config-dir DIR     keep the config (.env) in DIR and link ~/.config/german-anki
#                          to it. Default: <dotfiles>/.config/german-anki when this repo lives
#                          inside a dotfiles git repo that has a .config folder
#     --no-config-link     keep ~/.config/german-anki a plain folder
#     -y, --yes            don't ask before installing system packages
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
CONFIG_DIR="$CONFIG_HOME/german-anki"
CONFIG_FILE="${GERMAN_ANKI_CONFIG:-$CONFIG_DIR/.env}"
MPV_SCRIPTS_DIR="$CONFIG_HOME/mpv/scripts"
BIN_DIR="$HOME/.local/bin"
DICT_URL="https://www.reader-dict.com/file/de/dict-de-de.zip"

NO_PACKAGES=0 SKIP_BROWSER=0 ASSUME_YES=0 DICT_ZIP="" MIGRATE_FROM="" CONFIG_TARGET="" NO_CONFIG_LINK=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --config-dir)   CONFIG_TARGET="$(realpath -m "$2")"; shift ;;
        --no-config-link) NO_CONFIG_LINK=1 ;;
        --no-packages)  NO_PACKAGES=1 ;;
        --skip-browser) SKIP_BROWSER=1 ;;
        --dict-zip)     DICT_ZIP="$(realpath "$2")"; shift ;;
        --migrate-from) MIGRATE_FROM="$(realpath "$2")"; shift ;;
        -y|--yes)       ASSUME_YES=1 ;;
        -h|--help)      sed -n '2,15p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

say()  { printf '\n\033[1;34m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 1. system packages
say "Checking system programs"
if   command -v pacman  >/dev/null; then PM=pacman
elif command -v apt-get >/dev/null; then PM=apt
elif command -v dnf     >/dev/null; then PM=dnf
else PM=""; fi

# command -> package name per package manager (pacman apt dnf)
declare -A PKG=(
    [python3]="python python3 python3"
    [mpv]="mpv mpv mpv"
    [ffmpeg]="ffmpeg ffmpeg ffmpeg"
    [curl]="curl curl curl"
    [notify-send]="libnotify libnotify-bin libnotify"
    [xclip]="xclip xclip xclip"
    [wl-paste]="wl-clipboard wl-clipboard wl-clipboard"
)
pkg_for() {
    local names=(${PKG[$1]})
    case "$PM" in pacman) echo "${names[0]}" ;; apt) echo "${names[1]}" ;; dnf) echo "${names[2]}" ;; esac
}

wanted=(python3 mpv ffmpeg curl notify-send)
if [[ -n "${WAYLAND_DISPLAY:-}" ]]; then wanted+=(wl-paste); else wanted+=(xclip); fi
missing_pkgs=()
for cmd in "${wanted[@]}"; do
    if command -v "$cmd" >/dev/null; then info "✓ $cmd"
    else warn "missing: $cmd"; [[ -n "$PM" ]] && missing_pkgs+=("$(pkg_for "$cmd")"); fi
done
# Debian/Ubuntu ship venv separately
if command -v python3 >/dev/null && ! python3 -c "import venv, ensurepip" 2>/dev/null; then
    warn "missing: python3 venv module"; [[ "$PM" == apt ]] && missing_pkgs+=(python3-venv)
fi

if [[ ${#missing_pkgs[@]} -gt 0 ]]; then
    if [[ $NO_PACKAGES -eq 1 || -z "$PM" ]]; then
        warn "install these yourself: ${missing_pkgs[*]}"
    else
        cmd=()
        case "$PM" in
            pacman) cmd=(sudo pacman -S --needed --noconfirm "${missing_pkgs[@]}") ;;
            apt)    cmd=(sudo apt-get install -y "${missing_pkgs[@]}") ;;
            dnf)    cmd=(sudo dnf install -y "${missing_pkgs[@]}") ;;
        esac
        if [[ $ASSUME_YES -eq 0 ]]; then
            read -r -p "    Run: ${cmd[*]} ? [Y/n] " answer
            [[ "${answer:-y}" =~ ^[Yy] ]] || die "cancelled"
        fi
        [[ "$PM" == apt ]] && sudo apt-get update -q
        "${cmd[@]}"
    fi
fi
command -v python3 >/dev/null || die "python3 is required"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "Python 3.10 or newer is required"

# ---------------------------------------------------------------- 2. config
say "Config"

# Default link target: <dotfiles>/.config/german-anki when this repo is a folder (or submodule)
# of a dotfiles git repo that has a .config folder, like the other stow-style config links.
if [[ -z "$CONFIG_TARGET" && $NO_CONFIG_LINK -eq 0 && -z "${GERMAN_ANKI_CONFIG:-}" ]]; then
    parent="$(dirname "$REPO_DIR")"
    if [[ -d "$parent/.config" ]] && command -v git >/dev/null \
       && [[ "$(git -C "$parent" rev-parse --show-toplevel 2>/dev/null)" == "$parent" ]]; then
        CONFIG_TARGET="$parent/.config/german-anki"
    fi
fi
[[ -n "${GERMAN_ANKI_CONFIG:-}" || $NO_CONFIG_LINK -eq 1 ]] && CONFIG_TARGET=""

if [[ -n "$CONFIG_TARGET" ]]; then
    if [[ "$(realpath -m "$CONFIG_DIR")" == "$CONFIG_TARGET" ]]; then
        info "✓ $CONFIG_DIR -> $CONFIG_TARGET"
    else
        mkdir -p "$CONFIG_TARGET"
        if [[ -L "$CONFIG_DIR" ]]; then
            rm "$CONFIG_DIR"                        # a link to somewhere else: re-point it
        elif [[ -d "$CONFIG_DIR" ]]; then           # a plain folder: move its files over
            shopt -s dotglob nullglob
            for f in "$CONFIG_DIR"/*; do
                [[ -e "$CONFIG_TARGET/$(basename "$f")" ]] \
                    && die "$CONFIG_TARGET/$(basename "$f") already exists — merge it with $f by hand, then re-run"
            done
            for f in "$CONFIG_DIR"/*; do mv "$f" "$CONFIG_TARGET/"; done
            shopt -u dotglob nullglob
            rmdir "$CONFIG_DIR"
            info "moved the files of $CONFIG_DIR to $CONFIG_TARGET"
        elif [[ -e "$CONFIG_DIR" ]]; then
            die "$CONFIG_DIR exists and is not a folder"
        fi
        mkdir -p "$(dirname "$CONFIG_DIR")"
        ln -s "$CONFIG_TARGET" "$CONFIG_DIR"
        info "linked $CONFIG_DIR -> $CONFIG_TARGET"
    fi
    # the API key must never end up in git
    if git -C "$CONFIG_TARGET" rev-parse >/dev/null 2>&1 && ! git -C "$CONFIG_TARGET" check-ignore -q .env; then
        echo ".env" >> "$CONFIG_TARGET/.gitignore"
        info "added .env to $CONFIG_TARGET/.gitignore (keeps the API key out of git)"
    fi
fi

mkdir -p "$(dirname "$CONFIG_FILE")"
if [[ -f "$CONFIG_FILE" ]]; then
    info "keeping existing $CONFIG_FILE"
else
    install -m 600 "$REPO_DIR/.env.example" "$CONFIG_FILE"
    info "created $CONFIG_FILE from .env.example"
    warn "put your OPENROUTER_API_KEY into it"
fi

conf_get() {   # conf_get KEY DEFAULT — last KEY=value in the config, ~ expanded
    local v
    v="$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$CONFIG_FILE" | tail -n1 | cut -d= -f2- \
         | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/")" || true
    [[ -z "$v" ]] && v="$2"
    echo "${v/#\~/$HOME}"
}
DATA_DIR="$(conf_get GERMAN_ANKI_DATA_DIR "$HOME/.local/share/german-anki")"
CLI_LINK="$(conf_get GERMAN_ANKI_BIN "$BIN_DIR/german-anki")"
VENV="$DATA_DIR/venv"
mkdir -p "$DATA_DIR/assets"
info "data folder: $DATA_DIR"

# ---------------------------------------------------------------- 3. Python environment
say "Python environment ($VENV)"
[[ -x "$VENV/bin/python" ]] || python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install -q --upgrade pip
"$VENV/bin/python" -m pip install -q -e "$REPO_DIR"
info "installed german-anki $("$VENV/bin/python" -c 'import importlib.metadata as m; print(m.version("german-anki"))')"

if [[ $SKIP_BROWSER -eq 1 ]]; then
    warn "skipped Playwright Chromium (run: $VENV/bin/playwright install chromium)"
else
    info "Playwright Chromium (for conjugation tables)…"
    if [[ "$PM" == apt ]]; then "$VENV/bin/playwright" install --with-deps chromium
    else "$VENV/bin/playwright" install chromium; fi
fi

mkdir -p "$(dirname "$CLI_LINK")"
ln -sfn "$VENV/bin/german-anki" "$CLI_LINK"
info "command: $CLI_LINK"
case ":$PATH:" in *":$(dirname "$CLI_LINK"):"*) ;; *) warn "$(dirname "$CLI_LINK") is not in your PATH" ;; esac

# ---------------------------------------------------------------- 4. dictionary
say "German dictionary"
if [[ -n "$DICT_ZIP" ]]; then "$VENV/bin/german-anki" setup-dict --zip "$DICT_ZIP"
else info "source: $DICT_URL"; "$VENV/bin/german-anki" setup-dict; fi

# ---------------------------------------------------------------- 5. migrate an older setup
if [[ -n "$MIGRATE_FROM" ]]; then
    say "Migrating data from $MIGRATE_FROM"
    if [[ -d "$MIGRATE_FROM/assets" ]]; then
        cp -n "$MIGRATE_FROM/assets/"* "$DATA_DIR/assets/" 2>/dev/null || true
        info "assets: $(ls "$DATA_DIR/assets" | wc -l) files"
    fi
    for f in To-do-german-words.md To-do-german-words-done.md sentence-mining.md sentence-mining-done.md; do
        if [[ ! -f "$MIGRATE_FROM/$f" ]]; then continue
        elif [[ -f "$DATA_DIR/$f" ]]; then warn "$f already exists in $DATA_DIR — not overwritten"
        else cp "$MIGRATE_FROM/$f" "$DATA_DIR/$f"; info "copied $f"; fi
    done
fi

# ---------------------------------------------------------------- 6. mpv scripts
say "mpv scripts ($MPV_SCRIPTS_DIR)"
mkdir -p "$MPV_SCRIPTS_DIR"
backup_dir="$DATA_DIR/backup/$(date +%Y%m%d-%H%M%S)"
for src in "$REPO_DIR"/mpv/*/; do
    name="$(basename "$src")"
    for old in "$MPV_SCRIPTS_DIR/$name" "$MPV_SCRIPTS_DIR/$name.lua"; do
        if [[ -e "$old" && ! -L "$old" ]]; then   # an older copy would load twice
            mkdir -p "$backup_dir"; mv "$old" "$backup_dir/"
            warn "moved old $(basename "$old") to $backup_dir"
        fi
    done
    ln -sfn "${src%/}" "$MPV_SCRIPTS_DIR/$name"
    info "✓ $name"
done

# ---------------------------------------------------------------- 7. Anki
say "Anki"
if command -v anki >/dev/null || (command -v flatpak >/dev/null && flatpak info net.ankiweb.Anki >/dev/null 2>&1); then
    info "✓ Anki is installed"
else
    warn "Anki not found — install it: https://apps.ankiweb.net  (Arch: yay -S anki-bin)"
fi
info "Needs the AnkiConnect add-on: Anki → Tools → Add-ons → Get Add-ons → code 2055492159"
info "Log in to AnkiWeb and sync, so the deck and note type arrive on this machine."

# ---------------------------------------------------------------- 8. check
say "Checking the installation (german-anki doctor)"
"$VENV/bin/german-anki" doctor || warn "fix the items above, then run: german-anki doctor"

say "Done"
info "mpv: Ctrl+W captures a word, Alt+C toggles subtitle colours"
info "settings: $CONFIG_FILE"
