#!/usr/bin/env python3
"""Role cores (#244): the cores and ROM folders come from launch.py's own table, and every other reader agrees.

    python3 ansible/roles/cores/tests/test_cores.py

  1. files/launcher_table.py reproduces launch.py's CORE_BY_SYSTEM (systems, bare cores, absolute cores, binaries).
  2. A launch.py with broken tables (a relative core path) makes it exit 2: nothing is installed from a broken table.
  3. The role types no core or system list (only cores_built, whose names must be launcher cores).
  4. The other roles install what the table names outside the core dir: retroarch122 the GLES N64 core and RetroArch,
     dreamcast flycast-run.sh; the pinned ES-DE emulators (hk_alt_emulators) and Fightcade's ROM folders are launcher
     cores and systems.
Broken cases: a core missing from the table, a system Fightcade reads that the launcher does not know.
"""
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.normpath(os.path.join(ROLE, "..", "..", ".."))
TABLE = os.path.join(ROLE, "files", "launcher_table.py")
LAUNCH = os.path.join(REPO, "skills", "collections", "launch.py")
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
from hktest import check, main, skip  # noqa: E402


def _read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as fh:
        return fh.read()


def _launch():
    spec = importlib.util.spec_from_file_location("launch_under_test", LAUNCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _table(*args):
    r = subprocess.run([sys.executable, "-I", TABLE] + list(args), capture_output=True, text=True)
    return r


def test_table_is_launch_py():
    r = _table()
    check(r.returncode == 0, "launcher_table.py reads launch.py (%s)" % r.stderr.strip())
    t = json.loads(r.stdout)
    L = _launch()
    check(t["systems"] == sorted(L.CORE_BY_SYSTEM), "systems = CORE_BY_SYSTEM keys")
    rc = {s: n for s, n in L.CORE_BY_SYSTEM.items() if s not in L.STANDALONE}
    check(t["cores"] == sorted({n for n in rc.values() if not n.startswith("/")}), "bare cores = the RetroArch systems' cores")
    check(t["abs_cores"] == sorted({n for n in rc.values() if n.startswith("/")}), "absolute cores as launch.py names them")
    check(t["core_dir"] == L.CORES and L.RETROARCH in t["binaries"] and L.FLYCAST in t["binaries"],
          "core dir and the binaries it starts")
    paths = _table("--format", "paths", "--rom-root", "/x/roms").stdout.split()
    check(len(paths) == len(t["cores"]) + len(t["abs_cores"]) + len(t["binaries"]) + len(t["systems"])
          and "/x/roms/cps1" in paths and L.CORES + "/fbneo_libretro.so" in paths, "paths: every core, binary and ROM folder")


def test_broken_table_refused():
    src = _read("skills", "collections", "launch.py").replace('"psx": "pcsx_rearmed"', '"psx": "cores/pcsx_rearmed.so"')
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "launch.py")
        open(p, "w").write(src)
        r = _table("--launch", p)
    check(r.returncode == 2 and "broken" in r.stderr, "BROKEN: a relative core path in launch.py stops the table (rc 2)")


def test_role_types_no_list():
    defaults = _read("ansible", "roles", "cores", "defaults", "main.yml")
    tasks = _read("ansible", "roles", "cores", "tasks", "main.yml")
    t = json.loads(_table().stdout)
    built = re.findall(r"(?m)^  ([a-z0-9_]+): \"\{\{ hk_repo_root \}\}/build/", defaults)
    check(built and set(built) <= set(t["cores"]), "cores_built names launcher cores only: %s" % built)
    # #274: every bare core is built from a pinned commit, each from its own build/<core>/out
    paths = dict(re.findall(r'(?m)^  ([a-z0-9_]+): "\{\{ hk_repo_root \}\}/(build/[^"]+)"', defaults))
    check(set(built) == set(t["cores"]), "every launcher core is built here (#274): %s" % sorted(set(t["cores"]) - set(built)))
    check(all(p == "build/%s/out/%s_libretro.so" % (c, c) for c, p in paths.items()),
          "each built core comes from build/<core>/out/<core>_libretro.so: %s" % paths)
    others = [c for c in t["cores"] + t["systems"] if c not in built
              and re.search(r"(?<![\w/.-])%s(?![\w-])" % re.escape(c), defaults + tasks)]
    check(not others, "no core or system typed in the role (it reads launch.py): %s" % others)
    check("launcher_table.py" in tasks and "cores_launch_src" in tasks, "the role reads the table from launch.py")


def temp_problems(tasks):
    """The zips go to a fresh ansible.builtin.tempfile dir, removed in an always: block (no fixed /tmp path)."""
    p = []
    if re.search(r"/tmp/hk-cores\b|cores_download_dir", tasks):
        p.append("a fixed download dir (/tmp/hk-cores) instead of ansible.builtin.tempfile")
    if "ansible.builtin.tempfile:" not in tasks:
        p.append("no ansible.builtin.tempfile")
    m = re.search(r"(?ms)^  always:\n(.*)", tasks)
    if not m or not re.search(r'path: "\{\{ cores_tmp\.path \}\}"\n\s+state: absent', m.group(1)):
        p.append("the temp dir is not removed in an always: block")
    return p


def test_core_zips_in_a_tempdir():
    t = _read("ansible", "roles", "cores", "tasks", "main.yml")
    check(temp_problems(t) == [], "cores: zips in a tempfile dir, removed even on failure %s" % temp_problems(t))
    check(temp_problems(t.replace("  always:\n", "  rescue:\n")), "BROKEN: cleanup outside always: is caught")
    check(temp_problems(t.replace("{{ cores_tmp.path | default('/tmp') }}", "/tmp/hk-cores")), "BROKEN: a fixed /tmp path is caught")


def test_other_readers_agree():
    t = json.loads(_table().stdout)
    ra = _read("ansible", "roles", "retroarch122", "defaults", "main.yml")
    dests = {d.replace("{{ retroarch122_dir }}", "/data/emulators/retroarch-1.22")
              .replace("{{ retroarch122_core_dir }}", "/data/emulators/mupen64plus-gles")
             for d in re.findall(r'dest: "([^"]+)"', ra)}
    check(set(t["abs_cores"]) <= dests, "retroarch122 installs the absolute cores %s" % t["abs_cores"])
    check("/data/emulators/retroarch-1.22/retroarch" in dests, "retroarch122 installs RetroArch 1.22")
    dc = _read("ansible", "roles", "dreamcast", "defaults", "main.yml")
    m = re.search(r'(?m)^dreamcast_dir: "?([^"\s#]+)', dc)
    check(m is not None and m.group(1) + "/flycast-run.sh" in t["binaries"], "dreamcast installs launch.py's flycast-run.sh")
    launch_cfg = _read("config", "variables.d", "launch.yml")
    alt = re.findall(r"core: ([a-z0-9_]+)_libretro\.so", launch_cfg)
    check(alt and set(alt) <= set(t["cores"]), "hk_alt_emulators pins only launcher cores: %s" % alt)
    fc = _read("ansible", "roles", "fightcade", "defaults", "main.yml")
    m = re.search(r"(?m)^fightcade_rom_dirs: \[([^\]]+)\]", fc)
    dirs = [x.strip() for x in m.group(1).split(",")] if m else []
    # #298: roms/fightcade (Fightcade's own versions) comes first and is NOT a launcher system; the rest are
    own = [d for d in dirs if d == "fightcade"]
    check(own == ["fightcade"] and "fightcade" not in t["systems"], "#298: roms/fightcade is Fightcade's own, not a launcher system")
    rest = [d for d in dirs if d != "fightcade"]
    check(rest and set(rest) <= set(t["systems"]), "Fightcade's FBNeo falls back to launcher ROM folders: %s" % rest)


def test_broken_disagreement_caught():
    t = json.loads(_table().stdout)
    old_image = "arcade neogeo cps1 cps2 cps3 naomi dreamcast psx psp snes genesis nes gb gba n64".split()  # pre-#244 build.sh
    check(set(old_image) != set(t["systems"]), "BROKEN: the old image folder list differs from the launcher's")
    old_cores = "fbneo flycast pcsx_rearmed ppsspp mame2003_plus snes9x genesis_plus_gx mgba nestopia gambatte".split()
    check(not set(t["cores"]) <= set(old_cores), "BROKEN: the old image core list misses launcher cores")



# ---- #274 play tests: a built core comes from build/<core>/out; without it the buildbot (WARN) unless strict ------
PLAYBOOK = """- hosts: localhost
  connection: local
  gather_facts: false
  roles: [cores]
"""
# The ansible image has no unzip (the cabinet gets it from apt): a stand-in.
FAKE_UNZIP = r'''#!/usr/bin/env python3
import sys, zipfile
args = [a for a in sys.argv[1:] if a not in ("-q", "-o")]
arc, member, dest = args[0], args[1], args[args.index("-d") + 1]
zipfile.ZipFile(arc).extract(member, dest)
'''


def _image():
    try:
        out = subprocess.run([os.path.join(REPO, "ansible", "bin", "ans"), "--image"], capture_output=True, text=True)
        img = out.stdout.strip()
        if img and subprocess.run(["docker", "image", "inspect", img], capture_output=True).returncode == 0:
            return img
    except OSError:
        pass
    return None


class Box:
    """A throwaway container: the core dir holds every launcher core except fbneo and nestopia (the two under test);
    build/fbneo/out has a built core, build/nestopia/out only when built=True; the buildbot is a local file:// dir."""

    def __init__(self, image, nestopia_built=False, bad_sha=False):
        self.image = image
        base = os.path.join(os.path.expanduser("~"), ".cache", "coin-dojo", "ansible")  # colima mounts $HOME
        os.makedirs(base, exist_ok=True)
        self.dir = tempfile.mkdtemp(prefix="t-cores-", dir=base)
        for core in ["fbneo"] + (["nestopia"] if nestopia_built else []):
            out = os.path.join(self.dir, "build", core, "out")
            os.makedirs(out)
            data = ("built %s\n" % core).encode()
            with open(os.path.join(out, "%s_libretro.so" % core), "wb") as fh:
                fh.write(data)
            digest = hashlib.sha256(b"other" if bad_sha else data).hexdigest()
            with open(os.path.join(out, "%s_libretro.so.sha256" % core), "w") as fh:
                fh.write("%s  %s_libretro.so\n" % (digest, core))
        os.makedirs(os.path.join(self.dir, "bb"))
        for core in ("fbneo", "nestopia"):
            with zipfile.ZipFile(os.path.join(self.dir, "bb", "%s_libretro.so.zip" % core), "w") as z:
                z.writestr("%s_libretro.so" % core, "buildbot %s\n" % core)
        os.makedirs(os.path.join(self.dir, "bin"))
        with open(os.path.join(self.dir, "bin", "unzip"), "w") as fh:
            fh.write(FAKE_UNZIP)
        os.chmod(os.path.join(self.dir, "bin", "unzip"), 0o755)
        with open(os.path.join(self.dir, "site.yml"), "w") as fh:
            fh.write(PLAYBOOK)

    def run(self, strict):
        t = json.loads(_table().stdout)
        others = [c for c in t["cores"] if c not in ("fbneo", "nestopia")]
        evars = {"cores_user": "root", "cores_group": "root", "cores_rom_root": "/w/roms", "hk_repo_root": "/w",
                 "cores_launch_src": "/launch/launch.py", "cores_buildbot": "file:///w/bb",
                 "cores_built": {c: "/w/build/%s/out/%s_libretro.so" % (c, c) for c in ("fbneo", "nestopia")},
                 "ansible_become": False, "ansible_python_interpreter": "/usr/local/bin/python3"}
        with open(os.path.join(self.dir, "vars.json"), "w") as fh:
            json.dump(evars, fh)
        cd = t["core_dir"]
        cmd = ("mkdir -p %s && for c in %s; do : > %s/${c}_libretro.so; done && "
               "ansible-playbook -i localhost, -e @/w/vars.json /w/site.yml; rc=$?; "
               "for c in fbneo nestopia; do printf '%%s=' $c; cat %s/${c}_libretro.so 2>/dev/null || echo none; done; "
               "exit $rc") % (cd, " ".join(others), cd, cd)
        r = subprocess.run(["docker", "run", "--rm", "-v", "%s:/w" % self.dir, "-v", "%s:/roles/cores:ro" % ROLE,
                            "-v", "%s:/launch:ro" % os.path.dirname(LAUNCH),
                            "-e", "ANSIBLE_ROLES_PATH=/roles", "-e", "PATH=/w/bin:/usr/local/bin:/usr/bin:/bin",
                            "-e", "ANSIBLE_LOCAL_TEMP=/tmp/.al", "-e", "ANSIBLE_NOCOLOR=1",
                            "-e", "HK_LICENCES_STRICT=%s" % ("1" if strict else "0"), self.image, "sh", "-c", cmd],
                           capture_output=True, text=True, timeout=300)
        return r.returncode, r.stdout + r.stderr

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


WARN = "WARN nestopia: buildbot core: not source-pinned, fine for personal use, not for a release"


def test_play_built_or_buildbot_fallback():
    image = _image() if shutil.which("docker") else None
    if not image or subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        skip("play tests: docker not running or ansible image not built; make -C ansible ansible-image")
        return
    cases = []
    box = Box(image)
    try:
        rc, out = box.run(strict=False)
        cases.append((rc == 0 and "fbneo=built fbneo" in out and "nestopia=buildbot nestopia" in out and WARN in out,
                      "play: not strict: fbneo from its build, nestopia (not built) from the buildbot with a WARN", out))
        cases.append(("WARN fbneo" not in out, "play: no WARN for a core that is built", out))
        rc, out = box.run(strict=True)
        cases.append((rc != 0 and "HK_LICENCES_STRICT=1 (a release) needs every core built" in out
                      and "nestopia=none" in out,
                      "BROKEN: strict with nestopia not built stops before installing it", out))
    finally:
        box.close()
    box = Box(image, nestopia_built=True)
    try:
        rc, out = box.run(strict=True)
        cases.append((rc == 0 and "fbneo=built fbneo" in out and "nestopia=built nestopia" in out and "WARN" not in out,
                      "play: strict with every core built installs the built ones, no WARN", out))
    finally:
        box.close()
    box = Box(image, bad_sha=True)
    try:
        rc, out = box.run(strict=False)
        cases.append((rc != 0 and "does not match its .sha256" in out and "fbneo=none" in out,
                      "BROKEN: a built core that does not match its .sha256 is refused (no fallback)", out))
    finally:
        box.close()
    for ok, msg, out in cases:
        check(ok, msg + ("" if ok else "\n" + out[-2000:]))

if __name__ == "__main__":
    main(globals())
