#!/usr/bin/env python3
"""Tests for tools/recap.py (#136): diff two `make deploy` dry-run recaps; --zero for rollout's 2nd dry run.

Run: python3 tools/tests/test_recap.py   (stdlib only; reads fixtures, writes only to a temp dir)
Fixtures (tools/tests/fixtures/recap/): check1.log and apply.log are real cabinet runs (a dry run with --diff, changed=10,
and an apply with handlers, changed=20), scrubbed (host cab-test, user player, paths /home/dev/repo); zero.log is check1
as a clean second dry run would print it (changed=0, no diff bodies). Broken fixtures: count-diff.log (recap skipped=4),
extra-changed.log (one task flips, recap unchanged), truncated.log (no PLAY RECAP), bad-recap.log (changed=zero),
no-hosts.log (empty recap).
Cases: equal logs exit 0; --diff bodies and ANSI colours do not count; a count difference and an extra changed task
exit 1 naming them; loop items (also no_log ones that repeat), handlers and `host -> delegate` are parsed; several
PLAY RECAP blocks sum per host; --zero passes a clean run and fails changed/failed/unreachable > 0; a malformed or
missing log exits 2 in both modes; wrong argument count exits 2.
"""
import pathlib
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE.parent / "recap.py"
FX = HERE / "fixtures" / "recap"
sys.path.insert(0, str(HERE.parent))
import recap as R  # noqa: E402

sys.path.insert(0, str(HERE.parents[1] / "tests" / "lib"))
from hktest import check, finish  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)


def run(*args):
    r = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def test_parse():
    c = R.parse(FX / "check1.log")
    check(c.recap == {"cab-test": dict(ok=30, changed=10, unreachable=0, failed=0, skipped=3, rescued=0, ignored=0)},
          "parse: check1 recap counts (%s)" % c.recap)
    check(len(c.changed) == 20, "parse: check1 has 20 changed results (%d)" % len(c.changed))
    check("[cab-test] cabinet_tweaks : Per-core option dirs exist (item=Flycast)" in c.changed, "parse: loop item kept")
    check(c.changed.count("[cab-test] cabinet_tweaks : Set the ES-DE collection/startup keys (item=(censored due to no_log))") == 2,
          "parse: a repeated no_log item counts twice, parentheses kept")
    check(not any("Look for the local flycast build" in x for x in c.changed), "parse: ok results are not changed")
    a = R.parse(FX / "apply.log")
    check("[cab-test] handler: power : reload logind" in a.changed, "parse: handlers are marked")
    check(a.recap["cab-test"]["changed"] == 20, "parse: apply recap changed=20")
    z = R.parse(FX / "zero.log")
    check(z.changed == [] and z.recap["cab-test"]["changed"] == 0, "parse: zero.log has no changed results")


def test_compare(tmp):
    rc, out = run(FX / "check1.log", FX / "check1.log")
    check(rc == 0 and "recap: EQUAL" in out, "compare: a log against itself exits 0 (%d)" % rc)

    # the same run without --diff bodies and in colour is still equal
    plain = re.sub(r"(?m)^(---|\+\+\+|@@|[+-]).*\n", "", (FX / "check1.log").read_text())
    colour = re.sub(r"(?m)^(changed|ok): ", lambda m: "\x1b[0;33m%s: " % m.group(1), plain).replace("\n", "\x1b[0m\n")
    (tmp / "colour.log").write_text(colour)
    rc, out = run(FX / "check1.log", tmp / "colour.log")
    check(rc == 0, "compare: diff bodies and ANSI colours do not count (%d %s)" % (rc, out[-200:]))

    rc, out = run(FX / "zero.log", FX / "count-diff.log")
    check(rc == 1 and "recap: cab-test skipped=3 in A, 4 in B" in out and "changed only" not in out,
          "compare: a recap count difference exits 1 naming it (%d %s)" % (rc, out))

    rc, out = run(FX / "zero.log", FX / "extra-changed.log")
    check(rc == 1 and "changed only in B: [cab-test] dreamcast : Create the flycast dir" in out and "recap: cab-test" not in out,
          "compare: an extra changed task exits 1 naming it (%d %s)" % (rc, out))
    rc, out = run(FX / "extra-changed.log", FX / "zero.log")
    check(rc == 1 and "changed only in A: [cab-test] dreamcast : Create the flycast dir" in out, "compare: ... and only-in-A")

    rc, out = run(FX / "check1.log", FX / "zero.log")
    check(rc == 1 and "recap: cab-test changed=10 in A, 0 in B" in out and out.count("changed only in A:") == 20
          and "recap: DIFFERENT (1 recap count(s), 20 changed task(s))" in out,
          "compare: real dry run vs clean 2nd run names changed=10 and all 20 tasks")

    # the 2nd no_log item: a multiset, so one fewer of two identical items is a difference
    one = (FX / "check1.log").read_text().replace("changed: [cab-test] => (item=(censored due to no_log))",
                                                  "ok: [cab-test] => (item=(censored due to no_log))", 1)
    (tmp / "one-nolog.log").write_text(one)
    rc, out = run(FX / "check1.log", tmp / "one-nolog.log")
    check(rc == 1 and out.count("changed only in A:") == 1, "compare: repeated items compare as a multiset (%s)" % out)

    # a host only on one side, a delegated changed result, and two recap blocks that sum
    two = ("TASK [x : copy] ***\nchanged: [cab-b -> localhost]\n\nPLAY RECAP ***\ncab-a : ok=1 changed=0 unreachable=0 failed=0\n\n"
           "PLAY RECAP ***\ncab-a : ok=2 changed=1 unreachable=0 failed=0 skipped=1\ncab-b : ok=1 changed=1 unreachable=0 failed=0\n")
    (tmp / "two.log").write_text(two)
    t = R.parse(tmp / "two.log")
    check(t.recap["cab-a"]["ok"] == 3 and t.recap["cab-a"]["changed"] == 1 and t.recap["cab-a"]["rescued"] == 0,
          "compare: two PLAY RECAP blocks sum per host, missing optional keys are 0 (%s)" % t.recap)
    check(t.changed == ["[cab-b] x : copy"], "compare: `host -> delegate` counts as the host (%s)" % t.changed)
    (tmp / "one-host.log").write_text("PLAY RECAP ***\ncab-a : ok=3 changed=1 unreachable=0 failed=0 skipped=1\n")
    rc, out = run(tmp / "two.log", tmp / "one-host.log")
    check(rc == 1 and "recap: host cab-b only in A" in out and "changed only in A: [cab-b] x : copy" in out,
          "compare: a host only in A is named (%s)" % out)


def test_zero(tmp):
    rc, out = run("--zero", FX / "zero.log")
    check(rc == 0 and "recap: ZERO" in out, "zero: a clean 2nd dry run exits 0 (%d %s)" % (rc, out))
    rc, out = run("--zero", FX / "check1.log")
    check(rc == 1 and "recap: cab-test changed=10" in out and "changed: [cab-test] dreamcast : Create the flycast dir" in out,
          "zero: changed=10 exits 1 naming the host and the tasks (%d)" % rc)
    rc, out = run("--zero", FX / "extra-changed.log")
    check(rc == 1 and "changed: [cab-test] dreamcast : Create the flycast dir" in out,
          "zero: a changed task fails even when the recap says changed=0 (%d)" % rc)
    z = (FX / "zero.log").read_text()
    for key in ("failed", "unreachable"):
        (tmp / (key + ".log")).write_text(z.replace("%s=0 " % key, "%s=1 " % key, 1))
        rc, out = run("--zero", tmp / (key + ".log"))
        check(rc == 1 and "recap: cab-test %s=1" % key in out, "zero: %s=1 exits 1 (%d %s)" % (key, rc, out))


def test_malformed(tmp):
    for name, why in (("truncated.log", "no PLAY RECAP"), ("bad-recap.log", "bad recap field 'changed=zero'"),
                      ("no-hosts.log", "no host lines")):
        rc, out = run(FX / "zero.log", FX / name)
        check(rc == 2 and "MALFORMED" in out and why in out, "malformed: %s exits 2 (%s) (%d %s)" % (name, why, rc, out))
        rc, out = run("--zero", FX / name)
        check(rc == 2 and why in out, "malformed: --zero %s exits 2 (%d)" % (name, rc))
    (tmp / "nokeys.log").write_text("PLAY RECAP ***\ncab-a : ok=1 skipped=0\n")
    rc, out = run("--zero", tmp / "nokeys.log")
    check(rc == 2 and "lacks changed, unreachable, failed" in out, "malformed: a recap line without changed= exits 2 (%s)" % out)
    (tmp / "garbled.log").write_text("PLAY RECAP ***\nthis is not a recap line\n")
    rc, out = run("--zero", tmp / "garbled.log")
    check(rc == 2 and "is not 'host : key=N" in out, "malformed: a garbled recap line exits 2 (%s)" % out)
    (tmp / "empty.log").write_text("")
    rc, out = run("--zero", tmp / "empty.log")
    check(rc == 2, "malformed: an empty log exits 2")

    rc, out = run(FX / "zero.log", tmp / "missing.log")
    check(rc == 2 and "cannot read" in out, "missing: a missing B exits 2 (%d %s)" % (rc, out))
    rc, out = run(tmp / "missing.log", FX / "zero.log")
    check(rc == 2 and "cannot read" in out, "missing: a missing A exits 2")
    rc, out = run("--zero", tmp / "missing.log")
    check(rc == 2 and "cannot read" in out, "missing: --zero on a missing log exits 2")
    rc, _ = run(FX / "zero.log")
    check(rc == 2, "usage: compare with one log exits 2")
    rc, _ = run("--zero", FX / "zero.log", FX / "zero.log")
    check(rc == 2, "usage: --zero with two logs exits 2")


def main():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        test_parse()
        test_compare(tmp)
        test_zero(tmp)
        test_malformed(tmp)
    return finish()


if __name__ == "__main__":
    sys.exit(main())
