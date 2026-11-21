#!/usr/bin/env python3
"""esde_return.py (#224): after the SETTINGS app exits, put ES-DE back on the carousel, with no stale press.

Run by the ES-DE game-select hook of role `settings` (esde-settings-hook.sh.j2) right after the app, while ES-DE
still waits for the hook. Why it is needed (ES-DE 3.5.0, tag v3.5.0 `50e4b600`):
  - the hook is a BLOCKING event script: ES-DE sits in system() (es-core/src/utils/PlatformUtil.cpp:129-131) and
    pumps no events meanwhile. ES-DE's SDL reads the stick from evdev itself (HIDAPI off, es-app/src/main.cpp:638),
    so every press made inside SETTINGS waits in ES-DE's kernel evdev buffer and is handled the moment the hook
    returns (main.cpp:508-549). ES-DE's own launch drops those (ViewController.cpp/PlatformUtil.cpp:289 "Drop all
    events" + setBlockInput, ViewController.cpp:1084-1086); a script gets no such care. That is #220: the LK that
    left SETTINGS (or a quick second LK) reaches the one-entry SETTINGS list and launches it again.
  - nothing lets a script send ES-DE to the carousel (emulation's #223 research): a Back press is the only way.

What it does, in this order:
  1. make a one-key uinput keyboard (KEY_BACKSPACE, `coin-dojo-esde-back`); open the stick(s) read-write
     (/sys/class/input scan: X/Y or hat axes + gamepad buttons; never a `coin-dojo-*` device);
  2. wait until (a) cage holds the keyboard's /dev/input/eventN open (polled in /proc/<cage pid>/fd, at most SETTLE
     s; if never: log "cage never opened the keyboard" and try anyway) and (b) no button or direction is held and
     no press came for QUIET s (at most MAX_WAIT s; only EV_KEY, hat changes and analog moves past the held()
     threshold count, so analog jitter cannot keep it waiting);
  3. flush ES-DE's stale presses: write FLOOD no-op events (EV_MSC/MSC_SCAN + SYN_REPORT) into each stick's evdev
     node. Every reader's buffer overflows; the kernel keeps only the newest events and adds SYN_DROPPED; SDL then
     re-reads the real state (all released), so not one press from inside SETTINGS reaches ES-DE. (SDL ignores
     EV_MSC; cage does not open the sticks; nothing else reads them while ES-DE waits.)
  4. send exactly one Back: Backspace down + up (ES-DE's keyboard B, es-core/src/InputManager.cpp:575) and return at
     once; a forked child removes the keyboard LINGER s later (the hook does not wait for it). ES-DE handles the
     Back after the hook returns: SETTINGS list -> carousel, SETTINGS still selected.
Early keyboard (#225, `--hold`): the hook starts `esde_return.py --hold &` before the app. It makes the keyboard at
once (cage opens it while SETTINGS is in use, so step 2's cage wait is already over) and only holds it: it never
writes a key. The plain run after the app takes that keyboard over a unix socket (the open fd, SCM_RIGHTS) and does
steps 2-4 with it. Nothing there (holder not started, too slow, dead, silent past HANDOFF_WAIT, a stale socket) = the
plain run makes its own keyboard, exactly as #224: still one Back, sent only by the plain run. The holder exits once it
handed the keyboard over, or when the hook (its parent) is gone; closing its copy of the fd then removes the device.

Fail safe, per case (each logged): no /dev/uinput = nothing written at all (the old one-entry list); no stick = Back
only (keyboard-only use); a stick without MSC_SCAN or not writable = the others are flushed, that one is named in
the log; a button held past MAX_WAIT = flushed, but no Back (the held press would land on the carousel). Exit 0
always (2 = usage), the hook never waits on errors.

    esde_return.py            do it (cabinet; the hook runs it)
    esde_return.py --dry-run  scan and report, open nothing for writing, send nothing
    esde_return.py --hold     #225: make the keyboard now and hold it for the plain run (the hook, in the background)

Environment (all optional): HK_RETURN_DEVICES (comma list of event nodes, skips the scan), HK_RETURN_SYSFS,
HK_RETURN_UINPUT (default /dev/uinput), HK_RETURN_LOG (default ~/.cache/coin-dojo/settings-return.log),
HK_RETURN_QUIET / _MAX_WAIT / _SETTLE (max wait for cage) / _LINGER (s), HK_RETURN_FLOOD (event pairs),
HK_RETURN_SOCK (the hand-over socket, default $XDG_RUNTIME_DIR/coin-dojo-esde-back.sock). Pure stdlib, like stickmouse.py.
"""
import errno
import fcntl
import os
import select
import socket
import struct
import sys
import time

FMT = "llHHi"                          # struct input_event on the 64-bit Pi OS (stickmouse.py, powerd)
SZ = struct.calcsize(FMT)
EV_SYN, EV_KEY, EV_ABS, EV_MSC = 0, 1, 3, 4
SYN_REPORT, MSC_SCAN = 0, 4
KEY_BACKSPACE = 14
ABS_X, ABS_Y, ABS_HAT0X, ABS_HAT0Y = 0, 1, 16, 17
UI_SET_EVBIT, UI_SET_KEYBIT = 0x40045564, 0x40045565
UI_DEV_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY = 0x405c5503, 0x5501, 0x5502
KEY_BYTES = 96                         # (KEY_MAX 0x2ff + 1) / 8
EVIOCGKEY = (2 << 30) | (KEY_BYTES << 16) | (ord("E") << 8) | 0x18
EVIOCGABS = 0x80184540                 # + axis; struct input_absinfo = 6 ints
BUTTONS = range(0x100, 0x300)          # BTN_MISC.. (sticks report 288..297); keyboard keys below 0x100 do not count
NAME = "coin-dojo-esde-back"
OWN_PREFIX = "coin-dojo-"               # our own uinput devices (this one, stickmouse): never a stick

QUIET, MAX_WAIT, SETTLE, LINGER, FLOOD = 0.3, 5.0, 2.0, 1.5, 2048
UI_GET_SYSNAME = (2 << 30) | (64 << 16) | (ord("U") << 8) | 44   # _IOC(READ, 'U', 44, 64): "inputN"
HATS = range(ABS_HAT0X, ABS_HAT0X + 8)    # ABS_HAT0X..ABS_HAT3Y
SOCK_NAME = "coin-dojo-esde-back.sock"     # #225 hand-over socket in XDG_RUNTIME_DIR (0700, the cabinet user's)
HANDOFF_WAIT = 1.0                        # the plain run waits at most this for the held keyboard, then makes one
HOLD_POLL = 0.5                           # the holder checks its parent (the hook) this often


def _num(env, name, default, cast=float):
    try:
        return cast(env.get(name) or default)
    except ValueError:
        return default


class Log:
    def __init__(self, path):
        self.path = path

    def __call__(self, msg):
        line = "%s esde_return: %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
        sys.stderr.write(line + "\n")
        if self.path:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(self.path, "a") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass


# ---- payloads (pure) ------------------------------------------------------------------------------
def ev(etype, code, value):
    return struct.pack(FMT, 0, 0, etype, code, value)


def flood_bytes(pairs, scan=0x90001):
    """pairs x (MSC_SCAN, SYN_REPORT): changes no button or axis state anywhere, only fills buffers."""
    return (ev(EV_MSC, MSC_SCAN, scan) + ev(EV_SYN, SYN_REPORT, 0)) * pairs


BACK_EVENTS = [(EV_KEY, KEY_BACKSPACE, 1), (EV_SYN, SYN_REPORT, 0), (EV_KEY, KEY_BACKSPACE, 0), (EV_SYN, SYN_REPORT, 0)]


def any_bit(buf, bits):
    for b in bits:
        if b // 8 < len(buf) and buf[b // 8] >> (b % 8) & 1:
            return True
    return False


# ---- sysfs scan (pure, testable on a fake tree) ---------------------------------------------------
def _bits(path):
    """Set bit numbers of a capabilities file (hex words, most significant first), as in stickmouse.py."""
    try:
        with open(path) as fh:
            words = fh.read().split()
    except OSError:
        return set()
    width = struct.calcsize("L") * 8
    out = set()
    for i, w in enumerate(reversed(words)):
        try:
            n = int(w, 16)
        except ValueError:
            return set()
        out.update(i * width + b for b in range(width) if n >> b & 1)
    return out


def scan(sysfs="/sys/class/input", devdir="/dev/input"):
    """-> [(event node, can_flush, name)] for every stick: X/Y or hat axes + gamepad/joystick buttons (288..319).
    can_flush = it reports MSC_SCAN (HID sticks do), so a no-op flood is passed on to its readers."""
    found = []
    try:
        names = sorted(os.listdir(sysfs))
    except OSError:
        return found
    for n in names:
        if not n.startswith("event"):
            continue
        d = os.path.join(sysfs, n, "device")
        try:
            with open(os.path.join(d, "name")) as fh:
                name = fh.read().strip()
        except OSError:
            continue
        if name.startswith(OWN_PREFIX):
            continue
        absb, keyb = _bits(os.path.join(d, "capabilities", "abs")), _bits(os.path.join(d, "capabilities", "key"))
        if ((ABS_X in absb and ABS_Y in absb) or (ABS_HAT0X in absb and ABS_HAT0Y in absb)) \
                and any(288 <= b <= 319 for b in keyb):
            msc = _bits(os.path.join(d, "capabilities", "msc"))
            found.append((os.path.join(devdir, n), MSC_SCAN in msc, name))
    return found


# ---- hardware (thin; tests swap it for a fake) ----------------------------------------------------
def _deflected(v, lo, hi):
    """An analog axis past half deflection (the held() threshold): a real move, not jitter."""
    return hi > lo and abs(v - (lo + hi) / 2.0) > (hi - lo) / 4.0


class Hw:
    def __init__(self, ioctl=fcntl.ioctl, opener=os.open, clock=time.monotonic, sleep=time.sleep,
                 proc="/proc", sysfs="/sys/class/input", devdir="/dev/input"):
        self.ioctl, self.opener, self.clock, self.sleep = ioctl, opener, clock, sleep
        self.proc, self.sysfs, self.devdir = proc, sysfs, devdir
        self.ranges = {}                   # fd -> {ABS_X/ABS_Y: (lo, hi)}

    def _absinfo(self, fd, ax):
        return struct.unpack("iii", bytes(self.ioctl(fd, EVIOCGABS + ax, bytes(24)))[:12])

    def open_stick(self, path):
        fd = self.opener(path, os.O_RDWR | os.O_NONBLOCK)
        r = {}
        for ax in (ABS_X, ABS_Y):
            try:
                _v, lo, hi = self._absinfo(fd, ax)
                r[ax] = (lo, hi)
            except OSError:
                pass
        self.ranges[fd] = r
        return fd

    def held(self, fd):
        """A button or a direction is held right now (EVIOCGKEY + hat/axis values)."""
        buf = bytearray(KEY_BYTES)
        self.ioctl(fd, EVIOCGKEY, buf)
        if any_bit(buf, BUTTONS):
            return True
        for ax in (ABS_HAT0X, ABS_HAT0Y, ABS_X, ABS_Y):
            try:
                v, lo, hi = self._absinfo(fd, ax)
            except OSError:
                continue
            if ax in (ABS_HAT0X, ABS_HAT0Y):
                if v:
                    return True
            elif _deflected(v, lo, hi):
                return True
        return False

    def activity(self, fds, timeout):
        """Wait up to timeout s; -> True if a real input came (reads and drops our own copies). Counts EV_KEY, any hat
        change and an analog X/Y move past the held() threshold; analog jitter and other axes do not count."""
        try:
            ready, _w, _x = select.select(fds, [], [], max(0.0, timeout))
        except (OSError, ValueError):
            return False
        hit = False
        for fd in ready:
            try:
                data = os.read(fd, SZ * 64)
            except OSError:
                continue
            for off in range(0, len(data) - SZ + 1, SZ):
                _s, _u, t, c, v = struct.unpack(FMT, data[off:off + SZ])
                if t == EV_KEY or (t == EV_ABS and c in HATS):
                    hit = True
                elif t == EV_ABS and c in self.ranges.get(fd, {}) and _deflected(v, *self.ranges[fd][c]):
                    hit = True
        return hit

    def write(self, fd, data):
        view = memoryview(data)
        while view:
            try:
                n = os.write(fd, view[:SZ * 512])
            except OSError as e:
                if e.errno in (errno.EAGAIN, errno.EINTR):
                    continue
                raise
            view = view[n:]

    def keyboard(self, path):
        fd = self.opener(path, os.O_WRONLY | os.O_NONBLOCK)
        try:
            self.ioctl(fd, UI_SET_EVBIT, EV_KEY)
            self.ioctl(fd, UI_SET_EVBIT, EV_SYN)
            self.ioctl(fd, UI_SET_KEYBIT, KEY_BACKSPACE)
            # struct uinput_setup: input_id (bustype USB, vendor, product, version) + char name[80] + ff_effects_max
            self.ioctl(fd, UI_DEV_SETUP, struct.pack("HHHH80sI", 0x03, 0x1209, 0x0224, 1, NAME.encode(), 0))
            self.ioctl(fd, UI_DEV_CREATE)
        except Exception:
            os.close(fd)
            raise
        return fd

    def kbd_node(self, fd):
        """/dev/input/eventN of our uinput keyboard (UI_GET_SYSNAME -> inputM -> its eventN child), or None."""
        try:
            buf = bytearray(64)
            self.ioctl(fd, UI_GET_SYSNAME, buf)
            sysname = bytes(buf).split(b"\0", 1)[0].decode()
            for n in sorted(os.listdir(os.path.join(self.sysfs, sysname))):
                if n.startswith("event"):
                    return os.path.join(self.devdir, n)
        except (OSError, UnicodeDecodeError):
            pass
        return None

    def kbd_opened(self, node):
        """Does a `cage` process hold node open (a link in /proc/<pid>/fd)? The compositor opens a new keyboard
        through libinput/the seat; until then a key written to it would be lost."""
        if not node:
            return False
        try:
            pids = [p for p in os.listdir(self.proc) if p.isdigit()]
        except OSError:
            return False
        for pid in pids:
            try:
                with open(os.path.join(self.proc, pid, "comm")) as fh:
                    if fh.read().strip() != "cage":
                        continue
                fdd = os.path.join(self.proc, pid, "fd")
                for f in os.listdir(fdd):
                    try:
                        if os.readlink(os.path.join(fdd, f)) == node:
                            return True
                    except OSError:
                        continue
            except OSError:
                continue
        return False

    def release(self, fd, after):
        """Remove the keyboard `after` s from now in a forked child, so the hook (and ES-DE) need not wait for it.
        Our copy of the fd is closed at once; the device lives until the child destroys it."""
        try:
            pid = os.fork()
        except OSError:
            pid = -1
        if pid == 0:                                      # child: detach, wait, destroy, exit (never returns)
            try:
                os.setsid()
                time.sleep(after)
                self.ioctl(fd, UI_DEV_DESTROY)
            except Exception:  # noqa: BLE001
                pass
            os._exit(0)
        if pid < 0:                                       # no fork: destroy here after a short wait
            self.sleep(min(after, 0.3))
            try:
                self.ioctl(fd, UI_DEV_DESTROY)
            except OSError:
                pass
        self.close(fd)

    def close(self, fd):
        try:
            os.close(fd)
        except OSError:
            pass

    # #225 early keyboard: the holder's socket and the plain run's take-over
    def take(self, path, timeout=HANDOFF_WAIT):
        """The held keyboard's fd from the holder, or OSError (none there, refused, silent past timeout)."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(timeout)
            s.connect(path)
            msg, fds, _flags, _addr = socket.recv_fds(s, 16, 1)
        finally:
            s.close()
        if msg != b"kbd" or len(fds) != 1:
            for fd in fds:
                self.close(fd)
            raise OSError("the holder sent no keyboard (%r)" % (msg,))
        return fds[0]

    def listen(self, path):
        try:
            os.unlink(path)                               # a socket left by a killed holder
        except FileNotFoundError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(path)
            os.chmod(path, 0o600)
            srv.listen(1)
        except OSError:
            srv.close()
            raise
        return srv

    def accept(self, srv, timeout):
        try:
            if not select.select([srv], [], [], timeout)[0]:
                return None
            return srv.accept()[0]
        except OSError:
            return None

    def give(self, conn, fd):
        try:
            socket.send_fds(conn, [b"kbd"], [fd])
        finally:
            conn.close()

    def unlisten(self, srv, path):
        srv.close()
        try:
            os.unlink(path)
        except OSError:
            pass

    def parent(self):
        return os.getppid()


# ---- the sequence ---------------------------------------------------------------------------------
def run(hw, sticks, uinput, log, quiet=QUIET, max_wait=MAX_WAIT, settle=SETTLE, linger=LINGER, flood=FLOOD,
        early=None):
    """-> outcome: back | no-uinput | held. One Back on `back`, none otherwise; no-uinput writes nothing at all.
    early: the holder's socket (#225); its keyboard is used when it hands one over, else one is made here."""
    kbd, how = None, "new"
    if early:
        try:
            kbd, how = hw.take(early), "early"
        except (OSError, ValueError) as e:
            log("no early keyboard (%s): making one" % (e,))
    if kbd is None:
        try:
            kbd = hw.keyboard(uinput)
        except OSError as e:
            log("no uinput keyboard (%s: %s): nothing sent" % (uinput, e.strerror or e))
            return "no-uinput"
    fds = []
    try:
        for path, ok, name in sticks:
            if not ok:
                log("stick %s (%s) has no MSC_SCAN: its stale presses are not flushed" % (path, name))
                continue
            try:
                fds.append(hw.open_stick(path))
            except OSError as e:
                log("cannot open stick %s (%s) read-write (%s): its stale presses are not flushed"
                    % (path, name, e.strerror or e))
        if not sticks:
            log("no stick found: Back only")
        node = hw.kbd_node(kbd)
        start = hw.clock()
        since = start                                        # last real input
        seen, gave_up, held = False, False, False
        while True:
            now = hw.clock()
            if not seen:
                seen = hw.kbd_opened(node)
                if not seen and now - start >= settle and not gave_up:
                    gave_up = True
                    log("cage never opened the keyboard (%s) in %.1f s: trying anyway" % (node or "node unknown", settle))
            if any(hw.held(fd) for fd in fds):
                since = now
            elif now - since >= quiet and (seen or gave_up):
                break
            if now - start >= max_wait:
                held = True
                break
            if fds:
                if hw.activity(fds, 0.05):
                    since = hw.clock()
            else:
                hw.sleep(0.05)
        for fd in fds:
            hw.write(fd, flood_bytes(flood))
        if held:
            log("a button stayed held for %.1f s: flushed %d stick(s), no Back" % (max_wait, len(fds)))
            return "held"
        hw.write(kbd, b"".join(ev(*e) for e in BACK_EVENTS))
        log("flushed %d stick(s), Back sent (keyboard %s %s, cage %s)"
            % (len(fds), node, how, "ready" if seen else "not seen"))
        return "back"
    finally:
        hw.release(kbd, linger)
        for fd in fds:
            hw.close(fd)


def hold(hw, uinput, path, log, parent, poll=HOLD_POLL):
    """#225 --hold: make the keyboard now and hand it to the plain run; never write to it. -> given | orphan |
    no-uinput | no-socket. Our fd is closed on the way out: given = the device lives on in the plain run's copy;
    otherwise that removes it."""
    try:
        kbd = hw.keyboard(uinput)
    except OSError as e:
        log("hold: no uinput keyboard (%s: %s): the plain run tries itself" % (uinput, e.strerror or e))
        return "no-uinput"
    try:
        try:
            srv = hw.listen(path)
        except OSError as e:
            log("hold: no socket %s (%s): the plain run makes its own keyboard" % (path, e))
            return "no-socket"
        try:
            while True:
                if hw.parent() != parent:
                    log("hold: the hook is gone, keyboard not taken: removed")
                    return "orphan"
                conn = hw.accept(srv, poll)
                if conn is not None:
                    try:
                        hw.give(conn, kbd)
                    except OSError as e:
                        log("hold: hand-over failed (%s): the plain run makes its own keyboard" % (e,))
                        continue
                    return "given"
        finally:
            hw.unlisten(srv, path)
    finally:
        hw.close(kbd)


def sock_path(env):
    return env.get("HK_RETURN_SOCK") or os.path.join(env.get("XDG_RUNTIME_DIR") or "/tmp", SOCK_NAME)


def main(argv=None, env=None, hw=None):
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    if argv not in ([], ["--dry-run"], ["--hold"]):
        sys.stderr.write("usage: esde_return.py [--dry-run | --hold]\n")
        return 2
    log = Log(env.get("HK_RETURN_LOG") or os.path.join(env.get("HOME") or "/tmp", ".cache", "coin-dojo",
                                                        "settings-return.log"))
    if env.get("HK_RETURN_DEVICES"):
        sticks = [(p, True, p) for p in env["HK_RETURN_DEVICES"].split(",") if p]
    else:
        sticks = scan(env.get("HK_RETURN_SYSFS") or "/sys/class/input")
    uinput = env.get("HK_RETURN_UINPUT") or "/dev/uinput"
    if argv == ["--hold"]:
        try:
            hold(hw or Hw(), uinput, sock_path(env), log, os.getppid())
        except Exception as e:  # noqa: BLE001  (the plain run makes its own keyboard)
            log("hold error: %r" % (e,))
        return 0
    if argv == ["--dry-run"]:
        log("dry run: sticks %s, uinput %s %s" % (sticks or "none", uinput,
                                                  "writable" if os.access(uinput, os.W_OK) else "NOT writable"))
        return 0
    try:
        run(hw or Hw(), sticks, uinput, log, quiet=_num(env, "HK_RETURN_QUIET", QUIET),
            max_wait=_num(env, "HK_RETURN_MAX_WAIT", MAX_WAIT), settle=_num(env, "HK_RETURN_SETTLE", SETTLE),
            linger=_num(env, "HK_RETURN_LINGER", LINGER), flood=_num(env, "HK_RETURN_FLOOD", FLOOD, int),
            early=sock_path(env))
    except Exception as e:  # noqa: BLE001  (never fail the hook: ES-DE waits for it)
        log("error: %r: nothing more sent" % (e,))
    return 0


if __name__ == "__main__":
    sys.exit(main())
