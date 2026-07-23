# Changelog

## Unreleased

- New webview dashboard (`dashboard/`, `pywebview` + HTML/CSS/JS) as the
  primary settings/stats UI, replacing the Tk dashboard (kept as a fallback).
  Runs as its own process and syncs with the app through `config.json` /
  `runtime.json` / `history.jsonl`.
- `App` now mirrors live state to `runtime.json` and hot-applies a wider set of
  config keys (model reload, enable/disable, theme) written by the dashboard.
- Added `pywebview` to `requirements.txt`.

## 1.0.0 — 2026-07-22

Initial public release.

- Hold-to-dictate and hands-free (double-tap hold / single-tap toggle) modes
- Configurable hotkey with live capture
- Local speech-to-text via `faster-whisper`, GPU with CPU fallback
- Local cleanup LLM via Ollama with raw-transcript fallback
- App-aware tone, snippets/macros, voice commands
- Custom vocabulary, dictation history + stats dashboard
- Dark/light theme, start-on-login
- `scripts/install.ps1` one-command setup
