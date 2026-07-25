# Dictator — agent-facing overview

Read this file first instead of `main.py` when picking up work on this app cold.
It's a map, not a copy — line numbers point into `main.py` (~2450 lines) so you
can jump straight to the right spot instead of reading the whole thing.

This app is **archived/standalone** — it lives at
`E:\Completed Projects\Dictator`, fully self-contained, unrelated to any other
project on this machine. Treat changes here as maintenance unless a session
explicitly says otherwise.

## What it is

Local voice-dictation tool for **Windows and macOS** (Windows is the proven
daily driver; macOS support was added in this session and is best-effort —
see "macOS support" below). Hold a hotkey (Ctrl+Win / Ctrl+Cmd, configurable),
speak, release — audio is transcribed on-device (Whisper), cleaned up by a
local LLM (Ollama), and typed into whatever window has focus via simulated
keystrokes. No cloud, no clipboard use, nothing leaves the machine. Runs as a
system-tray app with a small on-screen status pill and a settings/stats
dashboard. Positioned as a free, local-first alternative to Wispr Flow, with
one thing most alternatives skip: an actual AI cleanup pass (filler-word
removal, self-correction resolution, grammar fixes), not just raw transcript.

## Dashboard architecture (read before touching UI)

**One dashboard** — `dashboard/` is a self-contained pywebview app
(`index.html` + `styles.css` + `app.js`). It runs as its OWN process, spawned
on demand by the tray "Dashboard" item (`App.launch_dashboard()` →
`pythonw dashboard/dashboard.py` on Windows, `python3 dashboard/dashboard.py`
on macOS). It never imports `main.py` (would drag faster_whisper/CUDA in).
It talks to the running Dictator through files:

- reads/writes `config.json` — `main.py`'s `_watch_config_file` (2s poll)
  syncs `SYNC_KEYS` into the live app and reloads Whisper on `model_size`.
- reads `history.jsonl` for stats/insights + the recent list (polls mtime).
- reads `runtime.json` — written by `main.py`'s `_write_runtime()` — for live
  whisper device/load status, the enabled flag, and last-injected text
  (undo/copy-last). If absent, the health panel shows "unknown".
- `dashboard/_preview.html` is a browser mock harness (sample data, no
  pywebview) for iterating on the look. Dev-only, never used at runtime.
- **Two GUI event loops can't share the main thread** — that's why the
  dashboard is a separate process, not an in-process webview. Don't try to
  merge them.

There is also a **Tk dashboard (fallback only)** — `App.open_dashboard()` in
`main.py`, used only if `dashboard/dashboard.py` is missing or fails to
launch. It does not have the current design system or the newer tabs
(Insights/About) — it's a safety net, not a second UI to keep in parity.

### Pages (4 tabs: Home / Insights / Settings / About)

- **Home** — hero stat grid (dictations, words spoken/typed, avg WPM, day
  streak), quick actions (undo last, clean clipboard), searchable recent list.
- **Insights** — one continuous page (no sub-tabs): WPM gauge + hero stats,
  desktop-usage bars (which apps you dictate into), a GitHub-style streak
  calendar (18 weeks, `--highlight` green by default), tone distribution +
  hour-of-day histogram.
- **Settings** — categorized: Voice & Recognition, Personalization, Behavior,
  Data & Privacy, Appearance, System.
- **About** — what the app is (1 paragraph) + who built it (3 paragraphs,
  Daksh — not "IntellectDaksh" in prose), with GitHub/LinkedIn/Instagram
  buttons (`Api.open_url`, opens in the system browser, not the webview).

### Design system / color tokens (`dashboard/styles.css`)

Black-and-white base with a single accent color used sparingly, plus a
separate "highlight" color reserved for positive/streak states (default
green, GitHub-heatmap-style). Both are user-configurable in
Settings → Appearance:

- `--accent` (default `#f2f1ee` dark theme / `#101010` light theme) — nav
  active state, primary buttons, data-viz bars. User override:
  `cfg["accent_color"]`.
- `--highlight` (default `#22c55e`) — streak calendar cells, "ok" status
  dots/health states. User override: `cfg["highlight_color"]`.
- The **logo/brand-mark is fixed pure white on black** (`#ffffff` literal,
  not tied to either variable) — deliberately not user-recolorable, so the
  brand mark stays recognizable regardless of accent/highlight choice.
- `applyTheme(cfg)` in `app.js` sets both CSS custom properties inline;
  `null` means "use the CSS default for the current theme."
- **WebView2 disk-caches `styles.css`/`app.js` across process relaunches** —
  bump the `?v=N` query string on both `<link>`/`<script>` tags in
  `index.html` (and `_preview.html`) after any CSS/JS change, or the running
  window will silently keep serving the old file even after a fresh launch.

### Logo (`assets/icon/gen_icon.py`)

Single smooth waveform/sine-trace stroke (audio, not Wi-Fi arcs — went
through several rejected concepts: equalizer bars, bold-D letterform,
concentric arcs, abstract-D swoosh). Black rounded-square badge, pure white
stroke. Same path used in: `assets/icon/dictator.ico` (window/taskbar icon),
`dashboard/dictator.ico` (copy, must be kept in sync — see below), the
sidebar `.brand-mark` SVG (`index.html`, `_preview.html`), the About page
`markSvg` constant in `app.js`, and `main.py`'s `make_icon_image()` (tray
icon, drawn independently via PIL — same math, kept in sync by hand).

**To regenerate the icon after a design change**: edit
`assets/icon/gen_icon.py`, run it from the project root with the venv
Python (it writes `dictator.ico`/`dictator-512.png`/`dictator-disabled-64.png`
into the *current directory* — move them into `assets/icon/` if run from
root), then `cp assets/icon/dictator.ico dashboard/dictator.ico`. Also update
`main.py`'s `make_icon_image()` stroke color to match if it changed.

## Features

- **Hold-to-dictate**: hold the hotkey while speaking, release to type.
- **Hands-free modes**: double-tap (hold mode) or single-tap (toggle mode,
  `cfg["hotkey_mode"]`) starts an open-ended recording until tapped again,
  with optional **silence auto-stop** (`silence_auto_stop`,
  `silence_threshold`, `silence_duration_s` — RMS-based, three presets in
  the dashboard: sensitive/balanced/relaxed).
- **Configurable hotkey**: dashboard preset dropdown or a "Capture..." button
  that records any key combo live. **Hotkey profiles**: named presets
  (`cfg["profiles"]`) swapping hotkey/model/language together.
- **Language**: `cfg["language"]`, 12 languages + auto-detect, passed to
  faster-whisper.
- **Local STT**: faster-whisper (base.en / small.en / medium.en), GPU (CUDA,
  Windows only) if available, CPU fallback everywhere.
- **Local cleanup LLM**: Ollama (`ollama_model`, "auto" picks first available
  from `PREFERRED_MODELS`) strips filler words, resolves self-corrections,
  fixes grammar. Falls back to the raw transcript (optionally
  auto-punctuated) if Ollama fails/times out (`ollama_timeout_s`, currently
  12s — see gotcha below).
- **App-aware tone**: casual/formal/verbatim per focused app — hardcoded
  default sets (`CASUAL_EXES`/`FORMAL_EXES`/`VERBATIM_EXES`, now covering
  both Windows `.exe` names and macOS app names) plus a per-user editable
  override list (`cfg["tone_overrides"]`).
- **Voice commands**: trailing "...make it formal"/"...make it casual"
  overrides tone for that one dictation and strips itself before typing;
  "new line"/"new paragraph"/"bullet point" become real formatting.
- **Snippets/macros**: exact-match trigger phrase → canned expansion text
  (`cfg["snippets"]`).
- **Instant mode**: transcripts under 6 words skip the LLM round-trip.
- **Review before typing**: cleaned text over 1000 characters triggers a
  review window (edit or cancel) if `review_before_typing` is on.
- **Undo last dictation**: backspaces out exactly what was last typed.
- **Sound cues**: optional start/stop chime (`sound_enabled` —
  `winsound.MessageBeep` on Windows, `afplay` a system sound on macOS).
- **Redaction**: word/phrase list (`cfg["redact_patterns"]`) scrubbed to
  `[redacted]` before anything is written to `history.jsonl` — the raw text
  is still typed, only what's persisted to disk is scrubbed.
- **Custom vocabulary**: names/brand words fed to Whisper + the cleanup
  model, editable as a chip list in the dashboard.
- **History + Insights**: dictations optionally logged to `history.jsonl`.
  Home shows headline stats + recent list; Insights adds a streak calendar,
  app-usage breakdown, tone distribution, and an hourly histogram — all
  computed in `Api._insights()` (dashboard.py), nothing pre-aggregated on
  disk.
- **Storage/memory controls**: on-disk size of Whisper cache / Ollama models
  / venv / history, with buttons to clear caches, open the history folder,
  change where history is saved, toggle logging, or purge everything.
- **Health panel + retry**: enabled/whisper/ollama/mic state; "Retry" button
  force-rechecks; auto-backoff rechecks every 5s while Ollama is unreachable.
- **Appearance**: dark/light theme, separate accent-color and highlight-color
  pickers (see design system above), auto theme by time of day.
- **Start on login**: Windows Run-key (`winreg`) or macOS LaunchAgent plist
  (`~/Library/LaunchAgents/com.uideas.dictator.plist`), same toggle in
  Settings → System either way.
- **Tray icon** reflects enabled/disabled state (greyed + red slash when off).
- **Tray menu**: enable/disable, mic picker, Whisper model picker, status-bar
  toggle, history logging toggle, start-on-login, open config folder, quit.

## Stack

- **Language**: Python 3.12, mostly single-file (`main.py`), see
  `requirements.txt`.
- **UI**: pywebview dashboard (primary), `tkinter` (status pill + Tk
  fallback dashboard), `pystray` (system tray).
- **STT**: `faster-whisper` (via `Transcriber`).
- **Cleanup LLM**: local Ollama HTTP API (`http://localhost:11434`), plain
  `urllib` calls.
- **Audio capture**: `sounddevice` (cross-platform, needs PortAudio — Homebrew
  installs it on macOS).
- **Global hotkey detection**: `keyboard` library, polled in a loop (not an
  OS-level hook).
- **Text injection**: Windows — raw Win32 `SendInput` via `ctypes`, never
  touches the clipboard. macOS — AppleScript (`osascript` driving System
  Events' `keystroke`/`key code`), one subprocess call per dictation, not per
  keystroke; requires the process to be granted Accessibility + Input
  Monitoring permission (System Settings → Privacy & Security).
- **Persistence**: plain JSON config + JSONL history log. No database.

## macOS support (added this session — best-effort, unverified on real hardware)

Every Windows-only API call in `main.py` and `dashboard/dashboard.py` is now
branched behind `IS_WIN`/`IS_MAC` (`sys.platform`). What changes per platform:

| Concern | Windows | macOS |
|---|---|---|
| App data dir | `%APPDATA%\Dictator` | `~/Library/Application Support/Dictator` |
| Text injection | `SendInput` (ctypes/user32) | `osascript` → System Events `keystroke`/`key code` |
| Foreground app | `GetForegroundWindow` + `QueryFullProcessImageNameW` | `osascript` → System Events frontmost process name |
| Autostart | `HKCU\...\Run` registry key | LaunchAgent plist + `launchctl load/unload` |
| Sound cue | `winsound.MessageBeep` | `afplay` a `/System/Library/Sounds/*.aiff` |
| Open folder/URL | `os.startfile` | `open` (subprocess) |
| Clipboard (dashboard "clean clipboard") | `clip` / `Get-Clipboard` | `pbcopy` / `pbpaste` |
| Launcher | `Dictator.bat` → `pythonw.exe` | `Dictator.command` → `python3` (no windowless/console binary split on macOS, so `sys.executable` is used directly) |
| CUDA (GPU Whisper) | Yes if available | No — CPU only, `nvidia-cublas/cudnn` are excluded from the install via `sys_platform == "win32"` markers in `requirements.txt` |
| Tk font loading (GDI private fonts) | Yes | No-op — Tk fallback dashboard uses system fonts on macOS (it's a fallback, not the primary UI, so this is a low-priority gap) |
| Dock/window icon | Set via `webview.start(icon=...)` on the edgechromium backend | Not set — no `.app` bundle here to carry an `Info.plist` icon; shows the generic Python icon. Known cosmetic gap. |

**Caveats to flag to a user before they rely on the macOS path**:

1. **Never run on real macOS hardware** — this was built by branching every
   Windows-only call to a macOS equivalent and reasoning through the APIs,
   not by testing on a Mac. Treat it as a first cut; expect to debug the
   Accessibility-permission flow and the hotkey capture in particular.
2. The `keyboard` library's macOS support is the shakiest link — it needs
   Accessibility + Input Monitoring permission and has known quirks catching
   modifier-only combos (like the default Ctrl+Cmd) reliably. If hotkey
   detection misbehaves on macOS, this is the first place to look.
3. `pywebview`'s Cocoa backend needs `pyobjc-framework-Cocoa` (in
   `requirements.txt` behind a `sys_platform == "darwin"` marker) — first
   launch on macOS may be slower while that resolves.

## Where things live in `main.py` (~2450 lines)

Sections are marked with `# ---- name` banner comments; grep for `^# ----`.
Line numbers are approximate — the file grows; use them as a starting search
point, not an exact index.

| Section | Around line | What's there |
|---|---|---|
| platform flags | ~20 | `IS_WIN`/`IS_MAC`, conditional `winreg`/`winsound` imports |
| config | ~50 | `DEFAULTS`, `APP_DIR` (platform-branched), `load_config`/`save_config` |
| text injection | ~200 | `send_text_keystrokes`/`send_backspaces`/`send_noop_key` — Windows `SendInput` branch + macOS `osascript` branch |
| hotkey | ~300 | `HOTKEY_PRESETS`, `hotkey_down(cfg)`, `win_pressed()` (Cmd on macOS) |
| app-aware tone | ~330 | `foreground_app()` (platform-branched), `tone_for(exe, title, cfg)` |
| text shaping | ~380 | `apply_commands`, `apply_voice_command`, `basic_punctuate`, `quick_clean`, `expand_snippet`, `redact` |
| STT | ~420 | `Transcriber` class |
| Ollama cleanup | ~470 | `resolve_ollama_model`, `ollama_cleanup` |
| injection / history | ~490–520 | `inject_text`, `log_history` |
| start on login | ~525 | Registry (Windows) / LaunchAgent plist (macOS) — `set_start_on_login` |
| overlay pill | ~545+ | `Overlay` class — fade, level meter, snippet-preview text |
| **App class** | ~650–2400 | hotkey_loop/process pipeline, dashboard (Tk fallback), tray menu, `run()` |
| selftest | end | `selftest_cleanup()` — `python main.py --selftest` |

### Threading model (important — read before touching dashboard/session code)

- `hotkey_loop` and `process()` run on background threads.
- Anything touching Tk widgets or shared session/stat state must run on the
  **Tk main thread**. Bridge is `self.ui_q`, drained by `poll_ui()` every
  100ms. `process()` pushes `self._record_dictation(...)` onto `ui_q` instead
  of mutating state directly.
- The webview dashboard does not poll on a timer for most content — refreshes
  on dictation recorded, search keystroke, or a control action. `refreshLive()`
  diffs against `lastLiveJSON` before re-rendering to avoid flicker on its
  periodic poll.

## Folder layout (`E:\Completed Projects\Dictator`)

- `main.py`, `Dictator.bat` (Windows launcher), `Dictator.command` (macOS
  launcher), `README.md`, `requirements.txt`, `update.md`, `info.md` — the
  live app.
- `.venv/` — private Python env.
- `dashboard/` — the pywebview dashboard (see "Dashboard architecture").
  `dashboard.py` (pywebview host + Python bridge, platform-branched same as
  main.py), `index.html`, `styles.css`, `app.js`, `_preview.html` (browser
  mock, dev-only), `dictator.ico` (window/taskbar icon — keep in sync with
  `assets/icon/dictator.ico`), `fonts/` (woff2, used by the Tk fallback path
  only).
- `assets/icon/` — `gen_icon.py` (logo generator, source of truth for the
  icon design), `dictator.ico`/`dictator-512.png`/`dictator-disabled-64.png`
  (generated, committed so a fresh clone doesn't need Pillow just to have an
  icon).
- `assets/fonts/` — bundled TTFs (Space Grotesk / Space Mono / Doto), used by
  the Tk fallback dashboard only, loaded via GDI on Windows
  (`load_private_fonts()`), no-op on macOS (falls back to system fonts).
- `scripts/` — `install.ps1` (Windows setup: git/python check, venv, deps,
  Ollama model detect+prompt+pull, launch), `install.sh` (same for macOS,
  via Homebrew), `bootstrap.ps1`/`bootstrap.sh` (the actual one-line
  installers — clone-if-needed then hand off to install.ps1/install.sh; these
  are what the README's `irm ... | iex` / `curl ... | bash` one-liners hit).
- `Cache/` — `Cache/whisper` (Whisper model cache) and the **live**
  `history.jsonl` (current `history_dir` in config.json points here).
- `__pycache__/` — regenerable, not committed.

Two folders were deleted in this session as stale: `Shipping/` (an abandoned
forked copy of the app with its own diverged README/LICENSE/docs — not the
code that actually runs) and `Logs/` (a dead `history.jsonl` from a previous
`history_dir` setting the app stopped reading a while back).

## Config file

`%APPDATA%\Dictator\config.json` (Windows) /
`~/Library/Application Support/Dictator/config.json` (macOS) — see
`DEFAULTS` (main.py) for every key. Notable: `hotkey_mods`/`hotkey_mode`,
`language`, `silence_auto_stop`/`silence_threshold`/`silence_duration_s`,
`review_before_typing`, `auto_punctuate`, `tone_overrides`, `snippets`,
`theme`/`accent_color`/`highlight_color`, `dash_geometry`, `model_size`,
`history_dir`, `vocabulary`, `redact_patterns`, `profiles`/`active_profile`,
`sound_enabled`.

## Operational gotchas (learned the hard way — read before moving this app again)

- **Ollama model path is an OS-level env var, not just this app's config.**
  The Ollama *service* reads its model store from the `OLLAMA_MODELS` user
  environment variable — check it's pointed where you expect before assuming
  models are missing. If you change it, fully kill and relaunch
  **`ollama app.exe`** (the tray supervisor, not just `ollama.exe`), then
  verify with `curl http://localhost:11434/api/tags`.
- **A registry/env `SetEnvironmentVariable` change does not reach processes
  in the current login session** unless set inline right before launching,
  even though it's persisted for next login. Verify by querying the running
  service, don't trust "I set it" alone.
- **`ollama_timeout_s` default is 12.0s, not 3.0s** — 3s was a real bug: a
  cold/just-restarted Ollama regularly takes longer than 3s to answer its
  first request, silently falling back to raw uncleaned text with no visible
  error. If filler-word removal seems flaky, check this value first.
- **`set_start_on_login()` (Windows) needs `Scripts\pythonw.exe`, not
  `sys.prefix\pythonw.exe`**, in a venv — checked in that order already, but
  if autostart silently doesn't work after a Python/venv change, check this.
- **Two `pythonw.exe` processes for one running Windows app is normal, not a
  bug** — the venv launcher stub spawns the real base-install interpreter as
  a child. Don't chase it as a duplicate-instance bug.
- **WebView2 disk-caches dashboard assets across relaunches** — see "Design
  system" above; bump `?v=N` after CSS/JS edits or you'll debug a "fix" that
  never actually loaded.
- **`classList.add("")` throws `DOMException` in this WebView2 build** (empty
  string, unlike some browsers) — this was the root cause of a real
  "blank dashboard on fresh install" bug in an earlier session. `app.js` now
  wraps every page-section render in `safe()`/`safeAsync()` error boundaries
  specifically so one section's bug can't blank the whole app again — don't
  remove those wrappers to "simplify" a render function.
- **Never verify the dashboard by opening it in Chrome** — always check the
  real running app on the PC (PrintWindow screenshots, or careful single
  real-cursor clicks on a window you've explicitly foregrounded and
  positioned — never synthetic PostMessage/SendMessage clicks, WebView2
  ignores those).

## Running / testing

- `Dictator.bat` (Windows) / `Dictator.command` (macOS) launches it normally.
- `python main.py --selftest` runs `selftest_cleanup()` — sanity-checks the
  Ollama cleanup prompt against a few fixed cases, requires Ollama running.
  No other automated tests exist.
- No build step; `.venv` is a plain venv, `requirements.txt` lists deps
  (platform-conditional via `sys_platform` markers for CUDA/pyobjc).

## Known deliberate limitations

- Review-before-typing triggers purely on character count (>1000), not word
  count or duration.
- `keyboard` library polling for hotkey detection, not a low-level OS hook —
  simplest correct option on Windows; the shakier link on macOS (see
  "macOS support" caveats).
- Dashboard has no dock/taskbar icon on macOS beyond the generic Python one —
  would need packaging as a real `.app` bundle to fix, out of scope for now.
- Accent-color customization doesn't derive `--accent-soft`'s tint from a
  user's custom accent choice — it stays a fixed neutral overlay regardless
  of the picked accent. Cheap to fix later if it's ever visibly wrong.
