#!/usr/bin/env python3
"""What the launcher needs installed, read from launch.py itself (#244): the one list of systems and cores.

    launcher_table.py [--launch PATH] [--format json|cores|abs-cores|systems]

ES-DE starts every game through skills/collections/launch.py, whose CORE_BY_SYSTEM names the core of each real system
(the first folder under the ROM root). The cores role installs exactly these cores, the image build seeds exactly these
ROM folders, and the image's verify.sh checks both against this output. Nothing else types the lists.

  json       {"systems": [...], "cores": [bare names], "abs_cores": [absolute .so paths], "standalone": [...],
              "core_dir": CORES}
  cores      one bare core name per line (installed as <core_dir>/<name>_libretro.so)
  abs-cores  one absolute core path per line (installed by another role, e.g. retroarch122's GLES N64 core)
  systems    one ROM folder name per line
  paths      with --rom-root DIR: every path the launcher needs, one per line (core files, binaries, ROM folders);
             the image's verify.sh checks each one
Exit 2 when launch.py cannot be read or its tables are broken (launch.table_problems()).
"""
import argparse
import importlib.util
import json
import os
import sys

DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "skills", "collections",
                       "launch.py")


def load(path):
    spec = importlib.util.spec_from_file_location("hk_launch", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def table(mod):
    problems = mod.table_problems()
    if problems:
        raise ValueError("launch.py tables are broken: %s" % "; ".join(problems))
    standalone = set(mod.STANDALONE)
    cores = mod.CORE_BY_SYSTEM
    rc = {s: n for s, n in cores.items() if s not in standalone}
    return {
        "systems": sorted(cores),
        "cores": sorted({n for n in rc.values() if not os.path.isabs(n)}),
        "abs_cores": sorted({n for n in rc.values() if os.path.isabs(n)}),
        "standalone": sorted(standalone),
        "core_dir": mod.CORES,
        # the programs it starts (RetroArch 1.22 and the flycast wrapper; other roles install them)
        "binaries": sorted({mod.RETROARCH, mod.FLYCAST} | {b for b, _c in mod.RETROARCH_BY_SYSTEM.values()}),
    }


def expected_paths(t, rom_root):
    """Every path an image (or a cabinet) must have for the launcher: core files, binaries, ROM folders."""
    return ([os.path.join(t["core_dir"], "%s_libretro.so" % c) for c in t["cores"]] + t["abs_cores"] + t["binaries"]
            + [os.path.join(rom_root, s) for s in t["systems"]])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--launch", default=os.path.normpath(DEFAULT))
    ap.add_argument("--format", choices=("json", "cores", "abs-cores", "systems", "paths"), default="json")
    ap.add_argument("--rom-root", default="/data/roms")
    a = ap.parse_args(argv)
    try:
        t = table(load(a.launch))
    except (OSError, SyntaxError, AttributeError, ValueError) as exc:
        print("launcher_table: %s: %s" % (a.launch, exc), file=sys.stderr)
        return 2
    if a.format == "json":
        print(json.dumps(t, sort_keys=True))
    elif a.format == "paths":
        print("\n".join(expected_paths(t, a.rom_root)))
    else:
        print("\n".join(t[{"cores": "cores", "abs-cores": "abs_cores", "systems": "systems"}[a.format]]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
