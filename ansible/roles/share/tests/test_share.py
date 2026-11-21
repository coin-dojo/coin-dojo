#!/usr/bin/env python3
"""Tests for the `share` role (#266): the network folder (SMB) for the ROM and BIOS folders.

    python3 ansible/roles/share/tests/test_share.py

Stdlib only. The template is rendered by a small renderer that knows exactly the Jinja forms the role's templates use
(test_templates_use_only_known_jinja fails on any other form, so the render stays faithful); the real Ansible render
is checked by the same smbconf_check.py as the template's validate: on the test VM. Every rule has a broken case.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANSIBLE = os.path.dirname(os.path.dirname(ROLE))
REPO = os.path.dirname(ANSIBLE)
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
sys.path.insert(0, os.path.join(ROLE, "files"))
sys.path.insert(0, os.path.join(ANSIBLE, "tests"))
from hktest import check, main  # noqa: E402
import hosts_allow as ha  # noqa: E402
import smbconf_check as sc  # noqa: E402
from test_cabinet_roles import rule_no_hidden_knob  # noqa: E402

USER = "coindojo"
SHARES = {"roms": "/data/roms", "bios": "/data/bios"}
VETO = [".DS_Store", "Thumbs.db", "desktop.ini"]
BIOS_VETO = ["mame", "*.lua"]
ROMS_VETO = ["mame", "*.lua", "*.cmd"]
HOSTS_FILE = "/run/coin-dojo-share/hosts.conf"
VARS = {
    "share_user": USER, "share_group": USER,
    "share_folders": [{"name": "roms", "path": "/data/roms", "comment": "Games: one folder per system", "veto": VETO + ROMS_VETO},
                      {"name": "bios", "path": "/data/bios", "comment": "BIOS files", "veto": VETO + BIOS_VETO}],
    "share_interfaces": ["lo", "eth0", "wlan0"],
    "share_hosts_base": ["127.0.0.1", "::1"], "share_hosts_file": HOSTS_FILE,
    "share_hosts_script": "/usr/local/lib/coin-dojo-share/hosts_allow.py",
    "share_check": "/usr/local/lib/coin-dojo-share/smbconf_check.py",
    "share_default_mark": "/etc/coindojo/default-password", "share_marker": "/etc/coindojo/share-default-login",
    "share_state": "/run/coindojo/default-login-state",
    "share_sync": "/usr/local/sbin/coin-dojo-share-sync",
    "share_password_lock": "/run/coindojo/password.lock",
    "share_admin_rule": "/etc/sudoers.d/coindojo-admin",
}


def _read(*parts, base=ROLE):
    with open(os.path.join(base, *parts), encoding="utf-8") as fh:
        return fh.read()


# ---- a renderer for exactly the forms the templates use --------------------------------------------------------
KNOWN = [
    r"\{\{ ansible_managed \| default\('Managed by Ansible'\) \}\}",
    r"\{\{ [a-z_]+ \}\}",
    r"\{\{ [a-z_]+ \| quote \}\}",
    r"\{\{ [a-z_]+ \| join\(' '\) \}\}",
    r"\{\{ share_interfaces \| reject\('equalto', 'lo'\) \| join\('\|'\) \}\}",
    r"\{\{ share_interfaces \| reject\('equalto', 'lo'\) \| join\(' '\) \| quote \}\}",
    r"\{\{ s\.[a-z]+ \}\}",
    r"\{\{ s\.veto \| join\('/'\) \}\}",
    r"\{% for s in share_folders %\}",
    r"\{% endfor %\}",
]


def unknown_jinja(text):
    return [t for t in re.findall(r"\{\{.*?\}\}|\{%.*?%\}", text) if not any(re.fullmatch(k, t) for k in KNOWN)]


def render(text, v=VARS):
    def loop(m):
        body = m.group(1)
        out = []
        for s in v["share_folders"]:
            b = body.replace("{{ s.veto | join('/') }}", "/".join(s["veto"]))
            out.append(re.sub(r"\{\{ s\.([a-z]+) \}\}", lambda x: s[x.group(1)], b))
        return "".join(out)
    text = re.sub(r"\{% for s in share_folders %\}\n?(.*?)\{% endfor %\}\n?", loop, text, flags=re.S)
    text = text.replace("{{ ansible_managed | default('Managed by Ansible') }}", "Managed by Ansible")
    nolo = [i for i in v["share_interfaces"] if i != "lo"]
    text = text.replace("{{ share_interfaces | reject('equalto', 'lo') | join('|') }}", "|".join(nolo))
    text = text.replace("{{ share_interfaces | reject('equalto', 'lo') | join(' ') | quote }}", "'%s'" % " ".join(nolo))
    text = re.sub(r"\{\{ ([a-z_]+) \| join\(' '\) \}\}", lambda m: " ".join(v[m.group(1)]), text)
    text = re.sub(r"\{\{ ([a-z_]+) \| quote \}\}", lambda m: "'%s'" % v[m.group(1)], text)
    return re.sub(r"\{\{ ([a-z_]+) \}\}", lambda m: str(v[m.group(1)]), text)


def conf():
    return render(_read("templates", "smb.conf.j2"))


def flagged(text, want, bios_veto=BIOS_VETO, roms_veto=ROMS_VETO):
    p = sc.problems(text, USER, SHARES, hosts_file=HOSTS_FILE, bios_veto=bios_veto, roms_veto=roms_veto)
    return any(want in x for x in p)


# ---- smb.conf ------------------------------------------------------------------------------------------------
def test_templates_use_only_known_jinja():
    for f in sorted(os.listdir(os.path.join(ROLE, "templates"))):
        bad = unknown_jinja(_read("templates", f))
        check(not bad, "templates/%s uses only the Jinja the test renderer knows %s" % (f, bad))
    check(unknown_jinja("{{ share_user | upper }}"), "BROKEN: an unknown filter is caught by the renderer guard")


def test_rendered_conf_is_safe():
    p = sc.problems(conf(), USER, SHARES, hosts_file=HOSTS_FILE, bios_veto=BIOS_VETO, roms_veto=ROMS_VETO)
    check(p == [], "the rendered smb.conf passes the checker %s" % p)
    secs = sc.parse(conf())
    check(sorted(s for s in secs if s != "global") == ["bios", "roms"], "exactly two shares: roms and bios")
    g = secs["global"]
    check(g["server min protocol"] == "SMB3" and g["map to guest"] == "never" and g["bind interfaces only"] == "yes",
          "SMB3 minimum, guest off, bind interfaces only")
    check(g["interfaces"] == "lo eth0 wlan0", "LAN interfaces lo eth0 wlan0")
    check(g["hosts allow"] == "127.0.0.1 ::1" and g["include"] == HOSTS_FILE and g["hosts deny"] == "ALL",
          "smb.conf allows loopback only, then includes the live subnets (fail closed)")
    check(g["follow symlinks"] == "no" and g["wide links"] == "no", "follow symlinks off, wide links off")
    check(g.get("fruit:resource") == "stream" and g.get("fruit:metadata") == "stream"
          and "streams_xattr" in g.get("vfs objects", ""),
          "macOS resource forks and metadata are streams in xattrs (no ._ files)")
    for s in ("roms", "bios"):
        check(secs[s]["force user"] == USER and secs[s]["create mask"] == "0644" and secs[s]["directory mask"] == "0755",
              "[%s] files as the cabinet user, 0644/0755" % s)
    check(secs["bios"]["veto files"] == "/.DS_Store/Thumbs.db/desktop.ini/mame/*.lua/", "[bios] vetoes mame/ and *.lua")
    check(secs["roms"]["veto files"] == "/.DS_Store/Thumbs.db/desktop.ini/mame/*.lua/*.cmd/",
          "[roms] vetoes mame/, *.lua and *.cmd (MAME command files can name an autoboot Lua script)")


BROKEN = [
    ("a third share", "\n[data]\n   path = /data\n   valid users = coindojo\n", "shares"),
    ("home folders", "\n[homes]\n   browseable = no\n", "shares"),
    ("printers", "\n[printers]\n   path = /var/spool/samba\n", "shares"),
    ("an include in a share", "   include = /etc/samba/x.conf\n", "include"),
]
REPLACE = [
    ("guest ok on a share", "guest ok = no", "guest ok = yes", "allows guests"),
    ("guest via the synonym public", "guest ok = no", "public = yes", "allows guests"),
    ("map to guest = bad user", "map to guest = never", "map to guest = bad user", "map to guest"),
    ("SMB2 allowed", "server min protocol = SMB3", "server min protocol = SMB2_10", "server min protocol"),
    ("SMB1 via the old synonym", "server min protocol = SMB3", "min protocol = NT1", "server min protocol"),
    ("no protocol floor", "server min protocol = SMB3\n", "", "server min protocol"),
    ("bind interfaces only off", "bind interfaces only = yes", "bind interfaces only = no", "bind interfaces only"),
    ("another interface", "interfaces = lo eth0 wlan0", "interfaces = lo eth0 wlan0 eth1", "interfaces"),
    ("all private ranges in smb.conf", "hosts allow = 127.0.0.1 ::1", "hosts allow = 127.0.0.1 ::1 192.168.0.0/16", "more than loopback"),
    ("hosts allow everyone", "hosts allow = 127.0.0.1", "hosts allow = 0.0.0.0/0 127.0.0.1", "more than loopback"),
    ("hosts allow ALL", "hosts allow = 127.0.0.1", "hosts allow = ALL 127.0.0.1", "more than loopback"),
    ("no hosts deny", "hosts deny = ALL\n", "", "hosts deny"),
    ("no live-subnet include", "   include = /run/coin-dojo-share/hosts.conf\n", "", "include"),
    ("an include of another file", "include = /run/coin-dojo-share/hosts.conf", "include = /etc/samba/extra.conf", "include"),
    ("include before hosts allow", "   hosts allow = 127.0.0.1 ::1\n   include = /run/coin-dojo-share/hosts.conf\n",
     "   include = /run/coin-dojo-share/hosts.conf\n   hosts allow = 127.0.0.1 ::1\n", "after hosts allow"),
    ("follow symlinks on", "follow symlinks = no", "follow symlinks = yes", "follow symlinks"),
    ("wide links on", "wide links = no\n", "wide links = yes\n", "wide links"),
    ("NetBIOS port", "smb ports = 445", "smb ports = 445 139", "smb ports"),
    ("printing on", "load printers = no", "load printers = yes", "printing"),
    ("anonymous listing", "restrict anonymous = 2", "restrict anonymous = 0", "restrict anonymous"),
    ("user shares", "usershare max shares = 0", "usershare max shares = 100", "usershare"),
    ("a root preexec", "   logging = systemd", "   root preexec = /bin/sh -c id\n   logging = systemd", "root preexec"),
    ("roms points at /", "path = /data/roms", "path = /", "path"),
    ("any user", "valid users = coindojo\n", "valid users = @users\n", "valid users"),
    ("world-writable files", "create mask = 0644", "create mask = 0777", "create mask"),
    ("files as root", "force user = coindojo\n", "force user = root\n", "force user"),
    ("fruit:resource = file (._ files)", "fruit:resource = stream", "fruit:resource = file", "macOS forks"),
    ("no fruit at all", "vfs objects = fruit streams_xattr", "vfs objects = streams_xattr", "macOS forks"),
    ("bios without the mame veto", "/desktop.ini/mame/*.lua/", "/desktop.ini/*.lua/", "does not veto ['mame']"),
    ("roms without the .cmd veto", "/desktop.ini/mame/*.lua/*.cmd/", "/desktop.ini/mame/*.lua/", "does not veto ['*.cmd']"),
    ("bios without any veto", "   veto files = /.DS_Store/Thumbs.db/desktop.ini/mame/*.lua/\n", "", "does not veto"),
]


def test_broken_confs_fail():
    good = conf()
    for what, extra, want in BROKEN:
        check(flagged(good + extra, want), "BROKEN: %s fails the check" % what)
    for what, old, new, want in REPLACE:
        bad = good.replace(old, new, 1)
        check(bad != good and flagged(bad, want), "BROKEN: %s fails the check" % what)
    one = good.replace("inherit permissions = no\n", "inherit permissions = no\n   wide links = yes\n", 1)
    check(flagged(one, "[roms] wide links"), "BROKEN: wide links switched on inside one share fails")
    check(not flagged(good.replace("/desktop.ini/mame/*.lua/", "/desktop.ini/"), "veto", bios_veto=[], roms_veto=[]),
          "hk_share_bios_veto / hk_share_roms_veto: [] (switched off) is accepted")


def test_checker_cli():
    with tempfile.TemporaryDirectory() as t:
        good, bad = os.path.join(t, "good.conf"), os.path.join(t, "bad.conf")
        open(good, "w").write(conf())
        open(bad, "w").write(conf().replace("guest ok = no", "guest ok = yes"))
        args = ["--user", USER, "--share", "roms=/data/roms", "--share", "bios=/data/bios",
                "--bios-veto", "mame", "--bios-veto", "*.lua", "--roms-veto", "*.cmd"]
        run = lambda f, extra=(): subprocess.run(
            [sys.executable, os.path.join(ROLE, "files", "smbconf_check.py")] + args + list(extra) + [f],
            capture_output=True, text=True)
        r = run(good)
        check(r.returncode == 0 and r.stdout.startswith("OK "), "CLI: the rendered file exits 0 (%s)" % r.stdout.strip())
        r = run(bad)
        check(r.returncode == 1 and "FAIL: [roms] allows guests" in r.stdout, "BROKEN: CLI exits 1 on guest ok")
        r = run(good, ["--bios-veto", "fbneo"])
        check(r.returncode == 1 and "does not veto ['fbneo']" in r.stdout, "BROKEN: CLI exits 1 on a missing bios veto")
        r = subprocess.run([sys.executable, os.path.join(ROLE, "files", "smbconf_check.py"), "--user", USER,
                            "--share", "roms=relative", good], capture_output=True, text=True)
        check(r.returncode == 2, "BROKEN: a relative share path is a usage error")


# ---- the live subnets ------------------------------------------------------------------------------------------
IP_JSON = [
    {"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
    {"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.5.15", "prefixlen": 24}]},
    {"ifname": "wlan0", "addr_info": [{"family": "inet", "local": "10.20.30.40", "prefixlen": 16},
                                      {"family": "inet", "local": "203.0.113.9", "prefixlen": 24}]},
    {"ifname": "tun0", "addr_info": [{"family": "inet", "local": "172.16.9.2", "prefixlen": 24}]},
]


def test_hosts_allow_from_live_subnets():
    nets = ha.subnets(IP_JSON, ["eth0", "wlan0"])
    check(nets == ["192.168.5.0/24", "10.20.0.0/16"], "the eth0/wlan0 private subnets only %s" % nets)
    check("203.0.113.0/24" not in nets, "a public address on wlan0 adds nothing")
    check("172.16.9.0/24" not in nets, "an unlisted interface (a VPN tun0) adds nothing")
    text = ha.render(nets)
    check("hosts allow = 127.0.0.1 ::1 fe80::/10 192.168.5.0/24 10.20.0.0/16\n" in text, "rendered hosts file")
    check(sc.hosts_file_problems(text) == [], "the hosts file passes its own check")
    import ipaddress
    allowed = [ipaddress.ip_network(n) for n in nets]
    other = ipaddress.ip_address("192.168.6.20")
    check(not any(other in n for n in allowed), "a different private subnet (192.168.6.x) is not allowed")
    check(ha.render([]) == ha.render(ha.subnets([], ["eth0"])) and "fe80::/10\n" in ha.render([]),
          "no addresses yet: loopback and link-local only")
    for bad in ("hosts allow = 0.0.0.0/0\n", "hosts allow = 127.0.0.1 8.8.8.0/24\n", "hosts allow = ALL\n",
                "hosts allow = 127.0.0.1 fc00::/7\n", "valid users = x\n"):
        check(sc.hosts_file_problems(bad), "BROKEN: hosts file %r fails" % bad.strip())


def test_hosts_only_check_fails_closed():
    """The smbd drop-in runs `smbconf_check.py --hosts-only` after hosts_allow.py: a bad file stops smbd."""
    chk = os.path.join(ROLE, "files", "smbconf_check.py")
    with tempfile.TemporaryDirectory() as t:
        f = os.path.join(t, "hosts.conf")
        run = lambda: subprocess.run([sys.executable, chk, "--hosts-only", f], capture_output=True, text=True)
        open(f, "w").write(ha.render(["192.168.5.0/24"]))
        r = run()
        check(r.returncode == 0 and r.stdout.startswith("OK "), "a hosts file from hosts_allow.py passes (exit 0)")
        for bad in ("hosts allow = 0.0.0.0/0\n", "hosts allow = 127.0.0.1 203.0.113.0/24\n", "include = /etc/x\n"):
            open(f, "w").write(bad)
            r = run()
            check(r.returncode == 1 and "FAIL:" in r.stdout, "BROKEN: hosts file %r exits 1 (smbd stays down)" % bad.strip())
        os.remove(f)
        check(run().returncode == 1, "BROKEN: a missing hosts file exits 1 (smbd stays down)")
    d = render(_read("templates", "50-coin-dojo-hosts.conf.j2"))
    lines = [l for l in d.splitlines() if l.startswith("ExecStartPre=")]
    check(len(lines) == 2 and "hosts_allow.py" in lines[0] and "smbconf_check.py --hosts-only /run/coin-dojo-share/hosts.conf" in lines[1],
          "the drop-in writes the file, then checks it (in that order)")


def test_hosts_allow_cli_writes_once():
    with tempfile.TemporaryDirectory() as t:
        j, out = os.path.join(t, "ip.json"), os.path.join(t, "run", "hosts.conf")
        open(j, "w").write(json.dumps(IP_JSON))
        cmd = [sys.executable, os.path.join(ROLE, "files", "hosts_allow.py"), "--interfaces", "eth0 wlan0",
               "--out", out, "--ip-json", j]
        r1, r2 = subprocess.run(cmd, capture_output=True, text=True), subprocess.run(cmd, capture_output=True, text=True)
        check(r1.returncode == 0 and "WROTE" in r1.stdout and "SAME" in r2.stdout, "writes, then reports SAME")
        check(stat.S_IMODE(os.stat(out).st_mode) == 0o644 and "192.168.5.0/24" in open(out).read(), "the file is 0644")
        open(j, "w").write("not json")
        r = subprocess.run(cmd, capture_output=True, text=True)
        check(r.returncode == 0 and "192.168.5.0/24" not in open(out).read() and "fe80::/10" in open(out).read(),
              "BROKEN input (unreadable interfaces): fails closed to loopback + link-local")


# ---- defaults, lever, wiring ---------------------------------------------------------------------------------
def test_defaults_read_the_levers():
    d = _read("defaults", "main.yml")
    check(rule_no_hidden_knob(d) == [], "every default is a lever or # internal %s" % rule_no_hidden_knob(d))
    check(any("share_x" in x for x in rule_no_hidden_knob(d + "\nshare_x: 1\n")), "BROKEN: a planted literal is flagged")
    check('share_enable: "{{ hk_share_enable }}"' in d and 'share_bios_veto: "{{ hk_share_bios_veto }}"' in d,
          "share_enable / share_bios_veto read their levers")
    check('{ name: roms, path: "{{ hk_rom_root }}"' in d and '{ name: bios, path: "{{ hk_bios_root }}"' in d,
          "roms -> hk_rom_root, bios -> hk_bios_root")
    check('veto: "{{ share_veto + share_bios_veto }}"' in d and 'veto: "{{ share_veto + share_roms_veto }}"' in d,
          "bios and roms get their vetoes")
    check(re.search(r"(?m)^share_packages: \[samba, samba-vfs-modules, avahi-daemon\]", d), "Samba + avahi from Debian")


def test_lever_declared_and_personal_off():
    t = _read("config", "variables.d", "share.yml", base=REPO)
    m = re.search(r"(?ms)^  hk_share_enable:\n(.*?)(?=^  [a-z]|\Z)", t)
    check(m and "type: bool" in m.group(1) and "default: true" in m.group(1), "hk_share_enable: bool, default true")
    check(m and "ansible/roles/share" in m.group(1), "its consumer is the share role")
    m = re.search(r"(?ms)^  hk_share_bios_veto:\n(.*?)(?=^  [a-z]|\Z)", t)
    check(m and "type: list" in m.group(1) and 'default: [mame, "*.lua"]' in m.group(1),
          "hk_share_bios_veto: list, default mame + *.lua (on)")
    m = re.search(r"(?ms)^  hk_share_roms_veto:\n(.*?)(?=^  [a-z]|\Z)", t)
    check(m and "type: list" in m.group(1) and 'default: [mame, "*.lua", "*.cmd"]' in m.group(1),
          "hk_share_roms_veto: list, default mame + *.lua + *.cmd (on)")
    s = _read("config", "variables.d", "settings.yml", base=REPO)
    check(re.search(r"(?ms)^  hk_cabinet_hostname:\n    type: str\n    default: coindojo$", s),
          "the community name is coindojo (coindojo.local)")


def rule_on_off(main_yml, off_yml):
    p = []
    if not re.search(r"include_tasks: on.yml\n\s+when: share_enable \| bool", main_yml):
        p.append("on.yml is not behind share_enable")
    if not re.search(r"include_tasks: off.yml\n\s+when: not share_enable \| bool", main_yml):
        p.append("off.yml does not run when the lever is off")
    if not re.search(r"name: smbd.service\n\s+enabled: false", off_yml):
        p.append("off.yml does not disable smbd")
    if "share_avahi_service" not in off_yml or "state: absent" not in off_yml:
        p.append("off.yml leaves the mDNS entry")
    return p


def test_lever_off_disables():
    m, o = _read("tasks", "main.yml"), _read("tasks", "off.yml")
    check(rule_on_off(m, o) == [], "lever off: smbd disabled and stopped, mDNS entry removed %s" % rule_on_off(m, o))
    check(rule_on_off(m, o.replace("enabled: false", "enabled: true", 1)), "BROKEN: off.yml that keeps smbd enabled")
    check(rule_on_off(m.replace("when: not share_enable | bool", "when: false"), o), "BROKEN: off.yml never runs")
    check(rule_on_off(m.replace("when: share_enable | bool", "when: true"), o), "BROKEN: on.yml ignores the lever")


def test_template_is_validated_before_install():
    on = _read("tasks", "on.yml")
    m = re.search(r"(?ms)src: smb.conf.j2\n(.*?)\n  become", on)
    check(m and "validate:" in m.group(1) and "{{ share_check }}" in m.group(1) and "--testparm %s" in m.group(1)
          and "--hosts-file" in m.group(1) and "--bios-veto" in m.group(1) and "--roms-veto" in m.group(1),
          "smb.conf is installed only after smbconf_check.py (hosts include, bios vetoes) + testparm pass")
    check(m and "backup: true" in m.group(1), "the old smb.conf is kept as a backup")
    check("masked: true" in on and "nmbd.service" in on, "nmbd (NetBIOS) masked")
    d = render(_read("templates", "50-coin-dojo-hosts.conf.j2"))
    check("ExecStartPre=/usr/bin/python3 /usr/local/lib/coin-dojo-share/hosts_allow.py --interfaces 'eth0 wlan0' "
          "--out /run/coin-dojo-share/hosts.conf" in d, "every smbd start writes the live subnets first")


def test_role_is_wired():
    for play in ("site.yml", "image.yml", "vm.yml"):
        check(re.search(r"(?m)^    - \{ role: share, tags: \[share\] \}", _read(play, base=ANSIBLE)), "%s runs the share role" % play)


def test_units():
    s = render(_read("templates", "coin-dojo-share-sync.service.j2"))
    check("After=local-fs.target coindojo-firstboot.service" in s and "Before=smbd.service" in s,
          "sync runs after the first-boot config and before smbd")
    check("PathChanged=/etc/shadow" in render(_read("templates", "coin-dojo-share-sync.path.j2")), "passwd triggers the sync")


# ---- the NetworkManager dispatcher, in a sandbox -----------------------------------------------------------------
def dispatcher(t, event, listening, text=None):
    """Run the rendered dispatcher for eth0 <event> with fake python3/systemctl/ip/ss; returns the call log."""
    b = os.path.join(t, "bin")
    os.makedirs(b, exist_ok=True)
    log = os.path.join(t, "calls")
    open(log, "w").close()
    fakes = {
        "python3": '#!/bin/sh\necho "python3 $*" >> "$LOG"\n',
        "systemctl": '#!/bin/sh\necho "systemctl $*" >> "$LOG"\n[ "$1" = is-active ] && exit 0\nexit 0\n',
        "ip": '#!/bin/sh\necho "2: eth0    inet 192.168.5.15/24 brd 192.168.5.255 scope global eth0"\n',
        "ss": '#!/bin/sh\n%s\n' % ('echo "LISTEN 0 50 192.168.5.15:445 0.0.0.0:*"' if listening
                                   else 'echo "LISTEN 0 50 127.0.0.1:445 0.0.0.0:*"'),
    }
    for n, body in fakes.items():
        open(os.path.join(b, n), "w").write(body)
        os.chmod(os.path.join(b, n), 0o755)
    s = os.path.join(t, "disp")
    open(s, "w").write(text if text is not None else render(_read("templates", "60-coin-dojo-share.j2")))
    env = dict(os.environ, PATH=b + ":/usr/bin:/bin", LOG=log)
    subprocess.run(["/bin/sh", s, "eth0", event], env=env, capture_output=True, text=True)
    return open(log).read()


def test_dispatcher_reloads_and_restarts_only_when_needed():
    with tempfile.TemporaryDirectory() as t:
        c = dispatcher(t, "dhcp4-change", listening=False)
        check("--reload" in c and "try-restart" not in c, "dhcp4-change: subnets rewritten + reload, no restart")
        c = dispatcher(t, "up", listening=True)
        check("--reload" in c and "try-restart" not in c, "up while smbd already listens there: no restart")
        c = dispatcher(t, "up", listening=False)
        check("try-restart smbd.service" in c, "up on a new address smbd does not listen on: one restart")
        c = dispatcher(t, "down", listening=False)
        check("--reload" in c and "try-restart" not in c, "down: subnets rewritten, no restart")
    with tempfile.TemporaryDirectory() as t:
        broken = render(_read("templates", "60-coin-dojo-share.j2")).replace('[ "$2" = up ] || exit 0\n', "")
        check("try-restart" in dispatcher(t, "dhcp4-change", listening=False, text=broken),
              "BROKEN: a dispatcher that restarts on an address change is caught")


# ---- the login sync script, in a sandbox with fake Samba tools -------------------------------------------------
FAKE_PDBEDIT = """#!/bin/sh
# fake: pdbedit -L -w -> user:uid:LM:NT:flags:LCT from $HK_STATE
[ -s "$HK_STATE" ] && printf '%s:1000:XXXX:%s:[U          ]:LCT-0:\\n' "$(cut -d: -f1 "$HK_STATE")" "$(cut -d: -f2 "$HK_STATE")"
exit 0
"""
FAKE_SMBPASSWD = """#!/bin/sh
# fake: smbpasswd -s -a USER (two lines on stdin) | smbpasswd -x USER
if [ "$1" = -x ]; then rm -f "$HK_STATE"; exit 0; fi
read -r a; read -r b; [ "$a" = "$b" ] || exit 1
printf '%s:NT-%s\\n' "$3" "$a" > "$HK_STATE"
"""


# fake flock(1) for `flock -w N FD` on the fd the shell holds (flock(2) on the shared open file, like util-linux)
FAKE_FLOCK = """#!/usr/bin/env python3
import fcntl, sys, time
a = sys.argv[1:]
wait = float(a[a.index("-w") + 1]) if "-w" in a else 1e9
fd = int(a[-1])
end = time.monotonic() + wait
while True:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sys.exit(0)
    except BlockingIOError:
        if time.monotonic() >= end:
            sys.exit(1)
        time.sleep(0.05)
"""


def sync_vars(t):
    return dict(VARS, share_default_mark=os.path.join(t, "default-password"), share_marker=os.path.join(t, "nd", "share"),
                share_state=os.path.join(t, "run", "coindojo", "state"),
                share_password_lock=os.path.join(t, "run", "coindojo", "password.lock"),
                share_admin_rule=os.path.join(t, "coindojo-admin"))


def sandbox(t, script_text=None):
    """Render the sync script into t with its paths moved into t; fake pdbedit/smbpasswd/logger on PATH; a fake
    /run/systemd/system (booted) so the state file is written."""
    bindir = os.path.join(t, "bin")
    os.makedirs(bindir)
    os.makedirs(os.path.join(t, "booted"))
    for name, body in (("pdbedit", FAKE_PDBEDIT), ("smbpasswd", FAKE_SMBPASSWD), ("logger", "#!/bin/sh\nexit 0\n"),
                       ("flock", FAKE_FLOCK)):
        p = os.path.join(bindir, name)
        open(p, "w").write(body)
        os.chmod(p, 0o755)
    v = sync_vars(t)
    text = script_text if script_text is not None else render(_read("templates", "coin-dojo-share-sync.j2"), v)
    text = text.replace("/etc/shadow", os.path.join(t, "shadow")).replace("/etc/group", os.path.join(t, "group")).replace("/run/systemd/system", os.path.join(t, "booted"))
    s = os.path.join(t, "sync")
    open(s, "w").write(text)
    os.chmod(s, 0o755)
    env = dict(os.environ, PATH=bindir + ":/usr/bin:/bin", HK_STATE=os.path.join(t, "passdb"))

    def run(*a):
        return subprocess.run(["/bin/sh", s] + list(a), capture_output=True, text=True, env=env)
    return run, v


def put_shadow(t, h):
    open(os.path.join(t, "shadow"), "w").write("root:!:1::::::\n%s:%s:1::::::\n" % (USER, h))


def login(t):
    p = os.path.join(t, "passdb")
    return open(p).read().strip() if os.path.exists(p) else None


def state(v):
    try:
        return open(v["share_state"]).read()
    except OSError:
        return None


def test_sync_default_login_and_passwd():
    with tempfile.TemporaryDirectory() as t:
        run, v = sandbox(t)
        put_shadow(t, "$6$salt$DEFAULT")
        r = run("--default-login")
        check("SKIP: no community default login" in r.stdout and login(t) is None,
              "no #265 mark (personal image, or a password from coindojo.yml): no default network login")
        check(state(v) == "linux_default=no\nshare_default=no\n", "state: no default anywhere")
        open(v["share_default_mark"], "w").write("$6$salt$DEFAULT\n")
        r = run("--default-login")
        check(r.returncode == 0 and "SET:" in r.stdout and login(t) == USER + ":NT-12345",
              "the Linux password is the default: Samba gets the default too")
        check(state(v) == "linux_default=yes\nshare_default=yes\n", "state: the default is active (for #265's sudo side)")
        check(stat.S_IMODE(os.stat(v["share_marker"]).st_mode) == 0o600, "the marker is 0600")
        check("OK:" in run("--default-login").stdout, "a second run changes nothing")
        check(run("--status").stdout.strip() == "LOGIN " + USER, "--status: LOGIN")
        r = run()
        check(r.returncode == 0 and login(t) == USER + ":NT-12345", "boot sync with the password unchanged keeps the login")
        put_shadow(t, "$6$salt$NEWPASSWORD")
        r = run()
        check("REMOVED:" in r.stdout and login(t) is None and not os.path.exists(v["share_marker"]),
              "after passwd the default network login is removed")
        check(state(v) == "linux_default=no\nshare_default=no\n", "state: the default is gone")
        check(run("--status").stdout.strip() == "NOLOGIN " + USER, "--status: NOLOGIN")
        check("no longer the default" in run().stdout and login(t) is None,
              "a stale #265 mark (password changed) gives no default login")


def test_boot_sync_adds_the_default_whatever_the_build_order():
    """Finding 3: the boot unit itself adds the default when the #265 mark matches and Samba has no entry, so the
    image does not depend on the role running after build.sh's --default-login."""
    with tempfile.TemporaryDirectory() as t:
        run, v = sandbox(t)
        put_shadow(t, "$6$salt$DEFAULT")
        check("SKIP" in run().stdout and login(t) is None, "image build: no mark yet, nothing set")
        open(v["share_default_mark"], "w").write("$6$salt$DEFAULT\n")   # #265 wrote it later
        r = run()   # first boot (no argument)
        check("SET:" in r.stdout and login(t) == USER + ":NT-12345", "first boot sets the default login itself")


def test_sync_never_replaces_your_own_samba_password():
    """Finding 2: a Samba password set while the Linux password is still the default is kept."""
    with tempfile.TemporaryDirectory() as t:
        run, v = sandbox(t)
        put_shadow(t, "$6$salt$DEFAULT")
        open(v["share_default_mark"], "w").write("$6$salt$DEFAULT\n")
        run("--default-login")
        open(os.path.join(t, "passdb"), "w").write(USER + ":NT-mine\n")   # sudo smbpasswd -a: your own password
        for args in (("--default-login",), ()):
            r = run(*args)
            check("KEEP:" in r.stdout and login(t) == USER + ":NT-mine",
                  "Linux still default, own Samba password: %s keeps it" % (args or "boot sync"))
        check(state(v) == "linux_default=yes\nshare_default=no\n", "state: Linux default, network password your own")
        put_shadow(t, "$6$salt$NEWPASSWORD")
        run()
        check(login(t) == USER + ":NT-mine", "...and it survives a later passwd")
    with tempfile.TemporaryDirectory() as t:   # no marker at all: an entry made before the role ran
        run, v = sandbox(t)
        put_shadow(t, "$6$salt$DEFAULT")
        open(v["share_default_mark"], "w").write("$6$salt$DEFAULT\n")
        open(os.path.join(t, "passdb"), "w").write(USER + ":NT-early\n")
        check("KEEP:" in run("--default-login").stdout and login(t) == USER + ":NT-early",
              "an entry that existed before the role is not replaced")


def test_broken_sync_variants_caught():
    with tempfile.TemporaryDirectory() as t:
        good = render(_read("templates", "coin-dojo-share-sync.j2"), sync_vars(t))
        line = '      smbpasswd -x "$U" >/dev/null || {'
        check(line in good, "the planted change applies (removal)")
        run, v = sandbox(t, good.replace(line, '      true || {'))
        put_shadow(t, "$6$salt$DEFAULT")
        open(v["share_default_mark"], "w").write("$6$salt$DEFAULT\n")
        run("--default-login")
        put_shadow(t, "$6$salt$NEWPASSWORD")
        run()
        check(login(t) is not None, "BROKEN: a sync that does not remove the login leaves the default usable (caught)")
    with tempfile.TemporaryDirectory() as t:
        good = render(_read("templates", "coin-dojo-share-sync.j2"), sync_vars(t))
        cond = '    if [ -z "$have" ]; then\n'
        check(cond in good, "the planted change applies (overwrite)")
        run, v = sandbox(t, good.replace(cond, "    if true; then\n"))
        put_shadow(t, "$6$salt$DEFAULT")
        open(v["share_default_mark"], "w").write("$6$salt$DEFAULT\n")
        open(os.path.join(t, "passdb"), "w").write(USER + ":NT-mine\n")
        run("--default-login")
        check(login(t) != USER + ":NT-mine", "BROKEN: a sync that overwrites your own password is caught")


HELPER_SIM = r"""
import io, os, sys
sys.path.insert(0, sys.argv[1])
import coin_dojo_set_password as H
t, lock, user = sys.argv[2], sys.argv[3], sys.argv[4]


class R:
    def __init__(self, rc=0):
        self.returncode, self.stdout, self.stderr = rc, "", ""


def run(argv, **kw):
    tool = os.path.basename(argv[0])
    if tool == "smbpasswd":                       # the helper's -a: the NEW network password
        open(os.path.join(t, "passdb"), "w").write("%s:NT-%s\n" % (user, kw["input"].splitlines()[0]))
    elif tool == "chpasswd":
        open(os.path.join(t, "shadow"), "w").write("root:!:1::::::\n%s:$6$salt$NEWEST:1::::::\n" % user)
    return R()


Ent = type("Ent", (), {})
e = Ent(); e.pw_name, e.pw_uid, e.pw_shell, e.pw_gid = user, 1001, "/bin/bash", 1001
g = Ent(); g.gr_mem, g.gr_gid = [], 27
err = io.StringIO()
rc = H.main([], stdin=io.BytesIO(b"n3w-Settings pw\n"), env={"SUDO_UID": "1001", "SUDO_USER": user},
            geteuid=lambda: 0, getpwuid=lambda u: e, getpwnam=lambda n: e, getgrnam=lambda n: g,
            admin_rule=lambda p: False, run=run, exists=lambda p: True, lock_path=lock, lock_wait=20,
            out=io.StringIO(), err=err)
open(os.path.join(t, "helper.err"), "w").write(err.getvalue())
open(os.path.join(t, "helper.done"), "w").write(str(rc))
"""

# pdbedit that interleaves the SETTINGS helper: on its first call it snapshots the passdb, starts the REAL helper
# (coin_dojo_set_password.main with fake tools, the same lock file) and gives it up to 1.5 s, then answers with the
# snapshot: exactly the window between the sync's read and its smbpasswd -x.
INTERLEAVE_PDBEDIT = """#!/bin/sh
if [ ! -e "$HK_T/interleaved" ]; then
  : > "$HK_T/interleaved"
  snap=$(cat "$HK_STATE" 2>/dev/null)
  python3 "$HK_T/helper_sim.py" "$HK_SETTINGS" "$HK_T" "$HK_LOCK" "$HK_USER" </dev/null >/dev/null 2>&1 9>&- &   # detached: not the sync's pipe or fd 9
  i=0; while [ ! -e "$HK_T/helper.done" ] && [ $i -lt 15 ]; do sleep 0.1; i=$((i+1)); done
  [ -n "$snap" ] && printf '%s:1000:XXXX:%s:[U          ]:LCT-0:\\n' "${snap%%:*}" "${snap#*:}"
  exit 0
fi
[ -s "$HK_STATE" ] && printf '%s:1000:XXXX:%s:[U          ]:LCT-0:\\n' "$(cut -d: -f1 "$HK_STATE")" "$(cut -d: -f2 "$HK_STATE")"
exit 0
"""


def _interleave(t, text):
    """A sync run while the SETTINGS helper changes the password mid-read. -> the passdb entry at the end."""
    v = sync_vars(t)
    os.environ.update(HK_T=t, HK_SETTINGS=os.path.join(REPO, "skills", "settings"), HK_LOCK=v["share_password_lock"], HK_USER=USER)
    run, v = sandbox(t, text)                                       # its env is taken from os.environ now
    open(os.path.join(t, "bin", "pdbedit"), "w").write(INTERLEAVE_PDBEDIT)
    open(os.path.join(t, "helper_sim.py"), "w").write(HELPER_SIM)
    put_shadow(t, "$6$salt$NEW1")                                   # an earlier passwd: the default is stale
    open(v["share_default_mark"], "w").write("$6$salt$DEFAULT\n")
    os.makedirs(os.path.dirname(v["share_marker"]))
    open(v["share_marker"], "w").write("$6$salt$DEFAULT\nNT-12345\n")
    open(os.path.join(t, "passdb"), "w").write(USER + ":NT-12345\n")
    try:
        r = run()
    finally:
        for k in ("HK_T", "HK_SETTINGS", "HK_LOCK", "HK_USER"):
            os.environ.pop(k, None)
    done = os.path.join(t, "helper.done")
    for _ in range(200):                                            # the helper finishes once the sync let go
        if os.path.exists(done):
            break
        __import__("time").sleep(0.05)
    errf = os.path.join(t, "helper.err")
    r.helper_err = open(errf).read() if os.path.exists(errf) else ""
    return r, (open(done).read() if os.path.exists(done) else None), login(t)


def test_sync_and_the_settings_helper_share_one_lock():
    """#270 M2: the sync holds the password lock from before its first read; the helper waits, so the new password
    it sets is never deleted by a sync that read the old entry."""
    with tempfile.TemporaryDirectory() as t:
        r, rc, entry = _interleave(t, None)
        check(r.returncode == 0 and "REMOVED:" in r.stdout, "the sync removed the stale default: %r %r" % (r.stdout, r.stderr))
        check(rc == "0" and entry == USER + ":NT-n3w-Settings pw",
              "the helper ran after the sync and its new network password survives (helper rc %s %r, entry %r)" % (rc, r.helper_err, entry))
    with tempfile.TemporaryDirectory() as t:                       # BROKEN: a sync that takes no lock
        good = render(_read("templates", "coin-dojo-share-sync.j2"), sync_vars(t))
        check("  lock\n  now=$(shadow)" in good, "the planted change applies (no lock)")
        r, rc, entry = _interleave(t, good.replace("  lock\n  now=$(shadow)", "  now=$(shadow)"))
        check(entry is None, "BROKEN: without the lock the sync deletes the password the helper just set (entry %r)" % entry)
    with open(os.path.join(REPO, "skills", "settings", "coin_dojo_set_password.py"), encoding="utf-8") as fh:
        helper_lock = re.search(r'(?m)^LOCK = "([^"]+)"', fh.read()).group(1)
    role_lock = re.search(r"(?m)^share_password_lock: (\S+)", _read("defaults", "main.yml")).group(1)
    check(helper_lock == role_lock == VARS["share_password_lock"], "one lock path on both sides: %s %s" % (helper_lock, role_lock))
    parent = os.path.dirname(role_lock)
    check(parent not in ("/run/lock", "/tmp", "/var/tmp", "/dev/shm") and not parent.startswith(("/tmp/", "/run/lock/")),
          "#270 N2: the lock lives in a root directory, not a world-writable one: %s" % parent)
    for bad in ("/run/lock/coin-dojo-password.lock", "/tmp/coin-dojo-password.lock"):     # BROKEN: the same check says no
        bp = os.path.dirname(bad)
        check(bp in ("/run/lock", "/tmp", "/var/tmp", "/dev/shm"), "BROKEN: %s would be caught" % bad)


def test_lock_symlink_and_busy_lock_refuse():
    import fcntl
    with tempfile.TemporaryDirectory() as t:
        run, v = sandbox(t)
        put_shadow(t, "$6$salt$DEFAULT")
        open(v["share_default_mark"], "w").write("$6$salt$DEFAULT\n")
        os.makedirs(os.path.dirname(v["share_password_lock"]))
        os.symlink(os.path.join(t, "victim"), v["share_password_lock"])
        r = run()
        check(r.returncode == 1 and "symlink" in r.stderr and not os.path.exists(os.path.join(t, "victim")) and login(t) is None,
              "BROKEN: a symlink at the lock name: refused, never followed: %r" % r.stderr)
    with tempfile.TemporaryDirectory() as t:
        run, v = sandbox(t)
        text = open(os.path.join(t, "sync")).read().replace("flock -w 60 9", "flock -w 0.3 9")
        open(os.path.join(t, "sync"), "w").write(text)
        put_shadow(t, "$6$salt$DEFAULT")
        open(v["share_default_mark"], "w").write("$6$salt$DEFAULT\n")
        os.makedirs(os.path.dirname(v["share_password_lock"]))
        fd = os.open(v["share_password_lock"], os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            r = run()
        finally:
            os.close(fd)
        check(r.returncode == 1 and "busy" in r.stderr and login(t) is None, "BROKEN: a held lock: the sync changes nothing: %r" % r.stderr)


def test_lock_dir_must_be_root_alone():
    """#270 N2: the sync refuses a lock directory others may write (or a link), and makes a missing one 0755."""
    with tempfile.TemporaryDirectory() as t:
        run, v = sandbox(t)
        put_shadow(t, "$6$salt$DEFAULT")
        open(v["share_default_mark"], "w").write("$6$salt$DEFAULT\n")
        r = run()
        d = os.path.dirname(v["share_password_lock"])
        check(r.returncode == 0 and stat.S_IMODE(os.stat(d).st_mode) == 0o755, "a missing lock dir is made 0755: %r" % r.stderr)
        for mode in (0o775, 0o757):
            os.chmod(d, mode)
            r = run()
            check(r.returncode == 1 and "not root's alone" in r.stderr, "BROKEN: lock dir mode %o refused: %r" % (mode, r.stderr))
        os.chmod(d, 0o755)
    with tempfile.TemporaryDirectory() as t:
        run, v = sandbox(t)
        put_shadow(t, "$6$salt$DEFAULT")
        d = os.path.dirname(v["share_password_lock"])
        os.makedirs(os.path.join(t, "elsewhere"))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        os.symlink(os.path.join(t, "elsewhere"), d)
        r = run()
        check(r.returncode == 1 and "symlink" in r.stderr, "BROKEN: a linked lock dir is refused: %r" % r.stderr)


def test_admin_on_says_sudo_smbpasswd():
    """#270 N3: with admin on, the PASSWORD page refuses, so the sync and the deploy say sudo smbpasswd -a."""
    def removed(t, plant):
        run, v = sandbox(t)
        open(v["share_default_mark"], "w").write("$6$salt$DEFAULT\n")
        os.makedirs(os.path.dirname(v["share_marker"]))
        open(v["share_marker"], "w").write("$6$salt$DEFAULT\nNT-12345\n")
        open(os.path.join(t, "passdb"), "w").write(USER + ":NT-12345\n")
        put_shadow(t, "$6$salt$NEWPASSWORD")
        plant(t, v)
        return run(), run("--status")
    cases = (("no admin", lambda t, v: None, "set its password with: SETTINGS > PASSWORD", "NOLOGIN " + USER),
             ("the admin rule", lambda t, v: open(v["share_admin_rule"], "w").write("x\n"),
              "set its password with: sudo smbpasswd -a " + USER, "NOLOGIN %s (admin)" % USER),
             ("in the sudo group", lambda t, v: open(os.path.join(t, "group"), "w").write("root:x:0:\nsudo:x:27:pi,%s\n" % USER),
              "set its password with: sudo smbpasswd -a " + USER, "NOLOGIN %s (admin)" % USER),
             ("another user in sudo", lambda t, v: open(os.path.join(t, "group"), "w").write("sudo:x:27:pi,%sx\n" % USER),
              "set its password with: SETTINGS > PASSWORD", "NOLOGIN " + USER))
    for name, plant, want, status in cases:
        with tempfile.TemporaryDirectory() as t:
            r, s = removed(t, plant)
            check("REMOVED:" in r.stdout and want in r.stdout and s.stdout.strip() == status,
                  "%s: %r / %r" % (name, r.stdout, s.stdout))
    on = _read("tasks", "on.yml")
    warn = on[on.find("- name: Say how to log in to the network folder"):]
    check("'(admin)' in (share_status.stdout" in warn and "'On the cabinet: sudo smbpasswd -a ' ~ share_user" in warn
          and "Set it in SETTINGS > PASSWORD" in warn, "the deploy WARN picks sudo smbpasswd with admin on, SETTINGS without")


def test_pdbedit_and_smbpasswd_failures_are_errors():
    """#270 L4: a failing pdbedit is an error, never "no entry"; the marker goes only after -x succeeded."""
    def setup(t, pdbedit=None, smbpasswd=None):
        run, v = sandbox(t)
        if pdbedit:
            open(os.path.join(t, "bin", "pdbedit"), "w").write(pdbedit)
        if smbpasswd:
            open(os.path.join(t, "bin", "smbpasswd"), "w").write(smbpasswd)
        open(v["share_default_mark"], "w").write("$6$salt$DEFAULT\n")
        os.makedirs(os.path.dirname(v["share_marker"]))
        open(v["share_marker"], "w").write("$6$salt$DEFAULT\nNT-12345\n")
        open(os.path.join(t, "passdb"), "w").write(USER + ":NT-12345\n")
        put_shadow(t, "$6$salt$NEWPASSWORD")
        return run, v
    failing = "#!/bin/sh\nexit 7\n"
    with tempfile.TemporaryDirectory() as t:
        run, v = setup(t, pdbedit=failing)
        r = run()
        check(r.returncode == 1 and "pdbedit failed (exit 7)" in r.stderr and os.path.exists(v["share_marker"])
              and login(t) == USER + ":NT-12345", "BROKEN: pdbedit fails -> exit 1, marker and entry kept: %r" % r.stderr)
        r = run("--status")
        check(r.returncode == 1 and "LOGIN" not in r.stdout, "BROKEN: --status with a failing pdbedit is an error, not NOLOGIN")
    with tempfile.TemporaryDirectory() as t:
        run, v = setup(t, smbpasswd="#!/bin/sh\n[ \"$1\" = -x ] && exit 5\nexit 0\n")
        r = run()
        check(r.returncode == 1 and "smbpasswd -x failed" in r.stderr and os.path.exists(v["share_marker"]),
              "BROKEN: smbpasswd -x fails -> exit 1, the marker stays for the retry: %r" % r.stderr)
    with tempfile.TemporaryDirectory() as t:                       # proven different: marker goes, entry kept
        run, v = setup(t)
        open(os.path.join(t, "passdb"), "w").write(USER + ":NT-mine\n")
        r = run()
        check(r.returncode == 0 and "KEPT:" in r.stdout and not os.path.exists(v["share_marker"]) and login(t) == USER + ":NT-mine",
              "the hash is proven your own: marker removed, entry kept")


def test_sync_unit_has_no_start_limit():
    check(re.search(r"(?m)^StartLimitIntervalSec=0$", _read("templates", "coin-dojo-share-sync.service.j2")) is not None,
          "#270 L6: coin-dojo-share-sync.service has StartLimitIntervalSec=0 (a burst of passwd never stops the sync)")
    unit = _read("templates", "coin-dojo-share-sync.service.j2")
    check(unit.index("StartLimitIntervalSec=0") < unit.index("[Service]"), "in the [Unit] section")


def test_state_not_written_in_the_image_chroot():
    with tempfile.TemporaryDirectory() as t:
        run, v = sandbox(t)
        os.rmdir(os.path.join(t, "booted"))
        put_shadow(t, "$6$salt$DEFAULT")
        run()
        check(state(v) is None, "not booted (image chroot): no state file under /run")


def test_scripts_parse():
    with tempfile.TemporaryDirectory() as t:
        for name in ("coin-dojo-share-sync.j2", "60-coin-dojo-share.j2"):
            p = os.path.join(t, name)
            open(p, "w").write(render(_read("templates", name)))
            r = subprocess.run(["/bin/sh", "-n", p], capture_output=True, text=True)
            check(r.returncode == 0, "%s: sh -n %s" % (name, r.stderr.strip()))
            if shutil.which("shellcheck"):
                r = subprocess.run(["shellcheck", "-s", "sh", p], capture_output=True, text=True)
                check(r.returncode == 0, "shellcheck %s %s" % (name, r.stdout[-400:]))


if __name__ == "__main__":
    main(globals())
