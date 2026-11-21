#!/usr/bin/env python3
"""Live test of the native power overlay (#66) against a REAL Wayland compositor (cage).

Skips (exit 0) when no compositor socket is there, so a plain `python3 <file>` on the laptop is safe.
Run it locally under a headless cage in a Debian trixie container (same cage/cairo stack as the cabinet):

    docker run --rm -v "$PWD/ansible/roles/power:/role:ro" \
      -v "$PWD/themes/coin-dojo/_inc/fonts:/fonts:ro" debian:trixie sh -c '
      apt-get update -qq && apt-get install -y -qq --no-install-recommends cage grim python3 libcairo2 \
        libfreetype6 >/dev/null && export XDG_RUNTIME_DIR=/tmp/x WLR_BACKENDS=headless WLR_RENDERER=pixman \
        WLR_LIBINPUT_NO_DEVICES=1 POWERD_FONT_DIR=/fonts && mkdir -m700 /tmp/x && \
        (cage -- sleep 600 &) && sleep 1 && WAYLAND_DISPLAY=wayland-0 python3 /role/tests/test_overlay_live.py'

Never run it on the cabinet's live session: it maps a fullscreen window on top of ES-DE.
Checks: prime; press -> prompt mapped fast; countdown repaints; hide unmaps (screen back to what was
under it); re-show works 3x; confirm -> 'shutting down' frame presented before poweroff, through the
real powerd state machine (poweroff stubbed). Broken case: a dead compositor never delays poweroff.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "tests", "lib"))
from hktest import check as _check, finish, skip  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)
SHOW_BUDGET_MS = 100.0      # press -> prompt committed (ticket target: visible < ~300 ms)


def _load(name):
    sys.path.insert(0, os.path.join(ROLE, "files"))
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROLE, "files", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _center_pixel():
    """RGB of the screen centre via grim (PPM), or None when grim is missing."""
    if not shutil.which("grim"):
        return None
    out = subprocess.run(["grim", "-t", "ppm", "-"], capture_output=True, check=True).stdout
    # P6\n<w> <h>\n255\n<data>
    parts = out.split(b"\n", 3)
    w, h = map(int, parts[1].split())
    data = parts[3]
    i = ((h // 2) * w + (w // 2)) * 3
    return tuple(data[i:i + 3])


def main():
    sock = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/nonexistent"), os.environ.get("WAYLAND_DISPLAY", "wayland-0"))
    if not os.path.exists(sock):
        skip("no Wayland compositor at %s (see the docstring to run it under a headless cage)" % sock)
        return finish()
    ov_mod = _load("overlay")
    pd = _load("powerd")
    logs = []
    ov = ov_mod.Overlay(log=logs.append)

    t = time.monotonic()
    _check(ov.prime(), "overlay primes against the compositor (%.0f ms) %s" % ((time.monotonic() - t) * 1000, logs))
    base = _center_pixel()

    for rnd in (1, 2, 3):
        t = time.monotonic()
        ok = ov.show_prompt(8)
        ms = (time.monotonic() - t) * 1000
        _check(ok and ms < SHOW_BUDGET_MS, "round %d: prompt mapped in %.1f ms (budget %d)" % (rnd, ms, SHOW_BUDGET_MS))
        time.sleep(0.2)
        shown = _center_pixel()
        if shown is not None:
            _check(shown != base, "round %d: the prompt is on screen (centre %s vs %s)" % (rnd, shown, base))
        if rnd == 1 and shown is not None:
            time.sleep(1.1)
            _check(ov.shown == "prompt" and ov.conn.alive, "countdown tick kept the prompt up")
        ov.hide()
        time.sleep(0.2)
        hidden = _center_pixel()
        if hidden is not None:
            _check(hidden == base, "round %d: hide unmaps, the screen below is back (%s)" % (rnd, hidden))

    # through the real powerd state machine, only poweroff stubbed
    pd.OVERLAY = ov
    events = []
    st = pd.PowerState(power_off=lambda: events.append(("poweroff", ov.shown)))
    t = time.monotonic()
    st.press()
    ms = (time.monotonic() - t) * 1000
    _check(st.cog == pd.NATIVE and ms < SHOW_BUDGET_MS, "powerd press -> native prompt in %.1f ms" % ms)
    time.sleep(0.5)
    t = time.monotonic()
    st.press()
    ms = (time.monotonic() - t) * 1000
    _check(events == [("poweroff", "off")], "confirm: 'shutting down' frame is up when poweroff runs (%s)" % events)
    _check(ms < 400, "confirm -> poweroff call in %.1f ms (frame wait is bounded)" % ms)
    off = _center_pixel()
    if off is not None:
        _check(off != base, "the 'safe to switch off at NO SIGNAL' frame stays on screen (not hidden)")
    _check(ov.conn.alive and ov.conn.error is None, "no protocol error from the compositor (%s)" % ov.conn.error)
    ov.hide()

    # broken case: the compositor is gone -> the double press still powers off at once
    with tempfile.TemporaryDirectory() as d:
        pd.OVERLAY = ov_mod.Overlay(display="wayland-dead", runtime_dir=d)
        pd.show_cog = lambda: None
        events = []
        st = pd.PowerState(power_off=lambda: events.append("poweroff"))
        t = time.monotonic()
        st.press()
        time.sleep(0.4)
        st.press()
        ms = (time.monotonic() - t) * 1000 - 400
        _check(events == ["poweroff"] and ms < 100,
               "BROKEN: no compositor -> double press still powers off, no delay (%.1f ms)" % ms)

    return finish()


if __name__ == "__main__":
    sys.exit(main())
