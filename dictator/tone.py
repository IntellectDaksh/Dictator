"""App-aware tone: which app has focus decides casual/formal/verbatim cleanup."""
import ctypes
import os
from ctypes import wintypes

user32 = ctypes.windll.user32

CASUAL_EXES = {"slack.exe", "discord.exe", "telegram.exe", "whatsapp.exe"}
FORMAL_EXES = {"outlook.exe", "olk.exe", "thunderbird.exe", "winword.exe"}
VERBATIM_EXES = {"code.exe", "devenv.exe", "windowsterminal.exe", "wt.exe",
                 "cmd.exe", "powershell.exe", "pycharm64.exe", "idea64.exe"}
TONE_HINT = {
    "casual": " Keep the tone relaxed and conversational.",
    "formal": " Polish into a clear, professional register.",
}


def foreground_app():
    """(exe_name, window_title) of the focused window, lowercased."""
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
