"""Generates Dictator's icon — black badge, a single smooth coral
waveform stroke (audio trace, tapered amplitude envelope) — as a multi-res
.ico for the dashboard window/taskbar, plus a 512px PNG master.
Run once with the venv's Python: ../../.venv/Scripts/python.exe gen_icon.py
"""
import math
from PIL import Image, ImageDraw

BLACK = (14, 13, 12, 255)      # near-black, matches dashboard --bg
ACCENT = (255, 255, 255, 255)  # pure white — logo is black & white only
MUTED_ACCENT = (130, 130, 130, 255)


def draw_badge(size, enabled=True):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = round(size * 0.04)
    radius = round(size * 0.26)
    bg = BLACK if enabled else (60, 60, 60, 255)
    d.rounded_rectangle((pad, pad, size - pad, size - pad), radius=radius, fill=bg)

    stroke = ACCENT if enabled else MUTED_ACCENT
    w = max(2, round(size * 0.075))
    cy = size / 2
    x0, x1 = size * 0.15, size * 0.85
    n = 48
    pts = []
    for i in range(n + 1):
        t = i / n
        x = x0 + t * (x1 - x0)
        envelope = math.sin(t * math.pi)          # 0 -> 1 -> 0, tapers ends
        y = cy + math.sin(t * math.pi * 3.4) * (size * 0.19) * envelope
        pts.append((x, y))
    d.line(pts, fill=stroke, width=w, joint="curve")
    r = w / 2
    d.ellipse((pts[0][0] - r, pts[0][1] - r, pts[0][0] + r, pts[0][1] + r), fill=stroke)
    d.ellipse((pts[-1][0] - r, pts[-1][1] - r, pts[-1][0] + r, pts[-1][1] + r), fill=stroke)

    if not enabled:
        lw = max(2, round(size * 0.06))
        m = round(size * 0.16)
        d.line((m, size - m, size - m, m), fill=(194, 65, 61, 255), width=lw)
    return img


if __name__ == "__main__":
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [draw_badge(s, enabled=True) for s in sizes]
    imgs[-1].save("dictator.ico", sizes=[(s, s) for s in sizes])
    draw_badge(512, enabled=True).save("dictator-512.png")
    draw_badge(64, enabled=False).save("dictator-disabled-64.png")
    print("wrote dictator.ico, dictator-512.png, dictator-disabled-64.png")
