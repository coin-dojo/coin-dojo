#!/usr/bin/env python3
"""Tests for the storage variables (ticket #129, C5 of #103): journald, diskhygiene, sshd_keyonly and the swap rows of
cabinet_tweaks read declared levers. The defaults equal today's live values, a bad size (hk_journal_max_use: 50X) FAILs
config-check, and a literal lever planted in a role's defaults FAILs the "no hidden knob" rule.

Run: python3 tools/tests/test_config_storage.py   (needs PyYAML: re-runs itself with ~/.venvs/coin-dojo/bin/python, else SKIPs)
"""
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
from hktest import check, main, skip  # noqa: E402

try:
    import yaml  # noqa: E402,F401  (only probes that PyYAML is installed)
except ImportError:
    venv = os.path.expanduser("~/.venvs/coin-dojo/bin/python")
    if os.path.exists(venv) and not os.environ.get("HK_TEST_CONFIG_REEXEC"):
        os.environ["HK_TEST_CONFIG_REEXEC"] = "1"
        os.execv(venv, [venv] + sys.argv)
    skip("PyYAML is not installed (make config-venv); the storage declarations are not tested")
    print("RESULT: PASS (1 skipped)")
    sys.exit(0)

sys.path.insert(0, os.path.join(ROOT, "tools"))
import config as C  # noqa: E402

HOST = "hk_cabinet_host: arcade.local\n"
ROLES = ("journald", "diskhygiene", "sshd_keyonly")


def cfg(local_text="", limit="coin-dojo"):
    p = os.path.join(tempfile.mkdtemp(prefix="hk-storage-"), "local.yml")
    with open(p, "w") as fh:
        fh.write(HOST + local_text)
    try:
        return C.Config(limit=limit, local=p, use_local=False)
    except C.ConfigError as exc:   # a failing check raises with the problems
        return exc


def has(local_text, *words, limit="coin-dojo"):
    return any(all(w in p for w in words) for p in cfg(local_text, limit).problems)


def defaults_text(role):
    with open(os.path.join(ROOT, "ansible", "roles", role, "defaults", "main.yml"), encoding="utf-8") as fh:
        return fh.read()


def rule_no_hidden_knob(text):
    """Every top-level default is `{{ hk_* }}` (a lever or derived from one) or marked `# internal` on its key line.
    A role-local value that is neither is a hidden knob."""
    problems = []
    for ln in text.splitlines():
        m = re.match(r"^(\w+):\s*(.*)$", ln)
        if not m:
            continue
        name, rest = m.groups()
        if "{{ hk_" in rest or re.search(r"#\s*internal\b", rest):
            continue
        problems.append("%s: a literal default that is not a lever (declare hk_* in config/variables.d, or mark # internal)" % name)
    return problems


def test_defaults_are_the_live_values():
    c = cfg()
    v = c.values
    check(not c.problems, "the shipped defaults pass config-check: %s" % c.problems)
    check(v["hk_journal_max_use"] == "50M" and v["hk_journal_keep_free"] == "500M" and v["hk_journal_retention"] == "1month",
          "journal: 50M cap, 500M free floor, 1 month (the live journald drop-in)")
    check(v["hk_swap"] == {"mechanism": "zram", "ram_multiplier": 1, "max_mib": 2048},
          "swap: zram, RAM x1, 2048 MiB (the live rpi-swap drop-in)")
    check(v["hk_purge_groups"] == ["toolchain"], "purge: only the toolchain group (the maintainer 2026-11-15)")
    check(v["hk_theme_keep"] == v["hk_theme_name"] == "coin-dojo", "the kept theme follows the active theme")
    check(v["hk_cabinet_hostname"] == "coindojo", "the host name sshd matches with is hk_cabinet_hostname (community default, #266)")


def test_vm_sees_the_same_storage_levers():
    v = cfg(limit="coin-dojo-vm").values
    check(v["hk_swap"]["mechanism"] == "zram" and v["hk_journal_max_use"] == "50M", "the VM gets the storage levers too")


def test_overrides():
    c = cfg("hk_swap: {max_mib: 1024}\nhk_journal_max_use: 100M\nhk_purge_groups: [toolchain, mkvtoolnix]\n")
    check(not c.problems, "valid overrides pass: %s" % c.problems)
    check(c.values["hk_swap"] == {"mechanism": "zram", "ram_multiplier": 1, "max_mib": 1024}, "one swap key set, the rest kept")
    check(c.values["hk_purge_groups"] == ["toolchain", "mkvtoolnix"], "purge list override")


def test_broken_values_fail_config_check():
    check(has("hk_journal_max_use: 50X\n", "hk_journal_max_use"), "BROKEN: hk_journal_max_use: 50X FAILs (size format)")
    check(has("hk_journal_max_use: 50\n", "hk_journal_max_use"), "BROKEN: a size without a unit FAILs")
    check(has("hk_journal_keep_free: lots\n", "hk_journal_keep_free"), "BROKEN: hk_journal_keep_free: lots FAILs")
    check(has("hk_journal_retention: forever\n", "hk_journal_retention"), "BROKEN: retention 'forever' FAILs")
    check(has("hk_swap: {mechanism: swapfile}\n", "mechanism"), "BROKEN: swap mechanism swapfile FAILs (not zram/zram+file/auto)")
    check(has("hk_swap: {max_mib: 0}\n", "max_mib"), "BROKEN: zram of 0 MiB FAILs")
    check(has("hk_swap: {ram_multiplier: -1}\n", "ram_multiplier"), "BROKEN: negative RAM multiplier FAILs")
    check(has("hk_swap: {size: 1}\n", "size"), "BROKEN: unknown swap key FAILs")
    check(has("hk_purge_groups: toolchain\n", "hk_purge_groups", "list"), "BROKEN: purge groups as a string FAILs")
    check(has("hk_theme_keep: ../x\n", "hk_theme_keep"), "BROKEN: a kept-theme name with a slash FAILs")
    check(has("hk_journal_max_use_mb: 50\n", "did you mean"), "BROKEN: unknown name gets a did-you-mean")


def test_roles_read_the_levers():
    for role in ROLES:
        t = defaults_text(role)
        check(rule_no_hidden_knob(t) == [], "%s: every default is {{ hk_* }} or # internal %s" % (role, rule_no_hidden_knob(t)))
    j = defaults_text("journald")
    for var, lever in (("journald_system_max_use", "hk_journal_max_use"), ("journald_system_keep_free", "hk_journal_keep_free"),
                       ("journald_max_retention", "hk_journal_retention")):
        check(re.search(r'(?m)^%s: "\{\{ %s \}\}"\s*$' % (var, lever), j) is not None, "%s reads %s" % (var, lever))
    d = defaults_text("diskhygiene")
    check('diskhygiene_purge: "{{ hk_purge_groups }}"' in d and 'diskhygiene_theme_keep: "{{ hk_theme_keep }}"' in d,
          "diskhygiene reads hk_purge_groups and hk_theme_keep")
    s = defaults_text("sshd_keyonly")
    check("host={{ hk_cabinet_hostname }}" in s and '"{{ hk_cabinet_user }}"' in s,
          "sshd_keyonly reads the alias and the user from config")
    c = defaults_text("cabinet_tweaks")
    check(all(x in c for x in ("hk_swap.mechanism", "hk_swap.ram_multiplier", "hk_swap.max_mib")), "cabinet_tweaks swap rows read hk_swap")


def test_broken_planted_literal_fails_the_rule():
    for role, var, lever_line in (("journald", "journald_system_max_use", 'journald_system_max_use: "{{ hk_journal_max_use }}"'),
                                  ("diskhygiene", "diskhygiene_purge", 'diskhygiene_purge: "{{ hk_purge_groups }}"'),
                                  ("sshd_keyonly", "cabinet_user", 'cabinet_user: "{{ hk_cabinet_user }}"')):
        t = defaults_text(role)
        check(lever_line in t, "%s: the lever line is there to plant over" % role)
        planted = t.replace(lever_line, var + ": x")
        check(any(var in x for x in rule_no_hidden_knob(planted)), "BROKEN: a planted literal %s is flagged" % var)
    planted = defaults_text("journald") + "\njournald_new_cap: 10M\n"
    check(any("journald_new_cap" in x for x in rule_no_hidden_knob(planted)), "BROKEN: a new unmarked literal is flagged")


def test_every_referenced_lever_is_declared():
    names = set(cfg().values) | {"hk_repo_root"}   # hk_repo_root is set by ansible/bin/deploy, not declared
    for role in ROLES + ("cabinet_tweaks",):
        for ref in sorted(set(re.findall(r"\bhk_[a-z0-9_]+", defaults_text(role)))):
            check(ref in names, "%s references %s: declared" % (role, ref))


if __name__ == "__main__":
    main(globals())
