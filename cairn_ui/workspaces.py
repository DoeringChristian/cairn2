"""The workspaces people and agents arrange, by project and name."""

import contextlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from cairn_next import Conflict, NotFound

from .layouts import validate_layout

TABLE = """CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, project TEXT NOT NULL, name TEXT NOT NULL,
  revision INTEGER NOT NULL, layout TEXT NOT NULL, updated REAL NOT NULL, UNIQUE (project, name))"""


class Workspaces:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, timeout=60, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(TABLE)

    def _row(self, where, args):
        with self._lock:
            row = self._db.execute(f"SELECT id, project, name, revision, updated, layout FROM workspaces WHERE {where}", args).fetchone()
        if row is None:
            raise NotFound("Unknown workspace")
        return dict(zip(("id", "project", "name", "revision", "updated"), row, strict=False), layout=json.loads(row[5]))

    def list(self, project):
        with self._lock:
            rows = self._db.execute("SELECT id, name, revision, updated FROM workspaces WHERE project = ? ORDER BY name", (project,)).fetchall()
        return [dict(zip(("id", "name", "revision", "updated"), row, strict=True)) for row in rows]

    def get(self, identifier):
        return self._row("id = ?", (identifier,))

    def named(self, project, name):
        return self._row("project = ? AND name = ?", (project, name))

    def write(self, project, name, layout, expected=None):
        """Replace a workspace, only if it still is at the expected revision (0: not there)."""
        validate_layout(layout, name)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT revision FROM workspaces WHERE project = ? AND name = ?", (project, name)).fetchone()
                revision = row[0] if row else 0
                if expected is not None and expected != revision:
                    raise Conflict("The workspace was changed", current=revision)
                self._db.execute("INSERT INTO workspaces VALUES (?, ?, ?, 1, ?, ?) ON CONFLICT (project, name) DO UPDATE SET "
                                 "revision = revision + 1, layout = excluded.layout, updated = excluded.updated",
                                 (uuid.uuid4().hex, project, name, json.dumps(layout), time.time()))
                self._db.execute("COMMIT")
            except BaseException:
                with contextlib.suppress(sqlite3.Error):
                    self._db.execute("ROLLBACK")
                raise
        return self.named(project, name)

    def delete(self, project, name):
        self.named(project, name)
        with self._lock:
            self._db.execute("DELETE FROM workspaces WHERE project = ? AND name = ?", (project, name))

    def close(self):
        self._db.close()
