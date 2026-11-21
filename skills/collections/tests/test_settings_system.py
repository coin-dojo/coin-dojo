#!/usr/bin/env python3
"""The SETTINGS carousel entry in the nested build (#34; `collgen.py nested --settings-entry FILE`). Stdlib only.

    python3 skills/collections/tests/test_settings_system.py

- without --settings-entry: no settings system, no link, no gamelist (a build without the app is unchanged)
- with it: SETTINGS is the LAST <system> (systemsortname after SYSTEMS), platform `ignore`, extension .hks,
  command = the app; one link roms_nested/settings/Settings.hks -> FILE; gamelists/settings/gamelist.xml has one
  game; validate_nested.py passes on the tree; a rebuild keeps ES-DE's lastplayed/playcount
- a deployed gamelists/settings/ in the INPUT is skipped (never a real system, never a SYSTEMS folder)
BROKEN cases: the entry file missing on the host = dead link -> validate_nested FAIL; a settings system that is not
last is caught by the order check.
"""
import importlib.util
import io
import os
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
import validate_nested as vn  # noqa: E402


def call(fn, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = fn(list(argv))
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


def build(base, gamelists=GL, entry=None, trophies=None):
    out, roms = os.path.join(base, "out"), os.path.join(base, "roms")
    if not os.path.isdir(roms):
        for system in os.listdir(GL):
            os.makedirs(os.path.join(roms, system))
            for line in open(os.path.join(GL, system, "gamelist.xml"), encoding="utf-8"):
                if "<path>./" in line:
                    open(os.path.join(roms, system, line.split("<path>./", 1)[1].split("</path>", 1)[0]), "w").close()
    argv = ["nested", "--gamelists", gamelists, "--picks", PICKS, "--out", out, "--min-decade", "1",
            "--rom-root", "/data/roms", "--apply"]
    if entry:
        argv += ["--settings-entry", entry]
    if trophies:
        argv += ["--trophies-entry", trophies]
    rc, o, err = call(cg.main, argv)
    assert rc == 0, err
    return out, roms, o


def systems(out):
    text = open(os.path.join(out, "custom_systems", "es_systems.xml"), encoding="utf-8").read()
    root = ET.fromstring("<systemList>" + text.split("<systemList>", 1)[1])     # <loadExclusive/> is a 2nd root
    return [(s.findtext("name"), s) for s in root.iter("system")]


def test_without_flag_no_settings():
    base = tempfile.mkdtemp()
    try:
        out, _r, o = build(base)
        assert nested.SETTINGS_SLUG not in [n for n, _s in systems(out)]
        assert not os.path.exists(os.path.join(out, "roms_nested", "settings"))
        assert not os.path.exists(os.path.join(out, "gamelists", "settings"))
        assert "settings   none" in o
    finally:
        shutil.rmtree(base)


def test_with_flag_settings_last_and_valid():
    base = tempfile.mkdtemp()
    try:
        entry = os.path.join(base, "settings.hks")
        open(entry, "w").write("{}\n")
        out, roms, _o = build(base, entry=entry)
        sy = systems(out)
        names = [n for n, _s in sy]
        assert names[-1] == "settings", names
        s = sy[-1][1]
        assert s.findtext("platform") == "ignore" and s.findtext("extension") == ".hks"
        assert s.findtext("command") == nested.SETTINGS_COMMAND + " %ROM%"
        assert s.findtext("theme") == "settings" and s.findtext("fullname") == "SETTINGS"
        sorts = [x.findtext("systemsortname") for _n, x in sy]
        assert sorts == sorted(sorts) and sorts[-1].endswith("-settings"), sorts
        link = os.path.join(out, "roms_nested", "settings", nested.SETTINGS_LINK)
        assert os.path.islink(link) and os.readlink(link) == entry
        games = ET.parse(os.path.join(out, "gamelists", "settings", "gamelist.xml")).getroot().findall("game")
        assert len(games) == 1 and games[0].findtext("path") == "./" + nested.SETTINGS_LINK
        assert games[0].findtext("nogamecount") == "true"          # no "1 GAME", never in LAST PLAYED
        rc, so, err = call(vn.main, ["--out", out, "--map", "/data/roms=" + roms])
        assert rc == 0 and "RESULT: PASS" in so and "settings" in so, (so, err)
        # ES-DE writes lastplayed/playcount; the next build keeps them
        gl = os.path.join(out, "gamelists", "settings", "gamelist.xml")
        txt = open(gl).read().replace("</game>", "\t<playcount>3</playcount>\n\t\t<lastplayed>20261115T120000</lastplayed>\n\t</game>")
        open(gl, "w").write(txt)
        build(base, entry=entry)
        txt2 = open(gl).read()
        assert "<playcount>3</playcount>" in txt2 and "<lastplayed>20261115T120000</lastplayed>" in txt2
    finally:
        shutil.rmtree(base)


def test_BROKEN_missing_entry_is_a_dead_link():
    base = tempfile.mkdtemp()
    try:
        out, roms, _o = build(base, entry=os.path.join(base, "nope.hks"))
        rc, so, _e = call(vn.main, ["--out", out, "--map", "/data/roms=" + roms])
        assert rc == 1 and "dead link: roms_nested/settings/" in so, so
    finally:
        shutil.rmtree(base)


def test_deployed_settings_gamelist_is_not_a_real_system():
    base = tempfile.mkdtemp()
    try:
        gl = os.path.join(base, "gl")
        shutil.copytree(GL, gl)
        os.makedirs(os.path.join(gl, "settings"))
        open(os.path.join(gl, "settings", "gamelist.xml"), "w").write(nested.settings_gamelist_xml({}))
        out, _r, _o = build(base, gamelists=gl)
        folders = [f for f in os.listdir(os.path.join(out, "roms_nested", "systems"))]
        assert "settings" not in folders, folders
        assert "settings" in cg.SKIP_SYSTEMS and tuple(cg.TOOL_SYSTEMS) == nested.TOOL_SLUGS
    finally:
        shutil.rmtree(base)


def test_BROKEN_order_check_catches_settings_not_last():
    xml = nested.systems_xml({"systems": {}}, "/data/nested", "python3 l.py", "python3 s.py")
    bad = xml.replace("<systemsortname>1-settings</systemsortname>", "<systemsortname>0-settings</systemsortname>")
    sorts = [s.findtext("systemsortname") for s in ET.fromstring(bad.split("\n", 2)[2]).iter("system")]
    assert not (sorts == sorted(sorts) and sorts[-1].endswith("-settings")), sorts


def test_trophies_before_settings_and_valid():
    """#176: TROPHIES is the same kind of tool entry, just before SETTINGS; without its flag it is absent."""
    base = tempfile.mkdtemp()
    try:
        out, _r, o = build(base)
        assert nested.TROPHIES_SLUG not in [n for n, _s in systems(out)] and "trophies   none" in o
        entry, trophies = os.path.join(base, "settings.hks"), os.path.join(base, "trophies.hks")
        for f in (entry, trophies):
            open(f, "w").write("{}\n")
        out, roms, _o = build(base, entry=entry, trophies=trophies)
        sy = systems(out)
        names = [n for n, _s in sy]
        assert names[-2:] == ["trophies", "settings"], names
        t = sy[-2][1]
        assert t.findtext("platform") == "ignore" and t.findtext("extension") == ".hks"
        assert t.findtext("command") == nested.TROPHIES_COMMAND + " %ROM%" and t.findtext("fullname") == "TROPHIES"
        sorts = [x.findtext("systemsortname") for _n, x in sy]
        assert sorts == sorted(sorts), sorts
        link = os.path.join(out, "roms_nested", "trophies", nested.TROPHIES_LINK)
        assert os.path.islink(link) and os.readlink(link) == trophies
        games = ET.parse(os.path.join(out, "gamelists", "trophies", "gamelist.xml")).getroot().findall("game")
        assert len(games) == 1 and games[0].findtext("nogamecount") == "true"
        rc, so, err = call(vn.main, ["--out", out, "--map", "/data/roms=" + roms])
        assert rc == 0 and "RESULT: PASS" in so, (so, err)
        os.remove(trophies)                                      # BROKEN: the entry file missing = dead link
        rc, so, err = call(vn.main, ["--out", out, "--map", "/data/roms=" + roms])
        assert rc != 0, "BROKEN case: a dead TROPHIES link must fail the validator"
    finally:
        shutil.rmtree(base)


if __name__ == "__main__":
    main(globals(), per_test=True)
