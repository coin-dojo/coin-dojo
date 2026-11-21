#!/usr/bin/env python3
"""SETTINGS app for the coin-dojo cabinet (#32-#35, #189, #171): Wi-Fi, cabinet name, benchmark mode and RetroAchievements
(the page list: settings_core.Page), driven by the arcade stick + panel
buttons and, at the same time, by a USB keyboard if one is plugged in.

Runs under cage like a game: ES-DE starts it from the SETTINGS carousel entry (`settings` system,
<command>python3 .../settings_app.py %ROM%</command>; the .hks entry file is a JSON config).
Stack: python3 stdlib + ctypes into libSDL2 (window, pad = SDL game controller with SDL_GAMECONTROLLERCONFIG from
~/.bash_profile, keyboard = SDL text input through the compositor's keymap, hot-plug) and libcairo + libfreetype
(drawing, the theme fonts). All three are already on the cabinet; no new packages. Look: brand/tokens.json only.

    settings_app.py [ENTRY.hks] [--actions a,b,...]        full screen (SDL); --actions replays inputs first
    settings_app.py --png OUT.png [--actions ...]          render one frame to a PNG, no window (tests, previews)
    options: --nmcli PATH  --timedatectl PATH  --hostname-helper PATH  --password-helper PATH  --font-dir DIR  --hostname NAME (PNG mode only: shown name)
    --test-hooks  TEST ONLY (#198): honour the entry's game_proc and a loopback http cheevos_api (fake_ra.py)

Panel: STICK move, LK select / type, MK back / delete (one page up), START done, SELECT quit (the whole app, from
any page, straight back to the carousel; #224), START+SELECT also quits (as in games).
Keyboard: arrows, Enter done/select, Esc back, Backspace delete, Tab next field, typed characters go in the field.
Exit codes: 0 ok, 2 usage, 3 no display / library missing.
"""
import ctypes
import ctypes.util
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
for _b in (os.path.join(HERE, "brand"), os.path.normpath(os.path.join(HERE, "..", "..", "brand"))):
    if os.path.isfile(os.path.join(_b, "brand.py")):
        sys.path.insert(0, _b)
        break
import brand  # noqa: E402
import settings_core as core  # noqa: E402

W, H = 1440, 1080                 # the 4:3 safe area; centred on the panel


# ================================================================================================
# cairo + FreeType (ctypes)
# ================================================================================================
class _TextExtents(ctypes.Structure):
    _fields_ = [(n, ctypes.c_double) for n in ("x_bearing", "y_bearing", "width", "height", "x_advance", "y_advance")]


def _lib(names):
    for n in names:
        p = ctypes.util.find_library(n.split(".")[0].replace("lib", "", 1)) if "/" not in n else n
        for cand in (n, p):
            if not cand:
                continue
            try:
                return ctypes.CDLL(cand)
            except OSError:
                continue
    raise OSError("library not found: %s" % names[0])


class Painter:
    def __init__(self, b):
        self.b = b
        c = self.c = _lib(["libcairo.so.2", "/opt/homebrew/lib/libcairo.2.dylib", "libcairo.2.dylib"])
        vp, d, i = ctypes.c_void_p, ctypes.c_double, ctypes.c_int
        sig = {
            "cairo_image_surface_create": (vp, [i, i, i]), "cairo_image_surface_get_data": (vp, [vp]),
            "cairo_image_surface_get_stride": (i, [vp]), "cairo_surface_write_to_png": (i, [vp, ctypes.c_char_p]),
            "cairo_create": (vp, [vp]), "cairo_destroy": (None, [vp]), "cairo_surface_flush": (None, [vp]),
            "cairo_paint": (None, [vp]), "cairo_set_source_rgba": (None, [vp, d, d, d, d]),
            "cairo_set_source": (None, [vp, vp]), "cairo_rectangle": (None, [vp, d, d, d, d]),
            "cairo_fill": (None, [vp]), "cairo_fill_preserve": (None, [vp]), "cairo_stroke": (None, [vp]),
            "cairo_set_line_width": (None, [vp, d]), "cairo_new_sub_path": (None, [vp]), "cairo_new_path": (None, [vp]),
            "cairo_close_path": (None, [vp]), "cairo_arc": (None, [vp, d, d, d, d, d]),
            "cairo_move_to": (None, [vp, d, d]), "cairo_line_to": (None, [vp, d, d]),
            "cairo_show_text": (None, [vp, ctypes.c_char_p]), "cairo_set_font_size": (None, [vp, d]),
            "cairo_set_font_face": (None, [vp, vp]), "cairo_select_font_face": (None, [vp, ctypes.c_char_p, i, i]),
            "cairo_text_extents": (None, [vp, ctypes.c_char_p, ctypes.POINTER(_TextExtents)]),
            "cairo_pattern_create_linear": (vp, [d, d, d, d]),
            "cairo_pattern_add_color_stop_rgba": (None, [vp, d, d, d, d, d]), "cairo_pattern_destroy": (None, [vp]),
            "cairo_ft_font_face_create_for_ft_face": (vp, [vp, i]),
        }
        for name, (res, args) in sig.items():
            fn = getattr(c, name)
            fn.restype, fn.argtypes = res, args
        self.surf = c.cairo_image_surface_create(0, W, H)                    # CAIRO_FORMAT_ARGB32
        self.cr = c.cairo_create(self.surf)
        self.faces = {}
        ft = _lib(["libfreetype.so.6", "/opt/homebrew/lib/libfreetype.6.dylib", "libfreetype.6.dylib"])
        ft.FT_Init_FreeType.argtypes = [ctypes.POINTER(vp)]
        ft.FT_New_Face.argtypes = [vp, ctypes.c_char_p, ctypes.c_long, ctypes.POINTER(vp)]
        lib = vp()
        if ft.FT_Init_FreeType(ctypes.byref(lib)) == 0:
            for role in ("display", "ui_bold", "ui_semi", "pixel"):
                face = vp()
                path = b.font(role)
                if os.path.isfile(path) and ft.FT_New_Face(lib, path.encode(), 0, ctypes.byref(face)) == 0:
                    self.faces[role] = c.cairo_ft_font_face_create_for_ft_face(face, 0)
        self._ft = (ft, lib)

    # -- primitives ----------------------------------------------------------------------------
    def col(self, name, a=1.0):
        r, g, bl = self.b.rgb(name)
        self.c.cairo_set_source_rgba(self.cr, r / 255.0, g / 255.0, bl / 255.0, a)

    def rect(self, x, y, w, h, colour, a=1.0):
        self.col(colour, a)
        self.c.cairo_rectangle(self.cr, x, y, w, h)
        self.c.cairo_fill(self.cr)

    def rrect(self, x, y, w, h, r, fill=None, stroke=None, width=2, a=1.0):
        c, cr, pi = self.c, self.cr, 3.141592653589793
        r = min(r, h / 2.0, w / 2.0)
        c.cairo_new_sub_path(cr)
        c.cairo_arc(cr, x + w - r, y + r, r, -pi / 2, 0)
        c.cairo_arc(cr, x + w - r, y + h - r, r, 0, pi / 2)
        c.cairo_arc(cr, x + r, y + h - r, r, pi / 2, pi)
        c.cairo_arc(cr, x + r, y + r, r, pi, 3 * pi / 2)
        c.cairo_close_path(cr)
        if fill:
            self.col(fill, a)
            (c.cairo_fill_preserve if stroke else c.cairo_fill)(cr)
        if stroke:
            self.col(stroke)
            c.cairo_set_line_width(cr, width)
            c.cairo_stroke(cr)
        c.cairo_new_path(cr)

    def font(self, role, px):
        f = self.faces.get(role)
        if f:
            self.c.cairo_set_font_face(self.cr, f)
        else:
            self.c.cairo_select_font_face(self.cr, b"sans-serif", 0, 1)
        self.c.cairo_set_font_size(self.cr, px)

    def width(self, text, role, px):
        self.font(role, px)
        ext = _TextExtents()
        self.c.cairo_text_extents(self.cr, text.encode("utf-8"), ctypes.byref(ext))
        return ext.x_advance

    def fit(self, text, role, px, maxw):
        if self.width(text, role, px) <= maxw:
            return text
        while text and self.width(text + "…", role, px) > maxw:
            text = text[:-1]
        return text + "…"

    def text(self, x, y, text, role, px, colour, align="l", a=1.0):
        """y = baseline. align l / c / r."""
        w = self.width(text, role, px)
        x = x - w / 2.0 if align == "c" else (x - w if align == "r" else x)
        self.col(colour, a)
        self.c.cairo_move_to(self.cr, x, y)
        self.c.cairo_show_text(self.cr, text.encode("utf-8"))
        return w

    # -- pieces -------------------------------------------------------------------------------
    def badge_w(self, label, h=34):
        """The width badge() draws for label (same rule), so a right-aligned badge can be placed first."""
        b = self.b
        rnd = label in b["badge_pill"]["round_labels"]
        tw = self.width(label, b["badge_pill"]["round_font" if rnd else "pill_font"], h * (0.5 if rnd else 0.62))
        return max(h * b["badge_pill"]["round_width"] / b["badge_pill"]["height"], tw + h * 0.6) if rnd else tw + h * 0.9

    def badge(self, x, cy, label, h=34, fill="gold", ink="bg"):
        """Panel-button badge in the gen_help style: gold, round for LP..HK, pill otherwise; label in bg colour."""
        b = self.b
        rnd = label in b["badge_pill"]["round_labels"]
        role = b["badge_pill"]["round_font" if rnd else "pill_font"]
        px = h * (0.5 if rnd else 0.62)
        tw = self.width(label, role, px)
        w = max(h * b["badge_pill"]["round_width"] / b["badge_pill"]["height"], tw + h * 0.6) if rnd else tw + h * 0.9
        self.rrect(x, cy - h / 2.0, w, h, h / 2.0, fill=fill)
        self.text(x + w / 2.0, cy + px * 0.36, label, role, px, ink, "c")
        return w

    def bars(self, x, y, signal, h=26, on="gold"):
        lit = 0 if signal <= 0 else 1 + min(3, signal // 25)
        for i in range(4):
            bh = h * (i + 1) / 4.0
            self.rect(x + i * 9, y + h - bh, 6, bh, on if i < lit else "dim")
        return 4 * 9

    def lock(self, x, y, colour):
        c = self.c
        self.col(colour)
        c.cairo_set_line_width(self.cr, 3)
        c.cairo_new_sub_path(self.cr)
        c.cairo_arc(self.cr, x + 9, y + 9, 6, 3.14159, 0)
        c.cairo_stroke(self.cr)
        self.rrect(x, y + 9, 18, 14, 3, fill=colour)

    def scanlines(self):
        for y in range(0, H, 4):
            self.rect(0, y + 2, W, 1, "bg_deep" if "bg_deep" in self.b["colors"] else "bg", 0.22)

    # -- frame --------------------------------------------------------------------------------
    def draw(self, v):
        b, ts = self.b, self.b["type_scale"]
        M = 72
        self.rect(0, 0, W, H, "bg")
        head = 260                                           # header glow height
        grad = self.c.cairo_pattern_create_linear(0, 0, 0, head)
        r, g, bl = b.rgb("panel")
        self.c.cairo_pattern_add_color_stop_rgba(grad, 0, r / 255.0, g / 255.0, bl / 255.0, 1)
        r, g, bl = b.rgb("bg")
        self.c.cairo_pattern_add_color_stop_rgba(grad, 1, r / 255.0, g / 255.0, bl / 255.0, 1)
        self.c.cairo_set_source(self.cr, grad)
        self.c.cairo_rectangle(self.cr, 0, 0, W, head)
        self.c.cairo_fill(self.cr)
        self.c.cairo_pattern_destroy(grad)

        # header: title + crumb, cabinet name right
        crumb = v.get("crumb") or {"home": "", "wifi": "SETTINGS", "network": "SETTINGS / WI-FI",
                                   "password": "SETTINGS / WI-FI"}.get(v["id"], "SETTINGS")
        if crumb:
            self.text(M, 74, crumb, "ui_bold", ts["label"], "muted")
        self.text(M, 136, self.fit(v["title"], "display", ts["title"], W - 2 * M - 300), "display", ts["title"], "gold")
        self.text(W - M, 74, "CABINET", "ui_bold", ts["small"], "muted", "r")
        self.text(W - M, 104, self.fit(v["hostname"], "ui_bold", ts["heading"] - 4, 300), "ui_bold", ts["heading"] - 4, "text", "r")
        self.rect(M, 162, W - 2 * M, b["border"]["rule"], "dim")
        self.rect(M, 162, 120, 4, "gold")

        if v["kind"] == "menu":
            self.draw_menu(v, M, 196)
        else:
            self.draw_keyboard(v, M, 196)

        # toast (result of the last action) just above the help bar
        if v.get("toast"):
            kind, msg = v["toast"]
            y = H - 172
            self.rrect(M, y, W - 2 * M, 60, b["radius"]["box"], fill="card", stroke="gold" if kind == "ok" else "red_light",
                       width=b["border"]["box"])
            self.text(M + 28, y + 40, self.fit(msg, "ui_bold", ts["body"] - 2, W - 2 * M - 56), "ui_bold", ts["body"] - 2,
                      "text" if kind == "ok" else "red_light")

        # help bar: primary (what the buttons do here) + secondary (exit), quieter
        y = H - 64
        self.rect(0, y - 34, W, 98, "card", 0.85)
        x = M
        for keys, word in v["hints"]:
            for i, k in enumerate(keys):
                if i:
                    x += self.text(x + 4, y + 9, "+", "ui_bold", ts["help"], "gold") + 8
                x += self.badge(x, y, k, 36) + 10
            x += self.text(x, y + 9, word, "ui_bold", ts["help"], "text") + 34
        if v.get("quit"):
            # #224: QUIT leaves the whole app from any page (SELECT), right-aligned at the primary size so it reads
            # as a real action, apart from MK BACK (one page up)
            keys, word = v["quit"]
            wx = W - M - self.width(word, "ui_bold", ts["help"])
            self.text(W - M, y + 9, word, "ui_bold", ts["help"], "gold", "r")
            bx = wx - 10
            for k in reversed(keys):
                bx -= self.badge_w(k, 36)
                self.badge(bx, y, k, 36)
                bx -= 10
        else:                                     # TROPHIES etc.: the quiet START+SELECT EXIT strip
            sec = b["help_bar"]["secondary"]
            word = "EXIT"
            wx = W - M - self.width(word, "ui_bold", sec["word_px"])
            self.text(W - M, y + 7, word, "ui_bold", sec["word_px"], sec["word_color"], "r")
            bx = wx - 10
            for i, k in enumerate(("SELECT", "START")):
                if i:
                    bx -= self.width("+", "ui_bold", sec["word_px"]) + 8
                    self.text(bx + 4, y + 7, "+", "ui_bold", sec["word_px"], sec["badge_color"])
                w = self.width(k, "ui_bold", 22 * 0.62) + 22 * 0.9
                bx -= w
                self.badge(bx, y, k, 22, fill=sec["badge_color"])
                bx -= 4
        self.scanlines()

        if v.get("busy"):
            self.rect(0, 0, W, H, "bg", 0.72)
            cw, ch = 860, 220
            x0, y0 = (W - cw) / 2, (H - ch) / 2
            self.rrect(x0, y0, cw, ch, b["radius"]["card"], fill="card", stroke="gold_card", width=b["border"]["card"])
            self.text(W / 2, y0 + 96, "PLEASE WAIT", "display", ts["heading"], "gold", "c")
            self.text(W / 2, y0 + 156, self.fit(v["busy"] + " …", "ui_bold", ts["body"], cw - 60), "ui_bold", ts["body"],
                      "text", "c")
        self.c.cairo_surface_flush(self.surf)

    def status_card(self, v, x, y, w):
        b, ts = self.b, self.b["type_scale"]
        st = v.get("status") or {}
        self.rrect(x, y, w, 96, b["radius"]["box"], fill="card", stroke="dim", width=b["border"]["box"])
        self.text(x + 28, y + 40, "WI-FI", "ui_bold", ts["small"], "muted")
        if st.get("ssid"):
            self.text(x + 28, y + 76, self.fit(st["ssid"], "ui_bold", ts["heading"], w * 0.42), "ui_bold", ts["heading"], "text")
            self.bars(x + w * 0.5, y + 46, st.get("signal", 0))
            self.text(x + w * 0.5 + 48, y + 72, "%d%%" % st.get("signal", 0), "ui_bold", ts["body"], "muted")
            self.text(x + w - 28, y + 40, "IP ADDRESS", "ui_bold", ts["small"], "muted", "r")
            self.text(x + w - 28, y + 76, st.get("ip") or "-", "ui_bold", ts["heading"], "gold", "r")
        else:
            self.text(x + 28, y + 76, (st.get("state") or "NOT CONNECTED").upper(), "ui_bold", ts["heading"], "salmon")

    def card(self, c, x, y, w):
        """One status card: caption + value left (gold when hot), optional caption + value right. -> next y."""
        b, ts = self.b, self.b["type_scale"]
        self.rrect(x, y, w, 96, b["radius"]["box"], fill="card", stroke="gold" if c.get("hot") else "dim",
                   width=b["border"]["box"])
        right = c.get("rvalue")
        self.text(x + 28, y + 40, c["caption"], "ui_bold", ts["small"], "muted")
        self.text(x + 28, y + 76, self.fit(c["value"], "ui_bold", ts["heading"], w * (0.55 if right else 0.9)), "ui_bold",
                  ts["heading"], "gold" if c.get("hot") else "text")
        if right:
            self.text(x + w - 28, y + 40, c.get("rcaption", ""), "ui_bold", ts["small"], "muted", "r")
            if "rhot" in c:
                self.text(x + w - 28, y + 76, right, "ui_bold", ts["heading"], "gold" if c["rhot"] else "text", "r")
            else:
                self.text(x + w - 28, y + 76, right, "ui_bold", ts["body"], "text_soft", "r")
        return y + 120

    def draw_menu(self, v, x, y):
        b, ts = self.b, self.b["type_scale"]
        w = W - 2 * x
        if v["id"] in ("home", "wifi"):
            self.status_card(v, x, y, w)
            y += 120
        if v["id"] == "home":
            self.text(x, y + 40, "CABINET NAME ON THE NETWORK:  %s.local" % v["hostname"], "ui_semi", ts["body"], "muted")
            y += 72
        for card in v.get("cards") or []:                      # a Page's status cards (settings_core.Page.cards)
            y = self.card(card, x, y, w)
        if v.get("text"):
            self.text(x, y + 40, self.fit(v["text"], "ui_semi", ts["body"], w), "ui_semi", ts["body"], "text_soft")
            y += 72
        if v["id"] == "network" and v.get("net"):
            n = v["net"]
            sec = n.security if n.secured else "OPEN"
            state = "CONNECTED" if n.in_use else ("SAVED" if n.uuid else "NOT SAVED")
            self.text(x, y + 40, "%s  -  %d%%  -  %s" % (sec, n.signal, state), "ui_semi", ts["body"], "muted")
            y += 72
        rh, avail = 66, (H - 190) - y
        n = len(v["items"])
        per = max(1, int(avail // rh))
        first = 0 if n <= per else min(max(0, v["sel"] - per // 2), n - per)
        for i in range(first, min(n, first + per)):
            ry = y + (i - first) * rh
            sel = i == v["sel"]
            net = v["nets"][i]
            if sel:
                self.rrect(x, ry + 4, w, rh - 8, b["radius"]["tag"], fill="gold")
            ink = "on_gold" if sel else "text"
            label = v["items"][i]
            px = ts["body"] + 2
            right = 0
            if net is not None:
                right = 230
                tag = "CONNECTED" if net.in_use else ("SAVED" if net.uuid else "")
                rx = x + w - 24
                self.text(rx, ry + 44, "%d%%" % net.signal, "ui_bold", ts["label"], ink if sel else "muted", "r")
                self.bars(rx - 104, ry + 18, net.signal, on="on_gold" if sel else "gold")
                if net.secured:
                    self.lock(rx - 138, ry + 18, ink if sel else "muted")
                if tag:
                    self.text(rx - 156, ry + 44, tag, "ui_bold", ts["small"], ink if sel else "gold", "r")
                    right += self.width(tag, "ui_bold", ts["small"]) + 20
            role = "ui_bold" if (net is None and label in ("BACK", "SCAN AGAIN")) else "ui_semi"
            self.text(x + 24, ry + 44, self.fit(label, role, px, w - 48 - right), role, px,
                      ink if sel else ("muted" if label == "BACK" else "text"))
        if n > per:
            self.text(x + w, H - 196, "%d / %d" % (v["sel"] + 1, n), "ui_bold", ts["small"], "muted", "r")

    def draw_keyboard(self, v, x, y):
        b, ts = self.b, self.b["type_scale"]
        w = W - 2 * x
        hint = v.get("hint") or {"password": "PASSWORD  -  ANY CHARACTER: SHIFT = CAPITALS, #+= = SYMBOLS",
                                 "hostname": "a-z, 0-9 AND -  (1-63, NOT AT THE START OR END)"}.get(v["mode"], "")
        self.text(x, y + 30, hint, "ui_bold", ts["small"], "muted")
        fy = y + 50
        self.rrect(x, fy, w, 92, b["radius"]["box"], fill="card", stroke="gold", width=b["border"]["box"])
        shown = v["field"]
        fx = x + 28
        tw = self.width(shown, "ui_semi", 44)
        if tw > w - 120:
            shown = "…" + shown[-int(len(shown) * (w - 140) / max(tw, 1)):]
        tw = self.text(fx, fy + 62, shown, "ui_semi", 44, "text")
        self.rect(fx + tw + 4, fy + 22, 4, 50, "gold")
        self.text(x + w - 24, fy + 58, "%d" % v["length"], "ui_bold", ts["small"], "muted", "r")
        if v.get("error"):
            self.text(x, fy + 132, v["error"], "ui_bold", ts["body"], "red_light")
        # key grid
        rows, (cr_, cc) = v["rows"], v["cur"]
        gy = fy + 156
        kh, gap = 72, 10
        for ri, row in enumerate(rows):
            ry = gy + ri * (kh + gap)
            units = sum(k.width for k in row)
            span = units if ri == len(rows) - 1 else 10          # character rows: 10 keys across; specials: full width
            uw = (w - gap * (span - 1)) / float(span)
            rw = units * uw + gap * (units - 1)
            kx = x + (w - rw) / 2.0
            for ci, k in enumerate(row):
                kw = k.width * uw + gap * (k.width - 1)
                sel = (ri, ci) == (cr_, cc)
                active = (k.action == "shift" and v["page"] == "ABC") or (k.action == "sym" and v["page"] == "#+=") or \
                         (k.action == "show" and v["mode"] == "password" and not v["field"].startswith(core.MASK) and v["length"])
                fill = "gold" if sel else ("panel" if k.action != "char" else "button")
                self.rrect(kx, ry, kw, kh, b["radius"]["tag"], fill=fill,
                           stroke=None if sel else ("gold_muted" if active else "dim"), width=2)
                lab = k.label
                if k.action == "show":
                    lab = "HIDE" if (v["length"] and not v["field"].startswith(core.MASK)) else "SHOW"
                if k.action == "char":
                    role, px = "ui_bold", 40
                else:
                    role, px = "ui_bold", ts["label"]
                self.text(kx + kw / 2.0, ry + kh / 2.0 + px * 0.36, lab, role, px, "on_gold" if sel else "chrome", "c")
                kx += kw + gap

    def veil(self, a):
        """#225: lay the theme bg over the current frame at alpha a (the quit fade; a = 1 -> plain bg)."""
        self.rect(0, 0, W, H, "bg", a)
        self.c.cairo_surface_flush(self.surf)

    def png(self, path):
        return self.c.cairo_surface_write_to_png(self.surf, path.encode()) == 0

    def pixels(self):
        return self.c.cairo_image_surface_get_data(self.surf), self.c.cairo_image_surface_get_stride(self.surf)


# ================================================================================================
# SDL2 (ctypes): window, game controllers, keyboard text input
# ================================================================================================
SDL_QUIT, SDL_WINDOWEVENT, SDL_KEYDOWN, SDL_TEXTINPUT = 0x100, 0x200, 0x300, 0x303
SDL_CAXIS, SDL_CBDOWN, SDL_CBUP, SDL_CADDED, SDL_CREMOVED = 0x650, 0x651, 0x652, 0x653, 0x654
BTN = {0: "ok", 1: "back", 6: "start", 4: "select", 11: "up", 12: "down", 13: "left", 14: "right"}   # A=LK B=MK
KEYS = {13: "enter", 0x40000058: "enter", 27: "esc", 8: "bksp", 9: "tab", 0x4000004F: "right", 0x40000050: "left",
        0x40000051: "down", 0x40000052: "up"}
AXIS_ON, REPEAT_FIRST, REPEAT_NEXT = 16000, 0.32, 0.085


class SDLRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int), ("w", ctypes.c_int), ("h", ctypes.c_int)]


class Input:
    """Pad + keyboard -> app actions. START fires on release unless SELECT was pressed during the hold
    (START+SELECT = exit, as in games), so pressing the combo never also means DONE.
    select_quits (#224, the SETTINGS app only): SELECT alone = exit on its release (QUIT in the help bar), unless it
    was part of START+SELECT (that already exited). TROPHIES and the boot menu keep SELECT for the combo only."""

    def __init__(self, select_quits=False):
        self.select_quits = select_quits
        self.held = set()
        self.combo = False
        self.dirs = {}                 # dir -> next repeat time
        self.axes = {}

    def button(self, name, down, now):
        out = []
        if name in ("up", "down", "left", "right"):
            return self.direction(name, down, now)
        if down:
            self.held.add(name)
            if {"start", "select"} <= self.held:
                self.combo = True
                return ["exit"]
            if name not in ("start", "select"):
                out.append(name)
        else:
            if name == "start" and "start" in self.held and not self.combo:   # its press may have been dropped (busy)
                out.append("start")
            if name == "select" and self.select_quits and "select" in self.held and not self.combo:
                out.append("exit")
            self.held.discard(name)
            if not ({"start", "select"} & self.held):
                self.combo = False
        return out

    def direction(self, d, down, now):
        if down:
            if d in self.dirs:
                return []
            self.dirs[d] = now + REPEAT_FIRST
            return [d]
        self.dirs.pop(d, None)
        return []

    def axis(self, axis, value, now):
        if axis not in (0, 1):
            return []
        neg, pos = ("left", "right") if axis == 0 else ("up", "down")
        want = neg if value < -AXIS_ON else (pos if value > AXIS_ON else None)
        old = self.axes.get(axis)
        if want == old:
            return []
        self.axes[axis] = want
        out = []
        if old:
            out += self.direction(old, False, now)
        if want:
            out += self.direction(want, True, now)
        return out

    def repeats(self, now):
        out = []
        for d, t in list(self.dirs.items()):
            if now >= t:
                out.append(d)
                self.dirs[d] = now + REPEAT_NEXT
        return out


class SDLFront:
    def __init__(self):
        if "WAYLAND_DISPLAY" in os.environ and "SDL_VIDEODRIVER" not in os.environ:
            os.environ["SDL_VIDEODRIVER"] = "wayland"
        s = self.s = _lib(["libSDL2-2.0.so.0", "/opt/homebrew/lib/libSDL2-2.0.0.dylib"])
        vp, i, u32 = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32
        sig = {"SDL_Init": (i, [u32]), "SDL_GetError": (ctypes.c_char_p, []), "SDL_Quit": (None, []),
               "SDL_CreateWindow": (vp, [ctypes.c_char_p, i, i, i, i, u32]),
               "SDL_CreateRenderer": (vp, [vp, i, u32]), "SDL_CreateTexture": (vp, [vp, u32, i, i, i]),
               "SDL_UpdateTexture": (i, [vp, vp, vp, i]), "SDL_RenderClear": (i, [vp]),
               "SDL_SetRenderDrawColor": (i, [vp, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8]),
               "SDL_RenderCopy": (i, [vp, vp, vp, vp]), "SDL_RenderPresent": (None, [vp]),
               "SDL_GetRendererOutputSize": (i, [vp, ctypes.POINTER(i), ctypes.POINTER(i)]),
               "SDL_WaitEventTimeout": (i, [vp, i]), "SDL_PollEvent": (i, [vp]),
               "SDL_StartTextInput": (None, []), "SDL_ShowCursor": (i, [i]),
               "SDL_NumJoysticks": (i, []), "SDL_IsGameController": (i, [i]), "SDL_GameControllerOpen": (vp, [i]),
               "SDL_FlushEvent": (None, [u32]), "SDL_FlushEvents": (None, [u32, u32]),
               "SDL_DestroyTexture": (None, [vp]), "SDL_DestroyRenderer": (None, [vp]), "SDL_DestroyWindow": (None, [vp])}
        for name, (res, args) in sig.items():
            fn = getattr(s, name)
            fn.restype, fn.argtypes = res, args
        if s.SDL_Init(0x20 | 0x2000 | 0x4000) != 0:                          # VIDEO | GAMECONTROLLER | EVENTS
            raise OSError("SDL_Init: %s" % s.SDL_GetError().decode(errors="replace"))
        self.win = s.SDL_CreateWindow(b"coin-dojo settings", 0, 0, W, H, 0x1001 | 0x4)   # FULLSCREEN_DESKTOP | SHOWN
        if not self.win:
            raise OSError("SDL_CreateWindow: %s" % s.SDL_GetError().decode(errors="replace"))
        self.ren = s.SDL_CreateRenderer(self.win, -1, 0) or s.SDL_CreateRenderer(self.win, -1, 1)
        self.tex = s.SDL_CreateTexture(self.ren, 0x16362004, 1, W, H)       # ARGB8888, streaming
        s.SDL_ShowCursor(0)
        s.SDL_StartTextInput()
        self.pads = {}
        for idx in range(s.SDL_NumJoysticks()):
            self.open_pad(idx)
        self.ev = (ctypes.c_uint64 * 8)()

    def open_pad(self, idx):
        if self.s.SDL_IsGameController(idx):
            p = self.s.SDL_GameControllerOpen(idx)
            if p:
                self.pads[idx] = p

    def present(self, painter):
        s = self.s
        data, stride = painter.pixels()
        s.SDL_UpdateTexture(self.tex, None, data, stride)
        ow, oh = ctypes.c_int(), ctypes.c_int()
        s.SDL_GetRendererOutputSize(self.ren, ctypes.byref(ow), ctypes.byref(oh))
        sc = min(ow.value / float(W), oh.value / float(H)) or 1.0
        dst = SDLRect(int((ow.value - W * sc) / 2), int((oh.value - H * sc) / 2), int(W * sc), int(H * sc))
        s.SDL_SetRenderDrawColor(self.ren, *painter.b.rgba("bezel"))        # the side bars the bezel hides
        s.SDL_RenderClear(self.ren)
        s.SDL_RenderCopy(self.ren, self.tex, None, ctypes.byref(dst))
        s.SDL_RenderPresent(self.ren)

    def events(self, inp, timeout_ms):
        """-> list of actions from the queued events (waits up to timeout_ms for the first)."""
        s, out, now = self.s, [], time.monotonic()
        got = s.SDL_WaitEventTimeout(self.ev, timeout_ms)
        while got:
            raw = bytes(self.ev)
            et = struct.unpack_from("<I", raw, 0)[0]
            if et == SDL_QUIT:
                out.append("exit")
            elif et == SDL_WINDOWEVENT:
                out.append("redraw")
            elif et == SDL_KEYDOWN:
                sym = struct.unpack_from("<i", raw, 20)[0] & 0xFFFFFFFF
                if sym in KEYS:
                    out.append(KEYS[sym])
            elif et == SDL_TEXTINPUT:
                txt = raw[12:44].split(b"\0", 1)[0].decode("utf-8", errors="replace")
                if txt:
                    out.append(("text", txt))
            elif et in (SDL_CBDOWN, SDL_CBUP):
                btn = raw[12]
                if btn in BTN:
                    out += inp.button(BTN[btn], et == SDL_CBDOWN, now)
            elif et == SDL_CAXIS:
                axis, value = raw[12], struct.unpack_from("<h", raw, 16)[0]
                out += inp.axis(axis, value, now)
            elif et == SDL_CADDED:
                self.open_pad(struct.unpack_from("<i", raw, 8)[0])           # hot-plugged pad
            got = s.SDL_PollEvent(self.ev)
        return out + inp.repeats(time.monotonic())

    def flush_presses(self):
        """Drop presses queued while a slow call ran (releases are kept, so held-state stays right)."""
        self.s.SDL_FlushEvents(SDL_KEYDOWN, SDL_TEXTINPUT)
        self.s.SDL_FlushEvent(SDL_CBDOWN)

    def close(self):
        s = self.s
        s.SDL_DestroyTexture(self.tex)
        s.SDL_DestroyRenderer(self.ren)
        s.SDL_DestroyWindow(self.win)
        s.SDL_Quit()


# #225: on quit the last frame fades to the theme bg. ES-DE's frame under the app (the SETTINGS gamelist) is that same
# plain bg (themes/.../_inc/per-system/settings.xml), so closing the window shows no stale list before the carousel.
FADE_STEPS, FADE_STEP_S = 6, 0.025            # 6 veils at 0.35 -> 7 % of the frame left, then plain bg (~0.15 s)


def fade(front, painter, steps=FADE_STEPS, step_s=FADE_STEP_S, sleep=time.sleep):
    for _ in range(steps):
        painter.veil(0.35)
        front.present(painter)
        sleep(step_s)
    painter.veil(1.0)
    front.present(painter)


# ================================================================================================
# main
# ================================================================================================
def load_config(argv_entry):
    """The .hks entry file ES-DE passes is JSON (written by the settings role); missing/empty = defaults."""
    cfg = {}
    if argv_entry and os.path.isfile(argv_entry):
        try:
            with open(argv_entry, encoding="utf-8") as f:
                txt = f.read().strip()
            cfg = json.loads(txt) if txt else {}
        except (OSError, ValueError):
            cfg = {}
    return cfg if isinstance(cfg, dict) else {}


def parse_args(argv):
    opts = dict(entry=None, png=None, actions=[], nmcli=None, helper=None, pwhelper=None, font_dir=None, hostname=None, timedatectl=None, test_hooks=False)
    a = list(argv)
    while a:
        x = a.pop(0)
        if x == "--test-hooks":
            opts["test_hooks"] = True
        elif x in ("--png", "--actions", "--nmcli", "--hostname-helper", "--password-helper", "--font-dir", "--hostname", "--timedatectl"):
            if not a:
                raise SystemExit("usage: %s needs a value" % x)
            val = a.pop(0)
            key = {"--hostname-helper": "helper", "--password-helper": "pwhelper", "--font-dir": "font_dir"}.get(x, x[2:])
            opts[key] = [s for s in val.split(",") if s] if key == "actions" else val
        elif x.startswith("-"):
            raise SystemExit("usage: settings_app.py [ENTRY.hks] [--png OUT] [--actions a,b] [--nmcli P] "
                             "[--hostname-helper P] [--password-helper P] [--font-dir D] [--hostname N] [--timedatectl P] [--test-hooks]")
        else:
            opts["entry"] = x
    return opts


def script_action(s):
    """--actions item: up/down/.../exit, text:<chars> (USB keyboard typing; %2C = ',')."""
    if s.startswith("text:"):
        return ("text", s[5:].replace("%2C", ","))
    return s


def build(opts):
    cfg = load_config(opts["entry"])
    font_dir = opts["font_dir"] or cfg.get("font_dir")
    if not font_dir and os.path.isdir(os.path.join(HERE, "fonts")):
        font_dir = os.path.join(HERE, "fonts")
    b = brand.load(font_dir=font_dir)
    log = core.Log(cfg.get("log", core.LOG_PATH))
    # TEST ONLY (#198): --test-hooks (tests, previews; never in ES-DE's command, never in the role's entry file) lets
    # the entry file swap the process table (game_proc: macOS has no /proc) and send the login to the loopback fake_ra.
    test = bool(opts.get("test_hooks"))
    busy = core.game_busy(cfg.get("game_procs", ("retroarch", "flycast")), cfg.get("game_patterns", ()),
                          cfg.get("game_proc", "/proc") if test else "/proc")
    state = cfg.get("benchmark_state", core.BENCH_STATE)
    bench = core.Benchmark(state, cfg.get("benchmark_keys", core.BENCH_KEYS), busy, log=log)
    files = cfg.get("cheevos_files")
    pages = [core.CheevosPage(core.Cheevos(files if isinstance(files, dict) else None, state,
                                           cfg.get("cheevos_api") or core.CHEEVOS_API, busy, log=log,
                                           allow_loopback=test)),
             core.TimeZonePage(core.TimeZone(log=log, state=state, tool=opts["timedatectl"] or cfg.get("timedatectl", "timedatectl"))),
             core.PasswordPage(core.Password(opts["pwhelper"] or cfg.get("password_helper", core.PASSWORD_HELPER), log=log)),
             core.ListsPage(core.Lists(opts["entry"], state, log=log)),
             core.AttractPage(core.Attract(opts["entry"], state, log=log))]
    nm = core.NM(opts["nmcli"] or cfg.get("nmcli", "nmcli"), log=log)
    host = core.Hostname(opts["helper"] or cfg.get("hostname_helper", core.HOSTNAME_HELPER), log=log)
    if opts["hostname"]:
        host.current = lambda: opts["hostname"]
    return b, core.App(nm, host, log, bench, pages)


def main(argv=None):
    opts = parse_args(sys.argv[1:] if argv is None else argv)
    b, app = build(opts)
    app.log.write("start", entry=opts["entry"] or "")
    painter = Painter(b)
    if opts["png"]:
        app.run_pending()
        for a in opts["actions"]:
            app.handle(script_action(a))
            app.run_pending()
        painter.draw(app.view())
        return 0 if painter.png(opts["png"]) else 1
    try:
        front = SDLFront()
    except OSError as e:
        print("settings: no display: %s" % e, file=sys.stderr)
        return 3
    inp = Input(select_quits=True)
    pending_script = [script_action(a) for a in opts["actions"]]
    dirty = True
    try:
        while not app.quit:
            if app.pending:
                painter.draw(app.view())
                front.present(painter)
                app.run_pending()
                front.flush_presses()
                dirty = True
            if pending_script:
                app.handle(pending_script.pop(0))
                dirty = True
                continue
            if dirty:
                painter.draw(app.view())
                front.present(painter)
                dirty = False
            acts = front.events(inp, 30 if inp.dirs else 250)
            for a in acts:
                if a == "redraw":
                    dirty = True
                    continue
                app.handle(a)
                dirty = True
                if app.quit or app.pending:
                    break
        fade(front, painter)                      # #225: only on a real quit (an error skips it)
    finally:
        app.log.write("stop")
        front.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
