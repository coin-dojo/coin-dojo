#!/usr/bin/env python3
"""coin-dojo-stickmouse (#211): the arcade stick as a mouse for the Fightcade lobby (evdev -> uinput).

Only in Fightcade mode: coin-dojo-fightcade starts this as a systemd USER unit (`systemctl --user start
coin-dojo-stickmouse`) and stops it when the mode ends. It also does nothing unless
$XDG_RUNTIME_DIR/coin-dojo/current-mode says `fightcade` (the session writes it, #209).

  stick            pointer, accelerating the longer it is held (digital hat or analog axes)
  LK               left click          MK   right click
  SELECT + MK      toggle the pointer off/on by hand (state survives a crash restart); ignored while a game runs (#257)
  SELECT + LK      (#227) open/close the lobby's on-screen keyboard (inject.js of the fightcade role); never while a game
                   runs. THIS daemon owns whether it is open: while open the stick, LK, MK, START and SELECT no longer touch
                   the pointer; they go to the page as dedicated keys on this uinput device (F13 opens, F14-F17 move the
                   focus, F18 = LK types, F19 = MK deletes, F23 = START closes, F24 = SELECT closes; the page matches e.code and
                   reopens on any of them, so a reloaded page agrees again). START (#262) then sends a REAL Enter
                   (KEY_ENTER on this device, what a USB keyboard sends; the page ignores script-made keys) to the
                   focused field. START acts on release, so SELECT+START (the exit combo) never sends a chat line.
                   Closed, in a game or outside Fightcade mode: no key, never an Enter.
  SELECT + START   held 2 s, in either toggle state:
                   - a game runs (#231): close ONLY the game, the lobby stays (escalation below);
                   - in the lobby: write `menu` to $XDG_RUNTIME_DIR/coin-dojo/next-mode and run the session's stop
                     hook (STICKMOUSE_STOP_CMD, default /usr/local/bin/coin-dojo-session-stop): leave Fightcade mode.

A game (#231) is a process of our uid whose cmdline has a token naming one of GAMES (last / or \\ path part, any
case). In the Fightcade scope (/proc/<pid>/cgroup names coin-dojo-fightcade.scope) an absolute or Z: path must lie
under the Fightcade tree and a relative or C: one counts too. Outside the scope (#241: no user manager, so
coin-dojo-fightcade runs without one; a renamed or crashed scope) only an absolute or Z: path under HK_FC_TREE that
is the program itself counts: argv[0], or argv[1..2] when every earlier token is a launcher (LAUNCHERS: box64, wine,
the wrappers). So `less`, `cp`, `strace -f` or `gdb --args box64` naming the FBNeo path never count. It is logged once ("game outside <scope>") and closed like any game, so the combo never leaves the mode while it
runs. Box64 + Wine run it, so the comm is not the exe name; the cmdline is. /proc is scanned
only in Fightcade mode: on demand when the combo fires, every GAME_EVERY s for the pointer, every CLOSE_EVERY s
while closing. While a game runs the pointer is off (automatic, not persisted) and comes back on when it ends (#257).
Closing a game (#257): first the game's X window is asked to close like the X button (WM_DELETE_WINDOW from a short
`stickmouse.py --xclose <pids>` process, plain libX11 through ctypes; only top-level windows whose _NET_WM_PID is one of
the game's pids and that list WM_DELETE_WINDOW, so never the lobby). Wine turns it into WM_CLOSE, the message taskkill
posts, so FBNeo quits as from its menu, without a cold Box64 + Wine start. When no window took it (rc 3/4) or after
X_GRACE s: `wine taskkill /im <exe>` (no /f) through the Fightcade wine wrapper, with the game's own DISPLAY/WINEPREFIX;
after CLOSE_GRACE s SIGTERM, after TERM_WAIT s more SIGKILL, each only to the game's process tree (the game pids +
descendants, our uid, never pid 1, ourselves or wineserver). taskkill's own run time is logged ("taskkill exited 0
after 5.0 s"); it is slow because taskkill.exe is an x86 Windows program (Box64 cold start, 2-6 s, #241).
After a game ended (closed by the combo or on its own) SELECT+START does not leave Fightcade mode for EXIT_GRACE s.
The pointer is ON again when a game ends, except an OFF set by hand before it came up (a toggle during the game is not a choice).
A close that gave up (the game survived SIGKILL: D state, or it came back) is remembered: the next combo on the same
pids leaves Fightcade mode instead, so the player is never trapped.
Wine's detached helpers (services, winedevice, explorer) and the wineserver are not the game's descendants and stay
after a SIGTERM/SIGKILL close; the next game reuses them, the mode's cleanup ends them.
Never `wineserver -k`: the lobby is native arm64 Electron, not Wine, but the RAM prefix's wineserver outlives a game
(the next one starts faster) and the mode's own cleanup kills it when the mode ends.

It never grabs the stick (on or off): the game and the lobby keep seeing every event. Devices come and go: a
vanished stick is dropped and found again by scanning /sys/class/input (a device with X/Y or hat axes and gamepad
buttons; never our own uinput device).

Environment (all optional): STICKMOUSE_DEVICES (comma list of event nodes, skips the scan), STICKMOUSE_UINPUT
(default /dev/uinput), STICKMOUSE_STOP_CMD, STICKMOUSE_BTN_LK/MK/SELECT/START (evdev codes, defaults 291 290 296 297 =
js index 3/2/8/9 + 288, measured on the cabinet), STICKMOUSE_BASE/ACCEL/MAX (pointer
speed px/s, px/s^2, px/s), XDG_RUNTIME_DIR, STICKMOUSE_GAMES (comma list of emulator exe names),
STICKMOUSE_FC_CONF (the fightcade role's config, default /usr/local/etc/coin-dojo-fightcade.conf: HK_FC_TREE,
HK_FC_BINDIR, HK_FC_UNIT come from there), STICKMOUSE_CLOSE_GRACE/TERM_WAIT (s). Exit 2 = /dev/uinput unusable (the
unit does not restart on 2). Pure stdlib (struct, fcntl, select), like powerd."""
import errno, fcntl, os, select, shlex, signal, struct, subprocess, sys, time

FMT = 'llHHi'                      # struct input_event on the 64-bit Pi OS, same as powerd
SZ = struct.calcsize(FMT)
EV_SYN, EV_KEY, EV_REL, EV_ABS = 0, 1, 2, 3
SYN_DROPPED = 3                    # the kernel lost events: our picture of held buttons/axes may be wrong
REL_X, REL_Y = 0, 1
ABS_X, ABS_Y, ABS_HAT0X, ABS_HAT0Y = 0, 1, 16, 17
BTN_LEFT, BTN_RIGHT = 0x110, 0x111
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_RELBIT = 0x40045564, 0x40045565, 0x40045566
UI_DEV_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY = 0x405c5503, 0x5501, 0x5502
EVIOCGABS = 0x80184540             # + axis; struct input_absinfo = 6 ints
# #227 on-screen keyboard: the keys the page (inject.js) listens to while it is open.
# Dedicated keys nothing else uses (F13-F19, F23, F24; FBNeo binds nothing above F12). xkb's inet(evdev) gives most of them
# other keysyms (F13 XF86Tools, F14-F18 XF86Launch5-9, F23 XF86TouchpadOff; F20-F22 mic mute and touchpad keys are not
# used; only F19 and F24 keep their names), which cage, Xwayland and Electron ignore: a stray one does nothing in the lobby or in FBNeo. The page matches them by e.code (KeyboardEvent.code F13..F24
# comes from the evdev keycode, not from the keysym, which inet(evdev) rewrites for most of them).
KEY_OPEN, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_PRESS, KEY_DELETE, KEY_SUBMIT, KEY_CANCEL = 183, 184, 185, 186, 187, 188, 189, 193, 194
OSK_KEYS = (KEY_OPEN, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_PRESS, KEY_DELETE, KEY_SUBMIT, KEY_CANCEL)
# #262: START sends a REAL Enter after F23, the one key that is not dedicated: an Enter the page makes up itself has
# isTrusted false and web apps ignore it (the maintainer, cabinet 2026-11-19: the lobby chat did nothing). Only on START release
# while the keyboard is open, in the lobby (no game), never in the SELECT+START combo.
KEY_ENTER = 28
OSK_DIR = 0.5                      # stick deflection that counts as a direction press
OSK_DELAY, OSK_REPEAT = 0.4, 0.12  # s before a held direction repeats, and between repeats
OSK_GAME_EVERY = 0.25              # s between game scans while the keyboard is open (a game must not receive its keys)
NAME = 'coin-dojo-stickmouse'
EXIT_UINPUT = 2

HOLD = 2.0                         # s of SELECT+START to leave Fightcade mode
DEADZONE = 0.25                    # analog deflection below this is centered
TICK = 0.016                       # s between pointer updates while moving
MODE_EVERY = 0.5                   # s between reads of current-mode
RESCAN = 2.0                       # s between device scans while no stick is open
# #231: the Fightcade emulators (FBNeo is the one the rooms start; the others only if a tree ships them).
GAMES = ('fcadefbneo.exe', 'ggpofba-ng.exe', 'ggpofba.exe', 'fcadesnes9x.exe', 'snes9x.exe', 'flycast.exe')
GAME_EVERY = 1.0                   # s between /proc scans for the pointer auto-off (Fightcade mode only)
CLOSE_EVERY = 0.25                 # s between /proc scans while a game is being closed
# #241, cabinet journal 2026-11-18: taskkill exited after 1.9 s / 5.0 s, the game closed after 3.3 s / 6.0 s (6.0 was
# the old limit). 10 s is the 6.0 s worst close plus 4 s of room for a slower Box64 cold start.
CLOSE_GRACE = 10.0                 # s from taskkill's start before SIGTERM
# #257: the first step is the desktop close (WM_DELETE_WINDOW to the game's X window, ~0.2-0.5 s, no Box64 cold start);
# taskkill (2-6 s cold on the Pi) only follows when no window took it, or after X_GRACE s.
X_GRACE = 3.0                      # s the window close gets before taskkill starts
EXIT_GRACE = 5.0                   # s after a game ended in which SELECT+START cannot leave Fightcade mode
TERM_WAIT = 2.0                    # s after SIGTERM before SIGKILL
KILL_WAIT = 2.0                    # s after SIGKILL before giving up (logged)
FC_CONF = '/usr/local/etc/coin-dojo-fightcade.conf'
FC_WINE = '/usr/local/lib/coin-dojo-fightcade/wine'
FC_SCOPE = 'coin-dojo-fightcade.scope'
# #241: what may run a game outside the scope, before its exe in argv (basenames, any case).
LAUNCHERS = ('box64', 'wine', 'wine64', 'wine-preloader', 'wine64-preloader', 'hk-fightcade-wrap')
LAUNCH_DEPTH = 2                   # the exe at argv[0..2] at most (box64 wine fcadefbneo.exe)
GAME_ENV = ('DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'WINEPREFIX')   # copied from the game into taskkill


def _num(name, default, cast=float):
    try:
        return cast(os.environ.get(name) or default)
    except ValueError:
        return default


def codes():
    # Measured on the cabinet (Coordinator, 2026-11-18): the stick is "USB gamepad", KEY=3ff00000000 = evdev 288..297 = js index 0..9.
    # Panel by js index (skills/settings/trophiesd.py:38, skills/badges/gen_help.py): MP=0 HK=1 MK=2 LK=3 LP=4 HP=5; the SDL
    # mapping in the cabinet's ~/.bash_profile has back:b8 start:b9. 288 = BTN_TRIGGER + js index.
    return {'lk': _num('STICKMOUSE_BTN_LK', 291, int), 'mk': _num('STICKMOUSE_BTN_MK', 290, int),
            'select': _num('STICKMOUSE_BTN_SELECT', 296, int), 'start': _num('STICKMOUSE_BTN_START', 297, int)}


def log(msg):
    sys.stderr.write('stickmouse: %s\n' % msg)
    sys.stderr.flush()


def stop_argv(stop_cmd):
    """The stop hook runs in its own transient user unit, not in ours: stopping coin-dojo-stickmouse (which the hook
    does) must not kill the hook mid-run."""
    return ['systemd-run', '--user', '--collect', '--quiet'] + list(stop_cmd)


def runtime_dir(env=None):
    env = os.environ if env is None else env
    return os.path.join(env.get('XDG_RUNTIME_DIR') or '/run/user/%d' % os.getuid(), 'coin-dojo')


def read_mode(rt):
    try:
        with open(os.path.join(rt, 'current-mode')) as fh:
            return fh.read().strip()
    except OSError:
        return ''


def write_next_mode(rt, mode='menu'):
    """Atomic: the session reads next-mode the moment Fightcade ends, never a half-written file."""
    os.makedirs(rt, exist_ok=True)
    tmp = os.path.join(rt, '.next-mode.%d' % os.getpid())
    with open(tmp, 'w') as fh:
        fh.write(mode + '\n')
    os.replace(tmp, os.path.join(rt, 'next-mode'))


# ---- games (#231): find the Fightcade emulator in /proc, close only its process tree ------------------------------
def fc_conf(path=FC_CONF):
    """KEY=value lines of the fightcade role's config (shell-quoted by Ansible); {} when it is missing."""
    out = {}
    try:
        with open(path) as fh:
            for line in fh:
                k, eq, v = line.strip().partition('=')
                if eq and k.startswith('HK_FC_') and not v.startswith('('):
                    try:
                        out[k] = ' '.join(shlex.split(v))
                    except ValueError:
                        pass
    except OSError:
        pass
    return out


def game_names(env=None):
    env = os.environ if env is None else env
    names = [n.strip().lower() for n in (env.get('STICKMOUSE_GAMES') or '').split(',') if n.strip()]
    return tuple(names) or GAMES


def _read(path, binary=False):
    try:
        with open(path, 'rb' if binary else 'r') as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _base(tok):
    return tok.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]


def game_token(argv, names=GAMES, tree='', anchored=False):
    """The emulator name if this cmdline runs one, else None. Our own `wine taskkill /im fcadefbneo.exe` names it
    too: a taskkill cmdline and a token right after /im never count. A unix-absolute or Z: path must lie under
    `tree` (when given); a relative or C: path is judged by the scope check alone (Wine may rewrite the cmdline).
    anchored (#241, a process outside the scope): only an absolute or Z: path under a known `tree` counts, and only
    as the program itself: argv[0], or argv[1..LAUNCH_DEPTH] when every earlier token is one of LAUNCHERS."""
    low = [a.lower() for a in argv]
    if any(_base(a) in ('taskkill', 'taskkill.exe') for a in low):
        return None
    t = tree.rstrip('/').lower()
    for i, a in enumerate(low):
        name = _base(a)
        if name not in names or (i and low[i - 1] == '/im'):
            continue
        p = a.replace('\\', '/')
        if p[:2] == 'z:':
            p = p[2:]
        if t and p.startswith('/') and not p.startswith(t + '/'):
            continue
        if anchored and not (t and p.startswith(t + '/') and i <= LAUNCH_DEPTH
                             and all(_base(b) in LAUNCHERS for b in low[:i])):
            continue
        return name
    return None


def in_scope(cgroup_text, scope):
    """/proc/<pid>/cgroup names the unit `scope` as one path component (cgroup v2 `0::/.../x.scope`)."""
    if not scope:
        return True
    for line in (cgroup_text or '').splitlines():
        if scope in line.split(':', 2)[-1].split('/'):
            return True
    return False


def _uid_of(path):
    return os.stat(path).st_uid


def _pids(proc, status=None):
    try:
        return [int(n) for n in os.listdir(proc) if n.isdigit()]
    except OSError:
        if status is not None:       # #262: the caller learns the scan saw nothing because it could not look
            status['ok'] = False
        return []


def scan_games(proc='/proc', uid=None, names=GAMES, scope=FC_SCOPE, tree='', uid_of=_uid_of, outside=None, status=None):
    """{pid: emulator name} of the running games. Cheap: stat first, cmdline/cgroup only for our own uid.
    A game outside `scope` (only an anchored path under `tree` counts there) is in the result too and also goes into
    the dict `outside` when one is given, for the log."""
    uid = os.getuid() if uid is None else uid
    found = {}
    for pid in _pids(proc, status):
        d = os.path.join(proc, str(pid))
        try:
            if uid_of(d) != uid:
                continue
        except OSError:
            continue
        raw = _read(os.path.join(d, 'cmdline'), binary=True)
        if not raw:
            continue
        argv = raw.decode('utf-8', 'replace').split('\0')
        name = game_token(argv, names, tree)
        if not name:
            continue
        if in_scope(_read(os.path.join(d, 'cgroup')), scope):
            found[pid] = name
        elif game_token(argv, names, tree, anchored=True):
            found[pid] = name
            if outside is not None:
                outside[pid] = name
    return found


class GameScan:
    """scan_games() as the find() of GameCloser and Pointer, plus one log line each time the set of games outside the
    scope changes (#241): the mode stays and the game is closed, but someone should know the scope is missing."""

    def __init__(self, scope=FC_SCOPE, **kw):
        self.scope, self.kw, self.outside = scope, kw, {}
        self.ok = True               # #262: False when the last scan could not list /proc (it saw nothing, not "no game")

    def __call__(self):
        outside, status = {}, {'ok': True}
        found = scan_games(scope=self.scope, outside=outside, status=status, **self.kw)
        self.ok = status['ok']
        if outside and sorted(outside) != sorted(self.outside):
            log('game outside %s (%s): treated as a game; the combo closes it, the mode stays' % (
                self.scope, ', '.join('%s pid %d' % (n, p) for p, n in sorted(outside.items()))))
        self.outside = outside
        return found


def _stat(proc, pid):
    """(ppid, comm) from /proc/<pid>/stat; the comm may hold spaces and parens."""
    s = _read(os.path.join(proc, str(pid), 'stat'))
    if not s or ')' not in s:
        return None, ''
    head, _, rest = s.rpartition(')')
    try:
        return int(rest.split()[1]), head.partition('(')[2]
    except (IndexError, ValueError):
        return None, ''


def game_tree(roots, proc='/proc', uid=None, uid_of=_uid_of, me=None):
    """The game pids plus their descendants: what a TERM/KILL may reach. Never pid 1, ourselves, another uid or a
    wineserver (it outlives the game; the mode's cleanup kills it)."""
    uid = os.getuid() if uid is None else uid
    me = os.getpid() if me is None else me
    kids, comm = {}, {}
    for pid in _pids(proc):
        ppid, c = _stat(proc, pid)
        comm[pid] = c
        if ppid is not None:
            kids.setdefault(ppid, []).append(pid)
    out, todo = set(), [p for p in roots]
    while todo:
        pid = todo.pop()
        if pid in out:
            continue
        out.add(pid)
        todo.extend(kids.get(pid, []))
    keep = set()
    for pid in out:
        if pid <= 1 or pid == me:
            continue
        try:
            if uid_of(os.path.join(proc, str(pid))) != uid:
                continue
        except OSError:
            continue
        raw = _read(os.path.join(proc, str(pid), 'cmdline'), binary=True) or b''
        if 'wineserver' in comm.get(pid, '') or any(_base(a.lower()) == 'wineserver' for a in raw.decode('utf-8', 'replace').split('\0')):
            continue
        keep.add(pid)
    return keep


def game_env(proc, pid):
    """GAME_ENV taken from the game's own environment, so taskkill reaches the same wineserver and display."""
    raw = _read(os.path.join(proc, str(pid), 'environ'), binary=True) or b''
    out = {}
    for kv in raw.split(b'\0'):
        k, eq, v = kv.decode('utf-8', 'replace').partition('=')
        if eq and k in GAME_ENV:
            out[k] = v
    return out


XCLOSE_SENT, XCLOSE_NONE, XCLOSE_NOCONN = 0, 3, 4   # exit codes of `stickmouse.py --xclose`


def x_close(pids, display=None):
    """#257: ask the X windows of `pids` to close the normal way: a WM_PROTOCOLS / WM_DELETE_WINDOW client message
    (what the X button sends; Wine turns it into WM_CLOSE, the same message taskkill posts, so FBNeo quits as from its
    menu). Only top-level windows (the root's children, and theirs when a window manager reparents) whose
    _NET_WM_PID is one of `pids` AND that list WM_DELETE_WINDOW; the lobby (another pid) never matches. Plain libX11
    through ctypes: no package. Returns the window ids it sent to; raises OSError when X cannot be reached."""
    import ctypes

    class ClientMsg(ctypes.Structure):
        _fields_ = [('type', ctypes.c_int), ('serial', ctypes.c_ulong), ('send_event', ctypes.c_int),
                    ('display', ctypes.c_void_p), ('window', ctypes.c_ulong), ('message_type', ctypes.c_ulong),
                    ('format', ctypes.c_int), ('l', ctypes.c_long * 5)]
    u, ul, vp, cp = ctypes.c_uint, ctypes.c_ulong, ctypes.c_void_p, ctypes.POINTER
    x = ctypes.CDLL('libX11.so.6')
    x.XOpenDisplay.restype, x.XOpenDisplay.argtypes = vp, [ctypes.c_char_p]
    x.XDefaultRootWindow.restype, x.XDefaultRootWindow.argtypes = ul, [vp]
    x.XInternAtom.restype, x.XInternAtom.argtypes = ul, [vp, ctypes.c_char_p, ctypes.c_int]
    x.XQueryTree.argtypes = [vp, ul, cp(ul), cp(ul), cp(cp(ul)), cp(u)]
    x.XGetWindowProperty.argtypes = [vp, ul, ul, ctypes.c_long, ctypes.c_long, ctypes.c_int, ul, cp(ul), cp(ctypes.c_int),
                                     cp(ul), cp(ul), cp(vp)]
    x.XSendEvent.argtypes = [vp, ul, ctypes.c_int, ctypes.c_long, vp]
    x.XFree.argtypes = x.XFlush.argtypes = x.XCloseDisplay.argtypes = [vp]
    dpy = x.XOpenDisplay(display.encode() if display else None)
    if not dpy:
        raise OSError('cannot open the X display %r' % display)
    pids, sent = set(pids), []
    try:
        root = x.XDefaultRootWindow(dpy)
        a_pid, a_prot, a_del = (x.XInternAtom(dpy, n, 0) for n in (b'_NET_WM_PID', b'WM_PROTOCOLS', b'WM_DELETE_WINDOW'))

        def prop(w, atom, typ):
            at, fmt, nit, after, data = ul(), ctypes.c_int(), ul(), ul(), vp()
            if x.XGetWindowProperty(dpy, w, atom, 0, 64, 0, typ, ctypes.byref(at), ctypes.byref(fmt), ctypes.byref(nit),
                                    ctypes.byref(after), ctypes.byref(data)) != 0 or not data.value:
                return []
            out = list(ctypes.cast(data.value, cp(ul))[:nit.value]) if fmt.value == 32 else []
            x.XFree(data)
            return out

        def walk(w, depth):
            r, p, kids, n = ul(), ul(), cp(ul)(), u()
            if not x.XQueryTree(dpy, w, ctypes.byref(r), ctypes.byref(p), ctypes.byref(kids), ctypes.byref(n)):
                return
            ids = list(kids[:n.value]) if kids else []
            if kids:
                x.XFree(kids)
            for c in ids:
                wp = prop(c, a_pid, 6)                      # XA_CARDINAL
                if wp and wp[0] in pids and a_del in prop(c, a_prot, 4):   # XA_ATOM
                    ev = ClientMsg(33, 0, 1, dpy, c, a_prot, 32, (ctypes.c_long * 5)(a_del, 0, 0, 0, 0))   # ClientMessage
                    buf = ctypes.create_string_buffer(192)   # sizeof(XEvent)
                    ctypes.memmove(buf, ctypes.byref(ev), ctypes.sizeof(ev))
                    x.XSendEvent(dpy, c, 0, 0, buf)
                    sent.append(c)
                elif depth < 2:
                    walk(c, depth + 1)
        walk(root, 0)
        x.XFlush(dpy)
    finally:
        x.XCloseDisplay(dpy)
    return sent


def xclose_main(argv):
    """`stickmouse.py --xclose pid...` (DISPLAY/XAUTHORITY from the environment): 0 sent, 3 no window, 4 no X."""
    try:
        pids = {int(a) for a in argv}
    except ValueError:
        return XCLOSE_NOCONN
    try:
        sent = x_close(pids, os.environ.get('DISPLAY'))
    except (OSError, AttributeError) as e:
        log('x close: %s' % e)
        return XCLOSE_NOCONN
    log('x close: WM_DELETE_WINDOW to %d window(s) of pids %s' % (len(sent), ' '.join(map(str, sorted(pids)))))
    return XCLOSE_SENT if sent else XCLOSE_NONE


class GameCloser:
    """Close the running game(s), escalating on a clock: the X window close (#257, WM_DELETE_WINDOW) -> taskkill (no /f)
    -> SIGTERM -> SIGKILL, each step only to the game's process tree. find() -> {pid: name}; tree(pids) -> pids that
    may be signalled; spawn(argv, pid) starts taskkill and returns a Popen-like (poll, kill) or raises OSError;
    xspawn(pids, pid) starts the window close the same way (None = no window step). Driven by step() from the main loop."""

    def __init__(self, find, tree, spawn=None, kill=os.kill, clock=time.monotonic, wine=FC_WINE,
                 grace=CLOSE_GRACE, term_wait=TERM_WAIT, kill_wait=KILL_WAIT, every=CLOSE_EVERY, xspawn=None, x_grace=X_GRACE):
        self.find, self.tree, self.spawn, self.kill, self.clock, self.wine = find, tree, spawn, kill, clock, wine
        self.grace, self.term_wait, self.kill_wait, self.every = grace, term_wait, kill_wait, every
        self.xspawn, self.x_grace, self.xproc, self.found = xspawn, x_grace, None, {}
        self.stage = None            # None | 'x' | 'close' | 'term' | 'kill'
        self.procs = []
        self.signalled = []          # (stage, pid, sig) for the log and the tests
        self.gave_up = set()         # pids the last close could not end (#241): the next combo on them leaves

    @property
    def busy(self):
        return self.stage is not None

    def start(self, found):
        now = self.clock()
        self.t0, self.check_at, self.procs, self.found, self.xproc = now, now + self.every, [], found, None
        if self.xspawn:
            pids = sorted(self.tree(list(found)))
            try:
                self.xproc = self.xspawn(pids, min(found))
                self.stage, self.deadline = 'x', now + self.x_grace
                log('closing the game window (WM_DELETE_WINDOW) of pids %s' % ' '.join(map(str, pids)))
                return self.deadline
            except OSError as e:
                log('cannot run the window close: %s; taskkill instead' % e)
        return self._taskkill(now)

    def _taskkill(self, now):
        found = self.found
        self.stage, self.xproc = 'close', None
        self.deadline = now + self.grace
        for name in sorted(set(found.values())):
            pid = min(p for p, n in found.items() if n == name)
            argv = [self.wine, 'taskkill', '/im', name]
            try:
                if not self.spawn:
                    raise OSError('no taskkill launcher')
                self.procs.append((self.spawn(argv, pid), now))
                log('closing %s: %s' % (name, ' '.join(argv)))
            except OSError as e:
                log('cannot run %s: %s; going straight to SIGTERM' % (' '.join(argv), e))
                self.deadline = now
        return self.deadline

    @staticmethod
    def _kill_wait(p):
        """Kill a helper and collect it, so it never stays a zombie."""
        try:
            p.kill()
        except Exception:               # already gone, or a fake
            pass
        try:
            p.wait(timeout=0.2)
        except Exception:               # a fake without wait(), a timeout: the next poll() reaps it
            pass

    def _reap(self, kill=False):
        for entry in list(self.procs):
            p, t_start = entry
            rc = p.poll()
            killed = False
            if rc is None and kill:
                self._kill_wait(p)
                killed = True
                rc = p.poll()
            if rc is not None:
                log('taskkill %s %s after %.1f s' % ('killed, rc' if killed else 'exited', rc, self.clock() - t_start))
                self.procs.remove(entry)

    def _signal(self, found, sig, label):
        pids = sorted(self.tree(list(found)))
        log('game still up after %.1f s: %s to %s' % (self.clock() - self.t0, label, ' '.join(map(str, pids)) or 'nothing'))
        for pid in pids:
            try:
                self.kill(pid, sig)
                self.signalled.append((self.stage, pid, sig))
            except OSError as e:
                log('%s %d: %s' % (label, pid, e))

    def step(self):
        """Returns True when closing finished (the game is gone, or we gave up) on this call."""
        if not self.busy:
            return False
        now = self.clock()
        self._reap()
        if now < self.check_at and now < self.deadline:
            return False
        self.check_at = now + self.every
        found = self.find()
        if not found:
            self.gave_up = set()
            log('game closed after %.1f s (%s); back to the lobby' % (now - self.t0, {'x': 'window close', 'close': 'taskkill', 'term': 'SIGTERM', 'kill': 'SIGKILL'}[self.stage]))
            return self._done()
        if self.stage == 'x':
            rc = self.xproc.poll() if self.xproc else 0
            if rc not in (None, XCLOSE_SENT) or now >= self.deadline:
                log('window close %s; the game is still up after %.1f s: taskkill' %
                    ('found no window (rc %s)' % rc if rc not in (None, XCLOSE_SENT) else 'did not end it', now - self.t0))
                if self.xproc and rc is None:
                    self._kill_wait(self.xproc)
                self._taskkill(now)
                self.check_at = now + self.every
            return False
        if now < self.deadline:
            return False
        if self.stage == 'close':
            self._reap(kill=True)
            self.stage, self.deadline = 'term', now + self.term_wait
            self._signal(found, signal.SIGTERM, 'SIGTERM')
        elif self.stage == 'term':
            self.stage, self.deadline = 'kill', now + self.kill_wait
            self._signal(found, signal.SIGKILL, 'SIGKILL')
        else:
            log('game still up %.1f s after SIGKILL (pids %s); giving up (the next combo on these pids leaves the mode)'
                % (now - self.t0, ' '.join(map(str, sorted(found)))))
            self.gave_up = set(found)
            return self._done()
        return False

    def _done(self):
        self._reap(kill=True)
        if self.xproc and self.xproc.poll() is None:
            self._kill_wait(self.xproc)
        self.xproc = None
        self.stage = None
        return True


class Pointer:
    """All the logic, no hardware: events in (dev, type, code, value), pointer events out through emit(type, code, value).
    clock and run_stop are injected so tests can drive time."""

    def __init__(self, emit, clock=time.monotonic, rt='/nonexistent', run_stop=None, btn=None, mode_every=MODE_EVERY,
                 base=150.0, accel=700.0, top=1600.0, games=None, closer=None, game_every=GAME_EVERY):
        self.emit, self.clock, self.rt, self.run_stop = emit, clock, rt, run_stop
        self.games, self.closer, self.game_every = games, closer, game_every   # #231: find() -> {pid: name}, GameCloser
        self.game = False            # a game (emulator) runs
        self.auto_off = False        # the pointer is off because a game came up (not by hand)
        self.pre_enabled = True      # #257: the toggle as it was when the game came up; it is what the game's end restores
        self.grace_until = -1e9      # #257: until then SELECT+START in the lobby does not leave Fightcade mode
        self.exit_grace = EXIT_GRACE
        self.game_at = -1e9
        self.btn = btn or codes()
        self.mode_every, self.base, self.accel, self.top = mode_every, base, accel, top
        self.active = False          # current-mode == fightcade
        self.enabled = True          # the SELECT+MK toggle
        self.ranges = {}             # dev -> {axis: (min, max)}
        self.axes = {}               # (dev, axis) -> normalised -1..1
        self.down = {}               # (dev, code) -> True
        self.swallow = False         # LK went into a combo: no click until it is released
        self.swallow_mk = False      # MK went into the pointer toggle: no right click until it is released
        self.osk = False             # #227: the on-screen keyboard is open (we think; the page follows our keys)
        self.osk_hold = False        # SELECT closed the keyboard: its LK must not reopen it until SELECT is released
        self.osk_dir = None          # (key, next repeat time) of the held stick direction
        self.osk_start = False       # START went down while open: it submits on release unless SELECT joins (the exit combo)
        self.sent = set()            # mouse buttons currently pressed on the virtual device
        self.moving_since = None
        self.last_tick = None
        self.rem = [0.0, 0.0]
        self.combo_since = None
        self.combo_done = False
        self.mode_at = -1e9
        self.on_toggle = None        # callback(enabled) for persistence/hints

    # -- devices ---------------------------------------------------------------------------------
    def add_device(self, dev, ranges):
        self.ranges[dev] = ranges

    def remove_device(self, dev):
        self.ranges.pop(dev, None)
        self.resync(dev)

    def resync(self, dev):
        """Forget what we think `dev` holds (unplugged, or SYN_DROPPED): release its clicks, stop its motion."""
        for d in (self.axes, self.down):
            for k in [k for k in d if k[0] == dev]:
                del d[k]
        self._sync_buttons()
        self._check_combo()

    # -- helpers ---------------------------------------------------------------------------------
    def held(self, name):
        return any(c == self.btn[name] for (_d, c) in self.down)

    def vector(self):
        x = y = 0.0
        for (_d, ax), v in self.axes.items():
            if ax in (ABS_X, ABS_HAT0X):
                x = x if abs(x) >= abs(v) else v
            elif ax in (ABS_Y, ABS_HAT0Y):
                y = y if abs(y) >= abs(v) else v
        return x, y

    def _norm(self, dev, axis, value):
        if axis in (ABS_HAT0X, ABS_HAT0Y):
            return float(max(-1, min(1, value)))
        lo, hi = self.ranges.get(dev, {}).get(axis, (-32768, 32767))
        mid, half = (lo + hi) / 2.0, max((hi - lo) / 2.0, 1.0)
        v = (value - mid) / half
        if abs(v) < DEADZONE:
            return 0.0
        return max(-1.0, min(1.0, v))

    def _press(self, code, on):
        if on and code not in self.sent:
            self.sent.add(code)
            self.emit(EV_KEY, code, 1)
            self.emit(EV_SYN, 0, 0)
        elif not on and code in self.sent:
            self.sent.discard(code)
            self.emit(EV_KEY, code, 0)
            self.emit(EV_SYN, 0, 0)

    def _sync_buttons(self):
        live = self.active and self.enabled and not self.osk
        self._press(BTN_LEFT, live and self.held('lk') and not self.swallow)
        self._press(BTN_RIGHT, live and self.held('mk') and not self.swallow_mk)
        if not self.held('lk'):
            self.swallow = False
        if not self.held('mk'):
            self.swallow_mk = False

    def _toggle(self):
        self.enabled = not self.enabled
        self.auto_off = False        # a hand choice wins over the automatic one
        self.swallow_mk = self.held('mk')
        self.moving_since = None
        self.rem = [0.0, 0.0]
        log('pointer %s' % ('ON' if self.enabled else 'OFF (the game has the stick)'))
        self._sync_buttons()
        if self.on_toggle:
            self.on_toggle(self.enabled)

    # -- on-screen keyboard (#227) ---------------------------------------------------------------------
    def _tap(self, code):
        self.emit(EV_KEY, code, 1)
        self.emit(EV_SYN, 0, 0)
        self.emit(EV_KEY, code, 0)
        self.emit(EV_SYN, 0, 0)

    def _osk_open(self):
        """Only in the lobby: never while a game runs (checked on demand, not from the 1 s poll)."""
        if not self.active or self.osk:
            return
        if self.game or self._find_games():   # the 1 s poll already knows a running game: no /proc scan per combo
            log('on-screen keyboard: not opened, a game runs')
            return
        self.osk = True
        self.swallow = self.held('lk')
        self.osk_dir = None
        self.moving_since, self.rem = None, [0.0, 0.0]
        log('on-screen keyboard OPEN (the stick types; SELECT or START closes)')
        self._sync_buttons()
        self._tap(KEY_OPEN)

    def _osk_close(self, key=None):
        """key: what the page gets to close itself (KEY_CANCEL, or KEY_SUBMIT to send); None = nothing is sent (the mode
        ended or a game has the focus: a key would go to the game)."""
        if not self.osk:
            return
        self.osk, self.osk_dir, self.osk_start = False, None, False
        log('on-screen keyboard CLOSED')
        if key:
            self._tap(key)
        self._sync_buttons()

    def _osk_button(self, code):
        """A button went down while the keyboard is open: its key. Returns True when it was used."""
        if code == self.btn['lk']:
            self._tap(KEY_PRESS)
        elif code == self.btn['mk']:
            self._tap(KEY_DELETE)
        elif code == self.btn['start']:
            self.osk_start = True                # acts on release: SELECT may follow (the exit combo)
        elif code == self.btn['select']:
            self.osk_hold = True
            self._osk_close(KEY_CANCEL)
        else:
            return False
        return True

    def _osk_start_up(self):
        if self.osk_start and self.osk and not self.held('select'):   # SELECT held: an exit-combo try, not a send
            found = self._find_games(strict=True)   # on demand (#262): a real Enter must never reach a game that
            if found is None:                       # came up since the last fast poll. A scan that failed: fail closed
                log('on-screen keyboard: the game scan failed; START sends no Enter, closed without a key')
                self._osk_close(None)
            elif found:                             # a game: closes the keyboard silently
                self._set_game(True, found)
            else:
                # F23 first: the page puts the focus back on its field and hides (it never blurs it). Then the real
                # Enter reaches that field exactly as a USB keyboard's Enter would, whatever the page does with it.
                self._osk_close(KEY_SUBMIT)
                self._tap(KEY_ENTER)
        self.osk_start = False

    def _osk_stick(self, now):
        """Stick -> arrow key presses: one on each change of direction, then repeats while held."""
        x, y = self.vector()
        key = None
        if max(abs(x), abs(y)) >= OSK_DIR:
            key = (KEY_RIGHT if x > 0 else KEY_LEFT) if abs(x) >= abs(y) else (KEY_DOWN if y > 0 else KEY_UP)
        if key is None:
            self.osk_dir = None
        elif self.osk_dir is None or self.osk_dir[0] != key:
            self.osk_dir = (key, now + OSK_DELAY)
            self._tap(key)
        elif now >= self.osk_dir[1]:
            self.osk_dir = (key, now + OSK_REPEAT)
            self._tap(key)

    # -- games (#231) --------------------------------------------------------------------------------
    def _find_games(self, strict=False):
        """strict (#262): None when the scan failed or could not list /proc, so a caller about to send a real key fails
        closed ("a game may run"); otherwise a failed scan means no game (the pointer must keep working)."""
        if not self.games:
            return {}
        try:
            found = self.games() or {}
            self.scan_err = None
        except Exception as e:       # a /proc surprise must never take the pointer down
            if str(e) != getattr(self, 'scan_err', None):
                log('game scan failed: %s (no game assumed; logged once until it changes)' % e)
            self.scan_err = str(e)
            return None if strict else {}
        if strict and getattr(self.games, 'ok', True) is False:
            return None
        return found

    def _set_game(self, on, found=None):
        if on == self.game:
            return
        self.game = on
        if on:
            self._osk_close(None)       # the game has the focus: no key, it would reach FBNeo (the page hides on blur)
            log('game up (%s): pointer %s' % (', '.join('%s pid %d' % (n, p) for p, n in sorted((found or {}).items())),
                                              'OFF until it ends' if self.enabled else 'stays off'))
            self.pre_enabled = self.enabled
            if self.enabled:
                self.enabled, self.auto_off = False, True
                self.moving_since, self.rem = None, [0.0, 0.0]
        else:
            # #257: the pointer comes back ON whatever happened in the game (cabinet 2026-11-19: it stayed off). SELECT+MK
            # pressed DURING the game (SELECT is the coin button, MK a game button) is not a choice; only an OFF set by hand
            # in the lobby BEFORE the game is kept.
            back = self.pre_enabled
            log('game gone: pointer %s' % ('ON' if back else 'stays off (it was off by hand before the game; SELECT+MK turns it on)'))
            self.auto_off = False
            if back != self.enabled:
                self.enabled = back
                self.moving_since, self.rem = None, [0.0, 0.0]
                if self.on_toggle:
                    self.on_toggle(back)
            self.grace_until = self.clock() + self.exit_grace
            log('SELECT+START will not leave Fightcade mode for %.0f s' % self.exit_grace)
        self._sync_buttons()

    def poll_games(self, force=False):
        """The slow pointer poll; skipped outside Fightcade mode and while the closer scans on its own."""
        now = self.clock()
        every = min(self.game_every, OSK_GAME_EVERY) if self.osk else self.game_every
        if not self.active or (self.closer and self.closer.busy) or (not force and now - self.game_at < every):
            return
        self.game_at = now
        found = self._find_games()
        self._set_game(bool(found), found)

    def fire(self):
        """SELECT+START held HOLD s: close the game if one runs (the mode stays), else leave Fightcade mode."""
        if self.closer and self.closer.busy:
            log('SELECT+START held %.0f s: the game is already being closed' % HOLD)
            return
        found = self._find_games()      # on demand, never a stale poll
        if not found and self.game:     # the game ended since the last poll (critic #257): that starts the grace now
            self._set_game(False)
        if found and self.closer and set(found) <= self.closer.gave_up:
            log('SELECT+START held %.0f s: the last close gave up on pids %s; leaving Fightcade mode instead'
                % (HOLD, ' '.join(map(str, sorted(found)))))
            found = {}
        if not found and self.clock() < self.grace_until:
            log('SELECT+START held %.0f s %.1f s after the game ended: ignored (grace %.0f s), Fightcade mode stays'
                % (HOLD, self.clock() - (self.grace_until - self.exit_grace), self.exit_grace))
            return
        if found:
            self._set_game(True, found)
            log('SELECT+START held %.0f s in a game: closing %s, Fightcade mode stays' % (HOLD, ', '.join(sorted(set(found.values())))))
            if self.closer:
                self.closer.start(found)
            return
        self.leave()

    def _check_combo(self):
        both = self.active and self.held('select') and self.held('start')
        if both:
            self.osk_start = False   # #262: START joined by SELECT is the exit combo, never a send (in either release order)
        if both and self.combo_since is None:
            self.combo_since = self.clock()
        elif not both:
            self.combo_since = None
            self.combo_done = False

    # -- input ---------------------------------------------------------------------------------
    def poll_mode(self, force=False):
        now = self.clock()
        if not force and now - self.mode_at < self.mode_every:
            return
        self.mode_at = now
        active = read_mode(self.rt) == 'fightcade'
        if active != self.active:
            self.active = active
            log('mode %s' % ('fightcade: active' if active else 'is not fightcade: inert'))
            if not active:
                self.axes.clear(); self.moving_since = None; self.rem = [0.0, 0.0]
                self.combo_since = None; self.combo_done = False
                self.osk, self.osk_dir, self.osk_hold, self.osk_start = False, None, False, False
                if self.auto_off:
                    self.enabled = True
                self.game, self.auto_off, self.game_at = False, False, -1e9
            self._sync_buttons()
            self._check_combo()

    def on_event(self, dev, etype, code, value):
        self.poll_mode()
        if not self.active:
            return
        if etype == EV_ABS and code in (ABS_X, ABS_Y, ABS_HAT0X, ABS_HAT0Y):
            self.axes[(dev, code)] = self._norm(dev, code, value)
        elif etype == EV_KEY and code in self.btn.values():
            if value:
                self.down[(dev, code)] = True
                lk_sel = (code == self.btn['lk'] and self.held('select')) or (code == self.btn['select'] and self.held('lk'))
                mk_sel = (code == self.btn['mk'] and self.held('select')) or (code == self.btn['select'] and self.held('mk'))
                if self.osk:
                    self._osk_button(code)       # LK types, MK deletes, START enters, SELECT closes
                elif lk_sel and not self.osk_hold:
                    self._osk_open()
                elif mk_sel and not self.osk_hold:
                    if self.game:        # #257: SELECT is the coin button and MK a game button: not a pointer toggle in a game
                        log('SELECT+MK ignored while a game runs (coin + kick); the pointer comes back by itself when it ends')
                        self.swallow_mk = True
                    else:
                        self._toggle()
            else:
                self.down.pop((dev, code), None)
                if code == self.btn['start']:
                    self._osk_start_up()
                if not self.held('select'):
                    self.osk_hold = False
            self._sync_buttons()
            self._check_combo()

    def tick(self):
        """Move the pointer and watch the exit combo; call about every TICK s."""
        self.poll_mode()
        if self.closer and self.closer.busy and self.closer.step():
            self.poll_games(force=True)  # closed: the pointer comes back at once, not at the next slow poll
        self.poll_games()
        now = self.clock()
        dt = 0.0 if self.last_tick is None else min(now - self.last_tick, 0.1)
        self.last_tick = now
        if self.active and self.combo_since is not None and not self.combo_done and now - self.combo_since >= HOLD:
            self.combo_done = True
            self.fire()
        if self.osk:
            self._osk_stick(now)
        x, y = self.vector()
        if not (self.active and self.enabled) or self.osk or (x == 0 and y == 0):
            self.moving_since = None
            self.rem = [0.0, 0.0]
            return
        if self.moving_since is None:
            self.moving_since = now
            dt = TICK                # the first step is immediate: a tap moves a few pixels
        speed = min(self.top, self.base + self.accel * (now - self.moving_since))
        out = []
        for i, v in enumerate((x, y)):
            self.rem[i] += v * speed * dt
            step = int(self.rem[i])
            self.rem[i] -= step
            out.append(step)
        if out[0]:
            self.emit(EV_REL, REL_X, out[0])
        if out[1]:
            self.emit(EV_REL, REL_Y, out[1])
        if out[0] or out[1]:
            self.emit(EV_SYN, 0, 0)

    def leave(self):
        log('SELECT+START held %.0f s in the lobby (no game runs): leaving Fightcade mode' % HOLD)
        self._osk_close(None)        # #262: the mode ends; no key, and nothing left to send on START's release
        try:
            write_next_mode(self.rt, 'menu')
        except OSError as e:
            log('cannot write next-mode: %s (still calling the stop hook)' % e)
        if self.run_stop:
            self.run_stop()

    def next_wake(self):
        if self.active and (self.moving_since is not None or self.combo_since is not None or self.osk):
            return TICK
        return CLOSE_EVERY if self.closer and self.closer.busy else 0.25


# ---- hardware ------------------------------------------------------------------------------------
class Uinput:
    def __init__(self, path='/dev/uinput', name=NAME, ioctl=fcntl.ioctl):
        self.path, self.name, self.ioctl = path, name, ioctl
        self.fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        try:
            self.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
            self.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
            self.ioctl(self.fd, UI_SET_EVBIT, EV_SYN)
            for c in (BTN_LEFT, BTN_RIGHT) + OSK_KEYS + (KEY_ENTER,):
                self.ioctl(self.fd, UI_SET_KEYBIT, c)
            for c in (REL_X, REL_Y):
                self.ioctl(self.fd, UI_SET_RELBIT, c)
            # struct uinput_setup: input_id (bustype, vendor, product, version) + char name[80] + ff_effects_max
            self.ioctl(self.fd, UI_DEV_SETUP, struct.pack('HHHH80sI', 0x06, 0x1209, 0x0211, 1, name.encode(), 0))
            self.ioctl(self.fd, UI_DEV_CREATE)
        except Exception:
            os.close(self.fd)
            raise

    def emit(self, etype, code, value):
        os.write(self.fd, struct.pack(FMT, 0, 0, etype, code, value))

    def close(self):
        try:
            self.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self.fd)


def _bits(path):
    """Set bit numbers of a /sys/class/input/eventN/device/capabilities/* file (hex words, most significant first)."""
    try:
        with open(path) as fh:
            words = fh.read().split()
    except OSError:
        return set()
    width = struct.calcsize('L') * 8
    out = set()
    for i, w in enumerate(reversed(words)):
        try:
            n = int(w, 16)
        except ValueError:
            return set()
        out.update(i * width + b for b in range(width) if n >> b & 1)
    return out


def scan_devices(sysfs='/sys/class/input', devdir='/dev/input'):
    """Event nodes that look like a stick: X/Y or hat axes plus gamepad/joystick buttons (288..319). Never our own device."""
    found = []
    try:
        names = sorted(os.listdir(sysfs))
    except OSError:
        return found
    for n in names:
        if not n.startswith('event'):
            continue
        d = os.path.join(sysfs, n, 'device')
        try:
            with open(os.path.join(d, 'name')) as fh:
                if fh.read().strip() == NAME:
                    continue
        except OSError:
            continue
        absb, keyb = _bits(os.path.join(d, 'capabilities', 'abs')), _bits(os.path.join(d, 'capabilities', 'key'))
        if ((ABS_X in absb and ABS_Y in absb) or (ABS_HAT0X in absb and ABS_HAT0Y in absb)) and any(288 <= b <= 319 for b in keyb):
            found.append(os.path.join(devdir, n))
    return found


def abs_ranges(fd):
    out = {}
    for ax in (ABS_X, ABS_Y):
        try:
            buf = fcntl.ioctl(fd, EVIOCGABS + ax, b'\0' * 24)
            _v, lo, hi = struct.unpack('iii', buf[:12])
            if hi > lo:
                out[ax] = (lo, hi)
        except OSError:
            pass
    return out


class Reader:
    """Open sticks by path; a read error or EOF (unplugged) drops the stick, the next scan finds it again."""

    def __init__(self, ptr, devices=None, scan=scan_devices, ranges=abs_ranges, clock=time.monotonic,
                 opener=os.open):
        self.ptr, self.fixed, self.scan, self.ranges, self.clock, self.opener = ptr, devices, scan, ranges, clock, opener
        self.fds = {}                # fd -> path
        self.scan_at = -1e9

    def rescan(self, force=False):
        now = self.clock()
        if not force and now - self.scan_at < RESCAN:
            return
        self.scan_at = now
        for p in self.fixed or self.scan():
            if p in self.fds.values():
                continue
            try:
                fd = self.opener(p, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as e:
                log('cannot open %s: %s' % (p, e))
                continue
            self.fds[fd] = p
            self.ptr.add_device(p, self.ranges(fd))
            log('stick: %s' % p)

    def drop(self, fd, why):
        p = self.fds.pop(fd, None)
        try:
            os.close(fd)
        except OSError:
            pass
        if p:
            log('stick gone (%s): %s; waiting for it' % (why, p))
            self.ptr.remove_device(p)
            self.scan_at = self.clock()

    def pump(self, timeout):
        self.rescan()
        if not self.fds:
            time.sleep(min(timeout, 0.25))
            return
        ready, _, _ = select.select(list(self.fds), [], [], timeout)
        for fd in ready:
            try:
                data = os.read(fd, SZ * 64)
            except BlockingIOError:
                continue
            except OSError as e:
                self.drop(fd, errno.errorcode.get(e.errno, str(e.errno)))
                continue
            if not data:
                self.drop(fd, 'EOF')
                continue
            for off in range(0, len(data) - SZ + 1, SZ):
                _s, _u, t, c, v = struct.unpack(FMT, data[off:off + SZ])
                if t == EV_SYN and c == SYN_DROPPED:
                    self.ptr.resync(self.fds.get(fd, '?'))
                    continue
                self.ptr.on_event(self.fds.get(fd, '?'), t, c, v)


def toggle_file(rt):
    return os.path.join(rt, 'stickmouse-off')


def main(env=None):
    env = os.environ if env is None else env
    rt = runtime_dir(env)
    try:
        ui = Uinput(env.get('STICKMOUSE_UINPUT') or '/dev/uinput')
    except OSError as e:
        log('cannot use %s: %s. The stick pointer needs /dev/uinput (udev rule 61-coin-dojo-stickmouse.rules, user in '
            'group input, uinput module loaded); not retrying.' % (env.get('STICKMOUSE_UINPUT') or '/dev/uinput', e))
        return EXIT_UINPUT
    stop_cmd = shlex.split(env.get('STICKMOUSE_STOP_CMD') or '/usr/local/bin/coin-dojo-session-stop')

    def run_stop():
        try:
            subprocess.Popen(stop_argv(stop_cmd), stdin=subprocess.DEVNULL)
        except OSError as e:
            log('stop hook %s failed: %s' % (stop_cmd, e))

    # #231: what a game is and how to close it, from the fightcade role's own config (one source for the paths).
    conf = fc_conf(env.get('STICKMOUSE_FC_CONF') or FC_CONF)
    names, uid = game_names(env), os.getuid()
    tree = conf.get('HK_FC_TREE', '')
    scope = (conf['HK_FC_UNIT'] + '.scope') if conf.get('HK_FC_UNIT') else FC_SCOPE
    wine = os.path.join(conf['HK_FC_BINDIR'], 'wine') if conf.get('HK_FC_BINDIR') else FC_WINE

    def spawn(argv, pid):
        return subprocess.Popen(argv, env=dict(env, **game_env('/proc', pid)), stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def xspawn(pids, pid):       # #257: the window close runs in its own short process (an X server that hangs cannot stall us)
        return subprocess.Popen([sys.executable, os.path.abspath(__file__), '--xclose'] + [str(p) for p in pids],
                                env=dict(env, **game_env('/proc', pid)), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)

    closer = GameCloser(GameScan(scope=scope, uid=uid, names=names, tree=tree),
                        lambda pids: game_tree(pids, uid=uid), spawn=spawn, wine=wine, xspawn=xspawn,
                        x_grace=_num('STICKMOUSE_X_GRACE', X_GRACE),
                        grace=_num('STICKMOUSE_CLOSE_GRACE', CLOSE_GRACE), term_wait=_num('STICKMOUSE_TERM_WAIT', TERM_WAIT))
    ptr = Pointer(ui.emit, rt=rt, run_stop=run_stop,
                  base=_num('STICKMOUSE_BASE', 150.0), accel=_num('STICKMOUSE_ACCEL', 700.0), top=_num('STICKMOUSE_MAX', 1600.0),
                  games=closer.find, closer=closer)
    log('games: %s (tree %s, scope %s, close via %s)' % (','.join(names), tree or 'any', scope, wine))
    # The toggle survives a crash restart (the file lives in the runtime dir, so not a reboot) and is dropped on a clean stop.
    ptr.enabled = not os.path.exists(toggle_file(rt))

    def persist(on):
        try:
            os.makedirs(rt, exist_ok=True)
            if on:
                os.unlink(toggle_file(rt))
            else:
                open(toggle_file(rt), 'w').close()
        except OSError:
            pass
    ptr.on_toggle = persist
    ptr.poll_mode(force=True)
    devs = [p for p in (env.get('STICKMOUSE_DEVICES') or '').split(',') if p] or None
    rd = Reader(ptr, devices=devs)
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(1))
    log('up (mode=%s, pointer %s)' % ('fightcade' if ptr.active else 'not fightcade', 'on' if ptr.enabled else 'off'))
    try:
        while not stopping:
            rd.pump(ptr.next_wake())
            ptr.tick()
    except KeyboardInterrupt:
        pass
    finally:
        ptr.active = False
        ptr._sync_buttons()
        ui.close()
    try:
        os.unlink(toggle_file(rt))   # a clean stop (the session ended Fightcade mode) starts the next one with the pointer ON
    except OSError:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(xclose_main(sys.argv[2:]) if sys.argv[1:2] == ['--xclose'] else main())
