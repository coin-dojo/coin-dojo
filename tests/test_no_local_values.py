#!/usr/bin/env python3
"""Compliance gate (ticket #115 report, #149 strict; T14 of #103): no maintainer-specific value in shared files.

Scans GIT-TRACKED files (`git ls-files`) for values that belong in local config, not in the community repo:
the maintainer's values from the tracked private list config/edition/private.txt (#240: LAN hosts, home folder,
Keychain item names, the forge repo, NAS paths; the same list the community export scans, so no real value is
written here) and the generic ones below (/home/coindojo, uid 1001 in paths, NAS share paths). In the private repo
(tools/edition_export.py present) the list must load; the community tree has no list and scans the generic ones.

Exempt: personal/, CHANGELOG.md, docs/history/ and memory/ (neither is exported, #240), .claude/ (agent-only),
config/edition/ (never exported: the list itself), config/local*.yml, personal/config/, and this file.

Mode: STRICT since #149: any hit prints FAIL file:line, exit 1. Where a value belongs: a config variable
(config/variables.d/*.yml, the maintainer's value in personal/config/local.yml), personal/, or a neutral wording in docs
(`<cabinet host>`, `your Keychain item`). `--report` (or HK_LOCAL_VALUES_REPORT=1) only prints WARN lines and
per-file counts, exit 0, for a worklist (HK_LOCAL_VALUES_WORKLIST=<path> also writes it). Tests that must name a
value (a needle, a planted broken case) use RFC 5737 addresses and example.invalid names in a fixture list (#240).

Pure stdlib; pytest-compatible, or run directly:  python3 tests/test_no_local_values.py [--report]
"""
import fnmatch
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELF = "tests/test_no_local_values.py"

PRIVATE_LIST = "config/edition/private.txt"     # tracked in the private repo, never exported (#240)
PRIVATE_REPO_MARK = "tools/edition_export.py"   # only the private repo has the export machinery
GENERIC = [
    # built from pieces: the export renames the stock user, and the community tree's own /home/<user> is no leak
    ("coindojo-home", re.compile("/home/" + "cap" + "com")),
    ("uid-1001-path", re.compile(r"/1001(?![\w])")),
    ("nas-path", re.compile(r"(?<![\w/.-])/share/\w")),
]

EXEMPT_GLOBS = ["personal/*", "CHANGELOG.md", "docs/history/*", "memory/*", ".claude/*", "config/edition/*",
                "config/local*.yml", "config/local*.yaml", SELF]


def load_private(path):
    """[(kind, regex)] from a private list (kind<TAB>value: a literal, case-insensitive on alphanumeric boundaries, or
    re:REGEX; the format of tools/edition_export.py). A missing file is []."""
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for raw in f.read().splitlines():
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            kind, _, val = raw.partition("\t")
            val = val.strip()
            rx = val[3:] if val.startswith("re:") else r"(?<![A-Za-z0-9])" + re.escape(val) + r"(?![A-Za-z0-9])"
            out.append((kind, re.compile(rx, re.IGNORECASE)))
    return out


def patterns(root):
    return GENERIC + load_private(os.path.join(root, PRIVATE_LIST))


PATTERNS = patterns(ROOT)       # this repo's rules (tests/test_docs.py reads them: one list, not a copy)


def exempt(path):
    return any(fnmatch.fnmatch(path, g) for g in EXEMPT_GLOBS)


def tracked(root):
    out = subprocess.run(["git", "-C", root, "ls-files", "-z"], check=True, capture_output=True).stdout
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def scan(root):
    """Return [(path, lineno, rule, text)] for every non-exempt hit."""
    hits, pats = [], patterns(root)
    for rel in tracked(root):
        if exempt(rel):
            continue
        full = os.path.join(root, rel)
        if os.path.islink(full) or not os.path.isfile(full):
            continue
        with open(full, "rb") as f:
            data = f.read()
        if b"\0" in data[:8192]:
            continue  # binary
        for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            for rule, rx in pats:
                if rx.search(line):
                    hits.append((rel, n, rule, line.strip()[:100]))
    return hits


def counts(hits):
    c = {}
    for rel, _n, _r, _t in hits:
        c[rel] = c.get(rel, 0) + 1
    return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))


def report(hits, strict):
    tag = "FAIL" if strict else "WARN"
    for rel, n, rule, text in hits:
        print(f"{tag} {rel}:{n}: [{rule}] {text}")
    print(f"{len(hits)} local value hit(s) in {len(counts(hits))} file(s) (per-file counts below)")
    for rel, c in counts(hits):
        print(f"  {c:5d} {rel}")


def _strict_requested():
    return "--report" not in sys.argv[1:] and os.environ.get("HK_LOCAL_VALUES_REPORT") != "1"


def test_repo_no_local_values():
    hits = scan(ROOT)
    strict = _strict_requested()
    report(hits, strict)
    wl = os.environ.get("HK_LOCAL_VALUES_WORKLIST")
    if wl:
        with open(wl, "w") as f:
            f.write(f"# {len(hits)} hits in {len(counts(hits))} files (count path)\n")
            for rel, c in counts(hits):
                f.write(f"{c}\t{rel}\n")
    if strict:
        assert not hits, f"{len(hits)} maintainer-specific value(s) in shared files (strict)"


def _make_repo(files):
    d = tempfile.mkdtemp(prefix="hk-localvals-")
    subprocess.run(["git", "init", "-q", d], check=True)
    for rel, body in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(body)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    return d


# A fixture private list (RFC 5737 addresses, example.invalid names): the mechanism, not anybody's values (#240).
FIXTURE_LIST = ("lan-host\tre:(?<![0-9])192\\.0\\.2\\.[0-9]{1,3}(?![0-9])\nmac-home\t/Users/fixture-user\n"
                "keychain-name\tfixture-cabinet-ssh\nforgejo-repo\tforge.example.invalid/fixture/repo\n")
PLANTED = ["host=192.0.2.124", "host=192.0.2.150", "/home/" + "cap" + "com/x", "/Users/fixture-user/y",
           "security find-generic-password -s fixture-cabinet-ssh", "http://forge.example.invalid/fixture/repo",
           "/share/" + "media/movies", "/run/user/" + "1001/bus"]


def _fixture_repo(files):
    return _make_repo(dict({PRIVATE_LIST: FIXTURE_LIST}, **files))


def test_selftest_planted_value_in_shared_file_fires():
    for v in PLANTED:
        d = _fixture_repo({"tools/x.sh": f"echo {v}\n"})
        hits = scan(d)
        assert hits and hits[0][0] == "tools/x.sh" and hits[0][1] == 1, f"not caught: {v}"


def test_private_list_loads_in_the_private_repo():
    """#240: in the private repo the tracked list must load (else the gate goes quiet); the export has none."""
    if not os.path.isfile(os.path.join(ROOT, PRIVATE_REPO_MARK)):
        return                                    # the community tree: no list, the generic patterns only
    got = load_private(os.path.join(ROOT, PRIVATE_LIST))
    assert len(got) >= 5, f"{PRIVATE_LIST} must load with its values (got {len(got)})"
    d = _make_repo({"tools/x.sh": "echo 192.0.2.124\n"})
    assert scan(d) == [], "BROKEN control: without a list, a fixture value is no hit"


def test_selftest_strict_fails_report_passes():
    d = _fixture_repo({"tools/x.sh": "echo 192.0.2.124\n"})
    env = dict(os.environ, HK_LOCAL_VALUES_REPORT="")
    # the scan targets ROOT, so run a copy of this test against the throwaway repo
    code = ("import sys; sys.path.insert(0, %r); import test_no_local_values as t; "
            "t.ROOT = %r; sys.argv = ['x'] + sys.argv[1:]; t.test_repo_no_local_values()"
            % (os.path.dirname(os.path.abspath(__file__)), d))
    bad = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert bad.returncode != 0 and "FAIL tools/x.sh:1" in bad.stdout, "strict is the default: " + bad.stdout + bad.stderr
    ok = subprocess.run([sys.executable, "-c", code, "--report"], env=env, capture_output=True, text=True)
    assert ok.returncode == 0 and "WARN tools/x.sh:1" in ok.stdout, ok.stdout + ok.stderr
    envr = dict(os.environ, HK_LOCAL_VALUES_REPORT="1")
    ok2 = subprocess.run([sys.executable, "-c", code], env=envr, capture_output=True, text=True)
    assert ok2.returncode == 0, "the env var must switch to report-only"


def test_selftest_exempt_paths_pass():
    body = "192.0.2.124 /Users/fixture-user fixture-cabinet-ssh\n"
    d = _fixture_repo({p: body for p in ["personal/a.sh", "personal/config/x.yml", "CHANGELOG.md", "docs/history/a.md",
                                         ".claude/team/x.md", "config/local.yml", "config/local-cab.yml",
                                         "config/edition/denylist.txt"]})
    assert scan(d) == [], scan(d)


def test_selftest_clean_and_innocent_values_pass():
    d = _fixture_repo({"a.sh": "echo /usr/share/doc 10.0.0.1 /home/pi uid 1000 192.0.2.1234 /Users/fixture-users\n"})
    assert scan(d) == [], scan(d)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}\n{e}")
    print("RESULT: FAIL no-local-values" if fails else "RESULT: PASS no-local-values")
    sys.exit(1 if fails else 0)
