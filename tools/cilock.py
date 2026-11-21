#!/usr/bin/env python3
"""cilock.py - host-wide test lock with heavy, cabinet and light lanes (#119, #134; ported from the maintainer's other repo's CI script).

    tools/cilock.py run heavy|cabinet|light [--] CMD [ARG...]

Several sessions/agents run tests on one laptop (and one small Docker VM) and slow each other 3-5x, so they take turns.
  lane     slots (env, default)           for                                       slot dirs
  heavy    CILOCK_HEAVY_SLOTS (2)         `make test`, container builds, premerge   $CILOCK_DIR, $CILOCK_DIR.heavy.<n> (n>=2)
  cabinet  CILOCK_CABINET_SLOTS (1)       deploy dry runs/applies, smoke-launch     $CILOCK_DIR.cabinet.<n>
  light    CILOCK_LIGHT_SLOTS (2)         one test file, lint                       $CILOCK_DIR.light.<n>
Lanes never wait for each other. Worst case on the host: 2 heavy + 1 cabinet + 2 light. The cabinet lane exists because a
deploy dry run is network-bound on the Mac (CPU ~37 % under a premerge) and queued 15-60 min behind heavy runs for nothing;
only one run at a time may talk to the one cabinet. `make deploy`, `make smoke-launch` and the other cabinet targets take
the cabinet lane themselves (mk/cabinet.mk CABINET_LOCK): agents need no wrapper.
Each slot is a dir made with mkdir (atomic) holding `info` = "<pid> <start epoch> <worktree>". A holder whose pid is dead
(or an ownerless dir older than a minute) is taken over; so is a live one older than CILOCK_MAX_AGE_S (default 1800 =
hung run). Takeovers go through a guard dir so two waiters never both take over. Waiters print
`cilock: waiting for <worktree> (holder pid N, held Ns)` every CILOCK_PROGRESS_S (30 s).
CI_PRIORITY=1 (make merge's premerge only) registers $CILOCK_DIR.prio/<pid>: normal heavy waiters do not even try
while a live priority waiter exists (no preemption of a holder; dead or over-age entries are removed); the cabinet and
light lanes ignore priority. The CPU gate (tools/cpu-gate.sh) is separate and unchanged.
A waiter that is killed (TERM, HUP, INT) says so and exits 128+N without running the child; a closed stderr never
kills a waiter (#134).
The child runs with CILOCK_HELD=1, so a nested `make test` or `make deploy` under premerge does not queue behind (or
deadlock on) its own parent.
Skipped: CI_NO_LOCK=1, CILOCK_HELD=1. Lock dir default /tmp/coin-dojo-ci-<uid>.lock (/tmp, not $TMPDIR: sandboxed
sessions differ in TMPDIR). The child's exit code is returned; an unusable lock dir warns and runs without the queue.
"""
import os
import shutil
import signal
import subprocess
import sys
import time

LOCK = os.environ.get("CILOCK_DIR") or "/tmp/coin-dojo-ci-%d.lock" % os.getuid()


def _num(name, default):
    try:
        v = float(os.environ.get(name, ""))
        return v if v > 0 else default
    except ValueError:
        return default


MAX_AGE = _num("CILOCK_MAX_AGE_S", 1800)
PROGRESS = _num("CILOCK_PROGRESS_S", 30)
POLL = _num("CILOCK_POLL_S", 1)
SLOTS = int(_num("CILOCK_LIGHT_SLOTS", 2))
HEAVY_SLOTS = int(_num("CILOCK_HEAVY_SLOTS", 2))
CABINET_SLOTS = int(_num("CILOCK_CABINET_SLOTS", 1))
LANES = ("heavy", "cabinet", "light")
TREE = os.path.basename(os.getcwd())


def say(msg):
    try:
        print("cilock: " + msg, file=sys.stderr, flush=True)
    except (OSError, ValueError):   # closed stderr (`| head`): never let a log line kill a waiter (#134)
        try:   # and no flush error (exit 120) at interpreter exit either
            sys.stderr = open(os.devnull, "w")
        except OSError:
            pass


def lane_dirs(lane):
    if lane == "heavy":
        return [LOCK] + ["%s.heavy.%d" % (LOCK, n) for n in range(2, HEAVY_SLOTS + 1)]
    n = CABINET_SLOTS if lane == "cabinet" else SLOTS
    return ["%s.%s.%d" % (LOCK, lane, i) for i in range(1, n + 1)]


def read_info(d):
    """(pid, start, tree) of the holder in dir d, or (None, 0, '') when unreadable."""
    try:
        parts = open(os.path.join(d, "info")).read().split(None, 2)
        return int(parts[0]), int(parts[1]), (parts[2].strip() if len(parts) > 2 else "")
    except (OSError, ValueError, IndexError):
        return None, 0, ""


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def write_info(d):
    tmp = os.path.join(d, "info.%d" % os.getpid())
    with open(tmp, "w") as f:
        f.write("%d %d %s\n" % (os.getpid(), time.time(), TREE))
    os.replace(tmp, os.path.join(d, "info"))


def stale(d):
    """Reason the holder of d may be replaced (dead_pid|no_owner|max_age) or ''."""
    pid, start, _ = read_info(d)
    if pid is None:
        try:
            return "no_owner" if time.time() - os.stat(d).st_mtime > 60 else ""
        except OSError:
            return ""
    if not alive(pid):
        return "dead_pid"
    return "max_age" if time.time() - start > MAX_AGE else ""


def takeover(d):
    guard = d + ".guard"
    try:
        os.mkdir(guard)
    except OSError:
        try:
            if time.time() - os.stat(guard).st_mtime > 60:
                os.rmdir(guard)   # an abandoned guard
        except OSError:
            pass
        return False
    try:
        if not stale(d):
            return False
        seen = read_info(d)[0]
        old = "%s.old.%d" % (d, os.getpid())
        os.rename(d, old)
        if read_info(old)[0] != seen:   # a fresh lock slipped in: put it back
            try:
                os.rename(old, d)
            except OSError:
                shutil.rmtree(old, ignore_errors=True)
            return False
        shutil.rmtree(old, ignore_errors=True)
        os.mkdir(d)
        write_info(d)
        return True
    except OSError:
        return False
    finally:
        try:
            os.rmdir(guard)
        except OSError:
            pass


def try_lock(d):
    """True = taken (also by takeover), False = held; OSError = lock dir unusable."""
    try:
        os.mkdir(d)
    except FileExistsError:
        pass
    else:
        try:
            write_info(d)
        except OSError:
            shutil.rmtree(d, ignore_errors=True)   # no ownerless dir left behind
            raise
        return True
    reason = stale(d)
    who = read_info(d)
    if reason and takeover(d):
        say("lock of %s (pid %s) is %s: taken over" % (who[2] or "?", who[0], reason))
        return True
    return False


def prio_dir():
    return LOCK + ".prio"


def prio_blocker():
    """A live priority waiter other than us (dead / over-age entries are removed), else None."""
    try:
        names = os.listdir(prio_dir())
    except OSError:
        return None
    for n in names:
        f = os.path.join(prio_dir(), n)
        try:
            started = float(open(f).read().strip() or 0)
            pid = int(n)
        except (OSError, ValueError):
            started, pid = 0, 0
        if pid and pid != os.getpid() and alive(pid) and time.time() - started <= MAX_AGE:
            return pid
        if pid != os.getpid():
            try:
                os.unlink(f)
            except OSError:
                pass
    return None


def acquire(lane):
    """Block until a lane lock is ours; return the dir to release, or None (unusable lock dir)."""
    t0 = last = 0.0
    t0 = time.time()
    prio = lane == "heavy" and os.environ.get("CI_PRIORITY") == "1"
    pfile = os.path.join(prio_dir(), str(os.getpid()))
    if prio:
        try:
            os.makedirs(prio_dir(), exist_ok=True)
            open(pfile, "w").write("%d\n" % time.time())
        except OSError:
            prio = False
    try:
        while True:
            dirs = lane_dirs(lane)
            if lane == "heavy" and not prio and prio_blocker():
                dirs = []   # a priority run queues first: do not even try
            for d in dirs:
                try:
                    if try_lock(d):
                        return d
                except OSError as e:
                    say("WARN cannot create lock %s (%s): running without the queue" % (d, e))
                    return None
            if time.time() - last >= PROGRESS:
                last = time.time()
                pid, start, tree = read_info(dirs[0] if dirs else LOCK)
                held = int(last - start) if start else 0
                say("waiting for %s (holder pid %s, held %ds, waited %ds) [lane=%s]"
                    % (tree or "another run", pid or "?", held, int(last - t0), lane))
            time.sleep(POLL)
    finally:
        if prio:
            try:
                os.unlink(pfile)
                os.rmdir(prio_dir())
            except OSError:
                pass


def release(d):
    """Remove the lock only when it is still ours (a max-age takeover may have replaced it)."""
    pid = read_info(d)[0]
    if pid is None or pid == os.getpid():
        shutil.rmtree(d, ignore_errors=True)


def main(argv):
    if len(argv) < 3 or argv[0] != "run" or argv[1] not in LANES:
        print("usage: cilock.py run heavy|cabinet|light [--] CMD [ARG...]", file=sys.stderr)
        return 2
    lane, cmd = argv[1], argv[2:]
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("usage: cilock.py run heavy|cabinet|light [--] CMD [ARG...]", file=sys.stderr)
        return 2
    env = dict(os.environ, CILOCK_HELD="1")
    if os.environ.get("CI_NO_LOCK") == "1" or os.environ.get("CILOCK_HELD") == "1":
        return subprocess.call(cmd, env=env)
    def on_signal(num, _frame):   # release the lock on kill; say why a waiter ends without running the child
        say("got signal %d: exiting with %d (%s)" % (num, 128 + num, "child not started" if not started else "stopping"))
        sys.exit(128 + num)
    started = False
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, on_signal)
    held = acquire(lane)
    started = True
    try:
        return subprocess.call(cmd, env=env)
    finally:
        if held:
            release(held)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
