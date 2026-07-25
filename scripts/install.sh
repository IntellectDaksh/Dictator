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

# 4. Ollama + cleanup model ------------------------------------------------
if ! command -v ollama >/dev/null 2>&1; then
    echo ""
    echo "Ollama not found."
    echo "Install it from https://ollama.com/download for transcript cleanup (filler-word removal, grammar fixes)."
    echo "Dictator still works without it - it just types your raw transcript instead."
else
    if ! curl -fsS --max-time 5 http://localhost:11434/api/tags >/tmp/dictator_tags.json 2>/dev/null; then
        echo "Starting Ollama..."
        (ollama serve >/dev/null 2>&1 &)
        sleep 3
        curl -fsS --max-time 5 http://localhost:11434/api/tags >/tmp/dictator_tags.json 2>/dev/null || echo '{"models":[]}' >/tmp/dictator_tags.json
    fi
    found=0
    for p in qwen3 qwen2.5 llama3.1; do
        if grep -q "\"$p" /tmp/dictator_tags.json 2>/dev/null; then found=1; fi
    done
    rm -f /tmp/dictator_tags.json
    if [ "$found" -eq 0 ]; then
        echo ""
        echo "No local cleanup model detected."
        echo "Suggested: qwen2.5:7b-instruct (~4.7 GB one-time download, good quality/speed balance)."
        read -r -p "Download it now? [Y/n] " ans
        if [ -z "$ans" ] || [[ "$ans" =~ ^[Yy] ]]; then
            ollama pull qwen2.5:7b-instruct
        else
            echo "Skipped - Dictator still works without it, it just types the raw transcript."
        fi
    else
        echo "Cleanup model already installed."
    fi
fi

# 5. Launch -----------------------------------------------------------------
chmod +x "$root/Dictator.command"
echo ""
echo "Setup done. Launching Dictator..."
open "$root/Dictator.command"
