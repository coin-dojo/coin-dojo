#!/usr/bin/env python3
"""attract_apply.py: ATTRACT MODE settings (#285) for the SETTINGS app, stdlib only.

    attract_apply.py apply [--delay S] ENTRY   write ES-DE's ScreensaverTimer / ScreensaverType from the app's choices,
                                               then restart ES-DE (the deploy's way); refuses while a game runs

The choices live in the app's state file (attract_enabled, attract_minutes, attract_type) and win over the hk_attract_*
defaults on every deploy (role cabinet_tweaks reads the same keys). ES-DE keeps its settings in memory and saves them on a
clean exit, so the file is written first and ES-DE is then killed with -9 (it cannot save its old values over ours).
"""
import json
import os
import re
import stat
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
MINUTES = [5, 10, 30, 60]          # a list: the brand lint reads a 4-int tuple as RGBA
TYPES = ("video", "slideshow", "dim", "black")
STATE = os.path.join(HOME, ".config", "coin-dojo", "settings-state.json")
DEFAULTS = dict(
    es_settings=os.path.join(HOME, "ES-DE", "settings", "es_settings.xml"),
    timer_ms=600000, type="video", state=STATE, run_dir="", game_procs=["retroarch", "flycast"], game_patterns=[],
)
KILL = ["pkill", "-9", "-x", "AppRun"]       # fixed here, like the deploy handler: a config file never names a command
PROC = "/proc"                               # tests point this at a fake process table


class AttractError(Exception):
    """A refusal or failure with the line the screen shows."""


def load_config(entry=None, overrides=None):
    cfg = dict(DEFAULTS)
    if entry and os.path.isfile(entry):
        try:
            with open(entry, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError, RecursionError):
            data = {}
        if isinstance(data, dict) and isinstance(data.get("attract"), dict):
            cfg.update(data["attract"])
    cfg.update(overrides or {})
    t = cfg.get("timer_ms")                                # (#285 M2) a number, or a string of digits, else the default
    if isinstance(t, str) and t.isdigit() and len(t) <= 9:
        t = int(t)
    cfg["timer_ms"] = t if _int(t) is not None and 0 <= t <= 86400000 else DEFAULTS["timer_ms"]
    for k in ("game_procs", "game_patterns"):              # lists of strings only
        v = cfg.get(k)
        if not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            cfg[k] = list(DEFAULTS[k])
    if cfg.get("type") not in TYPES:
        cfg["type"] = DEFAULTS["type"]
    for k in ("es_settings", "state", "run_dir"):
        if not isinstance(cfg.get(k), str):
            cfg[k] = DEFAULTS[k]
    return cfg


def read_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def choices(cfg, st=None):
    """(enabled, minutes, type) as the app shows them: the saved choice, else what the config default says."""
    st = read_state(cfg["state"]) if st is None else st
    dflt = _int(cfg.get("timer_ms")) or 0
    enabled = st["attract_enabled"] if isinstance(st.get("attract_enabled"), bool) else dflt > 0
    m = _int(st.get("attract_minutes"))
    if m not in MINUTES:
        m = min(MINUTES, key=lambda x: abs(x * 60000 - dflt)) if dflt > 0 else 10
    t = st.get("attract_type")
    t = t if t in TYPES else (cfg.get("type") if cfg.get("type") in TYPES else "video")
    return enabled, m, t


def desired(cfg, st=None):
    """(ScreensaverTimer in ms, ScreensaverType). OFF = 0. An untouched minutes choice keeps the config's exact timer."""
    st = read_state(cfg["state"]) if st is None else st
    enabled, m, t = choices(cfg, st)
    if not enabled:
        return 0, t
    if _int(st.get("attract_minutes")) in MINUTES:
        return m * 60000, t
    dflt = _int(cfg.get("timer_ms")) or 0
    return (dflt if dflt > 0 else 600000), t


def game_running(cfg, proc=None):
    """True while a game runs. Fails closed (#285 L4): an unreadable process table or a bad list counts as busy."""
    proc = proc or PROC
    try:
        procs, pats = set(cfg["game_procs"]), list(cfg["game_patterns"])
        pids = [n for n in os.listdir(proc) if n.isdigit()]
    except (OSError, TypeError, ValueError):
        return True
    for pid in pids:
        try:
            with open(os.path.join(proc, pid, "comm"), encoding="utf-8", errors="replace") as f:
                if f.read().strip() in procs:
                    return True
            if pats:
                with open(os.path.join(proc, pid, "cmdline"), "rb") as f:
                    cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
                if any(re.search(p, cmd) for p in pats):
                    return True
        except (OSError, re.error, TypeError):
            continue
    return False


def fightcade_running(cfg):
    rd = cfg["run_dir"]
    if not rd:
        return False
    try:
        with open(os.path.join(rd, "current-mode"), encoding="utf-8") as f:
            return f.read().strip() == "fightcade"
    except OSError:
        return False


def set_keys(text, values):
    """Replace (or append) `<type name="KEY" value="V" />` lines in es_settings.xml text; nothing else changes."""
    for key, (typ, val) in values.items():
        line = '<%s name="%s" value="%s" />' % (typ, key, val)
        pat = re.compile(r'^<(?:int|bool|string) name="%s" value="[^"]*" />[ \t]*$' % re.escape(key), re.M)
        if pat.search(text):
            text = pat.sub(lambda _m: line, text, count=1)
        else:
            text = text + ("" if text.endswith("\n") or not text else "\n") + line + "\n"
    return text


def current_es(cfg):
    """(ScreensaverTimer, ScreensaverType) as ES-DE's file has them now; None where missing or unreadable."""
    try:
        with open(cfg["es_settings"], encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None, None

    def get(key):
        m = re.search(r'^<(?:int|bool|string) name="%s" value="([^"]*)" />' % key, text, re.M)
        return m.group(1) if m else None
    t = get("ScreensaverTimer")
    return (int(t) if t and t.isdigit() else None), get("ScreensaverType")


def pending(cfg, st=None):
    """True when the app's choices are not in ES-DE's file yet (they apply on the next ES-DE restart)."""
    return current_es(cfg) != desired(cfg, st)


def write_settings(cfg, st=None):
    """Write the two keys into ES-DE's es_settings.xml: atomic, always 0600 (it holds a scraper password), and never
    through a symlink. -> (ms, type)."""
    ms, t = desired(cfg, st)
    path = cfg["es_settings"]
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)           # (#285 L2) a symlinked file is refused
    except OSError as e:
        raise AttractError("CANNOT READ ES-DE SETTINGS: %s" % (e.strerror or e))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise AttractError("CANNOT READ ES-DE SETTINGS: NOT A FILE")
        with os.fdopen(fd, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
            old = f.read()
    except OSError as e:
        raise AttractError("CANNOT READ ES-DE SETTINGS: %s" % (e.strerror or e))
    except AttractError:
        os.close(fd)
        raise
    new = set_keys(old, {"ScreensaverTimer": ("int", str(ms)), "ScreensaverType": ("string", t)})
    if new != old:
        tmp = os.path.join(os.path.dirname(path), ".%s.hk-tmp" % os.path.basename(path))
        try:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
                f.write(new)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)                                  # (#285 L1) flat 0600, whatever the old mode was
            os.replace(tmp, path)
        except OSError as e:
            raise AttractError("WRITE FAILED: %s" % (e.strerror or e))
    return ms, t


def restart_esde(cfg, run=subprocess.run):
    """Ask the session loop for ES-DE (next-mode), then kill ES-DE like the deploy handler. -> (ok, message)."""
    rd = cfg["run_dir"]
    if rd and os.path.isdir(rd):
        try:
            with open(os.path.join(rd, "next-mode"), "w", encoding="utf-8") as f:
                f.write("esde\n")
        except OSError:
            pass
    r = run(list(KILL), capture_output=True, text=True)
    return (True, "ES-DE RELOADS") if r.returncode in (0, 1) else (False, "COULD NOT RESTART ES-DE")


def apply(cfg, run=subprocess.run, proc=None):
    """Guard, write the keys, restart ES-DE. -> (ok, message). Nothing is written while a game runs."""
    if game_running(cfg, proc):
        return False, "A GAME IS RUNNING - APPLY LATER"
    if fightcade_running(cfg):
        return False, "FIGHTCADE IS RUNNING - APPLY LATER"
    try:
        write_settings(cfg)
    except AttractError as e:
        return False, str(e)
    return restart_esde(cfg, run)


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] != "apply":
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return 2
    rest, delay = a[1:], 0.0
    if rest[:1] == ["--delay"] and len(rest) >= 2:
        try:
            delay, rest = float(rest[1]), rest[2:]
        except ValueError:
            return 2
    if len(rest) != 1:
        return 2
    cfg = load_config(rest[0])
    time.sleep(delay)
    ok, msg = apply(cfg)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
