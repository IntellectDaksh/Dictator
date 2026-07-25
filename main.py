"""Dictator — local push-to-talk dictation for Windows and macOS.

Hold Ctrl+Win (Ctrl+Cmd on macOS) and speak; release to stop. Audio is
transcribed locally with faster-whisper (CUDA if available), cleaned up by a
local Ollama model, and typed into whatever window has keyboard focus. Fully
offline after setup: the only network traffic is Ollama on localhost.
"""
import ctypes
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

if IS_WIN:
    import winreg
    import winsound
    from ctypes import wintypes

# pip-installed nvidia cublas/cudnn DLLs aren't on the Windows DLL search path;
# ctranslate2 resolves them via PATH, so prepend their bin dirs before import.
# CUDA is Windows/Linux-only — faster-whisper falls back to CPU on macOS.
if IS_WIN:
    for _pkg in ("cublas", "cudnn"):
        _bin = os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", _pkg, "bin")
        if os.path.isdir(_bin):
            os.environ["PATH"] = _bin + os.pathsep + os.environ["PATH"]

import keyboard
import numpy as np
import pystray
import sounddevice as sd
import tkinter as tk
import tkinter.font as tkfont
from tkinter import colorchooser, filedialog, messagebox, ttk
from PIL import Image, ImageDraw, ImageTk
from faster_whisper import WhisperModel

# ---------------------------------------------------------------- config

if IS_WIN:
    APP_DIR = os.path.join(os.environ.get("APPDATA", "."), "Dictator")
elif IS_MAC:
    APP_DIR = os.path.join(os.path.expanduser("~/Library/Application Support"), "Dictator")
else:
    APP_DIR = os.path.join(os.path.expanduser("~/.config"), "Dictator")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
# live state the separate-process webview dashboard can't see in-process
# (whisper device/load status, last injected text) — mirrored here for it
RUNTIME_PATH = os.path.join(APP_DIR, "runtime.json")

# ---- Uideas OS "Nothing" typeface set -------------------------------------
# Real Space Grotesk / Space Mono / Doto (converted from the Uideas OS woff2s
# to TTF, shipped in assets/fonts). Loaded private-to-process via GDI so no
# install/admin is needed; if the load fails we fall back to system fonts and
# the dashboard still renders, just in Segoe/Consolas.
FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "assets", "fonts")
FR_PRIVATE = 0x10
# family names as GDI/Tk see them (see fontTools name table), with fallbacks
SANS = "Space Grotesk Light"
MONO = "Space Mono"
DOTO = "Doto Black"
SANS_FALLBACK, MONO_FALLBACK, DOTO_FALLBACK = "Segoe UI", "Consolas", "Consolas"


def load_private_fonts():
    """Register the bundled TTFs for this process only. Safe to call once at
    import; returns False (fall back to system fonts) on any failure."""
    if not IS_WIN:
        return False  # GDI is Windows-only; Tk fallback dashboard uses system fonts on macOS
    try:
        add = ctypes.windll.gdi32.AddFontResourceExW
        ok = False
        for name in ("SpaceGrotesk.ttf", "SpaceMono.ttf",
                     "SpaceMono-Bold.ttf", "Doto.ttf"):
            path = os.path.join(FONTS_DIR, name)
            if os.path.exists(path) and add(path, FR_PRIVATE, 0):
                ok = True
        return ok
    except Exception:
        return False


_FONTS_LOADED = load_private_fonts()


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
    "highlight_color": None,              # None = default green; streaks/positive states
    "dash_geometry": None,                # remembered dashboard window size/pos
    "auto_punctuate": True,               # capitalize/punctuate instant-mode + LLM-failure fallback text
    "tone_overrides": {"casual": [], "formal": [], "verbatim": []},  # extra exe names per tone
    "snippets": {},                       # {trigger phrase: expansion text}
    "pinned": [],                         # timestamps (isoformat) of starred dictations
    "auto_theme": False,                  # dark 7pm-7am, light otherwise
    "language": "en",                     # ISO 639-1 code, or "auto" to detect per-dictation
    "silence_auto_stop": False,           # hands-free mode: auto-stop after trailing silence
    "silence_threshold": 0.02,            # RMS level below which audio counts as silence
    "silence_duration_s": 1.5,            # seconds of continuous silence before auto-stop
    "sound_enabled": False,               # start/stop chime via winsound
    "redact_patterns": [],                # words/phrases scrubbed from history before it's written
    "profiles": {},                       # {name: {hotkey_mods, hotkey_mode, model_size, language}}
    "active_profile": None,
}

SAMPLE_RATE = 16000
PREFERRED_MODELS = ("qwen3:14b", "qwen2.5:7b-instruct", "llama3.1:8b")
# whisper models download here instead of C:\Users\<you>\.cache — safe to
# delete, they just re-download on next launch
WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WHISPER_CACHE = os.path.join(WORKSPACE_DIR, "Cache", "whisper")

SYSTEM_PROMPT = (
    "You clean up raw speech transcripts into what the speaker intended to "
    "write. Remove filler words and verbal disfluencies (um, uh, like, you "
    "know, I mean, sort of, kind of). When the speaker corrects, restates, "
    "or contradicts something they just said, keep ONLY their final intended "
    "version and silently drop the discarded part — do not narrate the "
    "correction. Fix punctuation, capitalization, and obvious grammar. Do "
    "not add information, opinions, or content the speaker didn't say. Do "
    "not change their tone, formality, or word choice beyond what's needed "
    "for fluency. Keep the literal phrases 'new line', 'new paragraph', and "
    "'bullet point' unchanged wherever they appear. Output only the corrected "
    "text — no preamble, no quotes around it, no explanation, no meta-commentary."
)
ONE_SHOT_IN = "lets connect at 12 pm um no actually 11 pm"
ONE_SHOT_OUT = "Let's connect at 11pm."


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


# ---------------------------------------------------------------- text injection

if IS_WIN:
    user32 = ctypes.windll.user32
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    VK_RETURN = 0x0D
    VK_BACK = 0x08
    VK_NOOP = 0xE8  # unassigned virtual key

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_ubyte * 32)]
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    def _key_input(vk=0, scan=0, flags=0):
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.ki = KEYBDINPUT(vk, scan, flags, 0, None)
        return inp

    def _send_inputs(inputs):
        arr = (INPUT * len(inputs))(*inputs)
        user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))

    def send_text_keystrokes(text):
        """Type text via KEYEVENTF_UNICODE — never touches the clipboard."""
        events = []
        for ch in text.replace("\r\n", "\n"):
            if ch == "\n":
                events.append(_key_input(vk=VK_RETURN))
                events.append(_key_input(vk=VK_RETURN, flags=KEYEVENTF_KEYUP))
            else:
                code = ord(ch)
                events.append(_key_input(scan=code, flags=KEYEVENTF_UNICODE))
                events.append(_key_input(scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        _send_inputs(events)

    def send_backspaces(count):
        events = []
        for _ in range(max(0, count)):
            events.append(_key_input(vk=VK_BACK))
            events.append(_key_input(vk=VK_BACK, flags=KEYEVENTF_KEYUP))
        if events:
            _send_inputs(events)

    def send_noop_key():
        """Tap an unassigned key so a lone Win-key release doesn't open Start."""
        _send_inputs([_key_input(vk=VK_NOOP),
                      _key_input(vk=VK_NOOP, flags=KEYEVENTF_KEYUP)])

else:
    # macOS: no clipboard-free unicode-keystroke API without pyobjc, so we
    # drive System Events via AppleScript instead — one osascript call per
    # dictation (not per keystroke). Requires the running process (Terminal/
    # pythonw) to be granted Accessibility + Input Monitoring permission in
    # System Settings, the same trust boundary SendInput needs admin-free on
    # Windows. ponytail: shells out to osascript rather than adding pyobjc as
    # a dependency; revisit if this proves too slow for very long dictations.
    def _applescript_run(script):
        try:
            subprocess.run(["osascript", "-e", script], check=False,
                           capture_output=True, timeout=10)
        except Exception as e:
            print(f"osascript failed: {e}")

    def _applescript_quote(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')

    def send_text_keystrokes(text):
        lines = text.replace("\r\n", "\n").split("\n")
        stmts = []
        for i, line in enumerate(lines):
            if line:
                stmts.append(f'keystroke "{_applescript_quote(line)}"')
            if i < len(lines) - 1:
                stmts.append("key code 36")  # Return
        if stmts:
            _applescript_run('tell application "System Events"\n' + "\n".join(stmts) + "\nend tell")

    def send_backspaces(count):
        count = max(0, count)
        if count:
            _applescript_run(f'tell application "System Events"\nrepeat {count} times\n'
                             'key code 51\nend repeat\nend tell')  # 51 = Delete

    def send_noop_key():
        pass  # no Start-menu equivalent to guard against on macOS


# ---------------------------------------------------------------- hotkey

HOTKEY_PRESETS = [
    ("Ctrl + Win", ["ctrl", "win"]),
    ("Ctrl + Alt", ["ctrl", "alt"]),
    ("Ctrl + Shift", ["ctrl", "shift"]),
    ("Alt + Win", ["alt", "win"]),
]


def win_pressed():
    if IS_MAC:
        return keyboard.is_pressed("command")
    return keyboard.is_pressed("left windows") or keyboard.is_pressed("right windows")


def _mod_pressed(mod):
    return win_pressed() if mod == "win" else keyboard.is_pressed(mod)


def hotkey_down(cfg):
    mods = cfg.get("hotkey_mods") or ["ctrl", "win"]
    return all(_mod_pressed(m) for m in mods)


def wait_keys_released(cfg, timeout=2.0):
    mods = cfg.get("hotkey_mods") or ["ctrl", "win"]
    t0 = time.time()
    while any(_mod_pressed(m) for m in mods) and time.time() - t0 < timeout:
        time.sleep(0.01)


# ---------------------------------------------------------------- app-aware tone

CASUAL_EXES = {"slack.exe", "discord.exe", "telegram.exe", "whatsapp.exe",
               "slack", "discord", "telegram", "whatsapp"}  # mac app names, no .exe
FORMAL_EXES = {"outlook.exe", "olk.exe", "thunderbird.exe", "winword.exe",
               "microsoft outlook", "thunderbird", "microsoft word"}
VERBATIM_EXES = {"code.exe", "devenv.exe", "windowsterminal.exe", "wt.exe",
                 "cmd.exe", "powershell.exe", "pycharm64.exe", "idea64.exe",
                 "code", "terminal", "iterm2", "pycharm", "intellij idea"}
TONE_HINT = {
    "casual": " Keep the tone relaxed and conversational.",
    "formal": " Polish into a clear, professional register.",
}


def foreground_app():
    """(app_name, window_title) of the focused window/app, lowercased.
    Windows: exe basename (e.g. "code.exe"). macOS: app name (e.g. "code"),
    via AppleScript — no window title equivalent, so title is left empty."""
    if IS_MAC:
        out = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first process whose frontmost is true'],
            capture_output=True, text=True, timeout=5, check=False)
        return out.stdout.strip().lower(), ""
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    k32 = ctypes.windll.kernel32
    exe = ""
    h = k32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
    if h:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            exe = os.path.basename(buf.value).lower()
        k32.CloseHandle(h)
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 256)
    return exe, title.value.lower()


def tone_for(exe, title, cfg=None):
    overrides = (cfg or {}).get("tone_overrides") or {}
    if exe in set(overrides.get("verbatim", ())) | VERBATIM_EXES:
        return "verbatim"  # code/terminal targets: type exactly what was said
    if exe in set(overrides.get("casual", ())) | CASUAL_EXES:
        return "casual"
    if exe in set(overrides.get("formal", ())) | FORMAL_EXES or "gmail" in title:
        return "formal"
    return None


# ---------------------------------------------------------------- text shaping

# spoken formatting commands become real formatting (applied after cleanup)
SPOKEN_CMDS = [
    (re.compile(r"[,;:]?\s*\bnew paragraph\b[,.;:]?\s*", re.I), "\n\n"),
    (re.compile(r"[,;:]?\s*\bnew line\b[,.;:]?\s*", re.I), "\n"),
    (re.compile(r"[,;:]?\s*\bbullet point\b[,.;:]?\s*", re.I), "\n- "),
]


VOICE_COMMANDS = [
    (re.compile(r"[,.\s]*\bmake (?:it|that|this)(?: more)? formal\.?$", re.I), "formal"),
    (re.compile(r"[,.\s]*\bmake (?:it|that|this)(?: more)? casual\.?$", re.I), "casual"),
]


def apply_voice_command(raw):
    """Trailing 'make it formal/casual' overrides tone for this dictation only.
    Returns (text_with_command_stripped, tone_override_or_None)."""
    for rx, tone in VOICE_COMMANDS:
        if rx.search(raw):
            return rx.sub("", raw).strip(), tone
    return raw, None


def apply_commands(text):
    for rx, rep in SPOKEN_CMDS:
        text = rx.sub(rep, text)
    return text.strip()


def basic_punctuate(text):
    """Capitalize + terminal punctuation only — no LLM involved."""
    t = text.strip()
    if not t:
        return t
    t = t[0].upper() + t[1:]
    if t[-1] not in ".?!":
        t += "."
    return t


def quick_clean(raw, cfg=None):
    """Instant mode: short phrases skip the LLM round-trip."""
    if cfg is not None and not cfg.get("auto_punctuate", True):
        return raw.strip()
    return basic_punctuate(raw)


def expand_snippet(text, cfg):
    """Exact-match trigger phrase -> canned expansion, else unchanged."""
    snippets = cfg.get("snippets") or {}
    return snippets.get(text.strip().lower(), text)


# ---------------------------------------------------------------- STT

class Transcriber:
    def __init__(self, size):
        self._lock = threading.Lock()
        self.size = size
        self.device = "?"
        self.model = None

    def load(self):
        with self._lock:
            try:
                model = WhisperModel(self.size, device="cuda", compute_type="float16",
                                     download_root=WHISPER_CACHE)
                # warmup forces CUDA init so a broken CUDA falls back at load
                # time, not mid-dictation
                list(model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32),
                                      language="en")[0])
                self.device = "CUDA"
            except Exception as e:
                print(f"CUDA unavailable ({type(e).__name__}), using CPU")
                model = WhisperModel(self.size, device="cpu", compute_type="int8",
                                     download_root=WHISPER_CACHE)
                self.device = "CPU"
            self.model = model
        print(f"STT ready: {self.size} on {self.device}")

    def transcribe(self, audio, vocab=(), language="en"):
        with self._lock:
            if self.model is None:
                return ""
            # vocab is NOT fed to Whisper as initial_prompt: on quiet/unclear
            # audio the model would latch onto the prompt and echo the
            # vocabulary word back verbatim instead of transcribing the real
            # speech (reproduced: every dictation came back as just the one
            # configured vocab word, regardless of what was said). Rewording
            # the prompt didn't help — dropping it did. Vocabulary is still
            # used for spelling in the cleanup step (ollama_cleanup).
            prompt = None
            # vad_filter drops trailing silence/breath noise and
            # no_repeat_ngram_size + condition_on_previous_text=False stop the
            # stuck-repeating-letter/gibberish hallucination short clips trigger
            segments, _ = self.model.transcribe(
                audio, language=(None if language == "auto" else language),
                beam_size=5, initial_prompt=prompt,
                vad_filter=True, vad_parameters=dict(min_silence_duration_ms=300),
                no_repeat_ngram_size=3, condition_on_previous_text=False)
            return " ".join(s.text.strip() for s in segments).strip()

    def reload(self, size):
        self.size = size
        self.load()


# ---------------------------------------------------------------- Ollama cleanup

def ollama_get(url, path, timeout=3.0):
    with urllib.request.urlopen(url + path, timeout=timeout) as r:
        return json.load(r)


def resolve_ollama_model(cfg):
    if cfg["ollama_model"] != "auto":
        return cfg["ollama_model"]
    try:
        names = [m["name"] for m in ollama_get(cfg["ollama_url"], "/api/tags")["models"]]
    except Exception:
        return None
    for want in PREFERRED_MODELS:
        for name in names:
            if name == want or name.startswith(want.split(":")[0]):
                return name
    return None


def ollama_cleanup(raw, cfg, model, tone=None):
    """Return cleaned text, or None on any failure (caller falls back to raw)."""
    if not model:
        return None
    system = SYSTEM_PROMPT
    if cfg["vocabulary"]:
        system += (" Spell these words exactly as written: "
                   + ", ".join(cfg["vocabulary"]) + ".")
    system += TONE_HINT.get(tone, "")
    payload = json.dumps({
        "model": model,
        "stream": False,
        # qwen3 is a thinking model: left on, it spends seconds reasoning before
        # answering and blows past ollama_timeout_s, so cleanup silently falls
        # back to raw. Disable it — for filler-stripping we want the direct
        # answer, not a reasoning pass. Ignored by non-thinking models.
        "think": False,
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": ONE_SHOT_IN},
            {"role": "assistant", "content": ONE_SHOT_OUT},
            {"role": "user", "content": raw},
        ],
    }).encode()
    req = urllib.request.Request(
        cfg["ollama_url"] + "/api/chat", data=payload,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=cfg["ollama_timeout_s"]) as r:
            text = json.load(r)["message"]["content"].strip()
        return text or None
    except Exception as e:
        print(f"cleanup skipped ({type(e).__name__}) — using raw transcript")
        return None


# ---------------------------------------------------------------- injection

def inject_text(text, cfg):
    """Always simulated keystrokes — the clipboard is never touched."""
    wait_keys_released(cfg)
    send_text_keystrokes(text)


# ---------------------------------------------------------------- history

def redact(text, patterns):
    for p in patterns:
        p = p.strip()
        if not p:
            continue
        text = re.sub(re.escape(p), "[redacted]", text, flags=re.I)
    return text


def log_history(cfg, raw, cleaned, secs, app=None, tone=None):
    if not cfg["log_history"]:
        return  # nothing is ever written to disk when the flag is off
    os.makedirs(cfg["history_dir"], exist_ok=True)
    patterns = cfg.get("redact_patterns") or []
    entry = {"timestamp": datetime.now().isoformat(timespec="seconds"),
             "raw_transcript": redact(raw, patterns), "cleaned_text": redact(cleaned, patterns),
             "duration_s": round(secs, 2), "app": app, "tone": tone}
    with open(history_path(cfg), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- start on login

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
LAUNCH_AGENT_PATH = os.path.expanduser(
    "~/Library/LaunchAgents/com.uideas.dictator.plist")


def set_start_on_login(on):
    if IS_MAC:
        if on:
            python_bin = sys.executable
            plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.uideas.dictator</string>
  <key>ProgramArguments</key><array>
    <string>{python_bin}</string><string>{os.path.abspath(__file__)}</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict></plist>"""
            os.makedirs(os.path.dirname(LAUNCH_AGENT_PATH), exist_ok=True)
            with open(LAUNCH_AGENT_PATH, "w", encoding="utf-8") as f:
                f.write(plist)
            subprocess.run(["launchctl", "load", LAUNCH_AGENT_PATH], check=False, capture_output=True)
        else:
            subprocess.run(["launchctl", "unload", LAUNCH_AGENT_PATH], check=False, capture_output=True)
            if os.path.exists(LAUNCH_AGENT_PATH):
                os.remove(LAUNCH_AGENT_PATH)
        return
    # venvs put pythonw.exe under Scripts\; base installs put it at the prefix root
    scripts_pythonw = os.path.join(sys.prefix, "Scripts", "pythonw.exe")
    pythonw = scripts_pythonw if os.path.exists(scripts_pythonw) \
        else os.path.join(sys.prefix, "pythonw.exe")
    cmd = f'"{pythonw}" "{os.path.abspath(__file__)}"'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "Dictator", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, "Dictator")
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------- overlay pill

def round_rect(cv, x1, y1, x2, y2, r, **kw):
    """Rounded rectangle on a canvas (smoothed polygon)."""
    pts = (x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1)
    return cv.create_polygon(pts, smooth=True, **kw)


STATE_STYLE = {
    "listening": ("●  listening", "#c2413d", "#fff7f5"),
    "thinking": ("…  polishing", "#b87918", "#fff8eb"),
    "review": ("□  review", "#3b6f9f", "#edf6ff"),
    "done": ("✓  done", "#237a57", "#effaf5"),
}


class Overlay:
    """Status UI: a single persistent bar at the bottom-center of the screen.
    Shows "on" when idle, listening/thinking/done during dictation. Tk objects
    only touched from the main thread; other threads call set_state()."""

    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self._pending = None
        self._pending_detail = None
        self._pending_level = 0.0
        self._lock = threading.Lock()
        self._state = "idle"
        self._detail = None
        self._done_until = 0.0
        self._rendered = None
        self._visible = False
        self._fade_job = None
        self._last_active = time.time()
        self.IDLE_FADE_AFTER = 10.0
        self.IDLE_FADE_ALPHA = 0.05

        trans = "#000001"  # transparent key color so the pill corners are round
        self.bar = tk.Toplevel(root)
        self.bar.overrideredirect(True)
        self.bar.attributes("-topmost", True)
        self.bar.attributes("-alpha", 0.0)
        self.bar.configure(bg=trans)
        self.bar.attributes("-transparentcolor", trans)
        self.bar_cv = tk.Canvas(self.bar, width=156, height=34, bg=trans,
                                highlightthickness=0)
        self.bar_cv.pack()
        self.bar_shadow = round_rect(self.bar_cv, 5, 5, 153, 33, 13,
                                     fill="#0b0d12")
        self.bar_rect = round_rect(self.bar_cv, 0, 0, 148, 28, 13, fill="#1f242c")
        self.bar_text = self.bar_cv.create_text(74, 14, text="●  ready",
                                                fill="#f8fafc",
                                                font=("Segoe UI Semibold", 9))
        self.bar_level = round_rect(self.bar_cv, 14, 24, 14, 26, 1, fill="#ffffff",
                                    outline="")
        self.bar.withdraw()
        self._poll()

    def set_state(self, state, detail=None):  # thread-safe
        with self._lock:
            self._pending = state
            self._pending_detail = detail

    def set_level(self, level):  # thread-safe — 0..1 input mic level while listening
        with self._lock:
            self._pending_level = max(0.0, min(1.0, level))

    def _place_bar(self):
        sw = self.bar.winfo_screenwidth()
        sh = self.bar.winfo_screenheight()
        self.bar.geometry(f"+{(sw - 156) // 2}+{sh - 96}")

    def _fade_to(self, target, rate=0.4):
        if self._fade_job:
            self.bar.after_cancel(self._fade_job)
            self._fade_job = None

        def step():
            try:
                cur = float(self.bar.attributes("-alpha"))
            except tk.TclError:
                return
            nxt = cur + (target - cur) * rate
            if abs(target - nxt) < 0.03:
                nxt = target
            self.bar.attributes("-alpha", nxt)
            if nxt == target:
                self._fade_job = None
                if target == 0:
                    self.bar.withdraw()
            else:
                self._fade_job = self.bar.after(15, step)

        step()

    def _poll(self):
        with self._lock:
            pending, self._pending = self._pending, None
            detail, self._pending_detail = self._pending_detail, None
            level = self._pending_level
        if pending == "hide":
            self._state = "idle"
        elif pending in STATE_STYLE:
            self._state = pending
            self._detail = detail
            if pending == "done":
                self._done_until = time.time() + 1.4 if detail else time.time() + 0.9
        if self._state == "done" and time.time() > self._done_until:
            self._state = "idle"
        if self._state != "idle":
            self._last_active = time.time()
        self._render(level)
        self._apply_idle_fade()
        self.root.after(50, self._poll)

    def _apply_idle_fade(self):
        if not self._visible or self._state != "idle" or self._fade_job:
            return
        idle_for = time.time() - self._last_active
        target = self.IDLE_FADE_ALPHA if idle_for > self.IDLE_FADE_AFTER else 1.0
        try:
            cur = float(self.bar.attributes("-alpha"))
        except tk.TclError:
            return
        if abs(cur - target) > 0.01:
            self._fade_to(target, rate=0.06)

    def _render(self, level=0.0):
        key = (self._state, self._detail, self.cfg["enabled"], self.cfg["show_status_bar"])
        if key == self._rendered:
            return
        self._rendered = key
        state, detail, enabled, show_bar = key
        if not (enabled and show_bar):
            if self._visible:
                self._visible = False
                self._fade_to(0)
            return
        if state == "idle":
            text, color, fg = "●  ready", "#1f242c", "#f8fafc"
        elif state == "done" and detail:
            text, color, fg = f"✓  {detail}", *STATE_STYLE["done"][1:]
        else:
            text, color, fg = STATE_STYLE[state]
        self.bar_cv.itemconfigure(self.bar_rect, fill=color)
        self.bar_cv.itemconfigure(self.bar_text, text=text, fill=fg)
        if state == "listening":
            self.bar_cv.coords(self.bar_level, 14, 24, 14 + level * 120, 26)
            self.bar_cv.itemconfigure(self.bar_level, fill=fg, state="normal")
        else:
            self.bar_cv.itemconfigure(self.bar_level, state="hidden")
        self._place_bar()
        if not self._visible:
            self._visible = True
            self.bar.attributes("-alpha", 0.0)
            self.bar.deiconify()
        self._fade_to(1.0)
        self.bar.attributes("-topmost", True)


# ---------------------------------------------------------------- app

class App:
    def __init__(self):
        self.cfg = load_config()
        self._apply_theme()
        self.running = True
        self.transcriber = Transcriber(self.cfg["model_size"])
        self.ollama_model = None
        self.overlay = None  # set from main thread
        self.icon = None
        self.session = []  # recent dictations for the dashboard list (memory-capped)
        self.totals = {"n": 0, "raw_w": 0, "cln_w": 0, "secs": 0.0}
        self.days = set()  # dates with >=1 dictation, for the streak stat
        self._search_cache = ("", [])
        self.last_injected_text = ""
        self._storage_cache = (0.0, {})
        self._health_cache = (0.0, {})
        self._storage_refreshing = False
        self._health_refreshing = False
        self._recent_render_key = None
        self._last_stats_key = None
        self._model_loading = None
        self._capturing_hotkey = False
        self._load_history()  # seed totals + recent list from past sessions
        self.ui_q = queue.Queue()  # marshals tray clicks onto the tk thread

    # Keys the running app can adopt live from a config.json edit made by
    # another process (the webview dashboard, or Doofus AI). Most just need
    # self.cfg updated — the hotkey loop, mic capture and pipeline all read
    # self.cfg fresh each time. model_size (whisper reload) and enabled (tray
    # icon) get extra side effects in apply() below.
    SYNC_KEYS = ("snippets", "vocabulary", "tone_overrides", "auto_punctuate",
                 "review_before_typing", "hotkey_mods", "hotkey_mode",
                 "input_device", "enabled", "log_history", "model_size",
                 "history_dir", "show_status_bar", "theme", "accent_color",
                 "highlight_color", "auto_theme", "language", "silence_auto_stop",
                 "silence_threshold", "silence_duration_s", "sound_enabled", "redact_patterns")

    def _watch_config_file(self):
        """Picks up config.json edits made by something other than this
        process (the webview dashboard, or Doofus AI's dictator_config_set)
        without a restart. Comparing values instead of tracking "was this our
        own write" means Dictator's own save_config() calls are naturally a
        no-op here — the file already matches self.cfg for these keys."""
        last_mtime = 0
        while True:
            time.sleep(2)
            try:
                mtime = os.path.getmtime(CONFIG_PATH)
            except OSError:
                continue
            if mtime == last_mtime:
                continue
            last_mtime = mtime
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    disk_cfg = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            changed = {k: disk_cfg[k] for k in self.SYNC_KEYS
                       if k in disk_cfg and disk_cfg[k] != self.cfg.get(k)}
            if not changed:
                continue

            def apply(changed=changed):
                self.cfg.update(changed)
                if "model_size" in changed:
                    self._health_cache = (0.0, {})
                    size = changed["model_size"]
                    threading.Thread(
                        target=lambda: (self.transcriber.reload(size), self._write_runtime()),
                        daemon=True).start()
                if "enabled" in changed:
                    self._refresh_tray_icon()
                self._write_runtime()
                if getattr(self, "dash", None) and self.dash.winfo_exists():
                    self._dash_reopen()
            self.ui_q.put(apply)

    def _record_dictation(self, raw, cleaned, secs, t):
        """Runs on the tk thread (via ui_q) — safe to touch session/totals/dashboard."""
        self._tally(raw, cleaned, secs, t)
        self.session.append({"t": t, "raw": raw, "cleaned": cleaned, "secs": secs})
        del self.session[:-100]  # cap memory, totals keep counting
        if getattr(self, "dash", None) and self.dash.winfo_exists():
            self._dash_refresh()

    def _tally(self, raw, cleaned, secs, t):
        self.totals["n"] += 1
        self.totals["raw_w"] += len(raw.split())
        self.totals["cln_w"] += len(cleaned.split())
        self.totals["secs"] += secs
        self.days.add(t.date())

    def _streak(self):
        d, n = date.today(), 0
        if d not in self.days:  # today not dictated yet — streak counts from yesterday
            d -= timedelta(days=1)
        while d in self.days:
            n += 1
            d -= timedelta(days=1)
        return n

    def _daily_counts(self, days=7):
        counts = {}
        for e in self.session:
            d = e["t"].date()
            counts[d] = counts.get(d, 0) + 1
        today = date.today()
        return [counts.get(today - timedelta(days=i), 0) for i in range(days - 1, -1, -1)]

    def _redraw_sparkline(self):
        item = getattr(self, "_spark_item", None)
        if not item:
            return
        cv, line = item
        width = cv.winfo_width()
        if width <= 1:
            return
        values = self._daily_counts(7)
        top = max(values) or 1
        step = width / (len(values) - 1)
        base = (cv.winfo_height() or 94) - 6
        pts = []
        for i, v in enumerate(values):
            pts.extend([i * step, base - (v / top) * 12])
        cv.coords(line, *pts)
        cv.delete("spark-today")
        cv.create_oval(pts[-2] - 3, pts[-1] - 3, pts[-2] + 3, pts[-1] + 3,
                       fill=self.ACCENT, outline="", tags="spark-today")

    def _load_history(self):
        try:
            with open(history_path(self.cfg), encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                        item = {"t": datetime.fromisoformat(e["timestamp"]),
                                "raw": e["raw_transcript"],
                                "cleaned": e["cleaned_text"],
                                "secs": e.get("duration_s", 0.0)}
                    except (ValueError, KeyError):
                        continue
                    self._tally(item["raw"], item["cleaned"], item["secs"], item["t"])
                    self.session.append(item)
                    del self.session[:-100]  # cap memory, totals keep counting
        except OSError:
            pass

    # ---- recording / pipeline (hotkey thread + workers)

    def record_stream(self, stop, silence_stop=False):
        chunks = []
        watch_silence = silence_stop and self.cfg.get("silence_auto_stop", False)
        threshold = self.cfg.get("silence_threshold", 0.02)
        duration = self.cfg.get("silence_duration_s", 1.5)
        silent_since = [None]

        def cb(indata, frames, t, status):
            chunks.append(indata.copy())
            level = float(np.abs(indata).mean())
            self.overlay.set_level(level * 8)  # rough level, 0..1ish
            if watch_silence:
                if level < threshold:
                    if silent_since[0] is None:
                        silent_since[0] = time.time()
                else:
                    silent_since[0] = None

        def should_stop():
            if stop():
                return True
            return watch_silence and silent_since[0] is not None \
                and time.time() - silent_since[0] >= duration

        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                device=self.cfg["input_device"], callback=cb):
                while not should_stop():
                    time.sleep(0.02)
        except Exception as e:
            print(f"mic unavailable: {e}")
            while not stop():
                time.sleep(0.02)
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)[:, 0] if chunks else np.zeros(0, dtype=np.float32)

    def _chime(self, which):
        if not self.cfg.get("sound_enabled", False):
            return
        try:
            if IS_MAC:
                sound = "Tink" if which == "start" else "Pop"
                subprocess.Popen(["afplay", f"/System/Library/Sounds/{sound}.aiff"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                winsound.MessageBeep(winsound.MB_ICONASTERISK if which == "start" else winsound.MB_OK)
        except Exception:
            pass

    def hotkey_loop(self):
        last_tap = 0.0
        while self.running:
            if not (self.cfg["enabled"] and hotkey_down(self.cfg)):
                time.sleep(0.02)
                continue
            target_hwnd = user32.GetForegroundWindow() if IS_WIN else None
            target_exe, _title = foreground_app()
            tone = tone_for(target_exe, _title, self.cfg)  # capture target app before dictating
            self.overlay.set_state("listening")
            self._chime("start")
            t0 = time.time()
            audio = self.record_stream(lambda: not hotkey_down(self.cfg))
            if "win" in (self.cfg.get("hotkey_mods") or ["ctrl", "win"]) \
                    and win_pressed() and not hotkey_down(self.cfg):
                send_noop_key()  # stop lone Win release from opening Start
            if time.time() - t0 < 0.35:  # a tap, not a hold
                # toggle mode: any tap starts hands-free recording, no double-tap needed
                hands_free = self.cfg.get("hotkey_mode") == "toggle" or t0 - last_tap < 0.6
                if hands_free:
                    last_tap = 0.0
                    audio = self.record_stream(lambda: hotkey_down(self.cfg), silence_stop=True)
                    if "win" in (self.cfg.get("hotkey_mods") or ["ctrl", "win"]) \
                            and win_pressed() and not hotkey_down(self.cfg):
                        send_noop_key()
                    wait_keys_released(self.cfg)
                else:
                    last_tap = t0
                    self.overlay.set_state("hide")
                    continue
            if len(audio) / SAMPLE_RATE < 0.3:
                self.overlay.set_state("hide")
                continue
            self.overlay.set_state("thinking")
            threading.Thread(target=self.process, args=(audio, tone, target_hwnd, target_exe),
                             daemon=True).start()

    def process(self, audio, tone=None, target_hwnd=None, target_exe=None):
        secs = len(audio) / SAMPLE_RATE
        try:
            raw = self.transcriber.transcribe(audio, self.cfg["vocabulary"], self.cfg.get("language", "en"))
            if not raw:
                print("(no speech detected)")
                self.overlay.set_state("hide")
                return
            raw, voice_tone = apply_voice_command(raw)
            if voice_tone:
                tone = voice_tone
            if not raw:
                print("(no speech detected)")
                self.overlay.set_state("hide")
                return
            if tone == "verbatim":
                cleaned = raw
            elif len(raw.split()) < 6:
                cleaned = quick_clean(raw, self.cfg)  # instant mode: no LLM round-trip
            else:
                if self.ollama_model is None:
                    self.ollama_model = resolve_ollama_model(self.cfg)
                fallback = basic_punctuate(raw) if self.cfg.get("auto_punctuate", True) else raw
                cleaned = ollama_cleanup(raw, self.cfg, self.ollama_model, tone) or fallback
            cleaned = apply_commands(cleaned)
            cleaned = expand_snippet(cleaned, self.cfg)
            if self.cfg.get("review_before_typing") and len(cleaned) > 1000:
                self.overlay.set_state("review")
                reviewed = self._review_text(raw, cleaned)
                if reviewed is None:
                    self.overlay.set_state("hide")
                    return
                cleaned = reviewed.strip()
            if not cleaned:
                self.overlay.set_state("hide")
                return
            print(f"raw:     {raw}\ncleaned: {cleaned}")
            if target_hwnd:
                try:
                    user32.SetForegroundWindow(target_hwnd)
                    time.sleep(0.05)
                except Exception:
                    pass
            inject_text(cleaned, self.cfg)
            self._chime("stop")
            self.last_injected_text = cleaned
            self._write_runtime()  # refresh last_text for the dashboard's undo/copy-last
            now = datetime.now()
            log_history(self.cfg, raw, cleaned, secs, app=target_exe, tone=tone)
            self.ui_q.put(lambda: self._record_dictation(raw, cleaned, secs, now))
            snippet = cleaned if len(cleaned) <= 28 else cleaned[:27] + "…"
            self.overlay.set_state("done", detail=snippet.replace("\n", " "))
        except Exception as e:
            print(f"pipeline error: {type(e).__name__}: {e}")
            self.overlay.set_state("hide")

    # ---- dashboard window (tk main thread only)

    THEMES = {
        # Uideas OS "Nothing" dialect: OLED black, flat 1px-border surfaces,
        # red (#D71921) as the single event color. Token names mirror the
        # Uideas OS CSS custom-properties so the two dashboards stay in sync.
        # Overlay pill keeps its own hardcoded palette — it is not themed here.
        "dark": dict(BG="#000000", PANEL="#111111", CARD="#1A1A1A", CARD_2="#1A1A1A",
                     BORDER="#222222", BORDER2="#333333",
                     FG="#E8E8E8", DISPLAY="#FFFFFF", MUT="#999999", SUBTLE="#666666",
                     ACCENT="#D71921", ACCENT_DARK="#3A1012", DANGER="#D71921",
                     OK="#4A9E5C", WARN="#D4A843"),
        "light": dict(BG="#F5F5F5", PANEL="#FFFFFF", CARD="#F0F0F0", CARD_2="#F0F0F0",
                      BORDER="#E8E8E8", BORDER2="#CCCCCC",
                      FG="#1A1A1A", DISPLAY="#000000", MUT="#666666", SUBTLE="#999999",
                      ACCENT="#D71921", ACCENT_DARK="#F3D2D4", DANGER="#C21620",
                      OK="#3B8C4D", WARN="#B58A2E"),
    }
    # class-level fallback (used before _apply_theme runs); overridden per-instance
    BG = THEMES["dark"]["BG"]
    PANEL = THEMES["dark"]["PANEL"]
    CARD = THEMES["dark"]["CARD"]
    CARD_2 = THEMES["dark"]["CARD_2"]
    BORDER = THEMES["dark"]["BORDER"]
    BORDER2 = THEMES["dark"]["BORDER2"]
    FG = THEMES["dark"]["FG"]
    DISPLAY = THEMES["dark"]["DISPLAY"]
    MUT = THEMES["dark"]["MUT"]
    SUBTLE = THEMES["dark"]["SUBTLE"]
    ACCENT = THEMES["dark"]["ACCENT"]
    ACCENT_DARK = THEMES["dark"]["ACCENT_DARK"]
    DANGER = THEMES["dark"]["DANGER"]
    OK = THEMES["dark"]["OK"]
    WARN = THEMES["dark"]["WARN"]
    # font families, resolved against real availability in run() (fallbacks
    # kept if the private-font load failed)
    SANS = SANS if _FONTS_LOADED else SANS_FALLBACK
    MONO = MONO if _FONTS_LOADED else MONO_FALLBACK
    DOTO = DOTO if _FONTS_LOADED else DOTO_FALLBACK

    def _apply_theme(self):
        for key, val in self.THEMES.get(self.cfg.get("theme", "dark"),
                                        self.THEMES["dark"]).items():
            setattr(self, key, val)
        if self.cfg.get("accent_color"):
            self.ACCENT = self.cfg["accent_color"]

    def _resolve_fonts(self):
        """Swap any bundled family Tk can't actually see for its fallback.
        Runs once root exists — private GDI fonts are visible to this process,
        but if the load failed we degrade gracefully instead of silently
        rendering in Tk's default font."""
        fams = set(tkfont.families(self.root))
        if SANS not in fams:
            self.SANS = SANS_FALLBACK
        if MONO not in fams:
            self.MONO = MONO_FALLBACK
        if DOTO not in fams:
            self.DOTO = DOTO_FALLBACK if DOTO_FALLBACK in fams else self.MONO

    def _sans(self, size, bold=False):
        return (self.SANS, size, "bold") if bold else (self.SANS, size)

    def _mono(self, size, bold=False):
        return (self.MONO, size, "bold") if bold else (self.MONO, size)

    def _doto(self, size):
        return (self.DOTO, size)

    def _write_runtime(self):
        """Mirror in-process live state to runtime.json for the separate-process
        webview dashboard (health panel, copy-last, undo-last). Best-effort."""
        try:
            data = {"whisper_device": self.transcriber.device,
                    "whisper_loaded": self.transcriber.model is not None,
                    "enabled": self.cfg.get("enabled", True),
                    "model_size": self.cfg.get("model_size"),
                    "last_text": self.last_injected_text}
            os.makedirs(APP_DIR, exist_ok=True)
            with open(RUNTIME_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass

    def launch_dashboard(self):
        """Open the pywebview dashboard (dashboard/dashboard.py) as its own
        process. Reuses an already-open one; falls back to the in-process Tk
        dashboard if the launcher is missing or won't start."""
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "dashboard", "dashboard.py")
        if os.path.exists(script):
            proc = getattr(self, "_dash_proc", None)
            if proc and proc.poll() is None:
                return  # already open
            if IS_WIN:
                scripts_pyw = os.path.join(sys.prefix, "Scripts", "pythonw.exe")
                pythonw = scripts_pyw if os.path.exists(scripts_pyw) \
                    else os.path.join(sys.prefix, "pythonw.exe")
            else:
                pythonw = sys.executable  # no windowless/console binary split on macOS
            try:
                self._write_runtime()  # make sure health/last-text are current
                self._dash_proc = subprocess.Popen(
                    [pythonw, script], cwd=os.path.dirname(script))
                return
            except OSError as e:
                print(f"webview dashboard failed to launch ({e}); using Tk fallback")
        self.ui_q.put(self.open_dashboard)

    def _dash_pick_mic(self, event=None):
        idx = dict(self._mic_options).get(self.mic_var.get())
        self.cfg["input_device"] = idx
        save_config(self.cfg)

    def _dash_pick_hotkey(self, event=None):
        mods = dict(HOTKEY_PRESETS).get(self.hotkey_var.get())
        if mods:
            self.cfg["hotkey_mods"] = mods
            save_config(self.cfg)

    def _dash_pick_hotkey_mode(self, mode):
        self.cfg["hotkey_mode"] = mode
        save_config(self.cfg)
        self._dash_reopen()

    def _dash_toggle_theme(self):
        self.cfg["theme"] = "light" if self.cfg.get("theme", "dark") == "dark" else "dark"
        save_config(self.cfg)
        self._dash_reopen()

    def _dash_pick_accent(self):
        _, hexval = colorchooser.askcolor(
            color=self.ACCENT, title="Accent color", parent=self.dash)
        if hexval:
            self.cfg["accent_color"] = hexval
            save_config(self.cfg)
            self._dash_reopen()

    def _dash_reset_accent(self):
        self.cfg["accent_color"] = None
        save_config(self.cfg)
        self._dash_reopen()

    def _dash_toggle_auto_theme(self):
        self.cfg["auto_theme"] = not self.cfg.get("auto_theme")
        save_config(self.cfg)
        self._dash_reopen()

    def _dash_capture_hotkey(self):
        self.hotkey_combo.set("press keys...")
        self._capturing_hotkey = True
        self._pulse_capture_hotkey()

        def work():
            try:
                combo = keyboard.read_hotkey(suppress=False)
            except Exception:
                combo = None
            self.ui_q.put(lambda: self._apply_captured_hotkey(combo))

        threading.Thread(target=work, daemon=True).start()

    def _pulse_capture_hotkey(self, on=True):
        if not self._capturing_hotkey:
            return
        try:
            if not self.hotkey_combo.winfo_exists():
                return
            self.hotkey_combo.configure(
                style="Dictator.Capturing.TCombobox" if on else "Dictator.TCombobox")
            self.hotkey_combo.after(450, self._pulse_capture_hotkey, not on)
        except tk.TclError:
            pass

    def _apply_captured_hotkey(self, combo):
        self._capturing_hotkey = False
        if combo:
            mods = ["win" if "windows" in p.strip().lower() else p.strip().lower()
                    for p in combo.split("+")]
            self.cfg["hotkey_mods"] = mods
            save_config(self.cfg)
        self._dash_reopen()

    def _dash_close(self, d):
        try:
            self.cfg["dash_geometry"] = d.winfo_geometry()
            save_config(self.cfg)
        except tk.TclError:
            pass
        d.destroy()

    def _dash_close_animated(self, d, steps=5):
        """Quick fade-out for a user-initiated close — _dash_reopen keeps using
        the instant _dash_close so it isn't racing a fading-out old window."""
        try:
            if not d.winfo_exists():
                return
            cur = float(d.attributes("-alpha"))
        except tk.TclError:
            return
        if steps <= 0 or cur <= 0.05:
            self._dash_close(d)
            return
        d.attributes("-alpha", cur - 0.2)
        d.after(12, self._dash_close_animated, d, steps - 1)

    def _dash_reopen(self):
        """Destroy + rebuild the dashboard — simplest way to repaint theme/
        accent/highlight changes without hand-updating every widget."""
        if getattr(self, "dash", None) and self.dash.winfo_exists():
            self._dash_close(self.dash)
        self.open_dashboard()

    def _dash_toggle_log(self):
        self.cfg["log_history"] = not self.cfg["log_history"]
        save_config(self.cfg)
        self._recent_render_key = None
        self._dash_refresh()

    def _dash_pick_folder(self):
        folder = filedialog.askdirectory(initialdir=self.cfg["history_dir"],
                                         title="Where should the log file be saved?")
        if folder:
            self.cfg["history_dir"] = os.path.normpath(folder)
            save_config(self.cfg)
            self._storage_cache = (0.0, {})
            self._recent_render_key = None
            self._dash_refresh()

    def _dash_purge(self):
        if not messagebox.askyesno(
                "Purge history",
                "This permanently deletes ALL dictation history and stats.\n"
                "There is no undo.\n\nContinue?",
                icon="warning", parent=self.dash):
            return
        if not messagebox.askyesno(
                "Last chance",
                "Are you absolutely sure you want to erase everything?",
                icon="warning", default="no", parent=self.dash):
            return
        try:
            os.remove(history_path(self.cfg))
        except OSError:
            pass
        self.session.clear()
        self.totals = {"n": 0, "raw_w": 0, "cln_w": 0, "secs": 0.0}
        self.days.clear()
        self._search_cache = ("", [])
        self._recent_render_key = None
        self._last_stats_key = None
        self._storage_cache = (0.0, {})
        self._dash_refresh()

    def _dash_export_history(self):
        path = filedialog.asksaveasfilename(
            title="Export dictation history", defaultextension=".md",
            initialfile="dictator-history.md",
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt")], parent=self.dash)
        if not path:
            return
        entries = self._scan_history("")  # empty query matches everything
        lines = ["# Dictator history\n"]
        for e in entries:
            lines.append(f"**{e['t']:%Y-%m-%d %H:%M}**\n\n{e['cleaned']}\n")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            messagebox.showinfo("Export history", f"Wrote {len(entries)} entries to:\n{path}",
                                parent=self.dash)
        except OSError as e:
            messagebox.showerror("Export history", str(e), parent=self.dash)

    BACKUP_KEYS = ("vocabulary", "snippets", "tone_overrides", "hotkey_mods",
                   "hotkey_mode", "theme", "accent_color", "auto_punctuate",
                   "review_before_typing", "auto_theme")

    def _dash_backup_settings(self):
        path = filedialog.asksaveasfilename(
            title="Backup settings", defaultextension=".json",
            initialfile="dictator-backup.json",
            filetypes=[("JSON", "*.json")], parent=self.dash)
        if not path:
            return
        data = {k: self.cfg[k] for k in self.BACKUP_KEYS if k in self.cfg}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            messagebox.showinfo("Backup settings", f"Saved to:\n{path}", parent=self.dash)
        except OSError as e:
            messagebox.showerror("Backup settings", str(e), parent=self.dash)

    def _dash_restore_settings(self):
        path = filedialog.askopenfilename(
            title="Restore settings", filetypes=[("JSON", "*.json")], parent=self.dash)
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            messagebox.showerror("Restore settings", str(e), parent=self.dash)
            return
        for k in self.BACKUP_KEYS:
            if k in data:
                self.cfg[k] = data[k]
        save_config(self.cfg)
        self._dash_reopen()

    def _save_vocab(self, event=None):
        self.cfg["vocabulary"] = [w.strip() for w in self.vocab_var.get().split(",")
                                  if w.strip()]
        save_config(self.cfg)

    def _save_tone_overrides(self, event=None):
        overrides = {}
        for key, var in self._tone_vars.items():
            overrides[key] = [w.strip().lower() for w in var.get().split(",") if w.strip()]
        self.cfg["tone_overrides"] = overrides
        save_config(self.cfg)

    def _save_snippets(self, event=None):
        snippets = {}
        for line in self.snippets_text.get("1.0", "end").splitlines():
            if "=>" not in line:
                continue
            trigger, _, expansion = line.partition("=>")
            trigger = trigger.strip().lower()
            if trigger:
                snippets[trigger] = expansion.strip()
        self.cfg["snippets"] = snippets
        save_config(self.cfg)

    def _scan_history(self, q):
        """Search full history file + in-memory session, deduped, oldest first."""
        hits = {}
        try:
            with open(history_path(self.cfg), encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                        if (q in e["cleaned_text"].lower()
                                or q in e["raw_transcript"].lower()):
                            t = datetime.fromisoformat(e["timestamp"])
                            hits[(t, e["cleaned_text"])] = {
                                "t": t, "cleaned": e["cleaned_text"]}
                    except (ValueError, KeyError):
                        continue
        except OSError:
            pass
        for e in self.session:  # entries never logged to disk live only here
            if q in e["cleaned"].lower() or q in e["raw"].lower():
                hits[(e["t"], e["cleaned"])] = e
        return sorted(hits.values(), key=lambda e: e["t"])

    def _btn(self, parent, text, cmd, danger=False, accent=False, feedback=None):
        """Uideas OS pill button: mono uppercase, 1px border, flat fill.
        Hover/press are instant colour swaps (no per-frame .after loop) —
        that stepped animation was the dashboard's main source of lag.
          default  — transparent, muted text, brighten on hover (hud-btn)
          accent   — filled display/black (hud-btn-primary)
          danger   — red outline + text (hud-btn-danger)"""
        label = text.upper()
        f = tkfont.Font(family=self.MONO, size=9)
        w = f.measure(label) + 32
        h = 30
        bg = parent.cget("bg") if hasattr(parent, "cget") else self.BG
        if accent:
            fill_i, fill_h = self.DISPLAY, self.FG
            border_i = border_h = self.DISPLAY
            txt_i = txt_h = self.BG
        elif danger:
            fill_i = fill_h = bg
            border_i, border_h = self.ACCENT, self.ACCENT
            txt_i, txt_h = self.ACCENT, self.ACCENT
        else:
            fill_i = fill_h = bg
            border_i, border_h = self.BORDER2, self.FG
            txt_i, txt_h = self.MUT, self.FG
        cv = tk.Canvas(parent, width=w, height=h, bg=bg,
                       highlightthickness=0, cursor="hand2")
        r = h // 2  # pill = full-height radius (border-radius: 999px)
        rect = round_rect(cv, 1, 1, w - 1, h - 1, r, fill=fill_i,
                          outline=border_i, width=1)
        lbl = cv.create_text(w // 2, h // 2, text=label, fill=txt_i, font=f)

        def paint(fill, border, txt):
            cv.itemconfigure(rect, fill=fill, outline=border)
            cv.itemconfigure(lbl, fill=txt)

        def on_click(_e):
            paint(self.ACCENT_DARK if not accent else self.FG,
                  border_h, txt_h)
            cv.after(110, lambda: cv.winfo_exists() and paint(fill_h, border_h, txt_h))
            cmd()
            if feedback:
                cv.itemconfigure(lbl, text=feedback.upper())
                cv.after(1100, lambda: cv.winfo_exists()
                         and cv.itemconfigure(lbl, text=label))

        cv.bind("<Button-1>", on_click)
        cv.bind("<Enter>", lambda _e: paint(fill_h, border_h, txt_h))
        cv.bind("<Leave>", lambda _e: paint(fill_i, border_i, txt_i))
        return cv

    def _section_label(self, parent, text):
        # hud-label: uppercase mono, muted. Drop any leading emoji/glyph so it
        # reads as a clean Nothing-style caption (no icons in this dialect).
        clean = text.strip()
        while clean and not (clean[0].isascii() and (clean[0].isalnum())):
            clean = clean[1:].strip()
        return tk.Label(parent, text=clean.upper(), bg=parent.cget("bg"),
                        fg=self.MUT, font=self._mono(9))

    def _styled_entry(self, parent, var, width=None):
        # hud-input: flat raised field, 1px border that lights to FG on focus
        e = tk.Entry(parent, textvariable=var, bg=self.CARD, fg=self.FG,
                     insertbackground=self.ACCENT, relief="flat", bd=0,
                     font=self._sans(10), width=width, highlightthickness=1,
                     highlightbackground=self.BORDER2, highlightcolor=self.BORDER2)
        e.bind("<FocusIn>", lambda ev: e.configure(highlightbackground=self.FG, highlightcolor=self.FG))
        e.bind("<FocusOut>", lambda ev: e.configure(highlightbackground=self.BORDER2, highlightcolor=self.BORDER2))
        return e

    def _copy_text(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def _dash_pick_model_size(self, size):
        if size == self.cfg["model_size"] or self._model_loading:
            return
        self.cfg["model_size"] = size
        save_config(self.cfg)
        self._health_cache = (0.0, {})
        self._model_loading = size

        def work():
            self.transcriber.reload(size)
            self.ui_q.put(self._model_load_done)

        threading.Thread(target=work, daemon=True).start()
        for card in getattr(self, "_model_cards", []):
            card.event_generate("<Configure>")

    def _model_load_done(self):
        self._model_loading = None
        self._health_cache = (0.0, {})
        self._write_runtime()  # device/model may have changed
        for card in getattr(self, "_model_cards", []):
            card.event_generate("<Configure>")

    def _model_card(self, parent, size, title, note):
        # flat selectable tile: 1px border, sans title + uppercase mono note.
        # active = accent-tinted fill + accent border/title (like a selected
        # hud-panel). No animation — instant repaint keeps it snappy.
        cv = tk.Canvas(parent, height=64, bg=self.PANEL, highlightthickness=0,
                       cursor="hand2")
        rect = cv.create_rectangle(0, 0, 10, 10, fill=self.CARD, outline=self.BORDER2)
        title_item = cv.create_text(14, 22, anchor="w", text=title, fill=self.FG,
                                    font=self._sans(11, bold=True))
        note_item = cv.create_text(14, 44, anchor="w", text=note.upper(),
                                   fill=self.MUT, font=self._mono(8))

        def repaint(e=None):
            active = self.cfg["model_size"] == size
            loading = self._model_loading == size
            fill = self.ACCENT_DARK if active else self.CARD
            outline = self.ACCENT if active else self.BORDER2
            width = cv.winfo_width() or 150
            cv.coords(rect, 0, 0, width - 1, 63)
            cv.itemconfigure(rect, fill=fill, outline=outline, width=1)
            cv.itemconfigure(title_item, fill=self.ACCENT if active else self.FG)
            cv.itemconfigure(note_item, text="LOADING…" if loading else note.upper(),
                             fill="#E8888C" if active else self.MUT)

        def pulse_loading(on=True):
            try:
                if not cv.winfo_exists() or self._model_loading != size:
                    return
                cv.itemconfigure(note_item, fill="#E8888C" if on else self.MUT)
                cv.after(500, pulse_loading, not on)
            except tk.TclError:
                pass

        def on_click(e):
            self._dash_pick_model_size(size)
            repaint()
            if self._model_loading == size:
                pulse_loading()

        cv.bind("<Configure>", repaint)
        cv.bind("<Button-1>", on_click)
        self._model_cards.append(cv)
        return cv

    def _review_text(self, raw, cleaned):
        done = threading.Event()
        result = {"text": None}

        def show():
            self._open_review_window(raw, cleaned, done, result)

        self.ui_q.put(show)
        done.wait()
        return result["text"]

    def _open_review_window(self, raw, cleaned, done, result):
        win = tk.Toplevel(self.root)
        win.title("Review dictation")
        win.configure(bg=self.BG)
        win.geometry("640x460")
        win.minsize(520, 360)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)

        def _fade_in(i=0, steps=8):
            try:
                if not win.winfo_exists():
                    return
                win.attributes("-alpha", i / steps)
                if i < steps:
                    win.after(15, _fade_in, i + 1)
            except tk.TclError:
                pass

        win.after(10, _fade_in)

        tk.Label(win, text="Review before typing", bg=self.BG, fg=self.DISPLAY,
                 font=self._sans(18, bold=True)).pack(anchor="w", padx=22, pady=(20, 4))
        tk.Label(win, text="Edit the text, then type it into the active app.",
                 bg=self.BG, fg=self.MUT, font=self._sans(10)).pack(
            anchor="w", padx=22)

        text = tk.Text(win, bg=self.PANEL, fg=self.FG, insertbackground=self.ACCENT,
                       relief="flat", bd=0, wrap="word", font=self._sans(11),
                       padx=14, pady=12, height=10)
        text.pack(fill="both", expand=True, padx=22, pady=18)
        text.insert("1.0", cleaned)
        text.focus_set()

        raw_preview = tk.Label(win, text=f"Raw: {raw[:160]}",
                               bg=self.BG, fg=self.SUBTLE, justify="left",
                               anchor="w", font=self._mono(8))
        raw_preview.pack(fill="x", padx=22, pady=(0, 10))

        row = tk.Frame(win, bg=self.BG)
        row.pack(fill="x", padx=22, pady=(0, 20))

        def finish(value):
            result["text"] = value
            done.set()
            win.destroy()

        self._btn(row, "Cancel", lambda: finish(None)).pack(side="right")
        self._btn(row, "Type text", lambda: finish(text.get("1.0", "end-1c")),
                  accent=True).pack(side="right", padx=(0, 8))
        win.protocol("WM_DELETE_WINDOW", lambda: finish(None))

    def _clean_clipboard(self):
        """Run the same Ollama cleanup used for dictation on whatever text is
        currently on the clipboard — a manual utility, no hotkey/pipeline involved."""
        try:
            raw = self.root.clipboard_get()
        except tk.TclError:
            return
        if not raw or not raw.strip():
            return

        def work():
            if self.ollama_model is None:
                self.ollama_model = resolve_ollama_model(self.cfg)
            cleaned = ollama_cleanup(raw, self.cfg, self.ollama_model) or raw
            self.ui_q.put(lambda: self._copy_text(cleaned))

        threading.Thread(target=work, daemon=True).start()

    def _undo_last(self):
        if not self.last_injected_text:
            return
        wait_keys_released(self.cfg)
        send_backspaces(len(self.last_injected_text.replace("\r\n", "\n")))
        self.last_injected_text = ""

    def _dash_toggle_review(self):
        self.cfg["review_before_typing"] = not self.cfg.get("review_before_typing")
        save_config(self.cfg)
        self._health_cache = (0.0, {})
        self._dash_reopen()

    def _dash_toggle_punctuate(self):
        self.cfg["auto_punctuate"] = not self.cfg.get("auto_punctuate", True)
        save_config(self.cfg)
        self._dash_reopen()

    def _dash_retry_health(self):
        self._health_cache = (0.0, {})
        self._refresh_health_async()
        self._dash_refresh()

    def _safe_clear_whisper_cache(self):
        cache = os.path.normpath(WHISPER_CACHE)
        allowed = os.path.normpath(os.path.join(WORKSPACE_DIR, "Cache"))
        if os.path.commonpath([cache, allowed]) != allowed:
            messagebox.showerror("Cache cleanup", "Cache path is outside the workspace.",
                                 parent=self.dash)
            return
        if not messagebox.askyesno(
                "Clear Whisper cache",
                "This deletes the local Whisper download cache. Dictator will re-download the model next time it needs it.",
                icon="warning", parent=self.dash):
            return
        try:
            shutil.rmtree(cache, ignore_errors=True)
        except OSError as e:
            messagebox.showerror("Cache cleanup", str(e), parent=self.dash)
        self._storage_cache = (0.0, {})
        self._dash_refresh()

    def _safe_clear_ollama_models(self):
        cache = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "ollama-models"))
        allowed = os.path.dirname(os.path.abspath(__file__))
        if os.path.commonpath([cache, allowed]) != allowed:
            messagebox.showerror("Cache cleanup", "Model path is outside the workspace.",
                                 parent=self.dash)
            return
        if not messagebox.askyesno(
                "Clear Ollama models",
                "This deletes the local cleanup model (~4.7 GB). Dictator falls back to "
                "raw transcripts until you run 'ollama pull qwen2.5:7b-instruct' again.",
                icon="warning", parent=self.dash):
            return
        try:
            shutil.rmtree(cache, ignore_errors=True)
        except OSError as e:
            messagebox.showerror("Cache cleanup", str(e), parent=self.dash)
        self.ollama_model = None
        self._health_cache = (0.0, {})
        self._storage_cache = (0.0, {})
        self._dash_refresh()

    def _open_history_folder(self):
        if IS_MAC:
            subprocess.run(["open", self.cfg["history_dir"]], check=False)
        else:
            os.startfile(self.cfg["history_dir"])

    def _health_stale_after(self):
        # backoff: recheck sooner if Ollama was unreachable last time
        return 5 if self._health_cache[1].get("ollama") == "not reachable" else 30

    def _health_snapshot(self):
        now = time.time()
        if now - self._health_cache[0] < self._health_stale_after():
            return self._health_cache[1]
        mic = "System default"
        try:
            if self.cfg["input_device"] is not None:
                mic = sd.query_devices()[self.cfg["input_device"]]["name"]
        except Exception:
            mic = "Unavailable"
        try:
            model = resolve_ollama_model(self.cfg)
            ollama = model or "not reachable"
        except Exception:
            ollama = "not reachable"
        data = {
            "enabled": "on" if self.cfg["enabled"] else "off",
            "whisper": f'{self.cfg["model_size"]} / {self.transcriber.device}',
            "loaded": "ready" if self.transcriber.model is not None else "loading",
            "ollama": ollama,
            "mic": mic,
            "review": "on" if self.cfg.get("review_before_typing") else "off",
        }
        self._health_cache = (now, data)
        return data

    def _refresh_health_async(self):
        now = time.time()
        if self._health_refreshing or now - self._health_cache[0] < self._health_stale_after():
            return
        self._health_refreshing = True

        def work():
            try:
                self._health_snapshot()
            finally:
                self._health_refreshing = False

        threading.Thread(target=work, daemon=True).start()

    def _storage_snapshot(self):
        now = time.time()
        if now - self._storage_cache[0] < 60:
            return self._storage_cache[1]
        base = os.path.dirname(os.path.abspath(__file__))
        items = {
            "Whisper cache": WHISPER_CACHE,
            "Ollama models": os.path.join(base, "ollama-models"),
            "Python env": os.path.join(base, ".venv"),
            "History": self.cfg["history_dir"],
        }
        data = {}
        for name, path in items.items():
            size, files = folder_size(path)
            data[name] = (fmt_bytes(size), files)
        self._storage_cache = (now, data)
        return data

    def _refresh_storage_async(self):
        now = time.time()
        if self._storage_refreshing or now - self._storage_cache[0] < 60:
            return
        self._storage_refreshing = True

        def work():
            try:
                self._storage_snapshot()
            finally:
                self._storage_refreshing = False

        threading.Thread(target=work, daemon=True).start()

    def open_dashboard(self):
        if getattr(self, "dash", None) and self.dash.winfo_exists():
            self.dash.deiconify()
            self.dash.lift()
            return
        if self.cfg.get("auto_theme"):
            hour = datetime.now().hour
            desired = "dark" if (hour >= 19 or hour < 7) else "light"
            if self.cfg.get("theme") != desired:
                self.cfg["theme"] = desired
                save_config(self.cfg)
        self._apply_theme()
        d = self.dash = tk.Toplevel(self.root)
        d.title("Dictator")
        d.configure(bg=self.BG)
        d.geometry(self.cfg.get("dash_geometry") or "760x840")
        d.minsize(680, 720)
        d.attributes("-alpha", 0.0)

        def _fade_in(i=0, steps=8):
            try:
                if not d.winfo_exists():
                    return
                d.attributes("-alpha", i / steps)
                if i < steps:
                    d.after(15, _fade_in, i + 1)
            except tk.TclError:
                pass

        d.after(10, _fade_in)

        style = ttk.Style(d)
        style.theme_use("clam")
        style.configure("Dictator.TCombobox", fieldbackground=self.CARD,
                        background=self.CARD, foreground=self.FG,
                        selectbackground=self.CARD, selectforeground=self.FG,
                        arrowcolor=self.MUT, bordercolor=self.BORDER2,
                        lightcolor=self.BORDER2, darkcolor=self.BORDER2)
        # clam's readonly/disabled states override the flat field colour above —
        # pin them back to the dark card so the value stays readable (light FG)
        style.map("Dictator.TCombobox",
                  fieldbackground=[("readonly", self.CARD), ("disabled", self.CARD)],
                  foreground=[("readonly", self.FG), ("disabled", self.MUT)],
                  selectbackground=[("readonly", self.CARD)],
                  selectforeground=[("readonly", self.FG)],
                  arrowcolor=[("active", self.FG)],
                  bordercolor=[("focus", self.FG), ("active", self.BORDER2)],
                  lightcolor=[("focus", self.FG)], darkcolor=[("focus", self.FG)])
        # dropdown list (a Tk Listbox, not themed by ttk) — match the dark card
        d.option_add("*TCombobox*Listbox.background", self.CARD)
        d.option_add("*TCombobox*Listbox.foreground", self.FG)
        d.option_add("*TCombobox*Listbox.selectBackground", self.ACCENT_DARK)
        d.option_add("*TCombobox*Listbox.selectForeground", self.FG)
        d.option_add("*TCombobox*Listbox.borderWidth", 0)
        style.configure("Dictator.Capturing.TCombobox", fieldbackground=self.ACCENT_DARK,
                        background=self.ACCENT_DARK, foreground=self.FG,
                        arrowcolor=self.ACCENT, bordercolor=self.ACCENT,
                        lightcolor=self.ACCENT_DARK, darkcolor=self.ACCENT_DARK)

        shell = tk.Frame(d, bg=self.BG)
        shell.pack(fill="both", expand=True, padx=28, pady=24)

        hero = tk.Canvas(shell, height=100, bg=self.BG, highlightthickness=0)
        hero.pack(fill="x")

        def hero_resize(e):
            # flat panel + single 1px border (no rounded corners in the dialect)
            hero.delete("bg")
            hero.create_rectangle(0, 0, e.width - 1, 99, fill=self.PANEL,
                                  outline=self.BORDER, width=1, tags="bg")
            hero.tag_lower("bg")
            # LOCAL badge — hud-badge pill, redrawn each resize so its geometry
            # is always right (round_rect is a smoothed polygon, not resizable
            # by coords()). accent dot + uppercase mono.
            hero.delete("badge")
            px = e.width - 26
            round_rect(hero, px - 76, 22, px, 44, 11, fill="",
                       outline=self.BORDER2, width=1, tags="badge")
            hero.create_oval(px - 64, 30, px - 58, 36, fill=self.ACCENT,
                             outline="", tags="badge")
            hero.create_text(px - 14, 33, anchor="e", text="LOCAL",
                             fill=self.MUT, font=self._mono(9), tags="badge")

        hero.bind("<Configure>", hero_resize)
        hero.create_text(28, 34, anchor="w", text="Dictator", fill=self.DISPLAY,
                         font=self._sans(24, bold=True))
        hero.create_line(28, 54, 62, 54, fill=self.ACCENT, width=2)
        hero.create_text(28, 74, anchor="w",
                         text="Hold Ctrl + Win to dictate. Double-tap for hands-free. Everything stays local.",
                         fill=self.MUT, font=self._sans(10))

        # stat-grid: 1px-gap grid on a border-coloured frame, flat PANEL tiles,
        # Doto dot-matrix hero numbers, uppercase mono captions.
        cards = tk.Frame(shell, bg=self.BORDER)
        cards.pack(fill="x", pady=(18, 8))
        self._stat_vars = {}
        tiles = [("n", "dictations"), ("raw_w", "words spoken"),
                 ("cln_w", "words typed"), ("wpm", "avg wpm"),
                 ("streak", "day streak")]
        for col, (key, caption) in enumerate(tiles):
            cv = tk.Canvas(cards, height=94, bg=self.PANEL, highlightthickness=0)
            cv.grid(row=0, column=col, sticky="nsew",
                    padx=(1, 1) if col == len(tiles) - 1 else (1, 0), pady=1)
            cards.columnconfigure(col, weight=1)
            num = cv.create_text(0, 40, text="0", fill=self.DISPLAY,
                                 font=self._doto(26))
            cap = cv.create_text(0, 72, text=caption.upper(), fill=self.MUT,
                                 font=self._mono(9))
            spark = cv.create_line(0, 0, 0, 0, fill=self.ACCENT, width=2,
                                   smooth=True, tags="spark") if key == "n" else None

            def resize(e, cv=cv, num=num, cap=cap):
                cv.coords(num, e.width / 2, 40)
                cv.coords(cap, e.width / 2, 72)

            cv.bind("<Configure>", resize)
            self._stat_vars[key] = (cv, num)
            if spark is not None:
                self._spark_item = (cv, spark)
                cv.bind("<Configure>", lambda e: self._redraw_sparkline(), add="+")

        main = tk.Frame(shell, bg=self.BG)
        main.pack(fill="both", expand=True, pady=(10, 0))
        main.columnconfigure(0, weight=3)
        main.columnconfigure(1, weight=2)
        main.rowconfigure(0, weight=1)

        recent = tk.Frame(main, bg=self.PANEL)
        recent.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        recent_hdr = tk.Frame(recent, bg=self.PANEL)
        recent_hdr.pack(fill="x", padx=16, pady=(16, 10))
        tk.Label(recent_hdr, text="Recent dictations", bg=self.PANEL, fg=self.FG,
                 font=self._sans(13, bold=True)).pack(side="left")
        self.search_var = tk.StringVar()
        se = self._styled_entry(recent_hdr, self.search_var, width=24)
        se.pack(side="right", ipady=5)
        se.bind("<KeyRelease>", lambda e: self._dash_refresh())
        se.bind("<FocusIn>", lambda e: se.configure(highlightthickness=1,
                highlightbackground=self.ACCENT, highlightcolor=self.ACCENT))
        se.bind("<FocusOut>", lambda e: se.configure(highlightthickness=0))
        tk.Label(recent_hdr, text="SEARCH", bg=self.PANEL, fg=self.MUT,
                 font=self._mono(9)).pack(side="right", padx=(0, 8))
        tk.Frame(recent, bg=self.BORDER, height=1).pack(fill="x", padx=16)

        self.recent_canvas = tk.Canvas(recent, bg=self.PANEL, highlightthickness=0)
        self.recent_canvas.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.recent_frame = tk.Frame(self.recent_canvas, bg=self.PANEL)
        self.recent_window = self.recent_canvas.create_window(
            0, 0, anchor="nw", window=self.recent_frame)

        def recent_frame_resize(_e=None):
            self.recent_canvas.configure(scrollregion=self.recent_canvas.bbox("all"))

        def recent_canvas_resize(e):
            self.recent_canvas.itemconfigure(self.recent_window, width=e.width)

        self.recent_frame.bind("<Configure>", recent_frame_resize)
        self.recent_canvas.bind("<Configure>", recent_canvas_resize)

        controls_outer = tk.Frame(main, bg=self.PANEL)
        controls_outer.grid(row=0, column=1, sticky="nsew")
        controls_canvas = tk.Canvas(controls_outer, bg=self.PANEL, highlightthickness=0)
        controls_canvas.pack(fill="both", expand=True)
        controls = tk.Frame(controls_canvas, bg=self.PANEL)
        controls_window = controls_canvas.create_window(0, 0, anchor="nw", window=controls)
        controls.columnconfigure(0, weight=1)

        def controls_frame_resize(_e=None):
            controls_canvas.configure(scrollregion=controls_canvas.bbox("all"))

        def controls_canvas_resize(e):
            controls_canvas.itemconfigure(controls_window, width=e.width)

        controls.bind("<Configure>", controls_frame_resize)
        controls_canvas.bind("<Configure>", controls_canvas_resize)

        def route_wheel(e):
            steps = int(-1 * (e.delta / 120))
            w = e.widget
            while w is not None:
                if w is self.recent_canvas:
                    self.recent_canvas.yview_scroll(steps, "units")
                    return
                if w is controls_canvas:
                    controls_canvas.yview_scroll(steps, "units")
                    return
                w = w.master

        def grab_wheel(_e=None):
            d.bind_all("<MouseWheel>", route_wheel)

        def release_wheel(_e=None):
            d.unbind_all("<MouseWheel>")

        d.bind("<Enter>", grab_wheel)
        d.bind("<Leave>", release_wheel)
        d.bind("<Destroy>", release_wheel)
        d.protocol("WM_DELETE_WINDOW", lambda: self._dash_close_animated(d))

        tk.Label(controls, text="Controls", bg=self.PANEL, fg=self.FG,
                 font=self._sans(13, bold=True)).pack(anchor="w", padx=16, pady=(16, 4))
        tk.Label(controls, text="Everyday settings, kept within reach.",
                 bg=self.PANEL, fg=self.MUT, font=self._sans(9)).pack(
            anchor="w", padx=16, pady=(0, 10))
        tk.Frame(controls, bg=self.BORDER, height=1).pack(fill="x", padx=16, pady=(0, 6))

        self._section_label(controls, "🎨 Appearance").pack(anchor="w", padx=16, pady=(0, 6))
        appearance = tk.Frame(controls, bg=self.PANEL)
        appearance.pack(fill="x", padx=16, pady=(0, 6))
        theme_label = "☀ Light theme" if self.cfg.get("theme", "dark") == "dark" else "🌙 Dark theme"
        self._btn(appearance, theme_label, self._dash_toggle_theme).pack(side="left")
        self._btn(appearance, "Accent color", self._dash_pick_accent,
                  accent=True).pack(side="left", padx=(8, 0))
        swatch = tk.Canvas(appearance, width=18, height=32, bg=self.PANEL, highlightthickness=0)
        round_rect(swatch, 3, 8, 15, 24, 6, fill=self.ACCENT, outline=self.CARD_2)
        swatch.pack(side="left", padx=(6, 0))
        if self.cfg.get("accent_color"):
            self._btn(appearance, "Reset accent", self._dash_reset_accent).pack(
                side="left", padx=(8, 0))
        self._btn(appearance, "Auto theme (7pm-7am)", self._dash_toggle_auto_theme,
                  accent=self.cfg.get("auto_theme")).pack(side="left", padx=(8, 0))

        self._section_label(controls, "🎙 Microphone").pack(anchor="w", padx=16, pady=(0, 6))
        self._mic_options = [("System default", None)]
        for idx, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                api = sd.query_hostapis(dev["hostapi"])["name"]
                self._mic_options.append((f'{dev["name"]} - {api}', idx))
        names = [n for n, _ in self._mic_options]
        current = next((n for n, i in self._mic_options
                        if i == self.cfg["input_device"]), names[0])
        self.mic_var = tk.StringVar(value=current)
        mic = ttk.Combobox(controls, textvariable=self.mic_var, values=names,
                           state="readonly", font=self._sans(10),
                           style="Dictator.TCombobox")
        mic.pack(fill="x", padx=16, ipady=4)
        mic.bind("<<ComboboxSelected>>", self._dash_pick_mic)

        self._section_label(controls, "⌨ Hotkey").pack(
            anchor="w", padx=16, pady=(16, 6))
        hotkey_names = [n for n, _ in HOTKEY_PRESETS]
        current_mods = self.cfg.get("hotkey_mods") or ["ctrl", "win"]
        preset_match = next((n for n, m in HOTKEY_PRESETS if m == current_mods), None)
        current_hotkey = preset_match or f"Custom ({'+'.join(current_mods)})"
        display_names = hotkey_names + ([] if preset_match else [current_hotkey])
        self.hotkey_var = tk.StringVar(value=current_hotkey)
        hk_row = tk.Frame(controls, bg=self.PANEL)
        hk_row.pack(fill="x", padx=16)
        self.hotkey_combo = ttk.Combobox(hk_row, textvariable=self.hotkey_var,
                          values=display_names, state="readonly", font=self._sans(10),
                          style="Dictator.TCombobox")
        self.hotkey_combo.pack(side="left", fill="x", expand=True, ipady=4)
        self.hotkey_combo.bind("<<ComboboxSelected>>", self._dash_pick_hotkey)
        self._btn(hk_row, "Capture...", self._dash_capture_hotkey).pack(
            side="left", padx=(8, 0))

        mode_row = tk.Frame(controls, bg=self.PANEL)
        mode_row.pack(fill="x", padx=16, pady=(8, 0))
        self._btn(mode_row, "Hold to talk", lambda: self._dash_pick_hotkey_mode("hold"),
                  accent=self.cfg.get("hotkey_mode", "hold") == "hold").pack(side="left")
        self._btn(mode_row, "Tap to toggle", lambda: self._dash_pick_hotkey_mode("toggle"),
                  accent=self.cfg.get("hotkey_mode") == "toggle").pack(
            side="left", padx=(8, 0))

        self._section_label(controls, "🧠 Whisper model").pack(
            anchor="w", padx=16, pady=(16, 8))
        model_grid = tk.Frame(controls, bg=self.PANEL)
        model_grid.pack(fill="x", padx=16)
        model_grid.columnconfigure((0, 1, 2), weight=1)
        self._model_cards = []
        self._model_card(model_grid, "base.en", "Base", "fast").grid(
            row=0, column=0, sticky="ew", padx=(0, 8))
        self._model_card(model_grid, "small.en", "Small", "balanced").grid(
            row=0, column=1, sticky="ew", padx=(0, 8))
        self._model_card(model_grid, "medium.en", "Medium", "accurate").grid(
            row=0, column=2, sticky="ew")

        self._section_label(controls, "📖 Custom vocabulary").pack(
            anchor="w", padx=16, pady=(16, 6))
        self.vocab_var = tk.StringVar(value=", ".join(self.cfg["vocabulary"]))
        ve = self._styled_entry(controls, self.vocab_var)
        ve.pack(fill="x", padx=16, ipady=7)
        ve.bind("<Return>", self._save_vocab)
        ve.bind("<FocusOut>", self._save_vocab, add="+")

        self._section_label(controls, "🗣 Per-app tone overrides — extra exe names, comma-separated").pack(
            anchor="w", padx=16, pady=(16, 6))
        overrides = self.cfg.get("tone_overrides") or {}
        self._tone_vars = {}
        for key, caption in (("casual", "Casual"), ("formal", "Formal"),
                             ("verbatim", "Verbatim")):
            tk.Label(controls, text=caption.upper(), bg=self.PANEL, fg=self.MUT,
                     font=self._mono(8)).pack(anchor="w", padx=16, pady=(6, 0))
            var = tk.StringVar(value=", ".join(overrides.get(key, [])))
            self._tone_vars[key] = var
            te = self._styled_entry(controls, var)
            te.pack(fill="x", padx=16, ipady=5)
            te.bind("<Return>", self._save_tone_overrides)
            te.bind("<FocusOut>", self._save_tone_overrides, add="+")

        self._section_label(controls, "⚡ Snippets — one per line: trigger => expansion").pack(
            anchor="w", padx=16, pady=(16, 6))
        self.snippets_text = tk.Text(controls, bg=self.CARD, fg=self.FG,
                                     insertbackground=self.ACCENT, relief="flat", bd=0,
                                     font=self._mono(9), height=4, padx=10, pady=8,
                                     highlightthickness=1, highlightbackground=self.BORDER2,
                                     highlightcolor=self.BORDER2)
        self.snippets_text.bind("<FocusIn>", lambda e: self.snippets_text.configure(
            highlightbackground=self.FG, highlightcolor=self.FG))
        self.snippets_text.bind("<FocusOut>", lambda e: self.snippets_text.configure(
            highlightbackground=self.BORDER2, highlightcolor=self.BORDER2))
        self.snippets_text.pack(fill="x", padx=16)
        tk.Label(controls, text="e.g.  omw => on my way", bg=self.PANEL, fg=self.SUBTLE,
                 font=self._sans(8)).pack(anchor="w", padx=16, pady=(3, 0))
        self.snippets_text.insert("1.0", "\n".join(
            f"{k} => {v}" for k, v in (self.cfg.get("snippets") or {}).items()))
        self.snippets_text.bind("<FocusOut>", self._save_snippets, add="+")

        self._section_label(controls, "🛡 Typing safety").pack(
            anchor="w", padx=16, pady=(16, 6))
        safety = tk.Frame(controls, bg=self.PANEL)
        safety.pack(fill="x", padx=16)
        self._btn(safety, "Review long dictations", self._dash_toggle_review,
                  accent=self.cfg.get("review_before_typing")).pack(side="left")
        self._btn(safety, "Auto-punctuate", self._dash_toggle_punctuate,
                  accent=self.cfg.get("auto_punctuate", True)).pack(
            side="left", padx=(8, 0))
        self._btn(safety, "Undo last", self._undo_last, feedback="✓ undone").pack(side="left", padx=(8, 0))
        self._btn(safety, "Clean up clipboard", self._clean_clipboard,
                  feedback="cleaning…").pack(side="left", padx=(8, 0))

        health_hdr = tk.Frame(controls, bg=self.PANEL)
        health_hdr.pack(fill="x", padx=16, pady=(16, 6))
        self._section_label(health_hdr, "❤ Health").pack(side="left")
        self._btn(health_hdr, "Retry", self._dash_retry_health, feedback="checking…").pack(side="right")
        self.health_var = tk.StringVar()
        health_box = tk.Frame(controls, bg=self.PANEL)
        health_box.pack(fill="x", padx=16)
        tk.Frame(health_box, bg=self.ACCENT, width=3).pack(side="left", fill="y")
        tk.Label(health_box, textvariable=self.health_var, bg=self.CARD,
                 fg=self.MUT, justify="left", anchor="w",
                 font=self._mono(9), padx=12, pady=10).pack(fill="x", expand=True)

        self.storage_var = tk.StringVar()
        self._section_label(controls, "💾 Storage").pack(
            anchor="w", padx=16, pady=(16, 6))
        storage_box = tk.Frame(controls, bg=self.PANEL)
        storage_box.pack(fill="x", padx=16)
        tk.Frame(storage_box, bg=self.SUBTLE, width=3).pack(side="left", fill="y")
        tk.Label(storage_box, textvariable=self.storage_var, bg=self.CARD,
                 fg=self.MUT, justify="left", anchor="w",
                 font=self._mono(9), padx=12, pady=10).pack(fill="x", expand=True)
        storage_row = tk.Frame(controls, bg=self.PANEL)
        storage_row.pack(fill="x", padx=16, pady=(8, 0))
        self._btn(storage_row, "Clear Whisper cache",
                  self._safe_clear_whisper_cache).pack(side="right")
        self._btn(storage_row, "Clear Ollama models", self._safe_clear_ollama_models,
                  danger=True).pack(side="right", padx=(0, 8))

        backup_row = tk.Frame(controls, bg=self.PANEL)
        backup_row.pack(fill="x", padx=16, pady=(8, 0))
        self._btn(backup_row, "💾 Backup settings...", self._dash_backup_settings).pack(side="right")
        self._btn(backup_row, "📂 Restore settings...", self._dash_restore_settings).pack(
            side="right", padx=(0, 8))

        self.log_var = tk.StringVar()
        tk.Label(controls, textvariable=self.log_var, bg=self.PANEL, fg=self.MUT,
                 anchor="w", justify="left", font=self._mono(9)).pack(
            fill="x", padx=16, pady=(16, 0))
        row = tk.Frame(controls, bg=self.PANEL)
        row.pack(fill="x", padx=16, pady=(12, 8))
        self._btn(row, "Copy last", self._copy_last, accent=True, feedback="✓ copied").pack(side="left")
        self._btn(row, "Logging", self._dash_toggle_log).pack(side="left", padx=(8, 0))
        self._btn(row, "Folder", self._dash_pick_folder).pack(side="left", padx=(8, 0))
        self._btn(row, "Open folder", self._open_history_folder).pack(
            side="left", padx=(8, 0))
        export_row = tk.Frame(controls, bg=self.PANEL)
        export_row.pack(fill="x", padx=16, pady=(2, 16))
        self._btn(export_row, "🗑 Purge history...", self._dash_purge, danger=True).pack(side="right")
        self._btn(export_row, "⇩ Export history...", self._dash_export_history).pack(
            side="right", padx=(0, 8))

        self._dash_refresh()

    def _toggle_pin(self, t):
        key = t.isoformat()
        pinned = list(self.cfg.get("pinned") or [])
        if key in pinned:
            pinned.remove(key)
        else:
            pinned.append(key)
        self.cfg["pinned"] = pinned
        save_config(self.cfg)
        self._recent_render_key = None
        self._dash_refresh()

    def _copy_last(self):
        if self.session:
            self._copy_text(self.session[-1]["cleaned"])

    def _dash_refresh(self):
        t = self.totals
        wpm = t["raw_w"] / t["secs"] * 60 if t["secs"] else 0
        stats = (("n", t["n"]), ("raw_w", t["raw_w"]),
                 ("cln_w", t["cln_w"]), ("wpm", round(wpm)),
                 ("streak", self._streak()))
        stats_key = tuple(stats)
        if stats_key != self._last_stats_key:
            self._last_stats_key = stats_key
            for key, val in stats:
                cv, item = self._stat_vars[key]
                cv.itemconfigure(item, text=f"{val:,}")
                if key == "streak":
                    cv.itemconfigure(item, fill=self.ACCENT if val >= 7 else
                                     (self.WARN if val >= 3 else self.DISPLAY))
                elif key == "wpm":
                    cv.itemconfigure(item, fill=self.OK if val >= 120 else self.DISPLAY)
            self._redraw_sparkline()

        if hasattr(self, "health_var"):
            h = self._health_cache[1]
            if h:
                dot = lambda ok: "🟢" if ok else "🔴"
                self.health_var.set(
                    f"{dot(h['enabled'] == 'on')} Enabled: {h['enabled']}\n"
                    f"{dot(h['loaded'] == 'ready')} Whisper: {h['whisper']} ({h['loaded']})\n"
                    f"{dot(h['ollama'] != 'not reachable')} Ollama: {h['ollama']}\n"
                    f"{dot(h['mic'] not in ('Unavailable',))} Mic: {h['mic']}\n"
                    f"Review: {h['review']}")
            else:
                self.health_var.set("Checking local services...")
            self._refresh_health_async()
        if hasattr(self, "storage_var"):
            s = self._storage_cache[1]
            if s:
                self.storage_var.set("\n".join(
                    f"{name}: {size} / {files:,} files"
                    for name, (size, files) in s.items()))
            else:
                self.storage_var.set("Calculating sizes in the background...")
            self._refresh_storage_async()

        q = self.search_var.get().strip().lower()
        pinned_ts = set(self.cfg.get("pinned") or [])
        if q:
            if self._search_cache[0] != q:
                self._search_cache = (q, self._scan_history(q))
            items = self._search_cache[1][-50:]
        else:
            items = self.session[-20:]
            missing = pinned_ts - {e["t"].isoformat() for e in items}
            if missing:
                # ponytail: full-file rescan to locate pinned entries outside the
                # last-20 window — pinning is rare, so this stays a lazy O(n) scan
                items = [e for e in self._scan_history("") if e["t"].isoformat() in missing] + items

        recent_key = (q, tuple(sorted(pinned_ts)), tuple((e["t"], e["cleaned"]) for e in items))
        if recent_key == self._recent_render_key:
            state = "on" if self.cfg["log_history"] else "off - new dictations stay in memory only"
            self.log_var.set(f"History logging: {state}\n{history_path(self.cfg)}")
            return
        self._recent_render_key = recent_key

        for child in self.recent_frame.winfo_children():
            child.destroy()
        if not items:
            tk.Label(self.recent_frame, text="No dictations yet.",
                     bg=self.PANEL, fg=self.MUT, font=self._sans(10)).pack(
                anchor="w", pady=18)
        ordered = sorted(items, key=lambda e: (e["t"].isoformat() not in pinned_ts, -e["t"].timestamp()))
        for e in ordered:
            pinned = e["t"].isoformat() in pinned_ts
            row = tk.Frame(self.recent_frame, bg=self.CARD)
            row.pack(fill="x")
            row.columnconfigure(0, weight=1)
            stamp = f'{"★ " if pinned else ""}{e["t"]:%d %b · %H:%M}'.upper()
            date_lbl = tk.Label(row, text=stamp, bg=self.CARD, fg=self.ACCENT,
                                font=self._mono(8))
            date_lbl.grid(row=0, column=0, sticky="w", padx=14, pady=(11, 0))
            text_lbl = tk.Label(row, text=e["cleaned"], bg=self.CARD, fg=self.FG,
                                justify="left", anchor="w", wraplength=330,
                                font=self._sans(10))
            text_lbl.grid(row=1, column=0, sticky="ew", padx=14, pady=(3, 11))
            btn_col = tk.Frame(row, bg=self.CARD)
            btn_col.grid(row=0, column=1, rowspan=2, padx=10, pady=10)
            self._btn(btn_col, "★" if pinned else "☆", lambda t=e["t"]: self._toggle_pin(t),
                      accent=pinned).pack(side="left")
            self._btn(btn_col, "copy", lambda text=e["cleaned"]: self._copy_text(text),
                      feedback="✓ copied").pack(side="left", padx=(6, 0))
            # hud-row hairline separator (1px), no hover fade — flat by design
            tk.Frame(self.recent_frame, bg=self.BORDER, height=1).pack(fill="x")

        state = "on" if self.cfg["log_history"] else "off - new dictations stay in memory only"
        self.log_var.set(f"History logging: {state}\n{history_path(self.cfg)}")

    # ---- tray menu

    def _toggle(self, key):
        def do(icon, item):
            self.cfg[key] = not self.cfg[key]
            if key == "start_on_login":
                try:
                    set_start_on_login(self.cfg[key])
                except OSError as e:
                    print(f"start-on-login failed: {e}")
                    self.cfg[key] = not self.cfg[key]
            save_config(self.cfg)
            if key == "enabled":
                self._refresh_tray_icon()
                self._write_runtime()
        return do

    def _pick_mic(self, index):
        def do(icon, item):
            self.cfg["input_device"] = index
            save_config(self.cfg)
        return do

    def _pick_model(self, size):
        def do(icon, item):
            if size == self.cfg["model_size"]:
                return
            self.cfg["model_size"] = size
            save_config(self.cfg)
            threading.Thread(target=self.transcriber.reload, args=(size,),
                             daemon=True).start()
        return do

    def build_menu(self):
        mics = [pystray.MenuItem(
            "System default", self._pick_mic(None),
            radio=True, checked=lambda i: self.cfg["input_device"] is None)]
        for idx, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                mics.append(pystray.MenuItem(
                    dev["name"], self._pick_mic(idx), radio=True,
                    checked=lambda i, idx=idx: self.cfg["input_device"] == idx))
        models = [pystray.MenuItem(
            s, self._pick_model(s), radio=True,
            checked=lambda i, s=s: self.cfg["model_size"] == s)
            for s in ("base.en", "small.en", "medium.en")]
        return pystray.Menu(
            pystray.MenuItem("Dashboard",
                             lambda i, item: self.launch_dashboard(),
                             default=True),
            pystray.MenuItem("Copy last dictation",
                             lambda i, item: self.ui_q.put(self._copy_last)),
            pystray.MenuItem("Undo last dictation",
                             lambda i, item: self.ui_q.put(self._undo_last)),
            pystray.MenuItem("Enabled", self._toggle("enabled"),
                             checked=lambda i: self.cfg["enabled"]),
            pystray.MenuItem("Review long dictations", self._toggle("review_before_typing"),
                             checked=lambda i: self.cfg["review_before_typing"]),
            pystray.MenuItem("Microphone", pystray.Menu(*mics)),
            pystray.MenuItem("Whisper model", pystray.Menu(*models)),
            pystray.MenuItem("Status bar", self._toggle("show_status_bar"),
                             checked=lambda i: self.cfg["show_status_bar"]),
            pystray.MenuItem("History log", self._toggle("log_history"),
                             checked=lambda i: self.cfg["log_history"]),
            pystray.MenuItem("Start on login", self._toggle("start_on_login"),
                             checked=lambda i: self.cfg["start_on_login"]),
            pystray.MenuItem("Open config folder",
                             lambda i, item: (subprocess.run(["open", APP_DIR], check=False)
                                              if IS_MAC else os.startfile(APP_DIR))),
            pystray.MenuItem("Quit", self.quit),
        )

    def make_icon_image(self, enabled=True):
        # black badge + a single smooth white waveform stroke (audio trace) —
        # logo is black & white only, no accent color — same mark as the
        # dashboard's brand-mark and dashboard/dictator.ico (assets/icon/gen_icon.py)
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        pad, radius = round(size * 0.04), round(size * 0.26)
        d.rounded_rectangle((pad, pad, size - pad, size - pad), radius=radius,
                            fill="#0e0d0c" if enabled else "#3c3c3c")
        stroke = "#ffffff" if enabled else "#828282"
        w = max(2, round(size * 0.075))
        cy = size / 2
        x0, x1 = size * 0.15, size * 0.85
        n = 32
        pts = []
        for i in range(n + 1):
            t = i / n
            x = x0 + t * (x1 - x0)
            envelope = math.sin(t * math.pi)
            y = cy + math.sin(t * math.pi * 3.4) * (size * 0.19) * envelope
            pts.append((x, y))
        d.line(pts, fill=stroke, width=w, joint="curve")
        r = w / 2
        d.ellipse((pts[0][0] - r, pts[0][1] - r, pts[0][0] + r, pts[0][1] + r), fill=stroke)
        d.ellipse((pts[-1][0] - r, pts[-1][1] - r, pts[-1][0] + r, pts[-1][1] + r), fill=stroke)
        if not enabled:
            m, lw = round(size * 0.16), max(2, round(size * 0.06))
            d.line((m, size - m, size - m, m), fill="#c2413d", width=lw)
        return img

    def _refresh_tray_icon(self):
        if self.icon:
            self.icon.icon = self.make_icon_image(self.cfg["enabled"])

    def quit(self, icon=None, item=None):
        self.running = False
        if self.icon:
            self.icon.stop()
        self.root.after(0, self.root.destroy)

    # ---- main

    def run(self):
        os.makedirs(APP_DIR, exist_ok=True)
        save_config(self.cfg)  # write defaults on first run so the file exists
        # own taskbar identity + window icon instead of pythonw's (Windows-only concept)
        if IS_WIN:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Dictator")
        self.root = tk.Tk()
        self.root.withdraw()
        self._resolve_fonts()
        self._tk_icon = ImageTk.PhotoImage(self.make_icon_image())
        self.root.iconphoto(True, self._tk_icon)
        self.overlay = Overlay(self.root, self.cfg)

        def poll_ui():
            while not self.ui_q.empty():
                self.ui_q.get()()
            self.root.after(100, poll_ui)
        poll_ui()

        # re-assert the Run-key path on every launch so a moved/renamed folder
        # self-heals — the stale path only updated on a manual menu toggle before,
        # so moving the app left start-on-login pointing at a dead path.
        if self.cfg["start_on_login"]:
            try:
                set_start_on_login(True)
            except OSError as e:
                print(f"start-on-login refresh failed: {e}")

        self._write_runtime()  # publish "loading" state before the model is up

        def _load_and_publish():
            self.transcriber.load()
            self._write_runtime()  # publish device + "ready" for the dashboard

        threading.Thread(target=_load_and_publish, daemon=True).start()
        threading.Thread(target=self.hotkey_loop, daemon=True).start()
        threading.Thread(target=self._watch_config_file, daemon=True).start()

        self.ollama_model = resolve_ollama_model(self.cfg)
        if self.ollama_model:
            print(f"cleanup model: {self.ollama_model}")
        else:
            print("Ollama not reachable or no model pulled — dictation will use "
                  f"raw transcripts. Fix: ollama pull {PREFERRED_MODELS[0]}")

        self.icon = pystray.Icon("Dictator", self.make_icon_image(self.cfg["enabled"]),
                                 "Dictator", self.build_menu())
        self.icon.run_detached()
        print("Dictator running. Hold Ctrl+Win to dictate. Quit from the tray icon.")
        try:
            self.root.mainloop()
        finally:
            self.running = False
            os._exit(0)  # pystray/keyboard threads don't always die cleanly


# ---------------------------------------------------------------- selftest

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
