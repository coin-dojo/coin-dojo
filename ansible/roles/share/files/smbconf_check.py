#!/usr/bin/env python3
"""Check a network-folder smb.conf (#266): exactly the two shares, no guest, SMB3, LAN only, no escape hatches.

    smbconf_check.py --user U --share roms=/data/roms --share bios=/data/bios [--interfaces "lo eth0 wlan0"]
                     [--bios-veto NAME ...] [--roms-veto NAME ...] [--testparm] FILE
    smbconf_check.py --hosts-only FILE   the live hosts file (hosts_allow.py); smbd's ExecStartPre runs it, so a bad
                                         or missing file stops smbd (fail closed)

Exit 0 and one `OK` line when the file is safe; else one `FAIL: <why>` line per problem and exit 1. The role runs it
as the template's `validate:` (a bad render is never installed); the role tests run it on broken copies. Stdlib only.
With --testparm it also asks Samba's own parser (`testparm -s`), which catches unknown parameters and typos.
"""
import argparse
import ipaddress
import re
import subprocess
import sys

SMB3 = {"smb3", "smb3_00", "smb3_02", "smb3_11"}
# Parameter names Samba accepts as synonyms, folded to one name before the rules run.
SYNONYMS = {
    "public": "guest ok",
    "only guest": "guest only",
    "min protocol": "server min protocol",
    "protocol": "server max protocol",
    "allow hosts": "hosts allow",
    "deny hosts": "hosts deny",
    "writeable": "writable",
    "write ok": "writable",
    "directory": "path",
    "create mode": "create mask",
    "directory mode": "directory mask",
    "user": "username",
    "users": "username",
    "browsable": "browseable",
}
# Never in this file: they run commands, pull in other config, or open more shares.
FORBIDDEN = ("config file", "usershare path", "root preexec", "root postexec", "preexec", "exec",
             "postexec", "magic script", "magic output", "add share command", "change share command",
             "delete share command", "add user script", "add machine script", "message command", "print command",
             "panic action", "registry shares", "admin users", "copy", "guest account", "username map script",
             "include system krb5 conf")
LOOPBACK = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "::1/128")]
# What the live hosts file (hosts_allow.py) may allow: loopback, IPv6 link-local, private IPv4 subnets.
HOSTS_FILE_OK = LOOPBACK + [ipaddress.ip_network(n) for n in ("fe80::/10", "10.0.0.0/8", "172.16.0.0/12",
                                                              "192.168.0.0/16", "169.254.0.0/16")]
HOSTS_FILE = "/run/coin-dojo-share/hosts.conf"
TRUE, FALSE = {"yes", "true", "1", "on"}, {"no", "false", "0", "off"}


def parse(text):
    """{section: {param: value}} in file order; names lowercased with single spaces, synonyms folded. A param twice
    in one section keeps the last value (as Samba does). Continuation lines (a trailing backslash) are joined."""
    secs, cur = {}, None
    text = re.sub(r"\\\n", " ", text)
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        m = re.match(r"^\[([^\]]+)\]$", line)
        if m:
            cur = secs.setdefault(m.group(1).strip().lower(), {})
            continue
        if "=" not in line:
            secs.setdefault("?", {})[line] = ""
            continue
        k, v = line.split("=", 1)
        k = re.sub(r"\s+", " ", k.strip().lower())
        k = SYNONYMS.get(k, k)
        if cur is None:
            cur = secs.setdefault("global", {})
        cur[k] = v.strip()
    return secs


def _is(v, values):
    return (v or "").strip().lower() in values


def _within(token, nets):
    """True when a hosts allow token is an address or network inside one of nets."""
    t = token.strip("[]")
    try:
        net = ipaddress.ip_network(t, strict=False)
    except ValueError:
        return False
    return any(net.version == p.version and net.subnet_of(p) for p in nets)


def global_order(text):
    """The parameter names of [global] in file order (synonyms folded), for `include` after `hosts allow`."""
    out, cur = [], "global"
    for raw in re.sub(r"\\\n", " ", text).splitlines():
        line = raw.strip()
        m = re.match(r"^\[([^\]]+)\]$", line)
        if m:
            cur = m.group(1).strip().lower()
        elif cur == "global" and "=" in line and line[0] not in "#;":
            k = re.sub(r"\s+", " ", line.split("=", 1)[0].strip().lower())
            out.append(SYNONYMS.get(k, k))
    return out


def hosts_file_problems(text):
    """The live hosts file (hosts_allow.py): only `hosts allow = ...` with loopback, fe80::/10, private IPv4 nets."""
    p = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        k, sep, v = line.partition("=")
        if not sep or re.sub(r"\s+", " ", k.strip().lower()) != "hosts allow":
            p.append("hosts file: %r is not a hosts allow line" % line)
            continue
        bad = [t for t in v.replace(",", " ").split() if not _within(t, HOSTS_FILE_OK)]
        if bad:
            p.append("hosts file allows non-local entries %s" % bad)
    return p


def problems(text, user, shares, interfaces=("lo", "eth0", "wlan0"), hosts_file=HOSTS_FILE, bios_veto=(),
             roms_veto=()):
    """The list of problems (empty = safe). shares = {name: path}; bios_veto / roms_veto = names that share must
    veto."""
    p = []
    secs = parse(text)
    if "?" in secs:
        p.append("lines that are neither a section nor a parameter: %s" % sorted(secs["?"]))
    g = secs.get("global")
    if g is None:
        return p + ["no [global] section"]
    have = sorted(s for s in secs if s not in ("global", "?"))
    if have != sorted(shares):
        p.append("shares %s, want exactly %s (no homes, printers or other folders)" % (have, sorted(shares)))
    if (g.get("server min protocol") or "").lower() not in SMB3:
        p.append("server min protocol is %r, want SMB3" % g.get("server min protocol"))
    if not _is(g.get("map to guest"), {"never"}):
        p.append("map to guest is %r, want never (guest off)" % g.get("map to guest"))
    if g.get("restrict anonymous") != "2":
        p.append("restrict anonymous is %r, want 2 (no anonymous listing)" % g.get("restrict anonymous"))
    if not _is(g.get("bind interfaces only"), TRUE):
        p.append("bind interfaces only is not yes")
    if (g.get("interfaces") or "").split() != list(interfaces):
        p.append("interfaces is %r, want %r" % (g.get("interfaces"), " ".join(interfaces)))
    allow = (g.get("hosts allow") or "").replace(",", " ").split()
    if not allow:
        p.append("no hosts allow (any address could connect)")
    bad = [t for t in allow if not _within(t, LOOPBACK)]
    if bad:
        p.append("hosts allow in smb.conf has more than loopback %s (the live subnets come from the include)" % bad)
    if (g.get("hosts deny") or "").strip().upper() != "ALL":
        p.append("hosts deny is %r, want ALL" % g.get("hosts deny"))
    order = global_order(text)
    if g.get("include") != hosts_file:
        p.append("[global] include is %r, want exactly %r (the live subnets)" % (g.get("include"), hosts_file))
    elif "hosts allow" not in order or order.count("include") != 1 \
            or max(i for i, k in enumerate(order) if k == "hosts allow") > order.index("include"):
        p.append("[global] include must come once, after hosts allow (so the live list replaces loopback)")
    if g.get("smb ports", "").split() != ["445"]:
        p.append("smb ports is %r, want 445 only" % g.get("smb ports"))
    if not _is(g.get("disable netbios"), TRUE):
        p.append("disable netbios is not yes")
    if not _is(g.get("load printers"), FALSE) or not _is(g.get("disable spoolss"), TRUE):
        p.append("printing is not off (load printers = no, disable spoolss = yes)")
    if (g.get("vfs objects") or "").split() != ["fruit", "streams_xattr"] or g.get("fruit:resource") != "stream":
        p.append("macOS forks: want vfs objects = fruit streams_xattr and fruit:resource = stream (else Finder "
                 "leaves ._ files the game menu lists)")
    if g.get("usershare max shares") != "0":
        p.append("usershare max shares is not 0 (users could add shares)")
    for name, sec in secs.items():
        if name == "?":
            continue
        if name != "global" and "include" in sec:
            p.append("[%s] has 'include' (pulls in config)" % name)
        for k in sec:
            if k in FORBIDDEN:
                p.append("[%s] has %r (runs commands, pulls in config or widens access)" % (name, k))
        if _is(sec.get("guest ok"), TRUE) or _is(sec.get("guest only"), TRUE):
            p.append("[%s] allows guests" % name)
        if _is(sec.get("usershare allow guests"), TRUE):
            p.append("[%s] lets user shares allow guests" % name)
        for k in ("follow symlinks", "wide links", "allow insecure wide links"):
            want = FALSE
            v = sec.get(k)
            if name == "global" and k != "allow insecure wide links" and not _is(v, want):
                p.append("[global] %s is %r, want no" % (k, v))
            elif v is not None and not _is(v, want):
                p.append("[%s] %s is %r, want no" % (name, k, v))
    for name, path in shares.items():
        s = secs.get(name)
        if s is None:
            continue
        want = {"path": path, "valid users": user, "write list": user, "force user": user,
                "create mask": "0644", "force create mode": "0644",
                "directory mask": "0755", "force directory mode": "0755"}
        for k, v in want.items():
            if (s.get(k) or "").strip() != v:
                p.append("[%s] %s is %r, want %r" % (name, k, s.get(k), v))
        if not _is(s.get("guest ok"), FALSE):
            p.append("[%s] guest ok is not explicitly no" % name)
        if "invalid users" in s and user in s["invalid users"].split():
            p.append("[%s] locks out its own user" % name)
        for k in ("read list", "hosts allow", "hosts deny"):
            if k in s:
                p.append("[%s] overrides %s (keep access in [global] and valid users)" % (name, k))
    for name, want in (("bios", bios_veto), ("roms", roms_veto)):
        if name in shares and name in secs:
            vetoed = {x for x in (secs[name].get("veto files") or "").split("/") if x}
            missing = [x for x in want if x not in vetoed]
            if missing:
                p.append("[%s] does not veto %s (emulator scripts/settings could be planted)" % (name, missing))
    return p


def testparm(path):
    """Samba's own parser: exit 0 and no 'Unknown parameter' line, else one problem."""
    try:
        r = subprocess.run(["testparm", "-s", "--suppress-prompt", path], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return ["testparm did not run: %s" % e]
    err = r.stderr + r.stdout
    bad = [l.strip() for l in err.splitlines() if re.search(r"(?i)unknown parameter|ignoring unknown|error", l)]
    if r.returncode != 0 or bad:
        return ["testparm rejects it (exit %d): %s" % (r.returncode, "; ".join(bad[:5]) or err.strip()[-300:])]
    return []


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hosts-only", action="store_true", help="FILE is the live hosts file")
    ap.add_argument("--user")
    ap.add_argument("--share", action="append", default=[], metavar="NAME=PATH")
    ap.add_argument("--interfaces", default="lo eth0 wlan0")
    ap.add_argument("--hosts-file", default=HOSTS_FILE)
    ap.add_argument("--bios-veto", action="append", default=[], metavar="NAME")
    ap.add_argument("--roms-veto", action="append", default=[], metavar="NAME")
    ap.add_argument("--testparm", action="store_true")
    ap.add_argument("file")
    a = ap.parse_args(argv)
    if a.hosts_only:
        try:
            with open(a.file, encoding="utf-8") as fh:
                p = hosts_file_problems(fh.read())
        except OSError as e:
            p = ["hosts file unreadable: %s" % e]
        for x in p:
            print("FAIL: " + x)
        if not p:
            print("OK %s: loopback, link-local and the live local subnets only" % a.file)
        return 1 if p else 0
    if not a.user or not a.share:
        ap.error("--user and --share are required (or --hosts-only)")
    shares = {}
    for s in a.share:
        name, sep, path = s.partition("=")
        if not sep or not name or not path.startswith("/"):
            ap.error("--share wants NAME=/absolute/path, not %r" % s)
        shares[name.lower()] = path
    with open(a.file, encoding="utf-8") as fh:
        text = fh.read()
    p = problems(text, a.user, shares, tuple(a.interfaces.split()), a.hosts_file, a.bios_veto, a.roms_veto)
    if a.testparm and not p:
        p += testparm(a.file)
    for x in p:
        print("FAIL: " + x)
    if p:
        return 1
    print("OK %s: shares %s, user %s, SMB3, guest off, live subnets only, vetoes bios %s roms %s"
          % (a.file, ", ".join(sorted(shares)), a.user, a.bios_veto or "none", a.roms_veto or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
