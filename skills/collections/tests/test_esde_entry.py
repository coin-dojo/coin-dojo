#!/usr/bin/env python3
"""The FIGHTCADE and QUIT TO MAIN MENU ES-DE systems (#214): generation + the entry script. Stdlib only.

    python3 skills/collections/tests/test_esde_entry.py

Generation (`collgen.py nested --fightcade-entry/--quit-entry`):
- without the flags no such system, link or gamelist; with them: own systems (not in `settings`), platform ignore,
  .hks, command = esde_entry.sh <mode>, order FIGHTCADE < TROPHIES < SETTINGS < QUIT, one gamelist game each,
  validate_nested passes, a rebuild keeps ES-DE's playcount
esde_entry.sh (fake pgrep, fake session-stop helper, fake ES-DE):
- writes next-mode atomically (dir 0700), calls the stop helper once, exits 0; without the helper TERMs ES-DE
BROKEN cases: a game runs (retroarch / retroarch122 / flycast / the 1.22 path) = refuses, nothing written, nothing
stopped; no XDG_RUNTIME_DIR and no /run/user/<uid>; fightcade without the launcher; a bad mode = exit 2;
an unwritable run dir; a missing entry file = dead link (validator FAIL).
"""
import importlib.util
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "lib"))
from hktest import main  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures")
GL, PICKS = os.path.join(FIX, "gamelists"), os.path.join(FIX, "picks.html")
SCRIPT = os.path.join(SKILL, "esde_entry.sh")
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


def build(base, **entries):
    out, roms = os.path.join(base, "out"), os.path.join(base, "roms")
    if not os.path.isdir(roms):
        for system in os.listdir(GL):
            os.makedirs(os.path.join(roms, system))
            for line in open(os.path.join(GL, system, "gamelist.xml"), encoding="utf-8"):
                if "<path>./" in line:
                    open(os.path.join(roms, system, line.split("<path>./", 1)[1].split("</path>", 1)[0]), "w").close()
    argv = ["nested", "--gamelists", GL, "--picks", PICKS, "--out", out, "--min-decade", "1",
            "--rom-root", "/data/roms", "--apply"]
    for flag, path in entries.items():
        argv += ["--" + flag.replace("_", "-"), path]
    rc, o, err = call(cg.main, argv)
    assert rc == 0, err
    return out, roms, o


def systems(out):
    text = open(os.path.join(out, "custom_systems", "es_systems.xml"), encoding="utf-8").read()
    root = ET.fromstring("<systemList>" + text.split("<systemList>", 1)[1])
    return [(s.findtext("name"), s) for s in root.iter("system")]


def test_without_flags_nothing():
    base = tempfile.mkdtemp()
    try:
        out, _r, o = build(base)
        names = [n for n, _s in systems(out)]
        for slug in (nested.FIGHTCADE_SLUG, nested.QUIT_SLUG):
            assert slug not in names
            assert not os.path.exists(os.path.join(out, "roms_nested", slug))
            assert not os.path.exists(os.path.join(out, "gamelists", slug))
        assert "fightcade  none" in o and "quit       none" in o, o
    finally:
        shutil.rmtree(base)


def test_both_systems_own_and_ordered():
    base = tempfile.mkdtemp()
    try:
        ent = {}
        for n in ("entry.sh", "settings.hks", "trophies.hks"):
            ent[n] = os.path.join(base, n)
            open(ent[n], "w").write("x\n")
        out, roms, _o = build(base, fightcade_entry=ent["entry.sh"], quit_entry=ent["entry.sh"],
                              settings_entry=ent["settings.hks"], trophies_entry=ent["trophies.hks"])
        sy = systems(out)
        names = [n for n, _s in sy]
        assert names[-4:] == ["fightcade", "trophies", "settings", "quit"], names
        sorts = [x.findtext("systemsortname") for _n, x in sy]
        assert sorts == sorted(sorts), sorts
        d = dict(sy)
        for slug, title, cmd, link in (("fightcade", "FIGHTCADE", nested.FIGHTCADE_COMMAND, nested.FIGHTCADE_LINK),
                                       ("quit", "QUIT TO MAIN MENU", nested.QUIT_COMMAND, nested.QUIT_LINK)):
            s = d[slug]
            assert s.findtext("fullname") == title and s.findtext("platform") == "ignore"
            assert s.findtext("extension") == ".hks" and s.findtext("theme") == slug
            assert s.findtext("command") == cmd + " %ROM%", s.findtext("command")
            assert s.findtext("path") == "/data/nested/%s" % slug or s.findtext("path").endswith("/" + slug)
            lk = os.path.join(out, "roms_nested", slug, link)
            assert os.path.islink(lk) and os.readlink(lk) == ent["entry.sh"]
            games = ET.parse(os.path.join(out, "gamelists", slug, "gamelist.xml")).getroot().findall("game")
            assert len(games) == 1 and games[0].findtext("path") == "./" + link
            assert games[0].findtext("nogamecount") == "true"
        assert nested.FIGHTCADE_COMMAND.endswith("esde_entry.sh fightcade") and nested.QUIT_COMMAND.endswith("esde_entry.sh menu")
        # never inside the settings system (its cursor-stop hook would fire)
        assert "fightcade" not in open(os.path.join(out, "gamelists", "settings", "gamelist.xml")).read().lower()
        rc, so, err = call(vn.main, ["--out", out, "--map", "/data/roms=" + roms])
        assert rc == 0 and "RESULT: PASS" in so, (so, err)
        gl = os.path.join(out, "gamelists", "quit", "gamelist.xml")
        txt = open(gl).read().replace("</game>", "\t<playcount>2</playcount>\n\t</game>")
        open(gl, "w").write(txt)
        build(base, fightcade_entry=ent["entry.sh"], quit_entry=ent["entry.sh"],
              settings_entry=ent["settings.hks"], trophies_entry=ent["trophies.hks"])
        assert "<playcount>2</playcount>" in open(gl).read()
    finally:
        shutil.rmtree(base)


def test_only_quit_when_fightcade_missing():
    """The role passes --fightcade-entry only with the launcher installed: FIGHTCADE is then simply not a system."""
    base = tempfile.mkdtemp()
    try:
        e = os.path.join(base, "entry.sh")
        open(e, "w").write("x\n")
        out, _r, _o = build(base, quit_entry=e)
        names = [n for n, _s in systems(out)]
        assert "quit" in names and "fightcade" not in names, names
    finally:
        shutil.rmtree(base)


def test_BROKEN_missing_entry_file_is_a_dead_link():
    base = tempfile.mkdtemp()
    try:
        out, roms, _o = build(base, quit_entry=os.path.join(base, "nope.sh"))
        rc, so, _e = call(vn.main, ["--out", out, "--map", "/data/roms=" + roms])
        assert rc == 1 and "dead link: roms_nested/quit/" in so, so
    finally:
        shutil.rmtree(base)


def test_tool_slugs_skipped_by_readers():
    for slug in ("fightcade", "quit"):
        assert slug in cg.SKIP_SYSTEMS and slug in nested.TOOL_SLUGS and slug not in nested.VIRTUAL_SLUGS


# ------------------------------------------------------------------------------------------------ the script

FAKE_PGREP = """#!/bin/bash
# fake pgrep: FAKE_RUNNING = space list of process names that "run"; FAKE_PATTERN=1 = the 1.22 path matches -f
case "$1" in
  -x)
    if [ "${2:-}" = "-u" ]; then
      for n in $FAKE_RUNNING; do case "$n" in es-de|AppRun) echo "$FAKE_ESDE_PID"; exit 0;; esac; done; exit 1
    fi
    for n in $FAKE_RUNNING; do [ "$n" = "$2" ] && { echo 4242; exit 0; }; done; exit 1 ;;
  -f) [ "${FAKE_PATTERN:-0}" = 1 ] && { echo 4243; exit 0; }; exit 1 ;;
esac
exit 1
"""


class Rig:
    def __init__(self):
        self.d = tempfile.mkdtemp()
        self.bin = os.path.join(self.d, "bin")
        os.makedirs(self.bin)
        self.xdg = os.path.join(self.d, "run")
        os.makedirs(self.xdg)
        self.calls = os.path.join(self.d, "stop.calls")
        self.stop = os.path.join(self.d, "stop")
        self.fc = os.path.join(self.d, "coin-dojo-fightcade")
        self.write(os.path.join(self.bin, "pgrep"), FAKE_PGREP)
        self.write(self.stop, '#!/bin/bash\necho "stop $(cat "$XDG_RUNTIME_DIR/coin-dojo/next-mode" 2>/dev/null)" >>%s\n' % self.calls)
        self.write(self.fc, "#!/bin/bash\n")
        self.log = os.path.join(self.d, "log")

    @staticmethod
    def write(path, text):
        open(path, "w").write(text)
        os.chmod(path, 0o755)

    def run(self, *args, running="", pattern=False, xdg=True, stop=True, fightcade=True, esde_pid=None):
        env = {"PATH": self.bin + os.pathsep + os.environ["PATH"], "HOME": self.d, "HK_ENTRY_LOG": self.log,
               "HK_ENTRY_STOP": self.stop if stop else os.path.join(self.d, "no-stop"),
               "HK_ENTRY_FIGHTCADE": self.fc if fightcade else os.path.join(self.d, "no-fc"),
               "FAKE_RUNNING": running, "FAKE_PATTERN": "1" if pattern else "0", "FAKE_ESDE_PID": str(esde_pid or 0)}
        if xdg:
            env["XDG_RUNTIME_DIR"] = self.xdg
        return subprocess.run(["bash", SCRIPT] + list(args), env=env, capture_output=True, text=True, timeout=30)

    def stops(self):
        return open(self.calls).read().splitlines() if os.path.exists(self.calls) else []

    def mode(self):
        p = os.path.join(self.xdg, "coin-dojo", "next-mode")
        return open(p).read() if os.path.exists(p) else None

    def done(self):
        shutil.rmtree(self.d)


def test_script_menu_and_fightcade():
    r = Rig()
    try:
        for mode in ("menu", "fightcade"):
            p = r.run(mode, "/data/nested/x/Entry.hks")
            assert p.returncode == 0, p.stderr
            assert r.mode() == mode + "\n", r.mode()
        assert r.stops() == ["stop menu", "stop fightcade"], r.stops()          # next-mode was written BEFORE the stop
        d = os.path.join(r.xdg, "coin-dojo")
        assert stat.S_IMODE(os.stat(d).st_mode) == 0o700
        assert sorted(os.listdir(d)) == ["next-mode"], os.listdir(d)            # no temp file left behind
    finally:
        r.done()


def test_script_without_helper_terms_esde():
    r = Rig()
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        p = r.run("menu", stop=False, running="es-de", esde_pid=sleeper.pid)
        assert p.returncode == 0 and r.mode() == "menu\n"
        sleeper.wait(timeout=5)
        assert sleeper.returncode == -15, sleeper.returncode                     # TERM, nothing harsher
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
        r.done()


def test_BROKEN_game_running_refuses():
    for running, pattern in (("retroarch", False), ("retroarch122", False), ("flycast", False), ("", True)):
        r = Rig()
        try:
            for mode in ("menu", "fightcade"):
                p = r.run(mode, running=running, pattern=pattern)
                assert p.returncode == 0, p.stderr
                assert "refused" in p.stderr and "quit the game" in p.stderr, p.stderr
                assert r.mode() is None and r.stops() == [], (running, r.mode(), r.stops())
                assert "refused" in open(r.log).read()
        finally:
            r.done()


def test_BROKEN_no_xdg_runtime_dir():
    r = Rig()
    try:
        p = r.run("menu", xdg=False)
        uid = os.getuid()
        if os.path.isdir("/run/user/%d" % uid):
            return                                      # a real session here: the fallback is legitimate
        assert p.returncode == 0 and "no XDG_RUNTIME_DIR" in p.stderr, p.stderr
        assert r.stops() == []
    finally:
        r.done()


def test_BROKEN_run_dir_unusable():
    r = Rig()
    try:
        open(os.path.join(r.xdg, "coin-dojo"), "w").write("a file where the dir should be\n")
        p = r.run("menu")
        assert p.returncode == 0 and "cannot create" in p.stderr and r.stops() == [], p.stderr
    finally:
        r.done()


def test_BROKEN_fightcade_not_installed():
    r = Rig()
    try:
        p = r.run("fightcade", fightcade=False)
        assert p.returncode == 0 and "not installed" in p.stderr, p.stderr
        assert r.mode() is None and r.stops() == []
        assert r.run("menu", fightcade=False).returncode == 0 and r.mode() == "menu\n"      # QUIT needs no Fightcade
    finally:
        r.done()


def test_BROKEN_bad_mode_is_usage():
    r = Rig()
    try:
        for args in ([], ["esde"], ["../x"]):
            p = r.run(*args)
            assert p.returncode == 2 and "usage" in p.stderr, (args, p.returncode)
            assert r.mode() is None and r.stops() == []
    finally:
        r.done()


if __name__ == "__main__":
    main(globals(), per_test=True)
