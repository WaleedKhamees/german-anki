"""Background worker for `danki add-capture`, so mpv captures don't each pay for starting
Python's heavy imports and Chromium (1–2 s per word).

`add-capture` is a thin client: it sends the capture's path over a Unix socket to `danki serve`, starting
the worker first when none is running, and prints the worker's output and exits with its status,
so the mpv script sees exactly what an in-process run would print. If the worker can't be used,
the capture is processed in-process as before.

The worker handles one capture at a time, exits after IDLE_SECONDS without work, and restarts itself
when the config file or the code changed since it started (the install is editable).
"""
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config

IDLE_SECONDS = 15 * 60
START_TIMEOUT = 10        # seconds to wait for a freshly started worker
END = b"\0"               # separates the output from the exit status in a reply
STALE = b"\0stale"        # reply of a worker whose config or code changed
PACKAGE_DIR = Path(__file__).resolve().parent


def socket_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        path = Path(runtime) / "danki.sock"
        if len(os.fsencode(path)) < 100:     # AF_UNIX paths are limited to ~108 bytes
            return path
    return Path(f"/tmp/danki-{os.getuid()}.sock")


def _signature():
    """Changes when the config file or any module of the package changes."""
    files = [config.CONFIG_PATH, *PACKAGE_DIR.glob("*.py")]
    return tuple(f.stat().st_mtime_ns if f.exists() else 0 for f in files)


# ---------------------------------------------------------------- client

def _connect(path):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(str(path))
        return s
    except OSError:
        s.close()
        return None


def _start_worker():
    log = config.load().cache_dir / "worker.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as out:
        # own session and no inherited pipes: mpv waits for EOF on add-capture's stdout, so the
        # worker must not hold it open
        return subprocess.Popen([sys.executable, "-m", "danki.cli", "serve"], stdin=subprocess.DEVNULL,
                                stdout=out, stderr=out, start_new_session=True, close_fds=True)


def _worker_connection():
    path = socket_path()
    conn = _connect(path)
    if conn:
        return conn
    proc = _start_worker()
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(0.05)
        conn = _connect(path)
        if conn:
            return conn
        if proc is not None and proc.poll() is not None and proc.returncode != 0:
            return None       # it crashed (see worker.log): don't wait out the timeout
    return None


def _ask(conn, request):
    """Send one request; -> (output bytes, exit status) or None if the worker was stale."""
    with conn:
        conn.sendall(json.dumps(request).encode() + b"\n")
        conn.shutdown(socket.SHUT_WR)
        data = b""
        while chunk := conn.recv(65536):
            data += chunk
    if data in (STALE, b""):
        return None     # stale, or it exited (idle) before taking the capture: nothing was done
    out, sep, status = data.rpartition(END)
    if not sep:
        # the worker died mid-capture: report a failure; the capture stays queued
        return data + b"\nSTATUS: failed\nRESULT: worker stopped unexpectedly\n", 1
    return out, int(status)


def run_capture(request):
    """add-capture through the worker. -> exit status, or None when the worker can't be used."""
    for _ in range(2):               # a worker that exits says so; the second try starts a fresh one
        conn = _worker_connection()
        if conn is None:
            return None
        try:
            reply = _ask(conn, request)
        except OSError:
            return None
        if reply is not None:
            out, status = reply
            sys.stdout.buffer.write(out)
            sys.stdout.flush()
            return status
    return None


# ---------------------------------------------------------------- worker

class _SocketWriter:
    """stdout for one request: what processing the capture prints goes back to the client."""

    def __init__(self, conn):
        self.conn = conn

    def write(self, text):
        try:
            self.conn.sendall(text.encode("utf-8", "replace"))
        except OSError:
            pass            # the client is gone; still finish the capture
        return len(text)

    def flush(self):
        pass


def _handle(conn, signature):
    data = b""
    while chunk := conn.recv(65536):
        data += chunk
    if _signature() != signature:
        return False
    request = json.loads(data)
    from . import examples, queue
    status = 1
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = _SocketWriter(conn)   # threads of the capture print too
    try:
        examples.set_length(request.get("min_words"), request.get("max_words"))
        status = queue.process(request["path"])
    except Exception as e:
        print("STATUS: failed")
        print(f"RESULT: {e}")
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    conn.sendall(END + str(status).encode())
    return True


def serve():
    path = socket_path()
    signature = _signature()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if _connect(path):
        print("a worker is already running")
        return 0
    path.unlink(missing_ok=True)    # left over from a worker that was killed
    old_umask = os.umask(0o177)     # only this user may send captures (they cost API credit)
    try:
        server.bind(str(path))
    finally:
        os.umask(old_umask)
    server.listen()
    server.settimeout(IDLE_SECONDS)
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))   # clean up the socket on kill
    print(f"[{time.strftime('%F %T')}] worker {os.getpid()} listening on {path}", flush=True)

    from . import cards, verbformen   # the heavy imports, once
    del cards
    owned = True
    try:
        while True:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                print(f"[{time.strftime('%F %T')}] idle, exiting", flush=True)
                return 0
            with conn:
                conn.settimeout(None)
                if not _handle(conn, signature):
                    path.unlink(missing_ok=True)   # before replying, so the client starts a new one
                    owned = False
                    conn.sendall(STALE)
                    print(f"[{time.strftime('%F %T')}] config or code changed, exiting", flush=True)
                    return 0
    finally:
        server.close()
        if owned:
            path.unlink(missing_ok=True)
        verbformen.close()
