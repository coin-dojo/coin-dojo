#!/usr/bin/env python3
"""Behaviour test for flycast-exit-combo.py (#88): Select+Start on the panel quits flycast.

Runs the REAL watcher against fake joystick devices (FIFOs fed with Linux js_event records,
struct js_event = u32 time, s16 value, u8 type, u8 number) and a stand-in "flycast" (sleep).
The press timings are the ones recorded on the cabinet on 2026-11-14: the maintainer taps (~0.3 s),
Select = js button 8, Start = js button 9.

    python3 ansible/roles/dreamcast/tests/test_exit_combo.py

Broken cases (must be caught): Start alone or Select alone does not quit; the old 0.6 s hold
ignores the maintainer's 0.3 s tap; a watcher on the wrong button numbers ignores the combo.
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHER = os.path.join(os.path.dirname(HERE), "files", "flycast-exit-combo.py")
RUN_SH = os.path.join(os.path.dirname(HERE), "templates", "flycast-run.sh.j2")
EV = struct.Struct("IhBB")
BUTTON, AXIS = 0x01, 0x02
SELECT, START = 8, 9
MP = 0
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "tests", "lib"))
from hktest import check as _check, finish  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)


def _hold_from_run_sh():
    with open(RUN_SH, encoding="utf-8") as fh:
        for line in fh:
            if "flycast-exit-combo.py" in line and '"$FPID"' in line:
                return line.split('"$FPID"', 1)[1].split()[0]
    raise SystemExit("flycast-run.sh: no exit watcher line")


def play(script, hold, pads, steps, settle=0.4, menu_log=None):
    """Run the watcher; `steps` = [(delay_s, pad_index, type, number, value)].
    Returns True when the stand-in flycast was SIGTERMed (combo fired)."""
    tmp = tempfile.mkdtemp(prefix="exitcombo-")
    try:
        fifos = [os.path.join(tmp, "js%d" % i) for i in range(pads)]
        for f in fifos:
            os.mkfifo(f)
        fly = subprocess.Popen(["sleep", "30"])
        env = dict(os.environ, FLYCAST_COMBO_MENU_LOG=menu_log or os.path.join(tmp, "menu.log"))
        watch = subprocess.Popen([sys.executable, "-I", script, str(fly.pid), hold] + fifos, env=env)
        # O_WRONLY open blocks until the watcher has opened the read end: that is our sync point.
        writers = [os.open(f, os.O_WRONLY) for f in fifos]
        t0 = time.monotonic()
        for delay, pad, typ, num, value in steps:
            time.sleep(delay)
            ms = int((time.monotonic() - t0) * 1000) & 0xFFFFFFFF
            try:
                os.write(writers[pad], EV.pack(ms, value, typ, num))
            except BrokenPipeError:  # the watcher already fired and left
                break
        time.sleep(settle)
        fired = fly.poll() is not None and fly.returncode == -15
        if fly.poll() is None:
            fly.terminate()
            fly.wait()
        try:
            watch.wait(timeout=2)  # the watcher must leave once flycast is gone
            watcher_left = True
        except subprocess.TimeoutExpired:
            watch.kill()
            watch.wait()
            watcher_left = False
        for w in writers:
            os.close(w)
        _check(watcher_left, "  watcher exits by itself once flycast is gone")
        return fired
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def tap(pad_sel=0, pad_start=0, press=0.3):
    """the maintainer's recorded tap: Select, Start 30 ms later, both released after `press` s."""
    return [(0.05, pad_sel, BUTTON, SELECT, 1), (0.03, pad_start, BUTTON, START, 1),
            (press, pad_start, BUTTON, START, 0), (0.01, pad_sel, BUTTON, SELECT, 0)]


def hold_one(num, secs=0.6):
    return [(0.05, 0, BUTTON, num, 1), (secs, 0, BUTTON, num, 0)]


def main():
    hold = _hold_from_run_sh()
    print("hold from flycast-run.sh: %s s" % hold)
    _check(play(WATCHER, hold, 1, tap(press=0.3)), "Select+Start tap (0.3 s, recorded) quits flycast")
    _check(play(WATCHER, hold, 1, tap(press=0.12)), "Select+Start quick tap (0.12 s) quits flycast")
    _check(play(WATCHER, hold, 2, tap(1, 1)), "Select+Start on the second pad (js1) quits flycast")
    start_first = [(0.05, 0, BUTTON, START, 1), (0.05, 0, BUTTON, SELECT, 1),
                   (0.3, 0, BUTTON, SELECT, 0), (0.0, 0, BUTTON, START, 0)]
    _check(play(WATCHER, hold, 1, start_first), "Start then Select (either order) quits flycast")
    # broken cases: these must NOT quit
    _check(not play(WATCHER, hold, 1, hold_one(START)), "BROKEN control: Start alone (held 0.6 s) does not quit")
    _check(not play(WATCHER, hold, 1, hold_one(SELECT)), "BROKEN control: Select alone (held 0.6 s) does not quit")
    _check(not play(WATCHER, hold, 2, tap(0, 1)), "BROKEN control: Select on pad 1 + Start on pad 2 does not quit")
    axes = [(0.05, 0, AXIS, SELECT, 32767), (0.03, 0, AXIS, START, 32767), (0.3, 0, AXIS, START, 0)]
    _check(not play(WATCHER, hold, 1, axes), "BROKEN control: axis events numbered 8/9 do not quit")
    _check(not play(WATCHER, "0.6", 1, tap(press=0.3)),
           "BROKEN control: the old 0.6 s hold ignores the maintainer's 0.3 s tap (the #88 bug)")
    tmp = tempfile.mkdtemp(prefix="exitcombo-bad-")
    try:
        bad = os.path.join(tmp, "flycast-exit-combo.py")
        with open(WATCHER, encoding="utf-8") as fh:
            src = fh.read()
        _check("SELECT, START = 8, 9" in src, "watcher listens on Select=8, Start=9")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write(src.replace("SELECT, START = 8, 9", "SELECT, START = 6, 7"))
        _check(not play(bad, hold, 1, tap()), "BROKEN control: watcher on wrong buttons (6/7) misses the combo")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    menu_tests(hold)
    uinput_rule_tests()
    return finish()


def menus(steps, hold, pads=1):
    """Run the watcher with the menu test hook; return (quit fired, number of menu presses)."""
    d = tempfile.mkdtemp(prefix="menucombo-")
    try:
        log = os.path.join(d, "menu.log")
        fired = play(WATCHER, hold, pads, steps, menu_log=log)
        n = open(log).read().count("menu") if os.path.exists(log) else 0
        return fired, n
    finally:
        shutil.rmtree(d, ignore_errors=True)


def uinput_load_problems(defaults, tasks):
    """#173: uinput is a module; without loading it udev has no device and the rule never applies."""
    p = []
    if "dreamcast_uinput_modules_file: /etc/modules-load.d/" not in defaults:
        p.append("modules file is not under /etc/modules-load.d")
    if "{{ dreamcast_uinput_modules_file }}" not in tasks or "\n      uinput\n" not in tasks:
        p.append("no task writes 'uinput' into the modules file")
    if "modprobe uinput && udevadm control --reload-rules && udevadm trigger" not in tasks:
        p.append("the module is not loaded (and rules reloaded) before the trigger")
    return p


def uinput_rule_tests():
    """#101: the role grants the input group /dev/uinput with a udev rule and applies it; nothing wider."""
    role = os.path.dirname(HERE)
    d = open(os.path.join(role, "defaults", "main.yml"), encoding="utf-8").read()
    t = open(os.path.join(role, "tasks", "main.yml"), encoding="utf-8").read()
    rule = """dreamcast_uinput_rule: 'KERNEL=="uinput", SUBSYSTEM=="misc", GROUP="input", MODE="0660"'"""
    _check(rule in d, "the udev rule gives only the input group rw on uinput (0660)")
    _check("{{ dreamcast_uinput_rule }}" in t and "udevadm trigger --action=change --sysname-match=uinput" in t,
           "the role installs the rule and applies it without a reboot")
    _check(uinput_load_problems(d, t) == [], "#173: the role loads uinput at boot and now %s" % uinput_load_problems(d, t))
    _check(uinput_load_problems(d, t.replace("modprobe uinput && ", "")) != [], "BROKEN case: a rule without loading the module is flagged")
    _check(uinput_load_problems(d.replace("/etc/modules-load.d/coin-dojo-uinput.conf", "/etc/coin-dojo-uinput.conf"), t) != [],
           "BROKEN case: a modules file outside modules-load.d is flagged")
    for bad in ('MODE="0666"', 'GROUP="users"'):
        _check(bad not in d, "BROKEN control absent: %s" % bad)


def menu_tests(hold):
    """#101: Start+MP types flycast's menu key once per press (open, again = close); it never quits."""
    press = [(0.05, 0, BUTTON, START, 1), (0.03, 0, BUTTON, MP, 1), (0.25, 0, BUTTON, MP, 0), (0.01, 0, BUTTON, START, 0)]
    fired, n = menus(press, hold)
    _check(n == 1 and not fired, "Start+MP: one menu key, flycast keeps running (menu=%d, quit=%s)" % (n, fired))
    fired, n = menus(press + press, hold)
    _check(n == 2 and not fired, "Start+MP twice: two menu keys (open, close) (menu=%d)" % n)
    held = [(0.05, 0, BUTTON, START, 1), (0.03, 0, BUTTON, MP, 1), (0.1, 0, BUTTON, 3, 1), (0.1, 0, BUTTON, 3, 0),
            (0.6, 0, BUTTON, MP, 0), (0.01, 0, BUTTON, START, 0)]
    _check(menus(held, hold)[1] == 1, "Start+MP held (other buttons pressed meanwhile): still one menu key")
    _check(menus(hold_one(MP), hold) == (False, 0), "BROKEN control: MP alone types no menu key")
    _check(menus(hold_one(START), hold) == (False, 0), "BROKEN control: Start alone types no menu key")
    split = [(0.05, 0, BUTTON, START, 1), (0.03, 1, BUTTON, MP, 1), (0.25, 1, BUTTON, MP, 0), (0.01, 0, BUTTON, START, 0)]
    _check(menus(split, hold, pads=2)[1] == 0, "BROKEN control: Start on pad 1 + MP on pad 2 types no menu key")
    init = [(0.05, 0, BUTTON | 0x80, START, 1), (0.0, 0, BUTTON | 0x80, MP, 1)]
    _check(menus(init, hold)[1] == 0, "BROKEN control: the driver's initial-state burst (both already down) types no key")
    fired, n = menus(tap(press=0.3), hold)
    _check(fired and n == 0, "Select+Start still quits and types no menu key")


if __name__ == "__main__":
    sys.exit(main())
