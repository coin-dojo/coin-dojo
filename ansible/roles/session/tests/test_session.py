#!/usr/bin/env python3
"""Tests for the `session` role (#209): the session loop, coin-dojo-cleanup, the stop hook, the .bash_profile line.

The scripts run for real against fakes (an ES-DE, a boot menu and a Fightcade mode that follow a plan file and log
each start), in a temp dir, with short timings. Bash 3.2 (macOS /bin/bash) and bash 5 alike. Broken cases: a hung
emulator that ignores TERM is still killed; a stale or unknown next-mode = menu; a boot menu crash / exit 3 /
missing app = esde; a missing coin-dojo-fightcade = esde; 3 kills in 60 s = back-off to the menu; SIGTERM = no
respawn; a bad session script = the profile line falls back to plain ES-DE.

    python3 ansible/roles/session/tests/test_session.py
"""
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANSIBLE = os.path.dirname(os.path.dirname(ROLE))
REPO = os.path.dirname(ANSIBLE)
FILES = os.path.join(ROLE, "files")
SESSION = os.path.join(FILES, "coin-dojo-session")
CLEANUP = os.path.join(FILES, "coin-dojo-cleanup")
STOP = os.path.join(FILES, "coin-dojo-session-stop")
BASH = "/bin/bash"
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
from hktest import check, main, skip  # noqa: E402


def _read(*parts, base=ROLE):
    with open(os.path.join(base, *parts), encoding="utf-8") as fh:
        return fh.read()


# One fake for ES-DE, the boot menu app and Fightcade. Each start pops the next line of <name>.plan:
#   exit N | sig N (kill itself) | wait (until TERM) | ignore (ignores TERM) | print WORD (stdout, exit 0)
#   nap S (sleep S s, exit 0) | req WORD [old] (write next-mode, `old` = mtime one hour back, then exit 0)
# and appends "<name> <argv...> | env" to trace. An empty plan = wait.
FAKE = r'''#!/usr/bin/env python3
import os, resource, signal, sys, time
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))   # the crash cases (abort, segv) leave no core file behind
name, d = sys.argv[1], os.environ["HKT_DIR"]
plan = os.path.join(d, name + ".plan")
lines = open(plan).read().splitlines() if os.path.exists(plan) else []
act = lines.pop(0) if lines else "wait"
open(plan, "w").write("\n".join(lines) + ("\n" if lines else ""))
with open(os.path.join(d, "trace"), "a") as f:
    f.write("%s %s | msg=%s\n" % (name, " ".join(sys.argv[2:]), os.environ.get("COIN_DOJO_BOOTMENU_MESSAGE", "")))
w = act.split()
if w[0] == "exit":
    sys.exit(int(w[1]))
if w[0] == "sig":
    os.kill(os.getpid(), int(w[1]))
    time.sleep(5)
if w[0] == "print":
    print(w[1])
    sys.exit(0)
if w[0] == "req":
    p = os.path.join(os.environ["HKT_RUN"], "next-mode")
    open(p, "w").write(w[1] + "\n")
    if len(w) > 2:
        t = time.time() - 3600
        os.utime(p, (t, t))
    sys.exit(0)
if w[0] == "nap":
    time.sleep(float(w[1]))
    sys.exit(0)
if w[0] == "ignore":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
open(os.path.join(d, name + ".pid"), "w").write(str(os.getpid()))
while True:
    time.sleep(0.05)
'''


class Rig:
    """A temp cabinet: fakes, a config, a run dir. start() runs the loop; trace() reads what started."""

    def __init__(self, has_fightcade=True, bootmenu=True, conf_extra=""):
        self.d = tempfile.mkdtemp(prefix="hk-session-")
        self.run = os.path.join(self.d, "run")
        self.token = "hkt%d" % os.getpid()
        fake = os.path.join(self.d, "fake.py")
        open(fake, "w").write(FAKE)
        for name, on in (("esde", True), ("fightcade", has_fightcade)):
            if on:
                p = os.path.join(self.d, name)
                open(p, "w").write("#!/bin/sh\nexec python3 %s %s \"$@\"\n" % (fake, name))
                os.chmod(p, 0o755)
        self.menu_app = os.path.join(self.d, "bootmenu_app.py")
        if bootmenu:
            open(self.menu_app, "w").write("import os, sys\nsys.argv[0:1] = ['x', 'menu']\nexec(open(%r).read())\n" % fake)
        self.conf = os.path.join(self.d, "session.conf")
        open(self.conf, "w").write(
            "HK_RUN_DIR=%s\nHK_LOG=%s/session.log\nHK_BOOTMENU_PY=python3\nHK_BOOTMENU_APP=%s\nHK_COUNTDOWN=5\n"
            "HK_ESDE=(%s/esde --resolution 1440 1080)\nHK_FIGHTCADE=%s/fightcade\nHK_CLEANUP=%s\n"
            "HK_FIGHTCADE_UNIT=hk-test-none.scope\nHK_CLEANUP_NAMES=()\nHK_CLEANUP_PATTERNS=(%s)\n"
            "HK_KILL_GRACE=1\nHK_STOP_GRACE=1\nHK_BACKOFF_N=3\nHK_BACKOFF_WINDOW=60\nHK_BACKOFF_SLEEP=1\n%s"
            % (self.run, self.d, self.menu_app, self.d, self.d, CLEANUP, self.token + "-hung", conf_extra))
        self.env = dict(os.environ, COIN_DOJO_SESSION_CONF=self.conf, HKT_DIR=self.d, HKT_RUN=self.run, HOME=self.d)
        self.proc = None

    def plan(self, name, *acts):
        open(os.path.join(self.d, name + ".plan"), "w").write("".join(a + "\n" for a in acts))

    def start(self):
        self.proc = subprocess.Popen([BASH, SESSION], env=self.env, cwd=self.d, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        return self

    def trace(self):
        p = os.path.join(self.d, "trace")
        return [ln.rstrip("\n") for ln in open(p)] if os.path.exists(p) else []

    def wait_trace(self, n, timeout=15.0):
        end = time.time() + timeout
        while time.time() < end:
            if len(self.trace()) >= n:
                time.sleep(0.3)
                return True
            time.sleep(0.05)
        return False

    def current(self):
        try:
            return open(os.path.join(self.run, "current-mode")).read().strip()
        except OSError:
            return None

    def log(self):
        try:
            return open(os.path.join(self.d, "session.log")).read()
        except OSError:
            return ""

    def stop_hook(self):
        return subprocess.run([BASH, STOP], env=self.env, capture_output=True, text=True, timeout=10)

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        for name in ("esde", "fightcade", "menu"):
            try:
                os.kill(int(open(os.path.join(self.d, name + ".pid")).read()), signal.SIGKILL)
            except (OSError, ValueError):
                pass
        shutil.rmtree(self.d, ignore_errors=True)


def kinds(trace):
    """['menu 5', 'esde', ...]: the mode of each start, the boot menu with its countdown."""
    out = []
    for ln in trace:
        w = ln.split(" | ")[0].split()
        out.append("menu %s" % w[w.index("--countdown") + 1] if w[0] == "menu" else w[0])
    return out


def scenario(menu=(), esde=(), fightcade=(), n=3, **rig):
    r = Rig(**rig)
    r.plan("menu", *menu)
    r.plan("esde", *esde)
    r.plan("fightcade", *fightcade)
    r.start()
    ok = r.wait_trace(n)
    return r, ok


# ---- the loop --------------------------------------------------------------------------------------
def test_power_on_menu_then_esde_then_menu_waits():
    r, ok = scenario(menu=["print esde"], esde=["exit 0"], n=3)
    try:
        check(ok and kinds(r.trace())[:3] == ["menu 5", "esde", "menu 0"],
              "power-on: menu countdown 5 -> esde; ES-DE quit -> menu countdown 0: %s" % kinds(r.trace()))
        check("esde --resolution 1440 1080" in r.trace()[1], "ES-DE gets its flags: %s" % r.trace()[1:2])
        check(r.current() == "menu", "current-mode is menu while the menu shows (%s)" % r.current())
    finally:
        r.close()


def test_current_mode_esde():
    r, ok = scenario(menu=["print esde"], esde=["wait"], n=2)
    try:
        check(ok and r.current() == "esde", "current-mode is esde while ES-DE runs (%s)" % r.current())
    finally:
        r.close()


def test_menu_failures_mean_esde():
    for act, label in (("exit 3", "exit 3 (no display)"), ("sig 11", "a crash"), ("print bogus", "unknown output"),
                       ("exit 0", "empty output"), ("sig 15", "SIGTERM 143 with no answer")):
        r, ok = scenario(menu=[act], esde=["wait"], n=2)
        try:
            check(ok and kinds(r.trace())[:2] == ["menu 5", "esde"], "BROKEN boot menu, %s -> esde: %s" % (label, kinds(r.trace())))
        finally:
            r.close()


def test_menu_missing_means_esde():
    r, ok = scenario(esde=["wait"], n=1, bootmenu=False)
    try:
        check(ok and kinds(r.trace()) == ["esde"] and "boot menu" in r.log() and "missing" in r.log(),
              "BROKEN: no bootmenu_app.py -> esde, logged: %s" % kinds(r.trace()))
    finally:
        r.close()


def test_esde_killed_restarts_crash_goes_to_menu():
    r, ok = scenario(menu=["print esde"], esde=["sig 9", "sig 6", "wait"], n=4)
    try:
        check(ok and kinds(r.trace())[:4] == ["menu 5", "esde", "esde", "menu 0"],
              "ES-DE killed (9) -> ES-DE again; crash (abort, 134) -> menu: %s" % kinds(r.trace()))
        check("crashed" in r.trace()[3], "the menu gets the crash message: %s" % r.trace()[3:4])
    finally:
        r.close()


def test_backoff_after_three_kills():
    r, ok = scenario(menu=["print esde"], esde=["sig 9", "sig 9", "sig 9", "wait"], n=5)
    try:
        k = kinds(r.trace())
        check(ok and k[:5] == ["menu 5", "esde", "esde", "esde", "menu 0"],
              "BROKEN ES-DE killed 3 times in 60 s -> back-off to the menu, no 4th start: %s" % k)
        check("stopped 3 times" in r.trace()[4] and "back-off" in r.log(), "the menu gets the back-off message")
    finally:
        r.close()


def test_fightcade_missing_means_esde():
    r, ok = scenario(menu=["print fightcade"], esde=["wait"], n=2, has_fightcade=False)
    try:
        check(ok and kinds(r.trace())[:2] == ["menu 5", "esde"] and "not installed" in r.log(),
              "BROKEN: menu picks FIGHTCADE without coin-dojo-fightcade -> esde, logged: %s" % kinds(r.trace()))
    finally:
        r.close()


def test_fightcade_ends_to_menu():
    r, ok = scenario(menu=["print fightcade"], fightcade=["exit 0"], n=3)
    try:
        check(ok and kinds(r.trace())[:3] == ["menu 5", "fightcade", "menu 0"], "Fightcade ends -> menu: %s" % kinds(r.trace()))
    finally:
        r.close()


def test_requests():
    r, ok = scenario(menu=["print esde"], esde=["req fightcade"], fightcade=["exit 1"], n=4)
    try:
        check(ok and kinds(r.trace())[:4] == ["menu 5", "esde", "fightcade", "menu 0"],
              "ES-DE writes next-mode=fightcade and quits -> Fightcade; its crash -> menu: %s" % kinds(r.trace()))
        check(not os.path.exists(os.path.join(r.run, "next-mode")), "the request file is deleted after reading")
    finally:
        r.close()
    r, ok = scenario(menu=["print esde"], esde=["req bogus"], n=3)
    try:
        check(ok and kinds(r.trace())[:3] == ["menu 5", "esde", "menu 0"] and "unknown next-mode" in r.log(),
              "BROKEN: unknown next-mode -> menu: %s" % kinds(r.trace()))
    finally:
        r.close()
    r, ok = scenario(menu=["print esde"], esde=["req fightcade old"], n=3)
    try:
        check(ok and kinds(r.trace())[:3] == ["menu 5", "esde", "menu 0"] and "stale next-mode" in r.log(),
              "BROKEN: a stale next-mode (written before the mode started) -> menu: %s" % kinds(r.trace()))
    finally:
        r.close()
    r = Rig()
    try:
        os.makedirs(r.run)
        open(os.path.join(r.run, "next-mode"), "w").write("fightcade\n")
        r.plan("menu", "wait")
        r.start()
        ok = r.wait_trace(1)
        check(ok and kinds(r.trace()) == ["menu 5"], "BROKEN: a next-mode left from before the session -> power-on menu")
    finally:
        r.close()


def test_sigterm_never_respawns():
    r, ok = scenario(menu=["print esde"], esde=["ignore"], n=2)
    try:
        pid = int(open(os.path.join(r.d, "esde.pid")).read())
        t0 = time.time()
        r.proc.send_signal(signal.SIGTERM)
        rc = r.proc.wait(10)
        took = time.time() - t0
        time.sleep(1.5)
        alive = True
        try:
            os.kill(pid, 0)
        except OSError:
            alive = False
        check(ok and rc == 0 and took < 6, "SIGTERM: the session exits 0 within the grace (rc %s, %.1f s)" % (rc, took))
        check(not alive, "SIGTERM: ES-DE ignoring TERM is KILLed")
        check(len(r.trace()) == 2, "BROKEN SIGTERM: nothing starts again: %s" % kinds(r.trace()))
        check(r.current() is None and not os.path.exists(os.path.join(r.run, "session.pid")),
              "SIGTERM: current-mode and session.pid are removed")
    finally:
        r.close()


def test_parent_gone_ends_session():
    """cage (the parent) dies: the session must not start the next mode into a dead display (an orphan loop)."""
    r = Rig()
    try:
        r.plan("menu", "print esde")
        r.plan("esde", "nap 2.5")
        parent = subprocess.Popen(["sh", "-c", "%s %s & sleep 1" % (BASH, SESSION)], env=r.env, cwd=r.d,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ok = r.wait_trace(2)
        parent.wait(10)
        pidf = os.path.join(r.run, "session.pid")
        spid = int(open(pidf).read()) if os.path.exists(pidf) else None
        end = time.time() + 10
        while time.time() < end and os.path.exists(pidf):
            time.sleep(0.1)
        time.sleep(0.5)
        check(ok and not os.path.exists(pidf) and len(r.trace()) == 2 and "parent (cage" in r.log(),
              "BROKEN cage gone: the session ends after the mode, starts nothing (%s, pid %s)" % (kinds(r.trace()), spid))
    finally:
        r.close()


# ---- the stop hook (#211 calls it) -------------------------------------------------------------------
def test_stop_hook():
    r, ok = scenario(menu=["print fightcade"], fightcade=["ignore"], n=2)
    try:
        t0 = time.time()
        out = r.stop_hook()
        check(out.returncode == 0 and time.time() - t0 < 2, "stop hook returns at once, 0 (%s)" % out.stderr.strip())
        ok = r.wait_trace(3)
        check(ok and kinds(r.trace())[:3] == ["menu 5", "fightcade", "menu 0"],
              "stop hook: Fightcade ignoring TERM is KILLed, menu shows: %s" % kinds(r.trace()))
    finally:
        r.close()
    r, ok = scenario(menu=["print fightcade"], fightcade=["wait"], esde=["wait"], n=2)
    try:
        open(os.path.join(r.run, "next-mode"), "w").write("esde\n")
        r.stop_hook()
        ok = r.wait_trace(3)
        check(ok and kinds(r.trace())[:3] == ["menu 5", "fightcade", "esde"],
              "stop hook keeps an earlier request (esde): %s" % kinds(r.trace()))
    finally:
        r.close()
    r = Rig()
    try:
        out = r.stop_hook()
        check(out.returncode == 0 and "no session" in out.stderr, "stop hook without a session: 0, says so")
        os.makedirs(r.run)
        open(os.path.join(r.run, "session.pid"), "w").write("%d\n" % os.getpid())
        out = r.stop_hook()
        check(out.returncode == 0 and "not coin-dojo-session" in out.stderr
              and not os.path.exists(os.path.join(r.run, "next-mode")),
              "BROKEN: a pid file naming another process is not signalled and nothing is written")
        check(r.stop_hook().returncode == 0 and subprocess.run([BASH, STOP, "x"], env=r.env, capture_output=True).returncode == 2,
              "idempotent; an argument is a usage error (2)")
    finally:
        r.close()
    src = _read("files", "coin-dojo-session-stop")
    check("sudo" not in re.sub(r"(?m)^#.*$", "", src) and "systemctl" not in src,
          "stop hook does no privileged work (no sudo, no systemctl): only a file write and a signal")


# ---- cleanup -----------------------------------------------------------------------------------------------
def _hung(token):
    return subprocess.Popen([sys.executable, "-c",
                             "import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)",
                             token + "-hung"])


def test_cleanup_kills_hung_emulator():
    r = Rig()
    try:
        p = _hung(r.token)
        time.sleep(0.3)
        out = subprocess.run([BASH, CLEANUP], env=r.env, capture_output=True, text=True, timeout=20)
        dead = p.poll() is not None or (p.wait(3) is not None)
        check(dead and out.returncode == 0, "a hung emulator ignoring TERM is KILLed (rc %d)" % out.returncode)
        check("TERM 1 process" in out.stdout and "KILL after 1 s" in out.stdout and "proof pgrep count=0" in out.stdout,
              "cleanup logs TERM, KILL and the pgrep proof: %s" % out.stdout.strip().replace("\n", " / "))
        out = subprocess.run([BASH, CLEANUP], env=r.env, capture_output=True, text=True, timeout=20)
        check(out.returncode == 0 and "TERM" not in out.stdout and "count=0" in out.stdout, "nothing left: a no-op with proof")
    finally:
        r.close()
    # BROKEN: a pattern list that misses the emulator leaves it running and the proof check catches it
    r = Rig(conf_extra="HK_CLEANUP_PATTERNS=(nothing-matches-this)\n")
    p = _hung(r.token)
    try:
        time.sleep(0.3)
        subprocess.run([BASH, CLEANUP], env=r.env, capture_output=True, text=True, timeout=20)
        check(p.poll() is None, "BROKEN control: a cleanup that misses the emulator leaves it alive (the test can fail)")
    finally:
        p.kill()
        p.wait()
        r.close()


def _default_patterns():
    d = _read("defaults", "main.yml")
    block = re.search(r"(?m)^session_cleanup_patterns:[^\n]*\n((?:  - [^\n]*\n)+)", d).group(1)
    return [ln.strip()[2:].strip().strip('"') for ln in block.splitlines()]


def test_cleanup_default_patterns_spare_editors():
    """The real default patterns: an app/Wine process dies, a shell or editor that only names such a file lives."""
    pats = _default_patterns()
    r = Rig(conf_extra="HK_CLEANUP_PATTERNS=(%s)\n" % " ".join("'%s'" % p for p in pats))
    loop = os.path.join(r.d, "settings_app.py")
    open(loop, "w").write("trap '' TERM\nwhile :; do sleep 0.2; done\n")
    procs = {}
    try:
        for key, argv0, args in (("app", "python3", [loop]),
                                 ("wine", "C:\\windows\\system32\\fakeapp.exe", [loop]),
                                 ("retroarch", "/data/emulators/retroarch-1.22/retroarch", [loop]),
                                 ("editor", "vim", [loop, "x.exe", "/data/emulators/retroarch-1.22/retroarch.cfg"]),
                                 ("pager", "less", [loop, "/data/coin-dojo/settings/bootmenu_app.py"])):
            procs[key] = subprocess.Popen(["bash", "-c", 'exec -a "$0" /bin/sh "$@"', argv0] + args)
        time.sleep(0.5)
        out = subprocess.run([BASH, CLEANUP], env=r.env, capture_output=True, text=True, timeout=30)
        time.sleep(0.3)
        alive = {k: p.poll() is None for k, p in procs.items()}
        check(not alive["app"] and not alive["wine"] and not alive["retroarch"],
              "default patterns kill the app (python3 .../settings_app.py), a Wine C:\\...exe and RetroArch 1.22: %s" % alive)
        check(alive["editor"] and alive["pager"], "BROKEN-case guard: `vim x.exe ...` and `less ...bootmenu_app.py` of the same user live")
        check(out.returncode == 0, "cleanup proof count=0 with the editors left alone (%s)" % out.stdout.strip()[-120:])
    finally:
        for p in procs.values():
            p.kill()
            p.wait()
        r.close()


def test_cleanup_lists_cover_the_contract():
    d = _read("defaults", "main.yml")
    names = re.search(r"(?m)^session_cleanup_names: \[(.*)\]", d).group(1)
    pats = re.search(r"(?m)^session_cleanup_patterns:[^\n]*\n((?:  - [^\n]*\n)+)", d).group(1)
    for n in ("retroarch", "flycast", "electron", "wineserver", "es-de", "AppRun"):
        check(re.search(r"\b%s\b" % re.escape(n), names), "cleanup kills %s" % n)
    for p in ("retroarch-1.22", "settings_app", "trophies_app", "bootmenu_app", ".exe"):
        check(p.replace(".", "[.]") in pats or p in pats, "cleanup pattern for %s" % p)
    check(all(len(n.strip()) <= 15 for n in names.split(",")), "pgrep -x names fit the 15-char comm")
    check("powerd" not in names + pats and "trophiesd" not in names + pats, "cleanup never touches powerd/trophiesd")


# ---- --check and the .bash_profile line ---------------------------------------------------------------------
def test_check_mode():
    r = Rig()
    try:
        ok = subprocess.run([BASH, SESSION, "--check"], env=r.env, capture_output=True, text=True)
        check(ok.returncode == 0 and "OK" in ok.stdout, "--check passes with a good config (%s)" % ok.stderr.strip())
        open(r.conf, "a").write("HK_ESDE=(/nonexistent/es-de)\n")
        bad = subprocess.run([BASH, SESSION, "--check"], env=r.env, capture_output=True, text=True)
        check(bad.returncode == 1 and "not executable" in bad.stderr, "BROKEN: --check fails when ES-DE is missing")
        open(r.conf, "w").write("HK_ESDE=(\n")
        bad = subprocess.run([BASH, SESSION, "--check"], env=r.env, capture_output=True, text=True)
        check(bad.returncode == 1, "BROKEN: --check fails on a config that does not parse")
    finally:
        r.close()


def _profile_rules():
    d = _read("defaults", "main.yml")
    esde = "/usr/local/bin/es-de"
    rx = re.search(r"(?ms)^session_profile_regexp: >-\n  (.*?)\n(?=\S)", d).group(1).strip()
    line = re.search(r"(?ms)^session_profile_line: >-\n  (.*?)\n(?=\S)", d).group(1).strip()
    plain = re.search(r"(?m)^session_profile_plain_line: '(.*?)'", d).group(1)
    rx = rx.replace("{{ session_esde_argv[0] | regex_escape }}", re.escape(esde))
    return rx, line, plain


def _apply(text, rx, line, session_bin="/usr/local/bin/coin-dojo-session"):
    """lineinfile backrefs: the LAST matching line becomes line (expanded); no match = unchanged."""
    lines = text.split("\n")
    idx = [i for i, ln in enumerate(lines) if re.match(rx, ln)]
    if idx:
        m = re.match(rx, lines[idx[-1]])
        lines[idx[-1]] = m.expand(line.replace("{{ session_bin }}", session_bin))
    return "\n".join(lines)


CABINET_PROFILE = """if [ "$(tty)" = "/dev/tty1" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
  export MESA_GL_VERSION_OVERRIDE=3.3COMPAT
  export XCURSOR_THEME=blank; export XCURSOR_SIZE=24  # transparent cursor (kiosk, no mouse)
  pulseaudio --start >/dev/null 2>&1 || true   # HDMI combine-sink for speaker audio
  if [ -f /data/launcher/USE_LAUNCHER ]; then exec cage -d -- /data/launcher/coin-dojo-session.sh; fi
  exec cage -d -- /usr/local/bin/es-de --resolution 1440 1080 --fullscreen-padding 1
fi
"""


def test_profile_line_edit():
    rx, line, plain = _profile_rules()
    new = _apply(CABINET_PROFILE, rx, line)
    changed = [b for a, b in zip(CABINET_PROFILE.split("\n"), new.split("\n")) if a != b]
    check(len(changed) == 1 and "exec cage -d -- /usr/local/bin/coin-dojo-session;" in changed[0]
          and changed[0].endswith("--fullscreen-padding 1  # coin-dojo-session (#209), plain ES-DE when its check fails"),
          "only the es-de exec line changes; ES-DE keeps its flags as the fallback: %s" % changed)
    check(_apply(new, rx, line) == new, "idempotent: a second run changes nothing")
    check(len(re.findall("(?m)" + rx, new)) == 1 and len(re.findall("(?m)" + rx, CABINET_PROFILE)) == 1,
          "the deploy's count assert sees exactly one line, before and after")
    check(_apply(new, rx, plain) == CABINET_PROFILE, "rollback (hk_session_enable false) restores the exact old line")
    for keep in ("MESA_GL_VERSION_OVERRIDE", "XCURSOR_THEME=blank", "pulseaudio --start", "USE_LAUNCHER"):
        check(keep in new, "the other roles' line stays: %s" % keep)
    lrx = re.search(r"(?m)^session_profile_launcher_regexp: '(.*?)'", _read("defaults", "main.yml")).group(1)
    gone = "\n".join(x for x in new.split("\n") if not re.match(lrx, x))
    diff = [a for a in new.split("\n") if a not in gone.split("\n")]
    check(diff == ["  if [ -f /data/launcher/USE_LAUNCHER ]; then exec cage -d -- /data/launcher/coin-dojo-session.sh; fi"],
          "the dead USE_LAUNCHER line (cabinet line 10) is removed, nothing else: %s" % diff)
    check(len(re.findall("(?m)" + rx, gone)) == 1 and not re.match(lrx, [x for x in gone.split("\n") if "coin-dojo-session --check" in x][0]),
          "the guarded session line survives the USE_LAUNCHER removal (lockout guard kept)")
    vm = _read("roles", "vm_base", "tasks", "main.yml", base=ANSIBLE)
    check(len(re.findall("(?m)" + rx, vm)) == 1, "the VM base profile has the line the role edits")
    odd = CABINET_PROFILE.replace("  exec cage", "  [ -f /x ] && exec cage -d -- /x; exec cage")
    check(len(re.findall("(?m)" + rx, odd)) == 0, "BROKEN: a hand-edited exec line is not matched (the deploy aborts)")


def test_profile_lockout_guard():
    """Run the guarded line with a fake cage: a good session wins; a bad one falls back to plain ES-DE."""
    rx, line, _ = _profile_rules()
    d = tempfile.mkdtemp(prefix="hk-profile-")
    try:
        cage = os.path.join(d, "cage")
        open(cage, "w").write("#!/bin/sh\necho \"$*\" > %s/cage.args\n" % d)
        os.chmod(cage, 0o755)
        r = Rig()
        sess = os.path.join(d, "coin-dojo-session")

        def run(content, mode=0o755):
            if os.path.exists(os.path.join(d, "cage.args")):
                os.remove(os.path.join(d, "cage.args"))
            if content is None:
                if os.path.exists(sess):
                    os.remove(sess)
            else:
                open(sess, "w").write(content)
                os.chmod(sess, mode)
            ln = [x for x in _apply(CABINET_PROFILE, rx, line, session_bin=sess).split("\n") if "coin-dojo-session --check" in x
                  or (sess + " --check") in x][0]
            env = dict(r.env, PATH=d + os.pathsep + os.environ["PATH"])
            subprocess.run([BASH, "-c", ln], env=env, capture_output=True, timeout=20)
            return open(os.path.join(d, "cage.args")).read().strip()

        good = open(SESSION).read()
        check(run(good) == "-d -- %s" % sess, "a good session: cage runs coin-dojo-session")
        esde = "-d -- /usr/local/bin/es-de --resolution 1440 1080 --fullscreen-padding 1"
        check(run(good + "\nif then fi (\n") == esde, "BROKEN session (syntax error): plain ES-DE")
        check(run(good, 0o644) == esde, "BROKEN session (not executable): plain ES-DE")
        check(run(None) == esde, "BROKEN session (missing): plain ES-DE")
        open(r.conf, "a").write("HK_CLEANUP=/nonexistent\n")
        check(run(good) == esde, "BROKEN config (--check fails): plain ES-DE")
        r.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---- role wiring --------------------------------------------------------------------------------------------
def test_role_wiring():
    t = _read("tasks", "main.yml")
    d = _read("defaults", "main.yml")
    site = _read("site.yml", base=ANSIBLE)
    vm = _read("vm.yml", base=ANSIBLE)
    check("role: session" in site and "role: session" in vm, "site.yml and vm.yml run the role")
    check("backrefs: true" in t and "backup: true" in t, "the profile edit uses backrefs (no match = no change) and a backup")
    check("regex_findall('(?m)' ~ session_profile_regexp) | length) == 1" in t, "the deploy asserts exactly one es-de line")
    check(t.index("Install gate - the session checks") < t.index("The tty1 exec line runs the session loop"),
          "the install gate runs before the profile is switched")
    check("session_old_launcher_items: [USE_LAUNCHER, coin-dojo-session.sh, games.json, cog.log, fetch_art.py, fetch.log, art]" in d,
          "only the #204 v2 leftovers are removed (power_dir stays live)")
    rm = re.search(r"(?ms)- name: Back up the leftovers, then remove them.*?changed_when", t).group(0)
    check(rm.index("cp -a") < rm.index("rm -rf") and "set -e" in rm, "leftovers are copied to the backup before rm, set -e")
    check(re.search(r"(?m)^session_vm_swappiness: \"\{\{ hk_vm_swappiness \}\}\"", d) and "session_sysctl_file: /etc/sysctl.d/99-zz-coin-dojo.conf" in d
          and "session_sysctl_old: /etc/sysctl.d/90-coin-dojo.conf" in d and "path: \"{{ session_sysctl_old }}\"\n    state: absent" in t,
          "swappiness: the hk_vm_swappiness lever in 99-zz-coin-dojo.conf (after 98-rpi.conf); the old 90- file is removed")
    check(sorted(["98-rpi.conf", "99-sysctl.conf", "99-zz-coin-dojo.conf"])[-1] == "99-zz-coin-dojo.conf",
          "the drop-in sorts last (sysctl --system applies in name order, the last value wins)")
    check(sorted(["98-rpi.conf", "90-coin-dojo.conf"])[-1] != "90-coin-dojo.conf", "BROKEN control: a 90- name would lose to 98-rpi.conf")
    storage = _read("config", "variables.d", "storage.yml", base=REPO)
    check(re.search(r"(?m)^  hk_vm_swappiness:\n    type: int\n    default: 1\n", storage), "hk_vm_swappiness defaults to 1")
    h = _read("handlers", "main.yml")
    check("sysctl, --system" in h, "the handler applies every sysctl.d file (sysctl --system)")
    for k in re.findall(r"(?m)^([a-z_]+):(.*)$", d):
        name, rest = k
        check("{{ hk_" in rest or "# internal" in rest or "{{ session_" in rest or name.startswith("session_profile_"),
              "default %s is a lever pass-through or # internal" % name)


def test_esde_handler_requests_esde_and_guard_sees_fightcade():
    h = _read("roles", "esde_common", "handlers", "main.yml", base=ANSIBLE)
    t = _read("roles", "esde_common", "tasks", "main.yml", base=ANSIBLE)
    g = _read("group_vars", "esde.yml", base=ANSIBLE)
    check('cmd: "{{ esde_request_cmd }}; {{ esde_kill_cmd }}; true"' in h, "restart es-de writes next-mode=esde before the kill")
    check("printf 'esde\\\\n' > {{ (esde_session_dir ~ '/next-mode') | quote }}" in g, "the request is esde, into next-mode")
    for name, text in (("task", t), ("handler", h)):
        check("grep -qsx fightcade {{ esde_session_cur | quote }} && {{ esde_session_live }} && echo" in text,
              "the %s game guard counts current-mode=fightcade as a game, only with a live session" % name)
    check("grep -qsx menu {{ esde_session_cur | quote }} && {{ esde_session_live }} && echo menu" in h
          and "esde_restart_skip" in h, "the handler skips the restart while the boot menu shows")
    kill = re.search(r"(?ms)- name: Restart ES-DE \(next-mode=esde.*?listen", h).group(0)
    check("not esde_restart_skip | bool" in kill, "the kill itself is skipped in the menu (no 180 s wait for AppRun)")
    for task in ("Wait for ES-DE to come back", "Wait for ES-DE after the getty restart", "Let the old session go"):
        blk = re.search(r"(?ms)- name: %s.*?listen" % re.escape(task), h).group(0)
        check("not esde_restart_skip | bool" in blk, "skipped in the menu: %s" % task)
    live = re.search(r"(?ms)^esde_session_live: >-\n(.*?)\n(?=\S)", g).group(1)
    check("grep -qsxE \"[0-9]+\"" in live and "/proc/{}/cmdline" in live and "coin-dojo-session" in live,
          "a live session = the pid in session.pid runs and is coin-dojo-session")
    if os.path.isdir("/proc/self"):
        d0 = tempfile.mkdtemp()
        try:
            shl = " ".join(x.strip() for x in live.splitlines()).replace(
                "{{ esde_session_pidfile | quote }}", os.path.join(d0, "session.pid"))
            sess = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "coin-dojo-session"])
            for pid, want, label in ((sess.pid, 0, "a live session"), (999999, 1, "BROKEN: a dead pid (stale state)"),
                                     (os.getpid(), 1, "BROKEN: a pid of another program")):
                open(os.path.join(d0, "session.pid"), "w").write("%d\n" % pid)
                rc = subprocess.run(["sh", "-c", shl]).returncode
                check((rc == 0) == (want == 0), "%s -> live=%s" % (label, rc == 0))
            sess.kill()
            sess.wait()
        finally:
            shutil.rmtree(d0, ignore_errors=True)
    else:
        skip("no /proc: the live-session test runs on Linux (CI) only")
    # the request command really writes the file, and is a no-op without a session dir
    d = tempfile.mkdtemp()
    try:
        cmd = "[ -d %s ] && printf 'esde\\n' > %s/next-mode; true" % (d, d)
        subprocess.run(["sh", "-c", cmd], check=True)
        check(open(os.path.join(d, "next-mode")).read() == "esde\n", "the request command writes `esde`")
        gone = os.path.join(d, "nope")
        subprocess.run(["sh", "-c", "[ -d %s ] && printf 'esde\\n' > %s/next-mode; true" % (gone, gone)], check=True)
        check(not os.path.exists(gone), "BROKEN control: no session dir = nothing written")
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main(globals())
