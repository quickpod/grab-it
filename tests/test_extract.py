"""URL extraction from plain lists and simple HTML."""

from __future__ import annotations

import pytest

from grabit import (GrabItError, extract_urls, looks_like_file, urls_from_file,
                    urls_from_html, urls_from_text)


def test_urls_from_text_list():
    text = """
    # a comment
    http://example.com/a.zip
    https://example.com/b.iso

    not-a-url
    """
    urls = urls_from_text(text)
    assert urls == ["http://example.com/a.zip", "https://example.com/b.iso"]


def test_urls_from_text_dedupes_and_strips_trailing_punct():
    text = "See https://example.com/file.pdf. https://example.com/file.pdf"
    assert urls_from_text(text) == ["https://example.com/file.pdf"]


def test_urls_from_html_filters_to_files_and_resolves_relative():
    html = """
    <html><body>
      <a href="song.mp3">song</a>
      <a href="/data/report.pdf">report</a>
      <a href="page.html">not a file</a>
      <a href="https://cdn.example.net/movie.mkv">movie</a>
    </body></html>
    """
    urls = urls_from_html(html, base_url="http://host.example/dir/")
    assert "http://host.example/dir/song.mp3" in urls
    assert "http://host.example/data/report.pdf" in urls
    assert "https://cdn.example.net/movie.mkv" in urls
    # page.html is not a "file-like" extension, so it is filtered out.
    assert all(not u.endswith("page.html") for u in urls)


def test_extract_urls_autodetects_html():
    html = '<a href="http://x/a.zip">a</a>'
    assert extract_urls(html) == ["http://x/a.zip"]


def test_extract_urls_autodetects_plain_list():
    text = "http://x/a\nhttp://x/b"
    assert extract_urls(text) == ["http://x/a", "http://x/b"]


def test_looks_like_file():
    assert looks_like_file("http://x/movie.MP4")
    assert looks_like_file("http://x/path/archive.tar.gz")
    assert not looks_like_file("http://x/index.html")
    assert not looks_like_file("http://x/noext")


def test_urls_from_file(tmp_path):
    p = tmp_path / "list.txt"
    p.write_text("http://x/a.zip\nhttp://x/b.iso\n")
    assert urls_from_file(str(p)) == ["http://x/a.zip", "http://x/b.iso"]


def test_urls_from_file_missing_raises():
    with pytest.raises(GrabItError):
        urls_from_file("/no/such/file.txt")


def test_non_string_raises():
    with pytest.raises(GrabItError):
        extract_urls(None)
