r"""Tiny JSON-backed config for GrabIt (shared by the GUI, queue and CLI).

Stores a handful of never-critical preferences: the chosen theme, the download
concurrency, the last download folder, and whether clipboard-watch is on.  On
Windows the file lives at ``%LOCALAPPDATA%\GrabIt\config.json``; elsewhere it
falls back to ``~/.grabit/config.json``.  Every function is defensive -- a
corrupt or unreadable config must never stop the app from starting.
"""

from __future__ import annotations

import json
import os

APP_DIRNAME = "GrabIt"
CONFIG_NAME = "config.json"
VALID_THEMES = ("light", "dark")
MIN_CONC, MAX_CONC = 1, 16


def config_dir():
    r"""Directory holding the config + queue files (created on demand).

    ``%LOCALAPPDATA%\GrabIt`` on Windows, ``~/.grabit`` otherwise.
    """
    local = os.environ.get("LOCALAPPDATA")
    if local and os.name == "nt":
        return os.path.join(local, APP_DIRNAME)
    return os.path.join(os.path.expanduser("~"), "." + APP_DIRNAME.lower())


def config_path():
    return os.path.join(config_dir(), CONFIG_NAME)


def _defaults():
    return {
        "theme": "dark",
        "concurrency": 2,
        "download_dir": os.path.join(os.path.expanduser("~"), "Downloads"),
        "clipboard_watch": False,
    }


def load():
    """Return the config dict, always fully populated with valid values."""
    cfg = _defaults()
    try:
        with open(config_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            if data.get("theme") in VALID_THEMES:
                cfg["theme"] = data["theme"]
            conc = data.get("concurrency")
            if isinstance(conc, int) and MIN_CONC <= conc <= MAX_CONC:
                cfg["concurrency"] = conc
            if isinstance(data.get("download_dir"), str) and data["download_dir"]:
                cfg["download_dir"] = data["download_dir"]
            if isinstance(data.get("clipboard_watch"), bool):
                cfg["clipboard_watch"] = data["clipboard_watch"]
    except Exception:
        pass  # missing/corrupt -> defaults; never fatal
    return cfg


def save(cfg):
    """Persist *cfg* (best-effort; failures are swallowed)."""
    try:
        os.makedirs(config_dir(), exist_ok=True)
        base = _defaults()
        base.update({k: cfg[k] for k in base if k in cfg})
        # clamp/validate
        if base["theme"] not in VALID_THEMES:
            base["theme"] = "dark"
        try:
            base["concurrency"] = max(MIN_CONC, min(MAX_CONC,
                                                    int(base["concurrency"])))
        except (TypeError, ValueError):
            base["concurrency"] = 2
        base["clipboard_watch"] = bool(base["clipboard_watch"])
        tmp = config_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(base, fh, indent=2)
        os.replace(tmp, config_path())
    except Exception:
        pass


def _get(key):
    return load().get(key)


def _set(key, value):
    cfg = load()
    cfg[key] = value
    save(cfg)


def get_theme():
    return load().get("theme", "dark")


def set_theme(theme):
    if theme in VALID_THEMES:
        _set("theme", theme)


def get_concurrency():
    return load().get("concurrency", 2)


def set_concurrency(n):
    try:
        _set("concurrency", max(MIN_CONC, min(MAX_CONC, int(n))))
    except (TypeError, ValueError):
        pass


def get_download_dir():
    return load().get("download_dir")


def set_download_dir(path):
    if path:
        _set("download_dir", path)


def get_clipboard_watch():
    return bool(load().get("clipboard_watch", False))


def set_clipboard_watch(on):
    _set("clipboard_watch", bool(on))
