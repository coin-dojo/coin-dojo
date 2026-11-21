#!/usr/bin/env python3
"""Spike fixture builder (needs Pillow for placeholder PNGs: run with ~/.venvs/badges/bin/python).

build_spike.py SCRATCH THEME_SRC HIDE [--home-theme-name N]
  HIDE = none | exclusive | override | romdir   (how the real systems are hidden)
Everything is synthetic: empty ROM files, solid-colour placeholder PNGs. Container paths are /preview/...
"""
import os, shutil, subprocess, sys
from PIL import Image, ImageDraw

REAL = {"cps1": ["1941.zip", "ffight.zip", "ghouls.zip"],
        "cps2": ["ssf2.zip", "19xx.zip", "sfa2.zip"],
        "dreamcast": ["Cannon Spike.chd", "Bangai-O.chd"]}
NAMES = {"1941.zip": "1941: Counter Attack", "ffight.zip": "Last Round", "ghouls.zip": "Ghouls'n Ghosts",
         "ssf2.zip": "Super Avenue Fighter II", "19xx.zip": "19XX", "sfa2.zip": "Avenue Fighter Zero 2",
         "Cannon Spike.chd": "Cannon Spike", "Bangai-O.chd": "Bangai-O"}
# virtual system -> folder -> real (system, file)
VIRT = {
    "must-play": {"": [("cps2", "ssf2.zip"), ("cps1", "ffight.zip"), ("cps2", "sfa2.zip")]},
    "genres": {"fighters": [("cps2", "ssf2.zip"), ("cps2", "sfa2.zip")],
               "shmups": [("cps2", "19xx.zip"), ("cps1", "1941.zip"), ("dreamcast", "Cannon Spike.chd")]},
    "decades": {"1990-94": [("cps2", "ssf2.zip"), ("cps2", "19xx.zip"), ("cps1", "ffight.zip")],
                "1995-99": [("cps2", "sfa2.zip")]},
    "systems": {"cps1": [("cps1", f) for f in REAL["cps1"]], "cps2": [("cps2", f) for f in REAL["cps2"]],
                "dreamcast": [("dreamcast", f) for f in REAL["dreamcast"]]},
}
VNAME = {"must-play": "MUST PLAY", "genres": "GENRES", "decades": "DECADES", "systems": "SYSTEMS"}
TITLES = {"fighters": "Fighters", "shmups": "Shmups", "1990-94": "1990-94", "1995-99": "1995-99",
          "cps1": "CP System", "cps2": "CP System II", "dreamcast": "Sega Dreamcast"}


def png(path, text, color):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im = Image.new("RGB", (320, 240), color)
    ImageDraw.Draw(im).text((10, 100), text, fill=(255, 255, 255))
    im.save(path)


def main(scratch, theme_src, hide):
    for d in ("roms", "home", "theme"):
        shutil.rmtree(os.path.join(scratch, d), ignore_errors=True)
    roms = os.path.join(scratch, "roms")
    home = os.path.join(scratch, "home", "ES-DE")
    for s, files in REAL.items():
        os.makedirs(os.path.join(roms, s))
        for f in files:
            open(os.path.join(roms, s, f), "w").close()
    nested = os.path.join(roms, "_nested")
    media = os.path.join(home, "downloaded_media")
    systems_xml = []
    for v, folders in VIRT.items():
        gl = ["<?xml version=\"1.0\"?>", "<gameList>"]
        for folder, games in folders.items():
            d = os.path.join(nested, v, folder)
            os.makedirs(d, exist_ok=True)
            if folder:
                gl.append("\t<folder><path>./%s</path><name>%s</name><desc>Synthetic folder %s</desc></folder>"
                          % (folder, TITLES[folder], folder))
                png(os.path.join(media, v, "covers", folder + ".png"), "FOLDER " + folder, (120, 60, 20))
            for sysname, f in games:
                os.symlink("/preview/roms/%s/%s" % (sysname, f), os.path.join(d, f))  # absolute, like the cabinet's /data/roms
                rel = ("./%s/%s" % (folder, f)) if folder else "./" + f
                gl.append("\t<game><path>%s</path><name>%s</name><desc>Synthetic game.</desc>"
                          "<genre>x</genre><releasedate>19950101T000000</releasedate></game>" % (rel, NAMES[f]))
                mrel = ("%s/%s" % (folder, os.path.splitext(f)[0])) if folder else os.path.splitext(f)[0]
                png(os.path.join(media, v, "covers", mrel + ".png"), NAMES[f], (30, 60, 120))
                png(os.path.join(media, v, "screenshots", mrel + ".png"), NAMES[f] + " SHOT", (30, 120, 60))
        gl.append("</gameList>")
        os.makedirs(os.path.join(home, "gamelists", v))
        open(os.path.join(home, "gamelists", v, "gamelist.xml"), "w").write("\n".join(gl) + "\n")
        path = "%ROMPATH%/" + (v if hide == "romdir" else "_nested/" + v)
        if os.environ.get("ABS"):
            path = "/preview/roms/_nested/" + v  # absolute <path> test
        systems_xml.append(
            "  <system>\n    <name>%s</name>\n    <fullname>%s</fullname>\n    <path>%s</path>\n"
            "    <extension>.zip .chd</extension>\n    <command>/spike/wrap.sh %%ROM%%</command>\n"
            "    <platform>arcade</platform>\n    <theme>%s</theme>\n    <systemsortname>%d-%s</systemsortname>\n  </system>"
            % (v, VNAME[v], path, v, list(VIRT).index(v), v))
    if hide == "override":
        for s in REAL:
            os.makedirs(os.path.join(roms, "_hidden", s))  # an EMPTY dir, as the ticket proposed
            systems_xml.append(
                "  <system>\n    <name>%s</name>\n    <fullname>%s</fullname>\n    <path>%%ROMPATH%%/_hidden/%s</path>\n"
                "    <extension>.zip .chd</extension>\n    <command>true %%ROM%%</command>\n"
                "    <platform>arcade</platform>\n    <theme>%s</theme>\n  </system>" % (s, s, s, s))
    os.makedirs(os.path.join(home, "custom_systems"))
    open(os.path.join(home, "custom_systems", "es_systems.xml"), "w").write(
        "<?xml version=\"1.0\"?>\n%s<systemList>\n%s%s\n</systemList>\n"
        % ("<loadExclusive/>\n" if hide == "exclusive" and os.environ.get("EXCL") != "inside" else "",
           "  <loadExclusive/>\n" if hide == "exclusive" and os.environ.get("EXCL") == "inside" else "",
           "\n".join(systems_xml)))
    # theme copy with spike badges (the repo theme is never edited)
    theme = os.path.join(scratch, "theme", "spike-theme")
    shutil.copytree(theme_src, theme)
    badge = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "badges", "badge.py")
    for v, text in (("genres", "GENRES"), ("decades", "DECADES"), ("systems", "SYSTEMS")):
        subprocess.run([sys.executable, badge, os.path.join(theme, "_inc", "systems", v + ".png"), text], check=True)
        shutil.copy(os.path.join(theme_src, "_inc", "art", "backdrops", "cps2.png"),
                    os.path.join(theme, "_inc", "art", "backdrops", v + ".png")) if os.path.exists(
            os.path.join(theme_src, "_inc", "art", "backdrops", "cps2.png")) else None
    os.makedirs(os.path.join(home, "themes"))
    os.symlink("/preview/theme/spike-theme", os.path.join(home, "themes", "spike-theme"))
    os.makedirs(os.path.join(home, "settings"))
    romdir = "/preview/roms/_nested" if hide == "romdir" else "/preview/roms"
    open(os.path.join(home, "settings", "es_settings.xml"), "w").write(
        '<?xml version="1.0"?>\n<string name="ROMDirectory" value="%s" />\n'
        '<string name="Theme" value="spike-theme" />\n<string name="ThemeAspectRatio" value="4:3" />\n'
        '<string name="ThemeTransitions" value="instant" />\n<string name="CollectionSystemsAuto" value="" />\n'
        '<string name="CollectionSystemsCustom" value="" />\n<string name="StartupSystem" value="" />\n'
        '<string name="UIMode" value="full" />\n<string name="MenuOpeningEffect" value="none" />\n'
        '<string name="ScreensaverType" value="none" />\n<bool name="InputDeviceNotifications" value="false" />\n'
        '<bool name="RunInBackground" value="false" />\n<int name="ScreensaverTimer" value="0" />\n' % romdir)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[3] not in ("none", "exclusive", "override", "romdir"):
        sys.exit(__doc__)
    main(*sys.argv[1:])
