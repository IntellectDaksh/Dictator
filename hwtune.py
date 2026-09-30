"""Hardware auto-tune: detect this machine, pick the Whisper + Ollama models
that keep dictation fast on it, pull the Ollama model if missing, and link
both into Dictator's config.

Run by the installers (scripts/install.ps1, scripts/install.sh). Dictator
itself never re-tunes at startup, so choices made later in the tray menu stick.
Re-run by hand after a hardware change.

  python hwtune.py            show detected hardware + chosen profile
  python hwtune.py --apply    write the profile into Dictator's config.json
  python hwtune.py --pull     also start Ollama and pull the model if missing
"""
import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# Ollama cleanup-model tiers, smallest last. Sizes are the q4 download.
LLM_3B = "qwen2.5:3b-instruct"      # ~1.9 GB
LLM_1_5B = "qwen2.5:1.5b-instruct"  # ~1.0 GB
LLM_0_5B = "qwen2.5:0.5b-instruct"  # ~0.4 GB


def total_ram_gb():
    try:
        if IS_WIN:
            class MEMSTAT(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEMSTAT(); m.dwLength = ctypes.sizeof(MEMSTAT)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 2**30
        if IS_MAC:
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                 text=True, timeout=5).stdout
            return int(out.strip()) / 2**30
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except Exception:
        return 8.0  # unknown: assume a mid-range machine


def nvidia_vram_gb():
    """Largest NVIDIA GPU's VRAM in GB, 0 if none / driver missing."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return 0.0
    try:
        out = subprocess.run([exe, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5,
                             creationflags=0x08000000 if IS_WIN else 0).stdout
        return max(float(x) for x in out.split() if x.strip()) / 1024
    except Exception:
        return 0.0


def detect():
    return {
        "os": platform.system(),
        "cpu_threads": os.cpu_count() or 4,
        "ram_gb": round(total_ram_gb(), 1),
        "vram_gb": round(nvidia_vram_gb(), 1),
        "apple_silicon": IS_MAC and platform.machine() == "arm64",
    }


def pick(hw):
    """Profile that keeps release-to-text around the same speed on any machine:
    stronger hardware gets bigger models, weaker hardware gets smaller ones."""
    ram, vram = hw["ram_gb"], hw["vram_gb"]
    if vram >= 4:
        # float16 measured faster than int8 on RTX-class cards (audit/bench.py)
        whisper, compute, why = "small.en", "float16", f"NVIDIA GPU {vram:.0f} GB"
    elif vram >= 2:
        whisper, compute, why = "small.en", "int8_float16", f"small NVIDIA GPU {vram:.0f} GB"
    elif hw["cpu_threads"] >= 8 and ram >= 8:
        whisper, compute, why = "base.en", "int8", f"CPU, {hw['cpu_threads']} threads"
    else:
        whisper, compute, why = "base.en", "int8", "low-power CPU"

    if vram >= 6 or (hw["apple_silicon"] and ram >= 16):
        llm = LLM_3B
    elif vram >= 3 or ram >= 12:
        llm = LLM_1_5B
    else:
        llm = LLM_0_5B
    return {"model_size": whisper, "cuda_compute": compute, "ollama_model": llm, "reason": why}


# ---------------------------------------------------------------- ollama

def _ollama_exe():
    exe = shutil.which("ollama")
    if exe:
        return exe
    if IS_WIN:
        p = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
        return p if os.path.exists(p) else None
    for p in ("/usr/local/bin/ollama", "/opt/homebrew/bin/ollama",
              "/Applications/Ollama.app/Contents/Resources/ollama"):
        if os.path.exists(p):
            return p
    return None


def ollama_tags(url: str, timeout: float = 2.0) -> list:
    """Fetch the list of model names currently available in Ollama."""
    with urllib.request.urlopen(url + "/api/tags", timeout=timeout) as r:
        return [m["name"] for m in json.load(r).get("models", [])]


def ensure_ollama_running(url, wait_s=15):
    """True if Ollama answers; starts `ollama serve` hidden if installed but down."""
    try:
        ollama_tags(url)
        return True
    except Exception:
        pass
    exe = _ollama_exe()
    if not exe:
        return False
    kw = {"creationflags": 0x08000000} if IS_WIN else {"start_new_session": True}
    try:
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    except OSError:
        return False
    t0 = time.time()
    while time.time() - t0 < wait_s:
        try:
            ollama_tags(url)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def has_model(names: list, model: str) -> bool:
    """Check if the given model or its latest tag exists in the list of names."""
    return any(n == model or n == model + ":latest" for n in names)


def pull(url, model, log=print):
    """Stream /api/pull, logging progress every ~10%. True on success."""
    req = urllib.request.Request(url + "/api/pull", data=json.dumps({"model": model}).encode(),
                                 headers={"Content-Type": "application/json"})
    last = -10
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            for line in r:
                msg = json.loads(line)
                if msg.get("error"):
                    log(f"ollama pull {model} failed: {msg['error']}")
                    return False
                total, done = msg.get("total"), msg.get("completed")
                if total and done:
                    pct = int(done * 100 / total)
                    if pct >= last + 10:
                        last = pct
                        log(f"downloading {model}: {pct}%")
                if msg.get("status") == "success":
                    return True
    except Exception as e:
        log(f"ollama pull {model} failed ({type(e).__name__})")
    return False


def ensure_model(url, model, log=print):
    """Make sure `model` is pulled. Returns the model name, or None."""
    if not ensure_ollama_running(url):
        log("Ollama not installed/running — Smart cleanup off until it is "
            "(https://ollama.com/download)")
        return None
    try:
        if has_model(ollama_tags(url), model):
            return model
    except Exception:
        return None
    log(f"pulling cleanup model {model} (one-time download)")
    return model if pull(url, model, log) else None


def config_path() -> str:
    """Get the absolute path to Dictator's config.json based on OS."""
    if IS_WIN:
        base = os.path.join(os.environ.get("APPDATA", "."), "Dictator")
    elif IS_MAC:
        base = os.path.expanduser("~/Library/Application Support/Dictator")
    else:
        base = os.path.expanduser("~/.config/Dictator")
    return os.path.join(base, "config.json")


def apply_to_config(prof):
    """Merge the profile into config.json, keeping every other user setting."""
    path = config_path()
    cfg = {}
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        pass
    cfg.update({k: prof[k] for k in ("model_size", "cuda_compute", "ollama_model")})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    return path


if __name__ == "__main__":
    hw = detect()
    print(json.dumps({"hardware": hw, "profile": pick(hw)}, indent=2))
    prof = pick(hw)
    if "--apply" in sys.argv or "--pull" in sys.argv:
        print("config:", apply_to_config(prof))
    if "--pull" in sys.argv:
        sys.exit(0 if ensure_model("http://127.0.0.1:11434", prof["ollama_model"]) else 1)
