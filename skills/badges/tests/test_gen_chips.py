#!/usr/bin/env python3
"""Tests for skills/badges/gen_chips.py (category chips + genre pictograms). Needs Pillow; SKIP if absent.
Run: python3 skills/badges/tests/test_gen_chips.py   (or pytest)"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.join(os.path.dirname(HERE), "gen_chips.py")
THEME = os.path.normpath(os.path.join(HERE, "..", "..", "..", "themes", "coin-dojo"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "tests", "lib"))
from hktest import check, finish, main, skip  # noqa: E402  (shared helper; SKIPs counted in RESULT, #152)

try:
    from PIL import Image
except ImportError:  # counted SKIP (#135): the Forgejo runner image has no Pillow
    skip("Pillow not installed")
    sys.exit(finish())


def mini_theme(d, names):
    os.makedirs(os.path.join(d, "_inc", "systems"))
    os.makedirs(os.path.join(d, "_inc", "fonts"))
    shutil.copy(os.path.join(THEME, "_inc", "fonts", "ArchivoBlack-Regular.ttf"),
                os.path.join(d, "_inc", "fonts"))
    for n in names:
        open(os.path.join(d, "_inc", "systems", n + ".png"), "wb").close()


def test_generates_chips_and_icons():
    with tempfile.TemporaryDirectory() as d:
        mini_theme(d, ["fighters", "1990s", "cps2", "must-play", "genres", "series", "decades", "systems"])
        r = subprocess.run([sys.executable, GEN, d], capture_output=True, text=True)
        check(r.returncode == 0, "gen_chips exits 0: " + r.stderr.strip())
        for n in ("fighters", "1990s", "cps2", "must-play", "default"):
            check(os.path.isfile(os.path.join(d, "_inc", "chips", n + ".png")), "chip for " + n)
        icon = os.path.join(d, "_inc", "icons", "fighters.png")
        check(os.path.isfile(icon) and Image.open(icon).getchannel("A").getbbox() is not None,
              "fighters pictogram exists and is not empty")
        check(not os.path.exists(os.path.join(d, "_inc", "icons", "cps2.png")), "no pictogram for a non-genre")
        check(Image.open(os.path.join(d, "_inc", "icons", "default.png")).getchannel("A").getbbox() is None,
              "icons/default.png is fully transparent")
        a = open(os.path.join(d, "_inc", "chips", "fighters.png"), "rb").read()
        b = open(os.path.join(d, "_inc", "chips", "must-play.png"), "rb").read()
        check(a != b, "GENRE and CURATED chips differ")
        g = open(os.path.join(d, "_inc", "chips", "genres.png"), "rb").read()
        others = [open(os.path.join(d, "_inc", "chips", n + ".png"), "rb").read()
                  for n in ("decades", "series", "systems")]
        check(all(g == o for o in others) and g != b,
              "the four nested-main doors share one BROWSE chip, different from CURATED")


def test_quit_entry_gets_exit_chip_and_pictogram():
    """#218: QUIT TO MAIN MENU is not a SYSTEM: its own EXIT chip and an exit pictogram above it."""
    with tempfile.TemporaryDirectory() as d:
        mini_theme(d, ["quit", "settings", "cps2"])
        r = subprocess.run([sys.executable, GEN, d], capture_output=True, text=True)
        check(r.returncode == 0, "gen_chips exits 0: " + r.stderr.strip())
        chip = lambda n: open(os.path.join(d, "_inc", "chips", n + ".png"), "rb").read()  # noqa: E731
        check(chip("quit") not in (chip("cps2"), chip("settings"), chip("default")),
              "QUIT has its own chip (not SYSTEM, not SETUP)")
        icon = os.path.join(d, "_inc", "icons", "quit.png")
        check(os.path.isfile(icon) and Image.open(icon).getchannel("A").getbbox() is not None, "QUIT pictogram drawn")
    committed = os.path.join(THEME, "_inc")
    for sub in ("systems", "chips", "icons"):
        check(os.path.isfile(os.path.join(committed, sub, "quit.png")), "theme ships _inc/%s/quit.png" % sub)


def test_bad_theme_dir_fails():
    with tempfile.TemporaryDirectory() as d:
        r = subprocess.run([sys.executable, GEN, d], capture_output=True, text=True)
        check(r.returncode == 2, "BROKEN CASE: dir that is not a theme => exit 2 (got %d)" % r.returncode)


if __name__ == "__main__":
    main(globals())
