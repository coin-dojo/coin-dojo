#!/usr/bin/env python3
"""Tests for skills/controls/controls.py (stdlib parts; render needs Pillow and is checked by eye).
Run: python3 skills/controls/tests/test_controls.py  (or pytest)"""
import glob
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "lib"))
from hktest import main  # noqa: E402  (shared runner: one PASS per test, counted RESULT line, #137)
import controls  # noqa: E402

THEME = os.path.join(os.path.dirname(__file__), "..", "..", "..", "themes", "coin-dojo")

GL = """<?xml version="1.0"?>
<alternativeEmulator>
\t<label>Genesis Plus GX</label>
</alternativeEmulator>
<gameList>
\t<game>
\t\t<path>./Eternal Champions (USA).zip</path>
\t\t<name>Eternal Champions</name>
\t\t<players>1-2</players>
\t</game>
\t<game>
\t\t<path>./Sonic &amp; Knuckles (World).zip</path>
\t\t<name>Sonic &amp; Knuckles</name>
\t\t<controller>gamepad_generic</controller>
\t\t<genre>Platform</genre>
\t</game>
</gameList>
"""


def test_split_title():
    assert controls.split_title("Avenue Fighter III: Third Round - Fight for the Future") == \
        ("Avenue Fighter III", "Third Round - Fight for the Future")
    assert controls.split_title("Super Spacefortress Macross II / Chou-Jikuu Yousai Macross II") == \
        ("Super Spacefortress Macross II", "Chou-Jikuu Yousai Macross II")
    assert controls.split_title("World of Illusion Starring Mickey Mouse and Donald")[1] == ""
    assert controls.split_title(": odd") == (": odd", "")          # nothing before the separator


def test_layouts():
    assert controls.layout_for("genesis", "x") == "genesis6"
    assert controls.layout_for("fbneo", "robotron") == "twinstick"
    assert controls.layout_for("fbneo", "1942") == "fbneo"
    assert controls.layout_for("n64", "x") == "n64"
    assert controls.layout_for("gb", "x") is None                      # not mapped: no card
    for name, lay in controls.LAYOUTS.items():
        assert len(lay["buttons"]) in (0, 6), name
    ids = [lay["controller"] for lay in controls.LAYOUTS.values()]
    assert len(ids) == len(set(ids)), "two layouts share a controller type"


def test_theme_maps_every_layout():
    xml = open(os.path.join(THEME, "theme.xml")).read()
    for lay, d in controls.LAYOUTS.items():
        assert '<customControllerIcon controller="%s">./_inc/controls/%s.png' % (d["controller"], lay) in xml, lay
        assert os.path.isfile(os.path.join(THEME, "_inc", "controls", lay + ".png")), lay


def test_set_controllers_inserts_replaces_keeps_fields():
    new, n = controls.set_controllers(GL, "genesis")
    assert n == 2
    assert new.count("<controller>gamepad_sega_md_6_buttons</controller>") == 2
    assert "gamepad_generic" not in new
    assert "<alternativeEmulator>" in new and "Sonic &amp; Knuckles" in new and "<genre>Platform</genre>" in new
    assert "\t\t<players>1-2</players>\n\t\t<controller>gamepad_sega_md_6_buttons</controller>\n\t</game>" in new
    again, n2 = controls.set_controllers(new, "genesis")
    assert n2 == 0 and again == new                                  # idempotent


def test_set_controllers_removes_when_unmapped():
    new, n = controls.set_controllers(GL, "gb")                     # gb has no layout
    assert n == 1 and "<controller>" not in new


def test_gamelists_skips_virtual_lists():
    with tempfile.TemporaryDirectory() as d:
        for s in ("genesis", "must-play"):
            os.makedirs(os.path.join(d, s))
            open(os.path.join(d, s, "gamelist.xml"), "w").write(GL)
        assert list(controls.system_gamelists(d)) == ["genesis"]
        controls.main(["gamelists", "--gamelists", d])                  # dry run writes nothing
        assert open(os.path.join(d, "genesis", "gamelist.xml")).read() == GL
        controls.main(["gamelists", "--gamelists", d, "--apply"])
        assert "gamepad_sega_md_6_buttons" in open(os.path.join(d, "genesis", "gamelist.xml")).read()
        assert open(os.path.join(d, "must-play", "gamelist.xml")).read() == GL


def test_series_virtual_list_is_skipped():
    # nested lists (incl. "series", #55) carry <controller> copies; treating one as a real system would strip them
    with tempfile.TemporaryDirectory() as d:
        for s in ("genesis", "series"):
            os.makedirs(os.path.join(d, s))
            open(os.path.join(d, s, "gamelist.xml"), "w").write(GL)
        assert list(controls.system_gamelists(d)) == ["genesis"]
        controls.main(["gamelists", "--gamelists", d, "--apply"])
        assert open(os.path.join(d, "series", "gamelist.xml")).read() == GL
        assert "series" in controls.VIRTUALS


def test_merge_override_keeps_foreign_lines():
    frag = controls.override_text("/data/coin-dojo/controls/overlays/fbneo/smashtv.cfg")
    old = 'log_verbosity = "true"\ninput_overlay_enable = "false"\n'
    new = controls.merge_override(old, frag)
    assert new.startswith('log_verbosity = "true"\n')
    assert new.count("input_overlay_enable") == 1
    assert controls.merge_override(new, frag) == new                 # idempotent
    assert controls.merge_override(None, frag) == frag


def test_overlays_off_while_ra120_deadlocks():
    assert controls.OVERLAYS_ON is False
    assert 'input_overlay_enable = "false"' in controls.override_text("/x.cfg")
    assert 'input_overlay_enable = "true"' in controls.override_text("/x.cfg", on=True)


def test_overrides_command():
    with tempfile.TemporaryDirectory() as d:
        src, dest = os.path.join(d, "src", "Stella"), os.path.join(d, "dest", "Stella")
        os.makedirs(src)
        os.makedirs(dest)
        open(os.path.join(src, "Adventure.cfg"), "w").write(controls.override_text("/x/Adventure.cfg"))
        open(os.path.join(dest, "Adventure.cfg"), "w").write('video_scale = "2"\n')
        open(os.path.join(d, "src", "._Stella"), "w").write("junk")          # macOS AppleDouble files
        open(os.path.join(src, "._Adventure.cfg"), "w").write("junk")
        controls.main(["overrides", "--src", os.path.join(d, "src"), "--dest", os.path.join(d, "dest")])
        assert open(os.path.join(dest, "Adventure.cfg")).read() == 'video_scale = "2"\n'   # dry run
        controls.main(["overrides", "--src", os.path.join(d, "src"), "--dest", os.path.join(d, "dest"), "--apply"])
        got = open(os.path.join(dest, "Adventure.cfg")).read()
        assert got.startswith('video_scale = "2"\n') and 'input_overlay = "/x/Adventure.cfg"' in got
        assert not os.path.exists(os.path.join(dest, "._Adventure.cfg"))


# A remap line we accept: a RetroPad target id (0-15 buttons, 16-23 analog half-axes: 20 R-X+, 21 R-X-,
# 22 R-Y+, 23 R-Y-), a port, or one of the default lines RetroArch writes when it saves a remap (the captured
# live files keep them as-is).
REMAP_BTN = re.compile(r'input_player[1-4]_btn_(?:b|y|a|x|l|r|l2|r2|up|down|left|right|select|start) = "(\d+)"')
REMAP_PORT = re.compile(r'input_remap_port_p([1-4]) = "([0-3])"')
REMAP_RA_DEFAULTS = re.compile(r'input_(?:libretro_device_p[1-4] = "1"|player[1-4]_analog_dpad_mode = "0"'
                               r'|turbo_(?:allow_dpad = "false"|bind = "-1"|button = "0"|duty_cycle = "0"'
                               r'|enable = "true"|mode = "0"|period = "6"))')


def remap_line_problem(ln):
    """None if the remap line is one we understand, else why not."""
    ln = ln.rstrip("\n")
    if not ln.strip() or ln.startswith("#"):
        return None
    if ln.startswith("#include") or ln.startswith("#reference"):
        return "RetroArch directive in a comment position"
    m = REMAP_BTN.fullmatch(ln)
    if m:
        return None if int(m.group(1)) <= 23 else "target id over 23"
    if REMAP_PORT.fullmatch(ln) or REMAP_RA_DEFAULTS.fullmatch(ln):
        return None
    return "unknown remap line"


def _remap_files():
    return sorted(glob.glob(os.path.join(os.path.dirname(__file__), "..", "remaps", "*", "*.rmp")))


def test_remaps_use_retropad_ids_only():
    files = _remap_files()
    assert len(files) >= 10
    for f in files:
        for ln in open(f, encoding="utf-8"):
            assert remap_line_problem(ln) is None, (f, ln, remap_line_problem(ln))
        assert open(f, encoding="utf-8").read().startswith("#"), (f, "needs a comment header saying why")


def test_broken_remap_line_is_caught():
    for bad in ('input_player1_btn_l = "R1"', 'input_player1_btn_l = "24"', 'input_player5_btn_l = "1"',
                'input_remap_port_p2 = "7"', 'input_turbo_enable = "maybe"', 'input_player1_btn_l= "1"'):
        assert remap_line_problem(bad) is not None, bad


# ---- twin stick (#59): every TWIN_STICK game has its game remap, and the remap sends user 2 to port 1 with
# the P2 d-pad on the game's fire inputs. FBNeo/N64: P1 right analog; SNES Smash TV: X B Y A diamond.
ANALOG_FIRE = {"up": 23, "down": 22, "left": 21, "right": 20}
SNES_FIRE = {"up": 9, "down": 0, "left": 1, "right": 8}


def twin_stick_problems(text, fire):
    p = []
    if not re.search(r'(?m)^input_remap_port_p2 = "0"$', text):
        p.append("user 2 does not feed core port 1")
    if re.search(r'(?m)^input_remap_port_p1 = "(?!0")', text):
        p.append("user 1 moved off core port 1")
    for d, want in sorted(fire.items()):
        m = re.search(r'(?m)^input_player2_btn_%s = "(\d+)"$' % d, text)
        if not m or int(m.group(1)) != want:
            p.append("P2 %s -> %s, want %d" % (d, m and m.group(1), want))
    return p


def _twin_remap(system, stem):
    return os.path.join(os.path.dirname(__file__), "..", "remaps", controls.CORE_DIR[system], stem + ".rmp")


def test_twin_stick_games_have_their_remap():
    assert ("fbneo", "robotron") in controls.TWIN_STICK and ("n64", "Robotron 64") in controls.TWIN_STICK
    for system, stem in sorted(controls.TWIN_STICK):
        assert controls.layout_for(system, stem) == "twinstick", (system, stem)
        path = _twin_remap(system, stem)
        assert os.path.isfile(path), path
        fire = SNES_FIRE if system == "snes" else ANALOG_FIRE
        assert twin_stick_problems(open(path, encoding="utf-8").read(), fire) == [], (path,)
    r64 = open(_twin_remap("n64", "Robotron 64"), encoding="utf-8").read()
    for b, v in (("x", 23), ("l", 11), ("a", 12), ("r", 22)):      # a game remap replaces the core one
        assert 'input_player1_btn_%s = "%d"' % (b, v) in r64, b


def test_broken_twin_stick_remaps_are_caught():
    good = open(_twin_remap("fbneo", "robotron"), encoding="utf-8").read()
    swapped = good.replace('btn_up = "23"', 'btn_up = "T"').replace('btn_down = "22"', 'btn_down = "23"') \
                  .replace('btn_up = "T"', 'btn_up = "22"')
    assert any("up" in x for x in twin_stick_problems(swapped, ANALOG_FIRE))
    own_port = good.replace('input_remap_port_p2 = "0"', 'input_remap_port_p2 = "1"')
    assert "user 2 does not feed core port 1" in twin_stick_problems(own_port, ANALOG_FIRE)
    p1_moved = good.replace('input_remap_port_p1 = "0"', 'input_remap_port_p1 = "1"')
    assert "user 1 moved off core port 1" in twin_stick_problems(p1_moved, ANALOG_FIRE)
    assert twin_stick_problems(good, SNES_FIRE) != []             # FBNeo remap is not the SNES diamond
    assert not os.path.isfile(_twin_remap("fbneo", "no-such-twin"))  # a TWIN_STICK entry without a file fails above


# ---- N64 per-game (#64): KI Gold (alt-map on, six attacks) -----------------
RETROPAD = {"b": 0, "y": 1, "select": 2, "start": 3, "up": 4, "down": 5, "left": 6, "right": 7,
            "a": 8, "x": 9, "l": 10, "r": 11, "l2": 12, "r2": 13}
PANEL_PAD = ["y", "x", "l", "b", "a", "r"]            # LP MP HP / LK MK HK -> RetroPad (autoconfig)
# Mupen64Plus-Next with mupen64plus-alt-map = "True": RetroPad id -> N64 button
ALT_MAP = {0: "A", 1: "B", 9: "C-Up", 8: "C-Down", 10: "C-Left", 11: "C-Right", 12: "Z", 13: "R", 2: "L"}
# default mode (alt-map False): RetroPad id -> N64 button (right analog 20-23 = C buttons)
STD_MAP = {0: "A", 1: "B", 10: "L", 11: "R", 12: "Z", 23: "C-Up", 22: "C-Down", 21: "C-Left", 20: "C-Right"}
KI_PANEL = ["B", "C-Left", "C-Up", "A", "C-Down", "C-Right"]   # QP MP FP / QK MK FK (C arrows: see the .rmp)
N64_DIR = os.path.join(os.path.dirname(__file__), "..", "remaps", "Mupen64Plus-Next")
KI_OPT = os.path.join(os.path.dirname(__file__), "..", "core-options", "Mupen64Plus-Next", "Killer Instinct Gold.opt")


def effective(text, player):
    out = dict(RETROPAD)
    for m in re.finditer(r'(?m)^input_player%d_btn_(\w+) = "(\d+)"$' % player, text):
        out[m.group(1)] = int(m.group(2))
    return out


def n64_panel(text, player, table):
    """What each panel button presses on the N64 (None = nothing)."""
    eff = effective(text, player)
    return [table.get(eff[b]) for b in PANEL_PAD]


def opt_problems(text):
    p, keys = [], {}
    for ln in text.splitlines():
        if not ln.strip() or ln.startswith("#"):
            continue
        m = re.fullmatch(r'(mupen64plus-[A-Za-z0-9_-]+) = "([^"]*)"', ln)
        if not m:
            p.append("bad line %r" % ln)
            continue
        if m.group(1) in keys:
            p.append("duplicate %s" % m.group(1))
        keys[m.group(1)] = m.group(2)
    if keys.get("mupen64plus-alt-map") != "True":
        p.append("mupen64plus-alt-map is not True")
    if len(keys) < 80:
        p.append("only %d options: a game .opt replaces the core .opt, copy it whole" % len(keys))
    return p


def test_ki_gold_six_attacks_with_alt_map():
    t = open(os.path.join(N64_DIR, "Killer Instinct Gold.rmp"), encoding="utf-8").read()
    for pl in (1, 2):
        assert n64_panel(t, pl, ALT_MAP) == KI_PANEL, (pl, n64_panel(t, pl, ALT_MAP))
    assert opt_problems(open(KI_OPT, encoding="utf-8").read()) == []
    assert controls.n64_games()["Killer Instinct Gold"]["labels"] == \
        ["Quick P", "Medium P", "Fierce P", "Quick K", "Medium K", "Fierce K"]


def test_broken_n64_game_remaps_are_caught():
    ki = open(os.path.join(N64_DIR, "Killer Instinct Gold.rmp"), encoding="utf-8").read()
    swapped = ki.replace('input_player1_btn_x = "10"', 'input_player1_btn_x = "9"') \
                .replace('input_player1_btn_l = "9"', 'input_player1_btn_l = "10"')
    assert n64_panel(swapped, 1, ALT_MAP) != KI_PANEL                     # MP <-> FP swapped
    assert n64_panel(ki, 1, STD_MAP) != KI_PANEL                          # without alt-map it is not 6 attacks
    opt = open(KI_OPT, encoding="utf-8").read()
    assert "mupen64plus-alt-map is not True" in opt_problems(opt.replace('alt-map = "True"', 'alt-map = "False"'))
    assert any("copy it whole" in x for x in opt_problems('mupen64plus-alt-map = "True"\n'))
    assert any("bad line" in x for x in opt_problems(opt + "mupen64plus-x = True\n"))


def test_n64_table_is_well_formed():
    t = controls.n64_games()
    assert len(t) >= 18          # 21 picks minus Worms + Bomberman 64 (removed by the maintainer 2026-11-15)
    for stem, row in t.items():
        assert len(row["labels"]) == 6, stem
        assert row["layout"] in controls.N64_LAYOUTS and row["confidence"] in controls.N64_CONFIDENCE, stem
        assert all(len(x) <= controls.LABEL_MAX for x in row["labels"]), stem
    assert "Super Smash Bros." in t                              # stem keeps its trailing dot (Super Smash Bros..zip)


def test_n64_per_game_labels_else_system_labels():
    labels, pos = controls.game_labels("n64", "Yoshi's Story")
    assert labels == ["Tongue", "Camera", "Ground pound", "Jump", "Aim egg", ""]
    assert pos == controls.N64_PAD                                # pad button printed under each action
    assert controls.game_labels("n64", "Killer Instinct Gold")[1] == controls.POSITIONS   # waits on the #64 remap
    assert controls.game_labels("n64", "Robotron 64") is None     # no labels in the row: system labels
    assert controls.game_labels("n64", "Some Unlisted Game") is None
    assert controls.game_labels("genesis", "Yoshi's Story") is None      # only n64 has per-game labels
    assert controls.layout_for("n64", "Robotron 64") == "twinstick"   # remaps/Mupen64Plus-Next/Robotron 64.rmp
    assert controls.layout_for("n64", "Some Unlisted Game") == "n64"
    assert controls.LAYOUTS["n64"]["buttons"] == controls.N64_PAD
    assert controls.CORE_DIR["n64"] == "Mupen64Plus-Next"
    assert controls.OVERLAYS_ON is False


def test_n64_every_game_stem_matches_a_gamelist_naming():
    for stem in controls.n64_games():
        controls.safe_stem(stem)


def test_broken_n64_rows_are_rejected():
    head = "\t".join(controls.N64_COLUMNS) + "\n"
    ok = "Game\tn64\tA\tB\tC\tD\tE\tF\tguess\tnote\n"
    assert list(controls.parse_n64_games("# c\n" + head + ok)) == ["Game"]
    bad = {
        "columns": ok.replace("\tnote", ""),                      # 9 columns
        "layout": ok.replace("\tn64\t", "\tn65\t"),
        "confidence": ok.replace("guess", "sure"),
        "long label": ok.replace("\tA\t", "\tThis label is far too long\t"),
        "duplicate": ok + ok,
    }
    for why, row in bad.items():
        try:
            controls.parse_n64_games(head + row, "t.tsv")
        except ValueError as e:
            assert "t.tsv:" in str(e), (why, str(e))                # names file and line
            continue
        raise AssertionError("malformed row accepted: " + why)
    try:
        controls.parse_n64_games(ok)                                # no header
    except ValueError as e:
        assert "header" in str(e)
    else:
        raise AssertionError("missing header accepted")


def test_overlay_cfg_has_blank_first_page():
    t = controls.overlay_cfg_text("sfiii.png")
    assert "overlays = 2" in t and 'overlay0_overlay = "../blank.png"' in t and 'overlay1_overlay = "sfiii.png"' in t


def test_broken_unsafe_stem_is_refused():
    for bad in ("../../etc/x", "", ".."):
        try:
            controls.safe_stem(bad)
        except ValueError:
            continue
        raise AssertionError("unsafe stem accepted: %r" % bad)


if __name__ == "__main__":
    main(globals(), per_test=True)
