# german-anki

Mine German vocabulary and sentences from videos in **mpv** straight into **Anki**.

> **Note:** This project, code and documentation alike, was generated with AI.

- `Ctrl+W` in mpv: pick a word from the subtitle. A card is made with a definition,
  examples, a conjugation table, a screenshot, the line's audio and a pronunciation.
- `Ctrl+E` in mpv: save a whole sentence (with your English translation) as two cards.
- Subtitles are coloured by what's already in your deck: white = known, red = new,
  yellow/blue = in Anki but missing media, gray = not a dictionary word. `Alt+C` toggles.

## Install

```sh
git clone https://github.com/WaleedKhamees/german-anki && cd german-anki
./install.sh
```

Then:

1. Put your `OPENROUTER_API_KEY` in `~/.config/german-anki/.env`.
2. In Anki, install the **AnkiConnect** add-on (code `2055492159`).
3. Run `german-anki doctor` and fix anything that isn't ✓.

Update with `git pull && ./install.sh`. Remove with `./uninstall.sh [--purge]`.

## Commands

| Command | What it does |
|---|---|
| `german-anki` | make cards for the words in the clipboard |
| `german-anki sentences` | import sentence pairs from `sentence-mining.md` |
| `german-anki sync` | refresh the word list used for subtitle colours |
| `german-anki doctor` | check the installation |
| `german-anki paths` | show where config and data are stored |

Run `german-anki --help` for the rest.

## Configuration

All settings (deck names, note type, hotkeys, colours) live in
`~/.config/german-anki/.env`. See `.env.example` for every option.

The note type (default `Basic with example`) needs the fields
`Word, Definition, Conjugation, Screenshot, SourceAudio, Examples, Pronunciation`.

## Tests

```sh
~/.local/share/german-anki/venv/bin/pip install -e '.[test]'
~/.local/share/german-anki/venv/bin/pytest
```
