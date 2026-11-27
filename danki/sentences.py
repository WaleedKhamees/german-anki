"""Sentence cards: a German sentence and its English translation become two notes (German-front
and English-front) in ANKI_SENTENCE_DECK, tagged `sentence-mining`. They come from the mpv
sentence capture (Ctrl+E; `queue.process` calls add_capture) or from `danki sentences`, which
reads '<German> - <English>' lines typed by hand. Image and audio are optional and come from
<data>/assets.
"""
import re
from pathlib import Path

from . import config, media
from .media import notify
from .anki import Anki, WORD, DEFINITION, SCREENSHOT, SOURCE_AUDIO

TAG = "sentence-mining"


def _search_text(text):
    """Escape a value for an Anki search inside double quotes."""
    return re.sub(r'([\\"*_:])', r"\\\1", text)


def exists(anki, deck, text):
    return bool(anki("findNotes", query=f'"deck:{deck}" "{WORD}:{_search_text(text)}"'))


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
    image_filename = media.upload_image(anki, settings.assets_dir, image) if image else None
    audio_filename = media.upload_audio(anki, settings.assets_dir, audio) if audio else None
    screenshot = f"<img src='{image_filename}'>" if image_filename else ""
    sound = f"[sound:{audio_filename}]" if audio_filename else ""
    add_note(anki, settings, german, english, screenshot, sound)
    add_note(anki, settings, english, german, screenshot, sound)
    return image_filename, audio_filename


def add_capture(german, english, image=None, audio=None):
    """-> (ok, message, kind) with kind = added | duplicate | failed."""
    try:
        settings = config.load()
        anki = Anki(settings.anki_url)
        if exists(anki, settings.sentence_deck, german):
            return True, f"already in the deck: {german}", "duplicate"
        image_filename, audio_filename = add_pair(anki, settings, german, english, image, audio)
    except Exception as e:   # e.g. AnkiConnect down
        notify("Sentence Mining failed", f"{german}\n{e}", is_error=True)
        return False, str(e), "failed"
    missing = [name for name, got in (("screenshot", image_filename), ("audio", audio_filename)) if not got]
    return True, "sentence added" + (f" (no {', '.join(missing)})" if missing else ""), "added"


# --- `danki sentences`: pairs typed by hand ---

SEPARATOR = re.compile(r"\s+[-–—]\s+")   # "German - English"; en/em dashes count too


def parse_line(line):
    """'<German> - <English>' -> (german, english); None if a side is missing. The first
    spaced dash splits, so the English side may contain dashes of its own."""
    parts = SEPARATOR.split(line.strip(), maxsplit=1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        return None
    return parts[0].strip(), parts[1].strip()


def run(file=None):
    """Add every '<German> - <English>' line of FILE (default: the clipboard) as two notes.
    Sentences already in the deck are skipped."""
    if file:
        text = Path(file).read_text(encoding="utf-8")
    else:
        import pyperclip
        text = pyperclip.paste()
    pairs, bad = [], []
    for n, line in enumerate(text.splitlines(), 1):
        if line.strip():
            pair = parse_line(line)
            (pairs.append(pair) if pair else bad.append((n, line.strip())))
    for n, line in bad:
        print(f"[!] line {n} skipped, not '<German> - <English>': {line}")
    if not pairs:
        print("no '<German> - <English>' lines found" + ("" if file else " in the clipboard"))
        return 1

    added = duplicates = failed = 0
    for i, (german, english) in enumerate(pairs, 1):
        ok, message, kind = add_capture(german, english)
        print(f"[{i}/{len(pairs)}] {german} | {english}: {message}")
        if kind == "added":
            added += 1
        elif kind == "duplicate":
            duplicates += 1
        else:
            failed += 1
    summary = f"{added} added, {duplicates} already in the deck" + (f", {failed} failed" if failed else "")
    print(summary)
    notify("Sentence Mining done" if not failed else "Sentence Mining: some failed", summary, is_error=bool(failed))
    return 1 if failed or bad else 0
