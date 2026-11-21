#!/usr/bin/env python3
"""#237: make base-image - the official Pi OS Lite arm64 image, sha256-pinned (scripts/base-image.sh).

Offline: every download is a file:// URL to a small fake .img.xz made here. Broken cases that must FAIL: a wrong
sha256 (the download is deleted), a URL without its sha, a malformed sha, a URL that is not a .img.xz.
"""
import hashlib
import lzma
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "tests", "lib"))
from hktest import check, main  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(HERE), "scripts", "base-image.sh")
PAYLOAD = b"not a real Pi OS image, test payload\n" * 100


def fake(t):
    src = os.path.join(t, "src", "2026-01-01-raspios-test-arm64-lite.img.xz")
    os.makedirs(os.path.dirname(src))
    with open(src, "wb") as f:
        f.write(lzma.compress(PAYLOAD))
    return src, hashlib.sha256(open(src, "rb").read()).hexdigest()


def run(dest, env_extra, path=None):
    env = dict(os.environ)
    env.pop("BASE_URL", None)
    env.pop("BASE_SHA256", None)
    env.update(env_extra)
    if path:
        env["PATH"] = path
    return subprocess.run(["bash", SCRIPT, dest], env=env, capture_output=True, text=True, timeout=60)


def test_pin_is_official_and_sha_pinned():
    body = open(SCRIPT).read()
    url = re.search(r"^PIN_URL=(\S+)$", body, re.M)
    sha = re.search(r"^PIN_SHA256=(\S+)$", body, re.M)
    check(url and url.group(1).startswith("https://downloads.raspberrypi.com/raspios_lite_arm64/images/")
          and url.group(1).endswith("-arm64-lite.img.xz"), "the pin is the official Pi OS Lite arm64 URL")
    check(sha and re.fullmatch(r"[0-9a-f]{64}", sha.group(1)), "the pin carries a sha256")


def test_fetch_verifies_and_decompresses():
    with tempfile.TemporaryDirectory() as t:
        src, sha = fake(t)
        dest = os.path.join(t, "dest")
        r = run(dest, {"BASE_URL": "file://" + src, "BASE_SHA256": sha})
        img = os.path.join(dest, "2026-01-01-raspios-test-arm64-lite.img")
        check(r.returncode == 0 and "BASE_IMG=" + img in r.stdout, "fetch -> sha ok -> prints BASE_IMG (%s)" % r.stderr.strip()[-120:])
        check(os.path.isfile(img) and open(img, "rb").read() == PAYLOAD, "the .img is the decompressed payload")
        os.remove(src)   # a second run must not download again
        r = run(dest, {"BASE_URL": "file://" + src, "BASE_SHA256": sha})
        check(r.returncode == 0 and "already downloaded" in r.stdout, "an existing, matching download is reused")


def test_wrong_sha_refused_and_deleted():
    with tempfile.TemporaryDirectory() as t:
        src, sha = fake(t)
        dest = os.path.join(t, "dest")
        bad = ("0" if sha[0] != "0" else "1") + sha[1:]
        r = run(dest, {"BASE_URL": "file://" + src, "BASE_SHA256": bad})
        check(r.returncode == 1 and "sha256 mismatch" in r.stderr, "BROKEN: wrong base sha -> refused")
        check(os.listdir(dest) == [], "the mismatching download is deleted (%s)" % os.listdir(dest))


def test_bad_inputs_refused():
    with tempfile.TemporaryDirectory() as t:
        src, sha = fake(t)
        dest = os.path.join(t, "dest")
        r = run(dest, {"BASE_URL": "file://" + src})
        check(r.returncode == 1 and "set both BASE_URL and BASE_SHA256" in r.stderr, "BROKEN: a URL without its sha -> refused")
        r = run(dest, {"BASE_URL": "file://" + src, "BASE_SHA256": "abc"})
        check(r.returncode == 1 and "64 lowercase hex" in r.stderr, "BROKEN: a malformed sha -> refused")
        r = run(dest, {"BASE_URL": "file://" + src[:-3], "BASE_SHA256": sha})
        check(r.returncode == 1 and "must point at a .img.xz" in r.stderr, "BROKEN: not a .img.xz -> refused")
        r = run(dest, {"BASE_URL": "file://" + os.path.join(t, "missing.img.xz"), "BASE_SHA256": sha})
        check(r.returncode == 1 and "download failed" in r.stderr and not os.listdir(dest), "BROKEN: a failed download leaves nothing behind")


def test_decompress_without_xz():
    with tempfile.TemporaryDirectory() as t:
        src, sha = fake(t)
        farm = os.path.join(t, "farm")
        os.makedirs(farm)
        for tool in ("bash", "curl", "python3", "mkdir", "rm", "mv", "basename", "dirname", "awk", "sha256sum", "shasum", "cat", "perl"):
            p = subprocess.run(["/bin/sh", "-c", "command -v " + tool], capture_output=True, text=True).stdout.strip()
            if p.startswith("/"):
                os.symlink(p, os.path.join(farm, tool))
        dest = os.path.join(t, "dest")
        r = run(dest, {"BASE_URL": "file://" + src, "BASE_SHA256": sha}, path=farm)
        img = os.path.join(dest, "2026-01-01-raspios-test-arm64-lite.img")
        check(r.returncode == 0 and open(img, "rb").read() == PAYLOAD, "no xz on the host -> python's lzma decompresses (%s)" % r.stderr.strip()[-120:])


if __name__ == "__main__":
    main(globals())
