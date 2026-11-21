"""hktest - the one shared helper for coin-dojo's plain-python test files (ticket #111).

Replaces the check()/FAILURES/SKIPS/RESULT-line block that test files copy today. Pure stdlib.

    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))  # path to tests/lib
    from hktest import check, skip, main

    def test_thing():
        check(1 + 1 == 2, "arithmetic works")
        skip("no Pillow")            # counted in the RESULT line

    if __name__ == "__main__":
        main(globals())              # runs every test_* function, prints RESULT, exits 0/1

Two more runners for files that are not check()-style (#137):
    main(globals(), per_test=True)   tests use bare `assert`: each returning test prints `PASS <name>`
    unittest_main()                  unittest.TestCase files: PASS/FAIL/SKIP per test, same RESULT line

Output contract (tools/runtests.py and mk/python.mk read it):
  PASS <msg> / FAIL <msg>            one line per check()
  SKIP <msg>                         one line per skip(); the RESULT line must then say "(N skipped)"
  RESULT: PASS | RESULT: PASS (N skipped) | RESULT: FAIL (K failed)
  exit code 0 only when nothing failed

Under pytest (detected by sys.modules) check() raises AssertionError on a false condition, so
`pytest file.py` fails the same checks; run directly it records the failure and carries on.
"""
import sys

FAILURES = []
SKIPS = []


def _under_pytest():
    return "pytest" in sys.modules


def check(cond, msg):
    """Print PASS/FAIL for one check, record a failure; return the truth value of cond."""
    ok = bool(cond)
    print(("PASS " if ok else "FAIL ") + str(msg))
    if not ok:
        FAILURES.append(msg)
        if _under_pytest():
            raise AssertionError(msg)
    return ok


def skip(msg):
    """Record a skipped check (printed as 'SKIP msg'); it is counted in the RESULT line."""
    print("SKIP " + str(msg))
    SKIPS.append(msg)


def result_line():
    """The RESULT line for the checks recorded so far."""
    sk = " (%d skipped)" % len(SKIPS) if SKIPS else ""
    if FAILURES:
        return "RESULT: FAIL (%d failed)%s" % (len(FAILURES), sk)
    return "RESULT: PASS" + sk


def finish():
    """Print the RESULT line and return the exit code (0 pass, 1 fail)."""
    print(result_line())
    return 1 if FAILURES else 0


def main(namespace, per_test=False):
    """Run every test_* callable in `namespace` (sorted by name), print RESULT, exit.
    An exception in a test counts as one failure and the run goes on.
    per_test=True (files whose tests use bare `assert`, #137): a test that returns prints `PASS <name>`,
    so each test function is one counted check."""
    for name, fn in sorted(namespace.items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - a crashing test is a failure, not a stop
                FAILURES.append("%s raised %r" % (name, e))
                print("FAIL %s raised %r" % (name, e))
            else:
                if per_test:
                    print("PASS " + name)
    sys.exit(finish())


def unittest_main(module="__main__"):
    """Run the unittest.TestCase classes of `module` with hktest output (#137), print RESULT, exit.
    One `PASS <Class.test>` per passing test, `FAIL <Class.test>: <error>` per failure, error, failing subTest or
    unexpected success, and `SKIP <Class.test>: <reason>` per skipTest (counted in the RESULT line)."""
    import traceback
    import unittest

    def name(t):
        return t.id().split(".", 1)[1] if t.id().startswith(module + ".") else t.id()

    def fail(t, err):
        print("".join(traceback.format_exception(*err)).rstrip().replace("\n", "\n    "))  # indented: not a FAIL line
        why = traceback.format_exception_only(err[0], err[1])[-1].strip()
        msg = "%s: %s" % (name(t), why)
        print("FAIL " + msg)
        FAILURES.append(msg)

    class Result(unittest.TestResult):
        def addSuccess(self, t):
            super().addSuccess(t)
            print("PASS " + name(t))

        def addFailure(self, t, err):
            super().addFailure(t, err)
            fail(t, err)

        def addError(self, t, err):
            super().addError(t, err)
            fail(t, err)

        def addSubTest(self, t, sub, err):
            super().addSubTest(t, sub, err)
            if err is not None:
                fail(sub, err)

        def addSkip(self, t, reason):
            super().addSkip(t, reason)
            skip("%s: %s" % (name(t), reason))

        def addUnexpectedSuccess(self, t):
            super().addUnexpectedSuccess(t)
            msg = "%s: unexpected success (marked expectedFailure)" % name(t)
            print("FAIL " + msg)
            FAILURES.append(msg)

    unittest.defaultTestLoader.loadTestsFromModule(sys.modules[module]).run(Result())
    sys.exit(finish())
