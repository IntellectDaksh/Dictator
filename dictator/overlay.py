"""Status pill UI: a single persistent bar at the bottom-center of the screen."""
import threading
import time
import tkinter as tk


def round_rect(cv, x1, y1, x2, y2, r, **kw):
    """Rounded rectangle on a canvas (smoothed polygon)."""
    pts = (x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1)
    return cv.create_polygon(pts, smooth=True, **kw)


def lerp_color(c1, c2, t):
    """Blend two #rrggbb colors — used for smooth hover/press fades."""
    t = max(0.0, min(1.0, t))
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(a[i] + (b[i] - a[i]) * t):02x}" for i in range(3))


def animate_color(widget, item, from_color, to_color, ms=110, steps=6):
    """Step-fade a canvas item's fill from one color to another. Guards
    against firing after the widget/item is gone (dashboard rebuilt etc.)."""
    delay = max(1, ms // steps)

    def step(i):
        try:
            if not widget.winfo_exists():
                return
            widget.itemconfigure(item, fill=lerp_color(from_color, to_color, i / steps))
            if i < steps:
                widget.after(delay, step, i + 1)
        except tk.TclError:
            pass

    step(0)


STATE_STYLE = {
    "listening": ("●  listening", "#c2413d", "#fff7f5"),
    "thinking": ("…  polishing", "#b87918", "#fff8eb"),
    "review": ("□  review", "#3b6f9f", "#edf6ff"),
    "done": ("✓  done", "#237a57", "#effaf5"),
}


class Overlay:
    """Status UI: a single persistent bar at the bottom-center of the screen.
    Shows "on" when idle, listening/thinking/done during dictation. Tk objects
    only touched from the main thread; other threads call set_state()."""

    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self._pending = None
        self._pending_detail = None
        self._pending_level = 0.0
        self._lock = threading.Lock()
        self._state = "idle"
        self._detail = None
        self._done_until = 0.0
        self._rendered = None
        self._visible = False
        self._fade_job = None
        self._last_active = time.time()
        self.IDLE_FADE_AFTER = 10.0
        self.IDLE_FADE_ALPHA = 0.05

        trans = "#000001"  # transparent key color so the pill corners are round
        self.bar = tk.Toplevel(root)
        self.bar.overrideredirect(True)
        self.bar.attributes("-topmost", True)
        self.bar.attributes("-alpha", 0.0)
        self.bar.configure(bg=trans)
        self.bar.attributes("-transparentcolor", trans)
        self.bar_cv = tk.Canvas(self.bar, width=156, height=34, bg=trans,
                                highlightthickness=0)
        self.bar_cv.pack()
        self.bar_shadow = round_rect(self.bar_cv, 5, 5, 153, 33, 13,
                                     fill="#0b0d12")
        self.bar_rect = round_rect(self.bar_cv, 0, 0, 148, 28, 13, fill="#1f242c")
        self.bar_text = self.bar_cv.create_text(74, 14, text="●  ready",
                                                fill="#f8fafc",
                                                font=("Segoe UI Semibold", 9))
        self.bar_level = round_rect(self.bar_cv, 14, 24, 14, 26, 1, fill="#ffffff",
                                    outline="")
        self.bar.withdraw()
        self._poll()

    def set_state(self, state, detail=None):  # thread-safe
        with self._lock:
            self._pending = state
            self._pending_detail = detail

    def set_level(self, level):  # thread-safe — 0..1 input mic level while listening
        with self._lock:
            self._pending_level = max(0.0, min(1.0, level))

    def _place_bar(self):
        sw = self.bar.winfo_screenwidth()
        sh = self.bar.winfo_screenheight()
        self.bar.geometry(f"+{(sw - 156) // 2}+{sh - 96}")

    def _fade_to(self, target, rate=0.4):
        if self._fade_job:
            self.bar.after_cancel(self._fade_job)
            self._fade_job = None

        def step():
            try:
                cur = float(self.bar.attributes("-alpha"))
            except tk.TclError:
                return
            nxt = cur + (target - cur) * rate
            if abs(target - nxt) < 0.03:
                nxt = target
            self.bar.attributes("-alpha", nxt)
            if nxt == target:
                self._fade_job = None
                if target == 0:
                    self.bar.withdraw()
            else:
                self._fade_job = self.bar.after(15, step)

        step()

    def _poll(self):
        with self._lock:
            pending, self._pending = self._pending, None
            detail, self._pending_detail = self._pending_detail, None
            level = self._pending_level
        if pending == "hide":
            self._state = "idle"
        elif pending in STATE_STYLE:
            self._state = pending
            self._detail = detail
            if pending == "done":
                self._done_until = time.time() + 1.4 if detail else time.time() + 0.9
        if self._state == "done" and time.time() > self._done_until:
            self._state = "idle"
        if self._state != "idle":
            self._last_active = time.time()
        self._render(level)
        self._apply_idle_fade()
        self.root.after(50, self._poll)

    def _apply_idle_fade(self):
        if not self._visible or self._state != "idle" or self._fade_job:
            return
        idle_for = time.time() - self._last_active
        target = self.IDLE_FADE_ALPHA if idle_for > self.IDLE_FADE_AFTER else 1.0
        try:
            cur = float(self.bar.attributes("-alpha"))
        except tk.TclError:
            return
        if abs(cur - target) > 0.01:
            self._fade_to(target, rate=0.06)

    def _render(self, level=0.0):
        key = (self._state, self._detail, self.cfg["enabled"], self.cfg["show_status_bar"])
        if key == self._rendered:
            return
        self._rendered = key
        state, detail, enabled, show_bar = key
        if not (enabled and show_bar):
            if self._visible:
                self._visible = False
                self._fade_to(0)
            return
        if state == "idle":
            text, color, fg = "●  ready", "#1f242c", "#f8fafc"
        elif state == "done" and detail:
            text, color, fg = f"✓  {detail}", *STATE_STYLE["done"][1:]
        else:
            text, color, fg = STATE_STYLE[state]
        self.bar_cv.itemconfigure(self.bar_rect, fill=color)
        self.bar_cv.itemconfigure(self.bar_text, text=text, fill=fg)
        if state == "listening":
            self.bar_cv.coords(self.bar_level, 14, 24, 14 + level * 120, 26)
            self.bar_cv.itemconfigure(self.bar_level, fill=fg, state="normal")
        else:
            self.bar_cv.itemconfigure(self.bar_level, state="hidden")
        self._place_bar()
        if not self._visible:
            self._visible = True
            self.bar.attributes("-alpha", 0.0)
            self.bar.deiconify()
        self._fade_to(1.0)
        self.bar.attributes("-topmost", True)
