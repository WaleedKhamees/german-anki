"""The definition and example sentences from the LLM (OpenRouter), with a per-word cache."""
import json
import os
import re
import time

import requests

from . import config, examples
from .examples import NUM_EXAMPLES, format_example

_settings = config.load()
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
CACHE_FILE = str(_settings.cache_dir / "llm_cache.json")


def PROMPT(word, candidates, hint=None):
    hint_block = (f"The card is for this meaning/use of the word: {hint}. "
                  "Define and illustrate THAT meaning first.\n\n") if hint else ""
    if candidates:
        listing = "\n".join(f"{i}. {de} | {en}" for i, (de, en) in enumerate(candidates))
        candidate_block = (
            "Here are real example sentences from the Tatoeba corpus (index. German | English):\n"
            f"{listing}\n\n"
            f"'picked': a list of up to {NUM_EXAMPLES} indices from the list above. "
            "Choose sentences that together cover the DIFFERENT common everyday meanings/uses of the word "
            "(e.g. different senses, reflexive vs. non-reflexive, typical prepositions or collocations). "
            "Prefer natural, everyday situations and avoid near-duplicates. "
            "Among good candidates, prefer the longer ones that give more context. "
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
        f"{hint_block}"
        "Respond ONLY with a valid JSON object with exactly five keys: 'is_verb', 'article', 'explanation', 'picked', 'examples'.\n\n"

        "'is_verb': true if the word is a German verb (infinitive, including reflexive like 'sich freuen' "
        "or separable verbs), otherwise false. JSON boolean, not a string.\n\n"

        "'article': if the word is a noun in the meaning you define, its article \"der\", \"die\" or \"das\"; "
        "otherwise null.\n\n"

        "'explanation': a short German definition of the word, AT MOST 20 words in total (A2/B1 vocabulary). "
        "Use the words to cover the word's common meanings, not to explain one meaning at length. "
        "No example situations, no filler. If the word has several clearly different common meanings, "
        "give them separated by ' / ' and keep each short. "
        "Plain text only: no HTML, and do not start with the article or the word itself.\n\n"

        f"{candidate_block}"
        "Each new example is an object {\"de\": \"...\", \"en\": \"...\"}: "
        f"'de' is ONE natural German sentence ({examples.MIN_WORDS}-{examples.MAX_WORDS} words) the way people actually speak in daily life; "
        "'en' is its fluent English translation. No numbering, no HTML, no extra text.\n\n"

        "Do not use markdown or code fences. The response must be valid JSON."
    )


# a header the LLM wrote anyway (or one from an old cache entry)
LLM_HEADER_RE = re.compile(r"^\s*<b>\s*(?:<font[^>]*>\s*(?:der|die|das)\s*</font>[\s/]*)+[^<]*</b>\s*:?\s*", re.I)


def strip_llm_header(explanation):
    return LLM_HEADER_RE.sub("", explanation).strip()


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


def get_content(word, hint=None, use_cache=True, candidates=None):
    """-> {is_verb, article, explanation, examples} for `word`; examples are Tatoeba sentences the
    LLM picked from `candidates` (fetched when None), topped up with ones it wrote."""
    if not _settings.api_key:
        raise ValueError("OPENROUTER_API_KEY is missing. Please check your .env file.")

    cache = load_cache()
    if use_cache and not hint and not examples.length_overridden and word in cache:
        print(f"[*] Using cached LLM response for '{word}'. Skipping API call.")
        return cache[word]

    if candidates is None:
        candidates = examples.fetch_tatoeba(word)

    payload = {
        "model": _settings.llm_model,
        "reasoning": {"effort": "none"},
        "messages": [{"role": "user", "content": PROMPT(word, candidates, hint)}]
    }
    headers = {
        "Authorization": f"Bearer {_settings.api_key}",
        "Content-Type": "application/json"
    }

    t0 = time.time()
    # a hanging request fails after 15 s instead of freezing the capture
    response = requests.post(OPENROUTER_URL, json=payload, headers=headers, timeout=15)
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
    picked = []
    for idx in llm.get("picked") or []:
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(candidates) and format_example(*candidates[idx]) not in picked:
            picked.append(format_example(*candidates[idx]))
    n_tatoeba = len(picked)

    for ex in llm.get("examples") or []:
        if len(picked) >= NUM_EXAMPLES:
            break
        if isinstance(ex, dict) and ex.get("de"):
            picked.append(format_example(ex["de"].strip(), (ex.get("en") or "").strip()))
        elif isinstance(ex, str) and ex.strip():
            picked.append(ex.strip())

    if not picked:
        raise ValueError("LLM returned no usable examples")
    if candidates:   # without candidates the caller combines them with Tatoeba itself
        print(f"[+] Examples: {n_tatoeba} from Tatoeba, {len(picked) - n_tatoeba} from LLM")

    result = {
        "is_verb": llm.get("is_verb"),
        "article": llm.get("article"),
        "explanation": llm.get("explanation", "Explanation missing."),
        "examples": "<br><br>".join(picked),
    }
    cache[word] = result
    save_cache(cache)
    return result
