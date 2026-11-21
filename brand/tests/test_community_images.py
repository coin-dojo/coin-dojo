#!/usr/bin/env python3
"""Images with a name baked in, community copies (#235, found by #240's leak review).

The theme wordmark and the style-kit specimen spell the private names in pixels, which the export's text rename cannot
reach. Each has a community copy generated from the community name; config/edition/rename.tsv ships it under the
private file's name and config/edition/edits.tsv drops the private file. the maintainer's own images stay as they are.

- each community copy is byte-identical to its source: the theme logo is a copy of brand/logo.png (the project logo,
  #258, gen_logo.py); the specimen is specimen.py --name's output
- (private repo) each copy differs from the private image, and the edition tables swap it in (rename + drop row)
BROKEN cases: a stale copy (the private image in its place) is reported; a swap without its drop row or without its
rename row is reported; specimen.py --name without a value exits 2.
Needs Pillow (~/.venvs/badges) for the byte checks, else SKIP (counted in RESULT).
Run: python3 brand/tests/test_community_images.py
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
from hktest import check, main, skip  # noqa: E402

SPECIMEN = os.path.join(REPO, "brand", "specimen.py")
NAME = "Coin Dojo"
# (community copy, argv to regenerate it into OUT)
COMMUNITY = [
    ("themes/coin-dojo/_inc/art/logo-wordmark.png", ["COPY", os.path.join(REPO, "brand", "logo.png")]),
    ("brand/specimen.png", [SPECIMEN, "--name", NAME, "OUT"]),
]
EDITION = os.path.join(REPO, "config", "edition")


def private_of(path):
    return path.replace("-community.png", ".png")


def have_pillow():
    return subprocess.run([sys.executable, "-c", "import PIL"], capture_output=True).returncode == 0


def stale(argv, committed, tmp):
    """-> a problem string when `committed` is not the generator's output (or, for ["COPY", src], src's bytes)."""
    if argv[0] == "COPY":
        if not os.path.isfile(committed):
            return "missing: %s" % committed
        if open(argv[1], "rb").read() != open(committed, "rb").read():
            return "%s is stale: copy %s over it" % (os.path.relpath(committed, REPO), os.path.relpath(argv[1], REPO))
        return ""
    out = os.path.join(tmp, "gen.png")
    r = subprocess.run([sys.executable] + [out if a == "OUT" else a for a in argv], capture_output=True, text=True)
    if r.returncode != 0:
        return "generator failed: %s" % (r.stderr.strip() or r.stdout.strip())
    if not os.path.isfile(committed):
        return "missing: %s" % committed
    if open(out, "rb").read() != open(committed, "rb").read():
        return "%s is stale: rerun %s" % (os.path.relpath(committed, REPO), " ".join(os.path.basename(a) if a.startswith("/") else a for a in argv))
    return ""


def swap_problems(rel, rename_text, edits_text):
    """-> what is missing for the export to ship `rel` (a community copy) under its private name."""
    out = []
    old, new = os.path.basename(rel), os.path.basename(private_of(rel))
    if "%s\t%s" % (old, new) not in rename_text.splitlines():
        out.append("rename.tsv has no row %s -> %s" % (old, new))
    if "drop\t%s" % private_of(rel) not in edits_text.splitlines():
        out.append("edits.tsv does not drop %s" % private_of(rel))
    return out


def test_community_copies_match_their_generators():
    if not have_pillow():
        skip("Pillow not installed: community image byte check")
        return
    with tempfile.TemporaryDirectory() as d:
        for rel, argv in COMMUNITY:
            bad = stale(argv, os.path.join(REPO, rel), d)
            check(not bad, "%s is its generator's output %s" % (rel, bad))


def test_BROKEN_stale_copy_is_reported():
    if not have_pillow():
        skip("Pillow not installed: stale-copy broken case")
        return
    with tempfile.TemporaryDirectory() as d:
        rel, argv = COMMUNITY[0]
        other = os.path.join(d, "other.png")
        from PIL import Image
        Image.new("RGBA", (8, 8)).save(other)
        check("stale" in stale(argv, other, d), "BROKEN: a wordmark that is not the generator's output is reported")
        check("missing" in stale(argv, os.path.join(d, "none.png"), d), "BROKEN: a missing copy is reported")
        check("stale" in stale(COMMUNITY[1][1], other, d), "BROKEN: a specimen that is not specimen.py's output is reported")
    r = subprocess.run([sys.executable, SPECIMEN, "--name"], capture_output=True, text=True)
    check(r.returncode == 2, "BROKEN: specimen.py --name without a value -> exit 2")


def test_edition_swaps_the_copies_in():
    if not os.path.isdir(EDITION):   # the exported tree has no config/edition/: the swap already happened
        skip("no config/edition/ (exported tree)")
        return
    rename_text = open(os.path.join(EDITION, "rename.tsv"), encoding="utf-8").read()
    edits_text = open(os.path.join(EDITION, "edits.tsv"), encoding="utf-8").read()
    for rel, _argv in COMMUNITY:
        bad = swap_problems(rel, rename_text, edits_text)
        check(not bad, "export ships %s as %s (%s)" % (rel, private_of(rel), "; ".join(bad)))
        priv = os.path.join(REPO, private_of(rel))
        check(os.path.isfile(priv) and open(priv, "rb").read() != open(os.path.join(REPO, rel), "rb").read(),
              "%s keeps its own private image, different from the community copy" % private_of(rel))
    rel = COMMUNITY[1][0]
    check(any("edits.tsv" in p for p in swap_problems(rel, rename_text, "")),
          "BROKEN: a swap without its drop row is reported")
    check(any("rename.tsv" in p for p in swap_problems(rel, "", edits_text)),
          "BROKEN: a swap without its rename row is reported")


if __name__ == "__main__":
    main(globals())
