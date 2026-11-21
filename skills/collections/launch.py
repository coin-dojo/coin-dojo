#!/usr/bin/env python3
"""Dispatcher launcher for the nested (virtual) ES-DE systems (stdlib only).

    launch.py [--dry-run] [--roms-root DIR] ROM
    launch.py --check                 # exit 5 if the tables below are broken (deploy gate, #62)

ES-DE's nested main screen (MUST PLAY / GENRES / SERIES / DECADES / SYSTEMS) uses virtual systems whose
folders hold symlinks to the real ROMs. Every virtual system's <command> is this script (es_systems.xml
has <loadExclusive/>, so every game on the cabinet starts here). It resolves the symlink (realpath; a crossover's pointer file is followed to the ROM, #116),
takes the first directory under the real ROM root as the real system (cps2, dreamcast, ...) and runs
that system's emulator command. ES-DE starts a new python3 per launch: a new launch.py takes effect at
the next game, no ES-DE restart.

Exit codes: 0 launched (or --dry-run printed the command / --check found nothing), 2 usage error,
            3 ROM missing / not under the ROM root, 4 unknown system directory,
            5 broken launcher table (relative core path, Debian RetroArch 1.20, ...).
Environment: COIN_DOJO_ROMS (ROM root, default /data/roms), COIN_DOJO_RETROARCH,
COIN_DOJO_CORES, COIN_DOJO_FLYCAST to relocate the emulators (the preview harness does).
COIN_DOJO_RETROARCH replaces RETROARCH only (not RETROARCH_BY_SYSTEM); COIN_DOJO_CORES applies to bare
core names only (an absolute core path is used as is).
"""
import os
import shlex
import sys

# RetroArch 1.22.2 + the coin-dojo EGL patches, built by build/retroarch (#61), installed by the
# retroarch122 role (#63), for EVERY RetroArch system (#62 scope change, 2026-11-14 20:35).
RETROARCH = "/data/emulators/retroarch-1.22/retroarch"
# Debian's RetroArch 1.20: with the Pi's libwayland 1.24 it deadlocks in flush_wayland_fd (#49,
# RA #18148), on every system once the #57 overlays load. table_problems() rejects it everywhere.
RETROARCH_DEBIAN = "/usr/bin/retroarch"
CORES = "/usr/lib/aarch64-linux-gnu/libretro"
FLYCAST = "/data/emulators/flycast/flycast-run.sh"

# real system dir -> libretro core: the core ES-DE actually runs for that system on the cabinet,
# i.e. the gamelist <alternativeEmulator> pin (cabinet_tweaks cabinet_alt_emulators, #47) where one
# exists, else ES-DE 3.5's default. A bare name is CORES/<name>_libretro.so; an absolute path is used
# as is (it must end in _libretro.so). Must only name cores the cabinet has installed; the test
# cross-checks the pins and the retroarch122 role's installed files. "flycast" = the standalone wrapper.
CORE_BY_SYSTEM = {
    "cps1": "fbneo", "cps2": "fbneo", "cps3": "fbneo", "fbneo": "fbneo", "neogeo": "fbneo",
    "arcade": "mame",
    "genesis": "genesis_plus_gx", "snes": "snes9x", "nes": "nestopia",
    # GLES3 build (build/n64, #61; retroarch122 role): Debian's core wants desktop GL, the Pi 5 is GLES-only.
    "n64": "/data/emulators/mupen64plus-gles/mupen64plus_next_libretro.so",
    "pcengine": "mednafen_pce", "psx": "pcsx_rearmed",
    "saturn": "mednafen_saturn", "segacd": "genesis_plus_gx",
    "atari2600": "stella",                  # built from source: build/stella (pinned 7.0)
    "dreamcast": "flycast", "naomi": "flycast",
}
STANDALONE = {"dreamcast", "naomi"}  # cabinet uses standalone flycast (libretro GL is blocked)

# system -> (RetroArch binary, its config): a different RetroArch for one system, run as
# [binary, -c, cfg, -L, core, rom]. Empty since every system runs RETROARCH with the main
# ~/.config/retroarch/retroarch.cfg (#62); kept so one system can move without touching the others.
RETROARCH_BY_SYSTEM = {}


class LaunchError(Exception):
    def __init__(self, msg, code):
        super().__init__(msg)
        self.code = code


def table_problems(retroarch=None, by_system=None, cores=None, standalone=None):
    """Everything wrong with the launcher tables (default: this module's). [] = OK."""
    retroarch = RETROARCH if retroarch is None else retroarch
    by_system = RETROARCH_BY_SYSTEM if by_system is None else by_system
    cores = CORE_BY_SYSTEM if cores is None else cores
    standalone = STANDALONE if standalone is None else standalone
    bad = []
    binaries = [("RETROARCH", retroarch)] + [("RETROARCH_BY_SYSTEM[%s]" % s, v[0]) for s, v in sorted(by_system.items())]
    for what, b in binaries:
        if not os.path.isabs(b):
            bad.append("%s = %r is not an absolute path" % (what, b))
        elif os.path.normpath(b) == RETROARCH_DEBIAN:
            bad.append("%s = %s is Debian's RetroArch 1.20 (Wayland deadlock, #49)" % (what, b))
    for s, (_b, cfg) in sorted(by_system.items()):
        if not os.path.isabs(cfg):
            bad.append("RETROARCH_BY_SYSTEM[%s] config %r is not an absolute path" % (s, cfg))
        if s not in cores or s in standalone:
            bad.append("RETROARCH_BY_SYSTEM[%s]: not a RetroArch system in CORE_BY_SYSTEM" % s)
    for s, name in sorted(cores.items()):
        if s in standalone:
            continue
        if os.path.isabs(name):
            if not name.endswith("_libretro.so"):
                bad.append("core for %s: %s does not end in _libretro.so" % (s, name))
        elif "/" in name or name.endswith(".so"):
            bad.append("core for %s: %r is a relative path (use a bare core name or an absolute path)" % (s, name))
    for s in sorted(standalone):
        if s not in cores:
            bad.append("STANDALONE system %s has no CORE_BY_SYSTEM entry" % s)
    return bad


# A crossover game (listed in two folders of one virtual system, #116) is linked through a pointer file under
# roms_nested/.crossover/ so ES-DE sees a distinct path per folder (nested.py POINTER_MAGIC / POINTER_DIR; this file
# is a single script on the cabinet, so the value is repeated here and tests/test_launch.py keeps them equal).
POINTER_MAGIC = "COIN-DOJO-LINK "
POINTER_MAX = 4096                       # a pointer file is one short line; a real ROM is never this small AND starts so


def follow_pointer(path):
    """A pointer file -> the realpath of the ROM it names; any other path is returned unchanged."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > POINTER_MAX:
            return path
        with open(path, encoding="utf-8", errors="replace") as f:
            first = f.readline()
    except OSError:
        return path
    if not first.startswith(POINTER_MAGIC):
        return path
    target = first[len(POINTER_MAGIC):].strip()
    if not target.startswith("/"):
        raise LaunchError("pointer file %s names a relative path %r" % (path, target), 3)
    return os.path.realpath(target)


def real_system(rom, roms_root):
    """Return (system, realpath) for a ROM path; the system is the first dir under roms_root."""
    real = follow_pointer(os.path.realpath(rom))
    if not os.path.exists(real):
        raise LaunchError("ROM does not exist: %s" % rom, 3)
    root = os.path.realpath(roms_root)
    rel = os.path.relpath(real, root)
    if rel.startswith("..") or os.path.isabs(rel) or os.sep not in rel:
        raise LaunchError("ROM %s resolves to %s, not inside a system dir of %s" % (rom, real, root), 3)
    return rel.split(os.sep)[0], real


def core_path(system, name, env=os.environ):
    """A bare core name -> CORES/<name>_libretro.so; an absolute path as is; a relative path is an error."""
    if os.path.isabs(name):
        return name
    if "/" in name or name.endswith(".so"):
        raise LaunchError("core for %s is a relative path %r (bare core name or absolute path)" % (system, name), 5)
    return "%s/%s_libretro.so" % (env.get("COIN_DOJO_CORES", CORES), name)


def build_command(system, real, env=os.environ):
    if system not in CORE_BY_SYSTEM:
        raise LaunchError("unknown system dir %r (no emulator mapping)" % system, 4)
    if system in STANDALONE:
        return [env.get("COIN_DOJO_FLYCAST", FLYCAST), real]
    core = core_path(system, CORE_BY_SYSTEM[system], env)
    if system in RETROARCH_BY_SYSTEM:
        binary, cfg = RETROARCH_BY_SYSTEM[system]
        return [binary, "-c", cfg, "-L", core, real]
    return [env.get("COIN_DOJO_RETROARCH", RETROARCH), "-L", core, real]


USAGE = "usage: launch.py [--dry-run] [--roms-root DIR] ROM | launch.py --check"


def main(argv, env=os.environ):
    args = list(argv)
    if args == ["--check"]:
        bad = table_problems()
        for b in bad:
            print("launch.py: BROKEN TABLE: %s" % b, file=sys.stderr)
        if not bad:
            print("launch.py: tables OK (%d systems, RetroArch %s)" % (len(CORE_BY_SYSTEM), RETROARCH))
        return 5 if bad else 0
    dry = False
    root = env.get("COIN_DOJO_ROMS", "/data/roms")
    while args and args[0].startswith("--"):
        a = args.pop(0)
        if a == "--dry-run":
            dry = True
        elif a == "--roms-root" and args:
            root = args.pop(0)
        else:
            print(USAGE, file=sys.stderr)
            return 2
    if len(args) != 1:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        system, real = real_system(args[0], root)
        cmd = build_command(system, real, env)
    except LaunchError as e:
        print("launch.py: %s" % e, file=sys.stderr)
        return e.code
    line = " ".join(shlex.quote(c) for c in cmd)
    if dry:
        print("DRYRUN system=%s cmd=%s" % (system, line))
        return 0
    os.execv(cmd[0], cmd)  # replaces this process; ES-DE waits on the emulator
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
