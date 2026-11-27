"""`german-anki sync`: export the deck for the german-subs mpv script. Anki is the only source.

Writes <data>/anki-words.json:
  {"generated", "synced_at", "words": {Word: {screenshot, audio}},
   "notes": {noteId: raw Word}, "other_notes": {noteId: label}, "mods": {noteId: mod},
   "forms": {form: "ok"|"noaudio"|"missing"}}          <- "forms" is what german-subs reads
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
    # one request instead of four (this runs every few seconds)
    ids, no_img, no_audio, edited = anki.multi(
        ("findNotes", {"query": deck}),
        ("findNotes", {"query": f"{deck} {SCREENSHOT}:"}),
        ("findNotes", {"query": f"{deck} {SOURCE_AUDIO}:"}),
        ("findNotes", {"query": f"{deck} edited:1"}),
        timeout=3)
    no_img, no_audio = set(no_img), set(no_audio)

    note_words = _int_keys(prev.get("notes"))
    other_notes = _int_keys(prev.get("other_notes"))
    mods = _int_keys(prev.get("mods"))
    full = time.time() - prev.get("synced_at", 0) > FULL_REFRESH_AFTER or not note_words
    if full:
        need = list(ids)
    else:
        known = note_words.keys() | other_notes.keys()
        need = [i for i in ids if i not in known]
        recent = [i for i in edited if i in known]
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


# Bump when build_forms changes, so the next sync rebuilds the export even if the deck didn't change.
FORMS_VERSION = 3

# Status of a form, best first. When several cards share a form, the best one wins.
#   ok      : screenshot and audio
#   noaudio : screenshot, no audio
#   missing : no screenshot (and usually no audio either)
STATUS_RANK = {"ok": 0, "noaudio": 1, "missing": 2}


def media_status(media):
    if media["screenshot"] and media["audio"]:
        return "ok"
    if media["screenshot"]:
        return "noaudio"
    return "missing"

# Determiners / pronouns decline like this, but the Wiktionary data lists them only partly
# (alle -> no allen/aller; jeder -> only jedes; dieser -> nothing). Closed class, so spell it out.
_ENDINGS = ("e", "en", "er", "es", "em")
DECLINED = [
    {"all", *("all" + e for e in _ENDINGS)},
    {"jed" + e for e in _ENDINGS},
    {"dies", *("dies" + e for e in _ENDINGS)},
    {"jen" + e for e in _ENDINGS},
    {"welch", *("welch" + e for e in _ENDINGS)},
    {"solch", *("solch" + e for e in _ENDINGS)},
    {"manch", *("manch" + e for e in _ENDINGS)},
    {"andre", "andern", *("ander" + e for e in _ENDINGS)},
    {"einig" + e for e in _ENDINGS},
    {"beid" + e for e in _ENDINGS},
    {"mehrer" + e for e in _ENDINGS},
    {"viel", *("viel" + e for e in _ENDINGS)},
    {"wenig", *("wenig" + e for e in _ENDINGS)},
    {"kein", "keins", *("kein" + e for e in _ENDINGS)},
    {"mein", "meins", *("mein" + e for e in _ENDINGS)},
    {"dein", "deins", *("dein" + e for e in _ENDINGS)},
    {"unser", "unsers", *("unser" + e for e in _ENDINGS), *("unsr" + e for e in _ENDINGS)},
    {"euer", "eures", *("eur" + e for e in _ENDINGS)},
]
DECLINED_OF = {form: group for group in DECLINED for form in group}


def _shares_stem(key, lemma):
    """andere/anderer, solcher/solch: yes.  gerecht/rechen, unterstrich/unterstreichen: no."""
    n = min(len(key), len(lemma)) - 2
    return n >= 3 and key[:n] == lemma[:n]


def lemmas_to_expand(keys, heads, lemmas_of):
    """Lemmas whose forms a key should also get: only for keys that are themselves an inflected
    form (not a headword) and only when key and lemma share the stem (dictionary links like
    gerecht -> rechen are wrong)."""
    return {k: {l for l in lemmas_of.get(k, ()) if _shares_stem(k, l)} for k in keys if k not in heads}


def build_forms(words, forms_of, expand=None):
    """{Anki word: media} -> {lowercase form: "ok"|"noaudio"|"missing"} for every form that should be coloured:
    the word itself, its dictionary forms, the full declension of determiners/pronouns, and the
    lemma + lemma forms from `expand` (andere -> anderer, anderen, anderes, ...)."""
    expand = expand or {}
    forms = {}
    for word, media in words.items():
        key, _ = normalize(word)
        if key is None:
            continue
        status = media_status(media)
        mine = {key} | forms_of.get(key, set()) | DECLINED_OF.get(key, set())
        for lemma in expand.get(key, ()):
            mine |= {lemma} | forms_of.get(lemma, set())
        for form in mine:
            if form not in forms or STATUS_RANK[status] < STATUS_RANK[forms[form]]:
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
                review["Inflected form instead of base word (the base word's forms are matched only if it shares the stem)"].append(
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

    unchanged = (prev.get("forms") is not None and prev.get("forms_version") == FORMS_VERSION
                 and words == prev.get("words")
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
    expand = lemmas_to_expand(keys, heads, lemmas_of)
    extra = set().union(*expand.values()) - keys if expand else set()
    if extra:   # forms of the lemmas behind inflected-form cards (andere -> anderer)
        _, lemma_forms, _ = dictionary.lookup(con, extra)
        forms_of = {**lemma_forms, **forms_of}
    forms = build_forms(words, forms_of, expand)

    _write_atomic(out_json, json.dumps({
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "synced_at": time.time(),
        "forms_version": FORMS_VERSION,
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
