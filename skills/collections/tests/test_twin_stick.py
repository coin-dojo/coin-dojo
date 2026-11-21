#!/usr/bin/env python3
"""Tests for the GENRES/twin-stick folder (coin-dojo #59; stdlib only; pytest or run directly).

    python3 skills/collections/tests/test_twin_stick.py
    CONTROLS_PY=/path/to/skills/controls/controls.py python3 skills/collections/tests/test_twin_stick.py

twin_stick.tsv must match skills/controls/controls.py TWIN_STICK; the cross-check runs when controls.py has
TWIN_STICK (it SKIPs, and says so, before Input's #59/#64 branch is merged). Broken cases: bad/duplicate/empty
/missing twin_stick.tsv -> InputError and `build` exit 3; a list that differs from controls.py is reported.
"""
import importlib.util
import io
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "lib"))
from hktest import main, skip  # noqa: E402  (shared runner: one PASS per test, counted RESULT line, #151)

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures")
CONTROLS_PY = os.environ.get("CONTROLS_PY") or os.path.join(os.path.dirname(SKILL), "controls", "controls.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, SKILL)
cg = _load("collgen", os.path.join(SKILL, "collgen.py"))
import nested  # noqa: E402

EXPECTED = {("fbneo", "robotron"), ("fbneo", "bwidow"), ("fbneo", "smashtv"), ("fbneo", "totcarn"),
            ("n64", "Robotron 64"), ("snes", "Super Smash T.V. (USA)")}


def row(fn, genre, year=1990):
    return {"fn": fn, "genre": genre, "year": year, "name": os.path.splitext(fn)[0], "hidden": False}


def tsv(text):
    fd, p = tempfile.mkstemp(suffix=".tsv")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def raises_input_error(text):
    p = tsv(text)
    try:
        cg.load_twin_stick(p)
    except cg.InputError:
        return True
    finally:
        os.unlink(p)
    return False


def diff_sets(ours, theirs):
    """-> human-readable differences between twin_stick.tsv and controls.TWIN_STICK ([] = same)."""
    return (["only in twin_stick.tsv: %s/%s" % g for g in sorted(set(ours) - set(theirs))] +
            ["only in controls.py TWIN_STICK: %s/%s" % g for g in sorted(set(theirs) - set(ours))])


# ---- genre text ----------------------------------------------------------------

def test_genre_bucket_twin_stick_text():
    for g in ("Twin Stick", "Shooter / Twin-Stick", "twin stick shooter"):
        assert cg.genre_bucket(g) == "twin-stick", g
    assert cg.genre_bucket("Shoot'em Up") == "shmups"           # plain shooters stay where they were
    assert cg.genre_bucket("Shooter / Run and Gun") == "run-and-gun"


def test_tws_pick_code_maps_to_twin_stick_bucket():
    assert cg.PICK_GENRE["tws"] == "Twin Stick"
    assert cg.genre_bucket(cg.PICK_GENRE["tws"]) == "twin-stick"
    assert cg.genre_bucket(cg.PICK_GENRE["shm"]) == "shmups"   # broken-case guard: shm is NOT twin-stick


# ---- the list --------------------------------------------------------------------

def test_shipped_list_is_the_six_games():
    assert cg.load_twin_stick() == EXPECTED


def test_broken_lists_are_input_errors():
    assert raises_input_error("fbneo\n")                                   # one column
    assert raises_input_error("fbneo\trobotron\textra\n")                  # three columns
    assert raises_input_error("fbneo\t robotron\n")                         # stray space
    assert raises_input_error("fbneo\troms/robotron\n")                     # a path, not a stem
    assert raises_input_error("fbneo\trobotron\nfbneo\trobotron\n")         # duplicate
    assert raises_input_error("# only comments\n\n")                        # empty list
    try:
        cg.load_twin_stick(os.path.join(FIX, "no-such-twin-stick.tsv"))
        assert False, "missing file must fail"
    except cg.InputError:
        pass
    assert not raises_input_error("# ok\nfbneo\trobotron\n")


def test_build_with_broken_list_exits_3():
    p, old = tsv("fbneo robotron\n"), cg.TWIN_STICK_TSV               # spaces, no tab
    cg.TWIN_STICK_TSV = p
    try:
        out = tempfile.mkdtemp()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = cg.main(["build", "--gamelists", os.path.join(FIX, "gamelists"),
                          "--picks", os.path.join(FIX, "picks.html"), "--out", out])
        assert rc == 3, rc
    finally:
        cg.TWIN_STICK_TSV = old
        os.unlink(p)


# ---- build_collections + nested ------------------------------------------------------

def systems_fixture():
    return {"fbneo": [row("robotron.zip", "Shoot'em Up", 1982), row("1942.zip", "Shoot'em Up", 1984),
                      row("smashtv.zip", "Shooter", 1990), row("llamatron.zip", "Twin Stick", 1991)],
            "n64": [row("Robotron 64.zip", "Shooter", 1998)],
            "snes": [row("Super Smash T.V. (USA).sfc", "Shoot'em Up", 1992), row("Smash Tennis (Europe).zip", "Sports")],
            "nes": [row("robotron.zip", "Shoot'em Up", 1989)]}       # same stem, other system: NOT twin-stick


def test_list_overrides_genre_text_and_only_one_genre():
    cols, _ = cg.build_collections(systems_fixture(), [], 1)
    assert cols["twin-stick"] == ["%ROMPATH%/fbneo/llamatron.zip", "%ROMPATH%/fbneo/robotron.zip",
                                  "%ROMPATH%/fbneo/smashtv.zip", "%ROMPATH%/n64/Robotron 64.zip",
                                  "%ROMPATH%/snes/Super Smash T.V. (USA).sfc"]
    assert cols["shmups"] == ["%ROMPATH%/fbneo/1942.zip", "%ROMPATH%/nes/robotron.zip"]
    assert cols["sports"] == ["%ROMPATH%/snes/Smash Tennis (Europe).zip"]


def test_empty_override_set_falls_back_to_genre_text():          # broken-case guard: the list is what moves them
    cols, _ = cg.build_collections(systems_fixture(), [], 1, twin_stick=frozenset())
    assert cols["twin-stick"] == ["%ROMPATH%/fbneo/llamatron.zip"]
    assert "%ROMPATH%/n64/Robotron 64.zip" in cols["shmups"]


def test_nested_puts_twin_stick_under_genres():
    assert "twin-stick" in nested.GENRE_SLUGS and nested.GENRE_TITLES["twin-stick"] == "Twin-Stick"
    systems = systems_fixture()
    cols, _ = cg.build_collections(systems, [], 1)
    model = nested.build_model(cols, systems)
    assert len(model["genres"]["twin-stick"]) == 5
    assert nested.folder_dir("twin-stick") == "twin-stick"         # not a MAME BIOS/device name


def test_every_genre_slug_has_title_and_blurb():
    text = os.path.join(SKILL, "folder_text.tsv")
    with open(text, encoding="utf-8") as f:
        rows = {tuple(l.split("\t")[:2]) for l in f.read().splitlines() if l and not l.startswith("#")}
    for slug in nested.GENRE_SLUGS:
        assert slug in nested.GENRE_TITLES, slug
        assert ("genres", slug) in rows, slug
        assert not nested.hidden_kind(slug), slug


# ---- cross-check with skills/controls -----------------------------------------------------

def test_diff_sets_reports_a_mismatch():                           # broken case for the cross-check itself
    d = diff_sets(EXPECTED, EXPECTED - {("n64", "Robotron 64")} | {("snes", "Smash T.V.")})
    assert d == ["only in twin_stick.tsv: n64/Robotron 64", "only in controls.py TWIN_STICK: snes/Smash T.V."]
    assert diff_sets(EXPECTED, set(EXPECTED)) == []


def test_twin_stick_matches_controls():
    if not os.path.exists(CONTROLS_PY):
        skip("test_twin_stick_matches_controls: no %s" % CONTROLS_PY)
        return
    controls = _load("controls_for_twin_stick", CONTROLS_PY)
    if not hasattr(controls, "TWIN_STICK"):
        skip("test_twin_stick_matches_controls: %s has no TWIN_STICK yet (#59/#64 not merged)" % CONTROLS_PY)
        return
    d = diff_sets(cg.load_twin_stick(), controls.TWIN_STICK)
    assert not d, "twin_stick.tsv and controls.py TWIN_STICK differ: %s" % "; ".join(d)


if __name__ == "__main__":
    main(globals(), per_test=True)
