#!/usr/bin/env python3
"""RetroAchievements secrets never reach git or the community edition (#45, Security #27 rule; the maintainer 2026-11-16).

- no tracked file outside personal/ carries a non-empty cheevos_token / cheevos_password / flycast Token value;
- the shared config ships achievements OFF with an empty user and no Keychain item;
- every task that writes the token runs with no_log: true and takes it only from `cheevos_token` (bin/deploy's vars
  file), never from config (no hk_* token lever);
- the user name is set only in personal/config/local.yml (edition-export drops personal/).
Rules work on TEXT, so the broken cases run on planted copies. Pure stdlib:  python3 tools/tests/test_cheevos_secrets.py
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
from hktest import check, main  # noqa: E402

SECRET_RX = re.compile(r"""(?im)^\s*["']?(cheevos_token|cheevos_password|cheevos_web_key|web_api_key|Token)["']?\s*[=:]\s*["']?([A-Za-z0-9]{8,})""")
ROLE_TASKS = ("ansible/roles/retroarch122/tasks/main.yml", "ansible/roles/dreamcast/tasks/main.yml",
              "ansible/roles/settings/tasks/main.yml")   # settings: the TROPHIES web API key (#176)


def tracked():
    return subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True).stdout.split()


def secret_hits(files):
    """{path: key} for files (path -> text) outside personal/ with a literal token/password value."""
    hits = {}
    for path, text in files.items():
        if path.startswith("personal/") or path == "tools/tests/test_cheevos_secrets.py":
            continue
        m = SECRET_RX.search(text)
        if m and "{{" not in m.group(0):
            hits[path] = m.group(1)
    return hits


def token_task_problems(tasks):
    """Tasks that mention cheevos_token must run with no_log: true; no task may read a token from an hk_* lever."""
    p = []
    for blk in re.split(r"(?m)^(?=\s*- name:)", tasks):           # tasks inside a block too (settings role)
        if re.search(r"cheevos_token|cheevos_web_key", blk) or re.search(r"option: Token\b", blk):
            if not re.search(r"(?m)^\s+no_log: true\s*$", blk):
                p.append("token task without no_log: " + blk.splitlines()[0])
        if re.search(r"\bhk_\w*token\w*", blk):
            p.append("token read from config: " + blk.splitlines()[0])
    return p


def test_no_literal_token_in_git():
    files = {}
    for p in tracked():
        try:
            files[p] = open(os.path.join(ROOT, p), encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            pass
    hits = secret_hits(files)
    check(hits == {}, "no tracked file outside personal/ holds a RetroAchievements token or password (%s)" % hits)
    fake = "Ab12Cd34" + "Ef56Gh78"   # built at run time: a literal here would trip gitleaks (make secrets)
    planted = {"ansible/x.yml": 'cheevos_token = "%s"\n' % fake, "a/emu.cfg": "[achievements]\nToken = %s\n" % fake[:12],
               "ok.yml": "line: 'cheevos_token = \"{{ cheevos_token }}\"'\n", "personal/x.cfg": 'cheevos_token = "%s"\n' % fake}
    check(sorted(secret_hits(planted)) == ["a/emu.cfg", "ansible/x.yml"], "BROKEN case: planted literal tokens are caught (%s)" % sorted(secret_hits(planted)))


def test_shared_defaults_are_off():
    decl = open(os.path.join(ROOT, "config", "variables.d", "achievements.yml")).read()
    for name, want in (("hk_cheevos_enable", "false"), ("hk_cheevos_user", '""'), ("hk_cheevos_keychain", '""')):
        check(re.search(r"(?m)^  %s:\n(?:    .*\n)*?    default: %s\s*$" % (name, re.escape(want)), decl) is not None,
              "shared default %s = %s" % (name, want))
    check(not re.search(r"(?m)^  hk_\w*(token|password)\w*:", decl), "no token/password lever is declared")
    users = []
    for p in tracked():
        try:
            text = open(os.path.join(ROOT, p), errors="replace").read()
        except OSError:
            continue
        if re.search(r"(?m)^hk_cheevos_user:\s*\S", text) and not p.startswith("tools/tests/"):
            users.append(p)
    check(all(p.startswith("personal/") for p in users), "hk_cheevos_user is set only under personal/ (%s)" % users)


def test_token_tasks_hide_the_token():
    for p in ROLE_TASKS:
        text = open(os.path.join(ROOT, p)).read()
        check(re.search(r"cheevos_token|cheevos_web_key", text), "%s writes a RetroAchievements secret" % p)
        check(token_task_problems(text) == [], "%s: token tasks run with no_log, token not from config %s" % (p, token_task_problems(text)))
    t = open(os.path.join(ROOT, ROLE_TASKS[0])).read()
    check(token_task_problems(t.replace("  no_log: true\n", "")) != [], "BROKEN case: a token task without no_log is flagged")
    check(token_task_problems(t.replace("{{ cheevos_token }}", "{{ hk_cheevos_token }}")) != [], "BROKEN case: a token from config is flagged")
    deploy = open(os.path.join(ROOT, "ansible", "bin", "deploy")).read()
    check('v["cheevos_token"] = os.environ["COIN_DOJO_CHEEVOS_TOKEN"]' in deploy and "set -x" not in deploy,
          "bin/deploy passes the token through the environment into the vars file (never argv)")


def test_flycast_keys_follow_the_levers():
    d = open(os.path.join(ROOT, "ansible", "roles", "dreamcast", "defaults", "main.yml")).read()
    for key, var in (("Enabled", "dreamcast_cheevos_enable"), ("HardcoreMode", "dreamcast_cheevos_hardcore")):
        check(re.search(r"section: achievements, key: %s, value: .*%s" % (key, var), d) is not None, "flycast [achievements] %s follows %s" % (key, var))
    for var, hk in (("dreamcast_cheevos_enable", "hk_cheevos_enable"), ("dreamcast_cheevos_user", "hk_cheevos_user"),
                    ("dreamcast_cheevos_hardcore", "hk_cheevos_hardcore")):
        check('%s: "{{ %s }}"' % (var, hk) in d, "%s is the %s pass-through" % (var, hk))


if __name__ == "__main__":
    main(globals())
