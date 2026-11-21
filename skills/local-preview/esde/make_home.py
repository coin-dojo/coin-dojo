#!/usr/bin/env python3
"""Build the throwaway ES-DE home + fake ROM tree for the headless preview (stdlib only).

make_home.py <fixture-dir> <theme-dir> <out-root> [--theme-name NAME]

Creates under <out-root>:
  home/ES-DE/{themes/<name>,gamelists,collections,settings/es_settings.xml}
  roms/<system>/<empty placeholder files named after each gamelist <path>>
  media/               (empty: no box art is ever shipped)
The theme is symlinked (target string used as-is, e.g. the in-container mount
point /preview/theme), never copied. es_settings.xml is written from scratch here
(template keys only; the cabinet's real file holds a scraper password and is never read).
"""
import os, re, shutil, sys, xml.etree.ElementTree as ET

SETTINGS = """<?xml version="1.0"?>
<string name="ROMDirectory" value="{roms}" />
<string name="MediaDirectory" value="{media}" />
<string name="Theme" value="{theme}" />
<string name="ThemeAspectRatio" value="4:3" />
<string name="ThemeVariant" value="" />
<string name="ThemeColorScheme" value="" />
<string name="ThemeTransitions" value="instant" />
<string name="ThemeLanguage" value="automatic" />
<string name="CollectionSystemsAuto" value="" />
<string name="CollectionSystemsCustom" value="{collections}" />
<string name="StartupSystem" value="must-play" />
<string name="CollectionCustomGrouping" value="never" />
<string name="UIMode" value="full" />
<string name="MenuOpeningEffect" value="none" />
<string name="ScreensaverType" value="none" />
<bool name="ScreensaverControls" value="false" />
<bool name="FavoritesAddButton" value="false" />
<bool name="FavoritesStar" value="false" />
<bool name="FavoritesFirst" value="false" />
<bool name="DebugSkipMissingThemeFilesCustomCollections" value="true" />
<bool name="InputDeviceNotifications" value="false" />
<bool name="RunInBackground" value="false" />
<int name="ScreensaverTimer" value="0" />
"""


def game_paths(gamelist):
    root = ET.parse(gamelist).getroot()
    return [p.text.strip() for p in root.iter("path") if p.text]


def build(fixture, theme, out, theme_name=None):
    theme_name = theme_name or os.path.basename(theme.rstrip("/"))
    home = os.path.join(out, "home", "ES-DE")
    roms = os.path.join(out, "roms")
    media = os.path.join(out, "media")
    for d in (home, roms, media, os.path.join(home, "themes"), os.path.join(home, "settings"),
              os.path.join(home, "collections")):
        os.makedirs(d, exist_ok=True)
    if os.path.isdir(os.path.join(fixture, "media")):          # optional placeholder media (<sys>/<type>/<stem>.png)
        shutil.copytree(os.path.join(fixture, "media"), media, dirs_exist_ok=True)
    link = os.path.join(home, "themes", theme_name)
    if os.path.lexists(link):
        os.remove(link)
    os.symlink(theme, link)
    systems = []
    gl_root = os.path.join(fixture, "gamelists")
    for system in sorted(os.listdir(gl_root)):
        src = os.path.join(gl_root, system, "gamelist.xml")
        if not os.path.isfile(src):
            continue
        systems.append(system)
        os.makedirs(os.path.join(home, "gamelists", system), exist_ok=True)
        with open(src, "rb") as f, open(os.path.join(home, "gamelists", system, "gamelist.xml"), "wb") as g:
            g.write(f.read())
        os.makedirs(os.path.join(roms, system), exist_ok=True)
        for p in game_paths(src):
            open(os.path.join(roms, system, os.path.basename(p)), "w").close()
    colls = []
    cdir = os.path.join(fixture, "collections")
    for name in sorted(os.listdir(cdir)):
        m = re.fullmatch(r"custom-(.+)\.cfg", name)
        if not m:
            continue
        colls.append(m.group(1))
        with open(os.path.join(cdir, name)) as f, open(os.path.join(home, "collections", name), "w") as g:
            g.write(f.read())
    with open(os.path.join(home, "settings", "es_settings.xml"), "w") as f:
        f.write(SETTINGS.format(roms="/preview/roms", media="/preview/media", theme=theme_name,
                                collections=",".join(colls)))
    return systems, colls


if __name__ == "__main__":
    a = [x for x in sys.argv[1:]]
    name = None
    if "--theme-name" in a:
        i = a.index("--theme-name")
        name = a[i + 1]
        del a[i:i + 2]
    if len(a) != 3:
        sys.exit(__doc__)
    s, c = build(a[0], a[1], a[2], name)
    print("systems:", ",".join(s), "collections:", ",".join(c))
