#!/usr/bin/env python3
"""Tests for skills/badges/badge.py. Needs Pillow; skips (exit 0 with SKIP) if absent.
Run: python3 skills/badges/tests/test_badge.py   (or pytest)"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BADGE = os.path.join(os.path.dirname(HERE), "badge.py")
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "tests", "lib"))
from hktest import check, finish, main, skip  # noqa: E402  (shared helper; SKIPs counted in RESULT, #152)

try:
    from PIL import Image
except ImportError:  # counted SKIP (#135): the Forgejo runner image has no Pillow
    skip("Pillow not installed")
    sys.exit(finish())


def run(*args):
    return subprocess.run([sys.executable, BADGE, *args], capture_output=True, text=True)


def test_output_shape():
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "a.png")
        out2 = os.path.join(d, "b.png")
        r = run(out, "1990-94", out2, "GO")
        check(r.returncode == 0, "badge.py exits 0: " + r.stderr.strip())
        for p in (out, out2):
            im = Image.open(p)
            check(im.mode == "RGBA", "%s is RGBA" % os.path.basename(p))
            check(im.height == 256, "%s height == 256 (got %d)" % (os.path.basename(p), im.height))
            check(im.width > im.height or p == out2, "%s width > height" % os.path.basename(p))
            check(im.getchannel("A").getbbox() is not None, "%s alpha non-empty" % os.path.basename(p))
        check(Image.open(out).width > 256, "1990-94 is wider than tall")
        # deterministic
        out3 = os.path.join(d, "c.png")
        run(out3, "1990-94")
        check(open(out, "rb").read() == open(out3, "rb").read(), "same text => identical bytes")


def test_missing_font_fails():
    with tempfile.TemporaryDirectory() as d:
        r = run("--font", os.path.join(d, "nope.ttf"), os.path.join(d, "x.png"), "X")
        check(r.returncode != 0, "BROKEN CASE: missing font => non-zero exit (got %d)" % r.returncode)
        check(not os.path.exists(os.path.join(d, "x.png")), "no output written on missing font")


def test_odd_args_fail():
    r = run("only-one-arg.png")
    check(r.returncode != 0, "BROKEN CASE: OUT without TEXT => non-zero exit")


if __name__ == "__main__":
    main(globals())
