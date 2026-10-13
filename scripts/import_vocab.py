#!/usr/bin/env python3
"""Vocabulary import and deduplication utility for Dictator.

Imports custom names, project jargon, and brand words into config.json's
vocabulary list to bias Whisper STT and Ollama formatting models.

Usage:
    python scripts/import_vocab.py vocab.txt [--config path/to/config.json]
"""
import argparse
import json
import os
import sys

DEFAULT_CONFIG_LOCATIONS = [
    os.path.join(os.environ.get("APPDATA", ""), "Dictator", "config.json"),
    os.path.expanduser("~/.config/Dictator/config.json"),
]


def find_config():
    for p in DEFAULT_CONFIG_LOCATIONS:
        if p and os.path.isfile(p):
            return p
    return None


def read_vocabulary_file(filepath):
    words = []
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            for item in line.replace(",", "\n").splitlines():
                term = item.strip()
                if term and not term.startswith("#"):
                    words.append(term)
    return words


def import_vocabulary(words, config_path):
    if not os.path.isfile(config_path):
        print(f"[ERROR] Config file not found at {config_path}")
        return False

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    existing = cfg.get("vocabulary") or []
    seen = set(w.lower() for w in existing)
    added = []

    for w in words:
        if w.lower() not in seen:
            existing.append(w)
            seen.add(w.lower())
            added.append(w)

    cfg["vocabulary"] = existing

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

    print(f"[OK] Added {len(added)} new terms. Total vocabulary count: {len(existing)}")
    if added:
        print(f"Sample new terms: {', '.join(added[:5])}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Import terms into Dictator's vocabulary list.")
    parser.add_argument("file", help="Path to text or CSV file containing terms")
    parser.add_argument("--config", default=None, help="Path to Dictator config.json")
    args = parser.parse_args()

    if not os.path.isfile(args.file):
        print(f"[ERROR] Word list file not found: {args.file}")
        sys.exit(1)

    target_config = args.config or find_config()
    if not target_config:
        print("[ERROR] No config.json found to update. Run Dictator once to create defaults.")
        sys.exit(1)

    words = read_vocabulary_file(args.file)
    print(f"Loaded {len(words)} entries from {args.file}")
    ok = import_vocabulary(words, target_config)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
