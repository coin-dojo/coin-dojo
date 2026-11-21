#!/usr/bin/env python3
"""Contrast guard for the Coin Dojo palette (#43). Pure stdlib; pytest-compatible or run directly:

    python3 themes/tests/test_palette_contrast.py

Every text colour the gamelist draws on its panel must reach WCAG contrast >= 4.5:1, in BOTH colour schemes,
in the WORST case: the panel PNG (its lowest-alpha pixel, times the scheme's panel tint) composited over pure
white, i.e. over any backdrop art however bright. The footer help text is checked the same way against the
footer scrim. The selected row (listSelected on listSelector) is checked directly.
The BROKEN case (must fail): a low-contrast pair and a colors.xml with a muted list colour are rejected.
"""
import os
import struct
import sys
import xml.etree.ElementTree as ET
import zlib

THEME = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "coin-dojo")
ART = os.path.join(THEME, "_inc", "art")
MIN = 4.5
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tests", "lib"))
from hktest import check, main  # noqa: E402  (shared helper; SKIPs counted in RESULT, #152)


def rgb(h):
    h = h.strip()
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def lum(c):
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def over(fg, alpha, bg):
    return tuple(round(fg[i] * alpha + bg[i] * (1 - alpha)) for i in range(3))


def read_png_rgba(path):
    """Minimal 8-bit RGBA, non-interlaced PNG reader -> (w, h, rows of bytes)."""
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w = 8, b"", None
    while pos < len(data):
        n, typ = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if typ == b"IHDR":
            w, h, depth, ctype, _, _, inter = struct.unpack(">IIBBBBB", body)
            assert (depth, ctype, inter) == (8, 6, 0), "need 8-bit RGBA non-interlaced: %s" % path
        elif typ == b"IDAT":
            idat += body
        pos += 12 + n
    raw, bpp, stride = zlib.decompress(idat), 4, w * 4
    rows, prev = [], bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        cur = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = cur[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                cur[i] = (cur[i] + a) & 255
            elif f == 2:
                cur[i] = (cur[i] + b) & 255
            elif f == 3:
                cur[i] = (cur[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                cur[i] = (cur[i] + pr) & 255
        rows.append(cur)
        prev = cur
    return w, h, rows


def panel_worst_case(png, tint):
    """Panel colour as seen over pure white at its most transparent interior pixel, with the scheme tint applied."""
    w, h, rows = read_png_rgba(os.path.join(ART, png))
    worst = None
    for y in range(h // 10, h - h // 10, max(1, h // 40)):
        for x in range(w // 4, w - w // 4, max(1, w // 8)):
            r, g, b, a = rows[y][x * 4:x * 4 + 4]
            col = tuple(round(c * t / 255) for c, t in zip((r, g, b), tint))
            seen = over(col, a / 255.0, (255, 255, 255))
            if worst is None or lum(seen) > lum(worst):
                worst = seen
    return worst


def schemes(colors_xml_text):
    out = {}
    for cs in ET.fromstring(colors_xml_text).iter("colorScheme"):
        out[cs.get("name")] = {v.tag: (v.text or "").strip() for v in cs.find("variables")}
    return out


def evaluate(scheme):
    """-> list of (label, ratio) for the pairs that must reach MIN."""
    tint = rgb(scheme["panelTint"][:6])
    red_tint = rgb(scheme["panelRedTint"][:6])
    left = panel_worst_case("panel.png", tint)
    right = panel_worst_case("panel-red.png", red_tint)
    scrim_w, scrim_h, scrim_rows = read_png_rgba(os.path.join(ART, "scrim.png"))
    sr, sg, sb, sa = scrim_rows[scrim_h - 1][0:4]          # bottom row: the footer
    foot = over((sr, sg, sb), sa / 255.0, (255, 255, 255))
    pairs = []
    for k in ("listPrimary", "listSecondary"):
        pairs.append(("%s on left panel" % k, contrast(rgb(scheme[k][:6]), left)))
    for k in ("valueColor", "descColor", "labelColor", "ratingColor", "gamelistInfoColor", "badgeColor"):
        pairs.append(("%s on right panel" % k, contrast(rgb(scheme[k][:6]), right)))
    pairs.append(("listSelected on listSelector", contrast(rgb(scheme["listSelected"][:6]), rgb(scheme["listSelector"][:6]))))
    for k in ("helpColor", "textMuted"):
        pairs.append(("%s on footer scrim" % k, contrast(rgb(scheme[k][:6]), foot)))
    return pairs


def test_contrast_helper_math():
    check(abs(contrast((0, 0, 0), (255, 255, 255)) - 21.0) < 1e-6, "black/white contrast is 21:1")
    check(contrast((119, 119, 119), (136, 136, 136)) < 1.5, "similar greys are nowhere near 4.5:1")


def test_all_schemes_pass():
    text = open(os.path.join(THEME, "colors.xml")).read()
    sch = schemes(text)
    check({"indigo", "crimson"} <= set(sch), "both colour schemes present")
    for name, vars_ in sorted(sch.items()):
        for label, ratio in evaluate(vars_):
            check(ratio >= MIN, "%s: %s = %.1f:1 (>= %.1f)" % (name, label, ratio, MIN))


def test_BROKEN_low_contrast_is_rejected():
    """Must FAIL the guard: a muted-lavender-on-violet list colour (what the old 5A6A92 help colour looked like)."""
    text = open(os.path.join(THEME, "colors.xml")).read()
    bad = text.replace("<listPrimary>FFF4DC</listPrimary>", "<listPrimary>3A3260</listPrimary>", 1)
    check(bad != text, "broken fixture built")
    failed = [lab for lab, r in evaluate(schemes(bad)["indigo"]) if r < MIN]
    check("listPrimary on left panel" in failed, "BROKEN CASE: dim listPrimary flagged as < 4.5:1")


if __name__ == "__main__":
    main(globals())
