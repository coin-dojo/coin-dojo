#!/usr/bin/env python3
"""Tests for the display / attract / esde / launch variables (ticket #125, C2 of #103): the declared defaults equal
today's live values, the VM overlay, and the broken cases that must FAIL config-check:
hk_attract_timer_ms: -1, an ES-DE key outside the allowlist (es_settings.xml holds a password), a wrong type for an
allowed key, a bad attract type, a wrong-typed kernel switch.

Run: python3 tools/tests/test_config_levers.py   (needs PyYAML: re-runs itself with ~/.venvs/coin-dojo/bin/python, else SKIPs)
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
from hktest import check, main, skip  # noqa: E402

try:
    import yaml  # noqa: E402,F401  (only probes that PyYAML is installed)
except ImportError:
    venv = os.path.expanduser("~/.venvs/coin-dojo/bin/python")
    if os.path.exists(venv) and not os.environ.get("HK_TEST_CONFIG_REEXEC"):
        os.environ["HK_TEST_CONFIG_REEXEC"] = "1"
        os.execv(venv, [venv] + sys.argv)
    skip("PyYAML is not installed (make config-venv); the lever declarations are not tested")
    print("RESULT: PASS (1 skipped)")
    sys.exit(0)

sys.path.insert(0, os.path.join(ROOT, "tools"))
import config as C  # noqa: E402

HOST = "hk_cabinet_host: arcade.local\n"


def cfg(local_text="", limit="coin-dojo"):
    p = os.path.join(tempfile.mkdtemp(prefix="hk-levers-"), "local.yml")
    with open(p, "w") as fh:
        fh.write(HOST + local_text)
    try:
        return C.Config(limit=limit, local=p, use_local=False)
    except C.ConfigError as exc:   # a failing check raises with the problems
        return exc


def problems(local_text="", limit="coin-dojo"):
    r = cfg(local_text, limit)
    return r.problems


def has(ps, *words):
    return any(all(w in p for w in words) for p in ps)


def test_defaults_are_the_live_values():
    c = cfg()
    v = c.values
    check(not c.problems, "the shipped defaults pass config-check: %s" % c.problems)
    check(v["hk_kernel_4k"] is True, "hk_kernel_4k defaults to true (the cabinet's 4 KB kernel)")
    check(v["hk_attract_timer_ms"] == 600000, "attract timer is the live 10 minutes (600000 ms)")
    check(v["hk_attract_controls"] is True and v["hk_attract_type"] == "video", "attract controls true, type video (live)")
    s = v["hk_esde_settings"]
    check(s["StartupSystem"] == "must-play" and s["CollectionSystemsAuto"] == "recent" and s["MenuColorScheme"] == "darkred",
          "esde settings: must-play start, LAST PLAYED, dark red menus")
    check(s["FavoritesAddButton"] is False and s["FavoritesStar"] is False and s["FavoritesFirst"] is False, "favorites off")
    check(not any(k.startswith("Screensaver") or k.lower().startswith("scrape") for k in s),
          "the settings map holds no attract keys (own variables) and nothing scraper-related")
    check(set(v["hk_alt_emulators"]) == {"nes", "cps1", "cps2", "cps3", "psx"}, "alt emulators: nes cps1 cps2 cps3 psx")
    check(len(v["hk_core_options"]) == 1 and len(v["hk_retroarch_cfg"]) == 17,
          "1 core option (the upscale ones derive from hk_upscale, #170), 17 retroarch.cfg keys (#174/#186: 9 for the bezel)")
    check({"key": "input_enable_hotkey_btn", "value": "9"} in v["hk_retroarch_cfg"], "hotkey = START (btn 9)")


def test_vm_overlay():
    c = cfg(limit="coin-dojo-vm")
    v = c.values
    check(not c.problems, "the VM config passes: %s" % c.problems)
    check(v["hk_kernel_4k"] is False, "VM: no 4 KB-kernel line (no Pi firmware)")
    check(set(v["hk_alt_emulators"]) == {"nes"}, "VM: only nes is pinned (no fbneo core on Debian)")
    check(cfg().values["hk_kernel_4k"] is True, "the VM overlay does not leak onto the cabinet")


def test_broken_attract_timer():
    ps = problems("hk_attract_timer_ms: -1\n")
    check(has(ps, "hk_attract_timer_ms", "below the minimum 0"), "BROKEN: hk_attract_timer_ms: -1 FAILs config-check: %s" % ps)
    check(has(problems('hk_attract_timer_ms: "10min"\n'), "hk_attract_timer_ms", "whole number"), "BROKEN: a string timer FAILs")
    check(has(problems("hk_attract_timer_ms: 999999999\n"), "above the maximum"), "BROKEN: a 11-day timer FAILs")
    check(not problems("hk_attract_timer_ms: 0\n"), "0 (automatic start off) is accepted")
    check(has(problems("hk_attract_type: screensaver\n"), "hk_attract_type", "not one of"), "BROKEN: unknown attract type FAILs")
    check(has(problems('hk_attract_controls: "yes"\n'), "hk_attract_controls", "true or false"), "BROKEN: a string bool FAILs")
    check(has(problems("hk_kernel_4k: 1\n"), "hk_kernel_4k", "true or false"), "BROKEN: hk_kernel_4k: 1 FAILs")


def test_broken_esde_key_outside_allowlist():
    ps = problems("hk_esde_settings:\n  ScraperPassword: x\n")
    check(has(ps, "hk_esde_settings", "ScraperPassword", "unknown key"),
          "BROKEN: an ES-DE key outside the allowlist FAILs: %s" % ps)
    ps = problems("hk_esde_settings:\n  StartupSytem: must-play\n")
    check(has(ps, "StartupSytem", "did you mean 'StartupSystem'"), "BROKEN: a typo in a key gets a did-you-mean hint: %s" % ps)
    ps = problems("hk_esde_settings:\n  ScreensaverTimer: 5\n")
    check(has(ps, "ScreensaverTimer", "unknown key"), "BROKEN: attract keys do not go through the settings map")
    ps = problems("hk_esde_settings:\n  FavoritesStar: 'no'\n")
    check(has(ps, "FavoritesStar", "true or false"), "BROKEN: a string for a bool key FAILs: %s" % ps)
    ps = problems("hk_esde_settings: [StartupSystem]\n")
    check(has(ps, "hk_esde_settings", "mapping"), "BROKEN: a list instead of a map FAILs")
    check(not problems("hk_esde_settings:\n  StartupSystem: favorites\n  FavoritesStar: true\n"), "allowed keys with right types pass")


def test_broken_launch_types():
    check(has(problems("hk_core_options: x\n"), "hk_core_options", "list"), "BROKEN: core options as a string FAILs")
    check(has(problems("hk_core_options: [a]\n"), "hk_core_options", "item 0", "mapping"), "BROKEN: a non-map core option FAILs")
    check(has(problems("hk_retroarch_cfg: {a: b}\n"), "hk_retroarch_cfg", "list"), "BROKEN: retroarch cfg as a map FAILs")
    check(has(problems("hk_alt_emulators: [nes]\n"), "hk_alt_emulators", "mapping"), "BROKEN: alt emulators as a list FAILs")


def test_partial_override_keeps_the_rest():
    v = cfg("hk_esde_settings:\n  StartupSystem: favorites\n").values["hk_esde_settings"]
    check(v["StartupSystem"] == "favorites" and v["MenuColorScheme"] == "darkred", "one key set, the others keep their defaults")


def test_removed_dead_levers_are_refused():
    """#165: hk_carousel_order and hk_romm were declared but read by nothing; a local config still setting one now
    FAILs config-check (unknown variable) instead of silently doing nothing."""
    for line in ("hk_carousel_order: [systems, genres]\n", "hk_romm: {url: http://romm.lan}\n"):
        ps = problems(line)
        check(has(ps, line.split(":")[0]), "BROKEN: %s is refused as unknown: %s" % (line.split(":")[0], ps))
    check(not problems(""), "the plain config still passes")


def test_chat_macros_variable():
    """#227: hk_fightcade_chat_macros, the lobby keyboard's lines: defaults in Fightcade's lingo, bounded and clean."""
    d = cfg().values["hk_fightcade_chat_macros"]
    check({m["group"] for m in d} == {"CHALLENGE", "AFTER", "STATUS"} and {"text": "gg", "group": "AFTER"} in d,
          "default macros: three groups, gg is there")
    check(all(len(m["text"]) <= 60 for m in d) and any(m["text"] == "FT3?" for m in d), "defaults fit the limits, FT3? is there")
    check(not problems('hk_fightcade_chat_macros: [{group: STATUS, text: "back soon"}]\n'), "a user list of {group, text} passes")
    for bad, why in (('[{group: STATUS, text: "%s"}]' % ("x" * 61), "61 characters"),
                     ('[{group: STATUS, text: "a\\tb"}]', "a control character"),
                     ('[{group: status, text: "x"}]', "a lower-case group"),
                     ('[{group: STATUS, text: "x", extra: 1}]', "an unknown key"),
                     ('[{group: STATUS, text: ""}]', "an empty line"),
                     ('[{group: STATUS}]', "a missing text"), ('[{text: "x"}]', "a missing group"),
                     ('[{group: MINE, text: "x"}]', "the reserved group MINE"), ('[{group: "STATUS ", text: "x"}]', "a trailing space in the group"),
                     ('[{group: STATUS, text: "x "}]', "a trailing space in the text"), ('[{group: STATUS, text: " x"}]', "a leading space in the text"),
                     ('[%s]' % ", ".join('{group: G%d, text: "m"}' % i for i in range(6)), "6 groups"),
                     ('[%s]' % ", ".join('{group: A, text: "m%d"}' % i for i in range(41)), "41 lines")):
        ps = problems("hk_fightcade_chat_macros: %s\n" % bad)
        check(has(ps, "hk_fightcade_chat_macros"), "BROKEN: %s is refused: %s" % (why, ps))
    check(has(problems('hk_fightcade_chat_macros: [{group: STATUS, text: "{{ hk_cabinet_host }}"}]\n'), "template"),
          "BROKEN: a Jinja template in a macro is refused")


if __name__ == "__main__":
    main(globals())
