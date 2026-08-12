"""Queue: add / remove / reorder / persist / reload."""

from __future__ import annotations

import pytest

from grabit import DownloadQueue, GrabItError
from grabit.download import QUEUED, RUNNING


def _queue(tmp_path):
    return DownloadQueue(path=str(tmp_path / "queue.json"), concurrency=2)


def test_add_and_list(tmp_path):
    q = _queue(tmp_path)
    a = q.add("http://x/a.zip", str(tmp_path / "a.zip"))
    b = q.add("http://x/b.zip", str(tmp_path / "b.zip"), threads=4)
    items = q.list()
    assert [it.id for it in items] == [a, b]
    assert items[1].threads == 4
    assert items[0].state == QUEUED


def test_remove(tmp_path):
    q = _queue(tmp_path)
    a = q.add("http://x/a.zip", "a.zip")
    q.add("http://x/b.zip", "b.zip")
    q.remove(a)
    assert [it.url for it in q.list()] == ["http://x/b.zip"]
    with pytest.raises(GrabItError):
        q.remove("nope")


def test_reorder(tmp_path):
    q = _queue(tmp_path)
    a = q.add("http://x/a", "a")
    b = q.add("http://x/b", "b")
    c = q.add("http://x/c", "c")
    q.reorder(c, 0)
    assert [it.id for it in q.list()] == [c, a, b]
    q.reorder(a, 99)  # clamps to the end
    assert [it.id for it in q.list()] == [c, b, a]


def test_persist_and_reload(tmp_path):
    path = str(tmp_path / "queue.json")
    q = DownloadQueue(path=path, concurrency=3)
    q.add("http://x/a.iso", "/downloads/a.iso", threads=8, sha256="abc")
    q.add("http://x/b.iso", "/downloads/b.iso")

    q2 = DownloadQueue.load_from(path)
    items = q2.list()
    assert len(items) == 2
    assert q2.concurrency == 3
    assert items[0].url == "http://x/a.iso"
    assert items[0].threads == 8
    assert items[0].sha256 == "abc"
    assert items[1].dest == "/downloads/b.iso"


def test_reload_demotes_running(tmp_path):
    path = str(tmp_path / "queue.json")
    q = DownloadQueue(path=path)
    q.add("http://x/a", "a")
    # Force a 'running' state on disk, then reload.
    q.items[0].state = RUNNING
    q.save()
    q2 = DownloadQueue.load_from(path)
    assert q2.list()[0].state == QUEUED


def test_add_empty_url_raises(tmp_path):
    q = _queue(tmp_path)
    with pytest.raises(GrabItError):
        q.add("", "dest")


def test_run_all_downloads_everything(server, known_file, tmp_path):
    name, data, _ = known_file
    q = DownloadQueue(path=str(tmp_path / "q.json"), concurrency=2)
    d1 = tmp_path / "one.bin"
    d2 = tmp_path / "two.bin"
    q.add(server.url(name), str(d1), threads=4)
    q.add(server.url(name), str(d2), threads=1)
    q.run_all(block=True)
    assert d1.read_bytes() == data
    assert d2.read_bytes() == data
    assert all(it.state == "done" for it in q.list())
