"""`danki subs`: write subtitles for videos with whisper.cpp, so there is something to mine.

VIDEO.mkv -> VIDEO.srt next to it (mpv loads it by itself). Steps:
1. ffmpeg extracts 16 kHz mono audio, the format whisper expects.
2. whisper-cli transcribes it with settings that keep it in sync (see WHISPER_ARGS).
3. fix_durations() moves the start of overlong cues forward.
4. fill_gaps() re-transcribes long stretches without subtitles, without VAD.

Why subtitles drift during music: whisper decodes 30 s windows and uses the timestamps it
predicts itself to decide where the next window starts. Music and other non-speech make those
predictions unreliable (and make it hallucinate text), and the error carries into later windows
until a clean pause resyncs it.
"""
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from . import config

MEDIA_SUFFIXES = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v",
                  ".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav"}
LANGUAGES = ["de", "en"]   # offered by the interactive picker; --lang takes any whisper code

#   -mc 0   don't feed the previous text as a prompt, so one bad window can't cascade into
#           repeated or hallucinated lines
#   -sns    suppress non-speech tokens like [Musik] or [Gähn]
WHISPER_ARGS = ["-mc", "0", "-sns"]
# Silero VAD drops music and silence before whisper sees the audio; whisper.cpp maps the
# timestamps back to the original timeline.
VAD_ARGS = ["--vad", "-vsd", "300", "-vp", "200"]

MIN_GAP = 3.0   # seconds without a subtitle before a gap is re-checked
EDGE = 0.25     # seconds trimmed from each gap edge so the clip doesn't catch the tail of a
                # neighbouring line (VAD pads speech by 0.2 s)

# Lines whisper often makes up for music or silence
HALLUCINATIONS = re.compile(
    r"untertitel|amara\.org|zdf|swr|vielen dank f[üu]r[s]? (zu|zus)|"
    r"bis zum n[äa]chsten mal|abonnier|copyright|www\.|\.com|"
    r"thank(s| you) for watching|subtitles by|please subscribe|"
    r"\bder erste mal\b",  # whisper's typical German filler for music
    re.IGNORECASE,
)
TIMESTAMP = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


class SubsError(Exception):
    pass


# --- SRT ---

def _secs(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def fmt_time(t):
    ms = max(0, int(round(t * 1000)))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def parse_srt(text):
    """-> [[start, end, text], ...]; cues without text are dropped."""
    cues = []
    for block in text.replace("\r", "").strip().split("\n\n"):
        lines = block.split("\n")
        for i, line in enumerate(lines):
            m = TIMESTAMP.search(line)
            if m:
                g = m.groups()
                body = "\n".join(l.strip() for l in lines[i + 1:]).strip()
                if body:
                    cues.append([_secs(*g[:4]), _secs(*g[4:]), body])
                break
    return cues


def format_srt(cues):
    return "".join(f"{n}\n{fmt_time(s)} --> {fmt_time(e)}\n{text}\n\n"
                   for n, (s, e, text) in enumerate(sorted(cues), 1))


def fix_durations(cues):
    """With VAD, a cue that spans removed music can get a start time from before the music,
    leaving the line on screen far too early; its end time is still right. A cue much longer
    than its text needs (about 1 s plus 0.09 s per character, at least 2 s) starts later."""
    for cue in cues:
        start, end, text = cue
        needed = max(2.0, 1.0 + len(text.replace("\n", "")) * 0.09)
        if end - start > needed + 1:
            cue[0] = end - needed
    return cues


# --- recovering speech that VAD dropped ---

def is_junk(text):
    stripped = re.sub(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*|♪", "", text)
    if not re.search(r"\w", stripped):
        return True
    # decoding loops like "Woohohohoho..." or "ah, ah, ah, ah, ah, ah"
    if re.search(r"(.{1,4})\1{5,}", re.sub(r"[\W_]", "", stripped.lower())):
        return True
    return bool(HALLUCINATIONS.search(text))


def _norm(text):
    return re.sub(r"[^\w ]", "", text.lower()).strip()


def similar(a, b):
    a, b = _norm(a), _norm(b)
    return bool(a) and bool(b) and (
        a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() > 0.75)


def find_gaps(cues, total, min_gap=MIN_GAP):
    """Stretches of at least min_gap seconds without a subtitle, file start and end included."""
    bounds = [(0.0, 0.0)] + sorted((c[0], c[1]) for c in cues) + [(total, total)]
    gaps, covered = [], 0.0
    for start, end in bounds:
        if start - covered >= min_gap:
            gaps.append((covered, start))
        covered = max(covered, end)
    return gaps


def _parse_whisper_json(path):
    """Segments from whisper-cli -ojf as (start, end, text)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return []
    return [(seg["offsets"]["from"] / 1000, seg["offsets"]["to"] / 1000, seg["text"].strip())
            for seg in data.get("transcription", [])]


def fill_gaps(wav_path, cues, lang, whisper, model, threads, tmp):
    """VAD keeps whisper in sync during music, but it can also throw away quiet or processed
    speech, often short lines said over music. Each gap is transcribed again on its own,
    without VAD; the clips are short, so their timestamps can't drift. -> the added cues."""
    added = []
    with wave.open(str(wav_path), "rb") as w:
        rate, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
        total = w.getnframes() / rate
        gaps = find_gaps(cues, total)
        print(f"    re-checking {len(gaps)} gap(s) without subtitles…", file=sys.stderr)
        for i, (g_start, g_end) in enumerate(gaps):
            c_start = min(g_start + EDGE, total) if g_start > 0 else 0.0
            c_end = max(g_end - EDGE, c_start) if g_end < total else total
            if c_end - c_start < 1.0:
                continue
            w.setpos(int(c_start * rate))
            clip = Path(tmp) / f"gap{i}.wav"
            with wave.open(str(clip), "wb") as out:
                out.setnchannels(channels)
                out.setsampwidth(width)
                out.setframerate(rate)
                out.writeframes(w.readframes(int((c_end - c_start) * rate)))
            base = Path(tmp) / f"gap{i}"
            subprocess.run([whisper, "-m", str(model), "-f", str(clip), "-l", lang, "-t", str(threads),
                            *WHISPER_ARGS, "-ojf", "-of", str(base)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # the subtitles right before and after this gap
            before = [c[2] for c in cues if abs(c[1] - g_start) < 0.01]
            after = [c[2] for c in cues if abs(c[0] - g_end) < 0.01]
            prev_text = None
            for s, e, text in _parse_whisper_json(f"{base}.json"):
                s, e = s + c_start, min(e + c_start, c_end)
                if e - s < 0.3 or is_junk(text):
                    continue
                # the neighbouring line heard again at the very edge of the gap
                if (s - g_start < 1.5 and any(similar(text, n) for n in before)) or \
                   (g_end - e < 1.5 and any(similar(text, n) for n in after)):
                    continue
                if prev_text is not None and _norm(text) == _norm(prev_text):
                    added[-1][1] = e
                    continue
                prev_text = text
                added.append([s, e, text])
                print(f"    + {fmt_time(s)}  {text}", file=sys.stderr)
    return added


# --- running it ---

def _write_atomic(path, text):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _run(cmd, what):
    res = subprocess.run(cmd, stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                         errors="replace")
    if res.returncode != 0:
        tail = "\n".join(res.stderr.strip().splitlines()[-5:])
        raise SubsError(f"{what} failed (exit {res.returncode})\n{tail}")


def check_tools(s):
    """-> (whisper-cli, model); raises SubsError saying how to fix what's missing."""
    if not shutil.which("ffmpeg"):
        raise SubsError("ffmpeg not found — install it")
    whisper = s.whisper_cli
    if not whisper:
        raise SubsError("whisper-cli not found — run install.sh (it builds whisper.cpp), "
                        "or set WHISPER_CLI in " + str(config.CONFIG_PATH))
    if not s.whisper_model.is_file():
        raise SubsError(f"whisper model {s.whisper_model} not found — run install.sh, "
                        "or set WHISPER_MODEL")
    return whisper, s.whisper_model


def make_subtitles(media, s, lang, whisper, model):
    """Write MEDIA's .srt next to it (atomically). -> number of cues."""
    target = media.with_suffix(".srt")
    threads = s.whisper_threads
    vad = s.whisper_vad_model if s.whisper_vad_model.is_file() else None
    with tempfile.TemporaryDirectory(prefix="danki-subs-") as tmp:
        wav = Path(tmp) / "audio.wav"
        _run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(media),
              "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)], "ffmpeg")
        base = Path(tmp) / "subs"
        vad_args = [*VAD_ARGS, "-vm", str(vad)] if vad else []
        # stdout shows each line as it is transcribed
        _run([whisper, "-m", str(model), "-f", str(wav), "-l", lang, "-t", str(threads),
              *vad_args, *WHISPER_ARGS, "-osrt", "-of", str(base)], "whisper-cli")
        cues = fix_durations(parse_srt(Path(f"{base}.srt").read_text(encoding="utf-8", errors="replace")))
        if vad:   # without VAD nothing was dropped
            added = fill_gaps(wav, cues, lang, whisper, model, threads, tmp)
            print(f"    recovered {len(added)} line(s) from the gaps", file=sys.stderr)
            cues += added
        _write_atomic(target, format_srt(cues))
    return len(cues)


def _fzf(items, header, multi=False):
    cmd = ["fzf", "--reverse", "--no-scrollbar", "--border", "--header", header]
    if multi:
        cmd += ["-m", "--bind", "ctrl-a:select-all"]
    res = subprocess.run(cmd, input="\n".join(items), stdout=subprocess.PIPE, text=True)
    return [line for line in res.stdout.splitlines() if line]


def pick(lang):
    """Choose media files in the current folder (and the language) with fzf. -> (files, lang)"""
    if not shutil.which("fzf"):
        raise SubsError("give the files to transcribe (fzf, for picking them, is not installed)")
    files = sorted((p.name for p in Path.cwd().iterdir() if p.suffix.lower() in MEDIA_SUFFIXES
                    and p.is_file()), key=str.lower)
    if not files:
        raise SubsError(f"no video or audio files in {Path.cwd()}")
    chosen = _fzf(files, "SELECT FILES (Tab: several, Ctrl+A: all)", multi=True)
    if chosen and not lang:
        langs = _fzf(sorted(LANGUAGES, key=lambda l: l != config.load().whisper_language), "LANGUAGE")
        lang = langs[0] if langs else None
        if not lang:
            chosen = []
    return [Path(f) for f in chosen], lang


def run(files, lang=None, overwrite=False):
    s = config.load()
    try:
        whisper, model = check_tools(s)
        if not files:
            files, lang = pick(lang)
            if not files:
                return 0
        files = [Path(f) for f in files]
        for f in files:
            if not f.is_file():
                raise SubsError(f"{f}: no such file")
    except SubsError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1
    lang = lang or s.whisper_language
    if not s.whisper_vad_model.is_file():
        print(f"[!] VAD model {s.whisper_vad_model} not found: running without VAD "
              "(subtitles may drift during music)", file=sys.stderr)

    failed = 0
    for n, media in enumerate(files, 1):
        target = media.with_suffix(".srt")
        if target.exists() and not overwrite:
            print(f"[{n}/{len(files)}] {target} exists, skipped (--overwrite replaces it)", file=sys.stderr)
            continue
        print(f"[{n}/{len(files)}] {media} ({lang})", file=sys.stderr)
        try:
            count = make_subtitles(media, s, lang, whisper, model)
            print(f"    wrote {target} ({count} lines)", file=sys.stderr)
        except (SubsError, OSError, wave.Error) as e:
            failed += 1
            print(f"[!] {media}: {e}", file=sys.stderr)
    return 1 if failed else 0
