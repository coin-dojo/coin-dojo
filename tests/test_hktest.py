#!/usr/bin/env python3
"""Self-test for tests/lib/hktest.py and tools/runtests.py (ticket #111).
Pure stdlib; run directly:  python3 tests/test_hktest.py
Broken cases (the runner MUST fail each): a FAIL line, a non-zero exit, an uncounted SKIP, a timeout, no files,
a hktest file with a false check. Good cases: a counted skip passes; hktest PASS/skip lines and exit codes."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
from hktest import check, main  # noqa: E402

RUNNER = os.path.join(ROOT, "tools", "runtests.py")
LIB = os.path.join(HERE, "lib")
TEMPLATE = "import sys\nsys.path.insert(0, %r)\nfrom hktest import check, skip, main\n" % LIB


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, **kw)


def runner(files, *extra):
    return run([sys.executable, RUNNER, "--root", ROOT, *extra, *files])


def test_runner_cases():
    with tempfile.TemporaryDirectory() as d:
        def mk(name, body):
            p = os.path.join(d, name)
            open(p, "w").write(body)
            return p
        good = mk("t_good.py", "print('PASS a')\nprint('RESULT: PASS')\n")
        counted = mk("t_counted.py", "print('SKIP no pillow')\nprint('RESULT: PASS (1 skipped)')\n")
        failline = mk("t_failline.py", "print('FAIL x')\nprint('RESULT: PASS')\n")
        failres = mk("t_failres.py", "print('RESULT: FAIL')\n")
        badexit = mk("t_badexit.py", "import sys\nprint('RESULT: PASS')\nsys.exit(3)\n")
        crash = mk("t_crash.py", "raise RuntimeError('boom')\n")
        bare = mk("t_bare.py", "print('SKIP no pillow')\nprint('RESULT: PASS')\n")
        nores = mk("t_nores.py", "print('SKIP a')\nprint('SKIP b')\n")
        wrongn = mk("t_wrongn.py", "print('SKIP a')\nprint('SKIP b')\nprint('RESULT: PASS (1 skipped)')\n")
        sleeper = mk("t_sleep.py", "import time\ntime.sleep(30)\n")
        quiet = mk("t_quiet.py", "pass\n")

        r = runner([good])
        check(r.returncode == 0 and "RESULT: PASS " + good in r.stdout, "runner: a clean file passes")
        r = runner([counted])
        check(r.returncode == 0 and "(1 skipped: SKIP no pillow)" in r.stdout and "RESULT: PASS (1 skipped)" in r.stdout,
              "runner: a counted SKIP passes and shows in the summary")
        r = runner([good, counted])
        check(r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "RESULT: PASS (1 skipped) runtests",
              "runner: one RESULT line per file, summary last")
        for name, p, why in (("FAIL line", failline, "FAIL line"), ("RESULT: FAIL line", failres, "FAIL line"),
                             ("non-zero exit", badexit, "exit 3"), ("crash", crash, "exit 1"),
                             ("uncounted SKIP (bare RESULT: PASS)", bare, "not counted"),
                             ("uncounted SKIP (no RESULT line)", nores, "not counted"),
                             ("SKIP count mismatch", wrongn, "not counted")):
            r = runner([good, p])
            check(r.returncode == 1 and "RESULT: FAIL " + p in r.stdout and why in r.stdout
                  and "RESULT: FAIL runtests: 1 of 2" in r.stdout, "runner: %s MUST fail the run" % name)
        r = runner([sleeper], "--timeout", "1")
        check(r.returncode == 1 and "(timeout)" in r.stdout, "runner: a hung file MUST fail (timeout)")
        r = runner([quiet])
        check(r.returncode == 0, "runner: no RESULT line is fine by default")
        r = runner([quiet], "--require-result")
        check(r.returncode == 1 and "no RESULT: PASS line" in r.stdout, "runner: --require-result MUST fail a silent file")
        r = runner([])
        check(r.returncode == 1 and "no test files" in r.stdout, "runner: no files MUST fail")
        r = runner([os.path.join(d, "missing.py")])
        check(r.returncode == 1, "runner: a missing file MUST fail")


def test_hktest_files():
    with tempfile.TemporaryDirectory() as d:
        def mk(name, body):
            p = os.path.join(d, name)
            open(p, "w").write(TEMPLATE + body)
            return p
        ok = mk("h_ok.py", "def test_a():\n    check(True, 'one')\nmain(globals())\n")
        bad = mk("h_bad.py", "def test_a():\n    check(False, 'broken')\n    check(True, 'after')\nmain(globals())\n")
        sk = mk("h_skip.py", "def test_a():\n    skip('no tool')\n    check(True, 'x')\nmain(globals())\n")
        boom = mk("h_boom.py", "def test_a():\n    raise ValueError('x')\nmain(globals())\n")
        r = run([sys.executable, ok])
        check(r.returncode == 0 and r.stdout == "PASS one\nRESULT: PASS\n", "hktest: pass prints PASS and RESULT: PASS, exit 0")
        r = run([sys.executable, bad])
        check(r.returncode == 1 and "FAIL broken" in r.stdout and "PASS after" in r.stdout
              and "RESULT: FAIL (1 failed)" in r.stdout, "hktest: a false check MUST exit 1, FAIL line, run continues")
        r = run([sys.executable, sk])
        check(r.returncode == 0 and "SKIP no tool" in r.stdout and r.stdout.strip().endswith("RESULT: PASS (1 skipped)"),
              "hktest: skip() prints SKIP and counts it in RESULT")
        r = run([sys.executable, boom])
        check(r.returncode == 1 and "FAIL test_a raised" in r.stdout, "hktest: a raising test MUST count as a failure")
        pt = mk("h_pertest.py", "def test_a():\n    assert True\ndef test_b():\n    assert 1 == 2, 'planted'\n"
                "main(globals(), per_test=True)\n")
        r = run([sys.executable, pt])
        check(r.returncode == 1 and "PASS test_a\n" in r.stdout and "PASS test_b" not in r.stdout
              and "FAIL test_b raised AssertionError('planted')" in r.stdout and "RESULT: FAIL (1 failed)" in r.stdout,
              "hktest: per_test=True prints PASS per returning test; a failing assert MUST FAIL without a PASS line")
        check(runner([pt]).returncode == 1, "runner MUST reject a per_test file with a failing assert")
        for p in (ok, sk):
            check(runner([p]).returncode == 0, "runner accepts hktest output (%s)" % os.path.basename(p))
        for p in (bad, boom):
            check(runner([p]).returncode == 1, "runner MUST reject hktest failure (%s)" % os.path.basename(p))


UT_HEAD = "import unittest\nfrom hktest import unittest_main\nclass T(unittest.TestCase):\n"


def test_unittest_main():
    with tempfile.TemporaryDirectory() as d:
        def mk(name, body):
            p = os.path.join(d, name)
            open(p, "w").write(TEMPLATE + UT_HEAD + body + "unittest_main()\n")
            return p
        good = mk("u_good.py", "    def test_a(self):\n        self.assertEqual(1, 1)\n"
                               "    def test_s(self):\n        self.skipTest('no docker')\n")
        r = run([sys.executable, good])
        check(r.returncode == 0 and "PASS T.test_a\n" in r.stdout and "SKIP T.test_s: no docker\n" in r.stdout
              and r.stdout.strip().endswith("RESULT: PASS (1 skipped)"), "hktest unittest_main: PASS and counted SKIP per test")
        check(runner([good]).returncode == 0, "runner accepts unittest_main output with a counted skip")
        broken = (("assert", "    def test_f(self):\n        self.assertIn('x', 'abc', 'planted')\n", "AssertionError"),
                  ("error", "    def test_f(self):\n        raise KeyError('planted')\n", "KeyError"),
                  ("subTest", "    def test_f(self):\n        for i in (1, 2):\n            with self.subTest(i=i):\n"
                              "                self.assertEqual(i, 1)\n", "AssertionError"),
                  ("unexpected success", "    @unittest.expectedFailure\n    def test_f(self):\n        pass\n",
                   "unexpected success"))
        for what, body, why in broken:
            p = mk("u_%s.py" % what.replace(" ", "_"), "    def test_ok(self):\n        pass\n" + body)
            r = run([sys.executable, p])
            fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL ")]
            check(r.returncode == 1 and len(fails) == 1 and "test_f" in fails[0] and why in fails[0]
                  and "PASS T.test_f" not in r.stdout and "PASS T.test_ok" in r.stdout
                  and "RESULT: FAIL (1 failed)" in r.stdout and runner([p]).returncode == 1,
                  "hktest unittest_main: a %s MUST fail (one FAIL line, exit 1, runner rejects)" % what)


def test_pytest_compat():
    import contextlib
    import io
    import hktest
    saved = list(hktest.FAILURES)
    had = "pytest" in sys.modules
    if not had:
        sys.modules["pytest"] = type(sys)("pytest")  # hktest only looks for the name in sys.modules
    raised = False
    try:
        with contextlib.redirect_stdout(io.StringIO()):  # the planted FAIL line must not reach our own output
            hktest.check(False, "planted under pytest")
    except AssertionError:
        raised = True
    finally:
        hktest.FAILURES[:] = saved
        if not had:
            del sys.modules["pytest"]
    check(raised, "hktest: under pytest a false check raises AssertionError")


if __name__ == "__main__":
    main(globals())
