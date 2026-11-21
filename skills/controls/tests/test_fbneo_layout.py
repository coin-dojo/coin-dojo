#!/usr/bin/env python3
"""FBNeo on the cabinet = Batocera's default (the maintainer 2026-11-15: "just assume from Batocera ... for a fight stick like ours").

Sources (pinned):
  Batocera batocera-43.1, package/batocera/core/batocera-configgen/configgen/configgen/generators/libretro/
    libretroConfig.py L241-243: input_libretro_device_p1/p2 = '1' (RetroPad); fbneo only gets a device for guns
      (L783, applied under `if system.config.use_guns`, L799-800). libretroOptions.py _fbneo_options L1990-2030:
      no layout option. No FBNeo remap anywhere in configgen.
    libretroControllers.py L112-115: ES a/b/x/y/pageup/pagedown -> RetroPad a/b/x/y/l/r 1:1 (default altlayout).
  libretro/FBNeo 3afad87ed555f6bfda2be3f4763b37068fd4ed86, src/burner/libretro/:
    retro_input.h L35-37: RETROPAD_CLASSIC = RETRO_DEVICE_ANALOG, 6PANEL/MODERN = subclasses.
    retro_input.cpp L2980-2990: any other device (Batocera's '1') is forced to RETROPAD_CLASSIC.
      L59-81: Classic FIRE01..06 = B A Y X R L; positional 1ST/2ND/3RD_COL_TOP/BOTTOM = Y X L / B A R.
      L183-205 + L397-402: P1 Weak/Medium/Strong Punch + Kick (or CPS-2 with >= 5 buttons) = bStreetFighterLayout,
      L2336-2356: fire 1..6 -> 1ST_COL_TOP 2ND_COL_TOP 3RD_COL_TOP 1ST_COL_BOTTOM 2ND_COL_BOTTOM 3RD_COL_BOTTOM.
    src/burn/drv/pre90s/d_sf.cpp L62-67: sf's P1 inputs are Weak/Medium/Strong Punch/Kick = fire 1..6.
  Cabinet RetroArch autoconfig (skills/build-image/scripts/chroot-setup.sh, same on the cabinet 2026-11-15):
    RetroPad y=js4 x=js0 l=js5 b=js3 a=js2 r=js1; panel js: LP4 MP0 HP5 LK3 MK2 HK1.
Run: python3 skills/controls/tests/test_fbneo_layout.py"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "lib"))
from hktest import main  # noqa: E402  (shared runner: one PASS per test, counted RESULT line, #137)
import controls  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CHROOT = os.path.join(REPO, "skills", "build-image", "scripts", "chroot-setup.sh")
TWEAKS = os.path.join(REPO, "ansible", "roles", "cabinet_tweaks", "defaults", "main.yml")
FBN_REMAPS = os.path.join(REPO, "skills", "controls", "remaps", "FinalBurn Neo")

PANEL_JS = {"LP": 4, "MP": 0, "HP": 5, "LK": 3, "MK": 2, "HK": 1}
# FBNeo Classic (the device Batocera ends up with): fire number -> RetroPad button
CLASSIC_GENERIC = {1: "b", 2: "a", 3: "y", 4: "x", 5: "r", 6: "l"}
CLASSIC_SF = {1: "y", 2: "x", 3: "l", 4: "b", 5: "a", 6: "r"}
# what Modern (517) would do instead (3RD_COL_BOTTOM = R2, 3RD_COL_TOP = R): only used by a broken case
MODERN_GENERIC = {1: "b", 2: "a", 3: "y", 4: "x", 5: "r2", 6: "r"}
SF_NAMES = {1: "LP", 2: "MP", 3: "HP", 4: "LK", 5: "MK", 6: "HK"}   # Weak/Medium/Strong Punch, Weak/Medium/Strong Kick
# fbneo-dir games that trip FBNeo's six-button fighter layout (driver scan at FBNeo 3afad87 over the cabinet's 413 ROMs:
# sf = pre90s/d_sf.cpp, gblchmp = taito/d_taitof3.cpp; no CPS-2 sets live in the fbneo dir)
SF6_GAMES = {"sf", "gblchmp"}
# systems that run the FBNeo core but have their own tested layouts: a core-wide remap would change them
OTHER_FBNEO_SYSTEMS = ("cps1", "cps2", "cps3", "neogeo")
BATOCERA_FBNEO_DEVICES = {"1", "5"}     # RetroPad (forced to Classic) or Classic itself


def autoconfig(text):
    """RetroPad button -> js index from the input_<b>_btn lines of the autoconfig heredoc."""
    out = {}
    for b in ("y", "x", "l", "b", "a", "r"):
        m = re.search(r'^input_%s_btn = "(\d+)"' % b, text, re.M)
        if m:
            out[b] = int(m.group(1))
    return out


def card(fire_to_pad, pad_to_js, label):
    """Panel order LP MP HP LK MK HK -> label of the fire button that lands there ("" = none)."""
    js_to_fire = {pad_to_js[pad]: n for n, pad in fire_to_pad.items() if pad in pad_to_js}
    return [label(js_to_fire[PANEL_JS[p]]) if PANEL_JS[p] in js_to_fire else "" for p in controls.POSITIONS]


def generic_problems(layout, pad_to_js, fire_to_pad=CLASSIC_GENERIC):
    want = card(fire_to_pad, pad_to_js, str)
    return [] if layout["buttons"] == want else ["fbneo card %r != FBNeo Classic on the panel %r" % (layout["buttons"], want)]


def sf_problems(pad_to_js, fire_to_pad=CLASSIC_SF):
    got = card(fire_to_pad, pad_to_js, SF_NAMES.get)
    want = controls.POSITIONS
    return [] if got == want else ["SF layout on the panel %r != %r" % (got, want)]


def remap_problems(entries, remap_dir_files):
    """No FBNeo remap may touch more than its own game / the neogeo dir: no core remap, no cps/fbneo dir remap."""
    p = []
    for core, f in entries:
        if core != "FinalBurn Neo":
            continue
        stem = f[:-4]
        if stem == "FinalBurn Neo":
            p.append("core-wide FinalBurn Neo remap would change cps1/2/3 (tested) too: %s" % f)
        elif stem in ("cps1", "cps2", "cps3", "fbneo"):
            p.append("content-dir remap %s would change a tested system or Batocera's default" % f)
    for f in remap_dir_files:
        if f[:-4] in ("FinalBurn Neo", "cps1", "cps2", "cps3", "fbneo"):
            p.append("remap file %s exists" % f)
    return p


def device_problems(texts):
    p = []
    for name, t in texts.items():
        for m in re.finditer(r'^input_libretro_device_p[12] = "(\d+)"', t, re.M):
            if m.group(1) not in BATOCERA_FBNEO_DEVICES:
                p.append("%s sets device %s (Batocera keeps RetroPad -> FBNeo Classic)" % (name, m.group(1)))
    return p


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _tweak_entries(text):
    return re.findall(r'\{\s*core:\s*"([^"]+)",\s*file:\s*"([^"]+)"\s*\}', text)


def _remap_texts():
    return {f: _read(os.path.join(FBN_REMAPS, f)) for f in sorted(os.listdir(FBN_REMAPS)) if f.endswith(".rmp")}


PAD = autoconfig(_read(CHROOT))


# --- tests ------------------------------------------------------------------------------------------------------
def test_autoconfig_is_the_panel():
    assert PAD == {"y": 4, "x": 0, "l": 5, "b": 3, "a": 2, "r": 1}, PAD


def test_fbneo_card_is_batocera_classic_numbering():
    lay = controls.LAYOUTS["fbneo"]
    assert generic_problems(lay, PAD) == [], generic_problems(lay, PAD)
    assert lay["buttons"] == ["3", "4", "6", "1", "2", "5"]
    assert lay["tested"] is True, "the maintainer asked to mark the fbneo layout tested (2026-11-15)"


def test_sf_layout_is_lp_mp_hp_over_lk_mk_hk():
    assert sf_problems(PAD) == [], sf_problems(PAD)


def test_six_button_fighters_show_the_arcade6_card():
    for stem in sorted(SF6_GAMES):
        assert controls.layout_for("fbneo", stem) == "arcade6", stem
    assert controls.LAYOUTS["arcade6"]["buttons"] == controls.POSITIONS
    assert controls.LAYOUTS["arcade6"]["tested"] is True


def test_other_fbneo_systems_keep_their_layouts():
    assert {s: controls.SYSTEM_LAYOUT[s] for s in OTHER_FBNEO_SYSTEMS} == \
        {"cps1": "arcade6", "cps2": "arcade6", "cps3": "arcade6", "neogeo": "neogeo"}
    assert controls.SYSTEM_LAYOUT["fbneo"] == "fbneo"


def test_no_remap_changes_fbneo_or_cps():
    files = [f for f in os.listdir(FBN_REMAPS) if f.endswith(".rmp")]
    p = remap_problems(_tweak_entries(_read(TWEAKS)), files)
    assert p == [], p
    assert sorted(files) == ["bwidow.rmp", "neogeo.rmp", "robotron.rmp", "smashtv.rmp", "totcarn.rmp"], files


def test_fbneo_remaps_keep_the_classic_device():
    p = device_problems(_remap_texts())
    assert p == [], p


# broken cases: each must be caught
def test_broken_core_remap_entry_fails():
    entries = _tweak_entries(_read(TWEAKS)) + [("FinalBurn Neo", "FinalBurn Neo.rmp")]
    assert remap_problems(entries, []) != [], "BROKEN: core-wide FBNeo remap accepted"
    assert remap_problems([("FinalBurn Neo", "cps2.rmp")], []) != [], "BROKEN: cps2 dir remap accepted"
    assert remap_problems([], ["FinalBurn Neo.rmp"]) != [], "BROKEN: core remap file accepted"


def test_broken_card_numbering_fails():
    for bad in (["1", "2", "3", "4", "5", "6"], ["3", "4", "5", "1", "2", "6"], ["LP", "MP", "HP", "LK", "MK", "HK"]):
        lay = dict(controls.LAYOUTS["fbneo"], buttons=bad)
        assert generic_problems(lay, PAD) != [], "BROKEN card accepted: %r" % bad


def test_broken_modern_device_fails():
    # Modern moves Fire5/6 to R2/R: R2 is not on the panel, so the card no longer matches
    assert generic_problems(controls.LAYOUTS["fbneo"], PAD, MODERN_GENERIC) != []
    assert device_problems({"x.rmp": 'input_libretro_device_p1 = "517"\n'}) != [], "BROKEN: Modern device accepted"


def test_broken_panel_rewired_fails():
    swapped = dict(PAD, y=PAD["x"], x=PAD["y"])
    assert sf_problems(swapped) != [], "BROKEN: LP/MP swapped panel accepted"
    assert generic_problems(controls.LAYOUTS["fbneo"], swapped) != []


def test_broken_sf6_game_without_card_fails():
    saved = dict(controls.GAME_LAYOUT)
    try:
        del controls.GAME_LAYOUT[("fbneo", "gblchmp")]
        assert controls.layout_for("fbneo", "gblchmp") != "arcade6", "BROKEN: gblchmp still arcade6"
    finally:
        controls.GAME_LAYOUT.clear()
        controls.GAME_LAYOUT.update(saved)


if __name__ == "__main__":
    main(globals(), per_test=True)
