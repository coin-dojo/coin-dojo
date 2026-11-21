#!/usr/bin/env python3
"""Generate the help-bar button icons (PHYSICAL panel names) for Coin Dojo.

ES-DE's help bar shows Xbox-style icons; the cabinet has an arcade panel, so each icon is
replaced via <customButtonIcon> with a white badge whose label is knocked out (transparent
letters). The helpsystem tints icons with iconColor, so they render as gold badges with
readable letters. One file per button (ES-DE shows a given file only once).

Mapping: the truth is SDL_GAMECONTROLLERCONFIG in the cabinet's ~/.bash_profile (ES-DE does NOT read
~/ES-DE/settings/es_controllers.txt): a:b3 b:b2 x:b0 y:b4 leftshoulder:b5 rightshoulder:b1 back:b8 start:b9; panel
by joystick index MP=0 HK=1 MK=2 LK=3 LP=4 HP=5. So (XBOX and XBOX360 alike):
  a=LK b=MK x=MP y=LP, l=HP, r=HK, lr=HP+HK, back=SELECT, start=START, dpad=stick.
Also writes hotkeys.png: the START+SELECT / START+MP / START+HP hint strip (same badges, Rajdhani words), the
small, muted secondary row above the help bar (HK_* constants; see themes/tests/test_theme_xml.py).
Writes _inc/help/<button>.png, 128 px tall. Original art (CC BY-SA 4.0), Archivo Black (OFL).
Deterministic. Usage: gen_help.py [THEME_DIR]. Needs Pillow (venv, see SKILL.md). Exit 0/2/1.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_THEME = os.path.normpath(os.path.join(HERE, "..", "..", "themes", "coin-dojo"))
# Colours, fonts and the badge spec come from the style kit (brand/tokens.json, brand/BRAND.md).
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "brand")))
import brand as _brand  # noqa: E402
BRAND = _brand.load()
PILLSPEC = BRAND["badge_pill"]
H, S = PILLSPEC["height"], PILLSPEC["supersample"]
DISPLAY_TTF = BRAND["fonts"][PILLSPEC["round_font"]]["file"]      # ArchivoBlack-Regular.ttf
PILL_TTF = BRAND["fonts"][PILLSPEC["pill_font"]]["file"]          # Rajdhani-Bold.ttf

BASE = {"button_a": "LK", "button_b": "MK", "button_x": "MP", "button_y": "LP"}
ROUND = {"button_l": "HP", "button_r": "HK"}
for _k, _v in BASE.items():
    for _sfx in ("_XBOX", "_XBOX360"):
        ROUND[_k + _sfx] = _v
PILL = {"button_lr": "HP+HK", "button_back_XBOX": "SELECT", "button_start_XBOX": "START",
        "button_back_XBOX360": "SELECT", "button_start_XBOX360": "START"}
STICK = ["dpad_updown", "dpad_leftright", "dpad_all"]
ALL = sorted(list(ROUND) + list(PILL) + STICK)
GOLD, LAV = BRAND.rgba("gold"), BRAND.rgba("lavender")
HOTKEYS = [(["START", "SELECT"], "Exit game"), (["START", "MP"], "Emulator menu"),
           (["START", "HP"], "Controls & pause")]
# No "RetroArch menu: not on Dreamcast" note line any more (8 px, unreadable at the cabinet; the maintainer 2026-11-15).
# A per-system strip cannot work: game lists live inside virtual systems, so ${system.theme} is never dreamcast.
# Hotkey strip = the SECONDARY (informational) row above the help bar (#57 rows): smaller than the help bar's
# icons and muted. Drawn at 2x; theme.xml shows it at HK_HEIGHT / 2 px (size = HK_HEIGHT / 2 / 1080).
# themes/tests/test_theme_xml.py checks the rendered badge stays well below the help bar's icon height.
HK_BADGE, HK_GAP, HK_NOTE_H = 30, 6, 28            # badge row, gap, empty row kept so the theme scale stays (2x px)
HK_HEIGHT = HK_BADGE + HK_GAP + HK_NOTE_H          # 64 px -> 32 px on a 1080 screen
HK_GOLD = BRAND.rgba(BRAND["help_bar"]["secondary"]["badge_color"])   # gold_muted D6B670 (help bar keeps the full gold)
HK_WORD = BRAND.rgba(BRAND["help_bar"]["secondary"]["word_color"])    # muted C9C0EA (contrast-checked on the footer)


def _knock(label, round_, font_path, pill_font):
    """White badge (circle-ish or pill) with the label knocked out, as big as fits.
    Cap height is ~68% of the badge (round) / ~72% (pill). Round labels are drawn condensed
    horizontally by one shared factor (the widest label, MP/HP, must fit in 86% of the badge)
    so all six buttons share one letter height and width scale. Pill width follows the text."""
    from PIL import Image, ImageDraw, ImageFont
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    track = PILLSPEC["track"] * S
    fp = pill_font
    size = 150 * S
    while True:                       # largest font whose cap height fits
        font = ImageFont.truetype(fp, size)
        cap = probe.textbbox((0, 0), "H", font=font)
        ch = cap[3] - cap[1]
        if ch <= (PILLSPEC["cap_round"] if round_ else PILLSPEC["cap_pill"]) * H * S:
            break
        size -= S

    def width(t):
        return sum(probe.textlength(c, font=font) + track for c in t) - track
    RW = PILLSPEC["round_width"] * S
    k = 1.0
    if round_:
        k = min(1.0, PILLSPEC["condense_fit"] * RW / max(width(t) for t in ROUND.values()))   # shared condense factor
    tw = width(label)
    W = RW if round_ else int(tw + PILLSPEC["pill_pad"] * S)
    txt = Image.new("L", (int(tw) + 4 * S, H * S), 0)
    td = ImageDraw.Draw(txt)
    x = 2 * S
    for c in label:
        td.text((x, (H * S - ch) // 2 - cap[1]), c, font=font, fill=255)
        x += probe.textlength(c, font=font) + track
    txt = txt.resize((max(1, int(txt.width * k)), txt.height), Image.LANCZOS)
    mask = Image.new("L", (W, H * S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, W - 1, H * S - 1), radius=(H * S) // 2, fill=255)
    hole = Image.new("L", mask.size, 0)
    hole.paste(txt, ((W - txt.width) // 2, 0))
    mask.paste(0, (0, 0), hole)
    return mask


def _stick():
    from PIL import Image, ImageDraw
    mask = Image.new("L", (H * S, H * S), 0)
    d = ImageDraw.Draw(mask)
    c = H * S // 2
    d.ellipse((c - 34 * S, 8 * S, c + 34 * S, 76 * S), fill=255)            # ball top
    d.rectangle((c - 7 * S, 70 * S, c + 7 * S, 102 * S), fill=255)         # shaft
    d.rounded_rectangle((c - 44 * S, 98 * S, c + 44 * S, 122 * S), radius=10 * S, fill=255)  # base
    return mask


def render(name, font_path):
    from PIL import Image
    if name in STICK:
        mask = _stick()
    else:
        label = ROUND.get(name) or PILL[name]
        mask = _knock(label, name in ROUND, font_path, os.path.join(os.path.dirname(font_path), PILL_TTF))
    mask = mask.resize((mask.width // S, mask.height // S), Image.LANCZOS)
    im = Image.new("RGBA", mask.size, (255, 255, 255, 0))
    im.putalpha(mask)
    return im


def _badge(label, height, font_path, colour=GOLD):
    """Badge of the given pixel height (label knocked out), same shapes as the help bar."""
    from PIL import Image
    mask = _knock(label, label in ("LP", "MP", "HP", "LK", "MK", "HK"), font_path,
                  os.path.join(os.path.dirname(font_path), PILL_TTF))
    w = round(mask.width / S * height / H)
    mask = mask.resize((w, height), Image.LANCZOS)
    im = Image.new("RGBA", mask.size, colour)
    im.putalpha(mask)
    return im


def hotkeys(font_path, entries=None):
    """One strip: [START]+[SELECT] Exit game   [START]+[MP] Emulator menu   [START]+[HP] Controls & pause
    (entries= picks other hotkeys). 2x the displayed size; muted (secondary row). The row below the badges stays
    empty: the canvas keeps HK_HEIGHT so the theme scale is unchanged."""
    from PIL import Image, ImageDraw, ImageFont
    words = ImageFont.truetype(os.path.join(os.path.dirname(font_path), PILL_TTF), 36)
    plus = ImageFont.truetype(os.path.join(os.path.dirname(font_path), PILL_TTF), 34)
    BH, GAP, SEP, G2 = HK_BADGE, 8, 56, 12
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    parts = []   # (kind, payload, width)
    for i, (keys, word) in enumerate(HOTKEYS if entries is None else entries):
        if i:
            parts.append(("gap", None, SEP))
        for j, k in enumerate(keys):
            if j:
                parts.append(("plus", "+", int(probe.textlength("+", font=plus)) + 2 * GAP))
            b = _badge(k, BH, font_path, HK_GOLD)
            parts.append(("img", b, b.width))
        parts.append(("gap", None, G2))
        parts.append(("word", word, int(probe.textlength(word, font=words))))
    W = sum(p[2] for p in parts) + 8
    im = Image.new("RGBA", (W, HK_HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x = 4
    for kind, pl, w in parts:
        if kind == "img":
            im.alpha_composite(pl, (x, 0))
        elif kind == "plus":
            d.text((x + GAP, BH // 2), pl, font=plus, fill=HK_GOLD, anchor="lm")
        elif kind == "word":
            d.text((x, BH // 2), pl, font=words, fill=HK_WORD, anchor="lm")
        x += w
    return im


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) > 1 or (argv and argv[0].startswith("-")):
        print("usage: gen_help.py [THEME_DIR]", file=sys.stderr)
        return 2
    theme = os.path.abspath(argv[0]) if argv else DEFAULT_THEME
    font = os.path.join(theme, "_inc", "fonts", DISPLAY_TTF)
    if not os.path.isfile(font):
        print("error: not a theme dir (font missing): %s" % theme, file=sys.stderr)
        return 2
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("error: Pillow missing; use a venv (see skills/badges/SKILL.md)", file=sys.stderr)
        return 2
    out = os.path.join(theme, "_inc", "help")
    os.makedirs(out, exist_ok=True)
    try:
        for n in ALL:
            render(n, font).save(os.path.join(out, n + ".png"))
        hotkeys(font).save(os.path.join(out, "hotkeys.png"))
    except Exception as e:  # noqa: BLE001
        print("error: %s" % e, file=sys.stderr)
        return 1
    print("wrote %d help icons + hotkeys.png under %s" % (len(ALL), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
