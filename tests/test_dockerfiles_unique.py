#!/usr/bin/env python3
"""No two tracked Dockerfiles may be byte-identical (one image, one Dockerfile; #113).
pytest-compatible, or run directly:  python3 tests/test_dockerfiles_unique.py
Broken case: a planted copy of a Dockerfile MUST be reported as a duplicate."""
import hashlib
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILURES = []


def check(cond, msg):
    print(("PASS: " if cond else "FAIL: ") + msg)
    if not cond:
        FAILURES.append(msg)


def tracked_dockerfiles():
    out = subprocess.run(["git", "-C", ROOT, "ls-files", "-z"], capture_output=True, text=True, check=True).stdout
    return [p for p in out.split("\0") if p and os.path.basename(p).startswith("Dockerfile")]


def duplicates(paths, root=ROOT):
    seen = {}
    for p in paths:
        h = hashlib.sha256(open(os.path.join(root, p), "rb").read()).hexdigest()
        seen.setdefault(h, []).append(p)
    return [v for v in seen.values() if len(v) > 1]


def test_no_duplicate_dockerfiles():
    paths = tracked_dockerfiles()
    check(len(paths) >= 2, "found tracked Dockerfiles (%d)" % len(paths))
    d = duplicates(paths)
    check(not d, "no two tracked Dockerfiles are byte-identical %s" % d)


def test_planted_copy_fails():
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        for name in ("a/Dockerfile", "b/Dockerfile.copy", "c/Dockerfile"):
            os.makedirs(os.path.join(t, os.path.dirname(name)), exist_ok=True)
            open(os.path.join(t, name), "w").write("FROM scratch\n" if name != "c/Dockerfile" else "FROM busybox\n")
        d = duplicates(["a/Dockerfile", "b/Dockerfile.copy", "c/Dockerfile"], t)
        check(d == [["a/Dockerfile", "b/Dockerfile.copy"]], "planted copy is detected as a duplicate")


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_") and callable(f):
            f()
    raise SystemExit(1 if FAILURES else 0)
