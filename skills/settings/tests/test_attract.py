#!/usr/bin/env python3
"""ATTRACT MODE page (#285), attract_apply.py, the SETTINGS home order and its docs.

    python3 skills/settings/tests/test_attract.py

Throwaway tree, fake process table and kill command: nothing touches a real ES-DE. Every destructive call (rmtree, rename,
replace, unlink, remove, rmdir, symlink) goes through GUARD, which refuses (and fails the run) when a path resolves
outside a registered scratch root. Broken cases: values outside the menu in the state file, a game or Fightcade running
(nothing written, nothing killed), a missing es_settings.xml, a choice that is not on the list, a REFERENCE table that
lists the home in another order (the check must flag it).
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APPDIR = os.path.dirname(HERE)
REPO = os.path.normpath(os.path.join(APPDIR, "..", ".."))
sys.path.insert(0, APPDIR)
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
import attract_apply as AA  # noqa: E402
import settings_core as core  # noqa: E402
from hktest import check, main  # noqa: E402


class Guard:
    """Fails (and refuses to run) a destructive call whose target resolves outside a scratch root. Looks at positional AND
    keyword arguments; os.symlink is judged by its link name; a dir_fd-relative name is allowed only inside the wrapped
    shutil.rmtree (whose root was checked first); real subprocess.run / Popen are replaced by a raiser."""
    roots, violations, depth = [], [], [0]

    @classmethod
    def inside(cls, p):
        ap = os.path.abspath(p)                               # ".." folded; the last name is NOT resolved (a symlink is judged
        r = os.path.join(os.path.realpath(os.path.dirname(ap)), os.path.basename(ap))   # by where it lives, not what it points at)
        return any(r == x or r.startswith(x + os.sep) for x in cls.roots)

    @classmethod
    def targets(cls, name, a, k):
        vals = list(a) + list(k.values())
        if name == "symlink":
            vals = [a[1]] if len(a) > 1 else [k["dst"]] if "dst" in k else []
        return [os.fsdecode(x) for x in vals if isinstance(x, (str, bytes, os.PathLike))]

    @classmethod
    def check(cls, name, a, k):
        if cls.depth[0] and k.get("dir_fd") is not None:
            return                                            # inside rmtree: names relative to a directory it opened
        for p in cls.targets(name, a, k):
            if not cls.inside(p):
                cls.violations.append((name, p))
                raise AssertionError("destructive %s outside the scratch root: %s" % (name, p))

    @classmethod
    def wrap(cls, mod, name):
        real = getattr(mod, name)

        def f(*a, **k):
            cls.check(name, a, k)
            if name == "rmtree":
                cls.depth[0] += 1
                try:
                    return real(*a, **k)
                finally:
                    cls.depth[0] -= 1
            return real(*a, **k)
        setattr(mod, name, f)

    @classmethod
    def no_spawn(cls):
        def boom(*a, **k):
            cls.violations.append(("spawn", str(a[:1])))
            raise AssertionError("a real process was started: %s" % (a[:1],))
        subprocess.run = subprocess.Popen = boom


tempfile.gettempdir()                    # its first call probes the temp dir with a create + unlink: do that before the guard
for _m, _n in ((shutil, "rmtree"), (os, "rename"), (os, "replace"), (os, "unlink"), (os, "remove"), (os, "rmdir"),
               (os, "symlink"), (os, "truncate")):
    Guard.wrap(_m, _n)
Guard.no_spawn()
PROC_DIR = tempfile.mkdtemp(prefix="hk-fakeproc-")      # an empty process table: no game (the real /proc is not read here)
AA.PROC = PROC_DIR
Guard.roots.append(os.path.realpath(PROC_DIR))

ES = ('<?xml version="1.0"?>\n<int name="MaxFPS" value="60" />\n<string name="ScraperPassword" value="s3cret" />\n'
      '<int name="ScreensaverTimer" value="600000" />\n<bool name="ScreensaverControls" value="true" />\n'
      '<string name="ScreensaverType" value="video" />\n')


class Env:
    def __init__(self, es=ES, **cfg):
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix="hk-attract-"))
        Guard.roots.append(self.dir)
        self.es = os.path.join(self.dir, "es_settings.xml")
        if es is not None:
            open(self.es, "w").write(es)
            os.chmod(self.es, 0o600)
        self.state = os.path.join(self.dir, "state.json")
        self.run_dir = os.path.join(self.dir, "run")
        os.makedirs(self.run_dir)
        self.proc = os.path.join(self.dir, "proc")
        os.makedirs(self.proc)
        self.cfg = dict(AA.DEFAULTS, es_settings=self.es, state=self.state, run_dir=self.run_dir, **cfg)
        self.kills = []

    def run(self, argv, **kw):
        self.kills.append((argv, open(self.es).read() if os.path.exists(self.es) else None))
        return type("R", (), dict(returncode=0))()

    def game(self, comm="retroarch"):
        os.makedirs(os.path.join(self.proc, "77"), exist_ok=True)
        open(os.path.join(self.proc, "77", "comm"), "w").write(comm + "\n")

    def save(self, **kw):
        core.save_state(self.state, **kw)

    def close(self):
        shutil.rmtree(self.dir)


def test_guard_itself_refuses_outside_paths():
    """The checker run on broken input: every one of these must be refused, and each is recorded then forgotten."""
    e = Env()
    n0 = len(Guard.violations)
    victim = tempfile.mkdtemp(prefix="hk-victim-")          # a real directory that is NOT a scratch root
    open(os.path.join(victim, "f"), "w").close()
    fd = os.open(victim, os.O_RDONLY)
    try:
        cases = [("rmtree", lambda: shutil.rmtree(victim)), ("rmdir", lambda: os.rmdir(victim)),
                 ("unlink", lambda: os.unlink(os.path.join(victim, "f"))),
                 ("rename src", lambda: os.rename(victim, os.path.join(e.dir, "moved"))),
                 ("rename dst", lambda: os.rename(os.path.join(e.dir, "es_settings.xml"), os.path.join(victim, "x"))),
                 ("a path with ..", lambda: shutil.rmtree(os.path.join(e.dir, "..", os.path.basename(victim)))),
                 ("kwarg", lambda: os.replace(os.path.join(e.dir, "es_settings.xml"), dst=os.path.join(victim, "y"))),
                 ("kwarg path", lambda: os.unlink(path=os.path.join(victim, "f"))),
                 ("dir_fd outside rmtree", lambda: os.unlink("f", dir_fd=fd)),
                 ("symlink dst", lambda: os.symlink(os.path.join(e.dir, "es_settings.xml"), os.path.join(victim, "ln"))),
                 ("truncate", lambda: os.truncate(os.path.join(victim, "f"), 0)),
                 ("a real spawn", lambda: subprocess.run(["true"])), ("a real Popen", lambda: subprocess.Popen(["true"]))]
        for why, fn in cases:
            try:
                fn()
                refused = False
            except AssertionError:
                refused = True
            except OSError:
                refused = False
            check(refused, "BROKEN: the guard refuses %s" % why)
        check(os.path.isdir(victim) and os.path.exists(os.path.join(victim, "f")) and not os.path.exists(os.path.join(victim, "ln")),
              "and nothing outside was touched")
        check(len(Guard.violations) - n0 == len(cases), "every refusal is recorded (%d)" % (len(Guard.violations) - n0))
        # the allowed side: a dir_fd call inside rmtree works (its root is inside), a symlink with a text target inside works
        sub = os.path.join(e.dir, "sub")
        os.makedirs(os.path.join(sub, "x"))
        shutil.rmtree(sub)
        check(not os.path.exists(sub), "rmtree inside the scratch root still works (dir_fd walk allowed)")
        os.symlink("/etc/hosts", os.path.join(e.dir, "ln"))
        os.unlink(os.path.join(e.dir, "ln"))
        check(True, "a symlink created inside the root is allowed whatever its text")
    finally:
        del Guard.violations[n0:]                            # these were the test's own, deliberate ones
        os.close(fd)
        Guard.roots.append(os.path.realpath(victim))         # only now is it ours to remove
        shutil.rmtree(victim)
        e.close()


def test_choices_and_desired():
    e = Env()
    try:
        check(AA.choices(e.cfg) == (True, 10, "video") and AA.desired(e.cfg) == (600000, "video"), "defaults: ON, 10 MIN, video")
        e.save(attract_enabled=False)
        check(AA.desired(e.cfg) == (0, "video"), "OFF is a timer of 0")
        e.save(attract_enabled=True, attract_minutes=30, attract_type="dim")
        check(AA.desired(e.cfg) == (1800000, "dim"), "30 MIN, dim")
        e.save(attract_enabled="yes", attract_minutes=7, attract_type="rm -rf")
        check(AA.choices(e.cfg) == (True, 10, "video"), "BROKEN: 'yes', 7 and a made-up type fall back to the defaults")
        e.save(attract_enabled=True, attract_minutes=True, attract_type=["dim"])
        check(AA.desired(e.cfg) == (600000, "video"), "BROKEN: a bool for minutes and a list for the type are ignored")
        off = dict(e.cfg, timer_ms=0)
        os.unlink(e.state)
        check(AA.desired(off)[0] == 0 and AA.choices(off)[0] is False, "hk_attract_timer_ms 0 = OFF until the owner turns it on")
        odd = dict(e.cfg, timer_ms=900000)
        check(AA.desired(odd)[0] == 900000, "an untouched timer that is not on the menu keeps its exact value")
        e.save(attract_minutes=5)
        check(AA.desired(odd)[0] == 300000, "and a menu choice replaces it")
    finally:
        e.close()


def test_set_keys_changes_only_its_lines():
    new = AA.set_keys(ES, {"ScreensaverTimer": ("int", "0"), "ScreensaverType": ("string", "dim")})
    check(new.count("\n") == ES.count("\n") and '<int name="ScreensaverTimer" value="0" />' in new
          and '<string name="ScreensaverType" value="dim" />' in new, "two lines changed")
    check(all(ln in new for ln in ES.splitlines() if "Timer" not in ln and "Type" not in ln), "every other line is as it was")
    miss = AA.set_keys('<int name="MaxFPS" value="60" />', {"ScreensaverTimer": ("int", "1")})
    check(miss.endswith('<int name="ScreensaverTimer" value="1" />\n') and "MaxFPS" in miss, "a missing key is appended")


def test_apply_writes_then_restarts():
    e = Env()
    try:
        e.save(attract_enabled=False, attract_type="black")
        ok, msg = AA.apply(e.cfg, run=e.run, proc=e.proc)
        check(ok and len(e.kills) == 1 and e.kills[0][0] == ["pkill", "-9", "-x", "AppRun"], "kill command runs once: %s %s" % (ok, msg))
        check('value="0"' in e.kills[0][1] and 'value="black"' in e.kills[0][1], "the file was written BEFORE ES-DE was killed")
        check(open(os.path.join(e.run_dir, "next-mode")).read() == "esde\n", "the session loop is asked for ES-DE")
        check(stat.S_IMODE(os.stat(e.es).st_mode) == 0o600 and "s3cret" in open(e.es).read(), "mode 0600 and the scraper password kept")
        check(not AA.pending(e.cfg), "nothing pending afterwards")
        check([n for n in os.listdir(e.dir) if n.startswith(".")] == [], "no temp file left")
        e.save(attract_enabled=True, attract_minutes=60)
        check(AA.pending(e.cfg) is True, "a new choice is pending until applied")
    finally:
        e.close()


def test_BROKEN_guards_write_nothing():
    e = Env()
    try:
        e.save(attract_enabled=False)
        before = open(e.es).read()
        e.game()
        ok, msg = AA.apply(e.cfg, run=e.run, proc=e.proc)
        check(not ok and "GAME" in msg and e.kills == [] and open(e.es).read() == before,
              "BROKEN: a game running -> nothing written, nothing killed: %s" % msg)
        shutil.rmtree(os.path.join(e.proc, "77"))
        open(os.path.join(e.run_dir, "current-mode"), "w").write("fightcade\n")
        ok, msg = AA.apply(e.cfg, run=e.run, proc=e.proc)
        check(not ok and "FIGHTCADE" in msg and e.kills == [] and open(e.es).read() == before,
              "BROKEN: Fightcade -> nothing written, nothing killed: %s" % msg)
    finally:
        e.close()
    e = Env(es=None)
    try:
        ok, msg = AA.apply(e.cfg, run=e.run, proc=e.proc)
        check(not ok and "CANNOT READ" in msg and e.kills == [], "BROKEN: no es_settings.xml -> told, ES-DE not killed: %s" % msg)
    finally:
        e.close()


def make_app(e, spawned):
    at = core.Attract(None, e.state, cfg=e.cfg, spawn=spawned.append)
    host = type("H", (), {"current": lambda s: "cab"})()
    app = core.App(core.NM("/bin/false", log=core.Log(None)), host, core.Log(None), None, [core.AttractPage(at)])
    app.pending = None                                   # the Wi-Fi status read the app queues at start
    return app, at


def pick(app, start):
    app.top["sel"] = [i for i, (lab, _w) in enumerate(app.top["items"]) if lab.startswith(start)][0]
    app.handle("ok")


def test_page_flow():
    e = Env()
    spawned = []
    try:
        app, at = make_app(e, spawned)
        pick(app, "ATTRACT MODE")
        labels = [lab for lab, _w in app.top["items"]]
        check(labels == ["TURN OFF", "START AFTER:  10 MIN", "SHOW:  VIDEO", "APPLY NOW  (RELOADS ES-DE)", "BACK"]
              or labels == ["TURN OFF", "START AFTER:  10 MIN", "SHOW:  VIDEO", "BACK"], "page rows: %s" % labels)
        pick(app, "START AFTER")
        check([lab for lab, _w in app.top["items"]][:4] == ["5 MIN", "10 MIN   (NOW)", "30 MIN", "60 MIN"], "minute choices")
        pick(app, "30 MIN")
        check(app.top["id"] == "confirm" and AA.choices(e.cfg) == (True, 30, "video"), "saved, then asks to apply")
        check(app.top["sel"] == 1, "NO is the default")
        app.handle("ok")                                     # NO
        check(app.top["id"] == "attract" and spawned == [] and AA.pending(e.cfg), "NO: saved, not applied, page offers APPLY NOW")
        check(any(lab.startswith("APPLY NOW") for lab, _w in app.top["items"]) and "APPLIES WHEN ES-DE RELOADS" in app.view()["text"],
              "and says so")
        pick(app, "SHOW")
        pick(app, "SLIDESHOW")
        app.top["sel"] = 0
        app.handle("ok")                                     # YES
        check(len(spawned) == 1 and spawned[0][-3] == "--delay" and spawned[0][-4] == "apply" and app.quit is True,
              "YES: the detached apply is started and the app closes: %s" % spawned)
        check(AA.choices(e.cfg) == (True, 30, "slideshow"), "slideshow saved")
        check(at.set_minutes(7)[0] is False and at.set_type("rm")[0] is False and AA.choices(e.cfg) == (True, 30, "slideshow"),
              "BROKEN: 7 MIN and an unknown type are refused and change nothing")
        e.game()
        at._cfg = dict(e.cfg, game_procs=["retroarch"])
        spawned.clear()
        real = AA.game_running
        AA.game_running = lambda cfg, proc="/proc": real(cfg, e.proc)
        try:
            check(at.apply()[0] is False and spawned == [], "BROKEN: a game running -> no apply is started")
        finally:
            AA.game_running = real
    finally:
        e.close()


def test_off_turns_the_timer_off():
    e = Env()
    spawned = []
    try:
        app, at = make_app(e, spawned)
        pick(app, "ATTRACT MODE")
        pick(app, "TURN OFF")
        check(AA.desired(e.cfg)[0] == 0 and app.top["id"] == "confirm", "OFF saved (timer 0), then asks to apply")
        app.handle("ok")
        check(app.view()["items"][0] == "TURN ON" and app.view()["cards"][0]["value"] == "OFF", "the row offers TURN ON, the card says OFF")
    finally:
        e.close()


def home_titles(app):
    return [lab.split(":")[0].strip() for lab, _w in app.stack[0]["items"]]


def reference_home_row(text):
    m = re.search(r"^\| SETTINGS \(first page\) \| [^|]*; ([^|]*) \|$", text, re.M)
    return [x.strip() for x in m.group(1).split(",")] if m else None


def test_home_order_matches_the_reference():
    e = Env()
    try:
        bench = core.Benchmark(os.path.join(e.dir, "s.json"), (), lambda: False)
        host = type("H", (), {"current": lambda s: "cab"})()
        at = core.Attract(None, os.path.join(e.dir, "s.json"), cfg=e.cfg, spawn=lambda argv: None)
        pages = [core.CheevosPage(core.Cheevos(None, os.path.join(e.dir, "s.json"), core.CHEEVOS_API, lambda: False)),
                 core.TimeZonePage(core.TimeZone(runner=lambda argv, t: (1, "", ""), state=os.path.join(e.dir, "s.json"))), core.AttractPage(at),
                 core.PasswordPage(core.Password("/bin/false")), core.ListsPage(core.Lists(None, os.path.join(e.dir, "s.json")))]
        app = core.App(core.NM("/bin/false", log=core.Log(None)), host, core.Log(None), bench, pages)
        got = home_titles(app)
        ref = open(os.path.join(REPO, "docs", "REFERENCE.md"), encoding="utf-8").read()
        row = reference_home_row(ref)
        check(got == ["WI-FI", "CABINET NAME", "PASSWORD", "GAME LISTS", "ATTRACT MODE", "SHOW FPS", "RETROACHIEVEMENTS", "TIME ZONE", "BACK"],
              "the app's home order: %s" % got)
        check(row == got, "docs/REFERENCE.md's SETTINGS row lists the same labels in the same order: %s" % row)
        for lab in got[3:-1]:
            check(re.search(r"^\| %s \|" % re.escape(lab), ref, re.M) is not None, "REFERENCE has a row for %s" % lab)
        section = ref.split("## SETTINGS app")[1].split("\n## ")[0]
        check("BENCHMARK MODE" not in section, "no BENCHMARK MODE label left in the SETTINGS section")
        check(reference_home_row(ref.replace("ATTRACT MODE, SHOW FPS,", "SHOW FPS, ATTRACT MODE,")) != got,
              "BROKEN: a REFERENCE row in another order is flagged")
        check(reference_home_row(ref.replace("SHOW FPS,", "BENCHMARK MODE,")) != got, "BROKEN: the old label is flagged")

        class Odd(core.Page):
            id, title = "odd", "ODD"
        app2 = core.App(core.NM("/bin/false", log=core.Log(None)), host, core.Log(None), bench, pages + [Odd()])
        check(home_titles(app2)[-2:] == ["ODD", "BACK"], "a page whose id is not in HOME_ORDER goes last")
    finally:
        e.close()


def test_BROKEN_config_coercion():
    """(#285 M2) Whatever the entry file says, the helper ends up with a number and lists of strings."""
    e = Env()
    try:
        def cfg_of(att):
            entry = os.path.join(e.dir, "entry.hks")
            open(entry, "w").write(json.dumps({"attract": att}))
            return AA.load_config(entry)
        check(cfg_of({"timer_ms": "900000"})["timer_ms"] == 900000, "a digit string becomes the number")
        for bad in ("fast", "-5", "9" * 30, None, 1.5, True, [600000], 99999999999):
            check(cfg_of({"timer_ms": bad})["timer_ms"] == 600000, "BROKEN: timer_ms %r falls back to the default" % (bad,))
        for key in ("game_procs", "game_patterns"):
            for bad in ("retroarch", None, [1], ["a", None], {"a": 1}):
                check(cfg_of({key: bad})[key] == AA.DEFAULTS[key], "BROKEN: %s %r falls back to the default list" % (key, bad))
        check(cfg_of({"game_procs": ["retroarch", "x"]})["game_procs"] == ["retroarch", "x"], "a real list of strings is kept")
        check(cfg_of({"type": "rm -rf"})["type"] == "video" and cfg_of({"es_settings": 5})["es_settings"] == AA.DEFAULTS["es_settings"],
              "BROKEN: an unknown type and a non-string path fall back")
        check("esde_kill" not in AA.DEFAULTS and cfg_of({"esde_kill": "rm -rf /"}).get("esde_kill") == "rm -rf /" and AA.KILL == ["pkill", "-9", "-x", "AppRun"],
              "the kill command is a constant; a key in the entry file is never read")
    finally:
        e.close()


def test_BROKEN_es_settings_file_rules():
    e = Env()
    try:
        os.chmod(e.es, 0o644)
        e.save(attract_enabled=False)
        ok, _m = AA.apply(e.cfg, run=e.run, proc=e.proc)
        check(ok and stat.S_IMODE(os.stat(e.es).st_mode) == 0o600, "(L1) the file ends up a flat 0600, whatever it was")
    finally:
        e.close()
    e = Env()
    try:
        real = os.path.join(e.dir, "real.xml")
        os.rename(e.es, real)
        os.symlink(real, e.es)
        before = open(real).read()
        e.save(attract_enabled=False)
        ok, msg = AA.apply(e.cfg, run=e.run, proc=e.proc)
        check(not ok and "CANNOT READ" in msg and e.kills == [] and open(real).read() == before and os.path.islink(e.es),
              "BROKEN (L2): a symlinked es_settings.xml is refused, nothing written, nothing killed: %s" % msg)
    finally:
        e.close()
    e = Env(es=None)
    try:
        os.mkdir(e.es)
        ok, msg = AA.apply(e.cfg, run=e.run, proc=e.proc)
        check(not ok and e.kills == [], "BROKEN (L2): a directory in its place is refused: %s" % msg)
    finally:
        e.close()


def test_BROKEN_l4_fails_closed():
    e = Env()
    try:
        check(AA.game_running(e.cfg, os.path.join(e.dir, "missing")) is True, "BROKEN: an unreadable process table counts as a game")
        check(AA.game_running(dict(e.cfg, game_procs=None), e.proc) is True, "BROKEN: a bad list (TypeError) counts as a game")
        check(AA.game_running(dict(e.cfg, game_patterns=["("]), e.proc) is False, "a bad regex is skipped")
        e.save(attract_enabled=False)
        before = open(e.es).read()
        ok, msg = AA.apply(e.cfg, run=e.run, proc=os.path.join(e.dir, "missing"))
        check(not ok and e.kills == [] and open(e.es).read() == before, "BROKEN: apply with no readable process table writes and kills nothing: %s" % msg)
    finally:
        e.close()


def test_zz_the_guard_saw_nothing():
    check(Guard.violations == [], "no destructive call or spawn outside the scratch roots in this whole run: %s" % Guard.violations)


if __name__ == "__main__":
    main(globals())
