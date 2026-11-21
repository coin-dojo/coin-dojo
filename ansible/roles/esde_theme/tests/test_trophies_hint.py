#!/usr/bin/env python3
"""#178: esde_theme stages the main screen's "LP Trophies" hint (theme.xml trophiesHint, #177) only when the TROPHIES
overlay runs (RetroAchievements on + a user set); otherwise its staging step strips the element.

Runs the staging step's own python line (taken from tasks/main.yml) on a copy of the real theme.xml. Broken cases:
the hint off must remove exactly that element (the hotkey strip and the rest stay, the XML still parses), and a
theme whose element the regex cannot find must fail the step instead of shipping the hint.

    python3 ansible/roles/esde_theme/tests/test_trophies_hint.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.dom.minidom

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(ROLE)))
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
from hktest import check, main  # noqa: E402

TASKS = open(os.path.join(ROLE, "tasks", "main.yml")).read()
DEFAULTS = open(os.path.join(ROLE, "defaults", "main.yml")).read()
THEME = os.path.join(REPO, "themes", "coin-dojo", "theme.xml")


def step():
    """The {% if not esde_theme_trophies_hint %} block as a shell script with $S set by the caller."""
    m = re.search(r"\{% if not esde_theme_trophies_hint \| bool %\}\n(.*?)\n\s*\{% endif %\}", TASKS, re.S)
    return m.group(1) if m else ""


def run(theme_text):
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "theme.xml"), "w") as fh:
            fh.write(theme_text)
        script = step().replace("{{ (role_path ~ '/files/strip_trophies_hint.py') | quote }}",
                                os.path.join(ROLE, "files", "strip_trophies_hint.py"))
        r = subprocess.run(["sh", "-eu", "-c", script], env=dict(os.environ, S=d), capture_output=True, text=True)
        return r.returncode, open(os.path.join(d, "theme.xml")).read(), r.stderr


def test_default_follows_retroachievements():
    check("esde_theme_trophies_hint: \"{{ (hk_cheevos_enable | default(false)) | bool and "
          "(hk_cheevos_user | default('')) | length > 0 }}\"" in DEFAULTS,
          "the hint is on exactly when RetroAchievements is on and a user is set (= trophiesd_enable)")
    check(step(), "the staging step has the trophiesHint block")


BLOCK = """            <!-- trophies-hint:begin (#180) the LP shortcut in the help bar row -->
            <helpsystem name="help">
                <pos>0.866 0.945</pos>
            </helpsystem>
            <image name="trophiesHintIcon"><path>./_inc/help/button_y_XBOX.png</path></image>
            <text name="trophiesHintLabel"><text>Trophies</text></text>
            <!-- trophies-hint:end -->
"""


def strip_check(src, label):
    rc, out, err = run(src)
    check(rc == 0 and "trophiesHint" not in out and "trophies-hint:" not in out,
          "%s: hint off, everything of it is gone (rc %d %s)" % (label, rc, err.strip()))
    import difflib
    diff = list(difflib.ndiff(src.splitlines(), out.splitlines()))
    gone = [ln[2:] for ln in diff if ln.startswith("- ")]
    added = [ln for ln in diff if ln.startswith("+ ")]
    want = []
    for m in re.finditer(r"[ \t]*<!-- trophies-hint:begin.*?<!-- trophies-hint:end -->\n|[ \t]*<image name=\"trophiesHint\">.*?</image>\n",
                         src, re.S):
        want += m.group(0).splitlines()
    check(gone == want and not added and 'name="controlsHint"' in out,
          "%s: exactly the hint's lines go (%d of %d), the hotkey strip stays" % (label, len(gone), len(want)))
    try:
        xml.dom.minidom.parseString(out)
        ok = True
    except Exception:
        ok = False
    check(ok, "%s: the stripped theme.xml still parses" % label)
    return out


def test_off_strips_the_repo_theme_hint():
    src = open(THEME).read()
    check("trophiesHint" in src, "the theme has the hint (#177/#180)")
    strip_check(src, "repo theme")


def test_off_strips_the_marked_block():
    src = open(THEME).read()
    src = re.sub(r"[ \t]*<!-- trophies-hint:begin.*?<!-- trophies-hint:end -->\n", "", src, flags=re.S)
    src = re.sub(r'[ \t]*<image name="trophiesHint">.*?</image>\n', "", src, flags=re.S)
    i = src.index('<view name="system">') + len('<view name="system">\n')
    out = strip_check(src[:i] + BLOCK + src[i:], "#180 block")
    check("0.866 0.945" not in out, "#180 block: the moved help bar position goes with it (help bar back at 0.97)")


def test_broken_unmatched_hint_fails_the_step():
    src = open(THEME).read()
    rc, out, err = run(src.replace("trophiesHint", "trophiesHint ").replace("trophies-hint:end", "trophies-hint:stop"))
    check(rc != 0 and "trophies hint not removed" in err,
          "BROKEN CASE: a hint the regex misses fails staging instead of shipping it")
    i = src.index('<view name="system">') + len('<view name="system">\n')
    rc, out, err = run(src[:i] + BLOCK.replace("trophies-hint:end", "trophies-hint:fin") + src[i:])
    check(rc != 0 and "trophies hint not removed" in err, "BROKEN CASE: a block without its end marker fails staging")
    lines = len(src.splitlines())
    check(len(out.splitlines()) > lines - 40, "BROKEN CASE: ... and never swallows the theme up to the next end marker")


if __name__ == "__main__":
    if shutil.which("sh") is None:
        sys.exit("needs sh")
    main(globals())
