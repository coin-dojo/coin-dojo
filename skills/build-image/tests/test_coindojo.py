#!/usr/bin/env python3
"""#237: coindojo-firstboot - the first-boot config (parse, check, apply once, scrub secrets, no-config hint).

Runs the real script: --check on good and malformed files, --apply --root <sandbox> with stub usermod/groupmod/
chpasswd on PATH (they log their arguments and stdin outside the sandbox and do the rename on the fixture's
/etc/passwd). The real-account proof (useradd/chpasswd/systemd in Linux) is tests/prove-firstboot.sh.

Broken cases that must FAIL: every malformed form below, a missing timezone, a user: that is not the image's own
user (#244: refused, nothing applied), a userconf.txt with a bad hash; and secrets left behind after first boot are looked for by grepping the sandbox.
"""
import os
import re
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "tests", "lib"))
from hktest import check, main  # noqa: E402

SKILL = os.path.dirname(HERE)
FB = os.path.join(SKILL, "scripts", "coindojo-firstboot.py")
EXAMPLE = os.path.join(SKILL, "coindojo.example.yml")

HASH = "$6$EXAMPLEsalt$EXAMPLEnotARealHashEXAMPLEnotARealHash0123456789abcdefghijklmnopqrstuvwx"
PSK = "EXAMPLE-wkey-0237x"   # no pass/psk/secret in test secrets: the allow-list must catch them
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEXAMPLEfakeKEYfakeKEYfakeKEYfakeKEY you@laptop"
GOOD = """# test config
user: arcade
password_hash: '%s'
ssh_pubkey: "%s"
hostname: coindojo
timezone: Europe/Berlin
wifi_ssid: "Home Net"
wifi_psk: %s   # comment
wifi_country: DE
""" % (HASH, KEY, PSK)


def run(*args, env=None):
    return subprocess.run([sys.executable, FB] + list(args), capture_output=True, text=True, env=env)


def checkfile(text):
    with tempfile.TemporaryDirectory() as t:
        f = os.path.join(t, "n.yml")
        with open(f, "w") as fh:
            fh.write(text)
        return run("--check", f)


def test_example_and_good_configs_pass():
    r = run("--check", EXAMPLE)
    check(r.returncode == 0 and "empty" in r.stdout, "coindojo.example.yml is valid and empty (%s)" % (r.stdout + r.stderr).strip())
    r = checkfile(GOOD)
    check(r.returncode == 0, "a full config passes (%s)" % r.stderr.strip())
    check(HASH not in r.stdout + r.stderr and PSK not in r.stdout + r.stderr, "--check never prints a secret")
    r = checkfile("password: prompt\nuser: arcade\n")
    check(r.returncode == 0, "password: prompt is accepted")
    r = checkfile("wifi_ssid: open-net\n")
    check(r.returncode == 0, "an open network (no psk) is accepted")
    r = checkfile("---\nuser: 'it''s'\n")
    check(r.returncode == 1, "a quote in a user name is refused")


MALFORMED = [
    ("tabs", "user:\tdojo\n", "tabs are not allowed"),
    ("nested", "user:\n  name: x\n", "indented line"),
    ("unknown key", "username: dojo\n", "unknown key"),
    ("duplicate", "user: a\nuser: b\n", "given twice"),
    ("no colon", "just text\n", "expected `key: value`"),
    ("list value", "ssh_pubkey: [a, b]\n", "not supported"),
    ("unterminated quote", 'hostname: "dojo\n', "unterminated"),
    ("plain password", "password: hunter2EXAMPLE\n", "only the value `prompt`"),
    ("both password forms", "password: prompt\npassword_hash: '%s'\n" % HASH, "not both"),
    ("bad hash", "password_hash: notahash\n", "not a crypt hash"),
    ("bad pubkey", "ssh_pubkey: hello\n", "not an SSH public key"),
    ("private key as pubkey", "ssh_pubkey: '-----BEGIN OPENSSH PRIVATE KEY-----'\n", "not an SSH public key"),
    ("bad user", "user: Root!\n", "not a valid login name"),
    ("system user", "user: root\n", "not a valid login name"),
    ("bad hostname", "hostname: -dojo-\n", "hostname: not valid"),
    ("bad timezone", "timezone: ../../etc/passwd\n", "not a zone name"),
    ("bad country", "wifi_ssid: n\nwifi_country: usa\n", "two capital letters"),
    ("short psk", "wifi_ssid: n\nwifi_psk: short\n", "8 to 63"),
    ("psk without ssid", "wifi_psk: %s\n" % PSK, "without wifi_ssid"),
    ("long ssid", "wifi_ssid: %s\n" % ("x" * 33), "1 to 32 bytes"),
]


def test_malformed_configs_refused():
    for name, text, want in MALFORMED:
        r = checkfile(text)
        check(r.returncode == 1 and want in r.stderr, "BROKEN: %s -> refused (%r)" % (name, r.stderr.strip()[-90:]))
        check(PSK not in r.stderr and "hunter2EXAMPLE" not in r.stderr, "the error for %s shows no secret" % name)


# ---- apply in a sandbox root ----

STUBS = {
    "usermod": r'''#!/bin/bash
echo "usermod $*" >> "$ND_LOG"; [ "$1" = --root ] && { R=$2; shift 2; } || R=/
if [ "$1" = -l ]; then sed -i.bak "s/^$3:/$2:/" "$R/etc/passwd"; rm -f "$R/etc/passwd.bak"; fi
if [ "$1" = -d ]; then u=${@: -1}; old=$(awk -F: -v u="$u" '$1==u{print $6}' "$R/etc/passwd")
  [ "$3" = -m ] && mv "$R$old" "$R$2"
  sed -i.bak "s#^\($u:[^:]*:[^:]*:[^:]*:[^:]*:\)[^:]*#\1$2#" "$R/etc/passwd"; rm -f "$R/etc/passwd.bak"; fi
''',
    "groupmod": r'''#!/bin/bash
echo "groupmod $*" >> "$ND_LOG"; [ "$1" = --root ] && { R=$2; shift 2; } || R=/
sed -i.bak "s/^$3:/$2:/" "$R/etc/group"; rm -f "$R/etc/group.bak"
''',
    # logs stdin; when the sandbox has an /etc/shadow (the #270 tests) it also sets the hash there, like the real one
    "chpasswd": r'''#!/bin/bash
echo "chpasswd $*" >> "$ND_LOG"; [ "$1" = --root ] && R=$2 || R=/
l=$(cat); printf '%s\n' "$l" >> "$ND_LOG.stdin"
if [ "$R" != / ] && [ -f "$R/etc/shadow" ]; then
  awk -F: -v OFS=: -v u="${l%%:*}" -v h="${l#*:}" '$1 == u {$2 = h} {print}' "$R/etc/shadow" > "$R/etc/shadow.new"
  mv "$R/etc/shadow.new" "$R/etc/shadow"; fi
''',
    "gpasswd": r'''#!/bin/bash
echo "gpasswd $*" >> "$ND_LOG"; [ "$1" = --root ] && { R=$2; shift 2; } || R=/
[ "$1" = -d ] && [ "$R" != / ] && awk -F: -v OFS=: -v u="$2" -v g="$3" '$1 == g {n = split($4, m, ","); s = "";
  for (i = 1; i <= n; i++) if (m[i] != u) s = s (s == "" ? "" : ",") m[i]; $4 = s} {print}' "$R/etc/group" > "$R/etc/group.new" \
  && mv "$R/etc/group.new" "$R/etc/group"
''',
    "visudo": '#!/bin/bash\necho "visudo $*" >> "$ND_LOG"\nexit "${ND_VISUDO_RC:-0}"\n',
    # #299: sshd -t (ND_SSHD_RC fails it) and systemctl (the reload) only log
    "sshd": '#!/bin/bash\necho "sshd $*" >> "$ND_LOG"\nexit "${ND_SSHD_RC:-0}"\n',
    "systemctl": '#!/bin/bash\necho "systemctl $*" >> "$ND_LOG"\n',
}


def sandbox(t):
    root = os.path.join(t, "root")
    for d in ("etc/coindojo", "etc/systemd/system/getty@tty1.service.d", "boot/firmware",
              "usr/share/zoneinfo/Europe", "home/arcade/.config/retroarch", "var/lib", "run"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    w = lambda rel, text: open(os.path.join(root, rel), "w").write(text)
    w("etc/passwd", "root:x:0:0:root:/root:/bin/bash\narcade:x:1000:1000::/home/arcade:/bin/bash\n")
    w("etc/coindojo/built-user", "arcade\n")
    w("etc/group", "root:x:0:\narcade:x:1000:\n")
    w("etc/hosts", "127.0.0.1 localhost\n127.0.1.1 coin-dojo\n")
    w("etc/systemd/system/getty@tty1.service.d/autologin.conf",
      "[Service]\nExecStart=\nExecStart=-/sbin/agetty --autologin arcade --noclear %I $TERM\n")
    w("boot/firmware/cmdline.txt", "console=tty1 root=PARTUUID=x cfg80211.ieee80211_regdom=US\n")
    w("usr/share/zoneinfo/Europe/Berlin", "TZif")
    w("home/arcade/.config/retroarch/retroarch.cfg", 'joypad_autoconfig_dir = "/home/arcade/.config/retroarch/autoconfig"\n')
    sd = os.path.join(t, "bin")
    os.makedirs(sd)
    for name, body in STUBS.items():
        p = os.path.join(sd, name)
        open(p, "w").write(body)
        os.chmod(p, 0o755)
    env = dict(os.environ, PATH=sd + ":" + os.environ["PATH"], ND_LOG=os.path.join(t, "log"))
    return root, env


def apply(root, env):
    return run("--apply", "--root", root, env=env)


def grep_tree(root, needle):
    hits = []
    for dp, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(dp, f)
            if not os.path.islink(p) and needle in open(p, errors="replace").read():
                hits.append(os.path.relpath(p, root))
    return hits


def rd(root, rel):
    return open(os.path.join(root, rel)).read()


def test_apply_full_config_then_scrub():
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write(GOOD)
        r = apply(root, env)
        log = open(os.path.join(t, "log")).read()
        check(r.returncode == 0, "apply exits 0 (%s)" % (r.stdout + r.stderr).strip()[-200:])
        check("usermod" not in log and "groupmod" not in log and "arcade:x:1000" in rd(root, "etc/passwd"),
              "user: naming the image's own user renames nothing (#244)")
        check(open(os.path.join(t, "log.stdin")).read() == "arcade:%s\n" % HASH and "chpasswd --root %s -e" % root in log,
              "the hash goes to chpasswd -e on stdin")
        check(rd(root, "home/arcade/.ssh/authorized_keys") == KEY + "\n", "SSH key installed")
        check(stat.S_IMODE(os.stat(os.path.join(root, "home/arcade/.ssh/authorized_keys")).st_mode) == 0o600, "authorized_keys is 0600")
        check(rd(root, "etc/hostname") == "coindojo\n" and "127.0.1.1 coindojo" in rd(root, "etc/hosts")
              and "coin-dojo" not in rd(root, "etc/hosts"), "hostname and /etc/hosts")
        check(os.readlink(os.path.join(root, "etc/localtime")) == "/usr/share/zoneinfo/Europe/Berlin", "timezone link")
        nm = os.path.join(root, "etc/NetworkManager/system-connections/coindojo-wifi.nmconnection")
        check(os.path.exists(nm) and stat.S_IMODE(os.stat(nm).st_mode) == 0o600, "Wi-Fi keyfile written, mode 0600")
        check("ssid=Home Net" in rd(root, nm[len(root) + 1:]) and "psk=" + PSK in rd(root, nm[len(root) + 1:]), "keyfile has SSID and PSK")
        check("cfg80211.ieee80211_regdom=DE" in rd(root, "boot/firmware/cmdline.txt")
              and "regdom=US" not in rd(root, "boot/firmware/cmdline.txt"), "Wi-Fi country on the kernel command line")
        # secrets: gone from the boot partition, never printed
        check(not os.path.exists(os.path.join(root, "boot/firmware/coindojo.yml")), "coindojo.yml removed after first boot")
        applied = rd(root, "boot/firmware/coindojo.applied.yml")
        check('hostname: "coindojo"' in applied and "# password_hash: removed" in applied and "# wifi_psk: removed" in applied,
              "coindojo.applied.yml keeps the non-secret values")
        check(grep_tree(os.path.join(root, "boot"), PSK) == [] and grep_tree(os.path.join(root, "boot"), HASH) == [],
              "no PSK or hash left anywhere on the boot partition")
        check(grep_tree(root, HASH) == [], "the hash is nowhere in the sandbox (only chpasswd got it)")
        check(PSK not in r.stdout + r.stderr and HASH not in r.stdout + r.stderr, "apply prints no secret")
        check(os.path.exists(os.path.join(root, "var/lib/coindojo/applied")), "applied marker written")
        check(not os.path.exists(os.path.join(root, "run/coindojo/no-config")), "no hint after a config")
        # second boot: nothing to do, no hint
        os.remove(os.path.join(t, "log"))
        r = apply(root, env)
        check(r.returncode == 0 and not os.path.exists(os.path.join(t, "log"))
              and not os.path.exists(os.path.join(root, "run/coindojo/no-config")), "the next boot changes nothing and shows no hint")


def test_apply_malformed_scrubs_and_applies_nothing():
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write(GOOD + "colour: red\n")
        r = apply(root, env)
        yml = rd(root, "boot/firmware/coindojo.yml")
        check(r.returncode == 1, "BROKEN: malformed coindojo.yml -> apply exits 1")
        check(not os.path.exists(os.path.join(t, "log")) and not os.path.exists(os.path.join(root, "var/lib/coindojo/applied")),
              "nothing applied from a malformed file")
        check(PSK not in yml and HASH not in yml and 'hostname: "coindojo"' in yml, "its secret lines are blanked, the rest kept to fix")
        err = rd(root, "boot/firmware/coindojo.error.txt")
        check("line 10: unknown key" in err and "colour" not in err, "coindojo.error.txt says why")
        hint = rd(root, "run/coindojo/no-config")
        check("COIN DOJO" in hint and "line 10: unknown key" in hint, "the on-screen hint shows the error")


def test_apply_missing_timezone_changes_nothing():
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write(GOOD.replace("Europe/Berlin", "Mars/Olympus"))
        r = apply(root, env)
        check(r.returncode == 1 and not os.path.exists(os.path.join(t, "log")), "BROKEN: unknown timezone -> refused before any change")
        check(PSK not in rd(root, "boot/firmware/coindojo.yml"), "secrets scrubbed even then")


def test_apply_other_user_refused():
    """#244 fix round M2: the roles baked the image's user into units, sudoers, polkit and the session config, so a
    user: naming anyone else is refused before any change: nothing applies, the reason is on screen and in
    coindojo.error.txt, the secrets are scrubbed."""
    for other, extra in (("dojo", ""), ("dojo", "dojo:x:1001:1001::/home/dojo:/bin/bash\n")):
        with tempfile.TemporaryDirectory() as t:
            root, env = sandbox(t)
            with open(os.path.join(root, "etc/passwd"), "a") as f:
                f.write(extra)
            open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write(GOOD.replace("user: arcade", "user: " + other))
            r = apply(root, env)
            err = rd(root, "boot/firmware/coindojo.error.txt")
            hint = rd(root, "run/coindojo/no-config")
            check(r.returncode == 1 and "is not this image's user (arcade)" in err and "hk_cabinet_user" in err,
                  "BROKEN: user: %s on an arcade image -> refused, coindojo.error.txt says why" % other)
            check("is not this image's user" in hint, "the reason is on the screen too")
            check(not os.path.exists(os.path.join(t, "log")) and not os.path.exists(os.path.join(root, "etc/hostname"))
                  and not os.path.exists(os.path.join(root, "home/arcade/.ssh"))
                  and not os.path.exists(os.path.join(root, "var/lib/coindojo/applied")),
                  "nothing of the file is applied (no password, key, hostname)")
            check(grep_tree(os.path.join(root, "boot"), PSK) == [] and grep_tree(os.path.join(root, "boot"), HASH) == [],
                  "its secrets are scrubbed anyway")
    r = checkfile(GOOD.replace("user: arcade", "user: dojo"))
    check(r.returncode == 0, "--check without --user knows no image user: the file itself is valid")
    with tempfile.TemporaryDirectory() as t:
        f = os.path.join(t, "n.yml")
        open(f, "w").write(GOOD.replace("user: arcade", "user: dojo"))
        r = run("--check", f, "--user", "coindojo")
        check(r.returncode == 1 and "is not this image's user (coindojo)" in r.stderr, "BROKEN: make image refuses CONFIG= with another user:")
        open(f, "w").write(GOOD.replace("user: arcade", "user: coindojo"))
        check(run("--check", f, "--user", "coindojo").returncode == 0, "user: coindojo on a coindojo image passes --check")
        open(f, "w").write("hostname: x\n")
        check(run("--check", f, "--user", "coindojo").returncode == 0, "no user: line passes --check")
        check(run("--check", f, "--usr", "coindojo").returncode == 2, "a wrong flag is a usage error")


def test_no_config_shows_hint():
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        r = apply(root, env)
        hint = os.path.join(root, "run/coindojo/no-config")
        check(r.returncode == 0 and os.path.exists(hint) and "coindojo.example.yml" in rd(root, "run/coindojo/no-config"),
              "no config, never configured -> on-screen hint, boot goes on")
        check("COIN DOJO" in r.stdout, "the hint goes to the console too")
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        open(os.path.join(root, "boot/firmware/firstrun.sh"), "w").write("#!/bin/sh\n")
        apply(root, env)
        check(os.path.exists(os.path.join(root, "run/coindojo/no-config")),
              "#244: Imager's firstrun.sh no longer counts as configured -> the hint still shows")


def test_imager_userconf_applied():
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        open(os.path.join(root, "boot/firmware/userconf.txt"), "w").write("arcade:%s\n" % HASH)
        r = apply(root, env)
        log = open(os.path.join(t, "log")).read()
        check(r.returncode == 0 and "usermod" not in log, "userconf.txt (Imager) for the image's user applies")
        check(open(os.path.join(t, "log.stdin")).read() == "arcade:%s\n" % HASH, "and sets its password hash")
        check(not os.path.exists(os.path.join(root, "boot/firmware/userconf.txt")), "userconf.txt removed afterwards")
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        open(os.path.join(root, "boot/firmware/userconf.txt"), "w").write("player:%s\n" % HASH)
        r = apply(root, env)
        check(r.returncode == 1 and not os.path.exists(os.path.join(t, "log"))
              and "names the user player, but this image's user is arcade" in rd(root, "run/coindojo/no-config")
              and "Write arcade:<hash> in userconf.txt" in rd(root, "boot/firmware/coindojo.error.txt")
              and "user: line" not in rd(root, "boot/firmware/coindojo.error.txt")
              and not os.path.exists(os.path.join(root, "boot/firmware/userconf.txt")),
              "BROKEN: userconf.txt naming another user -> refused with a userconf message, error file, file removed")
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        open(os.path.join(root, "boot/firmware/userconf.txt"), "w").write("player:plaintext\n")
        r = apply(root, env)
        check(r.returncode == 1 and not os.path.exists(os.path.join(root, "boot/firmware/userconf.txt"))
              and not os.path.exists(os.path.join(t, "log")), "BROKEN: userconf.txt with a plain password -> refused and removed")


def imager_renamed(root, new="bob"):
    """What Imager's firstrun.sh does to uid 1000: usermod -l, groupmod -n, home moved, autologin rewritten."""
    p = lambda rel: os.path.join(root, rel)
    open(p("etc/passwd"), "w").write("root:x:0:0:root:/root:/bin/bash\n%s:x:1000:1000::/home/%s:/bin/bash\n" % (new, new))
    open(p("etc/group"), "w").write("root:x:0:\n%s:x:1000:\n" % new)
    os.rename(p("home/arcade"), p("home/" + new))


def test_imager_rename_detected():
    """Round 3 M1: Imager's OS customisation renames the uid-1000 account (the cabinet user since #244). Every boot
    checks the image's user still exists; if not: nothing applied, loud screen error, coindojo.error.txt."""
    for cfg in ("", GOOD):
        with tempfile.TemporaryDirectory() as t:
            root, env = sandbox(t)
            imager_renamed(root)
            if cfg:
                open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write(cfg)
            open(os.path.join(root, "boot/firmware/userconf.txt"), "w").write("arcade:%s\n" % HASH)
            r = apply(root, env)
            hint = rd(root, "run/coindojo/no-config")
            err = rd(root, "boot/firmware/coindojo.error.txt")
            check(r.returncode == 1 and "THE CABINET USER IS GONE" in hint and "the account is now bob (uid 1000)" in hint,
                  "BROKEN: user renamed by Imager (config %s) -> loud on-screen error naming the new account" % bool(cfg))
            check("Nothing was applied" in err and "skip at Imager's customisation" in err and "coindojo.yml" in err,
                  "coindojo.error.txt says what happened and how to recover")
            check(not os.path.exists(os.path.join(t, "log")) and not os.path.exists(os.path.join(root, "etc/hostname"))
                  and not os.path.exists(os.path.join(root, "var/lib/coindojo/applied")), "nothing applied")
            check(grep_tree(os.path.join(root, "boot"), PSK) == [] and grep_tree(os.path.join(root, "boot"), HASH) == []
                  and not os.path.exists(os.path.join(root, "boot/firmware/userconf.txt")), "secrets scrubbed, userconf.txt removed")
    with tempfile.TemporaryDirectory() as t:   # the good path: the user is there -> no alarm
        root, env = sandbox(t)
        r = apply(root, env)
        check("THE CABINET USER IS GONE" not in rd(root, "run/coindojo/no-config"), "an intact image raises no alarm")


def test_wifi_ssid_escaping():
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write('wifi_ssid: "a;b"\nwifi_psk: "%s"\n' % PSK)
        apply(root, env)
        nm = rd(root, "etc/NetworkManager/system-connections/coindojo-wifi.nmconnection")
        check("ssid=97;59;98;" in nm, "an SSID with ';' is written as bytes")


# ---- round 2: scrub gaps, BOM, resumable rename, userconf failure, PSK spaces ----

LEAKS = [
    ("plain password", "password: hunter2EXAMPLE\nhostname: dojo\n", "hunter2EXAMPLE"),
    ("key in another case", "Password_hash: '%s'\nhostname: dojo\n" % HASH, HASH),
    ("BOM + bad key", "﻿wifi_ssid: n\nwifi_psk: %s\ncolour: red\n" % PSK, PSK),
    ("typo'd key", "wifi_ssid: n\nwifi_pks: %s\ncolour: red\n" % PSK, PSK),
    ("commented-out secret", "# wifi_psk: %s\ncolour: red\n" % PSK, PSK),
    ("bare value line", "hostname: dojo\n%s\n" % PSK, PSK),
    ("secret-looking key name", "hunter2EXAMPLEpass: x\n", "hunter2EXAMPLE"),   # the keyword path on purpose
    # round 3: error messages must not echo values (a lost newline glues a secret onto another key)
    ("lost newline after hostname", "hostname: dojo wifi_psk: hunter2XYZQ\n", "hunter2XYZQ"),
    ("lost newline after user", "user: dojo hunter2XYZQ\n", "hunter2XYZQ"),
    ("lost newline after timezone", "timezone: Europe/Berlin hunter2XYZQ\n", "hunter2XYZQ"),
    ("zone that does not exist", "timezone: Europe/hunter2XYZQ\n", "hunter2XYZQ"),
    ("bad country", "wifi_ssid: n\nwifi_country: hunter2XYZQ\n", "hunter2XYZQ"),
    ("bad ssh key", "ssh_pubkey: hunter2XYZQ\n", "hunter2XYZQ"),
    ("key that is the value", "hunter2XYZQ: x\n", "hunter2XYZQ"),
    # round 4: glued values that VALIDATED before (the success path), comments, the critic's attack3 cases
    ("glued after ssid", "wifi_ssid: home wifi_psk: Zq7mK9vR2x\n", "Zq7mK9vR2x"),
    ("glued after quoted ssid", "wifi_ssid: 'home' wifi_psk: Zq7mK9vR2x\n", "Zq7mK9vR2x"),
    ("glued after country", "wifi_ssid: home\nwifi_country: DE wifi_psk: Zq7mK9vR2x\n", "Zq7mK9vR2x"),
    ("glued into a key comment", "ssh_pubkey: %s me wifi_psk: Zq7mK9vR2x\n" % KEY.rsplit(" ", 1)[0], "Zq7mK9vR2x"),
    ("hash glued into a key comment", "ssh_pubkey: %s me password_hash: Zq7mK9vR2x\n" % KEY.rsplit(" ", 1)[0], "Zq7mK9vR2x"),
    ("comment after a valid ssid", "wifi_ssid: home # key Zq7mK9vR2x\n", "Zq7mK9vR2x"),
    ("comment line with a key", "# wifi key: Zq7mK9vR2x\nhostname: dojo\n", "Zq7mK9vR2x"),
    ("unknown key holding it", "wlan_key: Zq7mK9vR2x\nhostname: dojo\n", "Zq7mK9vR2x"),
    ("duplicate key", "hostname: dojo\nhostname: zq7mk9vr2x\n", "zq7mk9vr2x"),
    ("vertical tab", "wifi_ssid: home\x0bZq7mK9vR2x\n", "Zq7mK9vR2x"),
    ("indented continuation", "wifi_ssid: home\n  Zq7mK9vR2x\n", "Zq7mK9vR2x"),
]


def leak_run(text):
    """Apply text in a fresh sandbox; return (rc, files under the WHOLE root holding the needle, output)."""
    t = tempfile.mkdtemp()
    root, env = sandbox(t)
    # the example ships beside it on a real card: its comments are the only ones a scrub keeps
    with open(EXAMPLE) as f:
        open(os.path.join(root, "boot/firmware/coindojo.example.yml"), "w").write(f.read())
    open(os.path.join(root, "boot/firmware/coindojo.yml"), "w", encoding="utf-8").write(text)
    r = apply(root, env)
    return t, root, r


def test_malformed_never_leaves_a_secret():
    """Every leak case alone (error path) and inside a good config (success path, round 4): the secret must be in
    no file anywhere under the root (boot, /run hint, error file, /etc, homes, /var) and not on the screen/journal."""
    import shutil
    for name, text, secret in LEAKS:
        for path, body in (("alone", text), ("in a good config", GOOD.replace("user: arcade\n", "") + text)):
            t, root, r = leak_run(body)
            try:
                left = grep_tree(root, secret)
                shown = secret in r.stdout + r.stderr
                check(left == [] and not shown,
                      "BROKEN: %s (%s, rc %d) -> the secret is in no file and not shown (%s%s)"
                      % (name, path, r.returncode, left, " + shown" if shown else ""))
            finally:
                shutil.rmtree(t)
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write("hostname: dojo\ncolour: red\n")
        apply(root, env)
        check('hostname: "dojo"' in rd(root, "boot/firmware/coindojo.yml"), "harmless known keys stay for the user to fix")


def test_bom_file_applies():
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        open(os.path.join(root, "boot/firmware/coindojo.yml"), "w", encoding="utf-8").write("﻿" + GOOD)
        r = apply(root, env)
        check(r.returncode == 0 and rd(root, "etc/hostname") == "coindojo\n", "a Notepad file with a UTF-8 BOM applies")
        check(grep_tree(os.path.join(root, "boot"), PSK) == [], "and is scrubbed")
    r = checkfile("﻿" + GOOD)
    check(r.returncode == 0, "--check accepts a BOM")


def cut_at(root, step):
    """Leave the sandbox as a power cut after `step` of rename_user(arcade -> dojo) would."""
    p = lambda rel: os.path.join(root, rel)
    os.makedirs(p("var/lib/coindojo"), exist_ok=True)
    open(p("var/lib/coindojo/rename"), "w").write("arcade dojo\n")
    if step == "state":
        return
    passwd = rd(root, "etc/passwd").replace("arcade:x:1000:1000::/home/arcade:", "dojo:x:1000:1000::/home/arcade:")
    if step in ("record-only", "moved", "group", "autologin"):
        passwd = passwd.replace("::/home/arcade:", "::/home/dojo:")
    if step == "move-only":   # the folder moved, the record still old
        os.rename(p("home/arcade"), p("home/dojo"))
    if step in ("moved", "group", "autologin"):
        os.rename(p("home/arcade"), p("home/dojo"))
    open(p("etc/passwd"), "w").write(passwd)
    if step in ("group", "autologin"):
        open(p("etc/group"), "w").write("root:x:0:\ndojo:x:1000:\n")
    if step == "autologin":
        al = p("etc/systemd/system/getty@tty1.service.d/autologin.conf")
        text = open(al).read()
        open(al, "w").write(text.replace("--autologin arcade ", "--autologin dojo "))


def test_rename_resumes_after_a_cut_at_each_step():
    """An older image's rename a power cut stopped still finishes (a new image never starts one, #244)."""
    for step in ("state", "login", "move-only", "record-only", "moved", "group", "autologin"):
        with tempfile.TemporaryDirectory() as t:
            root, env = sandbox(t)
            open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write(GOOD.replace("user: arcade\n", ""))
            cut_at(root, step)
            r = apply(root, env)
            ok = (r.returncode == 0 and "dojo:x:1000:1000::/home/dojo:" in rd(root, "etc/passwd")
                  and "arcade" not in rd(root, "etc/passwd") and os.path.isdir(os.path.join(root, "home/dojo/.config"))
                  and not os.path.exists(os.path.join(root, "home/arcade"))
                  and rd(root, "etc/group") == "root:x:0:\ndojo:x:1000:\n"
                  and "--autologin dojo " in rd(root, "etc/systemd/system/getty@tty1.service.d/autologin.conf")
                  and "/home/dojo/" in rd(root, "home/dojo/.config/retroarch/retroarch.cfg")
                  and not os.path.exists(os.path.join(root, "var/lib/coindojo/rename")))
            check(ok, "power cut after '%s': the next boot finishes the rename (rc %d %s)" % (step, r.returncode, r.stdout.strip()[-120:]))


def test_pending_rename_resumes_even_if_user_changed():
    """A cut after usermod -l, then a new config -> the old rename finishes first; a user: naming someone else is then
    refused (#244), a config without user: applies to the renamed user."""
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        cut_at(root, "login")
        open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write("user: other\n")
        r = apply(root, env)
        check(r.returncode == 1 and "dojo:x:1000:1000::/home/dojo:" in rd(root, "etc/passwd")
              and "is not this image's user (dojo)" in rd(root, "boot/firmware/coindojo.error.txt"),
              "BROKEN: pending rename finished, then user: other refused")
    for second, want in (("hostname: later\n", "dojo"),):
        with tempfile.TemporaryDirectory() as t:
            root, env = sandbox(t)
            cut_at(root, "login")          # arcade -> dojo stopped after the login rename
            open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write(second)
            r = apply(root, env)
            ok = (r.returncode == 0 and ("%s:x:1000:1000::/home/%s:" % (want, want)) in rd(root, "etc/passwd")
                  and rd(root, "etc/group") == "root:x:0:\n%s:x:1000:\n" % want
                  and ("--autologin %s " % want) in rd(root, "etc/systemd/system/getty@tty1.service.d/autologin.conf")
                  and not os.path.exists(os.path.join(root, "var/lib/coindojo/rename"))
                  and rd(root, "etc/coindojo/user") == want + "\n")
            check(ok, "pending rename finished first, then %r -> user %s (rc %d)" % (second.strip(), want, r.returncode))
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        cut_at(root, "login")
        r = apply(root, env)               # no config at all: the rename still finishes
        check(r.returncode == 0 and "dojo:x:1000:1000::/home/dojo:" in rd(root, "etc/passwd")
              and not os.path.exists(os.path.join(root, "var/lib/coindojo/rename")), "a pending rename finishes even without a config")


def test_second_config_after_rename():
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        cut_at(root, "state")             # an older image's rename arcade -> dojo, finished on this boot
        check(apply(root, env).returncode == 0, "the pending rename arcade -> dojo finishes")
        open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write("hostname: second\n")
        r = apply(root, env)
        check(r.returncode == 0 and rd(root, "etc/hostname") == "second\n" and "dojo:x:1000" in rd(root, "etc/passwd"),
              "a second coindojo.yml without user: applies to the renamed user (rc %d %s)" % (r.returncode, r.stdout.strip()[-100:]))
        open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write("ssh_pubkey: \"%s\"\n" % KEY.replace("you@laptop", "second@box"))
        apply(root, env)
        check("second@box" in rd(root, "home/dojo/.ssh/authorized_keys"), "a later key goes to the renamed user's home")


def test_user_name_that_is_a_group_refused():
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        with open(os.path.join(root, "etc/group"), "a") as f:
            f.write("audio:x:29:arcade\n")
        open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write("user: audio\n")
        r = apply(root, env)
        check(r.returncode == 1 and "is not this image's user" in rd(root, "boot/firmware/coindojo.error.txt")
              and "arcade:x:1000" in rd(root, "etc/passwd") and not os.path.exists(os.path.join(t, "log")),
              "BROKEN: user: audio (an existing group) -> refused clearly, nothing renamed")


def test_stuck_rename_still_scrubs_boot():
    """Round 4: a pending rename that cannot finish returns early: coindojo.yml must be scrubbed and userconf.txt
    removed before that return, on every boot."""
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        cut_at(root, "login")                                   # arcade -> dojo pending ...
        open(os.path.join(root, "etc/group"), "w").write("root:x:0:\narcade:x:1000:\ndojo:x:1001:\n")   # ... and stuck
        open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write(GOOD)
        open(os.path.join(root, "boot/firmware/userconf.txt"), "w").write("player:%s\n" % HASH)
        r = apply(root, env)
        left = grep_tree(os.path.join(root, "boot"), PSK) + grep_tree(os.path.join(root, "boot"), HASH)
        check(r.returncode == 1 and left == [] and not os.path.exists(os.path.join(root, "boot/firmware/userconf.txt")),
              "BROKEN: stuck rename -> early return, but coindojo.yml is scrubbed and userconf.txt removed (%s)" % left)
        check("could not finish" in rd(root, "run/coindojo/no-config") and PSK not in r.stdout + r.stderr,
              "the hint names the stuck rename and shows no secret")


def test_userconf_not_utf8_removed():
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        open(os.path.join(root, "boot/firmware/userconf.txt"), "wb").write(b"player:\xff\xfe$6$EXAMPLE\n")
        r = apply(root, env)
        check(r.returncode == 1 and "Traceback" not in r.stderr
              and not os.path.exists(os.path.join(root, "boot/firmware/userconf.txt")),
              "BROKEN: a userconf.txt that is not UTF-8 -> no crash, removed (%s)" % r.stderr.strip()[-80:])


def test_pubkey_comment_rules():
    base = KEY.rsplit(" ", 1)[0]
    for comment, ok in (("you@laptop", True), ("the maintainer's key", False), ("me wifi_psk: Zq7mK9vR2x", False),
                        ("x" * 101, False), ("work laptop 2026", True)):
        r = checkfile('ssh_pubkey: "%s %s"\n' % (base, comment))
        check((r.returncode == 0) == ok, "ssh_pubkey comment %r -> %s" % (comment[:30], "ok" if ok else "refused"))


def test_userconf_failure_removes_it():
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        with open(os.path.join(root, "etc/passwd"), "a") as f:
            f.write("player:x:1001:1001::/home/player:/bin/bash\n")
        open(os.path.join(root, "boot/firmware/userconf.txt"), "w").write("player:%s\n" % HASH)
        r = apply(root, env)
        check(r.returncode == 1 and not os.path.exists(os.path.join(root, "boot/firmware/userconf.txt")),
              "BROKEN: userconf.txt that fails to apply (user exists) -> refused and removed (it holds a hash)")


def test_psk_spaces_encoded():
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write('wifi_ssid: n\nwifi_psk: " EXAMPLE psk  "\n')
        apply(root, env)
        nm = rd(root, "etc/NetworkManager/system-connections/coindojo-wifi.nmconnection")
        check("psk=\\sEXAMPLE psk\\s\\s\n" in nm, "leading/trailing PSK spaces written as \\s (NM would strip them)")


# ---- #265: the community image's default login (build time: --default-login; every boot: the reminder) ----

KEYONLY = "PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\n"


def openssl_sha512():
    """The real openssl that can make and check SHA-512 crypt hashes (the build uses the image's own), else None."""
    try:
        r = subprocess.run(["openssl", "passwd", "-6", "-salt", "EXAMPLEsalt", "-stdin"], input="x",
                           capture_output=True, text=True)
    except OSError:
        return None
    return r.returncode == 0 and r.stdout.startswith("$6$EXAMPLEsalt$")


def crypt_matches(h, password):
    """Does the SHA-512 crypt hash h verify `password`? Recomputed with h's own salt by openssl."""
    salt = h.split("$")[2]
    r = subprocess.run(["openssl", "passwd", "-6", "-salt", salt, "-stdin"], input=password,
                       capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == h


def default_login(root, env, *extra):
    os.makedirs(os.path.join(root, "etc/ssh/sshd_config.d"), exist_ok=True)
    if not os.path.exists(os.path.join(root, "etc/ssh/sshd_config.d/00-coin-dojo-keyonly.conf")):
        open(os.path.join(root, "etc/ssh/sshd_config.d/00-coin-dojo-keyonly.conf"), "w").write(KEYONLY)
    return run("--default-login", "--root", root, "--user", "arcade", *extra, env=env)


def chpasswd_lines(t):
    p = os.path.join(t, "log.stdin")
    return open(p).read().splitlines() if os.path.exists(p) else []


def test_default_login_community_sets_a_fresh_hash_of_12345():
    if not openssl_sha512():
        from hktest import skip
        skip("no openssl with passwd -6 on this host: the default-login hash is not checked")
        return
    hashes = []
    for _ in range(2):                        # two builds: two different random salts
        with tempfile.TemporaryDirectory() as t:
            root, env = sandbox(t)
            r = default_login(root, env, "--edition", "community", "--config", os.path.join(root, "boot/firmware/coindojo.yml"))
            lines = chpasswd_lines(t)
            check(r.returncode == 0 and len(lines) == 1 and lines[0].startswith("arcade:$6$"),
                  "community, no config: chpasswd -e gets arcade:<sha512 hash> (%s)" % (r.stdout + r.stderr).strip()[-160:])
            h = lines[0].split(":", 1)[1] if lines else "$6$x$y"
            hashes.append(h)
            check("chpasswd --root %s -e" % root in open(os.path.join(t, "log")).read(), "set with chpasswd -e (on stdin)")
            check(crypt_matches(h, "12345"), "the hash verifies the password 12345")
            check(not crypt_matches(h, "12346"), "BROKEN: the same hash does not verify 12346")
            mark = os.path.join(root, "etc/coindojo/default-password")
            check(os.path.exists(mark) and rd(root, "etc/coindojo/default-password").strip() == h
                  and stat.S_IMODE(os.stat(mark).st_mode) == 0o600, "the hash is kept in /etc/coindojo/default-password (0600)")
            check("default login arcade / 12345 set" in r.stdout and "key-only" in r.stdout, "the build log names the default login")
    salts = [h.split("$")[2] for h in hashes]
    check(salts[0] != salts[1] and hashes[0] != hashes[1], "two builds get two random salts, no fixed hash (%s)" % salts)


def test_default_login_configured_password_wins():
    for name, text in (("password_hash", "password_hash: '%s'\n" % HASH), ("password: prompt", "password: prompt\n")):
        with tempfile.TemporaryDirectory() as t:
            root, env = sandbox(t)
            cfg = os.path.join(root, "boot/firmware/coindojo.yml")
            open(cfg, "w").write("hostname: dojo\n" + text)
            r = default_login(root, env, "--edition", "community", "--config", cfg)
            check(r.returncode == 0 and chpasswd_lines(t) == [] and not os.path.exists(os.path.join(root, "etc/coindojo/default-password")),
                  "a configured %s wins: no default password, no mark" % name)
    with tempfile.TemporaryDirectory() as t:   # a config without a password still gets the default
        root, env = sandbox(t)
        cfg = os.path.join(root, "boot/firmware/coindojo.yml")
        open(cfg, "w").write('ssh_pubkey: "%s"\n' % KEY)
        r = default_login(root, env, "--edition", "community", "--config", cfg)
        check(r.returncode == 0 and len(chpasswd_lines(t)) == 1, "a config with only an SSH key keeps the default login")


def test_sets_password_is_the_one_rule():
    """--sets-password (run.sh, verify.sh) and --default-login share the parser: an empty or empty-quoted value is no
    password (critic round: verify.sh's own regex counted `password_hash: ""` as set and failed a good build)."""
    cases = [("password_hash: '%s'\n" % HASH, 0), ("password: prompt\n", 0), ('password_hash: ""\n', 1),
             ("password_hash: ''\n", 1), ("password_hash:\n", 1), ("hostname: dojo\n", 1), ("", 1),
             ("Password_hash: x\n", 2), ("password: hunter2EXAMPLE\n", 2)]
    with tempfile.TemporaryDirectory() as t:
        f = os.path.join(t, "n.yml")
        for text, want in cases:
            open(f, "w").write(text)
            r = run("--sets-password", f)
            check(r.returncode == want and "hunter2EXAMPLE" not in r.stdout + r.stderr,
                  "--sets-password %r -> %d (got %d)" % (text, want, r.returncode))
            r = subprocess.run([sys.executable, FB, "--sets-password", "-"], input=text, capture_output=True, text=True)
            check(r.returncode == want, "the same from stdin (%r -> %d)" % (text, r.returncode))
        check(run("--sets-password").returncode == 2, "BROKEN: --sets-password without a file is a usage error")
    for empty in ('password_hash: ""\n', "password_hash: ''\n"):
        with tempfile.TemporaryDirectory() as t:
            root, env = sandbox(t)
            cfg = os.path.join(root, "boot/firmware/coindojo.yml")
            open(cfg, "w").write(empty)
            r = default_login(root, env, "--edition", "community", "--config", cfg)
            check(r.returncode == 0 and len(chpasswd_lines(t)) == 1, "an empty-quoted %r gets the default login" % empty.strip())


def test_default_login_personal_unchanged():
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        r = default_login(root, env, "--edition", "personal")
        check(r.returncode == 0 and chpasswd_lines(t) == [] and not os.path.exists(os.path.join(root, "etc/coindojo/default-password")),
              "the personal edition gets no default password (the account stays locked)")


def test_default_login_refusals():
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        os.makedirs(os.path.join(root, "etc/ssh/sshd_config.d"))
        open(os.path.join(root, "etc/ssh/sshd_config.d/00-coin-dojo-keyonly.conf"), "w").write(KEYONLY.replace("PasswordAuthentication no", "PasswordAuthentication yes"))
        r = default_login(root, env, "--edition", "community")
        check(r.returncode == 1 and "password SSH is on" in r.stderr and chpasswd_lines(t) == [],
              "BROKEN: password SSH on + the default password -> refused, nothing set")
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        os.makedirs(os.path.join(root, "etc/ssh/sshd_config.d"))
        open(os.path.join(root, "etc/ssh/sshd_config.d/50-x.conf"), "w").write("ChallengeResponseAuthentication yes\n")
        r = default_login(root, env, "--edition", "community")
        check(r.returncode == 1 and "password SSH is on" in r.stderr and chpasswd_lines(t) == [],
              "BROKEN: challenge-response on + the default password -> refused")
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        r = default_login(root, env, "--edition", "bogus")
        check(r.returncode == 1 and "--edition" in r.stderr and chpasswd_lines(t) == [], "BROKEN: an unknown edition is refused")
        r = run("--default-login", "--root", root, "--edition", "community", env=env)
        check(r.returncode == 2, "BROKEN: --default-login without --user is a usage error")
        r = run("--default-login", "--root", root, "--user", "nobody2", "--edition", "community", env=env)
        check(r.returncode == 1 and "does not exist" in r.stderr, "BROKEN: a user the image lacks is refused")


def put_default(root, h="$6$EXAMPLEsalt$" + "A" * 86):
    open(os.path.join(root, "etc/shadow"), "w").write("root:!:1::::::\narcade:%s:1::::::\n" % h)
    open(os.path.join(root, "etc/coindojo/default-password"), "w").write(h + "\n")
    return h


def test_boot_reports_the_default_login_until_changed():
    line = "Default login arcade / 12345: change it in SETTINGS > PASSWORD"
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        put_default(root)
        r = apply(root, env)
        shown = os.path.join(root, "run/coindojo/default-login")
        check(r.returncode == 0 and line in r.stdout, "boot with the default password: the console/journal says '%s'" % line)
        check(os.path.exists(shown) and line in rd(root, "run/coindojo/default-login") and "key-only" in rd(root, "run/coindojo/default-login"),
              "the line is left for tty1 (/run/coindojo/default-login)")
        check(os.path.exists(os.path.join(root, "run/coindojo/no-config")), "no config: the no-config hint shows as well")
        # coindojo.yml with a password_hash on a later boot: the stub chpasswd does not touch shadow, so do it here
        open(os.path.join(root, "etc/shadow"), "w").write("root:!:1::::::\narcade:%s:1::::::\n" % HASH)
        r = apply(root, env)
        check(r.returncode == 0 and line not in r.stdout and not os.path.exists(shown)
              and not os.path.exists(os.path.join(root, "etc/coindojo/default-password")),
              "password changed (passwd / coindojo.yml / userconf.txt): no line, the mark is removed")
        r = apply(root, env)
        check(line not in r.stdout, "and it stays quiet on the next boot")
    with tempfile.TemporaryDirectory() as t:     # BROKEN: no mark (personal image, or changed): never a line
        root, env = sandbox(t)
        open(os.path.join(root, "etc/shadow"), "w").write("root:!:1::::::\narcade:%s:1::::::\n" % HASH)
        r = apply(root, env)
        check("Default login" not in r.stdout and not os.path.exists(os.path.join(root, "run/coindojo/default-login")),
              "BROKEN: no default-password mark -> no default-login line")
    with tempfile.TemporaryDirectory() as t:     # a config without a password keeps the default and the reminder
        root, env = sandbox(t)
        put_default(root)
        open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write("hostname: dojo\n")
        r = apply(root, env)
        check(r.returncode == 0 and line in r.stdout and not os.path.exists(os.path.join(root, "run/coindojo/no-config")),
              "configured without a password: no hint, but the default-login line")


# ---- #270: admin (sudo) is off with the default; only a boot-partition password applied at boot turns it on ----

ADMIN = "etc/sudoers.d/coindojo-admin"
PENDING = "etc/coindojo/sudo-pending"


def with_sudo_group(root):
    open(os.path.join(root, "etc/group"), "w").write("root:x:0:\nsudo:x:27:pi,arcade\narcade:x:1000:\n")


def in_sudo(root):
    return any(l.startswith("sudo:") and "arcade" in l.split(":")[3].split(",") for l in rd(root, "etc/group").splitlines())


def test_default_login_takes_sudo_away():
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        with_sudo_group(root)
        r = default_login(root, env, "--edition", "community")
        log = open(os.path.join(t, "log")).read()
        check(r.returncode == 0 and "gpasswd --root %s -d arcade sudo" % root in log and not in_sudo(root)
              and "sudo:x:27:pi" in rd(root, "etc/group"), "community default: arcade leaves the sudo group (pi's entry untouched)")
        check(rd(root, PENDING) == "arcade\n" and not os.path.exists(os.path.join(root, ADMIN)), "the admin grant is pending, no admin rule")
        check("admin off until the owner sets their own password" in r.stdout and "SSH stays key-only" in r.stdout, "the build log says so")
    for name, edition, cfg in (("personal", "personal", None), ("a configured password_hash", "community", "password_hash: '%s'\n" % HASH)):
        with tempfile.TemporaryDirectory() as t:
            root, env = sandbox(t)
            with_sudo_group(root)
            extra = ["--edition", edition]
            if cfg:
                p = os.path.join(root, "boot/firmware/coindojo.yml")
                open(p, "w").write(cfg)
                extra += ["--config", p]
            r = default_login(root, env, *extra)
            check(r.returncode == 0 and in_sudo(root) and not os.path.exists(os.path.join(root, PENDING)),
                  "%s: sudo from the start (still in the sudo group, nothing pending)" % name)


def pending_sandbox(t):
    root, env = sandbox(t)
    h = put_default(root)
    open(os.path.join(root, "etc/group"), "w").write("root:x:0:\nsudo:x:27:\narcade:x:1000:\n")
    open(os.path.join(root, PENDING), "w").write("arcade\n")
    os.makedirs(os.path.join(root, "etc/sudoers.d"))
    os.makedirs(os.path.join(root, "etc/ssh/sshd_config.d"))
    open(os.path.join(root, "etc/ssh/sshd_config.d/00-coin-dojo-keyonly.conf"), "w").write(KEYONLY)
    return root, env, h


def set_shadow(root, h):
    open(os.path.join(root, "etc/shadow"), "w").write("root:!:1::::::\narcade:%s:1::::::\n" % h)


def test_runtime_password_change_never_grants_sudo():
    """#270 round 2 (security review): code running as the user knows 12345 and can run passwd; the /etc/shadow
    path unit must then end the reminder and NEVER write a sudoers rule."""
    with tempfile.TemporaryDirectory() as t:
        root, env, h = pending_sandbox(t)
        r = run("--password-changed", "--root", root, env=env)
        check(r.returncode == 0 and not os.path.exists(os.path.join(root, ADMIN)), "still the default: no sudo")
        set_shadow(root, HASH)                        # passwd / chpasswd / SETTINGS at runtime: a new hash
        r = run("--password-changed", "--root", root, env=env)
        check(r.returncode == 0 and not os.path.exists(os.path.join(root, ADMIN)) and os.path.exists(os.path.join(root, PENDING))
              and not os.path.exists(os.path.join(root, "etc/sudoers.d/coindojo-admin.tmp")),
              "BROKEN: passwd at runtime -> no sudoers file, admin stays off")
        check(not os.path.exists(os.path.join(root, "etc/coindojo/default-password"))
              and not os.path.exists(os.path.join(root, "run/coindojo/default-login")), "but the 12345 reminder ends")
        r = apply(root, env)                          # the next boot, no boot-partition password: still nothing
        check(not os.path.exists(os.path.join(root, ADMIN)) and "admin (sudo) on" not in r.stdout,
              "BROKEN: the next boot after a runtime passwd -> still no sudo")
        log = os.path.join(t, "log")
        check(not os.path.exists(log) or "visudo" not in open(log).read(), "no sudoers rule was even attempted")


def test_boot_partition_password_turns_admin_on():
    """A password from the SD card's boot partition, applied at boot, gives sudo on that same boot; never revoked."""
    for name, f, text in (("coindojo.yml password_hash", "coindojo.yml", "password_hash: '%s'\n" % HASH),
                          ("userconf.txt", "userconf.txt", "arcade:%s\n" % HASH)):
        with tempfile.TemporaryDirectory() as t:
            root, env, h = pending_sandbox(t)
            open(os.path.join(root, "boot/firmware", f), "w").write(text)
            r = apply(root, env)
            rule = os.path.join(root, ADMIN)
            check(r.returncode == 0 and os.path.exists(rule) and "arcade ALL=(ALL:ALL) ALL" in rd(root, ADMIN)
                  and stat.S_IMODE(os.stat(rule).st_mode) == 0o440 and not os.path.exists(os.path.join(root, PENDING)),
                  "%s at boot: sudoers.d/coindojo-admin (0440), nothing pending" % name)
            check("visudo -cf %s.tmp" % rule in open(os.path.join(t, "log")).read()
                  and not os.path.exists(rule + ".tmp") and not os.path.exists(rule + ".tmp.tmp"),
                  "checked by visudo under one temp name (.tmp), nothing left behind")
            check("admin (sudo) on for arcade" in r.stdout and "Default login" not in r.stdout, "the journal says admin is on")
            set_shadow(root, h)                       # even the old default back: never revoked
            apply(root, env)
            run("--password-changed", "--root", root, env=env)
            check(os.path.exists(rule), "%s: sudo is never taken back" % name)
    with tempfile.TemporaryDirectory() as t:         # password: prompt off a live system (no keyboard): no grant
        root, env, h = pending_sandbox(t)
        open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write("password: prompt\n")
        apply(root, env)
        check(not os.path.exists(os.path.join(root, ADMIN)), "BROKEN: password: prompt not answered -> no sudo")
    with tempfile.TemporaryDirectory() as t:         # a boot config without a password: no grant
        root, env, h = pending_sandbox(t)
        open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write("hostname: dojo\n")
        apply(root, env)
        check(not os.path.exists(os.path.join(root, ADMIN)), "BROKEN: a boot config without a password -> no sudo")
    with tempfile.TemporaryDirectory() as t:
        root, env, h = pending_sandbox(t)
        open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write("password_hash: '%s'\n" % HASH)
        r = apply(root, dict(env, ND_VISUDO_RC="1"))
        check(not os.path.exists(os.path.join(root, ADMIN)) and not os.path.exists(os.path.join(root, ADMIN + ".tmp"))
              and os.path.exists(os.path.join(root, PENDING)) and "visudo refused" in r.stdout,
              "BROKEN: visudo refuses the rule -> no rule, still pending, said in the journal")
    with tempfile.TemporaryDirectory() as t:         # personal / configured build: nothing pending, nothing written
        root, env = sandbox(t)
        open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write("password_hash: '%s'\n" % HASH)
        apply(root, env)
        check(not os.path.exists(os.path.join(root, "etc/sudoers.d")), "personal: no pending -> no rule (already in sudo)")
    check(run("--password-changed", "--bogus").returncode == 2, "BROKEN: a wrong flag is a usage error")


def test_only_apply_grants_sudo():
    """Static (#270, #299): grant_sudo and enable_password_ssh are called only from grant_owner (which checks the
    password is no longer 12345); grant_owner only from apply_config (boot partition) and owner_access (SETTINGS'
    helper); the path unit's password_changed never touches sudoers or sshd."""
    src = open(FB).read()
    def callers(fn):
        out = []
        for chunk in src.split("\ndef ")[1:]:
            name = chunk.split("(", 1)[0]
            if name != fn and re.search(r"\b%s\(" % fn, chunk):
                out.append(name)
        return sorted(out)
    check(callers("grant_sudo") == ["grant_owner"], "grant_sudo is called by grant_owner only: %s" % callers("grant_sudo"))
    check(callers("enable_password_ssh") == ["grant_owner"], "enable_password_ssh by grant_owner only: %s" % callers("enable_password_ssh"))
    check(callers("grant_owner") == ["apply_config", "owner_access"], "grant_owner by apply_config and owner_access: %s" % callers("grant_owner"))
    body = src.split("def password_changed", 1)[1].split("\ndef ", 1)[0]
    check("grant" not in body and "sudoers" not in body.replace('"""', "") and "ssh" not in body.lower(),
          "password_changed never touches sudoers or sshd")




# ---- #299: the owner's own password turns admin AND password SSH on; 12345 never does ----

OWNER_SSH = "etc/ssh/sshd_config.d/10-coindojo-owner-ssh.conf"
OWN_HASH = "$6$EXAMPLEown$" + "B" * 86          # a stand-in for the owner's own password (not a real hash)


def owner(root, env, user="arcade"):
    return run("--owner-access", "--user", user, "--root", root, env=env)


def log_of(t):
    p = os.path.join(t, "log")
    return open(p).read() if os.path.exists(p) else ""


def nothing_granted(root):
    return (not os.path.lexists(os.path.join(root, OWNER_SSH)) and not os.path.exists(os.path.join(root, ADMIN))
            and os.path.exists(os.path.join(root, PENDING)))


def test_owner_access_after_an_own_password():
    with tempfile.TemporaryDirectory() as t:
        root, env, _ = pending_sandbox(t)
        set_shadow(root, OWN_HASH)                     # what the SETTINGS helper's chpasswd left
        r = owner(root, env)
        dropin = os.path.join(root, OWNER_SSH)
        text = open(dropin).read() if os.path.exists(dropin) else ""
        body = [l.strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
        check(r.returncode == 0 and body == ["Match User arcade", "PasswordAuthentication yes"],
              "own password: the drop-in is exactly Match User arcade + PasswordAuthentication yes (%r, %s)" % (body, r.stdout.strip()[-200:]))
        check(stat.S_IMODE(os.stat(dropin).st_mode) == 0o644 and "PermitRootLogin" not in text and "root" not in body[0],
              "0644, and it never touches root login")
        check(os.path.exists(os.path.join(root, ADMIN)) and "arcade ALL=(ALL:ALL) ALL" in rd(root, ADMIN)
              and not os.path.exists(os.path.join(root, PENDING)), "admin (sudo) on through the #270 grant, nothing pending")
        check("SETTINGS > PASSWORD" in rd(root, ADMIN), "the sudoers comment names where it came from")
        log = log_of(t)
        check("sshd -t" in log and "systemctl --no-block try-reload-or-restart ssh.service" in log
              and log.index("sshd -t") < log.index("visudo"), "sshd -t checks it, sshd reloads, before the sudo grant")
        check(not os.path.exists(os.path.join(root, "etc/coindojo/default-password")), "the default-password mark is gone")
        check("password SSH on for arcade" in r.stdout and "admin (sudo) on for arcade" in r.stdout, "the journal says both")
        r = owner(root, env)                          # again: nothing pending any more
        check(r.returncode == 3 and os.path.exists(dropin), "a second call: nothing to do (exit 3), access stays")


def test_BROKEN_owner_access_while_the_default_is_active():
    """The default 12345 still in /etc/shadow (the mark matches): no drop-in, no sudo, nothing even attempted."""
    with tempfile.TemporaryDirectory() as t:
        root, env, h = pending_sandbox(t)
        r = owner(root, env)
        check(r.returncode == 1 and nothing_granted(root) and "still the default 12345" in r.stdout,
              "BROKEN: default active -> refused, no drop-in, no sudo (%s)" % r.stdout.strip()[-160:])
        check("sshd" not in log_of(t) and "visudo" not in log_of(t), "neither sshd -t nor visudo ran")
        check(os.path.exists(os.path.join(root, "etc/coindojo/default-password")), "the mark stays")


def test_BROKEN_owner_access_with_a_fresh_hash_of_12345():
    """12345 again under a new salt (a boot-partition hash, or chpasswd): refused as "own", the mark follows it."""
    if not openssl_sha512():
        from hktest import skip
        skip("no openssl with passwd -6 on this host")
        return
    with tempfile.TemporaryDirectory() as t:
        root, env, _ = pending_sandbox(t)
        h = subprocess.run(["openssl", "passwd", "-6", "-salt", "EXAMPLEnew", "-stdin"], input="12345",
                           capture_output=True, text=True).stdout.strip()
        set_shadow(root, h)
        r = owner(root, env)
        check(r.returncode == 1 and nothing_granted(root) and "the new password is the default 12345" in r.stdout,
              "BROKEN: 12345 under a new salt -> refused, nothing granted (%s)" % r.stdout.strip()[-160:])
        check(rd(root, "etc/coindojo/default-password").strip() == h, "the mark is renewed with it: still the default")
        check(os.path.exists(os.path.join(root, "run/coindojo/default-login")), "and the reminder keeps showing")


def test_BROKEN_owner_access_no_password_or_unknown_hash():
    with tempfile.TemporaryDirectory() as t:
        root, env, _ = pending_sandbox(t)
        set_shadow(root, "!")
        r = owner(root, env)
        check(r.returncode == 1 and nothing_granted(root), "BROKEN: a locked account -> nothing granted")
    with tempfile.TemporaryDirectory() as t:
        root, env, _ = pending_sandbox(t)
        set_shadow(root, "$y$j9T$EXAMPLEsalt$EXAMPLEnotARealYescryptHash")
        r = owner(root, env)
        # libcrypt (Linux) tells it is not 12345; without it (macOS) it cannot be checked: fail closed
        check(r.returncode == 0 or (r.returncode == 1 and nothing_granted(root) and "cannot check" in r.stdout),
              "a hash that cannot be checked -> nothing granted (rc %d)" % r.returncode)


def test_BROKEN_sshd_t_failure_leaves_no_dropin_and_no_sudo():
    with tempfile.TemporaryDirectory() as t:
        root, env, _ = pending_sandbox(t)
        set_shadow(root, OWN_HASH)
        r = owner(root, dict(env, ND_SSHD_RC="1"))
        check(r.returncode == 1 and nothing_granted(root) and "sshd -t refused" in r.stdout
              and not os.path.exists(os.path.join(root, OWNER_SSH + ".tmp")),
              "BROKEN: sshd -t fails -> drop-in removed, no sudo, still pending (%s)" % r.stdout.strip()[-160:])
        check("visudo" not in log_of(t), "the sudo grant was not even tried")
        r = owner(root, env)                          # fixed sshd: the next try works (still pending)
        check(r.returncode == 0 and os.path.exists(os.path.join(root, OWNER_SSH)), "a retry after the fix grants")


def test_BROKEN_visudo_failure_takes_the_dropin_back():
    with tempfile.TemporaryDirectory() as t:
        root, env, _ = pending_sandbox(t)
        set_shadow(root, OWN_HASH)
        r = owner(root, dict(env, ND_VISUDO_RC="1"))
        check(r.returncode == 1 and nothing_granted(root) and "visudo refused" in r.stdout,
              "BROKEN: visudo refuses -> the SSH drop-in is removed again: both or neither (%s)" % r.stdout.strip()[-160:])


def test_BROKEN_owner_access_refusals():
    with tempfile.TemporaryDirectory() as t:
        root, env, _ = pending_sandbox(t)
        set_shadow(root, OWN_HASH)
        r = owner(root, env, user="root")
        check(r.returncode == 1 and nothing_granted(root), "BROKEN: --user root -> refused")
        r = owner(root, env, user="pi")
        check(r.returncode == 1 and nothing_granted(root), "BROKEN: another user -> refused")
        check(run("--owner-access", "--root", root, env=env).returncode == 2, "BROKEN: no --user -> usage error")
        check(run("--owner-access", "--user", "arcade", "--bogus", "x", env=env).returncode == 2, "BROKEN: a wrong flag -> usage")
    with tempfile.TemporaryDirectory() as t:         # personal / configured: nothing pending -> exit 3, key-only stays
        root, env = sandbox(t)
        os.makedirs(os.path.join(root, "etc/ssh/sshd_config.d"))
        set_shadow(root, OWN_HASH)
        r = owner(root, env)
        check(r.returncode == 3 and not os.path.exists(os.path.join(root, OWNER_SSH)), "nothing pending -> exit 3, no drop-in")


def test_runtime_passwd_never_writes_the_dropin():
    with tempfile.TemporaryDirectory() as t:
        root, env, _ = pending_sandbox(t)
        set_shadow(root, OWN_HASH)
        run("--password-changed", "--root", root, env=env)
        apply(root, env)
        check(nothing_granted(root), "BROKEN: passwd at runtime + the next boot -> no drop-in, no sudo")


def test_firstboot_password_hash_same_end_state():
    """coindojo.yml password_hash at boot ends exactly like the SETTINGS helper: drop-in + admin rule, nothing pending."""
    states = []
    for how in ("boot", "settings"):
        with tempfile.TemporaryDirectory() as t:
            root, env, _ = pending_sandbox(t)
            if how == "boot":
                open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write("password_hash: '%s'\n" % OWN_HASH)
                r = apply(root, env)
            else:
                set_shadow(root, OWN_HASH)
                r = owner(root, env)
            states.append((r.returncode, open(os.path.join(root, OWNER_SSH)).read() if os.path.exists(os.path.join(root, OWNER_SSH)) else None,
                           os.path.exists(os.path.join(root, ADMIN)), os.path.exists(os.path.join(root, PENDING)),
                           os.path.exists(os.path.join(root, "etc/coindojo/default-password")),
                           os.path.exists(os.path.join(root, "run/coindojo/default-login"))))
    check(states[0] == states[1] and states[0][0] == 0 and states[0][1] and states[0][2] and not states[0][3]
          and not states[0][4] and not states[0][5], "boot password_hash == SETTINGS helper end state: %s" % states)
    if openssl_sha512():
        with tempfile.TemporaryDirectory() as t:
            root, env, _ = pending_sandbox(t)
            h = subprocess.run(["openssl", "passwd", "-6", "-salt", "EXAMPLEboot", "-stdin"], input="12345",
                               capture_output=True, text=True).stdout.strip()
            open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write("password_hash: '%s'\n" % h)
            r = apply(root, env)
            check(nothing_granted(root) and "Default login arcade / 12345" in r.stdout,
                  "BROKEN: password_hash of 12345 at boot -> no drop-in, no sudo, the reminder stays")


def test_owner_dropin_name_matches_verify():
    check('OWNER_SSH = "/%s"' % OWNER_SSH in open(FB).read(), "firstboot's OWNER_SSH is the path the tests use")
    v = open(os.path.join(SKILL, "scripts", "verify.sh")).read()
    check("/" + OWNER_SSH.split("/", 1)[1] in v and "FAIL: the image already has the owner's password-SSH drop-in" in v,
          "verify.sh checks the image has no owner drop-in")

# ---- #270 re-review: nothing in the user's home is written through a symlink (it would hand /etc to the user) ----

def victim(t):
    """A folder outside the home standing in for /etc: a 'shadow' file that a followed write would change."""
    v = os.path.join(t, "victim")
    os.makedirs(v)
    open(os.path.join(v, "shadow"), "w").write("root:$6$EXAMPLEroot$x:1::::::\n/home/arcade/ stays\n")
    os.chmod(os.path.join(v, "shadow"), 0o640)
    os.chmod(v, 0o755)
    return v


def snapshot(d):
    out = {}
    for dp, dns, fns in os.walk(d):
        for n in dns + fns:
            q = os.path.join(dp, n)
            st = os.lstat(q)
            out[os.path.relpath(q, d)] = (stat.S_IMODE(st.st_mode), st.st_uid, open(q).read() if stat.S_ISREG(st.st_mode) else "")
    out["."] = (stat.S_IMODE(os.stat(d).st_mode), os.stat(d).st_uid, "")
    return out


def key_config(root):
    open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write('ssh_pubkey: "%s"\n' % KEY)


def test_ssh_dir_symlink_to_etc_is_refused():
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        v = victim(t)
        os.symlink(v, os.path.join(root, "home/arcade/.ssh"))
        before = snapshot(v)
        key_config(root)
        r = apply(root, env)
        check(snapshot(v) == before, "BROKEN: ~/.ssh -> /etc: nothing outside the home changes (no chmod 700, no file)")
        check(r.returncode == 1 and "not a plain folder" in rd(root, "boot/firmware/coindojo.error.txt"),
              "the key is refused with a reason in coindojo.error.txt (%s)" % r.stdout.strip()[-120:])


def test_authorized_keys_symlink_to_shadow_is_refused():
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        v = victim(t)
        os.makedirs(os.path.join(root, "home/arcade/.ssh"))
        os.symlink(os.path.join(v, "shadow"), os.path.join(root, "home/arcade/.ssh/authorized_keys"))
        before = snapshot(v)
        key_config(root)
        r = apply(root, env)
        check(snapshot(v) == before, "BROKEN: authorized_keys -> /etc/shadow: shadow unchanged (no key appended, mode kept)")
        check(r.returncode == 1 and grep_tree(os.path.join(root, "home"), "EXAMPLEroot") == [],
              "refused, and the shadow text is copied nowhere into the home")


def test_planted_tmp_symlink_is_not_followed():
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        v = victim(t)
        os.makedirs(os.path.join(root, "home/arcade/.ssh"))
        tmp = os.path.join(root, "home/arcade/.ssh/authorized_keys.tmp")
        os.symlink(os.path.join(v, "shadow"), tmp)
        before = snapshot(v)
        key_config(root)
        r = apply(root, env)
        ak = os.path.join(root, "home/arcade/.ssh/authorized_keys")
        check(snapshot(v) == before, "BROKEN: a planted authorized_keys.tmp -> /etc/shadow: shadow unchanged")
        check(r.returncode == 0 and not os.path.islink(ak) and rd(root, "home/arcade/.ssh/authorized_keys") == KEY + "\n"
              and stat.S_IMODE(os.stat(ak).st_mode) == 0o600 and not os.path.lexists(tmp),
              "the planted link is removed (not its target); the key lands in a real 0600 file")
        check(stat.S_IMODE(os.stat(os.path.join(root, "home/arcade/.ssh")).st_mode) == 0o700, "~/.ssh is 0700")


def test_rename_dotfile_symlink_is_not_followed():
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        v = victim(t)
        cut_at(root, "autologin")                     # the rename resumes at step 4: the dotfiles
        os.symlink(os.path.join(v, "shadow"), os.path.join(root, "home/dojo/.bash_profile"))
        before = snapshot(v)
        r = apply(root, env)
        check(snapshot(v) == before, "BROKEN: .bash_profile -> /etc/shadow during a rename: shadow unchanged")
        check("skipped ~/.bash_profile" in r.stdout and "/home/dojo/" in rd(root, "home/dojo/.config/retroarch/retroarch.cfg")
              and not os.path.exists(os.path.join(root, "var/lib/coindojo/rename")),
              "the symlinked dotfile is skipped, the real one is rewritten, the rename finishes")


def test_root_writes_refuse_a_planted_tmp_symlink():
    """write() (root's own files) never writes through a planted <file>.tmp symlink."""
    with tempfile.TemporaryDirectory() as t:
        root, env = sandbox(t)
        v = victim(t)
        os.symlink(os.path.join(v, "shadow"), os.path.join(root, "etc/hostname.tmp"))
        before = snapshot(v)
        open(os.path.join(root, "boot/firmware/coindojo.yml"), "w").write("hostname: dojo\n")
        r = apply(root, env)
        check(r.returncode == 0 and snapshot(v) == before and rd(root, "etc/hostname") == "dojo\n",
              "BROKEN: etc/hostname.tmp planted as a symlink -> not followed, hostname still written")


if __name__ == "__main__":
    main(globals())
