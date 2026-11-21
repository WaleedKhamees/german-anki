# german-anki

German vocabulary mining with mpv and Anki:

- **Capture** (`Ctrl+W` in mpv): pick a word from the current subtitle; a screenshot and
  the audio of the line are saved and the word is sent to Anki in the background.
- **Cards** (`german-anki`): definition and examples (OpenRouter LLM + Tatoeba), a
  conjugation table (verbformen), the captured screenshot/audio, a DWDS pronunciation.
  Tags `no-screenshot`, `no-audio`, `no-pronunciation` mark what's missing.
- **Subtitle colours** (mpv): white = in Anki and complete, yellow = in Anki but missing
  screenshot/audio, red = German word not in Anki, gray = not a dictionary word.
  `Alt+C` toggles. Inflected forms and split separable verbs are recognised.

## Install

```sh
git clone <this repo> && cd german-anki
./install.sh
```

The installer (Arch/Artix, Debian/Ubuntu, Fedora; safe to re-run):

1. installs missing system programs (`mpv ffmpeg python3 curl libnotify xclip|wl-clipboard`),
2. copies `.env.example` to `~/.config/german-anki/.env` (only if it doesn't exist),
3. creates a Python venv in `~/.local/share/german-anki/venv` and installs this package
   plus Playwright's Chromium, and links `~/.local/bin/german-anki`,
4. downloads the dictionary from <https://www.reader-dict.com/file/de/dict-de-de.zip>
   and builds `~/.local/share/german-anki/dict/`,
5. links the two mpv scripts into `~/.config/mpv/scripts/`,
6. runs `german-anki doctor`.

Then:

- put your `OPENROUTER_API_KEY` into `~/.config/german-anki/.env`,
- in Anki install **AnkiConnect** (Tools → Add-ons → Get Add-ons → `2055492159`) and sync
  from AnkiWeb so the deck and the note type exist,
- run `german-anki doctor` until everything is ✓.

Options: `--no-packages`, `--dict-zip FILE`, `--skip-browser`, `--migrate-from DIR`
(copy `assets/` and the to-do/done lists of an older setup), `-y`.
Update: `git pull && ./install.sh`. Remove: `./uninstall.sh [--purge]`.

## Where things live

| What | Where |
|---|---|
| settings + API key | `~/.config/german-anki/.env` (see `.env.example`) |
| your extra ignore words | `~/.config/german-anki/ignore.txt` |
| to-do / done lists, screenshots, audio | `~/.local/share/german-anki/` |
| dictionary, sync state, review list | `~/.local/share/german-anki/{dict/, anki-words.json, anki-words-to-review.txt}` |
| LLM cache | `~/.cache/german-anki/` |

`german-anki paths` prints them.

## Commands

| | |
|---|---|
| `german-anki` / `german-anki words` | cards for the words in the clipboard (one per line or to-do rows) |
| `german-anki add-row ROW` | one to-do row (what the mpv capture runs) |
| `german-anki sync` | export the deck for subtitle colouring (what mpv runs) |
| `german-anki backfill-tags` | make `no-screenshot` / `no-audio` match the fields |
| `german-anki setup-dict [--zip FILE] [--force]` | (re)build the dictionary |
| `german-anki doctor` | check the installation |

## Anki note type

`ANKI_NOTE_TYPE` (default `Basic with example`) needs the fields
`Word, Definition, Conjugation, Screenshot, SourceAudio, Examples, Pronunciation`.

## How the pieces talk

```
mpv: german-word-capture ──Ctrl+W──► to-do row ──► german-anki add-row ──► Anki
          │  script-message german-subs-suspend/-resume (screenshot with real subs)
          │  script-message german-subs-refresh (after a card was added)
          ▼
mpv: german-subs ──every load / 10 s──► german-anki sync ──► anki-words.json ──► colours
```

## Tests

```sh
~/.local/share/german-anki/venv/bin/pip install -e '.[test]'
~/.local/share/german-anki/venv/bin/pytest
```
