"""The App class: hotkey loop, transcription/cleanup pipeline, dashboard, tray menu."""
import ctypes
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import date, datetime, timedelta
from tkinter import colorchooser, filedialog, messagebox, ttk

import keyboard
import numpy as np
import pystray
import sounddevice as sd
from PIL import Image, ImageDraw, ImageTk

from .config import APP_DIR, CONFIG_PATH, RUNTIME_PATH, load_config, save_config, history_path, folder_size, fmt_bytes
from .history import log_history
from .hotkeys import HOTKEY_PRESETS, hotkey_down, wait_keys_released, win_pressed
from .injection import inject_text
from .ollama_client import PREFERRED_MODELS, ollama_cleanup, resolve_ollama_model
from .overlay import Overlay, animate_color, lerp_color, round_rect
from .paths import OLLAMA_MODELS_DIR, REPO_ROOT, VENV_DIR, WHISPER_CACHE
from .sendinput import send_backspaces, send_noop_key
from .startup import set_start_on_login
from .textshaping import apply_commands, basic_punctuate, expand_snippet, quick_clean
from .tone import foreground_app, tone_for
from .transcriber import SAMPLE_RATE, Transcriber

user32 = ctypes.windll.user32


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
    # another process (the webview dashboard, or an external settings tool).
    # Most just need self.cfg updated — the hotkey loop, mic capture and
    # pipeline all read self.cfg fresh each time. model_size (whisper reload)
    # and enabled (tray icon) get extra side effects in apply() below.
    SYNC_KEYS = ("snippets", "vocabulary", "tone_overrides", "auto_punctuate",
                 "review_before_typing", "hotkey_mods", "hotkey_mode",
                 "input_device", "enabled", "log_history", "model_size",
                 "history_dir", "show_status_bar", "theme", "accent_color",
                 "auto_theme")

    def _watch_config_file(self):
        """Picks up config.json edits made by something other than this
        process (the webview dashboard, or an external settings tool) without a
        restart. Comparing values instead of tracking "was this our own write"
        means Dictator's own save_config() calls are naturally a no-op here —
        the file already matches self.cfg for these keys."""
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

    def _write_runtime(self):
        """Mirror in-process live state to runtime.json for the separate-process
        webview dashboard (health panel, copy-last, undo-last). Best-effort;
        file-only, so safe to call from worker threads."""
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
        script = os.path.join(REPO_ROOT, "dashboard", "dashboard.py")
        if os.path.exists(script):
            proc = getattr(self, "_dash_proc", None)
            if proc and proc.poll() is None:
                return  # already open
            scripts_pyw = os.path.join(sys.prefix, "Scripts", "pythonw.exe")
            pythonw = scripts_pyw if os.path.exists(scripts_pyw) \
                else os.path.join(sys.prefix, "pythonw.exe")
            try:
                self._write_runtime()  # make sure health/last-text are current
                self._dash_proc = subprocess.Popen(
                    [pythonw, script], cwd=os.path.dirname(script))
                return
            except OSError as e:
                print(f"webview dashboard failed to launch ({e}); using Tk fallback")
        self.ui_q.put(self.open_dashboard)

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

    def _animate_count(self, cv, item, start, end, steps=10):
        if start == end:
            cv.itemconfigure(item, text=f"{end:,}")
            return

        def step(i):
            try:
                if not cv.winfo_exists():
                    return
                val = round(start + (end - start) * (i / steps))
                cv.itemconfigure(item, text=f"{val:,}")
                if i < steps:
                    cv.after(20, step, i + 1)
            except tk.TclError:
                pass

        step(0)

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
        pts = []
        for i, v in enumerate(values):
            pts.extend([i * step, 88 - (v / top) * 16])
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

    def record_stream(self, stop):
        chunks = []

        def cb(indata, frames, t, status):
            chunks.append(indata.copy())
            self.overlay.set_level(float(np.abs(indata).mean()) * 8)  # rough level, 0..1ish

        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                device=self.cfg["input_device"], callback=cb):
                while not stop():
                    time.sleep(0.02)
        except Exception as e:
            print(f"mic unavailable: {e}")
            while not stop():
                time.sleep(0.02)
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)[:, 0] if chunks else np.zeros(0, dtype=np.float32)

    def hotkey_loop(self):
        last_tap = 0.0
        while self.running:
            if not (self.cfg["enabled"] and hotkey_down(self.cfg)):
                time.sleep(0.02)
                continue
            target_hwnd = user32.GetForegroundWindow()
            tone = tone_for(*foreground_app(), self.cfg)  # capture target app before dictating
            self.overlay.set_state("listening")
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
                    audio = self.record_stream(lambda: hotkey_down(self.cfg))
                    if "win" in (self.cfg.get("hotkey_mods") or ["ctrl", "win"]) \
                            and win_pressed() and not hotkey_down(self.cfg):
                        from .sendinput import send_noop_key
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
            threading.Thread(target=self.process, args=(audio, tone, target_hwnd),
                             daemon=True).start()

    def process(self, audio, tone=None, target_hwnd=None):
        secs = len(audio) / SAMPLE_RATE
        try:
            raw = self.transcriber.transcribe(audio, self.cfg["vocabulary"])
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
            self.last_injected_text = cleaned
            self._write_runtime()  # refresh last_text for the dashboard's copy-last/undo
            now = datetime.now()
            log_history(self.cfg, raw, cleaned, secs)
            self.ui_q.put(lambda: self._record_dictation(raw, cleaned, secs, now))
            snippet = cleaned if len(cleaned) <= 28 else cleaned[:27] + "…"
            self.overlay.set_state("done", detail=snippet.replace("\n", " "))
        except Exception as e:
            print(f"pipeline error: {type(e).__name__}: {e}")
            self.overlay.set_state("hide")

    # ---- dashboard window (tk main thread only)

    THEMES = {
        # Nothing-inspired: monochrome canvas, red (#D71921) as the one accent.
        # Overlay pill keeps its own hardcoded palette — it is not themed here.
        "dark": dict(BG="#000000", PANEL="#111111", CARD="#191919", CARD_2="#262626",
                     FG="#E8E8E8", MUT="#999999", SUBTLE="#666666",
                     ACCENT="#D71921", ACCENT_DARK="#3A1012", DANGER="#D71921"),
        "light": dict(BG="#F5F5F5", PANEL="#FFFFFF", CARD="#F0F0F0", CARD_2="#DADADA",
                      FG="#1A1A1A", MUT="#666666", SUBTLE="#999999",
                      ACCENT="#D71921", ACCENT_DARK="#F3D2D4", DANGER="#C21620"),
    }
    # class-level fallback (used before _apply_theme runs); overridden per-instance
    BG = THEMES["dark"]["BG"]
    PANEL = THEMES["dark"]["PANEL"]
    CARD = THEMES["dark"]["CARD"]
    CARD_2 = THEMES["dark"]["CARD_2"]
    FG = THEMES["dark"]["FG"]
    MUT = THEMES["dark"]["MUT"]
    SUBTLE = THEMES["dark"]["SUBTLE"]
    ACCENT = THEMES["dark"]["ACCENT"]
    ACCENT_DARK = THEMES["dark"]["ACCENT_DARK"]
    DANGER = THEMES["dark"]["DANGER"]

    def _apply_theme(self):
        for key, val in self.THEMES.get(self.cfg.get("theme", "dark"),
                                        self.THEMES["dark"]).items():
            setattr(self, key, val)
        if self.cfg.get("accent_color"):
            self.ACCENT = self.cfg["accent_color"]

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
        f = tkfont.Font(family="Segoe UI Semibold", size=9)
        w = f.measure(text) + 30
        bg = parent.cget("bg") if hasattr(parent, "cget") else self.BG
        base = self.DANGER if danger else (self.ACCENT_DARK if accent else self.CARD_2)
        hover = "#B33036" if danger else ("#6E1A1F" if accent else "#2E2E2E")
        press = "#8F2127" if danger else ("#4A1013" if accent else "#191919")
        outline = base
        cv = tk.Canvas(parent, width=w, height=32, bg=bg,
                       highlightthickness=0, cursor="hand2")
        rect = round_rect(cv, 1, 1, w - 1, 31, 8, fill=base, outline=outline, width=1)
        fill = "#ffffff" if accent else self.FG
        label = cv.create_text(w // 2, 16, text=text, fill=fill, font=f)

        def on_click(_e):
            animate_color(cv, rect, base, press, ms=70, steps=3)
            cv.after(90, lambda: animate_color(cv, rect, press, hover, ms=90, steps=4))
            cmd()
            if feedback:
                cv.itemconfigure(label, text=feedback)
                cv.after(1100, lambda: cv.winfo_exists() and cv.itemconfigure(label, text=text))

        def on_enter(_e):
            animate_color(cv, rect, base, hover, ms=110, steps=6)
            if danger:
                cv.itemconfigure(rect, outline="#e0827a", width=1.5)

        def on_leave(_e):
            animate_color(cv, rect, hover, base, ms=110, steps=6)
            if danger:
                cv.itemconfigure(rect, outline=outline, width=1)

        cv.bind("<Button-1>", on_click)
        cv.bind("<Enter>", on_enter)
        cv.bind("<Leave>", on_leave)
        return cv

    def _section_label(self, parent, text):
        return tk.Label(parent, text="▍ " + text, bg=parent.cget("bg"), fg=self.SUBTLE,
                        font=("Segoe UI Semibold", 9))

    def _styled_entry(self, parent, var, width=None):
        e = tk.Entry(parent, textvariable=var, bg=self.CARD_2, fg=self.FG,
                     insertbackground=self.FG, relief="flat", bd=0,
                     font=("Segoe UI", 10), width=width, highlightthickness=1,
                     highlightbackground=self.CARD_2, highlightcolor=self.CARD_2)
        e.bind("<FocusIn>", lambda ev: e.configure(highlightbackground=self.ACCENT, highlightcolor=self.ACCENT))
        e.bind("<FocusOut>", lambda ev: e.configure(highlightbackground=self.CARD_2, highlightcolor=self.CARD_2))
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
        for card in getattr(self, "_model_cards", []):
            card.event_generate("<Configure>")

    def _model_card(self, parent, size, title, note):
        cv = tk.Canvas(parent, height=64, bg=self.PANEL, highlightthickness=0,
                       cursor="hand2")
        rect = round_rect(cv, 0, 0, 10, 10, 8, fill=self.CARD)
        title_item = cv.create_text(16, 20, anchor="w", text=title, fill=self.FG,
                                    font=("Segoe UI Semibold", 10))
        note_item = cv.create_text(16, 42, anchor="w", text=note, fill=self.MUT,
                                   font=("Segoe UI", 8))

        def repaint(e=None):
            active = self.cfg["model_size"] == size
            loading = self._model_loading == size
            fill = self.ACCENT_DARK if active else self.CARD
            outline = self.ACCENT if active else self.CARD_2
            width = cv.winfo_width() or 150
            cv.coords(rect, 0, 0, width, 64)
            cv.itemconfigure(rect, fill=fill, outline=outline, width=1.6 if active else 1)
            cv.itemconfigure(title_item, fill=self.ACCENT if active else self.FG)
            cv.itemconfigure(note_item, text="loading…" if loading else note,
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
            animate_color(cv, rect, cv.itemcget(rect, "fill"), self.ACCENT_DARK, ms=80, steps=3)
            cv.after(90, repaint)
            self._dash_pick_model_size(size)
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

        tk.Label(win, text="Review before typing", bg=self.BG, fg=self.FG,
                 font=("Segoe UI Semibold", 18)).pack(anchor="w", padx=22, pady=(20, 4))
        tk.Label(win, text="Edit the text, then type it into the active app.",
                 bg=self.BG, fg=self.MUT, font=("Segoe UI", 10)).pack(
            anchor="w", padx=22)

        text = tk.Text(win, bg=self.PANEL, fg=self.FG, insertbackground=self.FG,
                       relief="flat", bd=0, wrap="word", font=("Segoe UI", 11),
                       padx=14, pady=12, height=10)
        text.pack(fill="both", expand=True, padx=22, pady=18)
        text.insert("1.0", cleaned)
        text.focus_set()

        raw_preview = tk.Label(win, text=f"Raw: {raw[:160]}",
                               bg=self.BG, fg=self.SUBTLE, justify="left",
                               anchor="w", font=("Segoe UI", 8))
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
        allowed = os.path.normpath(os.path.join(REPO_ROOT, "Cache"))
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
        cache = os.path.normpath(OLLAMA_MODELS_DIR)
        allowed = os.path.normpath(REPO_ROOT)
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
        items = {
            "Whisper cache": WHISPER_CACHE,
            "Ollama models": OLLAMA_MODELS_DIR,
            "Python env": VENV_DIR,
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
        style.configure("Dictator.TCombobox", fieldbackground=self.CARD_2,
                        background=self.CARD_2, foreground=self.FG,
                        arrowcolor=self.ACCENT, bordercolor=self.CARD_2,
                        lightcolor=self.CARD_2, darkcolor=self.CARD_2)
        style.map("Dictator.TCombobox",
                  bordercolor=[("focus", self.ACCENT), ("active", self.ACCENT_DARK)],
                  lightcolor=[("focus", self.ACCENT)], darkcolor=[("focus", self.ACCENT)])
        style.configure("Dictator.Capturing.TCombobox", fieldbackground=self.ACCENT_DARK,
                        background=self.ACCENT_DARK, foreground=self.FG,
                        arrowcolor=self.ACCENT, bordercolor=self.ACCENT,
                        lightcolor=self.ACCENT_DARK, darkcolor=self.ACCENT_DARK)

        shell = tk.Frame(d, bg=self.BG)
        shell.pack(fill="both", expand=True, padx=28, pady=24)

        hero = tk.Canvas(shell, height=96, bg=self.BG, highlightthickness=0)
        hero.pack(fill="x")

        def hero_resize(e):
            hero.delete("bg")
            round_rect(hero, 0, 0, e.width, 96, 8, fill=self.PANEL, tags="bg")
            hero.tag_lower("bg")
            hero.coords("underline", 26, 40, 26 + 34, 40)
            hero.coords("local", e.width - 28, 28)
            hero.coords("local-pill", e.width - 58, 18, e.width - 12, 38)
            hero.tag_raise("local-pill")
            hero.tag_raise("local")

        hero.bind("<Configure>", hero_resize)
        hero.create_text(26, 28, anchor="w", text="Dictator", fill=self.FG,
                         font=("Segoe UI Semibold", 24))
        hero.create_line(26, 40, 60, 40, fill=self.ACCENT, width=2, tags="underline")
        hero.create_text(26, 62, anchor="w",
                         text="Hold Ctrl + Win to dictate. Double-tap for hands-free. Everything stays local.",
                         fill=self.MUT, font=("Segoe UI", 10))
        round_rect(hero, 0, 0, 10, 10, 9, fill=self.CARD_2, outline="", tags="local-pill")
        hero.create_text(704, 28, anchor="e", text="local", fill=self.ACCENT,
                         font=("Segoe UI Semibold", 10), tags="local")

        cards = tk.Frame(shell, bg=self.BG)
        cards.pack(fill="x", pady=(18, 8))
        self._stat_vars = {}
        for col, (key, caption) in enumerate([
                ("n", "dictations"), ("raw_w", "words spoken"),
                ("cln_w", "words typed"), ("wpm", "average wpm"),
                ("streak", "day streak")]):
            cv = tk.Canvas(cards, height=92, bg=self.BG, highlightthickness=0)
            cv.grid(row=0, column=col, sticky="nsew",
                    padx=(0, 12) if col < 4 else 0)
            cards.columnconfigure(col, weight=1)
            num = cv.create_text(0, 34, text="0", fill=self.FG,
                                 font=("Segoe UI Semibold", 22))
            cap = cv.create_text(0, 66, text=caption, fill=self.MUT,
                                 font=("Segoe UI", 9))
            spark = cv.create_line(0, 0, 0, 0, fill=self.ACCENT, width=2,
                                   smooth=True, tags="spark") if key == "n" else None

            def resize(e, cv=cv, num=num, cap=cap):
                cv.delete("bg")
                round_rect(cv, 0, 0, e.width, 92, 8, fill=self.CARD, outline=self.CARD, width=1, tags="bg")
                cv.tag_lower("bg")
                cv.coords(num, e.width / 2, 34)
                cv.coords(cap, e.width / 2, 66)

            cv.bind("<Configure>", resize)
            cv.bind("<Enter>", lambda e, cv=cv: cv.itemconfigure("bg", outline=self.ACCENT), add="+")
            cv.bind("<Leave>", lambda e, cv=cv: cv.itemconfigure("bg", outline=self.CARD), add="+")
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
                 font=("Segoe UI Semibold", 13)).pack(side="left")
        self.search_var = tk.StringVar()
        se = self._styled_entry(recent_hdr, self.search_var, width=24)
        se.pack(side="right", ipady=5)
        se.bind("<KeyRelease>", lambda e: self._dash_refresh())
        se.bind("<FocusIn>", lambda e: se.configure(highlightthickness=1,
                highlightbackground=self.ACCENT, highlightcolor=self.ACCENT))
        se.bind("<FocusOut>", lambda e: se.configure(highlightthickness=0))
        tk.Label(recent_hdr, text="search", bg=self.PANEL, fg=self.MUT,
                 font=("Segoe UI", 9)).pack(side="right", padx=(0, 8))
        tk.Frame(recent, bg=self.CARD_2, height=1).pack(fill="x", padx=16)

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
                 font=("Segoe UI Semibold", 13)).pack(anchor="w", padx=16, pady=(16, 4))
        tk.Label(controls, text="Everyday settings, kept within reach.",
                 bg=self.PANEL, fg=self.MUT, font=("Segoe UI", 9)).pack(
            anchor="w", padx=16, pady=(0, 10))
        tk.Frame(controls, bg=self.CARD_2, height=1).pack(fill="x", padx=16, pady=(0, 6))

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
                           state="readonly", font=("Segoe UI", 10),
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
                          values=display_names, state="readonly", font=("Segoe UI", 10),
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
            tk.Label(controls, text=caption, bg=self.PANEL, fg=self.MUT,
                     font=("Segoe UI", 8)).pack(anchor="w", padx=16, pady=(6, 0))
            var = tk.StringVar(value=", ".join(overrides.get(key, [])))
            self._tone_vars[key] = var
            te = self._styled_entry(controls, var)
            te.pack(fill="x", padx=16, ipady=5)
            te.bind("<Return>", self._save_tone_overrides)
            te.bind("<FocusOut>", self._save_tone_overrides, add="+")

        self._section_label(controls, "⚡ Snippets — one per line: trigger => expansion").pack(
            anchor="w", padx=16, pady=(16, 6))
        self.snippets_text = tk.Text(controls, bg=self.CARD_2, fg=self.FG,
                                     insertbackground=self.FG, relief="flat", bd=0,
                                     font=("Segoe UI", 9), height=4, padx=10, pady=8,
                                     highlightthickness=1, highlightbackground=self.CARD_2,
                                     highlightcolor=self.CARD_2)
        self.snippets_text.bind("<FocusIn>", lambda e: self.snippets_text.configure(
            highlightbackground=self.ACCENT, highlightcolor=self.ACCENT))
        self.snippets_text.bind("<FocusOut>", lambda e: self.snippets_text.configure(
            highlightbackground=self.CARD_2, highlightcolor=self.CARD_2))
        self.snippets_text.pack(fill="x", padx=16)
        tk.Label(controls, text="e.g.  omw => on my way", bg=self.PANEL, fg=self.SUBTLE,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=16, pady=(3, 0))
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
                 font=("Segoe UI", 9), padx=12, pady=10).pack(fill="x", expand=True)

        self.storage_var = tk.StringVar()
        self._section_label(controls, "💾 Storage").pack(
            anchor="w", padx=16, pady=(16, 6))
        storage_box = tk.Frame(controls, bg=self.PANEL)
        storage_box.pack(fill="x", padx=16)
        tk.Frame(storage_box, bg=self.SUBTLE, width=3).pack(side="left", fill="y")
        tk.Label(storage_box, textvariable=self.storage_var, bg=self.CARD,
                 fg=self.MUT, justify="left", anchor="w",
                 font=("Segoe UI", 9), padx=12, pady=10).pack(fill="x", expand=True)
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
                 anchor="w", justify="left", font=("Segoe UI", 9)).pack(
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
            prev = dict(self._last_stats_key) if self._last_stats_key else {}
            self._last_stats_key = stats_key
            for key, val in stats:
                cv, item = self._stat_vars[key]
                self._animate_count(cv, item, prev.get(key, val), val)
                if key == "streak":
                    cv.itemconfigure(item, fill=self.ACCENT if val >= 7 else
                                     ("#D4A843" if val >= 3 else self.FG))
                elif key == "wpm":
                    cv.itemconfigure(item, fill="#4A9E5C" if val >= 120 else self.FG)
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
                     bg=self.PANEL, fg=self.MUT, font=("Segoe UI", 10)).pack(
                anchor="w", pady=18)
        ordered = sorted(items, key=lambda e: (e["t"].isoformat() not in pinned_ts, -e["t"].timestamp()))
        for e in ordered:
            pinned = e["t"].isoformat() in pinned_ts
            row = tk.Frame(self.recent_frame, bg=self.CARD)
            row.pack(fill="x", pady=(0, 8))
            row.columnconfigure(0, weight=1)
            date_lbl = tk.Label(row, text=f'{"★ " if pinned else ""}{e["t"]:%d %b, %H:%M}',
                                bg=self.CARD, fg=self.ACCENT, font=("Segoe UI Semibold", 8))
            date_lbl.grid(row=0, column=0, sticky="w", padx=12, pady=(10, 0))
            text_lbl = tk.Label(row, text=e["cleaned"], bg=self.CARD, fg=self.FG,
                                justify="left", anchor="w", wraplength=330,
                                font=("Segoe UI", 10))
            text_lbl.grid(row=1, column=0, sticky="ew", padx=12, pady=(3, 10))
            btn_col = tk.Frame(row, bg=self.CARD)
            btn_col.grid(row=0, column=1, rowspan=2, padx=10, pady=10)
            self._btn(btn_col, "★" if pinned else "☆", lambda t=e["t"]: self._toggle_pin(t),
                      accent=pinned).pack(side="left")
            self._btn(btn_col, "copy", lambda text=e["cleaned"]: self._copy_text(text),
                      feedback="✓ copied").pack(side="left", padx=(6, 0))

            def _fade_bg(widgets, from_c, to_c, i=0, steps=5):
                try:
                    if not widgets[0].winfo_exists():
                        return
                    c = lerp_color(from_c, to_c, i / steps)
                    for w in widgets:
                        w.configure(bg=c)
                    if i < steps:
                        widgets[0].after(18, lambda: _fade_bg(widgets, from_c, to_c, i + 1, steps))
                except tk.TclError:
                    pass

            def _hover_on(_e, widgets=(row, date_lbl, text_lbl, btn_col)):
                _fade_bg(widgets, self.CARD, self.CARD_2)

            def _hover_off(_e, widgets=(row, date_lbl, text_lbl, btn_col)):
                _fade_bg(widgets, self.CARD_2, self.CARD)

            for w in (row, date_lbl, text_lbl, btn_col):
                w.bind("<Enter>", _hover_on)
                w.bind("<Leave>", _hover_off)

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
            threading.Thread(
                target=lambda: (self.transcriber.reload(size), self._write_runtime()),
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
                             lambda i, item: os.startfile(APP_DIR)),
            pystray.MenuItem("Quit", self.quit),
        )

    def make_icon_image(self, enabled=True):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((8, 8, 56, 56), fill="#2c3e50" if enabled else "#4a4f57")
        mic_color = "white" if enabled else "#9aa3af"
        d.rounded_rectangle((26, 16, 38, 38), radius=6, fill=mic_color)  # mic body
        d.line((32, 40, 32, 48), fill=mic_color, width=3)
        d.line((24, 48, 40, 48), fill=mic_color, width=3)
        if not enabled:
            d.line((14, 50, 50, 14), fill="#c2413d", width=4)  # disabled slash
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
        # own taskbar identity + window icon instead of pythonw's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Dictator")
        self.root = tk.Tk()
        self.root.withdraw()
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
