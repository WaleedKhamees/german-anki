"""`danki setup-anki`: create the decks and the note type in a fresh Anki.

Only adds what is missing. An existing deck or note type (even one with missing fields or other
templates) is never changed.
"""
import sys

from . import config
from .anki import Anki, AnkiError, REQUIRED_FIELDS

CARD_NAME = "Card 1"

FRONT = """<div class="word">{{Word}}</div>
"""

# sentence cards use the same note type with only Word, Definition, Screenshot and SourceAudio filled
BACK = """<div class="word">{{Word}}</div>
{{#Pronunciation}}<div class="pron">{{Pronunciation}}</div>{{/Pronunciation}}

<hr id=answer>

{{#Definition}}
<div class="section definition">{{Definition}}</div>
{{/Definition}}

{{#Screenshot}}
<div class="section shot">
  <div class="media">{{Screenshot}}</div>
  {{#SourceAudio}}<div class="shot-audio">{{SourceAudio}}</div>{{/SourceAudio}}
</div>
{{/Screenshot}}
{{^Screenshot}}{{#SourceAudio}}
<div class="section">
  <div class="label">Where I met it</div>
  <div class="pron">{{SourceAudio}}</div>
</div>
{{/SourceAudio}}{{/Screenshot}}

{{#Conjugation}}
<div class="section">
  <div class="label">Conjugation</div>
  <div class="media">{{Conjugation}}</div>
</div>
{{/Conjugation}}

{{#Examples}}
<div class="section">
  <div class="label">Examples</div>
  <div class="examples">{{Examples}}</div>
</div>
{{/Examples}}
"""

CSS = """.card {
    font-family: arial;
    font-size: 20px;
    line-height: 1.45;
    text-align: center;
    color: black;
    background-color: white;
    max-width: 760px;
    margin: 0 auto;
    padding: 0 8px;
}
.nightMode.card, .night_mode .card { color: #e6e6e6; background-color: #2f2f31; }
.word { font-size: 30px; font-weight: bold; margin: 4px 0; }
.pron { margin: 2px 0; }
hr#answer { margin: 8px 0; }
.section { text-align: left; margin: 12px 0; }
.label {
    font-size: 13px; font-weight: bold; text-transform: uppercase;
    letter-spacing: .06em; opacity: .55; margin-bottom: 4px;
}
.media img { max-width: 100%; height: auto; display: block; margin: 0 auto; }
.shot { display: flex; align-items: center; justify-content: center; gap: 10px; margin-top: 8px; }
.shot .media { min-width: 0; }
.shot-audio { flex: none; }
.shot .media img { max-height: 45vh; width: auto; border-radius: 6px; }
.examples { font-size: 18px; }
"""


def missing(anki, settings):
    """-> (decks to create, whether the note type must be created, fields an existing one lacks)"""
    decks = anki("deckNames", timeout=3)
    wanted = dict.fromkeys((settings.deck, settings.sentence_deck))   # unique, in order
    new_decks = [d for d in wanted if d not in decks]
    if settings.note_type not in anki("modelNames"):
        return new_decks, True, []
    fields = anki("modelFieldNames", modelName=settings.note_type)
    return new_decks, False, [f for f in REQUIRED_FIELDS if f not in fields]


def create(anki, settings, new_decks, new_note_type):
    for deck in new_decks:
        anki("createDeck", deck=deck)
        print(f"[+] created deck '{deck}'")
    if new_note_type:
        anki("createModel", modelName=settings.note_type, inOrderFields=REQUIRED_FIELDS, css=CSS,
             cardTemplates=[{"Name": CARD_NAME, "Front": FRONT, "Back": BACK}])
        print(f"[+] created note type '{settings.note_type}'")


def confirm():
    print("Already have these on AnkiWeb? Answer n, sync Anki first, then run: danki setup-anki")
    try:
        return input("Create them now? [Y/n] ").strip().lower() in ("", "y", "yes")
    except EOFError:   # no terminal: never create without being asked
        print()
        return False


def run(assume_yes=False):
    """Exit status: 0 everything is there, 1 declined or something needs fixing, 3 Anki offline."""
    settings = config.load()
    anki = Anki(settings.anki_url)
    try:
        new_decks, new_note_type, lacking = missing(anki, settings)
        if new_decks or new_note_type:
            print("Missing in Anki:")
            for deck in new_decks:
                print(f"    deck '{deck}'")
            if new_note_type:
                print(f"    note type '{settings.note_type}'")
            if not assume_yes and not confirm():
                print("nothing created")
                return 1
            create(anki, settings, new_decks, new_note_type)
        elif not lacking:
            print("the decks and the note type already exist")
    except OSError as e:
        print(f"Anki not reachable at {settings.anki_url} ({e})", file=sys.stderr)
        return 3
    except AnkiError as e:
        print(e, file=sys.stderr)
        return 1
    if lacking:
        print(f"note type '{settings.note_type}' exists but lacks: {', '.join(lacking)}\n"
              "Add them in Anki (Tools → Manage Note Types → Fields), or set ANKI_NOTE_TYPE in "
              f"{config.CONFIG_PATH} to a new name and run this again.", file=sys.stderr)
        return 1
    return 0
