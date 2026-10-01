<div align="center">
  <img src="assets/icon/dictator-512.png" width="96" height="96" alt="Dictator logo" />

  # Dictator

  **Talk. It types — cleaned up, not just transcribed.**

  ![CI](https://github.com/IntellectDaksh/Dictator/actions/workflows/ci.yml/badge.svg)
  ![Release](https://img.shields.io/github/v/release/IntellectDaksh/Dictator)
  ![License](https://img.shields.io/badge/license-MIT%20%2B%20Commons%20Clause-blue)
  ![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS-informational)
</div>

Local voice dictation. Hold a hotkey, speak, release — your words are
transcribed on your own machine, cleaned up by a local AI pass (filler words
removed, self-corrections resolved, grammar fixed), and typed into whatever
app has focus. No cloud, no accounts, nothing leaves your PC.

A free, local-first alternative to tools like Wispr Flow — with one thing
most free/open dictation tools skip entirely: **the AI cleanup pass**. Most
alternatives just hand you a raw transcript; Dictator fixes it before it
lands in your text box.

> **v1.1.** Core dictation is my own daily driver on Windows. This release
> is about speed and typing reliability — text lands roughly 200 ms after
> you let go of the hotkey. macOS support hasn't been run on real Mac
> hardware yet — the fundamentals work. [Open an issue](../../issues) if
> something breaks or you want a feature.

## Where it fits

| | Cloud dictation apps (Wispr Flow, etc.) | Other open-source dictation tools | Dictator |
|---|---|---|---|
| Cost | Subscription | Free | Free |
| Audio leaves your PC | Yes | No | No |
| Cleans filler words / self-corrections | Yes | Usually not — raw transcript only | Yes, local |
| Works offline | No | Yes | Yes |
| App-aware tone (casual/formal/verbatim) | Rare | No | Yes |
| Release-to-text latency | ~0.7–2 s (network round trip) | Usually 1 s+ (decodes after release) | ~0.2 s typical, ~0.45 s p95 |

The gap this fills: free and open dictation tools exist, but almost all of
them stop at the raw Whisper transcript. Dictator adds the cleanup pass —
the part that actually makes a transcript usable — without sending anything
to a server to do it.

## Install

**Windows** — PowerShell:

```powershell
irm https://raw.githubusercontent.com/IntellectDaksh/Dictator/main/scripts/bootstrap.ps1 | iex
```

**macOS** — Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/IntellectDaksh/Dictator/main/scripts/bootstrap.sh | bash
```

Either one-liner clones the repo, creates a virtual environment, installs
every dependency, installs Ollama if it's missing, **tunes Dictator to your
hardware** (see below), pulls the matching cleanup model, then launches the
app. No prompts. Safe to re-run any time; every step skips if it's already
done.

Requires [Git](https://git-scm.com/downloads) and Python 3.11+ (the
Windows script installs Git and Ollama via `winget` if missing; the macOS
script installs Homebrew, Python and Ollama if missing).

## Tuned to your machine

The installer runs `hwtune.py`, which reads your RAM, GPU and CPU and picks
the models that keep dictation around the same speed on any laptop — bigger
models where the hardware has room, smaller ones where it doesn't:

| Your hardware | Speech model (Whisper) | Cleanup model (Ollama) |
|---|---|---|
| NVIDIA GPU, 6 GB+ VRAM | `small.en`, float16 on GPU | `qwen2.5:3b-instruct` (~1.9 GB) |
| NVIDIA GPU, 4–6 GB | `small.en`, float16 on GPU | `qwen2.5:1.5b-instruct` (~1 GB) |
| NVIDIA GPU, 2–4 GB | `small.en`, int8 on GPU | `qwen2.5:1.5b-instruct` |
| Apple Silicon, 16 GB+ | `base.en`, int8 on CPU | `qwen2.5:3b-instruct` |
| No GPU, 12 GB+ RAM | `base.en`, int8 on CPU | `qwen2.5:1.5b-instruct` |
| No GPU, under 12 GB | `base.en`, int8 on CPU | `qwen2.5:0.5b-instruct` (~0.4 GB) |

It writes the choice into your config, starts Ollama, pulls the model if you
don't have it, and links it — nothing to set by hand. Dictator itself never
re-tunes at startup, so anything you change later in the tray menu sticks.
Upgraded your hardware? Re-run it:

```bash
python hwtune.py           # show what it detects and would pick
python hwtune.py --pull    # apply it and pull the model
```

After setup: double-click `Dictator.bat` (Windows) or `Dictator.command`
(macOS) to start it again.

**macOS note:** the first time you dictate, macOS will prompt for
Accessibility + Input Monitoring permission (needed to detect the hotkey and
type text) — grant it in System Settings → Privacy & Security and try again.
macOS support is new and best-effort; see `info.md` for exactly what's
platform-specific if something misbehaves.

## Why this exists

I dictate most of my own notes, DMs, and scripts for Uideas — typing is
slower than talking, but every dictation tool I tried either shipped audio to
a cloud API or left "um"s and false starts in the transcript. Built this to
run fully local and clean up the mess a real voice makes, then kept using it
daily until it stopped breaking.

## How to use it

1. Click into any text box.
2. Hold **Ctrl + Win** (Ctrl + Cmd on macOS) and speak — the status bar
   turns red.
3. Release — it turns amber while cleaning up, flashes green when your text
   is typed.

Say it messy: "um let's meet at 12 no wait 11" becomes "Let's meet at 11."
(Self-corrections like that need **Smart** cleanup — see below.)

## Cleanup modes

Pick one from the tray menu (right-click the mic icon → Processing mode).

| Mode | What it does | Speed | Extra memory |
|---|---|---|---|
| **Fast** (default) | Punctuation, capitals, filler words removed — no LLM | Instant | None |
| **Smart** | Full LLM pass: self-corrections, grammar, tone | ~0.5 s more | Small Ollama model, unloaded after 2 min idle |
| **Verbatim** | Exactly what Whisper heard | Instant | None |

Smart mode can't invent text: output with words you never said, or that
balloons in length, is rejected and the plain transcript is typed instead.

## Why it's fast

- The mic stream stays open, so the first word is never clipped (0.3 s of
  pre-roll is kept on press — nothing outside a dictation is stored).
- Whisper stays loaded, and finished sentences are transcribed *while* you're
  still talking — release only has to decode the last few words.
- The GPU is kept out of its idle clock during recording.
- Your custom vocabulary is passed to Whisper itself, not just the cleanup
  step, so names and brand words come out right the first time.

Every dictation logs stage timings (never text) to `latency.jsonl` in the
config folder, so slowdowns are visible.

## Light on memory

Built to sit in the tray all day on a laptop that's already running a browser
and an editor:

- **~340 MB RAM** for the app with Whisper loaded (measured on Windows,
  `small.en` on GPU). On GPU machines the model weights live in VRAM, not RAM.
- **No LLM in memory by default.** Fast mode never touches Ollama. Smart mode
  loads the small cleanup model on demand and Ollama drops it after 2 minutes
  idle, so it only costs memory while you're actively dictating.
- **Small models first.** 0.5B–3B cleanup models instead of the 7B–14B most
  local setups default to — the cleanup job is short and structured, so a
  small model does it in a fraction of the time and memory.
- **Tight context.** Cleanup requests use a 512-token window and cap the
  output length, so Ollama doesn't reserve memory it will never use.
- **Memory trimmed after each dictation** — freed audio buffers are handed
  back to the OS instead of sitting in the process.
- Audio is never written to disk, and only 0.3 s of it is buffered outside a
  dictation.

## Features

- Hold-to-dictate or hands-free (double-tap for hold mode, single-tap toggle),
  with optional silence auto-stop
- Configurable hotkey — pick a preset, capture any combo live, or save named
  hotkey profiles
- Local speech-to-text (`faster-whisper`), GPU-accelerated on Windows with
  CPU fallback everywhere
- Local cleanup LLM via Ollama — strips filler words, fixes grammar, resolves
  self-corrections, falls back to raw transcript if Ollama is unreachable
- Language selection — 12 languages plus auto-detect
- App-aware tone (casual/formal/verbatim per focused app, user-editable) and
  spoken tone overrides ("...make it formal")
- Voice commands: "new line", "new paragraph", "bullet point" — newlines are
  typed as Shift+Enter so chat boxes don't send early
- Safe typing — waits until you've let go of every modifier, and stops the
  moment focus moves to another window, so keystrokes never turn into
  shortcuts or land in the wrong app
- Snippets/macros, custom vocabulary for names/brand words
- Redaction list — sensitive words/phrases scrubbed before they're ever
  written to disk
- Dictation history + an Insights tab (streak calendar, app usage, WPM, tone
  distribution, hourly activity) — opt-in logging, off by default
- Dark/light theme with independently configurable accent and highlight
  colors, start-on-login

## Tray menu (right-click the mic icon)

Enable/disable, pick microphone, pick cleanup mode (Fast/Smart/Verbatim), pick
Whisper model size (base/small/medium),
toggle status bar, toggle history logging, start on login, open config
folder, quit.

## FAQ

**Does any audio or text leave my machine?**
No. Speech-to-text runs locally via `faster-whisper`, cleanup runs locally
via Ollama on `localhost`. The only network calls are to Ollama itself and,
during install, pulling the models. See [Privacy](#privacy) below.

**Will it be as fast on my laptop as on yours?**
That's what the hardware tuning is for: slower machines get lighter models so
the wait after you release stays short. A laptop without a GPU uses the
smaller `base.en` speech model, which is a little less accurate on unusual
words than `small.en` — add those to your custom vocabulary.

**Do I need a GPU?**
No — CPU works fine, GPU (CUDA, Windows only) just makes transcription
faster. `faster-whisper` picks whichever is available.

**What if Ollama isn't running or the cleanup model isn't pulled?**
Dictator falls back to the raw (optionally auto-punctuated) transcript
rather than failing silently. You never lose a dictation because the
cleanup step had a bad moment.

**Can I use my own cleanup model instead of the default?**
Yes — any model pulled in Ollama works; set it from Settings.

**Windows or macOS — which is more solid right now?**
Windows. It's the daily driver this was built for. macOS support is real
(every platform-specific call is branched and implemented) but hasn't been
run on physical Mac hardware yet — see the note in [Install](#install).

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for common issues and
the config file reference.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the stack, code map, and
threading model — read that before sending a PR. `info.md` has the deeper,
line-number-level map if you're picking this up cold.

## Privacy

Audio lives in memory only and is discarded after transcription. The mic
stream stays open while the app runs (that's what removes the start-up lag),
so Windows shows the mic as in use — audio outside a dictation is dropped
immediately, never buffered past 0.3 s. Turn it off with `keep_mic_warm:
false` in the config if you'd rather trade the lag for the indicator. Clipboard is
never touched — text is typed via simulated keystrokes. The only network
traffic is to Ollama on `localhost`, plus one-time model downloads during
setup. No telemetry, no accounts, no API keys.

## License

MIT + Commons Clause — free to use, modify, and share; not for resale. See
[LICENSE](LICENSE).

 #   U p d a t e  
 
 #   A n o t h e r   u p d a t e  
 