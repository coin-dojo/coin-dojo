#!/usr/bin/env python3
"""Tests for esde_verify.py (#89): the post-restart counts + ES-DE log check. Plain python3 (stdlib).

    python3 ansible/roles/esde_common/tests/test_esde_verify.py

Broken cases (must FAIL): a link without a game entry, a game without a link (the neogeo bug shape),
a count that differs from what the deploy built, a system the deploy built that es_systems.xml lacks,
a new Error line after a restart, malformed XML / missing files (exit 3), bad usage (exit 2).
"""
import io
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "files"))
import esde_verify as ev  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "tests", "lib"))
from hktest import check, main  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)


def run(*argv):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = ev.main(list(argv))
    return rc, out.getvalue()


def tree(games=None, links=None):
    """A fake cabinet: es_systems.xml with must-play + systems, gamelists, roms_nested symlinks."""
    base = tempfile.mkdtemp()
    nested = os.path.join(base, "nested")
    games = games or {"must-play": ["./cps2/ssf2t.zip", "./neo-geo/mslug.zip"], "systems": ["./cps2/ssf2t.zip"]}
    links = games if links is None else links
    sysxml = "<?xml version=\"1.0\"?>\n<systemList>\n<loadExclusive/>\n"
    for name in sorted(games):
        sysxml += "<system><name>%s</name><path>%s/%s</path><platform>arcade</platform></system>\n" % (name, nested, name)
        gdir = os.path.join(base, "gamelists", name)
        os.makedirs(gdir)
        with open(os.path.join(gdir, "gamelist.xml"), "w") as f:
            f.write("<?xml version=\"1.0\"?>\n<gameList>\n<folder><path>./cps2</path></folder>\n")
            for g in games[name]:
                f.write("<game><path>%s</path><name>x</name></game>\n" % g)
            f.write("</gameList>\n")
    for name, paths in links.items():
        for pth in paths:
            dst = os.path.join(nested, name, pth[2:])
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.symlink("/data/roms/whatever.zip", dst)
    os.makedirs(os.path.join(nested, "must-play"), exist_ok=True)
    os.makedirs(os.path.join(nested, "systems"), exist_ok=True)
    with open(os.path.join(base, "es_systems.xml"), "w") as f:
        f.write(sysxml + "</systemList>\n")
    return base


def counts(base, *extra):
    return run("counts", "--es-systems", os.path.join(base, "es_systems.xml"),
               "--gamelists", os.path.join(base, "gamelists"), *extra)


def test_counts_pass_and_json():
    b = tree()
    rc, out = counts(b)
    check(rc == 0 and "RESULT: PASS" in out and "links     2  games     2" in out, "consistent tree passes with counts")
    rc, out = counts(b, "--json")
    js = json.loads([l for l in out.splitlines() if l.startswith("{")][0])
    check(rc == 0 and js == {"must-play": 2, "systems": 1}, "--json prints the link counts %s" % js)
    rc, out = counts(b, "--expect", json.dumps(js))
    check(rc == 0, "--expect equal to the live counts passes")
    shutil.rmtree(b)


def test_broken_link_without_game():
    b = tree(links={"must-play": ["./cps2/ssf2t.zip", "./neo-geo/mslug.zip", "./cps2/extra.zip"],
                    "systems": ["./cps2/ssf2t.zip"]})
    rc, out = counts(b)
    check(rc == 1 and "FAIL must-play: 1 link(s) without a game entry" in out, "BROKEN: link without a game entry FAILs")
    shutil.rmtree(b)


def test_broken_game_without_link_neogeo_shape():
    b = tree(links={"must-play": ["./cps2/ssf2t.zip"], "systems": ["./cps2/ssf2t.zip"]})
    rc, out = counts(b)
    check(rc == 1 and "1 game(s) without a link ['./neo-geo/mslug.zip']" in out, "BROKEN: game without a link FAILs")
    shutil.rmtree(b)


def test_broken_expect_mismatch():
    b = tree()
    rc, out = counts(b, "--expect", json.dumps({"must-play": 669, "systems": 1}))
    check(rc == 1 and "must-play: the deploy built 669 game(s), the cabinet has 2" in out,
          "BROKEN: count differs from the build FAILs (1035 of 1143 shape)")
    rc, out = counts(b, "--expect", json.dumps({"must-play": 2, "systems": 1, "series": 37}))
    check(rc == 1 and "series: built by the deploy but not in" in out, "BROKEN: built system missing on the cabinet FAILs")
    shutil.rmtree(b)


def test_broken_inputs():
    b = tree()
    with open(os.path.join(b, "gamelists", "systems", "gamelist.xml"), "w") as f:
        f.write("<gameList><game>")
    rc, out = counts(b)
    check(rc == 3 and "RESULT: FAIL" in out and "malformed XML" in out, "BROKEN: malformed gamelist exits 3")
    rc, out = run("counts", "--es-systems", os.path.join(b, "nope.xml"), "--gamelists", b)
    check(rc == 3, "BROKEN: missing es_systems.xml exits 3")
    rc, out = counts(b, "--expect", "[1]")
    check(rc == 3, "BROKEN: --expect that is not an object exits 3")
    shutil.rmtree(os.path.join(b, "gamelists", "must-play"))
    with open(os.path.join(b, "gamelists", "systems", "gamelist.xml"), "w") as f:
        f.write("<gameList/>")
    rc, out = counts(b)
    check(rc == 1 and "must-play: gamelist" in out and "is missing" in out, "BROKEN: missing gamelist FAILs")
    rc, _ = run("counts")
    check(rc == 2, "BROKEN: bad usage exits 2")
    rc, _ = run()
    check(rc == 2, "BROKEN: no subcommand exits 2")
    shutil.rmtree(b)


def _logs(cur, prev=None):
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "es_log.txt"), "w") as f:
        f.write(cur)
    if prev is not None:
        with open(os.path.join(d, "es_log.txt.bak"), "w") as f:
            f.write(prev)
    return d, ["--log", os.path.join(d, "es_log.txt"), "--prev", os.path.join(d, "es_log.txt.bak")]


def test_log_new_errors():
    old = "Nov 14 22:00:01 Info:    ES-DE 3.5.0\nNov 14 22:00:02 Error:   Couldn't find media for x\n"
    same = "Nov 14 23:10:41 Info:    ES-DE 3.5.0\nNov 14 23:10:42 Error:   Couldn't find media for x\n"
    d, a = _logs(same, old)
    rc, out = run("log", *a)
    check(rc == 0 and "0 new error(s)" in out, "a known error (other timestamp) is not new")
    d, a = _logs(same + "Nov 14 23:10:43 Error:   Couldn't parse gamelist.xml for system \"series\"\n", old)
    rc, out = run("log", *a)
    check(rc == 1 and "FAIL new ES-DE error: Error:   Couldn't parse gamelist.xml" in out, "BROKEN: a new Error line FAILs")
    rc, out = run("log", *(a + ["--warn-only"]))
    check(rc == 0 and "WARN new ES-DE error" in out, "--warn-only reports it as WARN (no restart this run)")
    rc, out = run("log", *(a + ["--ignore", "parse gamelist"]))
    check(rc == 0, "--ignore drops a known harmless error")
    d, a = _logs("[2026-11-14 23:10:43.120] Warning: slow\n", "")
    rc, out = run("log", *a)
    check(rc == 0 and "WARN new ES-DE warning: Warning: slow" in out, "new warning is WARN, not FAIL; bracket stamps strip")
    d, a = _logs("Nov 14 23:10:43 Error: boom\n")
    rc, out = run("log", *a)
    check(rc == 1, "BROKEN: no previous log -> every error is new")
    rc, out = run("log", "--log", os.path.join(d, "missing.txt"))
    check(rc == 3, "BROKEN: missing log exits 3")
    rc, out = run("log", *(a + ["--ignore", "("]))
    check(rc == 3, "BROKEN: bad --ignore regex exits 3")


if __name__ == "__main__":
    main(globals())
