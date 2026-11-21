#!/usr/bin/env python3
"""Tests for the `retroarch122` role (#63): RetroArch 1.22.2 + GLES N64 core + N64 cfg/remap/options.

Pure stdlib, pytest-compatible, or run directly:
    python3 ansible/roles/retroarch122/tests/test_retroarch122.py

Two layers:
  1. Rules over file TEXT (no deps). Each rule also runs against a deliberately broken copy
     (test_broken_*): swapped remap pair, missing axis bind, input_driver not udev, no guard, bad pin.
  2. The real role in the pinned Ansible container against localhost (test_play_*): good run +
     idempotent, checksum mismatch fails before any copy, a running "retroarch" blocks every write,
     a wrong cabinet file with no local build fails. counted SKIP when docker is not running.
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
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "tests", "lib"))
from hktest import check as _check, main, skip  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _read(*parts):
    with open(os.path.join(ROLE, *parts), encoding="utf-8") as fh:
        return fh.read()


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _kv(text):
    """RetroArch cfg/rmp text -> {key: value} (key = "value" lines)."""
    return dict(re.findall(r'(?m)^\s*([A-Za-z0-9_\-]+)\s*=\s*"([^"]*)"\s*$', text))


# ---- rules over text: each returns a list of problems (empty = ok) ----------------------------
def rule_cfg(cfg):
    p, kv = [], _kv(cfg)
    if kv.get("input_poll_type_behavior") != "1":
        p.append("input_poll_type_behavior != 1")
    want = {"l_x_minus_axis": "-0", "l_x_plus_axis": "+0", "l_y_minus_axis": "-1", "l_y_plus_axis": "+1"}
    for pl in (1, 2):
        for k, v in want.items():
            key = "input_player%d_%s" % (pl, k)
            if kv.get(key) != v:
                p.append("%s != %s (got %r)" % (key, v, kv.get(key)))
    return p


def rule_remap(rmp, binds):
    p, kv = [], _kv(rmp)
    for pl in (1, 2):
        for slot, val in binds.items():
            key = "input_player%d_%s" % (pl, slot)
            if kv.get(key) != val:
                p.append("%s != %s (got %r)" % (key, val, kv.get(key)))
    return p


def rule_defaults(defaults):
    p = []
    if not re.search(r"key: input_driver, value: udev\b", defaults):
        p.append("retroarch.cfg input_driver=udev not managed")
    shas = re.findall(r"(?m)^\s*sha256:\s*(\S+)\s*$", defaults)
    if len(shas) < 5:
        p.append("expected >= 5 sha256 pins, found %d" % len(shas))
    p += ["bad sha256 pin %r" % s for s in shas if not HEX64.match(s)]
    if not re.search(r'''(?m)^retroarch122_guard_procs:\s*"\{\{ hk_game_procs \| join\('\|'\) \}\}"''', defaults):
        p.append("guard is not the hk_game_procs pass-through")
    if not re.search(r"key: mupen64plus-cpucore, value: dynamic_recompiler", defaults):
        p.append("cpucore=dynamic_recompiler missing")
    return p


def rule_tasks(tasks):
    p = []
    names = re.findall(r"(?m)^- name: (.*)$", tasks)
    blocks = re.split(r"(?m)^(?=- name: )", tasks)[1:]
    first_write = next((i for i, b in enumerate(blocks)
                        if re.search(r"ansible\.builtin\.(copy|file|lineinfile|template):", b)), None)
    guard = next((i for i, b in enumerate(blocks) if "retroarch122_running.rc == 1" in b), None)
    if guard is None:
        p.append("no running-game guard")
    elif first_write is not None and guard > first_write:
        p.append("guard comes after the first write (%s)" % names[first_write])
    if "item.stat.checksum == item.item.sha256" not in tasks:
        p.append("local artifacts are not checked against their pins")
    if "item.0.stat.checksum == item.0.item.sha256" not in tasks:
        p.append("cabinet artifacts are not checked against their pins")
    if "checksum_algorithm: sha256" not in tasks:
        p.append("stat does not use sha256")
    if re.search(r"(?m)^\s*(command|shell|cmd):.*(/retroarch|flycast)\b(?!\")", tasks.replace("pgrep", "")):
        p.append("role runs an emulator binary")
    return p


def _decl():
    """config/variables.d/shaders.yml: the CRT levers' defaults now live there (#126)."""
    with open(os.path.join(os.path.dirname(ANSIBLE), "config", "variables.d", "shaders.yml"), encoding="utf-8") as fh:
        return fh.read()


def _off_cores(decl):
    m = re.search(r"(?ms)^  hk_crt_off_cores:.*?^    default:\s*\[(.*?)\]\s*$", decl)
    return [c.strip() for c in m.group(1).split(",") if c.strip()] if m else []


def rule_crt(defaults, tasks, decl=None):
    decl = _decl() if decl is None else decl
    """CRT (#102): auto presets on, pinned shader files, global -> crt-pi, off-list cores -> stock."""
    p = []
    for key in ("auto_shaders_enable", "video_shader_enable"):
        if not re.search(r'key: %s, value: "\{\{ retroarch122_crt_enabled \| bool \| lower \}\}"\s*\}' % key, defaults):
            p.append("%s does not follow retroarch122_crt_enabled in retroarch122_cfg_keys" % key)
    for var, hk in (("retroarch122_crt_enabled", "hk_crt_enabled"), ("retroarch122_crt_preset", "hk_crt_preset"),
                    ("retroarch122_crt_off_cores", "hk_crt_off_cores")):
        if not re.search(r'(?m)^%s:\s*"\{\{ %s \}\}"\s*$' % (var, hk), defaults):
            p.append("%s is not a pass-through of %s" % (var, hk))
    if not re.search(r"(?m)^  hk_crt_enabled:\n(?:    .*\n)*?    default: true\s*$", decl):
        p.append("hk_crt_enabled does not default to true")
    if not re.search(r"(?m)^retroarch122_shader_commit:\s*[0-9a-f]{40}\s*$", defaults):
        p.append("shader commit is not pinned to a 40-hex sha")
    if "{{ retroarch122_shader_commit }}" not in defaults:
        p.append("shader base url does not use the pinned commit")
    block = defaults.split("retroarch122_shader_files:", 1)[-1].split("retroarch122_shader_stock_preset", 1)[0]
    pins = re.findall(r"- path: (\S+)\n\s+sha256: (\S+)", block)
    paths = [x for x, _ in pins]
    for need in ("crt/crt-pi.glslp", "crt/shaders/crt-pi.glsl", "stock.glsl"):
        if need not in paths:
            p.append("shader file %s is not pinned" % need)
    p += ["bad shader sha256 %r (%s)" % (h, x) for x, h in pins if not HEX64.match(h)]
    if "checksum: \"sha256:{{ item.sha256 }}\"" not in tasks:
        p.append("shader download is not checked against its sha256")
    # presets: each #reference stays inside the shaders dir and points at a file we install
    installed = set(paths) | {"stock.glslp"}
    g = re.search(r'dest: "\{\{ retroarch122_cfg_dir \}\}/config/global\.glslp"', tasks)
    gref = re.search(r'#reference "((?:\.\./)+)shaders/shaders_glsl/\{\{ (\w+) \}\}"\n(?:(?!- name).*\n)*?\s*dest: "[^"]*/config/global\.glslp"', tasks)
    if not g or not gref:
        p.append("no config/global.glslp preset")
    else:
        if gref.group(1) != "../":
            p.append("global preset path %r does not land in the shaders dir" % gref.group(1))
        crt = re.search(r"(?ms)^  hk_crt_preset:.*?^    default:\s*(\S+)\s*$", decl)
        if gref.group(2) != "retroarch122_crt_preset" or not crt or crt.group(1) not in installed:
            p.append("global preset does not reference an installed CRT preset")
        elif "crt-pi" not in crt.group(1):
            p.append("global preset is not crt-pi")
    off = _off_cores(decl)
    for core in ("Mupen64Plus-Next", "PCSX-ReARMed"):
        if core not in off:
            p.append("off-list core %s is missing from retroarch122_crt_off_cores" % core)
    cref = re.search(r'#reference "((?:\.\./)+)shaders/shaders_glsl/\{\{ (\w+) \}\}"\n(?:(?!- name).*\n)*?\s*dest: "[^"]*/config/\{\{ item \}\}/\{\{ item \}\}\.glslp"', tasks)
    if not cref or "loop: \"{{ retroarch122_crt_off_cores }}\"" not in tasks:
        p.append("no per-core <core>/<core>.glslp preset looped over retroarch122_crt_off_cores")
    else:
        if cref.group(1) != "../../":
            p.append("core preset path %r does not land in the shaders dir" % cref.group(1))
        if cref.group(2) != "retroarch122_shader_stock_preset":
            p.append("core presets do not reference the stock pass-through")
    return p


def _defaults_binds(defaults):
    m = re.search(r"(?m)^retroarch122_n64_remap_binds:\s*\{(.*)\}\s*$", defaults)
    return dict(re.findall(r'(\w+):\s*"([^"]*)"', m.group(1))) if m else {}


# ---- the shipped role ------------------------------------------------------------------------
def test_cfg_has_poll_and_axis_binds():
    p = rule_cfg(_read("files", "Mupen64Plus-Next.cfg"))
    _check(not p, "Mupen64Plus-Next.cfg: poll type + P1/P2 axis binds %s" % (p or ""))


def test_remap_matches_bind_table():
    defaults = _read("defaults", "main.yml")
    binds = _defaults_binds(defaults)
    _check(len(binds) == 4, "remap bind table has 4 slots (%s)" % binds)
    p = rule_remap(_read("files", "Mupen64Plus-Next.rmp"), binds)
    _check(not p, "Mupen64Plus-Next.rmp matches the bind table for P1+P2 %s" % (p or ""))


def test_repo_files_match_live_pins():
    defaults = _read("defaults", "main.yml")
    for src, sha in re.findall(r"(?m)^\s*- src: (Mupen64Plus-Next\.\w+)\n(?:\s+\w+:.*\n)*?\s+sha256: (\w+)", defaults):
        _check(_sha(os.path.join(ROLE, "files", src)) == sha, "files/%s == live sha256 pin" % src)


def test_defaults_rules():
    p = rule_defaults(_read("defaults", "main.yml"))
    _check(not p, "defaults: udev, pins, guard, cpucore %s" % (p or ""))


def test_tasks_rules():
    p = rule_tasks(_read("tasks", "main.yml"))
    _check(not p, "tasks: guard first, sha256 pins checked, no emulator run %s" % (p or ""))


def test_no_binary_in_role():
    for root, _, files in os.walk(ROLE):
        for f in files:
            with open(os.path.join(root, f), "rb") as fh:
                head = fh.read(4)
            _check(head != b"\x7fELF", "no ELF in role: %s" % os.path.relpath(os.path.join(root, f), ROLE))


def test_site_wires_role():
    with open(os.path.join(ANSIBLE, "site.yml"), encoding="utf-8") as fh:
        _check(re.search(r"role:\s*retroarch122\b", fh.read()) is not None, "site.yml wires retroarch122")


def test_crt_rules():
    p = rule_crt(_read("defaults", "main.yml"), _read("tasks", "main.yml"))
    _check(not p, "CRT: auto presets on, pinned shaders, global -> crt-pi, off-list -> stock %s" % (p or ""))


# ---- broken cases: the rules MUST flag these ---------------------------------------------------
def test_broken_missing_axis_bind():
    cfg = re.sub(r'(?m)^input_player2_l_y_plus_axis.*\n', "", _read("files", "Mupen64Plus-Next.cfg"))
    _check(rule_cfg(cfg) != [], "broken: a missing P2 axis bind is flagged")


def test_broken_swapped_remap_pair():
    rmp = _read("files", "Mupen64Plus-Next.rmp")
    rmp = rmp.replace('input_player1_btn_r = "22"', "@@").replace('input_player1_btn_x = "23"', 'input_player1_btn_x = "22"')
    rmp = rmp.replace("@@", 'input_player1_btn_r = "23"')
    _check(rule_remap(rmp, _defaults_binds(_read("defaults", "main.yml"))) != [],
           "broken: swapped C-Up/C-Down pair is flagged")


def test_broken_input_driver_not_udev():
    d = _read("defaults", "main.yml").replace("key: input_driver, value: udev", "key: input_driver, value: x")
    _check(rule_defaults(d) != [], "broken: input_driver=x is flagged")


def test_broken_bad_pin():
    d = _read("defaults", "main.yml")
    d = d.replace("sha256: 32041d76", "sha256: 32041d7", 1)
    _check(rule_defaults(d) != [], "broken: a malformed sha256 pin is flagged")


def test_broken_no_guard():
    t = _read("tasks", "main.yml").replace("retroarch122_running.rc == 1", "true")
    _check(rule_tasks(t) != [], "broken: a role without the running-game guard is flagged")


def test_broken_no_remote_checksum():
    t = _read("tasks", "main.yml").replace("item.0.stat.checksum == item.0.item.sha256", "true")
    _check(rule_tasks(t) != [], "broken: no cabinet checksum assert is flagged")


def _crt_broken(label, file, old, new, count=1):
    d, t, c = _read("defaults", "main.yml"), _read("tasks", "main.yml"), _decl()
    if file == "decl":
        assert old in c, old
        c = c.replace(old, new, count)
    elif file == "defaults":
        assert old in d, old
        d = d.replace(old, new, count)
    else:
        assert old in t, old
        t = t.replace(old, new, count)
    _check(rule_crt(d, t, c) != [], "broken: " + label)


def test_broken_auto_shaders_false():
    _crt_broken("auto_shaders_enable=false is flagged", "defaults",
                'key: auto_shaders_enable, value: "{{ retroarch122_crt_enabled | bool | lower }}"', 'key: auto_shaders_enable, value: "false"')


def test_broken_auto_shaders_missing():
    _crt_broken("a missing auto_shaders_enable key is flagged", "defaults",
                '  - { key: auto_shaders_enable, value: "{{ retroarch122_crt_enabled | bool | lower }}" }\n', "")


def test_broken_global_preset_missing():
    _crt_broken("no global preset is flagged", "tasks", "config/global.glslp", "config/globalx.glslp", 9)


def test_broken_global_preset_elsewhere():
    _crt_broken("a global preset pointing at a non-CRT preset is flagged", "decl",
                "default: crt/crt-pi.glslp", "default: stock.glslp")


def test_broken_global_preset_outside_shaders_dir():
    _crt_broken("a global preset path that leaves the shaders dir is flagged", "tasks",
                '#reference "../shaders/shaders_glsl/{{ retroarch122_crt_preset }}"',
                '#reference "../../shaders/shaders_glsl/{{ retroarch122_crt_preset }}"')


def test_broken_core_preset_outside_shaders_dir():
    _crt_broken("a core preset path that leaves the shaders dir is flagged", "tasks",
                '#reference "../../shaders/shaders_glsl/{{ retroarch122_shader_stock_preset }}"',
                '#reference "../../../shaders/shaders_glsl/{{ retroarch122_shader_stock_preset }}"')


def test_broken_off_core_without_stock_preset():
    _crt_broken("an off-list core with no stock preset task is flagged", "tasks",
                'loop: "{{ retroarch122_crt_off_cores }}"\n', 'loop: []\n', 2)


def test_broken_off_core_dropped_from_list():
    _crt_broken("PCSX-ReARMed missing from the off list is flagged", "decl",
                "default: [Mupen64Plus-Next, PCSX-ReARMed]", "default: [Mupen64Plus-Next]")


def test_broken_shader_sha_malformed():
    _crt_broken("a malformed shader sha256 is flagged", "defaults",
                "sha256: dc2676b46f57", "sha256: dc2676b4", 1)


def test_broken_shader_commit_not_pinned():
    _crt_broken("an unpinned shader commit (branch name) is flagged", "defaults",
                "retroarch122_shader_commit: a35069864b4bf30f8c8e740d57206543fb7e745e",
                "retroarch122_shader_commit: master")


def test_broken_shader_no_checksum():
    _crt_broken("a shader download without checksum is flagged", "tasks",
                'checksum: "sha256:{{ item.sha256 }}"', "force: false")


# ---- the real role in the pinned container ----------------------------------------------------
# pgrep stand-in (the python:slim image has no procps): real `pgrep -a -x PATTERN` semantics over
# /proc/*/comm, rc 0 = match, 1 = none.
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

PLAYBOOK = """- hosts: localhost
  connection: local
  gather_facts: false
  roles: [retroarch122]
"""


def _image():
    try:
        out = subprocess.run([os.path.join(ANSIBLE, "bin", "ans"), "--image"], capture_output=True, text=True)
        img = out.stdout.strip()
        if subprocess.run(["docker", "image", "inspect", img], capture_output=True).returncode == 0:
            return img
    except OSError:
        pass
    return None


class Box:
    """A throwaway "cabinet" in a work dir mounted at /w; the role is mounted read-only at /roles."""

    def __init__(self, image):
        self.image = image
        base = os.path.join(os.path.expanduser("~"), ".cache", "coin-dojo", "ansible")  # colima mounts $HOME
        os.makedirs(base, exist_ok=True)
        self.dir = tempfile.mkdtemp(prefix="t-retroarch122-", dir=base)
        for d in ("build", "cab/emu", "cab/home/.config/retroarch", "bin"):
            os.makedirs(os.path.join(self.dir, d), exist_ok=True)
        with open(os.path.join(self.dir, "bin", "pgrep"), "w") as fh:
            fh.write(FAKE_PGREP)
        os.chmod(os.path.join(self.dir, "bin", "pgrep"), 0o755)
        with open(os.path.join(self.dir, "site.yml"), "w") as fh:
            fh.write(PLAYBOOK)
        self.write("cab/home/.config/retroarch/retroarch.cfg", 'video_driver = "gl"\ninput_driver = "x"\n')
        # shader "repo" (file:// source for get_url; the real role downloads the pinned commit)
        self.shader_files = []
        for rel, body in (("crt/crt-pi.glslp", 'shaders = "1"\nshader0 = "shaders/crt-pi.glsl"\n'),
                          ("crt/shaders/crt-pi.glsl", "// fake crt-pi\n"), ("stock.glsl", "// fake stock\n")):
            self.write("shadersrc/" + rel, body)
            self.shader_files.append({"path": rel, "sha256": hashlib.sha256(body.encode()).hexdigest()})
        self.artifacts = []
        for name, dest in (("retroarch", "/w/cab/emu/ra/retroarch"), ("BUILDINFO", "/w/cab/emu/ra/BUILDINFO"),
                           ("core.so", "/w/cab/emu/core/mupen64plus_next_libretro.so")):
            data = ("fake %s build\n" % name).encode()
            self.write("build/" + name, data)
            self.artifacts.append({"src": "/w/build/" + name, "dest": dest,
                                   "sha256": hashlib.sha256(data).hexdigest(), "mode": "0755"})

    def write(self, rel, data):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data.encode() if isinstance(data, str) else data)

    def path(self, rel):
        return os.path.join(self.dir, rel)

    def run(self, check=False, pre="", artifacts=None, shader_files=None, **extra):
        evars = {"hk_crt_enabled": True, "hk_crt_preset": "crt/crt-pi.glslp", "hk_crt_off_cores": ["Mupen64Plus-Next", "PCSX-ReARMed"],
                 "hk_game_procs": ["retroarch", "flycast"], "hk_cabinet_user": "root", "hk_cabinet_group": "root", "hk_cabinet_home": "/w/cab/home",
                 "cabinet_user": "root", "cabinet_group": "root", "cabinet_home": "/w/cab/home",
                 "ansible_become": False, "ansible_python_interpreter": "/usr/local/bin/python3",
                 "retroarch122_dir": "/w/cab/emu/ra", "retroarch122_core_dir": "/w/cab/emu/core",
                 "retroarch122_artifacts": artifacts if artifacts is not None else self.artifacts,
                 "retroarch122_shader_base_url": "file:///w/shadersrc",
                 "retroarch122_shader_files": shader_files if shader_files is not None else self.shader_files,
                 "hk_cheevos_enable": False, "hk_cheevos_user": "", "hk_cheevos_hardcore": True, "hk_cheevos_hardcore_by_core": {}}
        evars.update(extra)
        with open(self.path("vars.json"), "w") as fh:
            json.dump(evars, fh)
        cmd = "%sansible-playbook -i localhost, -e @/w/vars.json %s /w/site.yml" % (pre, "--check --diff" if check else "")
        r = subprocess.run(["docker", "run", "--rm", "-v", "%s:/w" % self.dir, "-v", "%s:/roles/retroarch122:ro" % ROLE,
                            # the shared filters (#197 hk_settings_state), as ansible.cfg names them for real runs
                            "-v", "%s:/filter_plugins:ro" % os.path.join(ANSIBLE, "filter_plugins"),
                            "-e", "ANSIBLE_ROLES_PATH=/roles", "-e", "ANSIBLE_FILTER_PLUGINS=/filter_plugins", "-e", "PATH=/w/bin:/usr/local/bin:/usr/bin:/bin",
                            "-e", "ANSIBLE_LOCAL_TEMP=/tmp/.al", "-e", "ANSIBLE_NOCOLOR=1", self.image, "sh", "-c", cmd],
                           capture_output=True, text=True, timeout=300)
        return r.returncode, r.stdout + r.stderr

    def close(self):
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
    try:
        rc, out = box.run(check=True)
        _check(rc == 0, "play: check mode on a fresh box passes (rc=%d)%s" % (rc, "" if rc == 0 else "\n" + out[-2500:]))
        _check(not os.path.exists(box.path("cab/emu/ra/retroarch")), "play: check mode wrote nothing")
        rc, out = box.run()
        _check(rc == 0, "play: apply on a fresh box passes (rc=%d)%s" % (rc, "" if rc == 0 else "\n" + out[-2500:]))
        _check(_sha(box.path("cab/emu/core/mupen64plus_next_libretro.so")) == box.artifacts[2]["sha256"],
               "play: core installed with the pinned sha256")
        cfg = open(box.path("cab/home/.config/retroarch/retroarch.cfg")).read()
        _check('input_driver = "udev"' in cfg and 'input_driver = "x"' not in cfg, "play: input_driver x -> udev")
        rmp = box.path("cab/home/.config/retroarch/config/remaps/Mupen64Plus-Next/Mupen64Plus-Next.rmp")
        _check(os.path.exists(rmp) and _sha(rmp) == _sha(os.path.join(ROLE, "files", "Mupen64Plus-Next.rmp")),
               "play: N64 remap installed")
        opt = open(box.path("cab/home/.config/retroarch/config/Mupen64Plus-Next/Mupen64Plus-Next.opt")).read()
        _check('mupen64plus-cpucore = "dynamic_recompiler"' in opt, "play: cpucore=dynamic_recompiler set")
        sd = "cab/home/.config/retroarch/shaders/shaders_glsl/"
        cd = "cab/home/.config/retroarch/config/"
        cfg = open(box.path("cab/home/.config/retroarch/retroarch.cfg")).read()
        _check('auto_shaders_enable = "true"' in cfg and 'video_shader_enable = "true"' in cfg,
               "play: auto_shaders_enable + video_shader_enable set to true")
        for f in box.shader_files:
            _check(_sha(box.path(sd + f["path"])) == f["sha256"], "play: shader %s installed with its pinned sha256" % f["path"])
        _check(os.path.exists(box.path(sd + "stock.glslp")), "play: stock.glslp pass-through installed")

        def resolves(preset):
            """follow `#reference "rel"` from the preset's folder; return the path under the box (None if it leaves it)."""
            ref = re.search(r'#reference "([^"]+)"', open(box.path(preset)).read()).group(1)
            target = os.path.normpath(os.path.join(os.path.dirname(box.path(preset)), ref))
            return target if target.startswith(box.path(sd)) and os.path.exists(target) else None
        _check(resolves(cd + "global.glslp") == box.path(sd + "crt/crt-pi.glslp"),
               "play: config/global.glslp -> shaders_glsl/crt/crt-pi.glslp (inside the shaders dir, file exists)")
        for core in ("Mupen64Plus-Next", "PCSX-ReARMed"):
            _check(resolves(cd + "%s/%s.glslp" % (core, core)) == box.path(sd + "stock.glslp"),
                   "play: config/%s/%s.glslp -> stock.glslp" % (core, core))
        _check(not os.path.exists(box.path(cd + "Nestopia/Nestopia.glslp")), "play: a core off the list gets no override")
        rc, out = box.run()
        _check(rc == 0 and _changed(out) == 0, "play: second apply is idempotent (changed=%d)" % _changed(out))
        rc, out = box.run(check=True)
        _check(rc == 0 and _changed(out) == 0, "play: check mode on a deployed box is clean (changed=%d)" % _changed(out))

        # broken: a shader whose sha256 pin differs from the file -> the play fails
        bad = [dict(f) for f in box.shader_files]
        bad[1]["sha256"] = "0" * 64
        os.remove(box.path(sd + "crt/shaders/crt-pi.glsl"))
        rc, out = box.run(shader_files=bad)
        _check(rc != 0 and "checksum" in out.lower(), "broken play: a shader sha256 mismatch fails")
        _check(not os.path.exists(box.path(sd + "crt/shaders/crt-pi.glsl")),
               "broken play: the mismatched shader was not installed")
        rc, out = box.run()
        _check(rc == 0, "play: the right pin installs the shader again (rc=%d)" % rc)

        # broken: local build does not match its pin -> fail before any copy
        box.write("build/core.so", b"a different build\n")
        os.remove(box.path("cab/emu/core/mupen64plus_next_libretro.so"))
        rc, out = box.run()
        _check(rc != 0 and "does not match its pin" in out, "broken play: local checksum mismatch fails")
        _check(not os.path.exists(box.path("cab/emu/core/mupen64plus_next_libretro.so")),
               "broken play: the mismatched build was not copied")

        # broken: no local build and the cabinet file is wrong -> the cabinet assert fails
        os.remove(box.path("build/core.so"))
        box.write("cab/emu/core/mupen64plus_next_libretro.so", b"tampered\n")
        rc, out = box.run()
        _check(rc != 0 and "pinned " + box.artifacts[2]["sha256"] in out,
               "broken play: a wrong cabinet artifact with no local build fails")

        # broken: a game is running -> the guard stops the play before any write
        box.write("cab/emu/core/mupen64plus_next_libretro.so", ("fake core.so build\n").encode())
        box.write("cab/home/.config/retroarch/retroarch.cfg", 'input_driver = "x"\n')
        rc, out = box.run(pre="cp /bin/sleep /tmp/retroarch && (/tmp/retroarch 60 &) && ")
        _check(rc != 0 and "A game is running" in out, "broken play: a running retroarch blocks the play")
        _check('input_driver = "x"' in open(box.path("cab/home/.config/retroarch/retroarch.cfg")).read(),
               "broken play: nothing written while the game runs")
    finally:
        box.close()


def test_play_crt_levers():
    """#126: hk_crt_enabled false = switches off, no shader files; hk_crt_off_cores picks the pass-through cores."""
    image = _image() if shutil.which("docker") else None
    if not image or subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        skip("CRT lever play tests: docker not running or ansible image not built")
        return
    cd, sd = "cab/home/.config/retroarch/config/", "cab/home/.config/retroarch/shaders/shaders_glsl/"
    box = Box(image)
    try:
        rc, out = box.run(hk_crt_enabled=False)
        cfg = open(box.path("cab/home/.config/retroarch/retroarch.cfg")).read() if rc == 0 else ""
        _check(rc == 0 and 'auto_shaders_enable = "false"' in cfg and 'video_shader_enable = "false"' in cfg,
               "play: hk_crt_enabled false sets both RetroArch shader switches to false (rc=%d)" % rc)
        _check(not os.path.exists(box.path(sd)) and not os.path.exists(box.path(cd + "global.glslp")),
               "play: hk_crt_enabled false installs no shader files and no presets")
    finally:
        box.close()
    box = Box(image)
    try:
        rc, out = box.run(hk_crt_off_cores=["Nestopia"])
        _check(rc == 0 and os.path.exists(box.path(cd + "Nestopia/Nestopia.glslp"))
               and not os.path.exists(box.path(cd + "Mupen64Plus-Next/Mupen64Plus-Next.glslp")),
               "play: hk_crt_off_cores [Nestopia] gives only Nestopia the pass-through (rc=%d)" % rc)
    finally:
        box.close()


def test_play_cheevos():
    """#45: achievements off = only cheevos_enable false; on = hardcore + user + token (token never in the output);
    per-core exceptions go to the core override; Mupen64Plus-Next (whole-file override) is refused."""
    image = _image() if shutil.which("docker") else None
    if not image or subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        skip("cheevos play tests: docker not running or ansible image not built")
        return
    cfgp, cd = "cab/home/.config/retroarch/retroarch.cfg", "cab/home/.config/retroarch/config/"
    box = Box(image)
    try:
        rc, out = box.run()
        cfg = open(box.path(cfgp)).read() if rc == 0 else ""
        _check(rc == 0 and 'cheevos_enable = "false"' in cfg and "cheevos_username" not in cfg and "cheevos_token" not in cfg,
               "play: achievements off writes only cheevos_enable false (rc=%d)" % rc)
        rc, out = box.run(hk_cheevos_enable=True, hk_cheevos_user="tester", cheevos_token="FAKETOKEN45notreal",
                          hk_cheevos_hardcore_by_core={"PCSX-ReARMed": False})
        cfg = open(box.path(cfgp)).read() if rc == 0 else ""
        _check(rc == 0 and 'cheevos_enable = "true"' in cfg and 'cheevos_hardcore_mode_enable = "true"' in cfg
               and 'cheevos_username = "tester"' in cfg and 'cheevos_token = "FAKETOKEN45notreal"' in cfg,
               "play: achievements on writes enable, hardcore, user and token (rc=%d)" % rc)
        _check("FAKETOKEN45notreal" not in out, "play: the token never appears in the Ansible output (no_log)")
        ov = box.path(cd + "PCSX-ReARMed/PCSX-ReARMed.cfg")
        _check(os.path.exists(ov) and 'cheevos_hardcore_mode_enable = "false"' in open(ov).read(),
               "play: a per-core exception lands in that core's override (PCSX-ReARMed softcore)")
        rc, out = box.run(hk_cheevos_enable=True, hk_cheevos_user="tester", cheevos_token="FAKETOKEN45notreal")
        _check(rc == 0 and _changed(out) == 0, "play: a second run with the same token is changed=0 (got %d)" % _changed(out))
        rc, out = box.run(hk_cheevos_enable=True, hk_cheevos_hardcore_by_core={"Mupen64Plus-Next": False})
        _check(rc != 0 and "copied whole" in out, "BROKEN case: an exception for the whole-file N64 override is refused")
    finally:
        box.close()


if __name__ == "__main__":
    main(globals())
