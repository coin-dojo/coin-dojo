#!/usr/bin/env python3
"""Tests for the SERIES folder of `collgen.py nested` (series.tsv, ticket #55). Stdlib only; pytest or direct.

    python3 skills/collections/tests/test_series.py

Broken cases: a bad regex, a wrong field count, a bad slug/min, a duplicate slug and an empty file stop the
run with a clear error (nested.load_series ValueError, collgen exit 3); a bad row in the cases file is an error.

The curated lists come from nested.data_path (#190): the maintainer's in personal/skills/collections/ in a private checkout,
else the community examples beside the tools. Cases that name the maintainer's own franchises live in
personal/skills/collections/tests/series_cases.tsv (kind<TAB>slug<TAB>value; kind in/out/min/blurb) and are added
to the hand-kept ones below when that file is there.
"""
import importlib.util
import io
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "lib"))
from hktest import main  # noqa: E402  (shared runner: one PASS per test, counted RESULT line, #151)

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, SKILL)
spec = importlib.util.spec_from_file_location("collgen", os.path.join(SKILL, "collgen.py"))
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)
import nested  # noqa: E402

SERIES_TSV = nested.data_path("series.tsv")
FOLDER_TEXT = nested.data_path("folder_text.tsv")
CASES_TSV = os.path.join(SKILL, "..", "..", "personal", "skills", "collections", "tests", "series_cases.tsv")
CASE_KINDS = ("in", "out", "min", "blurb")


def load_cases(path):
    """series_cases.tsv -> {kind: {slug: [value, ...]}}; a missing file is no cases, a bad row raises ValueError."""
    cases = {k: {} for k in CASE_KINDS}
    if not os.path.isfile(path):
        return cases
    with open(path, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 3 or parts[0] not in CASE_KINDS or not parts[1] or not parts[2]:
                raise ValueError("%s:%d: want kind (in/out/min/blurb), slug, value" % (path, n))
            cases[parts[0]].setdefault(parts[1], []).append(parts[2])
    return cases


CASES = load_cases(CASES_TSV)


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cg.main(list(argv))
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


def tmp():
    return tempfile.mkdtemp()


def write(text, name="series.tsv"):
    p = os.path.join(tmp(), name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def el(name, developer="", hidden=""):
    g = ET.Element("game")
    for tag, v in (("name", name), ("developer", developer), ("hidden", hidden)):
        if v:
            ET.SubElement(g, tag).text = v
    return g


def games(spec):
    """{system: [(file, name[, developer[, hidden]])]} -> {system: {file: Element}} like nested.raw_games."""
    return {s: {t[0]: el(*t[1:]) for t in rows} for s, rows in spec.items()}


def names(found, games_):
    return {k: sorted(games_[s][fn].findtext("name") for s, fn in v) for k, v in found.items()}


def rules_of(text):
    return nested.load_series(write(text))


# --------------------------------------------------------------------------- membership

def test_real_series_tsv_loads_and_every_series_has_a_blurb():
    rules = nested.load_series(SERIES_TSV)
    texts = nested.load_folder_text(FOLDER_TEXT)
    assert len(rules) >= 30
    assert {r["slug"] for r in rules} == {f for (v, f) in texts if v == "series"}, "series.tsv and folder_text.tsv differ"
    assert all(t.endswith(".") and "\n" not in t for (v, _f), t in texts.items() if v == "series")


def test_membership_by_name_regex_and_min_three():
    r = rules_of("ab\tAvenue Fighter\tAvenue Fighter\n")
    g = games({"cps2": [("a.zip", "Avenue Fighter Zero 2"), ("b.zip", "Super Avenue Fighter II")],
               "snes": [("c.zip", "Avenue Fighter II Turbo")], "nes": [("d.zip", "Mario")]})
    assert names(nested.build_series(r, g), g) == {
        "ab": ["Avenue Fighter II Turbo", "Avenue Fighter Zero 2", "Super Avenue Fighter II"]}


def test_below_three_is_dropped_but_a_min_two_series_keeps_two():
    r = rules_of("ab\tAvenue Fighter\tAvenue Fighter\nlast-round\tLast Round\tLast Round\t-\t-\t2\n")
    g = games({"cps2": [("a.zip", "Avenue Fighter Zero 2"), ("b.zip", "Avenue Fighter III")],
               "cps1": [("c.zip", "Last Round")], "nes": [("d.zip", "Mighty Last Round")]})
    assert names(nested.build_series(r, g), g) == {"last-round": ["Last Round", "Mighty Last Round"]}
    one = games({"cps1": [("c.zip", "Last Round")]})
    assert nested.build_series(r, one) == {}                      # two is the floor even with min 2


def test_real_series_tsv_non_default_mins_are_the_listed_ones():
    rules = nested.load_series(SERIES_TSV)
    want = {s: int(v[-1]) for s, v in CASES["min"].items()}      # personal cases; the example has none
    assert {r["slug"]: r["min"] for r in rules if r["min"] != 3} == want


def test_system_filter_and_developer_filter():
    r = rules_of("x\tX\tFoo\tnes,snes\t-\t-\ny\tY\t.\t-\tToaplan\t-\n")
    g = games({"nes": [("a.zip", "Foo 1"), ("b.zip", "Foo 2")], "snes": [("c.zip", "Foo 3")],
               "fbneo": [("d.zip", "Foo 4", "Acme"), ("e.zip", "Tiger-Heli", "Toaplan"),
                         ("f.zip", "Zero Wing", "Toaplan"), ("g.zip", "Truxton", "Toaplan"),
                         ("h.zip", "Truxton II", "Toaplan Corp")]})
    got = names(nested.build_series(r, g), g)
    assert got["x"] == ["Foo 1", "Foo 2", "Foo 3"]                # fbneo "Foo 4" is outside the system list
    assert got["y"] == ["Tiger-Heli", "Truxton", "Zero Wing"]     # developer must match fully


def test_hidden_games_never_match():
    r = rules_of("x\tX\tFoo\n")
    g = games({"nes": [("a.zip", "Foo 1"), ("b.zip", "Foo 2"), ("c.zip", "Foo 3", "", "true")]})
    assert nested.build_series(r, g) == {}                        # only 2 visible


def test_a_game_can_sit_in_two_series():
    r = rules_of("cave\tCave\t^(?!.*DonPachi)\t-\tCave\t-\ndonpachi\tDonPachi\tDonPachi\n")
    g = games({"fbneo": [("a.zip", "DonPachi", "Cave"), ("b.zip", "DoDonPachi", "Cave"), ("c.zip", "DoDonPachi III", "Cave"),
                         ("d.zip", "Ketsui", "Cave"), ("e.zip", "Espgaluda", "Cave"), ("f.zip", "Guwange", "Cave")]})
    got = names(nested.build_series(r, g), g)
    assert got["donpachi"] == ["DoDonPachi", "DoDonPachi III", "DonPachi"]
    assert got["cave"] == ["Espgaluda", "Guwange", "Ketsui"]      # the DonPachi games stay out of the Cave folder


def test_regex_case_sensitive_unless_flagged():
    r = rules_of("a\tA\tsonic\nb\tB\t(?i)sonic\n")
    g = games({"x": [("1.zip", "Sonic 1"), ("2.zip", "Sonic 2"), ("3.zip", "sonic 3")]})
    assert names(nested.build_series(r, g), g) == {"b": ["Sonic 1", "Sonic 2", "sonic 3"]}


# ------------------------------------------------------ false-positive guard (the real series.tsv)

NOT_IN = {  # display name (developer) -> series it must NOT land in
    "sonic": ["Sonic Blast Man", "Sonic Wings", "ChuChu Rocket!", "SegaSonic Cosmo Fighter", "Supersonic"],
    "mario": ["Wario Land", "Warioware", "Mariokart"],
    "contra": ["Contraption", "Contraband", "Control"],
    "star-soldier": ["Pretty Soldier Sailor Moon", "Alien Soldier", "Soldier of Fortune"],
    "shinobi": ["Altered Beast", "Golden Axe"],
    "king-of-fighters": ["Fatal Fury: King of Fighters", "Fighters Megamix"],
    "194x": ["NAM-1975", "1945 Kai", "Strikers 1945"],
    "gradius": ["Nemesis Wars", "Galactic Gradient"],
    "galaga": ["Galactic Attack"],
    "donkey-kong": ["Donkey Bonkers"],
    "pac-man": ["Pac-it"],
    "puyo": ["Puzzle League"],
    "double-dragon": ["Dragon Buster", "Bad Dudes vs. Dragonninja"],
    "tetris": ["Puzzle League"],
    # #55c (2026-11-15)
    "marvel": ["Avengers", "Marvel Land", "The Punisher 2", "Spider-Man Clone"],      # Avengers = a 1987 shooter, not Marvel
    "sengoku": ["Sengoku Blade", "Sengoku Ace", "Sengoku Denshou 2001"],              # Psikyo shooters, not SNK's brawler
    "gauntlet": ["Gauntlet Legends", "Gauntlet: Dark Legacy"],
    "dig-dug": ["Digger", "Dig Fight", "Lode Runner: The Dig Fight"],
    "lode-runner": ["Atomic Runner", "Road Runner"],
    "road-rash": ["Road Runner", "RoadBlasters"],
    "thunder-force": ["Thunder Fox", "Thunder Cross", "Thunder Dragon", "Thunder Blade"],
    "rolling-thunder": ["Thunder Blade", "Rolling Rock"],
    "fantasy-zone": ["Fantasy Star Soldier"],
    "king-of-the-monsters": ["The King of Fighters '98", "Master of Monsters"],
    "crash-bandicoot": ["Crash 'n the Boys", "The Incredible Crash Dummies"],
    "space-invaders": ["Space Harrier", "Space Megaforce"],
    "star-wars": ["Star Fox", "Stargate"],
    "punch-out": ["Punch Line"],
}
IN = {
    "sonic": ["Sonic The Hedgehog 2", "Sonic CD", "Sonic & Knuckles + Sonic The Hedgehog 3", "SegaSonic The Hedgehog"],
    "mario": ["Super Mario World", "Mario Bros.", "Dr. Mario"],
    "contra": ["Contra", "Super Contra", "Contra III: The Alien Wars", "Super C", "Probotector"],
    "strikers": ["Sonic Wings", "Aero Fighters 2", "Strikers 1945 II"],
    "194x": ["1942", "1943 Kai: Midway Kaisen", "19XX: The War Against Destiny", "1941: Counter Attack"],
    "mega-man": ["Rockman & Forte", "Mega Man X2"],
    "ghosts-n-goblins": ["Ghouls'n Ghosts", "Super Ghouls 'N Ghosts", "Ghosts'n Goblins"],
    "king-of-fighters": ["The King of Fighters '98: The Slugfest"],
    "fatal-fury": ["Fatal Fury: King of Fighters", "Garou: Mark of the Wolves"],
    "gradius": ["Nemesis", "Vulcan Venture", "Life Force", "Salamander"],
    # #55c (2026-11-15); the crossovers (personal cases) sit in two series
    "marvel": ["X-Men: Children of the Atom", "X-Men", "X-Men 2: Clone Wars", "X-Men: Mutant Apocalypse",
               "Marvel Super Heroes", "The Punisher", "Captain America and The Avengers"],
    "sengoku": ["Sengoku", "Sengoku 2", "Sengoku 3"],
    "gauntlet": ["Gauntlet", "Gauntlet II", "Gauntlet IV"],
    "dig-dug": ["Dig Dug", "Dig Dug II"],
    "lode-runner": ["Lode Runner", "Lode Runner IV: Teikoku Karano Dasshutsu", "Lode Runner: The Dig Fight"],
    "thunder-force": ["Thunder Force II", "Thunder Force V: Perfect System", "Lightening Force: Quest for the Darkstar"],
    "rolling-thunder": ["Rolling Thunder", "Rolling Thunder 3"],
    "fantasy-zone": ["Fantasy Zone", "Fantasy Zone II: The Tears of Opa-Opa", "Super Fantasy Zone"],
    "king-of-the-monsters": ["King of the Monsters", "King of the Monsters 2: The Next Thing"],
    "crash-bandicoot": ["Crash Bandicoot", "Crash Bandicoot 3: Warped"],
    "space-invaders": ["Space Invaders", "Space Invaders DX", "Space Invaders '95: The Attack Of Lunar Loonies",
                       "Space Invaders / Space Invaders M"],
    "star-wars": ["Star Wars", "Super Star Wars", "Star Wars: The Empire Strikes Back"],
    "punch-out": ["Punch-Out!!", "Super Punch-Out!!", "Mike Tyson's Punch-Out!!"],
    "splatterhouse": ["Splatterhouse", "Splatterhouse 3"], "phantasy-star": ["Phantasy Star IV"],
    "road-rash": ["Road Rash", "Road Rash II"], "micro-machines": ["Micro Machines 64 Turbo"],
    "dragon-ball": ["Dragon Ball Z", "Dragon Ball Z: Hyper Dimension"],
}
for _slug, _names in CASES["out"].items():
    NOT_IN[_slug] = NOT_IN.get(_slug, []) + _names
for _slug, _names in CASES["in"].items():
    IN[_slug] = IN.get(_slug, []) + _names


def _matches(slug, name):
    r = next(x for x in nested.load_series(SERIES_TSV) if x["slug"] == slug)
    return bool(r["rx"].search(name)) and r["systems"] is None and r["dev"] is None


def test_false_positives_stay_out_and_true_matches_get_in():
    for slug, bad in NOT_IN.items():
        for name in bad:
            assert not _matches(slug, name), "%s wrongly matches %s" % (name, slug)
    for slug, good in IN.items():
        for name in good:
            assert _matches(slug, name), "%s should match %s" % (name, slug)


def bios_named_slugs(rules, hidden):
    """Series slugs that are a MAME BIOS/device short name (ES-DE would hide such a folder on an arcade platform)."""
    return sorted(r["slug"] for r in rules if r["slug"].lower() in hidden)


def test_real_series_slugs_are_not_mame_bios_or_device_names():
    hidden = nested.load_hidden_names()
    assert bios_named_slugs(nested.load_series(SERIES_TSV), hidden) == []


def test_bios_named_slug_is_caught():                             # BROKEN CASE
    hidden = nested.load_hidden_names()
    rules = rules_of("neogeo\tNeo Geo\tMetal Slug\nsengoku\tSengoku\t^Sengoku\n")
    assert bios_named_slugs(rules, hidden) == ["neogeo"]
    assert nested.folder_dir("neogeo") != "neogeo"                # nested would still write it under another dir


def test_new_series_55c_reach_their_floor_with_realistic_names():
    """#55c rows against the 2026-11-15 cabinet names (sample): each new series makes its folder."""
    rules = nested.load_series(SERIES_TSV)
    g = games({"neogeo": [("sengoku.zip", "Sengoku", "SNK"), ("sengoku2.zip", "Sengoku 2", "SNK"),
                          ("sengoku3.zip", "Sengoku 3", "Noise Factory")],
               "fbneo": [("sngkace.zip", "Sengoku Ace", "Psikyo"), ("avengers.zip", "Avengers", "Acme"),
                         ("xmen.zip", "X-Men", "Konami"), ("msh.zip", "Marvel Super Heroes", "Acme")],
               "snes": [("xmma.zip", "X-Men: Mutant Apocalypse", "Acme")]})
    got = names(nested.build_series(rules, g), g)
    assert got["sengoku"] == ["Sengoku", "Sengoku 2", "Sengoku 3"]
    assert got["marvel"] == ["Marvel Super Heroes", "X-Men", "X-Men: Mutant Apocalypse"]


FOLDER_PICKS = nested.data_path(os.path.join("folder_photos", "folder_picks.tsv"))


def series_pick_problems(picks_path, slugs):
    """folder_picks.tsv vs the series slugs -> list of problems: a series without a picks row, a row for an unknown
    series, not 3-4 regexes, or a regex that does not compile."""
    import re
    rows, problems = {}, []
    for line in open(picks_path, encoding="utf-8"):
        if not line.strip() or line.startswith("#"):
            continue
        v, folder, pats = line.rstrip("\n").split("\t")
        if v != "series":
            continue
        rows[folder] = pats.split("|")
    for slug in slugs:
        if slug not in rows:
            problems.append("no picks for " + slug)
    for folder, pats in rows.items():
        if folder not in slugs:
            problems.append("picks for unknown series " + folder)
        if not 3 <= len(pats) <= 4:
            problems.append("%s: %d picks" % (folder, len(pats)))
        for p in pats:
            try:
                re.compile(p)
            except re.error:
                problems.append("%s: bad regex %r" % (folder, p))
    return problems


def test_every_series_has_3_or_4_compiling_picks():
    slugs = [r["slug"] for r in nested.load_series(SERIES_TSV)]
    assert series_pick_problems(FOLDER_PICKS, slugs) == []


def test_bad_or_missing_series_picks_are_caught():                 # BROKEN CASE
    p = write("series\tab\ta|b(|c|d\nseries\tghost\ta|b|c\nseries\ttiny\ta|b\n", "folder_picks.tsv")
    got = series_pick_problems(p, ["ab", "tiny", "marvel"])
    assert "no picks for marvel" in got and "picks for unknown series ghost" in got
    assert "tiny: 2 picks" in got and any("bad regex" in x for x in got), got


def test_developer_series_ignore_ports_by_other_companies_names():
    rules = nested.load_series(SERIES_TSV)
    g = games({"fbneo": [("a.zip", "Tiger-Heli", "Toaplan"), ("b.zip", "Twin Cobra", "Toaplan"),
                         ("c.zip", "Zero Wing", "Toaplan"), ("d.zip", "Ketsui", "Cave"), ("e.zip", "Guwange", "Cave"),
                         ("f.zip", "Ibara", "Cave"), ("g.zip", "Truxton Fan Game", "Someone")]})
    got = names(nested.build_series(rules, g), g)
    assert got["toaplan"] == ["Tiger-Heli", "Twin Cobra", "Zero Wing"] and got["cave"] == ["Guwange", "Ibara", "Ketsui"]


# ------------------------------------------------------------------ the nested run end to end

def _gl(spec):
    """{system: [(file, name, genre, developer)]} -> gamelists dir."""
    root = os.path.join(tmp(), "gl")
    for system, rows in spec.items():
        os.makedirs(os.path.join(root, system))
        body = "".join(
            "\t<game>\n\t\t<path>./%s</path>\n\t\t<name>%s</name>\n\t\t<releasedate>19950101T000000</releasedate>\n"
            "\t\t<genre>%s</genre>\n\t\t<developer>%s</developer>\n\t</game>\n" % r for r in rows)
        with open(os.path.join(root, system, "gamelist.xml"), "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0"?>\n<gameList>\n' + body + "</gameList>\n")
    return root


def _picks():
    p = os.path.join(tmp(), "picks.html")
    open(p, "w").write('const P=[["Art of Fighting","fig","arc","1990s",1]];')
    return p


SPEC = {"neogeo": [("kof94.zip", "The King of Fighters '94", "Fighting", "SNK"),
                   ("kof95.zip", "The King of Fighters '95", "Fighting", "SNK"),
                   ("kof96.zip", "The King of Fighters '96", "Fighting", "SNK"),
                   ("aof.zip", "Art of Fighting", "Fighting", "SNK")]}


def _nested(*extra, apply=True):
    out = os.path.join(tmp(), "o")
    rc, o, e = run("nested", "--gamelists", _gl(SPEC), "--picks", _picks(), "--out", out, "--min-decade", "1",
                   *extra, *(["--apply"] if apply else []))
    return rc, o, e, out


def test_series_folder_and_genre_folder_both_get_the_game():
    rc, _o, err, out = _nested("--series", SERIES_TSV, "--folder-text", FOLDER_TEXT)
    assert rc == 0, err
    sroot = ET.parse(os.path.join(out, "gamelists", "series", "gamelist.xml")).getroot()
    folders = {f.findtext("path"): f for f in sroot.findall("folder")}
    assert list(folders) == ["./king-of-fighters"], "Art of Fighting has 1 game, below the 3 minimum"
    f = folders["./king-of-fighters"]
    assert f.findtext("name") == "The King of Fighters" and f.findtext("genre") == "Game series"
    assert f.findtext("desc").endswith("\n\n3 games") and "SNK" in f.findtext("desc") + f.findtext("developer")
    assert sorted(g.findtext("path") for g in sroot.findall("game")) == [
        "./king-of-fighters/kof94.zip", "./king-of-fighters/kof95.zip", "./king-of-fighters/kof96.zip"]
    assert os.path.islink(os.path.join(out, "roms_nested", "series", "king-of-fighters", "kof95.zip"))
    gen = ET.parse(os.path.join(out, "gamelists", "genres", "gamelist.xml")).getroot()
    assert "./fighters/kof95.zip" in [g.findtext("path") for g in gen.findall("game")]      # still in its genre
    assert "./1995-99/kof95.zip" in [g.findtext("path") for g in
                                    ET.parse(os.path.join(out, "gamelists", "decades", "gamelist.xml")).getroot().findall("game")]


def test_carousel_order_has_series_between_genres_and_decades():
    rc, _o, err, out = _nested("--series", SERIES_TSV)
    assert rc == 0, err
    with open(os.path.join(out, "custom_systems", "es_systems.xml"), encoding="utf-8") as f:
        sysx = ET.fromstring("<w>" + f.read().split("?>", 1)[1] + "</w>")      # <loadExclusive/> sits outside <systemList>
    assert [s.findtext("name") for s in sysx.iter("system")] == ["must-play", "genres", "series", "decades", "systems"]
    assert [s.findtext("fullname") for s in sysx.iter("system") if s.findtext("name") == "series"] == ["SERIES"]
    assert [s.findtext("systemsortname") for s in sysx.iter("system")] == [
        "0-must-play", "1-genres", "2-series", "3-decades", "4-systems"]


def test_without_series_flag_nothing_changes():
    rc, _o, err, out = _nested()
    assert rc == 0, err
    assert not os.path.exists(os.path.join(out, "gamelists", "series"))
    assert not os.path.exists(os.path.join(out, "roms_nested", "series"))


def test_no_series_reaching_the_minimum_means_no_series_system():
    p = write("ab\tAvenue Fighter\tNo such game\n")
    rc, _o, err, out = _nested("--series", p)
    assert rc == 0, err
    assert not os.path.exists(os.path.join(out, "gamelists", "series"))


def test_missing_folder_art_is_harmless_and_run_is_idempotent():
    art = tmp()                                                  # empty dir: no series/<slug>.png
    rc, _o, err, out = _nested("--series", SERIES_TSV, "--folder-art", art)
    assert rc == 0, err
    assert not os.path.exists(os.path.join(out, "downloaded_media", "series"))
    rc2, o2, err2 = run("nested", "--gamelists", _gl(SPEC), "--picks", _picks(), "--out", out, "--min-decade", "1",
                        "--series", SERIES_TSV, "--folder-art", art)
    assert rc2 == 0 and "SAME" in o2 and "NEW" not in o2, (rc2, o2, err2)


def test_series_system_dir_in_gamelists_is_not_a_real_system():
    gl = _gl(SPEC)
    os.makedirs(os.path.join(gl, "series"))
    with open(os.path.join(gl, "series", "gamelist.xml"), "w") as f:
        f.write("<gameList><game><path>./x/kof94.zip</path><name>The King of Fighters '94</name></game></gameList>")
    out = os.path.join(tmp(), "o")
    rc, _o, err = run("nested", "--gamelists", gl, "--picks", _picks(), "--out", out, "--min-decade", "1",
                      "--series", SERIES_TSV, "--apply")
    assert rc == 0, err
    root = ET.parse(os.path.join(out, "gamelists", "series", "gamelist.xml")).getroot()
    assert len(root.findall("game")) == 3                         # the deployed virtual copy was ignored


# ------------------------------------------------------------------------------ BROKEN CASES

def _bad(text, needle):
    try:
        nested.load_series(write(text))
    except ValueError as e:
        assert needle in str(e), str(e)
        assert "series.tsv:" in str(e)                            # file:line in the message
        return
    raise AssertionError("expected ValueError for %r" % text)


def test_bad_regex_is_a_clear_error():                         # BROKEN CASE
    _bad("# c\nab\tAvenue Fighter\tAvenue (Fighter\n", "bad regex in series ab")
    _bad("ab\tAvenue Fighter\tok\t-\t[unclosed\n", "bad regex")


def test_malformed_rows_are_errors():                          # BROKEN CASE
    _bad("ab\tonly two fields\n", "want 3 to 6")
    _bad("ab\tT\tx\t-\t-\t3\textra\n", "want 3 to 6")
    _bad("ab\t\tx\n", "want 3 to 6")
    _bad("Avenue Fighter\tT\tx\n", "bad slug")
    _bad("ab\tT\tx\t-\t-\tmany\n", "bad min")
    _bad("ab\tT\tx\t-\t-\t0\n", "bad min")
    _bad("ab\tT\tx\nab\tT2\ty\n", "duplicate series ab")


def test_empty_series_file_is_an_error():                      # BROKEN CASE
    _bad("# only comments\n\n", "no series rows")


def test_cli_exits_3_with_the_reason_on_a_bad_series_file():   # BROKEN CASE
    bad = write("ab\tAvenue Fighter\tAvenue (Fighter\n")
    out = os.path.join(tmp(), "o")
    rc, _o, err = run("nested", "--gamelists", _gl(SPEC), "--picks", _picks(), "--out", out, "--series", bad, "--apply")
    assert rc == 3 and "--series:" in err and "bad regex" in err, (rc, err)
    assert not os.path.exists(out)                              # nothing written


def test_cli_missing_series_file_exits_3():                    # BROKEN CASE
    rc, _o, err = run("nested", "--gamelists", _gl(SPEC), "--picks", _picks(), "--out", os.path.join(tmp(), "o"),
                      "--series", os.path.join(tmp(), "nope.tsv"))
    assert rc == 3 and "--series:" in err, (rc, err)


# ---------------------------------------------------------- blurbs only name games that are in the folder

# hand-kept: the game titles each series blurb names (substring of the game's display name, case-insensitive)
BLURB_TITLES = {
    "mega-man": ["Mega Man X"], "toaplan": ["Tiger-Heli", "Twin Cobra", "Truxton", "Batsugun"],
    "cave": ["Mushihime-Sama", "Espgaluda", "Ketsui", "Deathsmiles"],
    "king-of-fighters": ["King of Fighters '94", "King of Fighters 2003"],
    "gradius": ["Gradius", "Salamander", "Life Force", "Parodius"],
    "fatal-fury": ["Fatal Fury", "Garou: Mark of the Wolves"],
    "tmnt": ["Turtles in Time", "Tournament Fighters"], "tetris": ["Grand Master"],
    "194x": ["1941", "1942", "1943", "1944", "19XX"], "castlevania": ["Super Castlevania IV"],
    "strikers": ["Strikers 1945", "Aero Fighters"], "donpachi": ["DonPachi", "DoDonPachi", "Dai-Fukkatsu", "SaiDaiOuJou"],
    "donkey-kong": ["Donkey Kong Country"], "puyo": ["Puyo Puyo", "Puzzle Bobble", "Mean Bean Machine"],
    "galaga": ["Galaga", "Galaxian", "Gaplus"], "pac-man": ["Pac-Man", "Ms. Pac-Man", "Pac-Land"],
    "shinobi": ["The Revenge of Shinobi", "Shinobi III"], "sonic": ["SegaSonic"],
    "ghosts-n-goblins": ["Ghosts'n Goblins", "Ghouls'n Ghosts", "Super Ghouls"],
    "ninja-gaiden": ["Ninja Gaiden"], "bubble-bobble": ["Bubble Bobble", "Rainbow Islands"],
    "golden-axe": ["Death Adder"], "mario": ["Mario Bros.", "Mario Kart", "Super Mario World"],
    "star-soldier": ["Soldier Blade", "Super Star Soldier", "Fantasy Star Soldier"],
    "art-of-fighting": ["Path of the Warrior"],
    # #55c
    "marvel": ["Children of the Atom", "X-Men", "The Punisher"],
    "lode-runner": ["Lode Runner", "Dig Fight"], "space-invaders": ["Space Invaders DX", "Space Invaders '95"],
    "star-wars": ["Star Wars", "Super Star Wars"], "thunder-force": ["Thunder Force III", "Lightening Force"],
    "dragon-ball": ["Dragon Ball Z", "Hyper Dimension"], "phantasy-star": ["Phantasy Star II", "Phantasy Star IV"],
    "crash-bandicoot": ["Crash Bandicoot"],
}


for _slug, _titles in CASES["blurb"].items():
    BLURB_TITLES[_slug] = BLURB_TITLES.get(_slug, []) + _titles


def _blurbs():
    return {f: t for (v, f), t in nested.load_folder_text(FOLDER_TEXT).items() if v == "series"}


def test_blurb_title_list_only_names_known_series():
    assert set(BLURB_TITLES) <= set(_blurbs()), set(BLURB_TITLES) - set(_blurbs())
    assert all(BLURB_TITLES.values())


def test_blurb_titles_exist_in_their_series_on_the_real_gamelists():
    """Needs the cabinet's gamelists (not in git): COLLECTIONS_REAL_GAMELISTS=/path/to/gl. Skipped without them."""
    gl = os.environ.get("COLLECTIONS_REAL_GAMELISTS")
    if not gl or not os.path.isdir(gl):
        return
    rules = nested.load_series(SERIES_TSV)
    g = nested.raw_games(gl, cg.cleaned_texts(gl, None, None))
    found = names(nested.build_series(rules, g), g)
    for slug, titles in BLURB_TITLES.items():
        lowered = [n.lower() for n in found.get(slug, [])]
        for title in titles:
            assert any(title.lower() in n for n in lowered), "%s: blurb names %r, no such game in the folder" % (slug, title)


def test_cases_file_rows_load_and_a_missing_file_is_no_cases():
    p = write("# c\nin\tab\tAvenue Fighter II\nout\tab\tAlley Cat\nmin\tlr\t2\nblurb\tab\tZero 2\n", "series_cases.tsv")
    got = load_cases(p)
    assert got["in"] == {"ab": ["Avenue Fighter II"]} and got["out"] == {"ab": ["Alley Cat"]}
    assert got["min"] == {"lr": ["2"]} and got["blurb"] == {"ab": ["Zero 2"]}
    assert load_cases(os.path.join(tmp(), "nope.tsv")) == {k: {} for k in CASE_KINDS}


def test_bad_cases_row_is_an_error():                             # BROKEN CASE
    for bad in ("maybe\tab\tx\n", "in\tab\n", "in\t\tx\n"):
        try:
            load_cases(write(bad, "series_cases.tsv"))
        except ValueError as e:
            assert "series_cases.tsv:1" in str(e), str(e)
            continue
        raise AssertionError("expected ValueError for %r" % bad)


if __name__ == "__main__":
    main(globals(), per_test=True)
