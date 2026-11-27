"""`danki doctor`: check everything the setup needs and say how to fix what's missing."""
import os
import shutil
import sys
from pathlib import Path

from . import config, dictionary
from .anki import Anki, REQUIRED_FIELDS

MPV_SCRIPTS = ["danki-capture", "danki-subs"]
OK, BAD, WARN = "\033[32m✓\033[0m", "\033[31m✗\033[0m", "\033[33m!\033[0m"


def run():
    s = config.load()
    problems = 0

    def check(ok, label, fix="", warn_only=False):
        nonlocal problems
        mark = OK if ok else (WARN if warn_only else BAD)
        print(f" {mark} {label}" + ("" if ok or not fix else f"\n     → {fix}"))
        if not ok and not warn_only:
            problems += 1

    print("config")
    check(config.CONFIG_PATH.exists(), f"config file {config.CONFIG_PATH}",
          "run install.sh (it copies .env.example there)")
    check(bool(s.api_key), "OPENROUTER_API_KEY is set", f"put your key into {config.CONFIG_PATH}")

    print("programs")
    for prog, fix in [("mpv", "install mpv"), ("ffmpeg", "install ffmpeg (audio clips)"),
                      ("notify-send", "install libnotify (desktop notifications)")]:
        check(shutil.which(prog) is not None, prog, fix)
    clip = shutil.which("xclip") or shutil.which("xsel") or shutil.which("wl-paste")
    check(bool(clip), "clipboard tool (xclip / xsel / wl-clipboard)",
          "install xclip (X11) or wl-clipboard (Wayland) — only needed for `danki words`")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser_ok = Path(p.chromium.executable_path).exists()
    except Exception:
        browser_ok = False
    check(browser_ok, "Playwright Chromium (conjugation tables)",
          f"{Path(sys.executable).parent / 'playwright'} install chromium")

    print("files")
    check(dictionary.is_built(s.dict_dir), f"dictionary in {s.dict_dir}", "danki setup-dict")
    scripts_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mpv" / "scripts"
    for name in MPV_SCRIPTS:
        check((scripts_dir / name / "main.lua").exists(), f"mpv script {scripts_dir / name}", "run install.sh")

    print("subtitles (danki subs, optional)")
    check(bool(s.whisper_cli), f"whisper-cli ({s.whisper_cli or 'not found'})",
          "run install.sh (it builds whisper.cpp), or set WHISPER_CLI", warn_only=True)
    check(s.whisper_model.is_file(), f"whisper model {s.whisper_model}",
          "run install.sh (it downloads it), or set WHISPER_MODEL", warn_only=True)
    check(s.whisper_vad_model.is_file(), f"VAD model {s.whisper_vad_model}",
          "run install.sh (it downloads it), or set WHISPER_VAD_MODEL; without it subtitles drift "
          "during music", warn_only=True)

    print("anki")
    anki = Anki(s.anki_url)
    try:
        version = anki("version", timeout=3)
        check(True, f"AnkiConnect reachable at {s.anki_url} (API v{version})")
    except Exception as e:
        check(False, f"AnkiConnect reachable at {s.anki_url} ({e})",
              "start Anki; install the AnkiConnect add-on (Tools → Add-ons → Get Add-ons → 2055492159)")
        print(f"\n{problems} problem(s)" if problems else "")
        return 1
    decks = anki("deckNames")
    check(s.deck in decks, f"deck '{s.deck}' exists",
          "danki setup-anki (or sync from AnkiWeb, or set ANKI_DECK)")
    check(s.sentence_deck in decks, f"sentence deck '{s.sentence_deck}' exists",
          "danki setup-anki, or set ANKI_SENTENCE_DECK (only needed for sentence cards)",
          warn_only=True)
    if s.note_type in anki("modelNames"):
        fields = anki("modelFieldNames", modelName=s.note_type)
        missing = [f for f in REQUIRED_FIELDS if f not in fields]
        check(not missing, f"note type '{s.note_type}' has the fields {', '.join(REQUIRED_FIELDS)}",
              f"add the missing field(s) in Anki: {', '.join(missing)}")
    else:
        check(False, f"note type '{s.note_type}' exists",
              "danki setup-anki (or sync from AnkiWeb, or set ANKI_NOTE_TYPE)")

    print(f"\n{problems} problem(s) found" if problems else "\neverything looks good")
    return 1 if problems else 0
