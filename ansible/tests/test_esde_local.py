#!/usr/bin/env python3
"""Integration test for the ES-DE deploy roles (#89): the real roles, in the pinned container (bin/ans),
against a throwaway fake-cabinet tree under ~/.cache/coin-dojo/ansible (mounted by bin/ans). Never the cabinet.

    python3 ansible/tests/test_esde_local.py        # SKIP when docker is not running

Proves, in order:
  1. --check: plans changes, writes nothing (tree hash unchanged)
  2. apply: theme (named files + personal overlay, cabinet-only file kept, retired file removed + backed up),
     nested build + validator, virtual gamelists, es_systems.xml, links; backups; ES-DE "restarted" once
  3. --check right after: changed=0       4. apply again: changed=0, no second restart
  5. workspace gamelist edit (make pull-gamelists shape) is copied; one restart
  6. FIGHTCADE (#219): on with the switch + launcher (the #214 state), then a default redeploy removes it from
     es_systems.xml, ES-DE's gamelists (backed up first) and /data/nested; QUIT TO MAIN MENU stays
Broken cases (must FAIL, tree unchanged): a game running (guard), a dead ROM link (validator gate),
the cabinet's gamelist changed after the pull (drift).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ANSIBLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(ANSIBLE)
FIX = os.path.join(REPO, "skills", "collections", "tests", "fixtures")
sys.path.insert(0, os.path.join(REPO, "skills", "collections"))
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
from nested import ALL_VIRTUAL  # noqa: E402  (the one source of the virtual-system names, #94)
from hktest import check, finish, skip  # noqa: E402
CACHE = os.environ.get("ANS_CACHE", os.path.join(os.environ.get("HK_CACHE", os.path.expanduser("~/.cache/coin-dojo")), "ansible"))

PGREP_SHIM = r'''#!/usr/bin/env python3
# Minimal pgrep for the test container (python:slim has no procps): -a -x NAME | -a -f REGEX.
import os, re, sys
mode, pat = sys.argv[-2], sys.argv[-1]
hit = 1
for pid in filter(str.isdigit, os.listdir("/proc")):
    if int(pid) == os.getpid():
        continue
    try:
        comm = open("/proc/%s/comm" % pid).read().strip()
        cmd = open("/proc/%s/cmdline" % pid, "rb").read().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        continue
    if (mode == "-x" and comm == pat) or (mode == "-f" and re.search(pat, cmd)):
        print(pid, cmd); hit = 0
sys.exit(hit)
'''


def tree_hash(base):
    h = hashlib.sha256()
    for d, dirs, files in sorted(os.walk(base)):
        dirs.sort()
        rel = os.path.relpath(d, base)
        if rel.startswith(("ws", "bin")) or rel == "vars.json":
            continue
        for n in sorted(files + [x for x in dirs if os.path.islink(os.path.join(d, x))]):
            p = os.path.join(d, n)
            if os.path.relpath(p, base) in ("kills", "vars.json"):
                continue
            h.update(os.path.relpath(p, base).encode())
            if os.path.islink(p):
                h.update(b"->" + os.readlink(p).encode())
            else:
                with open(p, "rb") as f:
                    h.update(f.read())
    return h.hexdigest()


def build_tree():
    os.makedirs(CACHE, exist_ok=True)
    b = tempfile.mkdtemp(prefix="esde-local-", dir=CACHE)
    home = os.path.join(b, "home", "ES-DE")
    gl = os.path.join(home, "gamelists")
    shutil.copytree(os.path.join(FIX, "gamelists"), gl)
    for s in os.listdir(gl):
        os.makedirs(os.path.join(b, "roms", s))
        for line in open(os.path.join(gl, s, "gamelist.xml"), encoding="utf-8"):
            if "<path>./" in line:
                open(os.path.join(b, "roms", s, line.split("<path>./", 1)[1].split("</path>", 1)[0]), "w").close()
    for d in ("custom_systems", "logs", "themes/_backups", "themes/coin-dojo/_inc/systems"):
        os.makedirs(os.path.join(home, d))
    th = os.path.join(home, "themes", "coin-dojo")
    open(os.path.join(th, "theme.xml"), "w").write("<theme><!-- old --></theme>\n")
    open(os.path.join(th, "cabinet-only.txt"), "w").write("not in the repo; must survive\n")
    open(os.path.join(th, "_inc", "systems", "auto-favorites.png"), "w").write("retired\n")
    log = "Nov 14 22:00:02 Error:   Couldn't find media for x\n"
    open(os.path.join(home, "logs", "es_log.txt"), "w").write(log)
    open(os.path.join(home, "logs", "es_log.txt.bak"), "w").write(log)
    hd = os.path.join(b, "coin-dojo")
    os.makedirs(os.path.join(hd, "folder_photos"))
    shutil.copy(os.path.join(FIX, "picks.html"), os.path.join(hd, "cabinet-picks.html"))
    shutil.copy(os.path.join(FIX, "dat.txt"), os.path.join(hd, "libretro.dat"))
    os.makedirs(os.path.join(b, "media"))
    os.makedirs(os.path.join(b, "bin"))
    shim = os.path.join(b, "bin", "pgrep")
    open(shim, "w").write(PGREP_SHIM)
    os.chmod(shim, 0o755)
    return b


def write_vars(b, **extra):
    v = {
        "esde_user_home": os.path.join(b, "home"),
        "coin_dojo_dir": os.path.join(b, "coin-dojo"),
        "settings_dir": os.path.join(b, "coin-dojo", "settings"),   # the settings defaults are hk_tools_dir based (#128)
        "esde_nested_root": os.path.join(b, "nested"),
        "esde_rom_root": os.path.join(b, "roms"),
        "esde_media_root": os.path.join(b, "media"),
        "esde_pgrep": os.path.join(b, "bin", "pgrep"),
        "esde_kill_cmd": "echo kill >> %s" % os.path.join(b, "kills"),
        "esde_session_dir": os.path.join(b, "run"),   # #209: no session here (no dir) unless a case makes one
        "esde_up_cmd": "true",
        "esde_restart_settle": 0, "esde_log_settle": 0, "esde_restart_retries": 1, "esde_restart_delay": 0,
        "esde_theme_src": os.path.join(REPO, "themes", "coin-dojo"),
        "esde_theme_overlay": os.path.join(REPO, "personal", "themes", "coin-dojo"),
        "esde_collections_src": os.path.join(REPO, "skills", "collections"),
        "esde_virtual_systems": list(ALL_VIRTUAL),     # the one source (#94); a play gets it from the rendered config
        "esde_collections_nested_args": ["--min-decade", "1"],
        "coin_dojo_edition": "personal",
        "esde_theme_personal": True, "esde_theme_art_dir": "", "esde_theme_rev_stamp": True,   # the theme levers (config exports)
        "esde_theme_trophies_hint": True,   # #178: the LP Trophies hint stays (its off case: esde_theme/tests)
        "esde_theme_hotkey_strip": True, "esde_theme_eye_glow": {"enabled": True, "peak": 0.92, "box": [680, 85, 960, 345]},
        "ansible_connection": "local",
        "ansible_python_interpreter": "/usr/local/bin/python3",
    }
    v.update(extra)
    p = os.path.join(b, "vars.json")
    with open(p, "w") as f:
        json.dump(v, f)
    return p


def play(b, *args, **extra):
    vf = write_vars(b, **extra)
    cmd = [os.path.join(ANSIBLE, "bin", "ans"), "ansible-playbook", "-i", "fake,", "tests/esde_local.yml",
           "-e", "@" + vf] + list(args)
    r = subprocess.run(cmd, cwd=ANSIBLE, capture_output=True, text=True,
                       env=dict(os.environ, ANSIBLE_FORCE_COLOR="", ANS_WORKSPACE=""))
    m = re.search(r"fake\s*:\s*ok=(\d+)\s+changed=(\d+).*?failed=(\d+)", r.stdout)
    changed = int(m.group(2)) if m else -1
    return r.returncode, changed, r.stdout + r.stderr


def kills(b):
    p = os.path.join(b, "kills")
    return len(open(p).read().split()) if os.path.exists(p) else 0


def main():
    if shutil.which("docker") is None or subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        # counted SKIP (#135); the Forgejo runner's `docker` is a stopped-daemon shim (dind has no bind mounts)
        skip("docker is not running (colima start); the ES-DE roles were not run locally")
        return finish()
    b = build_tree()
    home = os.path.join(b, "home", "ES-DE")
    th = os.path.join(home, "themes", "coin-dojo")
    try:
        h0 = tree_hash(b)
        rc, ch, out = play(b, "--check", "--diff")
        check(rc == 0 and ch > 0, "1 check: plans changes (rc %d, changed %d)" % (rc, ch))
        check(tree_hash(b) == h0, "1 check: wrote nothing")
        if rc:
            print(out[-3000:])

        rc, ch, out = play(b)
        check(rc == 0 and ch > 0, "2 apply: rc %d, changed %d" % (rc, ch))
        if rc:
            print(out[-4000:])
        repo_th = os.path.join(REPO, "themes", "coin-dojo")
        # the deploy fills the REV stamp (#97): the live file is the repo file with @COIN_DOJO_VERSION@ replaced
        live, src = open(os.path.join(th, "theme.xml")).read(), open(os.path.join(repo_th, "theme.xml")).read()
        m = re.search(r"REV ([A-Za-z0-9.+-]+)<", live)
        check(m is not None and "@COIN_DOJO_VERSION@" not in live
              and live == src.replace("@COIN_DOJO_VERSION@", m.group(1)),
              "2 apply: theme.xml from the repo, REV stamp filled (%s)" % (m.group(1) if m else "none"))
        per = os.path.join(REPO, "personal", "themes", "coin-dojo", "_inc", "art", "backdrops", "default.png")
        if os.path.exists(per):
            check(open(os.path.join(th, "_inc/art/backdrops/default.png"), "rb").read() == open(per, "rb").read(),
                  "2 apply: backdrop is the personal art, not the repo placeholder")
        check(os.path.exists(os.path.join(th, "cabinet-only.txt")), "2 apply: cabinet-only theme file kept (no --delete)")
        check(not os.path.exists(os.path.join(th, "_inc/systems/auto-favorites.png")), "2 apply: retired file removed")
        bk = os.listdir(os.path.join(home, "themes", "_backups"))
        check(any(x.endswith("-theme") for x in bk) and any(x.endswith("-collections") for x in bk), "2 apply: backups %s" % bk)
        tb = [x for x in bk if x.endswith("-theme")]
        check(tb and os.path.exists(os.path.join(home, "themes/_backups", tb[0], "theme/_inc/systems/auto-favorites.png"))
              and "old" in open(os.path.join(home, "themes/_backups", tb[0], "theme/theme.xml")).read(),
              "2 apply: theme backup holds the old theme.xml + the retired file")
        check(os.path.isfile(os.path.join(home, "custom_systems", "es_systems.xml")), "2 apply: es_systems.xml")
        check(os.path.isfile(os.path.join(home, "gamelists", "must-play", "gamelist.xml")), "2 apply: virtual gamelists")
        check(os.path.islink(os.path.join(b, "nested")), "2 apply: nested root link")
        check(kills(b) == 1, "2 apply: ES-DE restarted exactly once (%d)" % kills(b))
        check("RESULT: PASS" in out and "links" in out, "2 apply: post-restart verify ran and passed")

        rc, ch, out = play(b, "--check", "--diff")
        check(rc == 0 and ch == 0, "3 check after apply: idempotent (rc %d, changed %d)" % (rc, ch))
        if ch:
            print("\n".join(l for l in out.splitlines() if l.startswith(("changed", "TASK")))[-3000:])
        rc, ch, out = play(b)
        check(rc == 0 and ch == 0 and kills(b) == 1, "4 apply again: changed %d, restarts %d" % (ch, kills(b)))

        # 5. workspace edit (pull-gamelists shape): copied, one more restart
        ws = os.path.join(b, "ws")
        shutil.copytree(os.path.join(home, "gamelists"), os.path.join(ws, "gamelists"))
        md5 = subprocess.run("cd %s && md5sum */gamelist.xml" % os.path.join(home, "gamelists"), shell=True,
                             capture_output=True, text=True).stdout
        open(os.path.join(ws, "pulled.md5"), "w").write(md5)
        g = os.path.join(ws, "gamelists", "cps2", "gamelist.xml")
        txt = open(g).read()
        open(g, "w").write(txt.replace("<name>", "<name>Edited ", 1))
        rc, ch, out = play(b, esde_gamelists_workspace=ws)
        live = open(os.path.join(home, "gamelists", "cps2", "gamelist.xml")).read()
        check(rc == 0 and "<name>Edited " in live and kills(b) == 2, "5 workspace edit copied (rc %d, restarts %d)" % (rc, kills(b)))
        if rc:
            print(out[-3000:])

        # 5b. the same while the session shows the boot menu (#209): files copied, no ES-DE restart (nothing runs;
        # a kill + wait for AppRun would time out because the menu waits)
        os.makedirs(os.path.join(b, "run"))
        open(os.path.join(b, "run", "current-mode"), "w").write("menu\n")
        open(os.path.join(b, "run", "session.pid"), "w").write("1\n")
        md5 = subprocess.run("cd %s && md5sum */gamelist.xml" % os.path.join(home, "gamelists"), shell=True,
                             capture_output=True, text=True).stdout
        open(os.path.join(ws, "pulled.md5"), "w").write(md5)
        open(g, "w").write(txt.replace("<name>", "<name>Menu ", 1))
        rc, ch, out = play(b, "-e", "hk_note=coin-dojo-session", esde_gamelists_workspace=ws)
        live = open(os.path.join(home, "gamelists", "cps2", "gamelist.xml")).read()
        check(rc == 0 and "<name>Menu " in live and kills(b) == 2 and "nothing to restart" in out
              and not os.path.exists(os.path.join(b, "run", "next-mode")),
              "5b boot menu showing: edit copied, no restart, no request (rc %d, restarts %d)" % (rc, kills(b)))
        if rc:
            print(out[-3000:])
        shutil.rmtree(os.path.join(b, "run"))

        # 6. FIGHTCADE carousel entry (#219): an earlier deploy built it, the default (off) redeploy removes it
        fc_bin = os.path.join(b, "bin", "coin-dojo-fightcade")
        open(fc_bin, "w").write("#!/bin/sh\n")
        es_xml, fc_gl = os.path.join(home, "custom_systems", "es_systems.xml"), os.path.join(home, "gamelists", "fightcade")
        rc, ch, out = play(b, esde_collections_fightcade_bin=fc_bin, esde_collections_fightcade_entry=True)
        check(rc == 0 and "<name>fightcade</name>" in open(es_xml).read() and os.path.isfile(fc_gl + "/gamelist.xml")
              and os.path.lexists(os.path.join(b, "nested", "fightcade")) and kills(b) == 3,
              "6 FIGHTCADE on (switch + launcher): system, gamelist, link (rc %d, restarts %d)" % (rc, kills(b)))
        if rc:
            print(out[-3000:])
        bk0 = set(os.listdir(os.path.join(home, "themes", "_backups")))
        rc, ch, out = play(b, esde_collections_fightcade_bin=fc_bin)
        es = open(es_xml).read()
        check(rc == 0 and "<name>fightcade</name>" not in es and "<name>quit</name>" in es and not os.path.exists(fc_gl)
              and os.path.isdir(os.path.join(home, "gamelists", "quit"))
              and not os.path.lexists(os.path.join(b, "nested", "fightcade")) and kills(b) == 4,
              "6 default redeploy: FIGHTCADE gone (es_systems, gamelists, nested), QUIT stays (rc %d, restarts %d)"
              % (rc, kills(b)))
        if rc:
            print(out[-3000:])
        nb = [x for x in set(os.listdir(os.path.join(home, "themes", "_backups"))) - bk0 if x.endswith("-collections")]
        names = tarfile.open(os.path.join(home, "themes", "_backups", nb[0], "gamelists.tar")).getnames() if nb else []
        check("gamelists/fightcade/gamelist.xml" in names, "6 the removed FIGHTCADE gamelist is in the new backup %s" % nb)
        rc, ch, out = play(b, "--check", "--diff", esde_collections_fightcade_bin=fc_bin)
        check(rc == 0 and ch == 0, "6 check after the removal: idempotent (rc %d, changed %d)" % (rc, ch))
        if ch:
            print("\n".join(l for l in out.splitlines() if l.startswith(("changed", "TASK")))[-3000:])
        os.remove(fc_bin)

        # BROKEN: drift (the cabinet's file changed after the pull)
        open(g, "w").write(txt.replace("<name>", "<name>Again ", 1))
        open(os.path.join(home, "gamelists", "cps2", "gamelist.xml"), "a").write("<!-- es-de saved -->\n")
        h1 = tree_hash(b)
        rc, ch, out = play(b, esde_gamelists_workspace=ws)
        check(rc != 0 and "the cabinet's file changed since the pull" in out and tree_hash(b) == h1,
              "BROKEN drift: aborts, nothing written (rc %d)" % rc)

        # BROKEN: game running (the shim sees ansible-playbook itself as the "game")
        rc, ch, out = play(b, esde_game_patterns=["ansible-playboo[k]"])
        check(rc != 0 and "ABORT: a game is running" in out and tree_hash(b) == h1, "BROKEN guard: aborts, nothing written")
        rc, ch, out = play(b, esde_pgrep="/nonexistent/pgrep")
        check(rc != 0 and "NO PGREP" in out and tree_hash(b) == h1, "BROKEN guard without pgrep: fails closed")

        # BROKEN: the session is in Fightcade mode (#209): counts as a running game. The live session is faked by
        # session.pid = 1 (ansible-playbook in the container) with "coin-dojo-session" on its command line.
        os.makedirs(os.path.join(b, "run"))
        open(os.path.join(b, "run", "current-mode"), "w").write("fightcade\n")
        open(os.path.join(b, "run", "session.pid"), "w").write("1\n")
        h1f = tree_hash(b)
        rc, ch, out = play(b, "-e", "hk_note=coin-dojo-session")
        check(rc != 0 and "ABORT: a game is running" in out and "the session is in Fightcade mode" in out
              and tree_hash(b) == h1f, "BROKEN guard: Fightcade mode aborts, nothing written (rc %d)" % rc)
        # a stale current-mode (the session pid is dead) is no session: the guard lets the dry run through
        open(os.path.join(b, "run", "session.pid"), "w").write("999999\n")
        rc, ch, out = play(b, "--check", "-e", "hk_note=coin-dojo-session")
        check("ABORT: a game is running" not in out, "stale current-mode=fightcade (dead session pid) does not block")
        shutil.rmtree(os.path.join(b, "run"))

        # BROKEN: dead ROM link -> validator gate
        os.remove(os.path.join(b, "roms", "cps2", sorted(os.listdir(os.path.join(b, "roms", "cps2")))[0]))
        h2 = tree_hash(b)
        rc, ch, out = play(b)
        check(rc != 0 and "ABORT: validate_nested.py failed" in out and tree_hash(b) == h2 and kills(b) == 4,
              "BROKEN validator: aborts before any cabinet write (rc %d)" % rc)
    finally:
        shutil.rmtree(b, ignore_errors=True)
    return finish()


if __name__ == "__main__":
    sys.exit(main())
