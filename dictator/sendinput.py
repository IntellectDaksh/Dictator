"""Raw Win32 SendInput text injection — never touches the clipboard."""
import ctypes
from ctypes import wintypes

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
