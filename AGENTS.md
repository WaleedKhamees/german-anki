# AGENTS.md

Guidance for AI coding agents working in this repository. For users: [README.md](README.md) and
[docs/GUIDE.md](docs/GUIDE.md).

## What this is

danki: German vocabulary and sentence mining for Linux. mpv scripts capture words and sentences
from subtitles, a Python CLI turns them into Anki cards through AnkiConnect, and the subtitles are
coloured by what is already in the deck. `danki subs` writes subtitles for videos that have none,
with whisper.cpp. The project used to be called german-anki; `install.sh` moves an old setup over
(`OLD_NAME`).

## Layout

| Path | Role |
|---|---|
| `danki/cli.py` | argparse entry point; heavy modules are imported lazily |
| `danki/cards.py` | word cards: puts the parts below into a note; clipboard run, captured words, `regen` |
| `danki/llm.py` | definition and examples from OpenRouter; the prompt and the per-word cache |
| `danki/examples.py` | example length limits, Tatoeba lookup |
| `danki/dwds.py` | article, verb/noun status and pronunciation from DWDS |
| `danki/verbformen.py` | conjugation table screenshot; one shared browser per run |
| `danki/server.py` | `serve`: background worker for `add-capture` (warm imports and browser); `add-capture` is its client |
| `danki/media.py` | captured screenshot/audio from `<data>/assets` → Anki (words and sentences) |
| `danki/sentences.py` | sentence cards (two notes per pair); `sentences`: `<German> - <English>` lines typed by hand |
| `danki/queue.py` | captures waiting for Anki (`<data>/queue/<id>.json`); `add-capture`, `retry`, `clean-assets` |
| `danki/legacy.py` | one-time conversion of the Markdown to-do files of older versions into the queue |
| `danki/files.py` | `write_atomic`, for every file another process may read |
| `danki/sync.py` | exports the deck to `anki-words.json` for the subtitle colours |
| `danki/dictionary.py` | builds `dict.sqlite` and `wordlist.txt` from reader.dict |
| `danki/normalize.py` | Word field → lookup key |
| `danki/anki.py` | AnkiConnect client; note field and tag names |
| `danki/anki_setup.py` | `setup-anki`: creates missing decks and the note type, holds its templates |
| `danki/subtitles.py` | `subs`: ffmpeg → whisper-cli (VAD) → overlong cues shortened → gaps re-transcribed without VAD |
| `danki/config.py`, `doctor.py` | settings; installation check |
| `mpv/danki-capture/main.lua` | capture panel, screenshot, audio clip, queue into the CLI |
| `mpv/danki-subs/main.lua` | subtitle colouring; `ignore.txt` lists words never coloured |
| `install.sh`, `uninstall.sh`, `completions/` | setup, removal, bash completion |

Data flow: mpv → screenshot and audio in `<data>/assets`, capture in `<data>/queue/<id>.json` →
`danki add-capture <file>` → (Unix socket → the
`danki serve` worker, started on demand; in-process if that fails) → AnkiConnect. mpv then
sends `danki-subs-refresh`, which runs `danki sync` and reloads `anki-words.json`.

## Commands

```sh
~/.local/share/danki/venv/bin/pip install -e '.[test]'   # once
~/.local/share/danki/venv/bin/python -m pytest -q
bash -n install.sh && bash -n uninstall.sh
luajit -bl mpv/danki-subs/main.lua >/dev/null                 # Lua syntax check; same for the other script
```

The tests are offline and cover pure functions. There are no automated tests for the Lua scripts,
the network code or the real Anki calls: say so rather than claiming those were tested.

## Safety

- **The install is live.** The venv is editable and `~/.config/mpv/scripts/` links to `mpv/`, so
  edits affect the user's running setup immediately.
- **AnkiConnect is the user's real collection.** Reading is fine. Don't run `danki` with no
  arguments (it makes cards from the clipboard), `words`, `sentences`, `add-capture`, `retry`, `clean-assets`,
  `regen`, `backfill-tags` or `setup-anki` unless asked. `regen --dry-run`, `retry --list`,
  `clean-assets --dry-run`, `doctor` and `paths` are safe.
- `subs` is local and free, but slow and CPU-heavy; test it on a short clip. It skips existing
  `.srt` files unless `--overwrite` is given.
- Captures and `regen` cost OpenRouter credit. Keep the verbformen rate limit.
- Never print or commit the API key in `~/.config/danki/.env`.
- Try installer changes on a copy of the repo with a throwaway `HOME`, with the `XDG_*` variables
  and `DANKI_CONFIG` unset. `--no-packages --skip-browser` keeps it quick. Its `doctor` step
  still reaches the real Anki, read-only.

## Things that must stay in step

- **CLI subcommands and `regen` options:** `cli.py` ↔ `completions/danki.bash` (a test checks
  this) ↔ README.
- **Settings:** `.env.example` ↔ `config.DEFAULTS` (Python settings) ↔ `conf.*` defaults in the Lua
  scripts. `install.sh` and `uninstall.sh` read `DANKI_DATA_DIR` and `DANKI_BIN` too.
- **The `.env` parser exists five times:** `config.py`, `config_reader()` in both Lua scripts, and
  `conf_get` in both shell scripts.
- **Note fields:** `REQUIRED_FIELDS` in `anki.py` ↔ README ↔ the templates in `anki_setup.py`
  (a test checks these). Use the constants, not literal names. `Word` must stay the first field.
- **CLI output parsed by Lua:** `STATUS:` and `RESULT:` lines from `add-capture`; output
  starting with `unchanged` and exit codes 0/2/3 from `sync`.
- **`anki-words.json`:** bump `FORMS_VERSION` when `build_forms` changes. Bump `DB_VERSION` in
  `dictionary.py` when the database layout changes.
- **`wordlist.txt`** must stay lowercase, LF-terminated and sorted by UTF-8 bytes: Lua
  binary-searches it.

## Behaviour to preserve

- The `add-capture` worker must print exactly what an in-process run prints, with the same exit
  status. It restarts by itself when the config file or a module changes, and stops after 15 idle
  minutes. Logs: `~/.cache/danki/worker.log`.

- `sync` runs every few seconds during playback. Keep its fast path cheap, and don't import
  `cards.py`, `verbformen.py` or `media.py` (Playwright, Pillow) from it.
- `regen` updates notes in place so review history survives, and never touches `Screenshot` or
  `SourceAudio`.
- Articles and verb/noun status come from DWDS first. The LLM's article only chooses between
  DWDS entries.
- A failed capture stays in the queue with its media; nothing captured may be lost. Only a
  capture whose card is in Anki is deleted, with its two asset files. The capture script names
  media `<slug>-<id>` and never reuses a name: storing a known name again overwrites that file
  in Anki, under the card that already uses it.
- `setup-anki` only adds what is missing, asks first, and never changes an existing note type or
  deck. Creating things before a first AnkiWeb sync can force an upload/download choice.
- Re-running `install.sh` never overwrites config or data. `uninstall.sh --purge` never deletes
  files behind a symlinked config folder.
- The subtitle handshake (`danki-subs-suspend` / `-suspended` / `-resume`) lets screenshots
  include mpv's own subtitle.

## Style

- Python 3.10+, standard library where practical; explicit `encoding="utf-8"` for files; write
  shared files atomically (`files.write_atomic`).
- Lua must run in mpv (LuaJIT / Lua 5.1–5.2). `lower()` handles umlauts because `string.lower`
  doesn't.
- Comments explain why, briefly. Documentation is in plain English.
- Add a test in `tests/test_core.py` for new pure logic.

## Known limits

Linux only: `notify-send`, `/tmp` paths, `mkdir -p` and `HOME` in Lua, symlinks and Bash installers.
