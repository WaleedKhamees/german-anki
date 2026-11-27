"""Example sentences for word cards: length limits, Tatoeba lookup and the field format."""
import time

import requests

from . import config

NUM_EXAMPLES = 3
TATOEBA_URL = "https://api.tatoeba.org/unstable/sentences"
TATOEBA_MAX_CANDIDATES = 25  # how many candidates the LLM gets to choose from

# length in words; short sentences ("Ich gebe auf.") show too little context.
# set_length() changes these for one run (the CLI's --min-words / --max-words).
_settings = config.load()
MIN_WORDS = _settings.example_min_words
MAX_WORDS = _settings.example_max_words
length_overridden = False


def check_length(min_words, max_words):
    if min_words < 1 or max_words < min_words:
        raise ValueError(f"need 1 <= min words <= max words, got {min_words}-{max_words}")


def set_length(min_words=None, max_words=None):
    """Set the example length for this run (or worker request); None takes that bound from .env."""
    global MIN_WORDS, MAX_WORDS, length_overridden
    lo = _settings.example_min_words if min_words is None else min_words
    hi = _settings.example_max_words if max_words is None else max_words
    check_length(lo, hi)
    MIN_WORDS, MAX_WORDS = lo, hi
    # cached LLM answers were made for the .env length
    length_overridden = (lo, hi) != (_settings.example_min_words, _settings.example_max_words)


def format_example(de, en):
    return f"{de} <br> {en}"


def fetch_tatoeba(word):
    """Return a list of (german, english) pairs from Tatoeba, or [] on failure."""
    t0 = time.time()
    params = {
        "q": word,
        "lang": "deu",
        "trans:lang": "eng",
        "showtrans": "matching",
        "sort": "random",
        "word_count": f"{MIN_WORDS}-{MAX_WORDS}",
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
        if not MIN_WORDS <= len(de.split()) <= MAX_WORDS:
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


def pick_longest(candidates):
    """Examples without the LLM: the longest of the (randomly drawn) Tatoeba candidates."""
    longest = sorted(candidates, key=lambda c: len(c[0].split()), reverse=True)[:NUM_EXAMPLES]
    return "<br><br>".join(format_example(de, en) for de, en in longest)


def merge(candidates, llm_examples):
    """Examples field: the longest Tatoeba sentences (real ones first), topped up with the
    LLM's ("de <br> en<br><br>..."). -> (field html, how many came from Tatoeba)."""
    picked = [e for e in pick_longest(candidates).split("<br><br>") if e]
    n_tatoeba = len(picked)
    for ex in (llm_examples or "").split("<br><br>"):
        if len(picked) >= NUM_EXAMPLES:
            break
        if ex.strip() and ex.strip() not in picked:
            picked.append(ex.strip())
    return "<br><br>".join(picked), n_tatoeba
