"""Captures waiting for Anki, one JSON file each in <data>/queue/.

The mpv capture script writes <id>.json (atomically, with a rename) next to the screenshot and
audio clip it saved in <data>/assets, then runs `danki add-capture <file>`:
    {"id": "...", "kind": "word" | "sentence", "text": "Störung" or the German sentence,
     "meaning": the note (word) or English translation (sentence, required),
     "image": "störung-1795791049123001.webp" or null, "audio": "....ogg" or null,
     "captured": unix time}
Once the card is in Anki, the entry and its two asset files are deleted (Anki keeps its own
copy of the media). A failed capture stays, for `danki retry`; nothing captured is lost.
One file per capture means mpv and the CLI never rewrite a shared file.
"""
import json
import sys
from pathlib import Path

from . import config
from .files import write_atomic

KINDS = ("word", "sentence")


class BadEntry(ValueError):
    pass


def validate(entry):
    """-> the entry with defaults filled in; raises BadEntry if it can't make a card."""
    if not isinstance(entry, dict):
        raise BadEntry("not a JSON object")
    kind, text = entry.get("kind"), (entry.get("text") or "").strip()
    meaning = (entry.get("meaning") or "").strip()
    if kind not in KINDS:
        raise BadEntry(f"unknown kind {kind!r}")
    if not text:
        raise BadEntry("no text")
    if kind == "sentence" and not meaning:
        raise BadEntry("a sentence needs its English translation")
    return {**entry, "text": text, "meaning": meaning,
            "image": _asset_name(entry.get("image")), "audio": _asset_name(entry.get("audio"))}


def _asset_name(name):
    """A bare file name inside the assets folder, never a path out of it."""
    return Path(name).name if name else None


def load(path):
    try:
        return validate(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError) as e:
        raise BadEntry(f"{path}: {e}") from None


def add(settings, entry):
    """Write a new entry (used when converting the old Markdown files). -> its path."""
    entry = validate(entry)
    settings.queue_dir.mkdir(parents=True, exist_ok=True)
    path = settings.queue_dir / f"{entry['id']}.json"
    write_atomic(path, json.dumps(entry, ensure_ascii=False, indent=1) + "\n")
    return path


def pending(settings):
    """-> [(path, entry)], oldest first; unreadable files are reported and skipped."""
    out = []
    if not settings.queue_dir.is_dir():
        return out
    for path in sorted(settings.queue_dir.glob("*.json")):   # ids start with the time
        try:
            out.append((path, load(path)))
        except BadEntry as e:
            print(f"[!] skipping {e}", file=sys.stderr)
    return out


def referenced_assets(settings):
    """Lowercase names of the asset files that pending captures still need."""
    return {name.lower() for _, e in pending(settings) for name in (e["image"], e["audio"]) if name}


def finish(settings, path, entry):
    """The card is in Anki: delete the capture and its media files."""
    for name in (entry["image"], entry["audio"]):
        if name:
            (settings.assets_dir / name).unlink(missing_ok=True)
    Path(path).unlink(missing_ok=True)


def _make_card(entry):
    """-> (ok, message, kind) with kind = added | updated | duplicate | failed."""
    if entry["kind"] == "word":
        from . import cards
        return cards.process_capture(entry["text"], entry["image"], entry["audio"])
    from . import sentences
    return sentences.add_capture(entry["text"], entry["meaning"], entry["image"], entry["audio"])


def process(path):
    """`add-capture`: make the card for one queued capture. Prints STATUS/RESULT for the mpv
    script. Exit 0 = done (the entry and its media are gone), 1 = it stays queued."""
    try:
        entry = load(path)
    except BadEntry as e:
        print("STATUS: failed")
        print(f"RESULT: {e}")
        return 1
    try:
        ok, message, kind = _make_card(entry)
    except Exception as e:   # e.g. AnkiConnect down
        ok, message, kind = False, str(e), "failed"
    if ok:
        finish(config.load(), path, entry)
    print(f"STATUS: {kind}")
    print(f"RESULT: {message}")
    return 0 if ok else 1


def retry(list_only=False):
    """`retry`: make the cards for every capture still queued (oldest first)."""
    from . import legacy
    settings = config.load()
    legacy.migrate(settings)
    entries = pending(settings)
    if not entries:
        print("nothing queued")
        return 0
    if list_only:
        for path, e in entries:
            extra = f" — {e['meaning']}" if e["meaning"] else ""
            print(f"{e['kind']:8} {e['text']}{extra}  ({path.name})")
        return 0
    failed = 0
    for i, (path, e) in enumerate(entries, 1):
        print(f"\n[{i}/{len(entries)}] {e['kind']}: {e['text']}")
        if process(path) != 0:
            failed += 1
    print(f"\n{len(entries) - failed} done, {failed} still queued" if failed else f"\nall {len(entries)} done")
    return 1 if failed else 0


def _size(paths):
    return sum(p.stat().st_size for p in paths) / 1e6


def clean_assets(dry_run=False, assume_yes=False):
    """`clean-assets`: delete the files in <data>/assets that no queued capture needs. Media of
    cards that were made are already in Anki's own media folder."""
    from . import legacy
    from .anki import Anki
    settings = config.load()
    legacy.migrate(settings)   # captures still in the old Markdown files need their media
    if not settings.assets_dir.is_dir():
        print(f"no assets folder ({settings.assets_dir})")
        return 0
    keep = referenced_assets(settings)
    files = [p for p in settings.assets_dir.iterdir() if p.is_file()]
    unused = [p for p in files if p.name.lower() not in keep]
    print(f"{len(files)} files in {settings.assets_dir} ({_size(files):.0f} MB); "
          f"{len(files) - len(unused)} kept for queued captures")
    if not unused:
        return 0
    try:
        in_anki = {n.lower() for n in Anki(settings.anki_url)("getMediaFilesNames", pattern="*", timeout=10)}
        copied = [p for p in unused if p.name.lower() in in_anki]
        print(f"  {len(copied)} already in Anki's media ({_size(copied):.0f} MB)")
        rest = [p for p in unused if p.name.lower() not in in_anki]
        print(f"  {len(rest)} never reached Anki ({_size(rest):.0f} MB), such as leftovers of older versions "
              "(*-original.*) and recaptures of words that already had media")
    except Exception as e:
        print(f"  (Anki not reachable, so not split by whether Anki has them: {e})")
    if dry_run:
        print(f"would delete {len(unused)} files ({_size(unused):.0f} MB)")
        return 0
    if not assume_yes:
        answer = input(f"Delete these {len(unused)} files ({_size(unused):.0f} MB)? [y/N] ")
        if not answer.strip().lower().startswith("y"):
            print("kept")
            return 0
    freed = _size(unused)
    for p in unused:
        p.unlink(missing_ok=True)
    print(f"deleted {len(unused)} files ({freed:.0f} MB)")
    return 0
