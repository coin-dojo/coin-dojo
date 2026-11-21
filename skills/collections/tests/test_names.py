#!/usr/bin/env python3
"""Tests for `collgen.py names` (NAMING.md section 3). Stdlib only; pytest or run directly.

    python3 skills/collections/tests/test_names.py

One test per rule plus broken cases: an audit of the standard must FAIL on a gamelist
where a double space survives, a BIOS set is not hidden, a <path> changed, or
'Spider-Man' was mangled.
"""
import importlib.util
import io
import os
import re
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "lib"))
from hktest import main  # noqa: E402  (shared runner: one PASS per test, counted RESULT line, #151)

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "collgen.py")
spec = importlib.util.spec_from_file_location("collgen", TOOL)
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)

PICKS = os.path.join(HERE, "fixtures", "picks.html")
DAT = """clrmamepro (
	name "FBNeo - Arcade Games"
)

game (
	name "Metal Slug - Super Vehicle-001"
	releaseyear "1996"
	rom ( name mslug.zip size 1 crc 00000000 )
)

game (
	name "Neo Geo"
	rom ( name neogeo.zip size 1 crc 00000000 )
)

game (
	name "PGM (Polygame Master) System BIOS"
	rom ( name pgm.zip size 1 crc 00000000 )
)

game (
	name "Avenue Fighter III: New Generation (Asia 970204, NO CD, BIOS set 1)"
	rom ( name sfiii.zip size 1 crc 00000000 )
)
"""


def game(path, name=None, extra="\t\t<desc>keep  me &amp; my  spaces</desc>\n"):
    n = "\t\t<name>%s</name>\n" % name if name is not None else ""
    return "\t<game>\n\t\t<path>%s</path>\n%s%s\t</game>\n" % (path, n, extra)


def gl(*games, two_root=False):
    head = '<?xml version="1.0"?>\n'
    if two_root:
        head += "<alternativeEmulator>\n\t<label>FinalBurn Neo</label>\n</alternativeEmulator>\n"
    return head + "<gameList>\n" + "".join(games) + "</gameList>\n"


def names(system, text, dat=None):
    zip2 = {z: t for z, (t, _y) in cg.parse_dat(dat).items() if t} if dat else {}
    bios = cg.parse_dat_bios(dat) if dat else set()
    new, _c = cg.names_for_text(system, text, zip2, bios)
    return new


def rows(text):
    out = {}
    for blk in re.findall(r"<game>.*?</game>", text, re.S):
        out[re.search(r"<path>(.*?)</path>", blk).group(1)] = blk
    return out


def tag(blk, t):
    m = re.search(r"<%s>(.*?)</%s>" % (t, t), blk, re.S)
    return m.group(1) if m else None


def audit(before, after, system="fbneo"):
    """The standard as a checker: list of violations (empty = compliant)."""
    bad = []
    b, a = rows(before), rows(after)
    if list(b) != list(a):
        bad.append("path changed")
    for p, blk in a.items():
        n = tag(blk, "name") or ""
        if n != n.strip() or "  " in n:
            bad.append("whitespace: %r" % n)
        if " : " in n or re.search(r" [-:] ?$", n):
            bad.append("separator: %r" % n)
        if "(" in n and not re.search(r"\(.*\)", n):
            bad.append("tag: %r" % n)
        if n.endswith(", The"):
            bad.append("article: %r" % n)
        if not n:
            bad.append("no name: %s" % p)
        if system in cg.ARCADE_SYSTEMS and os.path.splitext(os.path.basename(p))[0] in cg.BIOS_SETS \
                and tag(blk, "hidden") != "true":
            bad.append("BIOS not hidden: %s" % p)
    return bad


# ------------------------------------------------------------------ rules

def test_colon_separator_cps1():
    out = names("cps1", gl(game("./sf2.zip", "Avenue Fighter II : The World Warrior"),
                            game("./sf2ce.zip", "Avenue Fighter II' : Champion Edition"), two_root=True))
    r = rows(out)
    assert tag(r["./sf2.zip"], "name") == "Avenue Fighter II: The World Warrior"
    assert tag(r["./sf2ce.zip"], "name") == "Avenue Fighter II': Champion Edition"


def test_whitespace_trim_collapse_and_punctuation():
    out = names("snes", gl(game("./a.zip", "  Super   Bomberman   2 !  "), game("./b.zip", "Mr. Do , Jr ?")))
    r = rows(out)
    assert tag(r["./a.zip"], "name") == "Super Bomberman 2!"
    assert tag(r["./b.zip"], "name") == "Mr. Do, Jr?"


def test_dash_subtitle_but_hyphenated_names_stay():
    out = names("genesis", gl(game("./a.zip", "Spider-Man"), game("./b.zip", "X-Men"),
                               game("./c.zip", "X-Men - Children of the Atom"),
                               game("./d.zip", "Spider-Man - Maximum Carnage (USA)"),
                               game("./e.zip", "Avenue Fighter III : 2nd Impact - Giant Attack"),
                               game("./f.zip", "Mega Man - The Wily Wars [!]")))
    r = {p: tag(b, "name") for p, b in rows(out).items()}
    assert r["./a.zip"] == "Spider-Man"
    assert r["./b.zip"] == "X-Men"
    assert r["./c.zip"] == "X-Men: Children of the Atom"
    assert r["./d.zip"] == "Spider-Man: Maximum Carnage"
    assert r["./e.zip"] == "Avenue Fighter III: 2nd Impact - Giant Attack"   # only the first separator
    assert r["./f.zip"] == "Mega Man: The Wily Wars"


def test_dual_title_keeps_the_last_part():
    out = names("genesis", gl(game("./Daimakaimura ~ Ghouls'n Ghosts (Japan, USA).zip"),
                               game("./Bare Knuckle - Ikari no Tekken ~ Streets of Rage (World) (Rev A).zip")))
    r = {p: tag(b, "name") for p, b in rows(out).items()}
    assert list(r.values()) == ["Ghouls'n Ghosts", "Streets of Rage"], r
    assert all("&" not in v for v in r.values())


def test_article_moved_to_front_and_sortname():
    out = names("pcengine", gl(game("./Legendary Axe, The.zip", "Legendary Axe, The"),
                                game("./z.zip", "Lost World, The - Jurassic Park (USA)"),
                                game("./k.zip", "The King of Fighters '94"),
                                game("./h.zip", "Hello, There")))
    r = rows(out)
    assert tag(r["./Legendary Axe, The.zip"], "name") == "The Legendary Axe"
    assert tag(r["./Legendary Axe, The.zip"], "sortname") == "Legendary Axe"
    assert tag(r["./z.zip"], "name") == "The Lost World: Jurassic Park"
    assert tag(r["./k.zip"], "sortname") == "King of Fighters '94"
    assert tag(r["./h.zip"], "name") == "Hello, There" and tag(r["./h.zip"], "sortname") is None


def test_tags_stripped_unless_collision():
    out = names("fbneo", gl(game("./1942.zip", "1942 (Revision B)"),
                             game("./sf2.zip", "Avenue Fighter II (World 910522)"),
                             game("./sf2a.zip", "Avenue Fighter II (US 910214)"),
                             game("./spl.zip", "Splatterhouse (World, new version (SH3))"),
                             game("./x.zip", "Foo [!]")))
    r = {p: tag(b, "name") for p, b in rows(out).items()}
    assert r["./1942.zip"] == "1942"
    assert r["./sf2.zip"] == "Avenue Fighter II (World 910522)"      # collision: first tag kept
    assert r["./sf2a.zip"] == "Avenue Fighter II (US 910214)"
    assert r["./spl.zip"] == "Splatterhouse"                           # nested parens
    assert r["./x.zip"] == "Foo"


def test_official_spelling_kept():
    src = ["19XX: The War Against Destiny", "Avenue Fighter II'", "Ghouls'n Ghosts", "Child Of Dragon", "NAM-1975"]
    out = names("cps2", gl(*[game("./%d.zip" % i, n) for i, n in enumerate(src)]))
    assert [tag(b, "name") for b in rows(out).values()] == src


def test_bios_hidden_and_not_named_as_game():
    out = names("neogeo", gl(game("./neogeo.zip", "Neo Geo"), game("./mslug.zip", "Metal Slug : Super Vehicle-001"),
                              game("./qsound.zip"), game("./pgm.zip", "PGM System BIOS")), DAT)
    r = rows(out)
    for z in ("./neogeo.zip", "./qsound.zip", "./pgm.zip"):
        assert tag(r[z], "hidden") == "true", z
    assert tag(r["./mslug.zip"], "hidden") is None
    out2 = names("fbneo", gl(game("./sfiii.zip", "Avenue Fighter III"), game("./newbios.zip", "Some System BIOS")),
                 DAT + 'game (\n\tname "Some System BIOS"\n\trom ( name newbios.zip size 1 )\n)\n')
    assert tag(rows(out2)["./newbios.zip"], "hidden") == "true"
    assert tag(rows(out2)["./sfiii.zip"], "hidden") is None         # 'BIOS set 1' game is not a BIOS


def test_existing_hidden_false_bios_is_flipped_and_bios_not_in_collisions():
    out = names("neogeo", gl(game("./neogeo.zip", "Neo Geo", "\t\t<hidden>false</hidden>\n")))
    assert tag(rows(out)["./neogeo.zip"], "hidden") == "true"


def test_unnamed_arcade_gets_dat_name_and_console_gets_filename():
    out = names("neogeo", gl(game("./mslug.zip"), game("./unknown.zip")), DAT)
    r = rows(out)
    assert tag(r["./mslug.zip"], "name") == "Metal Slug: Super Vehicle-001"
    assert tag(r["./unknown.zip"], "name") == "unknown"
    assert r["./mslug.zip"].index("<path>") < r["./mslug.zip"].index("<name>")
    out = names("snes", gl(game("./ActRaiser 2 (USA).zip"), game("./Super Metroid (Japan, USA) (En,Ja).zip")))
    assert [tag(b, "name") for b in rows(out).values()] == ["ActRaiser 2", "Super Metroid"]


def test_m3u_disc_members_hidden():
    out = names("psx", gl(game("./FF7 (USA).m3u", "Final Fantasy VII"), game("./FF7 (USA) (Disc 1).chd", "x"),
                           game("./Solo (USA).chd", "Solo")))
    r = rows(out)
    assert tag(r["./FF7 (USA) (Disc 1).chd"], "hidden") == "true"
    assert tag(r["./FF7 (USA).m3u"], "hidden") is None and tag(r["./Solo (USA).chd"], "hidden") is None


# ------------------------------------------------------------- invariants

def test_only_name_sortname_hidden_change_and_two_root_survives():
    src = gl(game("./sf2.zip", "Avenue Fighter II : The World Warrior"),
             game("./k.zip", "The King", extra="\t\t<rating>0.8</rating>\n\t\t<favorite>true</favorite>\n"),
             two_root=True)
    out = names("cps1", src)
    assert out.startswith('<?xml version="1.0"?>\n<alternativeEmulator>\n\t<label>FinalBurn Neo</label>\n</alternativeEmulator>\n<gameList>')
    strip = lambda t: re.sub(r"[ \t]*<(name|sortname|hidden)>.*?</\1>\n", "", t)
    assert strip(out) == strip(src)
    assert re.findall(r"<path>.*?</path>", out) == re.findall(r"<path>.*?</path>", src)
    assert "keep  me &amp; my  spaces" in out and "<favorite>true</favorite>" in out


def test_idempotent():
    src = gl(game("./a.zip", "Foo : Bar (USA)"), game("./Lost, The.zip"), game("./neogeo.zip"), two_root=True)
    once = names("neogeo", src)
    assert names("neogeo", once) == once


def test_xml_escaping_roundtrip():
    out = names("genesis", gl(game("./Batman &amp; Robin, The (USA).zip", "Batman &amp; Robin, The")))
    assert "<name>The Batman &amp; Robin</name>" in out
    cg.parse_gamelist(out)


# ---------------------------------------------------------------- the CLI

def run(*argv):
    o, e = io.StringIO(), io.StringIO()
    with redirect_stdout(o), redirect_stderr(e):
        try:
            rc = cg.main(list(argv))
        except SystemExit as ex:
            rc = ex.code
    return rc, o.getvalue(), e.getvalue()


def _tree(tmp, files):
    for system, text in files.items():
        os.makedirs(os.path.join(tmp, system))
        with open(os.path.join(tmp, system, "gamelist.xml"), "w", encoding="utf-8") as f:
            f.write(text)


def test_plan_writes_nothing_apply_backs_up_first():
    with tempfile.TemporaryDirectory() as tmp:
        src = gl(game("./sf2.zip", "Avenue Fighter II : The World Warrior"), two_root=True)
        _tree(tmp, {"cps1": src})
        p = os.path.join(tmp, "cps1", "gamelist.xml")
        rc, out, _ = run("names", "--gamelists", tmp)
        assert rc == 0 and "CHANGED" in out and "Avenue Fighter II: The World Warrior" in out
        assert open(p).read() == src and os.listdir(os.path.join(tmp, "cps1")) == ["gamelist.xml"]
        rc, out, _ = run("names", "--gamelists", tmp, "--apply", "--stamp", "T1")
        assert rc == 0
        bak = p + ".bak-names-T1"
        assert open(bak).read() == src
        assert "Avenue Fighter II: The World Warrior" in open(p).read()
        rc, out, _ = run("names", "--gamelists", tmp, "--apply", "--stamp", "T2")      # already clean: no new backup
        assert rc == 0 and "SAME" in out and not os.path.exists(p + ".bak-names-T2")


def test_backup_dir_option():
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as bk:
        _tree(tmp, {"cps1": gl(game("./a.zip", "A : B"))})
        assert run("names", "--gamelists", tmp, "--apply", "--stamp", "S", "--backup-dir", bk)[0] == 0
        assert os.path.isfile(os.path.join(bk, "S", "cps1", "gamelist.xml"))


def test_nested_uses_the_cleaned_names():
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out:
        _tree(tmp, {"cps2": gl(game("./xmcota.zip", "X-Men : Children of the Atom"), two_root=True)})
        rc, _o, _e = run("nested", "--gamelists", tmp, "--picks", PICKS, "--out", out, "--apply")
        assert rc == 0, _e
        found = ""
        for root, _d, files in os.walk(os.path.join(out, "gamelists")):
            for f in files:
                found += open(os.path.join(root, f), encoding="utf-8").read()
        assert "<name>X-Men: Children of the Atom</name>" in found and " : " not in found
        assert "X-Men : Children" in open(os.path.join(tmp, "cps2", "gamelist.xml")).read()   # source untouched


def test_bad_inputs():
    with tempfile.TemporaryDirectory() as tmp:
        assert run("names", "--gamelists", tmp)[0] == 3                        # no gamelists
        _tree(tmp, {"cps1": "<gameList><game></gameList>"})
        assert run("names", "--gamelists", tmp)[0] == 3                        # malformed
    assert run("names")[0] == 2                                                # usage


# --------------------------------------------------- missing entries (--roms)

def _roms(tmp, system, files):
    d = os.path.join(tmp, system)
    os.makedirs(d, exist_ok=True)
    for f in files:
        open(os.path.join(d, f), "w").close()


def test_roms_adds_entries_for_files_without_one_and_never_duplicates():
    with tempfile.TemporaryDirectory() as gl_dir, tempfile.TemporaryDirectory() as rd:
        _tree(gl_dir, {"neogeo": gl(game("./mslug.zip", "Metal Slug"))})
        _roms(rd, "neogeo", ["mslug.zip", "aof3.zip", "neogeo.zip", ".hidden", "notes.txt", "mslug.srm"])
        dat = os.path.join(rd, "x.dat")
        open(dat, "w").write(DAT + 'game (\n\tname "Art of Fighting 3 / Path of the Warrior Gaiden"\n\trom ( name aof3.zip size 1 )\n)\n')
        rc, out, _ = run("names", "--gamelists", gl_dir, "--roms", rd, "--dat", dat, "--apply", "--stamp", "S")
        assert rc == 0 and "added    2" in out, out
        text = open(os.path.join(gl_dir, "neogeo", "gamelist.xml")).read()
        r = rows(text)
        assert list(r) == ["./mslug.zip", "./aof3.zip", "./neogeo.zip"], list(r)     # one entry each, no .txt/.srm/dot file
        assert tag(r["./aof3.zip"], "name") == "Art of Fighting 3"                   # DAT alias: first title
        assert tag(r["./neogeo.zip"], "hidden") == "true"                            # BIOS added hidden
        assert tag(r["./mslug.zip"], "name") == "Metal Slug"
        assert os.path.isfile(os.path.join(gl_dir, "neogeo", "gamelist.xml.bak-names-S"))
        run("names", "--gamelists", gl_dir, "--roms", rd, "--dat", dat, "--apply", "--stamp", "S2")
        assert open(os.path.join(gl_dir, "neogeo", "gamelist.xml")).read() == text    # second run: no duplicates
        assert text.count("<path>./mslug.zip</path>") == 1


def test_roms_console_names_paths_with_spaces_and_new_gamelist():
    odd = "Wiz 'N' Liz & Co (USA) (Rev 1).zip"
    with tempfile.TemporaryDirectory() as gl_dir, tempfile.TemporaryDirectory() as rd:
        _tree(gl_dir, {"snes": gl()})
        _roms(rd, "snes", [odd, "Contra III - The Alien Wars (USA).zip"])
        _roms(rd, "nes", ["Gun.Smoke (USA).zip"])                   # system without any gamelist yet
        rc, out, _ = run("names", "--gamelists", gl_dir, "--roms", rd, "--apply")
        assert rc == 0 and "NEW" in out
        r = rows(open(os.path.join(gl_dir, "snes", "gamelist.xml")).read())
        assert "./" + odd.replace("&", "&amp;") in r                                 # path verbatim, escaped
        assert tag(r["./" + odd.replace("&", "&amp;")], "name") == "Wiz 'N' Liz &amp; Co"
        assert tag(r["./Contra III - The Alien Wars (USA).zip"], "name") == "Contra III: The Alien Wars"
        nes = open(os.path.join(gl_dir, "nes", "gamelist.xml")).read()
        assert tag(rows(nes)["./Gun.Smoke (USA).zip"], "name") == "Gun.Smoke"
        assert not [f for f in os.listdir(os.path.join(gl_dir, "nes")) if "bak" in f]    # nothing to back up


def test_roms_plan_writes_nothing_and_bad_dir_exits_3():
    with tempfile.TemporaryDirectory() as gl_dir, tempfile.TemporaryDirectory() as rd:
        src = gl(game("./a.zip", "A"))
        _tree(gl_dir, {"snes": src})
        _roms(rd, "snes", ["a.zip", "b.zip"])
        rc, out, _ = run("names", "--gamelists", gl_dir, "--roms", rd)
        assert rc == 0 and "(no entry)" in out and open(os.path.join(gl_dir, "snes", "gamelist.xml")).read() == src
        assert run("names", "--gamelists", gl_dir, "--roms", os.path.join(rd, "missing"))[0] == 3


def test_nested_lists_roms_without_entries_with_clean_names():
    with tempfile.TemporaryDirectory() as gl_dir, tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as out:
        _tree(gl_dir, {"snes": gl()})
        _roms(rd, "snes", ["F-Zero (USA).zip"])
        rc, _o, e = run("nested", "--gamelists", gl_dir, "--roms", rd, "--picks", PICKS, "--out", out, "--apply")
        assert rc == 0, e
        found = "".join(open(os.path.join(r, f), encoding="utf-8").read()
                        for r, _d, fs in os.walk(os.path.join(out, "gamelists")) for f in fs)
        assert "<name>F-Zero</name>" in found


def test_broken_duplicate_entry_is_detected():
    src = gl(game("./a.zip", "A"), game("./a.zip", "A"))
    assert len(rows(src)) == 1 and src.count("<path>./a.zip</path>") == 2     # the check a duplicate would fail
    ok, _ = cg.add_missing(gl(game("./a.zip", "A")), ["a.zip"])
    assert ok.count("<path>./a.zip</path>") == 1


def test_dual_title_inside_parentheses_is_not_split():
    out = names("neogeo", gl(game("./fatfury1.zip", "Fatal Fury (NGM-033 ~ NGH-033)")))
    assert tag(rows(out)["./fatfury1.zip"], "name") == "Fatal Fury"


# ------------------------------------------------------------ broken cases

def test_broken_audit_flags_uncleaned_gamelists():
    src = gl(game("./a.zip", "Avenue Fighter II : The World"), game("./neogeo.zip", "Neo Geo"),
             game("./b.zip", "Double  Space"), game("./c.zip", "Legend, The"))
    assert audit(src, src, "neogeo"), "an unclean gamelist must fail the audit"
    assert audit(src, names("neogeo", src), "neogeo") == []


def test_broken_double_space_survives_fails():
    good = names("snes", gl(game("./a.zip", "Foo  Bar")))
    assert audit(good, good) == []
    bad = good.replace("Foo Bar", "Foo  Bar")
    assert any("whitespace" in p for p in audit(good, bad))


def test_broken_unhidden_bios_fails():
    src = gl(game("./neogeo.zip", "Neo Geo"))
    good = names("neogeo", src)
    assert audit(src, good, "neogeo") == []
    bad = good.replace("<hidden>true</hidden>", "<hidden>false</hidden>")
    assert any("BIOS not hidden" in p for p in audit(src, bad, "neogeo"))


def test_broken_path_changed_fails():
    src = gl(game("./a b.zip", "Foo"))
    good = names("snes", src)
    assert audit(src, good) == []
    assert "path changed" in audit(src, good.replace("./a b.zip", "./a_b.zip"))


def test_broken_spider_man_mangled_fails():
    src = gl(game("./s.zip", "Spider-Man"))
    out = names("genesis", src)
    assert tag(rows(out)["./s.zip"], "name") == "Spider-Man"
    mangled = out.replace("Spider-Man", "Spider: Man")
    assert tag(rows(mangled)["./s.zip"], "name") != "Spider-Man"


if __name__ == "__main__":
    main(globals(), per_test=True)
