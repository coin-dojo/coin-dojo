#!/usr/bin/env python3
"""Tests for gen_scrim.py / gen_deck.py / gen_panels.py (#43). Needs Pillow; SKIP if absent.
Run: python3 skills/badges/tests/test_gen_art.py   (or pytest)"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BADGES = os.path.dirname(HERE)
ART = os.path.normpath(os.path.join(BADGES, "..", "..", "themes", "coin-dojo", "_inc", "art"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "tests", "lib"))
from hktest import check, finish, main, skip  # noqa: E402  (shared helper; SKIPs counted in RESULT, #152)

try:
    from PIL import Image
except ImportError:  # counted SKIP (#135): the Forgejo runner image has no Pillow
    skip("Pillow not installed")
    sys.exit(finish())


def run(script, *args):
    return subprocess.run([sys.executable, os.path.join(BADGES, script), *args], capture_output=True, text=True)


def test_scrim_profile():
    with tempfile.TemporaryDirectory() as d:
        r = run("gen_scrim.py", d)
        check(r.returncode == 0, "gen_scrim exits 0: " + r.stderr.strip())
        im = Image.open(os.path.join(d, "scrim.png")).convert("RGBA")
        a = lambda f: im.getpixel((0, int(f * 1079)))[3] / 255.0  # noqa: E731
        check(im.size == (8, 1080), "scrim is 8x1080")
        check(a(0.05) < 0.02, "scrim is clear at the top (art visible), got %.2f" % a(0.05))
        check(0.30 <= a(0.55) <= 0.45, "scrim ~38%% behind the carousel, got %.2f" % a(0.55))
        check(a(1.0) >= 0.80, "scrim >= 80%% at the footer, got %.2f" % a(1.0))
        lst = Image.open(os.path.join(d, "scrim-list.png")).convert("RGBA")
        check(0.3 <= lst.getpixel((0, 100))[3] / 255.0 <= 0.6, "gamelist veil is a moderate flat tint")


def test_deck_is_translucent():
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "deck.png")
        r = run("gen_deck.py", out)
        check(r.returncode == 0, "gen_deck exits 0: " + r.stderr.strip())
        im = Image.open(out).convert("RGBA")
        check(im.getpixel((1440, 150))[3] < 200 and im.getpixel((1440, 339))[3] < 200,
              "generated deck is not opaque")


def test_committed_deck_and_panels_are_translucent():
    """Regression for the opaque 'slab' deck: the shipped PNGs must let the art through."""
    deck = Image.open(os.path.join(ART, "deck.png")).convert("RGBA")
    check(deck.getchannel("A").getextrema()[1] < 255, "committed deck.png has no fully opaque pixel")
    check(deck.getpixel((1440, 120))[3] < 100, "committed deck.png is mostly clear near its top")
    for n in ("panel.png", "panel-red.png"):
        a = Image.open(os.path.join(ART, n)).convert("RGBA").getpixel((300, 400))[3]
        check(200 <= a <= 250, "%s interior alpha is high but not opaque (%d)" % (n, a))


def test_bad_args_fail():
    for s in ("gen_scrim.py", "gen_deck.py", "gen_panels.py"):
        r = run(s, "--nope")
        check(r.returncode == 2, "BROKEN CASE: %s with a bad flag => exit 2 (got %d)" % (s, r.returncode))
    r = run("gen_deck.py", "a", "b")
    check(r.returncode == 2, "BROKEN CASE: gen_deck with two args => exit 2")


if __name__ == "__main__":
    main(globals())
