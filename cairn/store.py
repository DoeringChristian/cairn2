"""The repository: records, blobs and references, and the indexes derived from them.

Every operation takes and returns a protocol message, so a local repository and a server
are used alike. Only `segments`, `tail`, `artifacts`, `names`, `labels` and the blobs are the truth;
every other table is an index that `rebuild` derives from the records again."""

import contextlib
import fcntl
import hashlib
import math
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
import zlib
from array import array
from pathlib import Path

from . import cairn_pb2 as pb
from .errors import Conflict, Corrupt, Invalid, NotFound, Pending
from .values import MAX_RECORD, decode, encode

SCHEMA = 4
SEGMENT_RECORDS = 1024
SEGMENT_BYTES = 1024 * 1024
TEXT = 256  # longer text is not searchable
PAGE = 8 * 1024 * 1024
NONE, FLAG, INT, FLOAT, STRING, BLOB, REF = range(7)
NAME = re.compile(r"[^\x00-\x1f@*?\[\]]{1,255}")
KEY = re.compile(r"@?[^\s@=:\x00-\x1f][^\s=\x00-\x1f]{0,99}")
ALIAS = re.compile(r"@[A-Za-z][\w.-]{0,63}")
NAMESPACE = re.compile(r"[a-z][a-z0-9_-]{0,63}")
OPERATORS = {"=": "=", "!=": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">="}

TABLES = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT) WITHOUT ROWID;
CREATE TABLE artifacts (
  n INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, project TEXT NOT NULL, namespace TEXT NOT NULL,
  name TEXT NOT NULL, finished INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0, version INTEGER, records INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL, ended REAL, heard REAL NOT NULL, request TEXT UNIQUE, deleted INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX versions ON artifacts (project, namespace, name, version);
CREATE INDEX newest ON artifacts (project, namespace, n) WHERE deleted = 0;
CREATE TABLE names (project TEXT, namespace TEXT, name TEXT, next INTEGER NOT NULL,
  PRIMARY KEY (project, namespace, name)) WITHOUT ROWID;
CREATE TABLE labels (artifact INTEGER, key TEXT, kind INTEGER NOT NULL, num, text TEXT, PRIMARY KEY (artifact, key)) WITHOUT ROWID;
CREATE INDEX label_num ON labels (key, num);
CREATE INDEX label_text ON labels (key, text);
CREATE TABLE writers (id TEXT PRIMARY KEY, artifact INTEGER NOT NULL, sequence INTEGER NOT NULL,
  left INTEGER NOT NULL DEFAULT 0) WITHOUT ROWID;
CREATE INDEX writing ON writers (artifact, left);
CREATE TABLE tail (artifact INTEGER, first INTEGER, count INTEGER NOT NULL, lengths BLOB NOT NULL, data BLOB NOT NULL,
  sha BLOB NOT NULL, PRIMARY KEY (artifact, first));
CREATE TABLE segments (artifact INTEGER, first INTEGER, count INTEGER NOT NULL, lengths BLOB NOT NULL,
  data BLOB NOT NULL, sha BLOB NOT NULL, PRIMARY KEY (artifact, first));

CREATE TABLE blobs (digest BLOB PRIMARY KEY, size INTEGER NOT NULL, created REAL NOT NULL) WITHOUT ROWID;
CREATE TABLE places (digest BLOB, place TEXT, stamp INTEGER, PRIMARY KEY (digest, place)) WITHOUT ROWID;

CREATE TABLE paths (id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE);
CREATE TABLE keys (artifact INTEGER, key INTEGER, ordinal INTEGER NOT NULL, nested INTEGER NOT NULL,
  PRIMARY KEY (artifact, key)) WITHOUT ROWID;
CREATE TABLE marks (artifact INTEGER, key INTEGER, ordinal INTEGER, PRIMARY KEY (artifact, key, ordinal)) WITHOUT ROWID;
CREATE TABLE latest (path INTEGER, artifact INTEGER, key INTEGER NOT NULL, kind INTEGER NOT NULL, num, text TEXT,
  min REAL, max REAL, PRIMARY KEY (path, artifact)) WITHOUT ROWID;
CREATE INDEX latest_num ON latest (path, num);
CREATE INDEX latest_text ON latest (path, text);
CREATE INDEX latest_min ON latest (path, min);
CREATE INDEX latest_max ON latest (path, max);
CREATE INDEX latest_key ON latest (artifact, key);
CREATE TABLE columns (artifact INTEGER, path INTEGER, first INTEGER, ordinals BLOB NOT NULL, vals BLOB NOT NULL,
  PRIMARY KEY (artifact, path, first)) WITHOUT ROWID;
CREATE TABLE files (n INTEGER PRIMARY KEY, artifact INTEGER NOT NULL, path TEXT NOT NULL, key INTEGER NOT NULL,
  ordinal INTEGER NOT NULL, blob BLOB NOT NULL);
CREATE UNIQUE INDEX file_path ON files (artifact, path);
CREATE INDEX file_key ON files (artifact, key);
CREATE INDEX file_name ON files (path);
CREATE VIRTUAL TABLE file_text USING fts5 (path, content='files', content_rowid='n', tokenize='trigram', detail='none');
CREATE TABLE holds (digest BLOB, artifact INTEGER, PRIMARY KEY (digest, artifact)) WITHOUT ROWID;
CREATE TABLE refs (artifact INTEGER, path TEXT, target INTEGER, PRIMARY KEY (artifact, target, path)) WITHOUT ROWID;
CREATE INDEX referred ON refs (target);
"""
INDEXES = ("keys", "marks", "latest", "columns", "files", "holds", "refs")


TEXT_SOURCE = "file_text CROSS JOIN files f ON f.n = file_text.rowid CROSS JOIN artifacts a ON a.n = f.artifact"


def _trigrams(text):
    terms = dict.fromkeys(text[i : i + 3] for i in range(len(text) - 2))
    return " AND ".join('"' + t.replace('"', '""') + '"' for t in terms)


def _glob(pattern):
    """`*` stays within one level of a name, `**` crosses slashes."""
    parts = re.split(r"(\*\*|\*|\?)", pattern)
    table = {"**": ".*", "*": "[^/]*", "?": "[^/]"}
    return re.compile("".join(table.get(p) or re.escape(p) for p in parts) + r"\Z", re.S)


def _stored(kind, num, text):
    if kind in (STRING, REF):
        return text
    return None if kind == NONE else math.nan if text == "nan" else bool(num) if kind == FLAG else num


def _retry(call, seconds=60):
    """Switching to WAL does not wait for other processes by itself."""
    end = time.monotonic() + seconds
    while True:
        try:
            return call()
        except sqlite3.OperationalError as error:
            if "locked" not in str(error) or time.monotonic() > end:
                raise
            time.sleep(0.02)


def _prefix(pattern):
    return re.split(r"[*?]", pattern, maxsplit=1)[0]


def _after(prefix):
    """The smallest string after every string with this prefix."""
    return prefix + "\U0010ffff"


class _Batch:
    """What a batch of records changes in the indexes."""

    def __init__(self):
        self.keys, self.marks, self.latest, self.files, self.refs, self.holds = {}, [], {}, {}, [], set()
        self.columns = {}

    def read(self, data, ordinal):
        for entry in pb.Map.FromString(data).entries:
            key, kind = entry.key, entry.value.WhichOneof("kind")
            if kind in ("int", "float", "flag") and not (known := self.keys.get(key)):
                # The common case: a number that was one before.
                self.keys[key] = [ordinal, False]
                self._number(key, key, kind, entry.value, ordinal)
            elif kind in ("int", "float", "flag") and not known[1]:
                known[0] = ordinal
                self._number(key, key, kind, entry.value, ordinal)
            else:
                # The key is the unit: what it held before is gone.
                self._clear(key)
                nested = kind in ("map", "list", "blob", "ref")
                known = self.keys.get(key)
                self.keys[key] = [ordinal, nested or bool(known and known[1])]
                if kind not in ("int", "float", "flag"):  # those are in the columns
                    self.marks.append((key, ordinal))
                self._walk(key, key, entry.value, ordinal)

    def _clear(self, key):
        if (known := self.keys.get(key)) is None:
            return
        for table in (self.latest, self.files):
            for path in [p for p, row in table.items() if row[0] == key] if known[1] else [key]:
                table.pop(path, None)

    def _number(self, key, path, kind, value, ordinal):
        exact = getattr(value, kind)
        number = float(exact)
        column = self.columns.get(path)
        if column is None:
            column = self.columns[path] = (array("Q"), array("d"))
        column[0].append(ordinal)
        column[1].append(number)
        code = FLAG if kind == "flag" else INT if kind == "int" else FLOAT
        old = self.latest.get(path)
        finite = math.isfinite(number)
        low, high = (number, number) if finite else (None, None)
        if old is not None and old[4] is not None:
            low, high = (min(old[4], number), max(old[5], number)) if finite else old[4:6]
        # SQLite has no NaN: the number is then missing, and its text says which it is.
        self.latest[path] = (key, code, None if number != number else exact,
                             "nan" if number != number else None, low, high)

    def _walk(self, key, path, value, ordinal):
        kind = value.WhichOneof("kind")
        if kind == "map":
            for entry in value.map.entries:
                self._walk(key, f"{path}/{entry.key}", entry.value, ordinal)
        elif kind == "list":
            # Lists keep their content alive but are not searchable.
            for item in value.list.items:
                self._hold(path, item)
        elif kind in ("int", "float", "flag"):
            self._number(key, path, kind, value, ordinal)
        elif kind == "text":
            if len(value.text) <= TEXT:
                self.latest[path] = (key, STRING, None, value.text, None, None)
        elif kind == "blob":
            self.files[path] = (key, ordinal, value.blob.SerializeToString())
            self._hold(path, value)
        elif kind == "ref":
            self._hold(path, value)
            self.latest[path] = (key, REF, None, value.ref.artifact, None, None)

    def _hold(self, path, value):
        kind = value.WhichOneof("kind")
        if kind == "blob":
            self.holds.add(value.blob.digest)
        elif kind == "ref":
            self.refs.append((path, value.ref.artifact))
        elif kind == "list":
            for item in value.list.items:
                self._hold(path, item)
        elif kind == "map":
            for entry in value.map.entries:
                self._hold(path, entry.value)


class Repository:
    def __init__(self, path, *, link_roots=None):
        self.path = Path(path).expanduser().absolute()
        if (self.path / "metadata.sqlite3").exists() or (self.path / "repo.json").exists():
            raise Invalid(f"{self.path} is a repository of an older Cairn, which this version does not read")
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / "blobs").mkdir(exist_ok=True)
        # A local repository may link anything its user can read; a server names its folders.
        self.link_roots = None if link_roots is None else [Path(r).resolve() for r in link_roots]
        self._local, self._write = threading.local(), threading.RLock()
        self._connections, self._paths, self._uploads = [], {}, {}
        with self._transaction(write=True) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'meta'").fetchone():
                for statement in TABLES.split(";\n"):
                    if statement.strip():
                        db.execute(statement)
                db.execute("INSERT INTO meta VALUES ('schema', ?), ('id', ?)", (str(SCHEMA), uuid.uuid4().hex))
            found = db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0]
            if found != str(SCHEMA):
                raise Conflict(f"Repository schema {found}; this version needs {SCHEMA}")

    def close(self):
        with self._write:
            for connection in self._connections:
                connection.close()
            self._connections.clear()
            self._local = threading.local()

    def _db(self):
        db = getattr(self._local, "db", None)
        if db is None:
            db = sqlite3.connect(self.path / "cairn.sqlite3", timeout=60, isolation_level=None, check_same_thread=False)
            for pragma in ("journal_mode=WAL", "synchronous=NORMAL", "cache_size=-65536", "mmap_size=1073741824", "temp_store=MEMORY"):
                _retry(lambda p=pragma: db.execute("PRAGMA " + p))
            self._local.db = db
            self._connections.append(db)
        return db

    @contextlib.contextmanager
    def _transaction(self, write=False):
        db = self._db()
        with self._write if write else contextlib.nullcontext():
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                # Paths interned by a transaction that did not happen.
                self._paths.clear()
                raise
            db.execute("COMMIT")

    # Artifacts

    _COLUMNS = "n, id, project, namespace, name, finished, version, records, created, ended, heard, completed"

    def _message(self, db, row):
        found = pb.Artifact(id=row[1], project=row[2], namespace=row[3], name=row[4], finished=bool(row[5]),
                            version=row[6] or 0, records=row[7], created_at=row[8], finished_at=row[9] or 0, heard_at=row[10], completed=bool(row[11]))
        for key, kind, num, text in db.execute("SELECT key, kind, num, text FROM labels WHERE artifact = ? ORDER BY key", (row[0],)):
            if key.startswith("@"):
                found.aliases.append(key[1:])
            else:
                encode(_stored(kind, num, text), found.tags.entries.add(key=key).value)
        return found

    def _row(self, db, identifier, *, deleted=False):
        row = db.execute(f"SELECT {self._COLUMNS}, deleted FROM artifacts WHERE id = ?", (identifier,)).fetchone()
        if row is None or (row[12] and not deleted):
            raise NotFound(f"Unknown artifact: {identifier}")
        return row

    def open(self, m):
        if not all(isinstance(v, str) and NAME.fullmatch(v) for v in (m.project, m.name)) or not NAMESPACE.fullmatch(m.namespace):
            raise Invalid("Names have 1-255 characters without @ * ? [ ], namespaces are lowercase identifiers")
        with self._transaction(write=True) as db:
            if m.request_id and (old := db.execute("SELECT id FROM artifacts WHERE request = ?", (m.request_id,)).fetchone()):
                return self._message(db, self._row(db, old[0]))
            db.execute("INSERT OR IGNORE INTO names VALUES (?, ?, ?, 1)", (m.project, m.namespace, m.name))
            stamp, identifier = time.time(), uuid.uuid4().hex
            db.execute("INSERT INTO artifacts (id, project, namespace, name, created, heard, request) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (identifier, m.project, m.namespace, m.name, stamp, stamp, m.request_id or None))
            return self._message(db, self._row(db, identifier))

    def append(self, m):
        with self._transaction(write=True) as db:
            row = self._row(db, m.artifact)
            return pb.Appended(records=self._append(db, row, m.writer, m.sequence, list(m.records)))

    def _append(self, db, row, writer, sequence, records):
        """Records after the writer's last one. Sending a record again changes nothing."""
        if not writer or sequence < 1:
            raise Invalid("A batch needs a writer and the sequence of its first record")
        known = db.execute("SELECT artifact, sequence, left FROM writers WHERE id = ?", (writer,)).fetchone()
        if known and known[0] != row[0]:
            raise Conflict("The writer belongs to another artifact")
        done = known[1] if known else 0
        if sequence > done + 1:
            raise Pending("Records before this batch are missing", expected=done + 1)
        records = records[done + 1 - sequence :]
        if not records and known:
            return row[7]
        if row[5]:
            raise Conflict("The artifact is finished")
        if known and known[2]:
            raise Conflict("The writer has left")
        if any(len(r) > MAX_RECORD for r in records):
            raise Invalid("A record holds at most 64 MiB")
        batch, total = _Batch(), row[7]
        for i, data in enumerate(records):
            try:
                batch.read(data, total + 1 + i)
            except Exception as error:
                raise Invalid(f"Not a record: {error}") from None
        self._index(db, row[0], row[1], batch)
        if records:
            data = b"".join(records)
            db.execute("INSERT INTO tail VALUES (?, ?, ?, ?, ?, ?)", (row[0], total + 1, len(records),
                       array("I", map(len, records)).tobytes(), data, hashlib.sha256(data).digest()))
        total += len(records)
        db.execute("INSERT INTO writers VALUES (?, ?, ?, 0) ON CONFLICT (id) DO UPDATE SET sequence = excluded.sequence",
                   (writer, row[0], done + len(records)))
        db.execute("UPDATE artifacts SET records = ?, heard = ? WHERE n = ?", (total, time.time(), row[0]))
        self._seal(db, row[0], False)
        return total

    def _path(self, db, path):
        found = self._paths.get(path)
        if found is None:
            row = db.execute("SELECT id FROM paths WHERE path = ?", (path,)).fetchone()
            found = row[0] if row else db.execute("INSERT INTO paths (path) VALUES (?)", (path,)).lastrowid
            if len(self._paths) > 200_000:
                self._paths.clear()
            self._paths[path] = found
        return found

    def _known(self, db, path):
        row = db.execute("SELECT id FROM paths WHERE path = ?", (path,)).fetchone()
        return row[0] if row else -1

    def _index(self, db, n, identifier, batch):
        if batch.holds:
            missing = self._absent(db, batch.holds)
            if missing:
                raise Pending("Content of the records is not stored yet", missing=missing[0].hex())
            db.executemany("INSERT OR IGNORE INTO holds VALUES (?, ?)", [(d, n) for d in batch.holds])
        for path, target in batch.refs:
            row = self._row(db, target, deleted=True)
            if target == identifier:
                raise Invalid("An artifact cannot refer to itself")
            if not row[5]:
                raise Invalid(f"Only finished artifacts can be referred to; {target} is live")
            db.execute("INSERT OR IGNORE INTO refs VALUES (?, ?, ?)", (n, path, row[0]))
        path = self._path
        for key, (ordinal, nested) in batch.keys.items():
            k = path(db, key)
            old = db.execute("SELECT nested FROM keys WHERE artifact = ? AND key = ?", (n, k)).fetchone()
            if old and (old[0] or nested):
                # What the key held before this batch.
                db.execute("DELETE FROM latest WHERE artifact = ? AND key = ?", (n, k))
                gone = db.execute("SELECT n, path FROM files WHERE artifact = ? AND key = ?", (n, k)).fetchall()
                db.executemany("INSERT INTO file_text (file_text, rowid, path) VALUES ('delete', ?, ?)", gone)
                db.execute("DELETE FROM files WHERE artifact = ? AND key = ?", (n, k))
            elif old and key not in batch.latest:
                db.execute("DELETE FROM latest WHERE artifact = ? AND path = ?", (n, k))
            db.execute("INSERT INTO keys VALUES (?, ?, ?, ?) ON CONFLICT DO UPDATE SET ordinal = excluded.ordinal, nested = excluded.nested",
                       (n, k, ordinal, int(nested)))
        db.executemany("INSERT OR IGNORE INTO marks VALUES (?, ?, ?)", [(n, path(db, k), o) for k, o in batch.marks])
        db.executemany(
            "INSERT INTO latest VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (path, artifact) DO UPDATE SET "
            "kind = excluded.kind, num = excluded.num, text = excluded.text, "
            "min = CASE WHEN min IS NULL OR excluded.min < min THEN excluded.min ELSE min END, "
            "max = CASE WHEN max IS NULL OR excluded.max > max THEN excluded.max ELSE max END",
            [(path(db, p), n, path(db, r[0]), *r[1:]) for p, r in batch.latest.items()])
        if batch.files:
            start = db.execute("SELECT COALESCE(MAX(n), 0) FROM files").fetchone()[0]
            rows = [(start + i, n, p, path(db, r[0]), r[1], r[2]) for i, (p, r) in enumerate(batch.files.items(), 1)]
            db.executemany("INSERT INTO files VALUES (?, ?, ?, ?, ?, ?)", rows)
            db.executemany("INSERT INTO file_text (rowid, path) VALUES (?, ?)", [(r[0], r[2]) for r in rows])

    def _seal(self, db, n, force):
        """Turn the tail into a compressed segment and columns of its numbers."""
        count, size = db.execute("SELECT COALESCE(SUM(count), 0), COALESCE(SUM(LENGTH(data)), 0) FROM tail WHERE artifact = ?", (n,)).fetchone()
        if not count or not (force or count >= SEGMENT_RECORDS or size >= SEGMENT_BYTES):
            return
        rows = db.execute("SELECT first, lengths, data, sha FROM tail WHERE artifact = ? ORDER BY first", (n,)).fetchall()
        for _, _, data, sha in rows:
            if hashlib.sha256(data).digest() != sha:
                raise Corrupt("Stored records changed")
        first, lengths, data = rows[0][0], b"".join(r[1] for r in rows), b"".join(r[2] for r in rows)
        db.execute("INSERT INTO segments VALUES (?, ?, ?, ?, ?, ?)",
                   (n, first, count, lengths, zlib.compress(data, 1), hashlib.sha256(data).digest()))
        self._columns(db, n, first, lengths, data)
        db.execute("DELETE FROM tail WHERE artifact = ?", (n,))

    def _columns(self, db, n, first, lengths, data):
        batch = _Batch()
        for i, item in enumerate(_split(lengths, data)):
            batch.read(item, first + i)
        db.executemany("INSERT INTO columns VALUES (?, ?, ?, ?, ?)",
                       [(n, self._path(db, p), first, zlib.compress(array("I", (o - first for o in c[0])).tobytes(), 1), c[1].tobytes())
                        for p, c in batch.columns.items()])

    def leave(self, m):
        with self._transaction(write=True) as db:
            db.execute("UPDATE writers SET left = 1 WHERE id = ? AND artifact = ?", (m.writer, self._row(db, m.artifact)[0]))
        return pb.Empty()

    def finish(self, m):
        """Append a last record, seal, and assign the next version, all at once."""
        with self._transaction(write=True) as db:
            row = self._row(db, m.artifact)
            if row[5]:
                done = db.execute("SELECT sequence FROM writers WHERE id = ? AND artifact = ?", (m.writer, row[0])).fetchone()
                if (done and done[0] >= m.sequence) or m.force:
                    return self._message(db, row)
                raise Conflict("The artifact is finished")
            others = db.execute("SELECT COUNT(*) FROM writers WHERE artifact = ? AND left = 0 AND id <> ?", (row[0], m.writer)).fetchone()[0]
            if others and not m.force:
                raise Pending("Other processes are still writing", writers=others)
            self._append(db, row, m.writer, m.sequence, [m.record])
            db.execute("UPDATE writers SET left = 1 WHERE artifact = ?", (row[0],))
            self._seal(db, row[0], True)
            key, stamp = row[2:5], time.time()
            version = db.execute("SELECT next FROM names WHERE project=? AND namespace=? AND name=?", key).fetchone()[0]
            db.execute("UPDATE names SET next = next + 1 WHERE project=? AND namespace=? AND name=?", key)
            db.execute("UPDATE artifacts SET finished = 1, completed = ?, version = ?, ended = ? WHERE n = ?", (int(m.completed), version, stamp, row[0]))
            return self._message(db, self._row(db, m.artifact))

    def heartbeat(self, m):
        with self._transaction(write=True) as db:
            row = self._row(db, m.artifact)
            if not row[5]:
                db.execute("UPDATE artifacts SET heard = ? WHERE n = ?", (time.time(), row[0]))
            return self._message(db, row)

    # Names

    def _resolve(self, db, project, ref):
        if re.fullmatch(r"[0-9a-f]{32}", ref):
            return self._row(db, ref)
        if not (match := re.fullmatch(r"([^/@]+)/([^@]+)(?:@(.+))?", ref)):
            raise Invalid(f"References are namespace/name@selector or an ID, not {ref!r}")
        key, selector = (project, match[1], match[2]), match[3] or "latest"
        base = f"SELECT {self._COLUMNS} FROM artifacts WHERE project=? AND namespace=? AND name=? AND deleted=0 "
        if selector == "latest":
            rows = db.execute(base + "AND completed=1 ORDER BY version DESC LIMIT 1", key).fetchall()
        elif selector == "live":
            rows = db.execute(base + "AND finished=0 LIMIT 2", key).fetchall()
        elif re.fullmatch(r"v[1-9][0-9]*", selector):
            rows = db.execute(base + "AND version=?", (*key, int(selector[1:]))).fetchall()
        else:
            rows = db.execute(base + "AND n IN (SELECT artifact FROM labels WHERE key = ?)", (*key, "@" + selector)).fetchall()
        if not rows:
            if selector == "latest" and (other := db.execute(base + "AND finished=1 ORDER BY version DESC LIMIT 1", key).fetchone()):
                raise NotFound(f"No version of {match[1]}/{match[2]} completed; the newest is v{other[6]}")
            raise NotFound(f"Nothing is called {ref}")
        if len(rows) > 1:
            raise Conflict(f"Several artifacts are live under {ref}; use an ID")
        return rows[0]

    def resolve(self, m):
        with self._transaction() as db:
            return pb.Artifacts(items=[self._message(db, self._resolve(db, m.project, ref)) for ref in m.refs])

    def label(self, m):
        """Set and remove labels of an artifact. An alias moves here from other versions."""
        with self._transaction(write=True) as db:
            row = self._row(db, m.artifact)
            for key in m.remove:
                db.execute("DELETE FROM labels WHERE artifact = ? AND key = ?", (row[0], key))
            for entry in m.set.entries:
                value = decode(entry.value)
                if not KEY.fullmatch(entry.key):
                    raise Invalid(f"Not a tag or alias: {entry.key!r}")
                if not isinstance(value, (bool, int, float, str, type(None))) or (isinstance(value, float) and not math.isfinite(value)):
                    raise Invalid("Tags are text, numbers or booleans, or have no value")
                if isinstance(value, str) and len(value) > TEXT:
                    raise Invalid(f"Tags have at most {TEXT} characters")
                if entry.key.startswith("@"):
                    self._alias(db, row, entry.key, value, m)
                kind = NONE if value is None else FLAG if isinstance(value, bool) else INT if isinstance(value, int) else FLOAT if isinstance(value, float) else STRING
                text = value if kind == STRING else None
                db.execute("INSERT OR REPLACE INTO labels VALUES (?, ?, ?, ?, ?)", (row[0], entry.key, kind, None if kind in (NONE, STRING) else value, text))
            return self._message(db, row)

    def _alias(self, db, row, key, value, m):
        if value is not None:
            raise Invalid("Aliases have no value")
        if not ALIAS.fullmatch(key) or key in ("@latest", "@live") or re.fullmatch(r"@v\d+", key):
            raise Invalid(f"Not an alias: {key[1:]}")
        if not row[5]:
            raise Invalid("Aliases name finished artifacts")
        others = db.execute("SELECT a.id, a.n FROM labels l JOIN artifacts a ON a.n = l.artifact WHERE l.key=? AND a.project=? AND a.namespace=? AND a.name=?", (key, *row[2:5])).fetchall()
        current = others[0][0] if others else ""
        if m.compare and current != m.expected:
            raise Conflict("The alias was moved", current=current)
        db.executemany("DELETE FROM labels WHERE artifact = ? AND key = ?", [(n, key) for _, n in others])

    def aliases(self, m):
        with self._transaction() as db:
            columns = ", ".join("a." + c for c in self._COLUMNS.split(", "))
            rows = db.execute(f"SELECT DISTINCT {columns} FROM artifacts a JOIN labels l ON l.artifact = a.n WHERE a.project=? AND a.namespace=? AND a.name=? AND a.deleted=0 AND l.key LIKE '@%' ORDER BY a.version", (m.project, m.namespace, m.name)).fetchall()
            return pb.Artifacts(items=[self._message(db, row) for row in rows], total=len(rows))

    def delete(self, m):
        """Hide an artifact, or show it again. Artifacts that refer to it keep it readable,
        and `collect` frees the rest for good."""
        with self._transaction(write=True) as db:
            row = self._row(db, m.artifact, deleted=True)
            if not m.restore:
                db.execute("DELETE FROM labels WHERE artifact = ? AND key LIKE '@%'", (row[0],))
            db.execute("UPDATE artifacts SET deleted = ? WHERE n = ?", (int(not m.restore), row[0]))
            return self._message(db, row)

    def projects(self, m):
        answer = pb.ProjectList()
        with self._transaction() as db:
            rows = db.execute("SELECT project, namespace, COUNT(*), MAX(MAX(created, COALESCE(ended, 0))) FROM artifacts WHERE deleted = 0 GROUP BY project, namespace ORDER BY project")
            for project, namespace, count, updated in rows:
                if not answer.items or answer.items[-1].name != project:
                    answer.items.add(name=project)
                answer.items[-1].artifacts[namespace] = count
                answer.items[-1].updated = max(answer.items[-1].updated, updated)
        return answer

    # Reading one artifact

    def records(self, m):
        limit = min(m.limit or 1000, 10_000)
        page = pb.RecordPage()
        with self._transaction() as db:
            row = self._row(db, m.artifact, deleted=True)
            page.total, wanted = row[7], None
            if m.having:
                wanted = self._having(db, row, m.having, m.after, limit + 1)
                page.more, wanted = len(wanted) > limit, wanted[:limit]
                for ordinal, data in self._pick(db, row[0], wanted):
                    page.ordinals.append(ordinal)
                    page.records.append(data)
                return page
            size = 0
            for ordinal, data in self._scan(db, row[0], m.after):
                if len(page.records) >= limit or (size and size + len(data) > PAGE):
                    page.more = True
                    break
                page.ordinals.append(ordinal)
                page.records.append(data)
                size += len(data)
        return page

    def _blocks(self, db, n, after):
        """Segments, then the tail, from the block holding the record after an ordinal."""
        start = db.execute("SELECT MAX(first) FROM segments WHERE artifact = ? AND first <= ?", (n, after + 1)).fetchone()[0] or 0
        for first, lengths, data, sha in db.execute("SELECT first, lengths, data, sha FROM segments WHERE artifact = ? AND first >= ? AND first + count > ? ORDER BY first", (n, start, after + 1)):
            data = zlib.decompress(data)
            if hashlib.sha256(data).digest() != sha:
                raise Corrupt("Stored records changed")
            yield first, lengths, data
        yield from db.execute("SELECT first, lengths, data FROM tail WHERE artifact = ? AND first + count > ? ORDER BY first", (n, after + 1))

    def _scan(self, db, n, after):
        for first, lengths, data in self._blocks(db, n, after):
            for i, item in enumerate(_split(lengths, data)):
                if first + i > after:
                    yield first + i, item

    def _pick(self, db, n, ordinals):
        block, items = None, []
        for ordinal in ordinals:
            if block is None or not block <= ordinal < block + len(items):
                block, lengths, data = next(iter(self._blocks(db, n, ordinal - 1)))
                items = _split(lengths, data)
            yield ordinal, items[ordinal - block]

    def _having(self, db, row, key, after, limit):
        k = self._known(db, key)
        found = {r[0] for r in db.execute("SELECT ordinal FROM marks WHERE artifact=? AND key=? AND ordinal>? ORDER BY ordinal LIMIT ?", (row[0], k, after, limit))}
        found.update(self._column(db, row, key, after)[0])
        for first, lengths, data in db.execute("SELECT first, lengths, data FROM tail WHERE artifact=? AND first+count>?", (row[0], after + 1)):
            for i, item in enumerate(_split(lengths, data)):
                if first + i > after and any(e.key == key for e in pb.Map.FromString(item).entries):
                    found.add(first + i)
        return sorted(found)[:limit]

    def _column(self, db, row, path, after):
        n, p, ordinals, values = row[0], self._known(db, path), array("Q"), array("d")
        start = db.execute("SELECT MAX(first) FROM columns WHERE artifact=? AND path=? AND first<=?", (n, p, after + 1)).fetchone()[0] or 0
        for first, packed, data in db.execute("SELECT first, ordinals, vals FROM columns WHERE artifact=? AND path=? AND first>=? ORDER BY first", (n, p, start)):
            offsets, numbers = array("I", zlib.decompress(packed)), array("d", data)
            if first + offsets[0] > after:
                ordinals.extend(array("Q", (first + o for o in offsets)))
                values.extend(numbers)
            else:
                for offset, number in zip(offsets, numbers, strict=True):
                    if first + offset > after:
                        ordinals.append(first + offset)
                        values.append(number)
        return ordinals, values

    def series(self, m):
        with self._transaction() as db:
            row = self._row(db, m.artifact, deleted=True)
            answer = pb.Columns(total=row[7])
            tail = _Batch()
            for first, lengths, data in db.execute("SELECT first, lengths, data FROM tail WHERE artifact=? AND first+count>? ORDER BY first", (row[0], m.after + 1)):
                for i, item in enumerate(_split(lengths, data)):
                    if first + i > m.after:
                        tail.read(item, first + i)
            for path in m.paths:
                ordinals, values = self._column(db, row, path, m.after)
                if path in tail.columns:
                    ordinals.extend(tail.columns[path][0])
                    values.extend(tail.columns[path][1])
                column = answer.columns.add(path=path)
                column.ordinals.extend(ordinals)
                column.values.extend(values)
        return answer

    def keys(self, m):
        with self._transaction() as db:
            row = self._row(db, m.artifact, deleted=True)
            return pb.KeyList(keys=[r[0] for r in db.execute("SELECT p.path FROM keys k JOIN paths p ON p.id = k.key WHERE k.artifact = ? ORDER BY p.path", (row[0],))])

    def last(self, m):
        """The current value of each key: the one in the last record that has it."""
        answer = pb.LastValues()
        with self._transaction() as db:
            row = self._row(db, m.artifact, deleted=True)
            places = {}
            for key in m.keys:
                found = db.execute("SELECT ordinal FROM keys WHERE artifact=? AND key=?", (row[0], self._known(db, key))).fetchone()
                if found:
                    places.setdefault(found[0], []).append(key)
            for ordinal, data in self._pick(db, row[0], sorted(places)):
                for entry in pb.Map.FromString(data).entries:
                    if entry.key in places[ordinal]:
                        answer.entries.add().CopyFrom(entry)
        return answer

    # Finding

    def _condition(self, db, condition):
        op, (table, owner, key) = condition.op, self._place_of(db, condition.path)
        base, args = f"a.n IN (SELECT artifact FROM {table} WHERE {owner} = ?", [key]
        if not op:
            if table == "labels":
                return base + ")", args
            return (base + " UNION ALL SELECT artifact FROM keys WHERE key = ? UNION ALL SELECT artifact FROM files WHERE path = ?)"), [key, key, condition.path]
        value, column = decode(condition.value), condition.stat or "num"
        if column not in ("num", "min", "max") or (condition.stat and table == "labels"):
            raise Invalid(f"No {column} of {condition.path}")
        if op == "contains":
            if not isinstance(value, str) or condition.stat:
                raise Invalid("contains looks for text")
            return base + f" AND kind = {STRING} AND INSTR(text, ?))", [*args, value]
        if op not in OPERATORS:
            raise Invalid(f"Unknown operator: {op}")
        if isinstance(value, bool):
            kinds, value = (FLAG,), int(value)
        elif isinstance(value, (int, float)):
            if value != value:
                return base + f" AND kind = {FLOAT} AND text = 'nan')", args
            kinds = (INT, FLOAT)
        elif isinstance(value, str) and not condition.stat:
            kinds, column = (STRING, REF), "text"
        else:
            raise Invalid("Conditions compare numbers, booleans and text")
        return base + f" AND {column} {OPERATORS[op]} ? AND kind IN ({', '.join(map(str, kinds))}))", [*args, value]

    def _place_of(self, db, path):
        """The table, and its key, that answers for a path."""
        if path.startswith("tag:"):
            return "labels", "key", path[4:]
        if path.startswith("alias:"):
            return "labels", "key", "@" + path[6:]
        return "latest", "path", self._known(db, path)

    def find(self, m):
        limit = min(m.limit or 100, 1000)
        with self._transaction() as db:
            where, args = [f"a.deleted = {int(m.state == 'deleted')}"], []
            for column, values in (("project", [m.project] if m.project else []), ("namespace", m.namespaces)):
                if values:
                    where.append(f"a.{column} IN ({', '.join('?' * len(values))})")
                    args += values
            if m.state:
                states = {"live": "a.finished = 0", "finished": "a.finished = 1", "completed": "a.completed = 1", "deleted": "1"}
                if m.state not in states:
                    raise Invalid(f"state is one of {', '.join(states)}")
                where.append(states[m.state])
            if m.name:
                if prefix := _prefix(m.name):
                    where.append("a.name >= ? AND a.name < ?")
                    args += [prefix, _after(prefix)]
                if prefix != m.name:
                    expression = _glob(m.name)
                    db.create_function("cairn_glob", 1, lambda s: expression.match(s) is not None, deterministic=True)
                    where.append("cairn_glob(a.name)")
                else:
                    where[-1], args = "a.name = ?", [*args[:-2], m.name]
            if m.text:
                where.append("INSTR(LOWER(a.name), LOWER(?))")
                args.append(m.text)
            if m.latest:
                where.append("a.version = (SELECT MAX(version) FROM artifacts b WHERE b.project = a.project AND b.namespace = a.namespace AND b.name = a.name AND b.deleted = 0 AND b.completed = 1)")
            for condition in m.where:
                sql, values = self._condition(db, condition)
                where.append(sql)
                args += values
            clause = " AND ".join(where)
            total = db.execute(f"SELECT COUNT(*) FROM artifacts a WHERE {clause}", args).fetchone()[0]
            direction = "DESC" if m.descending else "ASC"
            if m.order:
                match = re.fullmatch(r"(min|max)\((.+)\)", m.order)
                column, path = (match[1], match[2]) if match else ("num", m.order)
                table, owner, key = self._place_of(db, path)
                if match and table == "labels":
                    raise Invalid("Tags have one value")
                join = f"LEFT JOIN {table} o ON o.{owner} = ? AND o.artifact = a.n"
                order = f"o.{column} IS NULL, o.{column} {direction}, " + ("" if match else f"o.text {direction}, ") + "a.n DESC"
                args = [key, *args]
            else:
                join, order = "", "a.n " + ("ASC" if m.descending else "DESC")
            columns = ", ".join("a." + c for c in self._COLUMNS.split(", "))
            rows = db.execute(f"SELECT {columns} FROM artifacts a {join} WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?", [*args, limit, m.offset]).fetchall()
            answer = pb.Artifacts(total=total)
            for row in rows:
                item = answer.items.add()
                item.CopyFrom(self._message(db, row))
                for path in m.values:
                    self._value(db, row[0], path, item.values)
        return answer

    def _value(self, db, n, path, target):
        match = re.fullmatch(r"(min|max)\((.+)\)", path)
        found = db.execute("SELECT kind, num, text, min, max FROM latest WHERE path = ? AND artifact = ?", (self._known(db, match[2] if match else path), n)).fetchone()
        if not found:
            return
        kind, num, text, low, high = found
        value = (low if match[1] == "min" else high) if match else _stored(kind, num, text)
        entry = target.entries.add(key=path)
        encode(value, entry.value)

    def files(self, m):
        """Current files, of one artifact or of all, by exact path, glob, or part of the path.
        Those below keys that start with _ are found only by a pattern that does too."""
        limit = min(m.limit or 1000, 10_000)
        answer = pb.FoundFiles()
        with self._transaction() as db:
            where, args, source = ["a.deleted = 0"], [], "files f JOIN artifacts a ON a.n = f.artifact"
            if not m.pattern.startswith("_"):
                where.append("(f.path < '_' OR f.path >= '`')")
            expression = None
            literals = [m.text]
            if m.text:
                # The index ignores case and knows only trigrams; this is the exact answer.
                where.append("INSTR(LOWER(f.path), LOWER(?))")
                args.append(m.text)
            if m.artifact:
                row = self._row(db, m.artifact, deleted=True)
                where[0] = "f.artifact = ?"
                args.insert(0, row[0])
            elif m.project:
                where.append("a.project = ?")
                args.append(m.project)
            if m.pattern:
                prefix = _prefix(m.pattern)
                if prefix == m.pattern:
                    where.append("f.path = ?")
                    args.append(m.pattern)
                else:
                    expression = _glob(m.pattern)
                    if prefix:
                        where.append("f.path >= ? AND f.path < ?")
                        args += [prefix, _after(prefix)]
                    else:
                        literals += re.split(r"[*?]", m.pattern)
            literal = max(literals, key=len)
            if len(literal) >= 3:
                terms = _trigrams(literal)
                found = db.execute("SELECT COUNT(*) FROM (SELECT 1 FROM file_text WHERE file_text MATCH ? LIMIT 20001)", (terms,)).fetchone()[0]
                # In one artifact, common text is found sooner by walking its files in order.
                if not m.artifact or found <= 20000:
                    source = TEXT_SOURCE
                    where.append("file_text MATCH ?")
                    args.append(terms)
            if m.after:
                where.append("f.path > ?")
                args.append(m.after)
            order = "f.path" if m.artifact else "file_text.rowid DESC" if source.startswith("file_text") else "f.n DESC"
            rows = db.execute(f"SELECT a.id, f.path, f.blob, f.ordinal, a.namespace, a.name, a.version FROM {source} WHERE {' AND '.join(where)} ORDER BY {order}"
                              + ("" if expression else f" LIMIT {limit + 1}"), args)
            for row in rows:
                if expression and not expression.match(row[1]):
                    continue
                if len(answer.items) >= limit:
                    answer.more = True
                    break
                name = f"{row[4]}/{row[5]}" + (f"@v{row[6]}" if row[6] else "@live")
                answer.items.add(artifact=row[0], path=row[1], blob=pb.Blob.FromString(row[2]), ordinal=row[3], reference=f"{name}#{row[1]}")
        return answer

    def links(self, m):
        answer = pb.LinkList()
        with self._transaction() as db:
            n = self._row(db, m.artifact, deleted=True)[0]
            mine, other = ("target", "artifact") if m.reverse else ("artifact", "target")
            for path, *row in db.execute(f"SELECT r.path, {', '.join('a.' + c for c in self._COLUMNS.split(', '))} FROM refs r JOIN artifacts a ON a.n = r.{other} WHERE r.{mine} = ? ORDER BY a.n", (n,)):
                answer.items.add(path=path, artifact=self._message(db, tuple(row)))
        return answer

    # Content

    def _file(self, digest):
        name = digest.hex()
        return self.path / "blobs" / name[:2] / name

    def _absent(self, db, digests):
        return [d for d in digests if not db.execute("SELECT 1 FROM blobs WHERE digest = ?", (d,)).fetchone()]

    def have(self, m):
        with self._transaction(write=True) as db:
            # What a client is about to log must survive a collection until it does.
            db.executemany("UPDATE blobs SET created = ? WHERE digest = ?", [(time.time(), d) for d in m.digests])
            absent = self._absent(db, m.digests)
        return pb.Missing(digests=absent, received=[self._received(d) for d in absent])

    def _part(self, digest):
        return self._file(digest).with_suffix(".part")

    def _received(self, digest):
        with contextlib.suppress(FileNotFoundError):
            return self._part(digest).stat().st_size
        return 0

    def put_part(self, digest, stream, offset, size):
        """Add a piece to content that is stored under its digest once it is complete.
        Every piece, also one that broke off, stays until then."""
        target, part = self._file(digest), self._part(digest)
        target.parent.mkdir(exist_ok=True)
        with open(part, "ab+") as out:
            fcntl.flock(out, fcntl.LOCK_EX)
            if not self._absent(self._db(), [digest]):
                part.unlink(missing_ok=True)
                return
            # Another writer may have removed or finished this file while this one waited.
            if not part.exists() or os.fstat(out.fileno()).st_ino != part.stat().st_ino or (received := out.seek(0, os.SEEK_END)) != offset:
                raise Conflict("The content continues elsewhere", received=self._received(digest))
            known, sha = self._uploads.pop(digest, (0, None))
            if known != offset:
                out.seek(0)
                sha = hashlib.file_digest(out, "sha256")
            sha = sha or hashlib.sha256()
            while chunk := stream.read(1 << 20):
                received += len(chunk)
                if received > size:
                    part.unlink()
                    raise Invalid("The content is longer than announced")
                out.write(chunk)
                sha.update(chunk)
            out.flush()
            if received < size:
                self._uploads[digest] = (received, sha)
                return
            if sha.digest() != digest:
                part.unlink()
                raise Invalid("The content does not match its digest")
            os.fsync(out.fileno())
            with self._transaction(write=True) as db:
                os.replace(part, target)
                self._place(db, digest, size, "", None)

    def put(self, digest, stream):
        """Store content under its digest, refusing anything else."""
        target, sha, size = self._file(digest), hashlib.sha256(), 0
        target.parent.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as out:
            try:
                while chunk := stream.read(1 << 20):
                    sha.update(chunk)
                    size += len(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
                if sha.digest() != digest:
                    raise Invalid("The content does not match its digest")
                with self._transaction(write=True) as db:
                    os.replace(out.name, target)
                    self._place(db, digest, size, "", None)
            except BaseException:
                Path(out.name).unlink(missing_ok=True)
                raise
        return size

    def _place(self, db, digest, size, place, stamp):
        db.execute("INSERT INTO blobs VALUES (?, ?, ?) ON CONFLICT DO UPDATE SET created = excluded.created", (digest, size, time.time()))
        db.execute("INSERT OR REPLACE INTO places VALUES (?, ?, ?)", (digest, place, stamp))

    def link_blob(self, m):
        """Register a file as content where it lies, if it has the digest the client saw."""
        path = Path(m.path)
        if not path.is_absolute() or not path.is_file():
            raise NotFound(f"The repository cannot read {m.path}")
        path = path.resolve()
        if self.link_roots is not None and not any(path.is_relative_to(r) for r in self.link_roots):
            raise Invalid(f"The repository does not link files in this folder: {m.path}")
        before = path.stat()
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").digest()
        after = path.stat()
        if digest != m.digest or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise Invalid("The repository sees another file at this path")
        with self._transaction(write=True) as db:
            self._place(db, digest, after.st_size, str(path), after.st_mtime_ns)
        return pb.Empty()

    def locate(self, digest):
        """A file holding the content: the stored copy, or an intact link."""
        with self._transaction() as db:
            places = db.execute("SELECT p.place, p.stamp, b.size FROM places p JOIN blobs b ON b.digest = p.digest WHERE p.digest = ? ORDER BY p.place", (digest,)).fetchall()
        for place, stamp, size in places:
            path = Path(place) if place else self._file(digest)
            with contextlib.suppress(OSError):
                found = path.stat()
                if found.st_size == size and (not place or found.st_mtime_ns == stamp):
                    return path
        raise NotFound("The content is missing, or its linked file changed" if places else "Unknown content")

    # Keeping

    def rebuild(self):
        """Derive every index from the records again."""
        with self._transaction(write=True) as db:
            for table in INDEXES:
                db.execute(f"DELETE FROM {table}")
            db.execute("INSERT INTO file_text (file_text) VALUES ('delete-all')")
            for n, identifier in db.execute("SELECT n, id FROM artifacts").fetchall():
                for first, lengths, data in list(self._blocks(db, n, 0)):
                    batch = _Batch()
                    for i, item in enumerate(_split(lengths, data)):
                        batch.read(item, first + i)
                    self._index(db, n, identifier, batch)
                for first, lengths, data in db.execute("SELECT first, lengths, data FROM segments WHERE artifact = ?", (n,)).fetchall():
                    self._columns(db, n, first, lengths, zlib.decompress(data))

    def check(self, *, content=False):
        """Problems found: counts, record hashes, missing content and references."""
        problems = []
        with self._transaction() as db:
            for n, identifier, total in db.execute("SELECT n, id, records FROM artifacts").fetchall():
                try:
                    count = sum(len(array("I", lengths)) for _, lengths, _ in self._blocks(db, n, 0))
                    if count != total:
                        problems.append(f"{identifier}: {count} of {total} records")
                    for data, sha in db.execute("SELECT data, sha FROM tail WHERE artifact = ?", (n,)):
                        if hashlib.sha256(data).digest() != sha:
                            problems.append(f"{identifier}: records changed")
                except Exception as error:  # noqa: BLE001 - report and go on
                    problems.append(f"{identifier}: {error}")
            held = [r[0] for r in db.execute("SELECT DISTINCT digest FROM holds")]
        for digest in held:
            try:
                path = self.locate(digest)
                if content:
                    with path.open("rb") as stream:
                        if hashlib.file_digest(stream, "sha256").digest() != digest:
                            problems.append(f"content {digest.hex()} changed")
            except NotFound as error:
                problems.append(f"content {digest.hex()}: {error}")
        return problems

    def collect(self, *, grace=3600):
        """Free deleted artifacts nothing refers to, and content nothing holds."""
        with self._transaction(write=True) as db:
            while True:
                gone = [r[0] for r in db.execute("SELECT n FROM artifacts WHERE deleted = 1 AND n NOT IN (SELECT target FROM refs)")]
                if not gone:
                    break
                for n in gone:
                    files = db.execute("SELECT n, path FROM files WHERE artifact = ?", (n,)).fetchall()
                    db.executemany("INSERT INTO file_text (file_text, rowid, path) VALUES ('delete', ?, ?)", files)
                    for table in (*INDEXES, "tail", "segments", "writers", "labels"):
                        db.execute(f"DELETE FROM {table} WHERE artifact = ?", (n,))
                    db.execute("DELETE FROM artifacts WHERE n = ?", (n,))
            free = [r[0] for r in db.execute("SELECT digest FROM blobs b WHERE created < ? AND NOT EXISTS (SELECT 1 FROM holds h WHERE h.digest = b.digest)", (time.time() - grace,))]
            for digest in free:
                db.execute("DELETE FROM blobs WHERE digest = ?", (digest,))
                db.execute("DELETE FROM places WHERE digest = ?", (digest,))
                self._file(digest).unlink(missing_ok=True)
            for part in (self.path / "blobs").glob("*/*.part"):
                if part.stat().st_mtime < time.time() - max(grace, 86400):
                    part.unlink(missing_ok=True)
        return len(free)


def _split(lengths, data):
    items, start = [], 0
    for length in array("I", lengths):
        items.append(data[start : start + length])
        start += length
    return items
