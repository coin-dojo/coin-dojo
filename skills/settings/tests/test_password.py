#!/usr/bin/env python3
"""PASSWORD page of the SETTINGS app and its root helper (#271).

    python3 skills/settings/tests/test_password.py

Nothing here runs sudo, chpasswd or smbpasswd: the helper's `run`, the caller lookup and the file checks are fakes, the
app's runner records (argv, stdin). Passwords used are obviously fake test strings.

The helper (coin_dojo_set_password.py): under the password flock (a tmp file here), sets the Samba password with
smbpasswd when Samba is installed, then the caller's own login password with chpasswd, both through stdin; refuses an
admin (sudo group or coindojo-admin, #270) with exit 2 and runs nothing; skips Samba with a line when it is missing; exit 3 when only the
Samba step failed. Broken cases (each exits 2 and runs NOTHING): an argument, not root, no/zero/system SUDO_UID, a
name that does not match the uid, a nologin shell, an unknown uid, empty input, a bare newline, too short, 12345, too
long, a control character, two lines, non-ASCII. Leaks: the password is never in argv, stdout, stderr, the app's log or
the screen model; a crash prints one generic line with no traceback; a tool that echoes the password is scrubbed.
The app (settings_core.Password / PasswordPage): both fields masked, mismatch / too short / 12345 refused before the helper
runs, the helper called as `sudo -n HELPER` with the password only on stdin, the fields emptied afterwards.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
from collections import namedtuple

HERE = os.path.dirname(os.path.abspath(__file__))
APPDIR = os.path.dirname(HERE)
REPO = os.path.normpath(os.path.join(APPDIR, "..", ".."))
sys.path.insert(0, APPDIR)
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
import coin_dojo_set_password as H  # noqa: E402
import settings_core as core  # noqa: E402
from hktest import check, main  # noqa: E402

PW = "tr1ck-Pass 9"                 # a fake test password (space and symbols on purpose)
Ent = namedtuple("Ent", "pw_name pw_uid pw_shell")
USERS = {1001: Ent("coindojo", 1001, "/bin/bash"), 1002: Ent("guest", 1002, "/usr/sbin/nologin"), 999: Ent("daemonx", 999, "/bin/sh"),
         0: Ent("root", 0, "/bin/bash")}
SUDO_ENV = {"SUDO_UID": "1001", "SUDO_USER": "coindojo"}
Grp = namedtuple("Grp", "gr_name gr_gid gr_mem")
PwEnt = namedtuple("PwEnt", "pw_name pw_gid")
LOCKDIR = tempfile.mkdtemp(prefix="hk-pwlock-")      # the helper's flock file lives here in tests, never /run/lock
LOCK = os.path.join(LOCKDIR, "password.lock")


def no_sudo_group(name):
    return Grp("sudo", 27, [])


def getpw(uid):
    if uid not in USERS:
        raise KeyError(uid)
    return USERS[uid]


class Result:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class Runner:
    """Fake subprocess.run: records (argv, input, env); answers by tool name."""

    def __init__(self, chpasswd=Result(), smbpasswd=Result(), firstboot=Result()):
        self.calls, self.answers = [], {"chpasswd": chpasswd, "smbpasswd": smbpasswd, "logger": Result(),
                                        "python3": firstboot}

    def tools(self):
        """The recorded calls without the audit line (logger)."""
        return [c for c in self.calls if os.path.basename(c[0][0]) != "logger"]

    def audits(self):
        return [c[0] for c in self.calls if os.path.basename(c[0][0]) == "logger"]

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw.get("input"), kw.get("env")))
        return self.answers[os.path.basename(argv[0])]


def helper(stdin=PW + "\n", argv=(), env=None, euid=0, samba=True, runner=None, getpwuid=getpw,
           getgrnam=no_sudo_group, admin_rule=lambda p: False, primary_gid=1001, lock_path=LOCK, lock_wait=2,
           fb_exists=lambda p: False):
    runner = runner or Runner()
    out, err = io.StringIO(), io.StringIO()
    exists = (lambda p: True) if samba else (lambda p: False)
    rc = H.main(list(argv), stdin=io.BytesIO(stdin if isinstance(stdin, bytes) else stdin.encode("utf-8")),
                env=SUDO_ENV if env is None else env, geteuid=lambda: euid, getpwuid=getpwuid, run=runner, exists=exists,
                out=out, err=err, getpwnam=lambda n: PwEnt(n, primary_gid), getgrnam=getgrnam, admin_rule=admin_rule,
                lock_path=lock_path, lock_wait=lock_wait, firstboot="/fb/coindojo-firstboot", fb_exists=fb_exists)
    return rc, out.getvalue(), err.getvalue(), runner


# ------------------------------------------------------------------------------------------------ the helper
def test_helper_sets_linux_and_samba_through_stdin():
    rc, out, err, r = helper()
    check(rc == 0 and "linux: ok" in out and "network folder: ok" in out, "ok with Samba: rc %d, %r %r" % (rc, out, err))
    check([c[0] for c in r.tools()] == [[H.SMBPASSWD, "-s", "-a", "coindojo"], [H.CHPASSWD]],
          "#270 M2: smbpasswd -s -a first, then chpasswd (the shadow change that starts the sync comes last): %s" % r.calls)
    check(r.tools()[1][1] == "coindojo:" + PW + "\n" and r.tools()[0][1] == PW + "\n" + PW + "\n", "the password goes in on stdin only")
    check(all(PW not in " ".join(c[0]) for c in r.calls), "the password is in no argv (the audit line included)")
    check(all(c[2] == H.FIXED_ENV for c in r.calls), "a fixed environment, not the caller's")
    check(PW not in out + err, "the password is not printed")


def test_helper_skips_samba_when_not_installed():
    rc, out, err, r = helper(samba=False)
    check(rc == 0 and "linux: ok" in out and "skipped (Samba not installed)" in out and len(r.tools()) == 1, "no Samba: %r %s" % (out, r.calls))


def test_helper_samba_failure_is_exit_3_after_linux_ok():
    rc, out, err, r = helper(runner=Runner(smbpasswd=Result(1, "", "boom " + PW)))
    check(rc == 3 and "linux: ok" in out and "FAILED" in err and PW not in out + err, "exit 3, the Linux password did change: %r %r" % (out, err))
    check([c[0][0] for c in r.tools()] == [H.SMBPASSWD, H.CHPASSWD], "a failed Samba step still sets the Linux password: %s" % r.calls)


def test_helper_chpasswd_failure_is_exit_1_and_scrubbed():
    rc, out, err, r = helper(runner=Runner(chpasswd=Result(1, "", "chpasswd: bad value " + PW + " here")))
    check(rc == 1 and [c[0][0] for c in r.tools()] == [H.SMBPASSWD, H.CHPASSWD], "chpasswd failed: exit 1 (Samba ran first) (%d, %s)" % (rc, r.calls))
    check(PW not in out + err and "***" in err, "a tool that echoes the password is scrubbed: %r" % err)


def test_BROKEN_refusals_run_nothing():
    cases = [
        ("an argument", dict(argv=["x"])),
        ("two arguments", dict(argv=["a", "b"])),
        ("not root", dict(euid=1001)),
        ("no SUDO_UID", dict(env={})),
        ("SUDO_UID not a number", dict(env={"SUDO_UID": "x", "SUDO_USER": "coindojo"})),
        ("root as the caller", dict(env={"SUDO_UID": "0", "SUDO_USER": "root"})),
        ("a system uid", dict(env={"SUDO_UID": "999", "SUDO_USER": "daemonx"})),
        ("name does not match the uid", dict(env={"SUDO_UID": "1001", "SUDO_USER": "someone"})),
        ("a nologin account", dict(env={"SUDO_UID": "1002", "SUDO_USER": "guest"})),
        ("an unknown uid", dict(env={"SUDO_UID": "4242", "SUDO_USER": "ghost"})),
        ("empty input", dict(stdin="")),
        ("a bare newline", dict(stdin="\n")),
        ("too short", dict(stdin="abcde\n")),
        ("the default password", dict(stdin="12345\n")),
        ("the default password without a newline", dict(stdin="12345")),
        ("too long", dict(stdin="a" * 129 + "\n")),
        ("a control character", dict(stdin="abc\x07defgh\n")),
        ("two lines", dict(stdin="abcdefgh\nsecond\n")),
        ("a carriage return inside", dict(stdin="abcd\refgh\n")),
        ("non-ASCII", dict(stdin="pässword1\n")),
        ("a NUL byte", dict(stdin=b"abcdefg\x00h\n")),
    ]
    for why, kw in cases:
        rc, out, err, r = helper(**kw)
        check(rc == 2 and not r.calls, "BROKEN: %s -> exit 2 and nothing runs (rc %d, calls %d)" % (why, rc, len(r.calls)))
        check(out == "" and "refused" in err or "usage" in err, "BROKEN: %s -> a reason on stderr, nothing on stdout: %r %r" % (why, out, err))


def test_BROKEN_admin_on_is_refused_and_runs_nothing():
    """#270 M1: with admin on, the login password is the sudo password; the NOPASSWD helper must not replace it."""
    sudo_with = lambda *mem: (lambda name: Grp("sudo", 27, list(mem)))
    cases = [
        ("in the sudo group", dict(getgrnam=sudo_with("pi", "coindojo"))),
        ("sudo is the primary group", dict(primary_gid=27)),
        ("the admin rule exists", dict(admin_rule=lambda p: p == "/etc/sudoers.d/coindojo-admin")),
    ]
    for why, kw in cases:
        rc, out, err, r = helper(**kw)
        check(rc == 2 and not r.calls and err.strip() == H.ADMIN_ON and out == "",
              "BROKEN: %s -> exit 2, %r, chpasswd never called (rc %d, calls %s)" % (why, H.ADMIN_ON, rc, r.calls))
    check(H.ADMIN_ON == "ADMIN IS ON: CHANGE IT WITH passwd" and H.ADMIN_RULE == "/etc/sudoers.d/coindojo-admin",
          "the message and the rule #270 writes")
    rc, _o, _e, r = helper(getgrnam=sudo_with("pi"))
    check(rc == 0, "someone else in the sudo group does not block coindojo")

    def no_group(name):
        raise KeyError(name)
    rc, _o, _e, r = helper(getgrnam=no_group)
    check(rc == 0, "no sudo group at all: not an admin")
    with tempfile.TemporaryDirectory() as d:        # the real admin-rule check on a real path in tmp
        rule = os.path.join(d, "coindojo-admin")
        os.symlink("/nonexistent", rule)
        check(H.is_admin("coindojo", lambda n: PwEnt(n, 1001), no_sudo_group, lambda p: os.path.lexists(rule)),
              "BROKEN: a dangling link at the admin rule's name still counts as admin (lexists)")


# ------------------------------------------------------------------------------------------------ #299 owner access
FB_ARGV = [H.PYTHON, "-I", "-B", "/fb/coindojo-firstboot", "--owner-access", "--user", "coindojo"]
HAS_FB = dict(fb_exists=lambda p: p == "/fb/coindojo-firstboot")


def fb_calls(r):
    return [c for c in r.calls if os.path.basename(c[0][0]) == "python3"]


def test_owner_access_runs_after_chpasswd_only():
    rc, out, err, r = helper(**HAS_FB)
    tools = [c[0] for c in r.tools()]
    check(rc == 0 and tools == [[H.SMBPASSWD, "-s", "-a", "coindojo"], [H.CHPASSWD], FB_ARGV],
          "own password: smbpasswd, chpasswd, THEN coindojo-firstboot --owner-access --user coindojo: %s" % tools)
    c = fb_calls(r)[0]
    check(c[1] is None and c[2] == H.FIXED_ENV and PW not in " ".join(c[0]), "no stdin, a fixed environment, no password in argv")
    check("admin and SSH: on" in out and any("admin and password SSH on for coindojo" in " ".join(a) for a in r.audits()),
          "says so, and the system log gets one line: %r" % out)
    check(H.PYTHON == "/usr/bin/python3" and H.FIRSTBOOT == "/usr/local/sbin/coindojo-firstboot", "the image's paths")
    rc, out, err, r = helper(runner=Runner(smbpasswd=Result(1)), **HAS_FB)
    check(rc == 3 and fb_calls(r), "Samba failed but the login changed: owner access still runs, exit stays 3")


def test_owner_access_outcomes_never_change_the_exit():
    rc, out, err, r = helper(runner=Runner(firstboot=Result(1, "coindojo-firstboot: ERROR: the password is still the default 12345: x\n")), **HAS_FB)
    check(rc == 0 and "linux: ok" in out and "admin and SSH: off (ERROR: the password is still the default" in err,
          "refused by firstboot: the password still changed (exit 0), the reason is said: %r" % err)
    rc, out, err, r = helper(runner=Runner(firstboot=Result(3)), **HAS_FB)
    check(rc == 0 and "admin and SSH: skipped (nothing pending)" in out, "nothing pending: said, exit 0")
    rc, out, err, r = helper()
    check(rc == 0 and "admin and SSH: skipped (no coindojo-firstboot" in out and not fb_calls(r), "no firstboot script: skipped")

    class Boom(Runner):
        def __call__(self, argv, **kw):
            if os.path.basename(argv[0]) == "python3":
                raise OSError("x")
            return Runner.__call__(self, argv, **kw)
    rc, out, err, r = helper(runner=Boom(), **HAS_FB)
    check(rc == 0 and "admin and SSH: off (OSError)" in err, "firstboot cannot run: off, said, exit 0")


def test_BROKEN_owner_access_never_runs_without_a_new_own_password():
    """With the default active the helper never reaches the step that writes the drop-in: every refusal (12345
    included), an admin, and a failed chpasswd leave coindojo-firstboot uncalled."""
    sudo_with = lambda *mem: (lambda name: Grp("sudo", 27, list(mem)))
    cases = [("12345", dict(stdin="12345\n")), ("12345 without newline", dict(stdin="12345")),
             ("too short", dict(stdin="abc\n")), ("not through sudo", dict(env={})), ("an argument", dict(argv=["x"])),
             ("admin on", dict(getgrnam=sudo_with("coindojo"))),
             ("chpasswd failed", dict(runner=Runner(chpasswd=Result(1))))]
    for why, kw in cases:
        rc, out, err, r = helper(**dict(HAS_FB, **kw))
        check(rc != 0 and not fb_calls(r) and "admin and SSH" not in out + err,
              "BROKEN: %s -> exit %d, coindojo-firstboot never called" % (why, rc))
    rc, out, err, r = helper(stdin="12345\n", **HAS_FB)
    check(rc == 2 and "that is the default password" in err, "12345 is refused as the own password: %r" % err)

def test_page_shows_admin_on_as_the_helper_says_it():
    p = core.Password("/h", runner=AppRunner(rc=2, err=H.ADMIN_ON + "\n"), log=core.Log(None))
    ok, msg = p.set(PW)
    check(not ok and msg == "ADMIN IS ON: CHANGE IT WITH passwd" and core.PW_ADMIN_ON == H.ADMIN_ON, "the page shows: %s" % msg)


def test_helper_holds_the_lock_and_refuses_when_busy():
    """#270 M2: both steps run under the flock that coin-dojo-share-sync also takes; a held lock means no change."""
    import fcntl
    with tempfile.TemporaryDirectory() as d:
        lock = os.path.join(d, "coin-dojo-password.lock")
        seen = []

        class Probe(Runner):
            def __call__(self, argv, **kw):
                if os.path.basename(argv[0]) != "logger":
                    fd = os.open(lock, os.O_RDWR)
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        seen.append("free")
                    except BlockingIOError:
                        seen.append("held")
                    finally:
                        os.close(fd)
                return super().__call__(argv, **kw)
        rc, _o, _e, _r = helper(runner=Probe(), lock_path=lock)
        check(rc == 0 and seen == ["held", "held"], "smbpasswd and chpasswd both run while the lock is held: %s" % seen)
        fd = os.open(lock, os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)                  # BROKEN: the sync holds it
        try:
            rc, out, err, r = helper(lock_path=lock, lock_wait=0.3)
        finally:
            os.close(fd)
        check(rc == 1 and not r.tools() and "busy" in err, "BROKEN: a held lock -> exit 1, nothing runs: %d %r %s" % (rc, err, r.calls))
        link = os.path.join(d, "link.lock")
        os.symlink(os.path.join(d, "target"), link)
        rc, out, err, r = helper(lock_path=link)
        check(rc == 1 and not r.tools() and not os.path.exists(os.path.join(d, "target")),
              "BROKEN: a symlink at the lock name is never followed (O_NOFOLLOW): %d %r" % (rc, err))


def test_lock_lives_in_a_root_directory():
    """#270 N2: the lock's directory is root's alone (not /run/lock or /tmp); a writable or linked one refuses."""
    parent = os.path.dirname(H.LOCK)
    check(H.LOCK == "/run/coindojo/password.lock" and parent not in ("/run/lock", "/tmp", "/var/tmp", "/dev/shm"),
          "the lock is in /run/coindojo: %s" % H.LOCK)
    with tempfile.TemporaryDirectory() as d:
        new = os.path.join(d, "nd")
        rc, _o, err, r = helper(lock_path=os.path.join(new, "password.lock"))
        check(rc == 0 and stat_mode(new) == 0o755, "a missing lock dir is made 0755 (tty1 reads the reminder there): %o" % stat_mode(new))
        for mode in (0o775, 0o757, 0o777):
            os.chmod(new, mode)
            rc, _o, err, r = helper(lock_path=os.path.join(new, "password.lock"))
            check(rc == 1 and not r.tools() and "not root's alone" in err, "BROKEN: lock dir %o refused, nothing runs: %r" % (mode, err))
        os.chmod(new, 0o755)
        os.makedirs(os.path.join(d, "real"))
        os.symlink(os.path.join(d, "real"), os.path.join(d, "link"))
        rc, _o, err, r = helper(lock_path=os.path.join(d, "link", "password.lock"))
        check(rc == 1 and not r.tools() and not os.listdir(os.path.join(d, "real")), "BROKEN: a linked lock dir refused: %r" % err)


def stat_mode(p):
    return os.stat(p).st_mode & 0o777


def test_audit_line_goes_to_the_system_log_without_the_value():
    rc, _o, _e, r = helper()
    check(r.audits() == [[H.LOGGER, "-p", "auth.notice", "-t", "coin-dojo-set-password", "password changed via SETTINGS for coindojo"]],
          "one audit line on success: %s" % r.audits())
    rc, _o, _e, r = helper(samba=False)
    check(len(r.audits()) == 1 and "coindojo" in r.audits()[0][-1] and PW not in " ".join(r.audits()[0]), "also without Samba")
    rc, _o, _e, r = helper(runner=Runner(chpasswd=Result(1, "", "no")))
    check(len(r.audits()) == 1 and "FAILED" in r.audits()[0][-1] and "chpasswd exit 1" in r.audits()[0][-1] and PW not in " ".join(r.audits()[0]),
          "one failure line when chpasswd fails: %s" % r.audits())
    rc, _o, _e, r = helper(runner=Runner(smbpasswd=Result(1)))
    check(len(r.audits()) == 1 and "network folder FAILED" in r.audits()[0][-1] and PW not in " ".join(r.audits()[0]), "one line when only the folder fails")
    for kw in (dict(stdin="12345\n"), dict(argv=["x"]), dict(euid=1001), dict(stdin="")):
        rc, _o, _e, r = helper(**kw)
        check(r.audits() == [], "BROKEN input %s: a refusal logs nothing (no change was tried)" % list(kw))


def test_a_logger_that_fails_or_is_missing_changes_nothing():
    class Boom(Runner):
        def __call__(self, argv, **kw):
            if os.path.basename(argv[0]) == "logger":
                raise OSError("no logger")
            return super().__call__(argv, **kw)
    rc, out, err, r = helper(runner=Boom())
    check(rc == 0 and "network folder: ok" in out, "a missing logger does not break the change: rc %d %r" % (rc, err))


def test_colon_in_the_password_is_set_verbatim_and_injects_nothing():
    for pw in ("a:b:c", "a:b:c1:", ":leading-colon", "x:root:hunter2", "coindojo:coindojo:again"):
        rc, out, err, r = helper(stdin=pw + "\n")
        check(rc == 0 or len(pw) < 6, "%r accepted: rc %d %r" % (pw, rc, err))
        if rc == 0:
            line = r.tools()[1][1]                           # #270 M2: smbpasswd first, chpasswd second
            user, _sep, rest = line.partition(":")           # chpasswd splits at the FIRST colon
            check(line.count("\n") == 1 and line.endswith("\n"), "%r: exactly one line goes to chpasswd (no second user can be added)" % pw)
            check(user == "coindojo" and rest == pw + "\n", "%r: the user is the caller, the password is everything after the first colon" % pw)
            check(r.tools()[0][1] == pw + "\n" + pw + "\n", "%r: smbpasswd gets it verbatim twice" % pw)
    # BROKEN: a newline is what would add a second `user:password` line, and it never gets through
    for inj in ("pw123456\nroot:owned", "pw123456\r\nroot:owned", b"pw123456\x00root:owned"):
        rc, _o, err, r = helper(stdin=inj)
        check(rc == 2 and not r.calls, "BROKEN: %r (a second user line) is refused, nothing runs" % (inj[:12],))


def test_limits_are_inclusive():
    for ok in ("a" * 6, "a" * 128, "12345 ", " 12345", "123456"):
        rc, _o, _e, _r = helper(stdin=ok + "\n")
        check(rc == 0, "%d characters %r is accepted" % (len(ok), ok[:8]))
    check(H.password_error("12345") is not None and core.password_error("12345") is not None, "12345 is refused by both sides")


def test_refusal_text_never_holds_the_value():
    for bad in ("12345", "abcde", "a" * 200, "tab\there1"):
        rc, out, err, _r = helper(stdin=bad + "\n")
        check(bad not in out + err, "BROKEN input %r is not echoed" % bad[:10])


def test_crash_prints_one_generic_line_no_traceback():
    real, errio = H.main, io.StringIO()

    def boom(argv):
        raise RuntimeError("secret was " + PW)
    H.main = boom
    old = sys.stderr
    sys.stderr = errio
    try:
        rc = H._safe_main()
    finally:
        sys.stderr, H.main = old, real
    check(rc == 1 and errio.getvalue().strip() == "unexpected error (RuntimeError)" and PW not in errio.getvalue(),
          "a crash: one generic line, no traceback, no value: %r" % errio.getvalue())


def test_real_cli_refuses_a_non_root_caller_without_echoing():
    script = os.path.join(APPDIR, "coin_dojo_set_password.py")
    env = dict(os.environ, SUDO_UID="1001", SUDO_USER="coindojo")
    r = subprocess.run([sys.executable, "-I", script], input=PW + "\n", capture_output=True, text=True, env=env)
    check(r.returncode == 2 and "refused" in r.stderr and PW not in r.stdout + r.stderr, "not root: %d %r" % (r.returncode, r.stderr))
    r = subprocess.run([sys.executable, "-I", script, PW], input="", capture_output=True, text=True, env=env)
    check(r.returncode == 2 and "usage" in r.stderr and PW not in r.stdout + r.stderr, "an argument (even the password) is refused, not echoed")


# ------------------------------------------------------------------------------------------------ the app side
class AppRunner:
    def __init__(self, rc=0, out="linux: ok\nnetwork folder: ok\n", err=""):
        self.calls, self.rc, self.out, self.err = [], rc, out, err

    def __call__(self, argv, data, timeout):
        self.calls.append((list(argv), data, timeout))
        return self.rc, self.out, self.err


def make_app(runner=None, logpath=None):
    log = core.Log(logpath)
    pw = core.Password("/usr/local/sbin/coin-dojo-set-password", runner=runner or AppRunner(), log=log)
    host = type("H", (), {"current": lambda s: "cab"})()
    app = core.App(core.NM("/bin/false", log=core.Log(None)), host, log, None, [core.PasswordPage(pw, user="coindojo")])
    app.pending = None
    return app, pw


def open_page(app):
    app.stack[0]["sel"] = [i for i, (lab, _w) in enumerate(app.top["items"]) if lab == "PASSWORD"][0]
    app.handle("ok")
    check(app.top["id"] == "pw", "SETTINGS lists PASSWORD and it opens")


def type_in(app, text):
    app.handle(("text", text))
    app.handle("start")


def test_password_class_validates_before_it_runs_anything():
    r = AppRunner()
    p = core.Password("/h", runner=r, log=core.Log(None))
    for bad, frag in (("", "TYPE"), ("abc", "AT LEAST"), ("12345", "DEFAULT"), ("a" * 129, "AT MOST"), ("pässword1", "ONLY")):
        ok, msg = p.set(bad)
        check(not ok and frag in msg, "BROKEN: %r refused: %s" % (bad[:6], msg))
    check(r.calls == [], "nothing reached the helper for any of them")


def test_password_class_calls_sudo_helper_with_stdin_only():
    r = AppRunner()
    with tempfile.TemporaryDirectory() as d:
        logf = os.path.join(d, "settings.log")
        p = core.Password("/usr/local/sbin/coin-dojo-set-password", runner=r, log=core.Log(logf))
        ok, msg = p.set(PW)
        check(ok and "NETWORK FOLDER" in msg, "ok: %s" % msg)
        check(r.calls[0][0] == ["sudo", "-n", "/usr/local/sbin/coin-dojo-set-password"], "sudo -n HELPER, no argument: %s" % r.calls[0][0])
        check(r.calls[0][1] == PW + "\n" and PW not in " ".join(r.calls[0][0]), "password on stdin only, never argv")
        logged = open(logf).read()
        check(PW not in logged and "password" in logged and '"rc": 0' in logged, "the log has the event and the exit code, never the value: %s" % logged.strip())


def test_helper_messages_map_and_are_scrubbed():
    p = core.Password("/h", runner=AppRunner(rc=0, out="linux: ok\nnetwork folder: skipped (Samba not installed)\n"), log=core.Log(None))
    ok, msg = p.set(PW)
    check(ok and msg == "PASSWORD CHANGED", "no Samba: %s" % msg)
    p = core.Password("/h", runner=AppRunner(rc=3, out="linux: ok\n", err="network folder: FAILED\n"), log=core.Log(None))
    ok, msg = p.set(PW)
    check(ok and "KEEPS THE OLD" in msg, "rc 3: changed, folder not: %s" % msg)
    p = core.Password("/h", runner=AppRunner(rc=2, err="refused: root and system accounts are refused " + PW + "\n"), log=core.Log(None))
    ok, msg = p.set(PW)
    check(not ok and PW.upper() not in msg and PW not in msg and "REFUSED" in msg, "a refusal is shown, scrubbed: %s" % msg)
    p = core.Password("/h", runner=AppRunner(rc=1, out="", err=""), log=core.Log(None))
    ok, msg = p.set(PW)
    check(not ok and "EXIT 1" in msg, "a silent failure still says something: %s" % msg)


def test_flow_two_masked_fields_then_the_helper():
    r = AppRunner()
    with tempfile.TemporaryDirectory() as d:
        app, _p = make_app(r, os.path.join(d, "log"))
        open_page(app)
        app.handle("ok")                                   # CHANGE PASSWORD
        check(app.top["id"] == "pw_new" and app.top["kb"].mode == "password", "first field, a password keyboard")
        app.handle(("text", PW))
        v = app.view()
        check(PW not in json.dumps(v, default=str) and v["field"] == core.MASK * len(PW), "the field is masked in the view model")
        app.handle("start")
        check(app.top["id"] == "pw_again", "second field")
        app.handle(("text", PW))
        check(PW not in json.dumps(app.view(), default=str), "second field masked too")
        app.handle("start")
        check(app.pending is not None and r.calls == [], "the write is queued (busy screen), not yet run")
        check(app.stack[-1].get("kb") is None or app.stack[-1]["kb"].text == "", "the typed fields are emptied once submitted")
        for s in app.stack:
            if s["kind"] == "keyboard":
                check(s["kb"].text == "" and "first" not in s, "screen %s holds no password" % s["id"])
        app.run_pending()
        check(r.calls and r.calls[0][1] == PW + "\n", "the helper got the password on stdin")
        check(app.top["id"] == "pw" and app.toast[0] == "ok" and "PASSWORD CHANGED" in app.toast[1], "back on the page: %s" % (app.toast,))
        check(PW not in json.dumps(app.view(), default=str) and PW not in repr(app.toast), "no password anywhere in the model afterwards")
        check(PW not in open(os.path.join(d, "log")).read(), "no password in the log file")


def test_BROKEN_mismatch_short_and_default_never_reach_the_helper():
    r = AppRunner()
    app, _p = make_app(r)
    open_page(app)
    app.handle("ok")
    type_in(app, "abc")
    check(app.top["id"] == "pw_new" and "AT LEAST" in app.top["error"], "BROKEN: too short stays on the first field: %s" % app.top["error"])
    app.top["kb"].text = ""
    type_in(app, "12345")
    check(app.top["id"] == "pw_new" and "DEFAULT" in app.top["error"], "BROKEN: 12345 is refused: %s" % app.top["error"])
    app.top["kb"].text = ""
    type_in(app, PW)
    check(app.top["id"] == "pw_again", "a good first password moves on")
    type_in(app, PW + "x")
    check(app.top["id"] == "pw_new" and "DIFFER" in app.top["error"] and app.top["kb"].text == "",
          "BROKEN: a mismatch goes back to an EMPTY first field with the reason: %s" % app.top.get("error"))
    check(app.pending is None and r.calls == [], "BROKEN: mismatch, short and 12345 never ran the helper")
    check(all(PW not in repr(s.get("first", "")) for s in app.stack), "the first password is not kept after a mismatch")


def test_helper_failure_is_told_and_fields_stay_empty():
    r = AppRunner(rc=2, err="refused: root and system accounts are refused\n")
    app, _p = make_app(r)
    open_page(app)
    app.handle("ok")
    type_in(app, PW)
    type_in(app, PW)
    app.run_pending()
    check(app.toast[0] == "err" and "REFUSED" in app.toast[1], "a refused change says why: %s" % (app.toast,))
    check(all(s["kb"].text == "" for s in app.stack if s["kind"] == "keyboard"), "no password left in any field")


def test_back_out_keeps_nothing():
    app, _p = make_app()
    open_page(app)
    app.handle("ok")
    app.handle(("text", PW))
    app.handle("start")
    app.handle(("text", "zz"))
    app.handle("esc")
    check(app.top["id"] == "pw_new", "MK/esc on the second field goes back one screen")
    app.handle("esc")
    check(app.top["id"] == "pw", "and out to the page")
    check(PW not in repr([s.get("first") for s in app.stack]), "nothing typed is kept on the screens that are left")


def test_page_text_and_home_row():
    app, _p = make_app()
    check("PASSWORD" in [lab for lab, _w in app.top["items"]], "home row PASSWORD")
    open_page(app)
    v = app.view()
    check("NOT 12345" in v["text"] and v["cards"][0]["value"] == "coindojo", "the page says the rules and the login: %s" % v["text"])


if __name__ == "__main__":
    main(globals())
