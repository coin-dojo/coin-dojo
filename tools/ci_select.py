#!/usr/bin/env python3
"""ci_select.py - pick the CI areas and test files a change needs (ticket #123, T10b of #103).

    tools/ci_select.py [--base REF | --files PATH...] [--areas A,B | --all] [--failopen]
                    [--format areas|tests|lane|explain]
    tools/ci_select.py --check

Reads ci/rules.tsv (path glob -> areas, first match wins, last rule `* ALL`) and ci/areas.tsv (area -> lane + test
globs). Semantics ported from the maintainer's other repo's CI path-selection scripts:
  - baseline area `security` is always selected; an unmatched path hits `* ALL`;
  - a selector error (bad rules file, unknown base ref) with --failopen selects ALL and still exits 0, so a
    broken selector can never skip a test (make test-changed / make ci always pass --failopen). Without
    --failopen an error prints `FAIL selector: ...` and exits 2.
Changed paths: --files, else `git diff --name-only --no-renames BASE...HEAD` + uncommitted + untracked.
Formats: areas (space separated, default), tests (one test file per line, from the selected areas; `always` is added
when any area other than docs/security/lint runs), lane (heavy if any selected area is heavy, else light),
explain (per path the rule line, glob, areas and reason, then the areas, lane and test files).
--check validates both files against the tracked tree (the rules the selector test also asserts) and prints
`FAIL ...` lines, exit 1 on any.
Env: HK_CI_RULES / HK_CI_AREAS (file paths, for tests), HK_CI_ROOT (repo root, for tests). Pure stdlib.
"""
import argparse
import fnmatch
import os
import re
import subprocess
import sys

ROOT = os.environ.get("HK_CI_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = ["security"]
ACTION_ONLY = {"docs", "security", "lint"}   # selections made only of these run no `always` tests
TEST_RX = re.compile(r"(^|/)test_[^/]*\.py$")


class SelectError(Exception):
    pass


def rules_path():
    return os.environ.get("HK_CI_RULES") or os.path.join(ROOT, "ci", "rules.tsv")


def areas_path():
    return os.environ.get("HK_CI_AREAS") or os.path.join(ROOT, "ci", "areas.tsv")


def _lines(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise SelectError("cannot read %s: %s" % (path, e))
    for n, raw in enumerate(text.splitlines(), 1):
        if raw.strip() and not raw.lstrip().startswith("#"):
            yield n, raw


def load_areas(path=None):
    """Return {area: {"lane": str, "globs": [str]}} (globs empty for `-`)."""
    path = path or areas_path()
    areas = {}
    for n, raw in _lines(path):
        cols = raw.split("\t")
        if len(cols) != 3 or not all(c.strip() for c in cols):
            raise SelectError("%s:%d: want <area><TAB><lane><TAB><globs or ->, got %r" % (path, n, raw))
        name, lane, globs = (c.strip() for c in cols)
        if lane not in ("light", "heavy"):
            raise SelectError("%s:%d: lane %r is not light|heavy" % (path, n, lane))
        if name in areas or name == "ALL":
            raise SelectError("%s:%d: duplicate or reserved area %r" % (path, n, name))
        areas[name] = {"lane": lane, "globs": [] if globs == "-" else globs.split()}
    if not areas:
        raise SelectError("%s: no areas" % path)
    return areas


def load_rules(areas, path=None):
    """Return [(lineno, glob, [areas], reason)]; validates names and the required reason."""
    path = path or rules_path()
    rules = []
    for n, raw in _lines(path):
        cols = raw.split("\t")
        if len(cols) == 2:
            raise SelectError("%s:%d: rule %r has no reason (third column: # why)" % (path, n, cols[0]))
        if len(cols) != 3:
            raise SelectError("%s:%d: want <glob><TAB><areas><TAB># reason, got %r" % (path, n, raw))
        glob, names, why = (c.strip() for c in cols)
        if not glob or not names:
            raise SelectError("%s:%d: empty glob or areas" % (path, n))
        if not why.startswith("#") or not why[1:].strip():
            raise SelectError("%s:%d: rule %r has no reason (third column: # why)" % (path, n, glob))
        sel = [] if names == "-" else names.split(",")
        for a in sel:
            if a != "ALL" and a not in areas:
                raise SelectError("%s:%d: rule %r names unknown area %r (not in ci/areas.tsv)" % (path, n, glob, a))
        rules.append((n, glob, sel, why[1:].strip()))
    if not rules:
        raise SelectError("%s: no rules" % path)
    return rules


def match_rule(rules, path):
    for rule in rules:
        if fnmatch.fnmatchcase(path, rule[1]):
            return rule
    return None


def tracked(root=None):
    r = subprocess.run(["git", "-C", root or ROOT, "ls-files", "-z"], capture_output=True)
    if r.returncode != 0:
        raise SelectError("git ls-files failed: %s" % r.stderr.decode(errors="replace").strip())
    return [p for p in r.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def changed_paths(base, root=None):
    root = root or ROOT

    def git(*args):
        r = subprocess.run(["git", "-C", root] + list(args), capture_output=True)
        if r.returncode != 0:
            raise SelectError("git %s failed: %s" % (" ".join(args), r.stderr.decode(errors="replace").strip()))
        return [p for p in r.stdout.decode("utf-8", "surrogateescape").split("\0") if p]

    out = set(git("diff", "--name-only", "-z", "--no-renames", base + "...HEAD"))
    out |= set(git("diff", "--name-only", "-z", "--no-renames", "HEAD"))
    out |= set(git("ls-files", "-z", "--others", "--exclude-standard"))
    return sorted(out)


def select(paths, areas, rules):
    """Return (selected areas in areas.tsv order, [(path, rule)])."""
    chosen, hits = set(BASELINE), []
    for p in paths:
        rule = match_rule(rules, p)
        if rule is None:   # cannot happen with a `* ALL` last rule; fail open anyway
            rule = (0, "*", ["ALL"], "unmatched (no rule)")
        hits.append((p, rule))
        for a in rule[2]:
            if a == "ALL":
                chosen |= set(areas)
            else:
                chosen.add(a)
    return [a for a in areas if a in chosen], hits


def expand_globs(globs, files):
    return sorted({f for f in files if any(fnmatch.fnmatchcase(f, g) for g in globs)})


def test_files(selected, areas, files):
    names = list(selected)
    if any(a not in ACTION_ONLY and a != "always" for a in selected) and "always" in areas:
        names.append("always")
    globs = [g for a in names if a in areas for g in areas[a]["globs"]]
    return expand_globs(globs, [f for f in files if TEST_RX.search(f)])


def lane_of(selected, areas):
    return "heavy" if any(areas[a]["lane"] == "heavy" for a in selected if a in areas) else "light"


def check(files=None, areas=None, rules=None):
    """Return a list of FAIL strings: the rule/area invariants against the tracked tree."""
    fails = []
    areas = areas if areas is not None else load_areas()
    rules = rules if rules is not None else load_rules(areas)
    files = files if files is not None else tracked()
    if rules[-1][1] != "*" or rules[-1][2] != ["ALL"]:
        fails.append("last rule must be `* ALL`, is %r %s" % (rules[-1][1], ",".join(rules[-1][2]) or "-"))
    body = rules[:-1]
    for f in files:
        if match_rule(body, f) is None:
            fails.append("tracked path %s matches no rule before `* ALL` (add a rule to ci/rules.tsv)" % f)
    for name, a in areas.items():
        for g in a["globs"]:
            if not any(fnmatch.fnmatchcase(f, g) for f in files):
                fails.append("area %s glob %s matches no tracked file (stale glob)" % (name, g))
    reach = {f for a in areas.values() for f in expand_globs(a["globs"], files)}
    for f in files:
        if TEST_RX.search(f) and f not in reach:
            fails.append("test file %s is reached by no area (add its folder to ci/areas.tsv)" % f)
    used = {x for r in rules for x in r[2]}
    for name in areas:
        if name != "always" and name not in used and name not in BASELINE:
            fails.append("area %s is named by no rule (it can never be selected)" % name)
    return fails


def explain(paths, selected, hits, tests, lane):
    out = []
    for p, (n, glob, names, why) in hits:
        out.append("%s -> %s  # rule %s %s: %s" % (p, ",".join(names) or "-", n, glob, why))
    out.append("areas: " + " ".join(selected))
    out.append("lane: " + lane)
    out.append("tests (%d):" % len(tests))
    out.extend("  " + t for t in tests)
    return "\n".join(out)


def run(args):
    areas = load_areas()
    rules = load_rules(areas)
    files = tracked()
    if args.areas:
        names = args.areas.split(",")
        for a in names:
            if a not in areas:
                raise SelectError("unknown area %r (known: %s)" % (a, " ".join(areas)))
        selected, hits, paths = [a for a in areas if a in set(names) | set(BASELINE)], [], []
    elif args.all:
        selected, hits, paths = list(areas), [], []
    else:
        paths = args.files if args.files is not None else changed_paths(args.base)
        selected, hits = select(paths, areas, rules)
    tests = test_files(selected, areas, files)
    return paths, selected, hits, tests, lane_of(selected, areas), areas, files


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--files", nargs="*", default=None)
    ap.add_argument("--areas", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--failopen", action="store_true")
    ap.add_argument("--format", choices=("areas", "tests", "lane", "explain"), default="areas")
    ap.add_argument("--explain", action="store_true", help="same as --format explain")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    fmt = "explain" if args.explain else args.format
    if args.check:
        try:
            fails = check()
        except SelectError as e:
            fails = [str(e)]
        for f in fails:
            print("FAIL " + f)
        print("RESULT: %s select --check (%d problem(s))" % ("FAIL" if fails else "PASS", len(fails)))
        return 1 if fails else 0
    try:
        paths, selected, hits, tests, lane, areas, files = run(args)
    except (SelectError, OSError) as e:
        if not args.failopen or args.areas:   # an explicit --areas typo is the caller's error, not a selector crash
            print("FAIL selector: %s" % e, file=sys.stderr)
            return 2
        print("WARN selector error, selecting ALL: %s" % e, file=sys.stderr)
        # Fail open without trusting the config files: every tracked test file, heavy lane.
        try:
            all_tests = sorted(f for f in tracked() if TEST_RX.search(f))
        except SelectError:
            all_tests = []
        paths, hits, lane = [], [], "heavy"
        selected, tests = ["ALL"], all_tests
    if fmt == "areas":
        print(" ".join(selected))
    elif fmt == "tests":
        print("\n".join(tests))
    elif fmt == "lane":
        print(lane)
    else:
        print(explain(paths, selected, hits, tests, lane))
    return 0


if __name__ == "__main__":
    sys.exit(main())
