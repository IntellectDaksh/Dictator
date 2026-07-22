"""Text injection into whatever window has focus."""
from .hotkeys import wait_keys_released
from .sendinput import send_text_keystrokes


def inject_text(text, cfg):
    """Always simulated keystrokes — the clipboard is never touched."""
    wait_keys_released(cfg)
    send_text_keystrokes(text)
