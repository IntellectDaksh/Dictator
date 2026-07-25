# Troubleshooting

## Nothing happens when I hold the hotkey

- Check the tray icon isn't greyed out (disabled) — right-click → Enable.
- **Windows:** make sure no other app has claimed the same combo globally.
- **macOS:** the `keyboard` library needs Accessibility + Input Monitoring
  permission. System Settings → Privacy & Security → grant it to your
  terminal app (or `python3`), then restart Dictator. This is the single
  most likely thing to go wrong on macOS — see `info.md`'s macOS support
  section.

## It types the raw, un-cleaned transcript (fillers, no punctuation)

Almost always Ollama-related:

- Is Ollama running? `curl http://localhost:11434/api/tags` should return
  JSON, not a connection error.
- Do you have a cleanup model pulled? `ollama list` — if empty, run
  `ollama pull qwen2.5:7b-instruct` (or re-run `scripts/install.ps1` /
  `install.sh`, which offers to do this for you).
- A cold/just-restarted Ollama can take a few seconds to answer its first
  request — `ollama_timeout_s` in `config.json` defaults to 12s specifically
  to cover this. If it's still falling back, something else is slow; check
  `ollama_cleanup()` latency directly before assuming it's a prompt problem.

Dictator always falls back to the raw (optionally auto-punctuated) transcript
rather than failing silently — this is a deliberate design choice: you never
lose a dictation because the cleanup step had a bad day.

## Dashboard opens blank / tabs are empty

Should not happen anymore — every dashboard page render is wrapped in an
error boundary (`safe()`/`safeAsync()` in `app.js`) specifically so one
section's bug can't blank the whole app. If you do hit this, open
`dashboard/dash-debug.log` (run `dashboard.py` with `--debug` for more) and
check the browser-style console error it should have surfaced instead of a
blank page — then file an issue with that message.

## A CSS/JS change to the dashboard "isn't showing up"

WebView2 (Windows) disk-caches `styles.css`/`app.js` across relaunches, even
after you edit the files. Check `index.html`'s `<link>`/`<script>` tags have
a `?v=N` query string, and bump it — that busts the cache.

## "Start on login" doesn't work

- **Windows:** check `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` has
  a `Dictator` value pointing at your venv's `Scripts\pythonw.exe` — if you
  moved the app or recreated the venv, toggle the setting off and back on to
  rewrite it.
- **macOS:** check `~/Library/LaunchAgents/com.uideas.dictator.plist` exists
  and `launchctl list | grep dictator` shows it loaded.

## Config file reference

`%APPDATA%\Dictator\config.json` (Windows) or
`~/Library/Application Support/Dictator/config.json` (macOS). Full key list
in `main.py`'s `DEFAULTS` dict; the ones people actually go looking for:

| Key | What it does |
|---|---|
| `hotkey_mods` / `hotkey_mode` | Which keys, hold vs. toggle |
| `model_size` | Whisper model — `base.en` / `small.en` / `medium.en` |
| `language` | ISO code or `"auto"` |
| `history_dir` | Where `history.jsonl` lives — change this via the dashboard, not by hand, if you want the app to notice |
| `accent_color` / `highlight_color` | `null` = theme default; hex string to override |
| `redact_patterns` | Words/phrases scrubbed before writing to history |

If something's badly wrong, quitting Dictator, deleting `config.json`, and
relaunching regenerates it from defaults — you'll lose custom settings, not
your dictation history (that's a separate file).
