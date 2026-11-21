#!/usr/bin/env python3
"""Pad hotkeys for standalone flycast, like RetroArch's on the other systems.

usage: flycast-exit-combo.py <pid> [hold_seconds] [device ...]
- Select (js button 8) + Start (9) held together on one pad for hold_seconds (default 0.1, a tap like RetroArch;
  #88): SIGTERM goes to <pid> (quit the game).
- Start (9) + MP (0) pressed together on one pad (#101): flycast's menu key (Tab) is typed once per press through a
  uinput virtual keyboard; the same combo again closes the menu. Without /dev/uinput access (the dreamcast role's
  udev rule gives the input group rw) only the menu combo is lost; quitting still works.
Devices default to /dev/input/js*. Exits by itself when <pid> is gone.
Test hook: FLYCAST_COMBO_MENU_LOG=<file> appends "menu" per menu press instead of using uinput.
"""
import fcntl, glob, os, select, signal, struct, sys, time

SELECT, START = 8, 9
MP = 0
EV = struct.Struct("IhBB")  # time, value, type, number

# uinput (linux/uinput.h, input-event-codes.h)
UI_SET_EVBIT, UI_SET_KEYBIT, UI_DEV_CREATE, UI_DEV_DESTROY = 0x40045564, 0x40045565, 0x5501, 0x5502
EV_SYN, EV_KEY, SYN_REPORT, KEY_TAB = 0, 1, 0, 15
INPUT_EVENT = struct.Struct("llHHi")   # struct input_event: timeval (2 longs), type, code, value


class MenuKey:
    """Types flycast's menu key. uinput keyboard, or a log line under the test hook; never raises."""

    def __init__(self):
        self.log = os.environ.get("FLYCAST_COMBO_MENU_LOG")
        self.fd = None
        if self.log:
            return
        try:
            fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(fd, UI_SET_KEYBIT, KEY_TAB)
            # struct uinput_user_dev: name[80], id {bustype, vendor, product, version}, ff_effects_max, abs* [4][64]
            dev = b"coin-dojo-flycast-menu".ljust(80, b"\0") + struct.pack("HHHHi", 0x06, 0x1, 0x1, 1, 0) + b"\0" * (4 * 64 * 4)
            os.write(fd, dev)
            fcntl.ioctl(fd, UI_DEV_CREATE)
            self.fd = fd
        except OSError as e:
            sys.stderr.write("flycast-exit-combo: no menu key (/dev/uinput: %s)\n" % e.strerror)

    def press(self):
        if self.log:
            with open(self.log, "a") as fh:
                fh.write("menu\n")
            return
        if self.fd is None:
            return
        try:
            for code_type, code, value in ((EV_KEY, KEY_TAB, 1), (EV_SYN, SYN_REPORT, 0), (EV_KEY, KEY_TAB, 0), (EV_SYN, SYN_REPORT, 0)):
                os.write(self.fd, INPUT_EVENT.pack(0, 0, code_type, code, value))
        except OSError:
            pass

    def close(self):
        if self.fd is not None:
            try:
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
                os.close(self.fd)
            except OSError:
                pass


def main():
    pid = int(sys.argv[1])
    hold = float(sys.argv[2]) if len(sys.argv) > 2 else 0.1
    paths = sys.argv[3:] or sorted(glob.glob("/dev/input/js*"))
    fds = {}
    for p in paths:
        try:
            fds[os.open(p, os.O_RDONLY | os.O_NONBLOCK)] = p
        except OSError:
            pass
    menu = MenuKey()
    down = {fd: set() for fd in fds}
    since = {}
    menu_armed = {fd: True for fd in fds}   # one menu key per START+MP press; re-armed when either is released
    try:
        while True:
            try:
                os.kill(pid, 0)
            except OSError:
                return 0
            timeout = 0.2
            if since:
                timeout = max(0.02, min(0.2, hold - (time.monotonic() - min(since.values()))))
            r = select.select(list(fds), [], [], timeout)[0] if fds else ([], time.sleep(0.2))[0:0]
            for fd in r:
                try:
                    data = os.read(fd, EV.size * 32)
                except OSError:
                    continue
                for off in range(0, len(data) - EV.size + 1, EV.size):
                    _, value, typ, num = EV.unpack_from(data, off)
                    if typ & 0x03 != 1:  # buttons only (init flag 0x80 allowed)
                        continue
                    (down[fd].add if value else down[fd].discard)(num)
                    if {SELECT, START} <= down[fd]:
                        since.setdefault(fd, time.monotonic())
                    else:
                        since.pop(fd, None)
                    if {START, MP} <= down[fd] and SELECT not in down[fd]:
                        if menu_armed[fd] and not (typ & 0x80):   # never on the driver's initial-state burst
                            menu.press()
                            menu_armed[fd] = False
                    else:
                        menu_armed[fd] = True
            now = time.monotonic()
            if any(now - t >= hold for t in since.values()):
                os.kill(pid, signal.SIGTERM)
                return 0
    finally:
        menu.close()


if __name__ == "__main__":
    sys.exit(main())
