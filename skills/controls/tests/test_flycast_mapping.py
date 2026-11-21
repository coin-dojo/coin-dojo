#!/usr/bin/env python3
"""Tests for the flycast 2.7 pad mappings in skills/controls/flycast/ (the maintainer 2026-11-15: SF III LP/MP swapped).

The model below re-implements, from flycast v2.7 source, how flycast builds its built-in SDL default for the cabinet
panel and how it reads a mapping file:
  core/sdl/sdl_gamepad.cpp   DefaultInputMapping (base ctor L29-47, SDL ctor L49-206, arcade gamepad L140-157,
                             Dreamcast L173-187, start/dpad/back/sticks L188-203), resetMappingToDefault L650-662
  core/sdl/sdl_mappingparser.h getBindForAxis (a missing axis = BINDTYPE_NONE; full axis takes the asked direction)
  core/input/mapping.cpp     set_button (clears the key, then drops any combo on the same code) L750-784,
                             set_axis L395-403, load() L445-588, button_list/axis_list names L32-128
  core/cfg/ini.cpp           IniFile::load L180-235 (';' comments, "k = v")
Run: python3 skills/controls/tests/test_flycast_mapping.py"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "lib"))
from hktest import main  # noqa: E402  (shared runner: one PASS per test, counted RESULT line, #137)
import controls  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DC_FILE = os.path.join(REPO, "skills", "controls", "flycast", "SDL_USB gamepad.cfg")
ARCADE_FILE = os.path.join(REPO, "skills", "controls", "flycast", "SDL_USB gamepad_arcade.cfg")
ROLE_DEFAULTS = os.path.join(REPO, "ansible", "roles", "dreamcast", "defaults", "main.yml")
ROLE_TASKS = os.path.join(REPO, "ansible", "roles", "dreamcast", "tasks", "main.yml")

# The cabinet's ~/.bash_profile SDL_GAMECONTROLLERCONFIG (read 2026-11-15; flycast log: GUID 0300cb231f08...,
# name 'USB gamepad'). The role installs the files only when the cabinet still has exactly this string.
SDLCFG = ("0300cb231f08000001e4000010010000,USB gamepad,a:b3,b:b2,x:b0,y:b4,leftshoulder:b5,rightshoulder:b1,"
          "back:b8,start:b9,dpup:h0.1,dpdown:h0.4,dpleft:h0.8,dpright:h0.2,leftx:a0,lefty:a1,platform:Linux,")
# panel position -> raw joystick button (the js index list in the ticket: MP0 HK1 MK2 LK3 LP4 HP5)
PANEL_JS = {"LP": 4, "MP": 0, "HP": 5, "LK": 3, "MK": 2, "HK": 1}
# flycast key name -> what the controls card prints
CARD_LABEL = {"btn_x": "X", "btn_y": "Y", "btn_a": "A", "btn_b": "B", "btn_c": "C",
              "btn_trigger_left": "LT", "btn_trigger_right": "RT"}


# --- flycast model ---------------------------------------------------------------------------------------------
def parse_sdlcfg(s):
    parts = s.rstrip(",").split(",")
    return {k: v for k, v in (p.split(":", 1) for p in parts[2:] if ":" in p)}


HAT_DIR = {1: 0, 4: 1, 8: 2, 2: 3}       # SDL_HAT_UP/DOWN/LEFT/RIGHT -> flycast dir (sdl_gamepad.cpp L66-79)


def _code(bind):
    if bind.startswith("b"):
        return int(bind[1:])
    if bind.startswith("h"):
        hat, mask = bind[1:].split(".")
        return ((int(hat) + 1) << 8) | HAT_DIR[int(mask)]
    raise ValueError(bind)


def flycast_default(sdlcfg, arcade):
    """{code: key} digital and {(code, positive): key} analog, as flycast's DefaultInputMapping builds them."""
    cfg = parse_sdlcfg(sdlcfg)
    dig, ana = {}, {}

    def set_button(key, code):                       # mapping.cpp set_button: clear key, then the same code
        for c in [c for c, k in dig.items() if k == key]:
            del dig[c]
        dig[code] = key

    def set_axis(key, code, pos):                    # mapping.cpp set_axis: clear_axis(key) then set
        for a in [a for a, k in ana.items() if k == key]:
            del ana[a]
        ana[(code, pos)] = key

    def map_button(sdl, key):
        if sdl in cfg:
            set_button(key, _code(cfg[sdl]))

    def map_axis(sdl, key, positive):                # only plain full axes and missing ones on this panel
        if sdl not in cfg:
            return False
        b = cfg[sdl]
        assert b.startswith("a") and b[1:].isdigit(), "model only knows full axes: %s" % b
        set_axis(key, int(b[1:]), positive)
        return True

    # base ctor L32-45
    for key, code in (("btn_y", 0), ("btn_b", 1), ("btn_a", 2), ("btn_x", 3), ("btn_start", 9)):
        set_button(key, code)
    for key, code, pos in (("btn_analog_left", 0, False), ("btn_analog_right", 0, True),
                           ("btn_analog_up", 1, False), ("btn_analog_down", 1, True),
                           ("axis2_left", 2, False), ("axis2_right", 2, True),
                           ("axis2_up", 3, False), ("axis2_down", 3, True)):
        set_axis(key, code, pos)
    if arcade:                                       # Arcade + Gamepad, L144-156
        pairs = (("a", "btn_a"), ("b", "btn_b"), ("x", "btn_c"), ("y", "btn_x"))
        rb_alt = "btn_y"
    else:                                            # Dreamcast, L175-186
        pairs = (("a", "btn_a"), ("b", "btn_b"), ("x", "btn_x"), ("y", "btn_y"))
        rb_alt = "btn_c"
    for sdl, key in pairs:
        map_button(sdl, key)
    if not map_axis("lefttrigger", "axis_trigger_left", True):
        map_button("leftshoulder", "btn_trigger_left")
    else:
        map_button("leftshoulder", "btn_z")
    if not map_axis("righttrigger", "axis_trigger_right", True):
        map_button("rightshoulder", "btn_trigger_right")
    else:
        map_button("rightshoulder", rb_alt)
    for sdl, key in (("start", "btn_start"), ("dpup", "btn_dpad1_up"), ("dpdown", "btn_dpad1_down"),
                     ("dpleft", "btn_dpad1_left"), ("dpright", "btn_dpad1_right"), ("guide", "btn_dpad2_up"),
                     ("back", "btn_menu")):
        map_button(sdl, key)
    for sdl, key, pos in (("leftx", "btn_analog_left", False), ("leftx", "btn_analog_right", True),
                          ("lefty", "btn_analog_up", False), ("lefty", "btn_analog_down", True),
                          ("rightx", "axis2_left", False), ("rightx", "axis2_right", True),
                          ("righty", "axis2_up", False), ("righty", "axis2_down", True)):
        map_axis(sdl, key, pos)
    return dig, ana


def parse_ini(text):
    """core/cfg/ini.cpp IniFile::load: ';' comments, [section], 'k = v' (one space after '=' dropped)."""
    out, sec = {}, ""
    for line in text.splitlines():
        s = line.lstrip()
        if not s or s[0] == ";":
            continue
        if s[0] == "[":
            sec = s[1:s.index("]")]
            continue
        k, _, v = line.partition("=")
        out.setdefault(sec, {})[k.strip(" \t")] = v[1:] if v.startswith(" ") else v
    return out


def load_mapping(text):
    """mapping.cpp InputMapping::load (version >= 3): bindN read from 0 until the first gap."""
    ini = parse_ini(text)
    dig, ana = {}, {}
    for i in range(1000):
        s = ini.get("digital", {}).get("bind%d" % i, "")
        if not s:
            break
        code, key = s.split(":", 1)
        dig[int(code)] = key
    for i in range(1000):
        s = ini.get("analog", {}).get("bind%d" % i, "")
        if not s:
            break
        code, key = s.split(":", 1)
        ana[(int(code[:-1]), code[-1] == "+")] = key
    return ini, dig, ana


EMULATOR = {"mapping_name": "USB gamepad", "dead_zone": "10", "saturation": "100", "rumble_power": "100",
            "version": "4", "triggers": ""}


def problems(text, arcade, sdlcfg=SDLCFG):
    """Everything wrong with a mapping file vs the maintainer's ask, for this SDL config. [] = good."""
    p = []
    ini, dig, ana = load_mapping(text)
    want_dig, want_ana = flycast_default(sdlcfg, arcade)
    if not arcade:                                   # the one change: LP (js 4) -> X, MP (js 0) -> Y
        if want_dig.get(4) != "btn_y" or want_dig.get(0) != "btn_x":
            p.append("SDL config no longer gives LP=Y MP=X by default: %r" % want_dig)
        want_dig = {**want_dig, 4: "btn_x", 0: "btn_y"}
    if ini.get("emulator") != EMULATOR:
        p.append("[emulator] %r != %r" % (ini.get("emulator"), EMULATOR))
    if "combo" in ini:
        p.append("[combo] present (the exit watcher stays; no combos)")
    if set(ini) - {"emulator", "digital", "analog"}:
        p.append("unknown sections %s" % sorted(set(ini) - {"emulator", "digital", "analog"}))
    for name, got, want in (("digital", dig, want_dig), ("analog", ana, want_ana)):
        n = len(ini.get(name, {}))
        if n != len(got):
            p.append("%s: %d entries but flycast reads only %d (gap in bindN)" % (name, n, len(got)))
        for c in sorted(set(got) | set(want), key=str):
            if got.get(c) != want.get(c):
                p.append("%s %s: file %s, want %s" % (name, c, got.get(c), want.get(c)))
    if "btn_menu" not in dig.values():
        p.append("BACK (js 8) -> btn_menu missing")
    if "btn_start" not in dig.values():
        p.append("START (js 9) -> btn_start missing")
    return p


def card_problems(text, layout):
    _, dig, _ = load_mapping(text)
    want = [CARD_LABEL.get(dig.get(PANEL_JS[pos]), "") for pos in controls.POSITIONS]
    return [] if layout["buttons"] == want else ["card %r != mapping %r" % (layout["buttons"], want)]


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _must_fail(text, arcade, why):
    if not problems(text, arcade):
        raise AssertionError("BROKEN case accepted: " + why)


# --- tests -----------------------------------------------------------------------------------------------------
def test_model_reproduces_the_cabinet_play_test():
    # The maintainer 2026-11-15 with no mapping file: LP gave MP, MP gave LP, kicks and HP/HK right.
    dig, _ = flycast_default(SDLCFG, arcade=False)
    by_pos = {pos: dig[js] for pos, js in PANEL_JS.items()}
    assert by_pos == {"LP": "btn_y", "MP": "btn_x", "HP": "btn_trigger_left", "LK": "btn_a", "MK": "btn_b",
                      "HK": "btn_trigger_right"}, by_pos
    assert dig[8] == "btn_menu" and dig[9] == "btn_start"


def test_dc_file_is_default_with_only_x_y_swapped():
    p = problems(_read(DC_FILE), arcade=False)
    assert p == [], p
    _, dig, _ = load_mapping(_read(DC_FILE))
    default, _ = flycast_default(SDLCFG, arcade=False)
    diff = {c for c in set(dig) | set(default) if dig.get(c) != default.get(c)}
    assert diff == {0, 4}, diff
    assert dig[PANEL_JS["LP"]] == "btn_x" and dig[PANEL_JS["MP"]] == "btn_y"


def test_arcade_file_is_the_unchanged_arcade_default():
    p = problems(_read(ARCADE_FILE), arcade=True)
    assert p == [], p


def test_broken_swap_back_fails():
    t = _read(DC_FILE).replace("bind0 = 0:btn_y", "bind0 = 0:btn_x").replace("bind4 = 4:btn_x", "bind4 = 4:btn_y")
    assert t != _read(DC_FILE)
    _must_fail(t, False, "LP back on DC Y")


def test_broken_lp_only_fails():
    _must_fail(_read(DC_FILE).replace("bind4 = 4:btn_x", "bind4 = 4:btn_y"), False, "LP -> Y, MP -> Y")


def _drop_bind(text, line):
    """Remove one digital bind and renumber, so flycast still reads the rest (only that key goes)."""
    assert line in text, line
    lines = [x for x in text.splitlines() if x != line]
    out, n, sec = [], 0, ""
    for x in lines:
        if x.startswith("["):
            sec = x
        if sec == "[digital]" and re.match(r"bind\d+ = ", x):
            x = re.sub(r"^bind\d+", "bind%d" % n, x)
            n += 1
        out.append(x)
    return "\n".join(out) + "\n"


def test_broken_missing_menu_fails():
    t = _drop_bind(_read(DC_FILE), "bind6 = 8:btn_menu")
    assert "BACK (js 8) -> btn_menu missing" in problems(t, False), problems(t, False)


def test_broken_missing_start_fails():
    t = _drop_bind(_read(DC_FILE), "bind7 = 9:btn_start")
    assert "START (js 9) -> btn_start missing" in problems(t, False), problems(t, False)


def test_broken_bind_gap_fails():
    # flycast stops at the first missing bindN: deleting a line without renumbering loses everything after it
    t = _read(DC_FILE).replace("bind6 = 8:btn_menu\n", "")
    _must_fail(t, False, "gap at digital bind6")


def test_broken_combo_or_other_change_fails():
    _must_fail(_read(DC_FILE) + "\n[combo]\nbind0 = 8,9:btn_escape:0\n", False, "[combo] added")
    _must_fail(_read(DC_FILE).replace("dead_zone = 10", "dead_zone = 15"), False, "dead zone changed")
    _must_fail(_read(DC_FILE).replace("bind5 = 5:btn_trigger_left", "bind5 = 5:btn_c"), False, "HP changed")
    _must_fail(_read(DC_FILE).replace("bind0 = 0-:btn_analog_left", "bind0 = 0-:btn_dpad1_left"), False,
               "stick changed")
    _must_fail(_read(ARCADE_FILE).replace("bind0 = 0:btn_c", "bind0 = 0:btn_y"), True, "arcade file swapped too")


def test_card_matches_mapping():
    lay = controls.LAYOUTS["dreamcast"]
    assert card_problems(_read(DC_FILE), lay) == [], card_problems(_read(DC_FILE), lay)
    assert lay["buttons"] == ["X", "Y", "LT", "A", "B", "RT"]
    assert lay["tested"] is True, "the maintainer asked to mark the dreamcast layout tested (2026-11-15)"
    assert lay.get("select") == "MENU", "BACK opens flycast's menu"


def test_broken_card_swapped_back_fails():
    lay = dict(controls.LAYOUTS["dreamcast"], buttons=["Y", "X", "LT", "A", "B", "RT"])
    assert card_problems(_read(DC_FILE), lay) != [], "BROKEN card accepted"


def test_role_installs_both_files_with_the_same_sdlcfg():
    d, t = _read(ROLE_DEFAULTS), _read(ROLE_TASKS)
    assert ('dreamcast_sdl_gamecontrollerconfig: "%s"' % SDLCFG) in d, "role SDL string != test SDLCFG"
    listed = re.findall(r"^  - (SDL_USB gamepad[^ #]*\.cfg)", d, re.M)
    assert listed[:2] == ["SDL_USB gamepad.cfg", "SDL_USB gamepad_arcade.cfg"], listed
    assert "skills/controls/flycast" in d and ".config/flycast/mappings" in d
    for need in ("pgrep -x flycast", "dreamcast_flycast_pgrep.rc == 1", "dreamcast_sdlcfg_grep.rc == 0",
                 "backup: true", 'mode: "0644"', 'owner: "{{ cabinet_user }}"'):
        assert need in t, "dreamcast tasks miss %r" % need


def test_broken_sdlcfg_drift_fails():
    # The files use raw js codes. With another SDL config (e.g. the map images had before #244; chroot-setup.sh now
    # bakes the cabinet's own) the same file is wrong, which is why the role checks the cabinet's string first.
    for other in (SDLCFG.replace("x:b0,y:b4", "x:b4,y:b0"),
                  "0300cb231f08000001e4000010010000,USB gamepad,a:b4,b:b0,x:b5,y:b3,leftshoulder:b2,"
                  "rightshoulder:b1,back:b8,start:b9,dpup:h0.1,dpdown:h0.4,dpleft:h0.8,dpright:h0.2,"
                  "leftx:a0,lefty:a1,platform:Linux,"):
        assert problems(_read(DC_FILE), False, other) != [], "BROKEN: file accepted for SDL config %s" % other
        assert ('dreamcast_sdl_gamecontrollerconfig: "%s"' % other) not in _read(ROLE_DEFAULTS)


if __name__ == "__main__":
    main(globals(), per_test=True)
