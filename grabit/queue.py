"""A persistent download queue / manager.

The queue owns a list of items (each an id + url + dest + segment count + an
optional SHA-256 + a state) and knows how to add, remove, reorder and run them
with a concurrency limit.  It persists to a JSON file in the GrabIt config dir
and, on reload, demotes anything that was mid-flight back to ``queued`` so a
restart resumes cleanly (single-stream items pick up from their ``.part``).
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid

from . import guiconfig
from .download import (
    CANCELLED, DONE, ERROR, PAUSED, QUEUED, RUNNING, Download,
)
from .errors import GrabItError

QUEUE_NAME = "queue.json"
# States that are "not yet finished" and should run when the queue is started.
PENDING_STATES = (QUEUED, PAUSED, ERROR)


def default_queue_path():
    return os.path.join(guiconfig.config_dir(), QUEUE_NAME)


class QueueItem:
    """One row in the queue.  Holds config + last-known progress, plus (at
    runtime only) the live :class:`Download` driving it."""

    __slots__ = ("id", "url", "dest", "threads", "sha256", "state",
                 "downloaded", "total", "download")

    def __init__(self, url, dest, threads=1, sha256=None, id=None,
                 state=QUEUED, downloaded=0, total=None):
        self.id = id or uuid.uuid4().hex[:12]
        self.url = url
        self.dest = dest
        self.threads = int(threads) if threads else 1
        self.sha256 = sha256
        self.state = state
        self.downloaded = downloaded or 0
        self.total = total
        self.download = None  # live Download when running

    def to_dict(self):
        # Snapshot live progress if a Download is attached.
        if self.download is not None:
            self.state = self.download.state
            self.downloaded = self.download.downloaded
            self.total = self.download.total
        return {
            "id": self.id,
            "url": self.url,
            "dest": self.dest,
            "threads": self.threads,
            "sha256": self.sha256,
            "state": self.state,
            "downloaded": self.downloaded,
            "total": self.total,
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or not data.get("url"):
            raise ValueError("bad queue item")
        return cls(
            url=data["url"],
            dest=data.get("dest") or ".",
            threads=data.get("threads") or 1,
            sha256=data.get("sha256"),
            id=data.get("id"),
            state=data.get("state") or QUEUED,
            downloaded=data.get("downloaded") or 0,
            total=data.get("total"),
        )


class DownloadQueue:
    """Manage a list of :class:`QueueItem` with a concurrency limit."""

    def __init__(self, path=None, concurrency=None):
        self.path = path or default_queue_path()
        self.concurrency = int(concurrency) if concurrency \
            else guiconfig.get_concurrency()
        self.items = []
        self._lock = threading.Lock()
        self._on_change = None
        self._runner = None
        self._stop = False

    # -- change notification ---------------------------------------------
    def set_on_change(self, callback):
        """Register a callback fired (best-effort) whenever an item changes."""
        self._on_change = callback

    def _notify(self):
        if self._on_change:
            try:
                self._on_change()
            except Exception:
                pass

    # -- CRUD -------------------------------------------------------------
    def add(self, url, dest, threads=1, sha256=None):
        """Append a new item and persist.  Returns its id."""
        if not url:
            raise GrabItError("Cannot add an empty URL to the queue.")
        item = QueueItem(url, dest, threads=threads, sha256=sha256)
        with self._lock:
            self.items.append(item)
        self.save()
        self._notify()
        return item.id

    def remove(self, item_id):
        """Remove an item (cancelling it first if it is running)."""
        with self._lock:
            idx = self._index_of(item_id)
            if idx is None:
                raise GrabItError(f"No queue item with id {item_id}.")
            item = self.items.pop(idx)
        if item.download is not None:
            item.download.cancel()
        self.save()
        self._notify()

    def reorder(self, item_id, new_index):
        """Move *item_id* to *new_index* (clamped to the list bounds)."""
        with self._lock:
            idx = self._index_of(item_id)
            if idx is None:
                raise GrabItError(f"No queue item with id {item_id}.")
            item = self.items.pop(idx)
            new_index = max(0, min(new_index, len(self.items)))
            self.items.insert(new_index, item)
        self.save()
        self._notify()

    def get(self, item_id):
        with self._lock:
            idx = self._index_of(item_id)
            return self.items[idx] if idx is not None else None

    def list(self):
        """Return a shallow copy of the item list (safe to iterate)."""
        with self._lock:
            return list(self.items)

    def clear_finished(self):
        with self._lock:
            self.items = [it for it in self.items
                          if it.state not in (DONE, CANCELLED)]
        self.save()
        self._notify()

    def _index_of(self, item_id):
        for i, it in enumerate(self.items):
            if it.id == item_id:
                return i
        return None

    # -- persistence ------------------------------------------------------
    def save(self):
        """Write the queue to disk (best-effort, atomic replace)."""
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with self._lock:
                payload = {
                    "concurrency": self.concurrency,
                    "items": [it.to_dict() for it in self.items],
                }
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp, self.path)
        except OSError as exc:
            raise GrabItError(f"Could not save the queue to {self.path}: {exc}")

    def load(self):
        """Load items from disk, demoting anything mid-flight to ``queued``."""
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            self.items = []
            return self
        except (OSError, ValueError) as exc:
            raise GrabItError(f"Could not read the queue at {self.path}: {exc}")
        items = []
        for raw in (data.get("items") if isinstance(data, dict) else []) or []:
            try:
                item = QueueItem.from_dict(raw)
            except ValueError:
                continue
            if item.state in (RUNNING, PAUSED):
                item.state = QUEUED  # nothing is running after a fresh load
            items.append(item)
        with self._lock:
            self.items = items
            if isinstance(data, dict) and isinstance(data.get("concurrency"), int):
                self.concurrency = max(1, data["concurrency"])
        return self

    @classmethod
    def load_from(cls, path=None):
        return cls(path=path).load()

    # -- running ----------------------------------------------------------
    def _pending(self):
        return [it for it in self.items if it.state in PENDING_STATES]

    def run_all(self, block=True, per_item_progress=None):
        """Download every pending item, at most ``concurrency`` at a time.

        With ``block=True`` this returns once the queue drains; otherwise a
        background thread drives it and the call returns immediately.
        """
        if not block:
            self._runner = threading.Thread(
                target=self._drive, args=(per_item_progress,), daemon=True)
            self._runner.start()
            return self._runner
        self._drive(per_item_progress)
        return None

    def stop(self):
        """Signal the background runner to stop and cancel live downloads."""
        self._stop = True
        for it in self.list():
            if it.download is not None:
                it.download.cancel()

    def _drive(self, per_item_progress):
        self._stop = False
        sema = threading.Semaphore(max(1, self.concurrency))
        threads = []

        def worker(item):
            try:
                if self._stop:
                    return
                dl = Download(
                    item.url, item.dest, threads=item.threads,
                    sha256=item.sha256,
                    on_progress=(lambda d: self._item_progress(
                        item, d, per_item_progress)))
                item.download = dl
                try:
                    dl.run()
                except GrabItError:
                    pass  # state recorded on the item via the callback/dl.state
                item.state = dl.state
            finally:
                self.save()
                self._notify()
                sema.release()

        for item in self._pending():
            if self._stop:
                break
            item.state = QUEUED
            sema.acquire()
            if self._stop:
                sema.release()
                break
            t = threading.Thread(target=worker, args=(item,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    def _item_progress(self, item, dl, per_item_progress):
        item.state = dl.state
        item.downloaded = dl.downloaded
        item.total = dl.total
        if per_item_progress:
            try:
                per_item_progress(item, dl)
            except Exception:
                pass
        self._notify()
