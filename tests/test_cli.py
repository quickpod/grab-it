"""CLI (`python -m grabit`) behaviour, driven against the local server."""

from __future__ import annotations

import hashlib
import os

from grabit.__main__ import main


def test_cli_get_downloads_and_verifies(server, known_file, tmp_path, capsys):
    name, data, digest = known_file
    dest = tmp_path / "cli.bin"
    rc = main(["get", server.url(name), str(dest), "--threads", "4",
               "--sha256", digest])
    assert rc == 0
    assert dest.read_bytes() == data
    out = capsys.readouterr().out
    assert str(dest) in out or os.path.abspath(str(dest)) in out


def test_cli_get_bad_sha_exits_1(server, known_file, tmp_path, capsys):
    name, _, _ = known_file
    dest = tmp_path / "cli2.bin"
    rc = main(["get", server.url(name), str(dest), "--sha256", "0" * 64])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err


def test_cli_get_404_exits_1(server, tmp_path, capsys):
    dest = tmp_path / "cli3.bin"
    rc = main(["get", server.url("nope.bin"), str(dest)])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_cli_batch(server, known_file, tmp_path, served_dir, capsys):
    name, data, _ = known_file
    # A second known file so the batch has two entries.
    (served_dir / "second.bin").write_bytes(data)
    listing = tmp_path / "urls.txt"
    listing.write_text(f"{server.url(name)}\n{server.url('second.bin')}\n")
    outdir = tmp_path / "out"
    rc = main(["batch", str(listing), str(outdir)])
    assert rc == 0
    assert (outdir / name).read_bytes() == data
    assert (outdir / "second.bin").read_bytes() == data


def test_cli_queue_lists(tmp_path, capsys):
    from grabit.queue import DownloadQueue
    qpath = tmp_path / "queue.json"
    q = DownloadQueue(path=str(qpath))
    q.add("http://x/a.zip", "/tmp/a.zip")
    rc = main(["queue", "--queue-file", str(qpath)])
    assert rc == 0
    assert "http://x/a.zip" in capsys.readouterr().out
