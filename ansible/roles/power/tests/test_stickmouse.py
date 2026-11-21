#!/usr/bin/env python3
"""Tests for stickmouse (#211): pointer logic with a fake clock, a fake evdev stick over a pipe, uinput failures.

Pure stdlib. Run: python3 ansible/roles/power/tests/test_stickmouse.py
Broken cases (test_broken_*): unplugged stick, unusable uinput, wrong mode, short combo, a broken role copy."""
import contextlib
import importlib.util
import io
import os
import re
import struct
import subprocess
import sys
import tempfile

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(ROLE)))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
from hktest import check as _check, main  # noqa: E402


def _load():
    spec = importlib.util.spec_from_file_location("stickmouse", os.path.join(ROLE, "files", "stickmouse.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()
LK, MK, SEL, ST = M.codes()["lk"], M.codes()["mk"], M.codes()["select"], M.codes()["start"]
DEV = "/dev/input/event9"


class Rig:
    def __init__(self, mode="fightcade"):
        self.d = tempfile.TemporaryDirectory()
        self.rt = os.path.join(self.d.name, "coin-dojo")
        os.makedirs(self.rt)
        self.set_mode(mode)
        self.now = 1000.0
        self.out, self.stops = [], []
        self.p = M.Pointer(lambda t, c, v: self.out.append((t, c, v)), clock=lambda: self.now, rt=self.rt,
                           run_stop=lambda: self.stops.append(self.now), mode_every=0.0)
        self.p.add_device(DEV, {M.ABS_X: (0, 255), M.ABS_Y: (0, 255)})

    def set_mode(self, mode):
        with open(os.path.join(self.rt, "current-mode"), "w") as fh:
            fh.write(mode + "\n")

    def ev(self, t, c, v):
        self.p.on_event(DEV, t, c, v)

    def key(self, c, v):
        self.ev(M.EV_KEY, c, v)

    def hat(self, x, y):
        self.ev(M.EV_ABS, M.ABS_HAT0X, x)
        self.ev(M.EV_ABS, M.ABS_HAT0Y, y)

    def run(self, seconds, step=M.TICK):
        t = 0.0
        while t < seconds - 1e-9:
            self.now += step
            t += step
            self.p.tick()

    def rel(self, axis):
        return sum(v for (t, c, v) in self.out if t == M.EV_REL and c == axis)

    def clicks(self):
        return [(c, v) for (t, c, v) in self.out if t == M.EV_KEY]

    def next_mode(self):
        try:
            return open(os.path.join(self.rt, "next-mode")).read().strip()
        except OSError:
            return None


def test_pointer_moves_and_accelerates():
    r = Rig()
    r.hat(1, 0)
    r.run(0.5)
    first = r.rel(M.REL_X)
    _check(first > 0 and r.rel(M.REL_Y) == 0, "hat right moves the pointer right only (%d px in 0.5 s)" % first)
    r.out.clear()
    r.run(0.5)
    second = r.rel(M.REL_X)
    _check(second > first * 1.5, "still held: the next 0.5 s covers more ground (%d > %d), acceleration" % (second, first))
    r.hat(0, 0)
    r.out.clear()
    r.run(0.2)
    _check(r.rel(M.REL_X) == 0, "released: no movement")
    r.hat(-1, 1)
    r.run(0.1)
    slow = r.rel(M.REL_X)
    _check(slow < 0 and r.rel(M.REL_Y) > 0 and abs(slow) < 40, "a new push starts slow again, diagonal works (%d)" % slow)


def test_speed_capped():
    r = Rig()
    r.hat(0, 1)
    r.run(5.0)
    r.out.clear()
    r.run(1.0)
    _check(abs(r.rel(M.REL_Y) - r.p.top) <= 40, "after a long hold the pointer runs at the cap (%d px/s)" % r.rel(M.REL_Y))


def test_analog_axis_and_deadzone():
    r = Rig()
    r.ev(M.EV_ABS, M.ABS_X, 140)     # 255 range, centre 127.5: ~10 % = inside the deadzone
    r.run(0.3)
    _check(not r.out, "small analog wobble inside the deadzone does not move the pointer")
    r.ev(M.EV_ABS, M.ABS_X, 0)
    r.run(0.3)
    _check(r.rel(M.REL_X) < 0, "analog full left moves left")


def test_default_codes_are_the_cabinet_stick():
    _check((LK, MK, SEL, ST) == (291, 290, 296, 297), "defaults = js index 3/2/8/9 + 288 (measured on the cabinet, 2026-11-18)")
    os.environ["STICKMOUSE_BTN_LK"] = "305"
    try:
        _check(M.codes()["lk"] == 305, "STICKMOUSE_BTN_LK still overrides")
    finally:
        del os.environ["STICKMOUSE_BTN_LK"]


def test_clicks():
    r = Rig()
    r.key(LK, 1)
    r.key(LK, 0)
    r.key(MK, 1)
    r.key(MK, 0)
    _check(r.clicks() == [(M.BTN_LEFT, 1), (M.BTN_LEFT, 0), (M.BTN_RIGHT, 1), (M.BTN_RIGHT, 0)],
           "LK = left click, MK = right click, press and release")


def test_toggle_off_on_swallows_click():
    r = Rig()
    r.key(SEL, 1)
    r.key(MK, 1)
    _check(not r.p.enabled and r.clicks() == [], "SELECT+MK turns the pointer off and sends no click")
    r.key(MK, 0)
    r.key(SEL, 0)
    r.hat(1, 0)
    r.key(MK, 1)
    r.key(MK, 0)
    r.run(0.3)
    _check(r.out == [], "while off: no motion, no clicks (the game has the stick)")
    r.hat(0, 0)
    r.key(SEL, 1)
    r.key(MK, 1)
    r.key(MK, 0)
    r.key(SEL, 0)
    _check(r.p.enabled and r.clicks() == [], "SELECT+MK again turns it on, still no stray click")
    r.key(MK, 1)
    r.key(MK, 0)
    _check(r.clicks() == [(M.BTN_RIGHT, 1), (M.BTN_RIGHT, 0)], "and clicks work again afterwards")


def test_toggle_with_held_click_releases_it():
    r = Rig()
    r.key(MK, 1)
    r.key(SEL, 1)     # MK first, SELECT second still counts as the combo
    _check(not r.p.enabled and r.clicks() == [(M.BTN_RIGHT, 1), (M.BTN_RIGHT, 0)],
           "toggling while MK is already down releases the click, no stuck button")


def test_exit_combo_timing():
    for enabled, label in ((True, "pointer on"), (False, "pointer off")):
        r = Rig()
        r.p.enabled = enabled
        r.key(SEL, 1)
        r.key(ST, 1)
        r.run(1.9)
        _check(r.stops == [] and r.next_mode() is None, "1.9 s of SELECT+START does nothing (%s)" % label)
        r.run(0.2)
        _check(len(r.stops) == 1 and r.next_mode() == "menu", "2.0 s -> next-mode=menu and the stop hook runs once (%s)" % label)
        r.run(3.0)
        _check(len(r.stops) == 1, "still held: the hook is not called again (%s)" % label)
        r.key(ST, 0)
        r.key(SEL, 0)
        r.key(SEL, 1)
        r.key(ST, 1)
        r.run(2.1)
        _check(len(r.stops) == 2, "a fresh hold fires again (%s)" % label)


def test_exit_combo_released_early_resets():
    r = Rig()
    r.key(SEL, 1)
    r.key(ST, 1)
    r.run(1.5)
    r.key(ST, 0)
    r.run(0.1)
    r.key(ST, 1)
    r.run(1.5)
    _check(r.stops == [], "release at 1.5 s restarts the timer: 1.5 + 1.5 s is not 2 s")


def test_broken_not_fightcade_is_inert():
    for mode in ("menu", "esde", "", "fightcade-ish"):
        r = Rig(mode)
        r.hat(1, 0)
        r.key(LK, 1)
        r.key(SEL, 1)
        r.key(ST, 1)
        r.run(3.0)
        _check(r.out == [] and r.stops == [] and r.next_mode() is None and r.p.enabled,
               "current-mode=%r: no pointer, no clicks, no toggle, no exit combo" % mode)
    r = Rig("fightcade")
    r.set_mode("")
    os.unlink(os.path.join(r.rt, "current-mode"))
    r.hat(1, 0)
    r.run(0.3)
    _check(r.out == [], "no current-mode file at all: inert")


def test_mode_changes_while_running():
    r = Rig("esde")
    r.hat(1, 0)
    r.run(0.2)
    _check(r.out == [], "inert in esde")
    r.set_mode("fightcade")
    r.hat(1, 0)
    r.run(0.3)
    _check(r.rel(M.REL_X) > 0, "mode switches to fightcade: active at once")
    r.key(LK, 1)
    r.set_mode("menu")
    r.run(0.1)
    _check(r.clicks()[-1] == (M.BTN_LEFT, 0), "mode leaves fightcade with LK down: the click is released")
    r.out.clear()
    r.run(0.3)
    _check(r.out == [], "and the pointer stops")


def test_remove_device_releases_everything():
    r = Rig()
    r.hat(1, 0)
    r.key(LK, 1)
    r.p.remove_device(DEV)
    r.out.clear()
    r.run(0.3)
    _check(r.out == [] and r.clicks() == [], "unplugged mid-use: pointer stops")
    _check((M.BTN_LEFT, 0) in [(c, v) for (t, c, v) in r.out] or r.p.sent == set(), "and no button stays pressed")


# ---- the hardware layer: a pipe plays the stick ----------------------------------------------------
def _ev(t, c, v):
    return struct.pack(M.FMT, 0, 0, t, c, v)


def test_reader_unplug_and_rediscover():
    r = Rig()
    now = [0.0]
    present = []          # write ends of the fake sticks that are plugged in

    def opener(path, flags):
        rfd, wfd = os.pipe()
        os.set_blocking(rfd, False)
        present.append(wfd)
        return rfd
    rd = M.Reader(r.p, scan=lambda: ["fake"] if present or want[0] else [], ranges=lambda fd: {}, clock=lambda: now[0],
                  opener=opener)
    want = [False]
    rd.pump(0.0)
    _check(rd.fds == {}, "no stick present: nothing open, no crash")
    want[0] = True
    now[0] += 5
    rd.rescan()
    _check(len(rd.fds) == 1, "stick appears: opened")
    w = present[0]
    os.write(w, _ev(M.EV_KEY, LK, 1) + _ev(M.EV_KEY, LK, 0))
    rd.pump(0.1)
    _check(r.clicks() == [(M.BTN_LEFT, 1), (M.BTN_LEFT, 0)], "events read from the stick reach the pointer")
    os.write(w, _ev(M.EV_ABS, M.ABS_HAT0X, 1) + _ev(M.EV_KEY, LK, 1))
    rd.pump(0.1)
    want[0] = False
    present.clear()
    os.close(w)           # unplugged mid-use with hat and LK held
    rd.pump(0.1)
    _check(rd.fds == {}, "EOF on the stick: dropped, the loop keeps running")
    r.out.clear()
    r.run(0.3)
    _check(r.out == [] and r.p.sent == set(), "the held hat and click are released with the device")
    want[0] = True
    now[0] += 5
    rd.rescan()
    _check(len(rd.fds) == 1, "replugged: found again by the next scan")
    for fd in list(rd.fds):
        rd.drop(fd, "test")
    for w in present:
        os.close(w)


def test_scan_devices_from_sysfs():
    with tempfile.TemporaryDirectory() as d:
        w = struct.calcsize("L") * 8

        def mk(n, name, abs_bits, key_bits):
            base = os.path.join(d, n, "device")
            os.makedirs(os.path.join(base, "capabilities"))
            open(os.path.join(base, "name"), "w").write(name + "\n")
            for f, bits in (("abs", abs_bits), ("key", key_bits)):
                words, top = [], (max(bits) // w + 1) if bits else 1
                for i in range(top):
                    words.append("%x" % sum(1 << (b - i * w) for b in bits if i * w <= b < (i + 1) * w))
                open(os.path.join(base, "capabilities", f), "w").write(" ".join(reversed(words)) + "\n")
        mk("event0", "pwr_button", [], [116])
        mk("event1", "pico stick", [0, 1], list(range(288, 298)))
        mk("event2", "hat pad", [16, 17], [288])
        mk("event3", M.NAME, [], [0x110, 0x111])
        mk("event4", "usb keyboard", [], list(range(1, 100)))
        mk("mouse0", "x", [0, 1], [304])
        got = M.scan_devices(sysfs=d, devdir="/dev/input")
        _check(got == ["/dev/input/event1", "/dev/input/event2"],
               "scan finds stick-like nodes only, not the power button, keyboard or our own uinput device (%s)" % got)
        _check(M.scan_devices(sysfs=os.path.join(d, "nope")) == [], "no sysfs -> empty list")


def test_uinput_setup_writes_the_device():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "uinput")
        open(path, "w").close()
        calls = []
        ui = M.Uinput(path, ioctl=lambda fd, req, arg=0: calls.append((req, arg)))
        ui.emit(M.EV_REL, M.REL_X, 5)
        ui.close()
        reqs = [c[0] for c in calls]
        _check(M.UI_DEV_CREATE in reqs and (M.UI_SET_KEYBIT, M.BTN_LEFT) in calls and (M.UI_SET_RELBIT, M.REL_Y) in calls,
               "uinput device declared with left/right buttons and X/Y motion, then created")
        _check(reqs[-1] == M.UI_DEV_DESTROY, "destroyed on close")
        _check(open(path, "rb").read() == _ev(M.EV_REL, M.REL_X, 5), "emit writes one input_event")


def _run_main(extra_env, timeout=10):
    env = dict(os.environ, **extra_env)
    return subprocess.run([sys.executable, os.path.join(ROLE, "files", "stickmouse.py")], env=env,
                          capture_output=True, text=True, timeout=timeout)


def test_broken_uinput_not_permitted_exits_clearly():
    with tempfile.TemporaryDirectory() as d:
        for label, path in (("missing", os.path.join(d, "nope")), ("no permission", os.path.join(d, "locked"))):
            if label == "no permission":
                open(path, "w").close()
                os.chmod(path, 0)
                if os.access(path, os.W_OK):      # running as root: cannot make a file unwritable
                    continue
            p = _run_main({"STICKMOUSE_UINPUT": path, "XDG_RUNTIME_DIR": d})
            _check(p.returncode == M.EXIT_UINPUT == 2 and "/dev/uinput" in p.stderr and "udev rule" in p.stderr,
                   "uinput %s: exit 2 with a message naming the udev rule (%s)" % (label, p.stderr.strip()[:80]))


def test_toggle_state_files():
    """main() persists the toggle in the runtime dir and a clean stop clears it (next Fightcade session starts ON)."""
    src = open(os.path.join(ROLE, "files", "stickmouse.py")).read()
    _check("os.unlink(toggle_file(rt))" in src and "ptr.on_toggle = persist" in src and "ptr.enabled = not os.path.exists(toggle_file(rt))" in src,
           "toggle is persisted on change, restored at start, dropped on a clean stop")
    bad = src.replace("ptr.enabled = not os.path.exists(toggle_file(rt))", "")
    _check("ptr.enabled = not os.path.exists(toggle_file(rt))" not in bad, "BROKEN: a daemon that forgets the toggle is distinguishable")


def test_stop_hook_runs_in_its_own_unit():
    _check(M.stop_argv(["/usr/local/bin/coin-dojo-session-stop"]) ==
           ["systemd-run", "--user", "--collect", "--quiet", "/usr/local/bin/coin-dojo-session-stop"],
           "the stop hook is launched through systemd-run --user, outside our cgroup")
    src = open(os.path.join(ROLE, "files", "stickmouse.py")).read()
    _check("subprocess.Popen(stop_argv(stop_cmd)" in src and "/usr/local/bin/coin-dojo-session-stop" in src,
           "main uses it, default is the absolute path")


def test_syn_dropped_releases_held_state():
    r = Rig()
    rd = M.Reader(r.p, devices=[], ranges=lambda fd: {})
    rfd, wfd = os.pipe()
    os.set_blocking(rfd, False)
    rd.fds[rfd] = DEV
    r.hat(1, 0)
    r.key(LK, 1)
    os.write(wfd, struct.pack(M.FMT, 0, 0, M.EV_SYN, M.SYN_DROPPED, 0))
    rd.pump(0.1)
    r.out.clear()
    r.run(0.3)
    _check(r.out == [] and r.p.sent == set(), "SYN_DROPPED: held hat and click are forgotten, no stuck button or drift")
    r.key(LK, 1)
    _check(r.clicks()[-1] == (M.BTN_LEFT, 1), "and the next press works normally")
    os.close(rfd)
    os.close(wfd)


def test_never_grabs():
    full = open(os.path.join(ROLE, "files", "stickmouse.py")).read()
    src = full.split("def main")[0]
    _check("EVIOCGRAB" not in full and "0x40044590" not in full, "the stick is never grabbed (on or off)")
    src = full
    _check("subprocess.Popen(stop_argv(stop_cmd)" in src and "shell=True" not in src and "os.system" not in src, "stop hook runs without a shell")


# ---- #231: games in /proc, close only the game, pointer auto-off -----------------------------------------
TREE = "/data/fightcade/opt/Fightcade"
FBNEO = TREE + "/emulator/fbneo/fcadefbneo.exe"
SCOPE_CG = "0::/user.slice/user-4242.slice/user@4242.service/app.slice/coin-dojo-fightcade.scope\n"
OTHER_CG = "0::/user.slice/user-4242.slice/user@4242.service/app.slice/coin-dojo-stickmouse.service\n"
SESSION_CG = "0::/user.slice/user-4242.slice/session-7.scope\n"      # #241: no user manager, no Fightcade scope
ME = 4242


class FakeProc:
    """A /proc in a temp dir: pid -> cmdline, ppid, comm, cgroup, uid (uid via the injected uid_of)."""

    def __init__(self):
        self.d = tempfile.TemporaryDirectory()
        self.root = self.d.name
        self.uids = {}

    def add(self, pid, argv, ppid=1, comm=None, cgroup=SCOPE_CG, uid=ME, environ=None):
        p = os.path.join(self.root, str(pid))
        os.makedirs(p, exist_ok=True)
        open(os.path.join(p, "cmdline"), "wb").write(("\0".join(argv) + "\0").encode())
        comm = comm or os.path.basename(argv[0])[:15]
        open(os.path.join(p, "stat"), "w").write("%d (%s) S %d %d 0 0\n" % (pid, comm, ppid, pid))
        open(os.path.join(p, "cgroup"), "w").write(cgroup)
        open(os.path.join(p, "environ"), "wb").write(b"\0".join(("%s=%s" % kv).encode() for kv in (environ or {}).items()))
        self.uids[p] = uid

    def gone(self, *pids):
        for pid in pids:
            p = os.path.join(self.root, str(pid))
            for f in os.listdir(p):
                os.unlink(os.path.join(p, f))
            os.rmdir(p)
            self.uids.pop(p, None)

    def uid_of(self, path):
        if path not in self.uids:
            raise FileNotFoundError(path)
        return self.uids[path]

    def find(self):
        return M.scan_games(proc=self.root, uid=ME, tree=TREE, uid_of=self.uid_of)

    def tree(self, roots):
        return M.game_tree(roots, proc=self.root, uid=ME, uid_of=self.uid_of, me=99999)


def cabinet_like(fp, cg=SCOPE_CG):
    """The cabinet's process picture during a match (#208/#228): the lobby, fcade, FBNeo through the wrapper/box64."""
    fp.add(500, ["/data/fightcade/opt/electron/electron", "--no-sandbox", TREE + "/fc2-electron/resources/app"], comm="electron", cgroup=cg)
    fp.add(600, ["/bin/bash", "/usr/local/lib/coin-dojo-fightcade/fcade-url", "fcade://served/sfiii3nr1"], ppid=500, cgroup=cg)
    fp.add(610, ["/data/fightcade/opt/box64/box64", "./fcade", "fcade://served/sfiii3nr1"], ppid=600, comm="fcade", cgroup=cg)
    fp.add(700, ["/data/fightcade/opt/box64/box64", "/data/fightcade/opt/wine/bin/wine", FBNEO, "quark:served,sfiii3nr1,7000,0"],
           ppid=610, comm="box64", environ={"DISPLAY": ":0", "WINEPREFIX": "/run/user/4242/coin-dojo/fcprefix", "SECRET": "x"}, cgroup=cg)
    fp.add(701, ["Z:\\data\\fightcade\\opt\\Fightcade\\emulator\\fbneo\\fcadefbneo.exe"], ppid=700, comm="fcadefbneo.exe", cgroup=cg)
    fp.add(800, ["/data/fightcade/opt/wine/bin/wineserver"], ppid=700, comm="wineserver", cgroup=cg)
    fp.add(900, ["C:\\windows\\system32\\services.exe"], ppid=1, comm="services.exe", cgroup=cg)


def test_scan_finds_the_game_not_the_lobby():
    fp = FakeProc()
    cabinet_like(fp)
    _check(fp.find() == {700: "fcadefbneo.exe", 701: "fcadefbneo.exe"},
           "the box64 + wine process (unix path) and Wine's rewritten Z: cmdline are the game; lobby, fcade, wineserver are not (%s)" % fp.find())
    fp.gone(700, 701, 800)
    _check(fp.find() == {}, "lobby only: no game")
    fp.add(702, ["fcadefbneo.exe", "sfiii3nr1"], ppid=610)
    _check(fp.find() == {702: "fcadefbneo.exe"}, "a relative exe (fcade cds into the emulator folder) still counts in the scope")
    fp.add(703, ["C:\\FIGHTCADE\\EMULATOR\\GGPOFBA-NG.EXE"])
    _check(fp.find().get(703) == "ggpofba-ng.exe", "other Fightcade emulators, any case and a C: path")


def test_broken_scan_ignores_lookalikes():
    fp = FakeProc()
    fp.add(10, ["/usr/local/lib/coin-dojo-fightcade/wine", "taskkill", "/im", "fcadefbneo.exe"])
    fp.add(11, ["/bin/grep", "/im", "fcadefbneo.exe"])
    fp.add(12, [FBNEO], uid=0)
    # 13: outside the scope a relative exe never counts. A bare [FBNEO] (absolute, under the tree) at argv[0] in another
    # unit DOES count since #241 (test_broken_outside_scope_needs_the_tree): accepted, that is how a scope-less game looks.
    fp.add(13, ["fcadefbneo.exe"], cgroup=OTHER_CG)
    fp.add(18, ["C:\\Fightcade\\emulator\\fbneo\\fcadefbneo.exe"], cgroup=SESSION_CG)
    fp.add(14, ["/home/player/elsewhere/fcadefbneo.exe"])
    fp.add(15, ["/usr/bin/vim", TREE + "/emulator/fbneo/fcadefbneo.exe.ini"])
    fp.add(16, ["box64", "Z:\\tmp\\fcadefbneo.exe"])
    fp.add(17, [FBNEO + "x"])
    _check(fp.find() == {}, "BROKEN look-alikes are not games: our own taskkill, an /im argument, another uid, a relative or C: path "
           "outside the scope, an absolute path outside the tree (unix and Z:), a .ini, a longer name (%s)" % fp.find())
    _check(M.scan_games(proc=os.path.join(fp.root, "nope"), uid=ME) == {}, "no /proc at all: no game, no crash")
    _check(M.in_scope("0::/a/coin-dojo-fightcade.scope-x\n", "coin-dojo-fightcade.scope") is False,
           "BROKEN: a scope name that only starts the same is not our scope")


def test_tree_is_the_game_only():
    fp = FakeProc()
    cabinet_like(fp)
    fp.add(702, ["C:\\windows\\system32\\conhost.exe"], ppid=701)            # a child of the game
    fp.add(703, ["/bin/sh", "-c", "x"], ppid=702, uid=0)                       # another uid inside the tree
    t = fp.tree([700, 701])
    _check(t == {700, 701, 702}, "the game pids and their descendants (%s)" % sorted(t))
    for pid, what in ((500, "the lobby"), (600, "fcade-url"), (610, "fcade"), (800, "wineserver"), (900, "services.exe"),
                      (703, "another uid"), (1, "pid 1")):
        _check(pid not in t, "BROKEN guard: %s (%d) is never in the kill set" % (what, pid))
    _check(M.game_tree([99999], proc=fp.root, uid=ME, uid_of=fp.uid_of, me=99999) == set(), "never ourselves")


class CloseRig:
    """Pointer + GameCloser on a fake /proc and a fake clock; kill() and taskkill are recorded, never real.
    timing=(taskkill exits after, game ends after) s from the taskkill start: a measured cabinet close (#241)."""

    def __init__(self, obeys=None, spawn_fails=False, mode="fightcade", cg=SCOPE_CG, timing=None, grace=M.CLOSE_GRACE, xmode=None):
        self.r = Rig(mode)
        self.fp = FakeProc()
        cabinet_like(self.fp, cg)
        self.kills, self.spawned = [], []
        self.obeys = obeys            # None | "taskkill" | "TERM" | "KILL" | "never"
        self.spawn_fails = spawn_fails
        self.timing, self.spawned_at = timing, None
        self.scan = M.GameScan(proc=self.fp.root, uid=ME, tree=TREE, uid_of=self.fp.uid_of)
        self.xmode, self.xspawned = xmode, []   # #257: None = no window step; 'obeys' | 'nowindow' (rc 3) | 'ignored' (sent, game stays)
        self.c = M.GameCloser(self.find, self.fp.tree, spawn=self.spawn, kill=self.kill, clock=lambda: self.r.now, grace=grace,
                              xspawn=self.xspawn if xmode else None)
        p = self.r.p
        p.games, p.closer, p.game_every = self.find, self.c, M.GAME_EVERY

    def find(self):
        if self.timing and self.spawned_at is not None and self.r.now - self.spawned_at >= self.timing[1] - 1e-9:
            self.game_ends()
        return self.scan()

    def game_ends(self):
        if os.path.isdir(os.path.join(self.fp.root, "700")):
            self.fp.gone(700, 701)

    def xspawn(self, pids, pid):
        rig = self
        rig.xspawned.append((list(pids), M.game_env(rig.fp.root, pid)))

        class X:
            def poll(self):
                if rig.xmode == "obeys":
                    rig.game_ends()
                    return M.XCLOSE_SENT
                return M.XCLOSE_NONE if rig.xmode == "nowindow" else (M.XCLOSE_SENT if rig.r.now - t0 > 0.1 else None)

            def kill(self):
                pass
        t0 = rig.r.now
        return X()

    def spawn(self, argv, pid):
        if self.spawn_fails:
            raise FileNotFoundError(argv[0])
        rig = self

        class P:
            rc = None
            t0 = rig.r.now

            def poll(self):
                if self.rc is None and rig.obeys == "taskkill":
                    rig.game_ends()
                    self.rc = 0
                if rig.timing and self.rc is None and rig.r.now - self.t0 >= rig.timing[0] - 1e-9:
                    self.rc = 0
                return self.rc

            def kill(self):
                self.rc = -9
        self.spawned.append((argv, M.game_env(self.fp.root, pid)))
        self.spawned_at = self.r.now
        return P()

    def kill(self, pid, sig):
        self.kills.append((self.r.now, pid, sig))
        if (self.obeys == "TERM" and sig == M.signal.SIGTERM) or (self.obeys == "KILL" and sig == M.signal.SIGKILL):
            self.game_ends()

    def combo(self, hold=2.1):
        self.r.key(SEL, 1)
        self.r.key(ST, 1)
        self.r.run(hold)
        self.r.key(ST, 0)
        self.r.key(SEL, 0)


def test_combo_in_game_closes_the_game_not_the_mode():
    for obeys in ("taskkill", "TERM", "KILL", "never"):
        k = CloseRig(obeys)
        k.r.run(1.1)
        k.combo()
        k.r.run(15.0)
        _check(k.r.stops == [] and k.r.next_mode() is None,
               "game obeys %s: the combo in a game never writes next-mode or runs the stop hook" % obeys)
        _check(k.spawned and k.spawned[0][0] == [M.FC_WINE, "taskkill", "/im", "fcadefbneo.exe"] and "/f" not in k.spawned[0][0],
               "first a graceful taskkill /im fcadefbneo.exe (no /f) through the Fightcade wine wrapper")
        _check(k.spawned[0][1] == {"DISPLAY": ":0", "WINEPREFIX": "/run/user/4242/coin-dojo/fcprefix"},
               "taskkill gets the game's DISPLAY and WINEPREFIX, nothing else from its environment")
        _check(not k.c.busy, "closing is over after %s" % obeys)


def test_escalation_timing():
    k = CloseRig("never")
    k.r.run(1.1)
    k.combo(hold=2.0 + M.TICK)
    t0 = k.c.t0
    k.r.run(M.CLOSE_GRACE + 6.0)
    terms = [t - t0 for (t, p, s) in k.kills if s == M.signal.SIGTERM]
    kills = [t - t0 for (t, p, s) in k.kills if s == M.signal.SIGKILL]
    _check(terms and abs(terms[0] - M.CLOSE_GRACE) <= 0.3, "SIGTERM %.2f s after taskkill (grace %.1f s)" % (terms[0] if terms else -1, M.CLOSE_GRACE))
    _check(kills and abs(kills[0] - M.CLOSE_GRACE - M.TERM_WAIT) <= 0.3, "SIGKILL %.2f s after it, TERM_WAIT %.1f s later" % (kills[0] if kills else -1, M.TERM_WAIT))
    _check({p for (t, p, s) in k.kills} == {700, 701}, "TERM and KILL reach only the game pids (%s)" % sorted({p for (t, p, s) in k.kills}))
    _check(len(terms) == 2 and len(kills) == 2, "one TERM and one KILL per game pid, no repeats (%d, %d)" % (len(terms), len(kills)))
    k = CloseRig("taskkill")
    k.r.run(1.1)
    k.combo()
    k.r.run(10.0)
    _check(k.kills == [], "a game that obeys taskkill gets no signal at all")
    k = CloseRig("TERM")
    k.r.run(1.1)
    k.combo()
    k.r.run(10.0)
    _check([s for (t, p, s) in k.kills] == [M.signal.SIGTERM] * 2, "a game that obeys TERM is never KILLed")


def _logged(fn):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        fn()
    return buf.getvalue()


def test_grace_covers_the_measured_taskkill():
    """#241, cabinet journal 2026-11-18: taskkill exited after 1.9 s and 5.0 s, the game closed after 3.3 s and 6.0 s."""
    for exit_after, end_after in ((1.9, 3.3), (5.0, 6.0)):
        k = CloseRig(timing=(exit_after, end_after))
        k.r.run(1.1)

        def go():
            k.combo(hold=2.0 + M.TICK)
            k.r.run(12.0)
        out = _logged(go)
        _check(k.kills == [] and not k.c.busy and k.r.stops == [],
               "taskkill %.1f s, game gone at %.1f s: closed by taskkill, no signal, the mode stays" % (exit_after, end_after))
        _check("taskkill exited 0 after %.1f s" % exit_after in out, "taskkill's own run time is logged (%r)" % out)
        _check("(taskkill); back to the lobby" in out and "SIGTERM" not in out, "the close is logged as (taskkill), never SIGTERM")
    _check(M.CLOSE_GRACE >= 6.0 + 3.0, "the grace leaves >= 3 s over the slowest measured close (6.0 s): %.1f" % M.CLOSE_GRACE)


def test_broken_close_slower_than_grace_is_termed():
    k = CloseRig(timing=(5.0, M.CLOSE_GRACE + 1.0))
    k.r.run(1.1)

    def go():
        k.combo(hold=2.0 + M.TICK)
        k.r.run(14.0)
    out = _logged(go)
    terms = [t - k.c.t0 for (t, p, s) in k.kills if s == M.signal.SIGTERM]
    _check(terms and abs(terms[0] - M.CLOSE_GRACE) <= 0.3, "BROKEN: a game still up at the grace gets SIGTERM at %.1f s (%s)"
           % (M.CLOSE_GRACE, terms))
    _check("taskkill exited 0 after 5.0 s" in out and "(SIGTERM)" in out, "and the log says so (%r)" % out)
    k = CloseRig("never", timing=(99.0, 99.0))
    k.r.run(1.1)
    out = _logged(lambda: (k.combo(hold=2.0 + M.TICK), k.r.run(4.0 + M.CLOSE_GRACE)))
    _check(re.search(r"taskkill killed, rc -9 after %.1f s" % M.CLOSE_GRACE, out), "a taskkill still running at the grace is "
           "killed and its run time logged (%r)" % out)
    k = CloseRig(timing=(1.0, 3.0), grace=0.5)
    k.r.run(1.1)
    k.combo(hold=2.0 + M.TICK)
    k.r.run(4.0)
    _check([s for (t, p, s) in k.kills][:1] == [M.signal.SIGTERM] and not k.c.busy,
           "a short test grace (STICKMOUSE_CLOSE_GRACE, the cabinet proof) forces the SIGTERM path")


def test_game_outside_the_scope_never_leaves_the_mode():
    for cg, why in ((SESSION_CG, "no user manager (session scope)"), (OTHER_CG, "another unit"),
                    ("0::/user.slice/user-4242.slice/user@4242.service/app.slice/coin-dojo-fightcade-old.scope\n", "renamed scope"),
                    ("", "no cgroup file content")):
        k = CloseRig("taskkill", cg=cg)
        out = _logged(lambda: k.r.run(1.1))
        _check(k.r.p.game and not k.r.p.enabled, "%s: the game outside the scope is seen, pointer auto-off" % why)
        _check(out.count("game outside coin-dojo-fightcade.scope") == 1 and "fcadefbneo.exe pid 700" in out,
               "%s: logged once (%r)" % (why, out))
        out = _logged(lambda: k.r.run(3.0))
        _check("game outside" not in out, "%s: not logged again while the same game runs" % why)
        k.combo()
        k.r.run(2.0)
        _check(k.r.stops == [] and k.r.next_mode() is None, "%s: the combo never leaves the mode" % why)
        _check(len(k.spawned) == 1 and k.spawned[0][0][-1] == "fcadefbneo.exe" and not k.c.busy and not k.r.p.game,
               "%s: it closes only the game (taskkill), then the lobby" % why)
    k = CloseRig("never", cg=SESSION_CG)
    k.r.run(1.1)
    k.combo()
    k.r.run(4.0 + M.CLOSE_GRACE)
    _check({p for (t, p, s) in k.kills} == {700, 701} and k.r.stops == [],
           "outside the scope too, TERM/KILL reach only the game pids (not the lobby 500, fcade 610, wineserver 800)")


def test_broken_tools_naming_the_game_outside_the_scope():
    """#241 round 2: over SSH as the cabinet user (same uid), a tool with the FBNeo path in argv is not a game."""
    tools = (["/usr/bin/less", FBNEO], ["strace", "-f", FBNEO], ["cp", FBNEO, "/tmp/x"], ["/usr/bin/sha256sum", FBNEO],
             ["grep", "-c", "x", FBNEO], ["gdb", "--args", "/data/fightcade/opt/box64/box64", FBNEO],
             ["/data/fightcade/opt/box64/box64", "/usr/bin/strace", FBNEO],
             ["/data/fightcade/opt/box64/box64", "/data/fightcade/opt/wine/bin/wine", "wine", FBNEO])
    for cg, where in ((SESSION_CG, "session scope"), (OTHER_CG, "another unit")):
        fp = FakeProc()
        for i, argv in enumerate(tools):
            fp.add(40 + i, argv, cgroup=cg)
        _check(fp.find() == {}, "BROKEN (%s): less, strace -f, cp, sha256sum, grep, gdb --args box64, box64 strace and "
               "an exe past argv[2] are never a game (%s)" % (where, fp.find()))
        fp.add(60, ["/data/fightcade/opt/box64/box64", "/data/fightcade/opt/wine/bin/wine", FBNEO, "quark:x"], cgroup=cg)
        fp.add(61, ["Z:\\data\\fightcade\\opt\\Fightcade\\emulator\\fbneo\\fcadefbneo.exe"], cgroup=cg)
        fp.add(62, ["/usr/local/lib/coin-dojo-fightcade/wine", FBNEO], cgroup=cg)
        _check(fp.find() == {60: "fcadefbneo.exe", 61: "fcadefbneo.exe", 62: "fcadefbneo.exe"},
               "(%s) box64 wine <exe>, Wine's Z: argv[0] and the wrapper + exe are games (%s)" % (where, fp.find()))
    fp = FakeProc()
    for i, argv in enumerate(tools):
        fp.add(40 + i, argv)
    _check(set(fp.find()) == {40, 41, 42, 43, 44, 45, 46, 47}, "inside the scope the old rule stands (the scope is the guard)")
    k = CloseRig("never", cg=SESSION_CG)
    k.game_ends()
    k.fp.add(70, ["/usr/bin/less", FBNEO], cgroup=SESSION_CG)
    k.r.run(1.1)
    k.combo()
    _check(len(k.r.stops) == 1 and k.spawned == [] and k.kills == [],
           "BROKEN: `less <fbneo path>` over SSH does not trap the player: the combo leaves, nothing is killed")


def test_close_gave_up_next_combo_leaves():
    k = CloseRig("never")
    k.r.run(1.1)
    k.combo()
    k.r.run(4.0 + M.CLOSE_GRACE)
    _check(not k.c.busy and k.c.gave_up == {700, 701} and k.r.stops == [], "a game that survives SIGKILL: the close gives up")
    n = len(k.kills)
    k.combo()
    _check(len(k.r.stops) == 1 and k.r.next_mode() == "menu" and len(k.kills) == n and len(k.spawned) == 1,
           "the next combo on the same pids leaves the mode (the player is never trapped), no new taskkill or signal")
    k = CloseRig("never")
    k.r.run(1.1)
    k.combo()
    k.r.run(4.0 + M.CLOSE_GRACE)
    k.game_ends()
    cabinet_like(k.fp)
    k.fp.gone(700, 701)
    k.fp.add(710, ["/data/fightcade/opt/box64/box64", "/data/fightcade/opt/wine/bin/wine", FBNEO], ppid=610)
    k.r.run(1.1)
    k.combo()
    _check(k.r.stops == [] and len(k.spawned) == 2, "BROKEN guard: a new game (other pids) after a give-up is closed, the mode stays")
    k = CloseRig("taskkill")
    k.c.gave_up = {123}
    k.r.run(1.1)
    k.combo()
    k.r.run(1.0)
    _check(k.r.stops == [] and k.c.gave_up == set(), "a clean close forgets the old give-up")


def test_broken_outside_scope_needs_the_tree():
    fp = FakeProc()
    fp.add(30, [FBNEO], cgroup=SESSION_CG)   # a bare [FBNEO] at argv[0] outside the scope counts (accepted, see case 13)
    _check(M.scan_games(proc=fp.root, uid=ME, tree="", uid_of=fp.uid_of) == {},
           "BROKEN: outside the scope with no known tree (no fightcade config), nothing counts as a game")
    _check(M.scan_games(proc=fp.root, uid=ME, tree=TREE, uid_of=fp.uid_of) == {30: "fcadefbneo.exe"},
           "with the tree, the same process is a game")
    fp.add(31, [FBNEO], cgroup=SESSION_CG, uid=0)
    _check(31 not in M.scan_games(proc=fp.root, uid=ME, tree=TREE, uid_of=fp.uid_of), "BROKEN: another uid outside the scope never counts")


def test_broken_taskkill_missing_goes_straight_to_term():
    k = CloseRig("TERM", spawn_fails=True)
    k.r.run(1.1)
    k.combo(hold=2.0 + M.TICK)
    k.r.run(1.0)
    first = k.kills[0][0] - k.c.t0 if k.kills else 99
    _check(k.spawned == [] and first <= M.CLOSE_EVERY + M.TICK and k.kills[0][2] == M.signal.SIGTERM,
           "no wine wrapper: SIGTERM after %.2f s, not after the %.0f s grace" % (first, M.CLOSE_GRACE))
    _check(k.r.stops == [], "and the mode still stays")


def test_combo_in_lobby_still_leaves():
    k = CloseRig()
    k.game_ends()
    k.r.run(1.1)
    k.combo()
    _check(len(k.r.stops) == 1 and k.r.next_mode() == "menu" and k.spawned == [] and k.kills == [],
           "lobby (no game): SELECT+START 2 s leaves Fightcade mode as before, nothing is closed or killed")
    k = CloseRig("taskkill")
    k.r.run(1.1)
    k.combo()
    k.r.run(2.0)
    k.combo()
    _check(k.r.stops == [], "#257: a second combo within the grace after the close does not leave")
    k.r.run(M.EXIT_GRACE)
    k.combo()
    _check(len(k.r.stops) == 1 and k.r.next_mode() == "menu", "first combo closes the game, a second one in the lobby after the grace leaves")


def test_broken_scan_error_falls_back_to_leaving():
    k = CloseRig()

    def boom():
        raise RuntimeError("proc went away")
    k.r.p.games = boom
    k.r.run(1.1)
    k.combo()
    _check(len(k.r.stops) == 1 and k.kills == [], "a scan that raises: no kill, the combo leaves the mode (never stuck)")


def test_combo_while_closing_does_not_double_fire():
    k = CloseRig("never")
    k.r.run(1.1)
    k.combo()
    k.combo()
    _check(len(k.spawned) == 1 and k.r.stops == [], "a second combo during the close neither starts a second taskkill nor leaves")


def test_pointer_auto_off_in_game_and_back_on():
    k = CloseRig()
    p = k.r.p
    _check(p.enabled and not p.game, "lobby: pointer on")
    k.r.run(1.1)
    _check(p.game and not p.enabled and p.auto_off, "a game comes up: the pointer switches off on its own")
    k.r.out.clear()
    k.r.hat(1, 0)
    k.r.key(LK, 1)
    k.r.key(LK, 0)
    k.r.run(0.5)
    _check(k.r.out == [], "in the game: the stick and LK drive no pointer and no click")
    k.r.hat(0, 0)
    k.game_ends()
    k.r.run(1.1)
    _check(not p.game and p.enabled, "the game ends: the pointer is back on")
    k.r.hat(1, 0)
    k.r.run(0.3)
    _check(k.r.rel(M.REL_X) > 0, "and moves again")


def test_pointer_back_at_once_after_combo_close():
    k = CloseRig("taskkill")
    k.r.run(1.1)
    k.combo()
    k.r.run(0.5)
    _check(not k.c.busy and k.r.p.enabled and not k.r.p.game, "closed by the combo: pointer on right away, not at the next slow poll")


def test_toggle_by_hand_wins_over_auto():
    k = CloseRig()
    p = k.r.p
    persisted = []
    p.on_toggle = persisted.append
    k.r.run(1.1)
    _check(persisted == [], "the automatic off is not persisted (a crash restart starts ON and re-detects the game)")
    k.r.key(SEL, 1)
    k.r.key(MK, 1)
    k.r.key(MK, 0)
    k.r.key(SEL, 0)
    _check(not p.enabled and persisted == [], "#257 BROKEN guard: SELECT+MK in a game is ignored (coin + kick), the pointer stays off meanwhile")
    k.game_ends()
    k.r.run(1.1)
    _check(p.enabled, "and the pointer is ON after the game")
    k = CloseRig()
    k.game_ends()
    k.r.key(SEL, 1)
    k.r.key(MK, 1)
    k.r.key(MK, 0)
    k.r.key(SEL, 0)
    _check(not k.r.p.enabled, "off by hand in the lobby")
    cabinet_like(k.fp)
    k.r.run(1.1)
    k.game_ends()
    k.r.run(1.1)
    _check(not k.r.p.enabled, "off by hand BEFORE the game: still off after it (decision: that choice is kept)")


def test_broken_no_scan_outside_fightcade():
    k = CloseRig(mode="esde")
    calls = []
    k.r.p.games = lambda: calls.append(1) or {700: "fcadefbneo.exe"}
    k.r.run(3.0)
    _check(calls == [] and k.r.p.enabled, "not in Fightcade mode: /proc is never scanned and the pointer is untouched")
    k = CloseRig()
    k.r.run(1.1)
    k.r.set_mode("menu")
    k.r.run(0.6)
    _check(k.r.p.enabled and not k.r.p.game, "mode ends during a game: the automatic off is undone")


def test_scan_is_slow_polled():
    k = CloseRig()
    calls = []
    real = k.fp.find
    k.r.p.games = lambda: calls.append(k.r.now) or real()
    k.r.run(5.0)
    _check(4 <= len(calls) <= 6, "about one /proc scan per GAME_EVERY s (%d scans in 5 s)" % len(calls))


def test_fc_conf_and_game_names():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "c.conf")
        open(f, "w").write("# x\nHK_FC_TREE='/data/fight cade/opt/Fightcade'\nHK_FC_BINDIR=/usr/local/lib/coin-dojo-fightcade\n"
                           "HK_FC_UNIT=coin-dojo-fightcade\nHK_FC_SCOPE_PROPS=()\nOTHER=1\n")
        c = M.fc_conf(f)
        _check(c == {"HK_FC_TREE": "/data/fight cade/opt/Fightcade", "HK_FC_BINDIR": "/usr/local/lib/coin-dojo-fightcade",
                     "HK_FC_UNIT": "coin-dojo-fightcade"}, "the role's shell-quoted config is read (%s)" % c)
        _check(M.fc_conf(os.path.join(d, "nope")) == {}, "BROKEN: a missing config gives {} (code defaults), no crash")
    _check(M.game_names({}) == M.GAMES and M.game_names({"STICKMOUSE_GAMES": "A.exe, b.EXE"}) == ("a.exe", "b.exe"),
           "STICKMOUSE_GAMES overrides the emulator list")


def test_never_wineserver_k():
    src = open(os.path.join(ROLE, "files", "stickmouse.py")).read().split('"""', 2)[2]
    _check("'-k'" not in src and '"-k"' not in src and "killpg" not in src and "'/f'" not in src,
           "no wineserver -k, no process-group kill, no taskkill /f in the code")


# ---- role structure --------------------------------------------------------------------------------
def _read(*p):
    return open(os.path.join(ROLE, *p), encoding="utf-8").read()


def defaults_text():
    return _read("defaults", "main.yml")


def rule_unit(unit):
    p = []
    if re.search(r"(?m)^\[Install\]|^WantedBy=", unit):
        p.append("stickmouse unit has [Install]: it must only run when coin-dojo-fightcade starts it")
    if "RestartPreventExitStatus=2" not in unit:
        p.append("exit 2 (uinput unusable) would restart-spin")
    if not re.search(r"(?s)\[Unit\][^\[]*StartLimitIntervalSec=0", unit):
        p.append("StartLimitIntervalSec=0 is not in [Unit] (systemd ignores it in [Service])")
    if "/usr/local/bin/coin-dojo-session-stop" not in defaults_text():
        p.append("stop hook default is not the absolute path")
    if "NoNewPrivileges=yes" not in unit:
        p.append("no NoNewPrivileges")
    if re.search(r"(?m)^(User|Group)=", unit):
        p.append("user unit must not set User= (it runs as the logged-in cabinet user)")
    if "{{ power_dir }}/stickmouse.py" not in unit or 'Environment="STICKMOUSE_STOP_CMD={{ stickmouse_stop_cmd }}"' not in unit:
        p.append("ExecStart or the stop hook is not wired")
    return p


def rule_tasks(tasks, defaults):
    p = []
    rule = re.search(r"(?m)^stickmouse_uinput_rule:\s*'(.*?)'", defaults)
    r = rule.group(1) if rule else ""
    if r != 'KERNEL=="uinput", SUBSYSTEM=="misc", GROUP="input", MODE="0660"':
        p.append("uinput rule is not the narrow group-input 0660 rule: %r" % r)
    if re.search(r'uaccess|MODE="066[67]"|MODE="06[0-9]6"|OWNER=', r):
        p.append("uinput rule too wide")
    m = re.search(r"- name: Install the stickmouse user unit.*?(?=\n- name:|\Z)", tasks, re.S)
    if not m or "reload user systemd" not in m.group(0) or "state: started" in m.group(0) or "enabled: true" in m.group(0):
        p.append("unit task missing, starts/enables it, or does not reload the user manager")
    if "stickmouse.py" not in tasks or "notify: restart stickmouse" not in tasks:
        p.append("stickmouse.py not shipped or no restart notify")
    for t in re.findall(r"- name: (?:Ship stickmouse|User unit dir|Install the stickmouse|The input group may use|Load the uinput|Load uinput and apply).*?(?=\n- name:|\Z)", tasks, re.S):
        if "become: true" not in t:
            p.append("a stickmouse task lacks become")
    if "when: (stickmouse_uinput_rule_copy is changed" not in tasks:
        p.append("modprobe/udev trigger runs every time (second run would not be zero)")
    return p


def test_role_rules():
    unit, tasks, defaults = _read("templates", "coin-dojo-stickmouse.service.j2"), _read("tasks", "main.yml"), _read("defaults", "main.yml")
    _check(rule_unit(unit) == [], "stickmouse unit rules hold: %s" % rule_unit(unit))
    _check(rule_tasks(tasks, defaults) == [], "stickmouse task rules hold: %s" % rule_tasks(tasks, defaults))
    h = _read("handlers", "main.yml")
    _check("name: reload user systemd" in h and "name: restart stickmouse" in h, "both handlers exist")
    d = os.stat(os.path.join(ROLE, "files", "stickmouse.py"))
    _check(d.st_mode & 0o100, "stickmouse.py is executable in the repo")


def test_broken_role_copies_are_flagged():
    unit, tasks, defaults = _read("templates", "coin-dojo-stickmouse.service.j2"), _read("tasks", "main.yml"), _read("defaults", "main.yml")
    _check(rule_unit(unit + "\n[Install]\nWantedBy=default.target\n"), "BROKEN: an enabled-at-login unit is flagged")
    _check(rule_unit(unit.replace("RestartPreventExitStatus=2\n", "")), "BROKEN: restart spin on exit 2 is flagged")
    _check(rule_unit(unit.replace("StartLimitIntervalSec=0\n", "").replace("[Service]", "[Service]\nStartLimitIntervalSec=0")), "BROKEN: StartLimitIntervalSec in [Service] is flagged")
    _check(rule_unit(unit.replace('"STICKMOUSE', 'STICKMOUSE')), "BROKEN: unquoted stop command is flagged")
    _check(rule_unit(unit.replace("[Service]", "[Service]\nUser=root")), "BROKEN: User= in a user unit is flagged")
    _check(rule_tasks(tasks, defaults.replace('GROUP="input", MODE="0660"', 'MODE="0666"')), "BROKEN: world-writable uinput rule is flagged")
    _check(rule_tasks(tasks, defaults.replace('MODE="0660"', 'MODE="0660", TAG+="uaccess"')), "BROKEN: uaccess uinput rule is flagged")
    _check(rule_tasks(tasks.replace("notify: restart stickmouse", "notify: nothing"), defaults), "BROKEN: no restart notify is flagged")
    _check(rule_tasks(tasks.replace("when: (stickmouse_uinput_rule_copy is changed", "when: (x"), defaults), "BROKEN: unconditional modprobe is flagged")


# ---- #227: the on-screen keyboard: dedicated keys to the lobby page only while it is open, never in a game ---------------
NAMES = {M.KEY_OPEN: "Open", M.KEY_SUBMIT: "Submit", M.KEY_CANCEL: "Cancel", M.KEY_PRESS: "Press", M.KEY_DELETE: "Delete",
         M.KEY_UP: "Up", M.KEY_DOWN: "Down", M.KEY_LEFT: "Left", M.KEY_RIGHT: "Right", M.KEY_ENTER: "Enter"}


def osk_keys(r):
    """The keyboard keys the virtual device was asked to press (down events only), as names."""
    return [NAMES[c] for (t, c, v) in r.out if t == M.EV_KEY and v == 1 and c in NAMES]


def mouse_events(r):
    return [e for e in r.out if (e[0] == M.EV_REL) or (e[0] == M.EV_KEY and e[1] in (M.BTN_LEFT, M.BTN_RIGHT))]


def open_osk(r):
    r.key(SEL, 1)
    r.key(LK, 1)
    r.key(LK, 0)
    r.key(SEL, 0)
    r.out.clear()


def test_osk_keys_are_dedicated():
    _check(len(set(M.OSK_KEYS)) == 9 and all(183 <= c <= 194 for c in M.OSK_KEYS), "nine distinct keys, all F13..F24")
    _check(not ({190, 191, 192} & set(M.OSK_KEYS)), "never F20/F21/F22 (xkb inet(evdev): mic mute, touchpad toggles)")
    _check(not ({1, 14, 28, 57, 103, 105, 106, 108} & set(M.OSK_KEYS)), "never Esc/Backspace/Enter/Space/arrows: a stray key does nothing")
    _check(max(M.OSK_KEYS) + 8 <= 255, "every keycode fits X11's 255 limit (Xwayland)")


def test_osk_opens_with_select_lk_and_types():
    r = Rig()
    r.key(SEL, 1)
    r.key(LK, 1)
    _check(r.p.osk and osk_keys(r) == ["Open"], "SELECT+LK opens the keyboard: one open key to the page")
    _check(mouse_events(r) == [], "and no mouse click comes out of the combo")
    r.key(LK, 0)
    r.key(SEL, 0)
    r.out.clear()
    r.hat(1, 0)
    r.run(0.05)
    r.hat(0, 0)
    r.hat(0, 1)
    r.run(0.05)
    r.hat(0, 0)
    r.key(LK, 1)
    r.key(LK, 0)
    r.key(MK, 1)
    r.key(MK, 0)
    _check(osk_keys(r) == ["Right", "Down", "Press", "Delete"], "stick = directions, LK = press (types), MK = delete (%s)" % osk_keys(r))
    _check(mouse_events(r) == [], "while open the stick and LK/MK never move or click the pointer")
    r.key(ST, 1)
    _check(r.p.osk, "START alone does not act on press")
    r.key(ST, 0)
    _check(not r.p.osk and osk_keys(r)[-2:] == ["Submit", "Enter"], "START (on release) closes it and sends a real Enter")
    open_osk(r)
    r.key(SEL, 1)
    r.key(SEL, 0)
    _check(not r.p.osk and osk_keys(r) == ["Cancel"], "SELECT closes it")


def test_osk_exit_combo_never_sends_a_chat_line():
    r = Rig()
    open_osk(r)
    r.key(ST, 1)       # START first, then SELECT (the exit combo the other way round)
    r.key(SEL, 1)
    r.key(ST, 0)
    r.key(SEL, 0)
    _check(osk_keys(r) == ["Cancel"] and "Submit" not in osk_keys(r), "START then SELECT: it cancels, nothing is submitted")
    r = Rig()
    open_osk(r)
    r.key(SEL, 1)      # SELECT first (closes it), then START
    r.key(ST, 1)
    r.key(ST, 0)
    r.key(SEL, 0)
    _check(osk_keys(r) == ["Cancel"], "SELECT then START: cancel only, START does nothing")
    r = Rig()
    r.key(SEL, 1)
    r.key(LK, 1)       # opened with SELECT still held
    r.key(LK, 0)
    r.out.clear()
    r.key(ST, 1)
    r.key(ST, 0)
    _check(r.p.osk and "Submit" not in osk_keys(r), "START released while SELECT is held (an exit try) does not submit")
    r.key(SEL, 0)
    r.key(ST, 1)
    r.key(ST, 0)
    _check(osk_keys(r) == ["Submit", "Enter"] and not r.p.osk, "once SELECT is up, START sends")
    r = Rig()
    open_osk(r)
    r.key(SEL, 1)
    r.key(ST, 1)
    r.run(M.HOLD + 0.1)
    _check(r.stops and r.next_mode() == "menu", "the 2 s exit combo still leaves Fightcade mode with the keyboard involved")


def test_osk_select_lk_closes_without_reopening():
    r = Rig()
    open_osk(r)
    r.key(SEL, 1)
    r.key(LK, 1)
    _check(not r.p.osk and osk_keys(r) == ["Cancel"], "SELECT+LK again closes it, and the LK of the same combo does not reopen it")
    r.key(LK, 0)
    r.key(SEL, 0)
    r.hat(1, 0)
    r.run(0.1)
    _check(osk_keys(r) == ["Cancel"] and mouse_events(r) != [], "closed: no key is sent, the stick is the pointer again")


def test_osk_direction_repeats_while_held():
    r = Rig()
    open_osk(r)
    r.hat(-1, 0)
    r.run(1.0)
    n = osk_keys(r).count("Left")
    _check(5 <= n <= 8 and set(osk_keys(r)) == {"Left"}, "a held direction repeats after a delay (%d presses in 1 s)" % n)
    r.hat(0, 0)
    r.out.clear()
    r.run(0.5)
    _check(osk_keys(r) == [], "released: no more presses")


def test_osk_inert_in_a_game_and_closes_silently_when_one_starts():
    k = CloseRig()
    k.r.run(1.1)             # the game is detected: pointer auto-off
    open_osk(k.r)
    _check(not k.r.p.osk and osk_keys(k.r) == [], "SELECT+LK in a game does not open the keyboard and sends no key")
    k.r.hat(1, 0)
    k.r.key(LK, 1)
    k.r.key(LK, 0)
    k.r.run(0.5)
    _check(osk_keys(k.r) == [], "in a game no key of the keyboard ever leaves the device (stick, LK)")
    k2 = CloseRig()
    k2.game_ends()
    k2.r.run(1.1)
    open_osk(k2.r)
    _check(k2.r.p.osk, "no game: it opens")
    cabinet_like(k2.fp)
    k2.r.run(1.2)
    _check(not k2.r.p.osk and osk_keys(k2.r) == [], "a game starting closes it WITHOUT any key (a cancel would reach the game)")
    k2.r.hat(1, 0)
    k2.r.run(0.5)
    _check(osk_keys(k2.r) == [], "and the stick sends no directions afterwards")


def test_osk_game_appearing_between_polls_leaks_at_most_one_poll():
    k = CloseRig()
    k.game_ends()
    k.r.run(1.1)
    open_osk(k.r)
    cabinet_like(k.fp)               # the game appears now, between two polls
    k.r.hat(1, 0)
    k.r.run(M.OSK_GAME_EVERY + 0.1)  # the open keyboard polls every OSK_GAME_EVERY, not every GAME_EVERY
    n = len(osk_keys(k.r))
    _check(not k.r.p.osk, "the keyboard closed within one fast poll of the game appearing")
    k.r.run(2.0)
    _check(len(osk_keys(k.r)) == n, "and not one key more after that (%d while the game came up)" % n)
    _check(M.OSK_GAME_EVERY < M.GAME_EVERY, "the open keyboard scans for a game faster than the idle 1 s")


def test_broken_osk_not_in_other_modes():
    r = Rig(mode="menu")
    open_osk(r)
    _check(not r.p.osk and r.out == [], "BROKEN: outside Fightcade mode SELECT+LK does nothing at all")
    r = Rig()
    open_osk(r)
    r.set_mode("menu")
    r.hat(1, 0)
    r.run(0.1)
    r.key(LK, 1)
    r.key(LK, 0)
    _check(not r.p.osk and osk_keys(r) == [] and r.out == [], "BROKEN: the mode ends while open: closed, silent, no key leaks")


def test_osk_closed_sends_no_keys():
    r = Rig()
    r.hat(1, 0)
    r.key(LK, 1)
    r.key(LK, 0)
    r.key(MK, 1)
    r.key(MK, 0)
    r.key(ST, 1)
    r.key(ST, 0)
    r.run(1.0)
    _check(osk_keys(r) == [], "closed: stick, LK, MK, START press no keyboard key (no leak into the lobby)")


def test_osk_no_proc_scan_per_combo_in_a_game():
    k = CloseRig()
    k.r.run(1.1)
    scans = []
    orig = k.r.p.games
    k.r.p.games = lambda: scans.append(1) or orig()
    open_osk(k.r)
    _check(scans == [] and not k.r.p.osk, "SELECT+LK in a game uses the known game state, no /proc scan")


def test_osk_device_declares_the_keys():
    calls = []
    M.Uinput(path=os.devnull, ioctl=lambda fd, req, *a: calls.append((req, a[0] if a else None))).close()
    keybits = {a for (req, a) in calls if req == M.UI_SET_KEYBIT}
    _check(set(M.OSK_KEYS) <= keybits and {M.BTN_LEFT, M.BTN_RIGHT} <= keybits, "the uinput device declares the pointer buttons and the keyboard keys")
    _check(M.KEY_ENTER == 28 and M.KEY_ENTER in keybits, "#262: and KEY_ENTER (evdev 28), or the kernel drops the real Enter")
    _check(M.KEY_ENTER not in M.OSK_KEYS, "#262: Enter is not one of the page's dedicated keys (the page never handles it)")


# ---- #262: START sends a REAL Enter (KEY_ENTER on the uinput device), only from the open keyboard in the lobby ------------
def enters(r):
    """(value, ...) of every KEY_ENTER event the virtual device was asked to emit: 1 = down, 0 = up."""
    return [v for (t, c, v) in r.out if t == M.EV_KEY and c == M.KEY_ENTER]


def test_osk_start_sends_one_real_enter_after_closing():
    r = Rig()
    open_osk(r)
    r.key(ST, 1)
    _check(enters(r) == [], "no Enter on START press (it acts on release)")
    r.key(ST, 0)
    _check(enters(r) == [1, 0], "START release: exactly one real Enter, down then up (%s)" % enters(r))
    keys = [(c, v) for (t, c, v) in r.out if t == M.EV_KEY]
    _check(keys == [(M.KEY_SUBMIT, 1), (M.KEY_SUBMIT, 0), (M.KEY_ENTER, 1), (M.KEY_ENTER, 0)],
           "F23 first (the page refocuses its field and hides), then Enter: nothing else (%s)" % keys)
    _check(r.out[-1] == (M.EV_SYN, 0, 0) and r.out.count((M.EV_SYN, 0, 0)) == 4, "every press and release is followed by a SYN_REPORT")
    _check(not r.p.osk, "and the keyboard is closed: the stick is the pointer again")
    r.out.clear()
    r.key(ST, 1)
    r.key(ST, 0)
    r.hat(1, 0)
    r.run(0.1)
    _check(enters(r) == [] and mouse_events(r) != [], "a second START with the keyboard closed: no Enter, the pointer moves")


def test_broken_no_enter_when_closed_in_a_game_outside_or_in_the_combo():
    r = Rig()
    for _ in range(3):
        r.key(ST, 1)
        r.key(ST, 0)
    _check(enters(r) == [], "BROKEN: START with the keyboard closed never sends Enter")
    r = Rig(mode="menu")
    open_osk(r)
    r.key(ST, 1)
    r.key(ST, 0)
    _check(r.out == [], "BROKEN: outside Fightcade mode nothing at all, no Enter")
    r = Rig()
    open_osk(r)
    r.set_mode("menu")
    r.key(ST, 1)
    r.key(ST, 0)
    _check(enters(r) == [], "BROKEN: the mode ended while open: START sends no Enter")
    k = CloseRig()
    k.r.run(1.1)                     # a game runs
    open_osk(k.r)
    k.r.key(ST, 1)
    k.r.key(ST, 0)
    _check(enters(k.r) == [] and not k.r.p.osk, "BROKEN: in a game START sends no Enter (the keyboard does not even open)")
    k = CloseRig()
    k.game_ends()
    k.r.run(1.1)
    open_osk(k.r)
    k.r.key(ST, 1)
    cabinet_like(k.fp)               # a game comes up between two fast polls, START still held
    k.r.key(ST, 0)
    _check(enters(k.r) == [] and osk_keys(k.r) == [] and not k.r.p.osk and k.r.p.game,
           "BROKEN: a game that came up since the last poll: START checks on demand, closes silently, no Enter, no F23")
    for order in (("st", "sel"), ("sel", "st")):
        r = Rig()
        open_osk(r)
        first, second = (ST, SEL) if order[0] == "st" else (SEL, ST)
        r.key(first, 1)
        r.key(second, 1)
        r.key(ST, 0)
        r.key(SEL, 0)
        _check(enters(r) == [], "BROKEN: SELECT+START (%s first) never sends Enter" % order[0])
    r = Rig()
    open_osk(r)
    r.key(SEL, 1)
    r.key(ST, 1)
    r.run(M.HOLD + 0.1)
    r.key(ST, 0)
    r.key(SEL, 0)
    _check(enters(r) == [] and r.stops, "BROKEN: the held 2 s exit combo leaves the mode and sends no Enter")
    # critic #262: SELECT held, LK opens (SELECT stays held), START, both held 2 s: leave() fires; then either release order
    for sel_first in (True, False):
        r = Rig()
        r.key(SEL, 1)
        r.key(LK, 1)
        r.key(LK, 0)
        r.out.clear()
        r.key(ST, 1)
        r.run(M.HOLD + 0.1)
        for c in ((SEL, ST) if sel_first else (ST, SEL)):
            r.key(c, 0)
        _check(enters(r) == [] and osk_keys(r) == [] and r.stops and not r.p.osk,
               "BROKEN: opened with SELECT held, combo held 2 s, %s released first: leaves, no Enter, no key (%s)"
               % ("SELECT" if sel_first else "START", osk_keys(r)))
        # the same, short (no 2 s): START joined SELECT, so its release never sends; the keyboard stays open
        r = Rig()
        r.key(SEL, 1)
        r.key(LK, 1)
        r.key(LK, 0)
        r.out.clear()
        r.key(ST, 1)
        for c in ((SEL, ST) if sel_first else (ST, SEL)):
            r.key(c, 0)
        _check(enters(r) == [] and "Submit" not in osk_keys(r) and r.p.osk and not r.stops,
               "BROKEN: a short SELECT+START (%s up first) sends nothing; the keyboard stays open" % ("SELECT" if sel_first else "START"))
        r.key(ST, 1)
        r.key(ST, 0)
        _check(enters(r) == [1, 0], "and a plain START afterwards sends as usual")


class _Boom:
    def __call__(self):
        raise OSError("proc gone")


class _Blind:
    ok = False                       # a GameScan that could not list /proc: it saw nothing, which is not "no game"

    def __call__(self):
        return {}


def test_broken_enter_fails_closed_when_the_scan_fails():
    for label, games in (("the scan raises", _Boom()), ("/proc cannot be listed", _Blind())):
        r = Rig()
        open_osk(r)
        r.p.games = games
        out = _logged(lambda: (r.key(ST, 1), r.key(ST, 0)))
        _check(enters(r) == [] and osk_keys(r) == [] and not r.p.osk,
               "BROKEN: %s: START sends no Enter and no key, the keyboard closes (fail closed)" % label)
        _check("START sends no Enter" in out, "and it is logged (%s)" % label)
        r.hat(1, 0)
        r.run(0.1)
        _check(mouse_events(r) != [], "the pointer still works afterwards (%s)" % label)
    with tempfile.TemporaryDirectory() as d:
        scan = M.GameScan(proc=os.path.join(d, "no-proc"), uid=ME, tree=TREE)
        _check(scan() == {} and scan.ok is False, "GameScan says ok=False when it cannot list /proc")
        fp = FakeProc()
        scan = M.GameScan(proc=fp.root, uid=ME, tree=TREE, uid_of=fp.uid_of)
        _check(scan() == {} and scan.ok is True, "and ok=True on a readable /proc with no game")



# ---- #257: window close first, exit grace, pointer back ------------------------------------------------------------
def test_x_close_first_no_taskkill_when_it_works():
    k = CloseRig(xmode="obeys")
    k.r.run(1.1)
    out = _logged(lambda: (k.combo(hold=2.0 + M.TICK), k.r.run(2.0)))
    _check(len(k.xspawned) == 1 and k.spawned == [] and k.kills == [], "the window close ends it: no taskkill, no signal")
    _check(k.xspawned[0][0] == [700, 701], "only the game's pids go to the window close, never the lobby or wineserver (%s)" % k.xspawned[0][0])
    _check(k.xspawned[0][1] == {"DISPLAY": ":0", "WINEPREFIX": "/run/user/4242/coin-dojo/fcprefix"}, "with the game's own DISPLAY (%s)" % k.xspawned[0][1])
    _check(not k.c.busy and k.r.stops == [] and "(window close); back to the lobby" in out, "closed, the mode stays, logged as window close (%r)" % out)


def test_broken_x_close_fails_falls_back_to_taskkill():
    k = CloseRig("taskkill", xmode="nowindow")
    k.r.run(1.1)
    out = _logged(lambda: (k.combo(hold=2.0 + M.TICK), k.r.run(2.0)))
    _check(len(k.xspawned) == 1 and len(k.spawned) == 1 and k.spawned[0][0][1:] == ["taskkill", "/im", "fcadefbneo.exe"],
           "BROKEN: no window found (rc 3): taskkill follows")
    _check("found no window" in out and "(taskkill); back to the lobby" in out and k.kills == [], "logged, ended by taskkill (%r)" % out)
    k = CloseRig("taskkill", xmode="ignored")
    k.r.run(1.1)
    t_start = []
    _logged(lambda: (k.combo(hold=2.0 + M.TICK), t_start.append(k.r.now), k.r.run(M.X_GRACE - 0.5)))
    _check(k.spawned == [], "a window close that was sent waits (%.1f s) before taskkill" % M.X_GRACE)
    k.r.run(1.5)
    _check(len(k.spawned) == 1 and not k.c.busy, "the game ignoring the window: taskkill after X_GRACE")
    k = CloseRig("never", xmode="nowindow")
    k.r.run(1.1)
    _logged(lambda: (k.combo(hold=2.0 + M.TICK), k.r.run(M.CLOSE_GRACE + 6.0)))
    _check({s for (t, p, s) in k.kills} == {M.signal.SIGTERM, M.signal.SIGKILL} and not k.c.busy,
           "window close and taskkill both useless: TERM then KILL as before")
    k = CloseRig("taskkill", xmode="ignored")
    k.xspawn_fails = True
    k.c.xspawn = lambda pids, pid: (_ for _ in ()).throw(FileNotFoundError("python3"))
    k.r.run(1.1)
    _logged(lambda: (k.combo(hold=2.0 + M.TICK), k.r.run(1.0)))
    _check(len(k.spawned) == 1, "the helper cannot start: taskkill at once")


def test_x_close_helper_exit_codes():
    saved = M.x_close
    try:
        M.x_close = lambda pids, display=None: [1, 2]
        _check(_logged_rc(["701"]) == (M.XCLOSE_SENT, True), "windows found: rc 0")
        M.x_close = lambda pids, display=None: []
        _check(_logged_rc(["701"])[0] == M.XCLOSE_NONE, "BROKEN: no window of the pids: rc 3 (nothing else is touched)")
        def boom(pids, display=None):
            raise OSError("no display")
        M.x_close = boom
        _check(_logged_rc(["701"])[0] == M.XCLOSE_NOCONN, "BROKEN: X unreachable: rc 4")
        _check(_logged_rc(["x"])[0] == M.XCLOSE_NOCONN, "BROKEN: a bad pid argument: rc 4")
    finally:
        M.x_close = saved
    try:
        M.x_close({1}, ":99")
        raise AssertionError("x_close on a missing display must raise")
    except OSError:
        pass
    _check(True, "the real x_close raises OSError without an X server (or libX11)")


def _logged_rc(argv):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = M.xclose_main(argv)
    return rc, bool(buf.getvalue())


def test_exit_grace_after_a_game_closed():
    """Cabinet 2026-11-19 15:06: a second press in the lobby 10 s after the first left Fightcade mode by accident."""
    k = CloseRig("taskkill")
    k.r.run(1.1)
    k.combo(hold=2.0 + M.TICK)
    k.r.run(0.5)
    _check(not k.c.busy and k.r.stops == [], "game closed by the combo")
    out = _logged(lambda: (k.combo(hold=2.0 + M.TICK), k.r.run(0.5)))
    _check(k.r.stops == [] and k.r.next_mode() is None, "SELECT+START in the lobby right after: the mode stays (%r)" % out)
    _check("ignored (grace 5 s)" in out, "and it is logged (%r)" % out)
    k.r.run(M.EXIT_GRACE)
    k.combo(hold=2.0 + M.TICK)
    _check(len(k.r.stops) == 1 and k.r.next_mode() == "menu", "after the grace the combo leaves Fightcade mode again")


def test_broken_no_grace_without_a_game():
    k = CloseRig()
    k.game_ends()
    k.r.run(1.1)
    k.combo(hold=2.0 + M.TICK)
    _check(len(k.r.stops) == 1, "BROKEN guard: no game was ever up: the combo leaves at once, the grace is only for after a game")
    k = CloseRig()
    k.r.run(1.1)
    k.game_ends()
    k.r.run(1.1)
    k.r.run(M.EXIT_GRACE)
    k.combo(hold=2.0 + M.TICK)
    _check(len(k.r.stops) == 1, "a game that ended on its own (match over) gives the same grace, then leaves")


def test_x_close_never_reaches_the_lobby_in_the_wiring():
    src = open(os.path.join(ROLE, "files", "stickmouse.py")).read()
    _check("a_del in prop(c, a_prot, 4)" in src and "wp[0] in pids" in src,
           "x_close sends only to windows whose _NET_WM_PID is one of the game pids and that list WM_DELETE_WINDOW")
    _check("--xclose" in src and "xspawn=xspawn" in src, "main wires the helper into the closer")


def test_game_ends_inside_the_hold_does_not_leave():
    """Critic #257: the game ends 0.5 s before the 2 s hold completes (before the 1 s poll could see it)."""
    k = CloseRig()
    k.r.run(1.1)
    k.r.key(SEL, 1)
    k.r.key(ST, 1)
    k.r.run(1.5)
    k.game_ends()
    k.r.run(0.55)
    k.r.key(ST, 0)
    k.r.key(SEL, 0)
    _check(k.r.stops == [] and k.r.next_mode() is None, "the game ended on its own inside the hold: Fightcade mode stays")
    _check(not k.r.p.game and k.r.p.enabled, "and the pointer is back on")
    k = CloseRig()
    k.game_ends()
    k.r.run(1.1)
    k.combo(hold=2.0 + M.TICK)
    _check(len(k.r.stops) == 1, "BROKEN guard: no game at all: the combo still leaves")


def test_helpers_are_waited_for():
    waited = []

    class P:
        def poll(self):
            return None

        def kill(self):
            waited.append("kill")

        def wait(self, timeout=None):
            waited.append("wait")
    M.GameCloser._kill_wait(P())
    _check(waited == ["kill", "wait"], "a killed helper is collected right away, no zombie (%s)" % waited)
    M.GameCloser._kill_wait(object())
    _check(True, "BROKEN: a helper without kill/wait does not crash the closer")


if __name__ == "__main__":
    main(globals())
