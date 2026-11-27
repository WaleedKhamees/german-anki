#!/usr/bin/env bash
# danki installer — sets the whole setup up on a fresh Linux machine. Safe to re-run
# (it never overwrites your config or data; re-running updates packages and links).
#
#   ./install.sh [options]
#     --no-packages        don't install system packages (only report what is missing)
#     --dict-zip FILE      use a local dict-de-de.zip instead of downloading it
#     --skip-browser       don't download Playwright's Chromium (conjugation tables)
#     --skip-whisper       don't build whisper.cpp or download its models (`danki subs`)
#     --migrate-from DIR   copy assets/ and the queued captures from an older setup
#     --config-dir DIR     keep the config (.env) in DIR and link ~/.config/danki
#                          to it. Default: <dotfiles>/.config/danki when this repo lives
#                          inside a dotfiles git repo that has a .config folder
#     --no-config-link     keep ~/.config/danki a plain folder
#     --no-anki-setup      don't create the decks and note type in Anki
#     -y, --yes            don't ask before installing system packages or creating the Anki decks
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
CONFIG_DIR="$CONFIG_HOME/danki"
CONFIG_FILE="${DANKI_CONFIG:-$CONFIG_DIR/.env}"
MPV_SCRIPTS_DIR="$CONFIG_HOME/mpv/scripts"
BIN_DIR="$HOME/.local/bin"
DICT_URL="https://www.reader-dict.com/file/de/dict-de-de.zip"
WHISPER_REPO="https://github.com/ggml-org/whisper.cpp"
WHISPER_REF="v1.9.5"   # a release known to have the VAD options `danki subs` uses
WHISPER_MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q8_0.bin"
WHISPER_VAD_URL="https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v6.2.0.bin"
# the project was called german-anki: install.sh moves an old setup over
OLD_NAME="german-anki"

NO_PACKAGES=0 SKIP_BROWSER=0 SKIP_WHISPER=0 ASSUME_YES=0 DICT_ZIP="" MIGRATE_FROM="" CONFIG_TARGET="" NO_CONFIG_LINK=0 NO_ANKI_SETUP=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --config-dir)   CONFIG_TARGET="$(realpath -m "$2")"; shift ;;
        --no-config-link) NO_CONFIG_LINK=1 ;;
        --no-packages)  NO_PACKAGES=1 ;;
        --skip-browser) SKIP_BROWSER=1 ;;
        --no-anki-setup) NO_ANKI_SETUP=1 ;;
        --dict-zip)     DICT_ZIP="$(realpath "$2")"; shift ;;
        --migrate-from) MIGRATE_FROM="$(realpath "$2")"; shift ;;
        -y|--yes)       ASSUME_YES=1 ;;
        --skip-whisper) SKIP_WHISPER=1 ;;
        -h|--help)      sed -n '2,17p' "$0"; exit 0 ;;
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
    [git]="git git git"
    [cmake]="cmake cmake cmake"
    [make]="make make make"
    [c++]="gcc g++ gcc-c++"
)
pkg_for() {
    local names=(${PKG[$1]})
    case "$PM" in pacman) echo "${names[0]}" ;; apt) echo "${names[1]}" ;; dnf) echo "${names[2]}" ;; esac
}

wanted=(python3 mpv ffmpeg curl notify-send)
if [[ -n "${WAYLAND_DISPLAY:-}" ]]; then wanted+=(wl-paste); else wanted+=(xclip); fi
# building whisper.cpp needs a compiler, unless there is a whisper-cli already
if [[ $SKIP_WHISPER -eq 0 ]] && ! command -v whisper-cli >/dev/null \
   && [[ ! -x "$HOME/.local/share/danki/whisper.cpp/build/bin/whisper-cli" ]]; then
    wanted+=(git cmake make c++)
fi
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

# Default link target: <dotfiles>/.config/danki when this repo is a folder (or submodule)
# of a dotfiles git repo that has a .config folder, like the other stow-style config links.
if [[ -z "$CONFIG_TARGET" && $NO_CONFIG_LINK -eq 0 && -z "${DANKI_CONFIG:-}" ]]; then
    parent="$(dirname "$REPO_DIR")"
    if [[ -d "$parent/.config" ]] && command -v git >/dev/null \
       && [[ "$(git -C "$parent" rev-parse --show-toplevel 2>/dev/null)" == "$parent" ]]; then
        CONFIG_TARGET="$parent/.config/danki"
    fi
fi
[[ -n "${DANKI_CONFIG:-}" || $NO_CONFIG_LINK -eq 1 ]] && CONFIG_TARGET=""

# An old ~/.config/german-anki: move it (or the dotfiles folder it links to) to the new name
OLD_CONFIG_DIR="$CONFIG_HOME/$OLD_NAME"
if [[ -z "${DANKI_CONFIG:-}" ]] && [[ -e "$OLD_CONFIG_DIR" || -L "$OLD_CONFIG_DIR" ]] \
   && [[ ! -e "$CONFIG_DIR" && ! -L "$CONFIG_DIR" ]]; then
    if [[ -L "$OLD_CONFIG_DIR" ]]; then
        old_real="$(realpath -m "$OLD_CONFIG_DIR")"
        rm "$OLD_CONFIG_DIR"
        if [[ -n "$CONFIG_TARGET" && ! -e "$CONFIG_TARGET" && -d "$old_real" ]]; then
            mv "$old_real" "$CONFIG_TARGET"
            info "moved $old_real to $CONFIG_TARGET"
        else
            CONFIG_TARGET="$old_real"           # keep using the folder it pointed to
        fi
    else
        mv "$OLD_CONFIG_DIR" "$CONFIG_DIR"
        info "moved $OLD_CONFIG_DIR to $CONFIG_DIR"
    fi
fi

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

# settings renamed with the project; an old default data folder follows the new name
if grep -qE '^[[:space:]]*(export[[:space:]]+)?GERMAN_ANKI_' "$CONFIG_FILE"; then
    sed -i -E \
        -e 's/^([[:space:]]*(export[[:space:]]+)?)GERMAN_ANKI_/\1DANKI_/' \
        -e "s#^([[:space:]]*DANKI_DATA_DIR[[:space:]]*=[[:space:]]*)(~|$HOME)/\.local/share/$OLD_NAME[[:space:]]*\$#\1~/.local/share/danki#" \
        -e "s#^([[:space:]]*DANKI_BIN[[:space:]]*=[[:space:]]*)(~|$HOME)/\.local/bin/$OLD_NAME[[:space:]]*\$#\1~/.local/bin/danki#" \
        "$CONFIG_FILE"
    info "renamed the GERMAN_ANKI_* settings in $CONFIG_FILE to DANKI_*"
fi

conf_get() {   # conf_get KEY DEFAULT — last KEY=value in the config, ~ expanded
    local v
    v="$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$CONFIG_FILE" | tail -n1 | cut -d= -f2- \
         | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/")" || true
    [[ -z "$v" ]] && v="$2"
    echo "${v/#\~/$HOME}"
}
DATA_DIR="$(conf_get DANKI_DATA_DIR "$HOME/.local/share/danki")"
CLI_LINK="$(conf_get DANKI_BIN "$BIN_DIR/danki")"
VENV="$DATA_DIR/venv"
OLD_DATA_DIR="$HOME/.local/share/$OLD_NAME"
if [[ -d "$OLD_DATA_DIR" && ! -e "$DATA_DIR" ]]; then
    pkill -TERM -f "german_anki.cli serve" 2>/dev/null || true   # the old worker
    mv "$OLD_DATA_DIR" "$DATA_DIR"
    rm -rf "$VENV"   # its scripts point at the old folder; made again below
    info "moved $OLD_DATA_DIR to $DATA_DIR"
fi
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}"
if [[ -d "$CACHE_DIR/$OLD_NAME" && ! -e "$CACHE_DIR/danki" ]]; then mv "$CACHE_DIR/$OLD_NAME" "$CACHE_DIR/danki"; fi
mkdir -p "$DATA_DIR/assets"
info "data folder: $DATA_DIR"

# ---------------------------------------------------------------- 3. Python environment
say "Python environment ($VENV)"
[[ -x "$VENV/bin/python" ]] || python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install -q --upgrade pip
"$VENV/bin/python" -m pip install -q -e "$REPO_DIR"
info "installed danki $("$VENV/bin/python" -c 'import importlib.metadata as m; print(m.version("danki"))')"

if [[ $SKIP_BROWSER -eq 1 ]]; then
    warn "skipped Playwright Chromium (run: $VENV/bin/playwright install chromium)"
else
    info "Playwright Chromium (for conjugation tables)…"
    if [[ "$PM" == apt ]]; then "$VENV/bin/playwright" install --with-deps chromium
    else "$VENV/bin/playwright" install chromium; fi
fi

# links of the old name that point into an install of this project
old_link="$(dirname "$CLI_LINK")/$OLD_NAME"
if [[ -L "$old_link" && "$(readlink "$old_link")" == */venv/bin/$OLD_NAME ]]; then rm "$old_link"; info "removed $old_link"; fi

mkdir -p "$(dirname "$CLI_LINK")"
ln -sfn "$VENV/bin/danki" "$CLI_LINK"
info "command: $CLI_LINK"
case ":$PATH:" in *":$(dirname "$CLI_LINK"):"*) ;; *) warn "$(dirname "$CLI_LINK") is not in your PATH" ;; esac

# bash completion (loaded on first use by the bash-completion package)
COMPLETION_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/bash-completion/completions"
mkdir -p "$COMPLETION_DIR"
old_link="$COMPLETION_DIR/$OLD_NAME"
if [[ -L "$old_link" && "$(readlink "$old_link")" == */completions/$OLD_NAME.bash ]]; then rm "$old_link"; fi
ln -sfn "$REPO_DIR/completions/danki.bash" "$COMPLETION_DIR/danki"
info "bash completion: $COMPLETION_DIR/danki (open a new shell)"

# ---------------------------------------------------------------- 4. dictionary
say "German dictionary"
if [[ -n "$DICT_ZIP" ]]; then "$VENV/bin/danki" setup-dict --zip "$DICT_ZIP"
else info "source: $DICT_URL"; "$VENV/bin/danki" setup-dict; fi

# ---------------------------------------------------------------- 5. whisper.cpp (danki subs)
say "whisper.cpp (subtitles)"
WHISPER_DIR="$DATA_DIR/whisper.cpp"
WHISPER_CLI="$(conf_get WHISPER_CLI "")"
WHISPER_MODEL="$(conf_get WHISPER_MODEL "$WHISPER_DIR/models/ggml-small-q8_0.bin")"
WHISPER_VAD_MODEL="$(conf_get WHISPER_VAD_MODEL "$WHISPER_DIR/models/ggml-silero-v6.2.0.bin")"

build_whisper() {
    local tool
    for tool in git cmake make c++; do
        command -v "$tool" >/dev/null || { warn "missing: $tool (needed to build whisper.cpp)"; return 1; }
    done
    if [[ ! -f "$WHISPER_DIR/CMakeLists.txt" ]]; then
        info "downloading whisper.cpp $WHISPER_REF…"
        # init + fetch instead of clone: the folder may exist already, holding the models
        mkdir -p "$WHISPER_DIR"
        git -C "$WHISPER_DIR" init -q \
            && git -C "$WHISPER_DIR" fetch -q --depth 1 "$WHISPER_REPO" "refs/tags/$WHISPER_REF" \
            && git -C "$WHISPER_DIR" -c advice.detachedHead=false checkout -q FETCH_HEAD || return 1
    fi
    info "building whisper-cli (takes a few minutes)…"
    cmake -S "$WHISPER_DIR" -B "$WHISPER_DIR/build" -DCMAKE_BUILD_TYPE=Release \
        -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=OFF >/dev/null || return 1
    cmake --build "$WHISPER_DIR/build" --config Release --target whisper-cli -j "$(nproc)" >/dev/null || return 1
}

download_model() {   # download_model FILE URL
    [[ -f "$1" ]] && { info "✓ $1"; return 0; }
    info "downloading $(basename "$1")…"
    mkdir -p "$(dirname "$1")"
    curl -fL --progress-bar -o "$1.part" "$2" && mv "$1.part" "$1" || { rm -f "$1.part"; return 1; }
}

if [[ $SKIP_WHISPER -eq 1 ]]; then
    warn "skipped (re-run without --skip-whisper to use danki subs)"
else
    if [[ -n "$WHISPER_CLI" ]]; then
        if command -v "$WHISPER_CLI" >/dev/null; then info "✓ WHISPER_CLI: $WHISPER_CLI"
        else warn "WHISPER_CLI=$WHISPER_CLI is not a program — fix it in $CONFIG_FILE"; fi
    elif [[ -x "$WHISPER_DIR/build/bin/whisper-cli" ]]; then
        info "✓ $WHISPER_DIR/build/bin/whisper-cli"
    elif command -v whisper-cli >/dev/null; then
        info "✓ using whisper-cli on PATH: $(command -v whisper-cli)"
    elif build_whisper; then
        info "✓ built $WHISPER_DIR/build/bin/whisper-cli"
    else
        warn "couldn't build whisper.cpp; danki subs won't work until you re-run install.sh"
    fi
    download_model "$WHISPER_MODEL" "$WHISPER_MODEL_URL" || warn "couldn't download the whisper model"
    download_model "$WHISPER_VAD_MODEL" "$WHISPER_VAD_URL" || warn "couldn't download the VAD model"
fi

# ---------------------------------------------------------------- 6. migrate an older setup
if [[ -n "$MIGRATE_FROM" ]]; then
    say "Migrating data from $MIGRATE_FROM"
    if [[ -d "$MIGRATE_FROM/assets" ]]; then
        cp -n "$MIGRATE_FROM/assets/"* "$DATA_DIR/assets/" 2>/dev/null || true
        info "assets: $(ls "$DATA_DIR/assets" | wc -l) files"
    fi
    if [[ -d "$MIGRATE_FROM/queue" ]]; then
        mkdir -p "$DATA_DIR/queue"
        cp -n "$MIGRATE_FROM/queue/"*.json "$DATA_DIR/queue/" 2>/dev/null || true
        info "queued captures: $(ls "$DATA_DIR/queue" | wc -l)"
    fi
    # pending rows of versions that kept the queue in Markdown (converted below)
    for f in To-do-german-words.md sentence-mining.md; do
        if [[ ! -f "$MIGRATE_FROM/$f" ]]; then continue
        elif [[ -e "$DATA_DIR/$f" || -e "$DATA_DIR/legacy/$f" ]]; then warn "$f was copied before — skipped"
        else cp "$MIGRATE_FROM/$f" "$DATA_DIR/$f"; info "copied $f"; fi
    done
fi

# older versions kept the capture queue in Markdown files: turn them into queue entries
"$VENV/bin/python" -m danki.legacy

# ---------------------------------------------------------------- 7. mpv scripts
say "mpv scripts ($MPV_SCRIPTS_DIR)"
mkdir -p "$MPV_SCRIPTS_DIR"
for old in german-subs german-word-capture; do   # the scripts' names before danki
    link="$MPV_SCRIPTS_DIR/$old"
    if [[ -L "$link" && "$(readlink "$link")" == */mpv/$old ]]; then rm "$link"; info "removed the old $old link"; fi
done
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

# ---------------------------------------------------------------- 8. Anki
say "Anki"
if command -v anki >/dev/null || (command -v flatpak >/dev/null && flatpak info net.ankiweb.Anki >/dev/null 2>&1); then
    info "✓ Anki is installed"
else
    warn "Anki not found — install it: https://apps.ankiweb.net  (Arch: yay -S anki-bin)"
fi
info "Needs the AnkiConnect add-on: Anki → Tools → Add-ons → Get Add-ons → code 2055492159"
if [[ $NO_ANKI_SETUP -eq 1 ]]; then
    warn "skipped the decks and note type (run: danki setup-anki)"
else
    setup_args=(); [[ $ASSUME_YES -eq 1 ]] && setup_args=(--yes)
    setup_status=0
    "$VENV/bin/danki" setup-anki "${setup_args[@]}" || setup_status=$?
    if [[ $setup_status -eq 3 ]]; then
        warn "start Anki with the add-on installed, then run: danki setup-anki"
    fi
fi

# ---------------------------------------------------------------- 9. check
say "Checking the installation (danki doctor)"
"$VENV/bin/danki" doctor || warn "fix the items above, then run: danki doctor"

say "Done"
info "mpv: Ctrl+W captures a word, Alt+C toggles subtitle colours"
info "subtitles for a video: danki subs VIDEO (or just danki subs, to pick files)"
info "settings: $CONFIG_FILE"
