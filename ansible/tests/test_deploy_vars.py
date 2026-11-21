#!/usr/bin/env python3
"""ansible/bin/deploy keeps the sudo vars file in a per-run 0700 dir and cleans up what a SIGKILL left (#140, Security L6).

The vars file (the become password as JSON) lives in $CACHE/runs/run.XXXXXX/ (0700), the file 0600; the EXIT trap
removes the dir. SIGKILL skips the trap, so every run first deletes per-run dirs and old-layout
$CACHE/coin-dojo-vars.* files older than a day, and keeps younger ones (a deploy running right now).

Everything is stubbed: DEPLOY_ANS = a python stub that records argv and the file modes, DEPLOY_NO_CONFIG=1, a
`security` stub that fails (no Keychain), a fake password. No Ansible, no cabinet, no real deploy.
Broken cases: a deploy copy without the prune call leaves the stale file; a SIGKILLed run really leaves its dir
(the reason for the prune), which the next run removes once it is old.

Pure stdlib. Run directly:  python3 ansible/tests/test_deploy_vars.py
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ANSIBLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY = os.path.join(ANSIBLE, "bin", "deploy")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests", "lib"))
from hktest import check, finish  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)
FAKE_PASS = "not-a-real-password-140"
OLD = time.time() - 2 * 86400

STUB = r'''#!/usr/bin/env python3
import json, os, stat, sys, time
out = {"args": sys.argv[1:], "modes": []}
for a in sys.argv[1:]:
    if a.startswith("@") and os.path.exists(a[1:]):
        p = a[1:]
        out["modes"].append([p, oct(stat.S_IMODE(os.stat(p).st_mode)), oct(stat.S_IMODE(os.stat(os.path.dirname(p)).st_mode))])
        try:
            out.setdefault("keys", []).extend(sorted(json.load(open(p))))   # names only, never the values
        except ValueError:
            pass
with open(os.environ["STUB_OUT"], "w") as fh:
    json.dump(out, fh)
if os.environ.get("STUB_SLEEP"):
    time.sleep(60)
'''


def setup(d):
    bindir = os.path.join(d, "bin")
    os.makedirs(bindir)
    stub = os.path.join(d, "ans")
    with open(stub, "w") as fh:
        fh.write(STUB)
    with open(os.path.join(bindir, "security"), "w") as fh:
        fh.write("#!/bin/sh\nexit 44\n")
    for f in (stub, os.path.join(bindir, "security")):
        os.chmod(f, 0o755)
    cache = os.path.join(d, "cache")
    env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "HK_SET")}
    env.update({"DEPLOY_ANS": stub, "DEPLOY_NO_CONFIG": "1", "ANS_CACHE": cache, "STUB_OUT": os.path.join(d, "out.json"),
                "COIN_DOJO_BECOME_PASS": FAKE_PASS, "PATH": bindir + os.pathsep + env.get("PATH", "/usr/bin:/bin")})
    return cache, env


def plant(cache):
    """Stale + fresh leftovers in both layouts; returns their paths."""
    runs = os.path.join(cache, "runs")
    os.makedirs(runs, mode=0o700)
    paths = {}
    for name, age in (("stale", OLD), ("fresh", None)):
        f = os.path.join(cache, "coin-dojo-vars." + name)
        with open(f, "w") as fh:
            fh.write("{}")
        rd = os.path.join(runs, "run." + name)
        os.makedirs(rd, mode=0o700)
        with open(os.path.join(rd, "coin-dojo-vars.json"), "w") as fh:
            fh.write("{}")
        if age:
            os.utime(f, (age, age))
            os.utime(rd, (age, age))       # the dir last: writing the file inside bumps its mtime
        paths[name] = (f, rd)
    return paths


def run(deploy, env, *args):
    return subprocess.run([deploy, *args], env=env, capture_output=True, text=True, timeout=60)


def test_per_run_dir_and_prune():
    with tempfile.TemporaryDirectory() as d:
        cache, env = setup(d)
        p = plant(cache)
        r = run(DEPLOY, env, "check")
        out = json.load(open(env["STUB_OUT"])) if os.path.exists(env["STUB_OUT"]) else {"args": [], "modes": []}
        check(r.returncode == 0, "stub deploy check exits 0 (%s)" % r.stderr.strip())
        modes = out["modes"]
        check(len(modes) == 1 and "/runs/run." in modes[0][0], "vars file passed as -e @$CACHE/runs/run.XXXXXX/...: %s" % modes)
        check(modes and modes[0][1] == "0o600" and modes[0][2] == "0o700", "vars file 0600 in a 0700 per-run dir: %s" % modes)
        check(FAKE_PASS not in " ".join(out["args"]) and FAKE_PASS not in r.stdout + r.stderr, "password not in argv or output")
        check(modes and not os.path.exists(os.path.dirname(modes[0][0])), "the run's own dir is removed by the EXIT trap")
        check(not os.path.exists(p["stale"][0]) and not os.path.exists(p["stale"][1]), "stale (2 days) vars file and run dir are deleted")
        check(os.path.exists(p["fresh"][0]) and os.path.exists(p["fresh"][1]), "fresh ones (a concurrent run) are kept")


def test_no_password_still_prunes():
    with tempfile.TemporaryDirectory() as d:
        cache, env = setup(d)
        env["COIN_DOJO_BECOME_PASS"] = ""
        p = plant(cache)
        r = run(DEPLOY, env, "ping")
        out = json.load(open(env["STUB_OUT"]))
        check(r.returncode == 0 and out["modes"] == [], "no password: no vars file passed (%s)" % r.stderr.strip())
        check(not os.path.exists(p["stale"][0]) and not os.path.exists(p["stale"][1]), "no password: stale leftovers still deleted")


def test_broken_without_prune_keeps_stale():
    with tempfile.TemporaryDirectory() as d:
        cache, env = setup(d)
        p = plant(cache)
        os.makedirs(os.path.join(d, "ansible", "bin"))
        broken = os.path.join(d, "ansible", "bin", "deploy")
        text = open(DEPLOY).read()
        check(text.count("\nprune_stale\n") == 1, "deploy calls prune_stale once at the top level")
        with open(broken, "w") as fh:
            fh.write(text.replace("\nprune_stale\n", "\n", 1))
        os.chmod(broken, 0o755)
        r = run(broken, env, "check")
        check(r.returncode == 0 and os.path.exists(p["stale"][0]) and os.path.exists(p["stale"][1]),
              "broken: a deploy without the prune leaves the stale vars file (the checks above would FAIL)")


def test_sigkill_leftover_is_pruned_next_time():
    with tempfile.TemporaryDirectory() as d:
        cache, env = setup(d)
        env["STUB_SLEEP"] = "1"
        proc = subprocess.Popen([DEPLOY, "check"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        for _ in range(100):
            if os.path.exists(env["STUB_OUT"]):
                break
            time.sleep(0.1)
        os.killpg(proc.pid, signal.SIGKILL)       # deploy and its stub child: only our own process group
        proc.wait()
        runs = os.path.join(cache, "runs")
        left = [os.path.join(runs, n) for n in os.listdir(runs)] if os.path.isdir(runs) else []
        check(len(left) == 1, "SIGKILL skips the trap: the run dir is left behind (%s)" % left)
        env.pop("STUB_SLEEP")
        r = run(DEPLOY, env, "check")
        check(r.returncode == 0 and all(os.path.exists(x) for x in left), "a young leftover survives the next run (may be a live deploy)")
        for x in left:
            os.utime(x, (OLD, OLD))
        r = run(DEPLOY, env, "check")
        check(r.returncode == 0 and not any(os.path.exists(x) for x in left), "once older than a day the next run deletes it")
        shutil.rmtree(cache, ignore_errors=True)


FAKE_TOKEN = "FAKETOKEN45notreal"


def test_cheevos_token_rides_in_the_vars_file():
    """#45: the RetroAchievements token goes into the same private vars file as `cheevos_token`, never argv or output;
    without a sudo password the file still carries the token, and --ask-become-pass stays."""
    with tempfile.TemporaryDirectory() as d:
        cache, env = setup(d)
        env.update({"COIN_DOJO_BECOME_PASS": "", "COIN_DOJO_CHEEVOS_TOKEN": FAKE_TOKEN})
        r = run(DEPLOY, env, "--", "site.yml", "--ask-become-pass")
        out = json.load(open(env["STUB_OUT"]))
        check(r.returncode == 0 and out.get("keys") == ["cheevos_token"], "token only: vars file holds cheevos_token (%s)" % out.get("keys"))
        check(FAKE_TOKEN not in " ".join(out["args"]) + r.stdout + r.stderr, "token not in argv or output")
        check("--ask-become-pass" in out["args"], "token only: --ask-become-pass is kept (no sudo password supplied)")
        env["COIN_DOJO_BECOME_PASS"] = FAKE_PASS
        r = run(DEPLOY, env, "check")
        out = json.load(open(env["STUB_OUT"]))
        check(out.get("keys") == ["ansible_become_password", "cheevos_token"], "both: one file, both keys (%s)" % out.get("keys"))
        env.update({"COIN_DOJO_BECOME_PASS": "", "COIN_DOJO_CHEEVOS_TOKEN": "", "COIN_DOJO_CHEEVOS_WEB_KEY": FAKE_TOKEN + "web"})
        r = run(DEPLOY, env, "check")
        out = json.load(open(env["STUB_OUT"]))
        check(out.get("keys") == ["cheevos_web_key"] and FAKE_TOKEN not in " ".join(out["args"]) + r.stdout + r.stderr,
              "#176 web API key only: the vars file holds cheevos_web_key, never argv or output (%s)" % out.get("keys"))
        env.update({"COIN_DOJO_CHEEVOS_WEB_KEY": ""})
        r = run(DEPLOY, env, "check")
        check(json.load(open(env["STUB_OUT"]))["modes"] == [], "neither: no vars file")
    with tempfile.TemporaryDirectory() as d:   # broken: a deploy that writes the vars file only for a sudo password
        cache, env = setup(d)
        env.update({"COIN_DOJO_BECOME_PASS": "", "COIN_DOJO_CHEEVOS_TOKEN": FAKE_TOKEN})
        os.makedirs(os.path.join(d, "ansible", "bin"))
        broken = os.path.join(d, "ansible", "bin", "deploy")
        text = open(DEPLOY).read()
        cond = '[ -n "$COIN_DOJO_BECOME_PASS" ] || [ -n "$COIN_DOJO_CHEEVOS_TOKEN" ]'
        check(text.count(cond) == 1, "deploy writes the vars file for a password OR a token")
        open(broken, "w").write(text.replace(cond, '[ -n "$COIN_DOJO_BECOME_PASS" ]'))
        os.chmod(broken, 0o755)
        run(broken, env, "check")
        check(json.load(open(env["STUB_OUT"]))["modes"] == [], "BROKEN case: without the token condition no token reaches the cabinet")


def main():
    test_cheevos_token_rides_in_the_vars_file()
    test_per_run_dir_and_prune()
    test_no_password_still_prunes()
    test_broken_without_prune_keeps_stale()
    test_sigkill_leftover_is_pruned_next_time()
    return finish()


if __name__ == "__main__":
    sys.exit(main())
