"""Single source of truth for every filesystem path derived from where the
app lives on disk. Everything else imports from here instead of recomputing
__file__-relative paths locally — that was previously done in three
different places and each assumed a different folder depth."""
import os

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(PACKAGE_DIR)
ENTRY_SCRIPT = os.path.join(REPO_ROOT, "main.py")

# whisper models download here instead of C:\Users\<you>\.cache — safe to
# delete, they just re-download on next launch
WHISPER_CACHE = os.path.join(REPO_ROOT, "Cache", "whisper")
OLLAMA_MODELS_DIR = os.path.join(REPO_ROOT, "ollama-models")
VENV_DIR = os.path.join(REPO_ROOT, ".venv")
