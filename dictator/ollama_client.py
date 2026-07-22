"""Local cleanup LLM via the Ollama HTTP API — plain urllib, no SDK dependency."""
import json
import urllib.request

from .tone import TONE_HINT

PREFERRED_MODELS = ("qwen3:14b", "qwen2.5:7b-instruct", "llama3.1:8b")

SYSTEM_PROMPT = (
    "You clean up raw speech transcripts into what the speaker intended to "
    "write. Remove filler words and verbal disfluencies (um, uh, like, you "
    "know, I mean, sort of, kind of). When the speaker corrects, restates, "
    "or contradicts something they just said, keep ONLY their final intended "
    "version and silently drop the discarded part — do not narrate the "
    "correction. Fix punctuation, capitalization, and obvious grammar. Do "
    "not add information, opinions, or content the speaker didn't say. Do "
    "not change their tone, formality, or word choice beyond what's needed "
    "for fluency. Keep the literal phrases 'new line', 'new paragraph', and "
    "'bullet point' unchanged wherever they appear. Output only the corrected "
    "text — no preamble, no quotes around it, no explanation, no meta-commentary."
)
ONE_SHOT_IN = "lets connect at 12 pm um no actually 11 pm"
ONE_SHOT_OUT = "Let's connect at 11pm."


def ollama_get(url, path, timeout=3.0):
    with urllib.request.urlopen(url + path, timeout=timeout) as r:
        return json.load(r)


def resolve_ollama_model(cfg):
    if cfg["ollama_model"] != "auto":
        return cfg["ollama_model"]
    try:
        names = [m["name"] for m in ollama_get(cfg["ollama_url"], "/api/tags")["models"]]
    except Exception:
        return None
    for want in PREFERRED_MODELS:
        for name in names:
            if name == want or name.startswith(want.split(":")[0]):
                return name
    return None


def ollama_cleanup(raw, cfg, model, tone=None):
    """Return cleaned text, or None on any failure (caller falls back to raw)."""
    if not model:
        return None
    system = SYSTEM_PROMPT
    if cfg["vocabulary"]:
        system += (" Spell these words exactly as written: "
                   + ", ".join(cfg["vocabulary"]) + ".")
    system += TONE_HINT.get(tone, "")
    payload = json.dumps({
        "model": model,
        "stream": False,
        # qwen3 is a thinking model: left on, it spends seconds reasoning before
        # answering and blows past ollama_timeout_s, so cleanup silently falls
        # back to raw. Disable it — for filler-stripping we want the direct
        # answer, not a reasoning pass. Ignored by non-thinking models.
        "think": False,
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": ONE_SHOT_IN},
            {"role": "assistant", "content": ONE_SHOT_OUT},
            {"role": "user", "content": raw},
        ],
    }).encode()
    req = urllib.request.Request(
        cfg["ollama_url"] + "/api/chat", data=payload,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=cfg["ollama_timeout_s"]) as r:
            text = json.load(r)["message"]["content"].strip()
        return text or None
    except Exception as e:
        print(f"cleanup skipped ({type(e).__name__}) — using raw transcript")
        return None
