"""Settings shared by every command (and, by format, the mpv scripts).

Read from ~/.config/german-anki/.env (or $GERMAN_ANKI_CONFIG), KEY=value per line.
Environment variables with the same names override the file.
"""
import os
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(os.environ.get(
    "GERMAN_ANKI_CONFIG",
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "german-anki" / ".env",
)).expanduser()

DEFAULTS = {
    "OPENROUTER_API_KEY": "",
    "ANKI_CONNECT_URL": "http://127.0.0.1:8765",
    "ANKI_DECK": "🇩🇪 German",
    "ANKI_NOTE_TYPE": "Basic with example",
    "ANKI_SENTENCE_DECK": "🇩🇪 German Sentence Mining",
    "LLM_MODEL": "deepseek/deepseek-v4-flash:floor",
    "GERMAN_ANKI_DATA_DIR": "~/.local/share/german-anki",
    "OBSIDIAN_VAULT_PATH": "",
    "GERMAN_ANKI_BIN": "~/.local/bin/german-anki",
}


def read_env_file(path):
    """Parse KEY=value lines (comments, blank lines and surrounding quotes allowed)."""
    values = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def _path(value):
    return Path(os.path.expandvars(value)).expanduser() if value else None


@dataclass(frozen=True)
class Settings:
    api_key: str
    anki_url: str
    deck: str
    note_type: str
    sentence_deck: str
    llm_model: str
    data_dir: Path
    obsidian_vault: Path | None

    @property
    def assets_dir(self): return self.data_dir / "assets"
    @property
    def todo_file(self): return self.data_dir / "To-do-german-words.md"
    @property
    def done_file(self): return self.data_dir / "To-do-german-words-done.md"
    @property
    def sentences_file(self): return self.data_dir / "sentence-mining.md"
    @property
    def sentences_done_file(self): return self.data_dir / "sentence-mining-done.md"
    @property
    def words_json(self): return self.data_dir / "anki-words.json"
    @property
    def review_file(self): return self.data_dir / "anki-words-to-review.txt"
    @property
    def dict_dir(self): return self.data_dir / "dict"
    @property
    def cache_dir(self):
        return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "german-anki"


def load():
    raw = dict(DEFAULTS)
    raw.update({k: v for k, v in read_env_file(CONFIG_PATH).items() if v != "" or k not in DEFAULTS})
    raw.update({k: os.environ[k] for k in DEFAULTS if os.environ.get(k)})
    return Settings(
        api_key=raw["OPENROUTER_API_KEY"],
        anki_url=raw["ANKI_CONNECT_URL"],
        deck=raw["ANKI_DECK"],
        note_type=raw["ANKI_NOTE_TYPE"],
        sentence_deck=raw["ANKI_SENTENCE_DECK"],
        llm_model=raw["LLM_MODEL"],
        data_dir=_path(raw["GERMAN_ANKI_DATA_DIR"]),
        obsidian_vault=_path(raw["OBSIDIAN_VAULT_PATH"]),
    )
