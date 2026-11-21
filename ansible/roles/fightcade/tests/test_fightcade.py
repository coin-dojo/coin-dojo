#!/usr/bin/env python3
"""Tests for the `fightcade` role (#208, part B of #204): Fightcade mode.

Pure stdlib, pytest-compatible, or run directly:
    python3 ansible/roles/fightcade/tests/test_fightcade.py

Three layers:
  1. Rules over the role's TEXT: the option B values, the contract paths, the archive pins taken from
     build/fightcade/pins.json (the one source, #267), every default a lever or `# internal`, the box64 fast flags off by default, the
     filter that writes the FBNeo ini (Windows paths keep their backslashes). Broken copies must be flagged.
  2. The cabinet scripts run here (bash, as on macOS's bash 3.2) against a fake install tree: the launcher builds
     the RAM prefix read-only and removes it, copies a fresh ini, gives only the lobby the visible cursor, starts and
     stops stick-as-mouse, enters the systemd scope, leaves promptly on TERM; broken cases: missing artifact, changed
     CSS, stale prefix, no DISPLAY, RAM prefix dir not writable. The unpack helper refuses a sha256 mismatch; the prefix
     builder refuses an incomplete wineboot.
  3. The real role in the pinned Ansible container against localhost with fake archives (test_play_*): check mode
     writes nothing, apply installs, the second apply and the dry run after it are changed=0; broken: a download that
     does not match its pin, a missing download, a Box64 build that does not match its own .sha256 files, Fightcade
     mode running. Counted SKIP when docker is not running.
"""
import hashlib
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANSIBLE = os.path.dirname(os.path.dirname(ROLE))
REPO = os.path.dirname(ANSIBLE)
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
sys.path.insert(0, os.path.join(ANSIBLE, "filter_plugins"))
from hktest import check as _check, main, skip  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)
from hk_fbneo_ini import hk_fbneo_ini, parse_overrides  # noqa: E402

FILES = os.path.join(ROLE, "files")
LAUNCHER = os.path.join(FILES, "coin-dojo-fightcade")
UNPACK = os.path.join(FILES, "hk-fightcade-unpack")
PREFIX = os.path.join(FILES, "hk-fightcade-prefix")
WRAP = os.path.join(FILES, "hk-fightcade-wrap")
BASH = "/bin/bash"


def _read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


def _defaults():
    return _read(ROLE, "defaults", "main.yml")


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# ---- 1. rules over text --------------------------------------------------------------------------------------------
OPTION_B = {"nVidSelect": "4", "bVidAutoSwitchFull": "1", "nVidHorWidth": "1920", "nVidHorHeight": "1080",
            "nVidScrnAspectX": "16", "nVidScrnAspectY": "9", "bVidCorrectAspect": "1", "bVidFullStretch": "0",
            "bVidUseHardwareGamma": "0", "bVidVSync": "0",
            "bForce60Hz": "1"}   # #233: 59.59 Hz games at 60 on the 60 Hz panel (offline; Fightcade ignores it online)
ROM_DIRS = ["fightcade", "arcade", "fbneo", "cps1", "cps2", "cps3", "neogeo"]   # #298: Fightcade's own folder first
ARCADE_DIRS = ["arcade", "fbneo", "cps1", "cps2", "cps3", "neogeo"]   # the game menu's arcade folders, the fallback
FBNEO_ROM_SLOTS = 20   # FBNeo's szAppRomPaths[0..19] (DIRS_MAX); the dirs + roms-extra must fit


def rule_rom_dirs(defaults):
    """#298: roms/fightcade is searched FIRST, then every arcade folder, all within FBNeo's slots, and the role makes it."""
    p = []
    m = re.search(r"(?m)^fightcade_rom_dirs:\s*\[([^\]]*)\]", defaults)
    dirs = [x.strip() for x in m.group(1).split(",")] if m else []
    if not dirs or dirs[0] != "fightcade":
        p.append("roms/fightcade is not the first ROM path: %s" % dirs)
    if dirs.count("fightcade") != 1:
        p.append("roms/fightcade is listed %d times" % dirs.count("fightcade"))
    missing = [d for d in ARCADE_DIRS if d not in dirs]
    if missing:
        p.append("arcade folders missing from the fallback: %s" % missing)
    if len(dirs) + 1 > FBNEO_ROM_SLOTS:
        p.append("%d ROM paths + roms-extra do not fit FBNeo's %d slots" % (len(dirs), FBNEO_ROM_SLOTS))
    if not re.search(r'(?m)^  - \["\{\{ fightcade_rom_root \}\}/fightcade", "0755"\]', defaults):
        p.append("the role does not make {{ fightcade_rom_root }}/fightcade (0755, the cabinet user's)")
    return p


def rule_option_b(defaults):
    """The #92 option B keys, exactly (the maintainer 2026-11-18)."""
    p = []
    block = re.search(r"(?ms)^fightcade_ini_keys:.*?\n((?:  \S.*\n)+)", defaults)
    keys = dict(re.findall(r"(?m)^  ([A-Za-z][A-Za-z0-9]*):\s*([0-9]+)", block.group(1))) if block else {}
    for k, v in OPTION_B.items():
        if keys.get(k) != v:
            p.append("%s is %r, option B wants %s" % (k, keys.get(k), v))
    m = re.search(r"(?m)^fightcade_rom_dirs:\s*\[([^\]]*)\]", defaults)
    if not m or [x.strip() for x in m.group(1).split(",")] != ROM_DIRS:
        p.append("fightcade_rom_dirs is not %s" % ROM_DIRS)
    return p


def rule_contract(defaults):
    """Names fixed by the #204 contract and the #209 session."""
    p = []
    want = {"fightcade_bin": "/usr/local/bin/coin-dojo-fightcade", "fightcade_unit": "coin-dojo-fightcade",
            "fightcade_ram_prefix": '"{{ fightcade_run_dir }}/fcprefix"', "fightcade_stickmouse_unit": "coin-dojo-stickmouse.service",
            "fightcade_libexec": "/usr/local/lib/coin-dojo-fightcade"}
    for k, v in want.items():
        if not re.search(r"(?m)^%s: %s\s" % (k, re.escape(v)), defaults):
            p.append("%s is not %s" % (k, v))
    if not re.search(r'(?m)^fightcade_profile: "\{\{ fightcade_data \}\}/profile"', defaults) or \
            not re.search(r'\["\{\{ fightcade_profile \}\}", "0700"\]', defaults):
        p.append("the lobby profile is not <data>/profile with mode 0700")
    if not re.search(r'(?m)^fightcade_box64_fast: "\{\{ hk_fightcade_box64_fast \}\}"', defaults):
        p.append("fightcade_box64_fast is not the hk_fightcade_box64_fast lever")
    if not re.search(r"path: emulator/fbneo/ROMs, to: \"\{\{ fightcade_data \}\}/roms-extra\"", defaults):
        p.append("the lobby's ROM download folder is not linked to roms-extra")
    for pkg in ("xwayland", "x11-xkb-utils", "libnss3", "libgtk-3-0t64", "libasound2t64", "libsdl2-2.0-0",
                "libvulkan1", "libpulse0", "libxkbcommon0", "xdg-utils", "dmz-cursor-theme"):
        if not re.search(r"(?m)^  - %s$" % re.escape(pkg), defaults):
            p.append("package %s missing" % pkg)
    return p


def rule_no_hidden_knob(defaults):
    """#125: every default is a pass-through ({{ hk_* }}), derived, or marked # internal."""
    p = []
    for m in re.finditer(r"(?m)^([a-z][a-z0-9_]*):(.*)$", defaults):
        name, rest = m.group(1), m.group(2)
        if "# internal" in rest or "{{ hk_" in rest:
            continue
        p.append("%s: a literal default that is not a lever (declare hk_* or mark # internal)" % name)
    return p


def _fetch_pins():
    """#267: build/fightcade/pins.json is the one source of the download pins (fetch.sh and the role read it)."""
    return json.loads(_read(REPO, "build", "fightcade", "pins.json"))


def rule_pins(defaults, pins):
    """Each archive's sha256/url/size is fightcade_pins.<name>.* (pins.json), never a literal of its own."""
    p = []
    found = dict(re.findall(r'(?m)^  - name: (\w+)\n(?:    .*\n)*?    sha256: (.*)$', defaults))
    for name in ("wine", "electron", "fightcade"):
        if found.get(name) != '"{{ fightcade_pins.%s.sha256 }}"' % name:
            p.append("%s sha256 is %s, not fightcade_pins.%s.sha256 (build/fightcade/pins.json)" % (name, found.get(name), name))
        if pins[name]["sha256"] in defaults:
            p.append("%s: the pin %s is written in the role too (a second source)" % (name, pins[name]["sha256"][:12]))
    return p


def test_defaults_rules():
    d = _defaults()
    _check(rule_option_b(d) == [], "option B FBNeo keys + ROM folders %s" % rule_option_b(d))
    _check(rule_contract(d) == [], "contract names, profile 0700, packages %s" % rule_contract(d))
    _check(rule_rom_dirs(d) == [], "#298: roms/fightcade first, then the arcade folders, made by the role %s" % rule_rom_dirs(d))
    _check(rule_rom_dirs(d.replace("[fightcade, arcade,", "[arcade,")) != [] and rule_option_b(d.replace("[fightcade, arcade,", "[arcade,")) != [],
           "BROKEN control #298: roms/fightcade missing from the ROM paths is flagged")
    _check(rule_rom_dirs(d.replace("[fightcade, arcade,", "[arcade, fightcade,")) != [],
           "BROKEN control #298: roms/fightcade not first (shared versions would win) is flagged")
    _check(rule_rom_dirs(d.replace(", cps3, neogeo]", ", neogeo]")) != [], "BROKEN control #298: a dropped arcade fallback folder is flagged")
    _check(rule_rom_dirs(d.replace('  - ["{{ fightcade_rom_root }}/fightcade", "0755"]', "  # gone")) != [],
           "BROKEN control #298: the role no longer making roms/fightcade is flagged")
    _check(rule_rom_dirs("fightcade_rom_dirs: [fightcade, %s]\n" % ", ".join(ARCADE_DIRS + ["x%d" % i for i in range(13)])) != [],
           "BROKEN control #298: more ROM paths than FBNeo's 20 slots is flagged")
    _check(rule_no_hidden_knob(d) == [], "every default is a lever or # internal %s" % rule_no_hidden_knob(d))
    pins = _fetch_pins()
    _check(rule_pins(d, pins) == [], "archive pins come from build/fightcade/pins.json %s" % rule_pins(d, pins))
    _check(rule_option_b(d.replace("bVidVSync: 0", "bVidVSync: 1")) != [], "BROKEN control: vsync on is flagged")
    _check(rule_option_b(d.replace("nVidSelect: 4", "nVidSelect: 0")) != [], "BROKEN control: DirectDraw is flagged")
    _check(rule_option_b(d.replace("bForce60Hz: 1", "bForce60Hz: 0")) != [], "BROKEN control #233: Force60 off is flagged")
    _check(rule_option_b(d.replace("  bForce60Hz: 1\n", "")) != [], "BROKEN control #233: a missing Force60 key is flagged")
    _check(rule_contract(d.replace('"{{ fightcade_run_dir }}/fcprefix"', "/dev/shm/fcprefix")) != [],
           "BROKEN control: a RAM prefix moved to world-shared /dev/shm is flagged")
    nat = re.search(r"(?ms)^fightcade_nativefier_keys:.*?\n(?=\S)", d).group(0)
    for k in ('internalUrls: "^https://(web|replay)\\\\.fightcade\\\\.com/"', "contextIsolation: true", "nodeIntegration: false",
              "enableRemoteModule: false", "plugins: false", "preload:"):
        _check(k in nat, "nativefier keys: %s" % k)
    _check(rule_no_hidden_knob(d + "\nfightcade_knob: 3\n") != [], "BROKEN control: a planted literal default is flagged")
    _check(rule_pins(d.replace('"{{ fightcade_pins.wine.sha256 }}"', "0" * 64), pins) != [], "BROKEN control: a literal pin is flagged")
    _check(rule_pins(d + "\n# %s\n" % pins["fightcade"]["sha256"], pins) != [], "BROKEN control: a pin copied into the role is flagged")


FORCE60_ONLINE_OFF = 'Disabled "force 60Hz" framerate for online games'   # Fightcade/ChangeLog.txt, FcadeFBNeo v0.2.97.44-53


def rule_force60_offline(changelog):
    """#233: bForce60Hz is only safe because Fightcade's FBNeo ignores it in online games (both peers stay at the
    game's own rate; a 60 vs 59.59 peer would run a different SH-2 cycle count per frame, cps3run.cpp nCyclesTotal)."""
    return [] if FORCE60_ONLINE_OFF in changelog else ["Fightcade's ChangeLog no longer says force 60Hz is off online"]


def test_force60_online_premise():
    _check(rule_force60_offline("FcadeFBNeo v0.2.97.44-53\n- %s: all games run at their original framerate now\n"
                                % FORCE60_ONLINE_OFF) == [], "force60 rule: the changelog line passes")
    _check(rule_force60_offline("FcadeFBNeo v0.2.97.44-53\n- Allow to fast-forward replays\n") != [],
           "BROKEN control #233: a changelog without the online-off line is flagged")
    rel = os.path.join("build", "fightcade", "out", _fetch_pins()["fightcade"]["file"])
    tgz = os.path.join(REPO, rel)
    if not os.path.isfile(tgz):   # a worktree: the downloads live in the main checkout (git's common dir)
        r = subprocess.run(["git", "-C", REPO, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            tgz = os.path.join(os.path.dirname(r.stdout.strip()), rel)
    if not os.path.isfile(tgz):
        skip("#233 force60 premise: no build/fightcade/out tarball here (make -C build fightcade)")
        return
    if _sha(tgz) != _fetch_pins()["fightcade"]["sha256"]:
        skip("#233 force60 premise: the local Fightcade tarball is not the pinned one")
        return
    with tarfile.open(tgz) as tf:
        log = tf.extractfile("Fightcade/ChangeLog.txt").read().decode("utf-8", "replace")
    _check(rule_force60_offline(log) == [], "#233: the pinned Fightcade 2.1.45 says force 60Hz is off online (ChangeLog.txt)")


BINFMT_ENTRY = "enabled\ninterpreter %s/wine\nflags: \noffset 0\nmagic 4d5a\n"   # fs/binfmt_misc.c entry_status


def rule_binfmt(defaults, tasks):
    """#228: the MZ rule names the root-owned wine wrapper, has no flags, and lives in /etc/binfmt.d."""
    p = []
    want = {"fightcade_binfmt_name": "hk-wine",
            "fightcade_binfmt_rule": '":{{ fightcade_binfmt_name }}:M::MZ::{{ fightcade_libexec }}/wine:"',
            "fightcade_binfmt_conf": "/etc/binfmt.d/coin-dojo-wine.conf", "fightcade_binfmt_proc": "/proc/sys/fs/binfmt_misc",
            "fightcade_binfmt_cmd": "/usr/lib/systemd/systemd-binfmt", "fightcade_binfmt": '"{{ hk_fightcade_binfmt }}"'}
    for k, v in want.items():
        if not re.search(r"(?m)^%s: %s\s" % (k, re.escape(v)), defaults):
            p.append("%s is not %s" % (k, v))
    if 'argv: ["{{ fightcade_binfmt_cmd }}", "{{ fightcade_binfmt_conf }}"]' not in tasks:
        p.append("systemd-binfmt is not given the one conf file (without it, it flushes every entry)")
    if re.search(r"(?m)^\s+(cmd|argv):.*systemctl.*systemd-binfmt", tasks):
        p.append("systemd-binfmt.service is restarted (flushes python3.13's entry too)")
    if "printf -- '-1\\\\n' >" not in tasks or "when: not (fightcade_binfmt | bool)" not in tasks:
        p.append("no removal path (hk_fightcade_binfmt false: write -1 to the entry)")
    if '"{{ fightcade_binfmt_conf }}"]' not in tasks:
        p.append("the conf path is not checked off the data partition")
    return p


def test_binfmt_rule():
    d, t = _defaults(), _read(ROLE, "tasks", "main.yml")
    _check(rule_binfmt(d, t) == [], "binfmt #228: rule, name, conf, register only this file, removal path %s" % rule_binfmt(d, t))
    _check(rule_binfmt(d.replace("::{{ fightcade_libexec }}/wine:", "::{{ fightcade_libexec }}/wine:F"), t) != [],
           "BROKEN binfmt #228: an F flag (interpreter pinned at registration) is flagged")
    _check(rule_binfmt(d.replace("::{{ fightcade_libexec }}/wine:", "::/usr/bin/wine:"), t) != [],
           "BROKEN binfmt #228: another interpreter path is flagged")
    _check(rule_binfmt(d.replace("fightcade_binfmt_conf: /etc/binfmt.d/coin-dojo-wine.conf", "fightcade_binfmt_conf: ''"), t) != [],
           "BROKEN binfmt #228: a missing conf path is flagged")
    _check(rule_binfmt(d, t.replace('argv: ["{{ fightcade_binfmt_cmd }}", "{{ fightcade_binfmt_conf }}"]',
                                     'argv: [systemctl, restart, systemd-binfmt]')) != [],
           "BROKEN binfmt #228: restarting systemd-binfmt (flushes every entry) is flagged")
    _check(rule_binfmt(d, t.replace("when: not (fightcade_binfmt | bool)", "when: false")) != [],
           "BROKEN binfmt #228: a role without the removal path is flagged")


def test_popup_patch_rule():
    """#215: the Fightcade archive carries the pinned main.js patch that hardens nativefier's popup options."""
    pp = _popup_patch()
    _check(pp is not None and pp["path"] == "fc2-electron/resources/app/lib/main.js",
           "defaults #215: the Fightcade archive patches lib/main.js (%s)" % (pp and pp["path"]))
    if not pp:
        return
    for k in ("plugins: false", "contextIsolation: true", "enableRemoteModule: false"):
        _check(k in pp["sed"], "defaults #215: the popup patch sets %s" % k)
    fc = re.search(r"(?ms)^  - name: fightcade\n.*?(?=^  - name: |^\S)", _defaults()).group(0)
    _check("patches:" in fc and pp["sha256"] in fc, "defaults #215: the patch belongs to the fightcade archive")
    _check(re.search(r"(?ms)^    patches:\n      - path:", _defaults().replace(fc, "")) is None,
           "defaults #215: no other archive is patched")
    t = _read(ROLE, "tasks", "main.yml")
    _check("--patch {{ (x.path ~ '=' ~ x.sha256 ~ ':' ~ x.sed) | quote }}" in t, "tasks #215: the unpack gets --patch")
    _check("recipe.update({'patches': a.patches})" in t, "tasks #215: a patch is part of the archive's stamp")
    # the real Fightcade 2.1.45 main.js (only where `make -C build fightcade` ran): pin and patch still fit
    arc = os.path.join(REPO, "build", "fightcade", "out", _fetch_pins()["fightcade"]["file"])
    if not os.path.exists(arc):
        skip("popup patch on the real main.js: no %s (make -C build fightcade)" % os.path.relpath(arc, REPO))
        return
    with tarfile.open(arc) as t:
        real = t.extractfile("Fightcade/" + pp["path"]).read()
    _check(hashlib.sha256(real).hexdigest() == pp["sha256"], "real main.js #215: sha256 equals the patch's pin")
    r = subprocess.run(["sed", "-e", pp["sed"]], input=real, capture_output=True)
    before, after = real.decode().splitlines(), r.stdout.decode().splitlines()
    diff = [(a, b) for a, b in zip(before, after) if a != b]
    _check(r.returncode == 0 and len(before) == len(after) and len(diff) == 1 and "plugins: true" in diff[0][0],
           "real main.js #215: the patch changes exactly nativefier's popup plugins line (%d lines)" % len(diff))


def test_config_lever():
    cfg = _read(REPO, "config", "variables.d", "fightcade.yml")
    m = re.search(r"(?ms)^  hk_fightcade_box64_fast:\n    type: bool\n    default: (\w+)", cfg)
    _check(m is not None and m.group(1) == "false", "hk_fightcade_box64_fast defaults to false (determinism gate)")
    for name in ("hk_fightcade_install_dir", "hk_fightcade_data_dir", "hk_fightcade_cursor_theme", "hk_fightcade_audio_sink"):
        _check(re.search(r"(?m)^  %s:" % name, cfg) is not None and "{{ %s }}" % name in _defaults(),
               "%s is declared and read by the role" % name)
    m = re.search(r"(?ms)^  hk_fightcade_audio_sink:\n    type: str\n    default: (\S+)", cfg)
    _check(m is not None and m.group(1) == "auto", "#233: hk_fightcade_audio_sink defaults to auto")
    _check(re.search(r'(?m)^    default: "\{\{ hk_data_root \}\}/fightcade"', cfg) is not None,
           "the data dir follows hk_data_root (no hardcoded /data)")
    m = re.search(r'(?ms)^  hk_fightcade_install_dir:\n    type: path\n    default: "([^"]+)"', cfg)
    _check(m is not None and m.group(1) == "{{ hk_fightcade_data_dir }}/opt",
           "the install dir defaults to the data partition (/data/fightcade/opt), not the 6.9 GB root fs")


def test_wiring():
    site, vm = _read(ANSIBLE, "site.yml"), _read(ANSIBLE, "vm.yml")
    _check(re.search(r"role: fightcade, tags: \[fightcade\]", site) is not None, "site.yml runs the role (tag fightcade)")
    _check(re.search(r"role: fightcade, tags: \[fightcade\]", vm) is not None, "vm.yml runs the role on the test VM")
    t = _read(ROLE, "tasks", "main.yml")
    _check("Refuse to touch Fightcade files while Fightcade mode runs" in t.split("- name: Packages")[0],
           "the guard comes before any write")
    _check(re.search(r'(?m)^\s*(dest|path): "\{\{ fightcade_ini \}\}"', t) is None,
           "the role never writes FBNeo's live ini (the launcher does)")


def test_ini_filter():
    default = "// FB Neo config\r\nnVidSelect 0\r\nbVidVSync 1\r\nszAppRomPaths[0] \r\nszAppRomPaths[6] roms/nes/\r\nnAudSampleRate[0] 44100\r\n"
    over = ("// a comment\nnVidSelect 4\nbVidVSync 0\n\nszAppRomPaths[0] Z:\\data\\roms\\arcade\\\n"
            "szAppRomPaths[6] Z:\\data\\fightcade\\roms-extra\\\nbModelessMenu 1\n")
    out = hk_fbneo_ini(default, over)
    lines = out.split("\r\n")
    _check("nVidSelect 4" in lines and "bVidVSync 0" in lines, "filter: values replaced in place")
    _check("szAppRomPaths[0] Z:\\data\\roms\\arcade\\" in lines, "filter: Windows path kept byte for byte (backslashes)")
    _check("szAppRomPaths[6] Z:\\data\\fightcade\\roms-extra\\" in lines and "roms/nes/" not in out,
           "filter: indexed key [6] replaced, not [0]")
    _check("bModelessMenu 1" in lines, "filter: a key the default lacks is appended")
    _check("nAudSampleRate[0] 44100" in lines and lines[0] == "// FB Neo config", "filter: other lines untouched")
    _check(out.count("\r\n") == out.count("\n"), "filter: CRLF line endings kept")
    _check(hk_fbneo_ini(out, over) == out, "filter: idempotent")
    try:
        parse_overrides("nVidSelect 4\n= broken\n")
        _check(False, "BROKEN filter input: a line that is not 'key value' raises")
    except ValueError:
        _check(True, "BROKEN filter input: a line that is not 'key value' raises")


# ---- #230: the sticks in FBNeo ------------------------------------------------------------------------------------
PANEL_FIRE = [("fire 1", "LP"), ("fire 2", "MP"), ("fire 3", "HP"), ("fire 4", "LK"), ("fire 5", "MK"), ("fire 6", "HK"),
              ("coin", "SELECT"), ("start", "START")]


def _stick_buttons(defaults):
    m = re.search(r"(?m)^fightcade_stick_buttons: \{([^}]*)\}", defaults)
    return {k: int(v) for k, v in re.findall(r"(\w+): (\d+)", m.group(1))} if m else {}


def _measured_buttons():
    """The panel by js index as measured on the cabinet (skills/settings/trophiesd.py:38 comment) + SELECT/START."""
    line = re.search(r"(?m)^LP = \d+ .*panel by js index: (.*?) \(", _read(REPO, "skills", "settings", "trophiesd.py")).group(1)
    got = {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", line)}
    got.update({"SELECT": 8, "START": 9})   # stickmouse.py codes(): 296/297 = js 8/9
    return got


def rule_presets(defaults, tasks, ini_tpl, preset_tpl):
    """#230: the preset map = the measured panel, seeded only when absent, and the base ini points at it."""
    p = []
    if _stick_buttons(defaults) != _measured_buttons():
        p.append("fightcade_stick_buttons %s != the measured panel %s" % (_stick_buttons(defaults), _measured_buttons()))
    pairs = re.findall(r"(?m)^  - \[([a-z0-9 ]+), ([A-Z]+)\]$", re.search(r"(?ms)^fightcade_preset_buttons:.*?\n(?=\S)", defaults).group(0))
    if pairs != PANEL_FIRE:
        p.append("fightcade_preset_buttons %s != LP MP HP / LK MK HK = fire 1-6, SELECT coin, START start" % pairs)
    if not re.search(r'(?m)^fightcade_fbneo_version: "0x029744"', defaults):
        p.append("fightcade_fbneo_version is not 0x029744 (Fightcade FBNeo v0.2.97.44)")
    task = re.search(r"(?ms)- name: FBNeo stick presets.*?(?=\n    - name:|\n    # ---)", tasks)
    if not task or not re.search(r"(?m)^        force: false$", task.group(0)):
        p.append("the preset task must not overwrite (force: false): the maintainer's edits stay")
    if not ("nPlayerDefaultControls[{{ n - 1 }}] 15" in ini_tpl and "szPlayerDefaultIni[{{ n - 1 }}] Z:" in ini_tpl):
        p.append("the base ini does not point players at the presets (nPlayerDefaultControls 15 + szPlayerDefaultIni)")
    for frag in ("version {{ fightcade_fbneo_version }}", "16384 + 256 * (fightcade_player - 1)", "j + 128 + fightcade_stick_buttons[button]"):
        if frag not in preset_tpl:
            p.append("preset template lacks %r" % frag)
    return p


def preset_lines(player, buttons):
    """What the preset template renders, in Python (codes per FBNeo gami.cpp InputCodeDesc)."""
    j = 0x4000 + 0x100 * (player - 1)
    out = ['input  "p%d %s"  switch 0x%04X' % (player, d, j + o) for d, o in (("up", 2), ("down", 3), ("left", 0), ("right", 1))]
    out += ['input  "p%d %s"  switch 0x%04X' % (player, n, j + 0x80 + buttons[b]) for n, b in PANEL_FIRE]
    return out


def fbneo_accepts(text, player, burn_ver=0x029744, min_ver=0):
    """The input lines FBNeo's GameInputAutoIni (gami.cpp) would apply for player N (0-based): version gate, then
    only quotes that start with "p<N+1> "."""
    ver, got = 0, []
    for line in text.splitlines():
        m = re.match(r"version (\S+)", line)
        if m:
            ver = int(m.group(1), 0)
        m = re.match(r'input +"(p(\d) [^"]+)" +switch (0x[0-9A-F]+)$', line)
        if m and min_ver <= ver <= burn_ver and int(m.group(2)) == player + 1:
            got.append((m.group(1), int(m.group(3), 16)))
    return got


def test_stick_presets():
    d, t = _defaults(), _read(ROLE, "tasks", "main.yml")
    ini, pre = _read(ROLE, "templates", "fcadefbneo-coin-dojo.ini.j2"), _read(ROLE, "templates", "fbneo-preset.ini.j2")
    _check(rule_presets(d, t, ini, pre) == [], "#230 presets: measured panel, fire 1-6, force: false, ini points at them %s"
           % rule_presets(d, t, ini, pre))
    b = _stick_buttons(d)
    p1 = preset_lines(1, b)
    _check('input  "p1 up"  switch 0x4002' in p1 and 'input  "p1 fire 1"  switch 0x4084' in p1
           and 'input  "p1 fire 6"  switch 0x4081' in p1 and 'input  "p1 coin"  switch 0x4088' in p1,
           "#230 presets: P1 = joystick 0, up = Y axis negative, LP = button 4, HK = button 1, coin = SELECT (8)")
    _check('input  "p2 start"  switch 0x4189' in preset_lines(2, b), "#230 presets: P2 = joystick 1 (0x41xx), START = button 9")
    text = "version 0x029744\n" + "\n".join(p1) + "\n"
    _check(len(fbneo_accepts(text, 0)) == 12 and fbneo_accepts(text, 1) == [], "#230 presets: FBNeo applies all 12 lines to P1 only")
    _check(fbneo_accepts(text.replace("0x029744", "0x029745"), 0) == [],
           "BROKEN control: a preset version above FBNeo's nBurnVer is ignored by FBNeo (version gate)")
    _check(rule_presets(d.replace("LP: 4, MP: 0", "LP: 0, MP: 4"), t, ini, pre) != [], "BROKEN control: LP/MP swapped is flagged")
    _check(rule_presets(d.replace("  - [fire 4, LK]\n  - [fire 5, MK]", "  - [fire 4, MK]\n  - [fire 5, LK]"), t, ini, pre) != [],
           "BROKEN control: kicks out of panel order are flagged")
    _check(rule_presets(d, t.replace("        force: false\n", "        force: true\n"), ini, pre) != [],
           "BROKEN control: a preset task that overwrites the maintainer's edits is flagged")
    _check(rule_presets(d, t, ini.replace("] 15\n", "] 1\n"), pre) != [], "BROKEN control: players left on FBNeo's guess are flagged")
    _check("winebus-map=0" in t and re.search(r"(?m)^fightcade_prefix_recipe: 2\s", d) is not None,
           "#230: the prefix stamp names winebus-map=0 and the recipe is 2 (every cabinet rebuilds once)")


def test_templates_text():
    ini = _read(ROLE, "templates", "fcadefbneo-coin-dojo.ini.j2")
    _check("Z:{{ (fightcade_rom_root ~ '/' ~ d) | replace('/', '\\\\') }}\\" in ini,
           "ini template: szAppRomPaths[0..6] in Windows form under Z:")
    _check("szAppRomPaths[{{ fightcade_rom_dirs | length }}] Z:{{ fightcade_roms_extra | replace('/', '\\\\') }}\\" in ini,
           "ini template: [7] (after the ROM dirs) = roms-extra in Windows form")
    css = _read(ROLE, "templates", "inject.css.j2")
    _check("background: #000" in css and "overflow: hidden" in css and "fightcade_column.width" in css,
           "CSS template: black outside, clipped column of the bezel width")
    _check(css.count("fightcade_brand.") >= 8 and not re.search(r"#[0-9A-Fa-f]{6}\\b", css.split("#227")[1]),
           "CSS template #227: the keyboard colours come from the brand tokens, no literal hex colour")
    _check("#hk-osk {\n  display: none;" in css and "#hk-osk.hk-osk-open { display: block; }" in css,
           "CSS template #227: the keyboard is hidden unless it is open")
    tasks = _read(ROLE, "tasks", "main.yml")
    _check("content: \"{{ fightcade_js_text }}\"" in tasks and "dest: \"{{ fightcade_inject_js }}\"" in tasks,
           "tasks #227: every deploy lays the keyboard script next to the CSS (nativefier's preload requires it)")
    conf = _read(ROLE, "templates", "coin-dojo-fightcade.conf.j2")
    _check("HK_FC_JS_SHA256={{ fightcade_js_text | hash('sha256') }}" in conf and "HK_FC_JS=" in conf,
           "config template #227: the launcher config carries the keyboard script's path and sha256")
    used = set()
    for f in (LAUNCHER, WRAP, PREFIX):
        used |= set(re.findall(r"\bHK_FC_[A-Z0-9_]+", _read(f)))
    used -= {"HK_FC_SYSTEMCTL", "HK_FC_GAME_XCURSOR_THEME", "HK_FC_ALLOW_ROOT", "HK_FC_PROC", "HK_FC_WINE_TMP"}   # test hooks / set by the launcher
    defined = set(re.findall(r"(?m)^(HK_FC_[A-Z0-9_]+)=", conf))
    _check(used <= defined, "every HK_FC_* the scripts read is in the config template (missing: %s)" % sorted(used - defined))
    _check("MemorySwapMax" not in conf.split("HK_FC_SCOPE_PROPS=")[1], "no MemorySwapMax (no memory cgroup on the Pi kernel)")


# ---- 2. the cabinet scripts, here -------------------------------------------------------------------------------
FAKE_BOX64 = """#!/bin/bash
# fake box64: log, then run the "x86" program (a script here)
echo "box64 $*" >> "$FAKE_LOG"
exec "$@"
"""
FAKE_WINE = """#!/bin/bash
echo "wine WINEPREFIX=$WINEPREFIX $*" >> "$FAKE_LOG"
case "$1" in
  wineboot)
    echo "fake wineboot WINEDEBUG=${WINEDEBUG:-} BOX64_LOG=${BOX64_LOG:-}"
    # #216 broken cases: SIGKILL (once, or every time; a half-built prefix left behind), or a plain rc
    if [ -n "${FAKE_WINEBOOT_KILL:-}" ]; then
      if [ "$FAKE_WINEBOOT_KILL" = always ] || [ ! -e "$FAKE_LOG.killed" ]; then
        : > "$FAKE_LOG.killed"; mkdir -p "$WINEPREFIX/drive_c/half-built"
        echo "err:fake:box64 about to be killed" >&2; kill -KILL $$
      fi
    fi
    [ -n "${FAKE_WINEBOOT_RC:-}" ] && { echo "err:fake:wineboot fails with $FAKE_WINEBOOT_RC" >&2; exit "$FAKE_WINEBOOT_RC"; }
    [ -n "${FAKE_BAD_WINEBOOT:-}" ] && { mkdir -p "$WINEPREFIX/drive_c"; echo x > "$WINEPREFIX/system.reg"; exit 0; }
    mkdir -p "$WINEPREFIX/drive_c/windows/syswow64" "$WINEPREFIX/drive_c/windows/system32" "$WINEPREFIX/dosdevices"
    : > "$WINEPREFIX/drive_c/windows/syswow64/kernel32.dll"; : > "$WINEPREFIX/drive_c/windows/system32/kernel32.dll"
    echo "WINE REGISTRY" > "$WINEPREFIX/system.reg"; echo "WINE REGISTRY" > "$WINEPREFIX/user.reg"
    echo "WINE REGISTRY" > "$WINEPREFIX/userdef.reg"; : > "$WINEPREFIX/.update-timestamp"
    ln -s ../drive_c "$WINEPREFIX/dosdevices/c:"; ln -s / "$WINEPREFIX/dosdevices/z:"; ln -s /dev "$WINEPREFIX/dosdevices/d:"
    ;;
  reg)
    case "$*" in
      *winebus*)   # #230: HKLM ... winebus "Map Controllers" (FAKE_REG_FAIL=winebus: the reg add fails)
        [ "${FAKE_REG_FAIL:-}" = winebus ] && { echo "err:fake:reg winebus" >&2; exit 1; }
        printf '[System\\\\CurrentControlSet\\\\Services\\\\winebus]\\n"Map Controllers"=dword:00000000\\n' >> "$WINEPREFIX/system.reg" ;;
      *) printf '[Software\\\\Wine\\\\Direct3D]\\n"csmt"=dword:00000000\\n' >> "$WINEPREFIX/user.reg" ;;
    esac
    ;;
esac
exit 0
"""
FAKE_WINESERVER = """#!/bin/bash
echo "wineserver WINEPREFIX=${WINEPREFIX:-} $* ready=$([ -f "${WINEPREFIX:-/nonexistent}/system.reg" ] && echo 1 || echo 0)" >> "$FAKE_LOG"
# #257: with FAKE_WS_PROC the fake is a real long-lived process listed in a fake /proc (environ, cmdline, comm)
if [ -n "${FAKE_WS_PROC:-}" ]; then
  case "$1" in
    -p)
      [ -n "${FAKE_WS_FAIL:-}" ] && exit 1
      [ -n "${FAKE_WS_SLOW:-}" ] && sleep "$FAKE_WS_SLOW"
      if [ -n "${FAKE_WS_FORKER:-}" ]; then   # a daemon (own session, like wineserver's): on TERM it forks a NEW wineserver pid, then exits
        python3 -c 'import os,sys; os.setsid(); os.execvp("bash", ["bash", "-c", sys.argv[1]])' 'trap '"'"'sleep 30 >/dev/null 2>&1 & q=$!; mkdir -p "$FAKE_WS_PROC/$q"; printf "WINEPREFIX=%s\\0" "$WINEPREFIX" > "$FAKE_WS_PROC/$q/environ"; printf "box64\\0wineserver\\0-p\\0" > "$FAKE_WS_PROC/$q/cmdline"; exit 0'"'"' TERM; sleep 30 >/dev/null 2>&1 & wait' >/dev/null 2>&1 &
      else   # a daemon like the real one: its own session, outside the start's process group
        python3 -c 'import os; os.setsid(); os.execvp("sleep", ["sleep", "30"])' >/dev/null 2>&1 &
      fi
      pid=$!
      mkdir -p "$FAKE_WS_PROC/$pid"
      printf 'WINEPREFIX=%s\\0' "$WINEPREFIX" > "$FAKE_WS_PROC/$pid/environ"
      printf 'box64\\0/opt/wine/bin/wineserver\\0-p\\0' > "$FAKE_WS_PROC/$pid/cmdline"
      echo wineserver > "$FAKE_WS_PROC/$pid/comm"
      ;;
    -k)
      [ -n "${FAKE_WS_K_FAILS:-}" ] && exit 0
      for d in "$FAKE_WS_PROC"/[0-9]*; do [ -d "$d" ] && kill -TERM "${d##*/}" 2>/dev/null; done
      ;;
  esac
fi
exit 0
"""
# the lobby: records what it sees, then exits (or waits for TERM with FAKE_ELECTRON_WAIT)
FAKE_ELECTRON = """#!/bin/bash
{
  echo "args=$*"
  echo "XCURSOR_THEME=${XCURSOR_THEME:-}"
  echo "GAME_XCURSOR=${HK_FC_GAME_XCURSOR_THEME:-}"
  echo "WINEPREFIX=$WINEPREFIX"
  echo "PATH0=${PATH%%:*}"
  echo "STRONGMEM=${BOX64_DYNAREC_STRONGMEM:-unset}"
  echo "PULSE_SINK=${PULSE_SINK:-unset}"
  # the mode bits, not `[ -w ]`: root may write anywhere, and the Forgejo job runs the tests as root (#269)
  for d in prefix:"$WINEPREFIX" dosdevices:"$WINEPREFIX/dosdevices"; do
    m=$(stat -c %a "${d#*:}" 2>/dev/null || stat -f %Lp "${d#*:}")
    case "$m" in *[2367]*) echo "${d%%:*}=writable" ;; *) echo "${d%%:*}=readonly" ;; esac
  done
  echo "c=$(readlink "$WINEPREFIX/dosdevices/c:")"
  echo "z=$(readlink "$WINEPREFIX/dosdevices/z:")"
  ls "$WINEPREFIX" | tr '\\n' ' ' | sed 's/^/files=/'; echo
  echo "ini=$(head -1 "$FAKE_INI")"
  echo "cwd=$(pwd)"
} > "$FAKE_SEEN"
if [ -n "${FAKE_ELECTRON_WAIT:-}" ]; then echo $$ > "$FAKE_SEEN.pid"; sleep 30 & wait; fi
exit "${FAKE_ELECTRON_RC:-0}"
"""
FAKE_SYSTEMCTL = """#!/bin/bash
echo "systemctl $*" >> "$FAKE_LOG"
case "$*" in
  "--user cat coin-dojo-stickmouse.service") [ -n "${FAKE_STICKMOUSE:-}" ] ;;
  "--user show-environment") [ -n "${FAKE_USER_MANAGER:-}" ] ;;
  "--user is-active --quiet coin-dojo-fightcade.scope") [ -n "${FAKE_STALE_SCOPE:-}" ] ;;
  *) exit 0 ;;
esac
"""
# #233: pactl replays a file (FAKE_PACTL_FILE: `LC_ALL=C pactl list sinks`) and logs the client.conf it was given;
# FAKE_PACTL_HANG=1 = a daemon that never answers.
FAKE_PACTL = """#!/bin/bash
echo "pactl $* LC_ALL=${LC_ALL:-} autospawn=$(grep -h autospawn "${PULSE_CLIENTCONFIG:-/nonexistent}" 2>/dev/null)" >> "$FAKE_LOG"
if [ -n "${FAKE_PACTL_HANG:-}" ]; then exec sleep 30; fi
[ -n "${FAKE_PACTL_SLOW:-}" ] && sleep 1.5   # exec: one process, like the real pactl
case "$*" in
  "list sinks") cat "${FAKE_PACTL_FILE:-/dev/null}" ;;
  *) exit 1 ;;
esac
"""
# coreutils timeout stand-in (macOS has none): logs its duration, kills the command after it
FAKE_TIMEOUT = """#!/bin/bash
echo "timeout $1" >> "$FAKE_LOG"
t=$1; shift
"$@" & p=$!
( sleep "$t"; kill -TERM "$p" ) >/dev/null 2>&1 & w=$!
wait "$p"; rc=$?
kill "$w" 2>/dev/null
exit "$rc"
"""
FAKE_ID_ROOT = "#!/bin/sh\necho 0\n"   # `id -u` says root (#215: the root refusals)
# #269: the Forgejo job runs the tests as root (node:20.18.1-bookworm). The scripts refuse uid 0 (the launcher with no
# escape at all), so as root the fake cabinet answers `id -u` as the cabinet user, like the real cabinet does. As a normal
# user (macOS, Linux) nothing is faked: the real `id` answers. HK_FC_ALLOW_ROOT is never set for this.
FAKE_ID_USER = "#!/bin/sh\n[ \"$*\" = \"-u\" ] && { echo 1000; exit 0; }\nexec /usr/bin/id \"$@\"\n"
AS_ROOT = os.geteuid() == 0
# nativefier's popup options as Fightcade 2.1.45 ships them (lib/main.js 4798-4810, byte for byte)
FAKE_MAIN_JS = """    const DEFAULT_WINDOW_OPTIONS = {
        // Convert dashes to spaces because on linux the app name is joined with dashes
        title: options.name,
        tabbingIdentifier: helpers_1.nativeTabsSupported() ? options.name : undefined,
        webPreferences: {
            javascript: true,
            plugins: true,
            nodeIntegration: false,
            webSecurity: !options.insecure,
            preload: path.join(__dirname, 'preload.js'),
            zoomFactor: options.zoom,
        },
    };
"""
POPUP_HARDENED = "            plugins: false, contextIsolation: true, enableRemoteModule: false,\n"


def _popup_patch():
    """The role's main.js patch (defaults: fightcade_archives[fightcade].patches[0]) as {path, sha256, sed}."""
    m = re.search(r"(?ms)^    patches:\n      - path: (\S+)\n        sha256: ([0-9a-f]{64})\n        sed: '([^']*)'\n", _defaults())
    return {"path": m.group(1), "sha256": m.group(2), "sed": m.group(3)} if m else None


FAKE_SYSTEMD_RUN = """#!/bin/bash
echo "systemd-run $*" >> "$FAKE_LOG"
while [ $# -gt 0 ] && [ "$1" != "--" ]; do shift; done
shift
exec "$@"
"""


class Cab:
    """A fake installed cabinet: install dir, data dir, conf, fake binaries, a /dev/shm stand-in."""

    def __init__(self):
        self.d = tempfile.mkdtemp(prefix="t-fightcade-")
        self.inst = self.p("opt")
        self.data = self.p("data")
        self.shm = self.p("shm")
        self.log = self.p("fake.log")
        self.seen = self.p("seen.txt")
        for sub in ("opt/bin", "opt/box64/x64lib", "opt/wine/bin", "opt/electron",
                    "opt/Fightcade/fc2-electron/resources/app/inject", "opt/Fightcade/emulator/fbneo",
                    "data/prefix", "data/profile", "data/fbneo/config", "data/logs", "shm", "fakebin"):
            os.makedirs(self.p(sub), exist_ok=True)
        self.exe("opt/box64/box64", FAKE_BOX64)
        self.write("opt/box64/box64.box64rc", "[fcadefbneo.exe]\n")
        self.exe("opt/wine/bin/wine", FAKE_WINE)
        self.exe("opt/wine/bin/wineserver", FAKE_WINESERVER)
        self.exe("opt/electron/electron", FAKE_ELECTRON)
        self.write("opt/Fightcade/fc2-electron/resources/app/package.json", "{}\n")
        self.css = "body { width: 1440px }\n"
        self.write("opt/Fightcade/fc2-electron/resources/app/inject/inject.css", self.css)
        self.js = "/* keyboard */\n"
        self.write("opt/Fightcade/fc2-electron/resources/app/inject/inject.js", self.js)
        self.exe("opt/Fightcade/emulator/fcade", "#!/bin/bash\necho \"fcade $* XCURSOR_THEME=$XCURSOR_THEME\" >> \"$FAKE_LOG\"\n"
                 "echo \"fcade: opening $1\"\n")
        self.exe("opt/Fightcade/emulator/fbneo/fcadefbneo.exe", "MZ")
        self.write("binfmt/hk-wine", BINFMT_ENTRY % (self.inst + "/bin"))
        self.write("opt/fcadefbneo.ini", "// base ini\r\nnVidSelect 4\r\n")
        for name in ("hk-fightcade-wrap", "hk-fightcade-prefix", "hk-fightcade-unpack"):
            shutil.copy(os.path.join(FILES, name), self.p("opt/bin", name))
        for name in ("wine", "wineserver", "fcade-url"):
            os.symlink("hk-fightcade-wrap", self.p("opt/bin", name))
        self.exe("fakebin/systemctl", FAKE_SYSTEMCTL)
        self.as_user()
        self.stamp = "wine=test recipe=1"
        self.conf_values = {}
        self.write_conf()

    def p(self, *rel):
        return os.path.join(self.d, *rel)

    def write(self, rel, text):
        path = self.p(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def exe(self, rel, text):
        os.chmod(self.write(rel, text), 0o755)

    def as_root(self):
        """`id -u` says 0 from now on (the #215 refusals)."""
        self.exe("fakebin/id", FAKE_ID_ROOT)

    def as_user(self):
        """`id -u` says the cabinet user: the real `id` when the tests run as a user, a fake one when they run as root (#269)."""
        if AS_ROOT:
            self.exe("fakebin/id", FAKE_ID_USER)
        elif os.path.exists(self.p("fakebin/id")):
            os.remove(self.p("fakebin/id"))

    def write_conf(self, **over):
        v = {
            "HK_FC_DIR": self.inst, "HK_FC_BINDIR": self.inst + "/bin", "HK_FC_BOX64": self.inst + "/box64/box64",
            "HK_FC_BOX64_RC": self.inst + "/box64/box64.box64rc", "HK_FC_BOX64_LIB": self.inst + "/box64/x64lib",
            "HK_FC_WINE": self.inst + "/wine", "HK_FC_ELECTRON": self.inst + "/electron/electron",
            "HK_FC_TREE": self.inst + "/Fightcade", "HK_FC_APP": self.inst + "/Fightcade/fc2-electron/resources/app",
            "HK_FC_CSS": self.inst + "/Fightcade/fc2-electron/resources/app/inject/inject.css",
            "HK_FC_CSS_SHA256": hashlib.sha256(self.css.encode()).hexdigest(),
            "HK_FC_JS": self.inst + "/Fightcade/fc2-electron/resources/app/inject/inject.js",
            "HK_FC_JS_SHA256": hashlib.sha256(self.js.encode()).hexdigest(),
            "HK_FC_INI_BASE": self.inst + "/fcadefbneo.ini", "HK_FC_INI": self.data + "/fbneo/config/fcadefbneo.ini",
            "HK_FC_PREFIX": self.data + "/prefix", "HK_FC_PREFIX_STAMP": "'%s'" % self.stamp,
            "HK_FC_SHM_PREFIX": self.shm + "/fcprefix", "HK_FC_PROFILE": self.data + "/profile",
            "HK_FC_LOGS": self.data + "/logs", "HK_FC_UNIT": "coin-dojo-fightcade", "HK_FC_SCOPE": "0",
            "HK_FC_BINFMT": self.p("binfmt/hk-wine"),
            "HK_FC_SCOPE_PROPS": "()", "HK_FC_ELECTRON_FLAGS": "()", "HK_FC_CURSOR": "DMZ-White", "HK_FC_STICKMOUSE": "coin-dojo-stickmouse.service",
            "HK_FC_BOX64_FAST": "0", "HK_FC_BOX64_FAST_ENV": "(BOX64_DYNAREC_STRONGMEM=0 BOX64_DYNAREC_BIGBLOCK=3)",
            "HK_FC_SYSTEMCTL": self.p("fakebin", "systemctl"),
            # #233: no pactl here unless a test writes fakebin/pactl; a DRM dir with no connectors
            "HK_FC_AUDIO_SINK": "auto", "HK_FC_PACTL": self.p("fakebin", "pactl"), "HK_FC_DRM_DIR": self.p("drm"),
        }
        v.update(over)
        self.conf_values = v
        self.write("fightcade.conf", "".join("%s=%s\n" % kv for kv in v.items()))

    def build_prefix(self):
        return self.run([self.p("opt/bin/hk-fightcade-prefix"), self.data + "/prefix", self.stamp])

    def env(self, **extra):
        e = {"PATH": self.p("fakebin") + ":/usr/bin:/bin:/usr/sbin:/sbin", "HOME": self.d,
             "COIN_DOJO_FIGHTCADE_CONF": self.p("fightcade.conf"), "FAKE_LOG": self.log, "FAKE_SEEN": self.seen,
             "FAKE_INI": self.data + "/fbneo/config/fcadefbneo.ini", "DISPLAY": ":0", "XCURSOR_THEME": "blank"}
        e.update(extra)
        return {k: v for k, v in e.items() if v is not None}

    def run(self, argv, timeout=60, **extra):
        return subprocess.run([BASH] + argv, capture_output=True, text=True, env=self.env(**extra), timeout=timeout)

    def launch(self, *args, **extra):
        return self.run([self.p("launcher")] + list(args), **extra)

    def seen_map(self):
        try:
            return dict(line.split("=", 1) for line in open(self.seen).read().splitlines() if "=" in line)
        except OSError:
            return {}

    def fake_log(self):
        try:
            return open(self.log).read()
        except OSError:
            return ""

    def close(self):
        for root, dirs, _ in os.walk(self.d):
            for x in dirs:
                try:
                    os.chmod(os.path.join(root, x), 0o755)
                except OSError:
                    pass
        shutil.rmtree(self.d, ignore_errors=True)


def _cab():
    c = Cab()
    shutil.copy(LAUNCHER, c.p("launcher"))
    r = c.build_prefix()
    _check(r.returncode == 0, "prefix builder: builds the base prefix with the fake Wine (rc %d)%s"
           % (r.returncode, "" if r.returncode == 0 else " " + r.stderr[-300:]))
    return c


def test_harness_user():
    """#269: the fake cabinet runs its scripts as a non-root user wherever the tests run, without HK_FC_ALLOW_ROOT,
    and the root refusals still fire for a real or faked uid 0."""
    c = _cab()
    try:
        _check("HK_FC_ALLOW_ROOT" not in c.env(), "harness #269: the fake cabinet never sets HK_FC_ALLOW_ROOT")
        r = c.run(["-c", "id -u"])
        _check(r.returncode == 0 and r.stdout.strip() not in ("", "0"),
               "harness #269: `id -u` in the fake cabinet is a user, not root (%r, tests as root: %s)" % (r.stdout.strip(), AS_ROOT))
        # an orphan that exits is reaped, not left a zombie that `kill -0` still finds (_reap_orphans in a container)
        orphan = int(subprocess.run(["sh", "-c", "sleep 0.2 >/dev/null 2>&1 & echo $!"], capture_output=True, text=True).stdout)
        gone = False
        for _ in range(60):
            time.sleep(0.05)
            if not _alive(orphan):
                gone = True
                break
        _check(gone, "harness #269: an exited orphan is reaped (no zombie for the launcher's kill -0 to count)")
        tools = (("hk-fightcade-prefix", [c.data + "/prefix", c.stamp]), ("hk-fightcade-unpack", ["a", "0" * 64, c.p("x"), "s"]))
        # BROKEN: `id -u` says 0 -> both refuse without the escape
        c.as_root()
        for name, args in tools:
            r = c.run([c.p("opt/bin", name)] + args)
            _check(r.returncode == 2 and "refusing to run as root" in r.stderr, "BROKEN harness #269: %s refuses uid 0 (rc %d)" % (name, r.returncode))
        # BROKEN, real root (the Forgejo job): no fake id at all -> the real uid 0 is refused too
        if AS_ROOT:
            os.remove(c.p("fakebin/id"))
            for name, args in tools:
                r = c.run([c.p("opt/bin", name)] + args)
                _check(r.returncode == 2 and "refusing to run as root" in r.stderr,
                       "BROKEN harness #269: %s refuses the real root without HK_FC_ALLOW_ROOT (rc %d)" % (name, r.returncode))
            r = c.launch()
            _check(r.returncode == 6 and "refusing to run as root" in r.stderr and not os.path.exists(c.seen),
                   "BROKEN harness #269: the launcher refuses the real root, starts nothing (rc %d)" % r.returncode)
        else:
            skip("harness #269: the real-root refusal runs where the tests run as root (the Forgejo job, tools/ci-repro.sh)")
        c.as_user()
    finally:
        c.close()


def test_prefix_builder():
    c = _cab()
    try:
        pre = c.data + "/prefix"
        _check(open(pre + "/.hk-stamp").read().strip() == c.stamp, "prefix builder: stamp written last")
        _check('"csmt"=dword:00000000' in open(pre + "/user.reg").read(), "prefix builder: Direct3D csmt=0 in user.reg")
        _check('"Map Controllers"=dword:00000000' in open(pre + "/system.reg").read(),
               "prefix builder #230: winebus Map Controllers=0 in system.reg (sticks as plain joysticks)")
        _check(sorted(os.listdir(pre + "/dosdevices")) == ["c:", "z:"], "prefix builder: only drives c: and z: stay")
        log = c.fake_log()
        _check("WINEPREFIX=%s wineboot -i" % pre in log and "box64 " in log, "prefix builder: wineboot -i through box64")
        _check("wineserver WINEPREFIX=%s -w" % pre in log, "prefix builder: waits for wineserver")
        open(c.log, "w").close()
        r = c.build_prefix()
        _check(r.returncode == 0 and "is current" in r.stderr and "wineboot" not in c.fake_log(),
               "prefix builder: a current stamp is a no-op")
        # BROKEN: wineboot leaves no syswow64 (the Mono/Gecko hang) -> fails, no stamp
        c.stamp = "wine=other recipe=1"
        r = c.run([c.p("opt/bin/hk-fightcade-prefix"), pre, c.stamp], FAKE_BAD_WINEBOOT="1")
        _check(r.returncode == 1 and "wineboot incomplete" in r.stderr and not os.path.exists(pre + "/.hk-stamp"),
               "BROKEN prefix: an incomplete wineboot fails and leaves no stamp (rc %d)" % r.returncode)
        # BROKEN (#230): the winebus reg add fails -> the build fails, no stamp (a prefix without the setting would
        # hand FBNeo a remapped Xbox-style pad and the presets would point at other buttons)
        c.stamp = "wine=other recipe=2"
        r = c.run([c.p("opt/bin/hk-fightcade-prefix"), pre, c.stamp], FAKE_REG_FAIL="winebus")
        _check(r.returncode == 1 and "Map Controllers" in r.stderr and not os.path.exists(pre + "/.hk-stamp"),
               "BROKEN prefix #230: a failed winebus Map Controllers write fails the build, no stamp (rc %d)" % r.returncode)
        r = c.run([c.p("opt/bin/hk-fightcade-prefix"), "relative/prefix", "x"])
        _check(r.returncode == 2, "BROKEN prefix: a relative path is refused")
    finally:
        c.close()


def _wineboots(c):
    """How many times the fake Wine itself ran wineboot -i (box64 logs its own line too)."""
    return len([x for x in c.fake_log().splitlines() if x.startswith("wine WINEPREFIX=") and x.endswith(" wineboot -i")])


def test_prefix_wineboot_log_and_retry():
    """#216: wineboot's output is kept, its rc decoded, rc 137 retried once from an empty prefix."""
    c = _cab()
    try:
        pre, logs = c.data + "/prefix", c.data + "/logs"
        log = logs + "/wineboot.log"
        build = [c.p("opt/bin/hk-fightcade-prefix"), pre]
        _check(os.path.isfile(log) and "fake wineboot WINEDEBUG=err+all BOX64_LOG=1" in open(log).read()
               and "wineboot rc 0" in open(log).read(),
               "prefix #216: wineboot's own output is kept in HK_FC_LOGS/wineboot.log, run with err+all and BOX64_LOG=1")
        _check(stat.S_IMODE(os.stat(log).st_mode) == 0o600, "prefix #216: the wineboot log is 0600")

        # SIGKILL once -> one retry after wineserver -k, from an empty prefix; the build passes
        open(c.log, "w").close()
        r = c.run(build + ["wine=kill-once"], FAKE_WINEBOOT_KILL="once")
        text = open(log).read() if os.path.exists(log) else ""
        flog = c.fake_log()
        _check(r.returncode == 0 and open(pre + "/.hk-stamp").read().strip() == "wine=kill-once",
               "prefix #216: a single SIGKILL is retried once and the build passes (rc %d)%s"
               % (r.returncode, "" if r.returncode == 0 else " " + r.stderr[-500:]))
        _check(_wineboots(c) == 2 and "retrying once from an empty prefix" in r.stderr,
               "prefix #216: exactly two wineboot runs, the retry is announced")
        _check(flog.find("wineserver WINEPREFIX=%s -k" % pre) > flog.find(" wineboot -i"),
               "prefix #216: wineserver -k between the killed wineboot and the retry")
        _check(not os.path.exists(pre + "/drive_c/half-built"), "prefix #216: the retry starts from an empty prefix")
        _check("attempt 1 of 2" in text and "attempt 2 of 2" in text and "killed by SIGKILL (rc 137" in text
               and "err:fake:box64 about to be killed" in text and "still running" in text,
               "prefix #216: the log keeps both attempts, names the SIGKILL and the wine/box64 processes left")

        # BROKEN: SIGKILL every time -> fails after the one retry, says signal (not timeout), prints the log's tail
        os.remove(c.log + ".killed")
        open(c.log, "w").close()
        r = c.run(build + ["wine=kill-always"], FAKE_WINEBOOT_KILL="always")
        _check(r.returncode == 1 and _wineboots(c) == 2 and not os.path.exists(pre + "/.hk-stamp"),
               "BROKEN prefix #216: SIGKILL twice fails (rc 1) after one retry, no stamp (rc %d, %d runs)"
               % (r.returncode, _wineboots(c)))
        _check("killed by SIGKILL (rc 137 = 128+9; not the 900 s timeout, which is rc 124)" in r.stderr
               and "attempt 2 of 2" in r.stderr and "full log: %s" % log in r.stderr,
               "BROKEN prefix #216: the error says SIGKILL, not the timeout, and names the log")
        _check("  | " in r.stderr and "err:fake:box64 about to be killed" in r.stderr,
               "BROKEN prefix #216: the error prints the log's tail")
        _check(os.path.isfile(log + ".1") and "attempt 2 of 2" in open(log + ".1").read(),
               "prefix #216: the previous build's log stays as wineboot.log.1")

        # BROKEN: the timeout's rc 124 -> no retry, says timeout
        open(c.log, "w").close()
        r = c.run(build + ["wine=timeout"], FAKE_WINEBOOT_RC="124")
        _check(r.returncode == 1 and _wineboots(c) == 1 and "timed out after 900 s" in r.stderr
               and "err:fake:wineboot fails with 124" in r.stderr,
               "BROKEN prefix #216: a timeout (rc 124) is not retried and is called a timeout")
        # BROKEN: another signal and a plain rc -> named, not retried
        open(c.log, "w").close()
        r = c.run(build + ["wine=segv"], FAKE_WINEBOOT_RC="139")
        _check(r.returncode == 1 and _wineboots(c) == 1 and "killed by signal SIGSEGV (rc 139 = 128+11)" in r.stderr,
               "BROKEN prefix #216: rc 139 is named SIGSEGV, not retried")
        open(c.log, "w").close()
        r = c.run(build + ["wine=rc3"], FAKE_WINEBOOT_RC="3")
        _check(r.returncode == 1 and _wineboots(c) == 1 and "exited with rc 3" in r.stderr,
               "BROKEN prefix #216: a plain rc 3 is not retried")

        # no usable HK_FC_LOGS -> the log goes next to the prefix (never inside it: the prefix is wiped)
        c.write_conf(HK_FC_LOGS=c.p("fake.log") + "/logs")
        r = c.run(build + ["wine=nologs"])
        _check(r.returncode == 0 and os.path.isfile(c.data + "/wineboot.log"),
               "prefix #216: without a writable HK_FC_LOGS the log is next to the prefix (rc %d)" % r.returncode)
        c.write_conf()
        # idempotent: a current stamp runs no wineboot and leaves the log alone
        before = open(log).read() if os.path.exists(log) else None
        open(c.log, "w").close()
        r = c.run(build + ["wine=nologs"])
        _check(r.returncode == 0 and _wineboots(c) == 0 and (open(log).read() if os.path.exists(log) else None) == before,
               "prefix #216: a current prefix runs nothing and keeps the last log")
    finally:
        c.close()


def test_launcher_runs_the_lobby():
    c = _cab()
    try:
        r = c.launch("--check")
        _check(r.returncode == 0 and "install ok" in r.stderr, "launcher --check: a complete install passes (rc %d)%s"
               % (r.returncode, "" if r.returncode == 0 else " " + r.stderr[-400:]))
        _check(not os.path.exists(c.seen), "launcher --check starts nothing")
        c.write("data/fbneo/config/fcadefbneo.ini", "nVidSelect 0 (what FBNeo wrote on exit)\n")
        r = c.launch(FAKE_STICKMOUSE="1")
        s = c.seen_map()
        _check(r.returncode == 0, "launcher: runs and returns when the lobby exits (rc %d)%s"
               % (r.returncode, "" if r.returncode == 0 else " " + r.stderr[-400:]))
        _check(s.get("prefix") == "readonly" and s.get("dosdevices") == "readonly", "launcher: the RAM prefix is read-only")
        _check(s.get("WINEPREFIX") == c.shm + "/fcprefix", "launcher: WINEPREFIX = the RAM prefix")
        _check(s.get("c") == c.data + "/prefix/drive_c" and s.get("z") == "/", "launcher: c: = base drive_c, z: = /")
        _check("system.reg" in s.get("files", "") and "user.reg" in s.get("files", ""), "launcher: registry copied into RAM")
        _check(s.get("ini") == "// base ini", "launcher: a fresh fcadefbneo.ini from the role's base")
        _check(s.get("XCURSOR_THEME") == "DMZ-White" and s.get("GAME_XCURSOR") == "blank",
               "launcher: visible cursor for the lobby only, the game keeps blank")
        _check(s.get("PATH0") == c.inst + "/bin", "launcher: the wine wrapper is first on PATH")
        _check(s.get("STRONGMEM") == "unset", "launcher: no Box64 fast flags by default")
        _check(s.get("PULSE_SINK") == "unset" and "WARN: audio: no PulseAudio sinks" in r.stderr,
               "launcher #233: no pactl = no PULSE_SINK, says so, still runs")
        _check("--no-sandbox --user-data-dir=%s/profile %s" % (c.data, c.conf_values["HK_FC_APP"]) in s.get("args", ""),
               "launcher: Electron with the persistent profile and Fightcade's app")
        _check(s.get("cwd") == c.inst + "/Fightcade/fc2-electron", "launcher: runs from fc2-electron like Fightcade2.sh")
        _check(not os.path.exists(c.shm + "/fcprefix"), "launcher: the RAM prefix is gone after the lobby exits")
        log = c.fake_log()
        _check("systemctl --user start --no-block coin-dojo-stickmouse.service" in log and
               "systemctl --user stop --no-block coin-dojo-stickmouse.service" in log,
               "launcher: stick-as-mouse started and stopped")
        _check("wineserver WINEPREFIX=%s/fcprefix -k" % c.shm in log, "launcher: wineserver -k on exit")
        c.write_conf(HK_FC_ELECTRON_FLAGS="(--disable-gpu)")
        r = c.launch()
        _check("--user-data-dir=%s/profile --disable-gpu %s" % (c.data, c.conf_values["HK_FC_APP"]) in c.seen_map().get("args", ""),
               "launcher: extra Electron switches (the VM's --disable-gpu) go before the app")
        c.write_conf()
        r = c.launch(FAKE_ELECTRON_RC="3")
        _check(r.returncode == 3, "launcher: a lobby crash is returned to the session (rc %d)" % r.returncode)

        c.write_conf(HK_FC_BOX64_FAST="1")
        open(c.log, "w").close()
        r = c.launch()
        _check(r.returncode == 0 and c.seen_map().get("STRONGMEM") == "0" and "fast flags ON" in r.stderr,
               "launcher: hk_fightcade_box64_fast exports the fast flags")
        _check("start --no-block coin-dojo-stickmouse" not in c.fake_log(), "launcher: no stick-as-mouse when not installed")

        # the systemd user scope (fake systemd-run): stale scope stopped first, then the unit name of the contract
        c.write_conf(HK_FC_SCOPE="1")
        c.exe("fakebin/systemd-run", FAKE_SYSTEMD_RUN)
        open(c.log, "w").close()
        r = c.launch(FAKE_USER_MANAGER="1", FAKE_STALE_SCOPE="1")
        log = c.fake_log()
        _check(r.returncode == 0 and "systemd-run --user --scope --collect --quiet --unit=coin-dojo-fightcade -- " in log,
               "launcher: runs in the systemd user scope coin-dojo-fightcade (rc %d)" % r.returncode)
        _check("systemctl --user stop coin-dojo-fightcade.scope" in log, "launcher: a stale scope is stopped first")
        os.remove(c.p("fakebin/systemd-run"))
        r = c.launch(FAKE_USER_MANAGER="1")
        _check(r.returncode == 0 and "running without the coin-dojo-fightcade scope" in r.stderr,
               "launcher: no systemd-run = runs without the scope, says so")
    finally:
        c.close()


FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
# REAL cabinet output, read-only, 2026-11-19 01:xx (Coordinator): `LC_ALL=C pactl list sinks`, byte for byte, and the DRM
# connector states, client.conf autospawn line and default sink (cabinet-drm-pulse.txt).
CAB_SINKS = os.path.join(FIXTURES, "cabinet-pactl-list-sinks.txt")
CAB_DRM = os.path.join(FIXTURES, "cabinet-drm-pulse.txt")
HDMI1 = "alsa_output.platform-107c706400.hdmi.hdmi-stereo"   # the cabinet's sink (alsa.card_name vc4-hdmi-1)
HDMI0 = "alsa_output.platform-107c701400.hdmi.hdmi-stereo"   # DERIVED: the other port's sink, see _two_sinks


def _cab_section(name):
    """Lines of one `=== name` section of the cabinet capture."""
    out, cur = [], None
    for line in open(CAB_DRM).read().splitlines():
        if line.startswith("=== "):
            cur = line[4:]
        elif cur == name and line:
            out.append(line)
    return out


def _two_sinks():
    """DERIVED, not captured: the cabinet's list with a second HDMI sink, as if both ports had a card (the real HDMI
    block copied with HDMI0's address, card 0 and vc4-hdmi-0). Only for the several-sinks path."""
    real = open(CAB_SINKS).read()
    hdmi_block = real.split("\n\n")[0]
    other = (hdmi_block.replace("107c706400", "107c701400").replace("vc4-hdmi-1", "vc4-hdmi-0")
             .replace('alsa.card = "1"', 'alsa.card = "0"').replace("Sink #0", "Sink #2").replace("/card1", "/card0"))
    return real + "\n" + other + "\n"


def test_cabinet_capture():
    """The fixtures are the cabinet's own words: one HDMI sink on vc4-hdmi-1, HDMI-A-2 connected, autospawn default."""
    real = open(CAB_SINKS).read()
    _check(re.findall(r"(?m)^\tName: (\S+)$", real) == [HDMI1, "combined"], "capture: sinks = the HDMI sink + combined")
    _check('\t\talsa.card_name = "vc4-hdmi-1"\n' in real, "capture: the HDMI sink is ALSA card vc4-hdmi-1")
    _check(_cab_section("drm")[:2] == ["card1-HDMI-A-1 disconnected", "card1-HDMI-A-2 connected"],
           "capture: HDMI-A-2 connected = vc4-hdmi-(2-1), the N-1 mapping")
    _check(_cab_section("client.conf") == ["; autospawn = yes"], "capture: autospawn left at libpulse's default (yes)")
    _check(_cab_section("default") == [HDMI1], "capture: the hand-set default sink (tonight's pactl move)")


def test_launcher_audio_sink():
    """#233: Fightcade mode plays to one HDMI sink (PULSE_SINK), never the image's `combined` combine-sink."""
    c = _cab()
    try:
        c.exe("fakebin/pactl", FAKE_PACTL)
        c.exe("fakebin/timeout", FAKE_TIMEOUT)
        two = c.write("two-sinks.txt", _two_sinks())
        empty = c.write("no-sinks.txt", "")
        only_combined = c.write("combined.txt", open(CAB_SINKS).read().split("\n\n", 1)[1])

        def drm(states):
            shutil.rmtree(c.p("drm"), ignore_errors=True)
            for line in states:
                conn, st = line.split()
                c.write("drm/%s/status" % conn, st + "\n")

        def sink(conf=None, **env):
            c.write_conf(**(conf or {}))
            open(c.log, "w").close()
            r = c.launch(**env)
            return r, c.seen_map().get("PULSE_SINK")

        # the cabinet as captured: one HDMI sink + combined, HDMI-A-2 connected
        drm(_cab_section("drm"))
        r, s = sink(FAKE_PACTL_FILE=CAB_SINKS)
        log = c.fake_log()
        _check(r.returncode == 0 and s == HDMI1 and "audio: PULSE_SINK=%s" % HDMI1 in r.stderr,
               "audio #233: the real cabinet list -> the HDMI sink, not combined (%s)" % s)
        _check("timeout 3\npactl list sinks LC_ALL=C autospawn=autospawn = no" in log and log.count("pactl ") == 1,
               "audio #233: one pactl call, under timeout 3, LC_ALL=C, with autospawn = no (%r)" % log[-300:])
        # several HDMI sinks (derived): the connected port's card wins
        r, s = sink(FAKE_PACTL_FILE=two)
        _check(s == HDMI1, "audio #233: two HDMI sinks, HDMI-A-2 connected -> vc4-hdmi-1's sink (%s)" % s)
        drm(["card1-HDMI-A-1 connected", "card1-HDMI-A-2 disconnected", "card1-Writeback-1 unknown"])
        r, s = sink(FAKE_PACTL_FILE=two)
        _check(s == HDMI0, "audio #233: two HDMI sinks, HDMI-A-1 connected -> vc4-hdmi-0's sink (%s)" % s)
        drm(_cab_section("drm"))
        # an explicit sink name (hk_fightcade_audio_sink)
        r, s = sink({"HK_FC_AUDIO_SINK": HDMI1}, FAKE_PACTL_FILE=CAB_SINKS)
        _check(s == HDMI1, "audio #233: an explicit sink name is used (%s)" % s)
        r, s = sink({"HK_FC_AUDIO_SINK": "combined"}, FAKE_PACTL_FILE=CAB_SINKS)
        _check(s == "combined", "audio #233: an explicit name may even be combined (the negative control) (%s)" % s)
        # default: never overrides, pactl not even asked
        r, s = sink({"HK_FC_AUDIO_SINK": "default"}, FAKE_PACTL_FILE=CAB_SINKS)
        _check(r.returncode == 0 and s == "unset" and "pactl" not in c.fake_log(), "audio #233: default = no override, no pactl")

        # BROKEN: a daemon that never answers -> killed after 3 s, default kept, the lobby still starts
        t0 = time.time()
        r, s = sink(FAKE_PACTL_FILE=CAB_SINKS, FAKE_PACTL_HANG="1")
        took = time.time() - t0
        _check(r.returncode == 0 and s == "unset" and "no answer in 3 s" in r.stderr and took < 15,
               "BROKEN audio #233: a hung pactl is cut at 3 s and the lobby starts (%.1f s)" % took)
        # BROKEN: an explicit sink that does not exist -> default kept, warned, the lobby still runs
        r, s = sink({"HK_FC_AUDIO_SINK": "alsa_output.nope"}, FAKE_PACTL_FILE=CAB_SINKS)
        _check(r.returncode == 0 and s == "unset" and "sink alsa_output.nope not found" in r.stderr,
               "BROKEN audio #233: a missing explicit sink keeps the default and warns")
        # BROKEN: two HDMI sinks and both ports connected -> no guess
        drm(["card1-HDMI-A-1 connected", "card1-HDMI-A-2 connected"])
        r, s = sink(FAKE_PACTL_FILE=two)
        _check(r.returncode == 0 and s == "unset" and "2 HDMI connectors connected" in r.stderr,
               "BROKEN audio #233: two connected ports = no guess, default kept, warned (%s)" % s)
        # BROKEN: two HDMI sinks and nothing connected -> no guess
        drm(["card1-HDMI-A-1 disconnected", "card1-HDMI-A-2 disconnected"])
        r, s = sink(FAKE_PACTL_FILE=two)
        _check(s == "unset" and "0 HDMI connectors connected" in r.stderr, "BROKEN audio #233: no connected port = no guess")
        # BROKEN: two HDMI sinks, but the connected port's card has none -> default kept
        drm(["card1-HDMI-A-3 connected"])
        r, s = sink(FAKE_PACTL_FILE=two)
        _check(s == "unset" and "no HDMI sink for vc4-hdmi-2" in r.stderr, "BROKEN audio #233: no sink for the connected card")
        # BROKEN: only combined, or no sinks at all -> default kept
        r, s = sink(FAKE_PACTL_FILE=only_combined)
        _check(s == "unset" and "no HDMI sink" in r.stderr, "BROKEN audio #233: only combined = default kept (%s)" % s)
        r, s = sink(FAKE_PACTL_FILE=empty)
        _check(s == "unset" and "no PulseAudio sinks" in r.stderr, "BROKEN audio #233: an empty list = default kept")
    finally:
        c.close()


def test_launcher_term():
    c = _cab()
    try:
        p = subprocess.Popen([BASH, c.p("launcher")], env=c.env(FAKE_ELECTRON_WAIT="1"),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(100):
            if os.path.exists(c.seen + ".pid"):
                break
            time.sleep(0.05)
        epid = int(open(c.seen + ".pid").read())
        t0 = time.time()
        p.send_signal(signal.SIGTERM)
        rc = p.wait(timeout=10)
        took = time.time() - t0
        _check(rc == 143 and took < 4.5, "launcher: TERM ends Fightcade mode promptly (rc %d in %.1f s, session KILLs at 5 s)" % (rc, took))
        alive = subprocess.run(["kill", "-0", str(epid)], capture_output=True).returncode == 0
        _check(not alive, "launcher: TERM also stops the lobby")
        _check(not os.path.exists(c.shm + "/fcprefix"), "launcher: TERM removes the RAM prefix")
    finally:
        c.close()


def _wsrig(c):
    proc = c.p("fakeproc")
    os.makedirs(proc, exist_ok=True)
    return proc


def _alive(pid):
    return subprocess.run(["kill", "-0", str(pid)], capture_output=True).returncode == 0


def _fake_ws_pids(proc):
    return [int(n) for n in os.listdir(proc) if n.isdigit() and _alive(int(n))]


def _start_waiting(c, proc, **extra):
    """Launcher with a lobby that waits; returns (Popen, stderr path) once the persistent wineserver is up or 6 s passed."""
    errp = c.p("launcher.err")
    p = subprocess.Popen([BASH, c.p("launcher")], env=c.env(FAKE_ELECTRON_WAIT="1", FAKE_WS_PROC=proc, HK_FC_PROC=proc,
                         HK_FC_WINE_TMP=c.p("winetmp"), **extra), stdout=subprocess.DEVNULL, stderr=open(errp, "w"))
    for _ in range(120):
        if os.path.exists(c.seen + ".pid") and ("persistent wineserver" in open(errp).read() or "did not come up" in open(errp).read()):
            break
        time.sleep(0.05)
    return p, errp


def test_launcher_persistent_wineserver_lifecycle():
    """#257: started after the RAM prefix exists, one instance, ended on every way out, a leak is loud."""
    c = _cab()
    try:
        proc = _wsrig(c)
        p, errp = _start_waiting(c, proc)
        log = c.fake_log()
        wsp = _fake_ws_pids(proc)
        _check("-p ready=1" in log, "start: wineserver -p runs only once the RAM prefix is built (%s)" % log.strip().splitlines()[-1:])
        _check(len(wsp) == 1 and "persistent wineserver up (pid %d" % wsp[0] in open(errp).read(), "start: exactly one wineserver, logged (%s)" % wsp)
        _check(c.seen_map().get("WINEPREFIX") == c.shm + "/fcprefix", "start: the lobby came up as usual alongside it")
        p.send_signal(signal.SIGTERM)
        rc = p.wait(timeout=10)
        err = open(errp).read()
        _check(rc == 143 and not _fake_ws_pids(proc), "exit (TERM, the SELECT+START leave): the wineserver is gone (rc %d)" % rc)
        _check("-k ready" in c.fake_log() and "no wine processes left" in err and "leak" not in err, "exit: wineserver -k, journal says no leak (%s)" % err[-200:])

        # BROKEN: wineserver -k does nothing (hung, wrong prefix): the final reap ends it and the journal says LEAK
        open(c.log, "w").close()
        p, errp = _start_waiting(c, proc, FAKE_WS_K_FAILS="1")
        wsp = _fake_ws_pids(proc)
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=10)
        err = open(errp).read()
        _check(wsp and not _fake_ws_pids(proc) and "WARN: leak: Fightcade mode ended with wine/box64 processes alive" in err and "still alive" in err,
               "BROKEN exit: -k failed, the survivor is TERMed and the leak is logged (%s)" % err[-300:])

        # BROKEN: the lobby crashes (rc 3): same cleanup
        open(c.log, "w").close()
        r = c.launch(FAKE_WS_PROC=proc, HK_FC_PROC=proc, FAKE_ELECTRON_RC="3", HK_FC_WINE_TMP=c.p("winetmp"))
        _check(r.returncode == 3 and not _fake_ws_pids(proc), "BROKEN exit: the lobby crashes (rc %d): nothing left" % r.returncode)
    finally:
        c.close()


def test_launcher_wineserver_start_is_solid():
    c = _cab()
    try:
        proc = _wsrig(c)
        # a crashed earlier run (launcher KILLed, no cleanup) left a wineserver and a stale socket for the same prefix
        os.makedirs(c.shm + "/fcprefix")
        st = os.stat(c.shm + "/fcprefix")
        oursock = "server-%x-%x" % (st.st_dev, st.st_ino)
        os.makedirs(c.p("winetmp/server-x"))
        os.makedirs(c.p("winetmp/" + oursock))
        class Orphan:   # not our child: init reaps it, like the real leftover of a crashed run (a child would stay a zombie)
            def __init__(self):
                self.pid = int(subprocess.run(["sh", "-c", "sleep 60 >/dev/null 2>&1 & echo $!"], capture_output=True, text=True).stdout)

            def poll(self):
                return None if _alive(self.pid) else 0

            def wait(self, timeout=None):
                for _ in range(int((timeout or 1) * 20)):
                    if self.poll() is not None:
                        return
                    time.sleep(0.05)

            def kill(self):
                try:
                    os.kill(self.pid, signal.SIGKILL)
                except OSError:
                    pass
        stale, other = Orphan(), Orphan()
        try:
            for pid, pre in ((stale.pid, c.shm + "/fcprefix"), (other.pid, "/some/other/prefix")):
                os.makedirs("%s/%d" % (proc, pid))
                open("%s/%d/environ" % (proc, pid), "wb").write(("WINEPREFIX=%s\0" % pre).encode())
                open("%s/%d/cmdline" % (proc, pid), "wb").write(b"box64\0wineserver\0-p\0")
                open("%s/%d/comm" % (proc, pid), "w").write("wineserver\n")
            p, errp = _start_waiting(c, proc)
            err = open(errp).read()
            stale.wait(timeout=5)
            _check(stale.poll() is not None and "stale wine processes from an earlier Fightcade mode" in err and "TERM" in err,
                   "stale wineserver of the same prefix from a crash is reaped before the new start, and logged")
            _check(other.poll() is None, "BROKEN guard: a process of another WINEPREFIX is never touched")
            live = [pid for pid in _fake_ws_pids(proc) if pid not in (stale.pid, other.pid)]
            _check(len(live) == 1, "second start after a crash: exactly one new wineserver (%s)" % live)
            _check(not os.path.exists(c.p("winetmp/" + oursock)) and os.path.exists(c.p("winetmp/server-x")),
               "the old prefix's own /tmp/.wine-<uid>/server-<dev>-<ino> socket is removed, another prefix's stays")
            p.send_signal(signal.SIGTERM)
            p.wait(timeout=10)
            _check(not [x for x in _fake_ws_pids(proc) if x != other.pid], "and nothing of ours is left after the exit")
            _check("leak" not in open(errp).read().split("TERM: leaving")[-1], "the start's stale reap is not blamed on the exit (no leak line)")
        finally:
            for x in (stale, other):
                x.kill()
                x.wait()

        # two starts in a row (the first ended normally): again exactly one
        for _ in range(2):
            p, errp = _start_waiting(c, proc)
            _check(len([x for x in _fake_ws_pids(proc) if x != other.pid]) == 1, "start in a row: one wineserver")
            p.send_signal(signal.SIGTERM)
            p.wait(timeout=10)

        # BROKEN: wineserver -p fails: the lobby still runs, the journal says what happens instead
        os.remove(c.seen)
        p, errp = _start_waiting(c, proc, FAKE_WS_FAIL="1")
        err = open(errp).read()
        _check(os.path.exists(c.seen) and "did not come up in 3 s: the first game starts wine's own" in err,
               "BROKEN start: -p fails: the lobby ran anyway and the journal names the fallback (%s)" % err[-200:])
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=10)

        # BROKEN: it dies mid-session: the exit says so
        p, errp = _start_waiting(c, proc)
        for pid in _fake_ws_pids(proc):
            os.kill(pid, signal.SIGKILL)
        time.sleep(0.2)
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=10)
        _check("the persistent wineserver was gone before the end" in open(errp).read(), "BROKEN: a wineserver that died mid-session is named at the exit")

        # off switch and no delay
        c.write_conf(HK_FC_WINESERVER_PERSIST="0")
        open(c.log, "w").close()
        r = c.launch(FAKE_WS_PROC=proc, HK_FC_PROC=proc)
        _check(" -p " not in c.fake_log() and r.returncode == 0, "HK_FC_WINESERVER_PERSIST=0: no persistent wineserver")
        c.write_conf()
        src = open(LAUNCHER).read()
        _check("bounded 3 \"$HK_FC_BINDIR/wineserver\" -p" in src and ") &\n  WSSTART=$!" in src,
               "the start runs in the background and bounded (the lobby never waits for it)")
    finally:
        c.close()


def _poll_log(c, needle, secs=8):
    for _ in range(int(secs * 20)):
        if needle in c.fake_log():
            return True
        time.sleep(0.05)
    return False


def test_launcher_wineserver_exit_is_airtight():
    """Critic on a34e894: a daemon that forks during the reap, TERM during the setup, the exit time, non-wine leftovers."""
    c = _cab()
    try:
        proc = _wsrig(c)
        # 1. BROKEN: the wineserver forks a NEW pid when it is TERMed (a `wineserver -p` still starting): caught on the next round
        p, errp = _start_waiting(c, proc, FAKE_WS_FORKER="1", FAKE_WS_K_FAILS="1")
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=10)
        err = open(errp).read()
        _check(not _fake_ws_pids(proc), "BROKEN exit: a wineserver forking a new pid during the reap is still caught (%s)" % _fake_ws_pids(proc))
        _check("(round 2)" in err and "no wine processes left" not in err, "and the journal says it took a second round, never 'no wine processes left'")
        m = re.search(r"finish took (\d+) ms", err)
        _check(m and int(m.group(1)) < 4500, "finish() logs how long it took, inside the session's 5 s (%s)" % (m.group(1) if m else None))
        for pid in _fake_ws_pids(proc):
            os.kill(pid, signal.SIGKILL)

        # 2. exit within a second of the start: the wineserver -p is still launching (0.8 s), its process group is killed
        for n in os.listdir(proc):
            shutil.rmtree(os.path.join(proc, n), ignore_errors=True)
        open(c.log, "w").close()
        p = subprocess.Popen([BASH, c.p("launcher")], env=c.env(FAKE_ELECTRON_WAIT="1", FAKE_WS_PROC=proc, HK_FC_PROC=proc,
                             FAKE_WS_SLOW="0.8", HK_FC_WINE_TMP=c.p("winetmp")), stdout=subprocess.DEVNULL, stderr=open(errp, "w"))
        _check(_poll_log(c, "-p ", 8), "exit within 1 s: wineserver -p was reached before the TERM")
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=10)
        time.sleep(1.2)
        _check(not _fake_ws_pids(proc), "exit within 1 s of the start: no wineserver appears after the mode ended (%s)" % _fake_ws_pids(proc))

        # 3. TERM during the setup (pactl is slow): no wineserver, no lobby
        c.exe("fakebin/pactl", FAKE_PACTL)
        for n in os.listdir(proc):
            shutil.rmtree(os.path.join(proc, n), ignore_errors=True)
        open(c.log, "w").close()
        if os.path.exists(c.seen):
            os.remove(c.seen)
        p = subprocess.Popen([BASH, c.p("launcher")], env=c.env(FAKE_PACTL_SLOW="1", FAKE_WS_PROC=proc, HK_FC_PROC=proc,
                             HK_FC_WINE_TMP=c.p("winetmp")), stdout=subprocess.DEVNULL, stderr=open(errp, "w"))
        _check(_poll_log(c, "pactl list sinks"), "setup reached the audio step")
        p.send_signal(signal.SIGTERM)
        rc = p.wait(timeout=10)
        _check(rc == 143 and not os.path.exists(c.seen) and " -p " not in c.fake_log() and not os.path.exists(c.shm + "/fcprefix"),
               "TERM during the setup: exit 143 at once, no wineserver started, no lobby, the prefix removed (rc %d)" % rc)
        # BROKEN pair: the same setup without the TERM does start both
        open(c.log, "w").close()
        r = c.launch(FAKE_PACTL_SLOW="1", FAKE_WS_PROC=proc, HK_FC_PROC=proc, HK_FC_WINE_TMP=c.p("winetmp"))
        _check(r.returncode == 0 and os.path.exists(c.seen) and " -p " in c.fake_log(), "BROKEN guard: without the TERM the lobby and the wineserver start")
        # TERM after the wineserver but before the lobby (a slow xset): wineserver started and ended, no lobby
        c.exe("fakebin/xset", "#!/bin/bash\necho xset-start >> \"$FAKE_LOG\"\nsleep 1.2\n")
        for n in os.listdir(proc):
            shutil.rmtree(os.path.join(proc, n), ignore_errors=True)
        os.remove(c.seen)
        open(c.log, "w").close()
        p = subprocess.Popen([BASH, c.p("launcher")], env=c.env(FAKE_WS_PROC=proc, HK_FC_PROC=proc, HK_FC_WINE_TMP=c.p("winetmp")),
                             stdout=subprocess.DEVNULL, stderr=open(errp, "w"))
        _poll_log(c, "xset-start")
        p.send_signal(signal.SIGTERM)
        rc = p.wait(timeout=10)
        _check(rc == 143 and not os.path.exists(c.seen) and not _fake_ws_pids(proc),
               "TERM before the lobby starts: 143, no lobby, and the wineserver that was started is ended (rc %d)" % rc)
        os.remove(c.p("fakebin/xset"))

        # 5. leftovers that are not wine are named as such, not called a wine leak
        c.write_conf(HK_FC_WINESERVER_PERSIST="0")
        p, errp = _start_waiting(c, proc, FAKE_WS_K_FAILS="1")
        # a leftover of this mode that is not wine appears while it runs (Electron's helper), after the start's own stale reap
        other = subprocess.run(["sh", "-c", "sleep 30 >/dev/null 2>&1 & echo $!"], capture_output=True, text=True).stdout.strip()
        os.makedirs("%s/%s" % (proc, other))
        open("%s/%s/environ" % (proc, other), "wb").write(("WINEPREFIX=%s\0" % (c.shm + "/fcprefix")).encode())
        open("%s/%s/cmdline" % (proc, other), "wb").write(b"/opt/electron --type=zygote\0")
        open("%s/%s/comm" % (proc, other), "w").write("electron\n")
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=10)
        err = open(errp).read()
        _check("not wine; reaped above" in err and "leak: Fightcade mode ended with wine" not in err, "a non-wine leftover is not called a wine leak (%s)" % err[-250:])
    finally:
        c.close()


def test_launcher_broken():
    c = _cab()
    try:
        # BROKEN: missing artifact -> exit 2 with a clear message (the session falls back to the menu)
        os.rename(c.p("opt/electron/electron"), c.p("opt/electron/electron.x"))
        r = c.launch()
        _check(r.returncode == 2 and "missing artifact: %s/electron/electron" % c.inst in r.stderr and not os.path.exists(c.seen),
               "BROKEN launcher: missing Electron -> exit 2, names the file, starts nothing (rc %d)" % r.returncode)
        r = c.launch("--check")
        _check(r.returncode == 2, "BROKEN launcher --check: missing Electron fails the check")
        os.rename(c.p("opt/electron/electron.x"), c.p("opt/electron/electron"))
        os.remove(c.p("opt/box64/box64"))
        r = c.launch()
        _check(r.returncode == 2 and "missing artifact" in r.stderr, "BROKEN launcher: missing box64 -> exit 2")
        c.exe("opt/box64/box64", FAKE_BOX64)
        # BROKEN: the lobby CSS changed or missing
        c.write("opt/Fightcade/fc2-electron/resources/app/inject/inject.css", "body {}\n")
        r = c.launch()
        _check(r.returncode == 2 and "lobby CSS" in r.stderr, "BROKEN launcher: a changed lobby CSS is refused")
        os.remove(c.p("opt/Fightcade/fc2-electron/resources/app/inject/inject.css"))
        r = c.launch()
        _check(r.returncode == 2 and "missing lobby CSS" in r.stderr, "BROKEN launcher: a missing lobby CSS is refused")
        c.write("opt/Fightcade/fc2-electron/resources/app/inject/inject.css", c.css)
        # BROKEN (#227): the on-screen keyboard script changed or missing
        c.write("opt/Fightcade/fc2-electron/resources/app/inject/inject.js", "/* edited */\n")
        r = c.launch()
        _check(r.returncode == 2 and "keyboard script" in r.stderr, "BROKEN launcher: a changed keyboard script is refused")
        os.remove(c.p("opt/Fightcade/fc2-electron/resources/app/inject/inject.js"))
        r = c.launch()
        _check(r.returncode == 2 and "missing lobby keyboard script" in r.stderr, "BROKEN launcher: a missing keyboard script is refused")
        c.write("opt/Fightcade/fc2-electron/resources/app/inject/inject.js", c.js)
        # BROKEN (#227 review): a stray file in the inject folder (nativefier would not load it today, but nothing checks it)
        c.write("opt/Fightcade/fc2-electron/resources/app/inject/extra.js", "alert(1)\n")
        r = c.launch()
        _check(r.returncode == 2 and "unexpected file in the lobby inject folder" in r.stderr, "BROKEN launcher: a stray file in inject/ is refused")
        os.remove(c.p("opt/Fightcade/fc2-electron/resources/app/inject/extra.js"))
        # BROKEN: the base prefix was built for another Wine
        c.write("data/prefix/.hk-stamp", "wine=old recipe=1\n")
        r = c.launch()
        _check(r.returncode == 2 and "not built for this Wine" in r.stderr, "BROKEN launcher: a stale base prefix is refused")
        c.write("data/prefix/.hk-stamp", c.stamp + "\n")
        # BROKEN: no config at all
        r = c.launch(COIN_DOJO_FIGHTCADE_CONF=c.p("nope.conf"))
        _check(r.returncode == 2 and "missing config" in r.stderr, "BROKEN launcher: no config -> exit 2")
        # BROKEN: no DISPLAY (no Xwayland in this cage)
        r = c.launch(DISPLAY=None)
        _check(r.returncode == 3 and "no DISPLAY" in r.stderr and not os.path.exists(c.seen),
               "BROKEN launcher: no DISPLAY -> exit 3, nothing started")
        # BROKEN: the RAM prefix dir not writable -> clean failure, nothing started, nothing left
        # (#269: root writes into a 0555 dir, so this case only exists for a user; the next one covers exit 4 as root too)
        if AS_ROOT:
            skip("RAM prefix dir not writable: root ignores a 0555 dir (runs as a normal user, e.g. on macOS)")
        else:
            os.chmod(c.shm, 0o555)
            r = c.launch()
            _check(r.returncode == 4 and "is not writable" in r.stderr and not os.path.exists(c.seen)
                   and not os.path.exists(c.shm + "/fcprefix"),
                   "BROKEN launcher: RAM prefix dir not writable -> exit 4, nothing started, no prefix left (rc %d)" % r.returncode)
            os.chmod(c.shm, 0o755)
        c.write_conf(HK_FC_SHM_PREFIX=c.p("fake.log") + "/run/fcprefix")
        open(c.log, "a").close()
        r = c.launch()
        _check(r.returncode == 4, "BROKEN launcher: a RAM prefix dir that cannot be created -> exit 4")
        c.write_conf()
        # a stale read-only RAM prefix from a crash is replaced
        os.makedirs(c.shm + "/fcprefix/dosdevices")
        os.chmod(c.shm + "/fcprefix/dosdevices", 0o555)
        os.chmod(c.shm + "/fcprefix", 0o555)
        r = c.launch()
        _check(r.returncode == 0 and c.seen_map().get("prefix") == "readonly", "launcher: a stale read-only RAM prefix is replaced")
        r = c.launch("--bogus")
        _check(r.returncode == 2, "BROKEN launcher: an unknown option is refused")
        # BROKEN (#228): FBNeo's exe 0644 (as Fightcade 2.1.45 ships it) -> fcade gets ENOEXEC; the launcher refuses
        exe = c.p("opt/Fightcade/emulator/fbneo/fcadefbneo.exe")
        os.chmod(exe, 0o644)
        r = c.launch("--check")
        _check(r.returncode == 2 and "fcadefbneo.exe is not executable" in r.stderr,
               "BROKEN launcher #228: a 0644 fcadefbneo.exe fails --check (rc %d)" % r.returncode)
        os.chmod(exe, 0o755)
        # the binfmt entry gone -> a WARN in the log, the lobby still starts (it is usable without games)
        os.rename(c.p("binfmt/hk-wine"), c.p("binfmt/hk-wine.x"))
        r = c.launch()
        _check(r.returncode == 0 and "WARN: binfmt entry %s is not enabled" % c.p("binfmt/hk-wine") in r.stderr,
               "BROKEN launcher #228: no binfmt entry is warned about, the lobby still runs (rc %d)" % r.returncode)
        os.rename(c.p("binfmt/hk-wine.x"), c.p("binfmt/hk-wine"))
        r = c.launch("--check")
        _check(r.returncode == 0 and "WARN" not in r.stderr, "launcher #228: an enabled entry is not warned about")
        # BROKEN (#215): as root -> exit 6 before the scope, the RAM prefix or Electron; --check (read-only) still runs
        c.as_root()
        if os.path.exists(c.seen):
            os.remove(c.seen)
        open(c.log, "w").close()
        r = c.launch(FAKE_USER_MANAGER="1")
        _check(r.returncode == 6 and "refusing to run as root" in r.stderr and not os.path.exists(c.seen)
               and not os.path.exists(c.shm + "/fcprefix") and "systemd-run" not in c.fake_log()
               and "systemctl --user start" not in c.fake_log(),
               "BROKEN launcher #215: run as root -> exit 6, nothing started (rc %d)" % r.returncode)
        r = c.launch(HK_FC_ALLOW_ROOT="1")
        _check(r.returncode == 6 and not os.path.exists(c.seen), "BROKEN launcher #215: HK_FC_ALLOW_ROOT does not unlock the lobby as root")
        r = c.launch("--in-scope")
        _check(r.returncode == 6 and not os.path.exists(c.seen), "BROKEN launcher #215: --in-scope as root is refused too")
        r = c.launch("--check")
        _check(r.returncode == 0 and "install ok" in r.stderr, "launcher #215: --check only reads and may run as root")
        c.as_user()
    finally:
        c.close()


def test_wrapper():
    c = _cab()
    try:
        r = c.run([c.p("opt/bin/fcade-url"), "fcade://play/x"], HK_FC_GAME_XCURSOR_THEME="blank", XCURSOR_THEME="DMZ-White")
        log = c.fake_log()
        _check(r.returncode == 0 and "box64 ./fcade fcade://play/x" in log and "XCURSOR_THEME=blank" in log,
               "wrapper fcade-url: fcade under box64, the game gets the blank cursor back")
        flog = open(c.data + "/logs/fcade-url.log").read()
        _check("fcade://play" not in flog and "<link>" in flog, "wrapper fcade-url: links in fcade's output are cut from the log")
        # #228: binfmt_misc + binfmt_script exec `/bin/bash <libexec>/wine <exe path> <fcade's args>` (no P flag:
        # fcade's argv[0] is replaced by the exe path). $0 is the symlink's path, so ${0##*/} = wine.
        exe = c.p("opt/Fightcade/emulator/fbneo/fcadefbneo.exe")
        open(c.log, "w").close()
        r = c.run([c.p("opt/bin/wine"), exe, "sfiii3nr1", "fbneo-training-mode/x.lua", "savestates/sfiii3nr1_fbneo.fs"])
        _check(r.returncode == 0 and "box64 %s/wine/bin/wine %s sfiii3nr1 fbneo-training-mode/x.lua savestates/sfiii3nr1_fbneo.fs"
               % (c.inst, exe) in c.fake_log(),
               "wrapper #228: as the binfmt interpreter, the exe and fcade's args go to box64 + wine (rc %d)%s"
               % (r.returncode, "" if r.returncode == 0 else " " + r.stderr[-300:]))
        probe = c.p("opt/bin/probe")
        c.exe("opt/bin/probe", "#!/bin/bash\necho \"base=${0##*/}\"\n")
        os.symlink("probe", c.p("opt/bin/wine-probe"))
        r = subprocess.run([BASH, c.p("opt/bin/wine-probe"), exe], capture_output=True, text=True)
        _check(r.stdout.strip() == "base=wine-probe" and os.path.realpath(c.p("opt/bin/wine-probe")) == os.path.realpath(probe),
               "wrapper #228: bash keeps the symlink's name in $0 (the kernel passes the interpreter path unresolved)")
        r = c.run([c.p("opt/bin/wine"), "winecfg"])
        _check(r.returncode == 0 and "WINEPREFIX=%s/fcprefix winecfg" % c.shm in c.fake_log(),
               "wrapper wine: box64 + wine, the RAM prefix by default")
        os.remove(c.p("opt/box64/box64"))
        r = c.run([c.p("opt/bin/wineserver"), "-k"])
        _check(r.returncode == 2 and "missing artifact" in r.stderr, "BROKEN wrapper: missing box64 -> exit 2")
        r = c.run([c.p("opt/bin/hk-fightcade-wrap")])
        _check(r.returncode == 2, "BROKEN wrapper: an unknown name is refused")
        # BROKEN (#215): as root every name refuses before Box64 runs (`sudo .../wineserver -k`)
        c.exe("opt/box64/box64", FAKE_BOX64)
        c.as_root()
        for name, args in (("wine", ["winecfg"]), ("wineserver", ["-k"]), ("fcade-url", ["fcade://play/x"])):
            open(c.log, "w").close()
            r = c.run([c.p("opt/bin", name)] + args)
            _check(r.returncode == 2 and "refusing to run %s as root" % name in r.stderr and "box64" not in c.fake_log(),
                   "BROKEN wrapper #215: %s as root is refused, Box64 never runs (rc %d)" % (name, r.returncode))
        r = c.run([c.p("opt/bin/wineserver"), "-k"], HK_FC_ALLOW_ROOT="1")
        _check(r.returncode == 0 and "box64 " in c.fake_log(), "wrapper #215: HK_FC_ALLOW_ROOT=1 (container tests only) lets root through")
        c.as_user()
        r = c.run([c.p("opt/bin/wineserver"), "-k"])
        _check(r.returncode == 0, "wrapper #215: the cabinet user still runs wineserver -k")
    finally:
        c.close()


def _tar(path, members, top=""):
    """members {rel: text|None (dir)|('link', target)} -> a tar archive."""
    mode = "w:xz" if path.endswith(".xz") else "w:gz"
    with tarfile.open(path, mode) as t:
        for rel, body in members.items():
            ti = tarfile.TarInfo((top + rel).rstrip("/"))
            if body is None:
                ti.type, ti.mode = tarfile.DIRTYPE, 0o755
                t.addfile(ti)
            elif isinstance(body, tuple):
                ti.type, ti.linkname = tarfile.SYMTYPE, body[1]
                t.addfile(ti)
            else:
                data = body.encode()
                ti.size, ti.mode = len(data), 0o755 if rel.endswith(("fcade", "wine", "wineserver")) else 0o644
                t.addfile(ti, io.BytesIO(data))


def _user_env(d):
    """os.environ for running the role's scripts as the cabinet user: as root (the Forgejo job, #269) a fake `id` in
    d/userbin says uid 1000; as a user, the environment unchanged. Never HK_FC_ALLOW_ROOT."""
    env = {k: v for k, v in os.environ.items() if k != "HK_FC_ALLOW_ROOT"}
    if AS_ROOT:
        os.makedirs(os.path.join(d, "userbin"), exist_ok=True)
        with open(os.path.join(d, "userbin", "id"), "w") as fh:
            fh.write(FAKE_ID_USER)
        os.chmod(os.path.join(d, "userbin", "id"), 0o755)
        env["PATH"] = os.path.join(d, "userbin") + ":" + env.get("PATH", "/usr/bin:/bin")
    return env


def test_unpack():
    d = tempfile.mkdtemp(prefix="t-fcunpack-")
    uenv = _user_env(d)   # #269: as the cabinet user, also when the tests run as root
    try:
        arc = os.path.join(d, "fc.tar.gz")
        _tar(arc, {"Fightcade/": None, "Fightcade/emulator/fbneo/config/": None,
                   "Fightcade/emulator/fbneo/config/fcadefbneo.default.ini": "nVidSelect 0\r\n",
                   "Fightcade/fc2-electron/resources/app/nativefier.json": '{"showMenuBar":true}',
                   "Fightcade/ROMs/FBNeo ROMs": ("link", "../emulator/fbneo/ROMs/")})
        sha = _sha(arc)
        dest = os.path.join(d, "opt", "Fightcade")
        os.makedirs(os.path.dirname(dest))
        args = [UNPACK, "--strip", "1", "--link", "emulator/fbneo/config=%s/cfg" % d,
                "--link", "emulator/fcade.log=%s/fcade.log" % d,
                "--dist", "fc2-electron/resources/app/nativefier.json", arc, sha, dest, "stamp-1"]
        r = subprocess.run([BASH] + args, capture_output=True, text=True, env=uenv)
        _check(r.returncode == 0, "unpack: a good archive unpacks (rc %d)%s" % (r.returncode, "" if r.returncode == 0 else " " + r.stderr[-300:]))
        _check(os.readlink(os.path.join(dest, "emulator/fbneo/config")) == d + "/cfg", "unpack: config folder becomes a link")
        _check(os.path.isfile(os.path.join(dest, "emulator/fbneo/config.dist/fcadefbneo.default.ini")),
               "unpack: the original folder is kept as .dist")
        _check(os.path.islink(os.path.join(dest, "emulator/fcade.log")), "unpack: a missing file can become a link too")
        _check(os.path.isfile(os.path.join(dest, "fc2-electron/resources/app/nativefier.json.dist")), "unpack: --dist copy kept")
        _check(open(os.path.join(dest, ".hk-stamp")).read() == "stamp-1\n", "unpack: stamp written")
        _check(not stat.S_IMODE(os.stat(os.path.join(dest, "emulator")).st_mode) & 0o022, "unpack: no group/other write")
        open(os.path.join(dest, "marker"), "w").close()
        # BROKEN: sha256 mismatch -> exit 3, the installed folder untouched
        r = subprocess.run([BASH, UNPACK, "--strip", "1", arc, "0" * 64, dest, "stamp-2"], capture_output=True, text=True, env=uenv)
        _check(r.returncode == 3 and "sha256 mismatch" in r.stderr and os.path.exists(os.path.join(dest, "marker"))
               and open(os.path.join(dest, ".hk-stamp")).read() == "stamp-1\n",
               "BROKEN unpack: a sha256 mismatch refuses and leaves the install as it was (rc %d)" % r.returncode)
        _check(not [x for x in os.listdir(os.path.dirname(dest)) if ".new." in x], "BROKEN unpack: no half-unpacked folder left")
        # BROKEN: a link that leaves the tree
        r = subprocess.run([BASH, UNPACK, "--link", "../../etc=/x", arc, sha, dest, "s"], capture_output=True, text=True, env=uenv)
        _check(r.returncode == 2 and "inside the tree" in r.stderr, "BROKEN unpack: a link path with .. is refused")
        r = subprocess.run([BASH, UNPACK, arc, sha, "relative/dest", "s"], capture_output=True, text=True, env=uenv)
        _check(r.returncode == 2, "BROKEN unpack: a relative DEST is refused")
        # zip (Electron) unpacks too; a re-unpack replaces the old folder whole
        z = os.path.join(d, "el.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("electron", "#!/bin/sh\n")
            zf.writestr("resources/default_app.asar", "x")
        zdest = os.path.join(d, "opt", "electron")
        r = subprocess.run([BASH, UNPACK, z, _sha(z), zdest, "z1"], capture_output=True, text=True, env=uenv)
        _check(r.returncode == 0 and os.path.isfile(os.path.join(zdest, "electron")), "unpack: a zip unpacks")
        r = subprocess.run([BASH, UNPACK, "--strip", "1", arc, sha, dest, "stamp-3"], capture_output=True, text=True, env=uenv)
        _check(r.returncode == 0 and not os.path.exists(os.path.join(dest, "marker")), "unpack: a re-unpack replaces the folder whole")
        # setuid/setgid bits never survive (Electron's chrome-sandbox ships 4755 in some archives)
        sarc = os.path.join(d, "s.tar.gz")
        with tarfile.open(sarc, "w:gz") as t:
            ti = tarfile.TarInfo("chrome-sandbox")
            ti.size, ti.mode = 1, 0o6755
            t.addfile(ti, io.BytesIO(b"x"))
        sdest = os.path.join(d, "opt", "sb")
        r = subprocess.run([BASH, UNPACK, sarc, _sha(sarc), sdest, "s1"], capture_output=True, text=True, env=uenv)
        _check(r.returncode == 0 and not stat.S_IMODE(os.stat(os.path.join(sdest, "chrome-sandbox")).st_mode) & 0o6000,
               "unpack: setuid/setgid bits are stripped")
        # --patch (#215): the role's popup patch on a pinned file; broken: another upstream file, a no-op, bad specs
        pp = _popup_patch()
        parc = os.path.join(d, "p.tar.gz")
        _tar(parc, {"app/": None, "app/lib/": None, "app/lib/main.js": FAKE_MAIN_JS}, top="")
        pdest = os.path.join(d, "opt", "pt")
        mjs = os.path.join(pdest, "app/lib/main.js")
        spec = "app/lib/main.js=%s:%s" % (hashlib.sha256(FAKE_MAIN_JS.encode()).hexdigest(), pp["sed"] if pp else "")
        r = subprocess.run([BASH, UNPACK, "--patch", spec, parc, _sha(parc), pdest, "p1"], capture_output=True, text=True, env=uenv)
        got = open(mjs).read() if os.path.exists(mjs) else ""
        _check(r.returncode == 0 and POPUP_HARDENED in got and "plugins: true" not in got
               and got == FAKE_MAIN_JS.replace("            plugins: true,\n", POPUP_HARDENED),
               "unpack --patch: the role's sed hardens nativefier's popup options, one line, nothing else (rc %d)%s"
               % (r.returncode, "" if r.returncode == 0 else " " + r.stderr[-300:]))
        _check(not [x for x in os.listdir(os.path.dirname(mjs)) if x.endswith(".hk-patch")], "unpack --patch: no temp file left")
        r = subprocess.run([BASH, UNPACK, "--patch", "app/lib/main.js=%s:%s" % ("e" * 64, pp["sed"] if pp else "s/x/y/"),
                            parc, _sha(parc), pdest, "p2"], capture_output=True, text=True, env=uenv)
        _check(r.returncode == 3 and "re-pin the patch" in r.stderr and open(os.path.join(pdest, ".hk-stamp")).read() == "p1\n"
               and POPUP_HARDENED in open(mjs).read(),
               "BROKEN unpack --patch: another upstream file (sha256) refuses, the install stays (rc %d)" % r.returncode)
        r = subprocess.run([BASH, UNPACK, "--patch", spec.split(":", 1)[0] + ":s/no such text/x/", parc, _sha(parc), pdest, "p3"],
                           capture_output=True, text=True, env=uenv)
        _check(r.returncode == 1 and "changed nothing" in r.stderr and open(os.path.join(pdest, ".hk-stamp")).read() == "p1\n",
               "BROKEN unpack --patch: a patch that changes nothing fails, the install stays (rc %d)" % r.returncode)
        for bad, why in (("app/lib/main.js", "no =SHA256:SED"), ("../x=%s:s/a/b/" % ("e" * 64), "a path with .."),
                         ("app/lib/main.js=abc:s/a/b/", "a short sha256"), ("app/lib/main.js=%s:" % ("e" * 64), "no sed")):
            r = subprocess.run([BASH, UNPACK, "--patch", bad, parc, _sha(parc), pdest, "p4"], capture_output=True, text=True, env=uenv)
            _check(r.returncode == 2, "BROKEN unpack --patch: %s is refused (rc %d)" % (why, r.returncode))
        # BROKEN: run as root -> refused (root never unpacks into the cabinet user's data partition)
        os.makedirs(os.path.join(d, "fakebin"))
        with open(os.path.join(d, "fakebin", "id"), "w") as fh:
            fh.write("#!/bin/sh\necho 0\n")
        os.chmod(os.path.join(d, "fakebin", "id"), 0o755)
        env = {"PATH": os.path.join(d, "fakebin") + ":/usr/bin:/bin"}
        for tool, args in ((UNPACK, [sarc, _sha(sarc), sdest, "s2"]), (PREFIX, [d + "/p/prefix", "x"])):
            r = subprocess.run([BASH, tool] + args, capture_output=True, text=True, env=env)
            _check(r.returncode == 2 and "refusing to run as root" in r.stderr,
                   "BROKEN %s: running as root is refused" % os.path.basename(tool))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_shell_syntax():
    for f in (LAUNCHER, UNPACK, PREFIX, WRAP):
        r = subprocess.run([BASH, "-n", f], capture_output=True, text=True)
        _check(r.returncode == 0, "bash -n %s (bash 3.2 on macOS) %s" % (os.path.basename(f), r.stderr.strip()))
    if shutil.which("shellcheck"):
        r = subprocess.run(["shellcheck", "-x", LAUNCHER, UNPACK, PREFIX, WRAP], capture_output=True, text=True)
        _check(r.returncode == 0, "shellcheck clean %s" % r.stdout[-800:])
    else:
        skip("shellcheck not installed (make lint runs it)")


# ---- 3. the role in the pinned Ansible container -----------------------------------------------------------------
# pgrep stand-in (the python:slim image has no procps): `pgrep -x [-u UID] PATTERN` over /proc/*/comm, rc 0 = match.
FAKE_PGREP = r'''#!/usr/bin/env python3
import os, re, sys
pat = re.compile("^(?:%s)$" % sys.argv[-1]); hit = 0
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        comm = open("/proc/%s/comm" % pid).read().strip()
    except OSError:
        continue
    if pat.match(comm):
        hit = 1; print(pid, comm)
sys.exit(0 if hit else 1)
'''
# The image has no xz and no unzip (the cabinet gets both from apt, fightcade_packages): stand-ins.
FAKE_XZ = r'''#!/usr/bin/env python3
import lzma, sys
sys.stdout.buffer.write(lzma.decompress(sys.stdin.buffer.read()))
'''
FAKE_UNZIP = r'''#!/usr/bin/env python3
import os, sys, zipfile
args = [a for a in sys.argv[1:] if a not in ("-q", "-o")]
arc, dest = args[0], args[args.index("-d") + 1]
with zipfile.ZipFile(arc) as z:
    for i in z.infolist():
        p = z.extract(i, dest)
        mode = (i.external_attr >> 16) & 0o777
        if mode:
            os.chmod(p, mode)
'''
# systemd-binfmt stand-in over a fake binfmt_misc dir (/w/cab/binfmt; the host's is never touched): for each
# :name:M:offset:magic:mask:interp:flags line, delete the entry of that name, then write what the kernel shows.
# FAKE_BINFMT_NOOP=1 = binfmt_misc not mounted (nothing registered).
FAKE_SYSTEMD_BINFMT = r'''#!/usr/bin/env python3
import os, sys
if os.environ.get("FAKE_BINFMT_NOOP"):
    sys.exit(0)
d = "/w/cab/binfmt"
os.makedirs(d, exist_ok=True)
with open("/w/fake-binfmt.log", "a") as log:
    log.write("systemd-binfmt %s\n" % " ".join(sys.argv[1:]))
for path in sys.argv[1:]:
    for line in open(path):
        line = line.strip()
        if not line or line[0] in "#;":
            continue
        sep = line[0]
        name, typ, off, magic, mask, interp, flags = line[1:].split(sep)[:7]
        with open(os.path.join(d, name), "w") as fh:
            fh.write("enabled\ninterpreter %s\nflags: %s\noffset %s\nmagic %s\n"
                     % (interp, flags, off or "0", magic.encode().hex()))
'''
PLAYBOOK = """- hosts: localhost
  connection: local
  gather_facts: false
  roles: [fightcade]
"""
DEFAULT_INI = ("// FB Neo v1.0.0.03 (Fightcade) config file\r\nnVidHorWidth 1280\r\nnVidHorHeight 720\r\nnVidScrnAspectX 16\r\n"
               "nVidScrnAspectY 9\r\nbVidUseHardwareGamma 1\r\nbVidAutoSwitchFull 0\r\nbVidFullStretch 0\r\n"
               "bVidCorrectAspect 1\r\nbVidVSync 0\r\nnVidSelect 4\r\nbModelessMenu 1\r\n"
               + "".join("szAppRomPaths[%d] \r\n" % i for i in range(6)) + "szAppRomPaths[6] roms/nes/\r\n")


def _image():
    try:
        out = subprocess.run([os.path.join(ANSIBLE, "bin", "ans"), "--image"], capture_output=True, text=True)
        img = out.stdout.strip()
        if img and subprocess.run(["docker", "image", "inspect", img], capture_output=True).returncode == 0:
            return img
    except OSError:
        pass
    return None


class Box:
    """A throwaway cabinet in a work dir mounted at /w (colima mounts $HOME); the role is mounted read-only."""

    def __init__(self, image):
        self.image = image
        base = os.path.join(os.path.expanduser("~"), ".cache", "coin-dojo", "ansible")
        os.makedirs(base, exist_ok=True)
        self.dir = tempfile.mkdtemp(prefix="t-fightcade-", dir=base)
        for sub in ("build/fightcade/out", "build/box64/out/x64lib", "cab", "run", "bin"):
            os.makedirs(self.path(sub), exist_ok=True)
        os.makedirs(self.path("brand"), exist_ok=True)
        shutil.copy(os.path.join(REPO, "brand", "tokens.json"), self.path("brand", "tokens.json"))   # #227: the keyboard CSS colours
        self.write("site.yml", PLAYBOOK)
        self.write("bin/pgrep", FAKE_PGREP, 0o755)
        self.write("bin/xz", FAKE_XZ, 0o755)
        self.write("bin/unzip", FAKE_UNZIP, 0o755)
        self.write("bin/systemd-binfmt", FAKE_SYSTEMD_BINFMT, 0o755)
        self.write_archives()
        self.write_box64()

    def path(self, *rel):
        return os.path.join(self.dir, *rel)

    def write(self, rel, text, mode=None):
        p = self.path(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
        if mode:
            os.chmod(p, mode)

    def write_archives(self, fc_extra=""):
        out = self.path("build/fightcade/out")
        _tar(os.path.join(out, "wine-10.0-amd64-wow64.tar.xz"),
             {"bin/": None, "bin/wine": FAKE_WINE, "bin/wineserver": FAKE_WINESERVER}, top="wine-10.0-amd64-wow64/")
        with zipfile.ZipFile(os.path.join(out, "electron-v9.1.0-linux-arm64.zip"), "w") as zf:
            info = zipfile.ZipInfo("electron")
            info.external_attr = 0o755 << 16
            zf.writestr(info, FAKE_ELECTRON)
        _tar(os.path.join(out, "Fightcade-linux-latest.tar.gz"), {
            "": None, "fc2-electron/resources/app/": None, "fc2-electron/resources/app/inject/": None,
            "fc2-electron/resources/app/inject/_placeholder": "",
            "fc2-electron/resources/app/package.json": "{}\n",
            "fc2-electron/resources/app/nativefier.json": '{"name":"Fightcade","showMenuBar":true,"maximize":false}',
            "fc2-electron/resources/app/lib/": None, "fc2-electron/resources/app/lib/main.js": FAKE_MAIN_JS,
            "emulator/": None, "emulator/fcade": "#!/bin/sh\necho fcade\n" + fc_extra,
            "emulator/fbneo/": None, "emulator/fbneo/fcadefbneo.exe": "MZ", "emulator/ggpofba-ng.exe": "MZ", "emulator/fbneo/ROMs/": None,
            "emulator/fbneo/config/": None, "emulator/fbneo/config/fcadefbneo.default.ini": DEFAULT_INI}, top="Fightcade/")
        self.archives = [
            {"name": "wine", "src": "/w/build/fightcade/out/wine-10.0-amd64-wow64.tar.xz", "dest": "wine", "strip": 1,
             "links": [], "dist": []},
            {"name": "electron", "src": "/w/build/fightcade/out/electron-v9.1.0-linux-arm64.zip", "dest": "electron",
             "strip": 0, "links": [], "dist": []},
            {"name": "fightcade", "src": "/w/build/fightcade/out/Fightcade-linux-latest.tar.gz", "dest": "Fightcade",
             "strip": 1, "links": [{"path": "emulator/fbneo/config", "to": "/w/cab/data/fbneo/config"},
                                   {"path": "emulator/fbneo/ROMs", "to": "/w/cab/data/roms-extra"},
                                   {"path": "emulator/fcade.log", "to": "/w/cab/data/logs/fcade.log"}],
             "dist": ["fc2-electron/resources/app/nativefier.json"],
             # the role's own popup patch (#215), pinned to the fake main.js
             "patches": [dict(_popup_patch(), sha256=hashlib.sha256(FAKE_MAIN_JS.encode()).hexdigest())]},
        ]
        for a in self.archives:
            a["sha256"] = _sha(self.path(a["src"][3:]))

    def write_box64(self, tamper=False):
        out = self.path("build/box64/out")
        self.write("build/box64/out/box64", FAKE_BOX64, 0o755)
        self.write("build/box64/out/box64.box64rc", "[fcadefbneo.exe]\n")
        self.write("build/box64/out/BUILDINFO", "tag: v0.4.4\n")
        self.write("build/box64/out/x64lib/libgcc_s.so.1", "x86 lib\n")
        for name in ("box64", "box64.box64rc"):
            self.write("build/box64/out/%s.sha256" % name, "%s  %s\n" % (_sha(os.path.join(out, name)), name))
        self.write("build/box64/out/x64lib.sha256", "%s  x64lib/libgcc_s.so.1\n" % _sha(os.path.join(out, "x64lib/libgcc_s.so.1")))
        if tamper:
            self.write("build/box64/out/box64", FAKE_BOX64 + "# tampered\n", 0o755)

    def entry(self):
        p = self.path("cab/binfmt/hk-wine")
        return open(p).read() if os.path.exists(p) else None

    def run(self, check=False, pre="", **extra):
        evars = {
            "hk_cabinet_user": "root", "hk_cabinet_group": "root", "hk_cabinet_home": "/w/cab/home", "hk_cabinet_uid": 0,
            "hk_fightcade_install_dir": "/w/cab/data/opt", "hk_fightcade_data_dir": "/w/cab/data", "hk_data_root": "/w/cab/data",
            "fightcade_libexec": "/w/cab/usr/lib/coin-dojo-fightcade",
            "hk_rom_root": "/data/roms", "hk_fightcade_box64_fast": False, "hk_fightcade_cursor_theme": "DMZ-White",
            "hk_fightcade_audio_sink": "auto", "hk_fightcade_install": "deploy", "hk_tools_dir": "/w/cab/tools",
            "hk_fightcade_chat_macros": [{"group": "STATUS", "text": "brb"}, {"group": "AFTER", "text": "gg"}],
            "hk_repo_root": "/w", "ansible_become": False, "ansible_python_interpreter": "/usr/local/bin/python3",
            "fightcade_packages_manage": False, "fightcade_archives": self.archives,
            "fightcade_bin": "/w/cab/usr/bin/coin-dojo-fightcade", "fightcade_conf": "/w/cab/etc/coin-dojo-fightcade.conf",
            "coin_dojo_run_dir": "/w/run", "fightcade_space_mib": 1, "hk_fightcade_binfmt": True,
            "fightcade_binfmt_conf": "/w/cab/etc/binfmt.d/coin-dojo-wine.conf", "fightcade_binfmt_proc": "/w/cab/binfmt",
            "fightcade_binfmt_cmd": "/w/bin/systemd-binfmt",
        }
        evars.update(extra)
        with open(self.path("vars.json"), "w") as fh:
            json.dump(evars, fh)
        os.makedirs(self.path("cab/usr/bin"), exist_ok=True)
        os.makedirs(self.path("cab/etc/binfmt.d"), exist_ok=True)
        os.makedirs(self.path("cab/binfmt"), exist_ok=True)
        cmd = "%sansible-playbook -i localhost, -e @/w/vars.json %s /w/site.yml" % (pre, "--check --diff" if check else "")
        os.makedirs(self.path("roms"), exist_ok=True)   # #298: hk_rom_root (/data/roms) persists between runs like the cabinet's
        r = subprocess.run(["docker", "run", "--rm", "-v", "%s:/w" % self.dir, "-v", "%s:/data/roms" % self.path("roms"),
                            "-v", "%s:/roles/fightcade:ro" % ROLE,
                            "-v", "%s:/filter_plugins:ro" % os.path.join(ANSIBLE, "filter_plugins"),
                            "-e", "ANSIBLE_ROLES_PATH=/roles", "-e", "ANSIBLE_FILTER_PLUGINS=/filter_plugins",
                            "-e", "PATH=/w/bin:/usr/local/bin:/usr/bin:/bin", "-e", "ANSIBLE_LOCAL_TEMP=/tmp/.al",
                            "-e", "ANSIBLE_NOCOLOR=1", "-e", "FAKE_LOG=/w/fake.log", "-e", "HK_FC_ALLOW_ROOT=1", self.image, "sh", "-c", cmd],
                           capture_output=True, text=True, timeout=600)
        return r.returncode, r.stdout + r.stderr

    def shell(self, script):
        """Run a shell snippet in the same image, as the cabinet would (bash 5)."""
        r = subprocess.run(["docker", "run", "--rm", "-v", "%s:/w" % self.dir, "-e", "FAKE_LOG=/w/fake.log",
                            self.image, "bash", "-c", script], capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout + r.stderr

    def close(self):
        subprocess.run(["docker", "run", "--rm", "-v", "%s:/w" % self.dir, self.image, "sh", "-c",
                        "chmod -R u+w /w/cab 2>/dev/null; rm -rf /w/cab"], capture_output=True)
        shutil.rmtree(self.dir, ignore_errors=True)


def _changed(out):
    m = re.search(r"localhost\s*:\s*ok=\d+\s+changed=(\d+)", out)
    return int(m.group(1)) if m else -1


def test_play_in_container():
    image = _image() if shutil.which("docker") else None
    if not image or subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        skip("play tests: docker not running or ansible image not built; make -C ansible ansible-image")
        return
    box = Box(image)
    opt = "cab/data/opt"
    try:
        rc, out = box.run(check=True)
        _check(rc == 0, "play: check mode on a fresh box passes (rc=%d)%s" % (rc, "" if rc == 0 else "\n" + out[-3000:]))
        _check(not os.path.exists(box.path(opt)) and not os.path.exists(box.path("cab/data")), "play: check mode wrote nothing")
        # BROKEN: a root-run path under the coindojo-owned data partition -> refused before any write
        rc, out = box.run(fightcade_libexec="/w/cab/data/lib")
        _check(rc != 0 and "Root-run files belong on the root fs" in out and not os.path.exists(box.path("cab/data/lib")),
               "BROKEN play: a root-run helper dir under the data partition is refused")
        rc, out = box.run(fightcade_bin="/w/cab/data/opt/coin-dojo-fightcade")
        _check(rc != 0 and "Root-run files belong on the root fs" in out, "BROKEN play: a launcher inside the install dir is refused")
        # BROKEN: a first install that does not fit on the install dir's filesystem -> refused before any write
        rc, out = box.run(fightcade_space_mib=10 ** 9)
        _check(rc != 0 and "MiB free" in out and not os.path.exists(box.path(opt)),
               "BROKEN play: too little free space on the install filesystem refuses the first install")
        rc, out = box.run()
        _check(rc == 0, "play: apply on a fresh box passes (rc=%d)%s" % (rc, "" if rc == 0 else "\n" + out[-3000:]))
        ini = open(box.path(opt, "fcadefbneo.ini"), newline="").read()
        lines = ini.split("\r\n")
        for k, v in OPTION_B.items():
            _check("%s %s" % (k, v) in lines, "play: base ini has option B %s %s" % (k, v))
        for i, d in enumerate(ROM_DIRS):
            _check("szAppRomPaths[%d] Z:\\data\\roms\\%s\\" % (i, d) in lines, "play: szAppRomPaths[%d] = Z:\\data\\roms\\%s\\" % (i, d))
        _check("szAppRomPaths[0] Z:\\data\\roms\\fightcade\\" in lines, "play #298: szAppRomPaths[0] = roms/fightcade (searched first)")
        _check(os.path.isdir(box.path("roms", "fightcade")), "play #298: the role makes /data/roms/fightcade")
        _check("szAppRomPaths[7] Z:\\w\\cab\\data\\roms-extra\\" in lines, "play: szAppRomPaths[7] = roms-extra in Windows form")
        css = box.path(opt, "Fightcade/fc2-electron/resources/app/inject/inject.css")
        css_text = open(css).read() if os.path.exists(css) else ""
        _check("left: 240px" in css_text and "width: 1440px" in css_text and "#000" in css_text,
               "play: inject CSS = a 1440 px column at x=240 on black")
        conf = open(box.path("cab/etc/coin-dojo-fightcade.conf")).read()
        _check("HK_FC_CSS_SHA256=%s" % hashlib.sha256(css_text.encode()).hexdigest() in conf,
               "play: the launcher config carries the CSS sha256 it checks at launch")
        _check("HK_FC_BOX64_FAST=0" in conf, "play: Box64 fast flags off by default")
        _check("HK_FC_AUDIO_SINK=auto" in conf and "HK_FC_PACTL=pactl" in conf and "HK_FC_DRM_DIR=/sys/class/drm" in conf,
               "play #233: the launcher config carries the audio sink lever (auto) and its probes")
        nat = json.load(open(box.path(opt, "Fightcade/fc2-electron/resources/app/nativefier.json")))
        _check(nat.get("showMenuBar") is False and nat.get("name") == "Fightcade", "play: nativefier.json: no menu bar, rest kept")
        wp = nat.get("browserwindowOptions", {}).get("webPreferences", {})
        _check(wp.get("contextIsolation") is True and wp.get("nodeIntegration") is False and wp.get("enableRemoteModule") is False
               and wp.get("preload", "").endswith("/resources/app/lib/preload.js"), "play: nativefier.json: hardened webPreferences, preload kept")
        _check(nat.get("internalUrls") == "^https://(web|replay)\\.fightcade\\.com/", "play: nativefier.json: internal links anchored")
        _check("Exec=/w/cab/usr/lib/coin-dojo-fightcade/fcade-url %u" in open(box.path("cab/home/.local/share/applications/coin-dojo-fcade.desktop")).read(),
               "play: the handler runs the root-owned wrapper on the root fs")
        _check(stat.S_IMODE(os.stat(box.path("cab/data/logs")).st_mode) == 0o700, "play: logs dir is 0700")
        _check(os.readlink(box.path(opt, "Fightcade/emulator/fbneo/ROMs")) == "/w/cab/data/roms-extra",
               "play: the lobby's ROM downloads land in roms-extra")
        _check(os.readlink(box.path(opt, "Fightcade/emulator/fbneo/config")) == "/w/cab/data/fbneo/config",
               "play: FBNeo's config folder is in the data dir")
        _check(stat.S_IMODE(os.stat(box.path("cab/data/profile")).st_mode) == 0o700, "play: lobby profile is 0700")
        # (owners are not visible through the colima mount; the VM run checked root:root)
        _check(stat.S_IMODE(os.stat(box.path(opt)).st_mode) == 0o755, "play: the install dir under the data dir is 0755")
        _check(open(box.path("cab/data/prefix/.hk-stamp")).read().startswith("wine=%s recipe=" % box.archives[0]["sha256"]),
               "play: base prefix built and stamped for this Wine")
        _check('"csmt"=dword:00000000' in open(box.path("cab/data/prefix/user.reg")).read(), "play: base prefix has csmt=0")
        _check('"Map Controllers"=dword:00000000' in open(box.path("cab/data/prefix/system.reg")).read(),
               "play #230: base prefix has winebus Map Controllers=0")
        for n in (1, 2):
            _check("nPlayerDefaultControls[%d] 15" % (n - 1) in lines and
                   "szPlayerDefaultIni[%d] Z:\\w\\cab\\data\\fbneo\\config\\presets\\coin-dojo-p%d.ini" % (n - 1, n) in lines,
                   "play #230: base ini sends P%d to its preset (15 + Z: path)" % n)
            pt = open(box.path("cab/data/fbneo/config/presets/coin-dojo-p%d.ini" % n)).read()
            want = preset_lines(n, _stick_buttons(_defaults()))
            _check(all(w in pt.splitlines() for w in want) and "version 0x029744" in pt,
                   "play #230: preset P%d renders the panel map (%d input lines)" % (n, len(want)))
            _check(len(fbneo_accepts(pt, n - 1)) == 12, "play #230: FBNeo would apply all 12 lines of preset P%d" % n)
        _check(os.path.exists(box.path("cab/home/.local/share/applications/coin-dojo-fcade.desktop")) and
               "x-scheme-handler/fcade=coin-dojo-fcade.desktop" in open(box.path("cab/home/.config/mimeapps.list")).read(),
               "play: fcade:// links open the handler")
        _check(not os.listdir(box.path("cab/data/.incoming")), "play: uploads deleted after unpacking")
        mjs = box.path(opt, "Fightcade/fc2-electron/resources/app/lib/main.js")
        _check(os.path.exists(mjs) and POPUP_HARDENED in open(mjs).read() and "plugins: true" not in open(mjs).read(),
               "play #215: the lobby's popups get hardened webPreferences (main.js patched at unpack)")
        for rel in ("Fightcade/emulator/fbneo/fcadefbneo.exe", "Fightcade/emulator/ggpofba-ng.exe"):
            _check(stat.S_IMODE(os.stat(box.path(opt, rel)).st_mode) == 0o755, "play #228: %s is 0755 (0644 in the archive)" % rel)
        rule = open(box.path("cab/etc/binfmt.d/coin-dojo-wine.conf")).read().splitlines()
        _check(rule[-1] == ":hk-wine:M::MZ::/w/cab/usr/lib/coin-dojo-fightcade/wine:",
               "play #228: the binfmt.d rule sends MZ files to the wine wrapper, no flags (%s)" % rule[-1])
        _check(box.entry() == BINFMT_ENTRY % "/w/cab/usr/lib/coin-dojo-fightcade",
               "play #228: the rule is registered now (systemd-binfmt on the one file)")
        _check("HK_FC_BINFMT=/w/cab/binfmt/hk-wine" in open(box.path("cab/etc/coin-dojo-fightcade.conf")).read(),
               "play #228: the launcher config names the entry it warns about")
        rc, sh = box.shell("COIN_DOJO_FIGHTCADE_CONF=/w/cab/etc/coin-dojo-fightcade.conf bash /w/cab/usr/bin/coin-dojo-fightcade --check")
        _check(rc == 0 and "install ok" in sh, "play: the deployed launcher's --check passes (rc %d)%s" % (rc, "" if rc == 0 else " " + sh[-400:]))

        rc, out = box.run()
        _check(rc == 0 and _changed(out) == 0, "play: second apply is idempotent (changed=%d)%s" % (_changed(out), "" if _changed(out) == 0 else out[-2500:]))
        rc, out = box.run(check=True)
        _check(rc == 0 and _changed(out) == 0, "play: dry run on a deployed box is ZERO (changed=%d)" % _changed(out))
        # #228 the maintainer's hand-registered hk-wine (same rule): left alone, nothing to change
        flog = box.path("fake-binfmt.log")
        os.remove(flog)
        rc, out = box.run()
        _check(rc == 0 and _changed(out) == 0 and not os.path.exists(flog),
               "play #228: an identical live entry (registered by hand as hk-wine) is not registered again")
        # BROKEN: a hand-registered hk-wine with another interpreter -> dry run shows it, apply replaces it
        box.write("cab/binfmt/hk-wine", BINFMT_ENTRY % "/usr/bin")
        rc, out = box.run(check=True)
        _check(rc == 0 and _changed(out) == 1 and "would register" in out,
               "BROKEN play #228: a live entry with the wrong interpreter shows in the dry run (changed=%d)" % _changed(out))
        rc, out = box.run()
        _check(rc == 0 and box.entry() == BINFMT_ENTRY % "/w/cab/usr/lib/coin-dojo-fightcade",
               "BROKEN play #228: a wrong interpreter is replaced by the role's rule")
        # BROKEN: a hand-registered hk-wine with the F flag -> replaced (flags must be empty)
        box.write("cab/binfmt/hk-wine", (BINFMT_ENTRY % "/w/cab/usr/lib/coin-dojo-fightcade").replace("flags: ", "flags: F"))
        rc, out = box.run()
        _check(rc == 0 and _changed(out) == 1 and box.entry() == BINFMT_ENTRY % "/w/cab/usr/lib/coin-dojo-fightcade",
               "BROKEN play #228: a live entry with the F flag is registered again without it")
        # BROKEN: the conf deleted -> dry run shows it, apply writes it back and registers again
        os.remove(box.path("cab/etc/binfmt.d/coin-dojo-wine.conf"))
        rc, out = box.run(check=True)
        _check(rc == 0 and _changed(out) >= 1, "BROKEN play #228: a missing binfmt.d conf drifts the dry run (changed=%d)" % _changed(out))
        rc, out = box.run()
        _check(rc == 0 and os.path.exists(box.path("cab/etc/binfmt.d/coin-dojo-wine.conf")), "play #228: the conf is written back")
        # BROKEN: binfmt_misc not mounted (systemd-binfmt registers nothing) -> the deploy fails with a clear message
        os.remove(box.path("cab/binfmt/hk-wine"))
        rc, out = box.run(pre="FAKE_BINFMT_NOOP=1 ")
        _check(rc != 0 and "Is binfmt_misc mounted" in out, "BROKEN play #228: an entry the kernel did not take fails the deploy")
        rc, out = box.run()
        _check(rc == 0 and box.entry() is not None, "play #228: registered again once binfmt_misc works")
        # another MZ handler under another name -> WARN, ours is kept
        box.write("cab/binfmt/wine", BINFMT_ENTRY % "/usr/bin")
        rc, out = box.run()
        _check(rc == 0 and "WARN other binfmt handlers for MZ files: /w/cab/binfmt/wine" in out,
               "play #228: another MZ handler is warned about")
        os.remove(box.path("cab/binfmt/wine"))
        # BROKEN: an exe back at 0644 (a re-unpack, a hand copy) -> dry run shows it, apply fixes it
        os.chmod(box.path(opt, "Fightcade/emulator/fbneo/fcadefbneo.exe"), 0o644)
        rc, out = box.run(check=True)
        _check(rc == 0 and _changed(out) == 1, "BROKEN play #228: a 0644 exe drifts the dry run (changed=%d)" % _changed(out))
        rc, out = box.run()
        _check(rc == 0 and stat.S_IMODE(os.stat(box.path(opt, "Fightcade/emulator/fbneo/fcadefbneo.exe")).st_mode) == 0o755,
               "play #228: the apply makes it 0755 again")
        # the removal path: hk_fightcade_binfmt false -> entry unregistered (-1 written), conf deleted; then changed=0
        rc, out = box.run(check=True, hk_fightcade_binfmt=False)
        _check(rc == 0 and "would unregister" in out and box.entry() == BINFMT_ENTRY % "/w/cab/usr/lib/coin-dojo-fightcade",
               "play #228 removal: the dry run shows the unregister and changes nothing")
        rc, out = box.run(hk_fightcade_binfmt=False)
        _check(rc == 0 and (box.entry() or "").strip() == "-1" and not os.path.exists(box.path("cab/etc/binfmt.d/coin-dojo-wine.conf"))
               and "HK_FC_BINFMT=''" in open(box.path("cab/etc/coin-dojo-fightcade.conf")).read(),
               "play #228 removal: -1 written to the entry, the conf deleted, the launcher no longer checks it")
        rc, out = box.run(hk_fightcade_binfmt=False)
        _check(rc == 0 and _changed(out) == 0, "play #228 removal: a second run is changed=0 (changed=%d)" % _changed(out))
        os.remove(box.path("cab/binfmt/hk-wine"))
        rc, out = box.run()
        _check(rc == 0 and box.entry() == BINFMT_ENTRY % "/w/cab/usr/lib/coin-dojo-fightcade", "play #228: re-enabled by the next deploy")
        # #230: the maintainer's edit of a preset stays (seeded only when absent); a deleted preset comes back
        mine = "version 0x029744\ninput  \"p1 fire 1\"  switch 0x4080\n"
        box.write("cab/data/fbneo/config/presets/coin-dojo-p1.ini", mine)
        rc, out = box.run()
        _check(rc == 0 and _changed(out) == 0 and open(box.path("cab/data/fbneo/config/presets/coin-dojo-p1.ini")).read() == mine,
               "play #230: a hand-edited preset is kept by the next deploy (changed=%d)" % _changed(out))
        os.remove(box.path("cab/data/fbneo/config/presets/coin-dojo-p2.ini"))
        rc, out = box.run(check=True)
        _check(rc == 0 and _changed(out) == 1, "BROKEN play #230: a missing preset drifts the dry run (changed=%d)" % _changed(out))
        rc, out = box.run()
        _check(rc == 0 and 'input  "p2 fire 1"  switch 0x4184' in open(box.path("cab/data/fbneo/config/presets/coin-dojo-p2.ini")).read(),
               "play #230: a deleted preset is written back by the next deploy")
        # FBNeo rewrites its ini on exit: no drift, the role never manages the live copy
        box.write("cab/data/fbneo/config/fcadefbneo.ini", "nVidSelect 0 (FBNeo wrote this)\n")
        rc, out = box.run(check=True)
        _check(rc == 0 and _changed(out) == 0, "play: FBNeo's rewritten live ini does not drift the dry run")
        rc, out = box.run(check=True, hk_fightcade_box64_fast=True)
        _check(rc == 0 and _changed(out) == 1, "play: hk_fightcade_box64_fast true changes only the config (changed=%d)" % _changed(out))

        # BROKEN: a local download that does not match its pin -> fail, nothing unpacked
        stamp_before = open(box.path(opt, "Fightcade/.hk-stamp")).read()
        bad = json.loads(json.dumps(box.archives))
        bad[2]["sha256"] = "0" * 64
        rc, out = box.run(fightcade_archives=bad)
        _check(rc != 0 and "refusing it" in out and open(box.path(opt, "Fightcade/.hk-stamp")).read() == stamp_before,
               "BROKEN play: a download that does not match its pin is refused, nothing unpacked")
        # BROKEN: a new pin with no local download -> clear error before any change
        moved = box.path("build/fightcade/out/Fightcade-linux-latest.tar.gz")
        os.rename(moved, moved + ".x")
        bad[2]["sha256"] = "1" * 64
        rc, out = box.run(fightcade_archives=bad)
        _check(rc != 0 and "Fetch it first: make -C build fightcade" in out, "BROKEN play: a missing download is a clear error")
        os.rename(moved + ".x", moved)
        # BROKEN (#215): the popup patch pinned to another main.js -> the unpack refuses, the installed tree stays
        badp = json.loads(json.dumps(box.archives))
        badp[2]["patches"][0]["sha256"] = "e" * 64
        rc, out = box.run(fightcade_archives=badp)
        _check(rc != 0 and "re-pin the patch" in out and open(box.path(opt, "Fightcade/.hk-stamp")).read() == stamp_before
               and POPUP_HARDENED in open(mjs).read(),
               "BROKEN play #215: a main.js that is not the pinned one refuses the unpack, the install stays")
        # BROKEN: a Box64 build that does not match its own .sha256 files
        box.write_box64(tamper=True)
        rc, out = box.run()
        _check(rc != 0 and "does not match the .sha256 files its build wrote" in out, "BROKEN play: a tampered Box64 build is refused")
        box.write_box64()
        # a stale current-mode (no live session in session.pid) is no session: the deploy goes on
        box.write("run/current-mode", "fightcade\n")
        box.write("run/session.pid", "999999\n")
        rc, out = box.run(check=True)
        _check(rc == 0, "play: a stale current-mode with a dead session.pid does not block the deploy")
        # BROKEN: Fightcade mode is running (a live coin-dojo-session in session.pid) -> no write at all
        os.remove(box.path(opt, "fcadefbneo.ini"))
        rc, out = box.run(pre="cp /bin/sleep /tmp/coin-dojo-session && (/tmp/coin-dojo-session 60 &) && sleep 0.5 && "
                              "pgrep -x coin-dojo-sessi | head -1 | cut -d' ' -f1 > /w/run/session.pid && ")
        _check(rc != 0 and "Fightcade mode is running" in out and not os.path.exists(box.path(opt, "fcadefbneo.ini")),
               "BROKEN play: Fightcade mode in a live session blocks every write")
        os.remove(box.path("run/current-mode"))
        os.remove(box.path("run/session.pid"))
        rc, out = box.run()
        _check(rc == 0 and os.path.exists(box.path(opt, "fcadefbneo.ini")), "play: after Fightcade mode ends the deploy runs again")
        # a new Fightcade build (new pin) re-unpacks only that tree
        box.write_archives(fc_extra="# 2.1.46\n")
        rc, out = box.run()
        _check(rc == 0 and _changed(out) >= 3 and "box64" not in re.findall(r"changed: \[localhost\] => \(item=(\S+)\)", out),
               "play: a re-pinned Fightcade re-unpacks its tree (changed=%d)" % _changed(out))
    finally:
        box.close()


def _fl_archives(box):
    """The Box's archives as the first-launch manifest needs them (#267): + title, url, size, install_mib."""
    out = json.loads(json.dumps(box.archives))
    for a in out:
        src = box.path(a["src"][3:])
        a.update(title=a["name"].title(), url="https://example.invalid/dl/" + os.path.basename(a["src"]),
                 size=os.path.getsize(src), install_mib=1)
    return out


def test_play_first_launch():
    """#267: hk_fightcade_install first-launch installs coin-dojo's pieces and NONE of Wine/Electron/Fightcade: no
    local downloads needed, no trees, no prefix; the engine, its screen, the ini filter and a valid manifest (the
    role's own pins, recipe, stamps, lobby files) are in place; idempotent; back to deploy removes them."""
    image = _image() if shutil.which("docker") else None
    if not image or subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        skip("play first-launch: docker not running or ansible image not built; make -C ansible ansible-image")
        return
    box = Box(image)
    try:
        arch = _fl_archives(box)
        for f in os.listdir(box.path("build/fightcade/out")):
            os.remove(box.path("build/fightcade/out", f))       # first-launch needs no local downloads
        fl = dict(hk_fightcade_install="first-launch", fightcade_archives=arch)
        rc, out = box.run(check=True, **fl)
        _check(rc == 0, "play first-launch: check mode passes without build/fightcade/out (rc %d)%s" % (rc, "" if rc == 0 else out[-2500:]))
        rc, out = box.run(**fl)
        _check(rc == 0, "play first-launch: apply passes without build/fightcade/out (rc %d)%s" % (rc, "" if rc == 0 else out[-2500:]))
        opt = box.path("cab/data/opt")
        _check(sorted(os.listdir(opt)) == ["box64"], "play first-launch: only Box64 in the install dir, no Wine/Electron/Fightcade: %s" % os.listdir(opt))
        _check(not os.path.exists(box.path("cab/data/prefix")), "play first-launch: no Wine prefix built")
        lib = box.path("cab/usr/lib/coin-dojo-fightcade")
        for f in ("hk-fightcade-fetch", "hk_fc_firstrun.py", "hk_fbneo_ini.py"):
            _check(os.path.isfile(os.path.join(lib, f)), "play first-launch: %s installed in the helper dir" % f)
        _check(os.access(os.path.join(lib, "hk-fightcade-fetch"), os.X_OK), "play first-launch: the engine is executable")
        conf = open(box.path("cab/etc/coin-dojo-fightcade.conf")).read()
        _check("HK_FC_INSTALL=first-launch" in conf and "HK_FC_FETCH_MANIFEST=/usr/local/etc/coin-dojo-fightcade-fetch.json" in conf
               and "HK_FC_UI_LIB=/w/cab/tools/settings" in conf, "play first-launch: the launcher config names the mode, engine, manifest, UI kit")
        mpath = "/w/cab/etc/fetch.json"
        rc, out = box.run(**dict(fl, fightcade_fetch_manifest=mpath))
        m = json.load(open(box.path("cab/etc/fetch.json")))
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import test_fetch   # noqa: E402  (the engine's manifest check)
        _check(test_fetch.ENGINE.manifest_problems(m) == [], "play first-launch: the rendered manifest is valid %s" % test_fetch.ENGINE.manifest_problems(m))
        _check([(a["name"], a["sha256"], a["url"], a["size"]) for a in m["archives"]] ==
               [(a["name"], a["sha256"], a["url"], a["size"]) for a in arch], "play first-launch: the manifest carries the role's pins")
        fcm = [a for a in m["archives"] if a["name"] == "fightcade"][0]
        _check(fcm["patches"] and fcm["links"] and fcm["dist"] == ["fc2-electron/resources/app/nativefier.json"],
               "play first-launch: the Fightcade recipe (patch, links, dist) is the deploy's")
        css = [f for f in m["files"] if f["path"].endswith("/inject/inject.css")]
        _check(css and hashlib.sha256(css[0]["text"].encode()).hexdigest() in conf,
               "play first-launch: the manifest's lobby CSS is the one the launcher checks (sha256 in the config)")
        _check(m["prefix"]["stamp"] in conf, "play first-launch: the manifest's prefix stamp = the launcher's")
        _check(m["nativefier"]["keys"].get("internalUrls") == "^https://(web|replay)\\.fightcade\\.com/", "play first-launch: nativefier keys")
        _check("nVidSelect 4" in m["ini"]["overrides"], "play first-launch: the FBNeo ini keys (option B)")
        _check(os.path.isfile(box.path("cab/data/fbneo/config/presets/coin-dojo-p1.ini")), "play first-launch: the stick presets are still written")
        rc, out = box.run(**dict(fl, fightcade_fetch_manifest=mpath))
        _check(rc == 0 and _changed(out) == 0, "play first-launch: second apply changed=0 (%d)" % _changed(out))
        rc, out = box.run(check=True, **dict(fl, fightcade_fetch_manifest=mpath))
        _check(rc == 0 and _changed(out) == 0, "play first-launch: dry run after it changed=0 (%d)" % _changed(out))
        rc, sh = box.shell("HK_FC_ALLOW_ROOT=1 HK_FC_FETCH_MANIFEST=/w/cab/etc/fetch.json python3 /w/cab/usr/lib/coin-dojo-fightcade/hk-fightcade-fetch --check; echo rc=$?")
        _check("rc=10" in sh, "play first-launch: on the fresh cabinet the engine says downloads are needed (%s)" % sh[-300:])
        # BROKEN: an http url in the pins -> the role still renders it, the engine refuses it
        bad = json.loads(json.dumps(arch))
        bad[0]["url"] = "http://example.invalid/x.tar.xz"
        rc, out = box.run(**dict(fl, fightcade_archives=bad, fightcade_fetch_manifest=mpath))
        rc2, sh = box.shell("HK_FC_ALLOW_ROOT=1 HK_FC_FETCH_MANIFEST=/w/cab/etc/fetch.json python3 /w/cab/usr/lib/coin-dojo-fightcade/hk-fightcade-fetch --check; echo rc=$?")
        _check("rc=2" in sh and "not https" in sh, "BROKEN play first-launch: an http pin is refused by the engine (%s)" % sh[-300:])
        # back to deploy: the first-launch pieces go (the deploy then needs the local downloads again)
        rc, out = box.run(hk_fightcade_install="first-launch", fightcade_archives=arch, fightcade_fetch_manifest=mpath, fightcade_install="bogus")
        _check(rc != 0 and "use deploy or first-launch" in out, "BROKEN play: an unknown install mode is refused")
        box.write_archives()
        rc, out = box.run(fightcade_fetch_manifest=mpath)
        _check(rc == 0 and not os.path.exists(os.path.join(lib, "hk-fightcade-fetch")) and not os.path.exists(box.path("cab/etc/fetch.json")),
               "play: back to deploy removes the engine and the manifest and installs the trees (rc %d)%s" % (rc, "" if rc == 0 else out[-1500:]))
    finally:
        box.close()


def _reap_orphans():
    """#269: a tiny init for this test run on Linux. The launcher ends its wine processes and counts what is left with
    `kill -0` (coin-dojo-fightcade wine_pids), which a zombie still passes. On the cabinet systemd reaps the orphans;
    in a container without an init (the Forgejo job: PID 1 does not reap; `docker run` without --init) they stay
    zombies, and the wineserver checks saw leaks that were not there. So: become a child subreaper, run the tests in a
    child, reap every orphan of that tree, and exit with the child's code. macOS (launchd reaps): nothing to do."""
    if not sys.platform.startswith("linux"):
        return
    try:
        import ctypes
        if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:   # PR_SET_CHILD_SUBREAPER
            return
    except (OSError, AttributeError):
        return
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:
        return   # the child runs the tests
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, lambda n, _f: os.kill(pid, n))
    while True:
        try:
            w, status = os.wait()
        except ChildProcessError:
            os._exit(1)
        if w == pid:
            code = os.waitstatus_to_exitcode(status)
            os._exit(code if code >= 0 else 128 - code)


if __name__ == "__main__":
    _reap_orphans()
    main(globals())
