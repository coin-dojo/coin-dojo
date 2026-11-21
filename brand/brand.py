#!/usr/bin/env python3
"""Load the coin-dojo style kit (brand/tokens.json). Stdlib only; import it or run it.

    import brand                      # with brand/ on sys.path
    b = brand.load()                  # BrandError if tokens.json is missing a required token
    b.rgb("gold")       -> (255, 201, 77)
    b.rgba("navy", 235) -> (24, 10, 40, 235)
    b.hex("text")       -> "FFF4DC"
    b.font("ui_bold")   -> absolute path of Rajdhani-Bold.ttf (font_dir= or $HK_BRAND_FONT_DIR overrides the dir)
    b["card"]["radius"] -> 24          (any section, as plain dicts)
    b.contrast_failures() -> [] when every contrast pair reaches contrast.min

CLI: brand.py check [TOKENS]   -> prints the contrast table, RESULT: PASS (exit 0) / RESULT: FAIL (exit 1).
Rules in plain words: brand/BRAND.md.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TOKENS = os.path.join(HERE, "tokens.json")
REQUIRED_COLORS = ("bg", "panel", "card", "button", "text", "text_soft", "chrome", "muted", "muted_card", "dimmed",
                   "dim", "gold", "gold_card", "gold_muted", "on_gold", "crimson", "salmon", "lavender", "pending",
                   "navy", "badge_outline", "decade", "cyan", "white")
REQUIRED_FONTS = ("display", "ui_bold", "ui_semi", "ui_medium", "pixel")
REQUIRED_SECTIONS = ("screen", "colors", "fonts", "type_scale", "spacing", "radius", "border", "badge_pill", "card",
                     "help_bar", "contrast", "gradients", "chips", "alpha")


class BrandError(ValueError):
    pass


def _hex_rgb(h):
    h = str(h).strip().lstrip("#")
    if len(h) != 6 or any(c not in "0123456789abcdefABCDEF" for c in h):
        raise BrandError("not a 6-digit hex colour: %r" % h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def luminance(rgb):
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    """WCAG contrast ratio of two (r, g, b) colours (same formula as themes/tests/test_palette_contrast.py)."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class Brand:
    def __init__(self, data, font_dir=None):
        self.data = data
        self._font_dir = font_dir
        self.validate()

    def __getitem__(self, key):
        return self.data[key]

    def validate(self):
        d = self.data
        missing = [s for s in REQUIRED_SECTIONS if s not in d]
        if missing:
            raise BrandError("tokens.json: missing section(s): %s" % ", ".join(missing))
        cols = d["colors"]
        missing = [c for c in REQUIRED_COLORS if c not in cols or "hex" not in cols[c]]
        if missing:
            raise BrandError("tokens.json: missing colour token(s): %s" % ", ".join(missing))
        for name, c in cols.items():
            _hex_rgb(c["hex"])
            if not c.get("role"):
                raise BrandError("tokens.json: colour %s has no role" % name)
        missing = [f for f in REQUIRED_FONTS if f not in d["fonts"] or "file" not in d["fonts"][f]]
        if missing:
            raise BrandError("tokens.json: missing font token(s): %s" % ", ".join(missing))
        for fg, bg in d["contrast"]["pairs"]:
            for c in (fg, bg):
                if c not in cols:
                    raise BrandError("tokens.json: contrast pair names an unknown colour: %s" % c)

    # colours
    def rgb(self, name):
        try:
            return _hex_rgb(self.data["colors"][name]["hex"])
        except KeyError:
            raise BrandError("unknown colour token: %s" % name)

    def rgba(self, name, alpha=255):
        return self.rgb(name) + (alpha,)

    def hex(self, name):
        return "%02X%02X%02X" % self.rgb(name)

    def stops(self, gradient):
        return [(float(p), _hex_rgb(h)) for p, h in self.data["gradients"][gradient]["stops"]]

    # fonts
    def font_dir(self):
        d = self._font_dir or os.environ.get("HK_BRAND_FONT_DIR") or os.path.join(REPO, self.data["fonts"]["dir"])
        return d

    def font(self, role):
        try:
            f = self.data["fonts"][role]["file"]
        except KeyError:
            raise BrandError("unknown font token: %s" % role)
        return os.path.join(self.font_dir(), f)

    # contrast
    def contrast_table(self):
        """-> [(fg, bg, ratio)] for every pair in contrast.pairs."""
        return [(fg, bg, contrast(self.rgb(fg), self.rgb(bg))) for fg, bg in self.data["contrast"]["pairs"]]

    def contrast_failures(self):
        m = float(self.data["contrast"]["min"])
        return [(fg, bg, r) for fg, bg, r in self.contrast_table() if r < m]


# ---------------------------------------------------------------- the logo (#258)
# The ONE place a page, banner or sheet gets the project logo from: brand/gen_logo.py renders the files, these pick
# the right one for a size (BRAND.md "Logo": the lockup needs >= 320 px of width, the ring text >= 64 px, below that
# the 32 px favicon). A new logo = re-run gen_logo.py; nothing that calls these changes.
LOGO_LOCKUP_MIN = 320
LOGO_RING_MIN = 64


def logo_file(width, light=False):
    """-> (absolute path, kind) of the logo file to draw `width` px wide; kind is lockup, icon or favicon.

    lockup: logo.png (logo-light.png on light pages) at >= LOGO_LOCKUP_MIN; icon: logo-icon-512.png (scaled) down to
    65 px, logo-icon-64.png at 64; favicon: logo-icon-32.png below 64 (never a scaled-down 512)."""
    width = int(width)
    if width < 1:
        raise BrandError("logo width must be >= 1 px, got %d" % width)
    if width >= LOGO_LOCKUP_MIN:
        return os.path.join(HERE, "logo-light.png" if light else "logo.png"), "lockup"
    if width > LOGO_RING_MIN:
        return os.path.join(HERE, "logo-icon-512.png"), "icon"
    if width == LOGO_RING_MIN:
        return os.path.join(HERE, "logo-icon-64.png"), "icon"
    return os.path.join(HERE, "logo-icon-32.png"), "favicon"


def logo_image(width, light=False):
    """-> a Pillow RGBA image of the logo, `width` px wide (aspect kept). Needs Pillow; BrandError if the file is gone."""
    from PIL import Image
    path, _kind = logo_file(width, light)
    if not os.path.isfile(path):
        raise BrandError("logo file missing: %s (run brand/gen_logo.py)" % path)
    with Image.open(path) as im:
        im = im.convert("RGBA")
        if im.width == width:
            return im.copy()
        return im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)


def load(path=None, font_dir=None):
    path = path or os.environ.get("HK_BRAND_TOKENS") or TOKENS
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise BrandError("cannot read %s: %s" % (path, e))
    except ValueError as e:
        raise BrandError("%s: not JSON: %s" % (path, e))
    return Brand(data, font_dir)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] != "check" or len(argv) > 2:
        print("usage: brand.py check [TOKENS]", file=sys.stderr)
        return 2
    try:
        b = load(argv[1] if len(argv) > 1 else None)
    except BrandError as e:
        print("FAIL %s" % e)
        print("RESULT: FAIL")
        return 1
    m = float(b["contrast"]["min"])
    for fg, bg, r in b.contrast_table():
        print("%s %-11s on %-11s %5.2f:1 (>= %.1f)" % ("PASS" if r >= m else "FAIL", fg, bg, r, m))
    bad = b.contrast_failures()
    print("RESULT: %s" % ("FAIL" if bad else "PASS"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
