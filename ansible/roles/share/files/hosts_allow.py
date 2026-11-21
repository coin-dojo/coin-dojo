#!/usr/bin/env python3
"""Write the network folder's `hosts allow` from the cabinet's LIVE interface subnets (#266).

    hosts_allow.py --interfaces "eth0 wlan0" --out /run/coin-dojo-share/hosts.conf [--reload] [--ip-json FILE]

smb.conf allows only loopback itself and then includes --out, so a missing or empty file fails closed (loopback only).
This writes: loopback, fe80::/10 (IPv6 link-local: the same link only) and, for each listed interface, the IPv4
subnet of each PRIVATE address it has now (10/8, 172.16/12, 192.168/16, 169.254/16). A public address adds nothing.
So a client on another private network (a neighbour's 10.x, a VPN's 172.16.x) is refused even when it can route here.

Run by smbd's ExecStartPre (every smbd start) and by the NetworkManager dispatcher on up/down/address changes, then
`smbcontrol smbd reload-config` (--reload): new connections see the new list, open copies are not cut. Stdlib only;
`--ip-json` (tests) reads `ip -j -4 addr` output from a file instead of running ip.
"""
import argparse
import ipaddress
import json
import os
import subprocess
import sys
import tempfile

BASE = ["127.0.0.1", "::1", "fe80::/10"]
PRIVATE_V4 = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")]


def subnets(ip_json, interfaces):
    """The private IPv4 networks of the named interfaces, in order, without duplicates."""
    out = []
    for link in ip_json:
        if link.get("ifname") not in interfaces:
            continue
        for a in link.get("addr_info", []):
            if a.get("family") != "inet" or "local" not in a or "prefixlen" not in a:
                continue
            try:
                net = ipaddress.ip_interface("%s/%s" % (a["local"], a["prefixlen"])).network
            except ValueError:
                continue
            if any(net.subnet_of(p) for p in PRIVATE_V4) and str(net) not in out:
                out.append(str(net))
    return out


def render(nets):
    return ("# coin-dojo network folder (#266): written by hosts_allow.py from the live interface subnets.\n"
            "hosts allow = %s\n" % " ".join(BASE + nets))


def write_if_changed(path, text):
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read() == text:
                return False
    except OSError:
        pass
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o755, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".hosts.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--interfaces", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--reload", action="store_true")
    ap.add_argument("--ip-json")
    a = ap.parse_args(argv)
    try:
        if a.ip_json:
            with open(a.ip_json, encoding="utf-8") as fh:
                data = json.load(fh)
        else:
            data = json.loads(subprocess.run(["ip", "-j", "-4", "addr", "show"], capture_output=True, text=True,
                                             check=True, timeout=10).stdout or "[]")
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        print("hosts_allow: cannot read the interfaces (%s): loopback and link-local only" % e, file=sys.stderr)
        data = []
    nets = subnets(data, a.interfaces.split())
    changed = write_if_changed(a.out, render(nets))
    print("hosts_allow: %s %s" % ("WROTE" if changed else "SAME", " ".join(BASE + nets)))
    if changed and a.reload:
        r = subprocess.run(["systemctl", "is-active", "--quiet", "smbd.service"])
        if r.returncode == 0:
            subprocess.run(["smbcontrol", "smbd", "reload-config"], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
