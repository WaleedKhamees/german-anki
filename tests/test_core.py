import struct

from german_anki import config, dictionary
from german_anki.normalize import normalize
from german_anki.sync import build_forms


def test_normalize_cleans_annotations():
    assert normalize("<b>Störung</b>") == ("störung", [])
    assert normalize("der Tisch")[0] == "tisch"
    assert normalize("sich freuen")[0] == "freuen"
    assert normalize("zu|ordnen")[0] == "zuordnen"
    assert normalize("reich (adj)")[0] == "reich"
    assert normalize("China&nbsp;🇹🇼")[0] == "china"
    assert normalize("Tut mir leid")[0] is None          # phrases are not used for colouring


def test_read_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text('# comment\nANKI_DECK="🇩🇪 German"\nexport LLM_MODEL=x/y\n\nEMPTY=\n', encoding="utf-8")
    assert config.read_env_file(f) == {"ANKI_DECK": "🇩🇪 German", "LLM_MODEL": "x/y", "EMPTY": ""}


def _write_stardict(d, entries):
    """entries: {headword: [forms]} -> minimal reader.dict-de.idx/.syn files"""
    heads = sorted(entries)
    (d / "reader.dict-de.idx").write_bytes(
        b"".join(h.encode() + b"\0" + struct.pack(">II", 0, 0) for h in heads))
    (d / "reader.dict-de.syn").write_bytes(b"".join(
        f.encode() + b"\0" + struct.pack(">I", heads.index(h)) for h in heads for f in entries[h]))


def test_dictionary_build_and_lookup(tmp_path):
    _write_stardict(tmp_path, {"Gehen": ["ging", "gegangen"], "Haus": ["Häuser"]})
    dictionary.build(tmp_path)
    assert dictionary.is_built(tmp_path)
    words = (tmp_path / "wordlist.txt").read_text(encoding="utf-8").split()
    assert words == sorted(words, key=lambda s: s.encode())
    assert {"gehen", "ging", "häuser"} <= set(words)

    con = dictionary.connect(tmp_path)
    heads, forms_of, lemmas_of = dictionary.lookup(con, {"gehen", "ging", "xyz"})
    assert heads == {"gehen"}
    assert forms_of["gehen"] == {"ging", "gegangen"}
    assert lemmas_of["ging"] == {"gehen"}


def test_build_forms_prefers_complete():
    words = {"gehen": {"screenshot": True, "audio": True},
             "Gang": {"screenshot": False, "audio": True}}
    forms = build_forms(words, {"gehen": {"ging"}, "gang": {"ging"}})
    assert forms["ging"] == "ok"          # one complete card behind a form wins
    assert forms["gang"] == "missing"


def _dwds_page(*labels):
    """Minimal DWDS article page with one 'Form / Grammatik' block per entry."""
    block = ('<div class="dwdswb-ft-block"><span class="dwdswb-ft-blocklabel serif italic">Form / Grammatik</span>'
             '<b>X</b> · <span class="dwdswb-ft-blocktext"><span>{}</span> · Genitiv Singular: <b>X</b></span></div>')
    return "<html>" + "".join(block.format(label) for label in labels) + "</html>"


def test_dwds_grammar_article_and_verb():
    from german_anki import cards
    g = cards.parse_dwds_grammar(_dwds_page("Substantiv (Femininum)"))
    assert g == ["Substantiv (Femininum)"]
    assert cards.dwds_article(g) == "die"
    assert cards.dwds_is_verb(g) is False

    assert cards.dwds_article(["Eigenname"]) is None                     # Frankreich: no header
    assert cards.dwds_article(["Interjektion"]) is None                  # hallo
    assert cards.dwds_article([]) is None                                # no DWDS entry
    assert cards.dwds_article(["Substantiv (Femininum, Maskulinum)"]) == "der/die"
    # homograph: the LLM's article only chooses between DWDS entries, never adds one
    leiter = ["Substantiv (Femininum)", "Substantiv (Maskulinum)"]
    assert cards.dwds_article(leiter, "der") == "der"
    assert cards.dwds_article(leiter, "das") == "die"
    assert cards.dwds_article(leiter) == "die"

    assert cards.dwds_is_verb(["Verb"]) is True
    assert cards.dwds_is_verb(["partizipiales Adjektiv"]) is False      # gewesen
    assert cards.dwds_is_verb([]) is None


def test_noun_header_and_llm_header_stripping():
    from german_anki import cards
    assert cards.noun_header("brille", "die") == "<b><font color='#ff0000'>die</font> Brille</b>: "
    assert cards.noun_header("Jugendliche", "der/die") == (
        "<b><font color='#00aaff'>der</font>/<font color='#ff0000'>die</font> Jugendliche</b>: ")
    assert cards.strip_llm_header("<b><font color='#00aaff'>der</font> Frankreich</b>: Land in Westeuropa") \
        == "Land in Westeuropa"
    assert cards.strip_llm_header("Land in Westeuropa") == "Land in Westeuropa"


def test_build_forms_declines_determiners():
    from german_anki.sync import build_forms
    words = {"alle": {"screenshot": True, "audio": True}, "jeder": {"screenshot": True, "audio": False}}
    forms = build_forms(words, {"alle": {"allem"}, "jeder": {"jedes"}})
    for f in ("alle", "allen", "aller", "alles", "allem", "all"):
        assert forms[f] == "ok", f
    for f in ("jeden", "jedem", "jede", "jedes"):
        assert forms[f] == "noaudio", f          # jeder has a screenshot but no audio
    assert "dieses" not in forms                       # only words that are in the deck


def test_inflected_form_card_gets_lemma_forms():
    from german_anki.sync import build_forms, lemmas_to_expand
    heads = {"anderer", "rechen"}
    lemmas_of = {"andere": {"anderer"}, "gerecht": {"rechen"}}
    expand = lemmas_to_expand({"andere", "gerecht"}, heads, lemmas_of)
    assert expand == {"andere": {"anderer"}, "gerecht": set()}   # gerecht -> rechen is a bad link
    media = {"screenshot": True, "audio": True}
    forms = build_forms({"andere": media, "gerecht": media},
                        {"anderer": {"anderen", "anderes"}, "rechen": {"rechte"}}, expand)
    assert {"anderer", "anderen", "anderes"} <= forms.keys()
    assert "rechte" not in forms


def test_verbformen_noun_page_and_noun_detection():
    from german_anki import cards
    assert cards.verbformen_urls("laufen") == ["https://www.verbformen.com/conjugation/?w=laufen"]
    assert cards.verbformen_urls("schaden", is_noun=True) == [
        "https://www.verbformen.com/declension/nouns/Schaden.htm",
        "https://www.verbformen.com/conjugation/?w=schaden"]
    assert cards.dwds_is_noun("Schaden", ["Substantiv (Maskulinum)"]) is True
    assert cards.dwds_is_noun("laufen", ["Verb"]) is False
    assert cards.dwds_is_noun("Essen", ["Verb", "Substantiv (Neutrum)"]) is True
    assert cards.dwds_is_noun("essen", ["Verb", "Substantiv (Neutrum)"]) is False
    assert cards.dwds_is_noun("Gewitter", []) is False      # no DWDS entry -> old behaviour


def test_screenshot_fit_width_keeps_aspect_ratio():
    from PIL import Image
    from german_anki.images import fit_width
    assert fit_width(Image.new("RGB", (1280, 720))).size == (720, 405)     # 16:9
    assert fit_width(Image.new("RGB", (1920, 1080))).size == (720, 405)    # 16:9, 1080p
    assert fit_width(Image.new("RGB", (1440, 1080))).size == (720, 540)    # 4:3
    assert fit_width(Image.new("RGB", (2580, 1080))).size == (720, 301)    # 21:9
    assert fit_width(Image.new("RGB", (720, 480))).size == (720, 480)      # already 720 wide
    assert fit_width(Image.new("RGB", (640, 360))).size == (640, 360)      # never upscaled


def test_build_forms_noaudio_status_and_ranking():
    from german_anki.sync import build_forms
    ok = {"screenshot": True, "audio": True}
    noaudio = {"screenshot": True, "audio": False}
    none = {"screenshot": False, "audio": False}
    forms = build_forms({"Haus": noaudio, "gehen": none, "Gang": noaudio, "laufen": ok, "Lauf": none},
                        {"gehen": {"ging"}, "gang": {"ging"}, "laufen": {"lief"}, "lauf": {"lief"}})
    assert forms["haus"] == "noaudio"
    assert forms["gehen"] == "missing"
    assert forms["ging"] == "noaudio"      # noaudio beats missing
    assert forms["lief"] == "ok"           # ok beats missing


def test_sentence_row_parsing():
    from german_anki.sentences import parse_row
    row = r"| Die Sache wird ernst | things get serious | ![[die-sache-v1.webp\|500x379]] | ![[die-sache-v1.ogg]] |"
    assert parse_row(row) == ("Die Sache wird ernst", "things get serious", "die-sache-v1.webp", "die-sache-v1.ogg")
    assert parse_row(r"| Für immer | always | ![[x.webp\|500x379]] |") == ("Für immer", "always", "x.webp", None)
    assert parse_row(r"| a \| b | c |  |  |") == ("a | b", "c", None, None)
    assert parse_row("| Nur Deutsch |  |  |  |") is None     # English is required
    assert parse_row("not a row") is None


def test_regen_selection_helpers():
    from german_anki import cards
    assert cards.parse_parts(None) == set(cards.REGEN_PARTS)
    assert cards.parse_parts("Examples, definition") == {"examples", "definition"}
    import pytest
    with pytest.raises(ValueError):
        cards.parse_parts("examples,audio")
    assert cards.regen_query("De", []) == 'deck:"De" tag:regen'
    assert cards.regen_query("De", [], "added:7") == 'deck:"De" (added:7)'
    assert cards.regen_query("De", ["Haus", "a_b*"]) == r'deck:"De" (Word:"Haus" OR Word:"a\_b\*")'
    assert cards.note_word("<b>Störung</b>&nbsp;") == "Störung"
    assert cards.note_word("sich&nbsp;freuen") == "sich freuen"


def test_resolve_is_verb_fallbacks():
    from german_anki import cards
    assert cards.resolve_is_verb(["Verb"], {"is_verb": False}, False) is True     # DWDS wins
    assert cards.resolve_is_verb([], {"is_verb": "true"}, False) is True           # then the LLM
    assert cards.resolve_is_verb([], {"is_verb": None}, True) is True              # then the table
    assert cards.resolve_is_verb([], None, None) is None                           # unknown: leave tag


def test_bash_completion_matches_cli():
    import argparse
    import re
    from pathlib import Path
    from german_anki.cli import build_parser
    script = (Path(__file__).parent.parent / "completions" / "german-anki.bash").read_text()
    sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    listed = re.search(r'local commands="([^"]+)"', script).group(1).split()
    assert sorted(listed) == sorted(sub.choices)
    for name, parser in sub.choices.items():
        for opt in (o for a in parser._actions for o in a.option_strings):
            assert opt in script, f"{name} {opt} missing from the completion"


def test_pick_tatoeba_examples_takes_the_longest():
    from german_anki import cards
    cands = [("a b", "x"), ("a b c d", "y"), ("a", "z"), ("a b c", "w")]
    assert cards.pick_tatoeba_examples(cands) == "a b c d <br> y<br><br>a b c <br> w<br><br>a b <br> x"
