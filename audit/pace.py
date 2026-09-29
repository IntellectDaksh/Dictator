"""Find the fastest typing pace that Notepad (worst-case app) receives intact."""
import sys, time
sys.argv = ["e2e", "x"]
import e2e, main
S = "I finished the first cut of the reel. So let's trim the intro, move the reveal to 3s & ping Ollama (whisper.cpp)!"
t = e2e.Notepad(); t.open(); e2e.focus(t.h); time.sleep(0.5)
for chunk, gap in [(24, 0.004)]:
    main.TYPE_CHUNK, main.TYPE_GAP = chunk, gap
    t.clear(); t0 = time.perf_counter(); main.send_text_keystrokes(S); ms = (time.perf_counter() - t0) * 1000
    time.sleep(0.5); got = e2e.norm_ws(t.read())
    print(f"chunk={chunk} gap={gap} ms={ms:.0f} ok={got == S} got={got[:70]!r}")
t.close()
