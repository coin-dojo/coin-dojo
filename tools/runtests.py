#!/usr/bin/env python3
"""runtests.py - run plain-python test files, one RESULT line each (ticket #111).

    tools/runtests.py [--python PY] [--root DIR] [--timeout SEC] [-v] [--require-result] FILE...

Every file runs from the repo root (like the SKILL.md/README commands), as `PY FILE`. A file FAILS when
  - it exits non-zero (or times out), or
  - it prints a line starting with `FAIL` or `RESULT: FAIL`, or
  - it prints `SKIP` lines but its `RESULT: PASS` does not say `(N skipped)` with N = the SKIP count
    (an uncounted skip hides a test that did not run), or
  - with --require-result, it prints no `RESULT: PASS` line.
Output: `RESULT: PASS <file>[ (N skipped: <first skip>)]` or `RESULT: FAIL <file> (<why>)` per file, then a
summary `RESULT: PASS|FAIL <label>`. Exit 0 only when every file passed. No files given = exit 1.
Pure stdlib. Format of the lines a test prints: tests/lib/hktest.py.
"""
import argparse
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIPPED_RX = re.compile(r"\((\d+) skipped")


def judge(rc, out, require_result=False, timed_out=False):
    """Return (ok, reason, skip_count, first_skip) for one file's exit code and combined output."""
    lines = out.splitlines()
    skips = [l for l in lines if l.startswith("SKIP")]
    first = skips[0] if skips else ""
    if timed_out:
        return False, "timeout", len(skips), first
    if rc != 0:
        return False, "exit %d" % rc, len(skips), first
    bad = [l for l in lines if l.startswith("FAIL") or l.startswith("RESULT: FAIL")]
    if bad:
        return False, "FAIL line: %s" % bad[0][:100], len(skips), first
    passes = [l for l in lines if l.startswith("RESULT: PASS")]
    if require_result and not passes:
        return False, "no RESULT: PASS line", len(skips), first
    if skips:
        counted = [int(m.group(1)) for l in passes for m in [SKIPPED_RX.search(l)] if m]
        if counted != [len(skips)] and not (len(passes) > 1 and sum(counted) == len(skips)):
            return False, "%d SKIP line(s) not counted in RESULT (print 'RESULT: PASS (%d skipped)')" % (
                len(skips), len(skips)), len(skips), first
    return True, "", len(skips), first


def run_file(path, python, root, timeout, verbose=False, require_result=False):
    t0 = time.monotonic()
    try:
        p = subprocess.run([python, path], cwd=root, capture_output=True, text=True, timeout=timeout)
        rc, out, to = p.returncode, p.stdout + p.stderr, False
    except subprocess.TimeoutExpired as e:
        rc, to = 124, True
        out = ((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""))
    ok, why, n, first = judge(rc, out, require_result, to)
    if verbose or not ok:
        sys.stdout.write(out if out.endswith("\n") or not out else out + "\n")
    took = time.monotonic() - t0
    if ok:
        extra = " (%d skipped: %s)" % (n, first) if n else ""
        print("RESULT: PASS %s%s [%.1fs]" % (path, extra, took))
    else:
        print("RESULT: FAIL %s (%s) [%.1fs]" % (path, why, took))
    return ok, n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--label", default="runtests")
    ap.add_argument("--require-result", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    if not a.files:
        print("RESULT: FAIL %s (no test files given)" % a.label)
        return 1
    fails = skipped = 0
    for f in a.files:
        ok, n = run_file(f, a.python, a.root, a.timeout, a.verbose, a.require_result)
        skipped += n
        fails += 0 if ok else 1
    if fails:
        print("RESULT: FAIL %s: %d of %d test file(s) failed" % (a.label, fails, len(a.files)))
        return 1
    print("RESULT: PASS%s %s" % (" (%d skipped)" % skipped if skipped else "", a.label))
    return 0


if __name__ == "__main__":
    sys.exit(main())
