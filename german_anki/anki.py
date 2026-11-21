"""AnkiConnect client and the note layout every command relies on."""
import json
import urllib.request

# fields of the note type (ANKI_NOTE_TYPE)
WORD = "Word"
DEFINITION = "Definition"
CONJUGATION = "Conjugation"
SCREENSHOT = "Screenshot"
SOURCE_AUDIO = "SourceAudio"
EXAMPLES = "Examples"
PRONUNCIATION = "Pronunciation"
REQUIRED_FIELDS = [WORD, DEFINITION, CONJUGATION, SCREENSHOT, SOURCE_AUDIO, EXAMPLES, PRONUNCIATION]

# tags kept in sync with empty fields
MEDIA_TAGS = {SCREENSHOT: "no-screenshot", SOURCE_AUDIO: "no-audio"}
NO_PRONUNCIATION_TAG = "no-pronunciation"


class AnkiError(RuntimeError):
    pass


class Anki:
    def __init__(self, url):
        self.url = url

    def __call__(self, action, timeout=30, **params):
        body = json.dumps({"action": action, "version": 6, "params": params}).encode()
        req = urllib.request.Request(self.url, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.load(r)
        if res.get("error"):
            raise AnkiError(f"AnkiConnect {action}: {res['error']}")
        return res["result"]
