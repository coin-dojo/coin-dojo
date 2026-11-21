#!/usr/bin/env python3
"""Power-button confirm daemon for the coin-dojo cabinet.
A single press shows a themed 'Shut down?' overlay; a second press within the
window shows 'SHUTTING DOWN - safe to switch off when the screen says NO SIGNAL'
and powers off; otherwise it cancels. logind must have HandlePowerKey=ignore so
the raw key no longer powers off on its own.

Overlay (#66): a resident native Wayland client (overlay.py) primed at start, so a
press maps a pre-rendered frame in one compositor round trip. If it is not
available (cage down, no cairo), the old cog page is the fallback. Whatever the
overlay does, a second press always powers off.

The state machine (PowerState) takes its clock, timer, overlay and poweroff as
injected callables so it can be unit-tested without hardware (see tests/)."""
import struct, subprocess, sys, threading, time, os

try:
    import overlay as _overlay_mod      # files/overlay.py, shipped next to this file
except Exception as _e:                 # never let the overlay break the power button
    _overlay_mod = None
    sys.stderr.write('powerd: native overlay unavailable: %r\n' % (_e,))

DEV       = '/dev/input/by-path/platform-pwr_button-event'   # stable; event0 can move when USB pads enumerate first
DEV_NAME  = 'pwr_button'
KEY_POWER = 116
EV_KEY    = 1
FMT       = 'llHHi'
SZ        = struct.calcsize(FMT)
WINDOW    = float(os.environ.get('POWERD_WINDOW') or 8)   # seconds to confirm (hk_power_prompt; shutdown.html counts the same)
DEBOUNCE  = 0.30                   # ignore presses closer together than this (seconds)
PAGE      = 'file:///data/launcher/shutdown.html?s=%d' % int(WINDOW)   # the page counts down from ?s=
FINAL_WAIT = 0.3                   # max s to wait for the 'shutting down' frame before poweroff
PRIME_EVERY = 2.0                  # s between re-prime attempts while cage is down/restarting
ENV       = dict(os.environ)
ENV.setdefault('XDG_RUNTIME_DIR', '/run/user/%d' % os.getuid())
ENV.setdefault('WAYLAND_DISPLAY', 'wayland-0')


def find_device(path=None, sysfs='/sys/class/input', devdir='/dev/input'):
    """POWERD_DEVICE env > DEV when it exists > scan sysfs for the pwr_button node. None if absent."""
    path = path or os.environ.get('POWERD_DEVICE') or DEV
    if os.path.exists(path):
        return path
    try:
        names = sorted(os.listdir(sysfs))
    except OSError:
        return None
    for n in names:
        if not n.startswith('event'):
            continue
        try:
            with open(os.path.join(sysfs, n, 'device', 'name')) as fh:
                if fh.read().strip() == DEV_NAME:
                    return os.path.join(devdir, n)
        except OSError:
            continue
    return None


def log(msg):
    sys.stdout.write('powerd: %s\n' % msg)


OVERLAY = None   # the primed native overlay (main() creates it)
NATIVE = 'native'


def keep_primed(ov, every=PRIME_EVERY, sleep=time.sleep, rounds=None):
    """Keep the overlay connected: cage starts after us and restarts with ES-DE. rounds=None: forever."""
    n = 0
    while rounds is None or n < rounds:
        n += 1
        try:
            if not ov.ready():
                ov.prime()
        except Exception as e:
            log('prime error %r' % (e,))
        sleep(every)


def show_cog():
    try:
        return subprocess.Popen(
            ['cog', '-b', '#00000000', PAGE], env=ENV,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return None


def show_overlay():
    t0 = time.monotonic()
    if OVERLAY is not None and OVERLAY.show_prompt(int(WINDOW)):
        log('prompt shown (native) in %.1f ms' % ((time.monotonic() - t0) * 1000))
        return NATIVE
    log('native overlay not ready, falling back to cog (slow)')
    return show_cog()


def hide_overlay(cog):
    if cog == NATIVE:
        if OVERLAY is not None:
            OVERLAY.hide()
        return
    try:
        if cog:
            cog.terminate()
    except Exception:
        pass
    subprocess.run(['pkill', '-x', 'cog'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def final_frame(cog):
    """'SHUTTING DOWN - safe to switch off when the screen says NO SIGNAL', bounded by FINAL_WAIT.
    A cog prompt (fallback) stays up until the session dies; never block or raise here."""
    t0 = time.monotonic()
    if OVERLAY is not None and OVERLAY.show_shutdown(wait=FINAL_WAIT):
        log('shutdown frame on screen in %.1f ms' % ((time.monotonic() - t0) * 1000))
        if cog not in (None, NATIVE):
            hide_overlay(cog)
        return True
    return False


def poweroff():
    log('poweroff')
    subprocess.run(['sudo', '-n', '/usr/bin/systemctl', 'poweroff'])


def make_timer(seconds, fn):
    tm = threading.Timer(seconds, fn)
    tm.daemon = True
    return tm


class PowerState:
    """armed/idle state machine. Every side effect is injected."""

    def __init__(self, show=show_overlay, hide=hide_overlay, power_off=poweroff,
                 timer=make_timer, clock=time.monotonic, window=WINDOW, debounce=DEBOUNCE,
                 final=final_frame):
        self.show, self.hide, self.power_off, self.final = show, hide, power_off, final
        self.timer, self.clock = timer, clock
        self.window, self.debounce = window, debounce
        self.armed = False
        self.cog = None
        self.tm = None
        self.last = 0.0

    def cancel(self):
        self.armed = False
        try:
            self.hide(self.cog)
        except Exception:
            pass
        self.cog = None

    def arm(self):
        self.armed = True
        try:
            self.cog = self.show()
        except Exception:      # no overlay at all: the double press must still work
            self.cog = None
        self.tm = self.timer(self.window, self.cancel)
        self.tm.start()

    def confirm(self):
        """Feedback first (bounded), then power off. The overlay is NOT hidden: the 'shutting down'
        frame stays until the session dies. Poweroff runs even if the overlay fails."""
        if self.tm:
            self.tm.cancel()
        self.armed = False
        try:
            self.final(self.cog)
        except Exception:
            pass
        finally:
            self.cog = None
            self.power_off()

    def on_power(self):
        if self.armed:
            self.confirm()
        else:
            self.arm()

    def press(self):
        """A KEY_POWER down event. Returns False when swallowed by the debounce."""
        now = self.clock()
        if now - self.last < self.debounce:
            return False
        self.last = now
        self.on_power()
        return True


def main():
    dev = find_device()
    if dev is None:
        sys.stderr.write('powerd: no pwr_button input device found\n')
        return 1   # systemd Restart=on-failure retries
    global OVERLAY
    if _overlay_mod is not None:
        OVERLAY = _overlay_mod.Overlay(display=ENV['WAYLAND_DISPLAY'], runtime_dir=ENV['XDG_RUNTIME_DIR'], log=log)
        threading.Thread(target=keep_primed, args=(OVERLAY,), name='overlay-primer', daemon=True).start()
    st = PowerState()
    log('listening on %s' % dev)
    with open(dev, 'rb', buffering=0) as f:
        while True:
            data = f.read(SZ)
            if not data:
                return 1   # device vanished (EOF): exit non-zero so systemd restarts us, never spin
            if len(data) < SZ:
                continue
            _s, _u, etype, code, value = struct.unpack(FMT, data)
            if etype == EV_KEY and code == KEY_POWER and value == 1:
                log('KEY_POWER down (armed=%s)' % st.armed)
                st.press()


if __name__ == '__main__':
    sys.exit(main())
