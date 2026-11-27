"""Word cards: puts the definition and examples (llm, examples), the conjugation table
(verbformen), the article and pronunciation (dwds) and the captured screenshot/audio (media)
together into Anki notes. Used by `danki words` (clipboard), `danki add-capture` (called by
the mpv capture script for every captured word, see queue.py) and `danki regen`."""
import base64
import concurrent.futures
import html
import json
import os
import re
import time

from . import config, dwds, examples, llm, media, verbformen
from .anki import (Anki, MEDIA_TAGS, NO_PRONUNCIATION_TAG, WORD, DEFINITION, CONJUGATION,
                   SCREENSHOT, SOURCE_AUDIO, EXAMPLES, PRONUNCIATION)
from .examples import NUM_EXAMPLES
from .media import notify

# --- Configuration (from ~/.config/danki/.env) ---
SETTINGS = config.load()
DECK_NAME = SETTINGS.deck
MODEL_NAME = SETTINGS.note_type
ASSET_DIR = SETTINGS.assets_dir  # where the mpv capture script saves screenshots/audio
WORD_DELAY = 3  # seconds; retry backoff base (attempt n waits n * WORD_DELAY)
MAX_RETRIES = 3

anki = Anki(SETTINGS.anki_url)


def find_existing_note(word):
    """Return notesInfo dict of the existing note for this word, or None (one request)."""
    notes = anki("notesInfo", timeout=5, query=f'deck:"{DECK_NAME}" Word:"{word}"')
    return notes[0] if notes else None


def fill_missing_media(note, custom_image=None, custom_audio=None):
    """If an existing note lacks its screenshot or its audio, replace BOTH with the newly
    captured pair (so picture and sound come from the same scene). A complete note is
    left alone. Returns the list of field names that were written."""
    fields = note.get("fields", {})

    def is_empty(name):
        return not (fields.get(name, {}).get("value") or "").strip()

    if not (is_empty(SCREENSHOT) or is_empty(SOURCE_AUDIO)):
        return []

    updates = {}
    if custom_image:
        uploaded = media.upload_image(anki, ASSET_DIR, custom_image)
        if uploaded:
            updates[SCREENSHOT] = f"<img src='{uploaded}'>"
    if custom_audio:
        uploaded = media.upload_audio(anki, ASSET_DIR, custom_audio)
        if uploaded:
            updates[SOURCE_AUDIO] = f"[sound:{uploaded}]"

    if updates:
        anki("updateNoteFields", note={"id": note["noteId"], "fields": updates})
        filled_tags = [MEDIA_TAGS[f] for f in updates]
        anki("removeTags", notes=[note["noteId"]], tags=" ".join(filled_tags))
    return list(updates)


def backfill_media_tags():
    """Make the no-screenshot / no-audio tags match the fields of every note in the deck."""
    ids = anki("findNotes", query=f'deck:"{DECK_NAME}"')
    notes = anki("notesInfo", notes=ids, timeout=60)
    for field, tag in MEDIA_TAGS.items():
        empty, filled = [], []
        for n in notes:
            if field not in n["fields"]:
                continue
            has_tag = tag in n["tags"]
            is_empty = not n["fields"][field]["value"].strip()
            if is_empty and not has_tag:
                empty.append(n["noteId"])
            elif not is_empty and has_tag:
                filled.append(n["noteId"])
        if empty:
            anki("addTags", notes=empty, tags=tag, timeout=60)
        if filled:
            anki("removeTags", notes=filled, tags=tag, timeout=60)
        print(f"[+] {tag}: tagged {len(empty)}, untagged {len(filled)}")


def parse_single_line(line):
    """One clipboard line -> (word, None, None); None for a blank line."""
    word = line.strip()
    return (word, None, None) if word else None


def parse_clipboard_input(raw_text):
    cleaned = raw_text.strip()
    if not cleaned:
        return []

    print(f"\n--- Clipboard Content ---\n{cleaned}\n-------------------------")

    lines = [l for l in cleaned.splitlines() if l.strip()]
    words = []
    seen = set()

    for line in lines:
        result = parse_single_line(line)
        if result is None:
            continue
        word, img, audio = result
        if word not in seen:
            seen.add(word)
            words.append((word, img, audio))

    for w, img, audio in words:
        suffix = (f" (image: {img})" if img else "") + (f" (audio: {audio})" if audio else "")
        print(f"[+] Parsed word: '{w}'{suffix}")

    print(f"[+] Total unique words: {len(words)}")
    return words


def upload_conjugation(word, image_path):
    """Store the verbformen table screenshot in Anki's media folder; -> media filename."""
    with open(image_path, "rb") as f:
        encoded_image = base64.b64encode(f.read()).decode("utf-8")
    media_filename = f"verbformen_{word}_{int(time.time())}.png".lower()
    anki("storeMediaFile", filename=media_filename, data=encoded_image)
    return media_filename


def _as_text(value):
    """LLM fields sometimes come back as a list or object instead of a string."""
    if isinstance(value, list):
        return "<br><br>".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value)
    return str(value)


def build_definition(word, llm_data, grammar):
    """Definition field: the LLM's explanation behind an article header. The article comes from
    DWDS only (the LLM's 'article' merely picks between DWDS entries for homographs)."""
    explanation = llm.strip_llm_header(_as_text(llm_data.get("explanation", "Explanation missing.")))
    article = dwds.article(grammar, llm_data.get("article"))
    if article:
        explanation = dwds.noun_header(word, article) + explanation
        print(f"[+] '{word}': article '{article}' from DWDS")
    return explanation


def resolve_is_verb(grammar, llm_data, has_table):
    """Verb or not: DWDS first; when DWDS has no entry, the LLM, then whether verbformen had a
    table. None when none of them could tell."""
    is_verb = dwds.is_verb(grammar)
    if is_verb is None and llm_data is not None:
        v = llm_data.get("is_verb")
        if isinstance(v, str):
            v = v.strip().lower() == "true"
        is_verb = v if isinstance(v, bool) else None
    if is_verb is None:
        is_verb = has_table
    return is_verb


def push_to_anki(word, explanation, examples_html, image_path=None, source_image=None, source_audio=None,
                 is_verb=False, pronunciation=None):
    conjugation = ""
    tags = ["all-german"]
    if is_verb:
        tags.append(VERB_TAG)
    if not source_image:
        tags.append(MEDIA_TAGS[SCREENSHOT])
    if not source_audio:
        tags.append(MEDIA_TAGS[SOURCE_AUDIO])
    if not pronunciation:
        tags.append(NO_PRONUNCIATION_TAG)

    if image_path and os.path.exists(image_path):
        conjugation = f"<img src='{upload_conjugation(word, image_path)}'>"

    anki("addNote", note={
        "deckName": DECK_NAME,
        "modelName": MODEL_NAME,
        "fields": {
            WORD: word,
            DEFINITION: explanation,
            CONJUGATION: conjugation,
            SCREENSHOT: f"<img src='{source_image}'>" if source_image else "",
            SOURCE_AUDIO: f"[sound:{source_audio}]" if source_audio else "",
            EXAMPLES: examples_html,
            PRONUNCIATION: f"[sound:{pronunciation}]" if pronunciation else "",  # DWDS recording
        },
        "options": {"allowDuplicate": False},
        "tags": tags,
    })


def guess_is_noun(word):
    """The table page to try before DWDS has answered: German nouns are capitalised."""
    return word.strip()[:1].isupper()


TATOEBA_GRACE = 0.3  # seconds Tatoeba may take beyond the other lookups before the LLM's examples are used


def capture_table(word, image_path, is_noun):
    """verbformen screenshot into image_path; -> the page's URL, or None (and no stale file)."""
    try:
        return verbformen.capture(word, image_path, is_noun=is_noun)
    except Exception:
        if os.path.exists(image_path):
            os.remove(image_path)
        return None


def table_needs_redo(word, guess_noun, is_noun, table_url):
    """Was the table taken with the wrong guess? Not when the guess ended on the very page the
    right attempt would use (a capitalised non-noun like 'Schön' lands on the search page either way)."""
    if is_noun == guess_noun:
        return False
    return not (not is_noun and table_url == verbformen.urls(word, False)[0])


def process_single_word(word, custom_image=None, custom_audio=None):
    existing = find_existing_note(word)
    if existing:
        filled = fill_missing_media(existing, custom_image, custom_audio)
        if filled:
            print(f"[+] '{word}' already in Anki — filled missing: {', '.join(filled)}")
            return True, "updated: " + ", ".join(filled)
        print(f"[!] Skipped duplicate: {word}")
        return False, "duplicate"

    image_path = f"/tmp/verbformen_{word}.png"

    # Everything starts at once; nothing waits for another site (a card takes as long as its
    # slowest lookup). The table page is guessed from the spelling and checked against DWDS
    # afterwards; the LLM writes its own examples instead of choosing among Tatoeba's.
    guess_noun = guess_is_noun(word)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=6)
    try:
        future_dwds = executor.submit(dwds.fetch, anki, word)
        future_tatoeba = executor.submit(examples.fetch_tatoeba, word)
        future_llm = executor.submit(llm.get_content, word, candidates=[])
        future_image = executor.submit(capture_table, word, image_path, guess_noun)
        future_upload = executor.submit(media.upload_image, anki, ASSET_DIR, custom_image) if custom_image else None
        future_audio = executor.submit(media.upload_audio, anki, ASSET_DIR, custom_audio) if custom_audio else None

        llm_data = future_llm.result()
        img_src = future_upload.result() if future_upload else None
        audio_src = future_audio.result() if future_audio else None
        dwds_grammar, pronunciation = future_dwds.result()
        table_url = future_image.result()
        try:   # Tatoeba is often the slowest; don't let it hold up the card
            tatoeba = future_tatoeba.result(timeout=TATOEBA_GRACE)
        except concurrent.futures.TimeoutError:
            print(f"[!] Tatoeba too slow; using the LLM's examples")
            tatoeba = []
    finally:
        executor.shutdown(wait=False)   # a slow Tatoeba request finishes in the background

    is_noun = dwds.is_noun(word, dwds_grammar)
    if table_needs_redo(word, guess_noun, is_noun, table_url):   # rare: Essen vs essen
        print(f"[!] '{word}': table guessed {'noun' if guess_noun else 'verb'}, DWDS says otherwise; redoing it")
        table_url = capture_table(word, image_path, is_noun)
    final_image_path = image_path if table_url else None

    explanation = build_definition(word, llm_data, dwds_grammar)
    examples_html, n_tatoeba = examples.merge(tatoeba, _as_text(llm_data.get("examples", "")))
    if not examples_html:
        raise ValueError("no example sentences from Tatoeba or the LLM")
    print(f"[+] Examples: {n_tatoeba} from Tatoeba, {examples_html.count('<br><br>') + 1 - n_tatoeba} from LLM")
    is_verb = resolve_is_verb(dwds_grammar, llm_data, final_image_path is not None)
    if is_verb:
        print(f"[+] '{word}' tagged as verb")

    push_to_anki(word, explanation, examples_html, final_image_path, source_image=img_src, source_audio=audio_src,
                 is_verb=is_verb, pronunciation=pronunciation)

    if final_image_path and os.path.exists(final_image_path):
        os.remove(final_image_path)

    llm.remove_from_cache(word)
    return True, "success"


def process_with_retries(word, custom_image=None, custom_audio=None, notify_success=True):
    """Add/update one word, retrying on errors.
    Returns (ok, message, kind) with kind = added | updated | duplicate | failed."""
    start_time = time.time()
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            ok, status = process_single_word(word, custom_image, custom_audio)
            if ok and status.startswith("updated"):
                kind, message = "updated", f"'{word}' existed — {status}"
            elif ok:
                kind, message = "added", f"Card created for '{word}' in {time.time() - start_time:.1f}s"
            else:
                kind, message = "duplicate", f"'{word}' already in Anki with screenshot and audio"
            print(f"[+] {message}")
            if notify_success:
                notify("Anki Auto Success", message)
            return True, message, kind
        except json.JSONDecodeError as e:
            last_error = f"JSON parse error: {e}"
        except Exception as e:
            last_error = str(e)
        print(f"[!] Attempt {attempt}/{MAX_RETRIES} failed for '{word}': {last_error}")
        if attempt < MAX_RETRIES:
            wait = WORD_DELAY * attempt
            print(f"[...] Retrying in {wait}s...")
            time.sleep(wait)
    print(f"[X] '{word}' FAILED after {MAX_RETRIES} attempts: {last_error}")
    return False, last_error, "failed"


def process_capture(word, custom_image=None, custom_audio=None):
    """One word captured in mpv (queue.process). -> (ok, message, kind)"""
    try:
        ok, message, kind = process_with_retries(word, custom_image, custom_audio)
    except Exception as e:   # e.g. AnkiConnect down
        ok, message, kind = False, str(e), "failed"
    if not ok:
        notify(f"Anki Auto: '{word}' failed", message, is_error=True)
    return ok, message, kind


def run_clipboard():
    import pyperclip
    raw_clipboard = ""
    try:
        raw_clipboard = pyperclip.paste().strip()
        if not raw_clipboard:
            notify("Anki Auto Error", "Clipboard is empty.", is_error=True)
            return

        words = parse_clipboard_input(raw_clipboard)
        if not words:
            notify("Anki Auto Error", "No valid words found in clipboard.", is_error=True)
            return

        total = len(words)
        succeeded = []
        failed = []

        notify("Anki Auto Started", f"Processing {total} word(s)...")

        # a failed word doesn't stop the run: the rest still get their cards, and every
        # failure is reported at the end (the words are still in the clipboard to retry)
        for idx, (word, custom_image, custom_audio) in enumerate(words):
            print(f"\n[{idx + 1}/{total}] Processing '{word}'...")
            ok, info, _ = process_with_retries(word, custom_image, custom_audio)
            if ok:
                succeeded.append(word)
            else:
                failed.append((word, info))

        report_lines = []
        if succeeded:
            report_lines.append(f"OK ({len(succeeded)}): {', '.join(succeeded)}")
        if failed:
            for w, err in failed:
                report_lines.append(f"FAIL: {w} - {err}")
        report = "\n".join(report_lines)

        if failed:
            notify("Anki Auto Done (with errors)", report, is_error=True)
        else:
            notify("Anki Auto Done", report)

        print(f"\n{'=' * 40}")
        print(f"Summary: {len(succeeded)} succeeded, {len(failed)} failed")
        print(report)
        print(f"{'=' * 40}")

    except Exception as e:
        print(f"[!] Fatal error: {e}")
        notify("Anki Auto Fatal Error", str(e), is_error=True)


# --- regenerating existing cards (`danki regen`) ---
REGEN_TAG = "regen"
REGEN_PARTS = ("definition", "examples", "conjugation", "pronunciation")
VERB_TAG = "german-verb"


def parse_parts(spec):
    """'examples,definition' -> {'examples', 'definition'}; None/'' -> every part."""
    if not spec:
        return set(REGEN_PARTS)
    parts = {p.strip().lower() for p in spec.split(",") if p.strip()}
    unknown = parts - set(REGEN_PARTS)
    if unknown or not parts:
        raise ValueError(f"unknown part(s) {', '.join(sorted(unknown)) or spec!r}; "
                         f"choose from {', '.join(REGEN_PARTS)}")
    return parts


def anki_quote(text):
    """Escape text for use inside a double-quoted Anki search term (* and _ are wildcards)."""
    return re.sub(r'([\\"*_])', r"\\\1", text)


def regen_query(deck, words=(), query=None):
    """Anki search for the notes to regenerate: the given words, else `query`, else tag:regen."""
    base = f'deck:"{anki_quote(deck)}"'
    if words:
        return base + " (" + " OR ".join(f'Word:"{anki_quote(w)}"' for w in words) + ")"
    if query:
        return f"{base} ({query})"
    return f"{base} tag:{REGEN_TAG}"


def note_word(field_value):
    """The word as it appears in the Word field, without HTML ('<b>Störung</b>' -> 'Störung')."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", field_value)).replace("\xa0", " ")
    return " ".join(text.split())


def regenerate_note(note, parts, hint=None):
    """Rebuild the chosen parts of an existing note in place, so its review history is kept.
    Screenshot and SourceAudio come from the video and are never touched.
    -> (updated field names, parts that could not be fetched and were left as they were)."""
    word = note_word(note["fields"][WORD]["value"])
    if not word:
        raise ValueError("the Word field is empty")
    note_id = note["noteId"]
    image_path = os.path.join("/tmp", f"verbformen_regen_{note_id}.png")
    llm.remove_from_cache(word)   # never reuse an old answer

    # DWDS gives the article (definition), noun-or-verb (conjugation) and the recording
    # (pronunciation); examples alone need none of it
    need_dwds = bool(parts & {"definition", "conjugation", "pronunciation"})

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_dwds = (executor.submit(dwds.fetch, anki, word, with_audio="pronunciation" in parts)
                       if need_dwds else None)
        # examples alone: Tatoeba first, and the LLM (a paid request) only when it is short
        # of sentences; a hint or a new definition needs the LLM anyway
        tatoeba_examples, candidates = None, None
        if parts & {"definition", "examples"} == {"examples"} and not hint:
            candidates = examples.fetch_tatoeba(word)
            if len(candidates) >= NUM_EXAMPLES:
                tatoeba_examples = examples.pick_longest(candidates)
                print(f"[+] Examples: {NUM_EXAMPLES} from Tatoeba, LLM not needed")
        future_llm = (executor.submit(llm.get_content, word, hint=hint, use_cache=False,
                                      candidates=candidates)
                      if parts & {"definition", "examples"} and tatoeba_examples is None else None)
        future_image = (executor.submit(
            lambda: verbformen.capture(word, image_path, is_noun=dwds.is_noun(word, future_dwds.result()[0])))
            if "conjugation" in parts else None)

        llm_data = future_llm.result() if future_llm else None
        table = None
        if future_image:
            try:
                future_image.result()
                table = image_path
            except Exception:
                pass
        grammar, pronunciation = future_dwds.result() if future_dwds else (None, None)

    updates, kept = {}, []
    if "definition" in parts:
        updates[DEFINITION] = build_definition(word, llm_data, grammar)
    if "examples" in parts:
        updates[EXAMPLES] = tatoeba_examples or _as_text(llm_data.get("examples", "Examples missing."))
    if "conjugation" in parts:
        if table:
            updates[CONJUGATION] = f"<img src='{upload_conjugation(word, table)}'>"
            os.remove(table)
        else:
            kept.append("conjugation")
    if "pronunciation" in parts:
        if pronunciation:
            updates[PRONUNCIATION] = f"[sound:{pronunciation}]"
        else:
            kept.append("pronunciation")
    if not updates:
        raise RuntimeError(f"nothing could be regenerated (failed: {', '.join(kept)})")

    anki("updateNoteFields", note={"id": note_id, "fields": updates})

    add, remove = [], [REGEN_TAG]
    # the verb tag is only re-checked when DWDS was asked (it is the reliable source)
    is_verb = (resolve_is_verb(grammar, llm_data, (table is not None) if "conjugation" in parts else None)
               if grammar is not None else None)
    if is_verb is not None:
        (add if is_verb else remove).append(VERB_TAG)
    if PRONUNCIATION in updates:
        remove.append(NO_PRONUNCIATION_TAG)
    if add:
        anki("addTags", notes=[note_id], tags=" ".join(add))
    anki("removeTags", notes=[note_id], tags=" ".join(remove))

    llm.remove_from_cache(word)
    return list(updates), kept


def run_regen(words=(), query=None, only=None, hint=None, dry_run=False):
    """Regenerate the notes picked by regen_query. Exit status 1 if any note failed."""
    parts = parse_parts(only)
    part_names = ", ".join(p for p in REGEN_PARTS if p in parts)
    search = regen_query(DECK_NAME, words, query)
    notes = [n for n in anki("notesInfo", notes=anki("findNotes", query=search), timeout=60)
             if WORD in n["fields"]]
    if not notes:
        print(f"[!] No notes match: {search}")
        return 1 if (words or query) else 0

    found = [note_word(n["fields"][WORD]["value"]) for n in notes]
    print(f"[+] {len(notes)} note(s) match {search}")
    print(f"[+] Regenerating: {part_names} (Screenshot and SourceAudio are kept)")
    for w in found:
        print(f"    - {w}")
    if words:
        missing = [w for w in words if w.lower() not in {f.lower() for f in found}]
        if missing:
            print(f"[!] Not found in '{DECK_NAME}': {', '.join(missing)}")
    if dry_run:
        return 0
    if hint and len(notes) > 1:
        print(f"[!] The hint '{hint}' is used for all {len(notes)} notes")

    succeeded, failed = [], []
    for idx, note in enumerate(notes, 1):
        word = found[idx - 1]
        print(f"\n[{idx}/{len(notes)}] Regenerating '{word}'...")
        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                updated, kept = regenerate_note(note, parts, hint)
                msg = f"updated {', '.join(updated)}"
                if kept:
                    msg += f"; kept old {', '.join(kept)} (fetch failed)"
                print(f"[+] '{word}': {msg}")
                succeeded.append(word)
                break
            except Exception as e:
                last_error = str(e)
                print(f"[!] Attempt {attempt}/{MAX_RETRIES} failed for '{word}': {last_error}")
                if attempt < MAX_RETRIES:
                    time.sleep(WORD_DELAY * attempt)
        else:
            failed.append((word, last_error))

    report = "\n".join(([f"OK ({len(succeeded)}): {', '.join(succeeded)}"] if succeeded else [])
                       + [f"FAIL: {w} - {err}" for w, err in failed])
    print(f"\n{'=' * 40}\nSummary: {len(succeeded)} regenerated, {len(failed)} failed\n{report}\n{'=' * 40}")
    if failed and not (words or query):
        print(f"[!] Failed notes keep the '{REGEN_TAG}' tag, so running again retries them.")
    notify("Anki Regen Done" + (" (with errors)" if failed else ""), report, is_error=bool(failed))
    return 1 if failed else 0
