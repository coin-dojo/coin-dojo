#!/usr/bin/env python3
"""tools/net-check.sh (#213): parse, verdicts, read-only. Fake ssh / ping / ip / route on PATH: no network.

    python3 tools/tests/test_net_check.py

Cases: a healthy run (Linux ping) PASSes with both rows; jitter and loss WARN but PASS; macOS ping output parses;
broken cases FAIL: 100 % loss, no default route, an unreachable cabinet, unreadable ping output; usage errors exit 2;
the call log (argv + the script on stdin) has no write verbs; one ssh call; a local run (no NET_CHECK_SSH) works.
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
from hktest import check, main  # noqa: E402

SCRIPT = os.path.join(ROOT, "tools", "net-check.sh")

LINUX = {   # host -> the summary `ping -q` prints (Linux iputils)
    "ok": "--- {h} ping statistics ---\n{n} packets transmitted, {n} received, 0% packet loss, time 59812ms\n"
          "rtt min/avg/max/mdev = 9.120/13.800/56.300/8.100 ms\n",
    "jitter": "--- {h} ping statistics ---\n{n} packets transmitted, 277 received, +3 errors, 7.5% packet loss, "
              "time 59870ms\nrtt min/avg/max/mdev = 9.000/21.900/130.700/29.800 ms\n",
    "dead": "--- {h} ping statistics ---\n{n} packets transmitted, 0 received, 100% packet loss, time 59000ms\n",
    "garbage": "ping: unknown host {h}\n",
    "mac": "--- {h} ping statistics ---\n{n} packets transmitted, {n} packets received, 0.0% packet loss\n"
           "round-trip min/avg/max/stddev = 1.000/2.500/9.000/0.700 ms\n",
}


def fakes(d, public="ok", gateway="ok", route=True, mac=False):
    """Fake ping/ip/route in d/bin. Every call is logged to d/calls."""
    b = os.path.join(d, "bin")
    os.makedirs(b, exist_ok=True)
    log = os.path.join(d, "calls")
    gw_line = "default via 192.0.2.1 dev wlan0 proto dhcp src 192.0.2.50 metric 600\\n" if route else ""
    ping = """#!/bin/sh
echo "ping $*" >> '%s'
for a; do h=$a; done
n=$(echo "$*" | sed -n 's/.*-c \\([0-9]*\\).*/\\1/p')
case $h in 192.0.2.1) kind='%s' ;; *) kind='%s' ;; esac
case $kind in
""" % (log, gateway, public)
    for k, v in LINUX.items():
        ping += "  %s) printf '%%b' '%s' | sed \"s/{h}/$h/; s/{n}/$n/g\"; [ %s ] ;;\n" % (
            k, v.replace("\n", "\\n"), "1 = 0" if k in ("dead", "garbage") else "0 = 0")
    ping += "esac\n"
    files = {"ping": ping}
    if mac:
        files["route"] = "#!/bin/sh\necho \"route $*\" >> '%s'\nprintf '   route to: default\\ngateway: 192.0.2.1\\n'\n" % log
        files["ip"] = "#!/bin/sh\nexit 127\n"
    else:
        files["ip"] = "#!/bin/sh\necho \"ip $*\" >> '%s'\nprintf '%s'\n" % (log, gw_line)
        files["route"] = "#!/bin/sh\nexit 1\n"
    for name, text in files.items():
        p = os.path.join(b, name)
        with open(p, "w") as fh:
            fh.write(text)
        os.chmod(p, 0o755)
    return b, log


def ssh_stub(d, rc=None):
    """ssh stub: logs argv + the remote script, then runs it with sh (the fakes are on PATH). rc: fail without running."""
    p = os.path.join(d, "fake-ssh")
    log = os.path.join(d, "ssh-log")
    body = "#!/bin/sh\necho \"ARGV $*\" >> '%s'\n" % log
    body += ("exit %d\n" % rc) if rc is not None else "tee -a '%s' | sh -s\n" % log
    with open(p, "w") as fh:
        fh.write(body)
    os.chmod(p, 0o755)
    return p, log


def run(d, ssh="", host="1.1.1.1", seconds="10", extra=None, **fake):
    b, calls = fakes(d, **fake)
    env = dict(os.environ, PATH=b + ":/usr/bin:/bin", NET_CHECK_SSH=ssh, CABINET="coindojo@cab", NET_CHECK_HOST=host,
               NET_CHECK_SECONDS=seconds, NET_CHECK_INTERVAL="0.2")
    env.update(extra or {})
    r = subprocess.run(["bash", SCRIPT], env=env, capture_output=True, text=True, timeout=60)
    return r, (open(calls).read() if os.path.exists(calls) else "")


def last_result(out):
    lines = [line for line in out.splitlines() if line.startswith("RESULT:")]
    return lines[-1] if lines else ""


WRITE_VERBS = re.compile(r"(^|[\s;|&(`])(rm|mv|cp|tee|dd|kill|pkill|reboot|shutdown|mkdir|touch|chmod|chown|ln|"
                         r"nmcli|systemctl|sudo|apt|sed\s+-i)(\s|$)|(^|[^0-9<>=!&-])>>?\s*(/(?!dev/null)|~|\$|[A-Za-z_])")


def test_healthy_run_passes():
    with tempfile.TemporaryDirectory() as d:
        ssh, log = ssh_stub(d)
        r, calls = run(d, ssh=ssh)
        rows = {l.split()[0]: l.split() for l in r.stdout.splitlines() if l.split() and l.split()[0] in ("public", "gateway")}
        check(r.returncode == 0 and last_result(r.stdout).startswith("RESULT: PASS net-check (0 warning"),
              "healthy run PASSes: %s" % (last_result(r.stdout) or r.stderr[-300:]))
        check(rows.get("public", [])[1:7] == ["1.1.1.1", "50", "0%", "13.800", "56.300", "8.100"],
              "public row: host, sent, loss, avg, max, mdev %s" % rows.get("public"))
        check(rows.get("gateway", [])[1:3] == ["192.0.2.1", "50"], "gateway row from the default route %s" % rows.get("gateway"))
        check("(wlan0)" in r.stdout, "names the interface of the default route")
        check(calls.count("ping ") == 2 and "-c 50" in calls and "-i 0.2" in calls, "two pings, 10 s / 0.2 s = 50 each")
        script = open(log).read()
        check(script.count("ARGV ") == 1, "exactly one ssh call")
        bad = [l for l in script.splitlines() if WRITE_VERBS.search(l.replace("2>/dev/null", "").replace("2>&1", ""))]
        check(not bad, "the remote script only reads: %s" % bad)


def test_jitter_and_loss_warn():
    with tempfile.TemporaryDirectory() as d:
        ssh, _ = ssh_stub(d)
        r, _ = run(d, ssh=ssh, public="jitter")
        check(r.returncode == 0 and "RESULT: PASS net-check (2 warning(s))" in last_result(r.stdout),
              "jitter + loss still PASS with 2 warnings: %s" % last_result(r.stdout))
        check("WARN public 1.1.1.1: 7.5% loss" in r.stdout and "jitter mdev 29.800 ms" in r.stdout,
              "WARN names the loss and the mdev")
        r, _ = run(d, ssh=ssh, public="jitter", extra={"NET_WARN_LOSS": "10", "NET_WARN_MDEV": "50"})
        check("(0 warning(s))" in last_result(r.stdout), "thresholds come from NET_WARN_LOSS / NET_WARN_MDEV")


def test_macos_output_parses_locally():
    with tempfile.TemporaryDirectory() as d:
        r, calls = run(d, ssh="", public="mac", gateway="mac", mac=True)
        check(r.returncode == 0 and "on this machine" in r.stdout and last_result(r.stdout).startswith("RESULT: PASS"),
              "local run (no ssh) with macOS ping/route PASSes: %s" % (last_result(r.stdout) or r.stderr[-300:]))
        check(re.search(r"(?m)^public\s+1\.1\.1\.1\s+50\s+0\.0%\s+2\.500\s+9\.000\s+0\.700$", r.stdout),
              "macOS round-trip ... stddev parses")
        check("route -n get default" in calls, "gateway from `route -n get default` when there is no ip")


def test_broken_full_loss_fails():
    with tempfile.TemporaryDirectory() as d:
        ssh, _ = ssh_stub(d)
        r, _ = run(d, ssh=ssh, public="dead")
        check(r.returncode == 1 and last_result(r.stdout).startswith("RESULT: FAIL"), "BROKEN CASE: 100% loss FAILs")
        check("FAIL public 1.1.1.1: no reply (100% loss)" in r.stdout, "names the dead target")


def test_broken_no_route_fails():
    with tempfile.TemporaryDirectory() as d:
        ssh, _ = ssh_stub(d)
        r, calls = run(d, ssh=ssh, route=False)
        check(r.returncode == 1 and "FAIL gateway: no default route" in r.stdout, "BROKEN CASE: no default route FAILs")
        check(calls.count("ping ") == 1, "no gateway ping without a gateway")


def test_broken_unreachable_fails():
    with tempfile.TemporaryDirectory() as d:
        ssh, _ = ssh_stub(d, rc=255)
        r, calls = run(d, ssh=ssh)
        check(r.returncode == 1 and "RESULT: FAIL net-check: coindojo@cab unreachable" == last_result(r.stdout),
              "BROKEN CASE: unreachable cabinet FAILs: %s" % last_result(r.stdout))
        check("ping" not in calls, "nothing ran")


def test_broken_garbage_fails():
    with tempfile.TemporaryDirectory() as d:
        ssh, _ = ssh_stub(d)
        r, _ = run(d, ssh=ssh, public="garbage")
        check(r.returncode == 1 and "FAIL public 1.1.1.1: no ping summary" in r.stdout,
              "BROKEN CASE: unreadable ping output FAILs (never a made-up number)")


def test_usage_errors():
    with tempfile.TemporaryDirectory() as d:
        for kw, what in (({"host": "1.1.1.1'; rm -rf ~"}, "a quote in the host"), ({"host": ""}, "no host"),
                         ({"seconds": "5"}, "under 10 s"), ({"seconds": "601"}, "over 600 s"),
                         ({"seconds": "1m"}, "not a number"), ({"host": "-f"}, "a host starting with -"),
                         ({"host": "--flood"}, "an option as host"),
                         ({"extra": {"NET_CHECK_INTERVAL": "0"}}, "interval 0 (division by zero)"),
                         ({"extra": {"NET_CHECK_INTERVAL": "0.01"}}, "interval under 0.2 s (a flood)"),
                         ({"extra": {"NET_CHECK_INTERVAL": "."}}, "interval '.'"),
                         ({"extra": {"NET_CHECK_INTERVAL": "1.2.3"}}, "interval '1.2.3'")):
            r, calls = run(d, ssh="", **kw)
            check(r.returncode == 2 and "ping" not in calls, "BROKEN CASE: %s exits 2 before any ping" % what)


def test_config_pattern_rejects_option_hosts():
    """hk_net_check_host's pattern (config-check) refuses what the script refuses: a leading -."""
    text = open(os.path.join(ROOT, "config", "variables.d", "network.yml")).read()
    m = re.search(r'(?ms)^  hk_net_check_host:.*?^    pattern: "([^"]+)"', text)
    pat = re.compile(m.group(1)) if m else None
    check(pat is not None, "hk_net_check_host has a pattern")
    if pat:
        for host, ok in (("1.1.1.1", True), ("one.one.one.one", True), ("2606:4700::1111", True),
                         ("-f", False), ("--flood", False), ("a b", False), ("", False)):
            check(bool(pat.fullmatch(host)) == ok, "pattern %s %r" % ("accepts" if ok else "BROKEN CASE: refuses", host))


if __name__ == "__main__":
    main(globals())
