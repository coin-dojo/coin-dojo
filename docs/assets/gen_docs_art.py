#!/usr/bin/env python3
"""Render the README/BUILD art from the style kit (#238): banners (PNG) and shields-style badges (SVG).

    ~/.venvs/badges/bin/python docs/assets/gen_docs_art.py [OUT_DIR]     (default: this folder)
    python3 docs/assets/gen_docs_art.py --badges-only [OUT_DIR]          (stdlib: the SVG badges only)

Colours and fonts come from brand/tokens.json through brand/brand.py (brand/BRAND.md rule 1: tokens, never
literals). Forgejo and GitHub strip inline CSS from markdown, so the themed warning and AI note are images. The
words in them live ONCE, in TEXT below: docs/tests/test_customer_docs.py checks that each banner's alt text and the
plain-markdown copy under it (README.md, docs/BUILD.md) say exactly the same thing.

PNG banners need Pillow (the ~/.venvs/badges venv, skills/badges/Makefile `venv`). The SVG badges are plain text
and byte-reproducible with stdlib only. No game art, no characters, no project name inside the pictures except
the header's project logo (brand/logo.png), so the export ships them unchanged.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "brand"))
import brand  # noqa: E402

# The words. Changing one means changing README.md / docs/BUILD.md too (the docs test fails until they agree).
TEXT = {
    "warning_label": "WARNING",
    "warning": ("This is not production software. Try it on your own, on a dedicated SD card you don't need for "
                "anything else. Back up first. There is no warranty: see the LICENSE."),
    "ai_label": "A NOTE ON AI",
    "ai": ("Yes, I used AI to help build this. The responsibility is mine: at the end of the day I'm responsible "
           "for the code I ship."),
    "header_sub": "DOWNLOAD THE IMAGE OR BUILD IT YOURSELF  -  BRING YOUR OWN GAMES",
}

# label, value, value colour token: shields-style, local files, no shields.io call.
BADGES = {
    "badge-license.svg": ("licence", "MIT + CC BY-SA 4.0", "gold"),
    "badge-platform.svg": ("platform", "Raspberry Pi 5", "lavender"),
    "badge-build.svg": ("image", "download or build", "salmon"),
    "badge-kofi.svg": ("ko-fi", "buy me a coffee", "gold"),
}

WIDTH = 1280  # banner width in px; the README shows them at 100 %
HEADER_LOGO_W = 660  # the logo lockup in the header (>= 320 px: brand.LOGO_LOCKUP_MIN)


# ---------------------------------------------------------------- SVG badges (stdlib)
def _text_px(s, size=11):
    """Rough Verdana advance at `size` px: enough to size a badge, the viewer draws the real font."""
    wide = set("MWmw@%")
    narrow = set("iljtf.,:;!|' ()-+")
    w = 0.0
    for c in s:
        w += 0.95 if c in wide else 0.38 if c in narrow else 0.62
    return int(math.ceil(w * size))


def badge_svg(b, label, value, colour):
    pad = 8
    lw, vw = _text_px(label) + 2 * pad, _text_px(value) + 2 * pad
    w, h = lw + vw, 20
    left, right = b.hex("button"), b.hex(colour)
    ltext, vtext = b.hex("chrome"), b.hex("on_gold")
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" role="img" aria-label="%s: %s">\n'
        '  <title>%s: %s</title>\n'
        '  <clipPath id="r"><rect width="%d" height="%d" rx="4"/></clipPath>\n'
        '  <g clip-path="url(#r)">\n'
        '    <rect width="%d" height="%d" fill="#%s"/>\n'
        '    <rect x="%d" width="%d" height="%d" fill="#%s"/>\n'
        '  </g>\n'
        '  <g font-family="Verdana,DejaVu Sans,Geneva,sans-serif" font-size="11" text-anchor="middle">\n'
        '    <text x="%d" y="14" fill="#%s">%s</text>\n'
        '    <text x="%d" y="14" fill="#%s" font-weight="bold">%s</text>\n'
        '  </g>\n'
        '</svg>\n'
    ) % (w, h, esc(label), esc(value), esc(label), esc(value), w, h, lw, h, left, lw, vw, h, right,
         lw // 2, ltext, esc(label), lw + vw // 2, vtext, esc(value))


def write_badges(b, out):
    for name, (label, value, colour) in sorted(BADGES.items()):
        with open(os.path.join(out, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(badge_svg(b, label, value, colour))
        print("wrote %s" % os.path.join(out, name))


# ---------------------------------------------------------------- PNG banners (Pillow)
def _wrap(draw, text, font, width):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def note_banner(b, label, body, accent, label_text):
    """A card with an accent stripe, a pill label and wrapped body text (the warning and the AI note)."""
    from PIL import Image, ImageDraw, ImageFont
    pad, stripe = b["spacing"]["xl"], b["spacing"]["m"]
    lab_font = ImageFont.truetype(b.font("ui_bold"), b["type_scale"]["label"])
    body_font = ImageFont.truetype(b.font("ui_semi"), b["type_scale"]["body"] + 4)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    text_x = stripe + pad
    lines = _wrap(probe, body, body_font, WIDTH - text_x - pad)
    line_h = int((b["type_scale"]["body"] + 4) * 1.35)
    pill_h = b["type_scale"]["label"] + 2 * b["spacing"]["s"]
    height = pad + pill_h + b["spacing"]["l"] + line_h * len(lines) + pad
    img = Image.new("RGBA", (WIDTH, height), (0, 0, 0, 0))  # transparent corners: clean on light and dark pages
    d = ImageDraw.Draw(img)
    r = b["radius"]["card"]
    d.rounded_rectangle([0, 0, WIDTH - 1, height - 1], radius=r, fill=b.rgb("card"), outline=b.rgb(accent), width=3)
    d.rounded_rectangle([0, 0, stripe + r, height - 1], radius=r, fill=b.rgb(accent))
    d.rectangle([stripe, 3, stripe + r, height - 4], fill=b.rgb("card"))
    pw = int(d.textlength(label, font=lab_font)) + 2 * b["spacing"]["m"]
    d.rounded_rectangle([text_x, pad, text_x + pw, pad + pill_h], radius=pill_h // 2, fill=b.rgb(accent))
    d.text((text_x + b["spacing"]["m"], pad + pill_h // 2), label, font=lab_font, fill=b.rgb(label_text), anchor="lm")
    y = pad + pill_h + b["spacing"]["l"]
    for line in lines:
        d.text((text_x, y), line, font=body_font, fill=b.rgb("text"))
        y += line_h
    return img


def header_banner(b):
    """Night sky over a dojo floor: a moon, a perspective grid, the logo. Shapes only, no characters or game art."""
    from PIL import Image, ImageDraw, ImageFont
    h = 360
    img = Image.new("RGB", (WIDTH, h), b.rgb("bg"))
    d = ImageDraw.Draw(img)
    top, bottom = b.rgb("bg_deep"), b.rgb("panel")
    horizon = 236
    for y in range(horizon):  # sky: deep -> panel
        t = y / float(horizon)
        d.line([(0, y), (WIDTH, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    d.rectangle([0, horizon, WIDTH, h], fill=b.rgb("card"))
    dim, gold = b.rgb("dim"), b.rgb("gold_muted")
    cx = WIDTH // 2
    for i in range(-14, 15):  # floor boards converge on the vanishing point
        d.line([(cx + i * 22, horizon), (cx + i * 160, h)], fill=dim, width=2)
    y, step = horizon, 6.0
    while y < h:  # board joints, closer together near the horizon
        d.line([(0, int(y)), (WIDTH, int(y))], fill=dim, width=1)
        step *= 1.32
        y += step
    d.line([(0, horizon), (WIDTH, horizon)], fill=gold, width=3)
    mx, my, mr = WIDTH - 200, 112, 58  # the moon and a soft halo
    for k, a in ((mr + 36, 0.10), (mr + 18, 0.22)):
        halo = tuple(int(bottom[i] + (b.rgb("text")[i] - bottom[i]) * a) for i in range(3))
        d.ellipse([mx - k, my - k, mx + k, my + k], fill=halo)
    d.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=b.rgb("text"))
    # the project logo (#258/#289): the lockup from brand.logo_image, the one place pages get it (BRAND.md "Logo")
    logo = brand.logo_image(HEADER_LOGO_W)
    canvas = img.convert("RGBA")
    canvas.alpha_composite(logo, (b["spacing"]["l"], 4))
    img = canvas.convert("RGB")
    d = ImageDraw.Draw(img)
    sub = ImageFont.truetype(b.font("ui_bold"), b["type_scale"]["label"])
    tx, ty = b["spacing"]["xxl"], 296
    l, t, r, bt = d.textbbox((tx, ty), TEXT["header_sub"], font=sub)
    pad = b["spacing"]["s"]
    d.rounded_rectangle([l - 2 * pad, t - pad, r + 2 * pad, bt + pad], radius=b["radius"]["tag"], fill=b.rgb("card"),
                        outline=b.rgb("dim"), width=b["border"]["rule"])  # calm panel over the floor boards
    d.text((tx, ty), TEXT["header_sub"], font=sub, fill=b.rgb("muted"))
    return img



def write_banners(b, out):
    imgs = {
        "header.png": header_banner(b),
        "warning.png": note_banner(b, TEXT["warning_label"], TEXT["warning"], "red_light", "bg"),
        "ai-note.png": note_banner(b, TEXT["ai_label"], TEXT["ai"], "gold", "on_gold"),
    }
    for name, img in sorted(imgs.items()):
        path = os.path.join(out, name)
        img.save(path, optimize=True)
        print("wrote %s (%dx%d)" % (path, img.width, img.height))


def main(argv):
    badges_only = "--badges-only" in argv
    args = [a for a in argv if not a.startswith("--")]
    out = args[0] if args else HERE
    os.makedirs(out, exist_ok=True)
    b = brand.load()
    write_badges(b, out)
    if not badges_only:
        try:
            import PIL  # noqa: F401
        except ImportError:
            print("gen_docs_art: Pillow missing: run with ~/.venvs/badges/bin/python (make -C skills/badges venv)",
                  file=sys.stderr)
            return 2
        write_banners(b, out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
