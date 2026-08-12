"""Command-line interface: ``python -m grabit <command> ...``.

Commands
--------
``get <url> [dest] [--threads N] [--sha256 HASH]``
    Download one URL.  Prints a live progress line and the final path.
``batch <urls.txt> [dest_dir] [--threads N]``
    Extract URLs from a file (plain list or HTML) and download each into
    ``dest_dir`` (default: current directory).
``queue``
    List the persisted queue.

Exits ``0`` on success and ``1`` (printing ``error: ...`` to stderr) on any
:class:`GrabItError`.
"""

from __future__ import annotations

import argparse
import os
import sys

from .download import DONE, Download, filename_from_url
from .errors import GrabItError
from .extract import urls_from_file
from .queue import DownloadQueue


# ---- progress rendering ----------------------------------------------------
def _human(num):
    size = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}TB"


class _ProgressPrinter:
    """Render a single carriage-returned progress line to stderr (a tty only)."""

    def __init__(self, label):
        self.label = label
        self._tty = sys.stderr.isatty()

    def __call__(self, dl):
        if not self._tty:
            return
        if dl.total:
            pct = dl.progress * 100.0
            bar_w = 24
            filled = int(bar_w * dl.progress)
            bar = "#" * filled + "-" * (bar_w - filled)
            line = (f"\r{self.label} [{bar}] {pct:5.1f}%  "
                    f"{_human(dl.downloaded)}/{_human(dl.total)}  "
                    f"{_human(dl.speed)}/s")
        else:
            line = (f"\r{self.label}  {_human(dl.downloaded)}  "
                    f"{_human(dl.speed)}/s")
        sys.stderr.write(line[:110].ljust(0))
        sys.stderr.flush()

    def done(self):
        if self._tty:
            sys.stderr.write("\n")
            sys.stderr.flush()


# ---- command handlers ------------------------------------------------------
def cmd_get(args):
    dest = args.dest or filename_from_url(args.url)
    printer = _ProgressPrinter(os.path.basename(dest))
    dl = Download(args.url, dest, threads=args.threads, sha256=args.sha256,
                  on_progress=printer)
    try:
        path = dl.run()
    finally:
        printer.done()
    mode = "segmented" if args.threads > 1 else "single-stream"
    verified = "  (sha256 OK)" if args.sha256 else ""
    print(f"Downloaded {args.url}")
    print(f"  -> {os.path.abspath(path)}  [{mode}]{verified}")


def cmd_batch(args):
    urls = urls_from_file(args.urls_file)
    if not urls:
        raise GrabItError(f"No downloadable URLs found in {args.urls_file}.")
    dest_dir = args.dest_dir or "."
    os.makedirs(dest_dir, exist_ok=True)
    print(f"Found {len(urls)} URL(s); downloading into {os.path.abspath(dest_dir)}")
    failures = 0
    for i, url in enumerate(urls, 1):
        dest = os.path.join(dest_dir, filename_from_url(url))
        label = f"[{i}/{len(urls)}] {os.path.basename(dest)}"
        printer = _ProgressPrinter(label)
        try:
            dl = Download(url, dest, threads=args.threads, on_progress=printer)
            path = dl.run()
            printer.done()
            print(f"  ok   {url} -> {os.path.abspath(path)}")
        except GrabItError as exc:
            printer.done()
            failures += 1
            print(f"  FAIL {url}: {exc}", file=sys.stderr)
    if failures:
        raise GrabItError(f"{failures} of {len(urls)} download(s) failed.")


def cmd_queue(args):
    q = DownloadQueue.load_from(args.queue_file)
    items = q.list()
    if not items:
        print("The queue is empty.")
        return
    print(f"Queue ({len(items)} item(s)) — {q.path}")
    for i, it in enumerate(items, 1):
        size = _human(it.total) if it.total else "?"
        got = _human(it.downloaded) if it.downloaded else "0B"
        print(f"  {i:2d}. [{it.state:9s}] {got}/{size}  {it.url}")
        print(f"       -> {it.dest}")


# ---- parser ----------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(
        prog="grabit",
        description="GrabIt — a multi-threaded download manager (CLI).")
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    p_get = sub.add_parser("get", help="download a single URL")
    p_get.add_argument("url")
    p_get.add_argument("dest", nargs="?", default=None,
                       help="output path (default: filename from the URL)")
    p_get.add_argument("--threads", "-t", type=int, default=1,
                       help="parallel segments when the server supports ranges")
    p_get.add_argument("--sha256", default=None,
                       help="expected SHA-256 hex to verify after download")
    p_get.set_defaults(func=cmd_get)

    p_batch = sub.add_parser("batch", help="download every URL in a list/HTML file")
    p_batch.add_argument("urls_file", help="text list or HTML file of URLs")
    p_batch.add_argument("dest_dir", nargs="?", default=None,
                         help="output folder (default: current directory)")
    p_batch.add_argument("--threads", "-t", type=int, default=1)
    p_batch.set_defaults(func=cmd_batch)

    p_queue = sub.add_parser("queue", help="list the persisted download queue")
    p_queue.add_argument("--queue-file", default=None,
                        help="path to a queue.json (default: config dir)")
    p_queue.set_defaults(func=cmd_queue)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except GrabItError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
