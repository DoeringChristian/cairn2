"""A durable queue between a program and a server.

Records are encoded once, stored here, and sent in order until the server has them, also
by a later process. Content waits here too, as a copy-on-write clone where the file system
can, or as the path of a file its owner promised not to change."""

import contextlib
import hashlib
import os
import shutil
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx

from . import cairn_pb2 as pb
from .errors import CairnError, Conflict, Corrupt, Full, NotFound, Pending

TABLES = """
CREATE TABLE IF NOT EXISTS artifacts (local TEXT PRIMARY KEY, open BLOB NOT NULL, id TEXT, left INTEGER NOT NULL DEFAULT 0,
  error TEXT);
CREATE TABLE IF NOT EXISTS entries (n INTEGER PRIMARY KEY AUTOINCREMENT, local TEXT NOT NULL, sequence INTEGER NOT NULL,
  kind INTEGER NOT NULL, data BLOB NOT NULL, digests BLOB NOT NULL, completed INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS content (digest BLOB PRIMARY KEY, size INTEGER NOT NULL, path TEXT NOT NULL, stamp INTEGER, link INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS results (local TEXT PRIMARY KEY, answer BLOB NOT NULL);
"""
RECORD, FINISH, LEAVE = range(3)
BATCH, BATCH_BYTES = 2000, 4 * 1024 * 1024
LIMIT, FREE, PATIENCE = 20 * 10**9, 5 * 10**9, 10


def clone(source, target):
    """Copy a file, sharing its blocks where the file system supports it."""
    if sys.platform == "darwin":
        import ctypes

        if ctypes.CDLL(None, use_errno=True).clonefile(os.fsencode(source), os.fsencode(target), 0) == 0:
            return
    elif sys.platform == "linux":
        import fcntl

        with contextlib.suppress(OSError), open(source, "rb") as a, open(target, "wb") as b:
            fcntl.ioctl(b, 0x40049409, a.fileno())  # FICLONE
            return
    shutil.copyfile(source, target)


class Outbox:
    def __init__(self, remote, folder, limit=LIMIT):
        self.remote, self.folder, self.limit = remote, Path(folder), limit
        self._used = 0
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.folder / "content").mkdir(exist_ok=True)
        self._db = sqlite3.connect(self.folder / "outbox.sqlite3", timeout=60, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(TABLES)
        self._lock, self._sending, self._wake, self._stop = threading.RLock(), threading.Lock(), threading.Event(), threading.Event()
        self._thread, self._pid, self.problem = None, os.getpid(), None

    def _start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="cairn-outbox", daemon=True)
            self._thread.start()

    @contextlib.contextmanager
    def _transaction(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def open(self, message):
        message.request_id = local = uuid.uuid4().hex
        with self._transaction() as db:
            db.execute("INSERT INTO artifacts (local, open) VALUES (?, ?)", (local, message.SerializeToString()))
        try:
            answer = self.remote.open(message)
        except BaseException:
            with self._transaction() as db:
                db.execute("DELETE FROM artifacts WHERE local = ?", (local,))
            raise
        with self._transaction() as db:
            db.execute("UPDATE artifacts SET id = ? WHERE local = ?", (answer.id, local))
        return local, answer

    def attach(self, identifier):
        local = uuid.uuid4().hex
        with self._transaction() as db:
            db.execute("INSERT INTO artifacts (local, open, id) VALUES (?, x'', ?)", (local, identifier))
        return local

    def room(self):
        """Refuse more while the server is away and too much is queued, so the disk never
        fills; wait while the server takes it, only slower than it comes."""
        since = None
        while self._full():
            self._start()
            self._wake.set()
            now = time.monotonic()
            since = since or now if self.problem else None
            if since and now - since > PATIENCE:
                raise Full(f"{self._used / 1e9:.1f} GB wait for {self.remote.url} and the disk has {shutil.disk_usage(self.folder).free / 1e9:.1f} GB left: {self.problem}")
            time.sleep(0.2)

    def _full(self):
        low = shutil.disk_usage(self.folder).free < FREE
        if not low and self._used < self.limit:
            return False
        # What a server refused stays, so waiting for it would never end.
        [[waiting]] = self._rows("SELECT COUNT(*) FROM entries e JOIN artifacts a ON a.local = e.local WHERE a.error IS NULL")
        [[self._used]] = self._rows("SELECT (SELECT COALESCE(SUM(LENGTH(data)), 0) FROM entries) + (SELECT COALESCE(SUM(size), 0) FROM content WHERE stamp IS NULL)")
        return waiting > 0 and (low or self._used >= self.limit)

    def add(self, local, sequence, kind, data, digests=(), completed=False):
        self._used += len(data)
        with self._transaction() as db:
            error = db.execute("SELECT error FROM artifacts WHERE local = ?", (local,)).fetchone()[0]
            if error:
                raise Conflict(f"The server refused this artifact's records: {error}")
            db.execute("INSERT INTO entries (local, sequence, kind, data, digests, completed) VALUES (?, ?, ?, ?, ?, ?)",
                       (local, sequence, kind, data, b"".join(digests), int(completed)))
        self._start()
        self._wake.set()

    def content(self, digest, size, path, *, stamp=None, link=False):
        """Content to upload before the records that hold it; without a stamp it lies in the outbox."""
        self._used += size if stamp is None else 0
        with self._transaction() as db:
            db.execute("INSERT OR REPLACE INTO content VALUES (?, ?, ?, ?, ?)", (digest, size, str(path), stamp, int(link)))

    def _run(self):
        delay = 0.1
        while not self._stop.is_set():
            self._wake.wait(1.0)
            self._wake.clear()
            self._stop.wait(0.05)  # let a burst of records travel together
            try:
                self._send()
                self.problem, delay = None, 0.1
            except (Pending, httpx.TransportError) as error:
                self.problem = error
                self._stop.wait(delay)
                delay = min(delay * 2, 10)
                self._wake.set()
            except Exception as error:  # noqa: BLE001 - shown by flush and by the next log
                self.problem = error
                self._stop.wait(1)

    def _send(self):
        with self._sending:
            for local, opened, identifier in self._rows("SELECT local, open, id FROM artifacts WHERE error IS NULL"):
                try:
                    if identifier is None:
                        answer = self.remote.open(pb.Open.FromString(opened))
                        identifier = answer.id
                        with self._transaction() as db:
                            db.execute("UPDATE artifacts SET id = ? WHERE local = ?", (identifier, local))
                    while self._deliver(local, identifier):
                        pass
                except (Pending, httpx.TransportError):
                    raise
                except CairnError as error:
                    # Only this artifact is refused; others go on, and its records stay here.
                    with self._transaction() as db:
                        db.execute("UPDATE artifacts SET error = ? WHERE local = ?", (str(error), local))
                    self.problem = error

    def _rows(self, sql, args=()):
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def _deliver(self, local, identifier):
        rows = self._rows("SELECT n, sequence, kind, data, digests, completed FROM entries WHERE local = ? ORDER BY n LIMIT ?", (local, BATCH))
        if not rows:
            return False
        kind = rows[0][2]
        batch, size = [], 0
        for row in rows:
            if row[2] != kind or (batch and (kind != RECORD or size + len(row[3]) > BATCH_BYTES)):
                break
            batch.append(row)
            size += len(row[3])
        self._upload(b"".join(row[4] for row in batch))
        answer = None
        if kind == RECORD:
            self.remote.append(pb.Append(artifact=identifier, writer=local, sequence=batch[0][1], records=[r[3] for r in batch]))
        elif kind == FINISH:
            answer = self.remote.finish(pb.Finish(artifact=identifier, writer=local, sequence=batch[0][1], record=batch[0][3], completed=bool(batch[0][5])))
        else:
            self.remote.leave(pb.Leave(artifact=identifier, writer=local))
        with self._transaction() as db:
            db.execute("DELETE FROM entries WHERE local = ? AND n <= ?", (local, batch[-1][0]))
            if answer is not None:
                db.execute("INSERT OR REPLACE INTO results VALUES (?, ?)", (local, answer.SerializeToString()))
            if kind != RECORD:
                db.execute("DELETE FROM artifacts WHERE local = ?", (local,))
        self._clean()
        return True

    def _upload(self, packed):
        digests = list(dict.fromkeys(packed[i : i + 32] for i in range(0, len(packed), 32)))
        for start in range(0, len(digests), 5000):
            missing = self.remote.have(pb.Have(digests=digests[start : start + 5000]))
            for digest, received in zip(missing.digests, missing.received, strict=True):
                rows = self._rows("SELECT size, path, stamp, link FROM content WHERE digest = ?", (digest,))
                if not rows:
                    raise NotFound(f"Content {digest.hex()} is neither on the server nor in the outbox")
                size, path, stamp, link = rows[0]
                if link:
                    try:
                        self.remote.link_blob(pb.LinkBlob(digest=digest, size=size, path=path))
                        continue
                    except (NotFound, CairnError) as error:
                        if isinstance(error, Pending):
                            raise
                        print(f"cairn: {error}; uploading a copy", file=sys.stderr)
                if stamp is not None and Path(path).stat().st_mtime_ns != stamp:
                    raise Corrupt(f"{path} changed before it was uploaded")
                with open(path, "rb") as stream:
                    self.remote.put(digest, stream, size, received)

    def _clean(self):
        if not self._rows("SELECT 1 FROM content LIMIT 1"):
            return
        with self._transaction() as db:
            held = {e[i : i + 32] for (e,) in db.execute("SELECT digests FROM entries") for i in range(0, len(e), 32)}
            rows = db.execute("SELECT digest, path, stamp FROM content").fetchall()
            gone = [r for r in rows if r[0] not in held] if not self._staging else []
            db.executemany("DELETE FROM content WHERE digest = ?", [(r[0],) for r in gone])
        for _, path, stamp in gone:
            if stamp is None:
                Path(path).unlink(missing_ok=True)

    _staging = 0

    @contextlib.contextmanager
    def staging(self):
        """Content named meanwhile is kept until its record is queued."""
        with self._lock:
            self._staging += 1
        try:
            yield self.folder / "content"
        finally:
            with self._lock:
                self._staging -= 1

    def result(self, local):
        rows = self._rows("SELECT answer FROM results WHERE local = ?", (local,))
        return pb.Artifact.FromString(rows[0][0]) if rows else None

    def pending(self):
        return self._rows("SELECT COUNT(*) FROM entries")[0][0]

    def refused(self, local=None):
        rows = self._rows("SELECT local, error FROM artifacts WHERE error IS NOT NULL")
        return [error for known, error in rows if local in (None, known)]

    def flush(self, timeout=None, local=None):
        """Send everything, and say why when that fails."""
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            try:
                self._send()
            except (Pending, httpx.TransportError) as error:
                if end is not None and time.monotonic() >= end:
                    raise Pending(f"Not everything reached the server yet: {error}") from error
                self._stop.wait(0.2)
                continue
            if refused := self.refused(local):
                raise Conflict(f"The server refused records: {refused[0]}")
            return

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread and self._pid == os.getpid():
            self._thread.join(timeout=10)
        with self._sending, self._lock:
            empty = not self._db.execute("SELECT 1 FROM entries UNION ALL SELECT 1 FROM artifacts WHERE error IS NOT NULL").fetchone()
            self._db.close()
        return empty


def digest_of(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").digest()
