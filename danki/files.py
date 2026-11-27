"""Writing shared files safely."""
import os
import tempfile
from pathlib import Path


def write_atomic(path, text):
    """Write `text` to `path` through a temporary file in the same folder, so readers (the mpv
    scripts) see the old or the new content, never half of it. Each call gets its own temporary
    file, so two writers at once (two mpv windows syncing) can't mix their output."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        umask = os.umask(0)
        os.umask(umask)
        os.fchmod(fd, 0o666 & ~umask)   # mkstemp makes 0600; keep the usual permissions
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
