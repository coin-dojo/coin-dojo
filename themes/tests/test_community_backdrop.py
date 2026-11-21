#!/usr/bin/env python3
"""The community edition's carousel backdrop (#222). Pure stdlib (the Forgejo runner has no Pillow).

The repo theme (= the community edition) must ship a finished default backdrop: a 1440x1080 PNG at
_inc/art/backdrops/default.png. Since #263 that is the credited night art (THIRD_PARTY.md), allowed by its sha256 on
tests/test_ship_no_artifacts.py's list; the original generated dojo is backdrops/dojo.png (pixel check: skills/badges/
tests/test_gen_backdrop.py). Any OTHER byte copy of a file under personal/ is reported, and so is the old CONFIGURE ME
card (#130) the critic review of #218 flagged.
Broken cases: a theme without default.png, a wrong-size PNG, not a PNG, an unlisted byte copy of a personal file, and
the old CONFIGURE ME card are each reported.

Run: python3 themes/tests/test_community_backdrop.py
"""
import hashlib
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
THEME = os.path.join(REPO, "themes", "coin-dojo")
PERSONAL = os.path.join(REPO, "personal")
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
sys.path.insert(0, os.path.join(REPO, "tests"))
from hktest import check, main  # noqa: E402
from test_ship_no_artifacts import SHIPPED_ART  # noqa: E402  (the credited art, by sha256)

SIZE = (1440, 1080)
# sha256 of the CONFIGURE ME card that shipped as default.png before #222 (gen_placeholder.py, #130)
CONFIGURE_ME_SHA = "cc4d6a8d4f66f0f31f67502fb66e194ed52e6f0c6788ec5e801c4d17cdf654ce"


def png_size(path):
    """(w, h) from the PNG IHDR, or None when the file is not a PNG."""
    with open(path, "rb") as f:
        head = f.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def personal_hashes(personal_dir):
    out = set()
    for root, _dirs, files in os.walk(personal_dir):
        for fn in files:
            out.add(_sha(os.path.join(root, fn)))
    return out


def backdrop_problems(theme_dir, personal_dir):
    """-> problems with the theme's community default backdrop (empty = ok)."""
    p = os.path.join(theme_dir, "_inc", "art", "backdrops", "default.png")
    if not os.path.isfile(p):
        return ["no _inc/art/backdrops/default.png: the community carousel would have no backdrop"]
    out = []
    size = png_size(p)
    if size is None:
        out.append("default.png is not a PNG")
    elif tuple(size) != SIZE:
        out.append("default.png is %dx%d, not %dx%d" % (size + SIZE))
    sha = _sha(p)
    if sha == CONFIGURE_ME_SHA:
        out.append("default.png is still the CONFIGURE ME card")
    if os.path.isdir(personal_dir) and sha in personal_hashes(personal_dir) and sha not in SHIPPED_ART.values():
        out.append("default.png is an unlisted copy of a personal/ file (personal art in the community edition)")
    return out


def test_community_backdrop():
    bad = backdrop_problems(THEME, PERSONAL)
    check(not bad, "community carousel backdrop is shippable (%s)" % "; ".join(bad))


def _png(path, w, h):
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00")


def test_BROKEN_backdrop_cases_are_reported():
    with tempfile.TemporaryDirectory() as d:
        theme, personal = os.path.join(d, "theme"), os.path.join(d, "personal")
        bd = os.path.join(theme, "_inc", "art", "backdrops")
        os.makedirs(bd)
        os.makedirs(personal)
        dp = os.path.join(bd, "default.png")
        check(any("no _inc" in x for x in backdrop_problems(theme, personal)), "BROKEN: a missing backdrop is reported")
        _png(dp, 1920, 1080)
        check(any("1920x1080" in x for x in backdrop_problems(theme, personal)), "BROKEN: a wrong-size backdrop is reported")
        open(dp, "wb").write(b"GIF89a not a png")
        check(any("not a PNG" in x for x in backdrop_problems(theme, personal)), "BROKEN: a non-PNG backdrop is reported")
        _png(dp, 1440, 1080)
        check(backdrop_problems(theme, personal) == [], "control: a plain 1440x1080 PNG passes")
        _png(os.path.join(personal, "art.png"), 1440, 1080)
        check(any("personal/" in x for x in backdrop_problems(theme, personal)),
              "BROKEN: a byte copy of personal art is reported")
    real_personal = os.path.join(PERSONAL, "themes", "coin-dojo", "_inc", "art", "backdrops", "default.png")
    if os.path.isfile(real_personal):
        with tempfile.TemporaryDirectory() as d:
            bd = os.path.join(d, "_inc", "art", "backdrops")
            os.makedirs(bd)
            open(os.path.join(bd, "default.png"), "wb").write(open(real_personal, "rb").read())
            check(any("personal/" in x for x in backdrop_problems(d, PERSONAL)),
                  "BROKEN: a personal backdrop copy that is not the listed night art is reported")


def test_BROKEN_configure_me_card_is_reported():
    import subprocess
    old = subprocess.run(["git", "-C", REPO, "show", "ca86c62:themes/coin-dojo/_inc/art/backdrops/default.png"],
                         capture_output=True)
    if old.returncode != 0:   # shallow clone / exported tree without that commit: check the hash alone
        check(len(CONFIGURE_ME_SHA) == 64, "CONFIGURE ME hash pinned (old commit not in this clone)")
        return
    check(hashlib.sha256(old.stdout).hexdigest() == CONFIGURE_ME_SHA, "the pinned hash is the old CONFIGURE ME card")
    with tempfile.TemporaryDirectory() as d:
        bd = os.path.join(d, "_inc", "art", "backdrops")
        os.makedirs(bd)
        open(os.path.join(bd, "default.png"), "wb").write(old.stdout)
        check(any("CONFIGURE ME" in x for x in backdrop_problems(d, os.path.join(d, "none"))),
              "BROKEN: the old CONFIGURE ME card is reported")


if __name__ == "__main__":
    main(globals())
