"""Pull direct file URLs out of a pasted list or a simple HTML page.

This is deliberately generic: it never logs in, never renders JavaScript and
never scrapes any particular site.  Given text it recognises one-URL-per-line
lists; given HTML it walks ``<a href>`` (and a few obvious media attributes) and
keeps the links whose path ends in a file-like extension.  Relative links are
resolved against an optional base URL.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .errors import GrabItError

# A pragmatic set of "this looks like a downloadable file" extensions.
FILE_EXTENSIONS = {
    # archives
    "zip", "gz", "tgz", "bz2", "xz", "7z", "rar", "tar", "zst",
    # disk / installers
    "iso", "img", "dmg", "exe", "msi", "deb", "rpm", "appimage", "apk",
    # docs
    "pdf", "epub", "mobi", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt",
    "csv", "json", "xml",
    # media
    "mp3", "flac", "wav", "ogg", "m4a", "mp4", "mkv", "avi", "mov", "webm",
    "png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "tif", "tiff",
    # code / data
    "whl", "jar", "bin", "dll", "so", "gguf", "safetensors", "pt", "onnx",
    "sql", "db",
}

_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def _ext(url):
    try:
        path = urlsplit(url).path
    except Exception:
        return ""
    _, _, tail = path.rpartition("/")
    _, dot, ext = tail.rpartition(".")
    return ext.lower() if dot else ""


def looks_like_file(url):
    """True if *url*'s path ends in a known file-like extension."""
    return _ext(url) in FILE_EXTENSIONS


class _LinkParser(HTMLParser):
    """Collect href/src-style attributes from anchors and media tags."""

    _ATTRS = {"href", "src", "data-href", "data-url"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in self._ATTRS and value:
                self.links.append(value.strip())


def urls_from_html(html, base_url=None, only_files=True):
    """Return the links found in *html* (resolved against *base_url*)."""
    if not isinstance(html, str):
        raise GrabItError("HTML to scan must be text.")
    parser = _LinkParser()
    try:
        parser.feed(html)
    except Exception as exc:
        raise GrabItError(f"Could not parse HTML: {exc}")
    out = []
    seen = set()
    for raw in parser.links:
        url = urljoin(base_url, raw) if base_url else raw
        if not url.lower().startswith(("http://", "https://")):
            continue
        if only_files and not looks_like_file(url):
            continue
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def urls_from_text(text, only_files=False):
    """Return URLs from a plain list/blob of text.

    Accepts one-URL-per-line lists (``#`` comments and blanks ignored) and also
    finds bare ``http(s)://`` URLs embedded anywhere in the text.
    """
    if not isinstance(text, str):
        raise GrabItError("Text to scan must be a string.")
    out = []
    seen = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for match in _URL_RE.findall(stripped):
            url = match.rstrip(".,);]")
            if only_files and not looks_like_file(url):
                continue
            if url not in seen:
                seen.add(url)
                out.append(url)
    return out


def _looks_like_html(source):
    lowered = source.lstrip()[:512].lower()
    return "<a " in lowered or "<html" in lowered or "<!doctype html" in lowered


def extract_urls(source, base_url=None, only_files=None):
    """Extract file URLs from *source*, auto-detecting a plain list vs. HTML.

    ``only_files`` defaults to True for HTML (which is link-dense) and False for
    a plain list (where the user typed exactly what they mean).
    """
    if not isinstance(source, str):
        raise GrabItError("Source to extract from must be a string.")
    if _looks_like_html(source):
        want = True if only_files is None else only_files
        return urls_from_html(source, base_url=base_url, only_files=want)
    want = False if only_files is None else only_files
    return urls_from_text(source, only_files=want)


def urls_from_file(path, base_url=None, only_files=None):
    """Read *path* and extract URLs from its contents."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = fh.read()
    except OSError as exc:
        raise GrabItError(f"Could not read {path}: {exc}")
    return extract_urls(data, base_url=base_url, only_files=only_files)
