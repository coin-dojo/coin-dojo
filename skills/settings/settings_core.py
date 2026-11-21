#!/usr/bin/env python3
"""SETTINGS app core (#32-#35): on-screen keyboard, NetworkManager (nmcli) wrapper, cabinet name, screens.

Pure Python stdlib, no drawing: settings_app.py turns pad/keyboard events into actions, calls App.handle(),
and draws App.view(). Tests drive the same App with actions (tests/test_settings.py).

Actions (strings): up down left right ok back start exit enter esc bksp tab, or ("text", "<typed chars>").
  arcade panel: stick = up/down/left/right, LK = ok, MK = back (delete / go back), START = start (done),
                START+SELECT = exit (same as in games)
  USB keyboard:  arrows, Enter = enter, Esc = esc, Backspace = bksp, Tab = tab, typed characters = text
Both work at the same time; a keyboard plugged in while the app runs works at once (the compositor owns it).

Security rules (the maintainer 2026-11-15; Security reviews):
  - every external command is an argv LIST, never a shell string (no shell anywhere: a test greps for it)
  - the Wi-Fi password goes to nmcli as ONE argv element, byte for byte (quotes, spaces, $ intact)
  - no password ever in the log, on screen (masked unless SHOW), or in an error shown (redacted)
  - renaming the cabinet = `sudo -n /usr/local/sbin/coin-dojo-set-hostname NAME` (root helper, fixed path,
    the name checked here AND by the helper AND by the sudoers rule)
  - TIME ZONE (#206): `timedatectl set-timezone ZONE` as the cabinet user (a polkit rule allows exactly that one action
    at the seat, no root helper); ZONE is checked here against `timedatectl list-timezones` and the zoneinfo files
    (one argument, no paths, no '..'), and remembered in the state file so a deploy keeps it (the cabinet wins)
  - RetroAchievements (#171): the password goes ONLY into the body of one HTTPS POST to RA's login API (TLS verified,
    no redirects followed); only the token it returns is written (retroarch.cfg, flycast emu.cfg); the password is
    never stored, logged or shown unmasked unless SHOW

Pages (#171): WI-FI and CABINET NAME are built in; every other home row is a `Page` (template below: a label on the
home screen, one menu screen with status cards, its own actions). A new cabinet setting = one Page subclass + one line
in settings_app.build().
"""
import http.client
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import attract_apply as AA

HOSTNAME_HELPER = "/usr/local/sbin/coin-dojo-set-hostname"
PASSWORD_HELPER = "/usr/local/sbin/coin-dojo-set-password"
# #270: an admin's login password is the sudo password, so the helper refuses (coin_dojo_set_password.ADMIN_ON)
PW_ADMIN_ON = "ADMIN IS ON: CHANGE IT WITH passwd"
PW_MIN, PW_MAX = 6, 128                      # the helper checks the same limits again (coin_dojo_set_password.py)
PW_DEFAULT = "12345"                         # the default login of a fresh card: never accepted as the new password
HOSTNAME_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")        # RFC 1123 label, lower case; fullmatch only
LOG_PATH = os.path.expanduser("~/.cache/coin-dojo/settings.log")
MASK = "•"


# ================================================================================================
# On-screen keyboard
# ================================================================================================
PAGE_ROWS = {
    "abc": ["1234567890", "qwertyuiop", "asdfghjkl-", "zxcvbnm_.@"],
    "ABC": ["1234567890", "QWERTYUIOP", "ASDFGHJKL-", "ZXCVBNM_.@"],
    "#+=": ["!\"#$%&'()*", "+,-./:;<=>", "?@[\\]^_`{|", "}~"],
}
# every printable ASCII character (32-126) is on some page: tests/test_settings.py checks it
SPECIALS = [("SHIFT", "shift", 2), ("#+=", "sym", 2), ("SPACE", "space", 4), ("DEL", "del", 2),
            ("SHOW", "show", 2), ("DONE", "done", 3)]


class Key:
    __slots__ = ("label", "action", "char", "width")

    def __init__(self, label, action="char", char=None, width=1):
        self.label, self.action, self.char, self.width = label, action, char, width

    def __repr__(self):
        return "Key(%r)" % self.label


class Keyboard:
    """Grid of keys + the text being typed. mode 'password' (any printable character, masked, SHOW toggle) or
    'hostname' (a-z 0-9 - only; capitals typed on a USB keyboard become lower case)."""

    def __init__(self, mode="password", text="", max_len=63):
        assert mode in ("password", "hostname", "text")
        self.mode, self.text, self.max_len = mode, "", max_len
        self.page = "abc"
        self.show = mode != "password"
        self.row = self.col = 0
        for ch in text:
            self.type_char(ch)
        self.done = self.cancelled = False

    # -- what may be typed -------------------------------------------------------------------
    def allowed(self, ch):
        if len(ch) != 1:
            return False
        if self.mode == "hostname":
            return ch in "abcdefghijklmnopqrstuvwxyz0123456789-"
        cat = ord(ch)
        return cat >= 32 and cat != 127 and not (0x80 <= cat < 0xA0)       # printable, any script

    def rows(self):
        if self.mode == "hostname":
            chars = ["1234567890", "qwertyuiop", "asdfghjkl-", "zxcvbnm"]
            specials = [s for s in SPECIALS if s[1] in ("del", "done")]
        else:
            chars = PAGE_ROWS[self.page]
            specials = [s for s in SPECIALS if s[1] != "show" or self.mode == "password"]
        out = [[Key(c, "char", c) for c in r] for r in chars]
        out.append([Key(lab, act, None, w) for lab, act, w in specials])
        return out

    def key(self):
        r = self.rows()
        self.row = min(self.row, len(r) - 1)
        self.col = min(self.col, len(r[self.row]) - 1)
        return r[self.row][self.col]

    # -- navigation: left/right wrap in the row, up/down go to the key nearest in x ------------
    @staticmethod
    def _centres(row):
        out, x = [], 0.0
        for k in row:
            out.append(x + k.width / 2.0)
            x += k.width
        total = x or 1.0
        return [c / total for c in out]

    def move(self, d):
        r = self.rows()
        self.key()
        if d in ("left", "right"):
            n = len(r[self.row])
            self.col = (self.col + (1 if d == "right" else -1)) % n
            return
        cx = self._centres(r[self.row])[self.col]
        self.row = (self.row + (1 if d == "down" else -1)) % len(r)
        cs = self._centres(r[self.row])
        self.col = min(range(len(cs)), key=lambda i: (abs(cs[i] - cx), i))

    def goto_action(self, action):
        for ri, row in enumerate(self.rows()):
            for ci, k in enumerate(row):
                if k.action == action:
                    self.row, self.col = ri, ci
                    return True
        return False

    # -- editing -------------------------------------------------------------------------------
    def type_char(self, ch):
        if self.mode == "hostname" and "A" <= ch <= "Z":
            ch = ch.lower()
        if not self.allowed(ch) or len(self.text) >= self.max_len:
            return False
        self.text += ch
        return True

    def type_text(self, s):
        """Characters from a USB keyboard (SDL text input: already shifted / composed, any layout)."""
        n = 0
        for ch in s:
            n += bool(self.type_char(ch))
        return n

    def backspace(self):
        self.text = self.text[:-1]

    def press(self):
        """LK on the highlighted key."""
        k = self.key()
        if k.action == "char":
            self.type_char(k.char)
        elif k.action == "shift":
            self.page = "ABC" if self.page == "abc" else "abc"
        elif k.action == "sym":
            self.page = "abc" if self.page == "#+=" else "#+="
        elif k.action == "space":
            self.type_char(" ")
        elif k.action == "del":
            self.backspace()
        elif k.action == "show":
            self.show = not self.show
        elif k.action == "done":
            self.done = True

    def shown(self):
        return self.text if self.show else MASK * len(self.text)


# ================================================================================================
# Logging (never a secret)
# ================================================================================================
def redact_argv(argv):
    out, hide = [], False
    for a in argv:
        out.append("***" if hide else a)
        hide = a in ("password", "wifi-sec.psk", "802-11-wireless-security.psk")
    return out


def redact(text, secrets):
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


class Log:
    MAX_BYTES = 1000000                 # above this the log starts over (it holds no secrets; it must not grow forever)

    def __init__(self, path=LOG_PATH):
        self.path = path

    def write(self, event, **kw):
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            mode = "w" if os.path.exists(self.path) and os.path.getsize(self.path) > self.MAX_BYTES else "a"
            with open(self.path, mode, encoding="utf-8") as f:
                f.write(json.dumps(dict(t=time.strftime("%Y-%m-%dT%H:%M:%S"), event=event, **kw)) + "\n")
        except OSError:
            pass


# ================================================================================================
# NetworkManager via nmcli (argv lists only)
# ================================================================================================
def split_terse(line):
    """nmcli -t line -> fields (':' separates, '\\:' and '\\\\' are escapes)."""
    out, cur, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if c == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


def unescape(value):
    """nmcli -g value -> text ('\\:' -> ':', '\\\\' -> '\\')."""
    return re.sub(r"\\(.)", r"\1", value)


class Network:
    __slots__ = ("ssid", "signal", "security", "in_use", "uuid")

    def __init__(self, ssid, signal=0, security="", in_use=False, uuid=None):
        self.ssid, self.signal, self.security, self.in_use, self.uuid = ssid, signal, security, in_use, uuid

    @property
    def secured(self):
        return bool(self.security and self.security not in ("--", ""))

    def __repr__(self):
        return "Network(%r, %d%%, %r%s%s)" % (self.ssid, self.signal, self.security, ", in use" if self.in_use else "",
                                             ", saved" if self.uuid else "")


class NM:
    def __init__(self, nmcli="nmcli", runner=None, log=None, timeout=45):
        self.nmcli = nmcli
        self.runner = runner or self._subprocess
        self.log = log or Log(None)
        self.timeout = timeout
        self._dev = None

    @staticmethod
    def _subprocess(argv, timeout):
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)
        env = dict(os.environ, LC_ALL="C", LANG="C")
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)   # argv list, no shell
            return p.returncode, p.stdout, p.stderr
        except FileNotFoundError:
            return 127, "", "%s: not found" % argv[0]
        except OSError as e:
            return 126, "", "%s: %s" % (argv[0], e.strerror or e)
        except subprocess.TimeoutExpired:
            return 124, "", "timed out after %d s" % timeout

    def run(self, args, timeout=None, secrets=()):
        argv = [self.nmcli] + list(args)
        rc, out, err = self.runner(argv, timeout or self.timeout)
        self.log.write("nmcli", argv=redact_argv(argv), rc=rc,
                       err=redact((err or "").strip(), secrets)[:300] if rc else "")
        return rc, out or "", redact(err or "", secrets)

    @staticmethod
    def error_text(rc, out, err):
        msg = (err or out or "").strip().splitlines()
        msg = [m for m in msg if m.strip()]
        return msg[-1] if msg else "nmcli failed (exit %d)" % rc

    def device(self):
        if self._dev:
            return self._dev
        rc, out, _ = self.run(["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
        for line in out.splitlines():
            f = split_terse(line)
            if len(f) >= 2 and f[1] == "wifi":
                self._dev = f[0]
                return f[0]
        return None

    def status(self):
        """-> dict(device, state, ssid, signal, ip). Missing values are ''."""
        st = dict(device="", state="", ssid="", signal=0, ip="")
        rc, out, _ = self.run(["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
        for line in out.splitlines():
            f = split_terse(line)
            if len(f) >= 4 and f[1] == "wifi":
                st.update(device=f[0], state=f[2])
                self._dev = f[0]
                break
        if not st["device"]:
            st["state"] = "no wi-fi device"
            return st
        for n in self.scan(rescan=False, with_saved=False):
            if n.in_use:
                st.update(ssid=n.ssid, signal=n.signal)
        rc, out, _ = self.run(["-g", "IP4.ADDRESS", "device", "show", st["device"]])
        ips = [p.strip().split("/")[0] for p in out.replace("\n", "|").split("|") if p.strip()]
        st["ip"] = ips[0] if ips else ""
        return st

    def saved(self):
        """{ssid: uuid} of the saved Wi-Fi connections."""
        rc, out, _ = self.run(["-t", "-f", "NAME,UUID,TYPE", "connection", "show"])
        res = {}
        for line in out.splitlines():
            f = split_terse(line)
            if len(f) >= 3 and f[2] in ("802-11-wireless", "wifi"):
                rc2, o2, _ = self.run(["-g", "802-11-wireless.ssid", "connection", "show", "uuid", f[1]])
                ssid = unescape(o2.strip()) if rc2 == 0 and o2.strip() else f[0]
                res.setdefault(ssid, f[1])
        return res

    def scan(self, rescan=True, with_saved=True):
        """Visible networks, strongest first, one row per SSID (hidden SSIDs skipped). Saved ones carry .uuid."""
        args = ["-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes" if rescan else "no"]
        rc, out, err = self.run(args, timeout=30)
        if rc != 0 and rescan:                       # NM refuses a rescan right after another one: use its cache
            rc, out, err = self.run(args[:-1] + ["auto"], timeout=30)
        if rc != 0:
            raise NMError(self.error_text(rc, out, err))
        best = {}
        for line in out.splitlines():
            f = split_terse(line)
            if len(f) < 4 or not f[1] or f[1] == "--":
                continue
            try:
                sig = int(f[2])
            except ValueError:
                sig = 0
            n = Network(f[1], sig, f[3], f[0].strip() == "*")
            old = best.get(n.ssid)                    # one row per SSID: the one in use, else the strongest
            if old is None or (n.in_use and not old.in_use) or (not old.in_use and sig > old.signal):
                best[n.ssid] = n
        if with_saved:
            saved = self.saved()
            for n in best.values():
                n.uuid = saved.get(n.ssid)
        return sorted(best.values(), key=lambda n: (not n.in_use, -n.signal, n.ssid.lower()))

    def connect(self, ssid, password=None):
        """Join a network: the password is ONE argv element after the word `password`, exactly as typed."""
        args = ["--wait", "30", "device", "wifi", "connect", ssid]
        if password is not None and password != "":
            args += ["password", password]
        dev = self.device()
        if dev:
            args += ["ifname", dev]
        rc, out, err = self.run(args, timeout=45, secrets=(password,) if password else ())
        if rc == 0:
            return True, "CONNECTED TO %s" % ssid
        msg = redact(self.error_text(rc, out, err), (password,) if password else ())
        if "Secrets were required" in msg:          # how NetworkManager says "wrong password"
            msg = "WRONG PASSWORD?  " + msg
        return False, msg

    def up(self, uuid, ssid=""):
        rc, out, err = self.run(["--wait", "30", "connection", "up", "uuid", uuid], timeout=45)
        return (True, "CONNECTED TO %s" % ssid) if rc == 0 else (False, self.error_text(rc, out, err))

    def forget(self, uuid, ssid=""):
        rc, out, err = self.run(["connection", "delete", "uuid", uuid])
        return (True, "FORGOT %s" % ssid) if rc == 0 else (False, self.error_text(rc, out, err))


class NMError(RuntimeError):
    pass


# ================================================================================================
# Cabinet name (hostname)
# ================================================================================================
def hostname_error(name):
    """None when NAME is a valid cabinet name (RFC 1123 label: a-z 0-9 -, 1-63, no '-' at either end)."""
    if not name:
        return "TYPE A NAME FIRST"
    if len(name) > 63:
        return "AT MOST 63 CHARACTERS"
    if name.startswith("-") or name.endswith("-"):
        return "NO - AT THE START OR END"
    if not HOSTNAME_RE.fullmatch(name):          # not .match + $: "$" also matches before a final newline
        return "ONLY a-z, 0-9 AND -"
    return None


class Hostname:
    def __init__(self, helper=HOSTNAME_HELPER, runner=None, log=None, sudo="sudo"):
        self.helper, self.sudo = helper, sudo
        self.runner = runner or NM._subprocess
        self.log = log or Log(None)

    def current(self):
        return socket.gethostname()

    def set(self, name):
        err = hostname_error(name)
        if err:
            return False, err
        argv = [self.sudo, "-n", self.helper, name]
        rc, out, e = self.runner(argv, 30)
        self.log.write("hostname", argv=argv, rc=rc, err=(e or "").strip()[:300] if rc else "")
        if rc == 0:
            return True, "CABINET NAME IS NOW %s" % name
        lines = [x for x in ((e or "") + "\n" + (out or "")).splitlines() if x.strip()]
        return False, lines[-1] if lines else "rename failed (exit %d)" % rc


# ================================================================================================
# Password (#271): the cabinet user's Linux password and the network folder's Samba password, together. The app
# calls the root helper `sudo -n coin-dojo-set-password` with NO arguments and the new password as the one line on
# stdin (never argv: ps, sudo's log and the journal never see it). Nothing here stores or logs the value; the log gets
# the command and the exit code only, and whatever the helper printed is scrubbed of the password before it is used.
def password_error(pw):
    """None when PW is an acceptable new password, else what to tell the person (the same rules as the helper)."""
    if not pw:
        return "TYPE A PASSWORD FIRST"
    if pw == PW_DEFAULT:
        return "NOT THE DEFAULT PASSWORD (%s)" % PW_DEFAULT
    if len(pw) < PW_MIN:
        return "AT LEAST %d CHARACTERS" % PW_MIN
    if len(pw) > PW_MAX:
        return "AT MOST %d CHARACTERS" % PW_MAX
    if any(not (32 <= ord(c) <= 126) for c in pw):
        return "ONLY LETTERS, DIGITS AND SYMBOLS OF A US KEYBOARD"
    return None


class Password:
    def __init__(self, helper=PASSWORD_HELPER, runner=None, log=None, sudo="sudo"):
        self.helper, self.sudo = helper, sudo
        self.runner = runner or self._stdin_subprocess
        self.log = log or Log(None)

    @staticmethod
    def _stdin_subprocess(argv, data, timeout):
        """(rc, stdout, stderr) of argv with DATA on its stdin (the pipe: never argv, never a file)."""
        try:
            r = subprocess.run(argv, input=data, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "", "timed out"
        except OSError as e:
            return 127, "", e.strerror or "cannot run"
        return r.returncode, r.stdout, r.stderr

    def set(self, pw):
        """-> (ok, message). The helper is told the password on stdin only; the app never keeps it."""
        err = password_error(pw)
        if err:
            return False, err
        argv = [self.sudo, "-n", self.helper]
        rc, out, e = self.runner(argv, pw + "\n", 60)
        seen = redact((out or "") + "\n" + (e or ""), [pw])
        self.log.write("password", argv=argv, rc=rc, folder=("ok" if "network folder: ok" in seen else
                                                             "skipped" if "skipped" in seen else "no"))
        if rc == 0:
            if "network folder: ok" in seen:
                return True, "PASSWORD CHANGED  -  LOGIN AND NETWORK FOLDER"
            return True, "PASSWORD CHANGED"
        if rc == 3:
            return True, "PASSWORD CHANGED, BUT THE NETWORK FOLDER KEEPS THE OLD ONE"
        if rc == 2 and PW_ADMIN_ON in seen:
            return False, PW_ADMIN_ON          # as the helper says it: `passwd` is the command, lower case
        lines = [x for x in seen.splitlines() if x.strip()]
        return False, (lines[-1].strip().upper()[:80] if lines else "COULD NOT CHANGE THE PASSWORD (EXIT %d)" % rc)


# ================================================================================================
# Time zone (#206): the cabinet's zone. The app calls `timedatectl set-timezone ZONE` as the cabinet user (polkit rule
# in role settings: that one action, at the seat); the zone also goes into the state file as "timezone", so the next
# deploy keeps it (role settings applies hk_timezone only when the state file has none: the cabinet wins, #189/#171).
# ================================================================================================
TZ_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+\-]*(/[A-Za-z0-9][A-Za-z0-9_+\-]*){0,2}")      # fullmatch; no '.', so no '..'; no leading slash or dash
TZ_MAX = 64
TZ_DIR = "/usr/share/zoneinfo"
TZ_PRESETS = (("US EASTERN  (NEW YORK)", "America/New_York"), ("US CENTRAL  (CHICAGO)", "America/Chicago"),
              ("US MOUNTAIN  (DENVER)", "America/Denver"), ("US PACIFIC  (LOS ANGELES)", "America/Los_Angeles"),
              ("UTC", "Etc/UTC"), ("LONDON", "Europe/London"), ("PARIS / BERLIN", "Europe/Paris"),
              ("TOKYO", "Asia/Tokyo"), ("SYDNEY", "Australia/Sydney"), ("SAO PAULO", "America/Sao_Paulo"))


def tz_error(zone):
    """None when ZONE has the shape of a zone name (the list check is TimeZone.valid)."""
    if not isinstance(zone, str) or not zone:
        return "NO TIME ZONE GIVEN"
    if len(zone) > TZ_MAX or not TZ_RE.fullmatch(zone):
        return "NOT A TIME ZONE NAME"
    return None


def tz_pretty(zone):
    return zone.replace("_", " ").replace("/", " / ").upper()


class TimeZone:
    def __init__(self, runner=None, log=None, state="~/.config/coin-dojo/settings-state.json", tzdir=TZ_DIR, tool="timedatectl", localtime="/etc/localtime"):
        self.runner = runner or NM._subprocess
        self.log = log or Log(None)
        self.state = os.path.expanduser(state)
        self.tzdir, self.tool, self.localtime = tzdir, tool, localtime
        self._cur = None
        self._zones = None

    def current(self):
        """The zone now ("" when unknown): timedatectl, else the /etc/localtime link."""
        if self._cur is None:
            rc, out, _e = self.runner([self.tool, "show", "-p", "Timezone", "--value"], 10)
            cur = (out or "").strip() if rc == 0 else ""
            if not cur:
                try:
                    cur = os.path.realpath(self.localtime).split("/zoneinfo/", 1)[1]
                except (IndexError, OSError):
                    cur = ""
            self._cur = cur if tz_error(cur) is None else ""
        return self._cur

    def zones(self):
        """Every zone with a file under the zoneinfo dir, and (when timedatectl answers) on its list too."""
        if self._zones is None:
            have = set()
            for root, dirs, files in os.walk(self.tzdir):
                dirs[:] = [d for d in dirs if d not in ("posix", "right")]
                for f in files:
                    z = os.path.relpath(os.path.join(root, f), self.tzdir)
                    if "/" in z and tz_error(z) is None:
                        have.add(z)
            rc, out, _e = self.runner([self.tool, "list-timezones"], 15)
            listed = {x.strip() for x in (out or "").splitlines() if x.strip()} if rc == 0 else set()
            self._zones = (have & listed) if listed else have
        return self._zones

    def valid(self, zone):
        return tz_error(zone) is None and zone in self.zones()

    def regions(self):
        return sorted({z.split("/", 1)[0] for z in self.zones()} - {"Etc"})

    def cities(self, region):
        return sorted((tz_pretty(z.split("/", 1)[1]), z) for z in self.zones() if z.split("/", 1)[0] == region)

    def clock(self):
        """Local time in the current zone for the status card, '' when it cannot be told."""
        try:
            import datetime
            import zoneinfo
            return datetime.datetime.now(zoneinfo.ZoneInfo(self.current())).strftime("%a %H:%M")
        except Exception:  # noqa: BLE001  (no tz data, unknown zone: show nothing rather than fail the page)
            return ""

    def set(self, zone):
        err = tz_error(zone)
        if err or not self.valid(zone):
            return False, err or "NOT A TIME ZONE ON THIS CABINET"
        argv = [self.tool, "set-timezone", zone]                       # one argument, argv list, no shell, no sudo
        rc, out, e = self.runner(argv, 30)
        self.log.write("timezone", argv=argv, rc=rc, err=(e or "").strip()[:300] if rc else "")
        if rc != 0:
            lines = [x for x in ((e or "") + "\n" + (out or "")).splitlines() if x.strip()]
            return False, lines[-1] if lines else "set-timezone failed (exit %d)" % rc
        self._cur = zone
        try:
            save_state(self.state, timezone=zone)
        except OSError as ex:
            self.log.write("timezone", state_err=str(ex)[:300])
            return True, "TIME ZONE IS NOW %s  -  NOT SAVED: A DEPLOY MAY CHANGE IT BACK" % tz_pretty(zone)
        return True, "TIME ZONE IS NOW %s" % tz_pretty(zone)


# ================================================================================================
# Benchmark mode (#189): the FPS counter in games. The cabinet wins (the maintainer 2026-11-17): the app writes the state file,
# deploys read it (roles cabinet_tweaks + dreamcast) and hk_benchmark_mode is only the first-install default.
# ================================================================================================
BENCH_STATE = "~/.config/coin-dojo/settings-state.json"          # {"benchmark_mode": true|false}; only this app writes
# The values to write, published by the roles that own them (one source: their defaults): {file, format retroarch|ini,
# [section], default, on {key: value}, off {key: value}}.
BENCH_KEYS = ("~/.config/coin-dojo/benchmark-retroarch.json", "~/.config/coin-dojo/benchmark-flycast.json")
BENCH_KEY_RE = re.compile(r"[A-Za-z0-9_.]{1,80}")
BENCH_VALUE_RE = re.compile(r"[A-Za-z0-9_.\-]{0,40}")


class BenchError(RuntimeError):
    pass


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, RecursionError):   # #203: deep nesting raises RecursionError on some pythons
        return None


def bench_spec_error(spec):
    """None when spec is a usable keys file (see BENCH_KEYS), else why not. Keys and values are a narrow charset: the
    app writes them into config files line by line."""
    if not isinstance(spec, dict):
        return "not a JSON object"
    if spec.get("format") not in ("retroarch", "ini"):
        return "format must be retroarch or ini"
    if not isinstance(spec.get("file"), str) or not os.path.isabs(spec["file"]):
        return "file must be an absolute path"
    if spec["format"] == "ini" and not (isinstance(spec.get("section"), str) and BENCH_KEY_RE.fullmatch(spec["section"])):
        return "ini needs a section"
    on, off = spec.get("on"), spec.get("off")
    if not isinstance(on, dict) or not isinstance(off, dict) or not on or set(on) != set(off):
        return "on and off must name the same keys"
    for d in (on, off):
        for k, v in d.items():
            if not BENCH_KEY_RE.fullmatch(k) or not isinstance(v, str) or not BENCH_VALUE_RE.fullmatch(v):
                return "bad key or value %r" % k
    return None


def _split_lines(text):
    return text.splitlines(keepends=True)


def _nl(lines):
    """The file's own line ending (retroarch.cfg / emu.cfg are LF; keep CRLF if someone saved one so)."""
    for ln in lines:
        if ln.endswith("\r\n"):
            return "\r\n"
    return "\n"


def set_retroarch_keys(text, values):
    """retroarch.cfg: for each key, the LAST `key = ...` line becomes `key = "value"` (as Ansible's lineinfile does);
    a missing key is appended. Every other line stays byte for byte."""
    lines = _split_lines(text)
    nl = _nl(lines)
    for k, v in values.items():
        rx = re.compile(r"^%s\s*=" % re.escape(k))
        new = '%s = "%s"' % (k, v)
        hits = [i for i, ln in enumerate(lines) if rx.match(ln)]
        if hits:
            i = hits[-1]
            end = lines[i][len(lines[i].rstrip("\r\n")):] or nl
            lines[i] = new + end
        else:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += nl
            lines.append(new + nl)
    return "".join(lines)


def set_ini_keys(text, section, values):
    """emu.cfg (flycast): `key = value` under [section] (as community.general.ini_file with spaces); a missing key goes
    after the section's last line, a missing section at the end. Every other line stays byte for byte."""
    lines = _split_lines(text)
    nl = _nl(lines)
    head = re.compile(r"^\s*\[([^\]]*)\]")
    for k, v in values.items():
        rx = re.compile(r"^\s*%s\s*=" % re.escape(k))
        new = "%s = %s" % (k, v)
        start = next((i for i, ln in enumerate(lines) if head.match(ln) and head.match(ln).group(1).strip() == section), None)
        if start is None:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += nl
            if lines and lines[-1].strip():
                lines.append(nl)
            lines += ["[%s]%s" % (section, nl), new + nl]
            continue
        end = next((i for i in range(start + 1, len(lines)) if head.match(lines[i])), len(lines))
        hit = next((i for i in range(start + 1, end) if rx.match(lines[i])), None)
        if hit is not None:
            tail = lines[hit][len(lines[hit].rstrip("\r\n")):] or nl
            lines[hit] = new + tail
        else:
            last = max([i for i in range(start, end) if lines[i].strip()] or [start])
            if not lines[last].endswith("\n"):
                lines[last] += nl
            lines.insert(last + 1, new + nl)
    return "".join(lines)


def _write_atomic(path, text, new_mode=0o644):
    """Same dir temp file, fsync, rename; the old file's mode kept, new_mode for a new file (an emulator never sees
    half a file). The temp file is created 0600 (it may hold a login token) and gets the final mode before the rename."""
    d = os.path.dirname(path)
    tmp = os.path.join(d, ".%s.hk-tmp" % os.path.basename(path))
    try:
        mode = os.stat(path).st_mode & 0o7777
    except OSError:
        mode = new_mode
    try:
        os.unlink(tmp)                                  # a stale one from a crash
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def game_busy(procs=("retroarch", "flycast"), patterns=(), proc="/proc"):
    """-> busy() callable: trophiesd's game check (stdlib only), asked from inside ES-DE (this app's own line of
    processes is not a game). procs/patterns = the deploy guard's list (the entry file carries it); proc = the process
    table to read (tests point it at a fake one; no /proc = no ES-DE = busy)."""
    def busy():
        import trophiesd
        return trophiesd.game_running(proc=proc, procs=tuple(procs), patterns=tuple(patterns), me=os.getpid())
    busy.proc = proc                     # tests: which process table it reads
    return busy


class Benchmark:
    """State file + keys files -> on/off. busy() is True while a game runs (then nothing is written)."""

    def __init__(self, state=BENCH_STATE, keys=BENCH_KEYS, busy=None, log=None):
        self.state = os.path.expanduser(state)
        self.keys = [os.path.expanduser(k) for k in keys]
        self.busy = busy or game_busy()
        self.log = log or Log(None)

    def specs(self):
        """[(path, spec)] of the keys files that are there and valid; a bad one raises BenchError."""
        out = []
        for p in self.keys:
            spec = _read_json(p)
            if spec is None:
                if os.path.exists(p):
                    raise BenchError("BROKEN KEYS FILE %s" % os.path.basename(p))
                continue
            err = bench_spec_error(spec)
            if err:
                raise BenchError("BROKEN KEYS FILE %s: %s" % (os.path.basename(p), err))
            out.append((p, spec))
        return out

    def available(self):
        try:
            return bool(self.specs())
        except BenchError:
            return False

    def default(self):
        """The Ansible default (hk_benchmark_mode) the keys files carry; False when they say nothing."""
        try:
            specs = self.specs()
        except BenchError:
            specs = []
        return any(s.get("default") is True for _p, s in specs)

    def saved(self):
        """True/False from the state file, None when it is missing, malformed or has no bool."""
        st = _read_json(self.state)
        v = st.get("benchmark_mode") if isinstance(st, dict) else None
        return v if isinstance(v, bool) else None

    def current(self):
        v = self.saved()
        return self.default() if v is None else v

    def set(self, on):
        """Write the on/off values into every emulator config, then the state file. -> (ok, message)."""
        try:
            specs = self.specs()
        except BenchError as e:
            return False, str(e)
        if not specs:
            return False, "NOT SET UP YET: RUN A DEPLOY FIRST"
        if self.busy():
            self.log.write("benchmark", on=on, refused="game running")
            return False, "A GAME IS RUNNING: CLOSE IT FIRST"
        plan = []
        for _p, s in specs:
            f = s["file"]
            try:
                with open(f, encoding="utf-8", errors="surrogateescape", newline="") as fh:
                    old = fh.read()
            except FileNotFoundError:
                if s["format"] == "retroarch":
                    continue                              # RetroArch writes its file on first start; the deploy follows
                old = ""
            except OSError as e:
                return False, "CANNOT READ %s: %s" % (os.path.basename(f), e.strerror or e)
            vals = s["on" if on else "off"]
            new = set_retroarch_keys(old, vals) if s["format"] == "retroarch" else set_ini_keys(old, s["section"], vals)
            if new != old:
                plan.append((f, new))
        try:
            for f, new in plan:
                os.makedirs(os.path.dirname(f), exist_ok=True)
                _write_atomic(f, new)
            save_state(self.state, benchmark_mode=bool(on))
        except OSError as e:
            self.log.write("benchmark", on=on, err=str(e)[:300])
            return False, "WRITE FAILED: %s" % (e.strerror or e)
        self.log.write("benchmark", on=on, files=[f for f, _n in plan])
        return True, "SHOW FPS IS NOW %s" % ("ON" if on else "OFF")


def save_state(path, **values):
    """Merge values into the app's state file (other keys kept; a malformed file starts over). Dir 0700, file 0600."""
    st = _read_json(path)
    st = st if isinstance(st, dict) else {}
    st.update(values)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    _write_atomic(path, json.dumps(st, indent=2, sort_keys=True) + "\n", new_mode=0o600)


# ================================================================================================
# RetroAchievements (#171): on/off, casual/hardcore, log in / log out. The cabinet wins (the maintainer 2026-11-17): the app
# writes retroarch.cfg + flycast emu.cfg and the state file; roles retroarch122 + dreamcast read the state file on every
# deploy: "cheevos_enable" / "cheevos_hardcore" override hk_cheevos_enable / hk_cheevos_hardcore, and any
# "cheevos_login" (true = logged in here, false = logged out here) makes them skip the user name and the token.
# ================================================================================================
CHEEVOS_API = "https://retroachievements.org/dorequest.php"       # r=login2
CHEEVOS_HOST = "retroachievements.org"     # the only host a password goes to (#198); tests: allow_loopback (--test-hooks)
CHEEVOS_FILES = {"retroarch": "~/.config/retroarch/retroarch.cfg", "flycast": "~/.config/flycast/emu.cfg"}
CHEEVOS_UA = "coin-dojo-settings/1"
CHEEVOS_USER_RE = re.compile(r"[A-Za-z0-9_.\-]{1,32}")             # hk_cheevos_user's pattern
CHEEVOS_TOKEN_RE = re.compile(r"[A-Za-z0-9]{8,64}")
CHEEVOS_MAX_REPLY = 64 * 1024
# the keys each emulator reads (the same ones roles retroarch122 / dreamcast write)
RA_KEYS = dict(enable="cheevos_enable", hardcore="cheevos_hardcore_mode_enable", user="cheevos_username",
               token="cheevos_token", password="cheevos_password")
FLY_SECTION = "achievements"
FLY_KEYS = dict(enable="Enabled", hardcore="HardcoreMode", user="UserName", token="Token")


class CheevosError(RuntimeError):
    """A failed login; the text is safe to show and log (never the password)."""


def api_url_error(url, allow_loopback=False):
    """None when url may receive a password: https://retroachievements.org (default port, no user info) only (#198).
    allow_loopback (TEST ONLY: settings_app.py --test-hooks, never written by the role) adds plain http to this machine
    (the tests' fake_ra.py)."""
    try:
        p = urllib.parse.urlsplit(url or "")
        port = p.port
    except ValueError:
        return "BAD LOGIN ADDRESS"
    if allow_loopback and p.scheme == "http" and p.hostname in ("127.0.0.1", "localhost", "::1") and not p.username:
        return None
    if p.scheme != "https":
        return "THE LOGIN ADDRESS MUST BE HTTPS"
    if (p.hostname or "").lower() != CHEEVOS_HOST or port not in (None, 443) or p.username is not None:
        return "THE LOGIN ADDRESS MUST BE %s" % CHEEVOS_HOST.upper()
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an error: the password is never sent on to another place."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def password_forms(password):
    """The password as typed and as it travels URL-encoded (form body: quote_plus; quote, also with safe=''): every
    form an error text could echo (#198)."""
    if not password:
        return ()
    forms = [password, urllib.parse.quote_plus(password), urllib.parse.quote(password), urllib.parse.quote(password, safe="")]
    return tuple(sorted(set(forms), key=len, reverse=True))          # longest first: no half-redacted leftovers


def _reply_error(raw, password, fallback):
    try:
        doc = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        doc = None
    msg = doc.get("Error") if isinstance(doc, dict) else None
    msg = msg if isinstance(msg, str) and msg.strip() else fallback
    ascii_only = lambda s: "".join(c for c in s if 32 <= ord(c) < 127)
    forms = password_forms(password)
    msg = redact(ascii_only(redact(msg, forms)), forms + ((ascii_only(password),) if password else ()))   # before AND after the filter
    return msg.strip()[:120]


def _opener():
    """No proxy (#198: ProxyHandler({}) ignores *_proxy env vars and system settings), verified TLS, no redirects."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(),
                                       urllib.request.HTTPSHandler(context=ssl.create_default_context()))


def ra_login(api, user, password, timeout=20, allow_loopback=False):
    """POST r=login2 to RA's connect API -> (user as RA spells it, token). The password travels only in the request
    body (never the URL), over verified TLS, straight to retroachievements.org (no proxy); redirects are refused.
    Raises CheevosError with a showable message. allow_loopback: TEST ONLY (see api_url_error)."""
    err = api_url_error(api, allow_loopback)
    if err:
        raise CheevosError(err)
    body = urllib.parse.urlencode({"r": "login2", "u": user, "p": password}).encode("utf-8")
    req = urllib.request.Request(api, data=body, method="POST", headers={
        "User-Agent": CHEEVOS_UA, "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})
    opener = _opener()
    try:
        with opener.open(req, timeout=timeout) as r:
            raw = r.read(CHEEVOS_MAX_REPLY + 1)
    except urllib.error.HTTPError as e:
        raw = b""
        try:
            raw = e.read(CHEEVOS_MAX_REPLY) or b""
        except (OSError, http.client.HTTPException):
            pass
        raise CheevosError(_reply_error(raw, password, "RETROACHIEVEMENTS ANSWERED HTTP %d" % e.code)) from None
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:   # #198: bad status line,
        reason = getattr(e, "reason", None)                                                  # cut-off answer, ...
        kind = type(reason).__name__ if isinstance(reason, BaseException) else type(e).__name__
        raise CheevosError("NO CONNECTION TO RETROACHIEVEMENTS (%s)" % kind) from None
    if len(raw) > CHEEVOS_MAX_REPLY:
        raise CheevosError("RETROACHIEVEMENTS SENT AN ODD ANSWER (TOO LONG)")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):   # #203: a deeply nested answer must not crash the app
        raise CheevosError("RETROACHIEVEMENTS SENT AN ODD ANSWER") from None
    if not isinstance(doc, dict) or doc.get("Success") is not True:
        raise CheevosError(_reply_error(raw, password, "LOGIN FAILED"))
    token, name = doc.get("Token"), doc.get("User", user)
    if not isinstance(token, str) or not CHEEVOS_TOKEN_RE.fullmatch(token):
        raise CheevosError("RETROACHIEVEMENTS SENT NO USABLE TOKEN")
    if not isinstance(name, str) or not CHEEVOS_USER_RE.fullmatch(name):
        name = user
    return name, token


def retroarch_value(text, key):
    """The value RetroArch reads for key (the LAST `key = "value"` line), None when absent."""
    val = None
    for ln in (text or "").splitlines():
        m = re.match(r'^%s\s*=\s*"?(.*?)"?\s*$' % re.escape(key), ln)
        if m:
            val = m.group(1)
    return val


def ini_value(text, section, key):
    cur, val = None, None
    for ln in (text or "").splitlines():
        h = re.match(r"^\s*\[([^\]]*)\]", ln)
        if h:
            cur = h.group(1).strip()
            continue
        m = re.match(r"^\s*%s\s*=\s*(.*?)\s*$" % re.escape(key), ln)
        if m and cur == section:
            val = m.group(1)
    return val


class Cheevos:
    """RetroAchievements settings in retroarch.cfg + emu.cfg, the app's state file and the login call.
    busy() is True while a game runs (then nothing is written); login(api, user, password) -> (user, token)."""

    def __init__(self, files=None, state=BENCH_STATE, api=CHEEVOS_API, busy=None, log=None, login=None,
                 allow_loopback=False):
        files = dict(CHEEVOS_FILES, **(files or {}))
        self.ra, self.fly = os.path.expanduser(files["retroarch"]), os.path.expanduser(files["flycast"])
        self.state = os.path.expanduser(state)
        self.api = api or CHEEVOS_API
        self.busy = busy or game_busy()
        self.log = log or Log(None)
        self._login = login or (lambda api, u, p: ra_login(api, u, p, allow_loopback=allow_loopback))

    @staticmethod
    def _read(path):
        try:
            with open(path, encoding="utf-8", errors="surrogateescape", newline="") as fh:
                return fh.read()
        except OSError:
            return None

    def _saved(self, key):
        st = _read_json(self.state)
        v = st.get(key) if isinstance(st, dict) else None
        return v if isinstance(v, bool) else None

    def _flag(self, what):
        """State file first (the cabinet wins), else what the configs say now (the last deploy), else off."""
        v = self._saved("cheevos_" + what)
        if v is not None:
            return v
        ra = retroarch_value(self._read(self.ra), RA_KEYS[what])
        if ra is not None:
            return ra == "true"
        fly = ini_value(self._read(self.fly), FLY_SECTION, FLY_KEYS[what])
        return fly == "yes" if fly is not None else False

    def enabled(self):
        return self._flag("enable")

    def hardcore(self):
        return self._flag("hardcore")

    def account(self):
        """The user logged in (a user name AND a token in retroarch.cfg, else in emu.cfg), or None. The token itself
        never leaves this method."""
        ra = self._read(self.ra)
        user, tok = retroarch_value(ra, RA_KEYS["user"]), retroarch_value(ra, RA_KEYS["token"])
        if not (user and tok):
            fly = self._read(self.fly)
            user, tok = ini_value(fly, FLY_SECTION, FLY_KEYS["user"]), ini_value(fly, FLY_SECTION, FLY_KEYS["token"])
        return user if user and tok else None

    def _apply(self, ra_vals, fly_vals, state, event, **logkw):
        """Write the keys into both configs (only those lines; a missing file is created 0600), then the state file."""
        if self.busy():
            self.log.write(event, refused="game running", **logkw)
            return False, "A GAME IS RUNNING: CLOSE IT FIRST"
        plan = []
        for path, vals, fmt in ((self.ra, ra_vals, "retroarch"), (self.fly, fly_vals, "ini")):
            old = self._read(path)
            if old is None and os.path.exists(path):
                return False, "CANNOT READ %s" % os.path.basename(path)
            base = old or ""
            new = set_retroarch_keys(base, vals) if fmt == "retroarch" else set_ini_keys(base, FLY_SECTION, vals)
            if new != old:
                plan.append((path, new))
        try:
            for path, new in plan:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                _write_atomic(path, new, new_mode=0o600)
            save_state(self.state, **state)
        except OSError as e:
            self.log.write(event, err=(e.strerror or type(e).__name__)[:120], **logkw)
            return False, "WRITE FAILED: %s" % (e.strerror or e)
        self.log.write(event, files=[p for p, _n in plan], **logkw)
        return True, None

    def set_enable(self, on):
        ok, msg = self._apply({RA_KEYS["enable"]: "true" if on else "false"}, {FLY_KEYS["enable"]: "yes" if on else "no"},
                              dict(cheevos_enable=bool(on)), "cheevos_enable", on=bool(on))
        return ok, msg or "RETROACHIEVEMENTS ARE NOW %s" % ("ON" if on else "OFF")

    def set_hardcore(self, on):
        ok, msg = self._apply({RA_KEYS["hardcore"]: "true" if on else "false"},
                              {FLY_KEYS["hardcore"]: "yes" if on else "no"},
                              dict(cheevos_hardcore=bool(on)), "cheevos_hardcore", on=bool(on))
        return ok, msg or "MODE IS NOW %s" % ("HARDCORE" if on else "CASUAL (SOFTCORE)")

    def login(self, user, password):
        """Trade user + password for a token; write user + token (never the password). A failure writes nothing."""
        if not CHEEVOS_USER_RE.fullmatch(user or ""):
            return False, "USER NAME: ONLY A-Z, 0-9, _ . AND -"
        if not password:
            return False, "TYPE THE PASSWORD FIRST"
        if self.busy():                                   # before the network call: no token fetched for nothing
            self.log.write("cheevos_login", user=user, refused="game running")
            return False, "A GAME IS RUNNING: CLOSE IT FIRST"
        try:
            name, token = self._login(self.api, user, password)
        except CheevosError as e:
            msg = redact(str(e), password_forms(password))
            self.log.write("cheevos_login", user=user, ok=False, err=msg)
            return False, "LOGIN FAILED: %s" % msg
        if not CHEEVOS_TOKEN_RE.fullmatch(token or "") or not CHEEVOS_USER_RE.fullmatch(name or ""):
            self.log.write("cheevos_login", user=user, ok=False, err="bad token or user in the answer")
            return False, "LOGIN FAILED: RETROACHIEVEMENTS SENT NO USABLE TOKEN"
        ok, msg = self._apply({RA_KEYS["user"]: name, RA_KEYS["token"]: token, RA_KEYS["password"]: ""},
                              {FLY_KEYS["user"]: name, FLY_KEYS["token"]: token},
                              dict(cheevos_login=True), "cheevos_login", user=name, ok=True)
        return ok, msg or "LOGGED IN AS %s" % name

    def logout(self):
        ok, msg = self._apply({RA_KEYS["user"]: "", RA_KEYS["token"]: "", RA_KEYS["password"]: ""},
                              {FLY_KEYS["user"]: "", FLY_KEYS["token"]: ""},
                              dict(cheevos_login=False), "cheevos_logout")
        return ok, msg or "LOGGED OUT: NO ACCOUNT ON THIS CABINET"


# ================================================================================================
# Page template (#171): one home row + one menu screen. Subclass and add it to settings_app.build(); App does the
# stack, input, busy frame and toast. A write action returns (ok, message) through app.run(busy_text, fn).
# ================================================================================================
class Page:
    id = "page"                     # screen id (also the crumb under SETTINGS)
    title = "PAGE"                  # screen title

    def label(self):
        """The row on the home screen (may carry the current value)."""
        return self.title

    def cards(self):
        """Status cards on top of the screen: [dict(caption, value, hot[, rcaption, rvalue, rhot])]."""
        return []

    def text(self):
        """One line under the cards, or None."""
        return None

    def items(self):
        """[(label, what)]: what is passed to choose(); 'back' goes back."""
        return [("BACK", "back")]

    def state(self):
        return None

    def choose(self, app, what):
        pass

    def submit(self, app, screen, kb):
        """DONE on a keyboard this page pushed (app.push_keyboard(..., page=self))."""

    def confirm(self, app, what, arg):
        """YES on a confirm screen this page pushed (app.push_confirm(..., ("page", self.id, what), arg))."""


class BenchPage(Page):
    """SHOW FPS (#189, renamed #285: it only ever was an FPS counter). The internal names stay (hk_benchmark_mode, the
    state-file key benchmark_mode, BENCH_*, id "bench") so a deployed cabinet's saved choice needs no migration."""
    id, title = "bench", "SHOW FPS"

    def __init__(self, bench):
        self.bench = bench

    def state(self):
        return "ON" if self.bench.current() else "OFF"

    def label(self):
        return "SHOW FPS:  %s" % self.state()

    def cards(self):
        on = self.bench.current()
        return [dict(caption="SHOW FPS", value=self.state(), hot=on, rcaption="CHANGES APPLY",
                     rvalue="FROM THE NEXT GAME")]

    def text(self):
        return ("FRAMES-PER-SECOND COUNTER IN THE CORNER OF RETROARCH AND DREAMCAST GAMES" if self.bench.available()
                else "NOT SET UP YET: THE NEXT DEPLOY ADDS IT")

    def items(self):
        on = self.bench.current()
        return [("TURN OFF" if on else "TURN ON", "bench_off" if on else "bench_on"), ("BACK", "back")]

    def choose(self, app, what):
        if what in ("bench_on", "bench_off"):
            on = what == "bench_on"
            app.run("TURNING THE FPS COUNTER %s" % ("ON" if on else "OFF"), lambda: self.bench.set(on))


class TimeZonePage(Page):
    """TIME ZONE (#206): a short list, MORE = region -> city. The sub-screens are static menus of this page."""
    id, title = "tz", "TIME ZONE"

    def __init__(self, tz):
        self.tz = tz

    def state(self):
        return None

    def label(self):
        return "TIME ZONE:  %s" % (tz_pretty(self.tz.current()) if self.tz.current() else "UNKNOWN")

    def cards(self):
        cur = self.tz.current()
        return [dict(caption="TIME ZONE", value=tz_pretty(cur) if cur else "UNKNOWN", hot=bool(cur),
                     rcaption="LOCAL TIME", rvalue=self.tz.clock() or "-")]

    def text(self):
        return "THE CABINET KEEPS YOUR PICK; A DEPLOY DOES NOT CHANGE IT BACK"

    def items(self):
        cur = self.tz.current()
        rows = [(lab + ("   (NOW)" if z == cur else ""), "z:" + z) for lab, z in TZ_PRESETS]
        return rows + [("MORE ZONES ...", "more"), ("BACK", "back")]

    def _push(self, app, sid, title, items):
        app.stack.append(dict(kind="menu", id=sid, title=title, sel=0, items=items + [("BACK", "back")],
                              page=self, static=True))

    def choose(self, app, what):
        if what == "more":
            self._push(app, "tz_regions", "TIME ZONE / REGION", [(r.upper(), "r:" + r) for r in self.tz.regions()])
        elif what.startswith("r:"):
            region = what[2:]
            cur = self.tz.current()
            self._push(app, "tz_cities", "TIME ZONE / %s" % region.upper(),
                       [(lab + ("   (NOW)" if z == cur else ""), "z:" + z) for lab, z in self.tz.cities(region)])
        elif what.startswith("z:"):
            zone = what[2:]
            app.run("SETTING THE TIME ZONE", lambda: self.tz.set(zone), back_to=self.id)


class CheevosPage(Page):
    """RETROACHIEVEMENTS (#171): on/off, casual/hardcore, log in / log out."""
    id, title = "cheevos", "RETROACHIEVEMENTS"

    def __init__(self, cheevos):
        self.ch = cheevos

    def state(self):
        return "ON" if self.ch.enabled() else "OFF"

    def label(self):
        user = self.ch.account()
        return "RETROACHIEVEMENTS:  %s%s" % (self.state(), "  -  %s" % user if user else "")

    def cards(self):
        user, on, hc = self.ch.account(), self.ch.enabled(), self.ch.hardcore()
        return [dict(caption="ACCOUNT", value=user or "NOT LOGGED IN", hot=bool(user),
                     rcaption="RETROACHIEVEMENTS", rvalue="ON" if on else "OFF", rhot=on),
                dict(caption="MODE", value="HARDCORE" if hc else "CASUAL (SOFTCORE)", hot=hc,
                     rcaption="CHANGES APPLY", rvalue="FROM THE NEXT GAME")]

    def text(self):
        if self.ch.hardcore():
            return "HARDCORE: NO SAVE STATES, REWIND OR CHEATS; UNLOCKS COUNT AS HARDCORE"
        return "CASUAL: SAVE STATES AND REWIND ALLOWED; UNLOCKS COUNT AS SOFTCORE"

    def items(self):
        on, hc, user = self.ch.enabled(), self.ch.hardcore(), self.ch.account()
        return [("TURN OFF" if on else "TURN ON", "ch_off" if on else "ch_on"),
                ("SWITCH TO CASUAL (SOFTCORE)" if hc else "SWITCH TO HARDCORE", "ch_casual" if hc else "ch_hardcore"),
                ("LOG OUT %s" % user, "ch_logout") if user else ("LOG IN", "ch_login"),
                ("BACK", "back")]

    def choose(self, app, what):
        if what in ("ch_on", "ch_off"):
            on = what == "ch_on"
            app.run("TURNING RETROACHIEVEMENTS %s" % ("ON" if on else "OFF"), lambda: self.ch.set_enable(on))
        elif what in ("ch_hardcore", "ch_casual"):
            on = what == "ch_hardcore"
            app.run("SWITCHING TO %s" % ("HARDCORE" if on else "CASUAL"), lambda: self.ch.set_hardcore(on))
        elif what == "ch_login":
            app.push_keyboard("ch_user", "RETROACHIEVEMENTS USER", "text", text=self.ch.account() or "", page=self,
                              max_len=32, hint="YOUR RETROACHIEVEMENTS.ORG USER NAME  (A-Z, 0-9, _ . -)")
        elif what == "ch_logout":
            user = self.ch.account()
            app.push_confirm("LOG OUT %s?" % user, "THE LOGIN TOKEN IS REMOVED FROM RETROARCH AND FLYCAST.",
                             ("page", self.id, "logout"), None)

    def submit(self, app, s, kb):
        if s["id"] == "ch_user":
            if not CHEEVOS_USER_RE.fullmatch(kb.text):
                s["error"] = "ONLY A-Z, 0-9, _ . AND -  (1-32)" if kb.text else "TYPE YOUR USER NAME FIRST"
                return
            app.push_keyboard("ch_password", "PASSWORD FOR %s" % kb.text, "password", page=self, max_len=128,
                              hint="PASSWORD  -  SENT ONCE TO RETROACHIEVEMENTS.ORG, NEVER STORED")
            app.top["user"] = kb.text
        elif s["id"] == "ch_password":
            user, pw = s["user"], kb.text
            if not pw:
                s["error"] = "TYPE THE PASSWORD FIRST"
                return
            kb.text, kb.show = "", False                  # gone from the screen whatever happens (retype on a failure)
            app.run("LOGGING IN AS %s" % user, lambda: self.ch.login(user, pw), back_to=self.id)

    def confirm(self, app, what, arg):
        if what == "logout":
            app.run("LOGGING OUT", self.ch.logout)


class Attract:
    """ATTRACT MODE (#285): the choices live in the state file (attract_enabled / attract_minutes / attract_type) and win
    over hk_attract_* on every deploy (the cabinet wins, like #189); attract_apply.py writes them into ES-DE's file."""

    def __init__(self, entry, state, cfg=None, spawn=None, log=None):
        self.entry, self.state, self._cfg, self.log = entry, os.path.expanduser(state), cfg, log or Log(None)
        self.spawn = spawn or self._spawn

    def cfg(self):
        c = self._cfg if self._cfg is not None else AA.load_config(self.entry)
        return dict(c, state=self.state)

    @staticmethod
    def _spawn(argv):
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)

    def current(self):
        return AA.choices(self.cfg())

    def pending(self):
        return AA.pending(self.cfg())

    def save(self, **kw):
        try:
            save_state(self.state, **kw)
        except OSError as e:
            return False, "WRITE FAILED: %s" % (e.strerror or e)
        self.log.write("attract", **kw)
        return True, "SAVED"

    def set_enabled(self, on):
        return self.save(attract_enabled=bool(on))

    def set_minutes(self, m):
        if m not in AA.MINUTES:
            return False, "NOT A CHOICE"
        return self.save(attract_minutes=m, attract_enabled=True)

    def set_type(self, t):
        if t not in AA.TYPES:
            return False, "NOT A CHOICE"
        return self.save(attract_type=t)

    def apply(self):
        """Detached: the app quits, then the helper writes ES-DE's file and restarts ES-DE."""
        c = self.cfg()
        if AA.game_running(c) or AA.fightcade_running(c):
            return False, "A GAME IS RUNNING - APPLY LATER"
        self.spawn([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "attract_apply.py"),
                    "apply", "--delay", "3", self.entry or ""])
        return True, "ES-DE RELOADS"


class AttractPage(Page):
    """ATTRACT MODE (#285): ON/OFF, START AFTER 5/10/30/60 MIN, SHOW video/slideshow/dim/black."""
    id, title = "attract", "ATTRACT MODE"
    SHOWN = {"video": "VIDEO", "slideshow": "SLIDESHOW", "dim": "DIM", "black": "BLACK"}

    def __init__(self, attract):
        self.at = attract

    def state(self):
        return "ON" if self.at.current()[0] else "OFF"

    def label(self):
        return "ATTRACT MODE:  %s" % self.state()

    def cards(self):
        on, m, _t = self.at.current()
        return [dict(caption="ATTRACT MODE", value=self.state(), hot=on, rcaption="STARTS AFTER",
                     rvalue=("%d MIN" % m) if on else "-")]

    def text(self):
        if self.at.pending():
            return "SAVED - APPLIES WHEN ES-DE RELOADS (APPLY NOW BELOW)"
        return "SHOWS %s AFTER THE CABINET IS LEFT ALONE" % self.SHOWN[self.at.current()[2]]

    def items(self):
        on, m, t = self.at.current()
        rows = [("TURN OFF" if on else "TURN ON", "off" if on else "on"),
                ("START AFTER:  %d MIN" % m, "minutes"), ("SHOW:  %s" % self.SHOWN[t], "type")]
        if self.at.pending():
            rows.append(("APPLY NOW  (RELOADS ES-DE)", "apply"))
        return rows + [("BACK", "back")]

    def _menu(self, app, sid, title, rows):
        app.stack.append(dict(kind="menu", id=sid, title=title, sel=0, items=rows + [("BACK", "back")], page=self,
                              static=True))

    def _saved(self, app, ok, msg):
        app.refresh_pages()
        app.say(ok, msg)
        if ok:
            while len(app.stack) > 1 and app.top["id"] != self.id:
                app.stack.pop()
            self._ask(app)

    def _ask(self, app):
        app.push_confirm("APPLY NOW?", "ES-DE RELOADS, BACK TO THE CAROUSEL IN A FEW SECONDS", ("page", self.id, "reload"), None)

    def choose(self, app, what):
        _on, m, t = self.at.current()
        if what in ("on", "off"):
            self._saved(app, *self.at.set_enabled(what == "on"))
        elif what == "minutes":
            self._menu(app, "attract_min", "START AFTER",
                       [("%d MIN%s" % (x, "   (NOW)" if x == m else ""), "m:%d" % x) for x in AA.MINUTES])
        elif what == "type":
            self._menu(app, "attract_type", "SHOW",
                       [(self.SHOWN[x] + ("   (NOW)" if x == t else ""), "t:" + x) for x in AA.TYPES])
        elif what.startswith("m:") and what[2:].isdigit():
            self._saved(app, *self.at.set_minutes(int(what[2:])))
        elif what.startswith("t:"):
            self._saved(app, *self.at.set_type(what[2:]))
        elif what == "apply":
            self._ask(app)

    def confirm(self, app, what, arg):
        if what == "reload":
            ok, msg = self.at.apply()
            app.say(ok, msg)
            if ok:
                app.quit = True

class PasswordPage(Page):
    """PASSWORD (#271): type a new password twice (masked); sets the Linux and the network folder's password."""
    id, title = "pw", "PASSWORD"          # not "password": that id is the Wi-Fi password keyboard

    def __init__(self, pw, user=None):
        self.pw = pw
        self.user = user or os.environ.get("USER") or "coindojo"

    def label(self):
        return "PASSWORD"

    def cards(self):
        return [dict(caption="LOGIN", value=self.user, hot=True, rcaption="ALSO CHANGES", rvalue="NETWORK FOLDER")]

    def text(self):
        return "ONE PASSWORD FOR THE CABINET LOGIN AND THE NETWORK FOLDER  -  AT LEAST %d CHARACTERS, NOT %s" % (PW_MIN, PW_DEFAULT)

    def items(self):
        return [("CHANGE PASSWORD", "pw_change"), ("BACK", "back")]

    def choose(self, app, what):
        if what == "pw_change":
            app.push_keyboard("pw_new", "NEW PASSWORD", "password", page=self, max_len=PW_MAX,
                              hint="AT LEAST %d CHARACTERS, NOT %s  -  NEVER SHOWN OR SAVED" % (PW_MIN, PW_DEFAULT))

    def submit(self, app, s, kb):
        if s["id"] == "pw_new":
            err = password_error(kb.text)
            if err:
                s["error"] = err
                return
            app.push_keyboard("pw_again", "TYPE IT AGAIN", "password", page=self, max_len=PW_MAX,
                              hint="THE SAME PASSWORD ONCE MORE")
            app.top["first"] = kb.text
        elif s["id"] == "pw_again":
            first, pw = s.pop("first", ""), kb.text
            if not pw:
                s["first"] = first
                s["error"] = "TYPE THE PASSWORD AGAIN"
                return
            kb.text, kb.show = "", False                    # gone from the screen whatever happens
            below = app.stack[-2]
            below["kb"].text = ""
            if pw != first:
                app.pop()                                   # back to the first field, empty
                app.top["error"] = "THE TWO PASSWORDS DIFFER  -  START AGAIN"
                return
            app.run("CHANGING THE PASSWORD", lambda: self.pw.set(pw), back_to=self.id)


# ================================================================================================
# GAME LISTS (#281): rebuild the main screen's lists (MUST PLAY, GENRES, SERIES, DECADES, SYSTEMS) on the cabinet from
# its own scraped gamelists (lists_rebuild.py, run here as a child process so a crash cannot take the app down), and the
# two sizes it reads: MUST PLAY per system and the MUST PLAY GENRES minimum stars (saved in the state file; a deploy's
# hk_must_play_* stay the deploy defaults). The personal edition refuses: its curated lists are made on the Mac.
class Lists:
    def __init__(self, entry=None, state=BENCH_STATE, runner=None, spawn=None, log=None, script=None):
        import lists_rebuild as lr
        self.lr = lr
        self.entry, self.state = entry, os.path.expanduser(state)
        self.script = script or os.path.join(os.path.dirname(os.path.abspath(__file__)), "lists_rebuild.py")
        self.runner = runner or NM._subprocess
        self.spawn = spawn or self._spawn
        self.log = log or Log(None)

    @staticmethod
    def _spawn(argv):
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)

    def cfg(self):
        try:
            return self.lr.load_config(self.entry, dict(state=self.state))
        except self.lr.ListsError:                       # fails closed (#281 M1): a bad or missing entry = managed
            return dict(self.lr.DEFAULTS, state=self.state)

    def managed(self):
        """True in the personal edition: the maintainer's curated lists are made on the Mac, never here."""
        return self.cfg()["edition"] != "community"

    def sizes(self):
        return self.lr.chosen(dict(state=self.state))

    def last(self):
        v = self.lr.read_state(self.state).get("lists_last")
        return v if isinstance(v, dict) else {}

    def set_size(self, top):
        if top not in self.lr.SIZES:
            return False, "PICK ONE OF 10, 15, 25, 50"
        save_state(self.state, must_play_top=top)
        return True, "MUST PLAY: %d PER SYSTEM" % top

    def set_stars(self, stars):
        if stars not in self.lr.STARS:
            return False, "PICK ONE OF 4.0, 4.5, 5.0"
        save_state(self.state, must_play_genre_min_stars=stars)
        return True, "MUST PLAY GENRES: %.1f STARS AND UP" % stars

    def rebuild(self):
        """-> (ok, message, summary dict or None). The child changes nothing unless everything checked out."""
        if self.managed():
            return False, "MANAGED FROM THE MAC (MAKE DEPLOY-COLLECTIONS)", None
        argv = [sys.executable, self.script, "rebuild"] + ([self.entry] if self.entry else [])
        rc, out, err = self.runner(argv, 1500)
        self.log.write("lists", argv=argv[1:], rc=rc)
        if rc == 0:
            try:
                res = json.loads([ln for ln in (out or "").splitlines() if ln.strip().startswith("{")][-1])
            except (IndexError, ValueError):
                return False, "THE REBUILD ANSWERED NOTHING", None
            return True, "GAME LISTS REBUILT", res
        lines = [x for x in (err or "").splitlines() if x.strip()]
        return False, (lines[-1].strip().upper()[:90] if lines else "REBUILD FAILED (EXIT %d)" % rc), None

    def reload(self):
        """Ask for an ES-DE restart a moment after the app has closed (the detached child waits, then guards and kills)."""
        if self.managed():
            return False, "MANAGED FROM THE MAC"
        self.spawn([sys.executable, self.script, "reload", "--delay", "3"] + ([self.entry] if self.entry else []))
        return True, "ES-DE RELOADS IN A MOMENT"


def lists_summary(res):
    return "%d GAMES  -  MUST PLAY %d  -  GENRES %d  -  DECADES %d" % (
        res.get("games", 0), res.get("must_play", 0), res.get("genres", 0), res.get("decades", 0))


class ListsPage(Page):
    """GAME LISTS (#281): REBUILD, MUST PLAY SIZE, MUST PLAY GENRES MIN STARS, RELOAD ES-DE."""
    id, title = "lists", "GAME LISTS"

    def __init__(self, lists):
        self.ls = lists

    def label(self):
        return "GAME LISTS"

    def cards(self):
        if self.ls.managed():
            return [dict(caption="GAME LISTS", value="MANAGED FROM THE MAC", hot=False)]
        top, stars = self.ls.sizes()
        last = self.ls.last()
        return [dict(caption="MUST PLAY", value="%d PER SYSTEM" % top, hot=True, rcaption="GENRES FROM", rvalue="%.1f STARS" % stars),
                dict(caption="LAST REBUILD", value=lists_summary(last) if last else "NEVER", hot=bool(last))]

    def text(self):
        if self.ls.managed():
            return "THE LISTS ARE MADE ON THE MAC  -  NOTHING HERE CAN OVERWRITE THEM"
        return "SCRAPE YOUR GAMES FIRST, THEN REBUILD  -  YOUR FAVORITES AND PLAY COUNTS STAY"

    def items(self):
        if self.ls.managed():
            return [("REBUILD  (MANAGED FROM THE MAC)", "refuse"), ("BACK", "back")]
        top, stars = self.ls.sizes()
        return [("REBUILD GAME LISTS", "rebuild"), ("MUST PLAY SIZE:  %d PER SYSTEM" % top, "size"),
                ("MUST PLAY GENRES MIN STARS:  %.1f" % stars, "stars"), ("RELOAD ES-DE", "reload"), ("BACK", "back")]

    def _push(self, app, sid, title, items):
        app.stack.append(dict(kind="menu", id=sid, title=title, sel=0, items=items + [("BACK", "back")], page=self, static=True))

    def choose(self, app, what):
        top, stars = self.ls.sizes()
        if what == "refuse":
            app.say(False, "MANAGED FROM THE MAC (MAKE DEPLOY-COLLECTIONS)")
        elif what == "rebuild":
            app.queue("REBUILDING GAME LISTS  -  ABOUT A MINUTE", lambda: self._rebuilt(app))
        elif what == "size":
            self._push(app, "lists_size", "MUST PLAY SIZE", [("%d PER SYSTEM%s" % (n, "   (NOW)" if n == top else ""), "s:%d" % n)
                                                             for n in self.ls.lr.SIZES])
        elif what == "stars":
            self._push(app, "lists_stars", "MUST PLAY GENRES MIN STARS",
                       [("%.1f STARS AND UP%s" % (n, "   (NOW)" if n == stars else ""), "m:%.1f" % n) for n in self.ls.lr.STARS])
        elif what.startswith("s:"):
            app.run("SAVING", lambda: self.ls.set_size(int(what[2:])), back_to=self.id)
        elif what.startswith("m:"):
            app.run("SAVING", lambda: self.ls.set_stars(float(what[2:])), back_to=self.id)
        elif what == "reload":
            app.push_confirm("RELOAD ES-DE?", "THIS APP CLOSES AND ES-DE RESTARTS WITH THE CURRENT LISTS.", ("page", self.id, "reload"), None)

    def _rebuilt(self, app):
        ok, msg, res = self.ls.rebuild()
        app.refresh_pages()
        app.say(ok, msg)
        if ok:
            app.push_confirm("GAME LISTS READY", lists_summary(res) + "  -  RELOAD ES-DE NOW?", ("page", self.id, "reload"), None)

    def confirm(self, app, what, arg):
        if what == "reload":
            ok, msg = self.ls.reload()
            app.say(ok, msg)
            if ok:
                app.quit = True


# ================================================================================================
# Screens
# ================================================================================================
# #224 (the maintainer 2026-11-18: "make sure settings menu shows quit is to back out all the way"): SELECT leaves the whole app
# from any page (settings_app.Input(select_quits=True) -> "exit"), then the ES-DE hook sends one Back to the carousel.
# The SETTINGS home, top to bottom (#285). docs/REFERENCE.md's table lists the same order (a test compares them).
HOME_ORDER = ("wifi", "name", "pw", "lists", "attract", "bench", "cheevos", "tz")
QUIT_HINT = (["SELECT"], "QUIT")


class App:
    """Screen stack. view() -> dict for the renderer. A slow call (scan/connect) is queued in self.pending
    with a 'busy' text; the frontend draws the busy frame, then calls run_pending()."""

    def __init__(self, nm, host, log=None, bench=None, pages=()):
        self.nm, self.host, self.bench = nm, host, bench
        self.pages = ([BenchPage(bench)] if bench is not None else []) + list(pages)
        self.log = log or Log(None)
        self.stack = []
        self.pending = None
        self.quit = False
        self.status = {}
        self.networks = []
        self.toast = None              # (kind 'ok'|'err', text)
        self.push_home()
        self.queue("READING WI-FI STATUS", self._refresh_status)

    # -- plumbing ------------------------------------------------------------------------------
    @property
    def top(self):
        return self.stack[-1]

    def queue(self, busy, fn):
        self.pending = (busy, fn)

    def run_pending(self):
        if self.pending:
            _busy, fn = self.pending
            self.pending = None
            fn()

    def say(self, ok, text):
        self.toast = ("ok" if ok else "err", text)

    def _refresh_status(self):
        try:
            self.status = self.nm.status()
        except Exception as e:  # noqa: BLE001
            self.status = dict(state=str(e))

    # -- screen constructors -----------------------------------------------------------------------
    def _home_items(self):
        """HOME_ORDER (#285): network and identity, the games, the screen, accounts, system. A page whose id is not
        listed goes last (before BACK)."""
        rows = {"wifi": ("WI-FI", "wifi"), "name": ("CABINET NAME", "name")}
        rows.update({p.id: (p.label(), ("page", p.id)) for p in self.pages})
        order = [i for i in HOME_ORDER if i in rows] + [i for i in rows if i not in HOME_ORDER]
        return [rows[i] for i in order] + [("BACK", "quit")]

    def push_home(self):
        self.stack = [dict(kind="menu", id="home", title="SETTINGS", sel=0, items=self._home_items())]

    def page(self, pid):
        return next((p for p in self.pages if p.id == pid), None)

    def push_page(self, page):
        self.stack.append(dict(kind="menu", id=page.id, title=page.title, sel=0, items=page.items(), page=page))

    def refresh_pages(self):
        """Re-read every page's labels/items (after a write, or anything else that changes what they show)."""
        for s in self.stack:
            if s["id"] == "home":
                s["items"] = self._home_items()
            elif s["kind"] == "menu" and s.get("page") and not s.get("static"):
                s["items"] = s["page"].items()
            else:
                continue
            s["sel"] = min(s["sel"], len(s["items"]) - 1)

    def run(self, busy, fn, back_to=None):
        """Page write: show busy, call fn() -> (ok, msg); on success go back to the screen back_to (if given)."""
        self.queue(busy, lambda: self._after_page(back_to, *fn()))

    def _after_page(self, back_to, ok, msg):
        if ok and back_to:
            while len(self.stack) > 1 and self.top["id"] != back_to:
                self.stack.pop()
        self.refresh_pages()
        self.say(ok, msg)

    def push_wifi(self):
        self.stack.append(dict(kind="menu", id="wifi", title="WI-FI", sel=0, items=[]))
        self.queue("SCANNING FOR NETWORKS", self._scan)

    def _scan(self):
        self._refresh_status()
        try:
            self.networks = self.nm.scan(rescan=True)
        except NMError as e:
            self.networks = []
            self.say(False, str(e))
        self._wifi_items()

    def _wifi_items(self):
        w = next((s for s in self.stack if s["id"] == "wifi"), None)
        if w is None:
            return
        items = [("SCAN AGAIN", "scan")]
        for n in self.networks:
            items.append((n.ssid, ("net", n)))
        items.append(("BACK", "back"))
        w["items"] = items
        w["sel"] = min(w["sel"], len(items) - 1)

    def push_network(self, n):
        items = []
        if n.in_use:
            pass
        elif n.uuid:
            items.append(("CONNECT", "up"))
        elif n.secured:
            items.append(("ENTER PASSWORD", "password"))
        else:
            items.append(("CONNECT (OPEN NETWORK)", "open"))
        if n.uuid:
            items.append(("FORGET THIS NETWORK", "forget"))
            if not n.in_use:
                items.append(("NEW PASSWORD", "password"))
        items.append(("BACK", "back"))
        self.stack.append(dict(kind="menu", id="network", title=n.ssid, sel=0, items=items, net=n))

    def push_keyboard(self, purpose, title, mode, text="", net=None, page=None, max_len=None, hint=None):
        self.stack.append(dict(kind="keyboard", id=purpose, title=title,
                               kb=Keyboard(mode, text, max_len or (63 if mode == "hostname" else 128)),
                               net=net, error=None, page=page, hint=hint))

    def push_confirm(self, title, text, action, arg):
        self.stack.append(dict(kind="menu", id="confirm", title=title, text=text, sel=1,
                               items=[("YES", ("yes", action, arg)), ("NO", "back")]))

    def pop(self):
        if len(self.stack) > 1:
            self.stack.pop()
        else:
            self.quit = True

    # -- input -------------------------------------------------------------------------------------
    def handle(self, action):
        if self.pending:
            return                                    # busy: ignore input until the call is done
        if action == "exit":
            self.quit = True
            return
        if isinstance(action, tuple) and self.top["kind"] != "keyboard":
            return                                    # typed text outside a text field
        self.toast = None if action not in ("up", "down", "left", "right") else self.toast
        if self.top["kind"] == "keyboard":
            self._keyboard(action)
        else:
            self._menu(action)

    def _menu(self, a):
        s = self.top
        n = len(s["items"])
        if a in ("up", "down", "tab") and n:
            s["sel"] = (s["sel"] + (-1 if a == "up" else 1)) % n
        elif a in ("back", "esc", "bksp"):
            self.pop()
        elif a in ("ok", "enter", "start") and n:
            self._choose(s, s["items"][s["sel"]][1])

    def _choose(self, s, what):
        if what == "quit":
            self.quit = True
        elif what == "back":
            self.pop()
        elif isinstance(what, tuple) and what[0] == "page":
            self.push_page(self.page(what[1]))
        elif s.get("page") is not None:
            s["page"].choose(self, what)
        elif what == "wifi":
            self.push_wifi()
        elif what == "name":
            self.push_keyboard("hostname", "CABINET NAME", "hostname", self.host.current())
        elif what == "scan":
            self.queue("SCANNING FOR NETWORKS", self._scan)
        elif isinstance(what, tuple) and what[0] == "net":
            self.push_network(what[1])
        elif what == "password":
            self.push_keyboard("password", "PASSWORD FOR %s" % s["net"].ssid, "password", net=s["net"])
        elif what == "open":
            net = s["net"]
            self.queue("CONNECTING TO %s" % net.ssid, lambda: self._after_connect(*self.nm.connect(net.ssid)))
        elif what == "up":
            net = s["net"]
            self.queue("CONNECTING TO %s" % net.ssid, lambda: self._after_connect(*self.nm.up(net.uuid, net.ssid)))
        elif what == "forget":
            net = s["net"]
            self.push_confirm("FORGET %s?" % net.ssid, "THE SAVED PASSWORD IS DELETED.", "forget", net)
        elif isinstance(what, tuple) and what[0] == "yes":
            _y, act, arg = what
            self.stack.pop()                                  # the confirm screen
            if isinstance(act, tuple) and act[0] == "page":
                self.page(act[1]).confirm(self, act[2], arg)
            elif act == "forget":
                self.queue("FORGETTING %s" % arg.ssid, lambda: self._after_forget(*self.nm.forget(arg.uuid, arg.ssid)))
            elif act == "hostname":
                self.queue("RENAMING THE CABINET", lambda: self._after_hostname(arg, *self.host.set(arg)))

    def _back_to_wifi(self):
        while len(self.stack) > 1 and self.top["id"] != "wifi":
            self.stack.pop()

    def _after_connect(self, ok, msg):
        if ok:
            self._back_to_wifi()
            self._scan()
            w = self.top                                   # the list re-sorts: put the cursor on the network in use
            for i, (_lab, what) in enumerate(w["items"]):
                if isinstance(what, tuple) and what[0] == "net" and what[1].in_use:
                    w["sel"] = i
            ip = self.status.get("ip")
            self.say(True, msg + ("  -  IP %s" % ip if ip else ""))
        else:
            if self.top["kind"] == "keyboard":            # back on the password keyboard: masked again (Security)
                self.top["kb"].show = self.top["kb"].mode != "password"
            self.say(False, msg)

    def _after_forget(self, ok, msg):
        self._back_to_wifi()
        if ok:
            self._scan()
        self.say(ok, msg)

    def _after_hostname(self, name, ok, msg):
        if ok:
            while len(self.stack) > 1:
                self.stack.pop()
            self.say(True, "%s  -  ON THE NETWORK: %s.local" % (msg, name))
        else:
            self.say(False, msg)

    def _keyboard(self, a):
        s = self.top
        kb = s["kb"]
        s["error"] = None
        if isinstance(a, tuple) and a[0] == "text":
            kb.type_text(a[1])
            return
        if a in ("up", "down", "left", "right"):
            kb.move(a)
        elif a == "ok":
            kb.press()
        elif a == "bksp":
            kb.backspace()
        elif a == "back":
            if kb.text:
                kb.backspace()
            else:
                self.pop()
        elif a == "esc":
            self.pop()
        elif a == "tab":
            kb.goto_action("done")
        elif a in ("start", "enter"):
            kb.done = True
        if kb.done:
            kb.done = False
            self._submit(s, kb)

    def _submit(self, s, kb):
        if s.get("page") is not None:
            s["page"].submit(self, s, kb)
        elif s["id"] == "password":
            net, pw = s["net"], kb.text
            if not pw:
                s["error"] = "TYPE THE PASSWORD FIRST"
                return
            self.queue("CONNECTING TO %s" % net.ssid, lambda: self._after_connect(*self.nm.connect(net.ssid, pw)))
        elif s["id"] == "hostname":
            err = hostname_error(kb.text)
            if err:
                s["error"] = err
                return
            if kb.text == self.host.current():
                s["error"] = "THAT IS ALREADY THE NAME"
                return
            self.push_confirm("RENAME THE CABINET?", "%s  ->  %s   (ON THE NETWORK: %s.local)"
                              % (self.host.current(), kb.text, kb.text), "hostname", kb.text)

    # -- what to draw ------------------------------------------------------------------------------
    def view(self):
        s = self.top
        v = dict(kind=s["kind"], id=s["id"], title=s["title"], busy=self.pending[0] if self.pending else None,
                 toast=self.toast, status=self.status, hostname=self.host.current())
        page = s.get("page")
        if page is not None:
            v["crumb"] = "SETTINGS" if s["kind"] == "menu" else "SETTINGS / %s" % page.title
        if s["kind"] == "menu":
            v.update(items=[lab for lab, _w in s["items"]], sel=s["sel"], text=s.get("text"),
                     nets=[w[1] if isinstance(w, tuple) and w[0] == "net" else None for _l, w in s["items"]],
                     net=s.get("net"), state=s.get("state"), cards=[])
            if page is not None and not s.get("static"):
                v.update(text=page.text(), state=page.state(), cards=page.cards())
            v["hints"] = [(["STICK"], "MOVE"), (["LK"], "SELECT"), (["MK"], "BACK")]
        else:
            kb = s["kb"]
            v.update(field=kb.shown(), mode=kb.mode, page=kb.page, rows=kb.rows(), cur=(kb.row, kb.col), error=s["error"],
                     length=len(kb.text), net=s.get("net"), hint=s.get("hint"))
            v["hints"] = [(["LK"], "TYPE"), (["MK"], "DELETE"), (["START"], "DONE")]
        v["quit"] = QUIT_HINT          # #224: on every screen; MK BACK stays one page up
        return v
