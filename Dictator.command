#!/usr/bin/env bash
# Double-click (Finder) or run to start Dictator with no console window
# staying attached. Paths relative to this file so the app survives moves.
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
nohup "$dir/.venv/bin/python3" "$dir/main.py" >/dev/null 2>&1 &
disown
