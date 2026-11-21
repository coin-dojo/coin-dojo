#!/usr/bin/env python3
"""Tests for tools/config.py and mk/config.mk (ticket #118, C1 of #103): declared variables, targets, local, cli.

Run: python3 tools/tests/test_config.py   (needs PyYAML: re-runs itself with ~/.venvs/coin-dojo/bin/python, else SKIPs)
Broken cases (each must FAIL the check, naming the problem): missing required host, unknown key (with a did-you-mean
hint), wrong type, bool for int, out of range, bad enum, a bad declaration (no description, no consumers, duplicate
name, duplicate export, unknown declaration key), a reference cycle, a command-line unknown variable, a stale
docs/CONFIG.md, `make config-check` on a broken local file.
Also: precedence default < target < local < cli, the VM render never carries the cabinet host, docs regenerate with no diff.
"""
import json
import os
import re
import subprocess
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
    skip("PyYAML is not installed (make config-venv); tools/config.py is not tested")
    print("RESULT: PASS (1 skipped)")
    sys.exit(0)

sys.path.insert(0, os.path.join(ROOT, "tools"))
import config as C  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_no_local_values as nlv  # noqa: E402  (the maintainer's values: config/edition/private.txt, #240)

EXAMPLE = os.path.join(ROOT, "config", "local.example.yml")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


def fake_root(variables, target_vm="", area="demo"):
    """A throwaway repo root with one area file and an optional vm target."""
    d = tempfile.mkdtemp(prefix="hk-config-test-")
    write(os.path.join(d, "config", "variables.d", area + ".yml"), "area: %s\nvariables:\n%s" % (area, variables))
    if target_vm:
        write(os.path.join(d, "config", "targets", "vm.yml"), target_vm)
    return d


def local(root, text):
    p = os.path.join(tempfile.mkdtemp(prefix="hk-config-local-"), "local.yml")
    write(p, text)
    return p


def problems(root, local_text=None, cli=None, limit="coin-dojo"):
    try:
        cfg = C.Config(limit=limit, root=root, local=local(root, local_text) if local_text is not None else None,
                       use_local=False, cli=cli)
        return cfg.problems
    except C.ConfigError as exc:
        return exc.problems


def has(ps, *words):
    return any(all(w in p for w in words) for p in ps)


DEMO = """  hk_demo_n:
    type: int
    default: 5
    min: 1
    max: 10
    description: A number.
    consumers: [x]
  hk_demo_mode:
    type: enum
    allowed: [fast, slow]
    default: fast
    description: A mode.
    consumers: [x]
  hk_demo_flag:
    type: bool
    default: true
    description: A flag.
    consumers: [x]
  hk_demo_host:
    type: str
    required: true
    targets: [cabinet]
    description: Needed.
    consumers: [x]
"""


# --- the shipped declarations ---------------------------------------------------------------------------------


def test_shipped_declarations_load():
    decls = C.load_declarations(ROOT)
    check(len(decls) >= 15, "config/variables.d declares the host, paths and guard variables (%d)" % len(decls))
    check(all(n.startswith("hk_") for n in decls), "every variable name has the hk_ prefix")
    for area in ("host", "paths", "guard"):
        check(any(d["_area"] == area for d in decls.values()), "area %s is declared" % area)
    cfg = C.Config(limit="coin-dojo", root=ROOT, local=EXAMPLE)
    check(cfg.problems == [], "the example local file validates: %s" % cfg.problems)
    check(cfg.values["hk_cabinet_host"] == "arcade.local", "the example host is the neutral arcade.local")


def test_defaults_are_neutral():
    text = ""
    for f in sorted(os.listdir(os.path.join(ROOT, "config", "variables.d"))):
        text += open(os.path.join(ROOT, "config", "variables.d", f)).read()
    for bad in ("192.168.", "1001"):
        check(bad not in text, "variables.d carries no maintainer-specific value %r" % bad)
    hits = [rule for rule, rx in nlv.PATTERNS if rx.search(text)]
    check(not hits, "variables.d carries no value of the private list: %s" % hits)


# --- broken cases ---------------------------------------------------------------------------------------------


def test_missing_required_host():
    ps = problems(ROOT, "")
    check(has(ps, "missing required hk_cabinet_host"), "no local file: missing required host FAILs: %s" % ps[:1])
    check(has(ps, "config/local.example.yml"), "the message names the example file")
    check(not has(problems(ROOT, "hk_cabinet_host: x.lan\n"), "missing"), "with the host set it passes")


def test_unknown_key_did_you_mean():
    ps = problems(ROOT, "hk_cabinet_hots: x.lan\nhk_cabinet_host: y.lan\n")
    check(has(ps, "unknown variable hk_cabinet_hots", "did you mean hk_cabinet_host"), "typo gets a did-you-mean: %s" % ps)
    ps = problems(ROOT, "hk_cabinet_host: y.lan\nhk_zzzzzz: 1\n")
    check(has(ps, "unknown variable hk_zzzzzz") and not has(ps, "did you mean"), "no hint when nothing is close")


def test_wrong_type_and_range():
    base = "hk_cabinet_host: y.lan\n"
    check(has(problems(ROOT, base + 'hk_ssh_port: "22"\n'), "hk_ssh_port", "whole number"), "a string for an int FAILs")
    check(has(problems(ROOT, base + "hk_ssh_port: true\n"), "hk_ssh_port", "whole number"), "a bool for an int FAILs")
    check(has(problems(ROOT, base + "hk_ssh_port: 70000\n"), "above the maximum 65535"), "above max FAILs")
    check(has(problems(ROOT, base + "hk_ssh_port: 0\n"), "below the minimum 1"), "below min FAILs")
    check(has(problems(ROOT, base + "hk_game_procs: retroarch\n"), "hk_game_procs", "must be a list"), "a str for a list FAILs")
    check(has(problems(ROOT, base + "hk_game_procs: [a, 3]\n"), "item 1"), "a bad list item FAILs")
    check(has(problems(ROOT, base + "hk_cabinet_user: 'Bad User'\n"), "does not match the pattern"), "a pattern miss FAILs")
    check(problems(ROOT, base + "hk_ssh_port: 2222\n") == [], "a valid override passes")


def test_enum_bool_and_float():
    r = fake_root("""  hk_demo_x:
    type: float
    default: 0.5
    min: 0
    max: 1
    description: d
    consumers: [x]
""" + DEMO)
    check(has(problems(r, "hk_demo_mode: fats\nhk_demo_host: h\n"), "not one of", "did you mean 'fast'"), "bad enum FAILs with a hint")
    check(has(problems(r, "hk_demo_flag: yes please\nhk_demo_host: h\n"), "true or false"), "a non-bool for bool FAILs")
    check(has(problems(r, "hk_demo_x: 1.5\nhk_demo_host: h\n"), "above the maximum"), "float range FAILs")
    check(problems(r, "hk_demo_x: 1\nhk_demo_host: h\n") == [], "an int is a valid float")


def test_list_of_enum_items():
    r = fake_root("""  hk_demo_cores:
    type: list
    items: enum
    allowed: [alpha, beta]
    default: [alpha]
    description: d
    consumers: [x]
""" + DEMO)
    check(problems(r, "hk_demo_cores: [alpha, beta]\nhk_demo_host: h\n") == [], "a list of enum items accepts allowed names")
    check(problems(r, "hk_demo_cores: []\nhk_demo_host: h\n") == [], "an empty list of enum items is valid")
    check(has(problems(r, "hk_demo_cores: [alpha, gamma]\nhk_demo_host: h\n"), "item 1", "not one of"), "a list item outside allowed FAILs")
    check(has(problems(r, "hk_demo_cores: [alpah]\nhk_demo_host: h\n"), "did you mean 'alpha'"), "a mistyped list item FAILs with a hint")
    bad = fake_root("""  hk_demo_cores:
    type: list
    items: enum
    default: []
    description: d
    consumers: [x]
""")
    check(any("allowed" in x for x in problems(bad, "")), "BROKEN: a list of enum items without allowed FAILs at load")


def test_bad_declarations():
    def decl_problems(vars_text, area="demo"):
        try:
            C.load_declarations(fake_root(vars_text, area=area))
            return []
        except C.ConfigError as exc:
            return exc.problems
    check(has(decl_problems("  hk_a:\n    type: int\n    default: 1\n    consumers: [x]\n"), "description is required"), "no description FAILs")
    check(has(decl_problems("  hk_a:\n    type: int\n    default: 1\n    description: d\n"), "consumers"), "no consumers FAILs")
    check(has(decl_problems("  hk_a:\n    type: int\n    description: d\n    consumers: [x]\n"), "needs a default"), "no default FAILs")
    check(has(decl_problems("  hk_a:\n    type: color\n    default: 1\n    description: d\n    consumers: [x]\n"), "type must be"), "bad type FAILs")
    check(has(decl_problems("  a_name:\n    type: int\n    default: 1\n    description: d\n    consumers: [x]\n"), "hk_"), "no hk_ prefix FAILs")
    check(has(decl_problems("  hk_a:\n    type: int\n    default: 1\n    descripton: d\n    description: d\n    consumers: [x]\n"),
              "unknown declaration key 'descripton'", "did you mean"), "a typo in a declaration key gets a hint")
    check(has(decl_problems("  hk_a:\n    type: enum\n    default: 1\n    description: d\n    consumers: [x]\n"), "needs allowed"), "enum without allowed FAILs")
    a = "  hk_a:\n    type: int\n    default: 1\n    description: d\n    consumers: [x]\n    exports: [dup_name]\n"
    r = fake_root(a)
    write(os.path.join(r, "config", "variables.d", "other.yml"),
          "area: other\nvariables:\n" + a.replace("hk_a", "hk_b"))
    try:
        C.load_declarations(r)
        got = []
    except C.ConfigError as exc:
        got = exc.problems
    check(has(got, "exports dup_name"), "two variables exporting one Ansible name FAILs")
    write(os.path.join(r, "config", "variables.d", "other.yml"), "area: other\nvariables:\n" + a)
    try:
        C.load_declarations(r)
        got = []
    except C.ConfigError as exc:
        got = exc.problems
    check(has(got, "declared twice"), "the same name in two areas FAILs")
    r = fake_root(a)
    write(os.path.join(r, "config", "variables.d", "demo.yml"), "area: wrong\nvariables: {}\n")
    try:
        C.load_declarations(r)
        got = []
    except C.ConfigError as exc:
        got = exc.problems
    check(has(got, "`area:` must be 'demo'"), "area must match the file name")


def test_cycle_and_reference():
    r = fake_root("""  hk_a:
    type: str
    default: "{{ hk_b }}/x"
    description: d
    consumers: [x]
  hk_b:
    type: str
    default: "{{ hk_a }}/y"
    description: d
    consumers: [x]
  hk_c:
    type: str
    default: "{{ hk_nope }}"
    description: d
    consumers: [x]
""")
    ps = problems(r)
    check(has(ps, "reference cycle"), "a reference cycle FAILs")
    check(has(ps, "hk_nope", "unknown or unset"), "a reference to a missing variable FAILs")


# --- precedence, targets, derived values ----------------------------------------------------------------------


def test_precedence():
    r = fake_root(DEMO.replace("    required: true\n", "    default: h0\n"), target_vm="hk_demo_n: 2\nhk_demo_mode: slow\n")
    one = lambda cfg, n: (cfg.values[n], cfg.source[n].split(" ")[0])  # noqa: E731
    d = C.Config("coin-dojo", r, local=local(r, "hk_demo_flag: false\n"), use_local=False)
    check(one(d, "hk_demo_n") == (5, "default"), "cabinet: the declared default")
    check(one(d, "hk_demo_flag") == (False, "local"), "local beats the default")
    v = C.Config("coin-dojo-vm", r, local=local(r, "hk_demo_flag: false\n"), use_local=False)
    check(one(v, "hk_demo_n") == (2, "target:vm"), "the vm target beats the default")
    v = C.Config("coin-dojo-vm", r, local=local(r, "hk_demo_n: 3\n"), use_local=False)
    check(one(v, "hk_demo_n") == (3, "local"), "local beats the target")
    v = C.Config("coin-dojo-vm", r, local=local(r, "hk_demo_n: 3\n"), use_local=False, cli={"hk_demo_n": 4})
    check(one(v, "hk_demo_n") == (4, "cli"), "cli beats local")
    ps = problems(r, "", cli={"hk_demo_nn": 4})
    check(has(ps, "unknown variable hk_demo_nn", "did you mean hk_demo_n"), "an unknown cli variable FAILs with a hint")
    ps = problems(r, "", cli={"hk_demo_n": 99})
    check(has(ps, "above the maximum", "cli"), "a cli value is range-checked, source named")
    ps = problems(r, "", cli={"hk_demo_host": "x"}, limit="coin-dojo-vm")
    check(has(ps, "not used by the vm target"), "a cabinet-only variable on the cli for the vm FAILs")


def test_aliases():
    """#240: a renamed variable keeps its old name as an alias: a local file that still sets it works; the new name
    wins when both are set; an alias that is a declared name, or two variables claiming one alias, FAIL."""
    ren = DEMO.replace("    required: true\n", "    default: h0\n").replace(
        "  hk_demo_n:\n", "  hk_demo_n:\n    aliases: [hk_demo_old]\n")
    r = fake_root(ren)
    d = C.Config("coin-dojo", r, local=local(r, "hk_demo_old: 7\n"), use_local=False)
    check(d.values["hk_demo_n"] == 7 and not d.problems, "the old name sets the new variable: %s" % d.problems)
    d = C.Config("coin-dojo", r, local=local(r, "hk_demo_n: 3\nhk_demo_old: 7\n"), use_local=False)
    check(d.values["hk_demo_n"] == 3, "the new name wins over the alias in the same file")
    check("hk_demo_old" not in d.values, "the alias is no variable of its own")
    check(has(problems(r, "hk_demo_old: 99\n"), "hk_demo_n", "above the maximum"), "an alias value is range-checked")
    for text, want in ((DEMO.replace("  hk_demo_n:\n", "  hk_demo_n:\n    aliases: [hk_demo_mode]\n"), "is itself"),
                       (DEMO.replace("  hk_demo_n:\n", "  hk_demo_n:\n    aliases: [hk_demo_x]\n").replace(
                           "  hk_demo_flag:\n", "  hk_demo_flag:\n    aliases: [hk_demo_x]\n"), "already an alias"),
                       (DEMO.replace("  hk_demo_n:\n", "  hk_demo_n:\n    aliases: old\n"), "aliases must be")):
        try:
            C.load_declarations(fake_root(text))
            got = []
        except C.ConfigError as exc:
            got = exc.problems
        check(has(got, want), "BROKEN: a bad alias FAILs (%s): %s" % (want, got))
    decls = C.load_declarations(ROOT)
    check("hk_iconic_image" in C.alias_of(decls) and C.alias_of(decls)["hk_iconic_image"] == "hk_stock_image",
          "#240: hk_iconic_image is the old name of hk_stock_image")


def test_derived_values():
    c = C.Config("coin-dojo", ROOT, local=local(tempfile.mkdtemp(), "hk_cabinet_host: h\nhk_cabinet_user: coordinator\n"), use_local=False)
    check(c.values["hk_cabinet_home"] == "/home/coordinator", "home follows the cabinet user: %s" % c.values["hk_cabinet_home"])
    check(c.values["hk_esde_home"] == "/home/coordinator/ES-DE", "ES-DE home follows the home")
    check(c.values["hk_ssh_user"] == "coordinator", "ssh user follows the cabinet user")
    e = c.effective()
    check(e["esde_user_home"] == "/home/coordinator" and e["cabinet_user"] == "coordinator", "legacy Ansible names carry the same values")
    c = C.Config("coin-dojo", ROOT, local=local(tempfile.mkdtemp(), "hk_cabinet_host: h\nhk_data_root: /mnt/d\n"), use_local=False)
    e = c.effective()
    check(e["esde_rom_root"] == "/mnt/d/roms" and e["coin_dojo_dir"] == "/mnt/d/coin-dojo" and e["dreamcast_bios_dir"] == "/mnt/d/bios/dc",
          "one data root moves every path under it")


def test_vm_render_never_has_the_cabinet():
    host = "10.99.88.77"
    t = tempfile.mkdtemp()
    c = C.Config("coin-dojo-vm", ROOT, local=local(t, "hk_cabinet_host: %s\nhk_cabinet_uid: 1234\n" % host), use_local=False)
    check(c.problems == [], "the VM validates without any cabinet host: %s" % c.problems)
    eff = json.dumps(c.effective())
    check(host not in eff, "the VM effective vars never contain the cabinet host")
    check("hk_cabinet_host" not in c.values, "hk_cabinet_host does not exist for the vm target")
    check(c.values["hk_vm_host"] == "host.lima.internal" and c.values["hk_vm_port"] == 60124, "the VM address is its own")
    check("StrictHostKeyChecking=no" in c.values["hk_ssh_common_args"], "the vm target overlay applies")
    cab = C.Config("coin-dojo", ROOT, local=local(t, "hk_cabinet_host: %s\n" % host), use_local=False)
    check("hk_vm_host" not in cab.values and "StrictHost" not in json.dumps(cab.effective()), "the cabinet render has no VM values")
    inv = cab.inventory()
    check("ansible_host: \"%s\"" % host in inv and "pios" in inv, "the cabinet inventory carries the configured host")
    check("coin-dojo-vm" in c.inventory() and "vm:" in c.inventory(), "the VM inventory carries the VM host")


def test_no_ip_in_committed_inventory():
    for f in ("hosts.yml", "vm.yml"):
        text = open(os.path.join(ROOT, "ansible", "inventory", f)).read()
        check(not re.search(r"192\.168\.[0-9]+\.[0-9]+", text), "ansible/inventory/%s has no cabinet address" % f)


def test_guard_cmd():
    c = C.Config("coin-dojo", ROOT, local=EXAMPLE)
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        C.main(["guard-cmd"], env={"HK_CONFIG": EXAMPLE})
    line = buf.getvalue().strip()
    run = "/run/user/1000/coin-dojo"
    check(line.startswith('pgrep -x retroarch; pgrep -x flycast; pgrep -f "retroarch-1.22/retroarc[h]"; ')
          and line.endswith("; true") and "grep -qsx fightcade %s/current-mode" % run in line
          and "'" not in line and "$" not in line,
          "guard-cmd = today's cabinet-guard line plus the #209 Fightcade-mode check, no quotes or $: %s" % line)
    d = tempfile.mkdtemp()
    probe = line.replace("pgrep", "false").replace(run, d)
    sh = lambda: subprocess.run(["sh", "-c", probe], capture_output=True, text=True)   # noqa: E731
    out = sh()
    check(out.returncode == 0 and out.stdout.strip() == "", "no session state: the guard line is idle and ends true")
    write(os.path.join(d, "current-mode"), "fightcade\n")
    write(os.path.join(d, "session.pid"), "999999\n")
    check(sh().stdout.strip() == "", "BROKEN case: a stale current-mode=fightcade (dead session pid) does not block")
    if not os.path.isdir("/proc/self"):
        skip("no /proc here: the live-session half of the guard runs on Linux only")
    else:
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "coin-dojo-session"])
        try:
            write(os.path.join(d, "session.pid"), "%d\n" % p.pid)
            check(sh().stdout.strip() == "session-in-fightcade-mode", "a live session in Fightcade mode blocks")
            write(os.path.join(d, "current-mode"), "esde\n")
            check(sh().stdout.strip() == "", "a live session in ES-DE mode does not block")
            write(os.path.join(d, "current-mode"), "fightcade\n")
            write(os.path.join(d, "session.pid"), "%d\n" % os.getpid())
            check(sh().stdout.strip() == "", "BROKEN case: session.pid naming another process does not block")
        finally:
            p.kill()
            p.wait()
    check(c.effective()["esde_game_procs"] == ["retroarch", "flycast"], "the guard list reaches Ansible as esde_game_procs")


# --- docs ---------------------------------------------------------------------------------------------------------


def test_docs_regenerate_without_diff():
    a, b = C.render_docs(ROOT), C.render_docs(ROOT)
    check(a == b, "docs rendering is deterministic")
    committed = open(os.path.join(ROOT, "docs", "CONFIG.md"), encoding="utf-8").read()
    check(a == committed, "docs/CONFIG.md equals a fresh `make config-docs` (run it and commit)")
    check("`hk_cabinet_host`" in a and "required" in a, "the docs list the required host")
    t = os.path.join(tempfile.mkdtemp(), "CONFIG.md")
    write(t, a + "stale\n")
    out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "config.py"), "docs", "--check", "--out", t],
                         capture_output=True, text=True)
    check(out.returncode == 1 and "out of date" in out.stdout, "a stale docs file FAILs `docs --check`")


# --- the CLI and make -------------------------------------------------------------------------------------------------


def run_cli(*args, env=None, cwd=ROOT):
    e = dict(os.environ)
    e.pop("HK_SET", None)
    e.update(env or {})
    return subprocess.run([sys.executable, os.path.join(ROOT, "tools", "config.py")] + list(args),
                          capture_output=True, text=True, env=e, cwd=cwd)


def test_bin_deploy_uses_the_config():
    """bin/deploy renders the effective vars + inventory and passes them to Ansible (stub DEPLOY_ANS records argv)."""
    d = tempfile.mkdtemp(prefix="hk-config-deploy-")
    stub = os.path.join(d, "ans")
    write(stub, '#!/bin/sh\necho "ARGS: $*" > "%s/calls"\necho "INV: $ANSIBLE_INVENTORY" >> "%s/calls"\n' % (d, d))
    os.chmod(stub, 0o755)
    e = dict(os.environ, DEPLOY_ANS=stub, ANS_CACHE=os.path.join(d, "cache"), HK_CONFIG=EXAMPLE,
             CONFIG_PYTHON=sys.executable, COIN_DOJO_BECOME_PASS="", PATH="/usr/bin:/bin")
    e.pop("HK_SET", None)
    r = subprocess.run([os.path.join(ROOT, "ansible", "bin", "deploy"), "--", "--check", "--limit", "coin-dojo"],
                       env=e, capture_output=True, text=True)
    calls = open(os.path.join(d, "calls")).read() if os.path.exists(os.path.join(d, "calls")) else ""
    check(r.returncode == 0 and "-e @%s/cache/config/effective-coin-dojo.json" % d in calls, "bin/deploy passes -e @effective: %s %s" % (calls, r.stderr))
    check("%s/cache/config/inventory-coin-dojo.yml" % d in calls, "bin/deploy points ANSIBLE_INVENTORY at the generated inventory")
    check(os.path.exists(os.path.join(d, "cache", "config", "inventory-coin-dojo.yml")), "the inventory file was rendered")
    e["HK_CONFIG"] = os.path.join(d, "missing.yml")
    r = subprocess.run([os.path.join(ROOT, "ansible", "bin", "deploy"), "--", "--check", "--limit", "coin-dojo"],
                       env=e, capture_output=True, text=True)
    check(r.returncode != 0 and "configuration is invalid" in r.stderr or "does not exist" in r.stderr, "bin/deploy stops on a broken config before Ansible")
    e["HK_CONFIG"] = EXAMPLE
    r = subprocess.run([os.path.join(ROOT, "ansible", "bin", "deploy"), "--", "--check", "--limit", "coin-dojo-vm"],
                       env=e, capture_output=True, text=True)
    calls = open(os.path.join(d, "calls")).read()
    check(r.returncode == 0 and "effective-coin-dojo-vm.json" in calls and "arcade.local" not in open(
        os.path.join(d, "cache", "config", "effective-coin-dojo-vm.json")).read(), "the VM run gets the VM render, without the cabinet host")


def test_cli_show_render_and_hk_set():
    r = run_cli("show", "hk_ssh_port", env={"HK_CONFIG": EXAMPLE})
    check(r.returncode == 0 and "hk_ssh_port = 22   [default]" in r.stdout, "show prints the value and its source")
    r = run_cli("show", "hk_ssh_port", env={"HK_CONFIG": EXAMPLE, "HK_SET": "hk_ssh_port=2222"})
    check("2222   [cli]" in r.stdout, "HK_SET (make's hk_x=v) wins and says cli")
    r = run_cli("check", env={"HK_CONFIG": EXAMPLE, "HK_SET": "hk_ssh_port=99999"})
    check(r.returncode == 1 and "RESULT: FAIL config-check" in r.stdout, "check FAILs on a bad cli value")
    out = tempfile.mkdtemp()
    r = run_cli("render", "--limit", "coin-dojo", "--out", out, env={"HK_CONFIG": EXAMPLE})
    check(r.returncode == 0, "render succeeds: %s" % r.stderr)
    eff = json.load(open(os.path.join(out, "effective-coin-dojo.json")))
    check(eff["hk_cabinet_host"] == "arcade.local" and eff["cabinet_user"] == "coindojo", "effective-coin-dojo.json has hk_* and the legacy names")
    bad = os.path.join(out, "bad.yml")
    write(bad, "hk_cabinet_host: h\nhk_cabinet_hots: 1\n")
    r = run_cli("render", "--limit", "coin-dojo", "--out", out + "/x", env={"HK_CONFIG": bad})
    check(r.returncode == 1 and not os.path.exists(out + "/x/effective-coin-dojo.json"), "render refuses an invalid config and writes nothing")
    r = run_cli("check", "--limit", "batocera", env={"HK_CONFIG": EXAMPLE})
    check(r.returncode == 1, "an unknown --limit FAILs")


def test_make_config_check():
    def mk(target, cfg, extra=()):
        e = dict(os.environ)
        e["HK_CONFIG"] = cfg
        e.pop("HK_SET", None)
        return subprocess.run(["make", "-s", "-C", ROOT, target, "CONFIG_PYTHON=" + sys.executable] + list(extra),
                              capture_output=True, text=True, env=e)
    r = mk("config-check", EXAMPLE)
    check(r.returncode == 0 and "config-check: OK" in r.stdout, "make config-check PASSes on the example: %s" % (r.stdout + r.stderr)[-200:])
    bad = os.path.join(tempfile.mkdtemp(), "bad.yml")
    write(bad, "hk_cabinet_host: h\nhk_ssh_port: lots\n")
    r = mk("config-check", bad)
    check(r.returncode != 0 and "RESULT: FAIL config-check" in r.stdout, "make config-check FAILs on a broken local file")
    r = mk("config-check", EXAMPLE, ["hk_ssh_port=99999"])
    check(r.returncode != 0 and "above the maximum" in r.stderr, "make hk_ssh_port=99999 reaches the loader and FAILs")
    r = mk("config", EXAMPLE, ["hk_ssh_port=2222"])
    check("hk_ssh_port" in r.stdout and "2222   [cli]" in r.stdout, "make config shows the value and its source")
    r = mk("deploy", bad)
    check(r.returncode != 0 and "RESULT: FAIL config-check" in r.stdout, "make deploy stops at config-check on a broken config")
    r = mk("cabinet-guard", bad)
    check(r.returncode != 0 and "RESULT: FAIL config-check" in r.stdout, "make cabinet-guard stops at config-check too")


def test_broken_shell_and_template_values():
    """#148 (security review L2/L3): values land in shell commands and Ansible extra vars."""
    base = "hk_cabinet_host: arcade.local\n"
    check(not problems(ROOT, base), "a plain host passes: %s" % problems(ROOT, base))
    for text, what in (("hk_cabinet_host: 'arcade.local; rm -rf ~'\n", "hk_cabinet_host"),
                       ("hk_cabinet_host: '$(id)'\n", "hk_cabinet_host"),
                       (base + "hk_ssh_user: 'coindojo -oProxyCommand=x'\n", "hk_ssh_user"),
                       (base + "hk_game_procs: [retroarch, 'x\"; reboot']\n", "hk_game_procs"),
                       (base + "hk_game_patterns: ['a`id`']\n", "hk_game_patterns")):
        check(has(problems(ROOT, text), what), "BROKEN CASE: shell characters in %s FAIL" % what)
    for text in (base + "hk_theme_name: \"{{ lookup('pipe', 'id') }}\"\n",
                 base + "hk_swap: {mechanism: \"{% raw %}x{% endraw %}\"}\n",
                 base + "hk_crt_off_cores: [\"{{ x }}\"]\n"):
        check(has(problems(ROOT, text), "template"), "BROKEN CASE: a Jinja template in a value FAILs: %s" % text.split("\n")[1])
    check(not problems(ROOT, base + "hk_game_patterns: ['retroarch-1.22/retroarc[h]']\n"),
          "the bracket trick still passes")


if __name__ == "__main__":
    main(globals())
