#!/usr/bin/env python3
"""Config validation script for Dictator.

Validates config.json syntax, field types, and supported option values.
Can be run prior to starting the service or during installation.

Usage:
    python scripts/validate_config.py [path_to_config.json]
"""
import json
import os
import sys

VALID_CLEANUP_MODES = {"fast", "smart", "verbatim"}
VALID_MODEL_SIZES = {"tiny.en", "base.en", "small.en", "medium.en", "large-v3"}

DEFAULT_CONFIG_LOCATIONS = [
    os.path.join(os.environ.get("APPDATA", ""), "Dictator", "config.json"),
    os.path.expanduser("~/.config/Dictator/config.json"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"),
]


def find_config(explicit_path=None):
    if explicit_path:
        return explicit_path
    for p in DEFAULT_CONFIG_LOCATIONS:
        if p and os.path.isfile(p):
            return p
    return None


def validate_config(config_path):
    if not os.path.exists(config_path):
        return False, [f"Config file not found: {config_path}"]

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        return False, [f"Invalid JSON in config file: {e}"]

    errors = []
    warnings = []

    # Validate cleanup_mode
    mode = cfg.get("cleanup_mode")
    if mode is not None and mode not in VALID_CLEANUP_MODES:
        errors.append(f"Invalid cleanup_mode: '{mode}'. Must be one of: {sorted(VALID_CLEANUP_MODES)}")

    # Validate model_size
    model = cfg.get("model_size")
    if model is not None and model not in VALID_MODEL_SIZES:
        warnings.append(f"Uncommon model_size: '{model}'. Recommended: {sorted(VALID_MODEL_SIZES)}")

    # Validate beam_size
    beam = cfg.get("beam_size")
    if beam is not None and (not isinstance(beam, int) or beam < 1):
        errors.append(f"beam_size must be a positive integer, got: {beam}")

    # Validate timeout
    timeout = cfg.get("ollama_timeout_s")
    if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
        errors.append(f"ollama_timeout_s must be a positive number, got: {timeout}")

    # Validate vocabulary
    vocab = cfg.get("vocabulary")
    if vocab is not None and not isinstance(vocab, list):
        errors.append("vocabulary must be a list of strings")

    return len(errors) == 0, errors + warnings


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else find_config()
    if not target:
        print("[INFO] No existing config.json found to validate. Run Dictator once to generate defaults.")
        sys.exit(0)

    print(f"Validating config: {target}")
    ok, messages = validate_config(target)

    for msg in messages:
        prefix = "[WARN]" if "Uncommon" in msg else "[ERROR]"
        print(f"{prefix} {msg}")

    if ok:
        print("[OK] Configuration is valid.")
        sys.exit(0)
    else:
        print("[FAIL] Configuration validation failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
