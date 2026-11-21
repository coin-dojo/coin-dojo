#!/usr/bin/env python3
"""Theme levers in the config (#130, C6 of #103): config/variables.d/theme.yml, the esde_theme role, the community backdrop.

Run: python3 themes/tests/test_theme_config.py   (needs PyYAML: re-runs itself with ~/.venvs/coin-dojo/bin/python, else SKIPs)
Broken cases (each must FAIL): hk_eye_glow.peak 1.5, a misspelt eye-glow key (did-you-mean), a box that is not ints,
a literal lever planted in the role defaults, a wrong-size art backdrop (WARN, not FAIL), a missing art folder (WARN).
Also: defaults are today's look, partial maps merge, exports reach the role, the committed backdrop is the community
backdrop (1440x1080 PNG, the only art in the public tree; #222), hotkey strip / REV stamp switches act on theme.xml.
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
from hktest import check, finish, main, skip  # noqa: E402

try:
    import yaml  # noqa: E402,F401
except ImportError:
    venv = os.path.expanduser("~/.venvs/coin-dojo/bin/python")
    if os.path.exists(venv) and not os.environ.get("HK_TEST_CONFIG_REEXEC"):
        os.environ["HK_TEST_CONFIG_REEXEC"] = "1"
        os.execv(venv, [venv] + sys.argv)
    skip("PyYAML is not installed (make config-venv); the theme config is not tested")
    sys.exit(finish())

sys.path.insert(0, os.path.join(ROOT, "tools"))
import config as C  # noqa: E402

THEME = os.path.join(ROOT, "themes", "coin-dojo")
ROLE = os.path.join(ROOT, "ansible", "roles", "esde_theme")
LEVER_EXPORTS = ("esde_theme_name", "esde_theme_personal", "esde_theme_art_dir", "esde_theme_eye_glow",
                 "esde_theme_rev_stamp", "esde_theme_hotkey_strip")


def cfg(text="", root=ROOT):
    d = tempfile.mkdtemp(prefix="hk-theme-cfg-")
    p = os.path.join(d, "local.yml")
    with open(p, "w") as fh:
        fh.write("hk_cabinet_host: test.invalid\n" + text)
    return C.Config(limit="coin-dojo", root=root, local=p)


def has(ps, *words):
    return any(all(w in p for w in words) for p in ps)


def test_defaults_are_todays_look():
    c = cfg()
    check(c.problems == [], "the theme variables validate with defaults: %s" % c.problems)
    v = c.values
    check(v["hk_theme_name"] == "coin-dojo", "theme name default")
    check(v["hk_theme_overlay"] is True and v["hk_rev_stamp"] is True and v["hk_hotkey_strip"] is True, "overlay, REV and hotkey strip on")
    check(v["hk_eye_glow"] == {"enabled": True, "peak": 0.92, "box": [680, 85, 960, 345]}, "eye glow default is the live look: %s" % v["hk_eye_glow"])
    check(v["hk_theme_art_dir"] == "" and "hk_carousel_order" not in v, "no art folder by default; no carousel-order lever (#165: hk_virtual_systems order is the carousel order)")


def test_eye_glow_levers_and_broken_cases():
    check(has(cfg("hk_eye_glow: {peak: 1.5}\n").problems, "hk_eye_glow", "peak", "above the maximum"), "BROKEN: hk_eye_glow.peak 1.5 FAILs")
    check(has(cfg("hk_eye_glow: {peak: -0.1}\n").problems, "peak", "below the minimum"), "BROKEN: negative peak FAILs")
    check(has(cfg("hk_eye_glow: {pek: 0.5}\n").problems, "unknown key", "did you mean 'peak'"), "BROKEN: misspelt key FAILs with a hint")
    check(has(cfg("hk_eye_glow: {box: [1, 2, x, 4]}\n").problems, "box", "whole number"), "BROKEN: box with a non-integer FAILs")
    check(has(cfg("hk_eye_glow: {enabled: yes please}\n").problems, "enabled", "true or false"), "BROKEN: enabled must be a bool")
    c = cfg("hk_eye_glow: {peak: 0.6}\n")
    check(c.problems == [] and c.values["hk_eye_glow"] == {"enabled": True, "peak": 0.6, "box": [680, 85, 960, 345]},
          "setting one key keeps the others: %s" % c.values["hk_eye_glow"])
    check(has(cfg("hk_theme_name: Bad Name\n").problems, "hk_theme_name", "pattern"), "BROKEN: theme name with a space FAILs")


def test_exports_reach_the_role():
    ex = cfg("hk_theme_art_dir: my-art\nhk_hotkey_strip: false\n").exports()
    for name in LEVER_EXPORTS:
        check(name in ex, "%s is exported to Ansible" % name)
    check(ex["esde_theme_art_dir"] == "my-art" and ex["esde_theme_hotkey_strip"] is False, "exported values follow the config")
    check(ex["esde_theme_eye_glow"]["enabled"] is True, "the eye glow map is exported whole")


def test_no_hidden_knob_in_role_defaults():
    def planted(text):
        return [n for n in LEVER_EXPORTS if re.search(r"(?m)^%s:" % n, text)]
    text = open(os.path.join(ROLE, "defaults", "main.yml")).read()
    check(planted(text) == [], "the role defaults hold no literal for a lever: %s" % planted(text))
    check(planted(text + "esde_theme_rev_stamp: true\n") == ["esde_theme_rev_stamp"], "BROKEN: a planted literal lever is caught")
    # levers live in config: the defaults stay short; the retired-file list items (#150) are data, not settings
    settings = [ln for ln in text.splitlines() if not ln.startswith("  - _inc/")]
    check(len(settings) <= 40, "role defaults stay short (%d lines without the file lists)" % len(settings))


def test_art_dir_warnings():
    d = tempfile.mkdtemp(prefix="hk-theme-art-")
    c = cfg("hk_theme_art_dir: %s\n" % d)
    check(has(C.art_warnings(c), "no backdrops/", "community backdrop"), "BROKEN: art folder without backdrops/ WARNs")
    os.makedirs(os.path.join(d, "backdrops"))
    with open(os.path.join(d, "backdrops", "default.png"), "wb") as fh:
        fh.write(b"not a png")
    check(has(C.art_warnings(c), "default.png", "not a PNG"), "BROKEN: a non-PNG backdrop WARNs")
    with open(os.path.join(d, "backdrops", "default.png"), "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + (800).to_bytes(4, "big") + (600).to_bytes(4, "big"))
    check(has(C.art_warnings(c), "800x600", "1440x1080"), "BROKEN: a wrong-size backdrop WARNs")
    with open(os.path.join(d, "backdrops", "default.png"), "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + (1440).to_bytes(4, "big") + (1080).to_bytes(4, "big"))
    check(C.art_warnings(c) == [], "a 1440x1080 PNG backdrop is fine")
    check(C.art_warnings(cfg()) == [], "no art folder set: nothing to warn about")


def test_public_backdrops_are_the_listed_ones():
    p = os.path.join(THEME, "_inc", "art", "backdrops", "default.png")
    check(C.png_size(p) == (1440, 1080), "committed default.png is a 1440x1080 PNG: %s" % (C.png_size(p),))
    tracked = subprocess.run(["git", "-C", ROOT, "ls-files", "themes/coin-dojo/_inc/art/backdrops"],
                             capture_output=True, text=True).stdout.split()
    names = sorted(os.path.basename(t) for t in tracked)
    want = ["README.md", "default.png", "dojo.png", "eye-glow.json", "eye-glow.png", "eye-glow.xml"]
    check(names in (want, [".gitignore"] + want),
          "only the night art, the original dojo, the eye glow and the README are in the public theme: %s" % tracked)
    # sizes: default.png is the night art, a painted full-screen illustration (~190k colours, 1.1 MB after a lossless
    # recompress; a palette PNG would change it, so the limit is raised on purpose, #263); the generated dojo stays small.
    check(os.path.getsize(p) < 1250000, "the night backdrop stays under 1.25 MB (%d bytes)" % os.path.getsize(p))
    dojo = os.path.join(THEME, "_inc", "art", "backdrops", "dojo.png")
    check(os.path.getsize(dojo) < 400000, "the generated dojo stays small (generated art, not a photo)")
    check(os.path.getsize(os.path.join(THEME, "_inc", "art", "backdrops", "eye-glow.json")) < 200000, "the eye glow Lottie is small")


def role_lines(marker):
    return [ln.strip() for ln in open(os.path.join(ROLE, "tasks", "main.yml")).read().splitlines() if marker in ln]


def run_stage(line, s):
    return subprocess.run(["sh", "-c", line], env=dict(os.environ, S=s), capture_output=True, text=True)


def test_theme_xml_switches():
    src = open(os.path.join(THEME, "theme.xml")).read()
    import xml.dom.minidom as md
    hot = role_lines('name=\\"controlsHint\\"')
    rev = role_lines("REV @COIN_DOJO_VERSION@")
    check(len(hot) == 1 and len(rev) == 1, "role has one hotkey-strip and one REV-stamp switch")
    s = tempfile.mkdtemp(prefix="hk-theme-xml-")
    for line, gone, kept in ((hot[0], 'name="controlsHint"', "REV @COIN_DOJO_VERSION@"),
                             (rev[0], "REV @COIN_DOJO_VERSION@", 'name="controlsHint"')):
        with open(os.path.join(s, "theme.xml"), "w") as fh:
            fh.write(src)
        r = run_stage(line, s)
        out = open(os.path.join(s, "theme.xml")).read()
        check(r.returncode == 0 and gone not in out and kept in out, "switch removes only its own element: %s" % r.stderr.strip())
        try:
            md.parseString(out)
            check(True, "theme.xml still parses after the switch")
        except Exception as exc:  # noqa: BLE001
            check(False, "theme.xml parses after the switch: %s" % exc)
    check(src.count('name="controlsHint"') == 1, "BROKEN guard: the source theme.xml has the strip exactly once")


if __name__ == "__main__":
    main(globals())
