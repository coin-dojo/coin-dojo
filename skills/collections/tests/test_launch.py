#!/usr/bin/env python3
"""Tests for skills/collections/launch.py (stdlib only; pytest or run directly).

    python3 skills/collections/tests/test_launch.py

Broken cases: unknown system dir exits 4, missing ROM / ROM outside the ROM root exits 3, bad usage exits 2,
Debian's /usr/bin/retroarch (1.20) for any system and a relative core path fail (#62; exit 5 / --check).

#62: for EVERY system the argv must equal what the cabinet's hot-patched launch.py built
(tests/fixtures/launch_live_20261114.py, sha pinned below) and launch_expected.json (the deploy's golden).
LAUNCH_LIVE=FILE compares against a freshly fetched cabinet copy as well.
"""
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "lib"))
from hktest import main  # noqa: E402  (shared runner: one PASS per test, counted RESULT line, #151)

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "launch.py")
CHECK = os.path.join(os.path.dirname(HERE), "launch_check.py")
EXPECTED = os.path.join(os.path.dirname(HERE), "launch_expected.json")
LIVE = os.path.join(HERE, "fixtures", "launch_live_20261114.py")
LIVE_SHA = "fc5fb338b0f7dfc3c836028353fea6f1b370b25044cdea8abe80e9054ce637ec"
LIVE_SHA_NEUTRAL = "c28ba922c2a0fdca8857c351f01133c2d33bb81932c4ec8f11d3a2a24a39dc58"   # project name blanked
RA122 = "/data/emulators/retroarch-1.22/retroarch"
# ls /data/roms on the cabinet, 2026-11-14 (README.txt left out)
CABINET_ROM_DIRS = {"arcade", "atari2600", "cps1", "cps2", "cps3", "dreamcast", "fbneo", "genesis", "n64",
                    "naomi", "neogeo", "nes", "pcengine", "psx", "saturn", "segacd", "snes"}
sys.path.insert(0, os.path.dirname(HERE))
import launch  # noqa: E402


def tree(files):
    """temp ROM root with empty files {relpath: None} and a nested dir of symlinks {linkrel: targetrel}."""
    d = tempfile.mkdtemp()
    roms = os.path.join(d, "roms")
    for rel in files:
        p = os.path.join(roms, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").close()
    return d, roms


def link(d, roms, nested_rel, target_rel):
    p = os.path.join(d, "nested", nested_rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    os.symlink(os.path.join(roms, target_rel), p)
    return p


def cli(*args, env=None):
    e = dict(os.environ)
    e.update(env or {})
    r = subprocess.run([sys.executable, TOOL] + list(args), capture_output=True, text=True, env=e)
    return r.returncode, r.stdout, r.stderr


def test_symlink_in_folder_resolves_to_real_system():
    d, roms = tree(["cps2/ssf2.zip"])
    p = link(d, roms, "genres/fighters/ssf2.zip", "cps2/ssf2.zip")
    rc, out, _ = cli("--dry-run", "--roms-root", roms, p)
    real = os.path.realpath(os.path.join(roms, "cps2/ssf2.zip"))
    assert rc == 0 and out.strip() == (
        "DRYRUN system=cps2 cmd=/data/emulators/retroarch-1.22/retroarch -L /usr/lib/aarch64-linux-gnu/libretro/fbneo_libretro.so " + real)


def test_path_with_spaces_is_one_argument():
    d, roms = tree(["dreamcast/Cannon Spike.chd"])
    p = link(d, roms, "systems/dreamcast/Cannon Spike.chd", "dreamcast/Cannon Spike.chd")
    cmd = launch.build_command(*launch.real_system(p, roms))
    assert cmd[0] == "/data/emulators/flycast/flycast-run.sh" and cmd[1].endswith("/dreamcast/Cannon Spike.chd")
    rc, out, _ = cli("--dry-run", "--roms-root", roms, p)
    assert rc == 0 and "'" in out and "Cannon Spike.chd'" in out  # shell-quoted as one argument


def test_every_mapped_system_builds_a_command():
    for s in launch.CORE_BY_SYSTEM:
        cmd = launch.build_command(s, "/data/roms/%s/x.zip" % s)
        assert cmd[-1] == "/data/roms/%s/x.zip" % s
    assert launch.build_command("naomi", "/r")[0].endswith("flycast-run.sh")
    assert launch.build_command("cps3", "/r")[2].endswith("fbneo_libretro.so")


def test_env_relocates_emulators():
    cmd = launch.build_command("psx", "/r/x.chd", {"COIN_DOJO_RETROARCH": "/opt/ra", "COIN_DOJO_CORES": "/opt/c"})
    assert cmd == ["/opt/ra", "-L", "/opt/c/pcsx_rearmed_libretro.so", "/r/x.chd"]
    # an absolute core is used as is: COIN_DOJO_CORES does not move the GLES N64 core
    cmd = launch.build_command("n64", "/r/x.z64", {"COIN_DOJO_RETROARCH": "/opt/ra", "COIN_DOJO_CORES": "/opt/c"})
    assert cmd == ["/opt/ra", "-L", "/data/emulators/mupen64plus-gles/mupen64plus_next_libretro.so", "/r/x.z64"]


def test_unknown_system_dir_exits_4():          # BROKEN CASE
    d, roms = tree(["zxspectrum/game.zip"])
    p = link(d, roms, "systems/zx/game.zip", "zxspectrum/game.zip")
    rc, out, err = cli("--dry-run", "--roms-root", roms, p)
    assert rc == 4 and out == "" and "unknown system" in err


def test_missing_rom_exits_3():                 # BROKEN CASE
    d, roms = tree(["cps2/a.zip"])
    dangling = os.path.join(d, "nested", "x", "gone.zip")
    os.makedirs(os.path.dirname(dangling))
    os.symlink(os.path.join(roms, "cps2", "gone.zip"), dangling)
    assert cli("--dry-run", "--roms-root", roms, dangling)[0] == 3


def test_rom_outside_root_exits_3():            # BROKEN CASE
    d, roms = tree(["cps2/a.zip"])
    stray = os.path.join(d, "stray.zip")
    open(stray, "w").close()
    assert cli("--dry-run", "--roms-root", roms, stray)[0] == 3
    top = os.path.join(roms, "loose.zip")       # a file directly in the root has no system dir
    open(top, "w").close()
    assert cli("--dry-run", "--roms-root", roms, top)[0] == 3


def test_bad_usage_exits_2():                   # BROKEN CASE
    assert cli()[0] == 2
    assert cli("--dry-run")[0] == 2
    assert cli("--bogus", "x")[0] == 2
    assert cli("a", "b")[0] == 2


def test_env_roms_root_default():
    d, roms = tree(["cps1/ffight.zip"])
    p = link(d, roms, "must-play/ffight.zip", "cps1/ffight.zip")
    rc, out, _ = cli("--dry-run", p, env={"COIN_DOJO_ROMS": roms})
    assert rc == 0 and "system=cps1" in out


# Cores installed on the cabinet (/usr/lib/aarch64-linux-gnu/libretro, smoke sweep 2026-11-14).
CABINET_CORES = {"fbneo", "flycast", "gambatte", "genesis_plus_gx", "mame2003_plus", "mednafen_pce",
                 "mednafen_pce_fast", "mednafen_saturn", "mgba", "mupen64plus_next", "nestopia",
                 "pcsx_rearmed", "ppsspp", "snes9x", "yabasanshiro",
                 "stella"}               # build/stella, installed 2026-11-14
# systems with no ROMs on the cabinet (mapping kept for the community image)
NOT_ON_CABINET = {"arcade"}


def pins_from_role():
    """system -> core from the declared default of hk_alt_emulators (#47, moved to config/variables.d/launch.yml by #125),
    parsed as text (no yaml dep). Only the `default:` block of that one variable counts (not the VM overlay)."""
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "variables.d", "launch.yml")
    block = re.search(r"^  hk_alt_emulators:\n(?:.*\n)*?    default:\n((?:      .*\n)+)", open(path).read(), re.M)
    assert block, "hk_alt_emulators default block not found in config/variables.d/launch.yml"
    pins = {}
    for m in re.finditer(r"^\s+(\w+):\s*\{\s*label:[^,]+,\s*core:\s*(\w+)_libretro\.so\s*\}", block.group(1), re.M):
        pins[m.group(1)] = m.group(2)
    return pins


def retroarch122_defaults():
    """(retroarch binary, set of absolute core paths) the retroarch122 role installs (#63), parsed as text."""
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "ansible", "roles",
                        "retroarch122", "defaults", "main.yml")
    text = open(path).read()
    ra_dir = re.search(r"^retroarch122_dir:\s*(\S+)", text, re.M).group(1)
    core_dir = re.search(r"^retroarch122_core_dir:\s*(\S+)", text, re.M).group(1)
    cores = {core_dir + "/" + m for m in re.findall(r'dest:\s*"\{\{ retroarch122_core_dir \}\}/([^"]+_libretro\.so)"', text)}
    return ra_dir + "/retroarch", cores


def mismatches(table, pins, installed, abs_installed=None):
    """Bare names must be installed cores; an absolute path must be a file the retroarch122 role installs.
    The pin check compares the core name (an absolute path's basename minus _libretro.so)."""
    abs_installed = retroarch122_defaults()[1] if abs_installed is None else abs_installed
    bad = []
    for sysname, core in table.items():
        if core == "flycast" and sysname in launch.STANDALONE:
            continue
        if os.path.isabs(core):
            if core not in abs_installed:
                bad.append("%s -> %s not installed by retroarch122" % (sysname, core))
            core = os.path.basename(core)[:-len("_libretro.so")]
        elif sysname not in NOT_ON_CABINET and core not in installed:
            bad.append("%s -> %s not installed" % (sysname, core))
        if sysname in pins and pins[sysname] != core:
            bad.append("%s -> %s but ES-DE is pinned to %s" % (sysname, core, pins[sysname]))
    return bad


def test_table_matches_cabinet_pins_and_cores():
    pins = pins_from_role()
    assert pins.get("nes") == "nestopia" and pins.get("psx") == "pcsx_rearmed", pins
    assert mismatches(launch.CORE_BY_SYSTEM, pins, CABINET_CORES) == []


def test_table_mismatch_is_caught():            # BROKEN CASE (the bug the 2026-11-14 sweep found)
    bad = dict(launch.CORE_BY_SYSTEM, nes="mesen", psx="mednafen_psx")
    out = mismatches(bad, pins_from_role(), CABINET_CORES)
    assert any("nes -> mesen" in x for x in out) and any("psx -> mednafen_psx" in x for x in out), out


def test_segacd_runs_on_genesis_plus_gx():
    assert launch.CORE_BY_SYSTEM["segacd"] == "genesis_plus_gx"


def test_atari2600_runs_on_stella():
    assert launch.CORE_BY_SYSTEM["atari2600"] == "stella"
    assert launch.build_command("atari2600", "/r/x.a26", {})[:3] == [
        RA122, "-L", "/usr/lib/aarch64-linux-gnu/libretro/stella_libretro.so"]


# --- #62: the hot-patch, ported -------------------------------------------------------------------
def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def live_files():
    files = [LIVE]
    if os.environ.get("LAUNCH_LIVE"):          # e.g. a fresh `ssh cabinet cat ~/ES-DE/scripts/launch.py`
        files.append(os.environ["LAUNCH_LIVE"])
    return files


def test_live_fixture_is_the_cabinet_hotpatch():
    assert sha256(LIVE) == LIVE_SHA or project_neutral_sha256(LIVE) == LIVE_SHA_NEUTRAL, (
        "the fixture must stay the cabinet's 2026-11-14 file")


def project_neutral_sha256(path):
    """sha256 of the file with the project name blanked: the community export renames it (#146), nothing else."""
    text = open(path, encoding="utf-8").read()
    return hashlib.sha256(re.sub("(?i)coin-dojo|night[-_ ]dojo", "PROJECT", text).encode("utf-8")).hexdigest()


def test_every_system_argv_equals_live_hotpatch():
    expected = json.load(open(EXPECTED))["systems"]
    assert set(launch.CORE_BY_SYSTEM) == set(expected) == CABINET_ROM_DIRS, (
        sorted(set(launch.CORE_BY_SYSTEM) ^ CABINET_ROM_DIRS))
    for i, path in enumerate(live_files()):
        live = load_module(path, "launch_live_%d" % i)
        assert set(live.CORE_BY_SYSTEM) == set(launch.CORE_BY_SYSTEM), path
        for s in sorted(CABINET_ROM_DIRS):
            rom = "/data/roms/%s/Avenue Fighter (USA) [!].zip" % s
            new, old = launch.build_command(s, rom, {}), live.build_command(s, rom, {})
            assert new == old, "%s: new %s != live %s (%s)" % (s, new, old, path)
            assert new == [rom if a == "{rom}" else a for a in expected[s]], (s, new, expected[s])


def test_launch_check_passes_for_new_and_live():
    for path in [TOOL] + live_files():
        r = subprocess.run([sys.executable, "-I", CHECK, "--launcher", path, "--expected", EXPECTED],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        assert r.stdout.count("RESULT: PASS launch-argv ") == len(CABINET_ROM_DIRS), r.stdout
        assert "RESULT: PASS launch-check" in r.stdout


def test_launch_check_emit_matches_expected():
    r = subprocess.run([sys.executable, "-I", CHECK, "--launcher", TOOL, "--emit"] + sorted(CABINET_ROM_DIRS),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["systems"] == json.load(open(EXPECTED))["systems"]


def test_every_retroarch_system_runs_122_and_dreamcast_naomi_flycast():
    for s in sorted(CABINET_ROM_DIRS):
        cmd = launch.build_command(s, "/r/x", {})
        if s in ("dreamcast", "naomi"):
            assert cmd == ["/data/emulators/flycast/flycast-run.sh", "/r/x"], cmd
        else:
            assert cmd[0] == RA122 and cmd[1] == "-L" and os.path.isabs(cmd[2]), cmd
    assert launch.build_command("n64", "/r/x", {})[2] == "/data/emulators/mupen64plus-gles/mupen64plus_next_libretro.so"


def test_tables_match_retroarch122_role():
    ra, cores = retroarch122_defaults()
    assert launch.RETROARCH == ra == RA122
    assert launch.CORE_BY_SYSTEM["n64"] in cores
    assert launch.table_problems() == []


def test_check_flag_ok():
    rc, out, err = cli("--check")
    assert rc == 0 and "tables OK" in out, out + err


def test_retroarch_by_system_emits_cfg():
    saved = dict(launch.RETROARCH_BY_SYSTEM)
    try:
        launch.RETROARCH_BY_SYSTEM["psx"] = ("/opt/ra-1.23/retroarch", "/opt/ra-1.23/retroarch.cfg")
        assert launch.build_command("psx", "/r/x.chd", {"COIN_DOJO_RETROARCH": "/ignored"}) == [
            "/opt/ra-1.23/retroarch", "-c", "/opt/ra-1.23/retroarch.cfg", "-L",
            "/usr/lib/aarch64-linux-gnu/libretro/pcsx_rearmed_libretro.so", "/r/x.chd"]
        assert launch.table_problems() == []
    finally:
        launch.RETROARCH_BY_SYSTEM.clear()
        launch.RETROARCH_BY_SYSTEM.update(saved)


def broken_copy(old, new):
    """A temp copy of launch.py with one line replaced (the line must exist)."""
    src = open(TOOL).read()
    assert old in src, old
    d = tempfile.mkdtemp()
    p = os.path.join(d, "launch.py")
    open(p, "w").write(src.replace(old, new, 1))
    return p


def run(*cmd):
    r = subprocess.run([sys.executable, "-I"] + list(cmd), capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def test_debian_retroarch_fails_for_any_system():                  # BROKEN CASE
    assert launch.table_problems(retroarch="/usr/bin/retroarch")
    assert launch.table_problems(retroarch="/usr/bin/../bin/retroarch")
    for s in sorted(set(launch.CORE_BY_SYSTEM) - launch.STANDALONE):
        bad = launch.table_problems(by_system={s: ("/usr/bin/retroarch", "/home/cab/.config/retroarch/retroarch.cfg")})
        assert any("1.20" in b for b in bad), (s, bad)
    p = broken_copy('RETROARCH = "/data/emulators/retroarch-1.22/retroarch"', 'RETROARCH = "/usr/bin/retroarch"')
    rc, out = run(p, "--check")
    assert rc == 5 and "Debian's RetroArch 1.20" in out, out
    rc, out = run(CHECK, "--launcher", p, "--expected", EXPECTED)
    assert rc == 1 and out.count("RESULT: FAIL launch-argv") == len(CABINET_ROM_DIRS) - 2, out


def test_relative_n64_core_fails():                                # BROKEN CASE
    rel = "mupen64plus-gles/mupen64plus_next_libretro.so"
    bad = launch.table_problems(cores=dict(launch.CORE_BY_SYSTEM, n64=rel))
    assert any("n64" in b and "relative" in b for b in bad), bad
    assert launch.table_problems(cores=dict(launch.CORE_BY_SYSTEM, n64="mupen64plus_next_libretro.so"))
    try:
        launch.core_path("n64", rel, {})
        raise AssertionError("relative core accepted")
    except launch.LaunchError as e:
        assert e.code == 5
    p = broken_copy('"n64": "/data/emulators/mupen64plus-gles/mupen64plus_next_libretro.so"', '"n64": "%s"' % rel)
    assert run(p, "--check")[0] == 5
    d, roms = tree(["n64/Mario Kart 64.z64"])
    rc, out = run(p, "--dry-run", "--roms-root", roms, os.path.join(roms, "n64/Mario Kart 64.z64"))
    assert rc == 5 and "relative path" in out, out
    rc, out = run(CHECK, "--launcher", p, "--expected", EXPECTED)
    assert rc == 1 and "FAIL launch-argv n64" in out and "PASS launch-argv nes" in out, out


def test_absolute_core_not_installed_is_caught():                  # BROKEN CASE
    bad = mismatches(dict(launch.CORE_BY_SYSTEM, n64="/data/emulators/elsewhere/mupen64plus_next_libretro.so"),
                     pins_from_role(), CABINET_CORES)
    assert any("n64" in b and "retroarch122" in b for b in bad), bad


def test_launch_check_broken_expected_and_usage():                 # BROKEN CASE
    d = tempfile.mkdtemp()
    bad = os.path.join(d, "x.json")
    open(bad, "w").write('{"systems": {}}')
    assert run(CHECK, "--launcher", TOOL, "--expected", bad)[0] == 2
    assert run(CHECK, "--launcher", TOOL)[0] == 2
    assert run(CHECK, "--expected", EXPECTED)[0] == 2
    wrong = os.path.join(d, "w.json")
    exp = json.load(open(EXPECTED))
    exp["systems"]["nes"][0] = "/usr/bin/retroarch"
    json.dump(exp, open(wrong, "w"))
    rc, out = run(CHECK, "--launcher", TOOL, "--expected", wrong)
    assert rc == 1 and "FAIL launch-argv nes" in out, out


def test_pointer_file_is_followed_to_the_rom():                         # crossover games (#116)
    d, roms = tree(["cps2/mvsc.zip"])
    ptr = os.path.join(d, "nested", ".crossover", "series", "marvel", "mvsc.zip")
    os.makedirs(os.path.dirname(ptr))
    open(ptr, "w").write(launch.POINTER_MAGIC + os.path.join(roms, "cps2/mvsc.zip") + "\n")
    link_path = os.path.join(d, "nested", "series", "marvel", "mvsc.zip")
    os.makedirs(os.path.dirname(link_path))
    os.symlink(os.path.relpath(ptr, os.path.dirname(link_path)), link_path)
    rc, out, _ = cli("--dry-run", "--roms-root", roms, link_path)
    real = os.path.realpath(os.path.join(roms, "cps2/mvsc.zip"))
    assert rc == 0 and out.startswith("DRYRUN system=cps2 ") and out.strip().endswith(real), out


def test_pointer_magic_matches_nested_py():
    sys.path.insert(0, os.path.dirname(HERE))
    import nested
    assert launch.POINTER_MAGIC == nested.POINTER_MAGIC


def test_pointer_to_a_missing_rom_exits_3():                            # BROKEN CASE
    d, roms = tree(["cps2/x.zip"])
    ptr = os.path.join(d, "ptr.zip")
    open(ptr, "w").write(launch.POINTER_MAGIC + os.path.join(roms, "cps2/gone.zip") + "\n")
    rc, _o, err = cli("--dry-run", "--roms-root", roms, ptr)
    assert rc == 3 and "does not exist" in err


def test_pointer_with_a_relative_target_exits_3():                      # BROKEN CASE
    d, roms = tree(["cps2/x.zip"])
    ptr = os.path.join(d, "ptr.zip")
    open(ptr, "w").write(launch.POINTER_MAGIC + "cps2/x.zip\n")
    rc, _o, err = cli("--dry-run", "--roms-root", roms, ptr)
    assert rc == 3 and "relative" in err


def test_small_non_pointer_file_is_left_alone():
    d, roms = tree([])
    p = os.path.join(roms, "cps2", "tiny.zip")
    os.makedirs(os.path.dirname(p))
    open(p, "w").write("PK just a tiny rom\n")
    rc, out, _ = cli("--dry-run", "--roms-root", roms, p)
    assert rc == 0 and out.strip().endswith(os.path.realpath(p))


if __name__ == "__main__":
    main(globals(), per_test=True)
