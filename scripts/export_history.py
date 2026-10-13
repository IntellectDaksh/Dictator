#!/usr/bin/env python3
"""Export Dictator dictation history (history.jsonl) to CSV or structured JSON.

Usage:
    python scripts/export_history.py [--input path/to/history.jsonl] [--output export.csv] [--format csv|json]
"""
import argparse
import csv
import json
import os
import sys

DEFAULT_LOCATIONS = [
    os.path.join(os.environ.get("APPDATA", ""), "Dictator", "history.jsonl"),
    os.path.expanduser("~/Library/Application Support/Dictator/history.jsonl"),
    os.path.expanduser("~/.config/Dictator/history.jsonl"),
]


def find_history_file(explicit_path=None):
    if explicit_path and os.path.isfile(explicit_path):
        return explicit_path
    for p in DEFAULT_LOCATIONS:
        if p and os.path.isfile(p):
            return p
    return None


def read_entries(history_file):
    entries = []
    with open(history_file, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def export_csv(entries, output_path):
    fields = ["timestamp", "duration_s", "app", "tone", "raw_transcript", "cleaned_text"]
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for e in entries:
            writer.writerow(e)


def export_json(entries, output_path):
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description="Export Dictator history to CSV or JSON.")
    parser.add_argument("--input", default=None, help="Path to history.jsonl")
    parser.add_argument("--output", default="dictator_export.csv", help="Output file path")
    parser.add_argument("--format", choices=["csv", "json"], default="csv", help="Export format")
    args = parser.parse_args()

    hfile = find_history_file(args.input)
    if not hfile:
        print("[INFO] No history.jsonl file found. No records to export.")
        sys.exit(0)

    entries = read_entries(hfile)
    print(f"Loaded {len(entries)} records from {hfile}")

    if args.format == "csv":
        export_csv(entries, args.output)
    else:
        export_json(entries, args.output)

    print(f"[OK] Successfully exported to {args.output}")


if __name__ == "__main__":
    main()
