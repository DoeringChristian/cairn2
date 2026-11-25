"""What a program prints, logged while it still reaches the terminal unchanged."""

import contextlib
import errno
import os
import select
import sys
import threading

LIMIT = 64 * 1024 * 1024
CHUNK = 1024 * 1024
CHUNKS = 4096
INTERVAL = 5
MAX_INTERVAL = 300

_active = threading.Lock()


class Console:
    """Logs stdout and stderr as `_console`, in pieces.

    Descriptors 1 and 2 are redirected, so extensions and subprocesses are included; a
    terminal stays one through a pseudo-terminal. Without descriptors (e.g. Jupyter)
    sys.stdout and sys.stderr are wrapped instead. Pieces get rarer as a run grows."""

    def __init__(self, artifact):
        self.artifact = artifact
        self.buffer = bytearray()
        self.lock = threading.Lock()
        self.flushing = threading.Lock()
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.chunks = self.stored = 0
        self.failed = False
        self.pid = os.getpid()
        self.restore = []
        self.threads = [threading.Thread(target=self._tick, daemon=True)]

    def start(self):
        if not _active.acquire(blocking=False):
            return False
        try:
            if _descriptors():
                sys.stdout.flush()
                sys.stderr.flush()
                for fd in (1, 2):
                    read, write = _channel(fd)
                    saved = os.dup(fd)
                    os.dup2(write, fd)
                    os.close(write)
                    self.restore.append(lambda fd=fd, saved=saved: os.dup2(saved, fd))
                    self.threads.append(
                        threading.Thread(target=self._pump, args=(read, saved), daemon=True)
                    )
            else:
                for name in ("stdout", "stderr"):
                    stream = getattr(sys, name)
                    setattr(sys, name, _Tee(stream, self._write))
                    self.restore.append(lambda name=name, stream=stream: setattr(sys, name, stream))
        except Exception:  # noqa: BLE001 - a program runs on without it
            self._undo()
            _active.release()
            return False
        for thread in self.threads:
            thread.start()
        return True

    def stop(self):
        if self.stopped.is_set() or os.getpid() != self.pid:
            return
        with contextlib.suppress(OSError, ValueError):
            sys.stdout.flush()
            sys.stderr.flush()
        self._undo()
        self.stopped.set()
        self.wake.set()
        for thread in self.threads:
            thread.join(timeout=5)
        self._flush()
        _active.release()

    def _undo(self):
        while self.restore:
            self.restore.pop()()

    def _write(self, data):
        with self.lock:
            self.buffer += data
            if len(self.buffer) >= CHUNK:
                self.wake.set()

    def _flush(self):
        with self.flushing:
            with self.lock:
                data, self.buffer = bytes(self.buffer[: LIMIT - self.stored]), bytearray()
            if not data or self.failed or self.stored >= LIMIT:
                return
            self.stored += len(data)
            self.chunks += 1
            if self.stored >= LIMIT or self.chunks == CHUNKS:
                self.stored = LIMIT
                data += b"\n[cairn: console output truncated]\n"
            try:
                self.artifact._log({"_console": data})
            except Exception as error:  # noqa: BLE001 - a program runs on without it
                self.failed = True
                print(f"cairn: console output is no longer logged: {error}", file=sys.stderr)

    def _tick(self):
        while not self.stopped.is_set():
            self.wake.wait(min(MAX_INTERVAL, INTERVAL + self.chunks))
            self.wake.clear()
            if not self.stopped.is_set():
                self._flush()

    def _pump(self, read, saved):
        poll, echo = select.poll(), True
        poll.register(read, select.POLLIN)
        try:
            while True:
                if not poll.poll(200):
                    if self.stopped.is_set():
                        return
                    continue
                try:
                    data = os.read(read, 65536)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    return
                if not data:
                    return
                self._write(data)
                try:
                    while echo and data:
                        data = data[os.write(saved, data) :]
                except OSError:
                    echo = False
        finally:
            os.close(read)
            os.close(saved)


class _Tee:
    def __init__(self, stream, write):
        self._stream, self._write = stream, write

    def write(self, text):
        self._write(text.encode("utf-8", "replace"))
        return self._stream.write(text)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _descriptors():
    try:
        return os.name == "posix" and sys.stdout.fileno() == 1 and sys.stderr.fileno() == 2
    except (AttributeError, OSError, ValueError):
        return False


def _channel(fd):
    """A pipe, or a raw pseudo-terminal of the same size when fd is a terminal."""
    if not os.isatty(fd):
        return os.pipe()
    import fcntl
    import pty
    import termios

    read, write = pty.openpty()
    attributes = termios.tcgetattr(write)
    attributes[1] &= ~termios.OPOST
    termios.tcsetattr(write, termios.TCSANOW, attributes)
    fcntl.ioctl(write, termios.TIOCSWINSZ, fcntl.ioctl(fd, termios.TIOCGWINSZ, bytes(8)))
    return read, write


def capture(artifact):
    console = Console(artifact)
    return console if console.start() else None
