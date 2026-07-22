"""Spoken formatting commands and punctuation fallback (no LLM involved)."""
import re

# spoken formatting commands become real formatting (applied after cleanup)
SPOKEN_CMDS = [
    (re.compile(r"[,;:]?\s*\bnew paragraph\b[,.;:]?\s*", re.I), "\n\n"),
    (re.compile(r"[,;:]?\s*\bnew line\b[,.;:]?\s*", re.I), "\n"),
    (re.compile(r"[,;:]?\s*\bbullet point\b[,.;:]?\s*", re.I), "\n- "),
]


def apply_commands(text):
    for rx, rep in SPOKEN_CMDS:
        text = rx.sub(rep, text)
    return text.strip()


def basic_punctuate(text):
    """Capitalize + terminal punctuation only — no LLM involved."""
    t = text.strip()
    if not t:
        return t
    t = t[0].upper() + t[1:]
    if t[-1] not in ".?!":
        t += "."
    return t


def quick_clean(raw, cfg=None):
    """Instant mode: short phrases skip the LLM round-trip."""
    if cfg is not None and not cfg.get("auto_punctuate", True):
        return raw.strip()
    return basic_punctuate(raw)


def expand_snippet(text, cfg):
    """Exact-match trigger phrase -> canned expansion, else unchanged."""
    snippets = cfg.get("snippets") or {}
    return snippets.get(text.strip().lower(), text)
