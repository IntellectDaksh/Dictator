"""Dictator dashboard — a pywebview window rendering the Uideas OS "Nothing"
UI (index.html / styles.css / app.js) over a Python bridge.

Runs as its OWN process, spawned on demand from the Dictator tray. It never
imports main.py (that would drag faster_whisper/CUDA in for nothing); instead
it talks to the running Dictator through the files main.py already owns:

  - config.json   read + written here; main.py hot-reloads it (_watch_config_file)
  - history.jsonl read here for stats + the recent list
  - runtime.json  written by main.py (live whisper device/loaded + last text)

Local-only: the sole network call is probing Ollama on localhost.
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes
from datetime import date, datetime, timedelta

import sounddevice as sd
import webview

# ---------------------------------------------------------------- paths
APP_DIR = os.path.join(os.environ.get("APPDATA", "."), "Dictator")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
RUNTIME_PATH = os.path.join(APP_DIR, "runtime.json")
DICT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # ...\Dictator
WORKSPACE_DIR = os.path.dirname(DICT_DIR)
WHISPER_CACHE = os.path.join(WORKSPACE_DIR, "Cache", "whisper")
OLLAMA_MODELS = os.path.join(DICT_DIR, "ollama-models")
HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.html")
ICON = os.path.join(HERE, "dictator.ico")

PREFERRED_MODELS = ("qwen3:14b", "qwen2.5:7b-instruct", "llama3.1:8b")
HOTKEY_PRESETS = [
    ("Ctrl + Win", ["ctrl", "win"]),
    ("Ctrl + Alt", ["ctrl", "alt"]),
    ("Ctrl + Shift", ["ctrl", "shift"]),
    ("Alt + Win", ["alt", "win"]),
]
BACKUP_KEYS = ("vocabulary", "snippets", "tone_overrides", "hotkey_mods",
               "hotkey_mode", "theme", "accent_color", "auto_punctuate",
               "review_before_typing", "auto_theme")
SYSTEM_PROMPT = (
    "You clean up raw speech transcripts into what the speaker intended to "
    "write. Remove filler words and verbal disfluencies. Fix punctuation, "
    "capitalization, and obvious grammar. Do not add information. Output only "
    "the corrected text — no preamble, no quotes, no explanation."
)


# ---------------------------------------------------------------- config io
def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config_patch(patch):
    cfg = load_config()
    cfg.update(patch)
    os.makedirs(APP_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    return cfg


def load_runtime():
    try:
        with open(RUNTIME_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def history_path(cfg):
    return os.path.join(cfg.get("history_dir", APP_DIR), "history.jsonl")


def fmt_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024


def folder_size(path):
    total = files = 0
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


# ---------------------------------------------------------------- ollama
def ollama_get(url, path, timeout=3.0):
    with urllib.request.urlopen(url + path, timeout=timeout) as r:
        return json.load(r)


def resolve_ollama_model(cfg):
    if cfg.get("ollama_model", "auto") != "auto":
        return cfg["ollama_model"]
    try:
        names = [m["name"] for m in ollama_get(cfg.get("ollama_url", "http://localhost:11434"),
                                               "/api/tags")["models"]]
    except Exception:
        return None
    for want in PREFERRED_MODELS:
        for name in names:
            if name == want or name.startswith(want.split(":")[0]):
                return name
    return None


def ollama_cleanup(raw, cfg, model):
    if not model:
        return None
    payload = json.dumps({
        "model": model, "stream": False, "think": False,
        "options": {"temperature": 0},
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": raw}],
    }).encode()
    req = urllib.request.Request(cfg.get("ollama_url", "http://localhost:11434") + "/api/chat",
                                 data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=cfg.get("ollama_timeout_s", 12.0)) as r:
            return (json.load(r)["message"]["content"].strip()) or None
    except Exception:
        return None


# ---------------------------------------------------------------- SendInput (undo)
user32 = ctypes.windll.user32
INPUT_KEYBOARD, KEYEVENTF_KEYUP, VK_BACK = 1, 0x0002, 0x08


class _KBD(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KBD), ("pad", ctypes.c_ubyte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _bk():
    inp = _INPUT(); inp.type = INPUT_KEYBOARD
    inp.ki = _KBD(VK_BACK, 0, 0, 0, None)
    return inp


def _bk_up():
    inp = _INPUT(); inp.type = INPUT_KEYBOARD
    inp.ki = _KBD(VK_BACK, 0, KEYEVENTF_KEYUP, 0, None)
    return inp


def send_backspaces(count):
    events = []
    for _ in range(max(0, count)):
        events.append(_bk()); events.append(_bk_up())
    if events:
        arr = (_INPUT * len(events))(*events)
        user32.SendInput(len(events), arr, ctypes.sizeof(_INPUT))


def set_clipboard(text):
    subprocess.run("clip", input=text.encode("utf-16-le"), shell=True, check=False)


def get_clipboard():
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.rstrip("\n")
    except Exception:
        return ""


# ---------------------------------------------------------------- bridge
class Api:
    def __init__(self):
        self._hist_cache = (0.0, [])       # (mtime, entries)
        self._storage_cache = (0.0, None)
        self._health_cache = (0.0, None)

    # ---- reads
    def _entries(self):
        p = history_path(load_config())
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            return []
        if mtime == self._hist_cache[0]:
            return self._hist_cache[1]
        out = []
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                        out.append({"t": datetime.fromisoformat(e["timestamp"]),
                                    "raw": e.get("raw_transcript", ""),
                                    "cleaned": e.get("cleaned_text", ""),
                                    "secs": e.get("duration_s", 0.0)})
                    except (ValueError, KeyError):
                        continue
        except OSError:
            pass
        self._hist_cache = (mtime, out)
        return out

    def _stats(self):
        entries = self._entries()
        n = len(entries)
        raw_w = sum(len(e["raw"].split()) for e in entries)
        cln_w = sum(len(e["cleaned"].split()) for e in entries)
        secs = sum(e["secs"] for e in entries)
        wpm = round(raw_w / secs * 60) if secs else 0
        days = {e["t"].date() for e in entries}
        d, streak = date.today(), 0
        if d not in days:
            d -= timedelta(days=1)
        while d in days:
            streak += 1; d -= timedelta(days=1)
        counts = {}
        for e in entries:
            counts[e["t"].date()] = counts.get(e["t"].date(), 0) + 1
        today = date.today()
        spark = [counts.get(today - timedelta(days=i), 0) for i in range(6, -1, -1)]
        return {"n": n, "raw_w": raw_w, "cln_w": cln_w, "wpm": wpm,
                "streak": streak, "spark": spark}

    def _recent(self, limit=25):
        cfg = load_config()
        pinned = set(cfg.get("pinned") or [])
        entries = self._entries()[-60:]
        rows = [self._row(e, pinned) for e in entries]
        rows.sort(key=lambda r: (r["pinned"], r["_ts"]), reverse=True)  # pinned first, then newest
        return [self._strip(r) for r in rows[:limit]]

    @staticmethod
    def _row(e, pinned):
        iso = e["t"].isoformat()
        return {"t": iso, "t_disp": e["t"].strftime("%d %b · %H:%M").upper(),
                "cleaned": e["cleaned"], "pinned": iso in pinned, "_ts": e["t"].timestamp()}

    @staticmethod
    def _strip(r):
        return {k: v for k, v in r.items() if not k.startswith("_")}

    def _mics(self):
        mics = [{"name": "System default", "index": None}]
        try:
            for idx, dev in enumerate(sd.query_devices()):
                if dev["max_input_channels"] > 0:
                    api = sd.query_hostapis(dev["hostapi"])["name"]
                    mics.append({"name": f'{dev["name"]} — {api}', "index": idx})
        except Exception:
            pass
        return mics

    def _health(self, cfg=None):
        now = time.time()
        if self._health_cache[1] and now - self._health_cache[0] < 15:
            return self._health_cache[1]
        cfg = cfg or load_config()
        rt = load_runtime()
        mic = "System default"
        try:
            if cfg.get("input_device") is not None:
                mic = sd.query_devices()[cfg["input_device"]]["name"]
        except Exception:
            mic = "Unavailable"
        ollama = resolve_ollama_model(cfg) or "not reachable"
        device = rt.get("whisper_device", "?")
        loaded = "ready" if rt.get("whisper_loaded") else ("loading" if rt else "unknown")
        data = {"enabled": "on" if cfg.get("enabled", True) else "off",
                "whisper": f'{cfg.get("model_size", "small.en")} / {device}',
                "loaded": loaded, "ollama": ollama, "mic": mic,
                "review": "on" if cfg.get("review_before_typing") else "off"}
        self._health_cache = (now, data)
        return data

    def _storage(self):
        now = time.time()
        if self._storage_cache[1] and now - self._storage_cache[0] < 30:
            return self._storage_cache[1]
        cfg = load_config()
        items = {"Whisper cache": WHISPER_CACHE, "Ollama models": OLLAMA_MODELS,
                 "Python env": os.path.join(DICT_DIR, ".venv"),
                 "History": cfg.get("history_dir", APP_DIR)}
        data = []
        for name, path in items.items():
            size, files = folder_size(path)
            data.append({"name": name, "size": fmt_bytes(size), "files": files})
        self._storage_cache = (now, data)
        return data

    # ---- public bridge
    def get_state(self):
        cfg = load_config()
        return {"config": cfg, "mics": self._mics(),
                "hotkey_presets": [[label, mods] for label, mods in HOTKEY_PRESETS],
                "stats": self._stats(), "recent": self._recent(),
                "health": self._health(cfg), "storage": self._storage(),
                "history_path": history_path(cfg)}

    def get_live(self):
        cfg = load_config()
        return {"stats": self._stats(), "recent": self._recent(),
                "health": self._health(cfg), "storage": self._storage()}

    def get_config(self):
        return load_config()

    def set_config(self, patch):
        save_config_patch(patch)
        return True

    def toggle_pin(self, t_iso):
        cfg = load_config()
        pinned = list(cfg.get("pinned") or [])
        if t_iso in pinned:
            pinned.remove(t_iso)
        else:
            pinned.append(t_iso)
        save_config_patch({"pinned": pinned})
        return True

    def search(self, q):
        q = (q or "").lower().strip()
        cfg = load_config()
        pinned = set(cfg.get("pinned") or [])
        hits = [self._strip(self._row(e, pinned)) for e in self._entries()
                if q in e["cleaned"].lower() or q in e["raw"].lower()]
        hits.sort(key=lambda r: r["t"], reverse=True)
        return hits[:80]

    def copy_text(self, text):
        set_clipboard(text or "")
        return True

    def copy_last(self):
        rt = load_runtime()
        text = rt.get("last_text")
        if not text:
            entries = self._entries()
            text = entries[-1]["cleaned"] if entries else ""
        set_clipboard(text)
        return True

    def undo_last(self):
        text = load_runtime().get("last_text", "")
        if text:
            send_backspaces(len(text.replace("\r\n", "\n")))
        return True

    def clean_clipboard(self):
        raw = get_clipboard()
        if not raw.strip():
            return False
        cfg = load_config()
        cleaned = ollama_cleanup(raw, cfg, resolve_ollama_model(cfg)) or raw
        set_clipboard(cleaned)
        return True

    def capture_hotkey(self):
        try:
            import keyboard
            combo = keyboard.read_hotkey(suppress=False)
        except Exception:
            return None
        mods = ["win" if "windows" in p.strip().lower() else p.strip().lower()
                for p in combo.split("+")]
        save_config_patch({"hotkey_mods": mods})
        return mods

    def retry_health(self):
        self._health_cache = (0.0, None)
        return True

    def confirm(self, msg):
        return bool(WIN.create_confirmation_dialog("Dictator", msg))

    # ---- file ops
    def _guarded_rmtree(self, path, allowed):
        path, allowed = os.path.normpath(path), os.path.normpath(allowed)
        if os.path.commonpath([path, allowed]) != allowed:
            return False
        shutil.rmtree(path, ignore_errors=True)
        self._storage_cache = (0.0, None)
        return True

    def clear_whisper_cache(self):
        return self._guarded_rmtree(WHISPER_CACHE, os.path.join(WORKSPACE_DIR, "Cache"))

    def clear_ollama_models(self):
        return self._guarded_rmtree(OLLAMA_MODELS, DICT_DIR)

    def open_folder(self):
        try:
            os.startfile(load_config().get("history_dir", APP_DIR))
        except OSError:
            pass
        return True

    def pick_folder(self):
        res = WIN.create_file_dialog(webview.FOLDER_DIALOG)
        if res:
            folder = os.path.normpath(res[0] if isinstance(res, (list, tuple)) else res)
            save_config_patch({"history_dir": folder})
            self._storage_cache = (0.0, None)
            return folder
        return None

    def export_history(self):
        res = WIN.create_file_dialog(webview.SAVE_DIALOG, save_filename="dictator-history.md",
                                     file_types=("Markdown (*.md)", "Text (*.txt)"))
        if not res:
            return None
        path = res if isinstance(res, str) else res[0]
        entries = sorted(self._entries(), key=lambda e: e["t"])
        lines = ["# Dictator history\n"]
        for e in entries:
            lines.append(f"**{e['t']:%Y-%m-%d %H:%M}**\n\n{e['cleaned']}\n")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            return len(entries)
        except OSError:
            return None

    def purge_history(self):
        try:
            os.remove(history_path(load_config()))
        except OSError:
            pass
        self._hist_cache = (0.0, [])
        return True

    def backup_settings(self):
        res = WIN.create_file_dialog(webview.SAVE_DIALOG, save_filename="dictator-backup.json",
                                     file_types=("JSON (*.json)",))
        if not res:
            return None
        path = res if isinstance(res, str) else res[0]
        cfg = load_config()
        data = {k: cfg[k] for k in BACKUP_KEYS if k in cfg}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return True
        except OSError:
            return None

    def restore_settings(self):
        res = WIN.create_file_dialog(webview.OPEN_DIALOG, file_types=("JSON (*.json)",))
        if not res:
            return None
        path = res[0] if isinstance(res, (list, tuple)) else res
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        save_config_patch({k: data[k] for k in BACKUP_KEYS if k in data})
        return True


WIN = None


def main():
    global WIN
    # own taskbar identity (not pythonw's) so the grouped icon + label are ours
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Dictator.Dashboard")
    except Exception:
        pass
    api = Api()
    WIN = webview.create_window(
        "Dictator", url=INDEX, js_api=api,
        width=1200, height=840, min_size=(940, 700),
        background_color="#000000", text_select=True)
    # icon= sets the window/taskbar icon; without it pywebview extracts
    # pythonw.exe's icon (the generic Python snake)
    webview.start(gui="edgechromium", icon=ICON if os.path.exists(ICON) else None,
                  debug=("--debug" in sys.argv))


if __name__ == "__main__":
    main()
