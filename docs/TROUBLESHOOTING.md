# Troubleshooting

| Symptom | Fix |
|---|---|
| Nothing happens on Ctrl+Win | Check the tray icon exists and "Enabled" is ticked. If the icon isn't there, double-click `Dictator.bat`. |
| Ums/filler words still showing up | Ollama isn't running or has no model pulled. The app still types the raw transcript on purpose so nothing is lost. |
| Doesn't type into an admin window | Windows blocks normal apps from typing into elevated ones — run `Dictator.bat` as administrator for that session. |
| Slow transcription | GPU unavailable, falls back to CPU automatically. Or switch to `base.en` in the tray menu. |
| Wrong microphone | Tray menu → Microphone → pick the right one. |
| Want to reset everything | Delete `%APPDATA%\Dictator\config.json` — defaults come back on next launch. |
| `install.ps1` fails on Python check | Install Python 3.11+ from [python.org](https://python.org), tick "Add python.exe to PATH", re-run. |
| Ollama pull hangs or fails | Check `ollama.com` service status, or pull manually: `ollama pull qwen2.5:7b-instruct`. Dictator works without it, just skips cleanup. |

## Config file reference

`%APPDATA%\Dictator\config.json`, created on first run. Notable keys:

| Key | Meaning |
|---|---|
| `ollama_timeout_s` | How long to wait for cleanup before falling back to raw text (default 12s — a cold Ollama restart can take longer than you'd expect). |
| `model_size` | Whisper model: `base.en` / `small.en` / `medium.en`. |
| `hotkey_mods` / `hotkey_mode` | Hotkey combo and hold-vs-toggle behavior. |
| `tone_overrides` | Per-app casual/formal/verbatim exe-name lists. |
| `snippets` | `{trigger phrase: expansion text}` macros. |
| `history_dir` | Where `history.jsonl` is written, if logging is on. |

Edit it with Notepad while the app is closed.
