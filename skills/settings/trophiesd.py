#!/usr/bin/env python3
"""trophiesd: the TROPHIES overlay on a tap of LP (#177), instant like the power prompt.

A resident daemon (systemd unit coin-dojo-trophiesd, role settings) that:
  - keeps the TROPHIES page (#176, trophies_app.TrophyPainter) PRE-RENDERED from the cache and holds a primed native
    Wayland overlay (the power role's overlay.py: an xdg_toplevel made on show and destroyed on hide, because cage has
    no layer-shell), so a press costs one compositor round trip;
  - sleeps in select() on /dev/input/js* (a wake every TICK s for the clock below; no busy polling);
  - opens on LP (js button 4) only while no game runs: ES-DE has no child process (a game or a tool app it started)
    and no guard process (hk_game_procs / hk_game_patterns, the deploy guard's list) is up; with no ES-DE at all
    (cage down or restarting) it never opens;
  - closes on any button or a stick push, after AUTO_HIDE s, or when a game starts;
  - refreshes the data in the background: at start, every REFRESH_EVERY s while ES-DE is idle, and AFTER_GAME s
    after a game exits (new unlocks). Never while a game runs.

The state machine (Trophiesd) takes its overlay, busy check, refresh and clock as callables (tests/test_trophiesd.py).

    trophiesd.py [ENTRY.hks]       ENTRY defaults to $TROPHIESD_ENTRY, then trophies.hks next to this file
Env: TROPHIESD_OVERLAY_DIR (where overlay.py is, default /data/launcher), TROPHIESD_GAME_PROCS (comma list),
TROPHIESD_GAME_PATTERNS (comma list; hk_game_patterns allows no commas), TROPHIESD_DEVICES (comma list, default /dev/input/js*), WAYLAND_DISPLAY,
XDG_RUNTIME_DIR. Exit 0 = nothing to do (no entry / no overlay module), 1 = error.
"""
import ctypes
import glob
import os
import re
import select
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

JS = struct.Struct("IhBB")          # time, value, type, number (linux/joystick.h)
JS_BUTTON, JS_AXIS, JS_INIT = 0x01, 0x02, 0x80
LP = 4                              # panel by js index: MP=0 HK=1 MK=2 LK=3 LP=4 HP=5 (skills/badges/gen_help.py)
DEBOUNCE = 0.30                     # s: a press closer than this to the last one is a bounce
AXIS_CLOSE = 16000                  # |value| of a stick push that closes the overlay (digital sticks give 32767)
AUTO_HIDE = 60.0                    # s the overlay stays up without input
TICK = 5.0                          # s between clock checks while hidden (busy state, refresh, re-prime)
TICK_SHOWN = 1.0                    # s while shown (a game starting closes it quickly)
REFRESH_EVERY = 15 * 60.0           # s between data refreshes while ES-DE is idle
AFTER_GAME = 8.0                    # s after a game exits before the refresh (RetroAchievements needs a moment)
RESCAN = 10.0                       # s between looks for new joystick devices
TITLE = "coin-dojo-trophies"


def log(msg):
    sys.stderr.write("trophiesd: %s\n" % msg)
    sys.stderr.flush()


# ---- is a game running? ------------------------------------------------------------------------
def _procs(proc):
    """{pid: (comm, ppid)} from /proc/<pid>/stat; unreadable or odd entries are skipped, and so are zombies (state Z):
    a process that has exited is no game (#194: ES-DE's launch shell stays a zombie while the SETTINGS app runs)."""
    out = {}
    try:
        names = os.listdir(proc)
    except OSError:
        return out
    for d in names:
        if not d.isdigit():
            continue
        try:
            with open(os.path.join(proc, d, "stat")) as fh:
                st = fh.read()
            comm = st[st.index("(") + 1:st.rindex(")")]    # comm may hold spaces and ')'
            fields = st[st.rindex(")") + 2:].split()
            ppid = int(fields[1])
            if fields[0] == "Z":
                continue
        except (OSError, ValueError, IndexError):
            continue
        out[int(d)] = (comm, ppid)
    return out


def _cmdline(proc, pid):
    try:
        with open(os.path.join(proc, str(pid), "cmdline"), "rb") as fh:
            return fh.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _lineage(tab, pid):
    """pid and its ancestors in tab (a set; a loop in odd /proc data stops)."""
    out = set()
    while pid in tab and pid not in out:
        out.add(pid)
        pid = tab[pid][1]
    return out


def game_running(proc="/proc", procs=(), patterns=(), frontend_parent="cage", me=None):
    """True when the overlay must not open: ES-DE (the child of cage) has a child of its own, a guard process
    (`procs`: exact comm names, `patterns`: regexes over the command line) runs, or there is no ES-DE.
    me = a pid whose own line of ancestors is not a game (the SETTINGS app asks from inside ES-DE, #189: ES-DE's child
    is the app itself, or the shell that started it). None = only this process is skipped for patterns."""
    tab = _procs(proc)
    mine = _lineage(tab, me) if me is not None else set()
    esde = [p for p, (_c, pp) in tab.items() if pp in tab and tab[pp][0] == frontend_parent]
    if not esde:
        return True
    if any(pp in esde and p not in mine for p, (_c, pp) in tab.items()):
        return True
    if any(c in procs and p not in mine for p, (c, _pp) in tab.items()):
        return True
    rx = []
    for p in patterns:
        try:
            rx.append(re.compile(p))
        except re.error:
            continue
    if rx:
        skip = mine | {os.getpid()}
        for pid in tab:
            if pid not in skip:
                line = _cmdline(proc, pid)
                if line and any(r.search(line) for r in rx):
                    return True
    return False


# ---- joystick events ---------------------------------------------------------------------------
def parse_js(buf):
    """bytes -> ([(type, number, value)], rest): whole 8-byte events, a partial tail is kept for the next read."""
    n = len(buf) // JS.size * JS.size
    out = [(t, num, v) for (_tm, v, t, num) in JS.iter_unpack(buf[:n])]
    return out, buf[n:]


class Sticks:
    """The open /dev/input/js* fds; devices that vanish are dropped, new ones are picked up every RESCAN s."""

    def __init__(self, paths=None, opener=None):
        self.paths = paths
        self.open = opener or (lambda p: os.open(p, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC))
        self.fds = {}            # fd -> [path, pending bytes]
        self.last_scan = -RESCAN

    def scan(self, now):
        if now - self.last_scan < RESCAN:
            return
        self.last_scan = now
        have = {v[0] for v in self.fds.values()}
        for p in self.paths or sorted(glob.glob("/dev/input/js*")):
            if p not in have:
                try:
                    self.fds[self.open(p)] = [p, b""]
                except OSError:
                    pass

    def read(self, fd):
        try:
            data = os.read(fd, JS.size * 64)
        except BlockingIOError:
            return []
        except OSError:
            data = b""
        if not data:                                       # unplugged
            self.drop(fd)
            return []
        ev, self.fds[fd][1] = parse_js(self.fds[fd][1] + data)
        return ev

    def drop(self, fd):
        self.fds.pop(fd, None)
        try:
            os.close(fd)
        except OSError:
            pass


# ---- the state machine -------------------------------------------------------------------------
class Trophiesd:
    def __init__(self, overlay, busy, refresh, idle=None, now=time.monotonic):
        self.overlay = overlay        # .show() -> bool, .hide(), .prime() -> bool
        self.busy = busy              # () -> bool: a game runs (or no ES-DE)
        self.refresh = refresh        # () -> None, starts a background refresh
        self.idle = idle or (lambda: None)   # hidden + idle: keep the next frame ready (new minute -> redraw)
        self.now = now
        self.shown_at = None
        self.last_press = -DEBOUNCE
        self.was_busy = None
        self.next_refresh = 0.0       # at start

    @property
    def shown(self):
        return self.shown_at is not None

    def event(self, typ, number, value):
        """One js event -> 'show' | 'hide' | 'busy' | 'fail' | None (ignored)."""
        if typ & JS_INIT:
            return None
        now = self.now()
        if self.shown:
            push = (typ == JS_BUTTON and value == 1) or (typ == JS_AXIS and abs(value) >= AXIS_CLOSE)
            if not push or (typ == JS_BUTTON and number == LP and now - self.last_press < DEBOUNCE):
                return None
            self.last_press = now                          # a bounce of the closing press must not reopen it
            self.close()
            return "hide"
        if typ != JS_BUTTON or number != LP or value != 1:
            return None
        if now - self.last_press < DEBOUNCE:
            return None
        self.last_press = now
        if self.busy():
            return "busy"
        if not self.overlay.show():
            return "fail"
        self.shown_at = now
        return "show"

    def close(self):
        self.shown_at = None
        self.overlay.hide()

    def tick(self):
        now = self.now()
        b = self.busy()
        if self.shown and (b or now - self.shown_at >= AUTO_HIDE):
            self.close()
        if self.was_busy and not b:                        # a game just ended: fetch the new unlocks soon
            self.next_refresh = min(self.next_refresh, now + AFTER_GAME)
        self.was_busy = b
        if b:
            return
        if now >= self.next_refresh:
            self.next_refresh = now + REFRESH_EVERY
            self.refresh()
        if not self.shown:
            self.overlay.prime()                           # no-op when primed; re-primes after a cage restart
            self.idle()

    def timeout(self):
        return TICK_SHOWN if self.shown else TICK


# ---- frame: the 1440x1080 page centred in the output buffer ------------------------------------
class FramePainter:
    """The overlay's painter (overlay.Overlay calls render()). prepare() draws the page into the TrophyPainter's own
    surface when its data or minute changed (the 'ago' texts), so render() is only a copy: black rows, the page
    centred (the 4:3 area of the 16:9 panel)."""

    def __init__(self, tp, view, size, clock=time.time):
        self.tp = tp
        self.page_size = size         # (w, h) of the TrophyPainter's surface
        self.view = view              # () -> the page view dict
        self.clock = clock
        self.version = 0              # bumped when new data or images arrived
        self.key = None
        self.lock = threading.Lock()
        self._black = None

    def changed(self):
        with self.lock:
            self.version += 1

    def prepare(self):
        """Draw if stale. Returns True when it drew."""
        with self.lock:
            key = (self.version, int(self.clock() // 60))
            if key == self.key:
                return False
            if self.key is None or key[0] != self.key[0]:
                self.tp._surf.clear()                      # new images may be in the cache
            self.tp.draw_trophies(self.view())
            self.key = key
            return True

    def render(self, addr, w, h, stride, kind=None, left=0, total=0):
        self.prepare()
        with self.lock:
            c = self.tp.c
            src = c.cairo_image_surface_get_data(self.tp.surf)
            sstride = c.cairo_image_surface_get_stride(self.tp.surf)
            sw, sh = self.page_size
            if self._black is None or len(self._black) < stride:
                self._black = ctypes.create_string_buffer(b"\x00\x00\x00\xff" * (stride // 4))   # ARGB8888 LE, opaque
            ox, oy = max(0, (w - sw) // 2), max(0, (h - sh) // 2)
            cw, ch = min(sw, w), min(sh, h)
            mm, black = ctypes.memmove, self._black
            for y in range(h):
                row = addr + y * stride
                mm(row, black, stride)
                if oy <= y < oy + ch:
                    mm(row + ox * 4, src + (y - oy) * sstride, cw * 4)


def make_overlay(ov_mod, painter, logf=log):
    """A subclass of the power role's Overlay: same wire code, its own title, one static frame (no countdown)."""
    ov_mod.TITLE = TITLE                                   # _map() reads the module's TITLE; this process only

    class TrophyOverlay(ov_mod.Overlay):
        def show(self):
            if not self._try_lock():
                return False
            try:
                try:
                    if not self.ready():
                        return False
                    self.gen += 1
                    if not self.surface:
                        size = self.size
                        self._map()
                        if self.size != size:              # cage gave another size: the ready frame is gone
                            self.fresh = False
                    if not self.fresh:
                        k = self._free_buffer()
                        self.front = self.front if k is None else k
                        self._paint(self.front, "trophies")
                    self._present(self.front)
                    self.fresh = False
                    self.shown = "trophies"
                    return True
                except Exception as e:
                    logf("overlay show failed: %r" % (e,))
                    self._drop()
                    return False
            finally:
                self.lock.release()

        def repaint(self):
            """New data: paint a free buffer now (shown: present it; hidden: it is the next show's frame)."""
            if not self._try_lock(1.0):
                return
            try:
                if not self.ready():
                    return
                k = self._free_buffer()
                if k is None:
                    return
                self._paint(k, "trophies")
                if self.surface:
                    self._present(k)
                    self.fresh = False
                else:
                    self.front, self.fresh = k, True
            except Exception as e:
                logf("overlay repaint failed: %r" % (e,))
                self._drop()
            finally:
                self.lock.release()

    return TrophyOverlay(painter_factory=lambda: painter, log=logf)


# ---- main --------------------------------------------------------------------------------------
def _env_list(name, sep):
    return [x.strip() for x in os.environ.get(name, "").split(sep) if x.strip()]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) > 1 or (argv and argv[0].startswith("-")):
        print("usage: trophiesd.py [ENTRY.hks]", file=sys.stderr)
        return 2
    entry = (argv[0] if argv else None) or os.environ.get("TROPHIESD_ENTRY") or os.path.join(HERE, "trophies.hks")
    if not os.path.isfile(entry):
        log("no entry file %s (RetroAchievements off or no user): nothing to do" % entry)
        return 0
    sys.path.insert(0, os.environ.get("TROPHIESD_OVERLAY_DIR", "/data/launcher"))
    try:
        import overlay as ov_mod
    except Exception as e:
        log("no overlay module (power role not deployed?): %r" % (e,))
        return 0
    import trophies_core as core
    import trophies_app as ta

    cfg = core.load_config(entry)
    font_dir = os.path.join(HERE, "fonts") if os.path.isdir(os.path.join(HERE, "fonts")) else None
    page = core.Page(cfg)
    tp = ta.TrophyPainter(ta.sa.brand.load(font_dir=font_dir), cfg["cache_dir"])
    tp.help = (([], "ANY BUTTON CLOSES"),)
    painter = FramePainter(tp, lambda: dict(page.view(), focus=-1), (ta.W, ta.H))   # no cursor: nothing to browse
    ov = make_overlay(ov_mod, painter)

    running = threading.Lock()

    def worker():
        try:
            page.refresh()
            painter.changed()
            for kind, name in core.images_for(page.data or {}):
                core.fetch_image(cfg["cache_dir"], core.image_url(kind, name))
            painter.changed()
            painter.prepare()
            ov.repaint()
            log("refreshed: %s" % page.state)
        except Exception as e:
            log("refresh failed: %r" % (e,))
        finally:
            running.release()

    def refresh():
        if running.acquire(blocking=False):
            threading.Thread(target=worker, daemon=True).start()

    procs = _env_list("TROPHIESD_GAME_PROCS", ",")
    pats = _env_list("TROPHIESD_GAME_PATTERNS", ",")
    def idle():
        if painter.prepare():
            ov.repaint()

    d = Trophiesd(ov, lambda: game_running(procs=procs, patterns=pats), refresh, idle)
    sticks = Sticks(_env_list("TROPHIESD_DEVICES", ",") or None)
    painter.prepare()
    log("up: entry %s, %s" % (entry, "primed" if ov.prime() else "overlay not primed yet (cage?)"))
    last_tick = 0.0
    while True:
        now = time.monotonic()
        sticks.scan(now)
        try:
            ready = select.select(list(sticks.fds), [], [], d.timeout())[0] if sticks.fds else (time.sleep(d.timeout()) or [])
        except InterruptedError:
            ready = []
        for fd in ready:
            for typ, num, val in sticks.read(fd):
                t0 = time.monotonic()
                r = d.event(typ, num, val)
                if r == "show":
                    log("shown in %.0f ms" % ((time.monotonic() - t0) * 1000))
                elif r in ("busy", "fail"):
                    log("LP ignored: %s" % ("a game runs" if r == "busy" else "overlay not ready"))
        now = time.monotonic()
        if now - last_tick >= d.timeout() or not ready:
            last_tick = now
            d.tick()


if __name__ == "__main__":
    sys.exit(main())
