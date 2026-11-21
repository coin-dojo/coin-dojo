#!/usr/bin/env python3
"""Refuse a VM deploy whose target could be the cabinet (#82). Stdlib only.

    bin/ans ansible-inventory --list | python3 -I vm/vm_guard.py --limit coin-dojo-vm

Reads `ansible-inventory --list` JSON on stdin (or --json FILE). PASS only when the LIMIT host:
  - is in group `vm` and in neither cabinet group (`pios`, `arcade`);
  - has an ansible_host that is not the cabinet's (hk_cabinet_host and hk_cabinet_hostname from the config, the image's
    generic names coindojo.lan / coindojo / coin-dojo, or the ansible_host of any cabinet-group host), also after resolving
    it on this Mac when it resolves. When the config cannot be read at all the guard REFUSES (it cannot know the
    cabinet's address); a config with no cabinet host (no cabinet to protect) is fine;
  - has an ansible_port (the lima forward), so a plain port-22 host is never taken for the VM.
Prints `RESULT: PASS|FAIL vm-guard ...`; exit 0 / 1 (2 = unreadable input).
"""
import argparse
import json
import os
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import hkconfig  # noqa: E402  (tools/hkconfig.py: the one way a script reads the config)

CABINET_GROUPS = ("pios", "arcade")
GENERIC_ADDRS = {"coindojo.lan", "coindojo", "coin-dojo"}   # names of the stock image, not anybody's address


def configured_addrs(read=hkconfig.get):
    """The cabinet's addresses from the config (hk_cabinet_host, hk_cabinet_hostname[.local]). Raises ConfigUnavailable."""
    out = set()
    for name in ("hk_cabinet_host", "hk_cabinet_hostname"):
        v = read(name).strip().lower()
        if v:
            out |= {v, v + ".local"} if name == "hk_cabinet_hostname" else {v}
    return out


def group_hosts(inv, group, seen=None):
    """All hosts of a group, children included."""
    seen = seen if seen is not None else set()
    if group in seen or group not in inv:
        return set()
    seen.add(group)
    node = inv.get(group) or {}
    hosts = set(node.get("hosts", []))
    for child in node.get("children", []):
        hosts |= group_hosts(inv, child, seen)
    return hosts


def resolve(name):
    try:
        return {ai[4][0] for ai in socket.getaddrinfo(name, None)}
    except (socket.gaierror, UnicodeError, OSError):
        return set()


def check(inv, limit, resolver=resolve, known=None):
    """Return a list of problems (empty = safe). known: the cabinet's configured addresses (None = read the config;
    an unreadable config is a problem: the guard cannot know the cabinet's address, so it refuses)."""
    if known is None:
        try:
            known = configured_addrs()
        except hkconfig.ConfigUnavailable as exc:
            return ["cannot read the cabinet's address from the config (%s); refusing" % exc]
    hostvars = (inv.get("_meta") or {}).get("hostvars") or {}
    problems = []
    vm_hosts = group_hosts(inv, "vm")
    cabinet_hosts = set()
    for g in CABINET_GROUPS:
        cabinet_hosts |= group_hosts(inv, g)
    if limit not in hostvars and limit not in vm_hosts:
        return ["host %r is not in the inventory" % limit]
    if limit not in vm_hosts:
        problems.append("%s is not in group vm" % limit)
    if limit in cabinet_hosts:
        problems.append("%s is in a cabinet group (%s)" % (limit, "/".join(CABINET_GROUPS)))
    hv = hostvars.get(limit, {})
    addr = str(hv.get("ansible_host", limit)).strip().lower()
    cabinet_addrs = set(GENERIC_ADDRS) | set(known)
    for h in cabinet_hosts:
        cabinet_addrs.add(str(hostvars.get(h, {}).get("ansible_host", h)).strip().lower())
    if addr in cabinet_addrs:
        problems.append("%s ansible_host %s is the cabinet's address" % (limit, addr))
    cabinet_ips = set()
    for a in cabinet_addrs:
        cabinet_ips |= {a} if a.replace(".", "").isdigit() else resolver(a)
    hit = resolver(addr) & cabinet_ips
    if hit:
        problems.append("%s ansible_host %s resolves to the cabinet (%s)" % (limit, addr, ", ".join(sorted(hit))))
    if "ansible_port" not in hv:
        problems.append("%s has no ansible_port (the VM is reached through the lima forward)" % limit)
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", required=True)
    ap.add_argument("--json", help="inventory JSON file (default: stdin)")
    a = ap.parse_args(argv)
    try:
        raw = open(a.json).read() if a.json else sys.stdin.read()
        inv = json.loads(raw)
    except (OSError, ValueError) as exc:
        print("RESULT: FAIL vm-guard: unreadable inventory JSON (%s)" % exc)
        return 2
    problems = check(inv, a.limit)
    if problems:
        for p in problems:
            print("vm-guard: " + p)
        print("RESULT: FAIL vm-guard: LIMIT=%s could be the cabinet; refusing" % a.limit)
        return 1
    print("RESULT: PASS vm-guard: LIMIT=%s is the VM (group vm, %s:%s)" % (
        a.limit, inv["_meta"]["hostvars"][a.limit].get("ansible_host"),
        inv["_meta"]["hostvars"][a.limit].get("ansible_port")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
