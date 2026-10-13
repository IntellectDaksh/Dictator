# Dictator Architecture Specification

This document describes the architectural pipeline, execution lifecycle, and component interactions of Dictator.

---

## 1. Pipeline Overview

Dictator runs as a lightweight, push-to-talk background daemon with minimal latency and zero cloud telemetry:

```text
[ Push-to-Talk Hotkey ] (Ctrl + Win / Ctrl + Cmd)
          │
          ▼
[ Audio Capture Stream ] ─── 16 kHz Mono Float32 (sounddevice)
          │
          ▼
[ Speech-to-Text ] ───────── faster-whisper (CTranslate2, CUDA / CPU)
          │
          ▼
[ Text Shaping & Tone ] ──── tone_for(exe, title) + Voice Commands
          │
          ▼
[ LLM Cleanup Engine ] ───── Local Ollama (qwen2.5:1.5b / fast regex)
          │
          ▼
[ Input Injection ] ──────── Windows SendInput / Unicode surrogate synthesis
          │
          ▼
[ Local History Log ] ────── AppData/Dictator/history.jsonl
```

---

## 2. Core Subsystems

### A. Push-to-Talk & Hotkey Loop
- **Trigger**: Defaults to `Ctrl + Win` (Windows) or `Ctrl + Cmd` (macOS).
- **Behavior**: Spawns an event loop monitoring raw key state. When held down, the audio recording thread starts; upon release, the recording buffers flush to the transcriber.

### B. Audio Capture & Speech-to-Text
- **Sample Rate**: 16,000 Hz, single channel (mono).
- **Model Engine**: `faster-whisper` based on CTranslate2.
- **Model Auto-tuning**: Defaults to `small.en` with greedy beam search (`beam_size=1`) for sub-second response times.

### C. Tone & Application Context
- Inspects foreground window process name and title via Win32 API / AppleScript.
- Matches against pre-configured application profiles (`VERBATIM_EXES`, `CASUAL_EXES`, `FORMAL_EXES`).
- Trailing voice commands (e.g. `"make it formal"`) dynamically override the target tone for the current utterance.

### D. Keystroke Injection
- Uses `SendInput` with virtual scan codes on Windows and Unicode surrogate synthesis for extended character sets.
- Automatically handles app-specific pacing adjustments (e.g. Notepad rate-limiting).

---

## 3. Configuration & State Management

- **Config Path**: `%APPDATA%\Dictator\config.json` (Windows) / `~/.config/Dictator/config.json` (Linux/macOS).
- **Runtime State**: `runtime.json` mirrors engine status for detached dashboard processes.
- **History**: Stored locally in `history.jsonl` with optional regex redaction patterns.
