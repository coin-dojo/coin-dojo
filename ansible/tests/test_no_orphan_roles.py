#!/usr/bin/env python3
"""Every directory under ansible/roles/ is used by a play (#110).

The Batocera layer sat unused for weeks because nothing noticed a role no play referenced.
`orphans()` is a function over a roles directory and the play TEXT, so the same rule runs on a
planted orphan (test_broken_*): that MUST be flagged. Also: LIMIT=batocera is refused by make.

Pure stdlib. Run directly:  python3 ansible/tests/test_no_orphan_roles.py
"""
import os
import re
import subprocess
import sys
import tempfile

ANSIBLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(ANSIBLE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests", "lib"))
from hktest import check, main  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)


def used_roles(*play_texts):
    """Role names a play lists: `role: x` or `{ role: x, ... }` (comments ignored)."""
    names = set()
    for text in play_texts:
        for ln in text.splitlines():
            code = ln.split("#", 1)[0]
            names.update(re.findall(r"\brole:\s*([A-Za-z0-9_]+)", code))
    return names


def orphans(roles_dir, *play_texts):
    """Directories under roles_dir that no play uses."""
    used = used_roles(*play_texts)
    return sorted(d for d in os.listdir(roles_dir)
                  if os.path.isdir(os.path.join(roles_dir, d)) and not d.startswith(".") and d not in used)


def _read(name):
    with open(os.path.join(ANSIBLE, name), encoding="utf-8") as fh:
        return fh.read()


def test_every_role_is_used():
    bad = orphans(os.path.join(ANSIBLE, "roles"), _read("site.yml"), _read("vm.yml"))
    check(not bad, "every roles/* is used by site.yml or vm.yml (orphans: %s)" % (bad or "none"))
    check(len(used_roles(_read("site.yml"), _read("vm.yml"))) >= 10, "the role scanner sees the plays' roles")


def test_every_used_role_exists():
    roles = os.path.join(ANSIBLE, "roles")
    missing = sorted(r for r in used_roles(_read("site.yml"), _read("vm.yml"))
                     if not os.path.isdir(os.path.join(roles, r)))
    check(not missing, "every role a play uses exists under roles/ (missing: %s)" % (missing or "none"))


def test_broken_planted_orphan_role_is_flagged():
    with tempfile.TemporaryDirectory() as d:
        for r in ("used_one", "used_two", "dead_batocera_thing"):
            os.makedirs(os.path.join(d, r, "tasks"))
        play = "- hosts: x\n  roles:\n    - role: used_one\n    - { role: used_two, tags: [t] }\n    # - role: dead_batocera_thing\n"
        check(orphans(d, play) == ["dead_batocera_thing"], "BROKEN: a planted orphan role (only in a comment) is flagged")
        check(orphans(d, play + "    - role: dead_batocera_thing\n") == [], "a role once a play uses it is no longer an orphan")


def test_limit_batocera_is_refused():
    for limit in ("batocera", "all"):
        r = subprocess.run(["make", "-n", "-C", ROOT, "deploy", "LIMIT=" + limit], capture_output=True, text=True)
        check(r.returncode != 0 and "LIMIT='%s'" % limit in r.stderr,
              "BROKEN: make deploy LIMIT=%s is refused" % limit)
    r = subprocess.run(["make", "-n", "-C", ROOT, "deploy", "LIMIT=coin-dojo"], capture_output=True, text=True)
    check(r.returncode == 0, "make deploy LIMIT=coin-dojo is accepted")
    r = subprocess.run(["make", "-n", "-C", ROOT, "pull"], capture_output=True, text=True)
    check("bin/deploy pull" not in r.stdout, "`make pull` no longer calls bin/deploy pull")


if __name__ == "__main__":
    main(globals())
