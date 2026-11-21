#!/usr/bin/env python3
"""Cabinet time zone (#206): hk_timezone applied by role settings, the zone picked in the app wins (cabinet wins, #189).

    python3 ansible/roles/settings/tests/test_settings_timezone.py     (PyYAML for the config part, else that part SKIPs)

  config    hk_timezone is declared, default Etc/UTC, pattern checked; the role default is a pass-through; no personal zone
            in any tracked file outside personal/ (the community edition ships Etc/UTC only)
  tasks     the state file is read as the cabinet user, never logged, by the shared filter; the wanted zone is the state's
            else hk_timezone; `timedatectl set-timezone` runs only when the zone differs (idempotent), argv list, no shell
  filter    hk_settings_state returns the "timezone" string only when it has a zone name's shape
Broken cases: no state file / unreadable -> hk_timezone; a path-traversal, shell, number, null, list, over-long or
option-shaped zone in the state file -> ignored (hk_timezone); a duplicated key -> whole file refused; a role that sets the
zone unconditionally, through a shell, or without the filter is flagged.
"""
import base64
import json
import os
import re
import subprocess
import sys

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANS = os.path.dirname(os.path.dirname(ROLE))
REPO = os.path.dirname(ANS)
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
sys.path.insert(0, os.path.join(ANS, "filter_plugins"))
from hktest import check, main, skip  # noqa: E402
import hk_settings_state as hk  # noqa: E402


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


TASKS = read(ROLE, "tasks", "main.yml")
DEFAULTS = read(ROLE, "defaults", "main.yml")


def slurp(raw):
    return {"content": base64.b64encode(raw).decode(), "encoding": "base64"}


def wanted(result, config="Etc/UTC"):
    """What the role's set_fact computes: (state | hk_settings_state).timezone | default(settings_timezone)."""
    return hk.hk_settings_state(result).get("timezone", config)


def tz_block(tasks):
    a = tasks.index("# #206 TIME ZONE")
    b = tasks.index("# #195: SETTINGS on the carousel")
    return tasks[a:b]


def tz_problems(tasks):
    p, blk = [], tz_block(tasks)
    if "ansible.builtin.slurp" not in blk or "become_user: \"{{ settings_user }}\"" not in blk or "no_log: true" not in blk \
            or "failed_when: false" not in blk:
        p.append("state file is not slurped as the cabinet user, no_log, failed_when: false")
    if "| hk_settings_state" not in blk or "else settings_timezone" not in blk:
        p.append("wanted zone is not (state via the filter) else settings_timezone")
    if "argv: [timedatectl, list-timezones]" not in blk or "settings_tz_state in settings_tz_list.stdout_lines" not in blk:
        p.append("the state zone is not checked against timedatectl list-timezones before it is used")
    if not re.search(r"when: settings_tz_state \| length > 0 and settings_tz_state not in settings_tz_list.stdout_lines", blk) \
            or "WARN" not in blk:
        p.append("no WARN when the state zone is not on the list")
    m = re.search(r"argv: \[timedatectl, set-timezone, --, \"\{\{ settings_tz_wanted \}\}\"\]", blk)
    if not m:
        p.append("set-timezone is not an argv list with `--` and the wanted zone only")
    if re.search(r"ansible\.builtin\.(shell|raw)|cmd:", blk):
        p.append("a shell in the time zone tasks")
    setter = blk[blk.find("Time zone set"):]
    # #244: an image build sets /etc/localtime instead, so the guard may follow `not coin_dojo_image ... and`
    if not re.search(r"when: (not coin_dojo_image \| default\(false\) \| bool and )?settings_tz_now\.stdout \| trim != settings_tz_wanted", setter):
        p.append("set-timezone is not guarded by 'differs from now' (not idempotent)")
    if "check_mode: false" in setter.split("when:")[0]:
        p.append("the setter runs in a dry run")
    return p


def test_tasks():
    probs = tz_problems(TASKS)
    check(not probs, "time zone tasks: cabinet user read, filter, idempotent guard, argv %s" % (probs or ""))


def test_BROKEN_tasks():
    blk = tz_block(TASKS)
    for bad, what in ((re.sub(r"      when: (not coin_dojo_image \| default\(false\) \| bool and )?settings_tz_now\.stdout \| trim "
                              r"!= settings_tz_wanted\n", "", blk, count=1), "unconditional set"),
                      (blk.replace("argv: [timedatectl, set-timezone, --, \"{{ settings_tz_wanted }}\"]",
                                   "cmd: timedatectl set-timezone {{ settings_tz_wanted }}"), "shell string"),
                      (blk.replace("| hk_settings_state", "| from_json"), "no shared filter"),
                      (blk.replace("settings_tz_state if settings_tz_state in settings_tz_list.stdout_lines else settings_timezone",
                                   "settings_tz_state | default(settings_timezone, true)"), "state zone used without the list check (Foo/Bar breaks every deploy)"),
                      (blk.replace("set-timezone, --,", "set-timezone,"), "no `--` before the zone"),
                      (blk.replace("      become_user: \"{{ settings_user }}\"\n", "", 1), "read as root"),
                      (blk.replace("      no_log: true\n", "", 1), "state logged")):
        check(tz_problems(TASKS.replace(blk, bad)) != [], "BROKEN CASE: %s is flagged" % what)


def test_role_default_is_pass_through():
    check(re.search(r'(?m)^settings_timezone: "\{\{ hk_timezone \}\}"', DEFAULTS) is not None, "settings_timezone = hk_timezone")
    check(not re.search(r"America/|Europe/|Asia/", DEFAULTS.replace("# ", "#")), "no literal zone in the role defaults")


def test_cabinet_wins_and_falls_back_to_config():
    check(wanted(slurp(b'{"timezone": "America/New_York"}'), "Etc/UTC") == "America/New_York", "state zone wins over hk_timezone")
    check(wanted(slurp(b'{"benchmark_mode": true}')) == "Etc/UTC", "state without a zone -> hk_timezone")
    check(wanted({"failed": True, "msg": "not found"}, "Europe/Paris") == "Europe/Paris", "BROKEN CASE: no state file -> hk_timezone")
    check(wanted({}, "Europe/Paris") == "Europe/Paris", "empty slurp result -> hk_timezone")
    check(wanted(slurp(b'{"timezone": "Asia/Tokyo", "timezone": "Europe/Paris"}'), "Etc/UTC") == "Etc/UTC",
          "BROKEN CASE: duplicated key -> whole file refused -> hk_timezone")


def test_BROKEN_hostile_state_values_are_ignored():
    for v in ("../../etc/passwd", "America/../x", "/etc/localtime", "Europe/London; reboot", "$(id)", "Europe/London\n",
              "a" * 65, "--help", "", "A/B/C/D", "Europe/London Asia/Tokyo", 5, None, ["Europe/London"], True, {"a": "b"}):
        got = wanted(slurp(json.dumps({"timezone": v}).encode()), "Etc/UTC")
        check(got == "Etc/UTC", "BROKEN CASE: state timezone %r is ignored, hk_timezone stays (%r)" % (str(v)[:20], got))
    check(wanted(slurp(b"\xff\xfe"), "Etc/UTC") == "Etc/UTC", "BROKEN CASE: not UTF-8 -> hk_timezone")
    check(wanted(slurp(b'{"timezone": "Etc/GMT+5"}')) == "Etc/GMT+5", "a real zone with '+' is accepted")
    check(wanted(slurp(b'{"timezone": "America/Argentina/Buenos_Aires"}')) == "America/Argentina/Buenos_Aires", "three parts ok")


def test_filter_regex_equals_the_apps():
    sys.path.insert(0, os.path.join(REPO, "skills", "settings"))
    import settings_core as core
    check(hk.TZ_RE.pattern == core.TZ_RE.pattern, "the filter and the app check the same zone shape")


def test_config_declares_hk_timezone():
    try:
        import yaml
    except ImportError:
        venv = os.path.expanduser("~/.venvs/coin-dojo/bin/python")
        if os.path.exists(venv) and not os.environ.get("HK_TEST_TZ_REEXEC"):
            r = subprocess.run([venv, os.path.abspath(__file__)], env=dict(os.environ, HK_TEST_TZ_REEXEC="1"))
            check(r.returncode == 0, "re-run with the coin-dojo venv")
            return
        skip("PyYAML is not installed (make config-venv)")
        return
    doc = yaml.safe_load(read(REPO, "config", "variables.d", "settings.yml"))
    v = doc["variables"]["hk_timezone"]
    check(v["default"] == "Etc/UTC" and v["type"] == "str", "hk_timezone: str, default Etc/UTC (the community edition)")
    rx = re.compile(v["pattern"])
    check(all(rx.fullmatch(z) for z in ("Etc/UTC", "America/New_York", "America/Argentina/Buenos_Aires", "Etc/GMT+5")),
          "pattern accepts real zones")
    check(not any(rx.fullmatch(z) for z in ("../etc/passwd", "/etc/passwd", "a b", "x;y", "", "A/B/C/D", "Europe/London\n")),
          "BROKEN CASE: pattern refuses traversal, paths, spaces, shell, empty")
    check("ansible/roles/settings" in v["consumers"], "consumer is the settings role")


def test_no_personal_zone_outside_personal():
    out = subprocess.run(["git", "-C", REPO, "grep", "-l", "-E", r"hk_timezone:\s*\S*(New_York|Chicago|Denver|Los_Angeles)", "--",
                          ".", ":(exclude)personal", ":(exclude)*/tests/*"], capture_output=True, text=True).stdout.split()
    check(out == [], "no tracked file outside personal/ sets a personal hk_timezone %s" % out)


if __name__ == "__main__":
    main(globals())
