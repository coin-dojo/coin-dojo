#!/usr/bin/env python3
"""Layout gate (ticket #117, T2 of #103): the repo's top level is an allowlist, every top-level dir has an
owner, and the retired `newos/` tree is mentioned nowhere but in the history files.

Scans GIT-TRACKED files (`git ls-files`). Pure stdlib + tests/lib/hktest.py; run directly:
    python3 tests/test_layout.py

Rules:
  top-dir   a tracked top-level directory not in TOP_DIRS (add it here AND to .claude/team/owners.tsv)
  owner     a top-level directory in which no tracked file has an owners.tsv line (first match wins)
  newos     the string `newos/` in a tracked text file outside CHANGELOG.md, .claude/team/RETRO.md,
            docs/history/ and this file
  unowned   a tracked file no owners.tsv line matches (#154)
  dead      an owners.tsv line that is the first match of no tracked file (#154)

The broken case: test_selftest_* plant `newos/x` (as a path and as text) and an unowned/unknown top dir in a
throwaway git repo and assert each rule fires; the clean repo passes.
"""
import fnmatch
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from hktest import check, main, skip  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELF = "tests/test_layout.py"

TOP_DIRS = {".claude", ".github", ".githooks", "ansible", "brand", "build", "ci", "config", "docs",
            "memory", "mk",
            "personal", "skills", "tests", "themes", "tools", "vm"}
NEWOS_EXEMPT = ("CHANGELOG.md", ".claude/team/RETRO.md", "docs/history/", SELF)
OWNERS = ".claude/team/owners.tsv"
PRIVATE_ONLY = {".claude", "personal", "memory"}   # not in the community export (#146, #240): no team files, private art, agent notes


def tracked(root):
    out = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def owner_rules(root):
    """owners.tsv rows, or None when the tree has no owners.tsv (the community export, #146)."""
    if not os.path.isfile(os.path.join(root, OWNERS)):
        return None
    rules = []
    with open(os.path.join(root, OWNERS), encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.rstrip("\n")
            if ln and not ln.startswith("#") and "\t" in ln:
                rules.append(tuple(ln.split("\t")[:2]))
    return rules


def owner_of(path, rules):
    for glob, persona in rules:   # first match wins; '*' matches '/' (fnmatch does that)
        if fnmatch.fnmatchcase(path, glob):
            return persona
    return None


def violations(root, files=None):
    files = tracked(root) if files is None else files
    rules = owner_rules(root)
    bad = []
    tops = sorted({p.split("/")[0] for p in files if "/" in p})
    for d in tops:
        if d not in TOP_DIRS:
            bad.append("top-dir %s/ is not in the allowlist (tests/test_layout.py TOP_DIRS)" % d)
        elif rules is not None and not any(owner_of(p, rules) for p in files if p.startswith(d + "/")):
            bad.append("owner %s/ has no line in %s" % (d, OWNERS))
    for p in files:
        if p.startswith("newos/") or "/newos/" in p:
            bad.append("newos path %s" % p)
        if p == SELF or p.startswith(NEWOS_EXEMPT):
            continue
        try:
            data = open(os.path.join(root, p), "rb").read()
        except OSError:
            continue
        if b"\0" not in data and b"newos/" in data:
            ln = data.split(b"newos/")[0].count(b"\n") + 1
            bad.append("newos %s:%d mentions newos/ (the tree is gone: use ansible/, themes/ or vm/)" % (p, ln))
    return bad


def owner_problems(root):
    """#154: every tracked file has an owner, and every owners.tsv line is the first match of some tracked file."""
    rules, bad, first = owner_rules(root), [], set()
    for p in tracked(root):
        glob = next((g for g, _ in rules if fnmatch.fnmatchcase(p, g)), None)
        if glob is None:
            bad.append("unowned %s (add a line to %s)" % (p, OWNERS))
        first.add(glob)
    return bad + ["dead %s\t%s owns no tracked file (remove it)" % r for r in rules if r[0] not in first]


def test_every_file_owned_no_dead_glob():
    if owner_rules(ROOT) is None:     # #193: the community export ships no .claude/ (the broken cases below still run)
        skip("owners.tsv: no %s here (community export: the team files are not shipped)" % OWNERS)
        return
    bad = owner_problems(ROOT)
    for b in bad:
        print("  " + b)
    check(not bad, "owners.tsv: every tracked file owned, no dead line (%d problems)" % len(bad))


def test_selftest_dead_glob_and_unowned_fail():
    d = _repo({"skills/a/x.md": "ok\n", "docs/a.md": "ok\n"}, owners="skills/**\tqa\nre/**\tinternals\n.claude/**\tdocs\n")
    bad = owner_problems(d)
    check(any(b.startswith("dead re/**") for b in bad), "planted dead glob FAILs: %s" % bad)
    check(any(b.startswith("unowned docs/a.md") for b in bad), "unowned file FAILs: %s" % bad)
    check(not owner_problems(_repo({"skills/a/x.md": "ok\n"}, owners="skills/**\tqa\n.claude/**\tdocs\n")),
          "a fully owned repo with no dead line passes")


def _repo(files, owners="skills/**\tqa\n.claude/team/owners.tsv\tdocs\n"):
    d = tempfile.mkdtemp(prefix="hk-layout-")
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    files = dict(files)
    files[OWNERS] = owners
    for p, body in files.items():
        os.makedirs(os.path.dirname(os.path.join(d, p)) or d, exist_ok=True)
        open(os.path.join(d, p), "w").write(body)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    return d


def test_repo_layout_is_clean():
    if owner_rules(ROOT) is None:
        skip("owner rule: no %s here (community export: the team files are not shipped)" % OWNERS)
    bad = violations(ROOT)
    for b in bad:
        print("  " + b)
    check(not bad, "layout: allowlisted top dirs, owned, no newos/ outside history (%d problems)" % len(bad))


def test_every_top_dir_in_allowlist_exists():
    tops = {p.split("/")[0] for p in tracked(ROOT) if "/" in p}
    want = TOP_DIRS
    if owner_rules(ROOT) is None:
        skip("allowlist: %s are private-only (community export)" % " ".join(sorted(PRIVATE_ONLY)))
        want = TOP_DIRS - PRIVATE_ONLY
    check(not (want - tops), "every allowlisted dir is tracked (stale: %s)" % sorted(want - tops))


def test_selftest_clean_repo_passes():
    d = _repo({"skills/a/x.md": "fine\n", "docs/history/old.md": "see newos/ansible\n",
               "CHANGELOG.md": "newos/ gone\n", ".claude/team/RETRO.md": "newos/ retro\n"},
              owners="skills/**\tqa\ndocs/**\tdocs\n.claude/team/**\tstaff\n")
    bad = violations(d)
    check(not bad, "clean throwaway repo passes: %s" % bad)


def test_selftest_planted_newos_text_fails():
    d = _repo({"skills/a/x.md": "run newos/x now\n"})
    bad = violations(d)
    check(any(b.startswith("newos skills/a/x.md:1") for b in bad), "planted newos/x text FAILs: %s" % bad)


def test_selftest_planted_newos_path_fails():
    d = _repo({"skills/a/x.md": "ok\n", "newos/x": "ok\n"})
    bad = violations(d)
    check(any(b.startswith("newos path newos/x") for b in bad), "planted newos/x path FAILs: %s" % bad)
    check(any(b.startswith("top-dir newos/") for b in bad), "planted newos/ top dir FAILs: %s" % bad)


def test_selftest_unowned_top_dir_fails():
    d = _repo({"skills/a/x.md": "ok\n", "docs/a.md": "ok\n"})   # docs/ allowlisted but no owners line
    bad = violations(d)
    check(any(b.startswith("owner docs/") for b in bad), "top dir without an owners line FAILs: %s" % bad)


if __name__ == "__main__":
    main(globals())
