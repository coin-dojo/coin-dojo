#!/usr/bin/env python3
"""An eye glow that slowly brightens and dims on the night backdrop (tickets #105, #263).

Reads the theme's night backdrop (themes/<theme>/_inc/art/backdrops/default.png, or the one in hk_theme_art_dir) and
writes, next to it, three small files:
  eye-glow.png   soft glow of the eye region (RGBA, blurred falloff, core brightest)
  eye-glow.json  Lottie: that PNG embedded as an image layer + an eased opacity ramp (rlottie / ES-DE `animation`)
  eye-glow.xml   ES-DE include with the `animation` element for the system and gamelist views
theme.xml includes eye-glow.xml through a variable path (a missing file only logs a debug line), so a theme or art
folder without the files renders without the glow.

Colour: brand/tokens.json crimson (edge) -> red_light (body) -> salmon (core), the hottest reds of the palette; the
art's own eyes are already red-orange, so the glow reads as them heating up and cooling down, not as a new light.
The glow follows the art: its mask is the saturated-red pixels of the eye socket (R-G large, B small), blurred.

Loop (24 fps, 10 s): 4 s ease in, 1 s hold, 4 s ease out, 1 s rest at zero. Deterministic: same input, same bytes.
Levers come from the config (hk_eye_glow: enabled, peak, box; hk_theme_art_dir = where the backdrop is), read with
tools/config.py (PyYAML: CONFIG_PYTHON, default ~/.venvs/coin-dojo/bin/python). enabled: false removes the three files.
Needs Pillow (venv, see SKILL.md).  Usage: gen_eye_glow.py [BACKDROPS_DIR]
Exit: 0 ok, 1 backdrop missing, 2 bad usage, Pillow missing or config unreadable.
"""
import base64
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "brand"))
DEFAULT_DIR = os.path.join(REPO, "themes", "coin-dojo", "_inc", "art", "backdrops")

# Screen the backdrop is stretched over (brand/tokens.json screen.safe_area) and the glow box, in backdrop pixels
# (measured on default.png: the red eye socket spans x 773-867, y 153-267; padding for the blur).
SCREEN = (1440, 1080)
BOX = None                          # hk_eye_glow.box: x0, y0, x1, y1 in backdrop pixels (config/variables.d/theme.yml)
SCALE = 2                           # glow raster is 2x the on-screen size: smooth edges, tiny file
RED_DIFF, RED_MAX_B = 100, 90       # mask: R-G above this and B below this = the eye's red
BLUR_TIGHT, BLUR_WIDE = 6, 42       # px at 1x: hot crisp core + wide menacing bloom
PEAK = None                         # hk_eye_glow.peak: strongest alpha at the loop's top (config, 0 to 1)
FPS, SECONDS = 24, 10
UP, HOLD, DOWN = 4.0, 1.0, 4.0      # seconds; the rest is 10 - 9 = 1 s
NAME = "eye-glow"
EASE_OUT = {"x": [0.37], "y": [0.0]}   # easeInOutSine
EASE_IN = {"x": [0.63], "y": [1.0]}


def config_get(name):
    """One value of the effective config, as tools/config.py get prints it (JSON for a map, plain text for a string)."""
    py = os.environ.get("CONFIG_PYTHON") or next(
        (p for p in (os.path.expanduser("~/.venvs/coin-dojo/bin/python"),) if os.path.exists(p)), sys.executable)
    r = subprocess.run([py, os.path.join(REPO, "tools", "config.py"), "get", name], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("config %s: %s" % (name, (r.stderr or r.stdout).strip()))
    return r.stdout.strip()


def glow_image(backdrop, tokens):
    """RGBA glow for BOX at SCALE x. alpha = soft mask * PEAK, colour = crimson -> red_light -> salmon -> gold by density."""
    from PIL import Image, ImageChops, ImageFilter
    w, h = BOX[2] - BOX[0], BOX[3] - BOX[1]
    crop = backdrop.convert("RGB").crop(BOX)
    r, g, b = crop.split()
    diff = ImageChops.subtract(r, g).point(lambda v: 255 if v > RED_DIFF else 0)
    low_b = b.point(lambda v: 255 if v < RED_MAX_B else 0)
    mask = ImageChops.multiply(diff, low_b).resize((w * SCALE, h * SCALE), Image.BICUBIC)
    tight = mask.filter(ImageFilter.GaussianBlur(BLUR_TIGHT * SCALE))
    wide = mask.filter(ImageFilter.GaussianBlur(BLUR_WIDE * SCALE))
    dens = ImageChops.add(tight.point(lambda v: v * 60 // 100), wide.point(lambda v: v * 75 // 100))   # 0..255 (clamped by add)
    alpha = dens.point(lambda v: round(255 * (v / 255.0) ** 0.6 * PEAK))   # lift the low end: the bloom spills onto the brow
    # red-hot ember: crimson halo -> red_light -> salmon -> gold-white core (palette colours only)
    stops = [(0.0, tokens.rgb("crimson")), (0.70, tokens.rgb("crimson")), (0.88, tokens.rgb("red_light")),
             (0.96, tokens.rgb("salmon")), (1.0, tokens.rgb("gold"))]
    lut = [[], [], []]
    for v in range(256):
        f = v / 255.0
        for (f0, c0), (f1, c1) in zip(stops, stops[1:]):
            if f0 <= f <= f1:
                t = (f - f0) / (f1 - f0)
                for i in range(3):
                    lut[i].append(round(c0[i] + (c1[i] - c0[i]) * t))
                break
    rgb = Image.merge("RGB", [dens.point(lut[i]) for i in range(3)])
    rgb.putalpha(alpha)
    return rgb


def opacity_keyframes():
    """Lottie opacity keyframes (0-100) for the loop; frames at FPS."""
    f_up, f_hold, f_down, f_end = (round(FPS * s) for s in (UP, UP + HOLD, UP + HOLD + DOWN, SECONDS))

    def kf(t, s, ease=True):
        k = {"t": t, "s": [s]}
        if ease:
            k["o"], k["i"] = EASE_OUT, EASE_IN
        return k
    return [kf(0, 0), kf(f_up, 100, False), kf(f_hold, 100), kf(f_down, 0, False), kf(f_end, 0, False)]


def lottie_doc(png_bytes, size):
    w, h = size
    return {
        "v": "5.7.0", "fr": FPS, "ip": 0, "op": FPS * SECONDS, "w": w, "h": h, "nm": NAME, "ddd": 0,
        "assets": [{"id": "eyes", "w": w, "h": h, "u": "", "e": 1,
                    "p": "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")}],
        "layers": [{
            "ddd": 0, "ind": 1, "ty": 2, "nm": "glow", "refId": "eyes", "sr": 1,
            "ks": {"o": {"a": 1, "k": opacity_keyframes()}, "r": {"a": 0, "k": 0},
                   "p": {"a": 0, "k": [w / 2, h / 2, 0]}, "a": {"a": 0, "k": [w / 2, h / 2, 0]},
                   "s": {"a": 0, "k": [100, 100, 100]}},
            "ao": 0, "ip": 0, "op": FPS * SECONDS, "st": 0, "bm": 0}],
        "markers": []}


def include_xml():
    """ES-DE include (paths are relative to this file, which sits next to the json): an animation over the eyes, between the backdrop (zIndex 2) and its scrim (3)."""
    px = BOX[0] / SCREEN[0], BOX[1] / SCREEN[1]
    sz = (BOX[2] - BOX[0]) / SCREEN[0], (BOX[3] - BOX[1]) / SCREEN[1]

    def view(name):
        return ('    <view name="%s">\n        <animation name="eyeGlow">\n'
                "            <pos>%.5f %.5f</pos>\n            <size>%.5f %.5f</size>\n"
                "            <path>./%s.json</path>\n"
                "            <interpolation>linear</interpolation>\n            <iterationCount>0</iterationCount>\n"
                "            <zIndex>2.5</zIndex>\n        </animation>\n    </view>\n" % (name, px[0], px[1], sz[0], sz[1], NAME))
    return ("<!-- The eye glow (#105, #263), generated by skills/badges/gen_eye_glow.py from the night backdrop.\n"
            "     theme.xml includes it through ${eyeGlow}; without this file ES-DE logs a debug line and renders on. -->\n"
            "<theme>\n" + view("system") + view("gamelist") + "</theme>\n")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) > 1 or (argv and argv[0].startswith("-")):
        print("usage: gen_eye_glow.py [BACKDROPS_DIR]", file=sys.stderr)
        return 2
    try:
        from PIL import Image
    except ImportError:
        print("error: Pillow missing; use a venv (see SKILL.md)", file=sys.stderr)
        return 2
    import brand
    global BOX, PEAK
    try:
        glow_cfg = json.loads(config_get("hk_eye_glow"))
        art = config_get("hk_theme_art_dir") if not argv else ""
    except (RuntimeError, ValueError) as exc:
        print("error: %s (run `make config-check`; PyYAML: `make config-venv`)" % exc, file=sys.stderr)
        return 2
    BOX, PEAK = tuple(glow_cfg["box"]), glow_cfg["peak"]
    if len(BOX) != 4 or not (0 <= BOX[0] < BOX[2] <= SCREEN[0] and 0 <= BOX[1] < BOX[3] <= SCREEN[1]):
        print("error: hk_eye_glow.box must be x0, y0, x1, y1 inside %dx%d, got %s" % (SCREEN + (list(BOX),)), file=sys.stderr)
        return 2
    if argv:
        out = os.path.abspath(argv[0])
    elif art:
        out = os.path.join(art if os.path.isabs(art) else os.path.join(REPO, art), "backdrops")
    else:
        out = DEFAULT_DIR
    if not glow_cfg["enabled"]:
        for ext in ("png", "json", "xml"):
            if os.path.exists(os.path.join(out, "%s.%s" % (NAME, ext))):
                os.remove(os.path.join(out, "%s.%s" % (NAME, ext)))
        print("hk_eye_glow.enabled is false: no glow files in %s" % out)
        return 0
    src = os.path.join(out, "default.png")
    if not os.path.isfile(src):
        print("error: no backdrop at %s" % src, file=sys.stderr)
        return 1
    glow = glow_image(Image.open(src), brand.load())
    buf = io.BytesIO()
    glow.save(buf, "PNG", optimize=False)
    png = buf.getvalue()
    with open(os.path.join(out, NAME + ".png"), "wb") as f:
        f.write(png)
    with open(os.path.join(out, NAME + ".json"), "w") as f:
        json.dump(lottie_doc(png, glow.size), f, separators=(",", ":"), sort_keys=True)
    with open(os.path.join(out, NAME + ".xml"), "w") as f:
        f.write(include_xml())
    print("wrote %s.{png,json,xml} in %s (glow %dx%d, loop %d s)" % (NAME, out, glow.size[0], glow.size[1], SECONDS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
