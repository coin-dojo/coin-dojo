#!/usr/bin/env python3
"""TROPHIES page for the coin-dojo cabinet (#176): your RetroAchievements at a glance, NeoStation style.

ES-DE starts it from the TROPHIES carousel entry like the SETTINGS app (same stack: ctypes SDL2 + cairo + FreeType,
brand kit only, 1440x1080 = the 4:3 safe area). It shows the cached page at once, then refreshes from the
RetroAchievements web API in the background (trophies_core.py) and fetches badges and icons into the cache.

    trophies_app.py [ENTRY.hks]                                  full screen
    trophies_app.py --png OUT.png [--entry ENTRY] [--fixture SUMMARY.json[,AOTW.json]] [--images DIR] [--state S]
                                                                 one frame to a PNG (tests, previews), no network
Panel: STICK left/right picks a list, up/down moves in it; MK back (exit); START+SELECT exit.
Exit codes: 0 ok, 2 usage, 3 no display / library missing.
"""
import ctypes
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import settings_app as sa  # noqa: E402  (Painter, SDLFront, Input, brand: one stack for both apps)
import trophies_core as core  # noqa: E402

W, H = sa.W, sa.H
M = 72


class TrophyPainter(sa.Painter):
    def __init__(self, b, cache_dir, images=None):
        super().__init__(b)
        vp, d, i = ctypes.c_void_p, ctypes.c_double, ctypes.c_int
        for name, (res, args) in {
                "cairo_image_surface_create_from_png": (vp, [ctypes.c_char_p]), "cairo_surface_status": (i, [vp]),
                "cairo_image_surface_get_width": (i, [vp]), "cairo_image_surface_get_height": (i, [vp]),
                "cairo_surface_destroy": (None, [vp]), "cairo_save": (None, [vp]), "cairo_restore": (None, [vp]),
                "cairo_translate": (None, [vp, d, d]), "cairo_scale": (None, [vp, d, d]), "cairo_clip": (None, [vp]),
                "cairo_set_source_surface": (None, [vp, vp, d, d])}.items():
            fn = getattr(self.c, name)
            fn.restype, fn.argtypes = res, args
        self.cache_dir = cache_dir
        self.images = images                         # a fixed folder of PNGs (previews), else the cache
        self.help = ((["STICK"], "BROWSE"), (["MK"], "BACK"))   # the overlay (trophiesd) shows its own
        self._surf = {}

    # -- images -------------------------------------------------------------------------------
    def _load(self, kind, name):
        url = core.image_url(kind, name)
        path = core.image_file(self.cache_dir, url)
        if self.images and url:
            path = os.path.join(self.images, os.path.basename(path))
        if not path or not os.path.isfile(path):
            return None
        if path not in self._surf:
            s = self.c.cairo_image_surface_create_from_png(path.encode())
            self._surf[path] = s if s and self.c.cairo_surface_status(s) == 0 else None
        return self._surf[path]

    def image(self, x, y, size, kind, name, label="", r=10, dim=False):
        """A square image clipped to a rounded rect; a lettered placeholder when it is not cached (yet)."""
        s = self._load(kind, name)
        if not s:
            self.rrect(x, y, size, size, r, fill="panel", stroke="dim", width=2)
            ini = "".join(w[0] for w in label.split()[:2]).upper() or "?"
            self.text(x + size / 2.0, y + size / 2.0 + size * 0.14, ini, "ui_bold", size * 0.38, "muted", "c")
            return
        c, cr = self.c, self.cr
        sw, sh = c.cairo_image_surface_get_width(s), c.cairo_image_surface_get_height(s)
        c.cairo_save(cr)
        self.rrect_path(x, y, size, size, r)
        c.cairo_clip(cr)
        c.cairo_translate(cr, x, y)
        c.cairo_scale(cr, size / float(max(sw, 1)), size / float(max(sh, 1)))
        c.cairo_set_source_surface(cr, s, 0, 0)
        c.cairo_paint(cr)
        c.cairo_restore(cr)
        if dim:
            self.rrect(x, y, size, size, r, fill="bg", a=0.55)

    def rrect_path(self, x, y, w, h, r):
        c, cr, pi = self.c, self.cr, 3.141592653589793
        c.cairo_new_sub_path(cr)
        c.cairo_arc(cr, x + w - r, y + r, r, -pi / 2, 0)
        c.cairo_arc(cr, x + w - r, y + h - r, r, 0, pi / 2)
        c.cairo_arc(cr, x + r, y + h - r, r, pi / 2, pi)
        c.cairo_arc(cr, x + r, y + r, r, pi, 3 * pi / 2)
        c.cairo_close_path(cr)

    def progress(self, x, y, w, h, got, total):
        self.rrect(x, y, w, h, h / 2.0, fill="dim")
        if total and got:
            self.rrect(x, y, max(h, w * got / float(total)), h, h / 2.0, fill="gold")

    def pill(self, x_right, cy, text, fill="gold", ink="on_gold", px=20):
        tw = self.width(text, "ui_bold", px)
        w = tw + px * 1.2
        self.rrect(x_right - w, cy - px * 0.8, w, px * 1.6, px * 0.8, fill=fill)
        self.text(x_right - w / 2.0, cy + px * 0.36, text, "ui_bold", px, ink, "c")
        return w

    # -- frame --------------------------------------------------------------------------------
    def draw_trophies(self, v):
        b, ts = self.b, self.b["type_scale"]
        d = v["data"] or {}
        p = d.get("profile") or {}
        self.rect(0, 0, W, H, "bg")
        head_h = 300                                    # header gradient height (px)
        grad = self.c.cairo_pattern_create_linear(0, 0, 0, head_h)
        for stop, name in ((0, "panel"), (1, "bg")):
            r, g, bl = b.rgb(name)
            self.c.cairo_pattern_add_color_stop_rgba(grad, stop, r / 255.0, g / 255.0, bl / 255.0, 1)
        self.c.cairo_set_source(self.cr, grad)
        self.c.cairo_rectangle(self.cr, 0, 0, W, head_h)
        self.c.cairo_fill(self.cr)
        self.c.cairo_pattern_destroy(grad)

        # header: picture, name, rank | points tiles
        self.text(M, 74, "TROPHIES", "ui_bold", ts["label"], "muted")
        self.image(M, 96, 104, "pic", p.get("pic", ""), p.get("user") or v["user"], r=52)
        name = p.get("user") or v["user"] or "NOT CONNECTED"
        self.text(M + 128, 150, self.fit(name, "display", ts["title"], 560), "display", ts["title"], "gold")
        sub = "RANK #{:,} OF {:,}".format(p["rank"], p["ranked"]) if p.get("rank") else (p.get("status") or "").upper()
        self.text(M + 130, 190, sub, "ui_bold", ts["small"], "muted")
        tx = W - M
        for label, val, col in () if not p else (("SOFTCORE", p.get("softcore", 0), "text"), ("HARDCORE", p.get("points", 0), "gold")):
            tw = 220
            self.rrect(tx - tw, 96, tw, 104, b["radius"]["box"], fill="card", stroke="dim", width=b["border"]["box"])
            self.text(tx - tw / 2.0, 132, label, "ui_bold", ts["small"], "muted", "c")
            self.text(tx - tw / 2.0, 180, "{:,}".format(val), "display", ts["heading"], col, "c")
            tx -= tw + 20
        self.rect(M, 228, W - 2 * M, b["border"]["rule"], "dim")
        self.rect(M, 228, 120, 4, "gold")

        if v["state"] == "nokey" and not d:
            self.draw_nokey(v)
        else:
            self.draw_aotw(d.get("aotw"), 256)
            self.draw_lists(v, d, 478)

        # help bar + status
        y = H - 64
        self.rect(0, y - 34, W, 98, "card", 0.85)
        x = M
        for keys, word in self.help:
            for k in keys:
                x += self.badge(x, y, k, 36) + 10
            x += self.text(x, y + 9, word, "ui_bold", ts["help"], "text") + 34
        st = v["status"]
        if st:
            self.text(W - M, y + 8, st, "ui_bold", ts["small"], "red_light" if v["state"] == "offline" else "muted", "r")
        self.scanlines()
        self.c.cairo_surface_flush(self.surf)

    def draw_nokey(self, v):
        ts = self.b["type_scale"]
        y = 330
        self.text(M, y, "CONNECT YOUR RETROACHIEVEMENTS ACCOUNT", "display", ts["heading"], "gold")
        lines = ["On retroachievements.org: Settings, Keys, copy your Web API Key.",
                 "On your Mac: store it in the Keychain item your config names (hk_cheevos_web_keychain),",
                 "set hk_cheevos_user, then run the deploy. The key never goes into git."]
        for i, ln in enumerate(lines):
            self.text(M, y + 70 + i * 48, ln, "ui_semi", ts["body"], "text_soft")

    def draw_aotw(self, a, y):
        b, ts = self.b, self.b["type_scale"]
        w = W - 2 * M
        self.rrect(M, y, w, 190, b["radius"]["card"], fill="card", stroke="gold_card", width=b["border"]["box"])
        self.text(M + 32, y + 44, "ACHIEVEMENT OF THE WEEK", "ui_bold", ts["small"], "gold")
        if not a:
            self.text(M + 32, y + 110, "NOT LOADED YET", "ui_bold", ts["body"], "muted")
            return
        self.image(M + 32, y + 62, 104, "badge", a.get("badge", ""), a.get("title", ""), r=12)
        tx, tw = M + 164, w - 164 - 180
        self.text(tx, y + 98, self.fit(a["title"], "ui_bold", ts["heading"], tw), "ui_bold", ts["heading"], "text")
        self.text(tx, y + 136, self.fit(a.get("desc", ""), "ui_semi", ts["label"], tw), "ui_semi", ts["label"], "text_soft")
        game = "  -  ".join(s for s in (a.get("game"), a.get("console")) if s)
        self.text(tx, y + 170, self.fit(game.upper(), "ui_bold", ts["small"], tw), "ui_bold", ts["small"], "muted")
        self.pill(M + w - 32, y + 110, "%d PTS" % a.get("points", 0), px=ts["label"])

    def draw_lists(self, v, d, y):
        ts = self.b["type_scale"]
        gap = 36
        cw = (W - 2 * M - gap) / 2.0
        cols = (("RECENT UNLOCKS", d.get("recent") or [], self.row_unlock),
                ("RECENTLY PLAYED", d.get("played") or [], self.row_played))
        for i, (title, rows, fn) in enumerate(cols):
            x = M + i * (cw + gap)
            focus = v["focus"] == i
            self.text(x, y + 30, title, "ui_bold", ts["label"], "gold" if focus else "muted")
            if focus:
                self.rect(x, y + 44, self.width(title, "ui_bold", ts["label"]), 3, "gold")
            if not rows:
                self.text(x, y + 100, "NOTHING YET" if v["state"] != "loading" else "LOADING …", "ui_bold", ts["body"], "dim")
            rh = 78
            for j, row in enumerate(rows[:core.RECENT_N]):
                ry = y + 64 + j * rh
                sel = focus and v["sel"][i] == j
                if sel:
                    self.rrect(x - 10, ry - 6, cw + 20, rh - 4, self.b["radius"]["tag"], fill="panel", stroke="gold_muted", width=2)
                fn(x, ry, cw, row, v["now"])

    def row_unlock(self, x, y, w, a, now):
        ts = self.b["type_scale"]
        self.image(x, y, 62, "badge", a.get("badge", ""), a.get("title", ""), r=8)
        tw = w - 62 - 20 - 120
        self.text(x + 80, y + 28, self.fit(a["title"], "ui_bold", ts["label"], tw), "ui_bold", ts["label"], "text")
        self.text(x + 80, y + 58, self.fit(a.get("game", "").upper(), "ui_bold", ts["small"] - 2, tw), "ui_bold",
                  ts["small"] - 2, "muted")
        self.text(x + w, y + 28, "+%d" % a.get("points", 0), "ui_bold", ts["label"], "gold" if a.get("hardcore") else "text", "r")
        self.text(x + w, y + 58, core.ago(a.get("date"), now), "ui_bold", ts["small"] - 4, "muted", "r")

    def row_played(self, x, y, w, g, now):
        ts = self.b["type_scale"]
        self.image(x, y, 62, "icon", g.get("icon", ""), g.get("title", ""), r=8)
        tw = w - 62 - 20 - 10
        self.text(x + 80, y + 28, self.fit(g["title"], "ui_bold", ts["label"], tw - 130), "ui_bold", ts["label"], "text")
        self.text(x + w, y + 28, core.ago(g.get("last"), now), "ui_bold", ts["small"] - 4, "muted", "r")
        self.text(x + 80, y + 58, self.fit(g.get("console", "").upper(), "ui_bold", ts["small"] - 2, 200), "ui_bold",
                  ts["small"] - 2, "muted")
        total, got = g.get("total", 0), g.get("got", 0)
        if total:
            self.progress(x + 300, y + 46, w - 300 - 110, 14, got, total)
        self.text(x + w, y + 60, ("%d / %d" % (got, total)) if total else "NO SET", "ui_bold", ts["small"] - 2,
                  "gold" if total and got == total else "text_soft", "r")


# ================================================================================================
# main
# ================================================================================================
def parse_args(argv):
    opts = dict(entry=None, png=None, fixture=None, images=None, state=None, font_dir=None, now=None)
    a = list(argv)
    while a:
        x = a.pop(0)
        if x in ("--png", "--entry", "--fixture", "--images", "--state", "--font-dir", "--now"):
            if not a:
                raise SystemExit("usage: %s needs a value" % x)
            opts[{"--font-dir": "font_dir"}.get(x, x[2:])] = a.pop(0)
        elif x.startswith("-"):
            raise SystemExit("usage: trophies_app.py [ENTRY.hks] [--png OUT] [--fixture S.json[,A.json]] [--images DIR]")
        else:
            opts["entry"] = x
    return opts


def fixture_getter(spec):
    """--fixture: answer the two API calls from files (no network)."""
    files = spec.split(",")

    def get(url):
        name = files[1] if ("AchievementOfTheWeek" in url and len(files) > 1) else files[0]
        with open(name, "rb") as fh:
            return fh.read()
    return get


def build(opts):
    cfg = core.load_config(opts["entry"])
    font_dir = opts["font_dir"] or (os.path.join(HERE, "fonts") if os.path.isdir(os.path.join(HERE, "fonts")) else None)
    b = sa.brand.load(font_dir=font_dir)
    return b, cfg


def main(argv=None):
    opts = parse_args(sys.argv[1:] if argv is None else argv)
    b, cfg = build(opts)
    if opts["png"]:
        if not (opts["entry"] and os.path.isfile(opts["entry"])):        # previews never touch the real cache
            import tempfile
            cfg["cache_dir"] = tempfile.mkdtemp(prefix="trophies-preview-")
        now = (lambda: float(opts["now"])) if opts["now"] else time.time
        get = fixture_getter(opts["fixture"]) if opts["fixture"] else (lambda url: (_ for _ in ()).throw(core.FetchError("offline preview")))
        page = core.Page(cfg, get=get, now=now)
        if opts["fixture"] and page.state == "nokey":         # previews: a fixture stands in for user + key
            page.key, page.cfg["user"], page.state = "fixturekey0000", page.cfg["user"] or "preview", "loading"
            page.user = page.user or page.cfg["user"]
        if opts["state"] != "loading":
            page.refresh()
        if opts["state"]:
            page.state = opts["state"]
        painter = TrophyPainter(b, cfg["cache_dir"], images=opts["images"])
        painter.draw_trophies(page.view())
        return 0 if painter.png(opts["png"]) else 1

    page = core.Page(cfg)
    painter = TrophyPainter(b, cfg["cache_dir"])
    try:
        front = sa.SDLFront()
    except OSError as e:
        print("trophies: no display: %s" % e, file=sys.stderr)
        return 3
    changed = threading.Event()

    def worker():
        page.refresh()
        changed.set()
        for kind, name in core.images_for(page.data or {}):
            if core.fetch_image(cfg["cache_dir"], core.image_url(kind, name)):
                changed.set()
    threading.Thread(target=worker, daemon=True).start()
    inp = sa.Input()
    dirty = True
    try:
        while not page.quit:
            if changed.is_set():
                changed.clear()
                painter._surf.clear()
                dirty = True
            if dirty:
                painter.draw_trophies(page.view())
                front.present(painter)
                dirty = False
            for a in front.events(inp, 30 if inp.dirs else 250):
                if a != "redraw":
                    page.handle(a)
                dirty = True
    finally:
        front.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
