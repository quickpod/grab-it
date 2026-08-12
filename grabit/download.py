"""The download engine: a single :class:`Download` object.

A ``Download`` knows how to fetch one URL to one destination path, either as a
plain single stream or -- when the server advertises ``Accept-Ranges: bytes``
and more than one segment is requested -- as several byte ranges pulled in
parallel by worker threads straight into the correct offsets of a ``.part``
file, which is atomically renamed into place on success.

Design notes:
  * Pure ``requests`` + stdlib ``threading``; importing this module never opens
    a socket.  All work happens inside :meth:`Download.run` (blocking) or
    :meth:`Download.start` (spawns a daemon thread).
  * Interruptions are first-class: :meth:`pause`/:meth:`resume` gate the worker
    loop, :meth:`cancel` unwinds it, and a single-stream download resumes from a
    leftover ``.part`` via a ``Range`` request so a restart never re-fetches
    bytes it already has.
  * Every failure a caller can handle is raised as :class:`GrabItError`.
  * Progress is reported through an optional callback that receives the
    ``Download`` itself, so a CLI or GUI can read ``.downloaded``, ``.total``,
    ``.progress``, ``.speed`` and ``.state`` off one object.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import hashlib
import os
import threading
import time

import requests

from .errors import GrabItError

# ---- download states --------------------------------------------------------
QUEUED = "queued"
RUNNING = "running"
PAUSED = "paused"
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"

PART_SUFFIX = ".part"
DEFAULT_CHUNK = 64 * 1024
DEFAULT_TIMEOUT = 30
DEFAULT_UA = "GrabIt/1.0 (+https://quickopen.ai)"
# Do not bother splitting anything smaller than this into segments.
MIN_SEGMENT_BYTES = 64 * 1024


class _Cancelled(Exception):
    """Internal signal that a running download was cancelled by the user."""


def filename_from_url(url):
    """Best-effort local filename for *url* (never empty)."""
    try:
        from urllib.parse import unquote, urlsplit
        path = urlsplit(url).path
        name = unquote(os.path.basename(path))
    except Exception:
        name = ""
    name = (name or "").strip().strip("/")
    return name or "download.bin"


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class Download:
    """One URL → one file, single-stream or multi-segment, with live progress.

    Parameters
    ----------
    url : str
        The HTTP(S) URL to fetch.
    dest : str
        Destination path.  If it names an existing directory (or ends with a
        path separator) a filename is derived from the URL.
    threads : int
        Number of parallel byte-range segments to use *when the server supports
        it*.  ``1`` (the default) forces a single stream.
    sha256 : str, optional
        Expected lower-case hex SHA-256; verified after assembly.
    on_progress : callable, optional
        Called (throttled) with this ``Download`` as its only argument whenever
        progress advances or the state changes.
    """

    def __init__(self, url, dest, threads=1, sha256=None, on_progress=None,
                 session=None, chunk_size=DEFAULT_CHUNK, timeout=DEFAULT_TIMEOUT,
                 headers=None):
        if not url or not isinstance(url, str):
            raise GrabItError("A download needs a non-empty URL.")
        self.url = url
        self.dest = self._resolve_dest(dest, url)
        try:
            self.threads = max(1, int(threads))
        except (TypeError, ValueError):
            raise GrabItError("threads must be a whole number ≥ 1.")
        self.sha256 = sha256.lower().strip() if sha256 else None
        self.on_progress = on_progress
        self.chunk_size = int(chunk_size) or DEFAULT_CHUNK
        self.timeout = timeout
        self.headers = {"User-Agent": DEFAULT_UA}
        if headers:
            self.headers.update(headers)

        self._session = session
        self._owns_session = session is None

        self.total = None          # bytes, or None if unknown
        self.speed = 0.0           # bytes/sec (rolling)
        self.error = None          # last error message
        self._state = QUEUED
        self._downloaded = 0
        self._lock = threading.Lock()
        self._run_gate = threading.Event()
        self._run_gate.set()       # set == "go"
        self._cancelled = False
        self._thread = None
        self._last_emit = 0.0
        self._last_t = time.monotonic()
        self._last_b = 0

    # -- small helpers ----------------------------------------------------
    @staticmethod
    def _resolve_dest(dest, url):
        dest = dest or "."
        if dest.endswith(("/", os.sep)) or os.path.isdir(dest):
            return os.path.join(dest, filename_from_url(url))
        return dest

    @property
    def session(self):
        if self._session is None:
            self._session = requests.Session()
        return self._session

    @property
    def state(self):
        return self._state

    @property
    def downloaded(self):
        with self._lock:
            return self._downloaded

    @property
    def progress(self):
        """Fraction complete in ``[0, 1]`` (``0.0`` when the size is unknown)."""
        with self._lock:
            if self.total and self.total > 0:
                return min(1.0, self._downloaded / self.total)
            return 1.0 if self._state == DONE else 0.0

    def _set_state(self, state):
        self._state = state
        self._emit(force=True)

    def _emit(self, force=False):
        if not self.on_progress:
            return
        now = time.monotonic()
        if not force and (now - self._last_emit) < 0.05:
            return
        self._last_emit = now
        try:
            self.on_progress(self)
        except Exception:
            pass  # a broken UI callback must never derail a download

    def _advance(self, n):
        now = time.monotonic()
        with self._lock:
            self._downloaded += n
            dt = now - self._last_t
            if dt >= 0.25:
                self.speed = (self._downloaded - self._last_b) / dt
                self._last_t = now
                self._last_b = self._downloaded
        self._emit()

    def _gate(self):
        """Block while paused; raise :class:`_Cancelled` if cancelled."""
        if self._cancelled:
            raise _Cancelled()
        if not self._run_gate.is_set():
            self._set_state(PAUSED)
            while not self._run_gate.wait(timeout=0.2):
                if self._cancelled:
                    raise _Cancelled()
            if self._cancelled:
                raise _Cancelled()
            self._set_state(RUNNING)

    # -- controls ---------------------------------------------------------
    def pause(self):
        """Request a pause; workers stop at the next chunk boundary."""
        self._run_gate.clear()

    def resume(self):
        """Undo a :meth:`pause`."""
        if self._state in (DONE, ERROR, CANCELLED):
            return
        self._run_gate.set()

    def cancel(self):
        """Stop the download as soon as possible (leaves ``.part`` in place)."""
        self._cancelled = True
        self._run_gate.set()  # wake any paused worker so it can see the flag

    def start(self):
        """Run the download on a background daemon thread and return it."""
        if self._thread and self._thread.is_alive():
            return self._thread
        self._thread = threading.Thread(target=self._run_guarded, daemon=True)
        self._thread.start()
        return self._thread

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    def _run_guarded(self):
        try:
            self.run()
        except GrabItError:
            pass  # state/error already recorded; thread must not raise

    # -- the actual work --------------------------------------------------
    def run(self):
        """Perform the download (blocking).  Returns the final path.

        Raises :class:`GrabItError` on any failure.
        """
        self._cancelled = False
        self._run_gate.set()
        with self._lock:
            self._downloaded = 0
            self._last_b = 0
        self._last_t = time.monotonic()
        self._set_state(RUNNING)
        try:
            size, accept, final_url = self._probe()
            self.total = size
            if accept and self.threads > 1 and size and size > MIN_SEGMENT_BYTES:
                self._run_segmented(final_url, size)
            else:
                self._run_single(final_url, size, accept)
            if self.sha256:
                self._verify()
            self._set_state(DONE)
            return self.dest
        except _Cancelled:
            self.error = "Cancelled."
            self._set_state(CANCELLED)
            raise GrabItError("Download cancelled.")
        except GrabItError as exc:
            self.error = str(exc)
            self._set_state(ERROR)
            raise
        except requests.RequestException as exc:
            self.error = str(exc)
            self._set_state(ERROR)
            raise GrabItError(f"Network error while downloading {self.url}: {exc}")
        finally:
            if self._owns_session and self._session is not None:
                try:
                    self._session.close()
                except Exception:
                    pass
                self._session = None

    def _probe(self):
        """Return ``(size_or_None, accept_ranges_bool, final_url)``."""
        # Prefer a cheap HEAD; fall back to a 1-byte ranged GET for servers that
        # refuse HEAD or hide their headers behind it.
        try:
            r = self.session.head(self.url, headers=self.headers,
                                  allow_redirects=True, timeout=self.timeout)
            if r.status_code < 400:
                size = _int_or_none(r.headers.get("Content-Length"))
                accept = "bytes" in r.headers.get("Accept-Ranges", "").lower()
                return size, accept, r.url
        except requests.RequestException:
            pass
        try:
            r = self.session.get(self.url, headers={**self.headers,
                                                   "Range": "bytes=0-0"},
                                stream=True, allow_redirects=True,
                                timeout=self.timeout)
        except requests.RequestException as exc:
            raise GrabItError(f"Could not reach {self.url}: {exc}")
        try:
            if r.status_code == 206:
                cr = r.headers.get("Content-Range", "")
                total = _int_or_none(cr.rsplit("/", 1)[-1]) if "/" in cr else None
                return total, True, r.url
            if r.status_code >= 400:
                raise GrabItError(f"HTTP {r.status_code} for {self.url}")
            # Plain 200: no range support advertised.
            return _int_or_none(r.headers.get("Content-Length")), False, r.url
        finally:
            r.close()

    def _run_single(self, url, size, accept):
        part = self.dest + PART_SUFFIX
        self._ensure_parent(part)
        existing = 0
        mode = "wb"
        headers = dict(self.headers)
        if accept and size and os.path.exists(part):
            have = os.path.getsize(part)
            if 0 < have < size:
                existing = have
                headers["Range"] = f"bytes={have}-"
                mode = "ab"
            # (have >= size means a stale/oversized part -> start fresh)
        try:
            resp = self.session.get(url, headers=headers, stream=True,
                                   allow_redirects=True, timeout=self.timeout)
        except requests.RequestException as exc:
            raise GrabItError(f"Network error while downloading {url}: {exc}")
        with resp:
            if resp.status_code == 416:  # requested range not satisfiable
                existing, mode = 0, "wb"
                resp.close()
                resp = self.session.get(url, headers=self.headers, stream=True,
                                       allow_redirects=True, timeout=self.timeout)
            if "Range" in headers and resp.status_code == 200:
                existing, mode = 0, "wb"  # server ignored our Range: full body
            if resp.status_code >= 400:
                raise GrabItError(f"HTTP {resp.status_code} for {url}")
            with self._lock:
                self._downloaded = existing
            self._emit(force=True)
            with open(part, mode) as fh:
                for chunk in resp.iter_content(self.chunk_size):
                    self._gate()
                    if chunk:
                        fh.write(chunk)
                        self._advance(len(chunk))
        os.replace(part, self.dest)

    def _run_segmented(self, url, size):
        part = self.dest + PART_SUFFIX
        self._ensure_parent(part)
        with open(part, "wb") as fh:  # preallocate so every worker can seek
            fh.truncate(size)
        n = max(1, min(self.threads, size))
        ranges = self._split_ranges(size, n)
        with self._lock:
            self._downloaded = 0
        self._emit(force=True)
        errors = []
        workers = []
        for start, end in ranges:
            t = threading.Thread(target=self._segment_worker,
                                 args=(url, part, start, end, errors),
                                 daemon=True)
            t.start()
            workers.append(t)
        for t in workers:
            t.join()
        for err in errors:
            if isinstance(err, _Cancelled):
                raise err
        for err in errors:
            raise err
        os.replace(part, self.dest)

    @staticmethod
    def _split_ranges(size, n):
        base = size // n
        ranges = []
        start = 0
        for i in range(n):
            end = size - 1 if i == n - 1 else start + base - 1
            ranges.append((start, end))
            start = end + 1
        return ranges

    def _segment_worker(self, url, part, start, end, errors):
        try:
            headers = dict(self.headers)
            headers["Range"] = f"bytes={start}-{end}"
            resp = self.session.get(url, headers=headers, stream=True,
                                   allow_redirects=True, timeout=self.timeout)
            with resp:
                if resp.status_code != 206:
                    raise GrabItError(
                        f"Server did not honour a range request "
                        f"(HTTP {resp.status_code}).")
                with open(part, "r+b") as fh:
                    fh.seek(start)
                    for chunk in resp.iter_content(self.chunk_size):
                        self._gate()
                        if chunk:
                            fh.write(chunk)
                            self._advance(len(chunk))
        except _Cancelled as exc:
            errors.append(exc)
        except GrabItError as exc:
            errors.append(exc)
            self.cancel()  # stop sibling workers
        except requests.RequestException as exc:
            errors.append(GrabItError(str(exc)))
            self.cancel()

    def _verify(self):
        h = hashlib.sha256()
        try:
            with open(self.dest, "rb") as fh:
                for block in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(block)
        except OSError as exc:
            raise GrabItError(f"Could not read {self.dest} to verify: {exc}")
        got = h.hexdigest()
        if got != self.sha256:
            try:
                os.remove(self.dest)
            except OSError:
                pass
            raise GrabItError(
                f"SHA-256 mismatch: expected {self.sha256}, got {got}.")

    @staticmethod
    def _ensure_parent(path):
        parent = os.path.dirname(os.path.abspath(path))
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            raise GrabItError(f"Could not create folder {parent}: {exc}")


def download(url, dest, threads=1, sha256=None, on_progress=None, **kw):
    """Convenience one-shot: build a :class:`Download`, run it, return the path."""
    return Download(url, dest, threads=threads, sha256=sha256,
                    on_progress=on_progress, **kw).run()
