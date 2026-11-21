#!/usr/bin/env python3
"""End-to-end preview: collgen.py nested output -> ES-DE 3.5.0 in the harness container (run with
~/.venvs/badges/bin/python: Pillow makes placeholder covers). Usage: e2e.py OUTDIR "STEPS" [--min-decade N]
Fixture = skills/collections/tests/fixtures (tiny, synthetic); ROMs and covers are empty/fake."""
import os, shutil, subprocess, sys
from PIL import Image
H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(H, "..", "..", ".."))
FIX = os.path.join(ROOT, "skills", "collections", "tests", "fixtures")
CACHE = os.path.expanduser("~/.cache/coin-dojo/preview")
out, steps = os.path.abspath(sys.argv[1]), sys.argv[2]
scr = os.path.join(CACHE, "e2e-%d" % os.getpid())
shutil.rmtree(scr, ignore_errors=True)
for d in ("roms", "media", "gen", "out", "theme"):
    os.makedirs(os.path.join(scr, d))
sys.path.insert(0, os.path.join(ROOT, "skills", "collections"))
import collgen, nested  # noqa
systems = collgen.load_gamelists(os.path.join(FIX, "gamelists"))
for s, rows in systems.items():
    os.makedirs(os.path.join(scr, "roms", s))
    for r in rows:
        open(os.path.join(scr, "roms", s, r["fn"]), "w").close()
        p = os.path.join(scr, "media", s, "covers", os.path.splitext(r["fn"])[0] + ".png")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        Image.new("RGB", (320, 240), (40 + len(r["fn"]) * 9 % 200, 70, 130)).save(p)
rc = subprocess.call([sys.executable, os.path.join(ROOT, "skills/collections/collgen.py"), "nested",
                      "--gamelists", os.path.join(FIX, "gamelists"), "--picks", os.path.join(FIX, "picks.html"),
                      "--out", os.path.join(scr, "gen"), "--rom-root", "/preview/roms", "--nested-root", "/preview/nested",
                      "--launcher", "/spike/wrap.sh", "--media", os.path.join(scr, "media"),
                      "--media-target", "/preview/media-real", "--min-decade", "1", "--apply"] + sys.argv[3:])
assert rc == 0
home = os.path.join(scr, "gen", "esde-home", "ES-DE")
os.makedirs(home)
for d in ("custom_systems", "gamelists", "downloaded_media"):
    shutil.move(os.path.join(scr, "gen", d), os.path.join(home, d))
theme = os.path.join(scr, "theme", "spike-theme")
shutil.copytree(os.path.join(ROOT, "themes/coin-dojo"), theme)
for v, t in (("genres", "GENRES"), ("decades", "DECADES"), ("systems", "SYSTEMS")):
    subprocess.run([sys.executable, os.path.join(ROOT, "skills/badges/badge.py"), os.path.join(theme, "_inc/systems", v + ".png"), t],
                   check=True, stdout=subprocess.DEVNULL)
os.makedirs(os.path.join(home, "themes"))
os.symlink("/preview/theme/spike-theme", os.path.join(home, "themes", "spike-theme"))
os.makedirs(os.path.join(home, "settings"))
open(os.path.join(home, "settings", "es_settings.xml"), "w").write(
    '<?xml version="1.0"?>\n<string name="ROMDirectory" value="/preview/roms" />\n<string name="Theme" value="spike-theme" />\n'
    '<string name="ThemeAspectRatio" value="4:3" />\n<string name="ThemeTransitions" value="instant" />\n'
    '<string name="CollectionSystemsAuto" value="" />\n<string name="CollectionSystemsCustom" value="" />\n'
    '<string name="UIMode" value="full" />\n<string name="MenuOpeningEffect" value="none" />\n'
    '<string name="ScreensaverType" value="none" />\n<bool name="InputDeviceNotifications" value="false" />\n')
os.makedirs(out, exist_ok=True)
subprocess.run(["docker", "run", "--rm", "--platform", "linux/arm64", "-e", "STEPS=" + steps,
                "-v", CACHE + "/es-de/squashfs-root:/es:ro", "-v", scr + "/gen/esde-home:/preview/home",
                "-v", scr + "/roms:/preview/roms:ro", "-v", scr + "/gen/roms_nested:/preview/nested:ro",
                "-v", scr + "/media:/preview/media-real:ro", "-v", scr + "/theme:/preview/theme:ro",
                "-v", scr + "/out:/preview/out", "-v", H + ":/spike:ro", "-v", ROOT + "/skills/collections/launch.py:/launch.py:ro",
                "coin-dojo-esde-spike:1", "/spike/run-steps.sh"], check=True)
for f in os.listdir(os.path.join(scr, "out")):
    shutil.copy(os.path.join(scr, "out", f), out)
shutil.rmtree(scr)
