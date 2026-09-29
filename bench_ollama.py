import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main as m

cfg = dict(m.DEFAULTS)
cfg["ollama_url"] = "http://127.0.0.1:11434"
cfg["ollama_model"] = "qwen2.5:1.5b-instruct"
cfg["ollama_timeout_s"] = 30.0
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
print(f"avg: {sum(times)/len(times):.2f}s  max: {max(times):.2f}s  min: {min(times):.2f}s")
print("ALL UNDER 2s" if max(times) <= 2.0 else "SOME OVER 2s")
