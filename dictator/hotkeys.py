"""Global hotkey detection — polled via the `keyboard` library, not an OS-level hook."""
import time

import keyboard

HOTKEY_PRESETS = [
    ("Ctrl + Win", ["ctrl", "win"]),
    ("Ctrl + Alt", ["ctrl", "alt"]),
    ("Ctrl + Shift", ["ctrl", "shift"]),
    ("Alt + Win", ["alt", "win"]),
]


def win_pressed():
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
