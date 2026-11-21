#!/usr/bin/env python3
"""The Coin Dojo logo (#258): brand/gen_logo.py and the PNGs it wrote into brand/.

- the five files exist with the sizes BRAND.md "Logo" promises (lockups 1600x560, icons 512/64/32, all RGBA with a
  transparent corner, so they sit on dark and light pages);
- a fresh render with the committed tokens is BYTE-IDENTICAL to the committed files (Pillow venv, else SKIP);
- gen_logo.py takes every colour and font from the tokens (no hex, RGB tuple or font file name; test_brand's lint).
BROKEN cases (must be caught): a token one step off changes the render; a committed icon of the wrong size; a
planted literal colour in the generator. Run: python3 brand/tests/test_logo.py
"""
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BRAND_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(BRAND_DIR)
sys.path.insert(0, BRAND_DIR)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
import brand  # noqa: E402
from hktest import check, main, skip  # noqa: E402
from test_brand import style_problems, VENV_PY  # noqa: E402

SIZES = {"logo.png": (1600, 560), "logo-light.png": (1600, 560), "logo-icon-512.png": (512, 512),
         "logo-icon-64.png": (64, 64), "logo-icon-32.png": (32, 32)}
GEN = os.path.join(BRAND_DIR, "gen_logo.py")


def png_size(path):
    """(width, height, colour type) from the IHDR chunk, stdlib only."""
    with open(path, "rb") as f:
        head = f.read(26)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = struct.unpack(">II", head[16:24])
    return w, h, head[25]


def size_problems(folder):
    probs = []
    for name, want in sorted(SIZES.items()):
        p = os.path.join(folder, name)
        got = png_size(p) if os.path.isfile(p) else None
        if got is None:
            probs.append("%s missing or not a PNG" % name)
        elif got[:2] != want:
            probs.append("%s is %dx%d, want %dx%d" % (name, got[0], got[1], want[0], want[1]))
        elif got[2] != 6:
            probs.append("%s is not RGBA (colour type %d)" % (name, got[2]))
    return probs


def drift(a, b):
    out = []
    for name in sorted(SIZES):
        pa, pb = os.path.join(a, name), os.path.join(b, name)
        if not (os.path.isfile(pa) and os.path.isfile(pb)) or open(pa, "rb").read() != open(pb, "rb").read():
            out.append(name)
    return out


def regen(out, tokens=None):
    env = dict(os.environ)
    if tokens:
        env["HK_BRAND_TOKENS"] = tokens
    return subprocess.run([VENV_PY, GEN, out], capture_output=True, text=True, env=env)


def test_files_and_sizes():
    check(size_problems(BRAND_DIR) == [], "brand/logo*.png: present, sizes and RGBA %s" % size_problems(BRAND_DIR))
    tmp = tempfile.mkdtemp()
    try:
        for name in SIZES:
            shutil.copyfile(os.path.join(BRAND_DIR, name), os.path.join(tmp, name))
        shutil.copyfile(os.path.join(BRAND_DIR, "logo-icon-512.png"), os.path.join(tmp, "logo-icon-32.png"))
        check(any("logo-icon-32.png is 512x512" in p for p in size_problems(tmp)),
              "BROKEN CASE: a 512 px file saved as the 32 px favicon is reported")
    finally:
        shutil.rmtree(tmp)


def test_generator_uses_tokens_only():
    with open(GEN, encoding="utf-8") as f:
        probs = style_problems(f.read())
    check(probs == [], "gen_logo.py has no hard-coded colour or font file %s" % probs[:3])
    check(style_problems('RING = (255, 201, 77)'), "BROKEN CASE: a planted literal colour is flagged")


def test_logo_picker():
    """brand.logo_file/logo_image (#258): the one place pages get the logo; lockup >= 320 px, ring >= 64, else favicon."""
    want = [(1280, "logo.png", "lockup"), (320, "logo.png", "lockup"), (319, "logo-icon-512.png", "icon"),
            (112, "logo-icon-512.png", "icon"), (64, "logo-icon-64.png", "icon"), (63, "logo-icon-32.png", "favicon"),
            (32, "logo-icon-32.png", "favicon")]
    for width, name, kind in want:
        path, k = brand.logo_file(width)
        check((os.path.basename(path), k) == (name, kind), "%d px -> %s (%s), got %s (%s)"
              % (width, name, kind, os.path.basename(path), k))
        check(os.path.isfile(path), "%s exists" % name)
    check(os.path.basename(brand.logo_file(400, light=True)[0]) == "logo-light.png", "light pages get logo-light.png")
    try:
        brand.logo_file(0)
        check(False, "BROKEN CASE: a 0 px logo is refused")
    except brand.BrandError:
        check(True, "BROKEN CASE: a 0 px logo is refused")
    if not os.path.exists(VENV_PY):
        skip("logo_image: no Pillow venv at %s" % VENV_PY)
        return
    code = ("import sys; sys.path.insert(0, %r); import brand; im = brand.logo_image(%d); print(im.size, im.mode)"
            % (BRAND_DIR, 400))
    r = subprocess.run([VENV_PY, "-c", code], capture_output=True, text=True)
    check(r.returncode == 0 and r.stdout.strip() == "(400, 140) RGBA", "logo_image(400) -> 400x140 RGBA lockup (%s %s)"
          % (r.stdout.strip(), r.stderr.strip()[-200:]))
    empty = tempfile.mkdtemp()
    try:
        code = ("import sys; sys.path.insert(0, %r); import brand; brand.HERE = %r; brand.logo_image(400)"
                % (BRAND_DIR, empty))
        r = subprocess.run([VENV_PY, "-c", code], capture_output=True, text=True)
    finally:
        os.rmdir(empty)
    check(r.returncode != 0 and "logo file missing" in r.stderr, "BROKEN CASE: a missing logo file is a BrandError")


def test_byte_identical():
    if not os.path.exists(VENV_PY):
        skip("logo byte-identical: no Pillow venv at %s" % VENV_PY)
        return
    tmp = tempfile.mkdtemp()
    try:
        r = regen(os.path.join(tmp, "fresh"))
        check(r.returncode == 0, "gen_logo.py renders (rc %d %s)" % (r.returncode, r.stderr.strip()[-200:]))
        check(drift(BRAND_DIR, os.path.join(tmp, "fresh")) == [],
              "the committed logo files equal a fresh render %s" % drift(BRAND_DIR, os.path.join(tmp, "fresh")))
        with open(brand.TOKENS, encoding="utf-8") as f:
            d = json.load(f)
        d["colors"]["gold"]["hex"] = "FFC94E"          # one step off
        tok = os.path.join(tmp, "tokens.json")
        with open(tok, "w") as f:
            json.dump(d, f)
        r = regen(os.path.join(tmp, "off"), tok)
        check(r.returncode == 0 and drift(BRAND_DIR, os.path.join(tmp, "off")) != [],
              "BROKEN CASE: a gold one step off changes the render (drift is caught)")
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    main(globals())
