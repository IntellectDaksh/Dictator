# Dictator

![CI](https://github.com/IntellectDaksh/Dictator/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/badge/license-MIT%20%2B%20Commons%20Clause-blue)
![Platform](https://img.shields.io/badge/platform-Windows-informational)

Local voice dictation for Windows. Hold **Ctrl + Win**, speak, release — your
words are transcribed on your own machine, cleaned up (filler words removed,
self-corrections resolved, grammar fixed), and typed into whatever app has
focus. No cloud, no accounts, nothing leaves your PC.

> **Status:** core dictation works reliably — this is my own daily driver.
> The dashboard UI is functional but plain. If this gets traction, dashboard
> polish and bug fixes are next — [open an issue](../../issues) if something
> breaks or you want a feature.

## Install

Requires [Python 3.11+](https://python.org) and, optionally, [Ollama](https://ollama.com/download)
for transcript cleanup.

```powershell
git clone https://github.com/IntellectDaksh/Dictator.git
cd Dictator
.\scripts\install.ps1
```

That one script creates the virtual environment, installs dependencies,
checks whether Ollama has a cleanup model pulled (suggests and downloads
`qwen2.5:7b-instruct` if not), and launches the app. Re-run it any time —
every step skips if it's already done.

After setup, just double-click `Dictator.bat` to start it again.

## How to use it

1. Click into any text box.
2. Hold **Ctrl + Win** and speak — the status bar turns red.
3. Release — it turns amber while cleaning up, flashes green when your text
   is typed.

Say it messy: "um let's meet at 12 no wait 11" becomes "Let's meet at 11."

## Features

- Hold-to-dictate or hands-free (double-tap for hold mode, single-tap toggle)
- Configurable hotkey — pick a preset or capture any combo live
- Local speech-to-text (`faster-whisper`), GPU-accelerated with CPU fallback
- Local cleanup LLM via Ollama — strips filler words, fixes grammar, resolves
  self-corrections, falls back to raw transcript if Ollama is unreachable
- App-aware tone (casual/formal/verbatim per focused app, user-editable)
- Snippets/macros — trigger phrase → canned expansion
- Voice commands: "new line", "new paragraph", "bullet point"
- Custom vocabulary for names/brand words
- Dictation history + stats dashboard (opt-in logging, off by default)
- Dark/light theme, custom accent color, start-on-login

## Tray menu (right-click the mic icon)

Enable/disable, pick microphone, pick Whisper model size (base/small/medium),
toggle status bar, toggle history logging, start on login, open config
folder, quit.

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for common issues and
the config file reference.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the stack, code map, and
threading model — read that before sending a PR.

## Privacy

Audio lives in memory only and is discarded after transcription. Clipboard is
never touched — text is typed via simulated keystrokes. The only network
traffic is to Ollama on `localhost`, plus one-time model downloads during
setup. No telemetry, no accounts, no API keys.

## License

MIT + Commons Clause — free to use, modify, and share; not for resale. See
[LICENSE](LICENSE).
