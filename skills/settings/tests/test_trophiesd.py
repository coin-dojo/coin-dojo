#!/usr/bin/env python3
"""Tests for trophiesd (#177): the TROPHIES overlay on a tap of LP.

No hardware, no compositor: the state machine gets a fake overlay, busy check and clock; the game check reads a fake
/proc tree; the frame copy runs on ctypes buffers. Broken cases: LP while a game runs (or with no ES-DE) never opens;
other buttons, releases, small stick moves and the driver's init events never open it; a failed overlay show leaves
it closed; odd /proc entries and a bad guard regex do not crash; a partial js read is kept; with no compositor the
overlay says False instead of raising.

    python3 skills/settings/tests/test_trophiesd.py
"""
import ctypes
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APPDIR = os.path.dirname(HERE)
REPO = os.path.normpath(os.path.join(APPDIR, "..", ".."))
sys.path.insert(0, APPDIR)
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
import trophiesd as td  # noqa: E402
from hktest import check, main  # noqa: E402

B, A = td.JS_BUTTON, td.JS_AXIS
MP, HK, MK, LK, LP, HP = 0, 1, 2, 3, 4, 5


class FakeOverlay:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def show(self):
        self.calls.append("show")
        return self.ok

    def hide(self):
        self.calls.append("hide")

    def prime(self):
        self.calls.append("prime")
        return True


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def daemon(busy=False, ok=True):
    ov, clk, st = FakeOverlay(ok), Clock(), {"busy": busy, "refresh": 0, "idle": 0}

    def refresh():
        st["refresh"] += 1

    def idle():
        st["idle"] += 1
    d = td.Trophiesd(ov, lambda: st["busy"], refresh, idle, now=clk)
    return d, ov, clk, st


def test_lp_opens_any_button_closes():
    d, ov, clk, _ = daemon()
    check(d.event(B, LP, 1) == "show" and d.shown and ov.calls == ["show"], "a tap of LP opens the overlay")
    check(d.event(B, LP, 0) is None and d.shown, "the LP release keeps it open")
    clk.t += 1
    check(d.event(B, MK, 1) == "hide" and not d.shown and ov.calls[-1] == "hide", "any button closes it (MK)")
    for btn in (MP, HK, LK, LP, HP, 8, 9):
        clk.t += 1
        d.event(B, LP, 1)
        clk.t += 1
        check(d.event(B, btn, 1) == "hide", "button %d closes it" % btn)
    clk.t += 1
    d.event(B, LP, 1)
    check(d.event(A, 0, 3000) is None and d.shown, "a small stick wobble keeps it open")
    check(d.event(A, 1, -32767) == "hide", "a stick push closes it")


def test_broken_other_inputs_never_open():
    d, ov, clk, _ = daemon()
    for typ, num, val in ((B, MP, 1), (B, HK, 1), (B, MK, 1), (B, LK, 1), (B, HP, 1), (B, 8, 1), (B, 9, 1),
                          (B, LP, 0), (A, 0, 32767), (B | td.JS_INIT, LP, 1), (A | td.JS_INIT, 0, 32767)):
        clk.t += 1
        check(d.event(typ, num, val) is None and not d.shown, "BROKEN: %r does not open it" % ((typ, num, val),))
    check(ov.calls == [], "the overlay was never asked to show")
    clk.t += 1
    d.event(B, LP, 1)
    check(d.event(B | td.JS_INIT, MK, 1) is None and d.shown, "BROKEN: a driver init event does not close it")


def test_broken_lp_while_a_game_runs():
    d, ov, clk, st = daemon(busy=True)
    check(d.event(B, LP, 1) == "busy" and not d.shown and "show" not in ov.calls, "BROKEN: LP during a game is ignored")
    st["busy"] = False
    clk.t += 1
    check(d.event(B, LP, 1) == "show", "after the game LP opens it")
    st["busy"] = True
    d.tick()
    check(not d.shown and ov.calls[-1] == "hide", "a game starting closes it on the next tick")


def test_broken_failed_show_stays_closed():
    d, ov, clk, _ = daemon(ok=False)
    check(d.event(B, LP, 1) == "fail" and not d.shown, "BROKEN: overlay not ready -> stays closed, no crash")


def test_debounce():
    d, ov, clk, _ = daemon()
    d.event(B, LP, 1)
    clk.t += 0.1
    check(d.event(B, LP, 1) is None and d.shown, "a bounce of LP right after opening does not close it")
    clk.t += 0.5
    check(d.event(B, LP, 1) == "hide", "a real second LP closes it")
    clk.t += 0.1
    check(d.event(B, LP, 1) is None and not d.shown, "a bounce right after closing does not reopen it")


def test_auto_hide_and_refresh_schedule():
    d, ov, clk, st = daemon()
    d.tick()
    check(st["refresh"] == 1 and "prime" in ov.calls and st["idle"] == 1, "first tick: refresh, prime, prepare")
    clk.t += 60
    d.tick()
    check(st["refresh"] == 1, "no refresh before %d s" % td.REFRESH_EVERY)
    clk.t += td.REFRESH_EVERY
    d.tick()
    check(st["refresh"] == 2, "refresh again after %d s while idle" % td.REFRESH_EVERY)
    st["busy"] = True
    clk.t += td.REFRESH_EVERY * 2
    n_idle = st["idle"]
    d.tick()
    check(st["refresh"] == 2 and st["idle"] == n_idle, "BROKEN: never refreshes or redraws while a game runs")
    st["busy"] = False
    clk.t += 1
    d.tick()
    check(st["refresh"] == 3, "the first idle tick after a long game refreshes (the 15 min passed)")
    st["busy"] = True
    clk.t += 1
    d.tick()
    st["busy"] = False
    clk.t += 1
    d.tick()
    check(st["refresh"] == 3, "a short game: no refresh at once ...")
    clk.t += td.AFTER_GAME
    d.tick()
    check(st["refresh"] == 4, "... but %d s after it ended (new unlocks)" % td.AFTER_GAME)
    clk.t += 1
    d.event(B, LP, 1)
    clk.t += td.AUTO_HIDE - 1
    d.tick()
    check(d.shown, "still open before %d s" % td.AUTO_HIDE)
    clk.t += 1
    d.tick()
    check(not d.shown, "closes by itself after %d s" % td.AUTO_HIDE)
    check(d.timeout() == td.TICK, "hidden: a wake every %d s" % td.TICK)


def _proc(tmp, rows):
    for row in rows:
        pid, comm, ppid, cmd = row[:4]
        state = row[4] if len(row) > 4 else "S"
        os.makedirs(os.path.join(tmp, str(pid)))
        with open(os.path.join(tmp, str(pid), "stat"), "w") as fh:
            fh.write("%d (%s) %s %d 1 1 0 -1\n" % (pid, comm, state, ppid))
        with open(os.path.join(tmp, str(pid), "cmdline"), "wb") as fh:
            fh.write(cmd.replace(" ", "\0").encode() + b"\0")
    return tmp


BASE = [(1, "systemd", 0, "/sbin/init"), (890, "cage", 880, "cage -d -- /usr/local/bin/es-de"),
        (909, "AppRun", 890, "/opt/ES-DE/squashfs-root/AppRun --resolution 1440 1080"),
        (500, "python3", 1, "/usr/bin/python3 /data/launcher/powerd.py")]


def test_game_running():
    with tempfile.TemporaryDirectory() as t:
        p = _proc(t, BASE)
        check(td.game_running(p, ("retroarch", "flycast")) is False, "idle ES-DE: no game")
    with tempfile.TemporaryDirectory() as t:
        p = _proc(t, BASE + [(950, "sh", 909, "sh -c retroarch -L core rom")])
        check(td.game_running(p) is True, "ES-DE has a child (game or tool app): busy")
    with tempfile.TemporaryDirectory() as t:
        p = _proc(t, BASE + [(960, "flycast", 1, "/data/emulators/flycast/flycast rom.gdi")])
        check(td.game_running(p, ("retroarch", "flycast")) is True, "a guard process name: busy")
    with tempfile.TemporaryDirectory() as t:
        p = _proc(t, BASE + [(970, "AppRun", 1, "/data/emulators/retroarch-1.22/retroarch -L x")])
        check(td.game_running(p, (), ["retroarch-1.22/retroarc[h]"]) is True, "a guard pattern on the command line: busy")
        check(td.game_running(p, (), ["(unclosed"]) is False, "BROKEN: a bad pattern is skipped, no crash")
    with tempfile.TemporaryDirectory() as t:
        p = _proc(t, [r for r in BASE if r[1] != "AppRun"])
        check(td.game_running(p) is True, "BROKEN: no ES-DE (cage restarting) counts as busy")
    with tempfile.TemporaryDirectory() as t:
        p = _proc(t, BASE + [(990, "odd) name (x", 1, "x")])
        os.makedirs(os.path.join(t, "991"))
        with open(os.path.join(t, "991", "stat"), "w") as fh:
            fh.write("garbage")
        os.makedirs(os.path.join(t, "self"))
        check(td.game_running(p) is False, "BROKEN: odd comm names and garbage stat files are skipped")
    check(td.game_running("/nonexistent-proc") is True, "BROKEN: unreadable /proc counts as busy")



def test_zombie_launch_shell_is_no_game():
    # #194, captured on the cabinet 2026-11-17: ES-DE's launch shell is a zombie, the app is reparented to pid 1.
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        p = _proc(t, BASE + [(593670, "sh", 909, "", "Z"),
                             (593671, "python3", 1, "python3 /data/coin-dojo/settings/settings_app.py Settings.hks")])
        check(td.game_running(p, ("retroarch", "flycast"), ["retroarch-1.22/retroarc[h]"], me=593671) is False,
              "#194 a zombie ES-DE child (exited launch shell) is no game")
    with tempfile.TemporaryDirectory() as t:
        p = _proc(t, BASE + [(593670, "sh", 909, "sh -c retroarch"), (593672, "retroarch", 593670, "retroarch -L x")])
        check(td.game_running(p, ("retroarch", "flycast"), me=None) is True,
              "BROKEN: a live launch shell with a game under it still counts as busy")
    with tempfile.TemporaryDirectory() as t:
        p = _proc(t, BASE + [(600, "flycast", 909, "flycast x", "Z")])
        check(td.game_running(p, ("retroarch", "flycast")) is False, "a zombie guard process is no game either")

def test_game_running_from_inside_es_de():
    """#189: the SETTINGS app asks from inside ES-DE (ES-DE -> sh -> python3 settings_app.py): its own line is no game."""
    app = [(950, "sh", 909, "sh -c python3 settings_app.py x.hks"), (951, "python3", 950, "python3 settings_app.py x.hks")]
    with tempfile.TemporaryDirectory() as t:
        p = _proc(t, BASE + app)
        check(td.game_running(p, ("retroarch", "flycast"), me=951) is False, "the app's own line under ES-DE: no game")
        check(td.game_running(p, ("retroarch", "flycast")) is True, "BROKEN: without me the app itself counts as busy")
        check(td.game_running(p, (), ["settings_ap[p]"], me=951) is False, "a pattern matching only the app's own line: no game")
    with tempfile.TemporaryDirectory() as t:
        p = _proc(t, BASE + app + [(960, "retroarch", 909, "retroarch -L core rom")])
        check(td.game_running(p, (), me=951) is True, "BROKEN: another ES-DE child (a game) next to the app is busy")
    with tempfile.TemporaryDirectory() as t:
        p = _proc(t, BASE + app + [(970, "flycast", 1, "/data/emulators/flycast/flycast rom.gdi")])
        check(td.game_running(p, ("retroarch", "flycast"), me=951) is True, "BROKEN: a guard process elsewhere is busy")


def test_parse_js_keeps_partial_events():
    ev = struct.pack("IhBB", 1, 1, B, LP) + struct.pack("IhBB", 2, 0, B, LP)
    out, rest = td.parse_js(ev + ev[:3])
    check(out == [(B, LP, 1), (B, LP, 0)] and rest == ev[:3], "two events, a partial tail kept")
    out, rest = td.parse_js(rest + ev[3:8])
    check(out == [(B, LP, 1)] and rest == b"", "BROKEN split read: the event is completed by the next read")


def test_sticks_pick_up_and_drop_devices():
    r, w = os.pipe()
    opened = []

    def opener(p):
        if p.endswith("js9"):
            raise OSError("ENOENT")
        opened.append(p)
        return r
    s = td.Sticks(["/dev/input/js0", "/dev/input/js9"], opener)
    s.scan(0.0)
    check(list(s.fds) == [r] and opened == ["/dev/input/js0"], "BROKEN: a missing device is skipped")
    os.write(w, struct.pack("IhBB", 1, 1, B, LP))
    check(s.read(r) == [(B, LP, 1)], "reads events from the fd")
    os.close(w)
    check(s.read(r) == [] and not s.fds, "BROKEN: an unplugged device (EOF) is dropped")


class FakeC:
    def __init__(self, buf, stride):
        self.buf, self.stride = buf, stride

    def cairo_image_surface_get_data(self, surf):
        return ctypes.addressof(self.buf)

    def cairo_image_surface_get_stride(self, surf):
        return self.stride


class FakeTP:
    def __init__(self, w, h):
        self.size = (w, h)
        self.buf = ctypes.create_string_buffer(b"\x11\x22\x33\xff" * (w * h))
        self.c, self.surf, self._surf, self.draws = FakeC(self.buf, w * 4), object(), {"x": 1}, 0

    def draw_trophies(self, v):
        self.draws += 1


def test_frame_is_centred_on_black():
    tp = FakeTP(4, 2)
    clock = Clock(1000.0)
    fp = td.FramePainter(tp, lambda: {}, tp.size, clock=clock)
    w, h = 8, 4
    out = ctypes.create_string_buffer(w * h * 4)
    fp.render(ctypes.addressof(out), w, h, w * 4)
    px = [out.raw[i * 4:i * 4 + 4] for i in range(w * h)]
    page, black = b"\x11\x22\x33\xff", b"\x00\x00\x00\xff"
    check(px[0] == black and px[w * 1 + 2] == page and px[w * 1 + 5] == page and px[w * 1 + 6] == black
          and px[w * 3] == black, "the page sits centred, the rest is opaque black")
    check(tp.draws == 1, "drawn once")
    fp.render(ctypes.addressof(out), w, h, w * 4)
    check(tp.draws == 1, "same minute, same data: the next frame is only a copy")
    clock.t += 60
    tp._surf["x"] = 1
    check(fp.prepare() and tp.draws == 2 and tp._surf, "a new minute redraws (the 'ago' texts), images kept")
    fp.changed()
    check(fp.prepare() and tp.draws == 3 and not tp._surf, "new data redraws and reloads the images")
    small = ctypes.create_string_buffer(2 * 1 * 4)
    fp.render(ctypes.addressof(small), 2, 1, 8)
    check(small.raw == page * 2, "BROKEN: an output smaller than the page is clipped, not overrun")


def test_broken_overlay_without_compositor():
    sys.path.insert(0, os.path.join(REPO, "ansible", "roles", "power", "files"))
    try:
        import overlay as ov_mod
    except Exception as e:                      # overlay.py needs nothing outside the stdlib at import
        check(False, "overlay.py imports: %r" % (e,))
        return
    logs = []
    with tempfile.TemporaryDirectory() as t:
        ov = td.make_overlay(ov_mod, td.FramePainter(FakeTP(4, 2), lambda: {}, (4, 2)), logf=logs.append)
        ov.runtime_dir, ov.display = t, "wayland-nope"
        check(ov_mod.TITLE == td.TITLE, "the overlay maps with its own title (cage tells it from the power prompt)")
        check(ov.prime() is False and ov.show() is False, "BROKEN: no compositor -> prime and show say False")
        ov.repaint()
        ov.hide()
        check(True, "repaint and hide without a compositor do not raise")


if __name__ == "__main__":
    main(globals())
