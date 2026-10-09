# Testing & Benchmarking Guide for Dictator

This document outlines the testing structure, regression test suites, and benchmarking tools available in the repository.

---

## 1. Unit Tests

Unit tests are located under the `tests/` directory. They run without requiring audio hardware, GPU devices, or active window manager focus:

```bash
# Run all unit tests
python -m unittest discover tests

# Run specific test modules
python -m unittest tests/test_text_cleanup.py
python -m unittest tests/test_bench_metrics.py
```

### Covered Areas:
- **`test_text_cleanup.py`**:
  - `fmt_bytes`: Memory and disk size string formatting across byte, KB, MB, and GB boundaries.
  - `remove_filler_words`: Speech filler cleanup ("um", "uh", "i mean", "kind of").
  - `apply_commands`: Formatting commands ("new paragraph", "new line", "bullet point").
  - `apply_voice_command`: End-of-dictation tone overrides ("make it formal", "make it casual").
- **`test_bench_metrics.py`**:
  - `norm`: Speech-to-text string normalization (punctuation stripping, case folding, contraction expansion).
  - `wer`: Word Error Rate Levenshtein edit distance calculations.

---

## 2. Dry-Run Integration Suite (`drytest.py`)

The dry-run suite executes real model inference on synthetic audio without sending keystrokes to real OS windows:

```bash
python drytest.py
```

- Synthesizes 16 kHz audio via Windows SAPI.
- Passes audio through the local Whisper model.
- Stubs Tkinter GUI and input injection.
- Exits with status `0` upon passing all test phases.

---

## 3. Ollama Response Benchmarking (`bench_ollama.py`)

Measures the cleanup round-trip latency of local LLMs running via Ollama:

```bash
# Run with default qwen2.5:1.5b-instruct
python bench_ollama.py

# Benchmark a custom model or remote endpoint
python bench_ollama.py --model llama3.2:1b --url http://127.0.0.1:11434 --threshold 1.5
```

Reports:
- Model warm-up latency (VRAM load time).
- Per-sample latency across 5 diverse conversational prompts.
- Minimum, average, and maximum response times.

---

## 4. End-to-End Real-Time Verifier (`audit/`)

For full hardware-in-the-loop validation:

```bash
python audit/e2e.py apps
python audit/bench.py baseline --runs 3
```
