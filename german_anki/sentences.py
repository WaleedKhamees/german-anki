"""`german-anki sentences`: import sentence pairs from a markdown table into the sentence deck.
`german-anki add-sentence ROW`: add one row (what the mpv sentence capture, Ctrl+E, runs).

The file (default <data>/sentence-mining.md) looks like:
    | German | English | image | audio |
    | ------ | ------- | ----- | ----- |
    | Die Sache wird langsam ernst | things are about to get serious | ![[die-sache-v1.webp\\|500x379]] | ![[die-sache-v1.ogg]] |
Every row becomes two notes (German-front and English-front) in ANKI_SENTENCE_DECK,
tagged `sentence-mining`; image and audio are optional and are searched in <data>/assets
and OBSIDIAN_VAULT_PATH.
"""
import base64
import io
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

from PIL import Image

from . import config
from .images import fit_width
from .anki import Anki, WORD, DEFINITION, SCREENSHOT, SOURCE_AUDIO

TAG = "sentence-mining"


def notify(title, message, is_error=False):
    subprocess.run(["notify-send", "-u", "critical" if is_error else "normal", title, message])


def find_media(settings, filename):
    for root in (settings.assets_dir, settings.obsidian_vault):
        if not root or not root.exists():
            continue
        if (root / filename).is_file():
            return root / filename
        found = next((p for p in root.rglob("*") if p.name.lower() == filename.lower()), None)
        if found:
            return found
    print(f"[!] '{filename}' not found", file=sys.stderr)
    return None


def find_and_upload_audio(anki, settings, filename):
    audio_file = find_media(settings, filename)
    if not audio_file:
        return None
    media_filename = audio_file.name.lower()
    anki("storeMediaFile", filename=media_filename, path=str(audio_file.resolve()))
    return media_filename


def find_and_upload_image(anki, settings, filename):
    image_file = find_media(settings, filename)
    if not image_file:
        return None

    with open(image_file, "rb") as f:
        img = Image.open(f)
        img.load()
        img = img.convert("RGBA" if img.mode in ("RGBA", "LA") else "RGB")
        img = fit_width(img)
        buf = io.BytesIO()
        img.save(buf, format="PNG" if image_file.suffix.lower() == ".png" else "WEBP", optimize=True)

    media_filename = image_file.name.lower()
    anki("storeMediaFile", filename=media_filename, data=base64.b64encode(buf.getvalue()).decode("ascii"))
    return media_filename


def _media_name(cell):
    """'![[x.webp\\|500x379]]' / '![[x.ogg]]' / 'x.webp' -> 'x.webp'; '' -> None."""
    if not cell:
        return None
    m = re.search(r"!\[\[\s*([^\]|\\]+)", cell)
    return Path(urllib.parse.unquote(m.group(1).strip())).name if m else cell


def split_row(line):
    """Cells of one markdown table row, honouring '\\|' inside a cell (Obsidian size hints)."""
    cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [c.strip() for c in cells]


def parse_row(line):
    """One table row -> (german, english, image, audio), or None if it isn't a usable row."""
    line = line.strip()
    if not line.startswith("|") or not line.endswith("|"):
        return None
    columns = split_row(line)
    if len(columns) < 2 or not columns[0] or not columns[1]:
        return None
    german, english = columns[0].replace("\\|", "|"), columns[1].replace("\\|", "|")
    image = _media_name(columns[2]) if len(columns) >= 3 else None
    audio = _media_name(columns[3]) if len(columns) >= 4 else None
    return german, english, image, audio


def parse_file(path):
    rows = []
    lines = [raw.strip() for raw in Path(path).read_text(encoding="utf-8").splitlines() if raw.strip()]
    if len(lines) < 3:
        print("File must have at least a header, separator, and one data row.", file=sys.stderr)
        return rows

    # Skip header (index 0) and separator (index 1)
    for lineno, line in enumerate(lines[2:], start=3):
        row = parse_row(line)
        if not row:
            print(f"Skipping line {lineno}: not a row with German and English: {line!r}", file=sys.stderr)
            continue
        rows.append(row)
    return rows


def _search_text(text):
    """Escape a value for an Anki search inside double quotes."""
    return re.sub(r'([\\"*_:])', r"\\\1", text)


def exists(anki, deck, text):
    return bool(anki("findNotes", query=f'"deck:{deck}" "{WORD}:{_search_text(text)}"'))


def deduplicate(anki, deck, rows):
    keep = []
    for row in rows:
        if exists(anki, deck, row[0]) or exists(anki, deck, row[1]):
            print(f"Duplicate, skipping: {row[0]}")
        else:
            keep.append(row)
    return keep


def add_note(anki, settings, front, back, screenshot="", audio=""):
    anki("addNote", note={
        "deckName": settings.sentence_deck,
        "modelName": settings.note_type,
        "fields": {WORD: front, DEFINITION: back, SCREENSHOT: screenshot, SOURCE_AUDIO: audio},
        # only the sentence deck counts: a sentence may equal a word card's front ("Bis bald")
        "options": {"allowDuplicate": False, "duplicateScope": "deck"},
        "tags": [TAG],
    })


def add_pair(anki, settings, german, english, image=None, audio=None):
    """Upload the media and add the German-front and English-front notes."""
    image_filename = find_and_upload_image(anki, settings, image) if image else None
    audio_filename = find_and_upload_audio(anki, settings, audio) if audio else None
    screenshot = f"<img src='{image_filename}'>" if image_filename else ""
    sound = f"[sound:{audio_filename}]" if audio_filename else ""
    add_note(anki, settings, german, english, screenshot, sound)
    add_note(anki, settings, english, german, screenshot, sound)
    return image_filename, audio_filename


def add_row(row):
    """One row from the mpv sentence capture. Prints STATUS/RESULT for the Lua script.
    Exit 0 = the row is done (added, or already in the deck)."""
    parsed = parse_row(row)
    if not parsed:
        print("STATUS: failed")
        print("RESULT: could not parse row (needs German and English)")
        return 1
    german, english, image, audio = parsed
    try:
        settings = config.load()
        anki = Anki(settings.anki_url)
        if exists(anki, settings.sentence_deck, german):
            print("STATUS: duplicate")
            print(f"RESULT: already in the deck: {german}")
            return 0
        image_filename, audio_filename = add_pair(anki, settings, german, english, image, audio)
    except Exception as e:   # e.g. AnkiConnect down
        notify("Sentence Mining failed", f"{german}\n{e}", is_error=True)
        print("STATUS: failed")
        print(f"RESULT: {e}")
        return 1
    missing = [name for name, got in (("screenshot", image_filename), ("audio", audio_filename)) if not got]
    print("STATUS: added")
    print(f"RESULT: sentence added" + (f" (no {', '.join(missing)})" if missing else ""))
    return 0


def run(file=None, skip_duplicates=False):
    settings = config.load()
    anki = Anki(settings.anki_url)
    path = Path(file) if file else settings.sentences_file

    rows = parse_file(path)
    if not rows:
        print(f"No valid sentences found in {path}.")
        return 1
    if skip_duplicates:
        rows = deduplicate(anki, settings.sentence_deck, rows)

    total = len(rows)
    for i, (german, english, image, audio) in enumerate(rows, start=1):
        add_pair(anki, settings, german, english, image, audio)
        print(f"[{i}/{total}] {german} | {english}")

    message = f"Added {total * 2} notes ({total} pairs) to '{settings.sentence_deck}'."
    print(message)
    notify("Sentence Mining Done", message)
    return 0
