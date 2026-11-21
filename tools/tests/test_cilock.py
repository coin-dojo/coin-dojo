#!/usr/bin/env python3
"""Tests for tools/cilock.py (#119, #134): host-wide lock with heavy (CILOCK_HEAVY_SLOTS = 2), cabinet (CILOCK_CABINET_SLOTS = 1)
and light (CILOCK_LIGHT_SLOTS = 2) lanes.

Run: python3 tools/tests/test_cilock.py   (stdlib only; a throwaway CILOCK_DIR, never the real lock)
Cases: two heavy holders run together and a third waits; the cabinet lane runs while both heavy slots are held and a 2nd
cabinet run waits; a killed waiter exits 143 without running the child; a closed stderr does not kill a waiter;
with CILOCK_HEAVY_SLOTS=1 heavies serialize; a light runs while a heavy holds; two lights run together and a third waits;
a CI_PRIORITY waiter takes the lock before an earlier normal waiter (no preemption of the holder); a dead holder's
lock and an over-age holder's lock are taken over; the waiting line is printed; the child's exit code passes through.
Broken case: with the lock disabled (CI_NO_LOCK=1) the heavy runs overlap, so "two heavies serialize" must FAIL there.
Time bounds are generous (timing tests elsewhere flaked): every wait polls an event file up to 30 s.
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import time

CILOCK = pathlib.Path(__file__).resolve().parents[1] / "cilock.py"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests", "lib"))
from hktest import check, finish as hk_finish  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)


def env_for(d, **extra):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("CILOCK_", "CI_"))}
    e.update(CILOCK_DIR=str(d / "lock"), CILOCK_POLL_S="0.1", **extra)
    return e


def start(d, lane, name, hold, **extra):
    """cilock run LANE: writes <name>.in, sleeps `hold` seconds, writes <name>.out."""
    script = "import time,pathlib;d=pathlib.Path(%r);(d/%r).write_text(str(time.time()));time.sleep(%s);(d/%r).write_text(str(time.time()))" % (
        str(d), name + ".in", hold, name + ".out")
    return subprocess.Popen([sys.executable, str(CILOCK), "run", lane, "--", sys.executable, "-c", script],
                            env=env_for(d, **extra), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def wait_for(path, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if path.exists() and path.read_text():
            return float(path.read_text())
        time.sleep(0.05)
    return None


def finish(*procs):
    return [p.communicate(timeout=60) for p in procs]


def t(d, name, ext):
    return wait_for(d / (name + "." + ext))


def overlap_case(extra):
    """Run two heavies of 1.5 s each (one heavy slot unless extra says otherwise); True when they never overlapped."""
    d = pathlib.Path(tempfile.mkdtemp(prefix="cilock-"))
    extra = dict({"CILOCK_HEAVY_SLOTS": "1"}, **extra)
    a = start(d, "heavy", "a", 1.5, **extra)
    assert t(d, "a", "in") is not None
    b = start(d, "heavy", "b", 1.5, **extra)
    finish(a, b)
    ai, ao, bi, bo = (t(d, n, e) for n in "ab" for e in ("in", "out"))
    return bi >= ao - 0.01 or ai >= bo - 0.01


def main():
    check(overlap_case({}), "with CILOCK_HEAVY_SLOTS=1 two heavies serialize (the second starts after the first ends)")

    # #134: two heavy slots; a third heavy waits; the cabinet lane never waits for heavy holders
    d = pathlib.Path(tempfile.mkdtemp(prefix="cilock-"))
    h1 = start(d, "heavy", "h1", 3)
    h2 = start(d, "heavy", "h2", 3)
    check(t(d, "h1", "in") is not None and t(d, "h2", "in") is not None and not (d / "h1.out").exists(),
          "two heavy holders run together (default CILOCK_HEAVY_SLOTS=2)")
    h3 = start(d, "heavy", "h3", 0.2, CILOCK_PROGRESS_S="0.3")
    c1 = start(d, "cabinet", "c1", 2.5)
    check(t(d, "c1", "in") is not None and not (d / "h1.out").exists(), "the cabinet lane runs while both heavy slots are held")
    c2 = start(d, "cabinet", "c2", 0.2, CILOCK_PROGRESS_S="0.3")
    time.sleep(1.0)
    check(not (d / "h3.in").exists(), "a third heavy waits while both heavy slots are held")
    check(not (d / "c2.in").exists(), "a second cabinet run waits while the one cabinet slot is held")
    outs = finish(h1, h2, h3, c1, c2)
    check(t(d, "h3", "in") >= min(t(d, "h1", "out"), t(d, "h2", "out")) - 0.01, "the third heavy starts after a heavy slot frees")
    check(t(d, "c2", "in") >= t(d, "c1", "out") - 0.01, "the second cabinet run starts after the first ends")
    check("lane=cabinet" in outs[4][1], "the cabinet waiter's progress line names its lane")
    check(not (d / "lock").exists() and not (d / "lock.heavy.2").exists() and not (d / "lock.cabinet.1").exists(),
          "all heavy and cabinet slots are released after the runs")

    d = pathlib.Path(tempfile.mkdtemp(prefix="cilock-"))
    c1 = start(d, "cabinet", "c1", 2, CILOCK_CABINET_SLOTS="2")
    c2 = start(d, "cabinet", "c2", 2, CILOCK_CABINET_SLOTS="2")
    check(t(d, "c1", "in") is not None and t(d, "c2", "in") is not None and not (d / "c1.out").exists(),
          "CILOCK_CABINET_SLOTS=2 lets two cabinet runs go together")
    finish(c1, c2)

    # a nested run under a holder (CILOCK_HELD=1, as premerge's `make deploy`) takes no lane and cannot deadlock
    d = pathlib.Path(tempfile.mkdtemp(prefix="cilock-"))
    h = start(d, "heavy", "h", 3, CILOCK_HEAVY_SLOTS="1")
    t(d, "h", "in")
    r = subprocess.run([sys.executable, str(CILOCK), "run", "cabinet", "--", sys.executable, "-c", "pass"],
                       env=env_for(d, CILOCK_HELD="1"), capture_output=True, text=True, timeout=20)
    check(r.returncode == 0 and not list(d.glob("lock.cabinet.*")), "CILOCK_HELD=1 skips the lane (nested make deploy under premerge)")
    finish(h)

    # a waiter killed by TERM exits 143 and never runs the child; its lock files do not linger
    d = pathlib.Path(tempfile.mkdtemp(prefix="cilock-"))
    c1 = start(d, "cabinet", "c1", 4)
    t(d, "c1", "in")
    w = start(d, "cabinet", "w", 0.1, CILOCK_PROGRESS_S="0.2")
    time.sleep(0.8)
    w.terminate()
    out, err = w.communicate(timeout=30)
    check(w.returncode == 143 and not (d / "w.in").exists() and "child not started" in err,
          "a waiter killed by TERM exits 143, says the child was not started, and does not run it")
    finish(c1)

    # a closed stderr (`| head`) must not kill a waiting run (#134): it keeps waiting and then runs the child
    d = pathlib.Path(tempfile.mkdtemp(prefix="cilock-"))
    c1 = start(d, "cabinet", "c1", 1.5)
    t(d, "c1", "in")
    script = "import pathlib;pathlib.Path(%r).write_text('ran')" % str(d / "w.ran")
    w = subprocess.Popen([sys.executable, str(CILOCK), "run", "cabinet", "--", sys.executable, "-c", script],
                         env=env_for(d, CILOCK_PROGRESS_S="0.2"), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    w.stderr.close()   # the reader goes away; the next progress line hits a broken pipe
    rc = w.wait(timeout=60)
    check(rc == 0 and (d / "w.ran").exists(), "a waiter whose stderr is closed keeps waiting and runs the child (exit 0)")
    finish(c1)

    d = pathlib.Path(tempfile.mkdtemp(prefix="cilock-"))
    h = start(d, "heavy", "h", 3)
    t(d, "h", "in")
    l1 = start(d, "light", "l1", 3)
    l2 = start(d, "light", "l2", 3)
    check(t(d, "l1", "in") is not None and t(d, "l2", "in") is not None and not (d / "h.out").exists(),
          "two lights run while a heavy holds")
    l3 = start(d, "light", "l3", 0.2, CILOCK_PROGRESS_S="0.3")
    time.sleep(1.0)
    check(not (d / "l3.in").exists(), "a third light waits while both light slots are held")
    outs = finish(h, l1, l2, l3)
    check(t(d, "l3", "in") >= min(t(d, "l1", "out"), t(d, "l2", "out")) - 0.01, "the third light starts after a slot frees")
    check("waiting for" in outs[3][1] and "holder pid" in outs[3][1], "a waiter prints `waiting for <worktree> (holder pid ...)`")

    d = pathlib.Path(tempfile.mkdtemp(prefix="cilock-"))
    h = start(d, "heavy", "h", 2, CILOCK_HEAVY_SLOTS="1")
    t(d, "h", "in")
    n = start(d, "heavy", "normal", 0.2, CILOCK_HEAVY_SLOTS="1")
    time.sleep(0.5)
    p = start(d, "heavy", "prio", 0.2, CI_PRIORITY="1", CILOCK_HEAVY_SLOTS="1")
    finish(h, n, p)
    check(t(d, "prio", "in") < t(d, "normal", "in") and t(d, "prio", "in") >= t(d, "h", "out") - 0.01,
          "a priority waiter goes before an earlier normal waiter, without preempting the holder")

    d = pathlib.Path(tempfile.mkdtemp(prefix="cilock-"))
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    (d / "lock").mkdir()
    (d / "lock" / "info").write_text("%d %d ghost-tree\n" % (dead.pid, time.time()))
    s = start(d, "heavy", "s", 0.1)
    check(t(d, "s", "in") is not None, "a dead holder's lock is taken over")
    check("taken over" in finish(s)[0][1] and not (d / "lock").exists(), "the takeover is logged and the lock is released after")

    d = pathlib.Path(tempfile.mkdtemp(prefix="cilock-"))
    (d / "lock").mkdir()
    (d / "lock" / "info").write_text("%d %d old-tree\n" % (os.getpid(), time.time() - 4000))   # alive, but 4000 s old
    s = start(d, "heavy", "s", 0.1)
    check(t(d, "s", "in") is not None, "a live holder older than the max age (simulated 4000 s > 1800 s) is taken over")
    finish(s)

    d = pathlib.Path(tempfile.mkdtemp(prefix="cilock-"))
    r = subprocess.run([sys.executable, str(CILOCK), "run", "light", "--", sys.executable, "-c", "raise SystemExit(7)"],
                       env=env_for(d), capture_output=True, text=True)
    check(r.returncode == 7 and not (d / "lock.light.1").exists(), "the child's exit code passes through and the slot is released")
    r = subprocess.run([sys.executable, str(CILOCK), "run", "bogus", "x"], env=env_for(d), capture_output=True, text=True)
    check(r.returncode == 2, "a bad lane is a usage error")

    # broken case: without the lock the heavies overlap, so the serialize check must fail
    check(not overlap_case({"CI_NO_LOCK": "1"}), "BROKEN CASE: with CI_NO_LOCK=1 two heavies overlap (the serialize check would FAIL)")

    return hk_finish()


if __name__ == "__main__":
    sys.exit(main())
