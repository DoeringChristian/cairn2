"""Reports: Markdown files that people and agents edit at the same time.

The file is what counts. While a report is open its text is shared (Yjs), changes are written
to the file a moment later, and what other programs write to the file is merged in. The shared
state is kept beside it, so browsers continue where they were when the server comes back."""

import asyncio
import contextlib
import difflib
import os
import re
import sqlite3
import uuid
from pathlib import Path
from urllib.parse import quote, unquote

from pycrdt import (
    Doc,
    Text,
    YMessageType,
    create_sync_message,
    create_update_message,
    handle_sync_message,
)

from cairn import Conflict, Invalid, NotFound

SAVE, WATCH = 0.5, 1.0
LIMIT = 8 * 1024 * 1024
TABLE = "CREATE TABLE IF NOT EXISTS texts (project TEXT, name TEXT, epoch TEXT NOT NULL, state BLOB NOT NULL, PRIMARY KEY (project, name))"


def _hunks(old, new):
    """Changed lines, as (from, to, text) of the old text."""
    a, b = old.splitlines(True), new.splitlines(True)
    starts = [0]
    for line in a:
        starts.append(starts[-1] + len(line))
    return [(starts[i], starts[j], "".join(b[k:m])) for tag, i, j, k, m in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if tag != "equal"]


def merge(base, ours, theirs):
    """Our text with what turned the base into theirs; where both changed the same lines, both stay."""
    mine, edits = _hunks(base, ours), []
    for start, end, text in _hunks(base, theirs):
        shift, touched = 0, None
        for first, last, own in mine:
            if (first < end and start < last) or (first == start and (first, last, own) != (start, end, text)):
                touched = (first, last, own)
            if last <= start and first < start:
                shift += len(own) - (last - first)
        if (start, end, text) in mine:
            continue
        if touched:
            # Their lines follow ours instead of replacing them.
            first, last, own = touched
            at = first + shift + len(own)
            edits.append((at, at, text))
        else:
            edits.append((start + shift, end + shift, text))
    for start, end, text in sorted(edits, reverse=True):
        ours = ours[:start] + text + ours[end:]
    return ours


class Live:
    """A report that is open: its shared text, and who is there."""

    def __init__(self, reports, project, name):
        self.reports, self.project, self.name = reports, project, name
        self.path = reports.path(project, name)
        self.people, self.presence = {}, {}
        self._saving = None
        row = reports.state(project, name)
        self.epoch = row[0] if row else uuid.uuid4().hex
        self.doc = Doc()
        if row:
            self.doc.apply_update(row[1])
        self.text = self.doc.get("text", type=Text)
        self.disk, self._stamp = self.read()
        self.replace(self.disk)
        self._updates = self.doc.observe(self._changed)

    def read(self):
        data = self.path.read_bytes()
        return data.decode(errors="replace").replace("\r\n", "\n"), self.path.stat().st_mtime_ns

    def replace(self, new):
        """Turn the text into another one, touching only what differs."""
        old = str(self.text)
        with self.doc.transaction():
            for start, end, text in reversed(_hunks(old, new)):
                # Within changed lines, only the changed middle.
                was = old[start:end]
                front = len(os.path.commonprefix([was, text]))
                back = len(os.path.commonprefix([was[front:][::-1], text[front:][::-1]]))
                self.splice(old, start + front, end - back, text[front : len(text) - back])

    def splice(self, old, start, end, text):
        # The shared text counts bytes.
        first = len(old[:start].encode())
        last = first + len(old[start:end].encode())
        if last > first:
            del self.text[first:last]
        if text:
            self.text.insert(first, text)

    def _changed(self, event):
        message = create_update_message(event.update)
        for queue in self.people.values():
            queue.put_nowait(message)
        if self._saving is None:
            self._saving = asyncio.get_running_loop().call_later(SAVE, self.save)

    def save(self):
        if self._saving:
            self._saving.cancel()
            self._saving = None
        if not self.path.exists():
            return  # deleted meanwhile
        self.look()
        text = str(self.text)
        if text != self.disk:
            part = self.path.with_name(f".{uuid.uuid4().hex}")
            part.write_text(text, newline="\n")
            os.replace(part, self.path)
            self.disk, self._stamp = text, self.path.stat().st_mtime_ns
        self.reports.keep(self)

    def look(self):
        """Merge what another program wrote to the file."""
        with contextlib.suppress(FileNotFoundError):
            if self.path.stat().st_mtime_ns != self._stamp:
                found, self._stamp = self.read()
                if found != self.disk:
                    base, self.disk = self.disk, found
                    self.replace(merge(base, str(self.text), found))

    async def join(self, socket):
        """Keep one browser and the text in step, until it leaves."""
        queue = asyncio.Queue()
        self.people[socket] = queue

        async def send():
            while True:
                await socket.send_bytes(await queue.get())

        sending = asyncio.ensure_future(send())
        try:
            queue.put_nowait(create_sync_message(self.doc))
            for message in self.presence.values():
                queue.put_nowait(message)
            async for data in socket.iter_bytes():
                if not data:
                    continue
                if data[0] == YMessageType.SYNC:
                    reply = handle_sync_message(data[1:], self.doc)
                    if reply:
                        queue.put_nowait(reply)
                    if len(self.text) > LIMIT:
                        break
                elif data[0] == YMessageType.AWARENESS:
                    self.presence[socket] = data
                    for other, waiting in self.people.items():
                        if other is not socket:
                            waiting.put_nowait(data)
        finally:
            sending.cancel()
            self.people.pop(socket, None)
            self.presence.pop(socket, None)


class Reports:
    def __init__(self, folder, database):
        self.folder = Path(folder)
        self._db = sqlite3.connect(database, timeout=60, isolation_level=None, check_same_thread=False)
        self._db.execute(TABLE)
        self._open, self._watching = {}, None

    def path(self, project, name):
        parts = name.split("/")
        if not project or any(not part or part.startswith(".") or re.search(r"[\x00-\x1f\\:*?\"<>|]", part) or len(part) > 100 for part in parts):
            raise Invalid(f"Not a report's name: {name!r}")
        return self.folder / quote(project, safe=" ") / f"{name}.md"

    def list(self, project):
        root = self.folder / quote(project, safe=" ")
        found = [(path, path.stat().st_mtime) for path in root.rglob("*.md") if not any(part.startswith(".") for part in path.relative_to(root).parts)] if project else []
        return [{"name": path.relative_to(root).as_posix().removesuffix(".md"), "updated": stamp} for path, stamp in sorted(found, key=lambda item: -item[1])]

    def counts(self):
        return {unquote(folder.name): len(self.list(unquote(folder.name))) for folder in self.folder.iterdir() if folder.is_dir()} if self.folder.is_dir() else {}

    def state(self, project, name):
        return self._db.execute("SELECT epoch, state FROM texts WHERE project = ? AND name = ?", (project, name)).fetchone()

    def keep(self, live):
        self._db.execute("INSERT OR REPLACE INTO texts VALUES (?, ?, ?, ?)", (live.project, live.name, live.epoch, live.doc.get_update()))

    def live(self, project, name):
        if not self.path(project, name).is_file():
            self._open.pop((project, name), None)
            raise NotFound(f"No report {name} in {project}")
        if (project, name) not in self._open:
            self._open[project, name] = Live(self, project, name)
            self.keep(self._open[project, name])
            if self._watching is None:
                self._watching = asyncio.ensure_future(self._watch())
        live = self._open[project, name]
        live.look()
        return live

    async def _watch(self):
        while True:
            await asyncio.sleep(WATCH)
            for key, live in list(self._open.items()):
                with contextlib.suppress(OSError):
                    live.look()
                if not live.people:
                    with contextlib.suppress(OSError):
                        live.save()
                    self._open.pop(key)

    def get(self, project, name):
        live = self.live(project, name)
        return {"name": name, "text": str(live.text), "epoch": live.epoch}

    def create(self, project, name, text=""):
        path = self.path(project, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "x", newline="\n") as out:
                out.write(text)
        except FileExistsError:
            raise Conflict(f"There is a report {name} already") from None
        self._db.execute("DELETE FROM texts WHERE project = ? AND name = ?", (project, name))
        return self.get(project, name)

    def write(self, project, name, text, base=None):
        """The whole text. Merged with what changed since `base`, when that is given."""
        if len(text) > LIMIT:
            raise Invalid("A report holds at most 8 MiB")
        if not self.path(project, name).is_file():
            return self.create(project, name, text)
        live = self.live(project, name)
        live.replace(text if base is None else merge(base, str(live.text), text))
        live.save()
        return self.get(project, name)

    def edit(self, project, name, old, new):
        """Replace the one passage that reads `old`."""
        live = self.live(project, name)
        text = str(live.text)
        if not old or text.count(old) != 1:
            raise Conflict(f"The passage is there {text.count(old) if old else 0} times, not once")
        at = text.index(old)
        with live.doc.transaction():
            live.splice(text, at, at + len(old), new)
        live.save()
        return self.get(project, name)

    def delete(self, project, name):
        path = self.path(project, name)
        if not path.is_file():
            raise NotFound(f"No report {name} in {project}")
        path.unlink()
        self._open.pop((project, name), None)
        self._db.execute("DELETE FROM texts WHERE project = ? AND name = ?", (project, name))
        root = self.folder / quote(project, safe=" ")
        for folder in path.parents:
            if folder == root or any(folder.iterdir()):
                break
            folder.rmdir()

    def close(self):
        if self._watching:
            self._watching.cancel()
        for live in self._open.values():
            with contextlib.suppress(OSError):
                live.save()
        self._open.clear()
        self._db.close()
