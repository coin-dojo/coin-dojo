#!/usr/bin/env python3
"""Tests for the Makefiles and the shared mk/ library (ticket #90).

Run: python3 mk/tests/test_make.py   (stdlib only; needs GNU make 3.81+ on PATH)

Checks: every area Makefile exists, is listed in the top-level SUBDIRS and includes the mk/*.mk it
uses; `make help` works in each folder and lists targets + flags; bad ARCH/EDITION/APPLY values
stop make; deploy is a --check dry run by default with coin_dojo_arch/coin_dojo_edition extra vars;
the community edition lists no personal/** file; cabinet-guard / esde-restart behave (against a
stub ssh, never the cabinet); `make test` runs every tracked test file exactly once.

Broken cases (must fail): EDITION=foo, ARCH=x86_64, APPLY=yes, an empty EDITION; a planted
Makefile that skips an include it needs; a planted test file with a FAIL line and one with exit 1
must turn py-test red; cabinet-guard must stop when the stub reports a running game.
"""
import atexit
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AREAS = ["ansible", "skills/collections", "skills/badges", "skills/controls",
         "skills/local-preview", "skills/build-image", "skills/settings", "brand", "build"]
FAILURES = []
SKIPS = []
# #193: the tests run against a fixture local config, not the developer's own one (a fresh clone or the community
# export has none, and the maintainer's personal/config/local.yml must not decide a verdict): the cabinet targets' config gate
# needs a host (ssh is always stubbed, it is never reached) and the help/deploy checks expect hk_edition personal.
FIX_DIR = tempfile.mkdtemp(prefix="hk-test-make-")
FIX_CONFIG = os.path.join(FIX_DIR, "local.yml")
with open(FIX_CONFIG, "w") as _f:
    _f.write("hk_cabinet_host: cabinet.test\nhk_edition: personal\n")
atexit.register(shutil.rmtree, FIX_DIR, True)

# Which include a Makefile needs when its text uses one of these names.
NEEDS = {
    "mk/flags.mk": None,   # always
    "mk/help.mk": None,    # always
    "mk/python.mk": re.compile(r"\bpy-test\b|\bPY_PIL\b|\bPY_TESTS\b"),
    "mk/cabinet.mk": re.compile(r"\bcabinet-guard\b|\bCABINET(_SSH|_SCP)?\b|\b(es|esde)-restart\b"),
    "mk/edition.mk": re.compile(r"\bedition-(check|files)\b|\bEDITION_(EXCLUDE|OVERLAYS|RSYNC_EXCLUDES)\b"),
}


def check(cond, msg):
    msg = " | ".join(str(msg).splitlines())   # one line: an embedded "RESULT: FAIL" from a sub-run must not start a line
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def env():
    """A clean environment: no flags or MAKEFLAGS inherited from a parent `make test`."""
    e = dict(os.environ, HK_CONFIG=FIX_CONFIG)
    for k in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "ARCH", "EDITION", "APPLY", "V",
              "TAGS", "BECOME", "W"):
        e.pop(k, None)
    return e


def make(*args, cwd=ROOT):
    return subprocess.run(["make", "--no-print-directory", *args], cwd=cwd, env=env(),
                          capture_output=True, text=True)


def missing_includes(text):
    """Return the mk/*.mk files a Makefile text uses but does not include."""
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    out = []
    for inc, pat in NEEDS.items():
        used = pat is None or pat.search(body)
        if used and ("$(REPO_ROOT)/" + inc) not in body:
            out.append(inc)
    return out


def test_make_available():
    r = subprocess.run(["make", "--version"], capture_output=True, text=True)
    check(r.returncode == 0 and "GNU Make" in r.stdout, "GNU make on PATH: " + r.stdout.split("\n")[0])


def test_makefiles_exist_and_listed():
    top = open(os.path.join(ROOT, "Makefile")).read()
    m = re.search(r"^SUBDIRS := (.*?)(?<!\\)$", top, re.M | re.S)
    subdirs = m.group(1).replace("\\\n", " ").split() if m else []
    check(sorted(subdirs) == sorted(AREAS), "top-level SUBDIRS == %s (got %s)" % (AREAS, subdirs))
    for d in AREAS:
        check(os.path.isfile(os.path.join(ROOT, d, "Makefile")), "%s/Makefile exists" % d)


def test_includes():
    for d in ["."] + AREAS:
        text = open(os.path.join(ROOT, d, "Makefile")).read()
        miss = missing_includes(text)
        check(not miss, "%s/Makefile includes what it uses (missing: %s)" % (d, miss or "none"))
        check("$(abspath $(dir $(lastword $(MAKEFILE_LIST))))" in text, "%s/Makefile sets HERE from MAKEFILE_LIST" % d)


def test_include_check_catches_missing_include():
    """Broken case: a Makefile using cabinet-guard without mk/cabinet.mk must be flagged."""
    bad = ("HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))\n"
           "include $(REPO_ROOT)/mk/flags.mk\ninclude $(REPO_ROOT)/mk/help.mk\n"
           "x: cabinet-guard\n\techo hi\n")
    check(missing_includes(bad) == ["mk/cabinet.mk"], "planted Makefile without mk/cabinet.mk is flagged")


def test_help_every_dir():
    for d in ["."] + AREAS:
        r = make("-C", d, "help")
        out = r.stdout
        check(r.returncode == 0, "%s: make help exits 0 %s" % (d, r.stderr.strip()))
        check("Targets:" in out and re.search(r"^  help\s", out, re.M) is not None, "%s: help lists targets" % d)
        check(re.search(r"^  test\s", out, re.M) is not None, "%s: help lists a test target" % d)
        for flag in ("ARCH=arm64", "EDITION=personal", "APPLY=0", "V=0"):
            check(flag in out, "%s: help shows %s" % (d, flag))
        r2 = make("-C", d)
        check(r2.returncode == 0 and "Targets:" in r2.stdout, "%s: plain `make` = help" % d)


def test_bad_flags_fail():
    for args, word in ((["EDITION=foo"], "EDITION='foo'"), (["ARCH=x86_64"], "ARCH='x86_64'"),
                       (["APPLY=yes"], "APPLY='yes'"), (["EDITION="], "EDITION=''"),
                       (["V=2"], "V='2'")):
        for d in (".", "skills/collections"):
            r = make("-C", d, "help", *args)
            check(r.returncode != 0 and word in r.stderr and "not valid" in r.stderr,
                  "%s: make help %s exits non-zero with a clear message" % (d, " ".join(args)))
    r = make("-C", "ansible", "help", "BECOME=yes")
    check(r.returncode != 0, "ansible: BECOME=yes fails")
    r = make("help", "EDITION=community")
    check(r.returncode == 0 and "EDITION=community" in r.stdout, "EDITION=community is accepted")


def deploy_lines(out):
    return [l for l in out.splitlines() if "bin/deploy" in l]


def test_deploy_dry_run_by_default():
    r = make("-n", "deploy")
    lines = deploy_lines(r.stdout)
    check(r.returncode == 0 and len(lines) == 1, "make -n deploy reaches bin/deploy once: %s" % r.stderr.strip())
    line = lines[0] if lines else ""
    check("--check --diff" in line, "deploy is --check --diff by default")
    check("-e coin_dojo_arch=arm64" in line and "-e coin_dojo_edition=personal" in line, "deploy passes the extra vars")
    check("cabinet-guard" not in r.stdout and "pgrep" not in r.stdout, "dry-run deploy does not need the cabinet guard")

    r = make("-n", "deploy", "APPLY=1", "TAGS=dreamcast cabinet_tweaks", "BECOME=1")
    line = (deploy_lines(r.stdout) or [""])[0]
    check("--check" not in line, "APPLY=1 drops --check")
    check("--tags dreamcast,cabinet_tweaks" in line and "--ask-become-pass" in line, "TAGS and BECOME reach bin/deploy")
    check("pgrep -x retroarch" in r.stdout, "APPLY=1 deploy runs cabinet-guard first")



def test_deploy_always_limited():
    """Every cabinet run must carry --limit (default coin-dojo)."""
    line = (deploy_lines(make("-n", "deploy", "TAGS=dreamcast").stdout) or [""])[0]
    check("--limit coin-dojo" in line, "make deploy passes --limit coin-dojo by default")
    r = make("-n", "deploy", "LIMIT=batocera")
    check(r.returncode != 0 and "LIMIT='batocera'" in r.stderr, "LIMIT=batocera is refused (the Batocera play is gone, #110)")
    r = make("-n", "deploy", "LIMIT=all")
    check(r.returncode != 0 and "LIMIT='all'" in r.stderr, "LIMIT=all is refused (broken case)")


def run_deploy(d, *args, **extra):
    stub, marker = stub_ssh(d, "")
    bindir = os.path.join(d, "bin")   # a `security` stub first on PATH: the real Keychain is never read
    os.makedirs(bindir, exist_ok=True)
    with open(os.path.join(bindir, "security"), "w") as f:
        f.write("#!/bin/sh\nexit 44\n")
    os.chmod(os.path.join(bindir, "security"), 0o755)
    e = env()
    # DEPLOY_NO_CONFIG: these tests are about the wrapper; the config wiring is tested in tools/tests/test_config.py.
    e.update({"DEPLOY_ANS": stub, "DEPLOY_NO_CONFIG": "1", "ANS_CACHE": os.path.join(d, "cache"),
              "PATH": bindir + os.pathsep + e.get("PATH", "/usr/bin:/bin")})
    e.update(extra)
    r = subprocess.run([os.path.join(ROOT, "ansible/bin/deploy"), *args], env=e, capture_output=True, text=True)
    return r, (open(marker).read() if os.path.exists(marker) else "")


def test_bin_deploy_limit_and_tempfile():
    with tempfile.TemporaryDirectory() as d:
        r, calls = run_deploy(d, "check", "dreamcast", COIN_DOJO_BECOME_PASS="not-a-real-password")
        check(r.returncode == 0 and "--check --diff --limit coin-dojo" in calls and "--tags dreamcast" in calls,
              "bin/deploy check: --check --diff --limit coin-dojo --tags: %s %s" % (calls.strip(), r.stderr.strip()))
        check("@" + os.path.join(d, "cache") in calls, "password vars file sits in the mounted cache dir")
        left = os.listdir(os.path.join(d, "cache")) if os.path.isdir(os.path.join(d, "cache")) else []
        check(left == [], "password temp file is removed after the run (was leaked under exec): %s" % left)
    with tempfile.TemporaryDirectory() as d:
        r, calls = run_deploy(d, "apply", COIN_DOJO_BECOME_PASS="")
        check(r.returncode == 0 and "--limit coin-dojo" in calls and "--check" not in calls,
              "bin/deploy apply: --limit coin-dojo, no --check, works without a password (bash 3.2 empty array): %s %s"
              % (calls.strip(), r.stderr.strip()))
    with tempfile.TemporaryDirectory() as d:
        r, calls = run_deploy(d, "ping", COIN_DOJO_BECOME_PASS="")
        check(calls.startswith("ansible coin-dojo -m ping"), "bin/deploy ping targets coin-dojo only: " + calls.strip())
    with tempfile.TemporaryDirectory() as d:
        r, calls = run_deploy(d, "check", DEPLOY_LIMIT="all", COIN_DOJO_BECOME_PASS="")
        check(r.returncode != 0 and calls == "", "DEPLOY_LIMIT=all is refused before Ansible runs (broken case)")


def test_ans_image_tag_is_pinned():
    r = subprocess.run([os.path.join(ROOT, "ansible/bin/ans"), "--image"], capture_output=True, text=True)
    check(re.fullmatch(r"coin-dojo-ansible:[0-9a-f]{12}\n", r.stdout) is not None, "bin/ans image tag = hash: " + r.stdout.strip())
    req = open(os.path.join(ROOT, "ansible/requirements.yml")).read()
    vers = re.findall(r'version:\s*"([^"]*)"', req)
    check(vers and all(re.fullmatch(r"\d+\.\d+\.\d+", v) for v in vers), "collections pinned to exact versions: %s" % vers)
    txt = open(os.path.join(ROOT, "ansible/requirements.txt")).read()
    check(re.search(r"^ansible-core==\d+\.\d+\.\d+$", txt, re.M) is not None, "ansible-core pinned exactly")


def test_ansible_syntax_catches_broken_playbook():
    """Broken case: a planted bad playbook must fail --syntax-check in the pinned container.
    Runs only when docker is up and the image is already built (a unit test never builds it)."""
    if shutil.which("docker") is None:
        print("SKIP ansible syntax broken case: no docker on this machine")
        SKIPS.append("ansible syntax")
        return
    ans = os.path.join(ROOT, "ansible/bin/ans")
    image = subprocess.run([ans, "--image"], capture_output=True, text=True).stdout.strip()
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode != 0:
        print("SKIP ansible syntax broken case: %s not built (make -C ansible ansible-image)" % image)
        SKIPS.append("ansible syntax")
        return
    cache = os.path.join(os.environ.get("HK_CACHE", os.path.expanduser("~/.cache/coin-dojo")), "ansible")   # mounted by bin/ans
    os.makedirs(cache, exist_ok=True)
    bad = os.path.join(cache, "broken-%d.yml" % os.getpid())
    try:
        open(bad, "w").write("- hosts: localhost\n  tasks:\n    - name: bad\n      no_such_module_xyz: {}\n")
        r = subprocess.run([ans, "ansible-playbook", bad, "--syntax-check"], capture_output=True, text=True,
                           cwd=os.path.join(ROOT, "ansible"))
        check(r.returncode != 0, "ansible --syntax-check FAILS on a planted bad playbook")
    finally:
        os.remove(bad)


def test_community_edition_has_no_personal():
    r = make("-n", "deploy", "EDITION=community")
    lines = deploy_lines(r.stdout)
    check(lines and "coin_dojo_edition=community" in lines[0], "community deploy passes coin_dojo_edition=community")
    check(lines and "personal/" not in lines[0], "community deploy command names no personal/ path")
    check("edition-check" in r.stdout, "community deploy runs edition-check first")
    com = make("-s", "edition-files", "EDITION=community").stdout.splitlines()
    per = make("-s", "edition-files", "EDITION=personal").stdout.splitlines()
    check(com and not [p for p in com if p.startswith("personal/")], "community file set has no personal/** (%d files)" % len(com))
    if os.path.exists(os.path.join(ROOT, "personal", "README.md")):
        check("personal/README.md" in per, "personal file set includes personal/**")
    else:   # #193: the community export has no personal/
        print("SKIP personal file set: no personal/ in this tree (community export)")
        SKIPS.append("personal file set")
    check(set(com) == {p for p in per if not p.startswith("personal/")}, "community = personal minus personal/**")
    r = make("-s", "edition-check", "EDITION=community")
    check(r.returncode == 0 and "RESULT: PASS" in r.stdout, "edition-check passes for community: " + r.stderr.strip())


def test_edition_check_fails_with_personal_file():
    """Broken case: a community set that still holds personal/** must fail edition-check."""
    with tempfile.TemporaryDirectory() as d:
        shutil.copytree(os.path.join(ROOT, "mk"), os.path.join(d, "mk"), ignore=shutil.ignore_patterns("tests"))
        mk = os.path.join(d, "mk", "edition.mk")
        text = open(mk).read().replace("EDITION_EXCLUDE_community  := personal/", "EDITION_EXCLUDE_community  :=")
        open(mk, "w").write(text)
        os.makedirs(os.path.join(d, "personal"))
        os.makedirs(os.path.join(d, "tests"))
        open(os.path.join(d, "personal", "art.txt"), "w").write("x\n")
        open(os.path.join(d, "tests", "test_ship_no_artifacts.py"), "w").write("print('ok')\n")
        open(os.path.join(d, "Makefile"), "w").write(
            "HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))\nREPO_ROOT := $(HERE)\n"
            "include $(REPO_ROOT)/mk/flags.mk\ninclude $(REPO_ROOT)/mk/help.mk\ninclude $(REPO_ROOT)/mk/edition.mk\n")
        subprocess.run(["git", "init", "-q", d], check=True)
        subprocess.run(["git", "-C", d, "add", "-A"], check=True)
        r = make("-s", "edition-check", "EDITION=community", cwd=d)
        check(r.returncode != 0 and "personal/" in r.stderr, "edition-check FAILS when community still lists personal/**")


def stub_ssh(d, output, rc=0):
    path = os.path.join(d, "fake-ssh")
    marker = os.path.join(d, "called")
    with open(path, "w") as f:
        f.write("#!/bin/sh\necho \"$@\" >> '%s'\nprintf '%s'\nexit %d\n" % (marker, output, rc))
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path, marker


def test_cabinet_guard():
    with tempfile.TemporaryDirectory() as d:
        ssh, _ = stub_ssh(d, "")
        r = make("-s", "-C", "skills/controls", "cabinet-guard", "CABINET_SSH=" + ssh)
        check(r.returncode == 0 and "OK" in r.stdout, "cabinet-guard passes when no game runs")
    with tempfile.TemporaryDirectory() as d:
        ssh, _ = stub_ssh(d, "4242\\n")
        r = make("-s", "-C", "skills/controls", "cabinet-guard", "CABINET_SSH=" + ssh)
        check(r.returncode != 0 and "4242" in r.stderr, "cabinet-guard FAILS when a game is running")
    with tempfile.TemporaryDirectory() as d:
        ssh, _ = stub_ssh(d, "", rc=255)
        r = make("-s", "-C", "skills/controls", "cabinet-guard", "CABINET_SSH=" + ssh)
        check(r.returncode != 0 and "cannot reach" in r.stderr, "cabinet-guard FAILS when the cabinet is unreachable")


def test_es_restart_dry_by_default():
    with tempfile.TemporaryDirectory() as d:
        ssh, marker = stub_ssh(d, "")
        r = make("-s", "-C", "skills/controls", "esde-restart", "CABINET_SSH=" + ssh)
        check(r.returncode == 0 and "DRY RUN" in r.stdout, "esde-restart is a dry run without APPLY=1")
        check(not os.path.exists(marker), "dry-run esde-restart never calls ssh")
        r = make("-s", "-C", "skills/controls", "esde-restart", "APPLY=1", "ES_RESTART_WAIT=0", "CABINET_SSH=" + ssh)
        calls = open(marker).read() if os.path.exists(marker) else ""
        check("pgrep -x retroarch" in calls and "pkill -9 -x AppRun" in calls and "pgrep -f AppRun" in calls,
              "APPLY=1 esde-restart: guard, pkill -9 -x AppRun, then the AppRun check")


def test_install_cfg_dry_by_default():
    with tempfile.TemporaryDirectory() as d:
        ssh, marker = stub_ssh(d, "")
        os.makedirs(os.path.join(d, "collections_out"))
        open(os.path.join(d, "collections_out", "custom-x.cfg"), "w").write("x\n")
        r = make("-s", "-C", "skills/collections", "install-cfg", "W=" + d, "CABINET_SSH=" + ssh, "CABINET_SCP=" + ssh)
        check(r.returncode == 0 and "DRY RUN" in r.stdout and not os.path.exists(marker),
              "install-cfg is a dry run without APPLY=1 (no ssh/scp)")
        r = make("-s", "-C", "skills/collections", "install-cfg")
        check(r.returncode != 0 and "W= is required" in r.stderr, "install-cfg without W= fails")


def test_py_test_catches_failures():
    """Broken case: a FAIL line or a non-zero exit must turn py-test red; a passing file stays green."""
    with tempfile.TemporaryDirectory() as d:
        shutil.copytree(os.path.join(ROOT, "mk"), os.path.join(d, "mk"), ignore=shutil.ignore_patterns("tests"))
        os.makedirs(os.path.join(d, "tests"))
        open(os.path.join(d, "Makefile"), "w").write(
            "HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))\nREPO_ROOT := $(HERE)\n"
            "include $(REPO_ROOT)/mk/flags.mk\ninclude $(REPO_ROOT)/mk/help.mk\ninclude $(REPO_ROOT)/mk/python.mk\n")
        good = os.path.join(d, "tests", "test_good.py")
        open(good, "w").write("print('PASS ok')\n")
        r = make("-s", "py-test", cwd=d)
        check(r.returncode == 0 and "RESULT: PASS tests/test_good.py" in r.stdout, "py-test passes a good file")
        for name, body in (("test_failline.py", "print('FAIL planted')\n"),
                           ("test_exit1.py", "import sys; sys.exit(1)\n"),
                           ("test_result.py", "print('RESULT: FAIL planted')\n")):
            bad = os.path.join(d, "tests", name)
            open(bad, "w").write(body)
            r = make("-s", "py-test", cwd=d)
            check(r.returncode != 0 and ("RESULT: FAIL tests/" + name) in r.stdout, "py-test FAILS on " + name)
            os.remove(bad)


def test_make_test_covers_every_test_file():
    r = make("-s", "test-list-all")
    listed = r.stdout.split()
    tracked = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True).stdout.split("\n")
    tests = sorted(p for p in tracked if re.search(r"(^|/)test_[^/]*\.py$", p))
    if "mk/tests/test_make.py" not in tests:   # not committed yet while developing
        tests = sorted(tests + ["mk/tests/test_make.py"])
    dupes = sorted({p for p in listed if listed.count(p) > 1})
    check(r.returncode == 0 and not dupes, "no test file runs twice (dupes: %s)" % dupes)
    check(sorted(set(listed)) == tests, "make test runs every tracked test file (missing: %s, extra: %s)"
          % (sorted(set(tests) - set(listed)), sorted(set(listed) - set(tests))))


# --- #142: area-verb names, forwarded help, `#?` rendering, one cache root ------------------------------------

# #162: the pre-v0.2 names (#142) are gone in v0.3.0. Each must now be an unknown target, and no Makefile may define
# an alias again.
OLD_NAMES = [("ansible", "check"), ("ansible", "ansible-check"), ("ansible", "ping"), ("ansible", "verify-esde"),
             ("ansible", "rollback-launch"), (".", "verify-esde"), (".", "rollback-launch"), (".", "list-tests-all"),
             (".", "es-restart"), ("skills/controls", "es-restart"), ("skills/badges", "list-tests")]
ALIAS_RE = re.compile(r"^([A-Za-z0-9_.-]+):.*\bhk_renamed\b", re.M)


def aliases_in(text):
    """Names of every `old: ; $(call hk_renamed,...)` line of a Makefile text (must be none since #162)."""
    return ALIAS_RE.findall(text)


def makefile_text(d):
    """A Makefile plus every mk/*.mk it includes, directly or through another mk file (what make reads)."""
    text, seen = open(os.path.join(ROOT, d, "Makefile")).read(), set()
    todo = re.findall(r"^include .*?([a-z][a-z-]*)\.mk$", text, re.M)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        inc = open(os.path.join(ROOT, "mk", name + ".mk")).read()
        text += "\n" + inc
        todo += re.findall(r"^include .*?([a-z][a-z-]*)\.mk$", inc, re.M)
    return text


def test_old_names_are_gone():
    for folder, old in OLD_NAMES:
        check(aliases_in(makefile_text(folder)) == [], "%s: no hk_renamed alias left" % folder)
        r = make("-n", "-C", folder, old)
        check(r.returncode != 0 and "No rule to make target" in r.stderr, "%s: make %s is an unknown target now" % (folder, old))
    check("hk_renamed" not in "".join(open(os.path.join(ROOT, "mk", f)).read() for f in os.listdir(os.path.join(ROOT, "mk"))
                                      if f.endswith(".mk")), "mk/*.mk no longer defines hk_renamed")
    planted = "esde-verify: ## x\n\t@true\nverify-esde: ; $(call hk_renamed,esde-verify)\n"
    check(aliases_in(planted) == ["verify-esde"], "BROKEN case: a planted alias line is caught")
    r = make("-s", "help")
    check(r.returncode == 0 and "Renamed" not in r.stdout, "make help has no Renamed section")


def help_target_names(out):
    """Target names `make help` lists (Targets + Forwarded blocks, not Renamed), with repeats."""
    body = out.split("Targets:", 1)[-1].split("Flags (current value):", 1)[0]
    body = re.split(r"^Renamed", body, flags=re.M)[0]
    return [l.split()[0] for l in body.splitlines() if l.startswith("  ")]


def fwd_rule_targets(text, d):
    """(HK_FWD_<d> list, target list of the rule whose recipe is `$(MAKE) -C $(REPO_ROOT)/<d> $@`)."""
    var = re.search(r"^HK_FWD_%s\s*:=\s*(.*)$" % re.escape(d), text, re.M)
    rule = re.search(r"^([^\n#:=]+):\n\t\$\(Q\)\$\(MAKE\) -C \$\(REPO_ROOT\)/%s \$@$" % re.escape(d), text, re.M)
    return (var.group(1).split() if var else []), (rule.group(1).split() if rule else [])


def test_help_lists_forwarded_targets_once():
    top = open(os.path.join(ROOT, "Makefile")).read()
    fwd, rule = fwd_rule_targets(top, "ansible")
    check(fwd and fwd == rule, "root Makefile: forwarding rule targets == HK_FWD_ansible (%s / %s)" % (fwd, rule))
    bad = top.replace("launch-rollback deploy-launch:\n", "launch-rollback deploy-launch deploy-extra:\n")
    f2, r2 = fwd_rule_targets(bad, "ansible")
    check(f2 != r2, "broken case: a rule target missing from HK_FWD_ansible is caught")
    ans = open(os.path.join(ROOT, "ansible", "Makefile")).read()
    out = make("help").stdout
    names = help_target_names(out)
    for t in fwd:
        m = re.search(r"^%s:[^\n]*## (.*)$" % re.escape(t), ans, re.M)
        check(m is not None, "ansible/Makefile documents %s with ##" % t)
        check(names.count(t) == 1, "make help lists %s once (got %d)" % (t, names.count(t)))
        if m:
            check(re.search(r"^  %s\s+%s$" % (re.escape(t), re.escape(m.group(1))), out, re.M) is not None,
                  "make help shows ansible's text for %s" % t)
        r = make("-n", t)
        check(r.returncode == 0 and ("-C %s/ansible %s" % (ROOT, t)) in r.stdout, "make -n %s forwards to ansible" % t)
    for d in ["."] + AREAS:
        names = help_target_names(make("-C", d, "help").stdout)
        dupes = sorted({n for n in names if names.count(n) > 1})
        check(not dupes, "%s: make help lists every target once (dupes: %s)" % (d, dupes))


def test_help_renders_quoted_flags():
    out = make("help").stdout
    check(len(re.findall(r"^  hk_<name>=v\s", out, re.M)) == 1, "config.mk's flag line is listed once")
    with tempfile.TemporaryDirectory() as d:
        shutil.copytree(os.path.join(ROOT, "mk"), os.path.join(d, "mk"), ignore=shutil.ignore_patterns("tests"))
        open(os.path.join(d, "extra.mk"), "w").write('#? A="x y"  the a flag\n#? B=1 plain flag\n')
        open(os.path.join(d, "Makefile"), "w").write(
            "HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))\nREPO_ROOT := $(HERE)\n"
            "include $(REPO_ROOT)/mk/flags.mk\ninclude $(REPO_ROOT)/mk/help.mk\n"
            "include $(REPO_ROOT)/extra.mk\ninclude $(REPO_ROOT)/extra.mk\n")
        out = make("-s", "help", cwd=d).stdout
        check(re.search(r'^  A="x y"\s+the a flag$', out, re.M) is not None, '#? A="x y" renders as name + text')
        check(re.search(r"^  B=1\s+plain flag$", out, re.M) is not None, "#? B=1 renders as name + text")
        check(out.count("the a flag") == 1, "a file included twice lists its flags once")
        check(re.search(r'^  A="x\s{2,}y"', out, re.M) is None, 'the old broken render (A="x   y", split at the space) is gone')


# #163: every reader uses the one root; nothing may name an old ~/.cache/coin-dojo-* folder again.
CACHE_PENDING = {}
OLD_CACHE_RE = re.compile(r"\.cache/coin-dojo-[a-z]|[\"']\.cache[\"'],\s*[\"']coin-dojo-[a-z]")
CACHE_EXEMPT = re.compile(r"^(CHANGELOG\.md|docs/history/|\.claude/team/(RETRO\.md|retros/))")


def old_cache_hits(files):
    """{path: first line} of files (path -> text) naming an old ~/.cache/coin-dojo-<x> folder."""
    hits = {}
    for path, text in files.items():
        if CACHE_EXEMPT.match(path):
            continue
        for line in text.splitlines():
            if OLD_CACHE_RE.search(line):
                hits[path] = line.strip()
                break
    return hits


def test_one_cache_root():
    tracked = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True).stdout.split()
    files = {}
    for p in tracked:
        if p == "mk/tests/test_make.py":
            continue   # this file names the old folders on purpose
        try:
            files[p] = open(os.path.join(ROOT, p), encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            pass
    hits = old_cache_hits(files)
    extra = {p: l for p, l in hits.items() if p not in CACHE_PENDING}
    check(not extra, "no ~/.cache/coin-dojo-* folder outside the one root HK_CACHE (found: %s)" % extra)
    stale = sorted(p for p in CACHE_PENDING if p not in hits)
    check(not stale, "CACHE_PENDING has no fixed file left in it (stale: %s)" % stale)
    planted = old_cache_hits({"tools/x.sh": 'C=$HOME/.cache/coin-dojo-ws/x\n', "a.py": 'os.path.join(h, ".cache", "coin-dojo-vm")\n',
                              "ok.sh": 'C=$HOME/.cache/coin-dojo/ws\n', "CHANGELOG.md": "~/.cache/coin-dojo-ws\n"})
    check(sorted(planted) == ["a.py", "tools/x.sh"], "broken case: planted old cache paths are found (%s)" % sorted(planted))
    with tempfile.TemporaryDirectory() as home:
        e = {k: v for k, v in env().items() if k not in ("HK_CACHE", "ANS_CACHE")}
        e["HOME"] = home
        r = subprocess.run(["make", "--no-print-directory", "-n", "-C", "ansible", "vm-guard", "LIMIT=coin-dojo-vm"],
                           cwd=ROOT, env=e, capture_output=True, text=True)
        want = '--out "%s/.cache/coin-dojo/ansible/config"' % home
        check(want in r.stdout, "ansible/Makefile renders the config into HK_CACHE/ansible/config: " + r.stderr.strip()[-200:])
        r = subprocess.run(["make", "--no-print-directory", "-n", "vm-smoke", "VM_NEED_UP=true", "VM_CPU_GATE="],
                           cwd=ROOT, env=e, capture_output=True, text=True)
        check(("%s/.cache/coin-dojo/vm/smoke" % home) in r.stdout, "vm-smoke copies into HK_CACHE/vm/smoke")


def stub_docker(d):
    """A docker stub on PATH: info / image inspect succeed, run logs its argv."""
    bindir = os.path.join(d, "bin")
    os.makedirs(bindir, exist_ok=True)
    log = os.path.join(d, "docker.log")
    with open(os.path.join(bindir, "docker"), "w") as f:
        f.write("#!/bin/sh\n[ \"$1\" = run ] && echo \"$@\" >> '%s'\nexit 0\n" % log)
    os.chmod(os.path.join(bindir, "docker"), 0o755)
    return bindir, log


def test_bin_ans_and_deploy_share_the_cache():
    """bin/ans mounts HK_CACHE/ansible; bin/deploy writes its vars file there: same files, one place."""
    with tempfile.TemporaryDirectory() as d:
        home = os.path.join(d, "home")
        os.makedirs(home)
        bindir, log = stub_docker(d)
        base = {k: v for k, v in env().items() if k not in ("ANS_CACHE", "HK_CACHE", "ANS_WORKSPACE", "ANSIBLE_INVENTORY")}
        base.update({"HOME": home, "PATH": bindir + os.pathsep + base.get("PATH", "/usr/bin:/bin")})
        cache = os.path.join(home, ".cache", "coin-dojo", "ansible")
        r = subprocess.run([os.path.join(ROOT, "ansible/bin/ans"), "true"], env=base, capture_output=True, text=True)
        calls = open(log).read() if os.path.exists(log) else ""
        check(r.returncode == 0 and ("-v %s:%s" % (cache, cache)) in calls and os.path.isdir(cache),
              "bin/ans mounts ~/.cache/coin-dojo/ansible at the same path: %s %s" % (calls.strip()[-300:], r.stderr.strip()))
        check(".cache/coin-dojo-ansible" not in calls, "bin/ans no longer mounts ~/.cache/coin-dojo-ansible")
        open(log, "w").close()
        r = subprocess.run([os.path.join(ROOT, "ansible/bin/ans"), "true"], env={**base, "HK_CACHE": os.path.join(home, "hk")},
                           capture_output=True, text=True)
        calls = open(log).read()
        check(("-v %s/hk/ansible:%s/hk/ansible" % (home, home)) in calls, "bin/ans follows HK_CACHE")
        open(log, "w").close()
        r = subprocess.run([os.path.join(ROOT, "ansible/bin/ans"), "true"], env={**base, "ANS_CACHE": os.path.join(home, "ac")},
                           capture_output=True, text=True)
        check(("-v %s/ac:%s/ac" % (home, home)) in open(log).read(), "ANS_CACHE still overrides the cache dir")
        # bin/deploy with a stub DEPLOY_ANS: the 0600 vars file sits in the folder bin/ans mounts.
        stub, marker = stub_ssh(d, "")
        sec = os.path.join(bindir, "security")
        with open(sec, "w") as f:
            f.write("#!/bin/sh\nexit 44\n")
        os.chmod(sec, 0o755)
        r = subprocess.run([os.path.join(ROOT, "ansible/bin/deploy"), "check"],
                           env={**base, "DEPLOY_ANS": stub, "DEPLOY_NO_CONFIG": "1", "COIN_DOJO_BECOME_PASS": "not-a-real-password"},
                           capture_output=True, text=True)
        calls = open(marker).read() if os.path.exists(marker) else ""
        check(r.returncode == 0 and ("@%s/" % cache) in calls and "coin-dojo-vars" in calls,
              "bin/deploy puts its vars file in ~/.cache/coin-dojo/ansible (mounted by bin/ans): " + calls.strip())


def test_cache_migrate():
    script = os.path.join(ROOT, "tools", "cache-migrate.sh")
    with tempfile.TemporaryDirectory() as home:
        for name in ("ansible", "ws", "vm"):
            os.makedirs(os.path.join(home, ".cache", "coin-dojo-" + name))
            open(os.path.join(home, ".cache", "coin-dojo-" + name, "f"), "w").write(name)
        os.makedirs(os.path.join(home, ".cache", "coin-dojo", "vm"))     # already there: must be left alone
        base = {**env(), "HOME": home}
        base.pop("HK_CACHE", None)
        r = subprocess.run(["make", "--no-print-directory", "-s", "cache-migrate"], cwd=ROOT, env=base, capture_output=True, text=True)
        check(r.returncode == 0 and r.stdout.count("DRY RUN cache-migrate: would move") == 2 and "SKIP" in r.stdout
              and os.path.isdir(os.path.join(home, ".cache", "coin-dojo-ws")), "make cache-migrate is a dry run: " + r.stdout)
        r = subprocess.run(["sh", script], env={**base, "APPLY": "1"}, capture_output=True, text=True)
        moved = all(open(os.path.join(home, ".cache", "coin-dojo", n, "f")).read() == n for n in ("ansible", "ws")
                    if os.path.exists(os.path.join(home, ".cache", "coin-dojo", n, "f")))
        check(r.returncode == 0 and moved and not os.path.exists(os.path.join(home, ".cache", "coin-dojo-ws"))
              and os.path.isfile(os.path.join(home, ".cache", "coin-dojo", "ws", "f")),
              "APPLY=1 moves coin-dojo-ansible/ws under ~/.cache/coin-dojo with their files")
        check(os.path.isfile(os.path.join(home, ".cache", "coin-dojo-vm", "f"))
              and not os.path.exists(os.path.join(home, ".cache", "coin-dojo", "vm", "f")),
              "a folder whose new place exists is left alone (never merged or deleted)")
        r = subprocess.run(["sh", script], env={**base, "APPLY": "yes"}, capture_output=True, text=True)
        check(r.returncode == 2, "broken case: APPLY=yes is refused (exit 2)")
        r = subprocess.run(["sh", script], env={**base, "HK_CACHE": "/private/tmp/x"}, capture_output=True, text=True)
        check(r.returncode == 2, "broken case: HK_CACHE outside $HOME is refused (exit 2)")

# --- #143: setup, doctor, cabinet-status, cabinet-ssh, cabinet-shot, rollout (stubs only, never the cabinet) ----

# A command that changes something on the cabinet. cabinet-status's call log (argv + the script on stdin) must hold none.
WRITE_VERBS = re.compile(r"(^|[\s;|&(`])(rm|mv|cp|tee|dd|kill|pkill|killall|reboot|shutdown|poweroff|mkdir|rmdir|touch|"
                         r"chmod|chown|ln|truncate|install|rsync|scp|apt|apt-get|dpkg|pip|sudo|systemctl|sed\s+-i)(\s|$)"
                         r"|(^|[^0-9<>=!-])>>?\s*(/(?!dev/null)|~|\$|[A-Za-z_])")


def write_verbs(text):
    """Lines of an ssh call log that would write on the cabinet (2>/dev/null and >/dev/null are fine)."""
    return [l for l in text.splitlines() if WRITE_VERBS.search(l.replace("2>/dev/null", "").replace(">/dev/null", ""))]


def stub_ssh_stdin(d, output, rc=0):
    """ssh stub that logs argv AND stdin (the remote script), prints output, exits rc."""
    path, marker = os.path.join(d, "fake-ssh"), os.path.join(d, "called")
    with open(path, "w") as f:
        f.write("#!/bin/sh\necho \"ARGV $*\" >> '%s'\ncat >> '%s'\nprintf '%s'\nexit %d\n" % (marker, marker, output, rc))
    os.chmod(path, 0o755)
    return path, marker


STATUS_OUT = ("host=coin-dojo\\nuptime= 10:00 up 2 days\\nesde=%s\\ngame=\\npagesize=4096\\nkernel=6.12-rpi-v8\\n"
              "df=/ 850\\ndf=/data 13000\\nzram=/dev/zram0 size=2048M used=10M\\nrev=v9.9.9\\n")


def test_cabinet_status_reads_the_rev_element():
    """#160: the REV comes from <text>REV x</text> of the real theme.xml, not from a comment line that says REV."""
    theme = open(os.path.join(ROOT, "themes", "coin-dojo", "theme.xml")).read()
    check(theme.count("REV") > 1, "the real theme.xml has REV in a comment too (the trap)")
    script = os.path.join(ROOT, "tools", "cabinet-status.sh")
    for name, text, want in (("real", theme.replace("@COIN_DOJO_VERSION@", "v7.7.7"), "v7.7.7 (= this checkout)"),
                             ("broken", "<!-- REV stamp (comment only) -->\n", "(no stamp found)")):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "ES-DE", "themes", "T"))
            open(os.path.join(d, "ES-DE", "themes", "T", "theme.xml"), "w").write(text)
            ssh = os.path.join(d, "fake-ssh")       # runs the remote script here: the parse itself is under test
            open(ssh, "w").write("#!/bin/sh\ncd '%s' && exec sh -s\n" % d)
            os.chmod(ssh, 0o755)
            env = dict(os.environ, CABINET_SSH=ssh, ESDE_HOME="ES-DE", THEME="T", LOCAL_REV="v7.7.7", GUARD_CMD="true")
            r = subprocess.run(["bash", script], env=env, capture_output=True, text=True)
            line = next((l for l in r.stdout.splitlines() if l.startswith("REV")), "")
            check(want in line, "#160 %s theme.xml: %r" % (name, line))


def hand_listed_tests(makefile):
    """tests/test_*.py files the root Makefile names by hand (#161: must be none, the wildcard finds them)."""
    return re.findall(r"(?<![*/])\btests/test_[A-Za-z0-9_]+\.py", makefile.split("PY_TESTS :=", 1)[-1].split("\n\n", 1)[0])


def test_root_tests_by_wildcard():
    mk = open(os.path.join(ROOT, "Makefile")).read()
    check("$(REPO_ROOT)/tests/test_*.py" in mk and hand_listed_tests(mk) == [],
          "#161: root PY_TESTS finds tests/test_*.py by wildcard (hand-listed: %s)" % hand_listed_tests(mk))
    old = "PY_TESTS := $(addprefix $(REPO_ROOT)/,tests/test_layout.py tests/test_docs.py runbooks/test_runbooks.py)\n\n"
    check(hand_listed_tests(old) == ["tests/test_layout.py", "tests/test_docs.py"], "#161 BROKEN case: a hand list is caught")
    listed = make("-s", "py-list").stdout.split()
    on_disk = sorted("tests/" + f for f in os.listdir(os.path.join(ROOT, "tests")) if re.match(r"test_.*\.py$", f))
    check(on_disk and all(f in listed for f in on_disk), "make py-list lists every tests/test_*.py on disk")

def test_cabinet_status_is_read_only():
    with tempfile.TemporaryDirectory() as d:
        ssh, marker = stub_ssh_stdin(d, STATUS_OUT % "up")
        r = make("-s", "cabinet-status", "CABINET_SSH=" + ssh)
        log = open(marker).read() if os.path.exists(marker) else ""
        check(r.returncode == 0 and "RESULT: PASS cabinet-status" in r.stdout, "cabinet-status PASSes on a healthy stub: "
              + (r.stdout + r.stderr)[-300:])
        check(log.count("ARGV ") == 1, "cabinet-status makes exactly one ssh call")
        check("pgrep -f AppRun" in log and "getconf PAGESIZE" in log and "/proc/swaps" in log and "theme.xml" in log,
              "the remote script checks ES-DE, page size, zram and the REV stamp")
        check(not write_verbs(log), "cabinet-status call log has no write verbs: %s" % write_verbs(log))
        check("coin-dojo" in r.stdout and "850 MB free" in r.stdout and "WARN this checkout is" in r.stdout,
              "cabinet-status shows host, free space and a REV mismatch WARN")
    planted = "ARGV sh -s\nrm -f /home/cab/ES-DE/x\necho ok > /tmp/x\npgrep -f AppRun >/dev/null 2>&1\npkill -x AppRun\n"
    check(len(write_verbs(planted)) == 3, "broken case: planted rm / redirect / pkill are caught as writes (%s)" % write_verbs(planted))
    with tempfile.TemporaryDirectory() as d:
        ssh, _ = stub_ssh_stdin(d, STATUS_OUT % "down")
        r = make("-s", "cabinet-status", "CABINET_SSH=" + ssh)
        check(r.returncode != 0 and "ES-DE" in r.stdout and "DOWN" in r.stdout, "cabinet-status FAILs when ES-DE is down")
    with tempfile.TemporaryDirectory() as d:
        ssh, _ = stub_ssh_stdin(d, "", rc=255)
        r = make("-s", "cabinet-status", "CABINET_SSH=" + ssh)
        check(r.returncode != 0 and "cannot reach" in r.stderr, "cabinet-status FAILs when the cabinet is unreachable")


def test_cabinet_ssh_and_shot():
    r = make("-n", "cabinet-ssh", "CABINET_KEY=/k", "CABINET_USER=u", "CABINET_HOST=h.test")
    check(r.returncode == 0 and "ssh -t -i /k" in r.stdout and "u@h.test" in r.stdout, "cabinet-ssh = ssh -t with the config key + host")
    with tempfile.TemporaryDirectory() as d:
        ssh, marker = stub_ssh(d, "\\x89PNGfake")
        shots = os.path.join(d, "shots")
        r = make("-s", "cabinet-shot", "CABINET_SSH=" + ssh, "HK_SHOTS=" + shots)
        files = os.listdir(shots) if os.path.isdir(shots) else []
        log = open(marker).read() if os.path.exists(marker) else ""
        check(r.returncode == 0 and len(files) == 1 and files[0].endswith(".png")
              and os.path.getsize(os.path.join(shots, files[0])) > 0, "cabinet-shot saves one png under HK_SHOTS: " + r.stderr)
        check("grim -t png -" in log and not write_verbs(log), "cabinet-shot streams grim to stdout (writes nothing there)")
    with tempfile.TemporaryDirectory() as d:
        ssh, _ = stub_ssh(d, "", rc=1)
        shots = os.path.join(d, "shots")
        r = make("-s", "cabinet-shot", "CABINET_SSH=" + ssh, "HK_SHOTS=" + shots)
        check(r.returncode != 0 and not (os.path.isdir(shots) and os.listdir(shots)),
              "broken case: a failed grim FAILs and leaves no empty png")


def doctor_env(d, with_docker=True):
    bindir = os.path.join(d, "bin")
    os.makedirs(bindir, exist_ok=True)
    stubs = {"security": "#!/bin/sh\necho \"$*\" >> '%s'\nexit 0\n" % os.path.join(d, "security.log"),
             "limactl": "#!/bin/sh\nexit 0\n", "gum": "#!/bin/sh\nexit 0\n"}
    if with_docker:
        stubs["docker"] = "#!/bin/sh\nexit 0\n"
    for name, body in stubs.items():
        with open(os.path.join(bindir, name), "w") as f:
            f.write(body)
        os.chmod(os.path.join(bindir, name), 0o755)
    home = os.path.join(d, "home")
    os.makedirs(os.path.join(home, ".ssh"), exist_ok=True)
    e = {k: v for k, v in env().items() if k not in ("HK_CACHE", "HK_CONFIG")}
    # keep the real python/make dirs on PATH, minus any real docker/limactl/gum (the stubs come first)
    e.update({"HOME": home, "PATH": bindir + os.pathsep + "/usr/bin:/bin:" + os.path.dirname(sys.executable)})
    return e, home


def test_doctor():
    pyyaml = subprocess.run([sys.executable, "-c", "import yaml"], capture_output=True).returncode == 0
    cfgpy = ([os.path.expanduser("~/.venvs/coin-dojo/bin/python")] if os.path.exists(os.path.expanduser("~/.venvs/coin-dojo/bin/python"))
             else [sys.executable] if pyyaml else [])
    if not cfgpy:
        print("SKIP doctor cases: no python with PyYAML (make setup)")
        SKIPS.append("doctor")
        return
    with tempfile.TemporaryDirectory() as d:
        e, home = doctor_env(d)
        r = subprocess.run(["make", "--no-print-directory", "-s", "doctor", "CONFIG_PYTHON=" + cfgpy[0],
                            "HK_CONFIG=" + os.path.join(d, "no-such-local.yml")], cwd=ROOT, env=e, capture_output=True, text=True)
        check(r.returncode != 0 and re.search(r"^FAIL config", r.stdout, re.M) and "RESULT: FAIL doctor" in r.stdout,
              "doctor FAILs without a config: " + r.stdout[-300:])
        empty = os.path.join(d, "empty.yml")
        open(empty, "w").write("# nothing set\n")
        r = subprocess.run(["make", "--no-print-directory", "-s", "doctor", "CONFIG_PYTHON=" + cfgpy[0], "HK_CONFIG=" + empty,
                            "EDITION=community"], cwd=ROOT, env=e, capture_output=True, text=True)
        check(r.returncode != 0 and re.search(r"^FAIL config", r.stdout, re.M), "doctor FAILs with a config that has no cabinet host")
        key = os.path.join(home, ".ssh", "id_test")
        good = os.path.join(d, "good.yml")
        open(good, "w").write("hk_cabinet_host: arcade.test\nhk_ssh_key: %s\nhk_become_keychain: test-item\n" % key)
        r = subprocess.run(["make", "--no-print-directory", "-s", "doctor", "CONFIG_PYTHON=" + cfgpy[0], "HK_CONFIG=" + good,
                            "EDITION=community"], cwd=ROOT, env=e, capture_output=True, text=True)
        check(r.returncode != 0 and re.search(r"^FAIL ssh-key", r.stdout, re.M), "doctor FAILs when the ssh key is missing")
        open(key, "w").write("not a key\n")
        r = subprocess.run(["make", "--no-print-directory", "-s", "doctor", "CONFIG_PYTHON=" + cfgpy[0], "HK_CONFIG=" + good,
                            "EDITION=community"], cwd=ROOT, env=e, capture_output=True, text=True)
        check(r.returncode == 0 and "RESULT: PASS doctor" in r.stdout and "PASS keychain" in r.stdout,
              "doctor PASSes with config, key, docker, stubs: " + r.stdout[-400:])
        sec = open(os.path.join(d, "security.log")).read()
        check("test-item" in sec and " -w" not in sec and " -g" not in sec, "doctor only checks the Keychain item exists (no -w/-g)")
    with tempfile.TemporaryDirectory() as d:
        e, home = doctor_env(d, with_docker=False)
        os.makedirs(os.path.join(home, ".cache", "coin-dojo-ws"))
        r = subprocess.run(["make", "--no-print-directory", "-s", "doctor", "CONFIG_PYTHON=" + cfgpy[0],
                            "HK_CONFIG=" + os.path.join(d, "none.yml")], cwd=ROOT, env=e, capture_output=True, text=True)
        check(re.search(r"^FAIL docker", r.stdout, re.M) and re.search(r"^WARN cache .*coin-dojo-ws", r.stdout, re.M),
              "doctor FAILs without docker and WARNs about old cache folders")


def test_setup():
    r = make("-n", "setup")
    out = r.stdout
    check(r.returncode == 0 and "--require-hashes -r" in out and "config-requirements.txt" in out and "pillow" in out and "core.hooksPath" in out and "local.example.yml" in out,
          "make -n setup: venv (hash-pinned PyYAML #166 + Pillow), hooks, local config")
    with tempfile.TemporaryDirectory() as d:
        dest = os.path.join(d, "local.yml")
        r = make("-s", "setup-config", "EDITION=community", "SETUP_LOCAL=" + dest, "HK_CONFIG=")
        has_local = os.path.exists(os.path.join(ROOT, "config", "local.yml"))
        if has_local:
            check(not os.path.exists(dest) and "left as is" in r.stdout, "setup-config leaves an existing config/local.yml alone")
        else:
            check(r.returncode == 0 and os.path.exists(dest) and "hk_cabinet_host" in open(dest).read(),
                  "setup-config copies the example when there is no local config")
            open(dest, "w").write("hk_cabinet_host: mine.test\n")
            r = make("-s", "setup-config", "EDITION=community", "SETUP_LOCAL=" + dest, "HK_CONFIG=")
            check(open(dest).read() == "hk_cabinet_host: mine.test\n", "broken case: setup-config never overwrites a local config")
        if os.path.exists(os.path.join(ROOT, "personal", "config", "local.yml")):
            r = make("-s", "setup-config", "EDITION=personal", "SETUP_LOCAL=" + dest + ".2", "HK_CONFIG=")
            check(not os.path.exists(dest + ".2") and "left as is" in r.stdout,
                  "setup-config: personal/config/local.yml counts as a local config (EDITION=personal)")
        else:   # #193: a fresh clone or the community export has no personal/config/local.yml
            print("SKIP setup-config personal: no personal/config/local.yml here")
            SKIPS.append("setup-config personal")


STUB_MAKE = r'''#!/bin/sh
# stub `make` for rollout tests: logs the call; deploy prints a PLAY RECAP; the 2nd dry run's changed= comes from STUB_CHANGED
echo "$*" >> "$STUB_DIR/make.log"
case "$*" in
  *"deploy APPLY=0"*)
    n=$(grep -c 'deploy APPLY=0' "$STUB_DIR/make.log")
    c=0; [ "$n" -ge 2 ] && c=${STUB_CHANGED:-0}
    [ "$n" -eq 1 ] && [ -n "$STUB_FAIL_FIRST" ] && { echo "fatal: unreachable"; exit 2; }
    printf 'TASK [esde_theme : sync] ****\nok: [coin-dojo]\n\nPLAY RECAP ****\ncoin-dojo                   : ok=5    changed=%s    unreachable=0    failed=0    skipped=1    rescued=0    ignored=0\n' "$c" ;;
  *"deploy APPLY=1"*) printf 'PLAY RECAP ****\ncoin-dojo                   : ok=5    changed=3    unreachable=0    failed=0\n' ;;
esac
exit 0
'''
STUB_RECAP = r'''import re, sys
# stub of tools/recap.py's contract (#136): --zero LOG -> 0 all zero, 1 changed/failed/unreachable > 0, 2 malformed
t = open(sys.argv[2]).read() if len(sys.argv) > 2 else ""
m = re.findall(r"changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)", t)
sys.exit(2 if not m else 1 if any(int(x) for row in m for x in row) else 0)
'''


def run_rollout(d, *args, **envx):
    open(os.path.join(d, "make.log"), "w").close()
    stub = os.path.join(d, "stub-make")
    with open(stub, "w") as f:
        f.write(STUB_MAKE)
    os.chmod(stub, 0o755)
    recap = os.path.join(d, "recap.py")
    with open(recap, "w") as f:
        f.write(STUB_RECAP)
    real = os.path.join(ROOT, "tools", "recap.py")
    use = envx.pop("_recap", None)
    e = {**env(), "STUB_DIR": d, "HK_CACHE": os.path.join(os.path.expanduser("~"), ".cache", "coin-dojo", "test-rollout-%d" % os.getpid())}
    e.update(envx)
    r = subprocess.run(["make", "--no-print-directory", "-s", "rollout", "ROLLOUT_MAKE=" + stub,
                        "ROLLOUT_RECAP=" + (use or recap), *args], cwd=ROOT, env=e, capture_output=True, text=True)
    calls = open(os.path.join(d, "make.log")).read().splitlines()
    shutil.rmtree(e["HK_CACHE"], ignore_errors=True)
    return r, calls, real


def test_rollout():
    with tempfile.TemporaryDirectory() as d:
        r, calls, real = run_rollout(d)
        check(r.returncode == 0 and calls == ["deploy APPLY=0"] and "DRY RUN rollout" in r.stdout,
              "rollout without APPLY=1 is one dry run, nothing applied: %s" % calls)
    with tempfile.TemporaryDirectory() as d:
        r, calls, real = run_rollout(d, "APPLY=1")
        check(r.returncode == 0 and calls == ["deploy APPLY=0", "deploy APPLY=1", "deploy APPLY=0", "esde-verify"]
              and "RESULT: PASS rollout" in r.stdout, "APPLY=1: dry, apply, dry (changed=0), esde-verify -> PASS: %s %s" % (calls, r.stdout[-200:]))
        check(re.search(r"log \S+/rollout/\S+/1-dry\.log", r.stdout) is not None, "both dry runs are tee'd to files under HK_CACHE/rollout")
    with tempfile.TemporaryDirectory() as d:
        r, calls, _ = run_rollout(d, "APPLY=1", STUB_CHANGED="2")
        check(r.returncode != 0 and "esde-verify" not in calls and "not idempotent" in r.stdout,
              "broken case: rollout FAILs when the 2nd dry run recaps changed=2 (no verify after): %s" % calls)
    with tempfile.TemporaryDirectory() as d:
        r, calls, _ = run_rollout(d, "APPLY=1", STUB_FAIL_FIRST="1")
        check(r.returncode != 0 and calls == ["deploy APPLY=0"], "a failed first dry run stops before the apply: %s" % calls)
    with tempfile.TemporaryDirectory() as d:
        r, calls, _ = run_rollout(d, "APPLY=1", "SMOKE=1")
        check(r.returncode == 0 and calls[-1] == "smoke-launch", "SMOKE=1 ends with smoke-launch")
    with tempfile.TemporaryDirectory() as d:
        r, calls, _ = run_rollout(d, "APPLY=1", _recap=os.path.join(d, "nope.py"))
        check(r.returncode != 0 and calls == [] and "nothing was applied" in r.stdout, "rollout refuses APPLY=1 without recap.py")
    if os.path.exists(real):   # the real tools/recap.py (#136) once merged: same verdicts as the stub
        for changed, want in (("0", 0), ("2", 1)):
            with tempfile.TemporaryDirectory() as d:
                r, calls, _ = run_rollout(d, "APPLY=1", STUB_CHANGED=changed, _recap=real)
                check((r.returncode == 0) == (want == 0), "real tools/recap.py: 2nd dry run changed=%s -> %s" % (changed, "PASS" if not want else "FAIL"))


def main():
    if shutil.which("make") is None:
        print("SKIP make not installed")
        print("RESULT: PASS (1 skipped)")
        return 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    skipped = " (%d skipped)" % len(SKIPS) if SKIPS else ""   # runtests.py fails uncounted SKIP lines
    print("RESULT: %s%s (%d failure(s))" % ("FAIL" if FAILURES else "PASS", skipped, len(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
