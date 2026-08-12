"""Shared pytest fixtures: a local, Range-capable HTTP server (no external net).

Everything here binds to 127.0.0.1 on an ephemeral port and serves a temp dir.
The handler implements HEAD (with ``Accept-Ranges: bytes`` + ``Content-Length``)
and GET with single-range support (``206`` + ``Content-Range``), which is
exactly what the segmented / resume code exercises.  A per-path toggle lets a
test force ``Accept-Ranges: none`` to check the single-stream fallback.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

# Paths under this name are served WITHOUT range support (forces single-stream).
NO_RANGE_PREFIX = "/norange/"

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def make_payload(n_bytes):
    """Deterministic pseudo-random bytes of length *n_bytes*."""
    out = bytearray()
    seed = 0x9E3779B1
    while len(out) < n_bytes:
        seed = (1103515245 * seed + 12345) & 0xFFFFFFFF
        out.extend(seed.to_bytes(4, "little"))
    return bytes(out[:n_bytes])


class _Handler(BaseHTTPRequestHandler):
    # set by the server factory
    directory = None

    def log_message(self, *args):  # silence test noise
        pass

    def _resolve(self):
        path = self.path.split("?", 1)[0]
        ranged = not path.startswith(NO_RANGE_PREFIX)
        if path.startswith(NO_RANGE_PREFIX):
            path = "/" + path[len(NO_RANGE_PREFIX):]
        rel = path.lstrip("/")
        full = os.path.normpath(os.path.join(self.directory, rel))
        if not full.startswith(os.path.abspath(self.directory)):
            return None, ranged
        return full, ranged

    def do_HEAD(self):
        full, ranged = self._resolve()
        if not full or not os.path.isfile(full):
            self.send_error(404, "Not Found")
            return
        size = os.path.getsize(full)
        self.send_response(200)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes" if ranged else "none")
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()

    def do_GET(self):
        full, ranged = self._resolve()
        if not full or not os.path.isfile(full):
            self.send_error(404, "Not Found")
            return
        with open(full, "rb") as fh:
            data = fh.read()
        size = len(data)
        rng = self.headers.get("Range")
        if ranged and rng:
            m = _RANGE_RE.search(rng)
            if m:
                start = int(m.group(1)) if m.group(1) else 0
                end = int(m.group(2)) if m.group(2) else size - 1
                end = min(end, size - 1)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                chunk = data[start:end + 1]
                self.send_response(206)
                self.send_header("Content-Range",
                                 f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(len(chunk)))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Type", "application/octet-stream")
                self.end_headers()
                self.wfile.write(chunk)
                return
        # full body
        self.send_response(200)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes" if ranged else "none")
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        self.wfile.write(data)


class LocalServer:
    def __init__(self, directory):
        self.directory = directory
        handler = type("BoundHandler", (_Handler,), {"directory": directory})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)

    def start(self):
        self.thread.start()
        return self

    def url(self, name):
        return f"{self.base_url}/{name}"

    def norange_url(self, name):
        return f"{self.base_url}{NO_RANGE_PREFIX}{name}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def served_dir(tmp_path):
    d = tmp_path / "www"
    d.mkdir()
    return d


@pytest.fixture
def server(served_dir):
    srv = LocalServer(str(served_dir)).start()
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture
def known_file(served_dir):
    """A ~500 KB deterministic file placed in the served dir.

    Returns ``(name, bytes, sha256_hex)``.
    """
    data = make_payload(500_000)
    (served_dir / "sample.bin").write_bytes(data)
    return "sample.bin", data, hashlib.sha256(data).hexdigest()
