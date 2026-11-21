#!/usr/bin/env python3
"""Tests for `collgen.py dupes` + duplicates.tsv (stdlib only; pytest or run directly).

    python3 skills/collections/tests/test_dupes.py

--delete: removes the <game> block, the ROM file and its scraped media; a second run counts them
as already gone. Broken cases: --delete without --roms exits 2; a hide-path (or keep-path) that is not in its gamelist exits 3 and writes nothing; a malformed
TSV row exits 3; the committed duplicates.tsv must be well formed and name no virtual system.
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
spec = importlib.util.spec_from_file_location("collgen", os.path.join(SKILL, "collgen.py"))
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)

ARC = """<?xml version="1.0"?>
<gameList>
\t<game>
\t\t<path>./altbeast.zip</path>
\t\t<name>Altered Beast</name>
\t</game>
\t<game>
\t\t<path>./contra.zip</path>
\t\t<name>Contra</name>
\t</game>
</gameList>
"""
GEN = """<?xml version="1.0"?>
<gameList>
\t<game>
\t\t<path>./Altered Beast (USA, Europe).zip</path>
\t\t<name>Altered Beast</name>
\t\t<desc>keep me &amp; my bytes</desc>
\t</game>
\t<game>
\t\t<path>./Sonic.zip</path>
\t\t<name>Sonic</name>
\t\t<hidden>false</hidden>
\t</game>
\t<game>
\t\t<path>./Already.zip</path>
\t\t<name>Already</name>
\t\t<hidden>true</hidden>
\t</game>
</gameList>
"""
TSV = ("# comment\n\nfbneo\t./altbeast.zip\tgenesis\t./Altered Beast (USA, Europe).zip\n"
       "fbneo\t./contra.zip\t-\t-\n"
       "fbneo\t./altbeast.zip\tgenesis\t./Already.zip\n"
       "fbneo\t./altbeast.zip\tgenesis\t./Sonic.zip\n")


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cg.main(list(argv))
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


def world(tsv=TSV, arc=ARC, gen=GEN):
    root = tempfile.mkdtemp()
    for s, t in (("fbneo", arc), ("genesis", gen)):
        os.makedirs(os.path.join(root, "gl", s))
        open(os.path.join(root, "gl", s, "gamelist.xml"), "w", encoding="utf-8").write(t)
    open(os.path.join(root, "dupes.tsv"), "w", encoding="utf-8").write(tsv)
    return os.path.join(root, "gl"), os.path.join(root, "dupes.tsv")


def rd(gl, s):
    return open(os.path.join(gl, s, "gamelist.xml"), encoding="utf-8").read()


def test_plan_writes_nothing_and_counts():
    gl, tsv = world()
    rc, out, _ = run("dupes", "--gamelists", gl, "--duplicates", tsv)
    assert rc == 0 and "plan only" in out and "hide   2  already hidden   1  delete   0  already gone   0  CHANGED" in out
    assert "1 keep-only note" in out
    assert rd(gl, "genesis") == GEN and rd(gl, "fbneo") == ARC
    assert not [f for f in os.listdir(os.path.join(gl, "genesis")) if f != "gamelist.xml"]


def test_apply_adds_hidden_only_backs_up_and_second_run_is_same():
    gl, tsv = world()
    rc, out, _ = run("dupes", "--gamelists", gl, "--duplicates", tsv, "--apply", "--stamp", "T1")
    assert rc == 0 and "1 gamelist(s) rewritten" in out
    new = rd(gl, "genesis")
    assert new.count("<hidden>true</hidden>") == 3 and "<hidden>false</hidden>" not in new
    assert "keep me &amp; my bytes" in new                       # untouched text stays byte-identical
    want = GEN.replace("<name>Altered Beast</name>", "<name>Altered Beast</name>\n\t\t<hidden>true</hidden>") \
              .replace("<hidden>false</hidden>", "<hidden>true</hidden>")
    assert new == want                                            # text-level: only those two edits
    assert rd(gl, "fbneo") == ARC                                 # keep side never touched
    assert open(os.path.join(gl, "genesis", "gamelist.xml.bak-dupes-T1"), encoding="utf-8").read() == GEN
    rc, out, _ = run("dupes", "--gamelists", gl, "--duplicates", tsv)
    assert rc == 0 and "hide   0  already hidden   3  delete   0  already gone   0  SAME" in out and out.rstrip().split("\n")[-2].endswith("SAME")


def test_backup_dir():
    gl, tsv = world()
    bd = tempfile.mkdtemp()
    run("dupes", "--gamelists", gl, "--duplicates", tsv, "--apply", "--stamp", "T2", "--backup-dir", bd)
    assert open(os.path.join(bd, "T2", "genesis", "gamelist.xml"), encoding="utf-8").read() == GEN


def test_missing_hide_path_exits_3_and_writes_nothing():      # BROKEN CASE
    gl, tsv = world(TSV + "fbneo\t./altbeast.zip\tgenesis\t./Nope.zip\n")
    rc, _o, err = run("dupes", "--gamelists", gl, "--duplicates", tsv, "--apply")
    assert rc == 3 and "hide genesis ./Nope.zip" in err
    assert rd(gl, "genesis") == GEN


def test_missing_keep_path_exits_3():                          # BROKEN CASE (would hide the only copy)
    gl, tsv = world("fbneo\t./gone.zip\tgenesis\t./Sonic.zip\n")
    rc, _o, err = run("dupes", "--gamelists", gl, "--duplicates", tsv, "--apply")
    assert rc == 3 and "keep fbneo ./gone.zip" in err and rd(gl, "genesis") == GEN


def test_missing_system_gamelist_exits_3():                    # BROKEN CASE
    gl, tsv = world("fbneo\t./altbeast.zip\tsnes\t./x.zip\n")
    rc, _o, err = run("dupes", "--gamelists", gl, "--duplicates", tsv)
    assert rc == 3 and "snes" in err


def test_bad_tsv_row_and_missing_file_exit_3():                # BROKEN CASE
    gl, tsv = world("fbneo\t./altbeast.zip\tgenesis\n")
    assert run("dupes", "--gamelists", gl, "--duplicates", tsv)[0] == 3
    assert run("dupes", "--gamelists", gl, "--duplicates", tsv + ".nope")[0] == 3
    gl, tsv = world("# only a comment\n")
    assert run("dupes", "--gamelists", gl, "--duplicates", tsv)[0] == 3


def test_virtual_system_in_tsv_is_refused():                   # BROKEN CASE
    gl, tsv = world("fbneo\t./altbeast.zip\tgenres\t./x.zip\n")
    os.makedirs(os.path.join(gl, "genres"))
    open(os.path.join(gl, "genres", "gamelist.xml"), "w").write("<gameList/>")
    rc, _o, err = run("dupes", "--gamelists", gl, "--duplicates", tsv)
    assert rc == 3 and "virtual" in err


def test_usage_error_exits_2():                                # BROKEN CASE
    assert run("dupes")[0] == 2


def test_committed_duplicates_tsv_is_well_formed():
    # the community example beside collgen.py and the default list (the maintainer's in personal/ when present, #190)
    for path in sorted({os.path.join(SKILL, "duplicates.tsv"), cg.DUPLICATES_TSV}):
        rows = cg.parse_duplicates(open(path, encoding="utf-8").read(), path)
        assert len(rows) >= 40 and sum(1 for r in rows if r[2] == "-") == 1, path
        hides = [(r[2], r[3]) for r in rows if r[2] != "-"]
        assert len(set(hides)) == len(hides) == len(rows) - 1, path     # no hide listed twice
        assert not {r[0] for r in rows} & set(cg.VIRTUAL_SYSTEMS) and not {r[2] for r in rows} & set(cg.VIRTUAL_SYSTEMS)
        assert not {h for h in hides if h[0] == "nes" and "Contra" in h[1]}   # kept on purpose


def test_virtual_list_matches_nested():
    sys.path.insert(0, SKILL)
    import nested
    assert tuple(nested.VIRTUAL_SLUGS) == tuple(cg.VIRTUAL_SYSTEMS)


def roms_media(gl):
    """ROM + media files beside the world: <root>/roms/<sys>/<file>, <root>/media/<sys>/<type>/<stem>.*"""
    root = os.path.dirname(gl)
    files = {"roms/fbneo/altbeast.zip": "A", "roms/fbneo/contra.zip": "C",
             "roms/genesis/Altered Beast (USA, Europe).zip": "x" * 2048, "roms/genesis/Sonic.zip": "S",
             "roms/genesis/Already.zip": "R",
             "media/genesis/covers/Altered Beast (USA, Europe).png": "img",
             "media/genesis/videos/Altered Beast (USA, Europe).mp4": "vid",
             "media/genesis/covers/Sonic.png": "keep"}
    for rel, body in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(body)
    return os.path.join(root, "roms"), os.path.join(root, "media")


ONE = "fbneo\t./altbeast.zip\tgenesis\t./Altered Beast (USA, Europe).zip\n"


def test_delete_plan_touches_nothing():
    gl, tsv = world(tsv=ONE)
    roms, media = roms_media(gl)
    rc, out, _ = run("dupes", "--gamelists", gl, "--duplicates", tsv, "--roms", roms, "--media", media, "--delete")
    assert rc == 0 and "delete: 3 file(s)" in out and "plan only" in out
    assert rd(gl, "genesis") == GEN and os.path.exists(os.path.join(roms, "genesis", "Altered Beast (USA, Europe).zip"))


def test_delete_apply_removes_entry_rom_media_only_and_rerun_is_gone():
    gl, tsv = world(tsv=ONE)
    roms, media = roms_media(gl)
    rc, out, _ = run("dupes", "--gamelists", gl, "--duplicates", tsv, "--roms", roms, "--media", media,
                     "--delete", "--apply", "--stamp", "D1")
    assert rc == 0 and "3 file(s) deleted" in out
    g = rd(gl, "genesis")
    assert "Altered Beast" not in g and "./Sonic.zip" in g and "./Already.zip" in g
    assert g.startswith('<?xml version="1.0"?>\n<gameList>\n\t<game>\n\t\t<path>./Sonic.zip')
    assert not os.path.exists(os.path.join(roms, "genesis", "Altered Beast (USA, Europe).zip"))
    assert not os.listdir(os.path.join(media, "genesis", "videos"))
    assert os.listdir(os.path.join(media, "genesis", "covers")) == ["Sonic.png"]       # other games' media stay
    assert os.path.exists(os.path.join(roms, "fbneo", "altbeast.zip"))                 # the kept version stays
    assert os.path.exists(os.path.join(gl, "genesis", "gamelist.xml.bak-dupes-D1"))
    rc, out, _ = run("dupes", "--gamelists", gl, "--duplicates", tsv, "--roms", roms, "--delete")
    assert rc == 0 and "already gone   1" in out and out.rstrip().split("\n")[-3].endswith("SAME")


def test_delete_without_roms_exits_2():                          # BROKEN CASE
    gl, tsv = world(tsv=ONE)
    rc, _, err = run("dupes", "--gamelists", gl, "--duplicates", tsv, "--delete")
    assert rc == 2 and "--roms" in err and rd(gl, "genesis") == GEN


def test_delete_refuses_when_kept_rom_file_is_missing():          # BROKEN CASE (would delete the only copy)
    gl, tsv = world(tsv=ONE)
    roms, media = roms_media(gl)
    os.remove(os.path.join(roms, "fbneo", "altbeast.zip"))
    rc, _, err = run("dupes", "--gamelists", gl, "--duplicates", tsv, "--roms", roms, "--delete", "--apply")
    assert rc == 3 and "keep fbneo ./altbeast.zip" in err
    assert os.path.exists(os.path.join(roms, "genesis", "Altered Beast (USA, Europe).zip")) and rd(gl, "genesis") == GEN


if __name__ == "__main__":
    main(globals(), per_test=True)
