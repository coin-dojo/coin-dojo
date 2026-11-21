#!/usr/bin/env python3
"""Tests for the first-launch screen (#267: files/hk_fc_firstrun.py) and the launcher's first-launch step
(files/coin-dojo-fightcade). Plain python3; the engine is a fake here (its own tests: test_fetch.py).

    python3 ansible/roles/fightcade/tests/test_firstrun.py

State: consent shows only when something must be downloaded; START (or LK/Enter) begins, SELECT (or Esc) cancels;
an error waits for any button; SELECT cancels a running download. Frames: consent / working / error render a
1440x1080 PNG with the brand kit, and the consent frame carries the size, the source and Fightcade's terms. Live loop
(SDL dummy driver): START installs (rc 0), SELECT cancels before the engine starts (rc 4), an engine error ends at rc 1
after the wait, a local-only repair skips the consent, TERM stops the engine's whole process group (rc 143).
Launcher (BROKEN cases included): --check 0 goes straight on, a cancel exits 0 without starting anything, a failed
install exits 2, a broken check exits 2, `--check` mode never runs the first launch, deploy mode never runs it.
"""
import json
import os
import signal
import struct
import subprocess
import sys
import tempfile
import time

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(ROLE)))
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
from hktest import check, main, skip  # noqa: E402

FILES = os.path.join(ROLE, "files")
UI = os.path.join(FILES, "hk_fc_firstrun.py")
LAUNCHER = os.path.join(FILES, "coin-dojo-fightcade")
UI_LIB = os.path.join(REPO, "skills", "settings")
sys.path.insert(0, FILES)
import hk_fc_firstrun as fr  # noqa: E402

PLAN = {"downloads_needed": True, "download_bytes": 306511333, "total_bytes": 306511333, "need_mib": 2129,
        "free_mib": 9000, "pieces": [
            {"name": "wine", "title": "Wine 10.0", "host": "github.com", "size": 65074156, "installed": False, "have": 0},
            {"name": "electron", "title": "Electron 9.1.0", "host": "github.com", "size": 76823422, "installed": False, "have": 0},
            {"name": "fightcade", "title": "Fightcade 2.1.45", "host": "web.fightcade.com", "size": 164613755,
             "installed": False, "have": 0}]}

FAKE_ENGINE = r'''#!/usr/bin/env python3
import json, os, sys, time
mode = os.environ.get("FAKE_MODE", "ok")
d = os.environ["FAKE_DIR"]
if "--plan" in sys.argv:
    p = json.load(open(os.path.join(d, "plan.json")))
    print(json.dumps(p)); sys.exit(0)
open(os.path.join(d, "engine-started"), "w").write(str(os.getpid()))
ev = lambda **k: (print(json.dumps(k)), sys.stdout.flush())
ev(ev="plan", downloads_needed=True, download_bytes=30, total_bytes=30, need_mib=1, free_mib=9, pieces=[])
if mode == "hang":
    child = os.fork()
    if child == 0:
        open(os.path.join(d, "child.pid"), "w").write(str(os.getpid())); time.sleep(600); sys.exit(0)
    time.sleep(600)
for i in range(1, 4):
    ev(ev="download", name="wine", title="Wine 10.0", host="github.com", done=i * 10, total=30, all_done=i * 10, all_total=30)
    time.sleep(0.05)
if mode == "error":
    ev(ev="error", kind="network", text="could not reach github.com to download Wine 10.0"); sys.exit(4)
if mode == "crash":
    sys.exit(9)
ev(ev="step", name="prefix", text="Preparing Wine (about a minute)")
ev(ev="done"); sys.exit(0)
'''


# ---- state ------------------------------------------------------------------------------------------------------
def test_state_machine():
    s = fr.FirstRun(PLAN)
    check(s.state == "consent", "downloads needed: the consent screen first")
    check(s.handle("up") is None and s.handle("ok") == "begin" and s.state == "working", "LK/START begins the download")
    for act in ("start", "enter"):
        s = fr.FirstRun(PLAN)
        check(s.handle(act) == "begin", "%s begins" % act)
    for act in ("exit", "esc"):
        s = fr.FirstRun(PLAN)
        check(s.handle(act) == "cancel" and s.state == "cancelled", "SELECT (%s) cancels on the consent screen" % act)
    s = fr.FirstRun(dict(PLAN, downloads_needed=False))
    check(s.state == "working", "a local-only repair (no download) skips the consent")
    s = fr.FirstRun(PLAN)
    s.handle("start")
    check(s.handle("start") is None and s.state == "working", "START during the download does nothing")
    check(s.handle("exit") == "cancel", "SELECT cancels a running download")
    s = fr.FirstRun(PLAN)
    s.handle("start")
    check(s.handle("back") == "cancel", "MK cancels too (an older settings_app.py whose SELECT alone sends nothing; VM 2026-11-19)")
    s = fr.FirstRun(PLAN)
    s.handle("start")
    s.on_event({"ev": "download", "title": "Wine 10.0", "done": 5, "total": 10, "all_done": 5, "all_total": 30}, 1.0)
    s.on_event({"ev": "download", "title": "Wine 10.0", "done": 10, "total": 10, "all_done": 10, "all_total": 30}, 2.5)
    check(s.dl["done"] == 10 and s.rate and abs(s.rate - 5 / 1.5) < 0.01, "progress and a download rate")
    s.on_event({"ev": "step", "text": "Unpacking Wine 10.0"})
    check(s.step == "Unpacking Wine 10.0", "steps are shown")
    s.on_event({"ev": "error", "kind": "network", "text": "no internet"})
    check(s.state == "error" and s.error == "no internet", "an error event shows its text")
    check(s.handle("up") is None and s.handle("ok") == "leave", "any button leaves the error (a stick move does not)")
    s = fr.FirstRun(PLAN)
    s.handle("start")
    s.engine_ended(9)
    check(s.state == "error" and "rc 9" in s.error, "BROKEN: an engine that dies without an event is an error")
    s = fr.FirstRun(PLAN)
    s.handle("start")
    s.on_event({"ev": "done"})
    check(s.state == "done", "done")
    check(fr.headline(PLAN) == "Downloading Fightcade, Wine and Electron (about 307 MB)", "the headline: %s" % fr.headline(PLAN))
    resume = dict(PLAN, download_bytes=82306878, todo_bytes=306511333)
    check(fr.headline(resume) == "Downloading Fightcade, Wine and Electron (about 82 MB left of 307 MB)",
          "a resume: what is left of the same total the bar shows: %s" % fr.headline(resume))
    repin = dict(PLAN, download_bytes=164613755, todo_bytes=164613755)
    check(fr.headline(repin) == "Downloading Fightcade, Wine and Electron (about 165 MB)",
          "only Fightcade to fetch (re-pin): the total is that piece, like the bar: %s" % fr.headline(repin))
    broken = dict(PLAN, download_bytes=82306878, todo_bytes=306511333)
    check("82 MB)" not in fr.headline(broken), "BROKEN: a resume never shows the remainder as if it were the total")
    check("fightcade.com" not in fr.headline(PLAN), "review #3: the headline names no single host (Wine and Electron come from github.com)")
    hosts = sorted({p["host"] for p in PLAN["pieces"]})
    check(hosts == ["github.com", "web.fightcade.com"], "the per-file rows carry each file's own host: %s" % hosts)
    check("Fightcade's own terms apply" in fr.LEGAL and "fightcade.com" in fr.LEGAL, "the consent names Fightcade's terms")


# ---- frames -----------------------------------------------------------------------------------------------------
def _png_size(path):
    with open(path, "rb") as fh:
        head = fh.read(24)
    return struct.unpack(">II", head[16:24]) if head[:8] == b"\x89PNG\r\n\x1a\n" else None


def _env(**kw):
    e = dict(os.environ, HK_FC_UI_LIB=UI_LIB, HOME=tempfile.mkdtemp())
    e.update(kw)
    return e


def test_frames():
    try:
        sys.path.insert(0, UI_LIB)
        import settings_app as sa  # noqa: E402
        sa.Painter(sa.brand.load())
    except (ImportError, OSError) as e:
        skip("first-launch frames: no cairo/FreeType here (%s)" % e)
        return
    d = tempfile.mkdtemp()
    plan = os.path.join(d, "plan.json")
    json.dump(PLAN, open(plan, "w"))
    dl = json.dumps({"ev": "download", "title": "Wine 10.0", "host": "github.com", "done": 30000000, "total": 65074156,
                     "all_done": 30000000, "all_total": 306511333})
    for state, extra in (("consent", []), ("working", ["--event", dl]),
                         ("error", ["--event", json.dumps({"ev": "error", "text": "could not reach github.com"})]),
                         ("done", [])):
        out = os.path.join(d, state + ".png")
        r = subprocess.run([sys.executable, UI, "--png", out, "--state", state, "--plan", plan] + extra,
                           capture_output=True, text=True, env=_env())
        check(r.returncode == 0 and _png_size(out) == (1440, 1080), "frame %s: a 1440x1080 PNG (rc %d %s)" % (state, r.returncode, r.stderr[-200:]))
    r = subprocess.run([sys.executable, UI, "--png", os.path.join(d, "x.png")], capture_output=True, text=True,
                       env=_env(HK_FC_UI_LIB=os.path.join(d, "nowhere")))
    check(r.returncode == 3, "BROKEN: no drawing kit = exit 3 (the launcher then refuses with a message)")
    r = subprocess.run([sys.executable, UI, "--bogus"], capture_output=True, text=True, env=_env())
    check(r.returncode == 2, "BROKEN: bad usage = exit 2")


# ---- live loop (SDL dummy) --------------------------------------------------------------------------------------
class Live:
    def __init__(self, mode="ok", plan=None):
        self.d = tempfile.mkdtemp()
        self.engine = os.path.join(self.d, "engine")
        open(self.engine, "w").write(FAKE_ENGINE)
        json.dump(plan or PLAN, open(os.path.join(self.d, "plan.json"), "w"))
        self.mode = mode

    def env(self):
        return _env(HK_FC_FETCH=self.engine, FAKE_MODE=self.mode, FAKE_DIR=self.d, SDL_VIDEODRIVER="dummy")

    def run(self, actions, timeout=40):
        return subprocess.run([sys.executable, UI, "--actions", actions, "--error-wait", "0.3"], capture_output=True,
                              text=True, env=self.env(), timeout=timeout)

    def started(self):
        return os.path.exists(os.path.join(self.d, "engine-started"))


def _sdl_ok():
    r = Live().run("exit", timeout=30)
    return r.returncode == 4


def test_live_loop():
    try:
        sys.path.insert(0, UI_LIB)
        import settings_app as sa  # noqa: E402
        sa.Painter(sa.brand.load())
        sdl = _sdl_ok()
    except (ImportError, OSError, subprocess.SubprocessError):
        sdl = False
    if not sdl:
        skip("first-launch live loop: SDL2 with the dummy video driver not available here")
        return
    lv = Live("ok")
    r = lv.run("start")
    check(r.returncode == 0 and lv.started(), "START on the consent screen installs: rc 0 (rc %d %s)" % (r.returncode, r.stderr[-300:]))
    lv = Live("ok")
    r = lv.run("exit")
    check(r.returncode == 4 and not lv.started(), "SELECT on the consent screen cancels: rc 4, the engine never ran")
    lv = Live("error")
    r = lv.run("start")
    check(r.returncode == 1, "BROKEN: an engine error ends at rc 1 after the error wait (rc %d)" % r.returncode)
    lv = Live("crash")
    r = lv.run("start")
    check(r.returncode == 1, "BROKEN: an engine crash ends at rc 1 (rc %d)" % r.returncode)
    lv = Live("ok", dict(PLAN, downloads_needed=False))
    r = lv.run("up")
    check(r.returncode == 0 and lv.started(), "a local-only repair starts without the consent (rc %d)" % r.returncode)
    # TERM during a hung download: the whole engine process group goes
    lv = Live("hang")
    p = subprocess.Popen([sys.executable, UI, "--actions", "start"], env=lv.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    child = os.path.join(lv.d, "child.pid")
    t = time.time()
    while not os.path.exists(child) and time.time() - t < 20:
        time.sleep(0.1)
    time.sleep(0.3)
    p.send_signal(signal.SIGTERM)
    rc = p.wait(timeout=20)
    eng = int(open(os.path.join(lv.d, "engine-started")).read() or 0) if lv.started() else 0
    cpid = int(open(child).read()) if os.path.exists(child) else 0
    time.sleep(0.3)

    def alive(pid):
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    check(rc == 143, "TERM ends the screen with rc 143 (rc %d)" % rc)
    check(eng and cpid and not alive(eng) and not alive(cpid), "TERM stops the engine AND its children (process group)")


# ---- the launcher's first-launch step ----------------------------------------------------------------------------
def _launcher(d, install="first-launch", check_rc=10, ui_rc=0, arg=None):
    conf = os.path.join(d, "fc.conf")
    log = os.path.join(d, "calls.log")
    fetch = os.path.join(d, "fetch")
    ui = os.path.join(d, "ui.py")
    open(fetch, "w").write("import sys\nopen(%r, 'a').write('fetch ' + ' '.join(sys.argv[1:]) + '\\n')\nsys.exit(%d)\n" % (log, check_rc))
    open(ui, "w").write("import sys, os\nopen(%r, 'a').write('ui ' + os.environ.get('HK_FC_FETCH_MANIFEST', '') + '\\n')\nsys.exit(%d)\n" % (log, ui_rc))
    lines = ["HK_FC_INSTALL=%s" % install, "HK_FC_FETCH=%s" % fetch, "HK_FC_FIRSTRUN=%s" % ui,
             "HK_FC_FETCH_MANIFEST=%s/m.json" % d, "HK_FC_UI_LIB=%s" % UI_LIB, "HK_FC_LOGS=%s" % d]
    for v in ("BOX64", "WINE", "ELECTRON", "BINDIR", "TREE", "BOX64_RC", "APP", "INI_BASE", "BOX64_LIB", "PREFIX",
              "PREFIX_STAMP", "CSS", "CSS_SHA256", "JS", "JS_SHA256", "PROFILE", "BINFMT"):
        lines.append("HK_FC_%s=%s/none" % (v, d))
    open(conf, "w").write("\n".join(lines) + "\n")
    env = dict(os.environ, COIN_DOJO_FIGHTCADE_CONF=conf)
    r = subprocess.run(["/bin/bash", LAUNCHER] + ([arg] if arg else []), capture_output=True, text=True, env=env, timeout=30)
    calls = open(log).read() if os.path.exists(log) else ""
    return r.returncode, r.stderr, calls


def test_launcher_first_launch():
    with tempfile.TemporaryDirectory() as d:
        rc, err, calls = _launcher(d, check_rc=0)
        check(calls == "fetch --check\n" and rc == 2 and "missing artifact" in err,
              "installed (--check 0): no screen, straight to the install check (rc %d, calls %r)" % (rc, calls))
    with tempfile.TemporaryDirectory() as d:
        rc, err, calls = _launcher(d, check_rc=10, ui_rc=4)
        check(rc == 0 and "ui %s/m.json" % d in calls and "cancelled by the player" in err and "missing artifact" not in err,
              "cancel on the screen: exit 0 (back to the menu, no failure), nothing started (rc %d)" % rc)
    with tempfile.TemporaryDirectory() as d:
        rc, err, calls = _launcher(d, check_rc=10, ui_rc=1)
        check(rc == 2 and "first launch did not finish" in err, "BROKEN: a failed install: exit 2, never the lobby (rc %d)" % rc)
    with tempfile.TemporaryDirectory() as d:
        rc, err, calls = _launcher(d, check_rc=11, ui_rc=0)
        check("ui " in calls and rc == 2 and "missing artifact" in err, "installed by the screen: the launcher goes on to its own install check")
    with tempfile.TemporaryDirectory() as d:
        rc, err, calls = _launcher(d, check_rc=2)
        check(rc == 2 and "ui " not in calls and "--check failed (rc 2)" in err, "BROKEN: a broken engine check: exit 2, no screen")
    with tempfile.TemporaryDirectory() as d:
        rc, err, calls = _launcher(d, check_rc=10, arg="--check")
        check(calls == "" and rc == 2, "`coin-dojo-fightcade --check` never runs the first launch (read only)")
    with tempfile.TemporaryDirectory() as d:
        rc, err, calls = _launcher(d, install="deploy", check_rc=10)
        check(calls == "" and rc == 2, "deploy mode (the maintainer's cabinet) never runs the first launch")
    r = subprocess.run(["/bin/bash", "-n", LAUNCHER], capture_output=True, text=True)
    check(r.returncode == 0, "the launcher passes bash -n")


if __name__ == "__main__":
    main(globals())
