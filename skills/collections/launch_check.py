#!/usr/bin/env python3
"""Check (or record) the exact emulator argv a launch.py builds for every system (#62, stdlib only).

    launch_check.py --launcher FILE --expected launch_expected.json [--python python3]
    launch_check.py --launcher FILE --emit SYSTEM [SYSTEM ...]      # print the JSON for those systems

For each system in the expected file it makes a throwaway ROM tree (<tmp>/roms/<system>/hk launch
check.zip, an empty file; no emulator is ever run), runs `python3 FILE --dry-run --roms-root <tmp>/roms
<rom>` with every COIN_DOJO_* variable removed, splits the printed command (shlex) and compares it with
the expected argv, "{rom}" standing for the ROM's real path. --dry-run is in every launch.py version
(also the 2026-11-14 hot-patch), so the same check runs against the old and the new file.

Used by skills/collections/tests/test_launch.py and the `launcher` Ansible role (pre-install gate on the
staged file, post-check on the installed one). Prints one RESULT line per system and a summary.
Exit 0 all equal, 1 a mismatch or a launcher error, 2 usage / unreadable expected file.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

ROM_NAME = "hk launch check.zip"   # a space on purpose: the command must keep it one argument
ROM_TOKEN = "{rom}"


def run_launcher(python, launcher, roms, system):
    rom = os.path.join(roms, system, ROM_NAME)
    os.makedirs(os.path.dirname(rom), exist_ok=True)
    open(rom, "w").close()
    env = {k: v for k, v in os.environ.items() if not k.startswith("COIN_DOJO_")}
    r = subprocess.run([python, launcher, "--dry-run", "--roms-root", roms, rom],
                       capture_output=True, text=True, env=env, timeout=30)
    prefix = "DRYRUN system=%s cmd=" % system
    out = r.stdout.strip()
    if r.returncode != 0 or not out.startswith(prefix):
        return None, os.path.realpath(rom), "exit %d: %s%s" % (r.returncode, out, r.stderr.strip())
    return shlex.split(out[len(prefix):]), os.path.realpath(rom), ""


def main(argv):
    args = list(argv)
    python, launcher, expected, emit = sys.executable, None, None, None
    while args:
        a = args.pop(0)
        if a == "--launcher" and args:
            launcher = args.pop(0)
        elif a == "--expected" and args:
            expected = args.pop(0)
        elif a == "--python" and args:
            python = args.pop(0)
        elif a == "--emit":
            emit, args = args, []
        else:
            launcher = None
            break
    if not launcher or (expected is None) == (emit is None) or emit == []:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        print("usage: launch_check.py --launcher FILE (--expected JSON | --emit SYSTEM...) [--python PY]", file=sys.stderr)
        return 2
    if expected is not None:
        try:
            want = json.load(open(expected))["systems"]
            assert isinstance(want, dict) and want and all(isinstance(v, list) for v in want.values())
        except (OSError, ValueError, KeyError, AssertionError) as e:
            print("RESULT: FAIL launch-check: cannot read %s (%s)" % (expected, e))
            return 2
        systems = sorted(want)
    else:
        systems = emit
    tmp = tempfile.mkdtemp(prefix="hk-launch-check-")
    try:
        roms = os.path.join(tmp, "roms")
        got = {}
        fails = 0
        for s in systems:
            cmd, real, err = run_launcher(python, launcher, roms, s)
            if cmd is None:
                print("RESULT: FAIL launch-argv %s: %s" % (s, err))
                fails += 1
                continue
            got[s] = [ROM_TOKEN if c == real else c for c in cmd]
            if expected is None:
                continue
            if got[s] == want[s]:
                print("RESULT: PASS launch-argv %s %s" % (s, " ".join(shlex.quote(c) for c in got[s])))
            else:
                print("RESULT: FAIL launch-argv %s: got %s, expected %s" % (s, got[s], want[s]))
                fails += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if expected is None:
        print(json.dumps({"systems": got}, indent=1, sort_keys=True))
        return 1 if fails else 0
    print("RESULT: %s launch-check %s: %d systems, %d mismatch(es)" % ("FAIL" if fails else "PASS", launcher, len(systems), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
