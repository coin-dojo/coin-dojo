#!/usr/bin/env python3
"""verify.sh's network-folder block (#266): the image's smb.conf and units, with broken cases.

    python3 skills/build-image/tests/test_verify_share.py

Runs the `# >>> share` block of scripts/verify.sh on a fixture root: the smb.conf the share role renders (its own
test's renderer, the same template), the enabled-unit links and the nmbd mask. Each planted case must FAIL.
"""
import os
import re
import subprocess
import sys
import tempfile

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(SKILL))
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
sys.path.insert(0, os.path.join(REPO, "ansible", "roles", "share", "tests"))
from hktest import check, main  # noqa: E402
import test_share  # noqa: E402

UNITS = ("smbd.service", "coin-dojo-share-sync.service", "coin-dojo-share-sync.path")


def block():
    with open(os.path.join(SKILL, "scripts", "verify.sh"), encoding="utf-8") as fh:
        m = re.search(r"\n# >>> share.*?\n(.*?)\n# <<< share", fh.read(), re.S)
    return m.group(1) if m else None


def image(root, conf=None, units=UNITS, nmbd_masked=True, dropin=True):
    wants = os.path.join(root, "etc/systemd/system/multi-user.target.wants")
    os.makedirs(wants)
    os.makedirs(os.path.join(root, "etc/samba"))
    open(os.path.join(root, "etc/samba/smb.conf"), "w").write(test_share.conf() if conf is None else conf)
    for u in units:
        os.symlink("/usr/lib/systemd/system/" + u, os.path.join(wants, u))
    if nmbd_masked:
        os.symlink("/dev/null", os.path.join(root, "etc/systemd/system/nmbd.service"))
    if dropin:
        os.makedirs(os.path.join(root, "etc/systemd/system/smbd.service.d"))
        open(os.path.join(root, "etc/systemd/system/smbd.service.d/50-coin-dojo-hosts.conf"), "w").write(
            test_share.render(test_share._read("templates", "50-coin-dojo-hosts.conf.j2")) if dropin is True else dropin)


def run(setup):
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        r = subprocess.run(["bash", "-c", "R=%s\n%s" % (t, block())], capture_output=True, text=True)
        return r.stdout + r.stderr


def test_block_exists():
    check(block() is not None, "verify.sh has a # >>> share ... # <<< share block")


def test_good_image_passes():
    out = run(image)
    check("FAIL" not in out and "PASS: network folder shares roms and bios only" in out
          and "PASS: network folder guest off" in out and "PASS: network folder min protocol SMB3" in out
          and "PASS: network folder bound to lo eth0 wlan0" in out and "PASS: smbd enabled" in out
          and "PASS: no NetBIOS" in out and "PASS: network folder allows only the live local subnets" in out
          and "PASS: bios share blocks MAME" in out and "PASS: roms share blocks MAME command files" in out
          and "PASS: smbd refuses to start on a bad hosts file" in out, "the rendered share passes verify")


def test_lever_off_passes():
    out = run(lambda t: image(t, units=()))
    check("PASS: no network folder" in out and "FAIL" not in out, "smbd not enabled = folder off, no FAIL\n" + out)


def test_broken_images_fail():
    good = test_share.conf()
    cases = [
        ("a third share", good + "\n[data]\n   path = /data\n", "FAIL: smb.conf sections"),
        ("home folders", good + "\n[homes]\n   browseable = no\n", "FAIL: smb.conf sections"),
        ("guest ok", good.replace("guest ok = no", "guest ok = yes", 1), "FAIL: network folder allows guests"),
        ("public = yes", good.replace("guest ok = no", "public = yes", 1), "FAIL: network folder allows guests"),
        ("map to guest", good.replace("map to guest = never", "map to guest = bad user"), "FAIL: network folder allows guests"),
        ("SMB2", good.replace("server min protocol = SMB3", "server min protocol = SMB2"), "FAIL: network folder min protocol"),
        ("no floor", good.replace("server min protocol = SMB3\n", ""), "FAIL: network folder min protocol"),
        ("not bound", good.replace("bind interfaces only = yes", "bind interfaces only = no"), "FAIL: network folder not bound"),
        ("port 139", good.replace("smb ports = 445", "smb ports = 445 139"), "FAIL: NetBIOS on"),
        ("all private ranges", good.replace("hosts allow = 127.0.0.1 ::1", "hosts allow = 127.0.0.1 ::1 192.168.0.0/16"),
         "FAIL: network folder hosts allow"),
        ("no live-subnet include", good.replace("   include = /run/coin-dojo-share/hosts.conf\n", ""),
         "FAIL: network folder hosts allow"),
        ("no mame veto", good.replace("/desktop.ini/mame/*.lua/", "/desktop.ini/*.lua/"), "WARN: bios share does not veto"),
        ("no .cmd veto", good.replace("/mame/*.lua/*.cmd/", "/mame/*.lua/"), "WARN: roms share does not veto *.cmd"),
    ]
    for what, conf, want in cases:
        out = run(lambda t, c=conf: image(t, conf=c))
        check(want in out, "BROKEN: %s fails verify" % what)
    check("FAIL: NetBIOS on" in run(lambda t: image(t, nmbd_masked=False)), "BROKEN: nmbd not masked fails verify")
    check("FAIL: smbd starts without checking its hosts file" in run(lambda t: image(t, dropin="[Service]\n")),
          "BROKEN: a drop-in without the --hosts-only check fails verify")
    check("FAIL: network folder hosts allow" in run(lambda t: image(t, dropin=False)),
          "BROKEN: no smbd drop-in (nothing writes the live subnets) fails verify")
    check("FAIL: coin-dojo-share-sync.path enabled" in run(lambda t: image(t, units=UNITS[:2])),
          "BROKEN: the passwd watch not enabled fails verify")


if __name__ == "__main__":
    main(globals())
