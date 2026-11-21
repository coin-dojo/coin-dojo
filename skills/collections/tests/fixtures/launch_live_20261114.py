#!/usr/bin/env python3
"""Dispatcher launcher for the nested (virtual) ES-DE systems (stdlib only).

    launch.py [--dry-run] [--roms-root DIR] ROM

ES-DE's nested main screen (GENRES / DECADES / SYSTEMS) uses virtual systems whose
folders hold symlinks to the real ROMs. Every virtual system's <command> is this
script. It resolves the symlink (realpath), takes the first directory under the real
ROM root as the real system (cps2, dreamcast, ...) and runs that system's emulator
command, the same one ES-DE would run for the real system on the cabinet.

Exit codes: 0 launched (or --dry-run printed the command), 2 usage error,
            3 ROM missing / not under the ROM root, 4 unknown system directory.
Environment: COIN_DOJO_ROMS (ROM root, default /data/roms), COIN_DOJO_RETROARCH,
COIN_DOJO_CORES, COIN_DOJO_FLYCAST to relocate the emulators (the preview harness does).
"""
import os
import shlex
import sys

RETROARCH = "/data/emulators/retroarch-1.22/retroarch"  # 1.22.2 + coin-dojo EGL patches (#61); 1.20 deadlocks (RA #18148); was /usr/bin/retroarch
CORES = "/usr/lib/aarch64-linux-gnu/libretro"
FLYCAST = "/data/emulators/flycast/flycast-run.sh"

# real system dir -> libretro core: the core ES-DE actually runs for that system on the cabinet,
# i.e. the gamelist <alternativeEmulator> pin (cabinet_tweaks cabinet_alt_emulators, #47) where one
# exists, else ES-DE 3.5's default. Must only name cores the cabinet has installed; the test
# cross-checks the pins. "flycast" = the standalone flycast wrapper.
CORE_BY_SYSTEM = {
    "cps1": "fbneo", "cps2": "fbneo", "cps3": "fbneo", "fbneo": "fbneo", "neogeo": "fbneo",
    "arcade": "mame",
    "genesis": "genesis_plus_gx", "snes": "snes9x", "nes": "nestopia",
    "n64": "/data/emulators/mupen64plus-gles/mupen64plus_next_libretro.so", "pcengine": "mednafen_pce", "psx": "pcsx_rearmed",
    "saturn": "mednafen_saturn", "segacd": "genesis_plus_gx",
    "atari2600": "stella",                  # built from source: build/stella (pinned 7.0)
    "dreamcast": "flycast", "naomi": "flycast",
}
RETROARCH_BY_SYSTEM = {}
STANDALONE = {"dreamcast", "naomi"}  # cabinet uses standalone flycast (libretro GL is blocked)


class LaunchError(Exception):
    def __init__(self, msg, code):
        super().__init__(msg)
        self.code = code


def real_system(rom, roms_root):
    """Return (system, realpath) for a ROM path; the system is the first dir under roms_root."""
    real = os.path.realpath(rom)
    if not os.path.exists(real):
        raise LaunchError("ROM does not exist: %s" % rom, 3)
    root = os.path.realpath(roms_root)
    rel = os.path.relpath(real, root)
    if rel.startswith("..") or os.path.isabs(rel) or os.sep not in rel:
        raise LaunchError("ROM %s resolves to %s, not inside a system dir of %s" % (rom, real, root), 3)
    return rel.split(os.sep)[0], real


def build_command(system, real, env=os.environ):
    if system not in CORE_BY_SYSTEM:
        raise LaunchError("unknown system dir %r (no emulator mapping)" % system, 4)
    if system in STANDALONE:
        return [env.get("COIN_DOJO_FLYCAST", FLYCAST), real]
    name = CORE_BY_SYSTEM[system]
    core = name if name.startswith("/") else "%s/%s_libretro.so" % (env.get("COIN_DOJO_CORES", CORES), name)
    if system in RETROARCH_BY_SYSTEM:  # newer RetroArch for GPU cores (Wayland deadlock in 1.20, #49)
        binary, cfg = RETROARCH_BY_SYSTEM[system]
        return [binary, "-c", cfg, "-L", core, real]
    return [env.get("COIN_DOJO_RETROARCH", RETROARCH), "-L", core, real]


def main(argv, env=os.environ):
    args = list(argv)
    dry = False
    root = env.get("COIN_DOJO_ROMS", "/data/roms")
    while args and args[0].startswith("--"):
        a = args.pop(0)
        if a == "--dry-run":
            dry = True
        elif a == "--roms-root" and args:
            root = args.pop(0)
        else:
            print("usage: launch.py [--dry-run] [--roms-root DIR] ROM", file=sys.stderr)
            return 2
    if len(args) != 1:
        print("usage: launch.py [--dry-run] [--roms-root DIR] ROM", file=sys.stderr)
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
