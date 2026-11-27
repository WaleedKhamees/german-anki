"""Settings shared by every command (and, by format, the mpv scripts).

Read from ~/.config/danki/.env (or $DANKI_CONFIG), KEY=value per line.
Environment variables with the same names override the file.
"""
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(os.environ.get(
    "DANKI_CONFIG",
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "danki" / ".env",
)).expanduser()

DEFAULTS = {
    "OPENROUTER_API_KEY": "",
    "ANKI_CONNECT_URL": "http://127.0.0.1:8765",
    "ANKI_DECK": "🇩🇪 German",
    "ANKI_NOTE_TYPE": "Basic with example",
    "ANKI_SENTENCE_DECK": "🇩🇪 German Sentence Mining",
    "LLM_MODEL": "deepseek/deepseek-v4-flash:floor",
    "EXAMPLE_MIN_WORDS": "10",
    "EXAMPLE_MAX_WORDS": "25",
    "DANKI_DATA_DIR": "~/.local/share/danki",
    "DANKI_BIN": "~/.local/bin/danki",
    # `danki subs`; empty paths mean the whisper.cpp that install.sh builds in <data>/whisper.cpp
    "WHISPER_CLI": "",
    "WHISPER_MODEL": "",
    "WHISPER_VAD_MODEL": "",
    "WHISPER_THREADS": "",
    "WHISPER_LANGUAGE": "de",
}
WHISPER_MODEL_FILE = "ggml-small-q8_0.bin"
WHISPER_VAD_MODEL_FILE = "ggml-silero-v6.2.0.bin"


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
    example_min_words: int
    example_max_words: int
    data_dir: Path
    whisper_cli_setting: str = ""
    whisper_model_setting: Path | None = None
    whisper_vad_model_setting: Path | None = None
    whisper_threads_setting: int = 0
    whisper_language: str = "de"

    @property
    def assets_dir(self): return self.data_dir / "assets"
    @property
    def queue_dir(self): return self.data_dir / "queue"     # captures waiting for Anki (queue.py)
    @property
    def legacy_dir(self): return self.data_dir / "legacy"   # Markdown files of older versions
    @property
    def words_json(self): return self.data_dir / "anki-words.json"
    @property
    def review_file(self): return self.data_dir / "anki-words-to-review.txt"
    @property
    def dict_dir(self): return self.data_dir / "dict"
    @property
    def whisper_dir(self): return self.data_dir / "whisper.cpp"
    @property
    def whisper_cli(self):
        """WHISPER_CLI, else install.sh's build, else whisper-cli on PATH; None if none exists."""
        if self.whisper_cli_setting:
            return shutil.which(os.path.expanduser(self.whisper_cli_setting))
        built = self.whisper_dir / "build" / "bin" / "whisper-cli"
        return str(built) if os.access(built, os.X_OK) else shutil.which("whisper-cli")
    @property
    def whisper_model(self):
        return self.whisper_model_setting or self.whisper_dir / "models" / WHISPER_MODEL_FILE
    @property
    def whisper_vad_model(self):
        return self.whisper_vad_model_setting or self.whisper_dir / "models" / WHISPER_VAD_MODEL_FILE
    @property
    def whisper_threads(self): return self.whisper_threads_setting or os.cpu_count() or 4
    @property
    def cache_dir(self):
        return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "danki"


def _int(raw, key):
    try:
        return int(raw[key])
    except ValueError:
        return int(DEFAULTS[key])


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
        example_min_words=_int(raw, "EXAMPLE_MIN_WORDS"),
        example_max_words=_int(raw, "EXAMPLE_MAX_WORDS"),
        data_dir=_path(raw["DANKI_DATA_DIR"]),
        whisper_cli_setting=raw["WHISPER_CLI"],
        whisper_model_setting=_path(raw["WHISPER_MODEL"]),
        whisper_vad_model_setting=_path(raw["WHISPER_VAD_MODEL"]),
        whisper_threads_setting=int(raw["WHISPER_THREADS"]) if raw["WHISPER_THREADS"].isdigit() else 0,
        whisper_language=raw["WHISPER_LANGUAGE"] or "de",
    )
