"""Shared STT benchmark: runs Dictator's real Transcriber + cleanup over audit/clips.

Usage: python audit/bench.py <label> [--model small.en] [--lang en] [--beam 1]
       [--compute float16] [--clips short1,med1] [--runs 3]
Writes audit/results/<label>.json: per-clip WER + latency, aggregate WER, p50/p95.
Latency = end-of-speech (audio complete) -> cleaned text ready, i.e. what
Dictator does after hotkey release, minus keystroke injection (timed separately).
"""
import argparse, glob, json, os, re, sys, time, wave
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import main  # noqa: E402  (real pipeline code, not a copy)

CLIPS = os.path.join(ROOT, "audit", "clips")
NUM = {"4k": "four k", "10": "ten", "3": "three", "v3": "v three"}


def norm(s):
    s = s.lower().replace(".cpp", " dot cpp").replace("-", " ")
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    return [NUM.get(w, w) for w in " ".join(NUM.get(w, w) for w in s.split()).split()]


def wer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)], len(r)


def load_wav(p):
    with wave.open(p) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


def main_():
    """Main entry point"""
    ap = argparse.ArgumentParser()
    ap.add_argument("label")
    ap.add_argument("--model", default="small.en")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--beam", type=int, default=1)
    ap.add_argument("--compute", default=None)
    ap.add_argument("--clips", default="")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--stream", action="store_true", help="feed audio in real time via StreamingSTT")
    ap.add_argument("--vocab", default="", help="comma-separated vocabulary")
    a = ap.parse_args()

    if a.compute:  # ponytail: monkeypatch compute_type rather than add a config knob nobody else needs
        orig = main.WhisperModel
        main.WhisperModel = lambda *x, **k: orig(*x, **{**k, "compute_type": a.compute} if k.get("device") == "cuda" else k)
    t = main.Transcriber(a.model)
    t0 = time.perf_counter()
    t.load()
    load_s = time.perf_counter() - t0

    names = sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(CLIPS, "*.wav")))
    if a.clips:
        names = [n for n in names if n in a.clips.split(",")]
    cfg = dict(main.DEFAULTS)
    vocab = [v.strip() for v in a.vocab.split(",") if v.strip()]
    rows, lats, errs, words = [], [], 0, 0
    for n in names:
        audio = load_wav(os.path.join(CLIPS, n + ".wav"))
        ref = open(os.path.join(CLIPS, n + ".txt"), encoding="utf-8-sig").read().strip()
        clip_lats = []
        for _ in range(a.runs):
            if a.stream:  # play the clip in real time, time only what happens after "release"
                t_play = time.perf_counter()
                st = main.StreamingSTT(t, lambda: audio[:int((time.perf_counter() - t_play) * 16000)],
                                       vocab, a.lang, a.beam)
                time.sleep(len(audio) / 16000)
                s = time.perf_counter()
                raw = st.finish(audio)
            else:
                s = time.perf_counter()
                raw = t.transcribe(audio, vocab, a.lang, a.beam)
            out = main.quick_clean(main.apply_commands(raw), cfg) if raw else ""
            clip_lats.append((time.perf_counter() - s) * 1000)
        e, nw = wer(ref, out)
        errs, words = errs + e, words + nw
        lats += clip_lats
        rows.append(dict(clip=n, secs=round(len(audio) / 16000, 1), wer=round(e / max(nw, 1), 3),
                         ms_p50=round(float(np.median(clip_lats))), out=out))
        print(f"{n:10s} wer={e / max(nw, 1):.2f} {np.median(clip_lats):6.0f}ms  {out}")
    res = dict(label=a.label, stream=a.stream, vocab=vocab, model=a.model, lang=a.lang, beam=a.beam, compute=a.compute or "float16",
               device=t.device, load_s=round(load_s, 2), wer=round(errs / max(words, 1), 4),
               p50_ms=round(float(np.percentile(lats, 50))), p95_ms=round(float(np.percentile(lats, 95))),
               clips=rows)
    os.makedirs(os.path.join(ROOT, "audit", "results"), exist_ok=True)
    with open(os.path.join(ROOT, "audit", "results", a.label + ".json"), "w") as f:
        json.dump(res, f, indent=1)
    print(f"== {a.label}: WER {res['wer']:.3f}  p50 {res['p50_ms']}ms  p95 {res['p95_ms']}ms  load {load_s:.1f}s on {t.device}")


if __name__ == "__main__":
    assert wer("Send the invoice", "send the invoice.") == (0, 3)  # self-check
    assert wer("a b c", "a x c") == (1, 3)
    main_()
