#!/usr/bin/env bash
# One-command setup: creates the venv, installs deps, makes sure Ollama has a
# cleanup model pulled (suggests + downloads one if none found), then launches
# Dictator. Safe to re-run any time - every step is skip-if-already-done.
#
# macOS support is best-effort: hotkey capture, text injection, and autostart
# all go through different OS APIs than Windows (see info.md) and haven't
# been run on real Mac hardware yet. File an issue if something's broken.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

echo "== Dictator setup (macOS) =="

# 1. Homebrew ------------------------------------------------------------
if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew not found. Installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
fi

# 2. System deps: python3, portaudio (sounddevice), tcl-tk (Tk fallback dashboard) --
for pkg in python-tk portaudio; do
    if ! brew list "$pkg" >/dev/null 2>&1; then
        echo "Installing $pkg via Homebrew..."
        brew install "$pkg"
    fi
done
if ! command -v python3 >/dev/null 2>&1; then
    brew install python
fi

# 3. Virtual env + deps ----------------------------------------------------
if [ -d ".venv" ] && [ ! -x ".venv/bin/pip" ]; then
    echo "Existing .venv is incomplete - recreating it."
    rm -rf .venv
fi
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi
echo "Installing dependencies..."
.venv/bin/pip install -q -r requirements.txt

echo ""
echo "IMPORTANT: macOS will prompt for Accessibility + Input Monitoring"
echo "permission the first time you dictate (needed to detect the hotkey and"
echo "type text). Grant it to your terminal app (or python3) in"
echo "System Settings > Privacy & Security, then try again."

# 4. Hardware auto-tune + Ollama cleanup model -----------------------------
# hwtune.py reads RAM / GPU / CPU, picks the Whisper + Ollama models that keep
# dictation fast on this machine, writes them into config.json, then starts
# Ollama and pulls the model if it's missing. No prompts.
if ! command -v ollama >/dev/null 2>&1 && [ ! -d /Applications/Ollama.app ]; then
    echo "Installing Ollama (local AI runtime for the cleanup pass)..."
    brew install ollama || echo "Ollama install failed - get it from https://ollama.com/download"
fi
echo ""
echo "Tuning Dictator for this machine..."
if ! .venv/bin/python hwtune.py --pull; then
    echo "Cleanup model not ready - Dictator still works, Smart mode types the plain transcript until it is."
fi

# 5. Launch -----------------------------------------------------------------
chmod +x "$root/Dictator.command"
echo ""
echo "Setup done. Launching Dictator..."
open "$root/Dictator.command"
