#!/usr/bin/env python3
r"""GrabIt -- a pure-stdlib tkinter GUI on top of the ``grabit`` download engine.

A single main window: a top bar to add a URL, a central queue table (name, size,
progress, speed, state), per-row Pause / Resume / Cancel / Remove controls, a
concurrency setting and an optional clipboard-watch that auto-adds copied URLs.
Every download runs on the tested :class:`grabit.Download` engine on background
threads; the UI refreshes on a light ``self.after`` poll and never blocks.

Design goals (mirrored from the QuickOpen house style):
  * pure standard-library tkinter/ttk -- NO third-party GUI deps.  Dark mode is
    a ttk-style + palette swap (the QuickOpen palette).
  * Importing this module does nothing.  Only :func:`main` builds a root window,
    and it degrades gracefully (prints a message, returns 0) with no display.
  * Frozen-exe safe: bundled assets are resolved via ``sys._MEIPASS`` / the exe
    directory when ``sys.frozen`` is set -- never ``__file__``.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys

# NOTE: tkinter is imported lazily inside main()/build_app so that merely
# importing this module (e.g. during packaging or on a headless CI box) never
# fails and has no side effects.

APP_NAME = "GrabIt"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "GrabIt — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai"

# ---- colour palettes (mirror the QuickOpen palette) -------------------------
PALETTES = {
    "light": {
        "bg": "#f5f7fa", "surface": "#ffffff", "text": "#141820",
        "muted": "#5b6472", "primary": "#2f5fe0", "primary_hi": "#2450c8",
        "entry": "#ffffff", "border": "#d5dae2", "sel": "#2f5fe0",
        "sel_fg": "#ffffff", "trough": "#e2e7ef", "ok": "#1f7a3d",
        "err": "#c0392b",
    },
    "dark": {
        "bg": "#0f1115", "surface": "#1a1e24", "text": "#f1f3f7",
        "muted": "#9aa4b2", "primary": "#5b86f7", "primary_hi": "#7098ff",
        "entry": "#1a1e24", "border": "#2a2f38", "sel": "#5b86f7",
        "sel_fg": "#0f1115", "trough": "#2a2f38", "ok": "#5bd68a",
        "err": "#ff6b5e",
    },
}


# ---------------------------------------------------------------------------
# Asset / frozen handling
# ---------------------------------------------------------------------------
def asset_path(name):
    """Locate a bundled asset from source OR a PyInstaller one-file build.

    For a frozen exe we look only at ``sys._MEIPASS`` and the executable's own
    directory (never ``__file__``).  From source we also consult the package
    dir, the repo root and the CWD.  Returns an absolute path or ``None``.
    """
    roots = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(meipass)
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        roots += [here, os.path.dirname(here), os.getcwd()]
    for root in roots:
        candidate = os.path.join(root, name)
        if os.path.exists(candidate):
            return candidate
    return None


def human_size(num_bytes):
    """Human-readable byte size."""
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{int(size)}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}TB"


def open_in_file_manager(path):
    """Best-effort 'reveal in file manager', guarded on every platform."""
    try:
        folder = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
        if hasattr(os, "startfile"):          # Windows
            os.startfile(folder)              # noqa: S606 - intended
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", folder])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", folder])
        return True
    except Exception:
        return False


def open_with_default_app(path):
    """Open a file/URL with the OS default application, guarded."""
    try:
        if hasattr(os, "startfile"):
            os.startfile(path)                # noqa: S606
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


def _looks_like_url(text):
    text = (text or "").strip()
    return (len(text) < 4096 and " " not in text
            and text.lower().startswith(("http://", "https://")))


# ---------------------------------------------------------------------------
# The app (built lazily; tkinter imported only inside build_app/main)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to a live tkinter import.

    Kept inside a function so this module imports cleanly without a display.
    """
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    from . import guiconfig
    from .download import (CANCELLED, DONE, ERROR, PAUSED, QUEUED, RUNNING,
                           Download, filename_from_url)
    from .errors import GrabItError

    FONT = "Segoe UI"
    POLL_MS = 300  # UI refresh / scheduler tick

    COLUMNS = [
        ("name", "Name", 300),
        ("size", "Size", 90),
        ("progress", "Progress", 150),
        ("speed", "Speed", 90),
        ("state", "State", 90),
    ]

    class Row:
        """One queue row: its config, tree id and (when live) its Download."""

        __slots__ = ("url", "dest", "threads", "sha256", "iid", "dl",
                     "requested")

        def __init__(self, url, dest, threads=1, sha256=None):
            self.url = url
            self.dest = dest
            self.threads = threads
            self.sha256 = sha256
            self.iid = None
            self.dl = None
            self.requested = True  # user wants this to run when a slot frees

        @property
        def state(self):
            if self.dl is not None:
                return self.dl.state
            return QUEUED if self.requested else PAUSED

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(WINDOW_TITLE)
            self.geometry("960x600")
            self.minsize(760, 460)

            self.theme = guiconfig.get_theme()
            self.rows = []                       # list[Row]
            self._img_refs = []
            self._last_clip = ""
            self._closing = False
            self.download_dir = (guiconfig.get_download_dir()
                                 or os.path.expanduser("~"))

            self._set_icon()
            self._build_menu()
            self._build_layout()
            self._apply_theme()
            self._load_saved_queue()

            self.protocol("WM_DELETE_WINDOW", self._on_close)
            self.after(POLL_MS, self._tick)

        # ---- assets / icon
        def _set_icon(self):
            try:
                ico = asset_path("grab-it.ico")
                if ico:
                    self.iconbitmap(ico)
                    return
            except Exception:
                pass
            try:
                png = asset_path("grab-it.png")
                if png:
                    img = tk.PhotoImage(file=png)
                    self._img_refs.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass  # icon is cosmetic; never block launch

        # ---- theming
        def _pal(self):
            return PALETTES[self.theme]

        def _apply_theme(self):
            p = self._pal()
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except Exception:
                pass
            self.configure(bg=p["bg"])
            style.configure(".", background=p["bg"], foreground=p["text"],
                            fieldbackground=p["entry"], bordercolor=p["border"],
                            font=(FONT, 10))
            style.configure("TFrame", background=p["bg"])
            style.configure("Sidebar.TFrame", background=p["surface"])
            style.configure("TLabel", background=p["bg"], foreground=p["text"])
            style.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
            style.configure("Header.TLabel", background=p["bg"], foreground=p["text"],
                            font=(FONT, 15, "bold"))
            style.configure("Brand.TLabel", background=p["surface"],
                            foreground=p["text"], font=(FONT, 13, "bold"))
            style.configure("Status.TLabel", background=p["surface"],
                            foreground=p["muted"])
            style.configure("Ok.TLabel", background=p["surface"], foreground=p["ok"])
            style.configure("Err.TLabel", background=p["surface"], foreground=p["err"])
            style.configure("TButton", background=p["surface"], foreground=p["text"],
                            bordercolor=p["border"], focuscolor=p["surface"],
                            padding=(10, 5))
            style.map("TButton",
                      background=[("active", p["trough"]), ("disabled", p["bg"])],
                      foreground=[("disabled", p["muted"])])
            style.configure("Accent.TButton", background=p["primary"],
                            foreground="#ffffff", padding=(12, 6))
            style.map("Accent.TButton",
                      background=[("active", p["primary_hi"]),
                                  ("disabled", p["border"])],
                      foreground=[("disabled", p["muted"])])
            style.configure("Toggle.TButton", background=p["surface"],
                            foreground=p["text"], padding=(8, 4))
            for name in ("TEntry", "TSpinbox"):
                style.configure(name, fieldbackground=p["entry"], foreground=p["text"],
                                insertcolor=p["text"], bordercolor=p["border"])
            style.configure("TCheckbutton", background=p["bg"], foreground=p["text"])
            style.map("TCheckbutton", background=[("active", p["bg"])])
            style.configure("Treeview", background=p["surface"],
                            fieldbackground=p["surface"], foreground=p["text"],
                            bordercolor=p["border"], rowheight=26)
            style.configure("Treeview.Heading", background=p["surface"],
                            foreground=p["muted"], font=(FONT, 10, "bold"))
            style.map("Treeview", background=[("selected", p["primary"])],
                      foreground=[("selected", p["sel_fg"])])
            style.configure("TScrollbar", background=p["surface"],
                            troughcolor=p["bg"], bordercolor=p["border"],
                            arrowcolor=p["text"])
            style.configure("TSeparator", background=p["border"])

        def toggle_theme(self):
            self.theme = "dark" if self.theme == "light" else "light"
            guiconfig.set_theme(self.theme)
            self._apply_theme()
            self._theme_btn.configure(
                text="☀ Light mode" if self.theme == "dark" else "🌙 Dark mode")

        # ---- menu
        def _build_menu(self):
            bar = tk.Menu(self)
            filem = tk.Menu(bar, tearoff=0)
            filem.add_command(label="Add URL…", command=self._focus_url)
            filem.add_command(label="Choose download folder…",
                              command=self._choose_folder)
            filem.add_separator()
            filem.add_command(label="Clear finished",
                              command=self._clear_finished)
            filem.add_separator()
            filem.add_command(label="Exit", command=self._on_close)
            bar.add_cascade(label="File", menu=filem)

            viewm = tk.Menu(bar, tearoff=0)
            viewm.add_command(label="Toggle dark mode", command=self.toggle_theme)
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="About", command=self._about)
            helpm.add_command(label="Open project page (quickopen.ai)",
                              command=lambda: open_with_default_app(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)

        # ---- layout
        def _build_layout(self):
            # top brand bar
            top = ttk.Frame(self, style="Sidebar.TFrame", padding=(12, 8))
            top.pack(fill="x", side="top")
            ttk.Label(top, text="GrabIt", style="Brand.TLabel").pack(side="left")
            ttk.Label(top, style="Status.TLabel",
                      text="  segmented downloads · resume · queue").pack(side="left")
            self._theme_btn = ttk.Button(
                top, style="Toggle.TButton", command=self.toggle_theme,
                text="☀ Light mode" if self.theme == "dark" else "🌙 Dark mode")
            self._theme_btn.pack(side="right")

            # add-URL controls
            add = ttk.Frame(self, style="TFrame", padding=(12, 10))
            add.pack(fill="x")
            ttk.Label(add, text="URL", width=5).pack(side="left")
            self.url_var = tk.StringVar()
            self.url_entry = ttk.Entry(add, textvariable=self.url_var)
            self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
            self.url_entry.bind("<Return>", lambda e: self._add_from_entry())
            ttk.Label(add, text="Segments").pack(side="left")
            self.threads_var = tk.StringVar(value="4")
            ttk.Spinbox(add, from_=1, to=16, width=4,
                        textvariable=self.threads_var).pack(side="left", padx=(4, 8))
            ttk.Button(add, text="Add", style="Accent.TButton",
                       command=self._add_from_entry).pack(side="left")

            # second controls row
            row2 = ttk.Frame(self, style="TFrame", padding=(12, 0))
            row2.pack(fill="x")
            ttk.Button(row2, text="Folder…",
                       command=self._choose_folder).pack(side="left")
            self.folder_lbl = ttk.Label(row2, style="Muted.TLabel",
                                        text=self._folder_text())
            self.folder_lbl.pack(side="left", padx=(6, 16))
            ttk.Label(row2, text="Max concurrent").pack(side="left")
            self.conc_var = tk.StringVar(value=str(guiconfig.get_concurrency()))
            ttk.Spinbox(row2, from_=1, to=16, width=4, textvariable=self.conc_var,
                        command=self._save_concurrency).pack(side="left", padx=(4, 16))
            self.clip_var = tk.BooleanVar(value=guiconfig.get_clipboard_watch())
            ttk.Checkbutton(row2, text="Watch clipboard for URLs",
                            variable=self.clip_var,
                            command=self._save_clipwatch).pack(side="left")

            # queue table
            mid = ttk.Frame(self, style="TFrame", padding=(12, 8))
            mid.pack(fill="both", expand=True)
            self.tree = ttk.Treeview(
                mid, columns=[c[0] for c in COLUMNS], show="headings",
                selectmode="browse")
            for cid, label, width in COLUMNS:
                self.tree.heading(cid, text=label)
                anchor = "w" if cid == "name" else "center"
                self.tree.column(cid, width=width, anchor=anchor,
                                 stretch=(cid == "name"))
            sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
            self.tree.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            self.tree.pack(side="left", fill="both", expand=True)

            # per-row actions
            act = ttk.Frame(self, style="TFrame", padding=(12, 4))
            act.pack(fill="x")
            ttk.Button(act, text="Pause", command=self._pause_sel).pack(side="left")
            ttk.Button(act, text="Resume", command=self._resume_sel).pack(side="left", padx=4)
            ttk.Button(act, text="Cancel", command=self._cancel_sel).pack(side="left")
            ttk.Button(act, text="Remove", command=self._remove_sel).pack(side="left", padx=4)
            ttk.Button(act, text="Open folder",
                       command=self._open_sel_folder).pack(side="left", padx=(12, 0))
            ttk.Button(act, text="Clear finished",
                       command=self._clear_finished).pack(side="right")

            # inline status / error bar
            bar = ttk.Frame(self, style="Sidebar.TFrame", padding=(12, 6))
            bar.pack(fill="x", side="bottom")
            self.status_lbl = ttk.Label(bar, text="Ready", style="Status.TLabel")
            self.status_lbl.pack(side="left")
            self.summary_lbl = ttk.Label(bar, text="", style="Status.TLabel")
            self.summary_lbl.pack(side="right")

        def _folder_text(self):
            d = self.download_dir or ""
            return d if len(d) < 60 else "…" + d[-57:]

        # ---- status helpers
        def _set_status(self, text, kind="idle"):
            p = self._pal()
            color = {"ok": p["ok"], "err": p["err"], "working": p["primary"]}.get(
                kind, p["muted"])
            self.status_lbl.configure(text=text, foreground=color)

        def _error(self, message):
            self._set_status("✕ " + message, kind="err")

        # ---- add / persistence
        def _focus_url(self):
            try:
                self.url_entry.focus_set()
            except Exception:
                pass

        def _choose_folder(self):
            d = filedialog.askdirectory(title="Choose download folder",
                                        initialdir=self.download_dir or ".")
            if d:
                self.download_dir = d
                guiconfig.set_download_dir(d)
                self.folder_lbl.configure(text=self._folder_text())

        def _save_concurrency(self):
            try:
                guiconfig.set_concurrency(int(self.conc_var.get()))
            except (ValueError, TypeError):
                pass

        def _save_clipwatch(self):
            guiconfig.set_clipboard_watch(bool(self.clip_var.get()))

        def _add_from_entry(self):
            url = self.url_var.get().strip()
            if not url:
                self._error("Enter a URL to add.")
                return
            if not _looks_like_url(url):
                self._error("That doesn't look like an http(s) URL.")
                return
            try:
                threads = max(1, int(self.threads_var.get()))
            except (ValueError, TypeError):
                threads = 1
            self._add_url(url, threads=threads)
            self.url_var.set("")

        def _add_url(self, url, threads=1, sha256=None, requested=True):
            dest = os.path.join(self.download_dir or ".", filename_from_url(url))
            row = Row(url, dest, threads=threads, sha256=sha256)
            row.requested = requested
            self.rows.append(row)
            row.iid = self.tree.insert("", "end", values=self._row_values(row))
            self._set_status(f"Added {os.path.basename(dest)}", kind="ok")
            self._persist()

        def _row_values(self, row):
            dl = row.dl
            name = os.path.basename(row.dest) or row.url
            if dl is not None:
                total = dl.total
                size = human_size(total) if total else "?"
                pct = dl.progress * 100.0
                bar = self._bar(dl.progress) if total else ""
                progress = f"{bar} {pct:4.0f}%" if total else "…"
                speed = human_size(dl.speed) + "/s" if dl.state == RUNNING else ""
                state = dl.state
            else:
                size, progress, speed = "?", "", ""
                state = row.state
            return (name, size, progress, speed, state)

        @staticmethod
        def _bar(frac, width=10):
            filled = int(width * max(0.0, min(1.0, frac)))
            return "█" * filled + "·" * (width - filled)

        def _refresh_row(self, row):
            if row.iid and self.tree.exists(row.iid):
                self.tree.item(row.iid, values=self._row_values(row))

        # ---- selection helpers
        def _selected_row(self):
            sel = self.tree.selection()
            if not sel:
                return None
            for row in self.rows:
                if row.iid == sel[0]:
                    return row
            return None

        def _pause_sel(self):
            row = self._selected_row()
            if not row:
                return
            row.requested = False
            if row.dl is not None:
                row.dl.pause()
            self._refresh_row(row)

        def _resume_sel(self):
            row = self._selected_row()
            if not row:
                return
            row.requested = True
            if row.dl is not None:
                row.dl.resume()
            self._refresh_row(row)

        def _cancel_sel(self):
            row = self._selected_row()
            if not row:
                return
            row.requested = False
            if row.dl is not None:
                row.dl.cancel()
            self._refresh_row(row)

        def _remove_sel(self):
            row = self._selected_row()
            if not row:
                return
            if row.dl is not None:
                row.dl.cancel()
            if row.iid and self.tree.exists(row.iid):
                self.tree.delete(row.iid)
            self.rows = [r for r in self.rows if r is not row]
            self._persist()

        def _open_sel_folder(self):
            row = self._selected_row()
            if row:
                open_in_file_manager(row.dest)

        def _clear_finished(self):
            keep = []
            for row in self.rows:
                if row.state in (DONE, CANCELLED):
                    if row.iid and self.tree.exists(row.iid):
                        self.tree.delete(row.iid)
                else:
                    keep.append(row)
            self.rows = keep
            self._persist()

        # ---- the scheduler / refresh tick
        def _tick(self):
            if self._closing:
                return
            try:
                self._schedule()
                self._refresh_all()
                self._watch_clipboard()
                self._update_summary()
            except Exception:
                pass
            self.after(POLL_MS, self._tick)

        def _schedule(self):
            try:
                limit = max(1, int(self.conc_var.get()))
            except (ValueError, TypeError):
                limit = 1
            running = sum(1 for r in self.rows
                          if r.dl is not None and r.dl.state == RUNNING)
            for row in self.rows:
                if running >= limit:
                    break
                if not row.requested:
                    continue
                if row.dl is None:
                    row.dl = Download(row.url, row.dest, threads=row.threads,
                                      sha256=row.sha256)
                    row.dl.start()
                    running += 1
                elif row.dl.state == PAUSED:
                    row.dl.resume()

        def _refresh_all(self):
            for row in self.rows:
                self._refresh_row(row)

        def _update_summary(self):
            done = sum(1 for r in self.rows if r.state == DONE)
            running = sum(1 for r in self.rows if r.state == RUNNING)
            total = len(self.rows)
            self.summary_lbl.configure(
                text=f"{running} active · {done}/{total} done")

        def _watch_clipboard(self):
            if not self.clip_var.get():
                return
            try:
                text = self.clipboard_get()
            except Exception:
                return
            if text == self._last_clip:
                return
            self._last_clip = text
            if _looks_like_url(text) and not any(r.url == text.strip()
                                                 for r in self.rows):
                self._add_url(text.strip(),
                              threads=max(1, int(self.threads_var.get() or 1)))

        # ---- persistence (via the tested queue module)
        def _persist(self):
            try:
                from .queue import DownloadQueue
                q = DownloadQueue()
                q.items = []
                for row in self.rows:
                    q.add(row.url, row.dest, threads=row.threads,
                          sha256=row.sha256)
            except Exception:
                pass

        def _load_saved_queue(self):
            try:
                from .queue import DownloadQueue
                q = DownloadQueue.load_from()
                for item in q.list():
                    if item.state in (DONE, CANCELLED):
                        continue
                    self._add_url(item.url, threads=item.threads or 1,
                                  sha256=item.sha256, requested=False)
            except Exception:
                pass

        # ---- About
        def _about(self):
            messagebox.showinfo(
                "About GrabIt",
                f"GrabIt {APP_VERSION}\n\n"
                "A fast, open-source, multi-connection download manager.\n"
                "Segmented downloads, pause/resume, a persistent queue,\n"
                "checksum verification and clipboard-watch.\n\n"
                "100% AI-built, open source, published on QuickOpen.\n"
                "Licensed under Apache-2.0.")

        # ---- shutdown
        def _on_close(self):
            self._closing = True
            for row in self.rows:
                if row.dl is not None:
                    try:
                        row.dl.cancel()
                    except Exception:
                        pass
            self._persist()
            self.destroy()

    return App


def main():
    """Entry point: build the root window and run.  Degrades on headless hosts.

    Importing this module does nothing; only this function creates a Tk root.
    With no display (e.g. a server) it prints a friendly note and returns 0
    instead of raising, so the frozen exe never crashes on a headless box.
    """
    try:
        import tkinter as tk
    except Exception as exc:  # tkinter missing entirely
        print(f"{APP_NAME}: a graphical environment with tkinter is required "
              f"to run the GUI ({exc}).")
        return 0

    if os.name != "nt" and not os.environ.get("DISPLAY") \
            and sys.platform != "darwin":
        print(f"{APP_NAME}: no graphical display available — cannot start the "
              f"GUI here. This app is intended for the Windows desktop.")
        return 0

    try:
        App = build_app()
        app = App()
    except tk.TclError as exc:
        print(f"{APP_NAME}: no graphical display available — cannot start the "
              f"GUI here ({exc}). This app is intended for the Windows desktop.")
        return 0
    except Exception as exc:
        print(f"{APP_NAME}: could not start the GUI ({exc}).")
        return 1

    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
