#!/usr/bin/env python3
"""Tests for validate_nested.py, the pre-write gate for a `collgen.py nested` tree (stdlib only; pytest or direct).

    python3 skills/collections/tests/test_validate_nested.py

Broken cases (must print RESULT: FAIL): a `neogeo` folder, a BIOS-named file, a dead link, a count mismatch
(link without a <game>, <game> without a link), missing --out / es_systems.xml (exit 3), bad --map (exit 2).
"""
import importlib.util
import io
import os
import sys
import tempfile
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
import validate_nested as vn  # noqa: E402


def call(fn, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = fn(list(argv))
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


def build():
    """A real nested tree from the fixtures; ROM targets in a local placeholder tree so no link is dead."""
    base = tempfile.mkdtemp()
    out, roms = os.path.join(base, "out"), os.path.join(base, "roms")
    for system in os.listdir(GL):
        os.makedirs(os.path.join(roms, system))
        for line in open(os.path.join(GL, system, "gamelist.xml"), encoding="utf-8"):
            if "<path>./" in line:
                fn = line.split("<path>./", 1)[1].split("</path>", 1)[0]
                open(os.path.join(roms, system, fn), "w").close()
    rc, _o, err = call(cg.main, ["nested", "--gamelists", GL, "--picks", PICKS, "--out", out, "--min-decade", "1",
                                 "--rom-root", "/data/roms", "--apply"])
    assert rc == 0, err
    return out, roms


def validate(out, roms, *extra):
    return call(vn.main, ["--out", out, "--map", "/data/roms=" + roms, *extra])


def test_generated_tree_passes():
    out, roms = build()
    rc, stdout, err = validate(out, roms)
    assert rc == 0 and stdout.rstrip().endswith("RESULT: PASS"), (stdout, err)
    assert "systems" in stdout and "must-play" in stdout


def test_neogeo_folder_fails():                                         # BROKEN CASE: the 2026-11-14 bug
    out, roms = build()
    nest = os.path.join(out, "roms_nested", "systems")
    os.rename(os.path.join(nest, "neo-geo"), os.path.join(nest, "neogeo"))
    gl = os.path.join(out, "gamelists", "systems", "gamelist.xml")
    text = open(gl).read().replace("./neo-geo", "./neogeo")
    open(gl, "w").write(text)                                           # counts still match: only the name is bad
    rc, stdout, _e = validate(out, roms)
    assert rc == 1 and "RESULT: FAIL" in stdout
    assert "folder systems/neogeo is a MAME bios name" in stdout, stdout
    assert "count" not in stdout


def test_bios_named_file_fails():                                       # BROKEN CASE
    out, roms = build()
    open(os.path.join(roms, "neogeo", "neogeo.zip"), "w").close()
    os.symlink("/data/roms/neogeo/neogeo.zip", os.path.join(out, "roms_nested", "systems", "neo-geo", "neogeo.zip"))
    rc, stdout, _e = validate(out, roms)
    assert rc == 1 and "file systems/neo-geo/neogeo.zip is a MAME bios name" in stdout, stdout


def test_hidden_names_ignored_on_a_non_arcade_platform():
    out, roms = build()
    es = os.path.join(out, "custom_systems", "es_systems.xml")
    text = open(es).read().replace("<platform>arcade</platform>", "<platform>pc</platform>")
    open(es, "w").write(text)
    nest = os.path.join(out, "roms_nested", "systems")
    os.rename(os.path.join(nest, "neo-geo"), os.path.join(nest, "neogeo"))
    gl = os.path.join(out, "gamelists", "systems", "gamelist.xml")
    text = open(gl).read().replace("./neo-geo", "./neogeo")
    open(gl, "w").write(text)
    rc, stdout, _e = validate(out, roms)
    assert rc == 0, stdout


def test_dead_link_fails():                                             # BROKEN CASE
    out, roms = build()
    os.remove(os.path.join(roms, "cps2", "ssf2t.zip"))
    rc, stdout, _e = validate(out, roms)
    assert rc == 1 and "dead link: roms_nested/" in stdout and "ssf2t.zip" in stdout, stdout


def test_dead_link_without_map_fails():                                 # BROKEN CASE: /data/roms is not here
    out, _roms = build()
    rc, stdout, _e = call(vn.main, ["--out", out])
    assert rc == 1 and "dead link" in stdout


def test_link_without_game_entry_fails():                               # BROKEN CASE: count mismatch
    out, roms = build()
    os.symlink("/data/roms/cps2/ssf2t.zip", os.path.join(out, "roms_nested", "genres", "fighters", "extra.zip"))
    rc, stdout, _e = validate(out, roms)
    assert rc == 1 and "genres: count:" in stdout and "genres/fighters/extra.zip" not in stdout, stdout
    assert "fighters/extra.zip" in stdout


def test_game_entry_without_link_fails():                               # BROKEN CASE: count mismatch
    out, roms = build()
    os.remove(os.path.join(out, "roms_nested", "decades", "1990-94", "ssf2t.zip"))
    rc, stdout, _e = validate(out, roms)
    assert rc == 1 and "decades: count:" in stdout and "1 entr(y/ies) without a link" in stdout, stdout


def test_hidden_game_keeps_its_link_and_counts():
    out, roms = build()
    gl = os.path.join(out, "gamelists", "genres", "gamelist.xml")
    text = open(gl).read().replace("<game>", "<game><hidden>true</hidden>", 1)
    open(gl, "w").write(text)
    rc, stdout, _e = validate(out, roms)
    assert rc == 0, stdout
    assert "  1 hidden" in stdout


def test_missing_out_and_es_systems_exit_3():                           # BROKEN CASE
    rc, stdout, _e = call(vn.main, ["--out", os.path.join(tempfile.mkdtemp(), "nope")])
    assert rc == 3 and "RESULT: FAIL" in stdout
    rc, stdout, _e = call(vn.main, ["--out", tempfile.mkdtemp()])
    assert rc == 3 and "RESULT: FAIL" in stdout


def test_bad_usage_exits_2():                                           # BROKEN CASE
    assert call(vn.main, [])[0] == 2
    assert call(vn.main, ["--out", tempfile.mkdtemp(), "--map", "nonsense"])[0] == 2


def test_empty_name_list_exits_3():                                     # BROKEN CASE: never pass on no names
    out, roms = build()
    bad = os.path.join(tempfile.mkdtemp(), "n.tsv")
    open(bad, "w").write("# nothing\n")
    rc, stdout, _e = validate(out, roms, "--names", bad)
    assert rc == 3 and "RESULT: FAIL" in stdout


def _crossover_build():
    """A nested tree where every game sits in two SERIES folders (series.tsv with two match-all rows)."""
    base = tempfile.mkdtemp()
    out, roms = os.path.join(base, "out"), os.path.join(base, "roms")
    for system in os.listdir(GL):
        os.makedirs(os.path.join(roms, system))
        for line in open(os.path.join(GL, system, "gamelist.xml"), encoding="utf-8"):
            if "<path>./" in line:
                fn = line.split("<path>./", 1)[1].split("</path>", 1)[0]
                open(os.path.join(roms, system, fn), "w").close()
    tsv = os.path.join(base, "series.tsv")
    open(tsv, "w").write("aaa\tAAA\t.\t-\t-\t1\nbbb\tBBB\t.\t-\t-\t1\n")
    rc, _o, err = call(cg.main, ["nested", "--gamelists", GL, "--picks", PICKS, "--out", out, "--min-decade", "1",
                                 "--series", tsv, "--rom-root", "/data/roms", "--apply"])
    assert rc == 0, err
    return out, roms


def test_crossover_tree_passes_and_counts_match():
    out, roms = _crossover_build()
    rc, stdout, _e = validate(out, roms)
    assert rc == 0 and "RESULT: PASS" in stdout, stdout
    assert os.path.isdir(os.path.join(out, "roms_nested", ".crossover", "series", "bbb"))


def test_two_links_to_one_rom_are_rejected():                           # BROKEN CASE: the pre-#116 layout
    out, roms = _crossover_build()
    link = next(p for rel, p in vn.walk_links(os.path.join(out, "roms_nested", "series", "bbb")))
    os.remove(link)
    # point it straight at the ROM the pointer named, as the old scheme did
    ptr = os.path.join(out, "roms_nested", ".crossover", "series", "bbb", os.path.basename(link))
    rom = open(ptr, encoding="utf-8").read().strip()[len("COIN-DOJO-LINK "):]
    os.symlink(rom, link)
    rc, stdout, _e = validate(out, roms)
    assert rc == 1 and "RESULT: FAIL" in stdout and "merge:" in stdout and "#116" in stdout, stdout


def test_dead_pointer_is_rejected():                                    # BROKEN CASE
    out, roms = _crossover_build()
    ptr = next(os.path.join(dp, n) for dp, _d, fns in os.walk(os.path.join(out, "roms_nested", ".crossover")) for n in fns)
    open(ptr, "w").write("COIN-DOJO-LINK /data/roms/cps2/does-not-exist.zip\n")
    rc, stdout, _e = validate(out, roms)
    assert rc == 1 and "dead pointer" in stdout, stdout
    open(ptr, "w").write("not a pointer\n")
    rc, stdout, _e = validate(out, roms)
    assert rc == 1 and "dead pointer" in stdout, stdout


if __name__ == "__main__":
    main(globals(), per_test=True)
