"""One-time move away from the Markdown files of older versions.

They kept the capture queue as Obsidian-style tables in <data>:
    To-do-german-words.md   | word | meaning | ![[x.webp\\|500x379]] | ![[x.ogg]] |
    sentence-mining.md      | German | English | ![[x.webp\\|500x379]] | ![[x.ogg]] |
plus the *-done.md logs. migrate() turns their pending rows into queue entries and moves all
four files to <data>/legacy/, which nothing reads any more. Safe to run again: with the files
gone it does nothing. install.sh runs it (`python -m danki.legacy`); so do `retry` and
`clean-assets`, which must not miss captures still in the old files.
"""
import re
import sys
import time
import urllib.parse
from pathlib import Path

from . import config, queue

PENDING = {"To-do-german-words.md": "word", "sentence-mining.md": "sentence"}
DONE = ["To-do-german-words-done.md", "sentence-mining-done.md"]
AUDIO_SUFFIXES = {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".aac"}


def _cells(line):
    """Cells of one table row; '\\|' inside a cell (Obsidian size hints) is not a separator."""
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _embeds(line):
    """-> (image, audio) file names from the ![[...]] embeds of a row."""
    image = audio = None
    for raw in re.findall(r"!\[\[\s*([^\]|\\]+)", line):
        name = Path(urllib.parse.unquote(raw.strip())).name
        if Path(name).suffix.lower() in AUDIO_SUFFIXES:
            audio = audio or name
        else:
            image = image or name
    return image, audio


def parse_row(line, kind):
    """One table row -> a queue entry without its id, or None (header, separator, junk)."""
    line = line.strip()
    if not line.startswith("|"):
        return None
    cells = _cells(line)
    text = cells[0] if cells else ""
    meaning = cells[1] if len(cells) > 1 else ""
    if not text or set(text) <= set("-: ") or text in ("German", "Word"):
        return None
    if kind == "sentence" and not meaning:
        return None
    image, audio = _embeds(line)
    return {"kind": kind, "text": text, "meaning": meaning, "image": image, "audio": audio}


def migrate(settings=None):
    """-> number of captures moved into the queue."""
    settings = settings or config.load()
    files = [settings.data_dir / name for name in [*PENDING, *DONE]]
    if not any(f.exists() for f in files):
        return 0
    moved = 0
    stamp = int(time.time() * 1000)
    for name, kind in PENDING.items():
        path = settings.data_dir / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            entry = parse_row(line, kind)
            if entry:
                entry.update(id=f"{stamp}{moved:04d}", captured=int(time.time()))
                queue.add(settings, entry)
                moved += 1
    settings.legacy_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        if f.exists():
            target = settings.legacy_dir / f.name
            if target.exists():   # left from an earlier move: keep both
                target = target.with_name(f"{f.stem}-{stamp}{f.suffix}")
            f.rename(target)
    print(f"moved {moved} pending capture(s) from the old Markdown files into {settings.queue_dir}; "
          f"the files themselves are in {settings.legacy_dir} (safe to delete)", file=sys.stderr)
    return moved


if __name__ == "__main__":
    migrate()
