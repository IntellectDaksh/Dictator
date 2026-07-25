#!/usr/bin/env bash
# Piped straight from GitHub:
#   curl -fsSL https://raw.githubusercontent.com/IntellectDaksh/Dictator/main/scripts/bootstrap.sh | bash
# Clones the repo (if not already sitting in it) then hands off to install.sh.
set -euo pipefail

if [ -f "./main.py" ] && [ -f "./scripts/install.sh" ]; then
    root="$(pwd)"
else
    if ! command -v git >/dev/null 2>&1; then
        if ! command -v brew >/dev/null 2>&1; then
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
        fi
        brew install git
    fi
    dest="$HOME/Dictator"
    if [ ! -d "$dest" ]; then
        echo "Cloning Dictator to $dest ..."
        git clone https://github.com/IntellectDaksh/Dictator.git "$dest"
    fi
    root="$dest"
fi

bash "$root/scripts/install.sh"
