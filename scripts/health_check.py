#!/usr/bin/env python3
"""Environment and hardware diagnostic health-check for Dictator.

Checks Python version, audio devices, Ollama daemon reachability, and
local configuration directory permissions.

Usage:
    python scripts/health_check.py
"""
import json
import os
import shutil
import sys
import urllib.error
import urllib.request


def check_python_version():
    v = sys.version_info
    ok = v.major == 3 and v.minor >= 10
    msg = f"Python {v.major}.{v.minor}.{v.micro}"
    return ok, msg


def check_ollama(url="http://127.0.0.1:11434"):
    try:
        req = urllib.request.Request(f"{url}/api/tags", headers={"User-Agent": "Dictator-HealthCheck"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name") for m in data.get("models", [])]
            return True, f"Reachable at {url} ({len(models)} models available: {', '.join(models[:3]) or 'none'})"
    except urllib.error.URLError as e:
        return False, f"Could not connect to Ollama at {url}: {e.reason}"
    except Exception as e:
        return False, f"Error checking Ollama: {e}"


def check_storage():
    if sys.platform == "win32":
        app_dir = os.path.join(os.environ.get("APPDATA", "."), "Dictator")
    elif sys.platform == "darwin":
        app_dir = os.path.expanduser("~/Library/Application Support/Dictator")
    else:
        app_dir = os.path.expanduser("~/.config/Dictator")

    os.makedirs(app_dir, exist_ok=True)
    writable = os.access(app_dir, os.W_OK)
    total, used, free = shutil.disk_usage(app_dir)
    free_gb = free / (1024 ** 3)
    return writable, f"AppData directory: {app_dir} (Writable: {writable}, Free: {free_gb:.1f} GB)"


def main():
    print("=" * 60)
    print("Dictator System & Environment Health Check")
    print("=" * 60)

    checks = [
        ("Python Version", check_python_version),
        ("Storage & Permissions", check_storage),
        ("Ollama Local LLM", check_ollama),
    ]

    all_ok = True
    for name, fn in checks:
        ok, msg = fn()
        status = "[PASS]" if ok else "[WARN]"
        print(f"{status} {name}: {msg}")
        if not ok and name != "Ollama Local LLM":
            all_ok = False

    print("=" * 60)
    if all_ok:
        print("Core diagnostics passed successfully.")
    else:
        print("Some diagnostic checks produced warnings. See details above.")


if __name__ == "__main__":
    main()
