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
