#!/usr/bin/env python3
"""Tests for skills/badges/gen_bootmenu_art.py (#218: the boot menu's card art from your backdrop). Needs Pillow; SKIP
if absent. Run: python3 skills/badges/tests/test_gen_bootmenu_art.py

Covers: two crops at exactly the app's card size (bootmenu_app.ILL_W x ILL_H, so the Pi blits them 1:1), crop boxes
stay inside odd-sized art, and the BROKEN cases: a missing source and bad arguments exit 2 and write nothing.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
GEN = os.path.join(os.path.dirname(HERE), "gen_bootmenu_art.py")
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "skills", "settings"))
from hktest import check, finish, main, skip  # noqa: E402
import gen_bootmenu_art as g  # noqa: E402
import bootmenu_app as bm  # noqa: E402

try:
    from PIL import Image
except ImportError:  # counted SKIP: the Forgejo runner image has no Pillow
    skip("Pillow not installed")
    sys.exit(finish())


def test_size_matches_the_app():
    check((g.ILL_W, g.ILL_H) == (bm.ILL_W, bm.ILL_H),
          "generator size %dx%d = the app's card art size %dx%d" % (g.ILL_W, g.ILL_H, bm.ILL_W, bm.ILL_H))
    check(g.OUT == os.path.join(REPO, "themes", "coin-dojo", "_inc", "art", "bootmenu")
          and g.SRC == os.path.join(REPO, "themes", "coin-dojo", "_inc", "art", "backdrops", "default.png"),
          "default source and output are the theme's night art and bootmenu folder (#263)")


def test_shipped_cards_are_current():
    """#263: the committed cards are this script's crops of the shipped night art (reproducible); a stale one is reported."""
    shipped = os.path.join(REPO, "themes", "coin-dojo", "_inc", "art", "bootmenu")
    with tempfile.TemporaryDirectory() as d:
        check(g.main([g.SRC, d]) == 0, "regenerating the cards from the shipped backdrop works")
        for n in ("esde.png", "fightcade.png"):
            check(open(os.path.join(d, n), "rb").read() == open(os.path.join(shipped, n), "rb").read(),
                  "shipped %s is current (rerun skills/badges/gen_bootmenu_art.py)" % n)
        # broken: other art gives other cards, so a stale shipped card would be seen
        other = os.path.join(d, "other.png")
        Image.new("RGB", (1440, 1080), (40, 20, 90)).save(other)
        out2 = os.path.join(d, "o2")
        check(g.main([other, out2]) == 0 and open(os.path.join(out2, "esde.png"), "rb").read()
              != open(os.path.join(shipped, "esde.png"), "rb").read(),
              "BROKEN: another backdrop gives other cards (a stale shipped card would be seen)")


def test_writes_two_prescaled_crops():
    with tempfile.TemporaryDirectory() as d:
        for size in ((1440, 1080), (300, 1200), (2000, 200)):        # odd art: crops stay inside it
            src = os.path.join(d, "src.png")
            Image.new("RGB", size, (90, 40, 160)).save(src)
            out = os.path.join(d, "out")
            r = subprocess.run([sys.executable, GEN, src, out], capture_output=True, text=True)
            check(r.returncode == 0, "%dx%d source: exit 0 (%s)" % (size + (r.stderr.strip(),)))
            for key in ("esde", "fightcade"):
                f = os.path.join(out, key + ".png")
                check(os.path.isfile(f) and Image.open(f).size == (g.ILL_W, g.ILL_H),
                      "%dx%d source: %s.png is %dx%d" % (size + (key, g.ILL_W, g.ILL_H)))
            for frac in g.CROPS.values():
                x0, y0, x1, y1 = g.crop_box(size, frac)
                check(0 <= x0 < x1 <= size[0] and 0 <= y0 < y1 <= size[1], "%dx%d: crop %r inside" % (size + (frac,)))


def test_BROKEN_missing_source_or_bad_args():
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "out")
        r = subprocess.run([sys.executable, GEN, os.path.join(d, "nope.png"), out], capture_output=True, text=True)
        check(r.returncode == 2 and not os.path.exists(out), "missing source: exit 2, nothing written (%d)" % r.returncode)
        r = subprocess.run([sys.executable, GEN, "--bogus"], capture_output=True, text=True)
        check(r.returncode == 2, "an option it does not know: exit 2")
        r = subprocess.run([sys.executable, GEN, "a", "b", "c"], capture_output=True, text=True)
        check(r.returncode == 2, "three arguments: exit 2")


if __name__ == "__main__":
    main(globals())
