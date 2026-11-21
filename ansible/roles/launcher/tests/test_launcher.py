#!/usr/bin/env python3
"""Tests for the `launcher` role (#62): install launch.py with backup + launch.py.prev, rollback.

Pure stdlib, run directly:
    python3 ansible/roles/launcher/tests/test_launcher.py

Two layers:
  1. Rules over file TEXT: wiring (site.yml, vm.yml, Makefiles), order (guard -> gates -> backup -> install
     -> post-check), no become, no ES-DE restart; each rule also runs against a broken copy.
  2. The real role in the pinned Ansible container against localhost: check mode writes nothing; install
     over the 2026-11-14 hot-patch keeps it as .prev + backup; idempotent; rollback restores the hot-patch
     (sha) and is a no-op the second time; re-install. Broken: Debian RetroArch, relative n64 core, an argv
     mismatch, a missing emulator, a running game, rollback without .prev -> fail, live file untouched.
     counted SKIP when docker is not running or the image is not built.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANSIBLE = os.path.dirname(os.path.dirname(ROLE))
REPO = os.path.dirname(ANSIBLE)
COLL = os.path.join(REPO, "skills", "collections")
LIVE = os.path.join(COLL, "tests", "fixtures", "launch_live_20261114.py")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "tests", "lib"))
from hktest import check as _check, main, skip  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# ---- rules over text ----------------------------------------------------------------------------
def rule_install(t):
    p = []
    order = ['"py_compile"', "Staged launch.py --check", "Staged launch.py builds the expected argv", "Back up the live launch.py into the backup dir",
             "Keep the live launch.py as launch.py.prev", "name: Install launch.py\n", "Post-check the installed launch.py"]
    pos = [t.find(x) for x in order]
    if -1 in pos:
        p.append("missing step: %s" % order[pos.index(-1)])
    elif pos != sorted(pos):
        p.append("steps out of order (gates -> backup -> .prev -> install -> post-check)")
    if "rescue:" not in t or "launcher_installed | default(false) | bool" not in t:
        p.append("no rescue that restores launch.py.prev after a failed post-check")
    if "become: true" in t:
        p.append("become: launch.py is coindojo's file")
    if "restart es-de" in t:
        p.append("notifies an ES-DE restart (not needed: launch.py runs per game)")
    return p


def rule_guard(t):
    return [] if ("pgrep" in t and "a game is running" in t and "NO PGREP" in t) else ["no game guard"]


def rule_rollback(t):
    p = []
    if "launcher_prev" not in t or "remote_src: true" not in t:
        p.append("does not restore launcher_prev")
    if t.find("Back up the live launch.py before the rollback") > t.find("Restore launch.py.prev") or \
            "Back up the live launch.py before the rollback" not in t:
        p.append("no backup before the restore")
    if "launcher_rb_after.stat.checksum != launcher_rb.results[1].stat.checksum" not in t:
        p.append("no sha check after the restore")
    return p


def test_rules():
    tasks = os.path.join(ROLE, "tasks")
    _check(rule_install(_read(os.path.join(tasks, "install.yml"))) == [], "install.yml: gates, backup, .prev, install, post-check, rescue")
    _check(rule_guard(_read(os.path.join(tasks, "guard.yml"))) == [], "guard.yml: game guard, fails closed")
    _check(rule_rollback(_read(os.path.join(tasks, "rollback.yml"))) == [], "rollback.yml: backup, restore .prev, sha check")
    main = _read(os.path.join(tasks, "main.yml"))
    _check(main.find("guard.yml") < main.find("install.yml") < main.find("rollback.yml") and "import_tasks" in main,
           "main.yml: guard first, static imports")


def test_wiring():
    site, vm = _read(os.path.join(ANSIBLE, "site.yml")), _read(os.path.join(ANSIBLE, "vm.yml"))
    _check(re.search(r"role: launcher, tags: \[launcher\]", site) is not None, "site.yml: pios play runs launcher")
    _check(site.find("role: retroarch122") < site.find("role: launcher"), "site.yml: launcher after retroarch122")
    _check(re.search(r"role: launcher, tags: \[launcher\]", vm) is not None, "vm.yml: the VM runs launcher too")
    mk = _read(os.path.join(ANSIBLE, "Makefile"))
    _check("launch-rollback:" in mk and "-e launcher_action=rollback" in mk and "$(CHECK_ARG)" in mk.split("launch-rollback:")[1].split("\n\n")[0],
           "Makefile: launch-rollback passes launcher_action=rollback and is a dry run unless APPLY=1")
    _check("deploy-launch:" in mk and "TAGS=launcher" in mk, "Makefile: deploy-launch = TAGS=launcher")
    top = _read(os.path.join(REPO, "Makefile"))
    _check(" launch-rollback " in top and "deploy-launch:" in top, "top Makefile: deploy-launch + launch-rollback")
    defaults = _read(os.path.join(ROLE, "defaults", "main.yml"))
    _check("/data/emulators/retroarch-1.22/retroarch" in defaults, "defaults: 1.22 binary is required before install")


def test_broken_rules():
    t = _read(os.path.join(ROLE, "tasks", "install.yml"))
    _check(rule_install(t.replace('"py_compile"', '"compileall"')) != [], "broken rule: no py_compile is caught")
    a, b = "Keep the live launch.py as launch.py.prev", "Install launch.py\n"
    _check(rule_install(t.replace(a, "XX").replace(b, a + "\n").replace("XX", b.strip())) != [],
           "broken rule: install before .prev is caught")
    _check(rule_install(t.replace("rescue:", "always_:")) != [], "broken rule: no rescue is caught")
    _check(rule_install(t + "\n      become: true\n") != [], "broken rule: become is caught")
    _check(rule_guard("- debug: msg=hi") != [], "broken rule: no guard is caught")
    r = _read(os.path.join(ROLE, "tasks", "rollback.yml"))
    _check(rule_rollback(r.replace("Back up the live launch.py before the rollback", "x")) != [],
           "broken rule: rollback without backup is caught")


# ---- the real role in the container ---------------------------------------------------------------
PLAYBOOK = """- hosts: localhost
  connection: local
  gather_facts: false
  roles: [launcher]
"""
# Minimal pgrep for the test container (python:slim has no procps): -a -x NAME | -a -f REGEX (as test_esde_local.py).
PGREP_SHIM = r'''#!/usr/bin/env python3
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
    """A throwaway cabinet home at /w/home; the repo's skills/collections mounted read-only at /coll."""

    def __init__(self, image):
        self.image = image
        base = os.path.join(os.path.expanduser("~"), ".cache", "coin-dojo", "ansible")  # colima mounts $HOME
        os.makedirs(base, exist_ok=True)
        self.dir = tempfile.mkdtemp(prefix="t-launcher-", dir=base)
        for d in ("home/ES-DE/scripts", "home/ES-DE/themes/_backups", "emu", "src", "bin"):
            os.makedirs(os.path.join(self.dir, d), exist_ok=True)
        for f in ("emu/retroarch", "emu/core.so"):
            open(os.path.join(self.dir, f), "w").close()
        with open(os.path.join(self.dir, "site.yml"), "w") as fh:
            fh.write(PLAYBOOK)
        with open(self.path("bin/pgrep"), "w") as fh:
            fh.write(PGREP_SHIM)
        os.chmod(self.path("bin/pgrep"), 0o755)
        shutil.copy(LIVE, self.path("home/ES-DE/scripts/launch.py"))
        os.chmod(self.path("home/ES-DE/scripts/launch.py"), 0o755)

    def path(self, rel):
        return os.path.join(self.dir, rel)

    def broken_src(self, old, new):
        src = _read(os.path.join(COLL, "launch.py"))
        assert old in src, old
        with open(self.path("src/launch.py"), "w") as fh:
            fh.write(src.replace(old, new, 1))
        return "/w/src/launch.py"

    def run(self, check=False, action="install", pre="", **extra):
        evars = {"hk_cabinet_home": "/w/home", "hk_game_procs": ["retroarch", "flycast"],
                 "hk_game_patterns": ["retroarch-1.22/retroarc[h]"], "launcher_user_home": "/w/home", "launcher_src_dir": "/coll",
                 "launcher_require_files": ["/w/emu/retroarch", "/w/emu/core.so"],
                 "launcher_action": action, "launcher_pgrep": "/w/bin/pgrep", "ansible_python_interpreter": "/usr/local/bin/python3"}
        evars.update(extra)
        with open(self.path("vars.json"), "w") as fh:
            json.dump(evars, fh)
        cmd = "%sansible-playbook -i localhost, -e @/w/vars.json %s /w/site.yml" % (pre, "--check --diff" if check else "")
        r = subprocess.run(["docker", "run", "--rm", "-v", "%s:/w" % self.dir, "-v", "%s:/roles/launcher:ro" % ROLE,
                            "-v", "%s:/coll:ro" % COLL, "-e", "ANSIBLE_ROLES_PATH=/roles",
                            "-e", "ANSIBLE_LOCAL_TEMP=/tmp/.al", "-e", "ANSIBLE_NOCOLOR=1", self.image, "sh", "-c", cmd],
                           capture_output=True, text=True, timeout=300)
        return r.returncode, r.stdout + r.stderr

    def sha(self, rel):
        p = self.path(rel)
        return _sha(p) if os.path.exists(p) else None

    def backups(self, suffix):
        root = self.path("home/ES-DE/themes/_backups")
        return sorted(d for d in os.listdir(root) if d.endswith(suffix))

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def _changed(out):
    m = re.search(r"localhost\s*:\s*ok=\d+\s+changed=(\d+)", out)
    return int(m.group(1)) if m else -1


def _tail(rc, out):
    return "" if rc == 0 else "\n" + out[-2500:]


def test_play_in_container():
    image = _image() if shutil.which("docker") else None
    if not image or subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        skip("play tests: docker not running or ansible image not built; make -C ansible ansible-image")
        return
    hot, new = _sha(LIVE), _sha(os.path.join(COLL, "launch.py"))
    live, prev = "home/ES-DE/scripts/launch.py", "home/ES-DE/scripts/launch.py.prev"
    box = Box(image)
    try:
        rc, out = box.run(check=True)
        _check(rc == 0 and "WILL INSTALL" in out, "play: check mode plans the install" + _tail(rc, out))
        _check("RESULT: PASS launch-check" in out, "play: check mode reports the live hot-patch passes the argv check")
        _check(box.sha(live) == hot and box.sha(prev) is None and box.backups("-launch") == [],
               "play: check mode wrote nothing (live = hot-patch, no .prev, no backup)")

        rc, out = box.run()
        _check(rc == 0, "play: install over the hot-patch" + _tail(rc, out))
        _check(box.sha(live) == new and box.sha(prev) == hot, "play: launch.py = repo, launch.py.prev = hot-patch")
        bk = box.backups("-launch")
        _check(len(bk) == 1 and box.sha("home/ES-DE/themes/_backups/%s/launch.py" % bk[0]) == hot,
               "play: backup dir <stamp>-launch/launch.py = hot-patch")
        _check(os.stat(box.path(live)).st_mode & 0o777 == 0o755, "play: launch.py mode 0755")

        rc, out = box.run()
        _check(rc == 0 and _changed(out) == 0 and box.sha(prev) == hot,
               "play: second install is a no-op, .prev kept (changed=%d)" % _changed(out))

        rc, out = box.run(check=True, action="rollback")
        _check(rc == 0 and "WILL RESTORE" in out and box.sha(live) == new, "play: rollback dry run writes nothing" + _tail(rc, out))
        rc, out = box.run(action="rollback")
        _check(rc == 0 and box.sha(live) == hot, "play: rollback restores the hot-patch (sha)" + _tail(rc, out))
        rb = box.backups("-launch-rollback")
        _check(len(rb) == 1 and box.sha("home/ES-DE/themes/_backups/%s/launch.py" % rb[0]) == new,
               "play: rollback backed up the replaced file")
        rc, out = box.run(action="rollback")
        _check(rc == 0 and _changed(out) == 0, "play: second rollback is a no-op (changed=%d)" % _changed(out))

        rc, out = box.run()
        _check(rc == 0 and box.sha(live) == new and box.sha(prev) == hot, "play: re-install after rollback" + _tail(rc, out))

        # --- broken cases: each must fail and leave launch.py as it was ----------------------------
        before = box.sha(live)
        src = box.broken_src('RETROARCH = "/data/emulators/retroarch-1.22/retroarch"', 'RETROARCH = "/usr/bin/retroarch"')
        rc, out = box.run(launcher_src=src)
        _check(rc != 0 and "1.20" in out and box.sha(live) == before, "broken play: Debian RetroArch is refused, nothing written")
        src = box.broken_src('"n64": "/data/emulators/mupen64plus-gles/mupen64plus_next_libretro.so"',
                             '"n64": "mupen64plus-gles/mupen64plus_next_libretro.so"')
        rc, out = box.run(launcher_src=src)
        _check(rc != 0 and "relative" in out and box.sha(live) == before, "broken play: relative n64 core is refused")
        src = box.broken_src('"nes": "nestopia"', '"nes": "mesen"')
        rc, out = box.run(launcher_src=src)
        _check(rc != 0 and "FAIL launch-argv nes" in out and box.sha(live) == before,
               "broken play: an argv mismatch fails the staged gate, nothing written")
        rc, out = box.run(launcher_require_files=["/w/emu/retroarch", "/w/emu/missing.so"],
                          launcher_src=box.broken_src("# GLES3 build", "# GLES3 build (changed)"))
        _check(rc != 0 and "missing on the host" in out and box.sha(live) == before,
               "broken play: a missing emulator file blocks the install")
        rc, out = box.run(pre="cp /bin/sleep /tmp/retroarch && (/tmp/retroarch 60 &) && ",
                          launcher_src=box.broken_src("# GLES3 build", "# GLES3 build (changed)"))
        _check(rc != 0 and "a game is running" in out and box.sha(live) == before, "broken play: a running game blocks it")
        rc, out = box.run(launcher_pgrep="/nonexistent/pgrep", launcher_src=box.broken_src("# GLES3 build", "# GLES3 build (changed)"))
        _check(rc != 0 and "NO PGREP" in out and box.sha(live) == before, "broken play: no pgrep fails closed")
        # a failed post-check after the install puts launch.py.prev back
        exp = json.load(open(os.path.join(COLL, "launch_expected.json")))
        exp["systems"]["snes"][0] = "/usr/bin/retroarch"
        with open(box.path("wrong.json"), "w") as fh:
            json.dump(exp, fh)
        rc, out = box.run(launcher_post_expected="/w/wrong.json",
                          launcher_src=box.broken_src("# GLES3 build", "# GLES3 build (changed)"))
        _check(rc != 0 and "launch.py.prev was put back" in out and box.sha(live) == before,
               "broken play: failed post-check restores launch.py.prev (live sha back to %s)" % (before or "")[:8])
        os.remove(box.path(prev))
        rc, out = box.run(action="rollback")
        _check(rc != 0 and "nothing to roll back to" in out and box.sha(live) == before,
               "broken play: rollback without launch.py.prev fails")
    finally:
        box.close()


if __name__ == "__main__":
    main(globals())
