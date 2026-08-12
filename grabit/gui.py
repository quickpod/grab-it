#!/usr/bin/env python3
r"""GrabIt -- an Aura (QuickOpen design system) GUI on top of the ``grabit``
download engine.

A single Aura window: a **Downloads** section with an add-URL bar, the queue
table (name, size, progress, speed, state) with per-row Pause / Resume /
Cancel / Remove controls and an overall progress bar; a **Settings** section
(download folder, concurrency, clipboard-watch); and an **About** section.
Every download runs on the tested :class:`grabit.Download` engine on
background threads; the UI refreshes on a light ``self.after`` poll and never
blocks.

Design goals baked in here (mirrors the QuickOpen house style):
  * built on the vendored ``grabit/aura.py`` design system, which layers the
    quickopen.ai look (deep space + light) over CustomTkinter.  Runtime deps:
    ``customtkinter`` (+ ``darkdetect``) — declared in requirements.txt; the
    PyInstaller build adds ``--collect-all customtkinter``.
  * Importing this module does nothing.  Only :func:`main` builds a root
    window, and it degrades gracefully (prints a message, returns 0) with no
    display or with customtkinter missing.
  * Frozen-exe safe: bundled assets are resolved via ``sys._MEIPASS`` / the
    exe directory when ``sys.frozen`` is set -- never ``__file__``.
  * Downloads run on background threads; state is polled with ``self.after``
    and errors surface in the Aura status bar, never as a traceback.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys

# NOTE: tkinter/customtkinter are imported lazily inside main()/build_app so
# that merely importing this module (e.g. during packaging or on a headless CI
# box) never fails and has no side effects.

APP_NAME = "GrabIt"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "GrabIt — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai"
ACCENT = "#17914b"      # UI-accent registry (ui/aurakit/README.md): grab-it


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
# The app (built lazily; tkinter/customtkinter imported only inside build_app)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to live GUI imports.

    Kept inside a function so this module imports cleanly without a display
    (and without customtkinter installed).
    """
    import tkinter as tk
    from tkinter import ttk, filedialog
    import customtkinter as ctk

    from . import aura, guiconfig
    from .download import (CANCELLED, DONE, PAUSED, QUEUED, RUNNING,
                           Download, filename_from_url)

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

    class App(aura.AuraApp):
        def __init__(self):
            super().__init__(
                title=WINDOW_TITLE, app_name=APP_NAME, accent=ACCENT,
                theme=guiconfig.get_theme(),
                icon_png=asset_path("grab-it.png"), version=APP_VERSION,
                tagline="download manager",
                on_theme_change=guiconfig.set_theme,
                size=(1080, 680), min_size=(900, 560))

            self.rows = []                       # list[Row]
            self._img_refs_gui = []
            self._last_clip = ""
            self._closing = False
            self.download_dir = (guiconfig.get_download_dir()
                                 or os.path.expanduser("~"))

            # tk variables shared across (lazily built) sections
            self.threads_var = tk.StringVar(value="4")
            self.conc_var = tk.StringVar(value=str(guiconfig.get_concurrency()))
            self.clip_var = tk.BooleanVar(value=guiconfig.get_clipboard_watch())

            self._set_icon()
            self._build_menu()
            self.add_section("queue", "Downloads", "↧", self._build_queue)
            self.add_section("settings", "Settings", "⚙", self._build_settings)
            self.add_section("about", "About", "◉", self._build_about)
            self.show("queue")
            self._load_saved_queue()
            self.set_status("Ready")

            self.protocol("WM_DELETE_WINDOW", self._on_close)
            self.after(POLL_MS, self._tick)

        # ---- assets / icon
        def _set_icon(self):
            try:
                ico = asset_path("grab-it.ico")
                if ico and os.name == "nt":
                    self.iconbitmap(ico)
                    return
            except Exception:
                pass
            try:
                png = asset_path("grab-it.png")
                if png:
                    img = tk.PhotoImage(file=png)
                    self._img_refs_gui.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass  # icon is cosmetic; never block launch

        # ---- menu (native menus stay; theme lives in the sidebar toggle too)
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
            viewm.add_command(
                label="Toggle dark mode",
                command=lambda: self.set_theme(
                    "light" if self.theme == "dark" else "dark"))
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="About",
                              command=lambda: self.show("about"))
            helpm.add_command(label="Open project page (quickopen.ai)",
                              command=lambda: open_with_default_app(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)

        # =================================================================
        # Downloads section (the queue)
        # =================================================================
        def _build_queue(self, frame):
            # add-URL bar
            bar = ctk.CTkFrame(frame, fg_color="transparent")
            bar.pack(fill="x", pady=(0, 12))
            # no textvariable: CTkEntry placeholders only work without one
            self.url_entry = aura.AuraEntry(
                bar, placeholder="https:// URL to download…")
            self.url_entry.pack(side="left", fill="x", expand=True)
            self.url_entry.bind("<Return>", lambda _e: self._add_from_entry())
            ctk.CTkLabel(bar, text="Segments",
                         font=aura.font()).pack(side="left", padx=(12, 6))
            ttk.Spinbox(bar, from_=1, to=16, width=4,
                        textvariable=self.threads_var).pack(side="left",
                                                            padx=(0, 12))
            aura.AuraButton(bar, "Add",
                            command=self._add_from_entry).pack(side="left")

            # queue table (per-row text progress bar stays; style_ttk skins it)
            body = ctk.CTkFrame(frame, fg_color="transparent")
            body.pack(fill="both", expand=True)
            self.tree = ttk.Treeview(
                body, columns=[c[0] for c in COLUMNS], show="headings",
                selectmode="browse")
            for cid, label, width in COLUMNS:
                self.tree.heading(cid, text=aura.spaced(label), anchor="w")
                anchor = "w" if cid == "name" else "center"
                self.tree.column(cid, width=width, anchor=anchor,
                                 stretch=(cid == "name"))
            sb = ttk.Scrollbar(body, orient="vertical",
                               command=self.tree.yview)
            self.tree.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            self.tree.pack(side="left", fill="both", expand=True)

            # per-row actions
            act = ctk.CTkFrame(frame, fg_color="transparent")
            act.pack(fill="x", pady=(10, 0))
            aura.AuraButton(act, "Pause", kind="secondary",
                            command=self._pause_sel).pack(side="left")
            aura.AuraButton(act, "Resume", kind="secondary",
                            command=self._resume_sel).pack(side="left",
                                                           padx=(8, 0))
            aura.AuraButton(act, "Cancel", kind="secondary",
                            command=self._cancel_sel).pack(side="left",
                                                           padx=(8, 0))
            aura.AuraButton(act, "Remove", kind="danger",
                            command=self._remove_sel).pack(side="left",
                                                           padx=(8, 0))
            aura.AuraButton(act, "Clear finished", kind="ghost",
                            command=self._clear_finished).pack(side="right")

            # overall progress (0..1 — aggregate across the whole queue)
            prog = ctk.CTkFrame(frame, fg_color="transparent")
            prog.pack(fill="x", pady=(14, 0))
            aura.SectionLabel(prog, "Overall progress").pack(anchor="w")
            self.overall = aura.ProgressBar(prog)
            self.overall.set(0)
            self.overall.pack(fill="x", pady=(6, 4))
            self.summary_lbl = aura.Caption(prog, "0 active · 0/0 done")
            self.summary_lbl.pack(anchor="w")

            # status-bar actions (built once; the section builder runs once)
            aura.AuraButton(self.statusbar.actions, "Open folder",
                            kind="secondary", height=30,
                            command=self._open_sel_folder).pack(side="left")

        # =================================================================
        # Settings section
        # =================================================================
        def _build_settings(self, frame):
            card = aura.Card(frame, title="Downloads")
            card.pack(fill="x")

            row1 = ctk.CTkFrame(card.body, fg_color="transparent")
            row1.pack(fill="x", pady=(0, 10))
            aura.AuraButton(row1, "Download folder…", kind="secondary",
                            command=self._choose_folder).pack(side="left")
            self.folder_lbl = ctk.CTkLabel(row1, font=aura.font(),
                                           text=self._folder_text(),
                                           anchor="w")
            self.folder_lbl.pack(side="left", padx=(10, 0))

            row2 = ctk.CTkFrame(card.body, fg_color="transparent")
            row2.pack(fill="x", pady=(0, 10))
            ctk.CTkLabel(row2, text="Max concurrent downloads",
                         font=aura.font()).pack(side="left", padx=(0, 8))
            ttk.Spinbox(row2, from_=1, to=16, width=4,
                        textvariable=self.conc_var,
                        command=self._save_concurrency).pack(side="left")

            ctk.CTkCheckBox(card.body, text="Watch clipboard for URLs",
                            variable=self.clip_var,
                            command=self._save_clipwatch,
                            font=aura.font()).pack(anchor="w")
            aura.Caption(
                card.body,
                "Copied http(s) links are added to the queue "
                "automatically while the app is open.").pack(
                anchor="w", pady=(4, 0))

        # =================================================================
        # About section
        # =================================================================
        def _build_about(self, frame):
            card = aura.Card(frame, title="About GrabIt")
            card.pack(fill="x")
            aura.Heading(card.body, APP_NAME).pack(anchor="w")
            aura.Caption(card.body, f"Version {APP_VERSION}").pack(
                anchor="w", pady=(0, 10))
            ctk.CTkLabel(
                card.body, font=aura.font(), justify="left", anchor="w",
                wraplength=520,
                text="A fast, open-source, multi-connection download manager. "
                     "Segmented downloads, pause/resume, a persistent queue, "
                     "checksum verification and clipboard-watch.\n\n"
                     "100% AI-built, open source, published on "
                     "QuickOpen.").pack(anchor="w")
            aura.Caption(card.body,
                         "Licensed under Apache-2.0. Built on "
                         "CustomTkinter (MIT).").pack(anchor="w",
                                                      pady=(10, 4))
            aura.AuraButton(card.body, "Project page: quickopen.ai",
                            kind="ghost",
                            command=lambda: open_with_default_app(
                                PROJECT_URL)).pack(anchor="w", pady=(6, 0))

        def _folder_text(self):
            d = self.download_dir or ""
            return d if len(d) < 60 else "…" + d[-57:]

        # ---- add / persistence
        def _focus_url(self):
            try:
                self.show("queue")
                self.url_entry.focus_set()
            except Exception:
                pass

        def _choose_folder(self):
            d = filedialog.askdirectory(title="Choose download folder",
                                        initialdir=self.download_dir or ".")
            if d:
                self.download_dir = d
                guiconfig.set_download_dir(d)
                try:
                    self.folder_lbl.configure(text=self._folder_text())
                except Exception:
                    pass  # settings section may not be built yet

        def _save_concurrency(self):
            try:
                guiconfig.set_concurrency(int(self.conc_var.get()))
            except (ValueError, TypeError):
                pass

        def _save_clipwatch(self):
            guiconfig.set_clipboard_watch(bool(self.clip_var.get()))

        def _add_from_entry(self):
            url = self.url_entry.get().strip()
            if not url:
                self.set_error("Enter a URL to add.")
                return
            if not _looks_like_url(url):
                self.set_error("That doesn't look like an http(s) URL.")
                return
            try:
                threads = max(1, int(self.threads_var.get()))
            except (ValueError, TypeError):
                threads = 1
            self._add_url(url, threads=threads)
            self.url_entry.delete(0, "end")

        def _add_url(self, url, threads=1, sha256=None, requested=True):
            dest = os.path.join(self.download_dir or ".",
                                filename_from_url(url))
            row = Row(url, dest, threads=threads, sha256=sha256)
            row.requested = requested
            self.rows.append(row)
            row.iid = self.tree.insert("", "end",
                                       values=self._row_values(row))
            self.set_success(f"Added {os.path.basename(dest)}")
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
            else:
                open_in_file_manager(self.download_dir or ".")

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
            # overall bar: mean per-row fraction, 0..1 (aura scale)
            if total:
                frac = 0.0
                for r in self.rows:
                    if r.state == DONE:
                        frac += 1.0
                    elif r.dl is not None:
                        frac += max(0.0, min(1.0, r.dl.progress))
                self.overall.set(frac / total)
            else:
                self.overall.set(0)

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
    With no display (e.g. a server) or without customtkinter installed, it
    prints a friendly note and returns 0 instead of raising, so the frozen exe
    never crashes on a headless box.
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
    except ImportError as exc:
        print(f"{APP_NAME}: the GUI needs the 'customtkinter' package "
              f"({exc}). Install it with:  pip install customtkinter")
        return 0
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
