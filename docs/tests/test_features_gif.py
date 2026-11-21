#!/usr/bin/env python3
"""The README features GIF (#264): docs/assets/features.gif, built by docs/assets/gen_features_gif.py.

  1. size <= 3 MB (gen_features_gif.MAX_BYTES), 960 px wide, loops forever;
  2. the frame count matches the scene list (SCENES) and each scene stays SCENE_MS on screen;
  3. the script rebuilds the committed GIF byte for byte from the committed sources (Pillow: this python, else
     ~/.venvs/badges/bin/python, else SKIP);
  4. README.md shows the GIF near the top, and its alt text names every scene in plain words;
  5. the alt texts of the three shot-*.png images are neutral: no denylisted name (config/edition/denylist.txt,
     private repo only; SKIP in the community tree, which has no denylist).
Each rule has a planted broken case that must FAIL (a missing scene, an oversized GIF, a changed byte, an alt text
without a scene). Plain python (hktest), stdlib only except the rebuild.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
sys.path.insert(0, os.path.join(ROOT, "docs", "assets"))
from hktest import check, main, skip  # noqa: E402
import gen_features_gif as g  # noqa: E402

GIF = os.path.join(ROOT, "docs", "assets", "features.gif")
GIF_REL = "docs/assets/features.gif"
TOP = 40  # "near the top": on the first screen of the README
IMG = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
SHOTS = ("docs/assets/shot-carousel.png", "docs/assets/shot-bootmenu.png", "docs/assets/shot-settings.png")


def readme():
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
        return f.read()


# ---- rules 1 + 2
def gif_problems(path, scenes=g.SCENES):
    probs = []
    size = os.path.getsize(path)
    if size > g.MAX_BYTES:
        probs.append("%d bytes > %d" % (size, g.MAX_BYTES))
    try:
        info = g.gif_info(path)
    except (ValueError, IndexError) as e:
        return probs + ["unreadable GIF: %s" % e]
    if info["width"] != g.W:
        probs.append("width %d, want %d" % (info["width"], g.W))
    if info["loop"] != 0:
        probs.append("does not loop forever (loop %r)" % info["loop"])
    if info["frames"] != g.frame_count(scenes):
        probs.append("%d frames, the scene list has %d" % (info["frames"], g.frame_count(scenes)))
    elif info["durations"] != g.frame_durations(scenes):
        probs.append("frame times %s, want %s" % (info["durations"], g.frame_durations(scenes)))
    return probs


def test_gif_size_frames_loop():
    probs = gif_problems(GIF)
    info = g.gif_info(GIF)
    check(probs == [], "features.gif: %d bytes, %d frames for %d scenes, %dx%d, loops %s" % (
        os.path.getsize(GIF), info["frames"], len(g.SCENES), info["width"], info["height"], probs))
    check(g.frame_count() == sum(len(s["frames"]) for s in g.SCENES) and len(g.SCENES) == 7,
          "seven scenes: carousel, boot menu, Fightcade, SETTINGS, trophies, CRT, SELECT+START")
    check(all(sum(g.frame_durations([s])) == g.SCENE_MS for s in g.SCENES), "every scene is %d ms" % g.SCENE_MS)
    check(gif_problems(GIF, g.SCENES[:-1]) != [], "BROKEN: a scene missing from the GIF (one frame short) is reported")
    check(gif_problems(GIF, g.SCENES + [g.SCENES[0]]) != [], "BROKEN: a GIF without a listed scene is reported")
    with tempfile.TemporaryDirectory() as tmp:
        big = os.path.join(tmp, "big.gif")
        shutil.copyfile(GIF, big)
        with open(big, "ab") as f:
            f.write(b"\0" * (g.MAX_BYTES + 1 - os.path.getsize(GIF)))
        check(any("bytes >" in p for p in gif_problems(big)), "BROKEN: a GIF over 3 MB is reported")
        noloop = os.path.join(tmp, "noloop.gif")
        with open(GIF, "rb") as f:
            data = f.read()
        with open(noloop, "wb") as f:
            f.write(data.replace(b"NETSCAPE2.0", b"XETSCAPE2.0", 1))
        check(any("loop" in p for p in gif_problems(noloop)), "BROKEN: a GIF that does not loop is reported")


# ---- rule 3
def pillow_python():
    try:
        import PIL  # noqa: F401
        return sys.executable
    except ImportError:
        venv = os.path.expanduser("~/.venvs/badges/bin/python")
        return venv if os.access(venv, os.X_OK) else None


def same_bytes(a, b):
    with open(a, "rb") as f, open(b, "rb") as h:
        return f.read() == h.read()


def test_rebuild_is_byte_identical():
    missing = [s for s in g.sources() if not os.path.isfile(os.path.join(g.SRC, s))]
    check(missing == [], "every source of the scene list is committed in docs/assets/features/ %s" % missing)
    py = pillow_python()
    if not py:
        skip("Pillow missing: features.gif not rebuilt (run with ~/.venvs/badges/bin/python)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        fresh = os.path.join(tmp, "features.gif")
        r = subprocess.run([py, os.path.join(ROOT, "docs", "assets", "gen_features_gif.py"), "--out", fresh],
                           capture_output=True, text=True, timeout=300)
        check(r.returncode == 0, "gen_features_gif.py builds (rc %d %s)" % (r.returncode, r.stderr.strip()[-200:]))
        if r.returncode != 0:
            return
        check(same_bytes(GIF, fresh), "the committed features.gif equals a fresh build byte for byte "
                                       "(else: ~/.venvs/badges/bin/python docs/assets/gen_features_gif.py)")
        with open(fresh, "r+b") as f:
            f.seek(-2, 2)
            f.write(b"\x01")
        check(not same_bytes(GIF, fresh), "BROKEN: one changed byte is reported as drift")


# ---- rule 4
def alt_problems(text, scenes=g.SCENES, top=TOP):
    for n, line in enumerate(text.splitlines()[:top]):
        for m in IMG.finditer(line):
            if m.group(2) == GIF_REL:
                alt = m.group(1)
                return ["line %d: the alt text does not name the scene '%s'" % (n + 1, s["alt"])
                        for s in scenes if s["alt"] not in alt]
    return ["README.md: no ![...](%s) in the first %d lines" % (GIF_REL, top)]


def test_readme_alt_lists_every_scene():
    text = readme()
    check(alt_problems(text) == [], "README shows features.gif near the top, alt text names all %d scenes %s" % (
        len(g.SCENES), alt_problems(text)))
    check("![%s](%s)" % (g.readme_alt(), GIF_REL) in text, "the alt text is gen_features_gif.readme_alt() exactly")
    cut = text.replace(g.SCENES[3]["alt"], "settings", 1)
    check(alt_problems(cut) != [], "BROKEN: an alt text without one scene is reported")
    check(alt_problems("\n" * TOP + text) != [], "BROKEN: the GIF pushed below line %d is reported" % TOP)


# ---- rule 5
def denylist(root=ROOT):
    p = os.path.join(root, "config", "edition", "denylist.txt")
    if not os.path.isfile(p):
        return None
    words = []
    for line in open(p, encoding="utf-8"):
        w = line.split("#", 1)[0].strip()
        if w and not w.startswith((":", "/")):
            words.append(w)
    return words


def shot_alt_problems(text, words):
    probs = []
    alts = {m.group(2): m.group(1) for m in IMG.finditer(text) if m.group(2) in SHOTS}
    for shot in SHOTS:
        if shot not in alts:
            probs.append("%s not shown in README.md" % shot)
            continue
        for w in words:
            pat = r"(?<![A-Za-z])" + r"[\s_-]*".join(re.escape(x) for x in w.split()) + r"(?![A-Za-z])"
            if re.search(pat, alts[shot], re.I):
                probs.append("%s alt text names '%s'" % (shot, w))
    return probs


def test_shot_alt_texts_neutral():
    words = denylist()
    if words is None:
        skip("no config/edition/denylist.txt here (community tree): shot alt texts not scanned")
        return
    text = readme()
    check(shot_alt_problems(text, words) == [], "the three shot alt texts name no denylisted word %s" %
          shot_alt_problems(text, words))
    planted = text.replace("](docs/assets/shot-carousel.png)", " " + words[0] + "](docs/assets/shot-carousel.png)", 1)
    check(shot_alt_problems(planted, words) != [], "BROKEN: a denylisted name in a shot alt text is reported")


if __name__ == "__main__":
    main(globals())
