#!/usr/bin/env python3
"""Tests for coin-dojo-vm, the test VM on the Mac (#82). Plain python3, stdlib only; no VM, lima or docker
needed (the one docker check SKIPs without it).

Run: python3 vm/tests/test_vm.py

Checks:
  1. the lima template pins an official Debian 13 arm64 image (https, cloud.debian.org, dated dir,
     sha512), vz/aarch64, small sizing, user from config (hk_cabinet_user/uid), no mounts, ports only on 127.0.0.1;
  2. inventory separation: coin-dojo-vm is in group vm only, never at the cabinet's address; vm.yml
     targets `vm` and asserts the hostname; site.yml imports it last;
  3. vm_guard.py passes the real shape and FAILS planted cases (cabinet IP, a name that resolves to
     the cabinet, the VM put in group pios, LIMIT=coin-dojo, no ansible_port);
  4. make: help lists every vm-* target; LIMIT=coin-dojo-vm is accepted, LIMIT=bogus stops make;
     `make vm-guard` with a planted inventory fails, also under dash (#135); vm-destroy is a dry run without APPLY=1;
  5. the guest helpers: the test ROM is a valid iNES image, shotcheck tells red / UI / blank apart.
Broken cases are the planted ones in 3, 4 and 5: each must FAIL for the test to PASS.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VM = os.path.join(ROOT, "vm")
ANS = os.path.join(ROOT, "ansible")
sys.path.insert(0, VM)
sys.path.insert(0, os.path.join(VM, "guest"))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
import vm_guard     # noqa: E402
import mknesrom     # noqa: E402
from hktest import check, main, skip  # noqa: E402

CABINET_IP = "192.0.2.124"   # a documentation address standing in for the cabinet (the real one is local config)


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as fh:
        return fh.read()


def env():
    e = dict(os.environ)
    for k in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "ARCH", "EDITION", "APPLY", "V",
              "TAGS", "BECOME", "LIMIT"):
        e.pop(k, None)
    return e


def make(*args, cwd=ROOT):
    return subprocess.run(["make", "--no-print-directory", *args], cwd=cwd, env=env(),
                          capture_output=True, text=True)


# --- 1. lima template ------------------------------------------------------------------------------
def test_template():
    t = read("vm", "coin-dojo-test.yaml")
    loc = re.search(r'^- location: "([^"]+)"', t, re.M)
    dig = re.search(r'^  digest: "sha512:([0-9a-f]+)"', t, re.M)
    check(loc is not None and re.fullmatch(
        r"https://cloud\.debian\.org/images/cloud/trixie/(\d{8}-\d{4})/debian-13-genericcloud-arm64-\1\.qcow2",
        loc.group(1)) is not None, "template: image is an official dated Debian 13 arm64 genericcloud qcow2 over https")
    check(dig is not None and len(dig.group(1)) == 128, "template: image pinned by a 128-hex sha512 digest")
    check(len(re.findall(r"^- location:", t, re.M)) == 1, "template: exactly one image (no unpinned fallback)")
    check("daily" not in t and "latest" not in t, "template: no daily/latest image")
    for key, want in (("vmType", "vz"), ("arch", "aarch64"), ("cpus", "4"), ("memory", "4GiB"), ("disk", "20GiB"),
                      ("mounts", "[]")):
        m = re.search(r"^%s: (\S+)" % key, t, re.M)
        check(m is not None and m.group(1) == want, "template: %s = %s" % (key, want))
    check(re.search(r"^user:\n  name: cabinet\n  uid: 1000\n", t, re.M) is not None, "template: neutral user, overridden by vm-up from the config")
    check("/home/" not in t.split("\nuser:", 1)[1].split("\nssh:", 1)[0], "template: no home literal (vm-up passes hk_cabinet_home)")
    check(re.search(r"^  localPort: 60124$", t, re.M) is not None, "template: ssh on a fixed port 60124")
    # Every forwarded (non-ignored) rule binds the Mac's loopback only.
    rules = re.split(r"\n- ", t.split("portForwards:", 1)[1].split("\n\n", 1)[0])
    forwards = [r for r in rules if "ignore: true" not in r and "guestPort" in r]
    check(forwards and all('hostIP: "127.0.0.1"' in r for r in forwards), "template: port forwards bind 127.0.0.1 only")
    check("0.0.0.0" not in t.split("portForwards:", 1)[1].split("ignore: true")[0].replace('guestIP: "0.0.0.0"', ""),
          "template: no 0.0.0.0 host binding")
    check("hostnamectl set-hostname coin-dojo-vm" in t, "template: guest hostname coin-dojo-vm")
    # The make fallback port and the inventory port agree with the template.
    inv = read("ansible", "inventory", "vm.yml")
    check("ansible_port: 60124" in inv, "inventory port == template ssh.localPort")
    check("VM_VNC_PORT  := 5924" in read("mk", "vm.mk") and "hostPort: 5924" in t, "vnc port 5924 in mk/vm.mk and the template")


# --- 2. inventory separation -----------------------------------------------------------------------
def cabinet_groups(inv):
    """The cabinet's inventory groups (pios, arcade) used as a YAML key in an inventory text, comments ignored."""
    return re.findall(r"^\s*(pios|arcade):", re.sub(r"#.*", "", inv), re.M)


def test_inventory_separation():
    inv = read("ansible", "inventory", "vm.yml")
    hosts = read("ansible", "inventory", "hosts.yml")
    check(re.search(r"^    vm:\n      hosts:\n        coin-dojo-vm:\n", inv, re.M) is not None, "vm.yml: coin-dojo-vm in group vm")
    check(not cabinet_groups(inv), "vm.yml: no cabinet group (%s)" % cabinet_groups(inv))
    # Broken cases: the VM put under a cabinet group FAILs; a login user named `arcade` (the community export's stock
    # user until #265, now coindojo) is a value, not a group, and passes.
    planted = inv.replace("    vm:\n", "    arcade:\n", 1)
    check(cabinet_groups(planted) == ["arcade"], "broken case: a planted `arcade:` group FAILs (%s)" % cabinet_groups(planted))
    check(cabinet_groups("    pios:\n      hosts: {}\n") == ["pios"], "broken case: a planted `pios:` group FAILs")
    check(not cabinet_groups("        x:\n          ansible_user: arcade\n"), "ansible_user: arcade is not a group")
    check(CABINET_IP not in inv, "vm.yml: the cabinet IP appears nowhere")
    check("coin-dojo-vm" not in hosts, "hosts.yml (cabinet inventory) does not list the VM")
    check("inventory/hosts.yml,inventory/vm.yml" in read("ansible", "ansible.cfg"), "ansible.cfg loads both inventories")
    play = read("ansible", "vm.yml")
    check(re.search(r"^  hosts: vm$", play, re.M) is not None, "vm.yml play: hosts: vm")
    check("ansible_hostname == 'coin-dojo-vm'" in play and "ansible_host == hk_vm_host" in play, "vm.yml play: asserts hostname and the VM address")
    for pi_only in ("role: power", "role: dreamcast"):
        check(pi_only not in play, "vm.yml play skips the Pi-only %s" % pi_only)
    site = read("ansible", "site.yml")
    # the last play imports vm.yml (named or not: ansible-lint name[play] wants a name, #124)
    check(site.rstrip().endswith("import_playbook: vm.yml"), "site.yml imports vm.yml last")
    base = read("ansible", "roles", "vm_base", "tasks", "main.yml")
    check("ansible_hostname == vm_base_hostname" in base, "vm_base asserts the hostname itself")


# --- 3. vm_guard -----------------------------------------------------------------------------------
def inventory(vm_host="host.lima.internal", vm_groups=("vm",), port=60124):
    hv = {"coin-dojo": {"ansible_host": CABINET_IP}, "batocera": {"ansible_host": CABINET_IP},
          "coin-dojo-vm": {"ansible_host": vm_host, "ansible_user": "coindojo"}}
    if port is not None:
        hv["coin-dojo-vm"]["ansible_port"] = port
    inv = {"_meta": {"hostvars": hv}, "all": {"children": ["ungrouped", "arcade", "pios", "vm"]},
           "arcade": {"hosts": ["batocera"]}, "pios": {"hosts": ["coin-dojo"]}, "vm": {"hosts": []}}
    for g in vm_groups:
        inv[g]["hosts"].append("coin-dojo-vm")
    return inv


def fake_resolver(table):
    return lambda name: set(table.get(name, ()))


def test_vm_guard():
    ok = vm_guard.check(inventory(), "coin-dojo-vm", fake_resolver({}), known=set())
    check(ok == [], "vm_guard: the real shape passes (%s)" % ok)
    planted = [
        ("ansible_host = cabinet IP", inventory(vm_host=CABINET_IP), "coin-dojo-vm", {}),
        ("ansible_host = coindojo.lan", inventory(vm_host="coindojo.lan"), "coin-dojo-vm", {}),
        ("ansible_host resolves to the cabinet", inventory(vm_host="sneaky.lan"), "coin-dojo-vm",
         {"sneaky.lan": [CABINET_IP]}),
        ("VM also in group pios", inventory(vm_groups=("vm", "pios")), "coin-dojo-vm", {}),
        ("VM only in group pios", inventory(vm_groups=("pios",)), "coin-dojo-vm", {}),
        ("LIMIT=coin-dojo (the cabinet)", inventory(), "coin-dojo", {}),
        ("LIMIT not in the inventory", inventory(), "nope", {}),
        ("no ansible_port", inventory(port=None), "coin-dojo-vm", {}),
    ]
    for name, inv, limit, table in planted:
        problems = vm_guard.check(inv, limit, fake_resolver(table), known=set())
        check(problems != [], "vm_guard broken case FAILS: %s -> %s" % (name, problems[:1]))
    # #131: the cabinet's address comes from the config (hk_cabinet_host / hk_cabinet_hostname), not a constant.
    solo = inventory(vm_host="my-cab.example")
    solo["pios"]["hosts"] = []          # an inventory that does not list the cabinet: only the config knows it
    check(vm_guard.check(solo, "coin-dojo-vm", fake_resolver({}), known=set()) == [], "vm_guard: unknown host passes without config")
    check(vm_guard.check(solo, "coin-dojo-vm", fake_resolver({}), known={"my-cab.example"}) != [],
          "vm_guard broken case FAILS: ansible_host = the configured hk_cabinet_host")
    # #236: a name the export does not rename (its rename of the project name has no single case-insensitive form)
    named = inventory(vm_host="MyCab1.local")
    check(vm_guard.check(named, "coin-dojo-vm", fake_resolver({}), known=vm_guard.configured_addrs(
        lambda n: {"hk_cabinet_host": "", "hk_cabinet_hostname": "mycab1"}[n])) != [],
          "vm_guard broken case FAILS: ansible_host = <hk_cabinet_hostname>.local")
    # an unreadable config refuses (the guard cannot know the cabinet), it never silently passes
    old = os.environ.get("CONFIG_PYTHON")
    os.environ["CONFIG_PYTHON"] = "/nonexistent/python"
    try:
        problems = vm_guard.check(inventory(), "coin-dojo-vm", fake_resolver({}))
    finally:
        if old is None:
            os.environ.pop("CONFIG_PYTHON")
        else:
            os.environ["CONFIG_PYTHON"] = old
    check(problems != [] and "refusing" in problems[0], "vm_guard broken case FAILS: unreadable config refuses (%s)" % problems[:1])


# --- 4. make ---------------------------------------------------------------------------------------
def test_make():
    r = make("help")
    for t in ("vm-up", "vm-down", "vm-ssh", "vm-status", "vm-provision", "vm-smoke", "vm-watch", "vm-destroy"):
        check(re.search(r"^  %s\s+\S" % t, r.stdout, re.M) is not None, "make help lists %s" % t)
    r = make("-C", "ansible", "help")
    check("coin-dojo-vm" in r.stdout, "ansible help documents LIMIT=coin-dojo-vm")
    r = make("-C", "ansible", "help", "LIMIT=coin-dojo-vm")
    check(r.returncode == 0, "LIMIT=coin-dojo-vm is accepted")
    r = make("-C", "ansible", "help", "LIMIT=bogus")
    check(r.returncode != 0 and "LIMIT='bogus' is not valid" in r.stderr, "LIMIT=bogus stops make")
    r = make("-C", "ansible", "help", "LIMIT=" + CABINET_IP)
    check(r.returncode != 0, "LIMIT=<cabinet IP> stops make")

    tmp = tempfile.mkdtemp(prefix="hk-vm-test-")
    try:
        good, bad = os.path.join(tmp, "good.json"), os.path.join(tmp, "bad.json")
        json.dump(inventory(), open(good, "w"))
        json.dump(inventory(vm_host=CABINET_IP), open(bad, "w"))
        r = make("-C", "ansible", "vm-guard", "LIMIT=coin-dojo-vm", "VM_INVENTORY_JSON=" + good)
        check(r.returncode == 0 and "RESULT: PASS vm-guard" in r.stdout, "make vm-guard passes the real shape")
        r = make("-C", "ansible", "vm-guard", "LIMIT=coin-dojo-vm", "VM_INVENTORY_JSON=" + bad)
        check(r.returncode != 0 and "RESULT: FAIL vm-guard" in r.stdout, "make vm-guard FAILS a planted cabinet-IP VM")
        # #135: dash (Debian's /bin/sh, the Forgejo runner) exits on `set -o pipefail`; the guard must run under it,
        # and without pipefail an unreadable inventory must still FAIL (vm_guard.py refuses empty JSON).
        dash = shutil.which("dash")
        if dash:
            r = make("SHELL=" + dash, "-C", "ansible", "vm-guard", "LIMIT=coin-dojo-vm", "VM_INVENTORY_JSON=" + good)
            check(r.returncode == 0 and "RESULT: PASS vm-guard" in r.stdout,
                  "make vm-guard under dash passes the real shape (rc %d %s)" % (r.returncode, r.stderr.strip()[-80:]))
            r = make("SHELL=" + dash, "-C", "ansible", "vm-guard", "LIMIT=coin-dojo-vm",
                     "VM_INVENTORY_JSON=" + os.path.join(tmp, "missing.json"))
            check(r.returncode != 0 and "RESULT: FAIL vm-guard" in r.stdout,
                  "make vm-guard under dash FAILS an unreadable inventory (no pipefail there)")
        else:
            skip("make vm-guard under dash: no dash on this machine")
        # deploy with the planted inventory stops at vm-guard, before Ansible (DEPLOY would fail loudly).
        r = make("-C", "ansible", "deploy", "LIMIT=coin-dojo-vm", "VM_INVENTORY_JSON=" + bad,
                 "DEPLOY=false-deploy-must-not-run")
        check(r.returncode != 0 and "false-deploy-must-not-run" not in (r.stdout + r.stderr),
              "make deploy LIMIT=coin-dojo-vm stops at vm-guard on a planted cabinet IP")
        r = make("-n", "-C", "ansible", "deploy", "LIMIT=coin-dojo-vm", "APPLY=1", "VM_INVENTORY_JSON=" + good)
        check("cabinet-guard" not in r.stdout and CABINET_IP not in r.stdout,
              "make deploy LIMIT=coin-dojo-vm APPLY=1 never runs cabinet-guard (no cabinet SSH)")
        # vm-destroy is a dry run without APPLY=1 (stub limactl that records its calls).
        stub = os.path.join(tmp, "limactl")
        log = os.path.join(tmp, "calls")
        with open(stub, "w") as fh:
            fh.write('#!/bin/sh\necho "$@" >> %s\n[ "$1" = list ] && echo Stopped\nexit 0\n' % log)
        os.chmod(stub, 0o755)
        r = make("vm-destroy", "LIMACTL=" + stub)
        calls = open(log).read() if os.path.exists(log) else ""
        check(r.returncode == 0 and "DRY RUN vm-destroy" in r.stdout and "delete" not in calls, "vm-destroy is a dry run by default")
        r = make("vm-destroy", "APPLY=1", "LIMACTL=" + stub)
        calls = open(log).read()
        check("delete --force coin-dojo-vm" in calls, "vm-destroy APPLY=1 deletes coin-dojo-vm only")
        r = make("vm-smoke", "LIMACTL=" + stub, "VM_CPU_GATE=")
        check(r.returncode != 0 and "not running" in r.stderr, "vm-smoke refuses a stopped VM")
        # vm-up creates the VM with the cabinet's login from the config (#149): name, uid and home as lima --set.
        newlog = os.path.join(tmp, "calls-new")
        stub_new = os.path.join(tmp, "limactl-new")
        with open(stub_new, "w") as fh:
            fh.write('#!/bin/sh\necho "$@" >> %s\nexit 0\n' % newlog)
        os.chmod(stub_new, 0o755)
        r = make("vm-up", "LIMACTL=" + stub_new, "VM_CPU_GATE=", "hk_cabinet_user=zed", "hk_cabinet_uid=4242")
        calls = open(newlog).read() if os.path.exists(newlog) else ""
        check(r.returncode == 0 and '.user.name = "zed"' in calls and ".user.uid = 4242" in calls
              and '.user.home = "/home/zed"' in calls, "vm-up passes the configured user, uid and home to lima: %s" % calls[-160:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_real_inventory_with_docker():
    """The real inventory through ansible-inventory (container). SKIP without docker."""
    if shutil.which("docker") is None or subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        skip("real inventory check (docker is not running)")
        return
    r = make("-C", "ansible", "vm-guard", "LIMIT=coin-dojo-vm")
    check(r.returncode == 0 and "RESULT: PASS vm-guard" in r.stdout, "real inventory: vm-guard passes coin-dojo-vm (%s)" % r.stdout.strip()[-120:])
    r = make("-C", "ansible", "vm-guard", "LIMIT=coin-dojo")
    check(r.returncode != 0, "real inventory: vm-guard refuses LIMIT=coin-dojo (the cabinet)")


# --- 5. guest helpers ------------------------------------------------------------------------------
def ppm(w, h, rgb_fn):
    data = bytearray()
    for y in range(h):
        for x in range(w):
            data += bytes(rgb_fn(x, y))
    return b"P6\n%d %d\n255\n" % (w, h) + bytes(data)


def test_guest_helpers():
    rom = mknesrom.build()
    check(len(rom) == 16 + 16384 + 8192 and rom[:4] == b"NES\x1a" and rom[4] == 1 and rom[5] == 1,
          "test ROM: iNES header, 16 KB PRG + 8 KB CHR (%d bytes)" % len(rom))
    prg = rom[16:16 + 16384]
    check(prg[0x3FFC] | (prg[0x3FFD] << 8) == 0xC000, "test ROM: reset vector -> $C000")
    check(prg[0x3D:0x40] == bytes([0x4C, 0x3D, 0xC0]), "test ROM: ends in JMP-to-self at $C03D")
    check(prg[0x40] == 0x40, "test ROM: NMI/IRQ handler at $C040 is RTI")
    shot = os.path.join(VM, "guest", "shotcheck.py")
    tmp = tempfile.mkdtemp(prefix="hk-shot-")
    try:
        cases = {
            "red": (ppm(64, 48, lambda x, y: (200, 40, 30)), "--red", 0),
            "ui": (ppm(64, 48, lambda x, y: ((x * 4) % 256, (y * 5) % 256, 90)), "--ui", 0),
            "blank-as-ui": (ppm(64, 48, lambda x, y: (0, 0, 0)), "--ui", 1),        # broken: black frame
            "grey-as-red": (ppm(64, 48, lambda x, y: (120, 120, 120)), "--red", 1),  # broken: core not running
        }
        for name, (data, mode, want) in cases.items():
            p = os.path.join(tmp, name + ".ppm")
            open(p, "wb").write(data)
            r = subprocess.run([sys.executable, "-I", shot, p, mode], capture_output=True, text=True)
            check(r.returncode == want, "shotcheck %s %s -> exit %d (want %d)" % (name, mode, r.returncode, want))
        p = os.path.join(tmp, "empty.ppm")
        open(p, "wb").write(b"")
        r = subprocess.run([sys.executable, "-I", shot, p, "--ui"], capture_output=True, text=True)
        check(r.returncode != 0, "shotcheck: an empty screenshot FAILS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for f in ("hk-session", "smoke.sh"):
        r = subprocess.run(["bash", "-n", os.path.join(VM, "guest", f)], capture_output=True, text=True)
        check(r.returncode == 0, "bash -n guest/%s" % f)


if __name__ == "__main__":
    main(globals())
