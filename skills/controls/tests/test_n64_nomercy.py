#!/usr/bin/env python3
"""WWF No Mercy (N64) per-game layout: game remap + game override (stick = d-pad AND left analog).

Sources: button roles from the WWF No Mercy controls list (FAQ by isv666, GameFAQs 2001, hosted at
video-games-museum.com, p.2-3); Mupen64Plus-Next (libretro/mupen64plus-libretro-nx develop 15bada8)
emulate_game_controller_via_libretro.c L331-380 (RetroPad -> N64, default mode); RetroArch v1.22.2
input/input_defines.h L31/L95-102 (remap ids 16-23 = analog directions), configuration.c config_load_override
(game override = config/<core>/<rom stem>.cfg, re-read with its input binds), input_driver.c L805-816 and
L1064-1069 (a user/override bind wins over the autoconfig per bind).
Run: python3 skills/controls/tests/test_n64_nomercy.py"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "lib"))
from hktest import main  # noqa: E402  (shared runner: one PASS per test, counted RESULT line, #137)
import controls  # noqa: E402
import test_controls as tc  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
RMP = os.path.join(HERE, "..", "remaps", "Mupen64Plus-Next", "WWF No Mercy.rmp")
DEFAULTS = os.path.join(REPO, "ansible", "roles", "cabinet_tweaks", "defaults", "main.yml")
TASKS = os.path.join(REPO, "ansible", "roles", "cabinet_tweaks", "tasks", "main.yml")
CHROOT = os.path.join(REPO, "skills", "build-image", "scripts", "chroot-setup.sh")
STEM = "WWF No Mercy"          # /data/roms/n64/WWF No Mercy.zip (cabinet, 2026-11-15)

# N64 button -> what it does in No Mercy (FAQ controls list)
ROLE = {"B": "Strike", "R": "Block/Counter", "C-Left": "Turn/Weapon", "A": "Grapple", "L": "Reverse/Pin",
        "C-Down": "Run", "C-Up": "Climb ropes"}
WANT_PANEL = ["B", "R", "C-Left", "A", "L", "C-Down"]      # LP MP HP / LK MK HK
# left-analog bind -> the autoconfig d-pad bind it must mirror
MIRROR = {"l_y_minus": "up", "l_y_plus": "down", "l_x_minus": "left", "l_x_plus": "right"}


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def remap_problems(text):
    p = []
    for pl in (1, 2):
        got = tc.n64_panel(text, pl, tc.STD_MAP)
        if got != WANT_PANEL:
            p.append("player%d panel %s, want %s" % (pl, got, WANT_PANEL))
        if tc.STD_MAP.get(tc.effective(text, pl)["select"]) != "C-Up":
            p.append("player%d SELECT is not C-Up" % pl)
    if not re.search(r'(?m)^input_remap_port_p2 = "1"$', text):
        p.append("player 2 does not keep its own port")
    return p


def labels_problems(row):
    want = [ROLE[b] for b in WANT_PANEL]
    return [] if row["labels"] == want else ["labels %s, want %s" % (row["labels"], want)]


def autoconfig_dpad_axes():
    t = _read(CHROOT)
    return {d: re.search(r'^input_%s_axis = "([+-]\d)"' % d, t, re.M).group(1) for d in ("up", "down", "left", "right")}


def override_keys(defaults_text, game=STEM):
    m = re.search(r"^  - core: Mupen64Plus-Next\n    game: %s\n    keys:\n((?:      (?:- |#).*\n)+)" % re.escape(game),
                  defaults_text, re.M)
    if not m:
        return {}
    return dict(re.findall(r'\{ key: (\w+), value: "([^"]*)" \}', m.group(1)))


def override_problems(keys, dpad):
    p = []
    for pl in (1, 2):
        for analog, d in MIRROR.items():
            k = "input_player%d_%s_axis" % (pl, analog)
            if keys.get(k) != dpad[d]:
                p.append("%s = %s, want %s (same axis as the d-pad %s)" % (k, keys.get(k), dpad[d], d))
    # The maintainer 2026-11-15: the stick on BOTH d-pad and analog made the wrestler gesture while walking, so the
    # d-pad must be moved off the stick's axes (to an axis the panel lacks): stick = N64 analog only.
    stick_axes = {v.lstrip("+-") for v in dpad.values()}
    dkeys = {"input_player%d_%s_axis" % (pl, d) for pl in (1, 2) for d in MIRROR.values()}
    for k in sorted(dkeys):
        v = keys.get(k)
        if v is None or v in ("nul", "") or v.lstrip("+-") in stick_axes:
            p.append("%s = %s: the d-pad must be unbound from the stick axes %s (not nul: that falls back to the autoconfig)"
                     % (k, v, sorted(stick_axes)))
    extra = set(keys) - {"input_player%d_%s_axis" % (pl, a) for pl in (1, 2) for a in MIRROR} - dkeys
    if extra:
        p.append("unexpected override keys %s" % sorted(extra))
    return p


# --- tests ------------------------------------------------------------------------------------------------------
def test_remap_lays_out_the_panel():
    assert remap_problems(_read(RMP)) == [], remap_problems(_read(RMP))
    for ln in open(RMP, encoding="utf-8"):
        assert tc.remap_line_problem(ln) is None, ln


def test_labels_follow_the_remap():
    row = controls.n64_games()[STEM]
    assert labels_problems(row) == [], labels_problems(row)
    assert row["layout"] == "n64-remap" and all(len(x) <= controls.LABEL_MAX for x in row["labels"])


def test_override_puts_the_stick_on_the_analog_too():
    dpad = autoconfig_dpad_axes()
    assert dpad == {"up": "-1", "down": "+1", "left": "-0", "right": "+0"}, dpad
    p = override_problems(override_keys(_read(DEFAULTS)), dpad)
    assert p == [], p


def test_role_deploys_remap_and_override():
    d, t = _read(DEFAULTS), _read(TASKS)
    assert '{ core: "Mupen64Plus-Next", file: "WWF No Mercy.rmp" }' in d
    for need in ("ansible.builtin.lineinfile", "cabinet_game_overrides | subelements('keys')",
                 "/config/{{ item.0.core }}/{{ item.0.game }}.cfg", "backup: true", "create: true"):
        assert need in t, need
    # the controls helper's own override keys must never be set here (they are merged by controls.py)
    assert not set(override_keys(d)) & set(controls.OVERRIDE_KEYS)


# broken cases
def test_broken_remaps_fail():
    t = _read(RMP)
    swapped = t.replace('input_player1_btn_x = "11"', 'input_player1_btn_x = "10"') \
               .replace('input_player1_btn_a = "10"', 'input_player1_btn_a = "11"')
    assert remap_problems(swapped) != [], "BROKEN: block and reverse swapped accepted"
    no_p2 = "\n".join(l for l in t.splitlines() if not l.startswith("input_player2_"))
    assert remap_problems(no_p2) != [], "BROKEN: player 2 without layout accepted"
    core_only = _read(os.path.join(HERE, "..", "remaps", "Mupen64Plus-Next", "Mupen64Plus-Next.rmp"))
    assert remap_problems(core_only) != [], "BROKEN: default N64 panel accepted for No Mercy"
    assert remap_problems(t.replace('input_player1_btn_select = "23"', 'input_player1_btn_select = "22"')) != []


def test_broken_labels_fail():
    row = dict(controls.n64_games()[STEM])
    row["labels"] = ["Grapple", "Block/Counter", "Turn/Weapon", "Strike", "Reverse/Pin", "Run"]
    assert labels_problems(row) != [], "BROKEN labels accepted"


def test_broken_overrides_fail():
    dpad = autoconfig_dpad_axes()
    good = override_keys(_read(DEFAULTS))
    inverted = dict(good, input_player1_l_y_minus_axis="+1")
    assert override_problems(inverted, dpad) != [], "BROKEN: inverted analog Y accepted"
    missing = {k: v for k, v in good.items() if not k.startswith("input_player2_")}
    assert override_problems(missing, dpad) != [], "BROKEN: player 2 analog missing accepted"
    assert override_problems(dict(good, input_overlay_enable="true"), dpad) != [], "BROKEN: stray key accepted"
    both = dict(good, input_player1_up_axis="-1")
    assert any("d-pad must be unbound" in x for x in override_problems(both, dpad)), "BROKEN: stick still on the d-pad accepted"
    nul = dict(good, input_player2_left_axis="nul")
    assert any("d-pad must be unbound" in x for x in override_problems(nul, dpad)), "BROKEN: nul (falls back to autoconfig) accepted"
    assert override_keys(_read(DEFAULTS), game="WWF No Mercy (USA)") == {}, "override must use the cabinet stem"


if __name__ == "__main__":
    main(globals(), per_test=True)
