"""End-to-end verifier: drives the real App.hotkey_loop -> record_stream ->
StreamingSTT -> process -> SendInput path with clip audio fed into the warm Mic
callback in real time, and synthesized Ctrl+Win holds.

Usage: python audit/e2e.py apps|full|soak|edge [...]
Writes audit/e2e_out/<phase>.json. Never touches the user's real history.
"""
import ctypes, glob, json, os, random, subprocess, sys, threading, time, wave
from ctypes import wintypes
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "audit"))
import main  # noqa: E402
from bench import wer  # noqa: E402

OUT = os.path.join(ROOT, "audit", "e2e_out")
CLIPS = os.path.join(ROOT, "audit", "clips")
SCRATCH = os.path.join(OUT, "scratch")
os.makedirs(SCRATCH, exist_ok=True)
user32 = ctypes.windll.user32
LOG = []


class Tee:
    def __init__(self, s): self.s = s
    def write(self, x):
        LOG.append(x); self.s.write(x)
    def flush(self): self.s.flush()


sys.stdout = Tee(sys.stdout)


def load(name):
    with wave.open(os.path.join(CLIPS, name + ".wav")) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    ref = open(os.path.join(CLIPS, name + ".txt"), encoding="utf-8-sig").read().strip()
    return a, ref


# ---------------------------------------------------------------- keys / windows
KEYUP = 0x0002


def key(vk, up=False):
    main._send_inputs([main._key_input(vk=vk, flags=KEYUP if up else 0)])


def combo(*vks):
    for v in vks: key(v)
    for v in reversed(vks): key(v, True)
    time.sleep(0.08)


def release_all():
    for v in (0x11, 0xA2, 0xA3, 0x5B, 0x5C, 0x10, 0x12):
        key(v, True)


def windows():
    res = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        if user32.IsWindowVisible(h):
            n = user32.GetWindowTextLengthW(h)
            b = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, b, n + 1)
            if b.value:
                res.append((h, b.value))
        return True
    user32.EnumWindows(CB(cb), 0)
    return res


def find_window(sub, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        for h, t in windows():
            if sub.lower() in t.lower():
                return h
        time.sleep(0.3)
    return None


def focus(h):
    for _ in range(5):
        key(main.VK_NOOP); key(main.VK_NOOP, True)  # earns foreground rights
        user32.ShowWindow(h, 9)
        user32.SetForegroundWindow(h)
        time.sleep(0.3)
        if user32.GetForegroundWindow() == h:
            return True
    return False


def click_center(h):
    r = wintypes.RECT(); user32.GetWindowRect(h, ctypes.byref(r))
    user32.SetCursorPos((r.left + r.right) // 2, (r.top + r.bottom) // 2)
    user32.mouse_event(2, 0, 0, 0, 0); user32.mouse_event(4, 0, 0, 0, 0)
    time.sleep(0.2)


def clipboard():
    return subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                          capture_output=True, text=True, encoding="utf-8").stdout or ""


def rss_mb():
    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + \
                   [(n, ctypes.c_size_t) for n in ("Peak", "WS", "a", "b", "c", "d", "Pagefile", "PeakPagefile")]
    p = PMC(); p.cb = ctypes.sizeof(p)
    ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(p), p.cb)
    return round(p.WS / 2**20, 1), round(p.Pagefile / 2**20, 1)


# ---------------------------------------------------------------- app harness
class Feeder(threading.Thread):
    def __init__(self, mic):
        super().__init__(daemon=True); self.mic = mic; self.buf = None; self.pos = 0
        self.done = threading.Event(); self.lock = threading.Lock()

    def play(self, audio):
        with self.lock:
            self.buf, self.pos = audio, 0; self.done.clear()

    def run(self):
        nxt = time.perf_counter()
        while True:
            with self.lock:
                if self.buf is not None:
                    blk = self.buf[self.pos:self.pos + 512]; self.pos += 512
                    if self.pos >= len(self.buf):
                        self.buf = None; self.done.set()
                else:
                    blk = np.zeros(0, np.float32)
            if len(blk) < 512:
                blk = np.concatenate([blk, np.zeros(512 - len(blk), np.float32)])
            self.mic._cb(blk.reshape(-1, 1), 512, None, None)
            nxt += 0.032
            time.sleep(max(0, nxt - time.perf_counter()))


class Harness:
    def __init__(self):
        self.app = app = main.App()
        app.cfg["log_history"] = False
        app.cfg["history_dir"] = OUT
        app._write_runtime = lambda: None  # don't touch the real runtime.json
        app.overlay = type("O", (), {"set_state": lambda *a, **k: None, "set_level": lambda *a, **k: None})()
        app._ensure_transcriber()
        app.mic = main.Mic(None, lambda l: None)
        app.mic.healthy = lambda: True
        app._warm_mic = lambda: app.mic
        self.injected = []
        orig = main.inject_text
        main.inject_text = lambda text, cfg: (self.injected.append(text), orig(text, cfg))[1]
        self.lat_path = os.path.join(OUT, "latency.jsonl")
        self.feeder = Feeder(app.mic); self.feeder.start()
        self.loop = threading.Thread(target=app.hotkey_loop, daemon=True); self.loop.start()

    def lat_lines(self):
        try:
            return open(self.lat_path, encoding="utf-8").read().splitlines()
        except OSError:
            return []

    def dictate(self, audio, hold_extra=0.3, wait=5.0):
        """Hold Ctrl+Win, play audio, release; return (latency dict|None, injected|None)."""
        n0, i0, log0 = len(self.lat_lines()), len(self.injected), len(LOG)
        try:
            key(0x11); key(0x5B)
            time.sleep(0.05)
            if audio is not None and len(audio):
                self.feeder.play(audio); self.feeder.done.wait(len(audio) / 16000 + 5)
            time.sleep(hold_extra)
        finally:
            key(0x11, True); time.sleep(0.03); key(0x5B, True)
        t_rel = time.time()
        while time.time() - t_rel < wait:
            if len(self.lat_lines()) > n0:
                break
            if "(no speech detected)" in "".join(LOG[log0:]):
                break
            time.sleep(0.02)
        lines = self.lat_lines()
        lat = json.loads(lines[-1]) if len(lines) > n0 else None
        time.sleep(0.25)
        inj = self.injected[i0] if len(self.injected) > i0 else None
        return lat, inj, "".join(LOG[log0:])


def norm_ws(s):
    return " ".join(s.replace("\r", "\n").split())


# ---------------------------------------------------------------- targets
CODE = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe")
WORD = r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


class Target:
    """open() -> hwnd; clear(); read() -> text; close()."""
    proc = None
    def clear(self):
        focus(self.h); combo(0x11, 0x41); key(0x2E); key(0x2E, True); time.sleep(0.15)
    def read(self):
        focus(self.h); combo(0x11, 0x41); combo(0x11, 0x43); time.sleep(0.2)
        t = clipboard(); key(0x23); key(0x23, True)  # End: drop selection
        return t
    def close(self):
        if self.proc:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True)


class Notepad(Target):
    """Notepad automation target"""
    name = "notepad"
    def open(self):
        self.f = os.path.join(SCRATCH, "e2e_notepad.txt"); open(self.f, "w").close()
        subprocess.Popen(["notepad.exe", self.f]); self.h = find_window("e2e_notepad"); return self.h
    def close(self):
        self.clear(); combo(0x11, 0x53); time.sleep(0.3); combo(0x11, 0x57)  # save empty, close tab
        time.sleep(0.5)


class Chrome(Target):
    """Chrome automation target"""
    name = "chrome"
    def open(self):
        page = "data:text/html,<title>E2EPAD</title><textarea autofocus style='width:95vw;height:90vh'></textarea>"
        self.proc = subprocess.Popen([CHROME, "--user-data-dir=" + os.path.join(SCRATCH, "chrome"),
                                      "--no-first-run", "--no-default-browser-check", "--new-window", page])
        self.h = find_window("E2EPAD")
        if self.h:
            time.sleep(1.5); focus(self.h); click_center(self.h)
        return self.h


class VSCode(Target):
    name = "vscode"
    def open(self):
        self.proc = subprocess.Popen([CODE, "--user-data-dir", os.path.join(SCRATCH, "code"),
                                      "--extensions-dir", os.path.join(SCRATCH, "codeext"),
                                      "--new-window", "--disable-workspace-trust", "--skip-welcome"])
        h = None
        for _ in range(60):  # only windows owned by our scratch-profile Code.exe, never the user's
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "Get-CimInstance Win32_Process -Filter \"Name='Code.exe'\" | ? { $_.CommandLine -match 'e2e_out' } | % ProcessId"],
                                 capture_output=True, text=True).stdout.split()
            pids = {int(x) for x in out}
            for hw, t in windows():
                pid = wintypes.DWORD(); user32.GetWindowThreadProcessId(hw, ctypes.byref(pid))
                if pid.value in pids and "Visual Studio Code" in t:
                    h = hw
            if h:
                break
            time.sleep(1)
        if not h:
            return None
        time.sleep(4); focus(h); combo(0x11, 0x4E); time.sleep(1.5)
        self.h = h if focus(h) else None
        return self.h
    def close(self):
        # Code.exe launcher may hand off; kill every Code.exe using our scratch profile
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name='Code.exe'\" | ? { $_.CommandLine -match 'e2e_out' } "
                        "| % { Stop-Process -Id $_.ProcessId -Force -EA 0 }"], capture_output=True)


class Word(Target):
    name = "word"
    def open(self):
        f = os.path.join(SCRATCH, "e2e_word.rtf")
        open(f, "w").write(r"{\rtf1 }")
        self.proc = subprocess.Popen([WORD, "/q", f])
        self.h = find_window("e2e_word", 60)
        if self.h:
            time.sleep(2)
        return self.h
    def close(self):
        subprocess.run(["taskkill", "/F", "/IM", "WINWORD.EXE"], capture_output=True)


class Terminal(Target):
    """Windows Terminal running a Read-Host loop that appends each line to a file."""
    name = "terminal"
    def open(self):
        self.f = os.path.join(SCRATCH, "e2e_term.txt"); open(self.f, "w").close()
        ps = ("while($true){ $x = Read-Host 'dict'; if($x -eq 'QUIT'){exit}; "
              f"Add-Content -Encoding utf8 -LiteralPath '{self.f}' -Value $x }}")
        ps1 = os.path.join(SCRATCH, "e2e_term.ps1")
        open(ps1, "w").write(ps)  # a file, since wt.exe treats ';' as its own command separator
        subprocess.Popen(["wt.exe", "-w", "new", "--title", "E2ETERM", "--suppressApplicationTitle",
                          "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1])
        self.h = find_window("E2ETERM", 45)
        if self.h:
            time.sleep(2)
        return self.h
    def clear(self):
        focus(self.h)
    def read(self):
        focus(self.h); key(0x0D); key(0x0D, True); time.sleep(0.8)
        lines = open(self.f, encoding="utf-8-sig").read().splitlines()
        return lines[-1] if lines else ""
    def close(self):
        focus(self.h); main.send_text_keystrokes("QUIT\n"); time.sleep(0.5)


TARGETS = {"notepad": Notepad, "chrome": Chrome, "vscode": VSCode, "word": Word, "terminal": Terminal}


def run_one(hz, tgt, name, audio=None, ref=None):
    tgt.clear()
    if audio is None:
        audio, ref = load(name)
    lat, inj, log = hz.dictate(audio)
    got = tgt.read()
    row = {"clip": name, "audio_s": round(len(audio) / 16000, 2), "lat": lat, "injected": inj,
           "readback": got, "match": inj is not None and norm_ws(got) == norm_ws(inj),
           "pipeline_error": "pipeline error" in log}
    if ref is not None:
        e, n = wer(ref, got); row.update(wer_err=e, wer_n=n, wer=round(e / max(n, 1), 3))
        e2, _ = wer(ref, inj or ""); row.update(wer_inj_err=e2, wer_inj=round(e2 / max(n, 1), 3))
    print(f"[e2e] {tgt.name}/{name}: match={row['match']} wer={row.get('wer')} "
          f"total_ms={lat and lat.get('total_ms')} got={got!r}")
    return row


def pct(v, p):
    return float(np.percentile(v, p)) if v else None


def save(phase, data):
    with open(os.path.join(OUT, phase + ".json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


def main_():
    phase = sys.argv[1]
    hz = Harness()
    try:
        if phase == "apps":
            res = {}
            for tname in sys.argv[2:] or list(TARGETS):
                t = TARGETS[tname]()
                try:
                    if not t.open() or not focus(t.h):
                        res[tname] = {"error": "window not found / not focusable"}
                        continue
                    res[tname] = [run_one(hz, t, c) for c in ("short1", "med1", "tech2")]
                except Exception as e:
                    res[tname] = {"error": f"{type(e).__name__}: {e}"}
                finally:
                    release_all()
                    try: t.close()
                    except Exception: pass
            save("apps" + ("_" + "_".join(sys.argv[2:]) if sys.argv[2:] else ""), res)
        elif phase in ("full", "soak", "edge"):
            t = TARGETS[sys.argv[2] if len(sys.argv) > 2 else "notepad"](); t.open(); focus(t.h)
            try:
                names = sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(CLIPS, "*.wav")))
                if phase == "full":
                    rows = [run_one(hz, t, n) for n in names]
                    save("full", rows)
                elif phase == "soak":
                    rows, mem = [], [rss_mb()]
                    random.seed(7)
                    for i in range(50):
                        rows.append(run_one(hz, t, names[i % len(names)]))
                        rows[-1]["loop_alive"] = hz.loop.is_alive()
                        if i % 10 == 9:
                            mem.append(rss_mb())
                        time.sleep(random.uniform(0.5, 3.0))
                    save("soak", {"rows": rows, "mem_ws_pagefile_mb": mem})
                else:
                    out = {}
                    for label, audio, extra in (("tap_0.2s", np.zeros(0, np.float32), 0.2),
                                                ("silence_3s", np.zeros(48000, np.float32), 0.0)):
                        t.clear()
                        lat, inj, _ = hz.dictate(audio, hold_extra=extra)
                        time.sleep(1.0)
                        out[label] = {"lat": lat, "injected": inj, "readback": t.read()}
                        print(f"[e2e] {label}: {out[label]}")
                    parts = [load(n) for n in ("long1", "med1", "med2")]
                    audio = np.concatenate([p[0] for p in parts]); ref = " ".join(p[1] for p in parts)
                    out["long_concat"] = run_one(hz, t, "long1+med1+med2", audio, ref)
                    save("edge", out)
            finally:
                release_all()
                t.close()
    finally:
        release_all()
        hz.app.running = False


if __name__ == "__main__":
    main_()
