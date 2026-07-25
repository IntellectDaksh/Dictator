@echo off
rem Double-click to start Dictator (no console window stays open).
rem Paths relative to this .bat (%~dp0) so the app survives being moved.
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
