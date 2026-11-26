# AGENTS.md

Guidance for AI coding agents working in this repository. For users: [README.md](README.md) and
[docs/GUIDE.md](docs/GUIDE.md).

## What this is

German vocabulary mining for Linux: mpv scripts capture words and sentences from subtitles, a
Python CLI turns them into Anki cards through AnkiConnect, and the subtitles are coloured by what
is already in the deck.

## Layout

| Path | Role |
|---|---|
| `german_anki/cli.py` | argparse entry point; heavy modules are imported lazily |
| `german_anki/cards.py` | word cards: puts the parts below into a note; clipboard run, `add-row`, `regen` |
| `german_anki/llm.py` | definition and examples from OpenRouter; the prompt and the per-word cache |
| `german_anki/examples.py` | example length limits, Tatoeba lookup |
| `german_anki/dwds.py` | article, verb/noun status and pronunciation from DWDS |
| `german_anki/verbformen.py` | conjugation table screenshot; one shared browser per run |
| `german_anki/media.py` | captured screenshot/audio from `<data>/assets` → Anki (words and sentences) |
| `german_anki/sentences.py` | sentence cards (two notes per pair) |
| `german_anki/sync.py` | exports the deck to `anki-words.json` for the subtitle colours |
| `german_anki/dictionary.py` | builds `dict.sqlite` and `wordlist.txt` from reader.dict |
| `german_anki/normalize.py` | Word field → lookup key |
| `german_anki/anki.py` | AnkiConnect client; note field and tag names |
| `german_anki/anki_setup.py` | `setup-anki`: creates missing decks and the note type, holds its templates |
| `german_anki/config.py`, `doctor.py` | settings; installation check |
| `mpv/german-word-capture/main.lua` | capture panel, screenshot, audio clip, queue into the CLI |
| `mpv/german-subs/main.lua` | subtitle colouring; `ignore.txt` lists words never coloured |
| `install.sh`, `uninstall.sh`, `completions/` | setup, removal, bash completion |

Data flow: mpv → row in `To-do-german-words.md` → `german-anki add-row` → AnkiConnect. mpv then
sends `german-subs-refresh`, which runs `german-anki sync` and reloads `anki-words.json`.

## Commands

```sh
~/.local/share/german-anki/venv/bin/pip install -e '.[test]'   # once
~/.local/share/german-anki/venv/bin/python -m pytest -q
bash -n install.sh && bash -n uninstall.sh
luajit -bl mpv/german-subs/main.lua >/dev/null                 # Lua syntax check; same for the other script
```

The tests are offline and cover pure functions. There are no automated tests for the Lua scripts,
the network code or the real Anki calls: say so rather than claiming those were tested.

## Safety

- **The install is live.** The venv is editable and `~/.config/mpv/scripts/` links to `mpv/`, so
  edits affect the user's running setup immediately.
- **AnkiConnect is the user's real collection.** Reading is fine. Don't run `german-anki` with no
  arguments (it makes cards from the clipboard), `words`, `add-row`, `add-sentence`, `sentences`,
  `regen`, `backfill-tags` or `setup-anki` unless asked. `regen --dry-run`, `doctor` and `paths` are safe.
- Captures and `regen` cost OpenRouter credit. Keep the verbformen rate limit.
- Never print or commit the API key in `~/.config/german-anki/.env`.
- Try installer changes on a copy of the repo with a throwaway `HOME`, with the `XDG_*` variables
  and `GERMAN_ANKI_CONFIG` unset. `--no-packages --skip-browser` keeps it quick. Its `doctor` step
  still reaches the real Anki, read-only.

## Things that must stay in step

- **CLI subcommands and `regen` options:** `cli.py` ↔ `completions/german-anki.bash` (a test checks
  this) ↔ README.
- **Settings:** `.env.example` ↔ `config.DEFAULTS` (Python settings) ↔ `conf.*` defaults in the Lua
  scripts. `install.sh` and `uninstall.sh` read `GERMAN_ANKI_DATA_DIR` and `GERMAN_ANKI_BIN` too.
- **The `.env` parser exists five times:** `config.py`, `config_reader()` in both Lua scripts, and
  `conf_get` in both shell scripts.
- **Note fields:** `REQUIRED_FIELDS` in `anki.py` ↔ README ↔ the templates in `anki_setup.py`
  (a test checks these). Use the constants, not literal names. `Word` must stay the first field.
- **CLI output parsed by Lua:** `STATUS:` and `RESULT:` lines from `add-row`/`add-sentence`; output
  starting with `unchanged` and exit codes 0/2/3 from `sync`.
- **`anki-words.json`:** bump `FORMS_VERSION` when `build_forms` changes. Bump `DB_VERSION` in
  `dictionary.py` when the database layout changes.
- **`wordlist.txt`** must stay lowercase, LF-terminated and sorted by UTF-8 bytes: Lua
  binary-searches it.

## Behaviour to preserve

- `sync` runs every few seconds during playback. Keep its fast path cheap, and don't import
  `cards.py`, `verbformen.py` or `media.py` (Playwright, Pillow) from it.
- `regen` updates notes in place so review history survives, and never touches `Screenshot` or
  `SourceAudio`.
- Articles and verb/noun status come from DWDS first. The LLM's article only chooses between
  DWDS entries.
- A failed capture keeps its row in the to-do file; nothing captured may be lost.
- `setup-anki` only adds what is missing, asks first, and never changes an existing note type or
  deck. Creating things before a first AnkiWeb sync can force an upload/download choice.
- Re-running `install.sh` never overwrites config or data. `uninstall.sh --purge` never deletes
  files behind a symlinked config folder.
- The subtitle handshake (`german-subs-suspend` / `-suspended` / `-resume`) lets screenshots
  include mpv's own subtitle.

## Style

- Python 3.10+, standard library where practical; explicit `encoding="utf-8"` for files; write
  shared files atomically (`_write_atomic`).
- Lua must run in mpv (LuaJIT / Lua 5.1–5.2). `lower()` handles umlauts because `string.lower`
  doesn't.
- Comments explain why, briefly. Documentation is in plain English.
- Add a test in `tests/test_core.py` for new pure logic.

## Known limits

Linux only: `notify-send`, `/tmp` paths, `mkdir -p` and `HOME` in Lua, symlinks and Bash installers.
