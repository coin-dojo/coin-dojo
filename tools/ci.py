#!/usr/bin/env python3
"""ci.py - `make ci`: lint + security + the tests a change needs (ticket #123, T10b of #103).

    tools/ci.py [--base REF] [--full] [--plan]

Steps (each a make target, run from the repo root):
  lint      when the selection has the `lint` area (or --full): `make lint`
  security  always: `make secrets` (gitleaks, #124)
  tests     --full: `make test`; else `make test-changed BASE=REF`
  --full only: `make edition-check EDITION=community`, `make config-check`, `python3 tools/changelog.py lint`
A step whose make target does not exist yet (lint/security/config-check come from other tickets) is a counted SKIP,
never a PASS. A selector error or crash runs --full: a broken selector can never skip a test.
--plan prints the steps and runs nothing. The last line is the only verdict:
`RESULT: PASS make ci[ (N skipped)]` or `RESULT: FAIL make ci: step, step`.
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import ci_select  # noqa: E402

MAKE = os.environ.get("MAKE", "make")


def target_exists(target, makefile=None, cwd=ROOT):
    """True when `make -n target` finds a rule (probe only: -n never runs the recipe)."""
    cmd = [MAKE, "-n"] + (["-f", makefile] if makefile else []) + [target]
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return "No rule to make target" not in r.stderr and r.returncode in (0, 1) and "No rule" not in r.stdout


def plan(base, full, selected_areas=None):
    """Return ([(name, argv, optional)], reason): the steps for a selection."""
    steps = []
    if full or selected_areas is None or "ALL" in selected_areas or "lint" in selected_areas:
        steps.append(("lint", [MAKE, "lint"], True))
    steps.append(("security", [MAKE, "secrets"], True))   # gitleaks (#124)
    if full or selected_areas is None:
        steps.append(("test", [MAKE, "test"], False))
    else:
        steps.append(("test-changed", [MAKE, "test-changed", "BASE=" + base], False))
    if full or selected_areas is None:
        steps.append(("edition-check", [MAKE, "edition-check", "EDITION=community"], False))
        steps.append(("config-check", [MAKE, "config-check"], True))
        steps.append(("changelog", [sys.executable, "tools/changelog.py", "lint"], False))
    return steps


def choose(base, full):
    """(areas or None for FULL, why)."""
    if full:
        return None, "FULL requested"
    try:
        areas = ci_select.load_areas()
        rules = ci_select.load_rules(areas)
        selected, _ = ci_select.select(ci_select.changed_paths(base), areas, rules)
        return selected, "changed paths vs " + base
    except Exception as e:  # noqa: BLE001 - any selector failure runs everything
        return None, "selector error, running FULL: %s" % e


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--plan", action="store_true")
    args = ap.parse_args(argv)
    areas, why = choose(args.base, args.full)
    steps = plan(args.base, args.full, areas)
    print("ci: %s; areas: %s" % (why, "FULL" if areas is None else " ".join(areas)))
    for name, argv_, optional in steps:
        print("ci: step %s: %s%s" % (name, " ".join(argv_), " (skipped when the target is missing)" if optional else ""))
    if args.plan:
        return 0
    fails, skipped = [], 0
    for name, argv_, optional in steps:
        if optional and argv_[0] == MAKE and not target_exists(argv_[1]):
            print("SKIP ci step %s: no make target %s yet" % (name, argv_[1]))
            skipped += 1
            continue
        print("== ci %s" % name, flush=True)
        if subprocess.run(argv_, cwd=ROOT).returncode != 0:
            fails.append(name)
            print("FAIL ci step %s" % name, flush=True)
    if fails:
        print("RESULT: FAIL make ci: " + ", ".join(fails))
        return 1
    print("RESULT: PASS make ci" + (" (%d skipped)" % skipped if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
