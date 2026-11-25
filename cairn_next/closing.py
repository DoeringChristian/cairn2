"""Artifacts a program leaves open, closed when it ends."""

import atexit
import os
import signal
import sys
import threading

TIMEOUT = 30

_open, _lock = {}, threading.Lock()
_pid, _error = None, None


class Terminated(SystemExit):
    """SIGTERM reached a program with open artifacts; their contexts unwind."""


def outcome(error):
    if error is None or (type(error) is SystemExit and not error.code):
        return "completed"
    return "cancelled" if isinstance(error, (KeyboardInterrupt, Terminated)) else "failed"


def track(made):
    with _lock:
        _install()
        _open[made._writer] = made


def forget(made):
    with _lock:
        _open.pop(made._writer, None)


def end(which, how, timeout=TIMEOUT, record=None):
    """Close open artifacts, the ones opened last first."""
    if os.getpid() != _pid:
        return
    for made in reversed(list(_open.values())):
        if not which(made):
            continue
        try:
            if made._attached:
                made.leave()
            else:
                made.finish(how, record=record, timeout=timeout)
        except Exception as error:  # noqa: BLE001 - the program ends; what is queued is not lost
            print(f"cairn: {made!r} could not be finished: {error}", file=sys.stderr)
        finally:
            forget(made)


def _install():
    global _pid
    if _pid == os.getpid():
        return
    first, _pid = _pid is None, os.getpid()
    _open.clear()  # a fork leaves its parent's artifacts alone
    if not first:
        return
    hook, leave = sys.excepthook, sys.exit

    def failed(kind, error, traceback):
        global _error
        _error = error
        hook(kind, error, traceback)

    def exit_(status=None, /):
        global _error
        _error = SystemExit(status)
        leave(status)

    sys.excepthook, sys.exit = failed, exit_
    atexit.register(_ended)
    if threading.current_thread() is threading.main_thread() and signal.getsignal(signal.SIGTERM) == signal.SIG_DFL:
        signal.signal(signal.SIGTERM, _terminate)


def _terminate(number, _frame):
    global _error
    # A second signal, a fork and a program with nothing open end at once.
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    if os.getpid() != _pid or not _open:
        os.kill(os.getpid(), number)
        return
    _error = Terminated(128 + number)
    raise _error


def _ended():
    how = outcome(_error)
    end(lambda _: True, how, record=None if how == "completed" else {"_error": {"type": type(_error).__name__, "message": str(_error)}})
