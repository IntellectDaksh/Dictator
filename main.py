"""Dictator — local push-to-talk dictation for Windows.

Hold Ctrl+Win and speak; release to stop. Audio is transcribed locally with
faster-whisper (CUDA if available), cleaned up by a local Ollama model, and
typed into whatever window has keyboard focus. Fully offline after setup:
the only network traffic is Ollama on localhost.

This file is the entrypoint only — the app itself lives in the dictator/
package next to this file. See docs/ARCHITECTURE.md for the module map.
"""
import os
import sys

# pip-installed nvidia cublas/cudnn DLLs aren't on the Windows DLL search path;
# ctranslate2 resolves them via PATH, so prepend their bin dirs before the
# dictator package (which imports faster_whisper) gets imported below
for _pkg in ("cublas", "cudnn"):
    _bin = os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", _pkg, "bin")
    if os.path.isdir(_bin):
        os.environ["PATH"] = _bin + os.pathsep + os.environ["PATH"]

from dictator.app import App
from dictator.config import load_config
from dictator.ollama_client import ollama_cleanup, resolve_ollama_model


def selftest_cleanup():
    """Print raw vs cleaned for the required test cases (needs Ollama up)."""
    cfg = load_config()
    model = resolve_ollama_model(cfg)
    if not model:
        print("FAIL: Ollama not reachable or no preferred model pulled")
        return 1
    cfg["ollama_timeout_s"] = 60  # generous for a cold model load
    cases = [
        "lets connect at 12 pm um no actually 11 pm",
        "um so I was thinking we could you know maybe grab lunch tomorrow",
        "send it to john wait I mean send it to sarah not john",
        "The meeting is scheduled for Thursday at 3pm in the main conference room.",
    ]
    print(f"cleanup model: {model}\n")
    for raw in cases:
        cleaned = ollama_cleanup(raw, cfg, model)
        print(f"raw:     {raw}")
        print(f"cleaned: {cleaned if cleaned is not None else '(FAILED)'}\n")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest_cleanup())
    try:
        App().run()
    except KeyboardInterrupt:
        pass
