"""Persistent settings: plain JSON config + JSONL history log, no database."""
import json
import os

APP_DIR = os.path.join(os.environ.get("APPDATA", "."), "Dictator")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULTS = {
    "enabled": True,
    "model_size": "small.en",            # base.en / small.en / medium.en
    "input_device": None,                # None = system default mic
    "ollama_url": "http://localhost:11434",
    "ollama_model": "auto",              # auto = first available preferred model
    "ollama_timeout_s": 12.0,  # 3s was too tight — cold Ollama restarts / first request
                               # after idle regularly exceed it, silently falling back
                               # to raw (unfiltered) text
    "log_history": True,  # on so stats/history survive restarts; purge in dashboard
    "history_dir": APP_DIR,
    "start_on_login": False,
    "show_status_bar": True,
    "vocabulary": [],  # names/brand words fed to Whisper + cleanup model
    "review_before_typing": False,
    "hotkey_mods": ["ctrl", "win"],
    "hotkey_mode": "hold",                # hold / toggle (tap to start, tap to stop)
    "theme": "dark",                      # dark / light
    "accent_color": None,                 # None = theme default
    "dash_geometry": None,                # remembered dashboard window size/pos
    "auto_punctuate": True,               # capitalize/punctuate instant-mode + LLM-failure fallback text
    "tone_overrides": {"casual": [], "formal": [], "verbatim": []},  # extra exe names per tone
    "snippets": {},                       # {trigger phrase: expansion text}
    "pinned": [],                         # timestamps (isoformat) of starred dictations
    "auto_theme": False,                  # dark 7pm-7am, light otherwise
}


def history_path(cfg):
    return os.path.join(cfg["history_dir"], "history.jsonl")


def folder_size(path):
    total = 0
    files = 0
    try:
        for root, _dirs, names in os.walk(path):
            for name in names:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                    files += 1
                except OSError:
                    pass
    except OSError:
        pass
    return total, files


def fmt_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    os.makedirs(APP_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
