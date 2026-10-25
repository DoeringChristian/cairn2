"""A bounded, crash-durable remote journal. Repository reads never consult it."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from .media import _blob_descriptor, references
from .model import (
    CairnError,
    CapacityError,
    Conflict,
    IntegrityError,
    NotFound,
    Pending,
    ValidationError,
    canonical,
    new_id,
)
from .objects import ensure_directory, sync_directory


@contextlib.contextmanager
def _binary_source(source):
    if isinstance(source, (bytes, bytearray)):
        with io.BytesIO(source) as stream:
            yield stream
    elif isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as stream:
            yield stream
    elif hasattr(source, "read"):
        yield source
    else:
        raise ValidationError("Blob source must be bytes, a path or a binary stream")


class Delivery:
    """One destination's SQLite journal and immutable byte staging area.

    The same journal may be reopened after a crash. Sequences are allocated in
    SQLite transactions, so concurrent processes cannot reuse a producer sequence.
    Network dispatch may race safely: repository mutations are idempotent.
    """

    def __init__(
        self,
        backend,
        path,
        destination,
        *,
        max_bytes=2 * 1024**3,
        max_entries=100_000,
        batch_size=256,
        batch_bytes=4 * 1024**2,
        linger=0.5,
        background=True,
    ):
        self.backend = backend
        self.path = Path(path).expanduser().absolute()
        journal = self.path / "journal.sqlite3"
        if journal.exists():
            with contextlib.closing(
                sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True)
            ) as previous:
                exists = previous.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
                ).fetchone()
                state = dict(previous.execute("SELECT key,value FROM meta")) if exists else {}
                if state.get("schema_version") != "3" or state.get(
                    "destination"
                ) != destination.rstrip("/"):
                    raise Conflict("Outbox schema or destination does not match")
        ensure_directory(self.path)
        self.staging = self.path / "blobs"
        ensure_directory(self.staging)
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.batch_size = batch_size
        self.batch_bytes = batch_bytes
        self.linger = linger
        self._lock = threading.RLock()
        self._dispatch = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self.last_error = None
        self._db = sqlite3.connect(journal, timeout=30, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS writers (
                id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL,
                next_sequence INTEGER NOT NULL DEFAULT 1,
                acknowledged INTEGER NOT NULL DEFAULT 0,
                closing INTEGER NOT NULL DEFAULT 0,
                closed INTEGER NOT NULL DEFAULT 0,
                descriptor TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entries (
                writer_id TEXT NOT NULL REFERENCES writers(id),
                sequence INTEGER NOT NULL, payload TEXT NOT NULL,
                bytes INTEGER NOT NULL, PRIMARY KEY(writer_id,sequence)
            );
            CREATE TABLE IF NOT EXISTS blobs (
                digest TEXT PRIMARY KEY, size INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS starts (
                id TEXT PRIMARY KEY, method TEXT NOT NULL,
                args TEXT NOT NULL, result TEXT
            );
            CREATE TABLE IF NOT EXISTS operations (
                id TEXT PRIMARY KEY, writer_id TEXT NOT NULL REFERENCES writers(id),
                method TEXT NOT NULL, args TEXT NOT NULL,
                result TEXT, error TEXT, finished INTEGER NOT NULL DEFAULT 0,
                UNIQUE(writer_id,method,id)
            );
        """)
        # SQLite/WAL overhead is bounded independently of the staged payload budget.
        self._db.execute(f"PRAGMA max_page_count={max(64, (max_bytes + 4095) // 4096)}")
        self._db.execute("PRAGMA wal_autocheckpoint=128")
        os.chmod(self.path / "journal.sqlite3", 0o600)
        destination = destination.rstrip("/")
        with self._transaction():
            previous = self._meta("destination")
            if previous is not None and previous != destination:
                raise Conflict(
                    "Outbox belongs to a different destination", details={"destination": previous}
                )
            self._db.execute("INSERT OR IGNORE INTO meta VALUES ('destination',?)", (destination,))
            schema = self._meta("schema_version")
            if schema is not None and schema != "3":
                raise Conflict("Unknown outbox schema version")
            self._db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version','3')")
        try:
            identity = backend.info()["repository_id"]
        except CairnError:
            self._db.close()
            raise
        except Exception:
            with self._lock:
                identity = self._meta("repository_id")
            if identity is None:
                self._db.close()
                raise
            self._verified = False
        else:
            try:
                self._bind(identity)
            except BaseException:
                self._db.close()
                raise
            self._verified = True
        self.repository_id = identity
        with self._transaction():
            retained = {row[0] for row in self._db.execute("SELECT digest FROM blobs")}
            for staged_path in self.staging.iterdir():
                if staged_path.name not in retained and staged_path.is_file():
                    staged_path.unlink()
        if background:
            self._thread = threading.Thread(target=self._run, name="cairn-delivery", daemon=True)
            self._thread.start()

    @contextlib.contextmanager
    def _transaction(self):
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                yield
                self._db.commit()
            except sqlite3.OperationalError as exc:
                self._db.rollback()
                if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
                    raise CapacityError(
                        "Outbox database is full; retained entries were not removed"
                    ) from exc
                raise
            except BaseException:
                self._db.rollback()
                raise

    def _meta(self, key):
        row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def _bind(self, identity):
        with self._transaction():
            previous = self._meta("repository_id")
            if previous is not None and previous != identity:
                raise Conflict(
                    "The destination now points at a different repository; outbox retained",
                    details={"expected": previous, "actual": identity},
                )
            self._db.execute("INSERT OR IGNORE INTO meta VALUES ('repository_id',?)", (identity,))

    def _verify(self):
        # Recheck before every delivery pass, including an HTTP endpoint's replacement.
        identity = self.backend.info()["repository_id"]
        self._bind(identity)
        self._verified = True

    def _used(self):
        blobs = self._db.execute("SELECT COALESCE(SUM(size),0) FROM blobs").fetchone()[0]
        entries = self._db.execute("SELECT COALESCE(SUM(bytes),0) FROM entries").fetchone()[0]
        operations = self._db.execute(
            "SELECT COALESCE(SUM(LENGTH(args)+LENGTH(COALESCE(result,''))+"
            "LENGTH(COALESCE(error,''))),0) FROM operations"
        ).fetchone()[0]
        starts = self._db.execute(
            "SELECT COALESCE(SUM(LENGTH(args)+LENGTH(COALESCE(result,''))),0) FROM starts"
        ).fetchone()[0]
        writers = self._db.execute(
            "SELECT COALESCE(SUM(LENGTH(descriptor)),0) FROM writers"
        ).fetchone()[0]
        return blobs + entries + operations + starts + writers

    def _budget(self, extra):
        if self._used() + extra > self.max_bytes:
            raise CapacityError(
                "Remote outbox capacity exceeded; flush, prune or increase its limit",
                details={"max_bytes": self.max_bytes},
            )

    def stage_blob(self, source, *, digest=None, size=None):
        temporary = None
        try:
            with _binary_source(source) as stream, self._transaction():
                available = self.max_bytes - self._used()
                sha = hashlib.sha256()
                total = 0
                with tempfile.NamedTemporaryFile(
                    dir=self.staging, prefix=".stage-", delete=False
                ) as out:
                    temporary = Path(out.name)
                    while chunk := stream.read(1024 * 1024):
                        if not isinstance(chunk, bytes):
                            raise ValidationError("Blob streams must return bytes")
                        total += len(chunk)
                        if total > available:
                            raise CapacityError("Staged blob would exceed remote outbox capacity")
                        sha.update(chunk)
                        out.write(chunk)
                    out.flush()
                    os.fsync(out.fileno())
                actual = sha.hexdigest()
                if (digest is not None and digest != actual) or (
                    size is not None and size != total
                ):
                    raise IntegrityError("Blob content does not match its declared digest or size")
                target = self.staging / actual
                os.replace(temporary, target)
                temporary = None
                sync_directory(self.staging)
                self._db.execute("INSERT OR IGNORE INTO blobs VALUES (?,?)", (actual, total))
                return {"digest": actual, "size": total}
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _save_writer(self, response):
        writer = response["writer"]
        next_sequence = writer.get("next_sequence", 1)
        self._db.execute(
            "INSERT OR IGNORE INTO writers(id,artifact_id,next_sequence,acknowledged,descriptor) "
            "VALUES (?,?,?,?,?)",
            (
                writer["id"],
                writer["artifact_id"],
                next_sequence,
                next_sequence - 1,
                canonical(response).decode(),
            ),
        )

    def start(self, method, args):
        request_id = new_id("request")
        payload = canonical(args).decode()
        with self._transaction():
            self._budget(len(payload.encode()))
            self._db.execute(
                "INSERT INTO starts(id,method,args) VALUES (?,?,?)", (request_id, method, payload)
            )
        return self._deliver_start(request_id)

    def _deliver_start(self, request_id):
        with self._lock:
            row = self._db.execute("SELECT * FROM starts WHERE id=?", (request_id,)).fetchone()
        if row["result"] is not None:
            return json.loads(row["result"])
        self._verify()
        args = json.loads(row["args"])
        response = getattr(self.backend, row["method"])(**args, request_id=request_id)
        if row["method"] == "register_writer":
            response = {
                "writer": response,
                "artifact": self.backend.resolve(args["artifact_id"]),
            }
        with self._transaction():
            self._budget(2 * len(canonical(response)))
            self._save_writer(response)
            self._db.execute(
                "UPDATE starts SET result=? WHERE id=?", (canonical(response).decode(), request_id)
            )
        return response

    def recover(self):
        with self._lock:
            pending = self._db.execute("SELECT id FROM starts WHERE result IS NULL").fetchall()
        for row in pending:
            self._deliver_start(row["id"])
        with self._lock:
            rows = self._db.execute("SELECT * FROM writers ORDER BY rowid").fetchall()
            recovered = []
            for row in rows:
                response = json.loads(row["descriptor"])
                response["writer"]["closed"] = bool(row["closing"])
                response["writer"]["next_sequence"] = row["next_sequence"]
                state = self._db.execute(
                    "SELECT args,result FROM operations WHERE writer_id=? AND method='finish'",
                    (row["id"],),
                ).fetchone()
                if state is not None:
                    response["finish_args"] = json.loads(state["args"])
                    response["result"] = json.loads(state["result"]) if state["result"] else None
                recovered.append(response)
        return recovered

    def completed_result(self, writer_id):
        with self._lock:
            row = self._db.execute(
                "SELECT result FROM operations WHERE writer_id=? AND method='finish'",
                (writer_id,),
            ).fetchone()
        return json.loads(row["result"]) if row is not None and row["result"] else None

    def writer(self, writer_id):
        with self._lock:
            row = self._db.execute("SELECT * FROM writers WHERE id=?", (writer_id,)).fetchone()
        if row is None:
            raise NotFound("Writer is not in this outbox")
        return dict(row)

    def append(self, writer_id, kind, data):
        with self._transaction():
            writer = self.writer(writer_id)
            if writer["closing"]:
                raise Conflict("This writer has already requested closure")
            sequence = writer["next_sequence"]
            payload = canonical({"sequence": sequence, "kind": kind, "data": data}).decode()
            amount = len(payload.encode())
            if amount > 1024**2:
                raise CapacityError("Record exceeds 1 MiB; use a typed value with blobs")
            count = self._db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            if count >= self.max_entries:
                raise CapacityError("Remote outbox entry limit reached")
            self._budget(amount)
            self._db.execute(
                "INSERT INTO entries VALUES (?,?,?,?)", (writer_id, sequence, payload, amount)
            )
            self._db.execute(
                "UPDATE writers SET next_sequence=next_sequence+1 WHERE id=?", (writer_id,)
            )
        self._wake.set()
        return {"sequence": sequence, "durable": True}

    def operation(self, writer_id, method, args):
        """Journal a finish intent and its exact retry identity before I/O."""
        if method != "finish":
            raise ValidationError("Only finish operations are supported")
        payload = canonical(args).decode()
        with self._transaction():
            self.writer(writer_id)
            if method == "finish":
                existing = self._db.execute(
                    "SELECT * FROM operations WHERE writer_id=? AND method='finish'", (writer_id,)
                ).fetchone()
                if existing:
                    if existing["args"] != payload:
                        raise Conflict("A different finalization intent is already journaled")
                    return existing["id"]
                self._db.execute("UPDATE writers SET closing=1 WHERE id=?", (writer_id,))
            self._budget(len(payload.encode()))
            operation_id = new_id("request")
            self._db.execute(
                "INSERT INTO operations(id,writer_id,method,args) VALUES (?,?,?,?)",
                (operation_id, writer_id, method, payload),
            )
        self._wake.set()
        return operation_id

    def discard_rejected_finish(self, writer_id):
        """Permit correction only after a terminal capacity rejection without server intent."""
        with self._dispatch:
            with self._lock:
                operation = self._db.execute(
                    "SELECT * FROM operations WHERE writer_id=? AND method='finish'",
                    (writer_id,),
                ).fetchone()
            if (
                not operation
                or not operation["finished"]
                or not operation["error"]
                or json.loads(operation["error"])["code"] != CapacityError.code
            ):
                return False
            writer = self.writer(writer_id)
            if self.backend.resolve(writer["artifact_id"])["intent"] is not None:
                return False
            with self._transaction():
                self._db.execute("DELETE FROM operations WHERE id=?", (operation["id"],))
            return True

    def request_close(self, writer_id):
        with self._transaction():
            self.writer(writer_id)
            self._db.execute("UPDATE writers SET closing=1 WHERE id=?", (writer_id,))
        self._wake.set()

    def result(self, operation_id):
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM operations WHERE id=?", (operation_id,)
            ).fetchone()
        if row["result"] is not None:
            return json.loads(row["result"])
        if row["error"]:
            error = json.loads(row["error"])
            from . import model

            cls = next(
                (
                    candidate
                    for candidate in vars(model).values()
                    if isinstance(candidate, type)
                    and issubclass(candidate, CairnError)
                    and getattr(candidate, "code", None) == error["code"]
                ),
                CairnError,
            )
            raise cls(error["message"], details=error["details"])
        return None

    def _ensure_blobs(self, values, deadline=None):
        required = {
            blob["digest"]: _blob_descriptor(blob)
            for value in values
            for kind, blob in references(value)
            if kind == "blob"
        }
        if not required:
            return
        with self._request_timeout(deadline):
            existing = self.backend.has_blobs(list(required))
        for digest, descriptor in required.items():
            path = self.staging / digest
            if not existing.get(digest):
                if not path.is_file():
                    raise IntegrityError(
                        "A required blob is absent from the repository and outbox",
                        details={"digest": digest},
                    )
                with self._request_timeout(deadline):
                    self.backend.put_blob(path, digest=digest, size=descriptor["size"])
            # Acknowledged immutable bytes are now safely stored by this repository.
            with self._transaction():
                self._db.execute("DELETE FROM blobs WHERE digest=?", (digest,))
                path.unlink(missing_ok=True)

    @contextlib.contextmanager
    def _request_timeout(self, deadline):
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise Pending("Flush timed out; undelivered data remains in the outbox")
        scope = getattr(self.backend, "request_timeout", None)
        with scope(remaining) if scope and remaining is not None else contextlib.nullcontext():
            yield

    def _deliver_writer(self, writer_id, deadline):
        writer = self.writer(writer_id)
        entries = []
        size = 0
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM entries WHERE writer_id=? ORDER BY sequence LIMIT ?",
                (writer_id, self.batch_size),
            )
            for row in rows:
                if entries and size + row["bytes"] > self.batch_bytes:
                    break
                entries.append(json.loads(row["payload"]))
                size += row["bytes"]
            rows.close()
        if entries:
            self._ensure_blobs([entry["data"] for entry in entries], deadline)
            with self._request_timeout(deadline):
                response = self.backend.append_batch(writer["artifact_id"], writer_id, entries)
            acknowledged = response["acknowledged"]
            if acknowledged < entries[-1]["sequence"]:
                raise IntegrityError("Repository did not acknowledge the complete atomic batch")
            with self._transaction():
                self._db.execute(
                    "DELETE FROM entries WHERE writer_id=? AND sequence<=?",
                    (writer_id, acknowledged),
                )
                self._db.execute(
                    "UPDATE writers SET acknowledged=MAX(acknowledged,?) WHERE id=?",
                    (acknowledged, writer_id),
                )
        writer = self.writer(writer_id)
        if writer["acknowledged"] < writer["next_sequence"] - 1:
            return
        if writer["closing"] and not writer["closed"]:
            with self._request_timeout(deadline):
                self.backend.close_writer(writer["artifact_id"], writer_id, writer["acknowledged"])
            with self._transaction():
                self._db.execute("UPDATE writers SET closed=1 WHERE id=?", (writer_id,))
        with self._lock:
            operations = self._db.execute(
                "SELECT * FROM operations WHERE writer_id=? AND finished=0 ORDER BY rowid LIMIT 1",
                (writer_id,),
            ).fetchall()
        for operation in operations:
            args = json.loads(operation["args"])
            try:
                self._ensure_blobs([args], deadline)
                with self._request_timeout(deadline):
                    result = getattr(self.backend, operation["method"])(
                        writer["artifact_id"], **args, request_id=operation["id"]
                    )
            except CairnError as exc:
                error = canonical({"code": exc.code, "message": str(exc), "details": exc.details})
                with self._transaction():
                    self._db.execute(
                        "UPDATE operations SET error=?,finished=? WHERE id=?",
                        (error.decode(), 0 if isinstance(exc, Pending) else 1, operation["id"]),
                    )
                if not isinstance(exc, Pending):
                    raise
            else:
                with self._transaction():
                    self._budget(len(canonical(result)))
                    self._db.execute(
                        "UPDATE operations SET result=?,error=NULL,finished=1 WHERE id=?",
                        (canonical(result).decode(), operation["id"]),
                    )

    def _pass(self, writer_id=None, deadline=None):
        remaining = None if deadline is None else max(0, deadline - time.monotonic())
        acquired = (
            self._dispatch.acquire()
            if remaining is None
            else self._dispatch.acquire(timeout=remaining)
        )
        if not acquired:
            raise Pending("Flush timed out waiting for delivery; journal contents are retained")
        try:
            with self._request_timeout(deadline):
                self._verify()
            with self._lock:
                writers = (
                    [writer_id]
                    if writer_id
                    else [
                        row[0]
                        for row in self._db.execute(
                            "SELECT id FROM writers WHERE closed=0 OR EXISTS "
                            "(SELECT 1 FROM operations WHERE writer_id=writers.id AND finished=0)"
                        )
                    ]
                )
            for identity in writers:
                self._deliver_writer(identity, deadline)
        finally:
            self._dispatch.release()

    def pending(self, writer_id=None):
        with self._lock:
            if writer_id is None:
                return self._db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            return self._db.execute(
                "SELECT COUNT(*) FROM entries WHERE writer_id=?", (writer_id,)
            ).fetchone()[0]

    def flush(self, writer_id=None, *, timeout=None, operation_id=None):
        deadline = None if timeout is None else time.monotonic() + max(0, timeout)
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise Pending(
                    "Flush timed out; undelivered data remains in the outbox",
                    details={"pending": self.pending(writer_id)},
                )
            try:
                self._pass(writer_id, deadline)
                if operation_id:
                    result = self.result(operation_id)
                    if result is not None:
                        return result
                elif self.pending(writer_id) == 0:
                    return {"pending": 0}
            except CairnError:
                raise
            except Exception as exc:
                self.last_error = exc
                if deadline is None:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Pending(
                        "Flush timed out; undelivered data remains in the outbox",
                        details={"pending": self.pending(writer_id)},
                    ) from exc
                self._stop.wait(min(0.1, remaining))

    def _run(self):
        delay, idle = 0.1, False
        while not self._stop.is_set():
            self._wake.wait(delay)
            self._wake.clear()
            if idle:
                # Collect what is logged meanwhile, so one transaction carries it.
                self._stop.wait(self.linger)
            if self._stop.is_set():
                break
            try:
                self._pass()
                self.last_error = None
                idle = not self.pending()
                delay = 1.0 if idle else 0.1
            except Exception as exc:  # noqa: BLE001 - retain and expose dispatcher failures.
                self.last_error = exc
                delay = min(delay * 2, 10.0)

    def close(self):
        """Stop dispatch and close SQLite; pending data deliberately stays on disk."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=getattr(self.backend, "timeout", 30) + 1)
            if self._thread.is_alive():
                raise Pending("Delivery is still in a network request; outbox remains open")
        with self._lock:
            self._db.close()

    def prune(self):
        """Explicitly discard local history for fully delivered, closed producers."""
        with self._transaction():
            rows = self._db.execute(
                "SELECT id FROM writers WHERE closed=1 AND NOT EXISTS "
                "(SELECT 1 FROM operations WHERE writer_id=writers.id AND result IS NULL) "
                "AND NOT EXISTS (SELECT 1 FROM entries WHERE writer_id=writers.id)"
            ).fetchall()
            for row in rows:
                writer_id = row["id"]
                self._db.execute("DELETE FROM operations WHERE writer_id=?", (writer_id,))
                self._db.execute("DELETE FROM writers WHERE id=?", (writer_id,))
            for row in self._db.execute("SELECT id,result FROM starts WHERE result IS NOT NULL"):
                if not self._db.execute(
                    "SELECT 1 FROM writers WHERE id=?", (json.loads(row["result"])["writer"]["id"],)
                ).fetchone():
                    self._db.execute("DELETE FROM starts WHERE id=?", (row["id"],))
        return len(rows)
