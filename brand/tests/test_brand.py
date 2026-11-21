#!/usr/bin/env python3
"""Style kit tests (brand/). Plain python3; pytest-compatible. Run: python3 brand/tests/test_brand.py

- tokens.json loads, has every required token, every contrast pair reaches contrast.min
- contrast.min equals the palette test's MIN (themes/tests/test_palette_contrast.py)
- the theme's colors.xml (indigo) and the tokens agree, every font file exists
- the generators read the tokens and their PNGs stay BYTE-IDENTICAL to the committed theme files
  (gen_help.py, gen_chips.py, controls.py render; needs Pillow: ~/.venvs/badges, else SKIP, counted in RESULT)
- style lint: every file under CLEAN (start: skills/settings) has no hex colour, RGB tuple or font file name
BROKEN cases (must be caught): a missing colour token, a missing font token, a contrast pair under the threshold,
a bad hex value, a generator that drifts from the committed art.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
BRAND_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(BRAND_DIR)
THEME = os.path.join(REPO, "themes", "coin-dojo")
sys.path.insert(0, BRAND_DIR)
import brand  # noqa: E402
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
from hktest import check, main, skip  # noqa: E402  (shared helper; SKIPs counted in RESULT, #137)

VENV_PY = os.path.expanduser(os.environ.get("BADGE_VENV", "~/.venvs/badges")) + "/bin/python"


# ---- style lint: files that must take every colour and font from the tokens (brand/BRAND.md rule 1) ----------
# Add a path here once it is clean. Tests folders are not linted (they plant the broken cases).
CLEAN = ["skills/settings"]
HEX_RX = re.compile(r"[\"']#?[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?[\"']")
TUPLE_RX = re.compile(r"\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(?:,\s*\d{1,3}\s*)?\)")
FONT_RX = re.compile(r"\.(?:ttf|otf)\b", re.I)


def style_problems(text):
    out = []
    for rx in (HEX_RX, TUPLE_RX, FONT_RX):
        out += rx.findall(text)
    return out


def clean_files():
    out = []
    for c in CLEAN:
        p = os.path.join(REPO, c)
        if os.path.isfile(p):
            out.append(p)
            continue
        for dp, dns, fns in os.walk(p):
            dns[:] = [d for d in dns if d not in ("tests", "__pycache__")]
            out += [os.path.join(dp, f) for f in sorted(fns) if f.endswith(".py")]
    return out


def test_clean_files_use_tokens_only():
    files = clean_files()
    check(len(files) >= 3, "lint covers %d file(s) in %s" % (len(files), CLEAN))
    for p in files:
        with open(p, encoding="utf-8") as f:
            probs = style_problems(f.read())
        check(not probs, "no hard-coded colour/font in %s %s" % (os.path.relpath(p, REPO), probs[:3]))
    for planted in ('GOLD = "FFC94D"', "c = (255, 201, 77)", 'f = "Rajdhani-Bold.ttf"', "x = '#0e0822'"):
        check(style_problems(planted), "BROKEN CASE: planted %r is flagged" % planted)


def raw():
    with open(brand.TOKENS, encoding="utf-8") as f:
        return json.load(f)


def raises(data, what):
    try:
        brand.Brand(data)
    except brand.BrandError as e:
        return check(True, "BROKEN CASE: %s rejected (%s)" % (what, e))
    return check(False, "BROKEN CASE: %s was accepted" % what)


def test_tokens_load_and_contrast():
    b = brand.load()
    check(b.rgb("gold") == (255, 201, 77), "gold = FFC94D")
    for fg, bg, r in b.contrast_table():
        check(r >= b["contrast"]["min"], "contrast %s on %s = %.2f:1" % (fg, bg, r))
    check(len(b["contrast"]["pairs"]) >= 10, "at least 10 contrast pairs")
    check(b["screen"]["safe_area"] == [1440, 1080], "safe area 1440x1080")
    check(subprocess.run([sys.executable, os.path.join(BRAND_DIR, "brand.py"), "check"],
                         capture_output=True).returncode == 0, "brand.py check exits 0")


def test_threshold_matches_palette_test():
    spec = importlib.util.spec_from_file_location("palette", os.path.join(THEME, "..", "tests", "test_palette_contrast.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    check(float(brand.load()["contrast"]["min"]) == float(mod.MIN), "contrast.min == palette test MIN (%s)" % mod.MIN)


def test_theme_colors_agree():
    b = brand.load()
    sch = {cs.get("name"): {v.tag: (v.text or "").strip()[:6].upper() for v in cs.find("variables")}
           for cs in ET.parse(os.path.join(THEME, "colors.xml")).getroot().iter("colorScheme")}["indigo"]
    for var, tok in (("baseColor", "bg"), ("textPrimary", "text"), ("textMuted", "muted"), ("descColor", "text_soft"),
                     ("listSelector", "gold"), ("listSelected", "on_gold"), ("listSecondary", "salmon"),
                     ("labelColor", "gold"), ("helpIcon", "gold"), ("helpDimmed", "dimmed"), ("gameCounterBg", "counter_bg")):
        check(sch[var] == b.hex(tok), "colors.xml %s %s == token %s %s" % (var, sch[var], tok, b.hex(tok)))


def test_fonts_exist():
    b = brand.load()
    for role in brand.REQUIRED_FONTS:
        check(os.path.isfile(b.font(role)), "font %s -> %s exists" % (role, os.path.basename(b.font(role))))
    b2 = brand.load(font_dir="/nowhere")
    check(b2.font("display") == "/nowhere/ArchivoBlack-Regular.ttf", "font_dir override")


def test_BROKEN_missing_tokens():
    d = raw()
    del d["colors"]["gold"]
    raises(d, "tokens.json without colours.gold")
    d = raw()
    del d["fonts"]["ui_bold"]
    raises(d, "tokens.json without fonts.ui_bold")
    d = raw()
    del d["help_bar"]
    raises(d, "tokens.json without help_bar")
    d = raw()
    d["colors"]["text"]["hex"] = "FFF4D"
    raises(d, "a 5-digit hex colour")
    d = raw()
    d["contrast"]["pairs"].append(["text", "nonesuch"])
    raises(d, "a contrast pair naming an unknown colour")


def test_BROKEN_low_contrast_pair():
    d = raw()
    d["colors"]["muted"]["hex"] = "3A3260"          # dim lavender on violet-black
    bad = brand.Brand(d).contrast_failures()
    check(any(fg == "muted" and bg == "bg" for fg, bg, _r in bad), "BROKEN CASE: dim muted on bg is under the threshold")
    tmp = tempfile.mkdtemp()
    try:
        p = os.path.join(tmp, "tokens.json")
        with open(p, "w") as f:
            json.dump(d, f)
        r = subprocess.run([sys.executable, os.path.join(BRAND_DIR, "brand.py"), "check", p], capture_output=True, text=True)
        check(r.returncode == 1 and "RESULT: FAIL" in r.stdout, "BROKEN CASE: brand.py check exits 1 on it")
    finally:
        shutil.rmtree(tmp)


def _regen(tmp, tokens=None):
    """Run the three generators with the venv python into tmp; -> {name: dir} of outputs."""
    env = dict(os.environ)
    if tokens:
        env["HK_BRAND_TOKENS"] = tokens
    th = os.path.join(tmp, "theme")
    os.makedirs(os.path.join(th, "_inc"))
    for sub in ("fonts", "systems"):
        shutil.copytree(os.path.join(THEME, "_inc", sub), os.path.join(th, "_inc", sub))
    os.makedirs(os.path.join(tmp, "gl"))
    run = lambda *a: subprocess.run([VENV_PY, *a], capture_output=True, text=True, env=env)  # noqa: E731
    rcs = [run(os.path.join(REPO, "skills", "badges", "gen_help.py"), th),
           run(os.path.join(REPO, "skills", "badges", "gen_chips.py"), th),
           run(os.path.join(REPO, "skills", "controls", "controls.py"), "render", "--gamelists", os.path.join(tmp, "gl"),
               "--fonts", os.path.join(THEME, "_inc", "fonts"), "--out", os.path.join(tmp, "ctl"))]
    for r in rcs:
        if r.returncode:
            print(r.stdout + r.stderr)
    return all(r.returncode == 0 for r in rcs), {"help": os.path.join(th, "_inc", "help"),
                                                 "chips": os.path.join(th, "_inc", "chips"),
                                                 "icons": os.path.join(th, "_inc", "icons"),
                                                 "controls": os.path.join(tmp, "ctl", "cards")}


def _same(a, b):
    fa = sorted(f for f in os.listdir(a) if f.endswith(".png"))
    fb = sorted(f for f in os.listdir(b) if f.endswith(".png"))
    if fa != fb:
        return False
    return all(open(os.path.join(a, f), "rb").read() == open(os.path.join(b, f), "rb").read() for f in fa)


def test_generators_byte_identical():
    if not os.path.exists(VENV_PY):
        skip("generators byte-identical: no Pillow venv at %s" % VENV_PY)
        return
    tmp = tempfile.mkdtemp()
    try:
        ok, out = _regen(tmp)
        check(ok, "gen_help / gen_chips / controls render ran with the tokens")
        for name in ("help", "chips", "icons", "controls"):
            check(_same(out[name], os.path.join(THEME, "_inc", name)), "%s PNGs byte-identical to the committed theme" % name)
    finally:
        shutil.rmtree(tmp)


def test_BROKEN_generator_drift_is_caught():
    if not os.path.exists(VENV_PY):
        skip("generator drift: no Pillow venv")
        return
    tmp = tempfile.mkdtemp()
    try:
        d = raw()
        d["colors"]["gold_card"]["hex"] = "E8B841"      # one step off: the cards must change
        tok = os.path.join(tmp, "tokens.json")
        with open(tok, "w") as f:
            json.dump(d, f)
        ok, out = _regen(tmp, tok)
        check(ok and not _same(out["controls"], os.path.join(THEME, "_inc", "controls")),
              "BROKEN CASE: a changed token changes the rendered cards (the generators really read tokens.json)")
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    main(globals())
