#!/usr/bin/env python3
"""Tests for the `power` role (#25): powerd state machine + role structure.

Pure stdlib, pytest-compatible, or run directly:  python3 ansible/roles/power/tests/test_power.py
Rules over text are also run against deliberately broken copies (test_broken_*): those MUST be flagged.
"""
import importlib.util
import os
import re
import sys

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(ROLE)))

try:
    import yaml  # noqa: F401  (tools/config.py needs PyYAML: use the repo venv, like tools/tests/test_config.py)
except ImportError:
    _venv = os.path.expanduser("~/.venvs/coin-dojo/bin/python")
    if os.path.exists(_venv) and not os.environ.get("HK_TEST_POWER_REEXEC"):
        os.environ["HK_TEST_POWER_REEXEC"] = "1"
        os.execv(_venv, [_venv] + sys.argv)
    print("FAIL PyYAML is not installed (make config-venv); the power role reads declared variables")
    sys.exit(1)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import config as C  # noqa: E402

# The cabinet's own uid is the maintainer's personal override (personal/config/local.yml); tests pin it so the unit renders.
CLI = {"hk_cabinet_host": "arcade.test", "hk_cabinet_uid": 4242}


def _cfg(**more):
    cli = dict(CLI)
    cli.update(more)
    return C.Config("coin-dojo", ROOT, use_local=False, cli=cli)


def _hk(defaults, cfg=None):
    """Role defaults with every `{{ hk_x }}` replaced by its effective value (what Ansible sees)."""
    vals = (cfg or _cfg()).values
    return re.sub(r"\{\{\s*(hk_\w+)\s*\}\}", lambda m: str(vals[m.group(1)]), defaults)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "tests", "lib"))
from hktest import check as _check, main  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)


def _read(*parts):
    with open(os.path.join(ROLE, *parts), encoding="utf-8") as fh:
        return fh.read()


def _load_powerd():
    sys.path.insert(0, os.path.join(ROLE, "files"))   # powerd imports overlay.py from its own dir
    spec = importlib.util.spec_from_file_location("powerd", os.path.join(ROLE, "files", "powerd.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- state machine harness ---------------------------------------------------------------------
class FakeTimer:
    def __init__(self, seconds, fn):
        self.seconds, self.fn, self.started, self.cancelled = seconds, fn, False, False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):  # the clock reached `seconds`; a cancelled timer never fires
        if self.started and not self.cancelled:
            self.fn()


class Rig:
    def __init__(self):
        m = _load_powerd()
        self.now = 100.0
        self.timers, self.events = [], []

        def timer(sec, fn):
            t = FakeTimer(sec, fn)
            self.timers.append(t)
            return t

        self.st = m.PowerState(
            show=lambda: self.events.append("show") or "cog",
            hide=lambda cog: self.events.append("hide"),
            power_off=lambda: self.events.append("poweroff"),
            final=lambda cog: self.events.append("final"),
            timer=timer, clock=lambda: self.now, window=m.WINDOW, debounce=m.DEBOUNCE)
        self.m = m

    def press(self, dt=1.0):
        self.now += dt
        return self.st.press()


# ---- state machine tests -----------------------------------------------------------------------
def test_first_press_arms():
    r = Rig()
    r.press()
    _check(r.st.armed and r.events == ["show"], "first press arms and shows the overlay, no poweroff")
    _check(len(r.timers) == 1 and r.timers[0].seconds == r.m.WINDOW == 8.0 and r.timers[0].started,
           "confirm window timer is 8 s and started")


def test_second_press_powers_off():
    r = Rig()
    r.press()
    r.press(3.0)
    _check(r.events == ["show", "final", "poweroff"],
           "second press within window -> 'shutting down' frame (not hidden, #66), then poweroff once")
    _check(not r.st.armed and r.timers[0].cancelled, "armed cleared and window timer cancelled")


def test_timeout_cancels():
    r = Rig()
    r.press()
    r.timers[0].fire()
    _check(r.events == ["show", "hide"] and not r.st.armed, "timeout hides the overlay, never powers off")
    r.press(20.0)
    _check(r.events == ["show", "hide", "show"] and r.st.armed, "after a timeout the next press arms again (not poweroff)")


def test_debounce_swallows_rapid_repeats():
    r = Rig()
    _check(r.press() is True, "press accepted")
    _check(r.press(0.1) is False and r.press(0.1) is False, "repeats inside 0.30 s are swallowed")
    _check(r.events == ["show"] and r.st.armed, "bounce does not confirm a poweroff")
    _check(r.press(0.5) is True and r.events[-1] == "poweroff", "a real second press after the debounce confirms")


def test_poweroff_only_once_and_rearm_after():
    r = Rig()
    r.press(); r.press(2.0)
    r.press(2.0)
    _check(r.events.count("poweroff") == 1 and r.st.armed, "after poweroff a further press arms afresh, no second poweroff")


def test_main_loop_constants():
    m = _load_powerd()
    _check((m.DEV, m.KEY_POWER, m.EV_KEY, m.DEBOUNCE) == ("/dev/input/by-path/platform-pwr_button-event", 116, 1, 0.30),
           "default device is the by-path symlink; key code, event type and debounce unchanged")


def test_find_device():
    import tempfile
    m = _load_powerd()
    with tempfile.TemporaryDirectory() as d:
        link = os.path.join(d, "present")
        open(link, "w").close()
        _check(m.find_device(link) == link, "existing configured path is used as is")
        sysfs = os.path.join(d, "sys")
        for n, name in (("event0", "usb pad"), ("event3", "pwr_button"), ("mouse1", "pwr_button")):
            os.makedirs(os.path.join(sysfs, n, "device"))
            with open(os.path.join(sysfs, n, "device", "name"), "w") as fh:
                fh.write(name + "\n")
        got = m.find_device(os.path.join(d, "missing"), sysfs=sysfs, devdir="/dev/input")
        _check(got == "/dev/input/event3", "missing symlink -> scan sysfs for pwr_button (got %s)" % got)
        _check(m.find_device(os.path.join(d, "missing"), sysfs=os.path.join(d, "nosys")) is None,
               "no symlink and no sysfs match -> None")


# ---- #66: overlay may be missing or fail; poweroff must never depend on it ----------------------
def test_missing_overlay_still_double_press_powers_off():
    """Broken case from the ticket: no overlay at all (show returns None) -> two presses still power off."""
    r = Rig()
    r.st.show = lambda: None
    r.press(); r.press(2.0)
    _check(r.events == ["final", "poweroff"], "no overlay: second press still powers off (%s)" % r.events)


def test_overlay_exceptions_never_block_poweroff():
    r = Rig()

    def boom(*a):
        raise RuntimeError("overlay crashed")
    r.st.show, r.st.final = boom, boom
    r.press()
    _check(r.st.armed, "show raising still arms the confirm window")
    r.press(2.0)
    _check(r.events == ["poweroff"], "final frame raising still powers off (%s)" % r.events)


def test_final_frame_comes_before_poweroff():
    r = Rig()
    r.press(); r.press(1.0)
    _check(r.events.index("final") < r.events.index("poweroff"), "feedback frame is shown before poweroff starts")


class FakeOverlay:
    def __init__(self, ok=True, ready=True):
        self.ok, self._ready, self.calls = ok, ready, []

    def ready(self):
        return self._ready

    def prime(self):
        self.calls.append("prime")
        if self.ok == "raise":
            raise OSError("boom")
        return bool(self.ok)

    def show_prompt(self, seconds):
        self.calls.append(("prompt", seconds))
        return bool(self.ok)

    def show_shutdown(self, wait):
        self.calls.append(("off", wait))
        return bool(self.ok)

    def hide(self):
        self.calls.append("hide")


def test_show_overlay_prefers_native_then_cog():
    m = _load_powerd()
    cog_calls = []
    m.show_cog = lambda: cog_calls.append(1) or "cogproc"
    m.OVERLAY = FakeOverlay(ok=True)
    _check(m.show_overlay() == m.NATIVE and cog_calls == [], "primed native overlay is used, cog never started")
    _check(m.OVERLAY.calls == [("prompt", 8)], "prompt countdown matches the 8 s window")
    m.hide_overlay(m.NATIVE)
    _check(m.OVERLAY.calls[-1] == "hide", "hiding a native prompt hides the overlay (no pkill)")
    m.OVERLAY = FakeOverlay(ok=False)
    _check(m.show_overlay() == "cogproc" and cog_calls == [1], "native not ready -> cog fallback")
    m.OVERLAY = None
    _check(m.show_overlay() == "cogproc", "no overlay module -> cog fallback")
    m.show_cog = lambda: None
    _check(m.show_overlay() is None, "no overlay and no cog -> None, no exception")


def test_final_frame_bounded_and_safe():
    m = _load_powerd()
    m.OVERLAY = FakeOverlay(ok=True)
    _check(m.final_frame(m.NATIVE) is True and m.OVERLAY.calls == [("off", m.FINAL_WAIT)],
           "final frame asks the overlay with the bounded wait")
    _check(0 < m.FINAL_WAIT <= 0.5, "final-frame wait is bounded to <= 0.5 s (got %s)" % m.FINAL_WAIT)
    m.OVERLAY = None
    _check(m.final_frame(None) is False, "no overlay -> final_frame returns False, does not raise")


def test_keep_primed():
    m = _load_powerd()
    ov = FakeOverlay(ok=True, ready=False)
    m.keep_primed(ov, every=0, sleep=lambda s: None, rounds=3)
    _check(ov.calls.count("prime") == 3, "not ready -> primed every round")
    ov = FakeOverlay(ok=True, ready=True)
    m.keep_primed(ov, every=0, sleep=lambda s: None, rounds=3)
    _check(ov.calls == [], "ready -> no re-prime")
    ov = FakeOverlay(ok="raise", ready=False)
    m.keep_primed(ov, every=0, sleep=lambda s: None, rounds=2)
    _check(ov.calls.count("prime") == 2, "a prime exception does not stop the primer")


# ---- overlay.py: wire protocol + no-compositor behaviour (live test: tests/test_overlay_live.py) --
def _load_overlay():
    spec = importlib.util.spec_from_file_location("overlay", os.path.join(ROLE, "files", "overlay.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_wire_encoding():
    o = _load_overlay()
    _check(o.enc_str("xdg") == b"\x04\x00\x00\x00xdg\x00", "string = len incl. NUL + bytes, 4-aligned")
    _check(len(o.enc_str("wl_shm")) == 4 + 8, "7 bytes padded to 8")
    msg = o.message(3, 6)
    _check(msg == b"\x03\x00\x00\x00\x06\x00\x08\x00", "header = object id, size<<16 | opcode")
    two = o.message(5, 0, o.enc_uint(7)) + o.message(6, 1, o.enc_int(-1))
    msgs, rest = o.split_messages(two + two[:5])
    _check(msgs == [(5, 0, b"\x07\x00\x00\x00"), (6, 1, b"\xff\xff\xff\xff")] and rest == two[:5],
           "split_messages returns whole messages and keeps a partial tail")
    s, off = o.dec_str(o.enc_str("wl_output") + b"\x02\x00\x00\x00")
    _check((s, off) == ("wl_output", 16), "dec_str reads back and skips the padding")


def test_broken_wire_size():
    o = _load_overlay()
    bad = b"\x01\x00\x00\x00\x00\x00\x04\x00"   # size 4 < header: corrupt stream
    try:
        o.split_messages(bad)
        flagged = False
    except ValueError:
        flagged = True
    _check(flagged, "BROKEN: a message smaller than its header is rejected, not looped on")


def test_overlay_without_compositor_fails_fast():
    import tempfile
    import time
    o = _load_overlay()
    with tempfile.TemporaryDirectory() as d:
        ov = o.Overlay(display="wayland-9", runtime_dir=d, painter_factory=lambda: None)
        t = time.monotonic()
        ok = (ov.prime(), ov.show_prompt(8), ov.show_shutdown(wait=0.3))
        ov.hide()
        dt = time.monotonic() - t
        _check(ok == (False, False, False) and not ov.ready(), "no cage socket -> every call returns False")
        _check(dt < 0.2, "and returns at once (%.3f s), the press never waits on a missing compositor" % dt)
        open(os.path.join(d, "wayland-9"), "w").close()        # a stale non-socket file
        _check(ov.prime() is False, "a stale wayland file (not a socket) -> False, no exception")


def test_press_never_waits_on_a_busy_primer():
    """The primer may hold the overlay lock for seconds against a half-dead compositor; a press must not."""
    import threading
    import time
    o = _load_overlay()
    ov = o.Overlay(display="wayland-9", runtime_dir="/nonexistent", painter_factory=lambda: None)
    held, release = threading.Event(), threading.Event()

    def primer():
        with ov.lock:
            held.set()
            release.wait(5)
    th = threading.Thread(target=primer)
    th.start()
    held.wait(1)
    t = time.monotonic()
    res = (ov.show_prompt(8), ov.show_shutdown(wait=0.3))
    dt = time.monotonic() - t
    release.set()
    th.join()
    _check(res == (False, False) and dt < 0.3, "busy primer -> show calls give up in %.3f s (fallback path)" % dt)


def test_overlay_text_tells_when_to_switch_off():
    o = _load_overlay()
    blob = " ".join(o.OFF_LINES)
    _check("NO SIGNAL" in blob and "SWITCH OFF" in blob, "final frame says switch off at NO SIGNAL (the maintainer, #66)")
    src = _read("files", "overlay.py")
    _check(not re.search(r"subprocess|sudo|poweroff|reboot", src.split("if __name__")[0]),
           "overlay.py never powers off or spawns processes itself")


# ---- role structure rules (text -> list of problems) -------------------------------------------
def rule_sudoers(defaults, tasks):
    p = []
    m = re.search(r'(?m)^power_sudoers_line:\s*"(.*?)"\s*(#.*)?$', defaults)
    line = m.group(1) if m else ""
    line_rendered = line.replace("{{ cabinet_user }}", "coindojo")
    if line_rendered != "coindojo ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff":
        p.append("sudoers line is not exactly the poweroff-only rule: %r" % line_rendered)
    if re.search(r"\bALL\s*$|NOPASSWD:\s*ALL|\*|reboot|,|systemctl\s*$", line_rendered):
        p.append("sudoers line too broad (ALL / wildcard / reboot / list)")
    if "validate: visudo -cf %s" not in tasks:
        p.append("sudoers task has no visudo validate")
    if not re.search(r'mode:\s*"0440"', tasks):
        p.append("sudoers mode is not 0440")
    return p


def rule_no_reboot(tasks, defaults, handlers):
    p = []
    blob = tasks + defaults + handlers
    if re.search(r"ansible\.builtin\.reboot|systemctl\s+(reboot|restart|stop)|\bshutdown\b -", blob):
        p.append("role reboots/restarts/stops something")
    if "pkill cage" in blob:
        p.append("kills cage")
    return p


def rule_logind(tasks, defaults):
    p = []
    if "HandlePowerKey=ignore" not in tasks:
        p.append("HandlePowerKey=ignore missing")
    if not re.search(r"(?m)^power_logind_dropin:\s*/etc/systemd/logind\.conf\.d/\S+\.conf\s*(#.*)?$", defaults):
        p.append("logind setting is not a logind.conf.d drop-in")
    if re.search(r"path:\s*/etc/systemd/logind\.conf\s*$", tasks, re.M):
        p.append("edits logind.conf directly")
    return p


def rule_bash_profile(tasks, defaults):
    """.bash_profile must be CLEANED of powerd.py, never get a start line."""
    p = []
    m = re.search(r"- name: Remove the powerd start line.*?(?=\n- name:|\Z)", tasks, re.S)
    blk = m.group(0) if m else ""
    if "ansible.builtin.lineinfile" not in blk or "power_bash_profile" not in blk:
        p.append(".bash_profile cleanup is not a lineinfile task")
    if "state: absent" not in blk:
        p.append(".bash_profile line is not removed (state: absent missing)")
    if not re.search(r"regexp:\s*'powerd\\\.py'", blk):
        p.append(".bash_profile cleanup has no regexp on powerd.py")
    if re.search(r"nohup", tasks + defaults.replace("# ", "#")) and re.search(r"(?m)^power_start_line:|^\s*line:.*powerd", tasks + defaults):
        p.append("a powerd start line is still written to .bash_profile")
    return p


def render_unit(unit, defaults):
    vals = dict(re.findall(r'(?m)^(\w+):\s*"?([^"\n#]+?)"?\s*(?:#.*)?$', defaults))
    vals["cabinet_user"] = "coindojo"
    vals["cabinet_group"] = "coindojo"
    vals["power_prompt"] = vals.get("power_prompt", "8")
    vals["power_dir"] = "/data/launcher"
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: vals.get(m.group(1), "?" + m.group(1)), unit)


def rule_unit(unit, defaults, py):
    p = []
    u = render_unit(unit, defaults)
    if re.search(r"\?\w+", u):
        p.append("unit has an unrendered variable")
    if not re.search(r"(?m)^Restart=on-failure\s*$", u):
        p.append("Restart=on-failure missing")
    if not re.search(r"(?m)^RestartSec=\d+\s*$", u):
        p.append("RestartSec missing")
    if re.search(r"(?m)^StartLimitIntervalSec=0\s*$", u) is None:
        p.append("StartLimitIntervalSec=0 missing (would give up after a few quick failures)")
    if "ExecStart=/usr/bin/python3 /data/launcher/powerd.py" not in u:
        p.append("ExecStart does not run powerd.py")
    if re.search(r"event\d", u):
        p.append("unit hard-codes an eventN device")
    if "POWERD_DEVICE=/dev/input/by-path/platform-pwr_button-event" not in u:
        p.append("unit does not point POWERD_DEVICE at the by-path symlink")
    m = re.search(r"(?m)^User=(\S+)", u)
    if not m:
        p.append("unit runs as root (no User=) without need: poweroff goes through the sudoers rule")
    elif m.group(1) != "coindojo":
        p.append("unit User= is %s, expected coindojo" % m.group(1))
    if "NoNewPrivileges=yes" in u or "NoNewPrivileges=true" in u:
        p.append("NoNewPrivileges would break the sudo poweroff")
    if not ("XDG_RUNTIME_DIR=/run/user/4242" in u and "WAYLAND_DISPLAY=wayland-0" in u):
        p.append("unit lacks coindojo's Wayland env for the overlay")
    if not re.search(r"(?m)^WantedBy=multi-user\.target", u):
        p.append("unit not enabled for boot")
    p += ["powerd.py: " + x for x in rule_py_device(py)]
    return p


def rule_py_device(py):
    p = []
    if re.search(r"/dev/input/event\d", py):
        p.append("hard-coded /dev/input/eventN")
    if "/dev/input/by-path/platform-pwr_button-event" not in py:
        p.append("by-path default missing")
    if "pwr_button" not in py or "/sys/class/input" not in py:
        p.append("no sysfs fallback by name")
    return p


def rule_deploy(tasks):
    p = []
    for f in ("powerd.py", "overlay.py", "shutdown.html"):
        if not re.search(r"-\s*\{\s*name:\s*%s\b" % re.escape(f), tasks):
            p.append("%s not deployed" % f)
    m = re.search(r"- name: Ship powerd\.py.*?(?=\n- name:|\n#|\Z)", tasks, re.S)
    if not m or "notify: restart powerd" not in m.group(0):
        p.append("changed powerd/overlay files do not restart powerd (old code keeps running)")
    return p


def _secs(v):
    m = re.fullmatch(r"(\d+)(s|min)?", v.strip())
    if not m:
        return None
    return int(m.group(1)) * (60 if m.group(2) == "min" else 1)


def rule_stop_timeout(tasks, defaults, handlers):
    """#66: cap the shutdown at ~15 s, but never so short that saves (RetroArch SRAM) get SIGKILLed."""
    p = []
    m = re.search(r'(?m)^power_stop_timeout:\s*"?([^"\s#]+)"?\s*(#.*)?$', defaults)
    v = _secs(m.group(1)) if m else None
    if v is None:
        p.append("power_stop_timeout missing or not N[s|min]")
    elif not 10 <= v <= 30:
        p.append("power_stop_timeout %ss outside 10..30 s (too short kills saves, too long feels hung)" % v)
    if "DefaultTimeoutStopSec={{ power_stop_timeout }}" not in tasks or "[Manager]" not in tasks:
        p.append("system.conf.d drop-in does not set DefaultTimeoutStopSec")
    if "TimeoutStopSec={{ power_stop_timeout }}" not in tasks.replace("DefaultTimeoutStopSec", "X"):
        p.append("user@.service drop-in does not set TimeoutStopSec")
    if not re.search(r"(?m)^power_system_dropin:\s*/etc/systemd/system\.conf\.d/\S+\.conf\s*(#.*)?$", defaults):
        p.append("manager timeout is not a system.conf.d drop-in")
    if not re.search(r"(?m)^power_user_unit_dropin:\s*/etc/systemd/system/user@\.service\.d/\S+\.conf\s*(#.*)?$", defaults):
        p.append("user manager timeout is not a user@.service.d drop-in")
    if "notify: reload systemd" not in tasks or "daemon_reload: true" not in handlers:
        p.append("timeout drop-ins do not trigger a daemon-reload")
    return p


def rule_unit_tasks(tasks):
    p = []
    if "coin-dojo-powerd.service.j2" not in tasks or "power_unit_file" not in tasks:
        p.append("unit template not installed")
    # #244: `state: "{{ omit if coin_dojo_image ... else 'started' }}"` (an image build only enables it)
    if not re.search(r"enabled:\s*true", tasks) or not re.search(r"state:\s*(started|\"\{\{ omit if coin_dojo_image[^\n]*'started')", tasks):
        p.append("unit not enabled+started")
    return p


def _role():
    return _read("tasks", "main.yml"), _hk(_read("defaults", "main.yml")), _read("handlers", "main.yml")


def test_layout():
    for rel in ("tasks/main.yml", "defaults/main.yml", "handlers/main.yml", "files/powerd.py", "files/overlay.py",
                "files/shutdown.html",
                "templates/coin-dojo-powerd.service.j2"):
        _check(os.path.isfile(os.path.join(ROLE, rel)), "role has " + rel)


def test_role_rules():
    t, d, h = _role()
    for name, probs in (("sudoers", rule_sudoers(d, t)), ("no reboot", rule_no_reboot(t, d, h)),
                        ("logind", rule_logind(t, d)), (".bash_profile", rule_bash_profile(t, d)),
                        ("unit", rule_unit(_read("templates", "coin-dojo-powerd.service.j2"), d, _read("files", "powerd.py"))),
                        ("unit installed+enabled", rule_unit_tasks(t)),
                        ("deploy", rule_deploy(t)),
                        ("stop timeout", rule_stop_timeout(t, d, h))):
        _check(probs == [], "%s rules %s" % (name, probs))


def test_shipped_files_match_rules():
    py = _read("files", "powerd.py")
    _check("DEBOUNCE  = 0.30" in py and "'/usr/bin/systemctl', 'poweroff'" in py and "reboot" not in py,
           "powerd.py calls only `sudo -n /usr/bin/systemctl poweroff`")
    _check("secret" not in _read("files", "shutdown.html").lower(), "shutdown.html has no secrets")


# ---- broken cases: each MUST be flagged --------------------------------------------------------
def test_broken_sudoers_all():
    t, d, h = _role()
    bad = d.replace("/usr/bin/systemctl poweroff", "ALL")
    _check(rule_sudoers(bad, t) != [], "BROKEN: sudoers granting ALL is flagged")


def test_broken_sudoers_reboot_and_wildcard():
    t, d, h = _role()
    _check(rule_sudoers(d.replace("poweroff", "poweroff, /usr/bin/systemctl reboot"), t) != [],
           "BROKEN: sudoers also allowing reboot is flagged")
    _check(rule_sudoers(d.replace("poweroff\"", "*\""), t) != [], "BROKEN: sudoers wildcard is flagged")


def test_broken_sudoers_no_validate_or_mode():
    t, d, h = _role()
    _check(rule_sudoers(d, t.replace("validate: visudo -cf %s", "")) != [], "BROKEN: missing visudo validate is flagged")
    _check(rule_sudoers(d, t.replace('mode: "0440"', 'mode: "0644"')) != [], "BROKEN: sudoers mode 0644 is flagged")


def test_broken_reboot_and_logind():
    t, d, h = _role()
    _check(rule_no_reboot(t + "\n- ansible.builtin.reboot:\n", d, h) != [], "BROKEN: a reboot task is flagged")
    _check(rule_logind(t.replace("HandlePowerKey=ignore", "HandlePowerKey=poweroff"), d) != [],
           "BROKEN: logind not ignoring the key is flagged")
    _check(rule_logind(t, d.replace("logind.conf.d/coin-dojo-power.conf", "logind.conf")) != [],
           "BROKEN: editing logind.conf directly is flagged")


def _unit():
    return _read("templates", "coin-dojo-powerd.service.j2"), _hk(_read("defaults", "main.yml")), _read("files", "powerd.py")


def test_unit_renders_as_expected():
    u, d, py = _unit()
    r = render_unit(u, d)
    for want in ("User=coindojo", "Restart=on-failure", "RestartSec=3",
                 "POWERD_DEVICE=/dev/input/by-path/platform-pwr_button-event"):
        _check(want in r, "rendered unit has " + want)


def test_broken_unit():
    u, d, py = _unit()
    _check(rule_unit(u.replace("Restart=on-failure", "Restart=no"), d, py) != [], "BROKEN: Restart missing/changed is flagged")
    _check(rule_unit(re.sub(r"(?m)^Restart=.*\n", "", u), d, py) != [], "BROKEN: no Restart line is flagged")
    _check(rule_unit(u, d.replace("/dev/input/by-path/platform-pwr_button-event", "/dev/input/event0"), py) != [],
           "BROKEN: unit with hard-coded event0 is flagged")
    _check(rule_unit(re.sub(r"(?m)^User=.*\n", "", u), d, py) != [], "BROKEN: unit running as root without need is flagged")
    _check(rule_unit(u.replace("User={{ cabinet_user }}", "User=root"), d, py) != [], "BROKEN: User=root is flagged")
    _check(rule_unit(u + "NoNewPrivileges=yes\n", d, py) != [], "BROKEN: NoNewPrivileges (breaks sudo) is flagged")
    _check(rule_unit(u, d, py.replace("/dev/input/by-path/platform-pwr_button-event", "/dev/input/event0")) != [],
           "BROKEN: powerd.py hard-coding event0 is flagged")


def test_broken_bash_profile():
    t, d, h = _role()
    _check(rule_bash_profile(t.replace("state: absent", "state: present"), d) != [],
           "BROKEN: .bash_profile line kept (state present) is flagged")
    _check(rule_bash_profile(t + "\n- ansible.builtin.lineinfile:\n    line: 'nohup python3 powerd.py &'\n", d) != [],
           "BROKEN: a powerd start line written to .bash_profile is flagged")
    _check(rule_unit_tasks(t.replace("enabled: true", "")) != [], "BROKEN: unit not enabled is flagged")


def test_broken_deploy_and_timeouts():
    t, d, h = _role()
    _check(rule_deploy(t.replace('    - { name: overlay.py, mode: "0644" }\n', "")) != [],
           "BROKEN: overlay.py not shipped is flagged")
    _check(rule_deploy(t.replace("  notify: restart powerd\n", "", 1)) != [],
           "BROKEN: file copy without restarting powerd is flagged")
    _check(rule_stop_timeout(t, d.replace('power_stop_timeout: "15s"', 'power_stop_timeout: "1s"'), h) != [],
           "BROKEN: a 1 s stop timeout (would SIGKILL saves) is flagged")
    _check(rule_stop_timeout(t, d.replace('power_stop_timeout: "15s"', 'power_stop_timeout: "90s"'), h) != [],
           "BROKEN: leaving 90 s is flagged")
    _check(rule_stop_timeout(t.replace("[Manager]", "[Service]"), d, h) != [],
           "BROKEN: manager drop-in without [Manager] is flagged")
    _check(rule_stop_timeout(t, d, h.replace("daemon_reload: true", "daemon_reload: false")) != [],
           "BROKEN: no daemon-reload for the drop-ins is flagged")


def test_unit_nice():
    u, d, py = _unit()
    r = render_unit(u, d)
    m = re.search(r"(?m)^Nice=(-?\d+)\s*$", r)
    _check(bool(m) and -10 <= int(m.group(1)) < 0, "powerd runs with a modest negative Nice (got %s)" % (m and m.group(1)))


# ---- #127: the role reads declared variables (config/variables.d/power.yml) ------------------------------
def _config_problems(**cli):
    try:
        _cfg(**cli).raise_for_problems()
    except C.ConfigError as e:
        return e.problems
    return []


def test_declared_defaults_are_todays_values():
    v = _cfg().values
    _check((v["hk_power_prompt"], v["hk_power_stop_timeout"], v["hk_power_nice"]) == (8, "15s", -5),
           "declared defaults are today's live values (8 s prompt, 15s stop timeout, nice -5)")
    _check(v["hk_cabinet_uid"] == 4242 and _hk("{{ hk_cabinet_uid }}") == "4242",
           "the uid has one source: hk_cabinet_uid (host area), no literal in the role")


def test_broken_declared_values_fail_config_check():
    for var, bad in (("hk_power_nice", 30), ("hk_power_nice", -21), ("hk_power_prompt", 1), ("hk_power_prompt", 99),
                     ("hk_power_stop_timeout", "soon"), ("hk_power_stop_timeout", 15)):
        ps = _config_problems(**{var: bad})
        _check(any(var in x for x in ps), "BROKEN: %s: %r fails config-check (%s)" % (var, bad, ps[:1]))
    _check(_config_problems(hk_power_nice=19, hk_power_prompt=10, hk_power_stop_timeout="20s") == [],
           "valid overrides pass config-check")


def rule_no_hidden_knob(defaults):
    """Every role default is a pass-through `{{ hk_* }}` or marked `# internal`."""
    p = []
    for line in defaults.splitlines():
        m = re.match(r"^(\w+):\s*(.*?)\s*$", line)
        if not m or m.group(2).startswith("#"):
            continue
        name, val = m.groups()
        if re.search(r"#\s*internal\b", val):
            continue
        if not re.fullmatch(r'"\{\{\s*hk_\w+\s*\}\}"\s*(#.*)?', val):
            p.append("%s is neither {{ hk_* }} nor # internal: %s" % (name, val))
    return p


def test_no_hidden_knob():
    d = _read("defaults", "main.yml")
    _check(rule_no_hidden_knob(d) == [], "every power default is {{ hk_* }} or # internal %s" % rule_no_hidden_knob(d))
    _check(rule_no_hidden_knob(d.replace('power_nice: "{{ hk_power_nice }}"', "power_nice: -5")) != [],
           "BROKEN: a planted literal power_nice: -5 is flagged")
    _check(rule_no_hidden_knob(d + "power_new_knob: 3\n") != [], "BROKEN: a new unmarked literal default is flagged")
    _check(rule_no_hidden_knob(d.replace("power_uid: \"{{ hk_cabinet_uid }}\"", "power_uid: 4242")) != [],
           "BROKEN: a literal power_uid is flagged")
    with open(os.path.join(ROOT, "config", "variables.d", "power.yml"), encoding="utf-8") as fh:
        decl = fh.read()
    _check("ansible/roles/power" in decl, "power.yml names the role as consumer")


def test_prompt_window_reaches_powerd():
    u, d, py = _unit()
    tpl = u
    _check("{% if power_prompt | int != 8 %}" in tpl and "Environment=POWERD_WINDOW={{ power_prompt }}" in tpl,
           "unit passes POWERD_WINDOW only when hk_power_prompt is not the built-in 8 (default render unchanged)")
    old = os.environ.pop("POWERD_WINDOW", None)
    try:
        m = _load_powerd()
        _check(m.WINDOW == 8.0 and m.PAGE.endswith("shutdown.html?s=8"), "powerd defaults to an 8 s window")
        os.environ["POWERD_WINDOW"] = "12"
        m = _load_powerd()
        _check(m.WINDOW == 12.0 and m.PAGE.endswith("shutdown.html?s=12"), "POWERD_WINDOW=12 sets the window and the page countdown")
    finally:
        os.environ.pop("POWERD_WINDOW", None)
        if old is not None:
            os.environ["POWERD_WINDOW"] = old
    _check("location.search" in _read("files", "shutdown.html"), "shutdown.html counts down from ?s=")


def test_broken_state_machine_no_confirm():
    """A daemon that powers off on the FIRST press must fail the state-machine expectations."""
    r = Rig()
    r.st.on_power = lambda: r.st.confirm()   # sabotage: no arming step
    r.press()
    _check(r.events != ["show"], "BROKEN: single-press poweroff daemon is distinguishable (test would fail it)")


if __name__ == "__main__":
    main(globals())
