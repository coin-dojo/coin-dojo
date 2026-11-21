#!/usr/bin/env python3
"""Tests for tools/commitlint.py, .githooks/commit-msg, tools/commit.sh (`make commit`) and board lint-titles (#99).

Run: python3 tools/tests/test_commitlint.py   (stdlib + git + make; throwaway repos, stub gum, no network)
Broken cases: no type, `feature:`, `Feat:`, no space after the colon, no #N, empty summary, 73-char subject,
trailing period, unknown type `wip`; the hook rejects a bad commit; `range` flags exactly the bad commits;
`make commit` refuses with nothing staged and with gum missing.
"""
import importlib.util
import os
import pathlib
import stat
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests", "lib"))
from hktest import check, finish  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


CL = load("commitlint")
TYPES = "feat fix perf refactor style docs test build ci chore revert".split()
ok = lambda m: CL.lint_message(m) == []

# --- valid -----------------------------------------------------------------------------------------------------
for t in TYPES:
    check(ok("%s: #12 do a thing" % t), "valid: %s without scope" % t)
    check(ok("%s(tools): #12 do a thing" % t), "valid: %s with scope" % t)
check(ok("feat(x): #1 add y\n\nWhy it matters.\n\nCloses #1\nCo-Authored-By: A <a@b.c>\n"), "valid: body + trailers")
check(ok("chore(release): v0.1.0"), "valid: chore(release): v0.1.0")
check(ok('Revert "feat(x): #3 y"'), "valid: git Revert subject")
check(ok("docs: tidy the readme\n\nRefs: none\n"), "valid: Refs: none body without #N")
check(ok("Merge branch 'x' into main"), "valid: merge commit (old history)")
check(ok("fix(a.b/c-d_e): #7 scope chars"), "valid: scope chars . / - _? ".replace("_?", ""))
check(ok("# comment\nfix: #7 comment lines are stripped\n"), "valid: leading comment line ignored")

# --- broken (must fail) ---------------------------------------------------------------------------------------
BAD = {
    "no type": "add a thing",
    "feature:": "feature: #1 add a thing",
    "Feat:": "Feat: #1 add a thing",
    "no space after colon": "feat:#1 add a thing",
    "missing #N": "feat(x): add a thing",
    "empty summary": "feat: #1 ",
    "empty summary no ticket": "feat: ",
    "73-char subject": "feat: #1 " + "x" * (73 - len("feat: #1 ")),
    "trailing period": "feat: #1 add a thing.",
    "unknown type wip": "wip: #1 add a thing",
    "empty scope": "feat(): #1 add a thing",
    "uppercase scope": "feat(Tools): #1 add a thing",
    "no blank line before body": "feat: #1 add a thing\nbody right away",
    "empty message": "\n# only a comment\n",
    "release without version": "chore(release): next",
}
for name, msg in BAD.items():
    check(not ok(msg), "broken: %s fails" % name)
check(ok("feat: #1 " + "x" * (72 - len("feat: #1 "))), "valid: exactly 72 chars")

# titles: same types, no number required
check(CL.lint_title("feat(scope): add a thing") == [], "title: type(scope): summary passes")
check(CL.lint_title("Add a thing") != [], "title: free text fails")
check(CL.lint_title("wip: add a thing") != [], "title: unknown type fails")


# --- throwaway repo: hook, range, make commit ------------------------------------------------------------------
def clean_env(**extra):
    e = dict(os.environ)
    for k in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "PY_TESTS", "APPLY", "V", "B", "GIT_DIR",
              "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        e.pop(k, None)
    e.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    e.update(extra)
    return e


def sh(args, cwd, env=None, inp=None):
    return subprocess.run(args, cwd=cwd, env=env or clean_env(), capture_output=True, text=True, input=inp)


def new_repo(tmp, hook=True):
    r = pathlib.Path(tmp) / "repo"
    r.mkdir()
    sh(["git", "init", "-q", "-b", "main"], r)
    if hook:
        h = r / ".githooks"
        h.mkdir()
        (h / "commit-msg").write_text((ROOT / ".githooks" / "commit-msg").read_text())
        (h / "commit-msg").chmod(0o755)
        (r / "tools").mkdir()
        (r / "tools" / "commitlint.py").write_text((ROOT / "tools" / "commitlint.py").read_text())
        sh(["git", "config", "core.hooksPath", ".githooks"], r)
    return r


def commit(r, msg, name="f.txt"):
    (r / name).write_text(msg + "\n" + str(len(list(r.iterdir()))))
    sh(["git", "add", name], r)
    return sh(["git", "commit", "-q", "-m", msg], r)


with tempfile.TemporaryDirectory() as tmp:
    r = new_repo(tmp)
    p = commit(r, "no type here")
    check(p.returncode != 0 and "RESULT: FAIL commitlint" in p.stderr, "hook rejects a bad commit")
    check(sh(["git", "rev-parse", "-q", "--verify", "HEAD"], r).returncode != 0, "rejected commit created nothing")
    p = commit(r, "feat(tools): #99 a good one")
    check(p.returncode == 0, "hook accepts a good commit (%s)" % p.stderr.strip())

with tempfile.TemporaryDirectory() as tmp:
    r = new_repo(tmp, hook=False)
    commit(r, "chore: #1 base")
    base = sh(["git", "rev-parse", "HEAD"], r).stdout.strip()
    commit(r, "feat: #2 good one")
    commit(r, "oops no format")
    commit(r, "fix: #3 good two")
    commit(r, "feat: missing the ticket")
    out = sh([sys.executable, str(ROOT / "tools" / "commitlint.py"), "range", base + "..HEAD"], r)
    bad_lines = [l for l in out.stdout.splitlines() if "<-" in l]
    check(out.returncode == 1 and len(bad_lines) == 2, "range flags exactly the 2 bad commits (%d)" % len(bad_lines))
    check("oops no format" in out.stdout and "missing the ticket" in out.stdout, "range names the bad subjects")
    check("good one" not in out.stdout and "good two" not in out.stdout, "range leaves good commits out")
    check(out.stdout.rstrip().splitlines()[-1].startswith("RESULT: FAIL"), "range ends with a RESULT: FAIL line")
    out = sh([sys.executable, str(ROOT / "tools" / "commitlint.py"), "range", base + "..HEAD~3"], r)
    check(out.returncode == 0 and "RESULT: PASS" in out.stdout, "range over only-good commits passes")
    out = sh([sys.executable, str(ROOT / "tools" / "commitlint.py"), "range", "nope..HEAD"], r)
    check(out.returncode != 0, "range with an unknown revision fails")

# --- make commit with a stub gum ---------------------------------------------------------------------------------
STUB = """#!/usr/bin/env bash
case "$1" in
  choose) echo "$STUB_TYPE" ;;
  input)
    shift; value=""; ph=""
    while [ $# -gt 0 ]; do case "$1" in --value) value="$2"; shift 2 ;; --placeholder) ph="$2"; shift 2 ;; *) shift ;; esac; done
    case "$ph" in
      scope) echo "$STUB_SCOPE" ;;
      "ticket number") echo "$STUB_TICKET" ;;
      *) echo "$value$STUB_SUMMARY" ;;
    esac ;;
  write) printf '%s' "$STUB_DESC" ;;
  confirm) exit "${STUB_CONFIRM:-0}" ;;
esac
"""


def make_commit(r, bindir, **stub):
    env = clean_env(PATH=str(bindir) + os.pathsep + os.environ["PATH"], **stub)
    # make must not see PY_TESTS from an outer `make test`: clean_env drops it (the env -u of the brief)
    return sh(["make", "-s", "-f", str(ROOT / "Makefile"), "commit"], r, env=env)


with tempfile.TemporaryDirectory() as tmp:
    bindir = pathlib.Path(tmp) / "bin"
    bindir.mkdir()
    gum = bindir / "gum"
    gum.write_text(STUB)
    gum.chmod(gum.stat().st_mode | stat.S_IXUSR)
    r = new_repo(tmp)
    p = make_commit(r, bindir, STUB_TYPE="feat", STUB_SCOPE="tools", STUB_TICKET="99", STUB_SUMMARY="add it", STUB_DESC="Why.")
    check(p.returncode != 0 and "nothing staged" in p.stderr, "make commit refuses with nothing staged")
    (r / "a.txt").write_text("a")
    sh(["git", "add", "a.txt"], r)
    p = make_commit(r, bindir, STUB_TYPE="feat", STUB_SCOPE="tools", STUB_TICKET="99", STUB_SUMMARY="add it", STUB_DESC="Why.")
    subj = sh(["git", "log", "-1", "--format=%s"], r).stdout.strip()
    body = sh(["git", "log", "-1", "--format=%b"], r).stdout.strip()
    check(p.returncode == 0 and subj == "feat(tools): #99 add it", "make commit subject is right (%r) %s" % (subj, p.stderr.strip()))
    check(body == "Why.", "make commit body is the description (%r)" % body)
    # no scope, "#12" typed, no description
    (r / "b.txt").write_text("b")
    sh(["git", "add", "b.txt"], r)
    make_commit(r, bindir, STUB_TYPE="fix", STUB_SCOPE="", STUB_TICKET="#12", STUB_SUMMARY="no scope", STUB_DESC="")
    subj = sh(["git", "log", "-1", "--format=%s"], r).stdout.strip()
    check(subj == "fix: #12 no scope", "make commit without scope (%r)" % subj)
    # ticket none -> Refs: none
    (r / "c.txt").write_text("c")
    sh(["git", "add", "c.txt"], r)
    p = make_commit(r, bindir, STUB_TYPE="docs", STUB_SCOPE="", STUB_TICKET="none", STUB_SUMMARY="tidy", STUB_DESC="Words.")
    full = sh(["git", "log", "-1", "--format=%s%n%b"], r).stdout
    check(p.returncode == 0 and full.startswith("docs: tidy") and "Refs: none" in full, "ticket none adds Refs: none (%r)" % full)
    # broken: bad ticket, declined confirm, hook still validates a summary the user mangled
    (r / "d.txt").write_text("d")
    sh(["git", "add", "d.txt"], r)
    n = sh(["git", "rev-list", "--count", "HEAD"], r).stdout
    p = make_commit(r, bindir, STUB_TYPE="fix", STUB_SCOPE="", STUB_TICKET="abc", STUB_SUMMARY="x", STUB_DESC="")
    check(p.returncode != 0 and "ticket number" in p.stderr, "make commit refuses a non-numeric ticket")
    p = make_commit(r, bindir, STUB_TYPE="fix", STUB_SCOPE="", STUB_TICKET="5", STUB_SUMMARY="x", STUB_DESC="", STUB_CONFIRM="1")
    check(p.returncode != 0, "make commit stops when the user declines the confirm")
    p = make_commit(r, bindir, STUB_TYPE="fix", STUB_SCOPE="", STUB_TICKET="5", STUB_SUMMARY="ends with a period.", STUB_DESC="")
    check(p.returncode != 0, "hook still rejects a bad summary typed into gum")
    check(sh(["git", "rev-list", "--count", "HEAD"], r).stdout == n, "no commit created by the refused runs")
    # gum missing: PATH without gum (git/make/bash found through the system dirs)
    nogum = pathlib.Path(tmp) / "nogum"
    nogum.mkdir()
    env = clean_env(PATH=os.pathsep.join(d for d in os.environ["PATH"].split(os.pathsep) if not (pathlib.Path(d) / "gum").exists()))
    p = sh(["make", "-s", "-f", str(ROOT / "Makefile"), "commit"], r, env=env)
    check(p.returncode != 0 and "brew install gum" in p.stderr, "gum missing: says brew install gum, exit 1")

sys.exit(finish())
