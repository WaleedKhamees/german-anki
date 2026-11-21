"""Turning a deck's Word field into the lookup key used for subtitle colouring."""
import html
import re

ARTICLE = re.compile(r"^(der|die|das)\s+", re.I)
ODD_CHARS = re.compile(r"[^\w\s'’\-]")


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s).replace("\xa0", " ")
    return " ".join(s.split())


def normalize(raw):
    """Word field -> (lowercase lookup key or None for phrases, list of issues found)."""
    s = strip_html(raw)
    issues = []
    if "|" in s:
        issues.append("'|' separable-verb mark")
        s = s.replace("|", "")
    if re.search(r"\(.*?\)", s):
        issues.append("note in parentheses")
        s = re.sub(r"\s*\(.*?\)\s*", " ", s).strip()
    s = ARTICLE.sub("", s)
    if s.lower().startswith("sich "):
        s = s[5:]
    if ODD_CHARS.search(s):
        issues.append("punctuation / emoji / symbols")
        s = ODD_CHARS.sub("", s)
    s = " ".join(s.split()).strip("-'’ ")
    if not s:
        return None, issues + ["empty after cleaning"]
    if " " in s:
        return None, issues + ["phrase / several words"]
    return s.lower(), issues
