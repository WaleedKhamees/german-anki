# german-anki

Mine German vocabulary and sentences from videos in **mpv** straight into **Anki**.

> **Note:** This project, code and documentation alike, was generated with AI.

![Coloured German subtitles in mpv](docs/screenshot.jpg)

- `Ctrl+W` in mpv: pick a word from the subtitle. A card is made with a definition,
  examples, a conjugation table, a screenshot, the line's audio and a pronunciation.
- `Ctrl+E` in mpv: save a whole sentence (with your English translation) as two cards.
- Subtitles are coloured by what's already in your deck: white = known, red = new,
  yellow/blue = in Anki but missing media, gray = not a dictionary word. `Alt+C` toggles.

## Install

New to this? Follow the [beginner's guide](docs/GUIDE.md) step by step.

```sh
git clone https://github.com/WaleedKhamees/german-anki && cd german-anki
./install.sh
```

Start Anki with the **AnkiConnect** add-on (code `2055492159`) first, and the installer offers to
create the decks and the note type. Already have them on AnkiWeb? Answer `n` and sync instead.

Then:

1. Put your `OPENROUTER_API_KEY` in `~/.config/german-anki/.env`.
2. If Anki wasn't running during the install, start it and run `german-anki setup-anki`.
3. Run `german-anki doctor` and fix anything that isn't ✓.

Update with `git pull && ./install.sh`. Remove with `./uninstall.sh [--purge]`.

## Commands

| Command | What it does |
|---|---|
| `german-anki` | make cards for the words in the clipboard |
| `german-anki regen` | regenerate the cards you tagged `regen` in Anki (see below) |
| `german-anki sentences` | import sentence pairs from `sentence-mining.md` |
| `german-anki sync` | refresh the word list used for subtitle colours |
| `german-anki setup-anki` | create the missing decks and note type in Anki |
| `german-anki doctor` | check the installation |
| `german-anki paths` | show where config and data are stored |

Run `german-anki --help` for the rest. `install.sh` also sets up bash completion for
subcommands, options and, after `regen`, the words in your deck.

### Regenerating cards

Tag bad cards `regen` in Anki, then run `german-anki regen`. In the browser, select the
cards and press `Ctrl+Shift+A`. While reviewing, press `E` to edit the note, then
`Ctrl+Shift+T` to jump to its tags. For a single key during review, flag the card instead
(`Ctrl+1`, red) and run `german-anki regen --query 'flag:1'`. Each note is updated in place, so its review history is kept. The
`Screenshot` and `SourceAudio` from the video are never touched. When a note is done, its
tag is removed.

```sh
german-anki regen                          # every note tagged `regen`
german-anki regen Leiter Haus              # these words instead
german-anki regen --query 'flag:1'         # any Anki search within the deck
german-anki regen Leiter --hint 'ladder'   # tell the LLM which meaning you want
german-anki regen --only examples          # redo only some parts: definition,examples,conjugation,pronunciation
german-anki regen --dry-run                # just list what would be regenerated
german-anki regen Haus --only examples --min-words 6 --max-words 12   # shorter examples
```

Close the note in Anki's browser first. AnkiConnect doesn't update a note that is open in the
editor. Old conjugation images are left unused; Tools → Check Media removes them.

## Configuration

All settings (deck names, note type, hotkeys, colours) live in
`~/.config/german-anki/.env`. See `.env.example` for every option.

Example sentences are 10–25 words long by default (`EXAMPLE_MIN_WORDS`, `EXAMPLE_MAX_WORDS`).
`german-anki words`, `add-row` and `regen` take `--min-words N` / `--max-words N` to change
that for one run.

The note type (default `Basic with example`) needs the fields
`Word, Definition, Conjugation, Screenshot, SourceAudio, Examples, Pronunciation`.

## Tests

```sh
~/.local/share/german-anki/venv/bin/pip install -e '.[test]'
~/.local/share/german-anki/venv/bin/pytest
```
