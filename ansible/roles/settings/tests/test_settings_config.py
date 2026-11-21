#!/usr/bin/env python3
"""The SETTINGS role and app read declared variables (ticket #128, C4b of #103).

Run: python3 ansible/roles/settings/tests/test_settings_config.py   (needs PyYAML: re-runs itself with ~/.venvs/coin-dojo)
Checks: config/variables.d/settings.yml declares the levers with today's live values; the role defaults are pass-through
({{ hk_* }}) or marked `# internal` ("no hidden knob"); skills/settings/Makefile reads the hostname from config and
writes under HK_CACHE.
Broken cases (each must FAIL): an invalid hostname (`bad_name!`, 64 characters, capitals, leading '-') in config-check
(via the loader and via the command), a planted literal lever in the settings defaults, a pass-through naming an
undeclared variable.
"""
import os
import re
import subprocess
import sys
import tempfile

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(ROLE)))
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
from hktest import check, main, skip  # noqa: E402

try:
    import yaml  # noqa: E402,F401
except ImportError:
    venv = os.path.expanduser("~/.venvs/coin-dojo/bin/python")
    if os.path.exists(venv) and not os.environ.get("HK_TEST_CONFIG_REEXEC"):
        os.environ["HK_TEST_CONFIG_REEXEC"] = "1"
        os.execv(venv, [venv] + sys.argv)
    skip("PyYAML is not installed (make config-venv); the settings variables are not tested")
    print("RESULT: PASS (1 skipped)")
    sys.exit(0)

sys.path.insert(0, os.path.join(REPO, "tools"))
import config as C  # noqa: E402

DEFAULTS = open(os.path.join(ROLE, "defaults", "main.yml"), encoding="utf-8").read()
MAKEFILE = open(os.path.join(REPO, "skills", "settings", "Makefile"), encoding="utf-8").read()
EXAMPLE = os.path.join(REPO, "config", "local.example.yml")


def local(text):
    p = os.path.join(tempfile.mkdtemp(prefix="hk-settings-local-"), "local.yml")
    with open(p, "w") as fh:
        fh.write("hk_cabinet_host: arcade.local\n" + text)
    return p


def problems(text):
    try:
        return C.Config(limit="coin-dojo", root=REPO, local=local(text), use_local=False).problems
    except C.ConfigError as exc:
        return exc.problems


def hidden_knobs(defaults):
    """Top-level defaults that are neither `{{ hk_* }}` pass-throughs nor marked `# internal` (same line or the line above)."""
    bad, prev = [], ""
    for line in defaults.splitlines():
        m = re.match(r"^([a-z_0-9]+):\s*(.*)$", line)
        if m and "hk_" not in m.group(2) and "# internal" not in line and not prev.startswith("# internal"):
            bad.append(m.group(1))
        prev = line
    return bad


def passthrough_names(defaults):
    return sorted(set(re.findall(r"\{\{\s*(hk_[a-z0-9_]+)", defaults)))


def test_declarations():
    decls = C.load_declarations(REPO)
    for n in ("hk_cabinet_hostname", "hk_settings_helper_dest", "hk_settings_nmcli", "hk_settings_polkit"):
        check(n in decls and decls[n]["_area"] == "settings", "%s is declared in config/variables.d/settings.yml" % n)
    cfg = C.Config(limit="coin-dojo", root=REPO, local=EXAMPLE, use_local=False)
    v = cfg.values
    check(v["hk_cabinet_hostname"] == "coindojo", "hostname default is the community `coindojo` (#266)")
    check(v["hk_settings_helper_dest"] == "/usr/local/sbin/coin-dojo-set-hostname", "helper default = today's path")
    check(v["hk_settings_nmcli"] == "nmcli" and v["hk_settings_polkit"] is True, "nmcli + polkit defaults = today's values")
    ex = cfg.exports()
    check((ex["settings_user"], ex["settings_group"]) == ("coindojo", "coindojo"), "settings_user/group = the cabinet user")
    check(ex["settings_dir"] == "/data/coin-dojo/settings", "settings_dir = <hk_tools_dir>/settings")
    check(ex["settings_esde_home"] == "/home/%s/ES-DE" % ex["settings_user"] and ex["settings_carousel_root"] == "/data/nested",
          "ES-DE home + nested root come from the paths area")
    undeclared = [n for n in passthrough_names(DEFAULTS) if n not in decls and n != "hk_repo_root"]
    check(not undeclared, "every hk_* the role defaults read is declared %s" % undeclared)
    vm = C.Config(limit="coin-dojo-vm", root=REPO, local=EXAMPLE, use_local=False)
    check(vm.values["hk_cabinet_hostname"] == "coindojo", "the VM sees the hostname too")


def test_no_hidden_knob():
    check(hidden_knobs(DEFAULTS) == [], "settings defaults: every non-internal default is {{ hk_* }} %s" % hidden_knobs(DEFAULTS))
    planted = DEFAULTS.replace('settings_nmcli: "{{ hk_settings_nmcli }}"', "settings_nmcli: nmcli")
    check(hidden_knobs(planted) == ["settings_nmcli"], "BROKEN: a planted literal nmcli lever is flagged")
    planted = DEFAULTS.replace('settings_user: "{{ hk_cabinet_user }}"', "settings_user: coindojo")
    check(hidden_knobs(planted) == ["settings_user"], "BROKEN: a planted literal user is flagged")
    planted = DEFAULTS + "settings_timeout: 45\n"
    check(hidden_knobs(planted) == ["settings_timeout"], "BROKEN: a new unmarked literal is flagged")
    check(hidden_knobs(DEFAULTS + "# internal: not a lever\nsettings_x: 1\n") == [], "an `# internal` line above is accepted")


def test_hostname_checks():
    check(problems("hk_cabinet_hostname: coin-dojo1\n") == [], "coin-dojo1 (the maintainer's live name) validates")
    check(problems("hk_cabinet_hostname: a\n") == [], "a one-character name validates")
    check(problems("hk_cabinet_hostname: %s\n" % ("a" * 63)) == [], "63 characters validate")
    for bad, what in (("bad_name!", "bad_name!"), ("a" * 64, "64 characters"), ("MyCab", "capitals"),
                      ("-coin-dojo", "leading dash"), ("coin-dojo-", "trailing dash"), ("", "empty"),
                      ("had_ouken", "underscore")):
        ps = problems('hk_cabinet_hostname: "%s"\n' % bad)
        check(any("hk_cabinet_hostname" in p for p in ps), "BROKEN: hostname %s is refused by config-check" % what)
    ps = problems("hk_cabinet_hostnam: x\n")
    check(any("hk_cabinet_hostname" in p for p in ps), "BROKEN: a mistyped name gets a did-you-mean hint")


def test_config_check_command():
    env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "F", "A", "BASE", "HK_SET")}
    for text, want in (("hk_cabinet_hostname: bad_name!\n", 1), ("hk_cabinet_hostname: coin-dojo1\n", 0)):
        p = local(text)
        r = subprocess.run([sys.executable, os.path.join(REPO, "tools", "config.py"), "check"], env=dict(env, HK_CONFIG=p),
                           capture_output=True, text=True, cwd=REPO)
        check(r.returncode == want, "config.py check on %r exits %d (got %d)" % (text.strip(), want, r.returncode))
        if want:
            check("hk_cabinet_hostname" in r.stdout + r.stderr, "BROKEN: the message names hk_cabinet_hostname")


def test_makefile():
    check("hk_cabinet_hostname" in MAKEFILE and "--hostname \"$(HOSTNAME_SHOWN)\"" in MAKEFILE,
          "preview shows hk_cabinet_hostname")
    check("--hostname coin-dojo " not in MAKEFILE, "no literal hostname in the preview recipe")
    check("$(HK_CACHE)/settings/" in MAKEFILE and "/tmp/coin-dojo" not in MAKEFILE, "preview output lives under HK_CACHE, not /tmp")


if __name__ == "__main__":
    main(globals())
