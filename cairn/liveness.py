"""Heartbeats of open runs, and closing them when their process ends."""

from __future__ import annotations

import atexit
import contextlib
import os
import signal
import sys
import threading

INTERVAL = 10
EXIT_TIMEOUT = 30

_open = {}
_lock = threading.Lock()
_installed = None
_failure = None
_terminated = False


class Terminated(SystemExit):
    """SIGTERM reached a process with open runs; their contexts unwind as cancelled."""


class Heartbeat:
    """Reports a run's process as alive until the run ends. Failures never reach the run."""

    def __init__(self, run):
        self.backend, self.ids = run.client.backend, (run.id, run.writer_id)
        self.stopped = threading.Event()
        threading.Thread(target=self._beat, name="cairn-heartbeat", daemon=True).start()

    def _beat(self):
        while not self.stopped.is_set():
            # An older or unreachable server must not disturb the run.
            with contextlib.suppress(Exception):
                if not self.backend.heartbeat(*self.ids)["open"]:
                    return
            self.stopped.wait(INTERVAL)

    def stop(self):
        self.stopped.set()


def track(run):
    """Watch a run; cairn.init() runs also close when the interpreter exits."""
    with _lock:
        _install()
        _open[run.writer_id] = run


def forget(run):
    with _lock:
        _open.pop(run.writer_id, None)


def _install():
    global _installed
    if _installed == os.getpid():
        return
    first, _installed = _installed is None, os.getpid()
    _open.clear()
    if not first:
        return
    excepthook, leave = sys.excepthook, sys.exit

    def hook(kind, value, traceback):
        global _failure
        _failure = {"type": kind.__name__, "message": str(value)}
        excepthook(kind, value, traceback)

    def exit_(status=None, /):
        global _failure
        _failure = {"type": "SystemExit", "message": str(status)} if status else None
        leave(status)

    sys.excepthook, sys.exit = hook, exit_
    atexit.register(_finish)
    if (
        threading.current_thread() is threading.main_thread()
        and signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
    ):
        signal.signal(signal.SIGTERM, _terminate)


def _terminate(signum, frame):
    global _terminated
    # A second signal, a forked child and a process without open runs end at once.
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    if os.getpid() != _installed or not _open:
        os.kill(os.getpid(), signal.SIGTERM)
        return
    _terminated = True
    raise Terminated(128 + signum)


def _finish():
    if os.getpid() != _installed:
        return
    for run in list(_open.values()):
        if not run._owns_client or run._closed or run._finish_args is not None:
            continue
        outcome = "cancelled" if _terminated else "failed" if _failure else "completed"
        metadata = {"exception": _failure} if outcome == "failed" else None
        try:
            run.finish(outcome=outcome, metadata=metadata, timeout=EXIT_TIMEOUT)
        except Exception as error:  # noqa: BLE001 - exiting; a remote intent is already journaled
            print(f"cairn: could not finish {run!r}: {error}", file=sys.stderr)
