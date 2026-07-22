# Architecture

`main.py` at the repo root is a thin entrypoint — the app itself lives in
the `dictator/` package next to it, split by responsibility.

## Stack

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| UI | `tkinter` (dashboard, status pill, review window), `pystray` (system tray) |
| Speech-to-text | `faster-whisper`, GPU if available, CPU fallback |
| Cleanup LLM | Local Ollama HTTP API (`http://localhost:11434`), plain `urllib` |
| Audio capture | `sounddevice` |
| Global hotkey | `keyboard` library, polled (not an OS-level hook) |
| Text injection | Raw Win32 `SendInput` via `ctypes` — never touches the clipboard |
| Persistence | Plain JSON config + JSONL history log, no database |

## Package layout

```
main.py                    entrypoint: DLL PATH fix-up, --selftest, App().run()
dictator/
  paths.py                 single source of truth for every __file__-relative path
  config.py                DEFAULTS, load_config/save_config, history_path, folder_size
  sendinput.py             raw Win32 SendInput keystroke injection
  hotkeys.py               hotkey polling (HOTKEY_PRESETS, hotkey_down, wait_keys_released)
  tone.py                  focused-window detection + casual/formal/verbatim tone rules
  textshaping.py           spoken commands, punctuation fallback, snippet expansion
  transcriber.py           Transcriber class wrapping faster-whisper
  ollama_client.py         resolve_ollama_model, ollama_cleanup (cleanup LLM calls)
  injection.py             inject_text — glues hotkeys + sendinput together
  history.py               log_history — JSONL append, opt-in
  startup.py               set_start_on_login — HKCU Run key toggle
  overlay.py                status pill UI (Overlay class + canvas helpers)
  app.py                   App class: hotkey loop, pipeline, dashboard, tray menu
```

Every module above is independently importable and has a single reason to
change. `app.py` is the one exception left as a single ~1700-line file — see
[Known deliberate limitations](#known-deliberate-limitations) for why.

## Threading model

- `hotkey_loop` and `process()` (both in `app.py`) run on background threads.
- Anything touching Tk widgets or shared session/stat state must run on the
  **Tk main thread**. The bridge is a queue drained every 100ms by
  `poll_ui()`. `process()` pushes state updates onto that queue instead of
  mutating shared state directly — don't reintroduce direct mutation from a
  worker thread, it will race with the dashboard reading the same state.
- The dashboard doesn't poll on a timer — it refreshes only on a dictation
  being recorded, a search keystroke, or a control action.

## Known deliberate limitations

- `app.py` stays one class instead of being split further: every dashboard
  method reads and writes the same `self.cfg`/`self.session`/widget state,
  so splitting it would mean passing that shared state across module
  boundaries for no real decoupling gain. Revisit if the dashboard grows a
  second independent surface (e.g. a web UI).
- Review-before-typing triggers purely on character count (>1000), not word
  count or duration.
- `keyboard` library polling for hotkey detection, not a low-level OS hook —
  simplest correct option for this use case.
