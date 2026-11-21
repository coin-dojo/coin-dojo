#!/usr/bin/env python3
"""Footer build stamp (the maintainer 2026-11-15): theme.xml carries @COIN_DOJO_VERSION@, mk/flags.mk passes
`git describe --tags --always --dirty=+dirty` as coin_dojo_version, and the esde_theme staging step fills it in.

Pure stdlib, pytest-compatible, or run directly:
    python3 ansible/roles/esde_theme/tests/test_version_stamp.py

The staging lines are cut out of tasks/main.yml and run for real with sh against a temp copy of theme.xml,
including broken cases (a shell-injection value, an empty value, an unfilled placeholder) that MUST fail.
"""
import os
import re
import subprocess
import sys
import tempfile

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(ROLE)))
THEME = os.path.join(REPO, "themes", "coin-dojo", "theme.xml")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "tests", "lib"))
from hktest import check as _check, main  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _stamp_script():
    """The shell lines of the staging task from `V=` through the unfilled-placeholder guard."""
    lines = _read(os.path.join(ROLE, "tasks", "main.yml")).splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip().startswith("V={{"))
    end = next(i for i in range(start, len(lines)) if "version stamp not filled" in lines[i])
    body = [ln.strip() for ln in lines[start:end + 1]]
    return body


def _run(value, theme_text):
    """Run the staging lines with coin_dojo_version=value; returns (rc, resulting theme.xml text)."""
    body = _stamp_script()
    body[0] = "V='%s'" % value.replace("'", "'\\''")   # stands in for {{ coin_dojo_version | quote }}
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "theme.xml"), "w", encoding="utf-8") as fh:
            fh.write(theme_text)
        script = "set -eu\nS=%s\n%s\n" % (d, "\n".join(body))
        r = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
        return r.returncode, _read(os.path.join(d, "theme.xml"))


def test_theme_has_one_placeholder_in_the_footer_stamp():
    t = _read(THEME)
    _check(t.count("@COIN_DOJO_VERSION@") == 1, "theme.xml has exactly one @COIN_DOJO_VERSION@")
    m = re.search(r'<text name="buildStamp">(.*?)</text>\s*\n\s*<fontPath>', t, re.S)
    _check(m is not None and "REV @COIN_DOJO_VERSION@" in t, "buildStamp text reads 'REV <version>'")
    _check('<image name="brandLogo">' not in t, "the wordmark image is gone from the footer")
    import xml.dom.minidom
    try:
        xml.dom.minidom.parseString(t)
        ok = True
    except Exception as e:   # e.g. "--" inside a comment (2026-11-15: the deploy gate caught it, this test did not)
        print("  parse error:", e)
        ok = False
    _check(ok, "theme.xml parses as XML")


def test_flags_pass_git_describe():
    f = _read(os.path.join(REPO, "mk", "flags.mk"))
    _check("git describe --tags --always --dirty=+dirty" in f, "mk/flags.mk derives the version with git describe")
    _check("-e coin_dojo_version=$(COIN_DOJO_VERSION)" in f, "ANSIBLE_EXTRA_VARS passes coin_dojo_version")


def test_stamp_fills_hash_and_tag():
    t = _read(THEME)
    for v in ("5de0947a1b", "v0.4.0", "v0.4.0-3-gabc1234", "5de0947+dirty"):
        rc, out = _run(v, t)
        _check(rc == 0 and ("REV %s<" % v) in out and "@COIN_DOJO_VERSION@" not in out,
               "stamp filled with %s" % v)


def test_BROKEN_values_are_refused():
    t = _read(THEME)
    for v in ("", "x;rm -rf /", "a/b", "v1 2", "$(id)"):
        rc, out = _run(v, t)
        _check(rc != 0, "bad version %r is refused" % v)


def test_BROKEN_unfilled_placeholder_fails():
    rc, _ = _run("v1.0.0", "<text>@COIN_DOJO_VERSION@ @COIN_DOJO_VERSION@</text>")
    _check(rc != 0, "a second, unfilled placeholder fails the staging step")


def _keep(staged, live):
    with tempfile.TemporaryDirectory() as d:
        a, b = os.path.join(d, "staged.xml"), os.path.join(d, "live.xml")
        open(a, "w").write(staged)
        if live is not None:
            open(b, "w").write(live)
        r = subprocess.run(["sh", os.path.join(ROLE, "files", "keep_rev.sh"), a, b], capture_output=True, text=True)
        return r.returncode, r.stdout.strip(), open(a).read()


def test_rev_only_difference_keeps_the_live_stamp():
    """#158: only a dirty-flag flip of the SAME commit keeps the live stamp; the footer tells the truth."""
    t = _read(THEME).replace("@COIN_DOJO_VERSION@", "v0.2.0-5-gabc1234")
    rc, out, staged = _keep(t.replace("v0.2.0-5-gabc1234", "v0.2.0-5-gabc1234-dirty"), t)
    _check(rc == 0 and out == "kept" and staged == t, "a -dirty flip of the same commit is not a change (repeat deploy = changed=0)")
    rc, out, staged = _keep(t.replace("v0.2.0-5-gabc1234", "v0.2.0-5-gabc1234+dirty"), t)
    _check(out == "kept" and staged == t, "a +dirty flip of the same commit is not a change")
    rc, out, staged = _keep(t, t)
    _check(out == "kept" and staged == t, "the same stamp is kept")


def test_BROKEN_new_commit_ships_its_stamp():
    """#158: v0.2.0 was released but the footer kept v0.1.0-54-ga7722f2: a new commit must ship its REV."""
    t = _read(THEME).replace("@COIN_DOJO_VERSION@", "v0.2.0")
    live = t.replace("v0.2.0", "v0.1.0-54-ga7722f2")
    rc, out, staged = _keep(t, live)
    _check(rc == 0 and out == "new" and "REV v0.2.0<" in staged, "BROKEN: a release over an older commit ships the new stamp")
    t2 = t.replace("REV v0.2.0<", "REV v0.2.0-1-gdef5678<")
    rc, out, staged = _keep(t2, t)
    _check(out == "new" and "v0.2.0-1-gdef5678" in staged, "BROKEN: one commit past the release ships its own stamp")


def test_BROKEN_real_theme_change_still_ships_with_new_stamp():
    t = _read(THEME).replace("@COIN_DOJO_VERSION@", "v0.2.0-5-gnew")
    live = t.replace("v0.2.0-5-gnew", "v0.1.0-8-gold") + "<!-- older -->\n"
    rc, out, staged = _keep(t, live)
    _check(out == "new" and "v0.2.0-5-gnew" in staged, "BROKEN: a real difference keeps the new file and its new REV")
    rc, out, staged = _keep(t, None)
    _check(out == "new" and staged == t, "no live theme.xml: staged file is untouched")
    rc, _, _ = _keep(t, t) if False else (subprocess.run(["sh", os.path.join(ROLE, "files", "keep_rev.sh")]).returncode, 0, 0)
    _check(rc != 0, "missing arguments are refused")


def test_task_runs_keep_rev_before_the_sync():
    tasks = _read(os.path.join(ROLE, "tasks", "main.yml"))
    _check(0 < tasks.find("keep_rev.sh") < tasks.find("- name: List the theme files that differ"),
           "keep_rev runs before the plan and the sync")


if __name__ == "__main__":
    main(globals())
