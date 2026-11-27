"""German dictionary (reader.dict, Wiktionary data) used to map inflected forms to base words.

`setup` downloads the StarDict zip and builds, in <data>/dict/:
  dict.sqlite   heads(head) + syn(head, form), indexed both ways  -> fast lookups for sync
  wordlist.txt  every single-word form, lowercase, sorted by UTF-8 bytes
                -> binary-searched by the mpv script to tell "unknown word" from "name"
"""
import collections
import os
import shutil
import sqlite3
import struct
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

DICT_URL = "https://www.reader-dict.com/file/de/dict-de-de.zip"
DICT_NAME = "reader.dict-de"
DB_VERSION = "1"


class DictionaryMissing(RuntimeError):
    pass


def _parse(dict_dir):
    """-> (set of headwords, set of (headword, form) pairs), all lowercase."""
    idx = (dict_dir / f"{DICT_NAME}.idx").read_bytes()
    heads, i = [], 0
    while i < len(idx):
        j = idx.index(b"\0", i)
        heads.append(idx[i:j].decode("utf-8", "replace").lower())
        i = j + 9  # NUL + 32-bit offset + 32-bit size

    syn = (dict_dir / f"{DICT_NAME}.syn").read_bytes()
    pairs, i = set(), 0
    while i < len(syn):
        j = syn.index(b"\0", i)
        form = syn[i:j].decode("utf-8", "replace").lower()
        (k,) = struct.unpack(">I", syn[j + 1:j + 5])
        i = j + 5
        pairs.add((heads[k], form))
    return set(heads), pairs


def _write_atomic(path, text):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def build(dict_dir):
    """Build dict.sqlite + wordlist.txt from the extracted .idx/.syn files."""
    t0 = time.time()
    heads, pairs = _parse(dict_dir)
    db_path = dict_dir / "dict.sqlite"
    tmp = dict_dir / "dict.sqlite.tmp"
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE heads (head TEXT PRIMARY KEY) WITHOUT ROWID;
        CREATE TABLE syn (head TEXT, form TEXT);
    """)
    con.executemany("INSERT INTO heads VALUES (?)", ((h,) for h in heads))
    con.executemany("INSERT INTO syn VALUES (?, ?)", pairs)
    con.executescript("CREATE INDEX syn_head ON syn(head); CREATE INDEX syn_form ON syn(form);")
    syn_mtime = str((dict_dir / f"{DICT_NAME}.syn").stat().st_mtime)
    con.executemany("INSERT INTO meta VALUES (?, ?)", [("syn_mtime", syn_mtime), ("version", DB_VERSION)])
    con.commit()
    con.close()
    os.replace(tmp, db_path)

    words = {w for w in heads | {f for _, f in pairs} if w and not any(c.isspace() for c in w)}
    _write_atomic(dict_dir / "wordlist.txt",
                  "\n".join(sorted(words, key=lambda s: s.encode("utf-8"))) + "\n")
    print(f"built {db_path} and wordlist.txt ({len(words)} words) in {time.time() - t0:.1f}s")


def setup(dict_dir, zip_path=None, force=False):
    """Download (or take `zip_path`), extract and build. Skips work that is already done."""
    dict_dir = Path(dict_dir)
    dict_dir.mkdir(parents=True, exist_ok=True)
    have_files = all((dict_dir / f"{DICT_NAME}.{ext}").exists() for ext in ("idx", "syn"))
    if force or not have_files:
        with tempfile.TemporaryDirectory() as tmp:
            if zip_path is None:
                zip_path = Path(tmp) / "dict-de-de.zip"
                print(f"downloading {DICT_URL} ...")
                req = urllib.request.Request(DICT_URL, headers={"User-Agent": "danki"})
                with urllib.request.urlopen(req, timeout=120) as r, open(zip_path, "wb") as out:
                    shutil.copyfileobj(r, out)
            with zipfile.ZipFile(zip_path) as z:
                for ext in ("idx", "syn", "ifo"):
                    name = f"{DICT_NAME}.{ext}"
                    if name not in z.namelist():
                        raise DictionaryMissing(f"{zip_path} does not contain {name}")
                    with z.open(name) as src, open(dict_dir / name, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        print(f"extracted dictionary to {dict_dir}")
    if force or not is_built(dict_dir):
        build(dict_dir)
    else:
        print(f"dictionary already set up in {dict_dir}")


def is_built(dict_dir):
    db_path = Path(dict_dir) / "dict.sqlite"
    if not db_path.exists() or not (Path(dict_dir) / "wordlist.txt").exists():
        return False
    try:
        con = sqlite3.connect(db_path)
        meta = dict(con.execute("SELECT key, value FROM meta"))
        con.close()
    except sqlite3.Error:
        return False
    syn = Path(dict_dir) / f"{DICT_NAME}.syn"
    return meta.get("version") == DB_VERSION and syn.exists() and meta.get("syn_mtime") == str(syn.stat().st_mtime)


def connect(dict_dir):
    if not is_built(dict_dir):
        raise DictionaryMissing(f"dictionary not set up in {dict_dir} — run: danki setup-dict")
    return sqlite3.connect(Path(dict_dir) / "dict.sqlite")


def lookup(con, keys):
    """-> (headwords among keys, {head: set(forms)}, {form: set(heads)} for keys used as forms)"""
    con.execute("CREATE TEMP TABLE IF NOT EXISTS k (key TEXT PRIMARY KEY)")
    con.execute("DELETE FROM k")
    con.executemany("INSERT OR IGNORE INTO k VALUES (?)", ((x,) for x in keys))
    heads = {h for (h,) in con.execute("SELECT head FROM heads JOIN k ON head = key")}
    forms_of, lemmas_of = collections.defaultdict(set), collections.defaultdict(set)
    for h, f in con.execute("SELECT syn.head, syn.form FROM syn JOIN k ON syn.head = k.key"):
        forms_of[h].add(f)
    for f, h in con.execute("SELECT syn.form, syn.head FROM syn JOIN k ON syn.form = k.key"):
        lemmas_of[f].add(h)
    return heads, forms_of, lemmas_of
