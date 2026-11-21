#!/usr/bin/env python3
"""Tests for ci/rules.tsv, ci/areas.tsv, tools/ci_select.py, tools/ci.py and the make targets (#123, T10b of #103).

Run: python3 tools/tests/test_select.py   (stdlib + git + make; throwaway files and repos, no network)
Broken cases: an unmatched path selects ALL; a rule naming a missing area, a rule without a reason, a bad lane and a
last rule that is not `* ALL` FAIL; a tracked path with no rule before `* ALL`, a test file no area reaches and a
stale area glob FAIL (planted into the tracked list); a bad rules file or an unknown base ref selects ALL with
--failopen and exits 2 without it; `make ci` plans FULL when the selector crashes; a missing make target is a counted SKIP.
(tools/select.py would shadow the stdlib `select` module for every script in tools/: the file is tools/ci_select.py.)
"""
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests", "lib"))
from hktest import check, main  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
SELECT = ROOT / "tools" / "ci_select.py"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


S = load("ci_select")
GOOD_AREAS = "a1\tlight\tx/test_*.py\nalways\tlight\tt/test_*.py\nsecurity\tlight\t-\n"
GOOD_RULES = "x/*\ta1\t# x tree\n*\tALL\t# unmatched\n"


def tmp_files(d, rules, areas):
    r, a = pathlib.Path(d) / "rules.tsv", pathlib.Path(d) / "areas.tsv"
    r.write_text(rules)
    a.write_text(areas)
    return str(r), str(a)


def cli(*args, env=None, cwd=ROOT):
    e = dict(os.environ, **(env or {}))
    return subprocess.run([sys.executable, str(SELECT)] + list(args), cwd=cwd, env=e, capture_output=True, text=True)


def raises(fn, needle):
    try:
        fn()
    except S.SelectError as e:
        return needle in str(e)
    return False


# A parent `make F=...` leaks F into nested makes through MAKEFLAGS: nested runs start clean.
CLEAN = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MAKELEVEL", "MFLAGS", "F", "A", "BASE")}


def test_no_stdlib_shadow():
    check(not (ROOT / "tools" / "select.py").exists(), "tools/select.py must not exist: it would shadow stdlib select")


def test_real_files_are_consistent():
    r = cli("--check")
    check(r.returncode == 0 and "RESULT: PASS" in r.stdout, "real rules/areas pass --check: " + r.stdout.strip()[-200:])
    check(S.load_rules(S.load_areas())[-1][1:3] == ("*", ["ALL"]), "the last real rule is `* ALL`")


def test_unmatched_path_selects_all():
    r = cli("--files", "newdir/x")
    names = set(r.stdout.split())
    check(r.returncode == 0 and set(S.load_areas()) <= names, "an unmatched path selects every area: " + r.stdout.strip())
    check("unmatched" in cli("--files", "newdir/x", "--explain").stdout, "explain names the catch-all reason")


def test_docs_only_selects_docs_and_security():
    r = cli("--files", "README.md", "docs/reference/x.md")
    check(r.stdout.split() == ["docs", "security"], "docs-only diff selects `docs security` only: " + r.stdout.strip())
    t = cli("--files", "README.md", "--format", "tests").stdout.split()
    check(t and all(x.startswith(("runbooks/", ".claude/team/", "docs/tests/")) for x in t), "docs-only runs only the doc tests, no always list: %s" % t)


def test_code_change_adds_always_and_lane():
    r = cli("--files", "skills/collections/collgen.py")
    check("collections" in r.stdout.split() and "lint" in r.stdout.split(), "collections change selects collections+lint: " + r.stdout.strip())
    t = cli("--files", "skills/collections/collgen.py", "--format", "tests").stdout.split()
    check("tests/test_layout.py" in t and "skills/collections/tests/test_nested.py" in t, "tests = collections + the always list")
    check(cli("--files", "skills/collections/x.py", "--format", "lane").stdout.strip() == "light", "collections is light")
    check(cli("--files", "tests/smoke/test_smoke_launch.py", "--format", "lane").stdout.strip() == "heavy", "smoke is heavy")


def test_rule_with_missing_area_fails():
    with tempfile.TemporaryDirectory() as d:
        r, a = tmp_files(d, "x/*\tnoSuchArea\t# oops\n*\tALL\t# all\n", GOOD_AREAS)
        areas = S.load_areas(a)
        check(raises(lambda: S.load_rules(areas, r), "unknown area"), "a rule naming a missing area FAILs")
        env = {"HK_CI_RULES": r, "HK_CI_AREAS": a}
        p = cli("--files", "x/y", env=env)
        check(p.returncode == 2 and "FAIL selector" in p.stderr, "CLI exits 2 on it without --failopen")
        p = cli("--files", "x/y", "--failopen", env=env)
        check(p.returncode == 0 and p.stdout.split() == ["ALL"], "with --failopen it selects ALL: " + p.stdout.strip())


def test_rule_without_reason_fails():
    with tempfile.TemporaryDirectory() as d:
        for bad in ("x/*\ta1\n*\tALL\t# all\n", "x/*\ta1\t#\n*\tALL\t# all\n", "x/*\ta1\tno hash\n*\tALL\t# all\n"):
            r, a = tmp_files(d, bad, GOOD_AREAS)
            check(raises(lambda: S.load_rules(S.load_areas(a), r), "no reason"), "rule without a reason FAILs: %r" % bad.split("\n")[0])


def test_bad_areas_file_fails():
    with tempfile.TemporaryDirectory() as d:
        for bad, why in (("a1\tfast\tx/*\n", "light|heavy"), ("a1\tlight\n", "want"), ("a1\tlight\tx\na1\tlight\ty\n", "duplicate")):
            _, a = tmp_files(d, GOOD_RULES, bad)
            check(raises(lambda: S.load_areas(a), why), "areas.tsv problem FAILs (%s)" % why)


def test_tracked_file_without_rule_fails():
    areas = S.load_areas(); rules = S.load_rules(areas); files = S.tracked()
    check(S.check(files, areas, rules) == [], "the real tree has a rule before `* ALL` for every tracked path")
    fails = S.check(files + ["newdir/x"], areas, rules)
    check(any("newdir/x" in f and "no rule" in f for f in fails), "a planted new top-level path FAILs until a rule exists")
    nolast = rules[:-1] + [(99, "*", [], "bad")]
    check(any("last rule" in f for f in S.check(files, areas, nolast)), "a last rule that is not `* ALL` FAILs")


def test_orphan_test_file_fails():
    areas = S.load_areas(); rules = S.load_rules(areas); files = S.tracked()
    fails = S.check(files + ["zzz/tests/test_orphan.py"], areas, rules)
    check(any("test_orphan.py" in f and "no area" in f for f in fails), "a planted orphan test_x.py FAILs")
    stale = dict(areas); stale["tools"] = {"lane": "light", "globs": areas["tools"]["globs"] + ["nowhere/test_*.py"]}
    check(any("nowhere/test_" in f and "stale" in f for f in S.check(files, stale, rules)), "a stale area glob FAILs")
    nov = [r for r in rules if "vm" not in r[2]]
    check(any("area vm is named by no rule" in f for f in S.check(files, areas, nov)), "an area no rule can select FAILs")


def test_every_real_test_file_reachable():
    files = S.tracked(); areas = S.load_areas()
    reach = {f for a in areas.values() for f in S.expand_globs(a["globs"], files)}
    orphans = [f for f in files if S.TEST_RX.search(f) and f not in reach]
    check(not orphans, "every tracked test file is in an area (orphans: %s)" % orphans)
    allsel = S.test_files(list(areas), areas, files)
    check(set(allsel) == {f for f in files if S.TEST_RX.search(f)}, "ALL selects every tracked test file exactly")


def test_selector_crash_runs_everything():
    with tempfile.TemporaryDirectory() as d:
        r, a = tmp_files(d, "garbage line without tabs\n", GOOD_AREAS)
        env = {"HK_CI_RULES": r, "HK_CI_AREAS": a}
        p = cli("--files", "README.md", "--format", "tests", "--failopen", env=env)
        check(p.returncode == 0 and "tools/tests/test_cilock.py" in p.stdout.split() and "WARN selector error" in p.stderr,
              "a bad rules file with --failopen selects every test file, not none")
        p = subprocess.run([sys.executable, str(ROOT / "tools" / "ci.py"), "--plan", "--base", "HEAD"], cwd=ROOT,
                           env=dict(os.environ, HK_CI_RULES=r, HK_CI_AREAS=a), capture_output=True, text=True)
        check(p.returncode == 0 and "FULL" in p.stdout and "step test: make test" in p.stdout and "selector error" in p.stdout,
              "make ci plans FULL when the selector crashes: " + p.stdout.strip()[:200])
    p = cli("--base", "no-such-ref-xyz", "--failopen")
    check(p.returncode == 0 and p.stdout.split() == ["ALL"], "an unknown base ref selects ALL with --failopen")
    check(cli("--base", "no-such-ref-xyz").returncode == 2, "an unknown base ref exits 2 without --failopen")


def test_ci_plan_and_skip():
    ci = load("ci")
    docs = ci.plan("origin/main", False, ["docs", "security"])
    check([s[0] for s in docs] == ["security", "test-changed"], "docs-only plan: security + test-changed, no lint: %s" % [s[0] for s in docs])
    full = [s[0] for s in ci.plan("origin/main", True, None)]
    check(full == ["lint", "security", "test", "edition-check", "config-check", "changelog"], "FULL plan: %s" % full)
    with tempfile.TemporaryDirectory() as d:
        mf = pathlib.Path(d) / "mf"
        mf.write_text("have:\n\t@true\n")
        check(ci.target_exists("have", str(mf), d) and not ci.target_exists("missing", str(mf), d), "target_exists finds only real targets")
        p = subprocess.run([sys.executable, str(ROOT / "tools" / "ci.py"), "--base", "HEAD"], cwd=ROOT,
                           env=dict(os.environ, MAKE="/usr/bin/false"), capture_output=True, text=True)
        check(p.returncode == 1 and p.stdout.strip().splitlines()[-1].startswith("RESULT: FAIL make ci"), "a failing step makes the LAST line RESULT: FAIL")


def test_changed_paths_in_throwaway_repo():
    with tempfile.TemporaryDirectory() as d:
        def git(*a):
            subprocess.run(["git", "-C", d] + list(a), check=True, capture_output=True,
                           env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x"))
        git("init", "-q", "-b", "main")
        (pathlib.Path(d) / "a.txt").write_text("1")
        git("add", "."); git("commit", "-qm", "base")
        git("checkout", "-qb", "feat")
        (pathlib.Path(d) / "README.md").write_text("doc")
        git("add", "."); git("commit", "-qm", "doc")
        (pathlib.Path(d) / "newdir").mkdir()
        (pathlib.Path(d) / "newdir" / "x").write_text("untracked")
        got = S.changed_paths("main", d)
        check(got == ["README.md", "newdir/x"], "committed + untracked paths vs base: %s" % got)
        check(raises(lambda: S.changed_paths("no-such-ref", d), "failed"), "an unknown base raises")


def test_make_test_list_equals_explain():
    make = subprocess.run(["make", "-s", "test-list", "BASE=HEAD"], cwd=ROOT, env=CLEAN, capture_output=True, text=True)
    direct = cli("--base", "HEAD", "--failopen", "--explain")
    check(make.returncode == 0 and make.stdout == direct.stdout, "make test-list output equals ci_select.py --explain")
    r = subprocess.run(["make", "-s", "test-file", "F="], cwd=ROOT, env=CLEAN, capture_output=True, text=True)
    check(r.returncode != 0, "make test-file without F= fails")
    r = subprocess.run(["make", "-s", "test-area", "A=nope"], cwd=ROOT, env=CLEAN, capture_output=True, text=True)
    check(r.returncode != 0, "make test-area with an unknown area fails (RESULT: FAIL, never a PASS)")


if __name__ == "__main__":
    main(globals())
