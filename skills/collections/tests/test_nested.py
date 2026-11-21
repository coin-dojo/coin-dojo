#!/usr/bin/env python3
"""Tests for `collgen.py nested` / nested.py (stdlib only; pytest or run directly).

    python3 skills/collections/tests/test_nested.py

Broken cases: malformed gamelist and missing --media dir exit 3, a real file in the way of a symlink is
refused, bad usage exits 2.
"""
import importlib.util
import io
import os
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "lib"))
from hktest import main  # noqa: E402  (shared runner: one PASS per test, counted RESULT line, #151)

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures")
GL, PICKS = os.path.join(FIX, "gamelists"), os.path.join(FIX, "picks.html")
sys.path.insert(0, SKILL)
spec = importlib.util.spec_from_file_location("collgen", os.path.join(SKILL, "collgen.py"))
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)
import nested  # noqa: E402


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cg.main(list(argv))
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


def gen(out, *extra, apply=True):
    base = ["nested", "--gamelists", GL, "--picks", PICKS, "--out", out, "--min-decade", "1"]
    return run(*base, *extra, *(["--apply"] if apply else []))


def tmp():
    return tempfile.mkdtemp()


def snapshot(root):
    snap = {}
    for dp, _d, fns in os.walk(root):
        for f in fns:
            p = os.path.join(dp, f)
            snap[os.path.relpath(p, root)] = os.readlink(p) if os.path.islink(p) else open(p, encoding="utf-8").read()
    return snap


def test_plan_writes_nothing():
    out = os.path.join(tmp(), "o")
    rc, stdout, _ = gen(out, apply=False)
    assert rc == 0 and "plan only" in stdout and "NEW" in stdout
    assert not os.path.exists(out)


def test_model_has_four_entries_must_play_flat():
    systems = cg.load_gamelists(GL)
    cols, _ = cg.build_collections(systems, cg.load_picks(PICKS), 1)
    m = nested.build_model(cols, systems, flat_must_play=True)
    assert list(m) == ["must-play", "genres", "decades", "systems"]
    assert list(m["must-play"]) == [""] and len(m["must-play"][""]) == 14      # #182: top 25 per system = every fixture game
    assert "fighters" in m["genres"] and "1990-94" in m["decades"]
    assert set(m["systems"]) == {"cps2", "fbneo", "genesis", "neogeo"}


def test_apply_builds_symlink_tree_and_files():
    out = tmp()
    rc, stdout, _ = gen(out, "--rom-root", "/data/roms")
    assert rc == 0 and "applied" in stdout
    link = os.path.join(out, "roms_nested", "genres", "fighters", "ssf2t.zip")
    assert os.path.islink(link) and os.readlink(link) == "/data/roms/cps2/ssf2t.zip"
    assert os.path.islink(os.path.join(out, "roms_nested", "must-play", "cps2", "mvsc.zip"))   # one folder per system
    for v in ("must-play", "genres", "decades", "systems"):
        assert os.path.isfile(os.path.join(out, "gamelists", v, "gamelist.xml"))


def test_es_systems_xml_exclusive_sorted_and_parses():
    out = tmp()
    gen(out, "--nested-root", "/data/nested", "--launcher", "python3 /x/launch.py")
    text = open(os.path.join(out, "custom_systems", "es_systems.xml"), encoding="utf-8").read()
    # <loadExclusive/> is only honoured OUTSIDE <systemList> (ES-DE 3.5.0, see spike/SPIKE.md)
    assert text.index("<loadExclusive/>") < text.index("<systemList>")
    root = ET.fromstring("<w>" + re.sub(r"^<\?xml[^>]*\?>", "", text) + "</w>")
    names = [s.findtext("name") for s in root.find("systemList")]
    assert names == ["must-play", "genres", "decades", "systems"]
    s0 = root.find("systemList")[0]
    assert s0.findtext("path") == "/data/nested/must-play" and s0.findtext("theme") == "must-play"
    assert s0.findtext("command") == "python3 /x/launch.py %ROM%"
    assert [s.findtext("systemsortname") for s in root.find("systemList")] == ["0-must-play", "1-genres", "2-decades", "3-systems"]


def test_gamelist_has_folders_and_real_metadata_without_state():
    out = tmp()
    gen(out)
    root = ET.parse(os.path.join(out, "gamelists", "genres", "gamelist.xml")).getroot()
    folders = {f.findtext("path"): f.findtext("name") for f in root.findall("folder")}
    assert folders["./fighters"] == "Fighters" and folders["./beat-em-up"] == "Beat 'em Up"
    g = [x for x in root.findall("game") if x.findtext("path") == "./fighters/ssf2t.zip"][0]
    assert g.findtext("name") == "Super Avenue Fighter II Turbo" and g.findtext("releasedate").startswith("1994")
    assert g.find("favorite") is None


def test_idempotent_and_deterministic():
    a, b = tmp(), tmp()
    gen(a)
    gen(b)
    assert snapshot(a) == snapshot(b)
    rc, stdout, _ = gen(a, apply=False)
    assert "NEW" not in stdout and "CHANGED" not in stdout and "SAME" in stdout
    assert "new 0 same" in stdout and "changed 0" in stdout


def test_favorites_and_playcount_survive_regeneration():
    out = tmp()
    gen(out)
    gl = os.path.join(out, "gamelists", "genres", "gamelist.xml")
    text = open(gl, encoding="utf-8").read()
    text = text.replace("<path>./fighters/mvsc.zip</path>",
                        "<path>./fighters/mvsc.zip</path>\n\t\t<favorite>true</favorite>\n\t\t<playcount>7</playcount>")
    open(gl, "w", encoding="utf-8").write(text)
    gen(out)
    again = open(gl, encoding="utf-8").read()
    assert "<favorite>true</favorite>" in again and "<playcount>7</playcount>" in again


def test_stale_symlinks_removed_real_files_never():
    out = tmp()
    gen(out)
    stale = os.path.join(out, "roms_nested", "genres", "fighters", "old.zip")
    os.symlink("/data/roms/cps2/old.zip", stale)
    keep = os.path.join(out, "roms_nested", "genres", "fighters", "notes.txt")
    open(keep, "w").close()
    rc, stdout, _ = gen(out, apply=False)
    assert "stale 1" in stdout
    gen(out)
    assert not os.path.lexists(stale) and os.path.exists(keep)


def test_name_clash_across_systems_gets_suffix():
    pairs = [("cps1", "sf2.zip"), ("fbneo", "sf2.zip")]
    names = [n for n, _s, _f in nested.link_names(pairs)]
    assert names == ["sf2.zip", "sf2 (fbneo).zip"]


def test_media_links_follow_folder_layout():
    out, media = tmp(), tmp()
    p = os.path.join(media, "cps2", "covers", "ssf2t.png")
    os.makedirs(os.path.dirname(p))
    open(p, "w").close()
    rc, stdout, _ = gen(out, "--media", media, "--media-target", "/home/cab/ES-DE/downloaded_media")
    assert rc == 0
    for rel in ("genres/covers/fighters/ssf2t.png", "decades/covers/1990-94/ssf2t.png", "must-play/covers/cps2/ssf2t.png",
                "systems/covers/cps2/ssf2t.png"):
        link = os.path.join(out, "downloaded_media", rel)
        assert os.readlink(link) == "/home/cab/ES-DE/downloaded_media/cps2/covers/ssf2t.png", rel


def test_folder_art_links_to_covers():
    out, art = tmp(), tmp()
    os.makedirs(os.path.join(art, "genres"))
    open(os.path.join(art, "genres", "fighters.png"), "w").close()
    gen(out, "--media", tmp(), "--folder-art", art)
    assert os.path.islink(os.path.join(out, "downloaded_media", "genres", "covers", "fighters.png"))


def test_malformed_gamelist_exits_3():          # BROKEN CASE
    gls = tmp()
    shutil.copytree(GL, os.path.join(gls, "g"))
    open(os.path.join(gls, "g", "cps2", "gamelist.xml"), "w").write("<gameList><game><path>x</gameList>")
    rc, _o, err = run("nested", "--gamelists", os.path.join(gls, "g"), "--picks", PICKS, "--out", tmp())
    assert rc == 3 and "malformed" in err


def test_missing_media_dir_exits_3():           # BROKEN CASE
    rc, _o, err = gen(tmp(), "--media", "/nonexistent/media", apply=False)
    assert rc == 3 and "--media" in err


def test_real_file_in_the_way_is_refused():     # BROKEN CASE
    out = tmp()
    d = os.path.join(out, "roms_nested", "must-play", "cps2")
    os.makedirs(d)
    open(os.path.join(d, "mvsc.zip"), "w").write("real data")
    try:
        gen(out)
        raise AssertionError("expected OSError")
    except OSError as e:
        assert "refusing" in str(e)
    assert open(os.path.join(d, "mvsc.zip")).read() == "real data"


def _must_play(*extra):
    systems = cg.load_gamelists(GL)
    cols, _ = cg.build_collections(systems, cg.load_picks(PICKS), 1)
    return systems, nested.build_model(cols, systems, *extra)["must-play"]


def test_must_play_nested_per_system_same_games_as_flat():
    _s, nest = _must_play()
    _s, flat = _must_play(True)
    assert list(nest) == sorted(nest) and "" not in nest
    assert sum(len(v) for v in nest.values()) == len(flat[""]) == 14           # flat count unchanged
    assert sorted(p for v in nest.values() for p in v) == sorted(flat[""])
    for folder, pairs in nest.items():                                          # games land in the right system
        assert pairs and all(system == folder for system, _fn in pairs)
    assert ("cps2", "mvsc.zip") in nest["cps2"] and "systems" not in nest


def test_must_play_folders_have_system_title_blurb_meta():
    out = tmp()
    ft = os.path.join(tmp(), "ft.tsv")
    open(ft, "w").write("systems\tcps2\tThe CPS2 blurb\nmust-play\tgenesis\tOwn must-play blurb\n")
    rc, _o, err = gen(out, "--folder-text", ft)
    assert rc == 0, err
    root = ET.parse(os.path.join(out, "gamelists", "must-play", "gamelist.xml")).getroot()
    f = {x.findtext("path"): x for x in root.findall("folder")}
    assert set(f) <= {"./cps2", "./fbneo", "./genesis", "./neo-geo"} and {"./cps2", "./genesis"} <= set(f)
    c = f["./cps2"]
    assert c.findtext("name") == nested.SYSTEM_TITLES["cps2"] and c.findtext("desc").startswith("The CPS2 blurb")
    assert c.findtext("publisher") == nested.SYSTEM_FACTS["cps2"][0] and c.findtext("genre") == "Arcade board"
    assert c.findtext("releasedate").startswith("1993")
    assert f["./genesis"].findtext("desc").startswith("Own must-play blurb")     # own key wins over systems/
    assert [g.findtext("path") for g in root.findall("game") if g.findtext("path") == "./cps2/mvsc.zip"]
    assert not [g for g in root.findall("game") if g.findtext("path").count("/") == 1]    # no loose games


def test_must_play_folder_art_falls_back_to_systems_photo():
    out, art = tmp(), tmp()
    os.makedirs(os.path.join(art, "systems"))
    open(os.path.join(art, "systems", "cps2.png"), "w").close()
    os.makedirs(os.path.join(art, "must-play"))
    open(os.path.join(art, "must-play", "genesis.png"), "w").close()            # an own picture wins
    gen(out, "--media", tmp(), "--folder-art", art)
    cov = os.path.join(out, "downloaded_media", "must-play", "covers")
    assert os.readlink(os.path.join(cov, "cps2.png")) == os.path.join(os.path.abspath(art), "systems", "cps2.png")
    assert os.readlink(os.path.join(cov, "genesis.png")) == os.path.join(os.path.abspath(art), "must-play", "genesis.png")
    assert not os.path.exists(os.path.join(cov, "neo-geo.png"))                 # no photo: nothing linked


def test_flat_must_play_flag_keeps_old_layout():
    out = tmp()
    gen(out, "--flat-must-play")
    assert os.path.islink(os.path.join(out, "roms_nested", "must-play", "mvsc.zip"))
    assert not os.path.isdir(os.path.join(out, "roms_nested", "must-play", "cps2"))


def test_must_play_unknown_system_folder_falls_back_not_crashes():           # BROKEN CASE
    cols = {"must-play": ["%ROMPATH%/weird/x.zip"]}
    m = nested.build_model(cols, {})
    assert list(m["must-play"]) == ["weird"]
    assert nested.folder_title("must-play", "weird") == "WEIRD"
    assert nested.folder_meta("must-play", "weird", m["must-play"]["weird"], {}) == {}
    assert nested.folder_desc("must-play", "weird", 1, {("systems", "cps2"): "x"}) == "1 game"


def test_must_play_malformed_collection_line_still_rejected():                # BROKEN CASE
    try:
        nested.build_model({"must-play": ["no-rompath-prefix.zip"]}, {})
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def _counts(stdout):
    return re.findall(r"^  (must-play|genres|decades|systems)\s.*?(\d+) link", stdout, re.M)


def test_virtual_system_dirs_in_gamelists_are_ignored():
    gl = os.path.join(tmp(), "gl")
    shutil.copytree(GL, gl)
    base_rc, base, _ = run("nested", "--gamelists", gl, "--picks", PICKS, "--out", os.path.join(tmp(), "a"), "--min-decade", "1")
    for v in nested.VIRTUAL_SLUGS:       # deployed virtual gamelists next to the real ones
        os.makedirs(os.path.join(gl, v))
        shutil.copy(os.path.join(GL, "genesis", "gamelist.xml"), os.path.join(gl, v, "gamelist.xml"))
    rc, out, err = run("nested", "--gamelists", gl, "--picks", PICKS, "--out", os.path.join(tmp(), "b"), "--min-decade", "1")
    assert base_rc == 0 and rc == 0, err
    assert _counts(out) == _counts(base) and len(_counts(base)) == 4
    assert set(cg.load_gamelists(gl)) == {"cps2", "fbneo", "genesis", "neogeo"}
    assert set(nested.raw_games(gl)) == {"cps2", "fbneo", "genesis", "neogeo"}


def test_virtual_dir_only_gamelists_is_an_input_error():     # BROKEN CASE
    gl = os.path.join(tmp(), "gl", "genres")
    os.makedirs(gl)
    shutil.copy(os.path.join(GL, "genesis", "gamelist.xml"), gl)
    rc, _o, err = run("nested", "--gamelists", os.path.dirname(gl), "--picks", PICKS, "--out", tmp())
    assert rc == 3 and "no <system>/gamelist.xml" in err


def _game(path, name, genre="Fighting", year="19950101T000000"):
    return ("\t<game>\n\t\t<path>./%s</path>\n\t\t<name>%s</name>\n\t\t<releasedate>%s</releasedate>\n"
            "\t\t<genre>%s</genre>\n\t</game>\n" % (path, name, year, genre))


def _collide_gl(spec):
    """{system: [(file, name)]} -> gamelists dir (tmp)."""
    root = os.path.join(tmp(), "gl")
    for system, games in spec.items():
        os.makedirs(os.path.join(root, system))
        with open(os.path.join(root, system, "gamelist.xml"), "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0"?>\n<gameList>\n' + "".join(_game(a, b) for a, b in games) + "</gameList>\n")
    return root


def _must_play_names(root, picks_rows):
    pk = os.path.join(tmp(), "picks.html")
    open(pk, "w").write("const P=[" + ",".join('["%s","%s","fig","1990s",1]' % r for r in picks_rows) + "];")
    out = tmp()
    rc, _o, err = run("nested", "--gamelists", root, "--picks", pk, "--out", out, "--min-decade", "1", "--flat-must-play", "--apply")
    assert rc == 0, err
    return out


def _names(out, virtual, path_prefix=""):
    r = ET.parse(os.path.join(out, "gamelists", virtual, "gamelist.xml")).getroot()
    return {g.findtext("path"): (g.findtext("name"), g.findtext("sortname")) for g in r.findall("game")}


def test_collision_gets_system_suffix_only_where_names_collide():
    root = _collide_gl({"cps1": [("bionic.zip", "Bionic Commando"), ("ghouls.zip", "Ghouls'n Ghosts")],
                        "nes": [("Bionic Commando (USA).zip", "Bionic Commando"), ("Mario.zip", "Super Mario")],
                        "snes": [("Out Run.zip", "Out Run")], "genesis": [("OutRun.zip", "OutRun")]})
    out = _must_play_names(root, [("Bionic Commando", "arc"), ("Bionic Commando", "nes"), ("Ghouls'n Ghosts", "arc"),
                                  ("Super Mario", "nes"), ("Out Run", "snes"), ("OutRun", "gen")])
    g = _names(out, "must-play")
    assert g["./bionic.zip"] == ("Bionic Commando (Arcade)", "Bionic Commando")
    assert g["./Bionic Commando (USA).zip"] == ("Bionic Commando (NES)", "Bionic Commando")
    assert g["./ghouls.zip"][0] == "Ghouls'n Ghosts" and g["./ghouls.zip"][1] is None      # not colliding: bare
    assert g["./Mario.zip"][0] == "Super Mario"
    assert g["./Out Run.zip"][0] == "Out Run (SNES)" and g["./OutRun.zip"][0] == "OutRun (Genesis)"  # punctuation ignored


def test_collision_arcade_systems_use_specific_labels_and_genre_folders_too():
    root = _collide_gl({"cps1": [("a.zip", "Last Round")], "cps2": [("b.zip", "Last Round")],
                        "fbneo": [("c.zip", "Last Round")], "snes": [("d.zip", "Last Round")]})
    out = _must_play_names(root, [("Last Round", "arc"), ("Last Round", "snes")])
    names = sorted(v[0] for v in _names(out, "must-play").values())
    assert names == ["Last Round (CPS1)", "Last Round (CPS2)", "Last Round (FBNeo)", "Last Round (SNES)"]
    assert all(v[1] == "Last Round" for v in _names(out, "must-play").values())
    gnames = sorted(v[0] for v in _names(out, "genres").values())
    assert gnames == names                      # same collision handling inside a genre folder


def test_collision_two_arcade_one_console_labels():
    root = _collide_gl({"cps1": [("a.zip", "Strider")], "fbneo": [("c.zip", "Strider")], "genesis": [("g.zip", "Strider")]})
    out = _must_play_names(root, [("Strider", "arc"), ("Strider", "gen")])
    assert sorted(v[0] for v in _names(out, "must-play").values()) == ["Strider (CPS1)", "Strider (FBNeo)", "Strider (Genesis)"]


def test_collision_never_touches_real_gamelists_or_systems_virtual():
    root = _collide_gl({"cps1": [("a.zip", "Strider")], "genesis": [("g.zip", "Strider")]})
    before = open(os.path.join(root, "cps1", "gamelist.xml")).read()
    out = _must_play_names(root, [("Strider", "arc"), ("Strider", "gen")])
    assert open(os.path.join(root, "cps1", "gamelist.xml")).read() == before
    assert all("(" not in n for n, _s in _names(out, "systems").values())        # per-system folders stay bare


def test_name_key_ignores_case_and_punctuation():
    assert nested.name_key("Out Run") == nested.name_key("OutRun") == nested.name_key("OUT-RUN")
    assert nested.name_key("Contra") != nested.name_key("Contra III")


def test_missing_args_exit_2():                 # BROKEN CASE
    assert run("nested")[0] == 2
    assert run("nested", "--gamelists", GL)[0] == 2


# ---- every real system the picks can map to has a nested title + collision label (Sega CD, 2026-11-14)
def system_table_gaps(sysmap, titles, labels):
    """Real system dirs named in collgen.SYSMAP that nested.py cannot title or label."""
    gaps = []
    for targets in sysmap.values():
        for s in targets:
            if s == "psp":            # PSP dropped from the cabinet (2026-11-14); no nested folder
                continue
            if s not in titles:
                gaps.append("%s: no SYSTEM_TITLES entry" % s)
            if s not in labels:
                gaps.append("%s: no SYSTEM_LABELS entry" % s)
    return gaps


def test_segacd_is_wired():
    import collgen
    assert collgen.SYSMAP["scd"] == ["segacd"]
    assert nested.SYSTEM_TITLES["segacd"] == "Sega CD" and nested.SYSTEM_LABELS["segacd"] == "Sega CD"
    assert system_table_gaps(collgen.SYSMAP, nested.SYSTEM_TITLES, nested.SYSTEM_LABELS) == []


def test_system_table_gap_is_caught():          # BROKEN CASE: a new system without titles fails
    import collgen
    bad = dict(collgen.SYSMAP, x68k=["x68000"])
    gaps = system_table_gaps(bad, nested.SYSTEM_TITLES, nested.SYSTEM_LABELS)
    assert "x68000: no SYSTEM_TITLES entry" in gaps and "x68000: no SYSTEM_LABELS entry" in gaps, gaps


# ---- folder blurbs + folder art (nested menus, 2026-11-14)
def _tsv(tmp_text):
    import tempfile
    fd, p = tempfile.mkstemp(suffix=".tsv")
    with os.fdopen(fd, "w") as f:
        f.write(tmp_text)
    return p


def test_folder_text_goes_into_desc_with_count():
    p = _tsv("# c\ngenres\tshmups\tShoot things.\n")
    texts = nested.load_folder_text(p)
    assert nested.folder_desc("genres", "shmups", 3, texts) == "Shoot things.\n\n3 games"
    assert nested.folder_desc("genres", "fighters", 1, texts) == "1 game"      # no blurb: count only
    xml = nested.gamelist_xml("genres", {"shmups": []}, {}, {}, texts)
    assert "<desc>Shoot things.\n\n0 games</desc>" in xml, xml


def test_folder_text_malformed_row_is_refused():           # BROKEN CASE
    for bad in ("genres\tshmups\n", "nope\tshmups\tx\n", "genres\tshmups\ta\ngenres\tshmups\tb\n"):
        try:
            nested.load_folder_text(_tsv(bad))
        except ValueError:
            continue
        raise AssertionError("accepted malformed folder text: %r" % bad)


def test_shipped_folder_text_covers_every_known_folder():
    here = os.path.dirname(os.path.abspath(__file__))
    texts = nested.load_folder_text(os.path.join(here, "..", "folder_text.tsv"))
    for g in nested.GENRE_SLUGS:
        assert ("genres", g) in texts, g
    for s in nested.SYSTEM_TITLES:
        if s in ("arcade", "naomi"):                       # not on the cabinet
            continue
        assert ("systems", s) in texts, s


def test_folder_art_prefers_icon_and_reports_missing():
    import tempfile
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    import folder_art
    theme = tempfile.mkdtemp()
    for rel in ("_inc/icons/shmups.png", "_inc/systems/shmups.png", "_inc/systems/1990-94.png"):
        os.makedirs(os.path.join(theme, os.path.dirname(rel)), exist_ok=True)
        open(os.path.join(theme, rel), "wb").write(b"x")
    files, missing = folder_art.plan(theme, [("genres", "shmups"), ("decades", "1990-94"), ("systems", "nope")])
    assert files[os.path.join("genres", "shmups.png")].endswith(os.path.join("icons", "shmups.png"))
    assert os.path.join("decades", "1990-94.png") in files
    assert missing == ["systems/nope"], missing                 # BROKEN CASE: missing art is reported


# ---- folder info panel (rating, players, released, developer, publisher, genre), 2026-11-14
def _mgame(**kw):
    g = ET.Element("game")
    for k, v in kw.items():
        ET.SubElement(g, k).text = v
    return g


def test_folder_meta_computed_from_games():
    games = {"fbneo": {"a.zip": _mgame(rating="0.8", players="1-2", releasedate="19910101T000000", developer="Acme"),
                       "b.zip": _mgame(rating="0.6", players="1-2", releasedate="19880101T000000", developer="Acme"),
                       "c.zip": _mgame(rating="0.9", players="1", releasedate="19950101T000000", developer="Taito")}}
    pairs = [("fbneo", "a.zip"), ("fbneo", "b.zip"), ("fbneo", "c.zip")]
    m = nested.folder_meta("genres", "shmups", pairs, games)
    assert m == {"rating": "0.75", "players": "1-2", "releasedate": "19880101T000000",
                 "developer": "Acme, Taito", "genre": "Shmups"}, m
    xml = nested.gamelist_xml("genres", {"shmups": pairs}, games, {})
    assert "<rating>0.75</rating>" in xml and "<genre>Shmups</genre>" in xml, xml


def test_folder_meta_systems_use_facts():
    m = nested.folder_meta("systems", "segacd", [], {})
    assert m == {"publisher": "Sega", "genre": "Console add-on", "releasedate": "19910101T000000"}, m


def test_folder_meta_ignores_bad_values():              # BROKEN CASE: junk ratings/years never leak in
    games = {"snes": {"x.zip": _mgame(rating="7.5", releasedate="abcd"), "y.zip": _mgame(rating="n/a")}}
    m = nested.folder_meta("decades", "1990-94", [("snes", "x.zip"), ("snes", "y.zip"), ("snes", "gone.zip")], games)
    assert m == {"genre": "Games from 1990-94"}, m


def test_seventies_bucket_becomes_a_decades_folder():
    cols = {"1970s": ["%ROMPATH%/fbneo/invaders.zip"], "1990-94": ["%ROMPATH%/snes/a.zip"], "junk": ["%ROMPATH%/x/y.zip"]}
    model = nested.build_model(cols, {})
    assert list(model["decades"]) == ["1970s", "1990-94"], model   # junk names never become folders


# ------------------------------------------- MAME BIOS/device names (ES-DE hides them on arcade platforms)

def test_hidden_name_list_has_the_ones_that_bit_us():
    names = nested.load_hidden_names()
    assert names["neogeo"] == "bios" and names["naomi"] == "bios" and names["pgm"] == "bios"
    assert len([k for k in names.values() if k == "device"]) > 1000          # the full ES-DE 3.5.0 list


def test_no_folder_slug_is_written_as_a_hidden_name():               # BROKEN CASE: the `neogeo` folder
    assert nested.folder_dir("neogeo") == "neo-geo" and nested.folder_dir("naomi") == "sega-naomi"
    assert nested.folder_dir("cps2") == "cps2" and nested.folder_dir("") == ""
    assert nested.folder_dir("pgm") == "pgm-games"                    # unknown hidden slug: generic rename
    out = tmp()
    rc, _o, err = gen(out)
    assert rc == 0, err
    for v in os.listdir(os.path.join(out, "roms_nested")):
        for dp, dns, fns in os.walk(os.path.join(out, "roms_nested", v)):
            for n in dns + [os.path.splitext(f)[0] for f in fns]:
                assert not nested.hidden_kind(n), (v, dp, n)
    assert os.path.isdir(os.path.join(out, "roms_nested", "systems", "neo-geo"))
    assert not os.path.exists(os.path.join(out, "roms_nested", "systems", "neogeo"))


def test_renamed_folder_keeps_title_blurb_facts_and_art():
    out, art = tmp(), tmp()
    os.makedirs(os.path.join(art, "systems"))
    open(os.path.join(art, "systems", "neogeo.png"), "w").close()         # art is still looked up by the slug
    ft = os.path.join(tmp(), "ft.tsv")
    open(ft, "w").write("systems\tneogeo\tThe Neo Geo blurb\n")
    rc, stdout, err = gen(out, "--media", tmp(), "--folder-art", art, "--folder-text", ft)
    assert rc == 0, err
    assert "folder systems/neogeo -> neo-geo/ on disk" in stdout
    root = ET.parse(os.path.join(out, "gamelists", "systems", "gamelist.xml")).getroot()
    f = {x.findtext("path"): x for x in root.findall("folder")}
    ng = f["./neo-geo"]
    assert ng.findtext("name") == "SNK Neo Geo" and ng.findtext("desc").startswith("The Neo Geo blurb")
    assert ng.findtext("publisher") == "SNK"
    assert "./neo-geo/mslug.zip" in [g.findtext("path") for g in root.findall("game")]
    cov = os.path.join(out, "downloaded_media", "systems", "covers", "neo-geo.png")
    assert os.readlink(cov) == os.path.join(os.path.abspath(art), "systems", "neogeo.png")


def test_favourite_survives_the_neogeo_folder_rename():
    out = tmp()
    gen(out)
    gl = os.path.join(out, "gamelists", "systems", "gamelist.xml")
    text = open(gl).read().replace("<path>./neo-geo/mslug.zip</path>", "<path>./neogeo/mslug.zip</path>"
                                   "<favorite>true</favorite>")
    open(gl, "w").write(text)                                            # as deployed before the fix
    gen(out)
    root = ET.parse(gl).getroot()
    g = [x for x in root.findall("game") if x.findtext("path") == "./neo-geo/mslug.zip"][0]
    assert g.findtext("favorite") == "true"


def test_bios_set_never_linked_and_device_named_game_renamed():
    gl = tmp()
    shutil.copytree(GL, gl, dirs_exist_ok=True)
    for system, entry in (("neogeo", "<game><path>./neogeo.zip</path><name>Neo Geo BIOS</name></game>"),
                          ("fbneo", "<game><path>./3c505.zip</path><name>Device Named Game</name></game>")):
        p = os.path.join(gl, system, "gamelist.xml")
        text = open(p).read().replace("</gameList>", entry + "\n</gameList>")
        open(p, "w").write(text)
    out = tmp()
    rc, stdout, err = run("nested", "--gamelists", gl, "--picks", PICKS, "--out", out, "--min-decade", "1", "--apply")
    assert rc == 0, err
    links = [f for _d, _n, fns in os.walk(os.path.join(out, "roms_nested")) for f in fns]
    assert "neogeo.zip" not in links and "3c505.zip" not in links
    assert "3c505 (fbneo).zip" in links
    assert os.readlink(os.path.join(out, "roms_nested", "systems", "fbneo", "3c505 (fbneo).zip")).endswith("/fbneo/3c505.zip")


def test_bios_set_not_linked_even_when_not_hidden_in_the_gamelist():   # `names` hides it; this is the 2nd guard
    systems = {"neogeo": [{"fn": "neogeo.zip"}, {"fn": "mslug.zip"}]}
    cols = {"must-play": ["%ROMPATH%/neogeo/neogeo.zip", "%ROMPATH%/neogeo/mslug.zip"]}
    m = nested.build_model(cols, systems)
    assert m["systems"]["neogeo"] == [("neogeo", "mslug.zip")]
    assert m["must-play"]["neogeo"] == [("neogeo", "mslug.zip")]


def test_bad_hidden_name_list_exits_3():                            # BROKEN CASE
    bad = os.path.join(tmp(), "names.tsv")
    open(bad, "w").write("# only a comment\n")
    try:
        nested.load_hidden_names(bad)
        assert False, "empty list accepted"
    except ValueError:
        pass
    open(bad, "w").write("bios neogeo\n")
    try:
        nested.load_hidden_names(bad)
        assert False, "malformed row accepted"
    except ValueError:
        pass


# ----------------------------------------------------- crossover games in two folders (#116)

def _crossover_tree(base, scheme="new"):
    """Real ROM files + a SERIES list with one game (mvsc) in two folders; scheme 'old' = the pre-#116 layout
    (a direct symlink per folder), 'new' = nested.symlink_plan/pointer_plan. -> (roms dir, system dir, model)."""
    roms, nest = os.path.join(base, "roms"), os.path.join(base, "nested")
    os.makedirs(os.path.join(roms, "cps2"))
    for fn in ("mvsc.zip", "xmvsf.zip", "ssf2t.zip"):
        open(os.path.join(roms, "cps2", fn), "w").close()
    model = {"series": {"acme": [("cps2", "mvsc.zip"), ("cps2", "ssf2t.zip")],
                        "marvel": [("cps2", "mvsc.zip"), ("cps2", "xmvsf.zip")]}}
    if scheme == "old":
        for v, folders in model.items():
            for folder, pairs in folders.items():
                for name, system, fn in nested.link_names(pairs):
                    p = os.path.join(nest, v, folder, name)
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    os.symlink(os.path.join(roms, system, fn), p)
    else:
        plan = nested.symlink_plan(model, roms, nest)
        nested.apply_links(plan, [], nest)
        pointers = nested.pointer_plan(model, roms, nest)
        nested.apply_pointers(pointers, [], nest)
    return roms, os.path.join(nest, "series"), model


def _es_de_keys(sdir):
    """What ES-DE's updateGamelist compares: getCanonicalPath() of every game (symlinks resolved) -> its rel paths."""
    keys = {}
    for dp, _dn, fns in os.walk(sdir):
        for fn in fns:
            p = os.path.join(dp, fn)
            keys.setdefault(os.path.realpath(p), []).append(os.path.relpath(p, sdir))
    return keys


def test_crossover_old_scheme_merges_in_esde():                        # BROKEN CASE: the pre-#116 layout
    _roms, sdir, _m = _crossover_tree(tmp(), "old")
    merged = {k: v for k, v in _es_de_keys(sdir).items() if len(v) > 1}
    assert [sorted(v) for v in merged.values()] == [["acme/mvsc.zip", "marvel/mvsc.zip"]], merged


def test_crossover_new_scheme_keeps_every_folder_entry_distinct():
    roms, sdir, _m = _crossover_tree(tmp())
    keys = _es_de_keys(sdir)
    assert all(len(v) == 1 for v in keys.values()), keys                # no two entries share a canonical path
    assert sum(len(v) for v in keys.values()) == 4                      # all four links still there
    first = os.path.join(sdir, "acme", "mvsc.zip")                    # first folder: the plain symlink to the ROM
    assert os.readlink(first) == roms + "/cps2/mvsc.zip"
    second = os.path.join(sdir, "marvel", "mvsc.zip")                   # later folder: relative link to a pointer file
    target = os.readlink(second)
    assert not os.path.isabs(target) and target.endswith(".crossover/series/marvel/mvsc.zip"), target
    text = open(os.path.realpath(second), encoding="utf-8").read()
    assert text == nested.POINTER_MAGIC + roms + "/cps2/mvsc.zip\n"
    # games in one folder only stay plain links
    assert os.readlink(os.path.join(sdir, "marvel", "xmvsf.zip")) == roms + "/cps2/xmvsf.zip"


def test_crossover_pointer_dir_sits_outside_every_system_path():
    base = tmp()
    _roms, sdir, _m = _crossover_tree(base)
    nest = os.path.dirname(sdir)
    assert os.path.isdir(os.path.join(nest, nested.POINTER_DIR))
    assert not os.path.exists(os.path.join(sdir, nested.POINTER_DIR))   # ES-DE only scans <nested_root>/<system>/


def test_crossover_only_within_one_virtual_system():
    # the same game in genres AND series are two gamelists: both stay plain symlinks
    model = {"genres": {"fighters": [("cps2", "mvsc.zip")]}, "series": {"acme": [("cps2", "mvsc.zip")]}}
    plan = nested.symlink_plan(model, "/data/roms", "/o")
    assert set(plan.values()) == {"/data/roms/cps2/mvsc.zip"}
    assert nested.pointer_plan(model, "/data/roms", "/o") == {}


def test_crossover_pointers_idempotent_stale_removed_foreign_files_kept():
    base = tmp()
    roms, sdir, model = _crossover_tree(base)
    nest = os.path.dirname(sdir)
    ptrs = nested.pointer_plan(model, roms, nest)
    counts, stale = nested.plan_pointers(ptrs, nest)
    assert counts == {"new": 0, "same": 1, "changed": 0} and stale == []
    old = os.path.join(nest, nested.POINTER_DIR, "series", "gone", "old.zip")
    os.makedirs(os.path.dirname(old))
    open(old, "w").write(nested.POINTER_MAGIC + "/data/roms/cps2/old.zip\n")
    note = os.path.join(nest, nested.POINTER_DIR, "series", "marvel", "notes.txt")
    open(note, "w").write("mine")
    counts, stale = nested.plan_pointers(ptrs, nest)
    assert stale == [old]                                               # only our own pointer file goes
    nested.apply_pointers(ptrs, stale, nest)
    assert not os.path.exists(old) and not os.path.isdir(os.path.dirname(old)) and os.path.isfile(note)


def test_crossover_pointer_refuses_to_overwrite_a_link():              # BROKEN CASE
    base = tmp()
    roms, sdir, model = _crossover_tree(base)
    nest = os.path.dirname(sdir)
    ptr = next(iter(nested.pointer_plan(model, roms, nest)))
    os.remove(ptr)
    os.symlink(roms + "/cps2/mvsc.zip", ptr)
    try:
        nested.apply_pointers(nested.pointer_plan(model, roms, nest), [], nest)
        assert False, "overwrote a symlink with a pointer file"
    except OSError:
        pass
    assert os.path.islink(ptr)                                          # the ROM behind it was not touched


def test_collgen_nested_reports_pointers_and_gamelist_keeps_both_entries():
    base = tmp()
    out = os.path.join(base, "out")
    tsv = os.path.join(base, "series.tsv")
    open(tsv, "w").write("aaa\tAAA\t.\t-\t-\t1\nbbb\tBBB\t.\t-\t-\t1\n")     # every game matches both rows
    rc, stdout, err = gen(out, "--series", tsv, "--rom-root", "/data/roms")
    assert rc == 0, err
    games = ET.parse(os.path.join(out, "gamelists", "series", "gamelist.xml")).getroot().findall("game")
    paths = [g.findtext("path") for g in games]
    assert len(paths) == len(set(paths)) and any(p.startswith("./aaa/") for p in paths) and any(p.startswith("./bbb/") for p in paths)
    assert re.search(r"crossover pointers: new [1-9]", stdout)
    sdir = os.path.join(out, "roms_nested", "series")
    assert all(len(v) == 1 for v in _es_de_keys(sdir).values())
    rc, stdout, _e = gen(out, "--series", tsv, "--rom-root", "/data/roms", apply=False)
    assert "crossover pointers: new 0" in stdout and "changed 0  stale 0" in stdout


def test_must_play_genres_only_when_configured():
    """#182: mpg-<bucket> collections become the MUST PLAY GENRES folders only when that slug is in hk_virtual_systems;
    their folders reuse the GENRES titles, info and art."""
    cols = {"mpg-fighters": ["%ROMPATH%/cps2/ssf2t.zip"], "mpg-bogus": ["%ROMPATH%/cps2/19xx.zip"], "fighters": ["%ROMPATH%/cps2/ssf2t.zip"]}
    systems = {"cps2": [dict(fn="ssf2t.zip")]}
    saved = nested.VIRTUAL
    try:
        nested.VIRTUAL = [("must-play", "MUST PLAY"), (nested.MUST_PLAY_GENRES, "MUST PLAY GENRES"), ("genres", "GENRES"), ("systems", "SYSTEMS")]
        m = nested.build_model(cols, systems)
        assert m[nested.MUST_PLAY_GENRES] == {"fighters": [("cps2", "ssf2t.zip")]}, "BROKEN: an unknown bucket is dropped"
        nested.VIRTUAL = [("must-play", "MUST PLAY"), ("genres", "GENRES"), ("systems", "SYSTEMS")]
        assert nested.MUST_PLAY_GENRES not in nested.build_model(cols, systems), "not configured: no MUST PLAY GENRES"
    finally:
        nested.VIRTUAL = saved
    assert nested.folder_title(nested.MUST_PLAY_GENRES, "beat-em-up") == "Beat 'em Up"
    assert nested.system_alias(nested.MUST_PLAY_GENRES) == "genres"
    assert nested.folder_meta(nested.MUST_PLAY_GENRES, "fighters", [], {}).get("genre") == "Fighters"


def test_data_path_prefers_the_personal_list_over_the_example():          # #190
    root = tempfile.mkdtemp()
    tools = os.path.join(root, "skills", "collections")
    mine = os.path.join(root, "personal", "skills", "collections")
    os.makedirs(tools)
    old = nested.HERE
    try:
        nested.HERE = tools
        assert nested.data_path("series.tsv") == os.path.join(tools, "series.tsv")       # no personal/: the example
        os.makedirs(mine)
        open(os.path.join(mine, "series.tsv"), "w").write("x\tX\tx\n")
        assert nested.data_path("series.tsv") == os.path.join(mine, "series.tsv")         # personal/ wins
        assert nested.data_path("folder_text.tsv") == os.path.join(tools, "folder_text.tsv")   # per file
    finally:
        nested.HERE = old


def test_system_names_override_titles_and_facts():                       # #190
    p = os.path.join(tempfile.mkdtemp(), "system_names.tsv")
    open(p, "w").write("# c\ncps2\tBoard Two\tMaker Co\t1993\tArcade board\nfbneo\tFB\t\t\tArcade\n")
    rows = nested.load_system_names(p)
    assert rows == {"cps2": ("Board Two", "Maker Co", "1993", "Arcade board"), "fbneo": ("FB", "", "", "Arcade")}
    assert nested.load_system_names(p + ".missing") == {}
    titles, facts = dict(nested.SYSTEM_TITLES), dict(nested.SYSTEM_FACTS)
    try:
        nested.apply_system_names(rows)
        assert nested.SYSTEM_TITLES["cps2"] == "Board Two" and nested.SYSTEM_FACTS["cps2"] == ("Maker Co", "1993", "Arcade board")
    finally:
        nested.SYSTEM_TITLES.clear(); nested.SYSTEM_TITLES.update(titles)
        nested.SYSTEM_FACTS.clear(); nested.SYSTEM_FACTS.update(facts)
    # the shipped example names no system: the neutral built-in names stay
    assert nested.load_system_names(os.path.join(os.path.dirname(nested.__file__), "system_names.tsv")) == {}


def test_bad_system_names_row_is_an_error():                             # BROKEN CASE
    for bad in ("cps2\tT\tM\t1993\n", "cps2\t\tM\t1993\tK\n", "cps2\tT\tM\tsoon\tK\n"):
        p = os.path.join(tempfile.mkdtemp(), "system_names.tsv")
        open(p, "w").write(bad)
        try:
            nested.load_system_names(p)
        except ValueError as e:
            assert "system_names.tsv:1" in str(e), str(e)
            continue
        raise AssertionError("expected ValueError for %r" % bad)


if __name__ == "__main__":
    main(globals(), per_test=True)
