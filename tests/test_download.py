"""Download engine tests -- all against the local Range-capable server."""

from __future__ import annotations

import hashlib
import os

import pytest

from grabit import DONE, Download, GrabItError
from grabit.download import PART_SUFFIX


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def test_single_stream_exact_bytes(server, known_file, tmp_path):
    name, data, digest = known_file
    dest = tmp_path / "out.bin"
    dl = Download(server.url(name), str(dest), threads=1)
    result = dl.run()
    assert result == str(dest)
    assert dl.state == DONE
    assert dest.read_bytes() == data
    assert _sha(dest) == digest
    assert not os.path.exists(str(dest) + PART_SUFFIX)


def test_single_stream_when_server_refuses_ranges(server, known_file, tmp_path):
    name, data, _ = known_file
    dest = tmp_path / "nr.bin"
    # Even asking for many segments must fall back to a single stream here.
    dl = Download(server.norange_url(name), str(dest), threads=8)
    dl.run()
    assert dest.read_bytes() == data


def test_segmented_exact_bytes(server, known_file, tmp_path):
    name, data, digest = known_file
    dest = tmp_path / "seg.bin"
    dl = Download(server.url(name), str(dest), threads=8)
    dl.run()
    assert dl.total == len(data)
    assert dest.read_bytes() == data
    assert _sha(dest) == digest


def test_segmented_matches_single_stream(server, known_file, tmp_path):
    name, _, _ = known_file
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    Download(server.url(name), str(a), threads=1).run()
    Download(server.url(name), str(b), threads=6).run()
    assert a.read_bytes() == b.read_bytes()


def test_resume_after_interruption(server, known_file, tmp_path):
    name, data, digest = known_file
    dest = tmp_path / "resume.bin"
    part = str(dest) + PART_SUFFIX
    # Simulate an interrupted single-stream download: first 123456 bytes on disk.
    prefix_len = 123_456
    with open(part, "wb") as fh:
        fh.write(data[:prefix_len])
    dl = Download(server.url(name), str(dest), threads=1)
    dl.run()
    # The finished file must be the whole, correct payload.
    assert dest.read_bytes() == data
    assert _sha(dest) == digest


def test_sha256_pass(server, known_file, tmp_path):
    name, _, digest = known_file
    dest = tmp_path / "ok.bin"
    dl = Download(server.url(name), str(dest), threads=4, sha256=digest)
    dl.run()
    assert dl.state == DONE
    assert dest.exists()


def test_sha256_mismatch_raises(server, known_file, tmp_path):
    name, _, _ = known_file
    dest = tmp_path / "bad.bin"
    bogus = "0" * 64
    dl = Download(server.url(name), str(dest), threads=1, sha256=bogus)
    with pytest.raises(GrabItError):
        dl.run()
    assert dl.state == "error"
    # A corrupt/unverified file must not be left behind as the final artifact.
    assert not dest.exists()


def test_404_raises_grabiterror(server, tmp_path):
    dest = tmp_path / "missing.bin"
    dl = Download(server.url("does-not-exist.bin"), str(dest), threads=1)
    with pytest.raises(GrabItError):
        dl.run()
    assert dl.state == "error"


def test_connection_error_raises_grabiterror(tmp_path):
    # Nothing is listening on this port -> a clean GrabItError, not a raw
    # requests exception.
    dest = tmp_path / "x.bin"
    dl = Download("http://127.0.0.1:9/never.bin", str(dest), threads=1,
                  timeout=2)
    with pytest.raises(GrabItError):
        dl.run()


def test_dest_directory_derives_filename(server, known_file, tmp_path):
    name, data, _ = known_file
    outdir = tmp_path / "into"
    outdir.mkdir()
    dl = Download(server.url(name), str(outdir), threads=1)
    path = dl.run()
    assert os.path.basename(path) == name
    assert os.path.dirname(path) == str(outdir)


def test_empty_url_raises():
    with pytest.raises(GrabItError):
        Download("", "out.bin")
