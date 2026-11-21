"""`german-anki sync`: export the deck for the german-subs mpv script. Anki is the only source.

Writes <data>/anki-words.json:
  {"generated", "synced_at", "words": {Word: {screenshot, audio}},
   "notes": {noteId: raw Word}, "other_notes": {noteId: label}, "mods": {noteId: mod},
   "forms": {form: "ok"|"missing"}}          <- "forms" is what german-subs reads
and <data>/anki-words-to-review.txt (phrases, annotations, words missing from the dictionary).

Fast path (runs every few seconds while a video plays):
  * screenshot/audio status comes from two Anki searches for empty fields;
  * the Word text is only fetched for notes that are new, or were edited in the last day and
    whose modification time changed (a full fetch happens when the state is older than 20h);
  * if nothing changed, the JSON is left alone and the output starts with "unchanged".

Exit status: 0 ok, 3 Anki offline (previous export kept), 2 Anki offline and no export.
"""
import collections
import json
import os
import sys
import time

from . import dictionary
from .anki import Anki, AnkiError, WORD, SCREENSHOT, SOURCE_AUDIO
from .normalize import normalize, strip_html

FULL_REFRESH_AFTER = 20 * 3600   # seconds; must stay below the 1 day of "edited:1"


def _write_atomic(path, text):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _load_previous(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _int_keys(d):
    return {int(k): v for k, v in (d or {}).items()}


def _str_keys(d):
    return {str(k): v for k, v in d.items()}


def fetch_deck(anki, deck_name, prev):
    """-> (note_words, other_notes, words, mods, full_fetch)"""
    deck = f'deck:"{deck_name}"'
    ids = anki("findNotes", timeout=3, query=deck)
    no_img = set(anki("findNotes", query=f"{deck} {SCREENSHOT}:"))
    no_audio = set(anki("findNotes", query=f"{deck} {SOURCE_AUDIO}:"))

    note_words = _int_keys(prev.get("notes"))
    other_notes = _int_keys(prev.get("other_notes"))
    mods = _int_keys(prev.get("mods"))
    full = time.time() - prev.get("synced_at", 0) > FULL_REFRESH_AFTER or not note_words
    if full:
        need = list(ids)
    else:
        known = note_words.keys() | other_notes.keys()
        need = [i for i in ids if i not in known]
        recent = [i for i in anki("findNotes", query=f"{deck} edited:1") if i in known]
        if recent:
            for m in anki("notesModTime", notes=recent):
                if mods.get(m["noteId"]) != m["mod"]:
                    need.append(m["noteId"])

    if need:
        for n in anki("notesInfo", timeout=60, notes=sorted(set(need))):
            nid, f = n["noteId"], n["fields"]
            mods[nid] = n.get("mod")
            note_words.pop(nid, None)
            other_notes.pop(nid, None)
            if WORD in f:
                note_words[nid] = f[WORD]["value"]
            else:
                front = strip_html(next(iter(f.values()), {}).get("value", ""))
                other_notes[nid] = f"{front}   [{n['modelName']}]"

    alive = set(ids)
    note_words = {i: w for i, w in note_words.items() if i in alive}
    other_notes = {i: w for i, w in other_notes.items() if i in alive}
    mods = {i: m for i, m in mods.items() if i in alive}
    words = {strip_html(raw): {"screenshot": nid not in no_img, "audio": nid not in no_audio}
             for nid, raw in note_words.items()}
    return note_words, other_notes, words, mods, full


def build_forms(words, forms_of):
    forms = {}
    for word, media in words.items():
        key, _ = normalize(word)
        if key is None:
            continue
        status = "ok" if media["screenshot"] and media["audio"] else "missing"
        for form in {key} | forms_of.get(key, set()):
            if forms.get(form) != "ok":   # if any word behind a form is complete, call it ok
                forms[form] = status
    return forms


def build_review(deck_name, note_words, other_notes, heads, lemmas_of):
    review = collections.defaultdict(list)
    keys_seen = collections.defaultdict(list)
    for label in other_notes.values():
        review["Notes without a 'Word' field (other note type)"].append(label)
    for raw in note_words.values():
        word = strip_html(raw)
        key, issues = normalize(raw)
        if key is None:
            review["Phrases / expressions (not used for colouring)"].append(word)
            continue
        if issues:
            review["Annotations or odd characters (cleaned, still used)"].append(
                f"{word}   ->  {key}   ({', '.join(issues)})")
        keys_seen[key].append(word)
        if key not in heads:
            if key in lemmas_of:
                review["Inflected form instead of base word (only this exact form is matched)"].append(
                    f"{word}   ->  base word: {', '.join(sorted(lemmas_of[key]))}")
            else:
                review["Not in the dictionary (typo? rare word? only this exact form is matched)"].append(word)
    for ws in keys_seen.values():
        if len(ws) > 1:
            review["Duplicates (same word after cleaning)"].append("  |  ".join(ws))

    lines = [f"# Anki deck entries to review — generated {time.strftime('%Y-%m-%d %H:%M')}",
             f"# {len(note_words) + len(other_notes)} notes in '{deck_name}'. Regenerated when the deck "
             "changes; edit the cards in Anki, not this file.", ""]
    for section in sorted(review):
        items = sorted(review[section], key=str.lower)
        lines += [f"## {section} ({len(items)})", *items, ""]
    return "\n".join(lines)


def run(settings):
    t0 = time.time()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    out_json = settings.words_json
    prev = _load_previous(out_json)
    anki = Anki(settings.anki_url)

    try:
        note_words, other_notes, words, mods, full = fetch_deck(anki, settings.deck, prev)
    except (OSError, AnkiError) as e:
        has_export = bool(prev.get("forms"))
        print(f"Anki not reachable ({e}); "
              + ("keeping the previous export" if has_export else "no previous export"), file=sys.stderr)
        return 3 if has_export else 2

    unchanged = (prev.get("forms") is not None and words == prev.get("words")
                 and _str_keys(other_notes) == (prev.get("other_notes") or {})
                 and _str_keys(note_words) == (prev.get("notes") or {}))
    if unchanged and not full:
        if _str_keys(mods) != (prev.get("mods") or {}):
            prev["mods"] = _str_keys(mods)   # remember new modification times only
            _write_atomic(out_json, json.dumps(prev, ensure_ascii=False, separators=(",", ":")))
        print(f"unchanged ({len(words)} Anki words, {time.time() - t0:.2f}s)")
        return 0

    con = dictionary.connect(settings.dict_dir)
    keys = {normalize(w)[0] for w in words} - {None}
    heads, forms_of, lemmas_of = dictionary.lookup(con, keys)
    forms = build_forms(words, forms_of)

    _write_atomic(out_json, json.dumps({
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "synced_at": time.time(),
        "deck": settings.deck,
        "words": words,
        "notes": _str_keys(note_words),
        "other_notes": _str_keys(other_notes),
        "mods": _str_keys(mods),
        "forms": forms,
    }, ensure_ascii=False, separators=(",", ":")))
    _write_atomic(settings.review_file, build_review(settings.deck, note_words, other_notes, heads, lemmas_of))

    n_missing = sum(1 for w in words.values() if not (w["screenshot"] and w["audio"]))
    print(f"updated: {len(words)} Anki words ({n_missing} missing media) -> {len(forms)} forms "
          f"in {time.time() - t0:.2f}s{' (full fetch)' if full else ''}")
    return 0
