"""grabit -- a permissively-licensed multi-threaded download engine.

Public API::

    from grabit import Download, DownloadQueue, GrabItError
    Download("https://example.com/file.iso", "file.iso", threads=8).run()

Everything raises :class:`GrabItError` on a recoverable failure so the CLI and
GUI have a single exception to catch.  A GUI (``grabit.gui``) and CLI
(``python -m grabit``) build on this module.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

from .errors import GrabItError
from .download import (
    CANCELLED,
    DONE,
    ERROR,
    PAUSED,
    QUEUED,
    RUNNING,
    Download,
    download,
    filename_from_url,
)
from .queue import DownloadQueue, QueueItem, default_queue_path
from .extract import (
    extract_urls,
    looks_like_file,
    urls_from_file,
    urls_from_html,
    urls_from_text,
)

__version__ = "1.0.0"

__all__ = [
    "GrabItError",
    "Download",
    "download",
    "filename_from_url",
    "DownloadQueue",
    "QueueItem",
    "default_queue_path",
    "extract_urls",
    "urls_from_text",
    "urls_from_html",
    "urls_from_file",
    "looks_like_file",
    "QUEUED",
    "RUNNING",
    "PAUSED",
    "DONE",
    "ERROR",
    "CANCELLED",
    "__version__",
]
