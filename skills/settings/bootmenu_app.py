#!/usr/bin/env python3
"""Boot menu for the coin-dojo cabinet (#210, part D of #204): choose ARCADE (ES-DE) or FIGHTCADE, in the SETTINGS look.

The session loop (#209) runs it under cage and reads ONE word from stdout:

    bootmenu_app.py [--countdown N]          full screen (SDL); prints `esde` or `fightcade`, exit 0
    bootmenu_app.py --png OUT.png [--actions a,b] [--countdown N] [--at S] [--moved S]   one frame to a PNG (tests, previews)
    options: --font-dir DIR  --art-dir DIR  --hostname NAME  --log FILE

    --countdown N  N seconds, then ARCADE is chosen; ANY input stops it (the menu then waits). 0 (default) = wait.
    --actions      inputs replayed first (up down left right ok back start esc enter bksp tab text:x)
    --at S         PNG mode only: draw the frame S seconds into the countdown (previews; also the glow's pulse phase)
    --moved S      PNG mode only: the highlight moved S seconds before --at (shows the illustration mid-slide)
    --art-dir DIR  card illustrations esde.png / fightcade.png (#218; default: the theme's _inc/art/bootmenu, which the
                   theme ships since #263). A missing or unreadable file falls back to the drawn illustration.

Panel: STICK move, LK (or START) start the highlighted mode. Keyboard: arrows, Enter. SELECT+START does nothing here.
Exit codes: 0 chose a mode (printed), 2 usage, 3 no display / library missing, 143 SIGTERM or SIGINT: prints NOTHING
(the session treats "no answer" as esde). stdout carries only the answer; diagnostics go to stderr and the log.
Look: brand/tokens.json only; the window is the 4:3 safe area (1440x1080), as in SETTINGS.

Motion (#218) is decoration only: the gold border moves on the very next frame after an input (that frame is a copy
of a cached card layer, cheaper than the old full redraw); the illustration then slides/fades in over ANIM_S and the
lit card's glow pulses at PULSE_FPS. Each card layer is drawn once at start; a frame is one paint + the glow + the
status strip.
"""
import ctypes
import math
import os
import signal
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import settings_app as sa  # noqa: E402  (also puts brand/ on the path)
import settings_core as core  # noqa: E402

W, H = sa.W, sa.H
M = 72
MODES = [
    dict(key="esde", name="ARCADE", sub="The game library", tag="DEFAULT",
         lines=["Every system on the cabinet", "Trophies and attract mode"],
         note="QUIT on the carousel brings you back here."),
    dict(key="fightcade", name="FIGHTCADE", sub="Online versus", tag="NEEDS INTERNET",
         lines=["Rollback matches online", "FBNeo fighters"],
         note="Exit Fightcade with START + SELECT (hold 2 s) to come back here."),
]
LOG_PATH = os.path.expanduser("~/.cache/coin-dojo/bootmenu.log")
ART_DIR = os.path.expanduser("~/ES-DE/themes/coin-dojo/_inc/art/bootmenu")   # the theme ships the cards since #263 (#218)
GRACE = 0.5            # seconds after the window opens in which a (held-over) button press is ignored

# card geometry (1440x1080 frame)
GAP, TOP, CH = 36, 196, 640
CW = (W - 2 * M - GAP) / 2.0
INSET = 6                                     # illustration inset inside the card border
ILL_W, ILL_H = int(CW) - 2 * INSET, 318       # the illustration; gen_bootmenu_art.py writes exactly this size
ANIM_S = 0.16          # illustration slide + fade after the highlight moved (decoration; the border is already there)
SLIDE = 28             # px the new illustration slides in from
PULSE_FPS = 12         # frame rate while only the glow pulses (light on the Pi 5; input still wakes the loop at once)
PULSE_S = 1.8          # glow period
PULSE_FOR = 6.0        # s of pulsing after the menu opens / the last input; then the glow holds still and the loop only
                       # waits for events (no idle redraws; the countdown already redraws on its own while it runs)
GLOW = 21              # px of the outer glow around the lit card
DIM = 0.62             # dark veil over the card that is not lit
STATUS_Y = 856         # status strip: countdown / hint line, bar, note (redrawn every frame)


def card_x(i):
    return M + i * (CW + GAP)


class Menu:
    """The state: which card is lit, the countdown, and what an input does. No drawing, no SDL."""

    def __init__(self, countdown=0, now=0.0):
        self.sel = 0
        self.total = max(0, int(countdown))
        self.t0 = now
        self.active_at = now               # menu opened / last input: the pulse runs PULSE_FOR after it
        self.counting = self.total > 0
        self.choice = None
        self.moved_at = None               # when the highlight last moved (None: settled; drives the slide only)

    def remaining(self, now):
        return max(0.0, self.total - (now - self.t0)) if self.counting else 0.0

    def tick(self, now):
        """Countdown over -> ARCADE (the default), whatever was lit."""
        if self.counting and self.remaining(now) <= 0:
            self.choice = MODES[0]["key"]
        return self.choice

    def handle(self, action, now=None):
        if self.choice:
            return self.choice
        self.counting = False                                  # any input, even an ignored one, stops the countdown
        old = self.sel
        if action in ("left", "up"):
            self.sel = max(0, self.sel - 1)
        elif action in ("right", "down"):
            self.sel = min(len(MODES) - 1, self.sel + 1)
        elif action in ("ok", "enter", "start"):
            self.choice = MODES[self.sel]["key"]
        if now is not None:
            self.active_at = now
        if self.sel != old and now is not None:
            self.moved_at = now
        return self.choice

    def anim(self, now):
        """0..1 progress of the illustration slide after the last move (1 = settled)."""
        if self.moved_at is None:
            return 1.0
        return max(0.0, min(1.0, (now - self.moved_at) / ANIM_S))

    def view(self, now, hostname):
        rem = self.remaining(now)
        return dict(sel=self.sel, counting=self.counting, remaining=rem, total=self.total, hostname=hostname,
                    anim=self.anim(now), t=now, pulsing=now - self.active_at < PULSE_FOR)


class BootPainter(sa.Painter):
    """Layers (#218): illustrations and one full card layer per selection are drawn once; draw() paints the
    layer for the lit card and adds only what moves (glow, the sliding illustration, the status strip)."""

    def __init__(self, b, art_dir=None):
        super().__init__(b)
        vp, d, i = ctypes.c_void_p, ctypes.c_double, ctypes.c_int
        for name, (res, args) in {
            "cairo_save": (None, [vp]), "cairo_restore": (None, [vp]), "cairo_clip": (None, [vp]),
            "cairo_translate": (None, [vp, d, d]), "cairo_scale": (None, [vp, d, d]),
            "cairo_set_source_surface": (None, [vp, vp, d, d]), "cairo_paint_with_alpha": (None, [vp, d]),
            "cairo_image_surface_create_from_png": (vp, [ctypes.c_char_p]), "cairo_surface_status": (i, [vp]),
            "cairo_image_surface_get_width": (i, [vp]), "cairo_image_surface_get_height": (i, [vp]),
            "cairo_surface_destroy": (None, [vp]), "cairo_pattern_create_radial": (vp, [d, d, d, d, d, d]),
            "cairo_text_path": (None, [vp, ctypes.c_char_p]), "cairo_stroke_preserve": (None, [vp]), "cairo_set_dash": (None, [vp, ctypes.POINTER(d), i, d]),
        }.items():
            fn = getattr(self.c, name)
            fn.restype, fn.argtypes = res, args
        self.art_dir = ART_DIR if art_dir is None else art_dir
        self.art_used = {}                 # mode key -> "png" | "drawn" (logged; tests read it)
        self.ill = {m["key"]: self._illustration(m["key"]) for m in MODES}
        self.glow = self._glow_ring()
        self.layers = {}

    # -- small cairo helpers -------------------------------------------------------------------
    def _on(self, surf):
        """Point the drawing helpers at another surface; returns the previous context (give it to _off)."""
        prev = self.cr
        self.cr = self.c.cairo_create(surf)
        return prev

    def _off(self, prev):
        self.c.cairo_destroy(self.cr)
        self.cr = prev

    def _surface(self, w, h):
        return self.c.cairo_image_surface_create(0, int(w), int(h))

    def _stops(self, pat, stops, alpha=1.0):
        for pos, rgb in stops:
            self.c.cairo_pattern_add_color_stop_rgba(pat, pos, rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, alpha)

    def _vgrad(self, x, y, w, h, top, bottom, a_top=1.0, a_bottom=1.0):
        pat = self.c.cairo_pattern_create_linear(0, y, 0, y + h)
        self._stops(pat, [(0, self.b.rgb(top))], a_top)
        self._stops(pat, [(1, self.b.rgb(bottom))], a_bottom)
        self.c.cairo_set_source(self.cr, pat)
        self.c.cairo_rectangle(self.cr, x, y, w, h)
        self.c.cairo_fill(self.cr)
        self.c.cairo_pattern_destroy(pat)

    def _glow(self, cx, cy, r, colour, a):
        pat = self.c.cairo_pattern_create_radial(cx, cy, 0, cx, cy, r)
        rgb = self.b.rgb(colour)
        self._stops(pat, [(0, rgb)], a)
        self._stops(pat, [(1, rgb)], 0.0)
        self.c.cairo_set_source(self.cr, pat)
        self.c.cairo_arc(self.cr, cx, cy, r, 0, 2 * math.pi)
        self.c.cairo_fill(self.cr)
        self.c.cairo_pattern_destroy(pat)

    def _circle(self, cx, cy, r, fill=None, stroke=None, width=2, a=1.0):
        self.c.cairo_new_sub_path(self.cr)
        self.c.cairo_arc(self.cr, cx, cy, r, 0, 2 * math.pi)
        if fill:
            self.col(fill, a)
            (self.c.cairo_fill_preserve if stroke else self.c.cairo_fill)(self.cr)
        if stroke:
            self.col(stroke, a)
            self.c.cairo_set_line_width(self.cr, width)
            self.c.cairo_stroke(self.cr)
        self.c.cairo_new_path(self.cr)

    def _grad_text(self, x, y, text, role, px, outline="navy", width=6):
        """Display text in the badge gradient (cream -> gold -> teal) with a navy outline: the carousel badge look."""
        c, cr = self.c, self.cr
        self.font(role, px)
        c.cairo_move_to(cr, x, y)
        c.cairo_text_path(cr, text.encode("utf-8"))
        self.col(outline)
        c.cairo_set_line_width(cr, width)
        c.cairo_stroke_preserve(cr)
        pat = c.cairo_pattern_create_linear(0, y - px * 0.74, 0, y)
        self._stops(pat, self.b.stops("badge"))
        c.cairo_set_source(cr, pat)
        c.cairo_fill(cr)
        c.cairo_pattern_destroy(pat)

    def _label(self, cx, y, text, colour, px=30):
        """A P1 / P2 label with a navy outline, so it reads over any art (critic #218: gold on orange hair)."""
        c, cr = self.c, self.cr
        x = cx - self.width(text, "display", px) / 2.0
        c.cairo_move_to(cr, x, y)
        c.cairo_text_path(cr, text.encode("utf-8"))
        self.col("badge_outline")
        c.cairo_set_line_width(cr, 6)
        c.cairo_stroke_preserve(cr)
        self.col(colour)
        c.cairo_fill(cr)

    def _rr_path(self, x, y, w, h, r):
        """Path only (no fill): a rounded box, for a gradient fill or a clip."""
        c, cr, pi = self.c, self.cr, math.pi
        c.cairo_new_sub_path(cr)
        c.cairo_arc(cr, x + w - r, y + r, r, -pi / 2, 0)
        c.cairo_arc(cr, x + w - r, y + h - r, r, 0, pi / 2)
        c.cairo_arc(cr, x + r, y + h - r, r, pi / 2, pi)
        c.cairo_arc(cr, x + r, y + r, r, pi, 3 * pi / 2)
        c.cairo_close_path(cr)

    def _round_top(self, x, y, w, h, r):
        """Clip path: a box with rounded top corners (the illustration inside the card)."""
        c, cr, pi = self.c, self.cr, math.pi
        c.cairo_new_sub_path(cr)
        c.cairo_arc(cr, x + w - r, y + r, r, -pi / 2, 0)
        c.cairo_line_to(cr, x + w, y + h)
        c.cairo_line_to(cr, x, y + h)
        c.cairo_arc(cr, x + r, y + r, r, pi, 3 * pi / 2)
        c.cairo_close_path(cr)

    # -- illustrations (drawn once) ------------------------------------------------------------
    def _load_png(self, key):
        """The pre-scaled art for a card, or None (missing, unreadable, not a PNG, empty): the caller draws instead."""
        if not self.art_dir:
            return None
        path = os.path.join(self.art_dir, key + ".png")
        if not os.path.isfile(path):
            return None
        s = self.c.cairo_image_surface_create_from_png(path.encode())
        if not s:
            return None
        if self.c.cairo_surface_status(s) != 0 or self.c.cairo_image_surface_get_width(s) < 2 \
                or self.c.cairo_image_surface_get_height(s) < 2:
            self.c.cairo_surface_destroy(s)
            return None
        return s

    def _illustration(self, key):
        surf = self._surface(ILL_W, ILL_H)
        prev = self._on(surf)
        c, cr = self.c, self.cr
        png = self._load_png(key)
        if png:
            pw, ph = c.cairo_image_surface_get_width(png), c.cairo_image_surface_get_height(png)
            sc = max(ILL_W / float(pw), ILL_H / float(ph))            # cover; 1.0 for the pre-scaled files
            c.cairo_save(cr)
            c.cairo_translate(cr, (ILL_W - pw * sc) / 2.0, (ILL_H - ph * sc) / 2.0)
            c.cairo_scale(cr, sc, sc)
            c.cairo_set_source_surface(cr, png, 0, 0)
            c.cairo_paint(cr)
            c.cairo_restore(cr)
            c.cairo_surface_destroy(png)
            self.rect(0, 0, ILL_W, ILL_H, "bg", 0.34)                   # calm the art under the pictogram
            self._glow(ILL_W / 2.0, ILL_H * 0.5, ILL_H * 0.62, "bg", 0.72)
        else:
            self._vgrad(0, 0, ILL_W, ILL_H, "button", "card")
            for k in range(-ILL_H, ILL_W, 34):                         # speed lines
                c.cairo_move_to(cr, k, ILL_H)
                c.cairo_line_to(cr, k + ILL_H * 0.6, 0)
            self.col("dim", 0.35)
            c.cairo_set_line_width(cr, 3)
            c.cairo_stroke(cr)
            self._glow(ILL_W / 2.0, ILL_H * 0.46, ILL_H * 0.7, "gold", 0.20)
        self.art_used[key] = "png" if png else "drawn"
        (self._pict_local if key == "esde" else self._pict_versus)(ILL_W / 2.0, ILL_H / 2.0)
        self._vgrad(0, ILL_H - 56, ILL_W, 56, "card", "card", 0.0, 0.9)  # melts into the card below
        self._off(prev)
        return surf

    def _stick(self, cx, base_y, ball, scale=1.0, shade=False):
        """An arcade stick on its own base (FIGHTCADE: one per player, apart)."""
        s = scale
        self.rrect(cx - 70 * s, base_y, 140 * s, 34 * s, 12 * s, fill="dim" if shade else "button",
                   stroke="badge_outline", width=4 * s)
        for k in range(3):
            self._circle(cx + (8 + k * 21) * s, base_y + 17 * s, 7 * s, fill="gold_muted" if not shade else "dimmed")
        self.rrect(cx - 42 * s - 5 * s, base_y - 52 * s, 10 * s, 56 * s, 5 * s, fill="chrome" if not shade else "dimmed")
        self._circle(cx - 42 * s, base_y - 60 * s, 20 * s, fill=ball, stroke="badge_outline", width=4 * s)

    def _pict_local(self, cx, cy):
        """ARCADE = local versus (the maintainer 2026-11-18: the pair to FIGHTCADE): P1 and P2 sticks side by side on ONE
        control panel, the gradient VS between them; no signal, no link."""
        self._glow(cx, cy, 170, "gold", 0.30)
        for side, ball in ((-1, "gold"), (1, "red_light")):
            self._glow(cx + side * 190, cy + 10, 110, ball, 0.22)
        px, pw, py, ph = cx - 290, 580, cy + 44, 46                     # the shared control panel
        self.rrect(px, py, pw, ph, 14, fill="button", stroke="badge_outline", width=4)
        self.rect(px + 14, py + 4, pw - 28, 3, "gold_muted", 0.55)       # its lit top edge
        for side, ball, label in ((-1, "gold", "P1"), (1, "red_light", "P2")):
            x = cx + side * 190
            for k in range(3):                                         # three punches per player
                self._circle(x + (8 + k * 21), py + ph / 2.0, 7, fill="gold_muted")
            self.rrect(x - 47, py - 52, 10, 56, 5, fill="chrome")
            self._circle(x - 42, py - 60, 20, fill=ball, stroke="badge_outline", width=4)
            self._label(x, cy + 124, label, ball)
        vs = self.width("VS", "display", 92)
        self._grad_text(cx - vs / 2.0 - 8, cy + 26, "VS", "display", 92)

    def _pict_versus(self, cx, cy):
        """FIGHTCADE = online versus: P1 and P2 sticks, a link between them, VS and a signal."""
        c, cr = self.c, self.cr
        self._glow(cx, cy, 170, "crimson", 0.30)
        dash = (ctypes.c_double * 2)(16, 12)
        c.cairo_set_dash(cr, dash, 2, 0)
        self.col("muted", 0.8)
        c.cairo_set_line_width(cr, 5)
        c.cairo_move_to(cr, cx - 150, cy + 62)
        c.cairo_line_to(cr, cx + 150, cy + 62)
        c.cairo_stroke(cr)
        c.cairo_set_dash(cr, dash, 0, 0)
        for side, ball, label in ((-1, "gold", "P1"), (1, "red_light", "P2")):
            x = cx + side * 190
            self._glow(x, cy + 10, 110, ball, 0.22)
            self._stick(x, cy + 44, ball)
            self._label(x, cy + 124, label, ball)
        for r, a in ((26, 1.0), (46, 0.75), (66, 0.5)):                  # signal arcs over the VS
            c.cairo_new_sub_path(cr)
            c.cairo_arc(cr, cx, cy - 34, r, math.pi * 1.22, math.pi * 1.78)
            self.col("muted", a)
            c.cairo_set_line_width(cr, 7)
            c.cairo_stroke(cr)
        self._circle(cx, cy - 36, 7, fill="muted")
        vs = self.width("VS", "display", 92)
        self._grad_text(cx - vs / 2.0 - 8, cy + 72, "VS", "display", 92)

    def _glow_ring(self):
        """The lit card's outer glow, drawn once (six soft strokes); a frame only paints it with the pulse's alpha."""
        surf = self._surface(CW + 2 * GLOW, CH + 2 * GLOW)
        prev = self._on(surf)
        r = self.b["radius"]["card"]
        for grow in range(3, GLOW, 3):
            self._rr_path(GLOW - grow, GLOW - grow, CW + 2 * grow, CH + 2 * grow, r + grow)
            self.col("gold", 0.30 * (1.0 - grow / float(GLOW)))           # (Painter.rrect strokes opaque)
            self.c.cairo_set_line_width(self.cr, 3.2)
            self.c.cairo_stroke(self.cr)
        self._off(prev)
        return surf

    # -- the card layer per selection (drawn once) ----------------------------------------------
    def _layer(self, sel, hostname):
        key = (sel, hostname)
        if key in self.layers:
            return self.layers[key]
        surf = self._surface(W, H)
        prev = self._on(surf)
        self._draw_static(sel, hostname)
        self._off(prev)
        self.layers[key] = surf
        return surf

    def _draw_static(self, sel, hostname):
        b, ts, c, cr = self.b, self.b["type_scale"], self.c, self.cr
        self.rect(0, 0, W, H, "bg")
        self._vgrad(0, 0, W, 260, "panel", "bg")
        self.text(M, 112, "COIN DOJO", "display", ts["title"], "gold")
        self.text(W - M, 62, "CABINET", "ui_bold", ts["small"], "muted", "r")
        self.text(W - M, 92, self.fit(hostname, "ui_bold", ts["heading"] - 4, 300), "ui_bold", ts["heading"] - 4, "text", "r")
        tw = self.width("COIN DOJO", "display", ts["title"])
        self.text(M + tw + 28, 112, "CHOOSE A MODE", "ui_bold", ts["label"], "muted")
        self.rect(M, 140, W - 2 * M, b["border"]["rule"], "dim")
        self.rect(M, 140, 120, 4, "gold")

        for i, m in enumerate(MODES):
            x, on = card_x(i), i == sel
            self.rrect(x, TOP, CW, CH, b["radius"]["card"], fill="card")
            ix, iy = x + INSET, TOP + INSET
            c.cairo_save(cr)
            self._round_top(ix, iy, ILL_W, ILL_H, b["radius"]["card"] - INSET)
            c.cairo_clip(cr)
            c.cairo_set_source_surface(cr, self.ill[m["key"]], ix, iy)
            c.cairo_paint(cr)
            if not on:
                self.rect(ix, iy, ILL_W, ILL_H, "card", DIM)
            c.cairo_restore(cr)
            ty = TOP + ILL_H + 78
            self.text(x + 36, ty, m["name"], "display", 56, "gold" if on else "dimmed")
            self.text(x + 36, ty + 46, m["sub"].upper(), "ui_bold", ts["label"], "text" if on else "dimmed")
            for j, ln in enumerate(m["lines"]):
                self.text(x + 36, ty + 98 + j * 44, ln, "ui_semi", ts["body"], "text_soft" if on else "dimmed")
            tw = self.width(m["tag"], "ui_bold", ts["small"] + 2)
            self.rrect(x + 36, TOP + CH - 66, tw + 28, 38, 19, stroke="lavender" if on else "dim", width=2)
            self.text(x + 50, TOP + CH - 39, m["tag"], "ui_bold", ts["small"] + 2, "lavender" if on else "dimmed")
            self.rrect(x, TOP, CW, CH, b["radius"]["card"], stroke="gold" if on else "dim",
                       width=b["border"]["card"] if on else b["border"]["rule"])

        y = H - 64
        self.rect(0, y - 34, W, 98, "card", 0.85)
        x = M
        for badge, word in (("STICK", "MOVE"), ("LK", "START")):
            x += self.badge(x, y, badge, 36) + 12
            x += self.text(x, y + 9, word, "ui_bold", ts["help"], "text") + 40
        self.scanlines()

    # -- one frame ------------------------------------------------------------------------------
    def draw(self, v):
        b, ts, c, cr = self.b, self.b["type_scale"], self.c, self.cr
        sel = v["sel"]
        self.shown = v                                                  # the view on screen (tests read it)
        c.cairo_set_source_surface(cr, self._layer(sel, v["hostname"]), 0, 0)
        c.cairo_paint(cr)
        x = card_x(sel)

        e = 1.0 - (1.0 - v.get("anim", 1.0)) ** 3                        # ease-out
        if e < 1.0:                                                     # the illustration catches up with the border
            ix, iy = x + INSET, TOP + INSET
            c.cairo_save(cr)
            self._round_top(ix, iy, ILL_W, ILL_H, b["radius"]["card"] - INSET)
            c.cairo_clip(cr)
            self.rect(ix, iy, ILL_W, ILL_H, "card")
            c.cairo_set_source_surface(cr, self.ill[MODES[sel]["key"]], ix + SLIDE * (1.0 - e), iy)
            c.cairo_paint(cr)
            self.rect(ix, iy, ILL_W, ILL_H, "card", DIM * (1.0 - e))
            c.cairo_restore(cr)

        pulse = 0.5 + 0.5 * math.sin(2 * math.pi * v.get("t", 0.0) / PULSE_S) if v.get("pulsing", True) else 0.7
        c.cairo_set_source_surface(cr, self.glow, x - GLOW, TOP - GLOW)   # the soft outer glow, pulsing
        c.cairo_paint_with_alpha(cr, 0.35 + 0.65 * pulse)
        self.rrect(x, TOP, CW, CH, b["radius"]["card"], stroke="gold", width=b["border"]["card"])

        # status strip: what LK does / the countdown, its bar, the note (redrawn every frame, scanlines included)
        sy = STATUS_Y
        self.rect(0, sy, W, 112, "bg")
        if v["counting"]:
            secs = int(math.ceil(v["remaining"]))
            self.text(M, sy + 40, "Starting %s in %d" % (MODES[0]["name"], secs), "ui_bold", ts["heading"], "text")
        else:
            w = self.text(M, sy + 40, "Press ", "ui_bold", ts["heading"], "muted")
            w += self.badge(M + w + 2, sy + 28, "LK", 34) + 10
            self.text(M + w, sy + 40, "to start %s" % MODES[sel]["name"], "ui_bold", ts["heading"], "muted")
        bw = W - 2 * M
        self.rrect(M, sy + 58, bw, 10, 5, fill="panel")
        if v["counting"] and v["total"]:
            frac = min(1.0, v["remaining"] / float(v["total"]))
            if frac > 0:
                self.rrect(M, sy + 58, max(10, bw * frac), 10, 5, fill="gold")
        note = MODES[sel]["note"]
        self.text(M, sy + 100, ("Any input stops the countdown. " if v["counting"] else "") + note, "ui_semi", ts["help"],
                  "dimmed")
        for yy in range(sy - sy % 4, sy + 112, 4):
            if yy + 2 >= sy:
                self.rect(0, yy + 2, W, 1, "bg_deep" if "bg_deep" in b["colors"] else "bg", 0.22)
        c.cairo_surface_flush(self.surf)

    def wants_frame(self, v):
        """Seconds until the next frame is due when nothing happens (motion only; inputs wake the loop anyway).
        None = no motion left: the loop waits for events (and the countdown's own redraws) only."""
        if v.get("anim", 1.0) < 1.0:
            return 1.0 / 60
        return 1.0 / PULSE_FPS if v.get("pulsing", True) else None


class Usage(SystemExit):
    def __init__(self, msg):
        print(msg, file=sys.stderr)
        super().__init__(2)


def parse_args(argv):
    opts = dict(png=None, actions=[], countdown=0, at=0.0, moved=None, font_dir=None, art_dir=None, hostname=None,
                log=LOG_PATH)
    a = list(argv)
    usage = ("usage: bootmenu_app.py [--countdown N] [--png OUT [--actions a,b] [--at S] [--moved S]] [--font-dir D] "
             "[--art-dir D] [--hostname N] [--log F]")
    while a:
        x = a.pop(0)
        if x in ("--png", "--actions", "--countdown", "--at", "--moved", "--font-dir", "--art-dir", "--hostname", "--log"):
            if not a:
                raise Usage(usage)
            val = a.pop(0)
            try:
                if x == "--countdown":
                    val = int(val)
                    if val < 0:
                        raise ValueError
                elif x in ("--at", "--moved"):
                    val = float(val)
                    if x == "--moved" and val < 0:
                        raise ValueError
                elif x == "--actions":
                    val = [s for s in val.split(",") if s]
            except ValueError:
                raise Usage(usage)
            opts[{"--font-dir": "font_dir", "--art-dir": "art_dir"}.get(x, x[2:])] = val
        else:
            raise Usage(usage)
    return opts


def _hostname(opts):
    if opts["hostname"]:
        return opts["hostname"]
    try:
        return socket.gethostname().split(".")[0] or "coin-dojo"
    except OSError:
        return "coin-dojo"


def _term(signum, frame):
    raise SystemExit(128 + signum)             # unwinds through finally (window closed); nothing printed


def main(argv=None):
    opts = parse_args(sys.argv[1:] if argv is None else argv)
    font_dir = opts["font_dir"]
    if not font_dir and os.path.isdir(os.path.join(HERE, "fonts")):
        font_dir = os.path.join(HERE, "fonts")
    b = sa.brand.load(font_dir=font_dir)
    log = core.Log(opts["log"])
    host = _hostname(opts)
    script = [sa.script_action(s) for s in opts["actions"]]

    if opts["png"]:
        menu = Menu(opts["countdown"], 0.0)
        for act in script:
            menu.handle(act)
        if opts["moved"] is not None:
            menu.moved_at = opts["at"] - opts["moved"]
        try:
            painter = BootPainter(b, opts["art_dir"])
        except OSError as e:
            print("bootmenu: %s" % e, file=sys.stderr)
            return 3
        log.write("art", **painter.art_used)
        painter.draw(menu.view(opts["at"], host))
        return 0 if painter.png(opts["png"]) else 1

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    try:
        front = sa.SDLFront()
        painter = BootPainter(b, opts["art_dir"])
    except OSError as e:
        print("bootmenu: no display: %s" % e, file=sys.stderr)
        return 3
    log.write("start", countdown=opts["countdown"], **painter.art_used)
    inp = sa.Input()
    t_open = time.monotonic()
    menu = Menu(opts["countdown"], t_open)
    for i in range(len(MODES)):                                         # both card layers before the first input
        painter._layer(i, host)
    last, due = None, 0.0
    try:
        for act in script:
            if menu.handle(act, time.monotonic()):
                break
        while not menu.choice:
            now = time.monotonic()
            if menu.tick(now):
                break
            v = menu.view(now, host)
            shown = (v["sel"], v["counting"], int(v["remaining"] * 10))
            if shown != last or now >= due:                             # a change (input, countdown) or motion due
                painter.draw(v)
                front.present(painter)
                nxt = painter.wants_frame(v)
                last, due = shown, (now + nxt if nxt is not None else float("inf"))
            wait = max(1, int(min(due - time.monotonic(), 1.0) * 1000))
            for act in front.events(inp, min(wait, 100 if menu.counting else 250)):
                if act == "redraw":
                    last = None
                elif act == "exit":
                    continue                                    # SELECT+START: nothing to leave here
                elif isinstance(act, tuple) and act[0] == "text":
                    menu.handle("text")
                elif time.monotonic() - t_open < GRACE:
                    continue                                    # a press held over from the app that just closed
                else:
                    menu.handle(act, time.monotonic())
                if menu.choice:
                    break
    finally:
        front.close()
    log.write("choice", mode=menu.choice)
    print(menu.choice)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
