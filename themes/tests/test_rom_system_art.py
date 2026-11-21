#!/usr/bin/env python3
"""Every system folder under /data/roms has its own carousel badge and chip in the theme (#296).

The game menu row picks `_inc/systems/<system>.png` and `_inc/chips/<system>.png` by the ES-DE system theme name, which
is the ROM folder name. A folder without them shows the plain fallback tile (Atari 2600 on a community test card).

Where the folder list comes from (nothing here types it):
  - the launcher table: skills/collections/launch.py CORE_BY_SYSTEM via ansible/roles/cores/files/launcher_table.py
    (the cores role and the image build create exactly these folders, #244);
  - the image's first boot: the `for s in ...; do mkdir -p "$DATA/roms/$s"` loop in
    skills/build-image/scripts/firstboot-expand.sh (the same list since #296).

Run: python3 themes/tests/test_rom_system_art.py (stdlib only).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
THEME = os.path.join(REPO, "themes", "coin-dojo")
LAUNCHER_TABLE = os.path.join(REPO, "ansible", "roles", "cores", "files", "launcher_table.py")
FIRSTBOOT = os.path.join(REPO, "skills", "build-image", "scripts", "firstboot-expand.sh")

sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
from hktest import check, main  # noqa: E402

FIRSTBOOT_LOOP = re.compile(r'^\s*for s in ([a-z0-9 ]+); do\s*\n\s*mkdir -p "\$DATA/roms/\$s"', re.M)


def launcher_systems():
    out = subprocess.run([sys.executable, "-I", LAUNCHER_TABLE, "--format", "systems"],
                         capture_output=True, text=True, check=True).stdout
    return {s for s in out.split() if s}


def firstboot_systems(text):
    """The ROM folders the first-boot script creates (empty set when the loop is gone)."""
    m = FIRSTBOOT_LOOP.search(text)
    return set(m.group(1).split()) if m else set()


def rom_systems():
    with open(FIRSTBOOT, encoding="utf-8") as f:
        return launcher_systems() | firstboot_systems(f.read())


def missing_art(theme_dir, systems):
    """Problems (empty = ok): each system needs _inc/systems/<s>.png and _inc/chips/<s>.png."""
    out = []
    for s in sorted(systems):
        for sub in ("systems", "chips"):
            if not os.path.isfile(os.path.join(theme_dir, "_inc", sub, s + ".png")):
                out.append("%s: no _inc/%s/%s.png" % (s, sub, s))
    return out


def test_sources_are_read():
    launch = launcher_systems()
    check({"arcade", "atari2600", "naomi", "neogeo"} <= launch, "launcher table lists the ROM systems (%d)" % len(launch))
    with open(FIRSTBOOT, encoding="utf-8") as f:
        fb = firstboot_systems(f.read())
    check("atari2600" in fb and "arcade" in fb, "first-boot ROM folder loop is parsed (%d folders)" % len(fb))


def test_every_rom_system_has_badge_and_chip():
    problems = missing_art(THEME, rom_systems())
    check(not problems, "every /data/roms system has a badge and a chip (%s)" % ("; ".join(problems) or "ok"))


def test_BROKEN_missing_badge_and_chip_are_reported():
    """Broken case: a theme copy without the Atari 2600 badge and the NAOMI chip must FAIL the real check."""
    with tempfile.TemporaryDirectory() as d:
        inc = os.path.join(d, "_inc")
        for sub in ("systems", "chips"):
            shutil.copytree(os.path.join(THEME, "_inc", sub), os.path.join(inc, sub))
        os.remove(os.path.join(inc, "systems", "atari2600.png"))
        os.remove(os.path.join(inc, "chips", "naomi.png"))
        problems = missing_art(d, rom_systems())
        check(problems == ["atari2600: no _inc/systems/atari2600.png", "naomi: no _inc/chips/naomi.png"],
              "BROKEN CASE: missing badge and chip are reported (%s)" % problems)
    check(missing_art(THEME, {"nosuchsystem"}) == ["nosuchsystem: no _inc/systems/nosuchsystem.png",
                                                   "nosuchsystem: no _inc/chips/nosuchsystem.png"],
          "BROKEN CASE: a ROM folder the theme never heard of fails")


def test_firstboot_folders_are_the_launcher_systems():
    """#296: first boot makes a folder for exactly the systems the launcher runs: no dead folder (psp, gb, gba),
    none missing (atari2600, fbneo, pcengine, saturn, segacd)."""
    with open(FIRSTBOOT) as f:
        fb = firstboot_systems(f.read())
    check(fb == launcher_systems(), "firstboot folders == launcher systems (extra %s, missing %s)"
          % (sorted(fb - launcher_systems()), sorted(launcher_systems() - fb)))
    check(firstboot_systems('for s in snes psp; do\n  mkdir -p "$DATA/roms/$s"\ndone\n') != launcher_systems(),
          "BROKEN: a dead folder (psp) does not match the launcher")


def test_BROKEN_firstboot_parse():
    check(firstboot_systems('for s in a b; do\n  mkdir -p "$DATA/roms/$s"\ndone\n') == {"a", "b"},
          "first-boot loop parser reads a plain loop")
    check(firstboot_systems("echo nothing here\n") == set(), "BROKEN CASE: no loop = no folders, not a crash")


if __name__ == "__main__":
    main(globals())
