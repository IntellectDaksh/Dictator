"""Start-on-login toggle via the HKCU Run registry key."""
import os
import sys
import winreg

from .paths import ENTRY_SCRIPT

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_start_on_login(on):
    # venvs put pythonw.exe under Scripts\; base installs put it at the prefix root
    scripts_pythonw = os.path.join(sys.prefix, "Scripts", "pythonw.exe")
    pythonw = scripts_pythonw if os.path.exists(scripts_pythonw) \
        else os.path.join(sys.prefix, "pythonw.exe")
    cmd = f'"{pythonw}" "{ENTRY_SCRIPT}"'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "Dictator", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, "Dictator")
            except FileNotFoundError:
                pass
