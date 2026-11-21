#!/usr/bin/env python3
"""Tests for skills/collections/collgen.py (stdlib only; pytest or run directly).

    python3 skills/collections/tests/test_collgen.py

Counts on the trimmed fixtures are exact. Broken cases: a malformed gamelist and a
missing input must exit 3, bad usage must exit 2.
"""
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "lib"))
from hktest import main  # noqa: E402  (shared runner: one PASS per test, counted RESULT line, #151)

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures")
TOOL = os.path.join(SKILL, "collgen.py")

spec = importlib.util.spec_from_file_location("collgen", TOOL)
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)

GL = os.path.join(FIX, "gamelists")
PICKS = os.path.join(FIX, "picks.html")


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cg.main(list(argv))
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


def build(min_decade=None):
    systems = cg.load_gamelists(GL)
    picks = cg.load_picks(PICKS)
    kw = {} if min_decade is None else {"min_decade": min_decade}
    return cg.build_collections(systems, picks, **kw)


# ---- parsing -------------------------------------------------------------

def test_two_root_gamelist_parses():
    with open(os.path.join(GL, "cps2", "gamelist.xml"), encoding="utf-8") as f:
        text = f.read()
    assert "<alternativeEmulator>" in text            # fixture really has the 2nd root
    rows = cg.parse_gamelist(text)
    assert [r["fn"] for r in rows] == ["19xx.zip", "ssf2t.zip", "mvsc.zip"]
    assert rows[0]["year"] == 1996 and rows[0]["genre"] == "Shoot'em Up"


def test_malformed_gamelist_is_input_error():
    try:
        cg.parse_gamelist("<gameList><game><path>x</gameList>", "bad")
    except cg.InputError as e:
        assert "bad" in str(e)
    else:
        raise AssertionError("malformed gamelist must raise InputError")


def test_malformed_gamelist_exits_3():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "gl", "snes"))
        with open(os.path.join(d, "gl", "snes", "gamelist.xml"), "w") as f:
            f.write("<gameList><game>")
        rc, _o, err = run("build", "--gamelists", os.path.join(d, "gl"), "--picks", PICKS,
                          "--out", os.path.join(d, "out"))
        assert rc == 3 and "malformed" in err
        assert not os.path.exists(os.path.join(d, "out"))


def test_missing_inputs_exit_3_and_bad_usage_exits_2():
    assert run("build", "--gamelists", "/nonexistent", "--picks", PICKS)[0] == 3
    assert run("build", "--gamelists", GL, "--picks", "/nonexistent")[0] == 3
    assert run("build")[0] == 2
    assert run("frobnicate")[0] == 2


# ---- counts --------------------------------------------------------------

def test_genre_counts_exact_and_unknown_genre_ignored():
    cols, _ = build()
    counts = {k: len(v) for k, v in cols.items()}
    assert counts["fighters"] == 3          # ssf2t mvsc samsho
    assert counts["shmups"] == 3            # 19xx 1943 galaga ("Shooter")
    assert counts["platformers"] == 2       # sonic dkong
    for k, n in {"beat-em-up": 1, "puzzle": 1, "sports": 1, "run-and-gun": 1, "maze": 1}.items():
        assert counts[k] == n, k
    # "Quiz" has no bucket: the quiz game is in no genre collection
    assert not any("quiz.zip" in l for k, v in cols.items() if k in cg_genres() for l in v)


def cg_genres():
    return {"fighters", "shmups", "beat-em-up", "run-and-gun", "platformers", "puzzle",
            "sports", "racing", "maze"}


def test_decade_threshold_respected():
    cols, stats = build()                     # default threshold 25: nothing on fixtures
    assert not [k for k in cols if k[:2] in ("19", "20")]
    assert stats["dropped_decades"] == {"1980-84": 3, "1985-89": 1, "1990-94": 6, "1995-99": 3}
    cols, _ = build(3)
    assert {k: len(v) for k, v in cols.items() if k[0].isdigit()} == \
        {"1980-84": 3, "1990-94": 6, "1995-99": 3}   # 1985-89 (1 game) dropped
    cols, _ = build(4)
    assert [k for k in cols if k[0].isdigit()] == ["1990-94"]


def test_decade_label_edges():
    assert cg.decade_label(1994) == "1990-94" and cg.decade_label(1995) == "1995-99"
    assert cg.decade_label(2000) == "2000-04"
    assert cg.decade_label(1974) == "1970s" and cg.decade_label(1979) == "1970s"
    assert cg.decade_label(1969) is None and cg.decade_label(2015) is None


def _seventies_systems(n):
    rows = [{"fn": "g%d.zip" % i, "name": "G%d" % i, "nname": "g%d" % i, "genre": "Shooter",
             "year": 1978, "hidden": False} for i in range(n)]
    return {"fbneo": rows}


def test_seventies_bucket_kept_from_five_games():
    cols, stats = cg.build_collections(_seventies_systems(5), [])
    assert len(cols.get("1970s", [])) == 5, stats


def test_seventies_bucket_dropped_below_five():           # BROKEN CASE: 4 games is not a folder
    cols, stats = cg.build_collections(_seventies_systems(4), [])
    assert "1970s" not in cols and stats["dropped_decades"] == {"1970s": 4}, stats


def _row(fn, rating, genre="Fighting / Versus", name=None):
    name = name or os.path.splitext(fn)[0]
    return dict(fn=fn, name=name, nname=cg.norm(name), genre=genre, year=1995, rating=rating)


def _lines(system, *fns):
    return {"%ROMPATH%/" + system + "/" + f for f in fns}


def test_must_play_is_top_n_per_system():
    """#182: the best-rated N per system; a tie goes to the Cabinet Pick, then the name; unrated last."""
    systems = {"cps2": [_row("a.zip", 0.8), _row("b.zip", 0.9), _row("c.zip", 0.9), _row("d.zip", None), _row("e.zip", 0.6)],
               "snes": [_row("x.zip", None), _row("y.zip", 0.7)]}
    cols, _ = cg.build_collections(systems, [], 1, twin_stick=frozenset(), must_play_top=3)
    assert set(cols["must-play"]) == _lines("cps2", "b.zip", "c.zip", "a.zip") | _lines("snes", "y.zip", "x.zip")
    assert cols["must-play"] == sorted(cols["must-play"])
    # tie at 0.9: the pick wins the last slot (top 1)
    rec = cg.must_play_rank(systems["cps2"][2], True) < cg.must_play_rank(systems["cps2"][1], False)
    assert rec, "a pick sorts before a non-pick at the same rating"
    assert cg.must_play_rank(_row("z.zip", 0.1), False) < cg.must_play_rank(_row("u.zip", None), True), \
        "BROKEN: an unrated pick never beats a rated game"


def test_must_play_genres_min_rating():
    """#182: mpg-<bucket> = rating >= the minimum (0.9 = 4.5 stars); unrated and 4.49 stay out; twin-stick list wins."""
    systems = {"cps2": [_row("a.zip", 0.9), _row("b.zip", 0.898), _row("c.zip", None), _row("d.zip", 1.0, "Shoot'em Up"),
                        _row("e.zip", 0.95, "Shoot'em Up")]}
    cols, _ = cg.build_collections(systems, [], 1, twin_stick=frozenset({("cps2", "e")}))
    assert set(cols["mpg-fighters"]) == _lines("cps2", "a.zip")
    assert set(cols["mpg-shmups"]) == _lines("cps2", "d.zip") and set(cols["mpg-twin-stick"]) == _lines("cps2", "e.zip")
    cols, _ = cg.build_collections(systems, [], 1, twin_stick=frozenset(), must_play_genre_min=1.0)
    assert set(k for k in cols if k.startswith("mpg-")) == {"mpg-shmups"} and set(cols["mpg-shmups"]) == _lines("cps2", "d.zip")


def test_rating_parsing():
    rows = cg.parse_gamelist("<gameList><game><path>./a.zip</path><rating>0.9</rating></game>"
                             "<game><path>./b.zip</path><rating>junk</rating></game>"
                             "<game><path>./c.zip</path><rating>7</rating></game>"
                             "<game><path>./d.zip</path></game></gameList>")
    assert [r["rating"] for r in rows] == [0.9, None, None, None], "BROKEN: junk / out-of-range / missing = unrated"


def test_cli_rejects_bad_must_play_values():
    for args in (["--must-play-top", "0"], ["--must-play-genre-min", "6"], ["--must-play-genre-min", "-1"]):
        rc, out, err = run("build", "--gamelists", GL, "--picks", PICKS, "--out", "/nonexistent-out", *args)
        assert rc != 0, "BROKEN: %r is refused" % (args,)


# ---- title cleanup -------------------------------------------------------

def test_clean_titles_drops_tags_without_collision():
    out = cg.clean_titles(["Pac-Man (Midway)", "Galaga (Namco rev. B)", "Bar (Revision B)", "Plain"])
    assert out == ["Pac-Man", "Galaga", "Bar", "Plain"]


def test_clean_titles_keeps_tags_only_on_collision():
    names = ["Avenue Fighter II (World 910522)", "Avenue Fighter II (US 910522)",
             "Foo Fighters (Euro)", "Foo Fighters", "Solo (Japan)"]
    assert cg.clean_titles(names) == [
        "Avenue Fighter II (World 910522)", "Avenue Fighter II (US 910522)",
        "Foo Fighters (Euro)", "Foo Fighters", "Solo"]


def test_clean_titles_full_name_when_first_tag_still_collides():
    names = ["Game (World) (set 1)", "Game (World) (set 2)"]
    assert cg.clean_titles(names) == names
    assert len(set(cg.clean_titles(names))) == 2


# ---- fbneo-gamelist ------------------------------------------------------

def test_fbneo_gamelist_plan_then_apply():
    args = ["fbneo-gamelist", "--dat", os.path.join(FIX, "dat.txt"),
            "--zips", os.path.join(FIX, "zips.txt"), "--picks", PICKS]
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "sub", "gamelist.xml")
        rc, stdout, _ = run(*args, "--out", out)
        assert rc == 0 and "plan only" in stdout and not os.path.exists(out)
        assert "zips: 8  named: 7  with-year: 7  with-genre: 3" in stdout
        rc, stdout, _ = run(*args, "--out", out, "--apply")
        assert rc == 0 and os.path.exists(out)
        xml = open(out, encoding="utf-8").read()
        rows = cg.parse_gamelist(xml)
        names = {r["fn"]: r["name"] for r in rows}
        assert names["pacman.zip"] == "Pac-Man"
        assert names["sf2.zip"].endswith("(World 910522)")        # collision keeps tag
        assert names["foo.zip"] == "Foo Fighters (Euro)" and names["foo2.zip"] == "Foo Fighters"
        assert names["bar.zip"] == "Bar & Grill"
        assert names["missing.zip"] == "missing"                  # not in dat -> zip stem
        assert "Bar &amp; Grill" in xml                           # escaped on disk
        genres = {r["fn"]: r["genre"] for r in rows}
        assert genres["pacman.zip"] == "Maze" and genres["sf2.zip"] == "Fighting"
        assert genres["galaga.zip"] is None                       # 'cls' has no bucket
        years = {r["fn"]: r["year"] for r in rows}
        assert years["pacman.zip"] == 1980 and years["missing.zip"] == 0
        # second plan: identical file -> SAME, and output is byte-stable
        rc, stdout, _ = run(*args, "--out", out)
        assert "SAME" in stdout


def test_fbneo_bad_inputs_exit_3():
    d = os.path.join(FIX, "dat.txt")
    assert run("fbneo-gamelist", "--dat", "/nonexistent", "--zips", os.path.join(FIX, "zips.txt"),
               "--picks", PICKS)[0] == 3
    with tempfile.TemporaryDirectory() as t:
        empty = os.path.join(t, "empty.txt")
        open(empty, "w").close()
        assert run("fbneo-gamelist", "--dat", d, "--zips", empty, "--picks", PICKS)[0] == 3
        assert run("fbneo-gamelist", "--dat", empty, "--zips", os.path.join(FIX, "zips.txt"),
                   "--picks", PICKS)[0] == 3


# ---- build plan / apply (end to end via the CLI) --------------------------

def test_build_plan_writes_nothing_apply_writes_and_is_idempotent():
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "out")
        base = ["build", "--gamelists", GL, "--picks", PICKS, "--out", out, "--min-decade", "3"]
        rc, stdout, _ = run(*base)
        assert rc == 0 and "plan only" in stdout and not os.path.exists(out)
        assert "fighters" in stdout and "NEW" in stdout
        rc, stdout, _ = run(*base, "--apply")
        assert rc == 0
        files = sorted(os.listdir(out))
        assert "custom-must-play.cfg" in files and "custom-1990-94.cfg" in files
        assert "custom-1985-89.cfg" not in files
        lines = open(os.path.join(out, "custom-must-play.cfg")).read().splitlines()
        assert len(lines) == 14 and all(l.startswith("%ROMPATH%/") for l in lines)   # every fixture system < 25 games
        rc, stdout, _ = run(*base)
        assert "SAME" in stdout and "NEW" not in stdout and "CHANGED" not in stdout
        with open(os.path.join(out, "custom-old.cfg"), "w") as f:
            f.write("x\n")
        assert "STALE" in run(*base)[1]
        # script directly via subprocess (shebang-free invocation, exit code)
        r = subprocess.run([sys.executable, TOOL, "build", "--gamelists", GL, "--picks", PICKS,
                            "--out", out], capture_output=True, text=True)
        assert r.returncode == 0


if __name__ == "__main__":
    main(globals(), per_test=True)
