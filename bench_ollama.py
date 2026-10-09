"""Benchmark Ollama latency and model cleanup execution.

Usage:
    python bench_ollama.py [--model qwen2.5:1.5b-instruct] [--url http://127.0.0.1:11434] [--timeout 30.0]
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
def main_():
    parser = argparse.ArgumentParser(description="Benchmark local Ollama model response latency for Dictator.")
    parser.add_argument("--url", default="http://127.0.0.1:11434", help="Ollama API base URL")
    parser.add_argument("--model", default="qwen2.5:1.5b-instruct", help="Ollama model tag to test")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds")
    parser.add_argument("--threshold", type=float, default=2.0, help="Max acceptable latency threshold in seconds")
    args = parser.parse_args()

    import main as m

    cfg = dict(m.DEFAULTS)
    cfg["ollama_url"] = args.url
    cfg["ollama_model"] = args.model
    cfg["ollama_timeout_s"] = args.timeout
    cfg["vocabulary"] = []

    model = m.resolve_ollama_model(cfg)
    print(f"resolved model: {model}")
    if not model:
        print("NO MODEL RESOLVED — aborting")
        sys.exit(1)

    samples = [
        "um so like i wanted to say that uh the meeting is at three pm tomorrow i think",
        "okay so basically what i need you to do is uh go to the store and buy milk and eggs and stuff like that",
        "hey can you uh send that report to john before like end of day today thanks",
        "so i was thinking maybe we should uh push the deadline back a bit because the client hasnt responded yet",
        "quick note remind me to call the dentist tomorrow morning around nine am",
    ]

    # warm-up call so model load time doesn't pollute the benchmark
    t0 = time.time()
    m.ollama_cleanup(samples[0], cfg, model)
    print(f"warm-up call: {time.time()-t0:.2f}s (first call includes model load into VRAM)")

    times = []
    for i, s in enumerate(samples, 1):
        t0 = time.time()
        out = m.ollama_cleanup(s, cfg, model)
        dt = time.time() - t0
        times.append(dt)
        print(f"[{i}] {dt:.2f}s  raw: {s!r}")
        print(f"    cleaned: {out!r}")

    print()
    avg_t = sum(times) / len(times)
    max_t = max(times)
    min_t = min(times)
    print(f"avg: {avg_t:.2f}s  max: {max_t:.2f}s  min: {min_t:.2f}s")
    print(f"ALL UNDER {args.threshold}s" if max_t <= args.threshold else f"SOME OVER {args.threshold}s")


if __name__ == "__main__":
    main_()
