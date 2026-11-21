"""Card creation: definition + examples (LLM/Tatoeba), conjugation table (verbformen),
captured screenshot/audio, DWDS pronunciation. Used by `german-anki words` (clipboard)
and `german-anki add-row` (called by the mpv capture script for every captured word)."""
import os
import re
import json
import time
import base64
import io
import urllib.parse
import requests
import concurrent.futures
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright
from PIL import Image

from . import config
from .anki import (Anki, MEDIA_TAGS, NO_PRONUNCIATION_TAG, WORD, DEFINITION, CONJUGATION,
                   SCREENSHOT, SOURCE_AUDIO, EXAMPLES, PRONUNCIATION)

# --- Configuration (from ~/.config/german-anki/.env) ---
SETTINGS = config.load()
OPENROUTER_API_KEY = SETTINGS.api_key
ANKI_CONNECT_URL = SETTINGS.anki_url
DECK_NAME = SETTINGS.deck
MODEL_NAME = SETTINGS.note_type
LLM_MODEL = SETTINGS.llm_model
ASSET_DIR = str(SETTINGS.assets_dir)  # where the mpv capture script saves screenshots/audio
MEDIA_SEARCH_DIRS = [d for d in (ASSET_DIR, SETTINGS.obsidian_vault and str(SETTINGS.obsidian_vault)) if d]
AUDIO_EXTS = {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".aac"}
WORD_DELAY = 3  # seconds between processing each word (for verbformen scraping)
MAX_RETRIES = 3

NUM_EXAMPLES = 3
TATOEBA_URL = "https://api.tatoeba.org/unstable/sentences"
TATOEBA_MIN_WORDS = 7    # short sentences ("Ich gebe auf.") show too little context
TATOEBA_MAX_WORDS = 20
TATOEBA_MAX_CANDIDATES = 25  # how many candidates the LLM gets to choose from


def PROMPT(word, candidates):
    if candidates:
        listing = "\n".join(f"{i}. {de} | {en}" for i, (de, en) in enumerate(candidates))
        candidate_block = (
            "Here are real example sentences from the Tatoeba corpus (index. German | English):\n"
            f"{listing}\n\n"
            f"'picked': a list of up to {NUM_EXAMPLES} indices from the list above. "
            "Choose sentences that together cover the DIFFERENT common everyday meanings/uses of the word "
            "(e.g. different senses, reflexive vs. non-reflexive, typical prepositions or collocations). "
            "Prefer natural, everyday situations and avoid near-duplicates. "
            "Skip sentences that are odd, unnatural, mistranslated, or use the word in a rare sense. "
            "Return [] if none are good.\n"
            f"'examples': write ONLY as many new examples as needed so that picked + examples = {NUM_EXAMPLES}, "
            "each covering an everyday use NOT already shown by the picked sentences.\n"
        )
    else:
        candidate_block = (
            "'picked': always [].\n"
            f"'examples': write exactly {NUM_EXAMPLES} examples, each showing a DIFFERENT common everyday "
            "meaning or use of the word (different senses, typical prepositions, collocations, or contexts). "
            "If the word really has only one meaning, vary the situation instead (home, work, shopping, friends, travel).\n"
        )

    return (
        f"You are making a German vocabulary flashcard for the word '{word}'.\n"
        "Respond ONLY with a valid JSON object with exactly four keys: 'is_verb', 'explanation', 'picked', 'examples'.\n\n"

        "'is_verb': true if the word is a German verb (infinitive, including reflexive like 'sich freuen' "
        "or separable verbs), otherwise false. JSON boolean, not a string.\n\n"

        "'explanation': ONE short German sentence (max 12 words, A2/B1 vocabulary) that defines the word. "
        "No example situations, no filler. If the word has two clearly different common meanings, "
        "give both separated by ' / ' and keep each short. "
        "If the word is a noun, start with the article and noun in exactly this HTML, followed by ': ':\n"
        "- Masculine -> <b><font color='#00aaff'>der</font> Word</b>: \n"
        "- Feminine -> <b><font color='#ff0000'>die</font> Word</b>: \n"
        "- Neuter -> <b><font color='#00ff00'>das</font> Word</b>: \n"
        "Otherwise start the definition directly.\n\n"

        f"{candidate_block}"
        "Each new example is an object {\"de\": \"...\", \"en\": \"...\"}: "
        "'de' is ONE natural German sentence (8-18 words) the way people actually speak in daily life; "
        "'en' is its fluent English translation. No numbering, no HTML, no extra text.\n\n"

        "Do not use markdown or code fences. The response must be valid JSON."
    )


def fetch_tatoeba_examples(word):
    """Return a list of (german, english) pairs from Tatoeba, or [] on failure."""
    t0 = time.time()
    params = {
        "q": word,
        "lang": "deu",
        "trans:lang": "eng",
        "showtrans": "matching",
        "sort": "random",
        "word_count": f"{TATOEBA_MIN_WORDS}-{TATOEBA_MAX_WORDS}",
        "limit": 50,
    }
    try:
        res = requests.get(TATOEBA_URL, params=params, timeout=8)
        res.raise_for_status()
        data = res.json().get("data", [])
    except Exception as e:
        print(f"[!] Tatoeba lookup failed for '{word}': {e}")
        return []

    pairs, seen = [], set()
    for s in data:
        de = (s.get("text") or "").strip()
        if not de or s.get("is_unapproved") or de.lower() in seen:
            continue
        if not TATOEBA_MIN_WORDS <= len(de.split()) <= TATOEBA_MAX_WORDS:
            continue
        translations = [t for t in s.get("translations", []) if not t.get("is_unapproved")]
        if not translations:
            continue
        # prefer direct translations over ones linked through a third language
        translations.sort(key=lambda t: not t.get("is_direct"))
        seen.add(de.lower())
        pairs.append((de, translations[0]["text"].strip()))
        if len(pairs) >= TATOEBA_MAX_CANDIDATES:
            break

    print(f"[⏱️] Tatoeba: {len(pairs)} usable sentence(s) in {time.time() - t0:.2f} seconds")
    return pairs


def format_example(de, en):
    return f"{de} <br> {en}"


CACHE_FILE = str(SETTINGS.cache_dir / "llm_cache.json")

def notify(title, message, is_error=False):
    urgency = "critical" if is_error else "normal"
    subprocess.run(["notify-send", "-u", urgency, title, message])

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_cache(cache_data):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=4)

def remove_from_cache(word):
    cache = load_cache()
    if word in cache:
        del cache[word]
        save_cache(cache)

anki = Anki(ANKI_CONNECT_URL)


def find_existing_note(word):
    """Return notesInfo dict of the existing note for this word, or None."""
    ids = anki("findNotes", timeout=2, query=f'deck:"{DECK_NAME}" Word:"{word}"')
    if not ids:
        return None
    return anki("notesInfo", notes=ids[:1])[0]


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
        uploaded = find_and_upload_obsidian_image(custom_image)
        if uploaded:
            updates[SCREENSHOT] = f"<img src='{uploaded}'>"
        else:
            print(f"[!] Image '{custom_image}' not found in {MEDIA_SEARCH_DIRS}")
    if custom_audio:
        uploaded = find_and_upload_audio(custom_audio)
        if uploaded:
            updates[SOURCE_AUDIO] = f"[sound:{uploaded}]"

    if updates:
        anki("updateNoteFields", note={"id": note["noteId"], "fields": updates})
        filled_tags = [MEDIA_TAGS[f] for f in updates]
        anki("removeTags", notes=[note["noteId"]], tags=" ".join(filled_tags))
    return list(updates)


DWDS_WB_URL = "https://www.dwds.de/wb/"
DWDS_AUDIO_RE = re.compile(r'<source[^>]+src="(https://www\.dwds\.de/audio/[^"]+\.mp3)"')


def fetch_dwds_pronunciation(word):
    """Download the DWDS recording of `word` into Anki's media folder.
    Returns the media filename, or None when DWDS has no recording (or can't be reached)."""
    t0 = time.time()
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        page = requests.get(DWDS_WB_URL + urllib.parse.quote(word.strip()), headers=headers, timeout=10)
        if page.status_code == 404:
            print(f"[!] DWDS: no entry for '{word}'")
            return None
        page.raise_for_status()
        match = DWDS_AUDIO_RE.search(page.text)
        if not match:
            print(f"[!] DWDS: no pronunciation for '{word}'")
            return None
        audio_url = match.group(1)
        audio = requests.get(audio_url, headers=headers, timeout=10)
        audio.raise_for_status()
    except requests.RequestException as e:
        print(f"[!] DWDS lookup failed for '{word}': {e}")
        return None

    media_filename = "dwds_" + os.path.basename(urllib.parse.urlparse(audio_url).path).lower()
    anki("storeMediaFile", filename=media_filename, data=base64.b64encode(audio.content).decode("ascii"))
    print(f"[⏱️] DWDS pronunciation ({media_filename}) took: {time.time() - t0:.2f} seconds")
    return media_filename


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

def get_openrouter_content(word):
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY is missing. Please check your .env file.")

    cache = load_cache()
    if word in cache:
        print(f"[*] Using cached LLM response for '{word}'. Skipping API call.")
        return cache[word]

    candidates = fetch_tatoeba_examples(word)

    url = "https://openrouter.ai/api/v1/chat/completions"
    payload = {
        "model": f"{LLM_MODEL}",
        "reasoning": {"effort": "none"},
        "messages": [{"role": "user", "content": PROMPT(word, candidates)}]
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    t0 = time.time()
    # 15 SECOND TIMEOUT FOR AI: If it hangs, it crashes instantly instead of freezing your script.
    response = requests.post(url, json=payload, headers=headers, timeout=15)
    response.raise_for_status()
    print(f"[⏱️] AI Generation took: {time.time() - t0:.2f} seconds")

    data = response.json()
    content = data['choices'][0]['message']['content'].strip()

    if content.startswith("```json"):
        content = content[7:-3]
    elif content.startswith("```"):
        content = content[3:-3]

    llm = json.loads(content.strip())

    # Real Tatoeba sentences first, then LLM-written ones to fill the gaps.
    examples = []
    for idx in llm.get("picked") or []:
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(candidates) and format_example(*candidates[idx]) not in examples:
            examples.append(format_example(*candidates[idx]))
    n_tatoeba = len(examples)

    for ex in llm.get("examples") or []:
        if len(examples) >= NUM_EXAMPLES:
            break
        if isinstance(ex, dict) and ex.get("de"):
            examples.append(format_example(ex["de"].strip(), (ex.get("en") or "").strip()))
        elif isinstance(ex, str) and ex.strip():
            examples.append(ex.strip())

    if not examples:
        raise ValueError("LLM returned no usable examples")
    print(f"[+] Examples: {n_tatoeba} from Tatoeba, {len(examples) - n_tatoeba} from LLM")

    result = {
        "is_verb": llm.get("is_verb"),
        "explanation": llm.get("explanation", "Explanation missing."),
        "examples": "<br><br>".join(examples),
    }
    cache[word] = result
    save_cache(cache)
    return result

def capture_conjugation(word, output_path):
    t0 = time.time()
    try: 
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(color_scheme="dark")
            page = context.new_page()
            
            page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "font"] else route.continue_())
            page.goto(f"https://www.verbformen.com/conjugation/?w={word}")
            
            try:
                page.evaluate("""
                    const banner = document.querySelector('.cmpwrapper');
                    if (banner) { banner.remove(); }
                """)
                page.wait_for_timeout(200)
            except Exception:
                pass
            
            locator = page.locator("section > .rAufZu")
            count = locator.count()

            if count < 2:
                raise Exception(f"Only found {count} matching elements. (Word might not be a verb)")

            table_locator = locator.nth(1)
            table_locator.wait_for(state="visible", timeout=10000)
            table_locator.screenshot(path=output_path)
            browser.close()
            print(f"[⏱️] Screenshot capture took: {time.time() - t0:.2f} seconds")
    except Exception as e:
        print(f"[⏱️] Screenshot failed/skipped after {time.time() - t0:.2f} seconds: {e}")
        raise

def parse_single_line(line):
    cleaned = line.strip()
    if not cleaned:
        return None

    custom_image = None
    custom_audio = None
    word = cleaned

    for raw_filename in re.findall(r'!\[\[\s*([^\]|\\]+)', cleaned):
        name = Path(urllib.parse.unquote(raw_filename.strip())).name
        if Path(name).suffix.lower() in AUDIO_EXTS:
            custom_audio = custom_audio or name
        else:
            custom_image = custom_image or name

    if cleaned.startswith("|"):
        columns = [c.strip() for c in cleaned.strip("|").split("|")]
        if columns:
            word = columns[0].strip()

    if not word:
        return None

    return (word, custom_image, custom_audio)


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

def find_media_file(filename):
    """Look for a captured file in ./assets first, then the Obsidian vault."""
    for d in MEDIA_SEARCH_DIRS:
        root = Path(d).expanduser()
        if not root.exists():
            continue
        direct = root / filename
        if direct.is_file():
            return direct
        lower_filename = filename.lower()
        match = next((p for p in root.rglob("*") if p.name.lower() == lower_filename), None)
        if match:
            return match
    return None


def find_and_upload_audio(filename):
    audio_file = find_media_file(filename)
    if not audio_file:
        print(f"[!] Audio '{filename}' not found in {MEDIA_SEARCH_DIRS}")
        return None
    media_filename = audio_file.name.lower()
    res = requests.post(ANKI_CONNECT_URL, json={
        "action": "storeMediaFile",
        "version": 6,
        "params": {"filename": media_filename, "path": str(audio_file.resolve())}
    }).json()
    if res.get("error"):
        raise Exception(f"Audio upload failed: {res['error']}")
    return media_filename


def find_and_upload_obsidian_image(filename):
    t0 = time.time()
    image_file = find_media_file(filename)
    if not image_file:
        return None

    with open(image_file, "rb") as f:
        img = Image.open(f)
        img.load()
        if img.mode in ("RGBA", "LA"):
            img = img.convert("RGBA")
        else:
            img = img.convert("RGB")
        w, h = img.size
        img = img.resize((w // 2, h // 2), Image.LANCZOS)
        buf = io.BytesIO()
        fmt = "PNG" if image_file.suffix.lower() == ".png" else "WEBP"
        img.save(buf, format=fmt, optimize=True)
        encoded_image = base64.b64encode(buf.getvalue()).decode("utf-8")

    media_filename = image_file.name.lower()

    media_payload = {
        "action": "storeMediaFile",
        "version": 6,
        "params": {
            "filename": media_filename,
            "data": encoded_image
        }
    }
    
    requests.post(ANKI_CONNECT_URL, json=media_payload)
    print(f"[⏱️] Vault Image Search & Upload took: {time.time() - t0:.2f} seconds")
    return media_filename

def push_to_anki(word, explanation, examples, image_path=None, source_image=None, source_audio=None, is_verb=False, pronunciation=None):
    conjugation = ""
    tags = ["all-german"]
    if is_verb:
        tags.append("german-verb")
    if not source_image:
        tags.append(MEDIA_TAGS[SCREENSHOT])
    if not source_audio:
        tags.append(MEDIA_TAGS[SOURCE_AUDIO])
    if not pronunciation:
        tags.append(NO_PRONUNCIATION_TAG)

    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as f:
            encoded_image = base64.b64encode(f.read()).decode("utf-8")
        
        media_filename = f"verbformen_{word}_{int(time.time())}.png".lower()
        media_payload = {
            "action": "storeMediaFile",
            "version": 6,
            "params": {
                "filename": media_filename,
                "data": encoded_image
            }
        }
        requests.post(ANKI_CONNECT_URL, json=media_payload)
        conjugation = f"<img src='{media_filename}'>"

    note_payload = {
        "action": "addNote",
        "version": 6,
        "params": {
            "note": {
                "deckName": DECK_NAME,
                "modelName": MODEL_NAME,
                "fields": {
                    WORD: word,
                    DEFINITION: explanation,
                    CONJUGATION: conjugation,
                    SCREENSHOT: f"<img src='{source_image}'>" if source_image else "",
                    SOURCE_AUDIO: f"[sound:{source_audio}]" if source_audio else "",
                    EXAMPLES: examples,
                    PRONUNCIATION: f"[sound:{pronunciation}]" if pronunciation else "",  # DWDS recording
                },
                "options": {
                    "allowDuplicate": False
                },
                "tags": tags
            }
        }
    }
    
    res = requests.post(ANKI_CONNECT_URL, json=note_payload).json()
    if res.get("error"):
        raise Exception(res.get("error"))

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
    final_image_path = None
    img_src = None

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_llm = executor.submit(get_openrouter_content, word)
        future_image = executor.submit(capture_conjugation, word, image_path)
        future_upload = executor.submit(find_and_upload_obsidian_image, custom_image) if custom_image else None
        future_audio = executor.submit(find_and_upload_audio, custom_audio) if custom_audio else None
        future_dwds = executor.submit(fetch_dwds_pronunciation, word)

        llm_data = future_llm.result()

        try:
            future_image.result()
            final_image_path = image_path
        except Exception:
            final_image_path = None

        if future_upload:
            uploaded_filename = future_upload.result()
            img_src = uploaded_filename if uploaded_filename else custom_image

        audio_src = future_audio.result() if future_audio else None
        pronunciation = future_dwds.result()

    explanation = llm_data.get("explanation", "Explanation missing.")
    examples = llm_data.get("examples", "Examples missing.")

    if isinstance(explanation, list):
        explanation = "<br><br>".join([str(item) for item in explanation])
    elif isinstance(explanation, dict):
        explanation = json.dumps(explanation)

    if isinstance(examples, list):
        examples = "<br><br>".join([str(item) for item in examples])
    elif isinstance(examples, dict):
        examples = json.dumps(examples)

    explanation = str(explanation)
    examples = str(examples)

    # LLM decides; fall back to whether verbformen had a conjugation table (e.g. old cache entries).
    is_verb = llm_data.get("is_verb")
    if isinstance(is_verb, str):
        is_verb = is_verb.strip().lower() == "true"
    if not isinstance(is_verb, bool):
        is_verb = final_image_path is not None
    if is_verb:
        print(f"[+] '{word}' tagged as verb")

    push_to_anki(word, explanation, examples, final_image_path, source_image=img_src, source_audio=audio_src,
                 is_verb=is_verb, pronunciation=pronunciation)

    if final_image_path and os.path.exists(final_image_path):
        os.remove(final_image_path)

    remove_from_cache(word)
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


def process_row(row):
    """Single markdown row from the mpv capture script. Exit status 0 = row is done."""
    parsed = parse_single_line(row)
    if not parsed:
        print("RESULT: could not parse row")
        return 1
    word, custom_image, custom_audio = parsed
    try:
        ok, message, kind = process_with_retries(word, custom_image, custom_audio)
    except Exception as e:   # e.g. AnkiConnect down
        ok, message, kind = False, str(e), "failed"
    if not ok:
        notify(f"Anki Auto: '{word}' failed", message, is_error=True)
    print(f"STATUS: {kind}")   # read by german-word-capture.lua: only 'added' goes to the done file
    print(f"RESULT: {message}")
    return 0 if ok else 1


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

        for idx, (word, custom_image, custom_audio) in enumerate(words):
            print(f"\n[{idx + 1}/{total}] Processing '{word}'...")
            ok, info, _ = process_with_retries(word, custom_image, custom_audio)
            if ok:
                succeeded.append(word)
            else:
                failed.append((word, info))
                notify(f"Anki Auto Stopped: '{word}' failed", info, is_error=True)
                remaining = words[idx + 1:]
                if remaining:
                    skipped_words = [w for w, _, _ in remaining]
                    print(f"[X] Stopping. Skipped remaining {len(remaining)} word(s): {', '.join(skipped_words)}")
                break

            if idx < total - 1:
                print(f"[...] Waiting {WORD_DELAY}s before next word...")
                time.sleep(WORD_DELAY)

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
