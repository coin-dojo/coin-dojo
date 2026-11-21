#!/usr/bin/env python3
"""Tests for esde_return.py (#224): after SETTINGS, flush ES-DE's stale stick presses and send exactly one Back.

No hardware: the sequence runs on a fake Hw with a simulated clock; the sysfs scan, the uinput node lookup and the
cage check on fake trees; the uinput setup and the held check on a fake ioctl; the jitter filter on a real pipe; the
delayed keyboard removal in a real forked child. Broken cases: no /dev/uinput writes nothing; a stick without
MSC_SCAN or not writable does not stop the others (named in the log); no stick still sends Back; a button held past
the limit is flushed but gets no Back; cage that never opens the keyboard still gets the Back (logged) but not
before SETTLE; a press during the quiet wait moves the Back after it; analog jitter does not count as input; a
doubled Back payload is caught by the counter.

#225 early keyboard: the plain run uses the holder's keyboard (fd over a real unix socket) and no second one; a
silent holder (accepts, sends nothing), a dead one (stale socket) or none = it makes its own: still exactly one Back.
The holder never writes a key, hands the fd over once, and goes when its parent (the hook) is gone.

    python3 skills/settings/tests/test_esde_return.py
"""
import os
import shutil
import struct
import sys
import socket
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APPDIR = os.path.dirname(HERE)
REPO = os.path.normpath(os.path.join(APPDIR, "..", ".."))
sys.path.insert(0, APPDIR)
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
import esde_return as er  # noqa: E402
from hktest import check, main  # noqa: E402


def events(data):
    return [struct.unpack(er.FMT, data[o:o + er.SZ])[2:] for o in range(0, len(data), er.SZ)]


def backs(data):
    """Backspace presses (value 1) in a written payload."""
    return sum(1 for t, c, v in events(data) if t == er.EV_KEY and c == er.KEY_BACKSPACE and v == 1)


class FakeHw:
    """held_until: time the stick is released; taps: times of extra presses; cage_at: time cage opens the keyboard
    (None = never)."""

    def __init__(self, held_until=0.0, taps=(), kbd_error=None, open_error=(), cage_at=0.2):
        self.t = 0.0
        self.held_until, self.taps = held_until, list(taps)
        self.kbd_error, self.open_error, self.cage_at = kbd_error, set(open_error), cage_at
        self.writes = []                 # (fd, bytes, time)
        self.calls = []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def open_stick(self, path):
        if path in self.open_error:
            raise OSError(13, "Permission denied")
        self.calls.append(("open", path))
        return "stick:" + path

    def held(self, fd):
        return self.t < self.held_until

    def activity(self, fds, timeout):
        end = self.t + timeout
        hit = any(self.t < x <= end for x in self.taps)
        self.t = end
        return hit

    def write(self, fd, data):
        self.writes.append((fd, data, self.t))

    early = None                       # #225: what take() gives: an fd, or None = OSError (no holder)

    def take(self, path, timeout=er.HANDOFF_WAIT):
        self.calls.append(("take", path))
        if self.early is None:
            raise OSError(111, "Connection refused")
        return self.early

    def keyboard(self, path):
        if self.kbd_error:
            raise OSError(2, "No such file or directory")
        self.calls.append(("kbd", path))
        return "kbd"

    def kbd_node(self, fd):
        return "/dev/input/event9"

    def kbd_opened(self, node):
        return self.cage_at is not None and self.t >= self.cage_at

    def release(self, fd, after):
        self.calls.append(("release", fd, after))

    def close(self, fd):
        self.calls.append(("close", fd))


S3, S7 = ("/dev/input/event3", True, "USB gamepad"), ("/dev/input/event7", True, "USB gamepad")
LOGS = []


def log(msg):
    LOGS.append(msg)


def run(hw, sticks=(S3,), **kw):
    return er.run(hw, list(sticks), "/dev/uinput", log, **kw)


def kb_writes(hw):
    return [w for w in hw.writes if w[0] == "kbd"]


def flushed(hw):
    return sorted(w[0] for w in hw.writes if w[0] != "kbd")


def test_payloads():
    f = events(er.flood_bytes(10))
    check(len(f) == 20 and all(t in (er.EV_MSC, er.EV_SYN) for t, _c, _v in f),
          "flood = MSC_SCAN + SYN_REPORT pairs only: no button or axis ever changes")
    back = b"".join(er.ev(*e) for e in er.BACK_EVENTS)
    check(backs(back) == 1 and events(back) == [(1, 14, 1), (0, 0, 0), (1, 14, 0), (0, 0, 0)],
          "Back = Backspace down, SYN, up, SYN (ES-DE keyboard B)")
    check(backs(back * 2) == 2, "BROKEN CASE: a doubled Back payload is counted as 2 (the checks below would fail)")


def test_back_sent_exactly_once():
    hw = FakeHw()
    out = run(hw, sticks=(S3, S7))
    kb = kb_writes(hw)
    fl = [w for w in hw.writes if w[0] != "kbd"]
    check(out == "back", "a released, quiet stick + uinput + cage ready -> back (%s)" % out)
    check(len(kb) == 1 and backs(kb[0][1]) == 1, "exactly one Back on the keyboard")
    check(flushed(hw) == ["stick:/dev/input/event3", "stick:/dev/input/event7"]
          and all(len(events(w[1])) == 2 * er.FLOOD for w in fl), "every stick flushed once, FLOOD pairs")
    check(hw.writes.index(kb[0]) > max(hw.writes.index(w) for w in fl), "the flush comes before the Back")
    check(kb[0][2] >= 0.2 and kb[0][2] >= er.QUIET, "Back only once cage holds the keyboard and the stick was quiet")
    check(kb[0][2] < 1.0, "no fixed long wait when cage is ready early (%.2f s)" % kb[0][2])
    check(("release", "kbd", er.LINGER) in hw.calls and er.LINGER >= 1.0,
          "the keyboard is removed later by release() (forked child, %.1f s), not right after the write" % er.LINGER)
    check(("close", "stick:/dev/input/event3") in hw.calls, "stick fds closed")


def test_cage_never_opens_the_keyboard():
    LOGS[:] = []
    hw = FakeHw(cage_at=None)
    check(run(hw) == "back", "cage never opens the keyboard -> still one Back")
    kb = kb_writes(hw)
    check(len(kb) == 1 and kb[0][2] >= er.SETTLE, "BROKEN CASE: ...but not before SETTLE s of waiting (%.2f)" % kb[0][2])
    check(any("cage never opened the keyboard" in m for m in LOGS), "and it is logged")


def test_waits_for_release_and_double_tap():
    hw = FakeHw(held_until=1.0)
    check(run(hw) == "back", "held for 1 s, then released -> back")
    t = [w[2] for w in hw.writes]
    # held is polled every 0.05 s (the activity wait), so the release is seen within one step
    check(min(t) >= 1.0 - 0.05 + er.QUIET, "nothing written while held or before QUIET s after the release (%.2f)" % min(t))
    hw = FakeHw(taps=(0.25,))                   # a second LK just before the Back would have gone (0.3 s)
    check(run(hw) == "back", "double tap at exit -> still one back")
    t = [w[2] for w in hw.writes]
    check(min(t) >= 0.25 + er.QUIET, "BROKEN CASE: the press during the wait moves the flush + Back after it (%.2f)" % min(t))


def test_fail_safe_paths():
    LOGS[:] = []
    hw = FakeHw(kbd_error=True)
    check(run(hw) == "no-uinput" and not hw.writes, "BROKEN CASE: no /dev/uinput -> nothing written at all")
    hw = FakeHw()
    check(run(hw, sticks=()) == "back" and len(kb_writes(hw)) == 1 and not flushed(hw),
          "BROKEN CASE: no stick -> Back only (keyboard-only use)")
    hw = FakeHw()
    out = run(hw, sticks=(("/dev/input/event3", False, "Old pad"), S7))
    check(out == "back" and flushed(hw) == ["stick:/dev/input/event7"] and len(kb_writes(hw)) == 1,
          "BROKEN CASE: one stick without MSC_SCAN -> the other is flushed, Back sent")
    check(any("Old pad" in m and "MSC_SCAN" in m for m in LOGS), "the stick without MSC_SCAN is named in the log")
    hw = FakeHw(open_error=("/dev/input/event3",))
    out = run(hw, sticks=(S3, S7))
    check(out == "back" and flushed(hw) == ["stick:/dev/input/event7"],
          "BROKEN CASE: one stick not writable -> the other is flushed, Back sent")
    hw = FakeHw(held_until=99.0)
    out = run(hw)
    check(out == "held" and flushed(hw) == ["stick:/dev/input/event3"] and not kb_writes(hw),
          "BROKEN CASE: button held past MAX_WAIT -> flushed, no Back")
    check(("release", "kbd", er.LINGER) in hw.calls, "held: the keyboard is still removed")
    check(any("held" in m for m in LOGS) and any("uinput" in m for m in LOGS), "each refusal is logged")


# ---- scan + hardware layer on fakes ------------------------------------------------------------------------
def _dev(root, n, name, key="", abs_="", msc=""):
    d = os.path.join(root, n, "device", "capabilities")
    os.makedirs(d)
    with open(os.path.join(root, n, "device", "name"), "w") as f:
        f.write(name + "\n")
    for k, v in (("key", key), ("abs", abs_), ("msc", msc)):
        with open(os.path.join(d, k), "w") as f:
            f.write(v + "\n")


def test_scan():
    root = tempfile.mkdtemp()
    try:
        stick_keys = "3ff00000000 0 0 0 0"            # 288..297, measured on the cabinet (stickmouse.py)
        _dev(root, "event1", "USB gamepad", stick_keys, "30003", "10")       # X, Y, hat; MSC_SCAN (bit 4)
        _dev(root, "event4", "Old pad", stick_keys, "3", "0")                # no MSC_SCAN
        _dev(root, "event0", "pwr_button", "100000 0 0 0", "", "")
        _dev(root, "event5", "AT keyboard", "fffffffffffffffe", "", "10")
        _dev(root, "event6", "coin-dojo-stickmouse", stick_keys, "3", "10")
        _dev(root, "event7", er.NAME, stick_keys, "3", "10")
        got = er.scan(root, "/dev/input")
        check(got == [("/dev/input/event1", True, "USB gamepad"), ("/dev/input/event4", False, "Old pad")],
              "scan: sticks only, MSC_SCAN + name noted; keys/keyboards and our coin-dojo-* devices skipped (%r)" % got)
        check(er.scan(os.path.join(root, "missing")) == [], "no sysfs -> no stick")
    finally:
        shutil.rmtree(root)


class FakeIoctl:
    def __init__(self, key_bits=(), absvals=None, sysname=b"input42", log=None):
        self.calls, self.key_bits, self.absvals, self.sysname, self.log = [], key_bits, absvals or {}, sysname, log

    def __call__(self, fd, req, arg=0):
        self.calls.append((req, arg))
        if self.log:
            with open(self.log, "a") as f:
                f.write("%x\n" % req)
        if req == er.EVIOCGKEY:
            for b in self.key_bits:
                arg[b // 8] |= 1 << (b % 8)
            return 0
        if req == er.UI_GET_SYSNAME:
            arg[:len(self.sysname)] = self.sysname
            return 0
        if er.EVIOCGABS <= req < er.EVIOCGABS + 64:
            ax = req - er.EVIOCGABS
            if ax not in self.absvals:
                raise OSError(22, "Invalid argument")
            v, lo, hi = self.absvals[ax]
            return struct.pack("iiiiii", v, lo, hi, 0, 0, 0)
        return 0


def test_held():
    check(er.Hw(ioctl=FakeIoctl(key_bits=(291,))).held(3), "LK (291) down -> held")
    hw = er.Hw(ioctl=FakeIoctl(absvals={er.ABS_HAT0X: (0, -1, 1), er.ABS_HAT0Y: (0, -1, 1), er.ABS_X: (128, 0, 255)}))
    check(not hw.held(3), "nothing down, hat centred, X centred -> not held")
    check(er.Hw(ioctl=FakeIoctl(absvals={er.ABS_HAT0Y: (1, -1, 1)})).held(3), "hat down -> held")
    check(er.Hw(ioctl=FakeIoctl(absvals={er.ABS_X: (250, 0, 255)})).held(3), "analog X far right -> held")
    check(not er.Hw(ioctl=FakeIoctl(key_bits=(28,))).held(3), "a keyboard key bit (< 0x100) is not a stick button")


def test_activity_ignores_jitter():
    hw = er.Hw()
    r, w = os.pipe()
    try:
        hw.ranges[r] = {er.ABS_X: (0, 255), er.ABS_Y: (0, 255)}
        for name, evs, want in (
                ("analog jitter around the centre", [(er.EV_ABS, er.ABS_X, 131), (er.EV_ABS, er.ABS_Y, 124),
                                                     (er.EV_SYN, 0, 0)], False),
                ("another axis (throttle) moving", [(er.EV_ABS, 2, 255), (er.EV_SYN, 0, 0)], False),
                ("MSC_SCAN only", [(er.EV_MSC, er.MSC_SCAN, 1), (er.EV_SYN, 0, 0)], False),
                ("a button press", [(er.EV_KEY, 291, 1), (er.EV_SYN, 0, 0)], True),
                ("a hat change", [(er.EV_ABS, er.ABS_HAT0Y, 0), (er.EV_SYN, 0, 0)], True),
                ("analog X past half deflection", [(er.EV_ABS, er.ABS_X, 250), (er.EV_SYN, 0, 0)], True)):
            os.write(w, b"".join(er.ev(*e) for e in evs))
            got = hw.activity([r], 0.5)
            label = ("BROKEN CASE: %s is not input" if not want else "%s counts as input") % name
            check(got == want, label)
    finally:
        os.close(r)
        os.close(w)


def test_keyboard_node_and_cage():
    d = tempfile.mkdtemp()
    try:
        sysfs = os.path.join(d, "sys")
        os.makedirs(os.path.join(sysfs, "input42", "event9"))
        os.makedirs(os.path.join(sysfs, "input42", "capabilities"))
        hw = er.Hw(ioctl=FakeIoctl(), sysfs=sysfs, proc=os.path.join(d, "proc"))
        check(hw.kbd_node(5) == "/dev/input/event9", "UI_GET_SYSNAME inputN -> /dev/input/eventM")
        check(er.Hw(ioctl=FakeIoctl(sysname=b"input7"), sysfs=sysfs).kbd_node(5) is None,
              "BROKEN CASE: unknown sysname -> None (the cage wait then times out, logged)")
        proc = os.path.join(d, "proc")
        for pid, comm, links in (("100", "cage", ["/dev/input/event0", "/dev/input/event3"]),
                                 ("200", "es-de", ["/dev/input/event9"])):
            os.makedirs(os.path.join(proc, pid, "fd"))
            with open(os.path.join(proc, pid, "comm"), "w") as f:
                f.write(comm + "\n")
            for i, target in enumerate(links):
                os.symlink(target, os.path.join(proc, pid, "fd", str(i + 3)))
        os.makedirs(os.path.join(proc, "self-ish"))
        check(not hw.kbd_opened("/dev/input/event9"),
              "BROKEN CASE: another process (es-de) holding the node is not cage")
        os.symlink("/dev/input/event9", os.path.join(proc, "100", "fd", "9"))
        check(hw.kbd_opened("/dev/input/event9"), "cage holds /dev/input/event9 -> opened")
        check(not hw.kbd_opened(None), "no node -> not opened")
    finally:
        shutil.rmtree(d)


def test_uinput_setup_and_delayed_release():
    d = tempfile.mkdtemp()
    try:
        path = os.path.join(d, "uinput")
        open(path, "w").close()
        ilog = os.path.join(d, "ioctl.log")
        io = FakeIoctl(log=ilog)
        hw = er.Hw(ioctl=io)
        fd = hw.keyboard(path)
        hw.write(fd, b"".join(er.ev(*e) for e in er.BACK_EVENTS))
        reqs = [r for r, _a in io.calls]
        setup = [a for r, a in io.calls if r == er.UI_DEV_SETUP]
        check((er.UI_SET_KEYBIT, er.KEY_BACKSPACE) in io.calls and reqs.count(er.UI_SET_KEYBIT) == 1,
              "uinput keyboard has one key: Backspace")
        check(setup and er.NAME.encode() in setup[0] and len(setup[0]) == 92, "uinput_setup: 92 bytes, our name")
        t0 = time.monotonic()
        hw.release(fd, 0.3)
        took = time.monotonic() - t0
        check(took < 0.2, "release() returns at once (%.3f s): the hook does not wait for the removal" % took)
        destroy = "%x" % er.UI_DEV_DESTROY
        with open(ilog) as f:
            check(destroy not in f.read().split(), "not destroyed yet right after release()")
        deadline = time.monotonic() + 5
        done = False
        while time.monotonic() < deadline and not done:
            time.sleep(0.05)
            with open(ilog) as f:
                done = destroy in f.read().split()
        check(done, "the forked child destroys the keyboard later")
        with open(path, "rb") as f:
            check(backs(f.read()) == 1, "the device got exactly one Backspace press")
        bad = er.Hw(ioctl=lambda *a: (_ for _ in ()).throw(OSError(25, "Inappropriate ioctl")))
        try:
            bad.keyboard(path)
            check(False, "BROKEN CASE: a failing uinput ioctl raises")
        except OSError:
            check(True, "BROKEN CASE: a failing uinput ioctl raises (run() turns it into no-uinput)")
    finally:
        shutil.rmtree(d)


def test_main():
    d = tempfile.mkdtemp()
    try:
        logf = os.path.join(d, "log", "r.log")
        env = {"HK_RETURN_LOG": logf, "HK_RETURN_SYSFS": os.path.join(d, "nosys"), "HOME": d,
               "HK_RETURN_UINPUT": os.path.join(d, "no-uinput")}
        check(er.main(["--bogus"], env) == 2, "usage -> 2")
        check(er.main([], env) == 0, "no uinput -> exit 0")
        with open(logf) as f:
            check("no uinput" in f.read(), "and it is logged to HK_RETURN_LOG")
        env2 = dict(env, HK_RETURN_DEVICES="/dev/input/event3", HK_RETURN_QUIET="0")
        hw = FakeHw()
        check(er.main([], env2, hw=hw) == 0 and sum(backs(w[1]) for w in kb_writes(hw)) == 1
              and flushed(hw) == ["stick:/dev/input/event3"], "HK_RETURN_DEVICES skips the scan; flush + one Back")

        class Boom(FakeHw):
            def held(self, fd):
                raise RuntimeError("boom")
        check(er.main([], env2, hw=Boom()) == 0, "BROKEN CASE: an unexpected error still exits 0 (ES-DE waits on it)")
        check(er.main(["--dry-run"], env2, hw=FakeHw()) == 0, "--dry-run exits 0")
    finally:
        shutil.rmtree(d)


# ---- #225 early keyboard ---------------------------------------------------------------------------------
def test_run_uses_the_early_keyboard():
    LOGS[:] = []
    hw = FakeHw()
    hw.early = "kbd"
    out = run(hw, early="/run/x.sock")
    check(out == "back" and ("kbd", "/dev/uinput") not in hw.calls and len(kb_writes(hw)) == 1,
          "held keyboard taken -> no second keyboard made, exactly one Back")
    check(any("Back sent (keyboard /dev/input/event9 early" in m for m in LOGS), "the Back line says `early`")
    for name, early in (("no holder (connection refused)", None),):
        LOGS[:] = []
        hw = FakeHw()
        hw.early = early
        out = run(hw, early="/run/x.sock")
        check(out == "back" and ("kbd", "/dev/uinput") in hw.calls and len(kb_writes(hw)) == 1,
              "BROKEN CASE: %s -> own keyboard (#224 path), still exactly one Back" % name)
        check(any("no early keyboard" in m for m in LOGS) and any(" new, cage" in m for m in LOGS), "  ...logged")
    hw = FakeHw()
    run(hw)
    check(("take", None) not in hw.calls and not any(c[0] == "take" for c in hw.calls), "no socket given -> no take")


def _short_dir():
    return tempfile.mkdtemp(prefix="hk225")          # AF_UNIX paths are short (104 bytes on macOS)


def test_take_from_silent_or_dead_holder():
    d = _short_dir()
    try:
        path = os.path.join(d, "s")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(1)
        t0 = time.monotonic()
        try:
            er.Hw().take(path, timeout=0.3)
            check(False, "BROKEN CASE: a silent holder (accepts, sends nothing) raises")
        except OSError:
            check(time.monotonic() - t0 < 1.5, "BROKEN CASE: a silent holder raises after the timeout, no hang")
        srv.close()                                   # dead holder: the socket file stays, nobody listens
        try:
            er.Hw().take(path, timeout=0.3)
            check(False, "BROKEN CASE: a dead holder's stale socket raises")
        except OSError:
            check(True, "BROKEN CASE: a dead holder's stale socket raises (-> own keyboard)")
        try:
            er.Hw().take(os.path.join(d, "none"), timeout=0.3)
            check(False, "no socket raises")
        except OSError:
            check(True, "no socket raises (-> own keyboard)")
    finally:
        shutil.rmtree(d)


def test_hold_hands_over_the_real_fd():
    d = _short_dir()
    try:
        path, dev = os.path.join(d, "s"), os.path.join(d, "uinput")
        open(dev, "w").close()
        with open(path, "w") as f:                     # a stale socket file from a killed holder
            f.write("x")
        hw = er.Hw(ioctl=FakeIoctl())
        res = []
        th = threading.Thread(target=lambda: res.append(er.hold(hw, dev, path, log, os.getppid(), poll=0.05)))
        th.start()
        fd = None
        deadline = time.monotonic() + 5
        while fd is None and time.monotonic() < deadline:
            try:
                fd = er.Hw().take(path, timeout=1)
            except OSError:
                time.sleep(0.05)
        th.join(5)
        check(fd is not None and res == ["given"], "holder -> plain run: the keyboard fd crosses the socket (%r)" % res)
        check(fd is not None and os.fstat(fd).st_ino == os.stat(dev).st_ino,
              "it is the holder's own open device (same file), not a new one")
        check(os.path.getsize(dev) == 0, "BROKEN CASE: the holder wrote no key (only the plain run sends the Back)")
        check(not os.path.exists(path), "the socket is removed after the hand-over (a stale one was replaced)")
        if fd is not None:
            os.write(fd, b"".join(er.ev(*e) for e in er.BACK_EVENTS))
            os.close(fd)
            with open(dev, "rb") as f:
                check(backs(f.read()) == 1, "the plain run writes its one Back through the handed-over fd")
    finally:
        shutil.rmtree(d)


class HoldHw:
    """Fake for hold(): parent changes at parent_gone (time), a connection comes at conn_at."""

    def __init__(self, kbd_error=False, parent_gone=None, conn_at=None, listen_error=False):
        self.t, self.calls = 0.0, []
        self.kbd_error, self.parent_gone, self.conn_at, self.listen_error = kbd_error, parent_gone, conn_at, listen_error

    def keyboard(self, path):
        if self.kbd_error:
            raise OSError(2, "No such file or directory")
        self.calls.append("kbd")
        return "kbd"

    def listen(self, path):
        if self.listen_error:
            raise OSError(98, "Address in use")
        self.calls.append("listen")
        return "srv"

    def parent(self):
        return 1 if self.parent_gone is not None and self.t >= self.parent_gone else 42

    def accept(self, srv, timeout):
        self.t += timeout
        if self.t > 60:                                   # a holder that never notices the hook left: fail, not hang
            raise RuntimeError("holder still waiting after 60 s (parent check missing)")
        if self.conn_at is not None and self.t >= self.conn_at:
            self.conn_at = None
            return "conn"
        return None

    def give(self, conn, fd):
        self.calls.append(("give", fd))

    def unlisten(self, srv, path):
        self.calls.append("unlisten")

    def close(self, fd):
        self.calls.append(("close", fd))

    def write(self, fd, data):
        self.calls.append(("WRITE", fd))


def test_hold_lifecycle():
    LOGS[:] = []
    for name, kw, want in (("plain run connects", {"conn_at": 3.0}, "given"),
                           ("the hook goes without taking it", {"parent_gone": 2.0}, "orphan"),
                           ("no uinput", {"kbd_error": True}, "no-uinput"),
                           ("no socket", {"listen_error": True}, "no-socket")):
        hw = HoldHw(**kw)
        out = er.hold(hw, "/dev/uinput", "/run/x.sock", log, 42, poll=0.5)
        check(out == want, "hold: %s -> %s (%s)" % (name, want, out))
        check(not any(c[0] == "WRITE" for c in hw.calls if isinstance(c, tuple)),
              "BROKEN CASE: %s: the holder never writes a key" % name)
        if want != "no-uinput":
            check(("close", "kbd") in hw.calls, "  ...its copy of the keyboard is closed on the way out")
        if want in ("given", "orphan"):
            check("unlisten" in hw.calls, "  ...and the socket removed")
    hw = HoldHw(parent_gone=2.0)
    er.hold(hw, "/dev/uinput", "/run/x.sock", log, 42, poll=0.5)
    check(hw.t <= 2.5, "BROKEN CASE: an orphaned holder goes within one poll of the hook leaving (%.1f s)" % hw.t)


def test_main_hold_and_socket_path():
    check(er.sock_path({"XDG_RUNTIME_DIR": "/run/user/1000"}) == "/run/user/1000/" + er.SOCK_NAME
          and er.sock_path({"HK_RETURN_SOCK": "/x"}) == "/x", "socket in XDG_RUNTIME_DIR (HK_RETURN_SOCK overrides)")
    d = _short_dir()
    try:
        env = {"HK_RETURN_LOG": os.path.join(d, "r.log"), "HOME": d, "HK_RETURN_UINPUT": os.path.join(d, "none"),
               "HK_RETURN_SOCK": os.path.join(d, "s"), "HK_RETURN_SYSFS": os.path.join(d, "nosys")}
        check(er.main(["--hold"], env) == 0, "--hold without uinput exits 0")
        with open(env["HK_RETURN_LOG"]) as f:
            check("hold: no uinput keyboard" in f.read(), "  ...and logs it")
        check(er.main(["--hold", "x"], env) == 2, "BROKEN CASE: --hold with an argument -> usage 2")
    finally:
        shutil.rmtree(d)


if __name__ == "__main__":
    main(globals())
