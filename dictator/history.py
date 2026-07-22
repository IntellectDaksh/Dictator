"""Dictation history log — JSONL, opt-in, off by default."""
import json
import os
from datetime import datetime

from .config import history_path


def log_history(cfg, raw, cleaned, secs):
    if not cfg["log_history"]:
        return  # nothing is ever written to disk when the flag is off
    os.makedirs(cfg["history_dir"], exist_ok=True)
    entry = {"timestamp": datetime.now().isoformat(timespec="seconds"),
             "raw_transcript": raw, "cleaned_text": cleaned,
             "duration_s": round(secs, 2)}
    with open(history_path(cfg), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
