"""DWDS: grammar (article, verb or noun) and the pronunciation recording of a word."""
import base64
import os
import re
import time
import urllib.parse

import requests

DWDS_WB_URL = "https://www.dwds.de/wb/"
DWDS_AUDIO_RE = re.compile(r'<source[^>]+src="(https://www\.dwds\.de/audio/[^"]+\.mp3)"')

# "Form / Grammatik" line of each DWDS entry, e.g. "Substantiv (Femininum)", "Verb", "Adjektiv".
# A page has one such line per entry (homographs like "Leiter" have two).
DWDS_GRAMMAR_RE = re.compile(
    r'Form / Grammatik</span>.*?<span class="dwdswb-ft-blocktext"><span>([^<]+)</span>', re.S)
GENDER_ARTICLES = {"Maskulinum": "der", "Femininum": "die", "Neutrum": "das"}
ARTICLE_COLORS = {"der": "#00aaff", "die": "#ff0000", "das": "#00ff00"}


def parse_grammar(page_html):
    """-> list of grammar labels, one per DWDS entry on the page."""
    return [g.strip() for g in DWDS_GRAMMAR_RE.findall(page_html)]


def is_verb(grammar):
    """True/False from the DWDS grammar labels, or None when DWDS had no entry.
    Participles ("partizipiales Adjektiv") are not verbs."""
    if not grammar:
        return None
    return any(g == "Verb" for g in grammar)


def article(grammar, hint=None):
    """Article(s) for a noun from the DWDS grammar labels, e.g. "die" or "der/die"; None if not a noun.
    With several noun entries (homographs: die/der Leiter) `hint` (the article the LLM gave for the
    meaning it defined) picks the entry; it is never used unless DWDS lists that article."""
    entries = []
    for g in grammar:
        m = re.match(r"Substantiv \(([^)]*)\)", g)
        if m:
            arts = [GENDER_ARTICLES[x.strip()] for x in m.group(1).split(",") if x.strip() in GENDER_ARTICLES]
            if arts:
                entries.append(sorted(set(arts), key=list(ARTICLE_COLORS).index))
    if not entries:
        return None
    chosen = next((e for e in entries if hint in e), entries[0]) if len(entries) > 1 else entries[0]
    return "/".join(chosen)


def is_noun(word, grammar):
    """Should the card get a noun declension table? DWDS lists a noun entry, and — when the same
    page also has a verb entry — the word was written capitalised (Essen vs essen)."""
    if article(grammar) is None:
        return False
    return not is_verb(grammar) or word.strip()[:1].isupper()


def noun_header(word, art):
    """'<b><font color=..>die</font> Brille</b>: ' (each article coloured; 'der/die' for two genders)."""
    arts = "/".join(f"<font color='{ARTICLE_COLORS[a]}'>{a}</font>" for a in art.split("/"))
    word = word.strip()
    return f"<b>{arts} {word[:1].upper() + word[1:]}</b>: "


def fetch(anki, word, with_audio=True):
    """Look `word` up on DWDS once. -> (grammar labels, media filename of the recording or None).
    The recording is stored in Anki's media folder; with_audio=False only reads the grammar."""
    t0 = time.time()
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        page = requests.get(DWDS_WB_URL + urllib.parse.quote(word.strip()), headers=headers, timeout=10)
        if page.status_code == 404:
            print(f"[!] DWDS: no entry for '{word}'")
            return [], None
        page.raise_for_status()
    except requests.RequestException as e:
        print(f"[!] DWDS lookup failed for '{word}': {e}")
        return [], None

    grammar = parse_grammar(page.text)
    if not with_audio:
        print(f"[⏱️] DWDS lookup took: {time.time() - t0:.2f} seconds")
        return grammar, None
    match = DWDS_AUDIO_RE.search(page.text)
    if not match:
        print(f"[!] DWDS: no pronunciation for '{word}'")
        return grammar, None
    audio_url = match.group(1)
    try:
        audio = requests.get(audio_url, headers=headers, timeout=10)
        audio.raise_for_status()
    except requests.RequestException as e:
        print(f"[!] DWDS audio download failed for '{word}': {e}")
        return grammar, None

    media_filename = "dwds_" + os.path.basename(urllib.parse.urlparse(audio_url).path).lower()
    anki("storeMediaFile", filename=media_filename, data=base64.b64encode(audio.content).decode("ascii"))
    print(f"[⏱️] DWDS lookup + pronunciation ({media_filename}) took: {time.time() - t0:.2f} seconds")
    return grammar, media_filename
