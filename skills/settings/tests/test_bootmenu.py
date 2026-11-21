#!/usr/bin/env python3
"""Boot menu tests (#210). Plain python3, stdlib only. Run from the repo root:

    python3 skills/settings/tests/test_bootmenu.py

Covers: stick + keyboard + panel-button selection (the Menu state and the real Input layer), countdown expiry -> esde,
any input stopping the countdown, --png renders a 1440x1080 frame, the live loop against SDL's dummy video driver
(--actions replay prints the answer, the countdown expires to esde), and the BROKEN cases: no display -> exit 3,
SIGTERM -> nothing on stdout and a non-zero exit, a bad argument -> exit 2, a stray word never reaches stdout.
Art + motion (#218): a card PNG is used when present; a missing, empty or corrupt one falls back to the drawn
illustration (BROKEN cases); the community tree carries no card art and the app never reads personal/; the lit card's
gold border is on the very first frame after an input (the slide only follows), driven through a fake SDL front.
"""
import ctypes
import os
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APPDIR = os.path.dirname(HERE)
REPO = os.path.normpath(os.path.join(APPDIR, "..", ".."))
sys.path.insert(0, APPDIR)
import bootmenu_app as bm  # noqa: E402
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
from hktest import check, main, skip  # noqa: E402

APP = os.path.join(APPDIR, "bootmenu_app.py")


def run(args, env=None, timeout=30):
    e = dict(os.environ, HOME=tempfile.mkdtemp())
    e.update(env or {})
    return subprocess.run([sys.executable, APP] + args, capture_output=True, text=True, timeout=timeout, env=e)


def dummy_ok():
    """True when SDL2 + cairo + freetype load here and SDL has its dummy video driver (the live-loop tests need it)."""
    try:
        bm.sa.Painter(bm.sa.brand.load())
        r = run(["--actions", "ok"], {"SDL_VIDEODRIVER": "dummy"})
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ------------------------------------------------------------------------------ state
def test_selection_stick_and_keyboard():
    for left, right in (("left", "right"), ("up", "down")):
        m = bm.Menu()
        check(m.sel == 0, "ARCADE is lit first")
        m.handle(right)
        check(m.sel == 1 and not m.choice, "%s moves to FIGHTCADE without choosing" % right)
        m.handle(right)
        check(m.sel == 1, "no wrap past the last card")
        m.handle(left)
        check(m.sel == 0, "%s moves back" % left)
    for confirm in ("ok", "enter", "start"):
        m = bm.Menu()
        check(m.handle(confirm) == "esde", "%s on ARCADE prints esde" % confirm)
        m = bm.Menu()
        m.handle("right")
        check(m.handle(confirm) == "fightcade", "%s on FIGHTCADE prints fightcade" % confirm)
    m = bm.Menu()
    for noise in ("back", "esc", "bksp", "tab", "text", "exit-ish"):
        m.handle(noise)
    check(m.choice is None and m.sel == 0, "back/esc/typing choose nothing")


def test_pad_input_layer_maps_to_menu():
    inp = bm.sa.Input()
    acts = inp.axis(0, 30000, 1.0) + inp.axis(0, 0, 1.1)
    check(acts == ["right"], "stick right -> right (got %r)" % acts)
    m = bm.Menu()
    for a in acts:
        m.handle(a)
    check(m.sel == 1, "the stick lights FIGHTCADE")
    acts = inp.button("ok", True, 2.0) + inp.button("ok", False, 2.1)                  # LK (SDL button A)
    check(acts == ["ok"], "LK press -> ok")
    check(m.handle(acts[0]) == "fightcade", "LK starts the lit mode")
    inp = bm.sa.Input()
    acts = inp.button("start", True, 1.0) + inp.button("start", False, 1.1)
    check(acts == ["start"], "START released alone -> start")
    inp = bm.sa.Input()
    acts = inp.button("select", True, 1.0) + inp.button("start", True, 1.1)
    check(acts == ["exit"], "SELECT+START is the exit combo, which the menu ignores (never a choice)")


# ------------------------------------------------------------------------------ countdown
def test_countdown():
    m = bm.Menu(5, 100.0)
    check(m.counting and m.remaining(102.0) == 3.0, "5 s countdown shows 3 s left at +2 s")
    check(m.tick(104.9) is None, "not over before 5 s")
    m.handle("right")
    check(not m.counting and m.remaining(104.0) == 0.0 and m.tick(200.0) is None,
          "any input (even moving the highlight) stops the countdown: it never expires afterwards")
    m = bm.Menu(5, 100.0)
    check(m.tick(105.0) == "esde", "expiry selects ARCADE")
    m = bm.Menu(0, 100.0)
    check(not m.counting and m.tick(10000.0) is None, "countdown 0 waits forever")
    m = bm.Menu(5, 100.0)
    m.sel = 1
    check(m.tick(106.0) == "esde", "expiry selects ARCADE even if FIGHTCADE was lit (the lit card cannot differ: input stops it)")


# ------------------------------------------------------------------------------ render
def test_png_render():
    try:
        bm.sa.Painter(bm.sa.brand.load())
    except OSError as e:
        skip("png render: %s" % e)
        return
    d = tempfile.mkdtemp()
    sizes = {}
    for name, args in (("count", ["--countdown", "5", "--at", "1"]), ("wait", []), ("fight", ["--actions", "right"])):
        out = os.path.join(d, name + ".png")
        r = run(["--png", out, "--hostname", "coin-dojo", "--log", ""] + args)
        data = open(out, "rb").read() if os.path.exists(out) else b""
        check(r.returncode == 0 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[16:24] == bytes.fromhex("000005a000000438"),
              "%s: renders a 1440x1080 PNG (rc %d %s)" % (name, r.returncode, r.stderr[-200:]))
        check(r.stdout == "", "%s: --png prints nothing on stdout" % name)
        sizes[name] = data
    check(len(set(sizes.values())) == 3, "the three states look different (countdown / waiting / FIGHTCADE lit)")


# ------------------------------------------------------------------------------ live loop (SDL dummy video driver)
DUMMY = {"SDL_VIDEODRIVER": "dummy"}


def test_live_loop():
    if not dummy_ok():
        skip("live loop: SDL2 dummy video driver not usable here")
        return
    r = run(["--actions", "right,ok"], DUMMY)
    check(r.returncode == 0 and r.stdout == "fightcade\n", "replayed stick right + LK prints exactly `fightcade` (%r %r)" % (r.stdout, r.stderr[-200:]))
    r = run(["--actions", "ok"], DUMMY)
    check(r.returncode == 0 and r.stdout == "esde\n", "replayed LK prints exactly `esde`")
    r = run(["--countdown", "1"], DUMMY)
    check(r.returncode == 0 and r.stdout == "esde\n", "countdown 1 expires to `esde` (%r %r)" % (r.stdout, r.stderr[-200:]))
    p = subprocess.Popen([sys.executable, APP, "--countdown", "1", "--actions", "left"], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, env=dict(os.environ, HOME=tempfile.mkdtemp(), **DUMMY))
    time.sleep(2.5)
    still = p.poll() is None
    p.send_signal(signal.SIGTERM)
    out, _ = p.communicate(timeout=10)
    check(still and out == "", "an input before expiry stops the countdown: still waiting after 2.5 s, nothing printed")


def test_BROKEN_sigterm_prints_nothing():
    if not dummy_ok():
        skip("sigterm: SDL2 dummy video driver not usable here")
        return
    home = tempfile.mkdtemp()
    log = os.path.join(home, "b.log")
    p = subprocess.Popen([sys.executable, APP, "--log", log], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         env=dict(os.environ, HOME=home, **DUMMY))
    for _ in range(100):                                    # the log's "start" line = the window is up
        if os.path.exists(log) or p.poll() is not None:
            break
        time.sleep(0.1)
    check(p.poll() is None, "the menu waits (countdown 0) until it is told to stop")
    p.send_signal(signal.SIGTERM)
    out, err = p.communicate(timeout=10)
    check(out == "" and p.returncode not in (0, None) and p.returncode != 3,
          "SIGTERM: nothing on stdout, non-zero exit (rc %r out %r err %r)" % (p.returncode, out, err[-200:]))


def test_BROKEN_no_display_exit_3():
    r = run([], {"SDL_VIDEODRIVER": "coin-dojo-no-such-driver"})
    check(r.returncode == 3 and r.stdout == "", "no usable display: exit 3, nothing on stdout (rc %d %r)" % (r.returncode, r.stdout))
    r = run(["--countdown", "5"], {"SDL_VIDEODRIVER": "coin-dojo-no-such-driver"})
    check(r.returncode == 3 and r.stdout == "", "no display with a countdown: still exit 3 and silent")


def test_BROKEN_bad_arguments_exit_2():
    for args in (["--countdown", "abc"], ["--countdown", "-1"], ["--bogus"], ["--png"], ["esde"]):
        r = run(args)
        check(r.returncode == 2 and r.stdout == "", "%s: usage error exit 2 (rc %d)" % (" ".join(args), r.returncode))


# ------------------------------------------------------------------------------ art (#218)
def painter_or_skip(art_dir):
    try:
        return bm.BootPainter(bm.sa.brand.load(), art_dir)
    except OSError as e:
        skip("art: %s" % e)
        return None


def frame_bytes(p, view):
    p.draw(view)
    data, stride = p.pixels()
    return ctypes.string_at(data, stride * bm.H)


def pixel(p, x, y):
    """(r, g, b) at x, y of the painter's frame (cairo ARGB32, little endian: B G R A)."""
    data, stride = p.pixels()
    px = ctypes.string_at(data + int(y) * stride + int(x) * 4, 4)
    return px[2], px[1], px[0]


def test_card_png_is_used():
    d = tempfile.mkdtemp()
    src = painter_or_skip("")
    if not src:
        return
    src.draw(bm.Menu().view(0, "x"))
    src.png(os.path.join(d, "esde.png"))                    # any PNG will do; the app scales it once to the card
    p = bm.BootPainter(bm.sa.brand.load(), d)
    check(p.art_used == {"esde": "png", "fightcade": "drawn"}, "esde.png is used, fightcade is drawn (%r)" % p.art_used)
    v = bm.Menu().view(0, "x")
    check(frame_bytes(p, v) != frame_bytes(src, v), "the card art changes the frame")


def test_BROKEN_missing_or_bad_art_falls_back():
    base = painter_or_skip("")
    if not base:
        return
    v = bm.Menu().view(0, "x")
    want = frame_bytes(base, v)
    d = tempfile.mkdtemp()
    open(os.path.join(d, "esde.png"), "wb").close()                                   # empty file
    open(os.path.join(d, "fightcade.png"), "wb").write(b"\x89PNG\r\n\x1a\nnot really a png")  # corrupt
    cases = (("no such dir", "/nonexistent/coin-dojo-art"), ("empty + corrupt files", d), ("art off", ""))
    for name, art in cases:
        p = bm.BootPainter(bm.sa.brand.load(), art)
        check(p.art_used == {"esde": "drawn", "fightcade": "drawn"}, "%s: both cards drawn (%r)" % (name, p.art_used))
        check(frame_bytes(p, v) == want, "%s: the frame equals the drawn-art frame (clean fallback)" % name)
    r = run(["--png", os.path.join(d, "out.png"), "--art-dir", d, "--log", ""])
    check(r.returncode == 0 and os.path.getsize(os.path.join(d, "out.png")) > 0,
          "--png with broken art still renders, exit 0 (rc %d %s)" % (r.returncode, r.stderr[-200:]))


def test_theme_ships_the_card_art():
    """#263: the theme ships the two card crops of the night art; the app reads them from the deployed theme only."""
    src = open(APP, encoding="utf-8").read()
    check("personal" not in bm.ART_DIR and "personal/" not in src,
          "the app reads card art from the deployed theme only, never from personal/ (%s)" % bm.ART_DIR)
    shipped = os.path.join(REPO, "themes", "coin-dojo", "_inc", "art", "bootmenu")
    files = sorted(os.listdir(shipped)) if os.path.isdir(shipped) else []
    check(files == ["esde.png", "fightcade.png"], "the theme ships exactly the two cards: %r" % files)
    for key in ("esde", "fightcade"):
        head = open(os.path.join(shipped, key + ".png"), "rb").read(24)
        check(head[16:24] == bm.ILL_W.to_bytes(4, "big") + bm.ILL_H.to_bytes(4, "big"),
              "shipped %s.png is pre-scaled to %dx%d (blitted 1:1 on the Pi)" % (key, bm.ILL_W, bm.ILL_H))
    p = painter_or_skip(shipped)
    if p:
        p.draw(bm.Menu().view(0.0, "x"))
        check(p.art_used == {"esde": "png", "fightcade": "png"}, "both cards use the shipped art (%r)" % p.art_used)
    # broken: an empty folder (the files missing) falls back to the drawn illustrations
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = painter_or_skip(d)
        if p:
            p.draw(bm.Menu().view(0.0, "x"))
            check(set(p.art_used.values()) == {"drawn"}, "BROKEN: without the files both cards are drawn")


# ------------------------------------------------------------------------------ motion (#218)
def test_motion_is_decoration_only():
    m = bm.Menu()
    check(m.anim(5.0) == 1.0, "no move yet: settled")
    m.handle("right", 10.0)
    v = m.view(10.0, "x")
    check(v["sel"] == 1 and v["anim"] == 0.0, "the highlight moves at once; the slide starts at 0")
    check(m.anim(10.0 + bm.ANIM_S) == 1.0 and bm.ANIM_S <= 0.2, "the slide is over within %.2f s" % bm.ANIM_S)
    m.handle("right", 11.0)
    check(m.moved_at == 10.0, "pushing against the edge does not restart the slide")
    p = painter_or_skip("")
    if not p:
        return
    gold = p.b.rgb("gold")
    for sel, label in ((1, "FIGHTCADE"), (0, "ARCADE")):
        m = bm.Menu()
        if sel:
            m.handle("right", 0.0)
        p.draw(m.view(0.0, "x"))                                    # the very first frame after the input
        x = bm.card_x(sel) + bm.CW / 2.0
        top = pixel(p, x, bm.TOP + 1)
        other = pixel(p, bm.card_x(1 - sel) + bm.CW / 2.0, bm.TOP + 1)
        check(max(abs(a - b) for a, b in zip(top, gold)) < 40 and other != top,
              "%s: its gold border is on the first frame after the input (got %r, other card %r)" % (label, top, other))
    check(p.wants_frame(dict(anim=0.5)) < p.wants_frame(dict(anim=1.0)) <= 1.0 / 10,
          "frames come faster only while sliding; the pulse alone runs at <= %d fps" % bm.PULSE_FPS)


def test_idle_menu_stops_redrawing():
    """Critic #218: no 12 fps full-frame redraw forever. After PULSE_FOR without input the glow holds still and
    wants_frame says None (the loop then only waits for events); an input starts the pulse again."""
    m = bm.Menu(0, 100.0)
    check(m.view(100.0 + bm.PULSE_FOR - 0.1, "x")["pulsing"], "pulsing right after the menu opens")
    idle = m.view(100.0 + bm.PULSE_FOR + 0.1, "x")
    check(not idle["pulsing"], "still after PULSE_FOR (%.0f s) without input" % bm.PULSE_FOR)
    m.handle("right", 200.0)
    check(m.view(200.5, "x")["pulsing"], "an input wakes the pulse")
    p = painter_or_skip("")
    if not p:
        return
    check(p.wants_frame(idle) is None, "idle: no frame is scheduled (events only)")
    check(p.wants_frame(dict(idle, anim=0.2)) is not None, "BROKEN guard: a slide in progress still gets frames")
    a, b = frame_bytes(p, idle), frame_bytes(p, dict(idle, t=idle["t"] + 0.7))
    check(a == b, "idle frames do not change over time (nothing to redraw)")
    notes = [mm["note"] for mm in bm.MODES]
    check("QUIT" in notes[0] and "START + SELECT" in notes[1], "per-card note: ARCADE says QUIT, FIGHTCADE START+SELECT")


class FakeFront:
    """Stands in for SDLFront (no SDL here): replays batches of inputs, records what each frame showed."""

    def __init__(self, batches):
        self.batches, self.frames, self.waits = list(batches), [], []

    def present(self, painter):
        self.frames.append(dict(painter.shown))

    def events(self, inp, timeout_ms):
        self.waits.append(timeout_ms)
        batch = self.batches.pop(0) if self.batches else ["ok"]
        if not batch:
            time.sleep(timeout_ms / 1000.0)                          # nothing pressed: SDL waits the whole timeout
        return batch

    def close(self):
        pass


def test_live_loop_logic_with_fake_front():
    if not painter_or_skip(""):
        return
    import contextlib
    import io
    front = FakeFront([[], [], ["right"], [], [], []])
    saved = (bm.sa.SDLFront, bm.GRACE, signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
    bm.sa.SDLFront, bm.GRACE = (lambda: front), 0
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = bm.main(["--art-dir", "", "--log", "", "--hostname", "x"])
    finally:
        bm.sa.SDLFront, bm.GRACE = saved[0], saved[1]
        signal.signal(signal.SIGTERM, saved[2])
        signal.signal(signal.SIGINT, saved[3])
    check(rc == 0 and out.getvalue() == "fightcade\n", "fake front: right + ok prints fightcade (rc %r %r)" % (rc, out.getvalue()))
    after = [f for f in front.frames if f["sel"] == 1]
    check(after and after[0]["anim"] < 0.5, "the first frame after the input already lights FIGHTCADE (anim %.2f)"
          % (after[0]["anim"] if after else -1))
    check(len(front.frames) >= 3, "motion keeps frames coming while nothing is pressed (%d frames)" % len(front.frames))
    check(max(front.waits) <= 1000 // bm.PULSE_FPS + 1, "the loop never sleeps past the next motion frame (%r)" % front.waits)


def test_stdout_is_only_the_answer():
    src = open(APP, encoding="utf-8").read()
    prints = [ln for ln in src.splitlines() if ln.strip().startswith("print(")]
    check(sum("file=sys.stderr" not in ln for ln in prints) == 1 and any("print(menu.choice)" in ln for ln in prints),
          "exactly one print goes to stdout (the answer); the others use stderr")


if __name__ == "__main__":
    main(globals())
