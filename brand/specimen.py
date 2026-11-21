#!/usr/bin/env python3
"""Render brand/specimen.png: one sheet of the style kit (colours with roles, fonts, type scale, badge pills,
card, help-bar hierarchy, contrast pairs), drawn only from brand/tokens.json. Original art (CC BY-SA 4.0).

Usage: specimen.py [--name NAME] [OUT.png]   (default brand/specimen.png). Needs Pillow (~/.venvs/badges,
skills/badges/SKILL.md). --name titles the sheet "NAME" / "NAME STYLE KIT" instead of the default title and adds the
project logo's token top right (brand.logo_image, #258). The Night
Dojo sheet: `specimen.py --name "Coin Dojo" brand/specimen.png` (#235).
Deterministic: same tokens + same fonts => same bytes. Exit 0 ok, 2 no Pillow / bad usage.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brand  # noqa: E402

W, H = 1440, 1680          # safe-area width; taller than one screen (it is a sheet)
LOGO_ICON = 112            # px: the token on the --name sheet (title row; the lockup needs 320, brand.logo_file)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    name = None
    if argv[:1] == ["--name"]:
        if len(argv) < 2 or not argv[1].strip() or argv[1].startswith("-"):
            print("usage: specimen.py [--name NAME] [OUT.png]", file=sys.stderr)
            return 2
        name, argv = argv[1].strip().upper(), argv[2:]
    if len(argv) > 1 or (argv and argv[0].startswith("-")):
        print("usage: specimen.py [--name NAME] [OUT.png]", file=sys.stderr)
        return 2
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("error: Pillow missing; use ~/.venvs/badges (skills/badges/SKILL.md)", file=sys.stderr)
        return 2
    out = argv[0] if argv else os.path.join(HERE, "specimen.png")
    b = brand.load()
    ts, sp, rad, bd = b["type_scale"], b["spacing"], b["radius"], b["border"]

    def F(role, px):
        return ImageFont.truetype(b.font(role), px)

    im = Image.new("RGBA", (W, H), b.rgba("bg"))
    d = ImageDraw.Draw(im)
    m = sp["xl"]
    title = name or "COIN DOJO"
    kit = (name + " STYLE KIT") if name else "COIN DOJO STYLE KIT"
    d.text((m, m), title, font=F("display", ts["title"]), fill=b.rgba("gold"))
    if name:  # the community sheet carries the project logo (#258): the token, top right (no room for the lockup)
        icon = brand.logo_image(LOGO_ICON)
        im.alpha_composite(icon, (W - m - icon.width, m - 8))
    d.text((m, m + 64), kit + "  -  BRAND/TOKENS.JSON  -  1440 X 1080 SAFE AREA", font=F("ui_bold", ts["label"]),
           fill=b.rgba("muted"))

    # colour swatches: 4 columns
    names = list(b["colors"])
    cw, chh, y0 = (W - 2 * m - 3 * sp["m"]) // 4, 58, m + 120
    for i, n in enumerate(names):
        x = m + (i % 4) * (cw + sp["m"])
        y = y0 + (i // 4) * (chh + sp["s"])
        d.rounded_rectangle((x, y, x + 52, y + chh - 6), radius=rad["tag"], fill=b.rgba(n), outline=b.rgba("dim"), width=bd["rule"])
        d.text((x + 64, y + 2), n.upper(), font=F("ui_bold", ts["small"]), fill=b.rgba("text"))
        d.text((x + 64, y + 26), "#" + b.hex(n), font=F("ui_semi", ts["small"]), fill=b.rgba("muted"))
    y = y0 + ((len(names) + 3) // 4) * (chh + sp["s"]) + sp["l"]

    # fonts + type scale
    d.line((m, y, W - m, y), fill=b.rgba("dim"), width=bd["rule"])
    y += sp["m"]
    x = m
    for role in ("display", "ui_bold", "ui_semi", "ui_medium", "pixel"):
        px = ts["heading"] if role != "pixel" else ts["small"]
        d.text((x, y), b["fonts"][role]["family"].upper(), font=F(role, px), fill=b.rgba("text"))
        d.text((x, y + 44), role.upper() + "  " + b["fonts"][role]["file"], font=F("ui_semi", ts["small"]), fill=b.rgba("muted"))
        y += 78
    sx, sy = 760, y - 5 * 78 + 4
    for k in ("title", "heading", "body", "label", "help", "small", "hotkey"):
        d.text((sx, sy), "%s %d PX" % (k.upper(), ts[k]), font=F("ui_bold", ts[k]), fill=b.rgba("text"))
        sy += ts[k] + 12

    # badge pills (gen_help.py spec: white mask tinted gold, label knocked out -> drawn here as gold + bg text)
    y += sp["s"]
    d.line((m, y, W - m, y), fill=b.rgba("dim"), width=bd["rule"])
    y += sp["m"]
    bp = b["badge_pill"]
    bh = 48
    x = m
    for lab in bp["round_labels"] + bp["pill_labels"]:
        rnd = lab in bp["round_labels"]
        f = F(bp["round_font"] if rnd else bp["pill_font"], int(bh * (0.5 if rnd else 0.55)))
        tw = d.textlength(lab, font=f)
        w = int(bh * bp["round_width"] / bp["height"]) if rnd else int(tw + bh * bp["pill_pad"] / bp["height"])
        d.rounded_rectangle((x, y, x + w, y + bh), radius=bh // 2, fill=b.rgba(bp["tint"]))
        d.text((x + w / 2, y + bh / 2), lab, font=f, fill=b.rgba("bg"), anchor="mm")
        x += w + sp["s"]
    d.text((x + sp["m"], y + bh / 2), "PRIMARY: HELP BAR, %d PX, GOLD" % b["help_bar"]["primary"]["font_px"],
           font=F("ui_bold", ts["small"]), fill=b.rgba("text"), anchor="lm")
    y += bh + sp["m"]
    sec = b["help_bar"]["secondary"]
    x = m
    for lab in ("START", "SELECT"):
        f = F(bp["pill_font"], int(sec["badge_px"] * 1.3))
        w = int(d.textlength(lab, font=f) + 18)
        d.rounded_rectangle((x, y, x + w, y + sec["badge_px"] + 6), radius=(sec["badge_px"] + 6) // 2, fill=b.rgba(sec["badge_color"]))
        d.text((x + w / 2, y + (sec["badge_px"] + 6) / 2), lab, font=f, fill=b.rgba("bg"), anchor="mm")
        x += w + 6
    d.text((x + 8, y + 10), "EXIT GAME     SECONDARY: HOTKEY STRIP, %d PX, MUTED" % sec["word_px"],
           font=F("ui_bold", sec["word_px"]), fill=b.rgba(sec["word_color"]), anchor="lm")

    # card (controls.py spec, half size) + contrast table
    y += 48
    c = b["card"]
    cw2, ch2 = c["size"][0] // 2 + 60, c["size"][1] // 2 + 40
    for i, tested in enumerate((True, False)):
        x0 = m + i * (cw2 + sp["l"])
        d.rounded_rectangle((x0, y, x0 + cw2, y + ch2), radius=c["radius"], fill=b.rgba(c["fill"]),
                            outline=b.rgba(c["border_tested"] if tested else c["border_pending"]), width=c["border"])
        d.text((x0 + 20, y + 16), "6-BUTTON" if tested else "NES", font=F(c["title_font"], c["title_px"] - 4), fill=b.rgba("gold_card"))
        if not tested:
            f = F(c["tag_font"], ts["small"])
            tw = d.textlength(c["tag_text"], font=f)
            d.rounded_rectangle((x0 + cw2 - 30 - tw, y + 16, x0 + cw2 - 14, y + 44), radius=c["tag_radius"], fill=b.rgba(c["tag_fill"]))
            d.text((x0 + cw2 - 22 - tw / 2, y + 30), c["tag_text"], font=f, fill=b.rgba(c["tag_text_color"]), anchor="mm")
        for j, lab in enumerate(("LP", "MP", "HP", "LK", "MK", "HK")):
            cx, cy, r = x0 + 44 + (j % 3) * 76, y + 96 + (j // 3) * 76, 30
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=b.rgba("button"),
                      outline=b.rgba(c["border_tested"] if tested else c["border_pending"]), width=bd["pill"])
            d.text((cx, cy), lab, font=F("display", 20), fill=b.rgba("chrome"), anchor="mm")
        px = x0 + cw2 - c["pill_width"] - 20
        for k, lab in enumerate(("START", "SELECT")):
            py = y + 80 + k * 70
            d.rounded_rectangle((px, py, px + c["pill_width"], py + 44), radius=22, fill=b.rgba(c["pill_fill"]),
                                outline=b.rgba(c["pill_outline"]), width=bd["pill"])
            d.text((px + c["pill_width"] / 2, py + 22), lab, font=F("ui_bold", 24), fill=b.rgba(c["pill_text"]), anchor="mm")
    tx = m + 2 * (cw2 + sp["l"])
    ty = y - 6
    mn = float(b["contrast"]["min"])
    d.text((tx, ty), "CONTRAST >= %.1f:1" % mn, font=F("ui_bold", ts["small"]), fill=b.rgba("gold"))
    ty += 26
    for fg, bg, r in b.contrast_table():
        d.rectangle((tx, ty + 2, tx + 18, ty + 18), fill=b.rgba(bg))
        d.text((tx + 4, ty + 10), "A", font=F("ui_bold", 16), fill=b.rgba(fg), anchor="lm")
        d.text((tx + 26, ty + 10), "%s / %s  %.1f" % (fg.upper(), bg.upper(), r), font=F("ui_semi", 17),
               fill=b.rgba("text" if r >= mn else "red_light"), anchor="lm")
        ty += 20
    im.convert("RGB").save(out)
    print("wrote %s %dx%d" % (out, W, H))
    return 0


if __name__ == "__main__":
    sys.exit(main())
