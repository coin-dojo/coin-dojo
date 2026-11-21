#!/usr/bin/env python3
"""Tests for gen_eye_glow.py and its theme wiring (#105). Needs Pillow; SKIP if absent.
The generator runs on a SYNTHETIC backdrop (never the shipped art) in a temp dir; the shipped files are
only checked structurally. Run: python3 skills/badges/tests/test_gen_eye_glow.py"""
import base64
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
BADGES = os.path.dirname(HERE)
REPO = os.path.normpath(os.path.join(BADGES, "..", ".."))
THEME = os.path.join(REPO, "themes", "coin-dojo")
SHIPPED = os.path.join(THEME, "_inc", "art", "backdrops")   # the night art and its eye glow (#263)
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "tests", "lib"))
from hktest import check, finish, main, skip  # noqa: E402  (shared helper; SKIPs counted in RESULT, #152)

try:
    from PIL import Image
except ImportError:  # counted SKIP (#135): the Forgejo runner image has no Pillow
    skip("Pillow not installed")
    sys.exit(finish())


CFG_DIR = tempfile.mkdtemp(prefix="hk-eyes-cfg-")


def cfg_file(extra=""):
    """A local config for the generator's `tools/config.py get` (never a personal/config/local.yml)."""
    p = os.path.join(CFG_DIR, "c%d.yml" % len(os.listdir(CFG_DIR)))
    with open(p, "w") as fh:
        fh.write("hk_cabinet_host: test.invalid\n" + extra)
    return p


def run(*args, extra=""):
    return subprocess.run([sys.executable, os.path.join(BADGES, "gen_eye_glow.py"), *args],
                          capture_output=True, text=True, env=dict(os.environ, HK_CONFIG=cfg_file(extra)))


def synthetic_backdrop(d):
    """Violet 1440x1080 with a red-orange blob where the generator looks for the eyes (not real art)."""
    im = Image.new("RGB", (1440, 1080), (60, 50, 140))
    for y in range(150, 270):
        for x in range(770, 870):
            im.putpixel((x, y), (180, 36, 10))
    im.save(os.path.join(d, "default.png"))


def generated(d):
    synthetic_backdrop(d)
    r = run(d)
    check(r.returncode == 0, "generator exits 0 on a backdrop: " + r.stderr.strip())
    return r


def test_outputs_and_glow_shape():
    with tempfile.TemporaryDirectory() as d:
        generated(d)
        for n in ("eye-glow.png", "eye-glow.json", "eye-glow.xml"):
            check(os.path.isfile(os.path.join(d, n)), "writes " + n)
        g = Image.open(os.path.join(d, "eye-glow.png")).convert("RGBA")
        a = g.getchannel("A")
        w, h = g.size
        check(a.getpixel((2, 2)) <= 8, "glow corner is practically clear (soft falloff): %d" % a.getpixel((2, 2)))
        core = a.getpixel((w * 85 // 180, h * 85 // 170))
        # The maintainer 2026-11-15 wants it bright and menacing: strong core, but never fully opaque (the art shows through)
        check(round(255 * 0.75) <= core <= round(255 * 0.95), "glow core is bright but never fully opaque: %d" % core)
        check(core > a.getpixel((w // 6, h // 2)), "alpha falls off toward the edge")
        r, gg, b = g.getpixel((w * 85 // 180, h * 85 // 170))[:3]
        check(r > 200 and r > gg and r > b, "core colour is a hot red from the palette: %s" % ((r, gg, b),))


def test_lottie_loop_timing():
    with tempfile.TemporaryDirectory() as d:
        generated(d)
        doc = json.load(open(os.path.join(d, "eye-glow.json")))
        fr = doc["fr"]
        check(doc["op"] == fr * 10, "loop is 10 s")
        ks = doc["layers"][0]["ks"]["o"]["k"]
        times = [k["t"] for k in ks]
        check(times == [0, 4 * fr, 5 * fr, 9 * fr, 10 * fr], "keyframes: 4 s up, 1 s hold, 4 s down, 1 s rest: %s" % times)
        vals = [k["s"][0] for k in ks]
        check(vals == [0, 100, 100, 0, 0], "opacity ramps 0 -> 100 -> 100 -> 0 -> 0: %s" % vals)
        check("o" in ks[0] and "i" in ks[0] and "o" in ks[2], "ramps carry ease in/out curves")
        asset = doc["assets"][0]
        check(asset["p"].startswith("data:image/png;base64,") and asset["e"] == 1, "PNG is embedded (no external file)")
        png = Image.open(io.BytesIO(base64.b64decode(asset["p"].split(",", 1)[1])))
        check(png.mode == "RGBA" and png.size == (asset["w"], asset["h"]) == (doc["w"], doc["h"]), "embedded image is RGBA at composition size")


def test_include_xml_and_placement():
    with tempfile.TemporaryDirectory() as d:
        generated(d)
        root = ET.parse(os.path.join(d, "eye-glow.xml")).getroot()
        views = {v.get("name"): v for v in root.findall("view")}
        check(set(views) == {"system", "gamelist"}, "animation in the system and gamelist views: %s" % sorted(views))
        for name, v in views.items():
            an = v.find("animation")
            x, y = (float(t) for t in an.findtext("pos").split())
            w, h = (float(t) for t in an.findtext("size").split())
            check(x * 1440 <= 770 and (x + w) * 1440 >= 870 and y * 1080 <= 150 and (y + h) * 1080 >= 270,
                  "%s box covers the eye region at 1440x1080" % name)
            path = an.findtext("path")
            check(path == "./eye-glow.json" and os.path.isfile(os.path.join(d, path)),
                  "%s path is relative to the include and exists" % name)
            check(float(an.findtext("zIndex")) > 2 and float(an.findtext("zIndex")) < 3, "%s sits above the backdrop (2), below the scrim (3)" % name)


def test_deterministic():
    with tempfile.TemporaryDirectory() as d:
        generated(d)
        first = [open(os.path.join(d, n), "rb").read() for n in ("eye-glow.png", "eye-glow.json", "eye-glow.xml")]
        run(d)
        second = [open(os.path.join(d, n), "rb").read() for n in ("eye-glow.png", "eye-glow.json", "eye-glow.xml")]
        check(first == second, "same input gives the same bytes")


# sha256 of the three files for the synthetic backdrop (PEAK 0.92, BOX 680,85,960,345 = the config defaults): the
# defaults must give these exact bytes (#130; json and xml re-pinned for the neutral names, #263).
GOLDEN = {
    "eye-glow.png": "c971547fd350a8423d64ecd36870ca1c7fb0a4be4a5ffecdc43bf90c44e515dc",
    "eye-glow.json": "16dc3818fe55cf7088021ecfcf14114035ece277b03628fdc9a59c95a3f49056",
    "eye-glow.xml": "daa10505db7c7a35b7a1319521c90969762d886dcd2487d9386008079b463266",
}


def sha(d, n):
    import hashlib
    return hashlib.sha256(open(os.path.join(d, n), "rb").read()).hexdigest()


def test_defaults_are_byte_identical_to_the_old_literals():
    with tempfile.TemporaryDirectory() as d:
        generated(d)
        for n, want in GOLDEN.items():
            check(sha(d, n) == want, "%s is byte-identical with the default hk_eye_glow" % n)


def test_config_levers_change_the_output():
    with tempfile.TemporaryDirectory() as d:
        synthetic_backdrop(d)
        r = run(d, extra="hk_eye_glow: {peak: 0.5}\n")
        check(r.returncode == 0 and sha(d, "eye-glow.png") != GOLDEN["eye-glow.png"], "peak 0.5 changes the glow PNG")
        check(sha(d, "eye-glow.xml") == GOLDEN["eye-glow.xml"], "peak alone leaves the placement alone")
        r = run(d, extra="hk_eye_glow: {box: [700, 100, 900, 300]}\n")
        check(r.returncode == 0 and sha(d, "eye-glow.xml") != GOLDEN["eye-glow.xml"], "box changes the placement")
        r = run(d, extra="hk_eye_glow: {enabled: false}\n")
        check(r.returncode == 0 and not any(os.path.exists(os.path.join(d, n)) for n in GOLDEN), "enabled false removes the glow files")


def test_broken_config():
    with tempfile.TemporaryDirectory() as d:
        synthetic_backdrop(d)
        r = run(d, extra="hk_eye_glow: {peak: 1.5}\n")
        check(r.returncode == 2 and "peak" in r.stderr and not os.path.exists(os.path.join(d, "eye-glow.png")),
              "BROKEN: peak 1.5 -> exit 2, nothing written: " + r.stderr.strip()[:80])
        r = run(d, extra="hk_eye_glow: {box: [900, 100, 700, 300]}\n")
        check(r.returncode == 2 and "box" in r.stderr, "BROKEN: a box with x1 < x0 -> exit 2")


def test_broken_cases():
    with tempfile.TemporaryDirectory() as d:
        r = run(d)
        check(r.returncode == 1 and "no backdrop" in r.stderr, "BROKEN: no backdrop in the folder -> exit 1")
        check(not os.path.exists(os.path.join(d, "eye-glow.json")), "BROKEN: nothing written without a backdrop")
    r = run("--nope")
    check(r.returncode == 2, "BROKEN: bad usage -> exit 2")
    r = run("a", "b")
    check(r.returncode == 2, "BROKEN: two directories -> exit 2")
    # a malformed include must be caught by the same parse the deploy gate uses
    try:
        ET.fromstring("<theme><view name='system'><animation name='eyeGlow'></view></theme>")
        check(False, "BROKEN: malformed include XML must not parse")
    except ET.ParseError:
        check(True, "BROKEN: malformed include XML does not parse")


def test_theme_wiring():
    s = open(os.path.join(THEME, "theme.xml")).read()
    check(re.search(r"<eyeGlow>eye-glow</eyeGlow>", s) is not None, "theme.xml defines the eyeGlow variable")
    check("<include>./_inc/art/backdrops/${eyeGlow}.xml</include>" in s,
          "theme.xml includes the glow through the variable (a missing file only logs a debug line)")
    check(not re.search(r"<path>[^<]*eye-glow", s), "theme.xml has no literal path to a glow file")
    # the deploy copies the glow files by name (tasks) and removes them while hk_eye_glow is off (esde_theme_eye_files)
    role = os.path.join(REPO, "ansible", "roles", "esde_theme")
    roles = open(os.path.join(role, "defaults", "main.yml")).read()
    tasks = open(os.path.join(role, "tasks", "main.yml")).read()
    dirs = roles.split("esde_theme_dirs:", 1)[1].split("\n\n", 1)[0] if "esde_theme_dirs:" in roles else ""
    check(dirs and "_inc/art/backdrops" not in dirs, "esde_theme_dirs does not name _inc/art/backdrops")
    check("_inc/art/backdrops" in ("esde_theme_dirs:\n  - _inc/art/backdrops\n").split("esde_theme_dirs:", 1)[1],
          "BROKEN: a planted backdrops dir in esde_theme_dirs would be seen")
    for ext in ("png", "json", "xml"):
        check("_inc/art/backdrops/eye-glow.%s" % ext in roles, "esde_theme_eye_files names eye-glow.%s" % ext)
        check("eye-glow.%s" % ext in tasks, "the theme staging copies eye-glow.%s" % ext)
    bad = s.replace("${eyeGlow}", "eye-glow")
    check("<include>./_inc/art/backdrops/eye-glow.xml</include>" in bad and "${eyeGlow}" not in bad,
          "BROKEN: a literal include would no longer follow the variable (the check sees it)")


def test_shipped_files():
    for n in ("eye-glow.json", "eye-glow.xml", "eye-glow.png", "default.png"):
        check(os.path.isfile(os.path.join(SHIPPED, n)), "the theme ships " + n)
    ET.parse(os.path.join(SHIPPED, "eye-glow.xml"))
    json.load(open(os.path.join(SHIPPED, "eye-glow.json")))
    with tempfile.TemporaryDirectory() as d:
        import shutil
        shutil.copy(os.path.join(SHIPPED, "default.png"), d)
        check(run(d).returncode == 0, "regenerating from the shipped night art works")
        for n in ("eye-glow.json", "eye-glow.xml", "eye-glow.png"):
            check(open(os.path.join(d, n), "rb").read() == open(os.path.join(SHIPPED, n), "rb").read(),
                  "shipped %s is current (rerun skills/badges/gen_eye_glow.py)" % n)
        # broken: a stale shipped glow (another backdrop) is reported by the same comparison
        stale = Image.new("RGB", (1440, 1080), (30, 20, 60))
        stale.save(os.path.join(d, "default.png"))
        check(run(d).returncode == 0 and open(os.path.join(d, "eye-glow.png"), "rb").read()
              != open(os.path.join(SHIPPED, "eye-glow.png"), "rb").read(),
              "BROKEN: a different backdrop gives a different glow (a stale shipped glow would be seen)")


if __name__ == "__main__":
    main(globals())
