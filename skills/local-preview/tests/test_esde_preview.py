#!/usr/bin/env python3
"""Tests for the local ES-DE preview harness (skills/local-preview/preview-esde.sh), stdlib only.

pytest-compatible, or run directly:   python3 skills/local-preview/tests/test_esde_preview.py

FAST (always run, ~1 s, no docker): the fallback detector on fixture logs (good PASS, fallback /
parse-error / no-theme logs MUST FAIL), the fixture/home builder, CLI usage errors.

SMOKE (opt-in, ~1 min, needs Colima + the cached AppImage + a theme dir under $HOME):
    ESDE_PREVIEW_SMOKE=1 [ESDE_PREVIEW_THEME=/path/to/theme] python3 skills/local-preview/tests/test_esde_preview.py
renders the good theme (MUST exit 0 + PNGs) and a copy with malformed XML (MUST exit 1).
Why opt-in: a render is ~25 s of software GL each and needs the 126 MB AppImage cached locally.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
LP = os.path.join(ROOT, "skills", "local-preview")
ESDE = os.path.join(LP, "esde")
FIX = os.path.join(LP, "fixtures", "esde-preview")
PREVIEW = os.path.join(LP, "preview-esde.sh")
THEME = "coin-dojo"
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
from hktest import check, main, skip  # noqa: E402  (SKIPs counted in the RESULT line, #135)


def run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def detector(log):
    return run([sys.executable, "-I", os.path.join(ESDE, "check_log.py"), os.path.join(FIX, "logs", log), THEME])


def test_detector_good():
    r = detector("good.log")
    check(r.returncode == 0, "detector: good log passes (audio/VSync noise ignored)")


def test_detector_broken():
    for name, needle in (("fallback-linear.log", "FALLBACK"),
                         ("broken-xml.log", "ThemeData"),
                         ("no-theme-loaded.log", "never loaded")):
        r = detector(name)
        check(r.returncode == 1 and needle in r.stdout, "detector: %s MUST fail (%s)" % (name, needle))
    r = run([sys.executable, "-I", os.path.join(ESDE, "check_log.py"), "/nonexistent/es_log.txt", THEME])
    check(r.returncode == 2, "detector: unreadable log exits 2")


def test_make_home():
    out = tempfile.mkdtemp()
    try:
        r = run([sys.executable, "-I", os.path.join(ESDE, "make_home.py"), FIX, "/preview/theme", out,
                 "--theme-name", THEME])
        check(r.returncode == 0, "make_home: builds fixture home (%s)" % r.stderr.strip()[-200:])
        home = os.path.join(out, "home", "ES-DE")
        s = open(os.path.join(home, "settings", "es_settings.xml")).read()
        check('name="Theme" value="%s"' % THEME in s, "make_home: es_settings selects the theme via the Theme key")
        check("assword" not in s and "ScraperUser" not in s, "make_home: no scraper credentials in settings")
        check(os.readlink(os.path.join(home, "themes", THEME)) == "/preview/theme", "make_home: theme is a symlink")
        roms = os.path.join(out, "roms")
        files = [os.path.join(d, f) for d, _, fs in os.walk(roms) for f in fs]
        check(len(files) >= 30 and all(os.path.getsize(f) == 0 for f in files),
              "make_home: %d ROM placeholders, all empty (no real ROMs)" % len(files))
        check(os.path.isfile(os.path.join(home, "collections", "custom-fighters.cfg")), "make_home: collections copied")
    finally:
        shutil.rmtree(out, ignore_errors=True)
    out = tempfile.mkdtemp()
    try:
        r = run([sys.executable, "-I", os.path.join(ESDE, "make_home.py"), "/nonexistent-fixture", "/preview/theme", out])
        check(r.returncode != 0, "make_home: missing fixture dir MUST fail")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_fixture_is_clean():
    bad = []
    for d, _, fs in os.walk(FIX):
        for f in fs:
            if not f.endswith((".xml", ".cfg", ".log")):
                bad.append(f)
    check(not bad, "fixtures: only text fixtures, no binaries/art (%s)" % bad)


def test_cli_usage():
    r = run([PREVIEW])
    check(r.returncode == 2, "cli: no args exits 2")
    d = tempfile.mkdtemp()
    try:
        r = run([PREVIEW, d, os.path.join(d, "out")])
        check(r.returncode == 2 and "theme.xml" in r.stderr, "cli: dir without theme.xml MUST be refused")
        open(os.path.join(d, "theme.xml"), "w").write("<theme/>")
        r = run([PREVIEW, d, os.path.join(d, "out"), "--view", "bogus"])
        check(r.returncode == 2 and "--view" in r.stderr, "cli: bad --view MUST be refused")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_smoke():
    if os.environ.get("ESDE_PREVIEW_SMOKE") != "1":
        skip("smoke render (set ESDE_PREVIEW_SMOKE=1)")
        return
    theme = os.environ.get("ESDE_PREVIEW_THEME") or os.path.join(ROOT, "themes", THEME)
    check(os.path.isfile(os.path.join(theme, "theme.xml")), "smoke: theme dir found: " + theme)
    cache = os.path.join(os.path.expanduser("~"), ".cache", "coin-dojo", "preview")
    os.makedirs(cache, exist_ok=True)
    work = tempfile.mkdtemp(dir=cache, prefix="test-")  # must be under $HOME for Colima
    try:
        out = os.path.join(work, "good")
        r = run([PREVIEW, theme, out])
        check(r.returncode == 0, "smoke: good theme renders, exit 0 (rc=%s) %s" % (r.returncode, r.stdout[-300:]))
        for png in ("system.png", "gamelist.png"):
            p = os.path.join(out, png)
            check(os.path.isfile(p) and os.path.getsize(p) > 20000, "smoke: %s written" % png)
        bad = os.path.join(work, "broken-theme")
        shutil.copytree(theme, bad)
        with open(os.path.join(bad, "colors.xml"), "a") as f:
            f.write("<broken\n")
        r = run([PREVIEW, bad, os.path.join(work, "bad")])
        check(r.returncode == 1 and "CHECK: FAIL" in r.stdout,
              "smoke: malformed-XML theme MUST fail with exit 1 (rc=%s)" % r.returncode)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main(globals())
