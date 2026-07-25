# Architecture

Read this before sending a PR. For a deeper, line-number-level map (useful if
you're picking this codebase up cold or working with an AI coding assistant),
see [`info.md`](../info.md) at the repo root — this file is the shorter,
human-facing version.

## Two processes, one config file

- **`main.py`** — the actual dictation engine. Runs as a system-tray app
  (`pystray`), owns the global hotkey loop, audio capture, Whisper
  transcription, Ollama cleanup, and text injection.
- **`dashboard/`** — a separate process (`dashboard.py`, a `pywebview` app
  over `index.html`/`styles.css`/`app.js`), spawned on demand from the tray's
  "Dashboard" item. It **never imports `main.py`** — that would drag
  `faster_whisper`/CUDA into a process that only needs to render a settings
  UI.

They talk to each other through files, not IPC:

- `config.json` — the dashboard reads and writes it; `main.py` polls it every
  2s (`_watch_config_file`) and hot-reloads a known set of keys (`SYNC_KEYS`)
  into the live session without a restart.
- `history.jsonl` — appended to by `main.py` after every dictation, read by
  the dashboard for stats, Insights, and the recent-dictations list.
- `runtime.json` — written by `main.py` (`_write_runtime()`) so the dashboard
  can show live Whisper device/load status and support undo/copy-last without
  being in the same process.

**Why two processes and not one?** Two GUI event loops (Tk's `mainloop` for
the status pill, `webview.start()` for the dashboard) can't share a thread.
Rather than fight that, the dashboard is a fully separate process that
happens to agree on a file-based protocol with the engine.

## Threading inside `main.py`

- `hotkey_loop()` and `process()` (transcribe → clean → inject) run on
  background threads.
- Anything touching Tk widgets or shared session state runs on the **Tk main
  thread only**. Background work pushes onto `self.ui_q`, drained by
  `poll_ui()` every 100ms — don't mutate shared state directly from a worker
  thread.

## Platform split

Every OS-specific call (text injection, foreground-app detection, autostart,
sound cues, clipboard, "open folder") is branched behind `IS_WIN`/`IS_MAC`
(`sys.platform`) in both `main.py` and `dashboard/dashboard.py`. See the
"macOS support" table in [`info.md`](../info.md) for the full map of what
differs per platform and why.

## Stack

- Python 3.12, mostly single-file (`main.py`).
- STT: `faster-whisper` (CUDA on Windows if available, CPU otherwise).
- Cleanup: local Ollama over plain `urllib` HTTP calls to `localhost:11434`.
- Text injection: Win32 `SendInput` via `ctypes` on Windows (never touches
  the clipboard); AppleScript/System Events on macOS.
- UI: `pywebview` (primary dashboard), `tkinter` (status pill + a Tk
  dashboard kept only as a fallback if the webview one fails to launch).
- Persistence: plain JSON config + a JSONL history log. No database.

## Making a change

- New config key that should hot-reload without a restart? Add it to both
  `DEFAULTS` and `SYNC_KEYS` in `main.py`.
- New dashboard setting? It almost always round-trips through
  `Api.set_config`/`patch()` in `app.js` rather than needing a new bridge
  method — only add a new `Api` method if there's a real side effect
  (writing a registry key, opening a URL, computing Insights).
- Touching CSS/JS in the dashboard? Bump the `?v=N` query string on the
  `<link>`/`<script>` tags in `index.html` — WebView2 disk-caches those
  files across relaunches, so a change that "does nothing" is very often
  just a stale cache, not a bug in the change itself.
