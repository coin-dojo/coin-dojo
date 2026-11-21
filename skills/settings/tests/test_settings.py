#!/usr/bin/env python3
"""SETTINGS app tests (#32-#35). Plain python3, stdlib only; pytest-compatible. Run from the repo root:

    python3 skills/settings/tests/test_settings.py

Covers: on-screen keyboard (every printable ASCII character reachable, navigation, pages, hostname filter),
USB keyboard text (shifted symbols, non-US characters intact), the password reaching nmcli as ONE argv element
unchanged through BOTH the on-screen keyboard and the USB keyboard (real subprocess into fake_nmcli.py), nothing
secret in the log or on screen, nmcli errors shown plainly, cabinet-name validation + the root helper's /etc/hosts
rewrite, the pad input layer (START+SELECT exit, START fires on release, stick repeat), and no shell anywhere.
(No colour/font literals: brand/tests/test_brand.py lints skills/settings with the other clean files.)
BROKEN cases (must be caught): shell=True / os.system in the code, bad
host names (incl. one sneaking past the app straight to the helper), a password split into two argv elements.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APPDIR = os.path.dirname(HERE)
REPO = os.path.normpath(os.path.join(APPDIR, "..", ".."))
sys.path.insert(0, APPDIR)
import settings_core as core  # noqa: E402
import coin_dojo_set_hostname as helper  # noqa: E402
import settings_app  # noqa: E402
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
from hktest import check, main, skip  # noqa: E402  (shared helper; SKIPs counted in RESULT, #152)

FAKE = os.path.join(APPDIR, "fake_nmcli.py")
NASTY = "p@ss \"word\" $HOME 'x'"            # quotes, spaces, $ : must arrive as ONE argv element, unchanged


# ------------------------------------------------------------------------------ helpers
class Env:
    """Temp dir with a fake nmcli state + argv log; NM/App wired to the real fake_nmcli.py subprocess."""

    def __init__(self):
        self.dir = tempfile.mkdtemp()
        self.state = os.path.join(self.dir, "state.json")
        self.argv = os.path.join(self.dir, "argv.jsonl")
        self.logf = os.path.join(self.dir, "settings.log")
        os.environ["HK_FAKE_NMCLI_STATE"] = self.state
        os.environ["HK_FAKE_NMCLI_ARGV"] = self.argv
        self.log = core.Log(self.logf)
        self.nm = core.NM(FAKE, log=self.log)
        self.calls = []
        self.host = core.Hostname("/usr/local/sbin/coin-dojo-set-hostname", runner=self._host_runner, log=self.log)
        self.host.current = lambda: "coin-dojo"

    def _host_runner(self, argv, timeout):
        self.calls.append(argv)
        return 0, "hostname set", ""

    def app(self):
        a = core.App(self.nm, self.host, self.log)
        a.run_pending()
        return a

    def argvs(self):
        if not os.path.exists(self.argv):
            return []
        with open(self.argv) as f:
            return [json.loads(x) for x in f if x.strip()]

    def close(self):
        os.environ.pop("HK_FAKE_NMCLI_ARGV", None)
        shutil.rmtree(self.dir)


def drive(app, actions):
    for a in actions:
        app.handle(a)
        app.run_pending()


def open_network(app, ssid):
    """From home: WI-FI, then the row of SSID, then its first action (ENTER PASSWORD / CONNECT)."""
    drive(app, ["ok"])
    items = app.view()["items"]
    drive(app, ["down"] * items.index(ssid) + ["ok", "ok"])


def keys_for(kb, text):
    """Pad actions that type TEXT with the on-screen keyboard (page switches + stick moves + LK)."""
    acts = []
    sim = core.Keyboard(kb.mode, "", kb.max_len)
    sim.page, sim.row, sim.col = kb.page, kb.row, kb.col

    def go(pred):
        for _ in range(200):
            k = sim.key()
            if pred(k):
                return
            # walk row by row, column by column (deterministic, short enough for tests)
            rows = sim.rows()
            if sim.col < len(rows[sim.row]) - 1:
                sim.move("right")
                acts.append("right")
            else:
                sim.move("down")
                acts.append("down")
                while sim.col:
                    sim.move("left")
                    acts.append("left")
        raise AssertionError("key not reachable")

    for ch in text:
        page = next(p for p, rows in core.PAGE_ROWS.items() if any(ch in r for r in rows)) if ch != " " else sim.page
        while ch != " " and sim.page != page:
            target = "sym" if (page == "#+=" or sim.page == "#+=") else "shift"
            go(lambda k: k.action == target)
            sim.press()
            acts.append("ok")
        if ch == " ":
            go(lambda k: k.action == "space")
        else:
            go(lambda k: k.action == "char" and k.char == ch)
        sim.press()
        acts.append("ok")
    assert sim.text == text, (sim.text, text)
    return acts


# ------------------------------------------------------------------------------ keyboard
def test_deep_json_is_no_state():
    # #203: 100000 nested arrays raise RecursionError on the CI python (not on every python); the app must not crash.
    import tempfile, unittest.mock
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "state.json")
        with open(p, "w") as f:
            f.write("[" * 100000 + "]" * 100000)
        got = core._read_json(p)   # None where the python raises RecursionError, a list where it does not
        check(not isinstance(got, dict), "#203 a deeply nested state file reads as no state (no crash)")
        with open(p, "w") as f:
            f.write("{}")
        with unittest.mock.patch.object(core.json, "load", side_effect=RecursionError("deep")):
            check(core._read_json(p) is None, "BROKEN: a RecursionError from json.load (any python) reads as no state")
    with unittest.mock.patch.object(core.json, "loads", side_effect=RecursionError("deep")):
        check(core._reply_error(b"{}", "pw", "FALLBACK") == "FALLBACK", "a deeply nested RA error reply falls back")

def test_every_printable_ascii_is_on_the_keyboard():
    chars = set("".join("".join(r) for r in core.PAGE_ROWS.values())) | {" "}
    want = set(chr(c) for c in range(32, 127))
    check(want <= chars, "all 95 printable ASCII characters are on a page (missing: %r)" % "".join(sorted(want - chars)))


def test_keyboard_navigation_and_pages():
    kb = core.Keyboard("password")
    check(kb.key().char == "1", "starts on '1'")
    kb.move("left")
    check(kb.key().char == "0", "left wraps to the end of the row")
    kb.move("up")
    check(kb.key().action == "done", "up from the top row wraps to the specials row (nearest key: DONE)")
    kb.move("down")
    kb.move("down")
    check(kb.key().char == "p", "down goes to the nearest key in x")
    kb.goto_action("shift")
    kb.press()
    check(kb.page == "ABC" and kb.rows()[1][0].char == "Q", "SHIFT shows capitals")
    kb.goto_action("sym")
    kb.press()
    check(kb.page == "#+=", "#+= shows symbols")
    kb.goto_action("show")
    kb.type_text("ab")
    check(kb.shown() == core.MASK * 2, "password is masked")
    kb.press()
    check(kb.shown() == "ab", "SHOW reveals it")
    kb.goto_action("del")
    kb.press()
    check(kb.text == "a", "DEL deletes")
    kb.goto_action("done")
    kb.press()
    check(kb.done, "DONE finishes")
    k2 = core.Keyboard("password", max_len=3)
    k2.type_text("abcdef")
    check(k2.text == "abc", "max length enforced")


def test_usb_keyboard_text_intact():
    kb = core.Keyboard("password")
    kb.type_text("Ünïcødé ß€ !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
    check(kb.text == "Ünïcødé ß€ !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~", "shifted symbols + non-US characters kept byte for byte")
    kb2 = core.Keyboard("password")
    kb2.type_text("a\x08b\x1bc\x7fd")
    check(kb2.text == "abcd", "control characters are not typed into the field")


def test_hostname_keyboard_filters():
    kb = core.Keyboard("hostname")
    kb.type_text("My_Cab! 2")
    check(kb.text == "mycab2", "hostname field: capitals lowered, other characters dropped")
    chars = set(k.char for r in kb.rows() for k in r if k.action == "char")
    check(chars <= set("abcdefghijklmnopqrstuvwxyz0123456789-"), "hostname keyboard shows only a-z 0-9 -")
    check({k.action for k in kb.rows()[-1]} == {"del", "done"}, "hostname keyboard: no SHIFT/#+=/SPACE/SHOW")


# ------------------------------------------------------------------------------ host names
def test_hostname_validation():
    for good in ("a", "coin-dojo", "cab-1", "1cab", "x" * 63):
        check(core.hostname_error(good) is None and helper.valid(good), "valid name %r (app + helper)" % good[:20])
    for bad in ("", "-cab", "cab-", "Cab", "cab_1", "cab.local", "x" * 64, "cab name", "ca$b", "cab;reboot",
                "--help", "cab\n", "ünï"):
        check(core.hostname_error(bad) is not None and not helper.valid(bad),
              "BROKEN CASE: bad name %r rejected by the app AND the helper" % bad[:20])


def test_BROKEN_bad_hostname_never_reaches_sudo():
    calls = []
    h = core.Hostname(runner=lambda argv, t: calls.append(argv) or (0, "", ""))
    ok, msg = h.set("evil;reboot")
    check(not ok and not calls, "BROKEN CASE: invalid name -> no sudo call at all (%s)" % msg)
    ok, _ = h.set("arcade-2")
    check(ok and calls == [["sudo", "-n", core.HOSTNAME_HELPER, "arcade-2"]], "valid name -> sudo -n HELPER NAME (argv list)")


def test_helper_rewrites_hosts():
    cabinet = ("127.0.0.1\tlocalhost\n::1\t\tlocalhost ip6-localhost ip6-loopback\nff02::1\t\tip6-allnodes\n\n"
               "127.0.1.1\t\traspberrypi\n127.0.1.1 coin-dojo\n")
    new = helper.rewrite_hosts(cabinet, "arcade")
    check(new.count("127.0.1.1") == 1 and "127.0.1.1\tarcade\n" in new, "two 127.0.1.1 lines (the cabinet's) -> one")
    check(new.replace("127.0.1.1\tarcade\n", "") == cabinet.replace("127.0.1.1\t\traspberrypi\n", "").replace(
        "127.0.1.1 coin-dojo\n", ""), "every other /etc/hosts line byte-identical")
    check(helper.rewrite_hosts("127.0.0.1 localhost", "x").endswith("127.0.0.1 localhost\n127.0.1.1\tx\n"),
          "no 127.0.1.1 line -> appended")
    d = tempfile.mkdtemp()
    try:
        hosts = os.path.join(d, "hosts")
        with open(hosts, "w") as f:
            f.write(cabinet)
        os.chmod(hosts, 0o644)
        runs = []

        class R:
            returncode, stdout, stderr = 0, "", ""

        rc = helper.main(["arcade"], hosts=hosts, run=lambda argv, **kw: runs.append(argv) or R())
        check(rc == 0 and runs[0] == [helper.HOSTNAMECTL, "set-hostname", "arcade"], "helper: hostnamectl set-hostname NAME")
        check(runs[1] == [helper.SYSTEMCTL, "try-restart", "avahi-daemon.service"], "helper: avahi restarted (NAME.local)")
        check(open(hosts).read() == new and (os.stat(hosts).st_mode & 0o777) == 0o644, "helper: /etc/hosts written, mode kept")
        with open(hosts, "wb") as f:
            f.write(b"127.0.1.1 \xff\xfe broken\n")
        runs.clear()
        rc = helper.main(["arcade"], hosts=hosts, run=lambda argv, **kw: runs.append(argv) or R())
        check(rc == 1 and not runs, "BROKEN CASE: unreadable /etc/hosts -> exit 1 BEFORE hostnamectl runs")
        runs.clear()
        for bad in (["Bad"], ["a", "b"], [], ["-n"], ["a;b"]):
            rc = helper.main(bad, hosts=hosts, run=lambda argv, **kw: runs.append(argv) or R())
            check(rc == 2 and not runs, "BROKEN CASE: helper refuses %r and runs nothing" % (bad,))
    finally:
        shutil.rmtree(d)


# ------------------------------------------------------------------------------ nmcli
def test_password_one_argv_element_via_usb_keyboard():
    e = Env()
    try:
        app = e.app()
        open_network(app, "Dojo: 5GHz")
        check(app.top["id"] == "password", "Dojo: 5GHz (WPA, not saved) -> password keyboard")
        drive(app, [("text", NASTY), "enter"])
        conn = [a for a in e.argvs() if "connect" in a]
        check(len(conn) == 1, "one nmcli connect call")
        a = conn[0]
        check(a[a.index("password") + 1] == NASTY, "USB keyboard: password is ONE argv element, unchanged: %r" % a[a.index("password") + 1])
        check(a[a.index("connect") + 1] == "Dojo: 5GHz", "SSID with ':' and space is one argv element")
        check(app.toast and app.toast[0] == "ok" and "CONNECTED" in app.toast[1], "connected toast: %s" % (app.toast,))
        check(app.status.get("ssid") == "Dojo: 5GHz", "status shows the new network")
        v = app.view()
        check(v["id"] == "wifi" and v["items"][v["sel"]] == "Dojo: 5GHz", "after connecting the cursor is on that network")
        with open(e.logf) as f:
            log = f.read()
        check(NASTY not in log and "$HOME" not in log and "***" in log, "the log never holds the password (redacted ***)")
    finally:
        e.close()


def test_password_one_argv_element_via_onscreen_keyboard():
    e = Env()
    try:
        app = e.app()
        open_network(app, "Dojo: 5GHz")
        kb = app.top["kb"]
        drive(app, keys_for(kb, NASTY) + ["start"])
        conn = [a for a in e.argvs() if "connect" in a]
        check(len(conn) == 1 and conn[0][conn[0].index("password") + 1] == NASTY,
              "on-screen keyboard (stick + LK + START): password is ONE argv element, unchanged")
    finally:
        e.close()


def test_BROKEN_split_password_is_detected():
    """The argv check above must fail on a runner that splits the password like a shell would."""
    seen = []

    def shellish(argv, t):
        seen.append(" ".join(argv).split())                       # what `sh -c "nmcli ... password $PW"` would do
        return 0, "", ""
    nm = core.NM("nmcli", runner=shellish)
    nm._dev = "wlan0"
    nm.connect("Dojo", NASTY)
    a = seen[-1]
    check(a[a.index("password") + 1] != NASTY, "BROKEN CASE: a shell-split argv no longer carries the password intact")


def test_wrong_password_shows_nmcli_error_without_secret():
    e = Env()
    try:
        app = e.app()
        open_network(app, "Neighbour")
        drive(app, [("text", "nope nope"), "enter"])
        check(app.toast and app.toast[0] == "err" and "Secrets were required" in app.toast[1], "nmcli error shown plainly: %s" % (app.toast,))
        check(app.top["id"] == "password" and app.top["kb"].text == "nope nope", "stays on the keyboard to fix the password")
        kb = app.top["kb"]
        kb.goto_action("show")
        drive(app, ["ok"])
        check(kb.show, "SHOW reveals the password")
        drive(app, ["start"])
        check(not kb.show and app.view()["field"] == core.MASK * 9, "after a failed connect the password is masked again")
    finally:
        e.close()
    leak = core.NM("nmcli", runner=lambda argv, t: (4, "", "Error: bad psk 'hunter2 x' for Dojo"))
    leak._dev = "wlan0"
    ok, msg = leak.connect("Dojo", "hunter2 x")
    check(not ok and "hunter2" not in msg and "***" in msg, "an nmcli error echoing the password is redacted: %s" % msg)


def test_saved_network_connect_and_forget():
    e = Env()
    try:
        app = e.app()
        drive(app, ["ok"])
        v = app.view()
        i = v["items"].index("Cabinet Test")
        check(v["nets"][i].in_use and v["nets"][i].uuid, "Cabinet Test: in use and saved")
        drive(app, ["down"] * i + ["ok"])
        check(app.view()["items"][0] == "FORGET THIS NETWORK", "connected network offers FORGET")
        drive(app, ["ok"])
        check(app.top["id"] == "confirm" and app.top["sel"] == 1, "forget asks first (cursor on NO)")
        drive(app, ["up", "ok"])
        dels = [a for a in e.argvs() if "delete" in a]
        check(len(dels) == 1 and dels[0][-2:] == ["uuid", "8a3f6c1e-0000-4000-8000-000000000001"], "forget = connection delete uuid U")
        check(app.toast == ("ok", "FORGOT Cabinet Test") and not app.status.get("ssid"), "forgotten and disconnected")
        drive(app, ["esc"])
        open_network(app, "Arcade Guest")
        check(app.toast and app.toast[0] == "ok", "open network connects without a password")
        a = [x for x in e.argvs() if "connect" in x][-1]
        check("password" not in a, "no password argv for an open network")
    finally:
        e.close()


def test_log_starts_over_above_1mb():
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "settings.log")
        with open(p, "w") as f:
            f.write("x" * (core.Log.MAX_BYTES + 10))
        core.Log(p).write("start")
        check(os.path.getsize(p) < 200 and '"start"' in open(p).read(), "a log over 1 MB starts over")
        core.Log(p).write("again")
        check(open(p).read().count("\n") == 2, "below 1 MB it appends")
    finally:
        shutil.rmtree(d)


def test_terse_parsing():
    check(core.split_terse(r" :Dojo\: 5GHz:64:WPA2") == [" ", "Dojo: 5GHz", "64", "WPA2"], "nmcli -t escapes '\\:'")
    check(core.split_terse(r"a\\b:c") == ["a\\b", "c"], "nmcli -t escapes '\\\\'")
    check(core.redact_argv(["nmcli", "dev", "wifi", "connect", "x", "password", "s3cret"])[-1] == "***", "argv redaction")


def test_menus_and_exit():
    e = Env()
    try:
        app = e.app()
        check(app.view()["items"] == ["WI-FI", "CABINET NAME", "BACK"], "home menu")
        drive(app, ["down", "ok"])
        check(app.top["id"] == "hostname" and app.top["kb"].text == "coin-dojo", "CABINET NAME opens the keyboard with the name")
        drive(app, ["esc"])
        check(app.top["id"] == "home", "Esc goes back")
        drive(app, ["ok", "bksp", "bksp", ("text", "-2"), "enter"])          # cursor still on CABINET NAME
        new = "coin-dojo"[:-2] + "-2"        # the default name, two letters off, "-2" (follows the export rename, #146)
        check(app.top["id"] == "confirm" and new + ".local" in app.top["text"], "confirm names the .local name")
        drive(app, ["down", "ok"]) if app.top["sel"] == 0 else drive(app, ["up", "ok"])
        check(e.calls == [["sudo", "-n", core.HOSTNAME_HELPER, new]], "YES -> sudo -n helper " + new)
        check(app.top["id"] == "home" and new + ".local" in app.toast[1], "back home with the result")
        drive(app, ["ok", ("text", "-"), "start"])                  # home cursor still on CABINET NAME
        check(app.top["error"] and app.top["id"] == "hostname", "trailing '-' -> error shown, no confirm")
        drive(app, ["exit"])
        check(app.quit, "START+SELECT quits from anywhere")
        app2 = e.app()
        drive(app2, ["down", "down", "ok"])
        check(app2.quit, "BACK on home quits")
    finally:
        e.close()


# ------------------------------------------------------------------------------ benchmark mode (#189)
RA_OFF = {"fps_show": "false", "menu_enable_widgets": "true", "video_message_pos_y": "0.050000",
          "video_message_color": "ffff00"}
RA_ON = {"fps_show": "true", "menu_enable_widgets": "false", "video_message_pos_y": "0.940000",
         "video_message_color": "36d6e7"}
# a retroarch.cfg as RetroArch writes it, the keys at their off values; look-alike keys and a duplicate around them
RA_CFG = ('aspect_ratio_index = "22"\n'
          'fps_show = "false"\n'
          'fps_show_extra = "false"\n'
          'menu_enable_widgets = "true"\n'
          '# video_message_color = "ffffff"\n'
          'input_exit_emulator_btn = "8"\n'
          'video_message_color = "aaaaaa"\n'
          'video_message_pos_y = "0.050000"\n'
          'netplay_nickname = "p@ss \\"x\\" $HOME"\n'
          'video_message_color = "ffff00"\n'
          'video_message_pos_x = "0.050000"\n')
EMU_CFG = ("[config]\nrend.Resolution = 960\nrend.ShowFPS = no\nrend.vsync = yes\n\n"
           "[window]\nfullscreen = yes\n\n[achievements]\nEnabled = yes\nUserName = player_one\n")


class Bench:
    """Temp home with retroarch.cfg, emu.cfg and the two keys files the roles publish; a Benchmark on them."""

    def __init__(self, default=False, busy=False, ra=RA_CFG, emu=EMU_CFG):
        self.dir = tempfile.mkdtemp()
        self.ra, self.emu = os.path.join(self.dir, "retroarch.cfg"), os.path.join(self.dir, "emu.cfg")
        self.state = os.path.join(self.dir, "coin-dojo", "settings-state.json")
        self.keys = [os.path.join(self.dir, "coin-dojo", "benchmark-retroarch.json"),
                     os.path.join(self.dir, "coin-dojo", "benchmark-flycast.json")]
        os.makedirs(os.path.dirname(self.state))
        for path, text in ((self.ra, ra), (self.emu, emu)):
            if text is not None:
                with open(path, "w", newline="") as f:
                    f.write(text)
        self.spec(0, dict(file=self.ra, format="retroarch", default=default, on=RA_ON, off=RA_OFF))
        self.spec(1, dict(file=self.emu, format="ini", section="config", default=default,
                          on={"rend.ShowFPS": "yes"}, off={"rend.ShowFPS": "no"}))
        self.busy = busy
        self.logf = os.path.join(self.dir, "settings.log")
        self.b = core.Benchmark(self.state, self.keys, busy=lambda: self.busy, log=core.Log(self.logf))

    def spec(self, i, d):
        with open(self.keys[i], "w") as f:
            f.write(d if isinstance(d, str) else json.dumps(d))

    def read(self, path):
        with open(path, newline="") as f:
            return f.read()

    def close(self):
        shutil.rmtree(self.dir)


def ra_values(text):
    out = {}
    for ln in text.splitlines():
        m = re.match(r'^(\w+) = "(.*)"$', ln)
        if m:
            out[m.group(1)] = m.group(2)                 # last one wins, like RetroArch
    return out


def test_bench_toggle_writes_exactly_the_keys():
    t = Bench()
    try:
        e = Env()
        app = core.App(e.nm, e.host, e.log, t.b)
        app.run_pending()
        check(app.view()["items"] == ["WI-FI", "CABINET NAME", "SHOW FPS:  OFF", "BACK"], "home shows SHOW FPS: OFF")
        drive(app, ["down", "down", "ok"])
        check(app.top["id"] == "bench" and app.view()["items"] == ["TURN ON", "BACK"] and app.view()["state"] == "OFF",
              "SHOW FPS screen: TURN ON, BACK")
        drive(app, ["ok"])
        ra, emu = t.read(t.ra), t.read(t.emu)
        check(app.toast == ("ok", "SHOW FPS IS NOW ON") and app.view()["items"][0] == "TURN OFF", "toggled on: %r" % (app.toast,))
        check({k: ra_values(ra)[k] for k in RA_ON} == RA_ON, "retroarch.cfg has every on value")
        old_l, new_l = RA_CFG.splitlines(True), ra.splitlines(True)
        changed = [i for i, (a, b) in enumerate(zip(old_l, new_l)) if a != b]
        check(len(old_l) == len(new_l) and [old_l[i].split(" =")[0] for i in changed] ==
              ["fps_show", "menu_enable_widgets", "video_message_pos_y", "video_message_color"],
              "only the key lines changed (the LAST video_message_color, as lineinfile); every other line byte-identical: %r"
              % [new_l[i] for i in changed])
        check('video_message_color = "aaaaaa"\n' in ra and "fps_show_extra = \"false\"\n" in ra and
              '# video_message_color = "ffffff"\n' in ra, "look-alike keys, comments and the earlier duplicate untouched")
        check(emu == EMU_CFG.replace("rend.ShowFPS = no", "rend.ShowFPS = yes"), "emu.cfg: only rend.ShowFPS changed")
        check(json.loads(t.read(t.state)) == {"benchmark_mode": True}, "state file says on")
        check(app.stack[0]["items"][2][0] == "SHOW FPS:  ON", "home label follows")
        drive(app, ["ok"])
        check(t.read(t.ra) == RA_CFG and t.read(t.emu) == EMU_CFG, "off again: both files byte-identical to the start")
        check(json.loads(t.read(t.state)) == {"benchmark_mode": False}, "state file says off")
        check(not [f for f in os.listdir(t.dir) if f.endswith(".hk-tmp")], "no temp files left")
        e.close()
    finally:
        t.close()


def test_bench_refuses_while_a_game_runs():
    t = Bench(busy=True)
    try:
        st = (os.stat(t.ra).st_mtime_ns, os.stat(t.emu).st_mtime_ns)
        ok, msg = t.b.set(True)
        check(not ok and "GAME" in msg, "BROKEN CASE: a running game refuses the toggle (%s)" % msg)
        check(t.read(t.ra) == RA_CFG and t.read(t.emu) == EMU_CFG and
              (os.stat(t.ra).st_mtime_ns, os.stat(t.emu).st_mtime_ns) == st, "files untouched while a game runs")
        check(not os.path.exists(t.state) and t.b.current() is False, "no state written")
    finally:
        t.close()


def test_bench_state_file_falls_back_to_the_default():
    for default in (False, True):
        t = Bench(default=default)
        try:
            check(t.b.saved() is None and t.b.current() is default, "missing state file -> Ansible default %s" % default)
            for bad in ("", "{", "garbage", "[true]", '{"benchmark_mode": "yes"}', '{"benchmark_mode": 1}', '{"other": true}'):
                with open(t.state, "w") as f:
                    f.write(bad)
                check(t.b.saved() is None and t.b.current() is default,
                      "BROKEN CASE: state %r -> Ansible default %s" % (bad, default))
            with open(t.state, "w") as f:
                f.write('{"benchmark_mode": %s, "later": 1}' % ("false" if default else "true"))
            check(t.b.current() is (not default), "a valid state file wins over the default")
            t.b.set(default)
            check(json.loads(t.read(t.state)) == {"benchmark_mode": default, "later": 1}, "other state keys kept")
        finally:
            t.close()


def test_bench_broken_keys_files_write_nothing():
    for i, bad, why in ((0, "{not json", "malformed JSON"),
                        (0, dict(file="rel/retroarch.cfg", format="retroarch", on=RA_ON, off=RA_OFF), "relative path"),
                        (0, dict(file="/x", format="retroarch", on={"fps_show": "true\ninput_x = \"1\""},
                                 off={"fps_show": "false"}), "a newline in a value"),
                        (0, dict(file="/x", format="retroarch", on={"fps_show": 'true"'}, off={"fps_show": "false"}), "a quote"),
                        (0, dict(file="/x", format="retroarch", on=RA_ON, off={"fps_show": "false"}), "on/off keys differ"),
                        (1, dict(file="/x", format="ini", on={"rend.ShowFPS": "yes"}, off={"rend.ShowFPS": "no"}),
                         "ini without a section"),
                        (1, dict(file="/x", format="sh", on={"a": "b"}, off={"a": "c"}), "unknown format")):
        t = Bench()
        try:
            t.spec(i, bad)
            ok, msg = t.b.set(True)
            check(not ok and "BROKEN KEYS FILE" in msg and t.read(t.ra) == RA_CFG and t.read(t.emu) == EMU_CFG
                  and not os.path.exists(t.state), "BROKEN CASE: %s -> refused, nothing written (%s)" % (why, msg))
        finally:
            t.close()
    t = Bench()
    try:
        for k in t.keys:
            os.remove(k)
        ok, msg = t.b.set(True)
        check(not ok and "DEPLOY" in msg and not t.b.available() and t.b.current() is False,
              "no keys files yet (never deployed) -> says so, writes nothing")
    finally:
        t.close()


def test_bench_missing_keys_and_sections_are_added():
    t = Bench(ra='fps_show = "false"', emu="[window]\r\nfullscreen = yes\r\n")
    try:
        ok, _m = t.b.set(True)
        ra, emu = t.read(t.ra), t.read(t.emu)
        check(ok and ra.startswith('fps_show = "true"\n') and ra_values(ra) == RA_ON, "missing keys appended, a last line without newline fixed")
        check(emu == "[window]\r\nfullscreen = yes\r\n\r\n[config]\r\nrend.ShowFPS = yes\r\n", "missing [config] appended, CRLF kept: %r" % emu)
        check(core.set_ini_keys("[config]\na = 1\n\n[x]\n", "config", {"b": "2"}) == "[config]\na = 1\nb = 2\n\n[x]\n",
              "a missing key goes at the end of its section")
    finally:
        t.close()
    t = Bench(ra=None)
    try:
        ok, _m = t.b.set(True)
        check(ok and not os.path.exists(t.ra) and "rend.ShowFPS = yes" in t.read(t.emu),
              "no retroarch.cfg yet (RetroArch never started): skipped, flycast still set")
    finally:
        t.close()


def test_bench_png_render():
    try:
        settings_app.Painter(settings_app.brand.load())
    except OSError as e:
        skip("png render: %s" % e)
        return
    t = Bench()
    try:
        entry = os.path.join(t.dir, "settings.hks")
        with open(entry, "w") as f:
            json.dump(dict(benchmark_state=t.state, benchmark_keys=t.keys, log=t.logf), f)
        out = os.path.join(t.dir, "bench.png")
        r = subprocess.run([sys.executable, os.path.join(APPDIR, "settings_app.py"), entry, "--png", out, "--nmcli", FAKE,
                            "--hostname", "coin-dojo", "--actions", "down,down,down,down,down,ok"],
                           capture_output=True, text=True, env=dict(os.environ, HOME=t.dir,
                                                                    HK_FAKE_NMCLI_STATE=os.path.join(t.dir, "nm.json")))
        data = open(out, "rb").read() if os.path.exists(out) else b""
        check(r.returncode == 0 and data[:8] == b"\x89PNG\r\n\x1a\n", "renders the SHOW FPS screen (rc %d %s)"
              % (r.returncode, r.stderr[-200:]))
        check(t.read(t.ra) == RA_CFG, "opening the screen writes nothing")
    finally:
        t.close()


# ------------------------------------------------------------------------------ RetroAchievements page (#171)
import fake_ra  # noqa: E402

OLD_TOKEN = "OLDtoken99999999"          # obviously fake (the deploy's login in the fixtures)
RA_CH = ('video_driver = "gl"\n'
         'cheevos_enable = "true"\n'
         'cheevos_hardcore_mode_enable = "true"\n'
         'cheevos_username = "OldUser"\n'
         'cheevos_token = "%s"\n'
         'cheevos_password = ""\n'
         'netplay_nickname = "x"\n') % OLD_TOKEN
EMU_CH = ("[config]\nrend.ShowFPS = no\n\n[achievements]\nEnabled = yes\nHardcoreMode = yes\nUserName = OldUser\n"
          "Token = %s\n\n[window]\nfullscreen = yes\n") % OLD_TOKEN
RA_OUT = 'video_driver = "gl"\ncheevos_enable = "false"\nnetplay_nickname = "x"\n'      # never logged in, off
EMU_OUT = "[config]\nrend.ShowFPS = no\n\n[achievements]\nEnabled = no\n"


class Ch:
    """Temp home: retroarch.cfg, emu.cfg, the state file, a log; a Cheevos on them against the fake RA server."""

    def __init__(self, api, ra=RA_CH, emu=EMU_CH, busy=False):
        self.dir = tempfile.mkdtemp()
        self.ra, self.emu = os.path.join(self.dir, "retroarch.cfg"), os.path.join(self.dir, "emu.cfg")
        self.state = os.path.join(self.dir, "coin-dojo", "settings-state.json")
        self.logf = os.path.join(self.dir, "settings.log")
        for path, text in ((self.ra, ra), (self.emu, emu)):
            if text is not None:
                with open(path, "w", newline="") as f:
                    f.write(text)
        self.busy = busy
        self.api = api
        self.ch = core.Cheevos(dict(retroarch=self.ra, flycast=self.emu), self.state, api, busy=lambda: self.busy,
                               log=core.Log(self.logf), allow_loopback=True)          # the fake server (test hook)

    def app(self):
        e = Env()
        self._env = e
        a = core.App(e.nm, e.host, core.Log(self.logf), None, [core.CheevosPage(self.ch)])
        a.run_pending()
        return a

    def read(self, path):
        with open(path, newline="") as f:
            return f.read()

    def snap(self):
        return tuple((self.read(p), os.stat(p).st_mtime_ns) for p in (self.ra, self.emu))

    def files_with(self, needles):
        """Every file under the temp home whose bytes hold any of needles (raw, URL-encoded or JSON-escaped)."""
        forms = set()
        for n in needles:
            forms |= {n.encode("utf-8"), urllib.parse.quote_plus(n).encode(), urllib.parse.quote(n).encode(),
                      urllib.parse.quote(n, safe="").encode(), json.dumps(n)[1:-1].encode()}
        hits = []
        for root, _d, files in os.walk(self.dir):
            for f in files:
                with open(os.path.join(root, f), "rb") as fh:
                    data = fh.read()
                if any(x in data for x in forms):
                    hits.append(os.path.relpath(os.path.join(root, f), self.dir))
        return hits

    def close(self):
        if hasattr(self, "_env"):
            self._env.close()
        shutil.rmtree(self.dir)


import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402
PW = fake_ra.FAKE_PASSWORD


def test_ch_login_writes_only_user_and_token():
    with fake_ra.Server() as srv:
        t = Ch(srv.url, ra=RA_OUT, emu=EMU_OUT)
        try:
            app = t.app()
            check(app.view()["items"] == ["WI-FI", "CABINET NAME", "RETROACHIEVEMENTS:  OFF", "BACK"], "home row %r"
                  % app.view()["items"])
            drive(app, ["down", "down", "ok"])
            v = app.view()
            check(app.top["id"] == "cheevos" and v["items"] == ["TURN ON", "SWITCH TO HARDCORE", "LOG IN", "BACK"]
                  and v["cards"][0]["value"] == "NOT LOGGED IN", "RA page: logged out: %r" % v["items"])
            drive(app, ["down", "down", "ok"])
            check(app.top["id"] == "ch_user" and app.top["kb"].mode == "text", "LOG IN opens the user keyboard")
            drive(app, [("text", "bad user!"), "start"])
            check(app.top["id"] == "ch_user" and app.top["error"] and not srv.requests,
                  "BROKEN CASE: a user name with a space/! is refused before any request")
            app.top["kb"].text = ""
            drive(app, [("text", "player_one"), "start"])
            check(app.top["id"] == "ch_password" and app.top["kb"].mode == "password" and app.top["user"] == "player_one",
                  "then the password keyboard for that user")
            drive(app, [("text", PW)])
            check(PW not in repr(app.view()) and app.view()["field"] == core.MASK * len(PW), "the password is masked on screen")
            drive(app, ["start"])
            check(app.toast == ("ok", "LOGGED IN AS Player_One") and app.top["id"] == "cheevos",
                  "logged in, back on the page: %r" % (app.toast,))
            check(len(srv.requests) == 1 and srv.requests[0]["method"] == "POST" and srv.requests[0]["path"] == "/dorequest.php"
                  and srv.requests[0]["query"] == "" and srv.requests[0]["form"] == {"r": "login2", "u": "player_one", "p": PW}
                  and srv.requests[0]["ua"] == core.CHEEVOS_UA,
                  "one POST r=login2, the password byte for byte in the BODY only (never the URL): %r"
                  % [dict(r, form=sorted(r["form"])) for r in srv.requests])
            ra, emu = t.read(t.ra), t.read(t.emu)
            check(ra == RA_OUT + 'cheevos_username = "Player_One"\ncheevos_token = "FAKEtoken0123456"\ncheevos_password = ""\n',
                  "retroarch.cfg: user + token appended, password key empty, every other byte kept: %r" % ra)
            check(emu == EMU_OUT + "UserName = Player_One\nToken = FAKEtoken0123456\n", "emu.cfg: [achievements] UserName + Token: %r" % emu)
            check(json.loads(t.read(t.state)) == {"cheevos_login": True}, "state: the app owns the login now")
            check(t.ch.account() == "Player_One" and app.stack[0]["items"][2][0] == "RETROACHIEVEMENTS:  OFF  -  Player_One",
                  "home shows who is logged in")
            check(not t.files_with([PW]), "BROKEN CASE guard: the password is in no file (cfgs, state, log): %r" % t.files_with([PW]))
            log = t.read(t.logf)
            check("FAKEtoken0123456" not in log and '"cheevos_login"' in log, "the log has the event, never the token")
            check(oct(os.stat(t.ra).st_mode & 0o777) == oct(0o644), "an existing cfg keeps its mode")
            check(not [f for f in os.listdir(t.dir) if f.endswith(".hk-tmp")], "no temp files left")
        finally:
            t.close()


def test_ch_api_errors_keep_the_old_login():
    with fake_ra.Server() as srv:
        cases = [("wrong password", srv.url, "player_one", "nope", "Invalid user/password"),
                 ("HTTP 500 + HTML", srv.base + "/broken/dorequest.php", "player_one", PW, "HTTP 500"),
                 ("a redirect (not followed)", srv.base + "/redirect/dorequest.php", "player_one", PW, "HTTP 302"),
                 ("an error echoing the password", srv.base + "/echo/dorequest.php", "player_one", PW, "bad password ***"),
                 ("plain http to another host", "http://ra.example.invalid/dorequest.php", "player_one", PW, "HTTPS"),
                 ("no server listening", "http://127.0.0.1:9/dorequest.php", "player_one", PW, "NO CONNECTION")]
        for why, api, user, pw, want in cases:
            t = Ch(api)
            try:
                before, n = t.snap(), len(srv.requests)
                ok, msg = t.ch.login(user, pw)
                check(not ok and want in msg and t.snap() == before and not os.path.exists(t.state)
                      and t.ch.account() == "OldUser",
                      "BROKEN CASE: %s -> refused (%s), the old login and both cfgs byte-identical" % (why, msg))
                check(PW not in msg and not t.files_with([PW]), "%s: the password is not in the message or any file" % why)
                if "redirect" in why:
                    check(len(srv.requests) == n + 1, "the redirect was not followed (one request)")
                if "another host" in why:
                    check(len(srv.requests) == n, "nothing sent to a non-https remote address")
            finally:
                t.close()
        t = Ch(srv.url)
        try:
            for name, tok in (("Player_One", "tok\nen12345678"), ("Player_One", ""), ("x y", "FAKEtoken0123456")):
                t.ch._login = lambda api, u, p, _n=name, _t=tok: (_n, _t)
                before = t.snap()
                ok, msg = t.ch.login("player_one", PW)
                check(not ok and t.snap() == before, "BROKEN CASE: an answer with user %r / token %r writes nothing" % (name, tok))
            t.ch._login = core.ra_login
            app = t.app()
            drive(app, ["down", "down", "ok", "down", "down", "ok", "ok"])         # LOG OUT -> confirm -> NO
            check(app.top["id"] == "cheevos" and t.ch.account() == "OldUser", "the confirm starts on NO: nothing happens")
        finally:
            t.close()
        t = Ch(srv.url, ra=RA_OUT, emu=EMU_OUT)
        try:
            app = t.app()
            before = t.snap()
            drive(app, ["down", "down", "ok", "down", "down", "ok", ("text", "player_one"), "start", ("text", "wrong"), "start"])
            check(app.top["id"] == "ch_password" and app.top["kb"].text == "" and app.toast[0] == "err"
                  and "Invalid" in app.toast[1] and t.snap() == before and not os.path.exists(t.state),
                  "UI: a wrong password stays on the keyboard, field emptied, error shown, nothing written: %r" % (app.toast,))
        finally:
            t.close()


def test_ch_logout_clears_both_files():
    t = Ch("http://127.0.0.1:9/unused")
    try:
        app = t.app()
        drive(app, ["down", "down", "ok"])
        check(app.view()["items"][2] == "LOG OUT OldUser" and app.view()["cards"][0]["value"] == "OldUser",
              "the page shows the deploy's login")
        drive(app, ["down", "down", "ok"])
        check(app.top["id"] == "confirm" and app.top["sel"] == 1, "LOG OUT asks first, cursor on NO")
        drive(app, ["up", "ok"])
        ra, emu = t.read(t.ra), t.read(t.emu)
        check(app.toast[0] == "ok" and core.retroarch_value(ra, "cheevos_token") == "" and
              core.retroarch_value(ra, "cheevos_username") == "", "retroarch.cfg: token and user cleared: %r" % ra)
        check(core.ini_value(emu, "achievements", "Token") == "" and core.ini_value(emu, "achievements", "UserName") == "",
              "emu.cfg: Token and UserName cleared")
        check(ra == RA_CH.replace('"OldUser"', '""').replace('"OLDtoken99999999"', '""') and
              emu == EMU_CH.replace("UserName = OldUser", "UserName = ").replace("Token = OLDtoken99999999", "Token = "),
              "only those lines changed")
        check(json.loads(t.read(t.state)) == {"cheevos_login": False} and t.ch.account() is None
              and app.view()["items"][2] == "LOG IN", "state: logged out here (a deploy will not log back in)")
        check(not t.files_with(["OLDtoken99999999"]), "the old token is gone from every file")
    finally:
        t.close()


def test_ch_toggles_and_refusal_while_a_game_runs():
    t = Ch("http://127.0.0.1:9/unused")
    try:
        app = t.app()
        drive(app, ["down", "down", "ok", "ok"])                     # TURN OFF
        check(app.toast == ("ok", "RETROACHIEVEMENTS ARE NOW OFF") and t.read(t.ra) == RA_CH.replace(
            'cheevos_enable = "true"', 'cheevos_enable = "false"') and t.read(t.emu) == EMU_CH.replace("Enabled = yes", "Enabled = no"),
            "TURN OFF writes only cheevos_enable / Enabled")
        drive(app, ["down", "ok"])                                   # SWITCH TO CASUAL
        check(t.ch.hardcore() is False and core.ini_value(t.read(t.emu), "achievements", "HardcoreMode") == "no"
              and core.retroarch_value(t.read(t.ra), "cheevos_hardcore_mode_enable") == "false", "casual written to both")
        check(json.loads(t.read(t.state)) == {"cheevos_enable": False, "cheevos_hardcore": False}, "both in the state file")
        t.busy = True
        before = t.snap()
        for name, fn in (("on", lambda: t.ch.set_enable(True)), ("hardcore", lambda: t.ch.set_hardcore(True)),
                         ("login", lambda: t.ch.login("player_one", PW)), ("logout", t.ch.logout)):
            ok, msg = fn()
            check(not ok and "GAME" in msg and t.snap() == before, "BROKEN CASE: %s refused while a game runs (%s)" % (name, msg))
        check(json.loads(t.read(t.state)) == {"cheevos_enable": False, "cheevos_hardcore": False}, "state untouched")
    finally:
        t.close()


def test_ch_state_file_falls_back_to_the_configs():
    t = Ch("http://127.0.0.1:9/unused", ra=RA_OUT, emu=EMU_CH)
    try:
        check(t.ch.enabled() is False and t.ch.hardcore() is True, "no state: retroarch.cfg first, then emu.cfg")
        os.makedirs(os.path.dirname(t.state))
        for bad in ("", "{", "garbage", "[true]", '{"cheevos_enable": "yes"}', '{"cheevos_enable": 1}'):
            with open(t.state, "w") as f:
                f.write(bad)
            check(t.ch.enabled() is False, "BROKEN CASE: state %r -> the configs decide" % bad)
        with open(t.state, "w") as f:
            f.write('{"benchmark_mode": true, "cheevos_enable": true}')
        check(t.ch.enabled() is True, "a valid state wins over the configs")
        t.ch.set_hardcore(False)
        check(json.loads(t.read(t.state)) == {"benchmark_mode": True, "cheevos_enable": True, "cheevos_hardcore": False},
              "other state keys (benchmark mode) kept")
    finally:
        t.close()
    t = Ch("http://127.0.0.1:9/unused", ra=None, emu=None)
    try:
        check(t.ch.enabled() is False and t.ch.account() is None, "no configs at all: off, not logged in")
        ok, _m = t.ch.set_enable(True)
        check(ok and oct(os.stat(t.ra).st_mode & 0o777) == oct(0o600) and oct(os.stat(t.emu).st_mode & 0o777) == oct(0o600)
              and oct(os.stat(t.state).st_mode & 0o777) == oct(0o600), "a cfg or state file the app creates is 0600")
    finally:
        t.close()


def test_ch_api_url_rules():
    """#198: in production the password goes only to https://retroachievements.org; loopback http is a test hook."""
    for url in ("https://retroachievements.org/dorequest.php", "https://RetroAchievements.org:443/dorequest.php"):
        check(core.api_url_error(url) is None, "allowed: %s" % url)
    for url in ("http://127.0.0.1:8080/dorequest.php", "http://localhost/dorequest.php",
                "https://evil.example/dorequest.php", "https://retroachievements.org.evil.example/dorequest.php",
                "https://evilretroachievements.org/dorequest.php", "https://www.retroachievements.org/dorequest.php",
                "https://u:p@retroachievements.org/dorequest.php", "https://retroachievements.org:8443/dorequest.php",
                "https://retroachievements.org:99999/x", "https://127.0.0.1/dorequest.php",
                "http://retroachievements.org/dorequest.php", "http://10.0.0.5/x", "ftp://x/y", "", "https://",
                "file:///etc/passwd"):
        check(core.api_url_error(url), "BROKEN CASE: production refuses login address %r" % url)
    for url in ("http://127.0.0.1:8080/dorequest.php", "http://localhost/dorequest.php"):
        check(core.api_url_error(url, allow_loopback=True) is None, "test hook: loopback http allowed: %s" % url)
    for url in ("http://10.0.0.5/x", "https://evil.example/dorequest.php", "http://u@127.0.0.1/x"):
        check(core.api_url_error(url, allow_loopback=True), "BROKEN CASE: the test hook still refuses %r" % url)
    check(core.api_url_error(core.CHEEVOS_API) is None and core.CHEEVOS_API.startswith("https://retroachievements.org/"),
          "the built-in address is RA over https and passes")


def test_ch_production_never_sends_to_loopback():
    """#198 broken case: a Cheevos built like the cabinet's (no test hook) refuses the fake server: nothing sent."""
    with fake_ra.Server() as srv:
        t = Ch(srv.url)
        try:
            t.ch = core.Cheevos(dict(retroarch=t.ra, flycast=t.emu), t.state, srv.url, busy=lambda: False,
                                log=core.Log(t.logf))
            before = t.snap()
            ok, msg = t.ch.login(fake_ra.FAKE_USER, PW)
            check(not ok and "THE LOGIN ADDRESS MUST BE" in msg and not srv.requests and t.snap() == before,
                  "BROKEN CASE: no test hook -> loopback login refused, no request, nothing written (%s)" % msg)
        finally:
            t.close()


def test_ch_http_exception_is_a_failed_login():
    """#198: an answer http.client cannot parse (bad status line, cut-off body) is LOGIN FAILED, not a crash."""
    import http.client
    with fake_ra.Server() as srv:
        for path, kind, want in (("/badstatus/dorequest.php", "BadStatusLine", "NO CONNECTION TO RETROACHIEVEMENTS (BadStatusLine)"),
                                 ("/short/dorequest.php", "IncompleteRead", "RETROACHIEVEMENTS SENT AN ODD ANSWER")):
            url = srv.base + path
            raised = None
            try:                                   # the broken case: what urllib does with it uncaught
                with core._opener().open(urllib.request.Request(url, data=b"r=login2"), timeout=10) as r:
                    r.read()
            except http.client.HTTPException as e:
                raised = type(e).__name__
            check(raised == kind, "BROKEN CASE: %s raises http.client.%s through urllib (%s)" % (path, kind, raised))
            t = Ch(url)
            try:
                before = t.snap()
                ok, msg = t.ch.login(fake_ra.FAKE_USER, PW)
                check(not ok and msg == "LOGIN FAILED: " + want and t.snap() == before and t.ch.account() == "OldUser",
                      "%s -> %s, old login kept" % (path, msg))
            finally:
                t.close()
            t = Ch(url, ra=RA_OUT, emu=EMU_OUT)
            try:
                app = t.app()
                drive(app, ["down", "down", "ok", "down", "down", "ok", ("text", "player_one"), "start", ("text", "x"), "start"])
                check(app.top["id"] == "ch_password" and app.toast[0] == "err" and want in app.toast[1],
                      "UI: %s stays on the password keyboard with an error toast: %r" % (path, app.toast))
            finally:
                t.close()

    class Raiser:                                  # any http.client.HTTPException out of open() or read()
        def __init__(self, exc):
            self.exc = exc

        def open(self, req, timeout=None):
            raise self.exc

    real = core._opener
    try:
        for exc in (http.client.IncompleteRead(b"x"), http.client.LineTooLong("header"), http.client.BadStatusLine("x"),
                    http.client.HTTPException("odd")):
            core._opener = lambda _e=exc: Raiser(_e)
            try:
                core.ra_login(core.CHEEVOS_API, "player_one", PW)
                got = "no error"
            except core.CheevosError as e:
                got = str(e)
            check(got == "NO CONNECTION TO RETROACHIEVEMENTS (%s)" % type(exc).__name__,
                  "BROKEN CASE: http.client.%s -> %s" % (type(exc).__name__, got))
    finally:
        core._opener = real


def test_ch_url_encoded_password_is_redacted():
    """#198: RA echoing the password URL-encoded (quote_plus / quote) is redacted in the toast and the log."""
    with fake_ra.Server() as srv:
        t = Ch(srv.base + "/echoenc/dorequest.php")
        try:
            forms = [urllib.parse.quote_plus(PW), urllib.parse.quote(PW), urllib.parse.quote(PW, safe="")]
            check(len(set(forms)) >= 2 and all(f != PW for f in forms), "the fixture password has encoded forms of its own")
            ok, msg = t.ch.login("player_one", PW)
            check(not ok and msg.count("***") == 3 and not any(f in msg for f in forms) and PW not in msg,
                  "every encoded form redacted: %r" % msg)
            check(not t.files_with([PW]), "no form of the password in the log or any file: %r" % t.files_with([PW]))
            old = core.redact("bad password %s / %s / %s" % tuple(forms), (PW,))          # the #171 redaction
            check(all(f in old for f in forms), "BROKEN CASE: redacting only the typed password leaves the encoded forms")
        finally:
            t.close()


def test_ch_login_ignores_proxy_settings():
    """#198: the login opener has ProxyHandler({}): *_proxy env vars never route the password through a proxy."""
    keys = ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY", "no_proxy", "NO_PROXY")
    saved = {k: os.environ.get(k) for k in keys}
    with fake_ra.Server() as srv, fake_ra.Server() as proxy:
        try:
            for k in keys:
                os.environ.pop(k, None)
            for k in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
                os.environ[k] = proxy.base
            name, token = core.ra_login(srv.url, fake_ra.FAKE_USER, PW, allow_loopback=True)
            check(name == fake_ra.FAKE_USER and token == fake_ra.FAKE_TOKEN and len(srv.requests) == 1
                  and not proxy.requests, "with *_proxy set the login goes straight to the server, the proxy saw nothing")
            try:                                   # the broken case: an opener without ProxyHandler({})
                urllib.request.build_opener(core._NoRedirect()).open(
                    urllib.request.Request(srv.url, data=b"r=login2"), timeout=10).read()
            except urllib.error.URLError:
                pass
            check(len(proxy.requests) == 1, "BROKEN CASE: a default opener sends the request to the env proxy")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def test_test_hooks_only_with_the_flag():
    """#198: the entry file's game_proc and a loopback cheevos_api count only with --test-hooks (never in ES-DE's
    command, never written by the role); without it the app reads /proc and refuses the loopback address."""
    with fake_ra.Server() as srv:
        t = Ch(srv.url)
        try:
            proc = os.path.join(t.dir, "proc")
            entry = os.path.join(t.dir, "settings.hks")
            with open(entry, "w") as f:
                json.dump(dict(cheevos_files=dict(retroarch=t.ra, flycast=t.emu), cheevos_api=srv.url,
                               benchmark_state=t.state, log=t.logf, game_proc=proc), f)
            for flag in (False, True):
                _b, app = settings_app.build(settings_app.parse_args([entry] + (["--test-hooks"] if flag else [])))
                ch = [p for p in app.pages if isinstance(p, core.CheevosPage)][0].ch
                check(ch.busy.proc == (proc if flag else "/proc"),
                      "%s: the game check reads %s" % ("--test-hooks" if flag else "BROKEN CASE: no flag", ch.busy.proc))
                n = len(srv.requests)
                try:
                    ch._login(ch.api, fake_ra.FAKE_USER, PW)
                    sent = None
                except core.CheevosError as e:
                    sent = str(e)
                if flag:
                    check(sent is None and len(srv.requests) == n + 1, "--test-hooks: the loopback fake server answers")
                else:
                    check(sent and "THE LOGIN ADDRESS MUST BE" in sent and len(srv.requests) == n,
                          "BROKEN CASE: no flag -> the entry's loopback address is refused, nothing sent (%s)" % sent)
            check(settings_app.parse_args([entry])["test_hooks"] is False, "the flag is off by default")
        finally:
            t.close()


SHOTS = os.environ.get("HK_SETTINGS_SHOTS")          # a dir: keep the rendered screens (previews for the report)


def test_ch_png_screens():
    try:
        settings_app.Painter(settings_app.brand.load())
    except OSError as e:
        skip("png render: %s" % e)
        return
    with fake_ra.Server() as srv:
        t = Ch(srv.url, ra=RA_OUT, emu=EMU_OUT)
        try:
            proc = os.path.join(t.dir, "proc")                 # a process table with ES-DE under cage and no game
            for pid, comm, ppid in ((1, "cage", 0), (2, "es-de", 1)):
                os.makedirs(os.path.join(proc, str(pid)))
                with open(os.path.join(proc, str(pid), "stat"), "w") as f:
                    f.write("%d (%s) S %d 0 0\n" % (pid, comm, ppid))
            entry = os.path.join(t.dir, "settings.hks")
            with open(entry, "w") as f:
                json.dump(dict(cheevos_files=dict(retroarch=t.ra, flycast=t.emu), cheevos_api=srv.url,
                               benchmark_state=t.state, log=t.logf, game_proc=proc), f)
            pw = PW.replace(",", "%2C")
            screens = [("ra-home", "down,down,down,down,down,down"), ("ra-page", "down,down,down,down,down,down,ok"),
                       ("ra-user", "down,down,down,down,down,down,ok,down,down,ok,text:player_one"),
                       ("ra-password", "down,down,down,down,down,down,ok,down,down,ok,text:player_one,start,text:" + pw),
                       ("ra-wrong-password", "down,down,down,down,down,down,ok,down,down,ok,text:player_one,start,text:nope,start"),
                       ("ra-logged-in", "down,down,down,down,down,down,ok,down,down,ok,text:player_one,start,text:" + pw + ",start"),
                       ("ra-logout-confirm", "down,down,down,down,down,down,ok,down,down,ok")]
            for name, acts in screens:
                out = os.path.join(t.dir, name + ".png")
                r = subprocess.run([sys.executable, os.path.join(APPDIR, "settings_app.py"), entry, "--png", out, "--nmcli",
                                    FAKE, "--hostname", "coin-dojo", "--test-hooks", "--actions", acts], capture_output=True, text=True,
                                   env=dict(os.environ, HOME=t.dir, HK_FAKE_NMCLI_STATE=os.path.join(t.dir, "nm.json")))
                data = open(out, "rb").read() if os.path.exists(out) else b""
                check(r.returncode == 0 and data[:8] == b"\x89PNG\r\n\x1a\n", "renders %s (rc %d %s)"
                      % (name, r.returncode, r.stderr[-200:]))
                check(PW not in r.stdout + r.stderr, "%s: the password is not on stdout/stderr" % name)
                if SHOTS:
                    os.makedirs(SHOTS, exist_ok=True)
                    shutil.copy(out, os.path.join(SHOTS, name + ".png"))
            check(t.ch.account() == "Player_One", "the logged-in render really logged in (through the fake server)")
            check(all(r["path"] == "/dorequest.php" and r["query"] == "" for r in srv.requests), "every request: body only")
        finally:
            t.close()


# ------------------------------------------------------------------------------ pad input layer
def test_input_layer():
    i = settings_app.Input()
    out = i.button("start", True, 0) + i.button("select", True, 0) + i.button("start", False, 0) + i.button("select", False, 0)
    check(out == ["exit"], "START+SELECT = exit only (no DONE on the START release): %r" % out)
    out = i.button("start", True, 0) + i.button("start", False, 0)
    check(out == ["start"], "START alone fires on release")
    check(i.button("start", False, 0) == [], "BROKEN CASE: a START release whose press was dropped (busy screen) does nothing")
    check(i.button("ok", True, 0) == ["ok"] and i.button("back", True, 0) == ["back"], "LK = ok, MK = back")
    check(i.axis(1, 30000, 0.0) == ["down"] and i.repeats(0.1) == [] and i.repeats(0.4) == ["down"], "stick down + repeat")
    check(i.axis(1, 0, 0.5) == [] and i.repeats(1.0) == [], "stick centred: repeat stops")
    check(settings_app.BTN[0] == "ok" and settings_app.BTN[1] == "back" and settings_app.BTN[6] == "start"
          and settings_app.BTN[4] == "select", "SDL a=LK->ok, b=MK->back, start, back=SELECT (gen_help mapping)")


def test_select_quits_from_any_depth():
    """#224 (the maintainer: "make sure settings menu shows quit is to back out all the way"): SELECT = QUIT, the whole app at
    once from any page; MK BACK stays one page up. The hint is on every screen."""
    q = settings_app.Input(select_quits=True)
    check(q.button("select", True, 0) == [] and q.button("select", False, 0) == ["exit"],
          "SETTINGS input: SELECT alone = exit (on its release)")
    out = q.button("start", True, 0) + q.button("select", True, 0) + q.button("select", False, 0) + q.button("start", False, 0)
    check(out == ["exit"], "START+SELECT still exits exactly once (no second exit on the SELECT release): %r" % out)
    check(q.button("select", False, 0) == [], "BROKEN CASE: a SELECT release without its press does nothing")
    plain = settings_app.Input()
    check(plain.button("select", True, 0) + plain.button("select", False, 0) == [],
          "BROKEN CASE: TROPHIES / boot menu input (select_quits off): SELECT alone does nothing")
    e = Env()
    try:
        app = e.app()
        drive(app, ["ok"])                                         # WI-FI page (scan queued)
        app.run_pending()
        check(app.top["id"] != "home" and len(app.stack) >= 2, "on a nested page (%s)" % app.top["id"])
        drive(app, ["back"])
        check(not app.quit and app.top["id"] == "home", "BROKEN CASE: MK BACK on a nested page goes up one page, not out")
        drive(app, ["down", "ok"])                                 # CABINET NAME keyboard: deepest kind of screen
        check(app.top["kind"] == "keyboard", "on the keyboard screen")
        for a in q.button("select", True, 0) + q.button("select", False, 0):
            app.handle(a)
        check(app.quit, "SELECT on the keyboard screen quits the whole app")
        app2 = e.app()
        views = [app2.view()]
        drive(app2, ["ok"])
        app2.run_pending()
        views.append(app2.view())
        drive(app2, ["back", "down", "ok"])
        views.append(app2.view())
        check(all(v.get("quit") == (["SELECT"], "QUIT") for v in views),
              "the QUIT hint (SELECT) is on home, WI-FI and the keyboard screen")
        check(all("SELECT" not in [k for keys, _w in v["hints"] for k in keys] for v in views),
              "SELECT is used by no other hint (only QUIT)")
    finally:
        e.close()
    src = open(os.path.join(APPDIR, "settings_app.py"), encoding="utf-8").read()
    check("inp = Input(select_quits=True)" in src, "the SETTINGS main loop turns select_quits on")


# ------------------------------------------------------------------------------ #225 quit fade
def test_quit_fade():
    calls = []

    class Front:
        def present(self, painter):
            calls.append(("present", painter.last))

    class P:
        last = None

        def veil(self, a):
            self.last = a
            calls.append(("veil", a))
    settings_app.fade(Front(), P(), sleep=lambda s: None)
    veils = [a for k, a in calls if k == "veil"]
    check(len(veils) == settings_app.FADE_STEPS + 1 and veils[-1] == 1.0 and all(0 < a < 1 for a in veils[:-1]),
          "fade: %d partial veils, then plain bg" % settings_app.FADE_STEPS)
    check(calls[-1] == ("present", 1.0), "BROKEN CASE: the last frame shown is the plain bg (what the theme's "
                                         "SETTINGS gamelist draws), not a half-faded one")
    total = settings_app.FADE_STEPS * settings_app.FADE_STEP_S
    check(total <= 0.25, "the fade adds at most 0.25 s to the exit (%.2f s)" % total)
    src = open(os.path.join(APPDIR, "settings_app.py"), encoding="utf-8").read()
    loop = src[src.index("        while not app.quit:"):src.index("    finally:\n        app.log.write(\"stop\")")]
    check("fade(front, painter)" in loop and "fade(front" not in src[src.index("    finally:\n        app.log.write"):],
          "main: the fade runs after a real quit (end of the loop), not in finally (an error skips it)")
    try:
        p = settings_app.Painter(settings_app.brand.load())
    except OSError as e:
        skip("veil render: %s" % e)
        return
    import ctypes
    p.rect(0, 0, settings_app.W, settings_app.H, "gold")
    p.veil(1.0)
    data, stride = p.pixels()
    b, g, r, _a = ctypes.string_at(data + stride * 500 + 4 * 700, 4)
    check((r, g, b) == tuple(p.b.rgb("bg")), "veil(1.0) leaves the plain theme bg (%r)" % ((r, g, b),))
    p.rect(0, 0, settings_app.W, settings_app.H, "gold")
    p.veil(0.35)
    b, g, r, _a = ctypes.string_at(data + stride * 500 + 4 * 700, 4)
    check((r, g, b) != tuple(p.b.rgb("bg")), "BROKEN CASE: one 0.35 veil is not yet plain bg (it is a fade)")


# ------------------------------------------------------------------------------ source rules
SHELL_RX = [re.compile(r"shell\s*=\s*True"), re.compile(r"\bos\.system\s*\("), re.compile(r"\bos\.popen\s*\("),
            re.compile(r"subprocess\.(?:run|call|check_call|check_output|Popen)\(\s*[\"'f]")]
def shell_problems(text):
    return [rx.pattern for rx in SHELL_RX if rx.search(text)]


def app_sources():
    return [os.path.join(APPDIR, f) for f in sorted(os.listdir(APPDIR)) if f.endswith(".py")]


def test_no_shell_anywhere():
    for p in app_sources() + [os.path.join(REPO, "ansible", "roles", "settings", "files", f)
                              for f in os.listdir(os.path.join(REPO, "ansible", "roles", "settings", "files"))
                              if f.endswith(".py")] if os.path.isdir(os.path.join(REPO, "ansible", "roles", "settings", "files")) else app_sources():
        with open(p, encoding="utf-8") as f:
            probs = shell_problems(f.read())
        check(not probs, "no shell in %s %s" % (os.path.relpath(p, REPO), probs or ""))
    for planted in ('subprocess.run("nmcli dev wifi connect " + ssid, shell=True)', 'os.system("nmcli")',
                    'subprocess.run(f"nmcli {x}")', "subprocess.Popen('nmcli')"):
        check(shell_problems(planted), "BROKEN CASE: planted %r is flagged" % planted)


# ------------------------------------------------------------------------------ render smoke
def test_png_render():
    try:
        settings_app.Painter(settings_app.brand.load())
    except OSError as e:
        skip("png render: %s" % e)
        return
    e = Env()
    try:
        out = os.path.join(e.dir, "kb.png")
        r = subprocess.run([sys.executable, os.path.join(APPDIR, "settings_app.py"), "--png", out, "--nmcli", FAKE,
                            "--hostname", "coin-dojo", "--actions", "ok,down,down,ok,ok,text:abc"],
                           capture_output=True, text=True, env=dict(os.environ, HOME=e.dir))
        data = open(out, "rb").read() if os.path.exists(out) else b""
        check(r.returncode == 0 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[16:24] == bytes.fromhex("000005a000000438"),
              "renders a 1440x1080 PNG of the password keyboard (rc %d %s)" % (r.returncode, r.stderr[-200:]))
    finally:
        e.close()


if __name__ == "__main__":
    main(globals())
