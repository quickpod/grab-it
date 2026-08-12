# GrabIt

A fast, **offline**, **100% open-source** download manager for Windows. Nothing is uploaded anywhere. Built entirely by AI with human testing and guidance, and published on [QuickOpen](https://quickopen.ai/projects/grab-it).

> **100% AI-built and open source.** Apache-2.0.

## What it does

Download files faster with multi-connection segmented transfers, pause/resume, a queue with scheduling, checksum verification, and batch downloads from a list. Clipboard-watch adds URLs automatically. A clean, open-source download accelerator.

## Install

Download **`GrabIt-Setup.exe`** from the [QuickOpen page](https://quickopen.ai/projects/grab-it) or the [GitHub release](https://github.com/quickpod/grab-it/releases/latest) and double-click it. It installs per-user, adds Desktop and Start Menu shortcuts, and can optionally trust the QuickOpen Root CA. Authenticode-signed by the QuickOpen Code Signing CA — verify at [quickopen.ai/trust](https://quickopen.ai/trust).

## Run from source

```sh
pip install -r requirements.txt
python grab_it_app.py          # GUI
python -m grabit --help    # CLI
```


## Features

- **Multi-connection segmented downloads.** When a server advertises
  `Accept-Ranges: bytes`, GrabIt splits the file into N byte ranges and pulls
  them in parallel worker threads straight into the right offsets of a `.part`
  file, then atomically renames it into place. Otherwise it falls back to a
  clean single stream.
- **Pause / resume / cancel.** Every download can be paused and resumed live;
  an interrupted single-stream download resumes from its `.part` via a `Range`
  request instead of starting over.
- **Persistent queue.** Add, remove, reorder and run downloads with a
  concurrency limit. The queue is saved as JSON in the config dir and reloads
  on restart (anything mid-flight is re-queued).
- **Checksum verification.** Pass an expected SHA-256 and GrabIt verifies the
  finished file, raising on a mismatch (and removing the bad file).
- **Batch from a list or page.** Extract direct file URLs from a pasted list or
  a simple HTML page (generic `<a href>` scanning — no site-specific scraping).
- **Clipboard-watch.** Optionally auto-add copied URLs to the queue.
- **Clean tkinter GUI.** A queue table (name, size, progress, speed, state)
  with per-row controls, a dark-mode QuickOpen palette, and fully threaded live
  progress. Pure standard library — no third-party GUI dependencies.

## CLI examples

```sh
# Download one file with 8 parallel segments and verify its checksum
python -m grabit get https://example.com/big.iso big.iso --threads 8 \
    --sha256 <expected-hex>

# Download to a folder (filename is taken from the URL)
python -m grabit get https://example.com/song.mp3 ./downloads/

# Batch-download every URL found in a list or HTML file into a folder
python -m grabit batch urls.txt ./downloads --threads 4

# List the persisted download queue
python -m grabit queue
```

The CLI prints a live progress bar to the terminal and the final path, and
exits non-zero with a clean `error: …` message (never a traceback) on failure.

## License

Apache-2.0 — see [LICENSE](LICENSE). A 100% AI-built project published on QuickOpen.
