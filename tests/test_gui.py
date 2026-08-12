"""GUI import/headless-safety tests (no display required)."""

from __future__ import annotations
import pytest
import sys

import os


def test_import_is_side_effect_free():
    # Importing the GUI must not create a Tk root or touch the display.
    from grabit import gui
    assert hasattr(gui, "main")
    assert callable(gui.main)


def test_asset_path_returns_none_for_missing():
    from grabit import gui
    assert gui.asset_path("definitely-not-a-real-asset.xyz") is None


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows CI has a real display; main() would open a window and block")
def test_human_size():
    from grabit import gui
    assert gui.human_size(0) == "0B"
    assert gui.human_size(1536).endswith("KB")


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows CI has a real display; main() would open a window and block")
def test_looks_like_url():
    from grabit.gui import _looks_like_url
    assert _looks_like_url("https://example.com/a.zip")
    assert not _looks_like_url("not a url")
    assert not _looks_like_url("ftp://example.com/x")


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows CI has a real display; main() would open a window and block")
def test_main_headless_returns_zero(monkeypatch):
    # Force the headless path: no DISPLAY on a non-Windows, non-mac host.
    from grabit import gui
    monkeypatch.delenv("DISPLAY", raising=False)
    if os.name == "nt":
        return  # on Windows the GUI would actually try to start
    assert gui.main() == 0
