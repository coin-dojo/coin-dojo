#!/usr/bin/env python3
"""#237: the image build runs on Linux x86_64/arm64 and macOS, without the Keychain, and refuses early.

Exercises scripts/lib-host.sh, scripts/imgsize.py and scripts/run.sh with a stub `docker` on PATH (no daemon,
no image build): every case is a fast-fail path or the orchestration order.

Broken cases that must FAIL: no docker, docker daemon down, x86_64 without binfmt and HK_BINFMT=no, a binfmt
install that does not take, a WORK folder the daemon cannot see, no space, an unpinned image, an image too big
for the smallest card, an xz/garbage base, a verify FAIL (no .sha256 written).
"""
import os
import re
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "tests", "lib"))
from hktest import check, main  # noqa: E402

SKILL = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL, "scripts")
LIB = os.path.join(SCRIPTS, "lib-host.sh")
IMGSIZE = os.path.join(SCRIPTS, "imgsize.py")
BASH = "/bin/bash"

# A stub docker: STUB_ARCH = daemon arch, STUB_STATE dir holds 'binfmt' once registered, STUB_LOG gets every call.
STUB = r'''#!/bin/bash
echo "docker $* HK_SET=[${HK_SET:-}]" >> "$STUB_LOG"
case "$1" in
  info)
    [ -n "${STUB_INFO_FAIL:-}" ] && exit 1
    [ "${2:-}" = --format ] && echo "$STUB_ARCH"; exit 0 ;;
  run)
    a="$*"
    case "$a" in
      *--install\ arm64*) [ -z "${STUB_BINFMT_BROKEN:-}" ] && touch "$STUB_STATE/binfmt"; exit 0 ;;
      *--platform\ linux/arm64*uname\ -m*)
        if [ "$STUB_ARCH" = aarch64 ] || [ -e "$STUB_STATE/binfmt" ]; then echo aarch64; exit 0; fi
        echo "exec /usr/bin/uname: exec format error" >&2; exit 1 ;;
      *test\ -f\ /work/*) [ -n "${STUB_MOUNT_FAIL:-}" ] && exit 1; exit 0 ;;
      *prep-image.sh*) out=${a##*/work/}; out=${out%% *}; : > "$STUB_WORK/$out"; echo PREP_OK; exit 0 ;;
      *verify.sh*) printf '%s\n' "${STUB_VERIFY:-PASS: all}" VERIFY_DONE; exit 0 ;;
      *) exit 0 ;;
    esac ;;
esac
exit 0
'''


def stubenv(tmp, arch="aarch64", **extra):
    sd = os.path.join(tmp, "stubbin")
    os.makedirs(sd, exist_ok=True)
    os.makedirs(os.path.join(tmp, "state"), exist_ok=True)
    p = os.path.join(sd, "docker")
    if not os.path.exists(p):          # a test may have tailored the stub already
        with open(p, "w") as f:
            f.write(STUB)
        os.chmod(p, 0o755)
    env = dict(os.environ, PATH=sd + ":/usr/bin:/bin:/usr/sbin:/sbin", STUB_ARCH=arch,
               STUB_STATE=os.path.join(tmp, "state"), STUB_LOG=os.path.join(tmp, "log"), STUB_WORK=tmp)
    env.update(extra)
    return env


def lib(snippet, env):
    return subprocess.run([BASH, "-c", '. "%s"\n%s' % (LIB, snippet)], env=env, capture_output=True, text=True)


def log(tmp):
    p = os.path.join(tmp, "log")
    return open(p).read() if os.path.exists(p) else ""


def test_no_docker_is_refused():
    with tempfile.TemporaryDirectory() as t:
        empty = os.path.join(t, "empty")
        os.makedirs(empty)
        r = lib("hk_check_docker", dict(os.environ, PATH=empty))
        check(r.returncode == 1 and "docker not found" in r.stderr and "Colima" in r.stderr and "Docker Engine" in r.stderr,
              "BROKEN: no docker on PATH -> refused, names the install for Linux and macOS (%r)" % r.stderr[:120])


def test_daemon_down_is_refused():
    with tempfile.TemporaryDirectory() as t:
        r = lib("hk_check_docker", stubenv(t, STUB_INFO_FAIL="1"))
        check(r.returncode == 1 and "daemon does not answer" in r.stderr and "systemctl start docker" in r.stderr,
              "BROKEN: docker daemon down -> refused with how to start it")


def test_arm64_host_runs_native():
    with tempfile.TemporaryDirectory() as t:
        r = lib("hk_ensure_arm64", stubenv(t, "aarch64"))
        check(r.returncode == 0 and "natively" in r.stdout, "arm64 docker host (Mac, Linux arm64) -> native chroot")
        check("--install" not in log(t), "arm64 host never touches binfmt")


def test_x86_with_binfmt_registered():
    with tempfile.TemporaryDirectory() as t:
        env = stubenv(t, "x86_64")
        open(os.path.join(t, "state", "binfmt"), "w").close()
        r = lib("hk_ensure_arm64", env)
        check(r.returncode == 0 and "already registered" in r.stdout, "x86_64 with qemu-aarch64 registered -> goes on")
        check("--install" not in log(t), "an existing binfmt registration is not redone")


def test_x86_without_binfmt_refused_when_no():
    with tempfile.TemporaryDirectory() as t:
        r = lib("hk_ensure_arm64", stubenv(t, "x86_64", HK_BINFMT="no"))
        check(r.returncode == 1 and "no qemu-aarch64 binfmt handler" in r.stderr and "--install arm64" in r.stderr,
              "BROKEN: x86_64 without binfmt and HK_BINFMT=no -> refused with the exact pinned command")
        check("--install" not in log(t), "HK_BINFMT=no never registers anything")


def test_x86_without_binfmt_auto_registers_pinned():
    with tempfile.TemporaryDirectory() as t:
        r = lib("hk_ensure_arm64", stubenv(t, "x86_64"))
        calls = log(t)
        check(r.returncode == 0 and "registering the qemu-aarch64 binfmt handler" in r.stdout and "--uninstall" in r.stdout,
              "x86_64 without binfmt -> registers it, says what it changes and how to undo it")
        check(re.search(r"run --privileged --rm tonistiigi/binfmt:[^ ]+@sha256:[0-9a-f]{64} --install arm64", calls),
              "the binfmt image is digest-pinned in the call (%s)" % calls.strip().splitlines()[-2:])


def test_x86_binfmt_install_that_does_not_take():
    with tempfile.TemporaryDirectory() as t:
        r = lib("hk_ensure_arm64", stubenv(t, "x86_64", STUB_BINFMT_BROKEN="1"))
        check(r.returncode == 1 and "still does not start" in r.stderr, "BROKEN: binfmt registered but arm64 still fails -> refused")


def test_mount_not_shared_is_refused():
    with tempfile.TemporaryDirectory() as t:
        r = lib('hk_check_mount "%s"' % t, stubenv(t, STUB_MOUNT_FAIL="1"))
        check(r.returncode == 1 and "docker cannot see" in r.stderr and "$HOME" in r.stderr,
              "BROKEN: WORK not shared with the docker VM -> refused, says Colima shares only $HOME")
        check(not [f for f in os.listdir(t) if f.startswith(".hk-mount-probe")], "the mount probe file is cleaned up")
        r = lib('hk_check_mount "%s"' % t, stubenv(t))
        check(r.returncode == 0, "a shared WORK passes")


def test_space_check():
    with tempfile.TemporaryDirectory() as t:
        r = lib('hk_check_space "%s" 999999999' % t, stubenv(t))
        check(r.returncode == 1 and "not enough free space" in r.stderr, "BROKEN: too little disk -> refused")
        r = lib('hk_check_space "%s" 1' % t, stubenv(t))
        check(r.returncode == 0, "enough disk passes")


def test_sha256_portable():
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "f")
        open(p, "w").write("abc")
        r = lib('hk_sha256 "%s"' % p, stubenv(t))
        check(r.stdout.strip() == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "hk_sha256 = sha256 of the file")


PIN = re.compile(r'^(HK_[A-Z]+_IMAGE)=\$\{\1:-([^}]+)\}$', re.M)


def unpinned(text):
    return [img for _, img in PIN.findall(text) if not re.search(r"@sha256:[0-9a-f]{64}$", img)]


def test_images_digest_pinned():
    text = open(LIB).read()
    names = [n for n, _ in PIN.findall(text)]
    check(set(names) == {"HK_DEBIAN_IMAGE", "HK_BINFMT_IMAGE"}, "lib-host.sh pins the debian and binfmt images (%s)" % names)
    check(not unpinned(text), "every container image is digest-pinned")
    check(unpinned("HK_DEBIAN_IMAGE=${HK_DEBIAN_IMAGE:-debian:12}\n") == ["debian:12"], "BROKEN: a tag-only image is caught")
    run = open(os.path.join(SCRIPTS, "run.sh")).read()
    check(not re.search(r"\bdebian:12\b", run), "run.sh uses the pinned image variable, not a bare debian:12")


def test_no_mac_only_tool_on_the_build_path():
    run = open(os.path.join(SCRIPTS, "run.sh")).read()
    code = "\n".join(l for l in run.splitlines() if not l.lstrip().startswith("#"))
    check("colima" not in code.lower().replace("colima start", ""), "run.sh does not require Colima")
    # `security` (the macOS Keychain) only behind `command -v security`
    check(re.search(r"command -v security >/dev/null 2>&1 \|\| KC=", code) is not None, "the Keychain is optional (only when `security` exists)")
    for tool in ("diskutil", "hdiutil", "shasum -a 256", "sha256sum"):
        check(tool not in code, "run.sh calls no host-specific tool %s directly" % tool)


# ---- imgsize: the smallest card ----

def fake_base(path, parts=((8192, 1048576, 0x0c), (1056768, 4194304, 0x83)), magic=b"\x55\xaa"):
    mbr = bytearray(512)
    for i, (start, size, typ) in enumerate(parts):
        e = 446 + 16 * i
        mbr[e + 4] = typ
        mbr[e + 8:e + 16] = struct.pack("<II", start, size)
    mbr[510:512] = magic
    with open(path, "wb") as f:
        f.write(bytes(mbr))


def imgsize(*args):
    return subprocess.run([sys.executable, IMGSIZE] + [str(a) for a in args], capture_output=True, text=True)


def test_imgsize_defaults_fit_16gb():
    with tempfile.TemporaryDirectory() as t:
        b = os.path.join(t, "base.img")
        fake_base(b)
        r = imgsize(b, 7, 512, 16)
        # prep-image.sh geometry: p2 start 1056768 + 7 GiB, data aligned up to 1 MiB, 512 MiB
        root_end = 1056768 + 7 * 2097152 - 1
        data_start = ((root_end + 1 + 2047) // 2048) * 2048
        want = (data_start + 512 * 2048) * 512 // 1048576
        check(r.returncode == 0 and ("image: %d MiB" % want) in r.stdout, "defaults (7 GiB root, 512 MiB data) fit a 16 GB card: %s" % r.stdout.strip())


def test_imgsize_too_big_refused():
    with tempfile.TemporaryDirectory() as t:
        b = os.path.join(t, "base.img")
        fake_base(b)
        r = imgsize(b, 14, 512, 16)
        check(r.returncode == 1 and "would not fit a 16 GB card" in r.stderr, "BROKEN: ROOT_GIB=14 on a 16 GB minimum -> refused")
        r = imgsize(b, 14, 512, 32)
        check(r.returncode == 0, "the same image fits when MIN_CARD_GB=32")


def test_imgsize_bad_base_refused():
    with tempfile.TemporaryDirectory() as t:
        b = os.path.join(t, "x.img.xz")
        open(b, "wb").write(b"\xfd7zXZ\x00" + b"\0" * 600)
        r = imgsize(b, 7, 512)
        check(r.returncode == 2 and "xz-compressed" in r.stderr, "BROKEN: an .xz base -> refused, says to decompress")
        g = os.path.join(t, "g.img")
        open(g, "wb").write(b"\0" * 1024)
        r = imgsize(g, 7, 512)
        check(r.returncode == 2 and "no MBR" in r.stderr, "BROKEN: a file without an MBR -> refused")
        one = os.path.join(t, "one.img")
        fake_base(one, parts=((8192, 1048576, 0x0c),))
        r = imgsize(one, 7, 512)
        check(r.returncode == 2 and "1 partition" in r.stderr, "BROKEN: a 1-partition image -> refused")
        r = imgsize(os.path.join(t, "missing.img"), 7, 512)
        check(r.returncode == 2, "BROKEN: a missing base -> refused")


# ---- run.sh orchestration with the stub (no Keychain, order, verify gate, .sha256) ----

def run_sh(t, env_extra, rootgib="7"):
    base = os.path.join(t, "base.img")
    fake_base(base)
    out = os.path.join(t, "out", "out.img")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fake = os.path.join(t, "fakebuild")   # #244: the emulator builds the roles copy (only their presence is checked)
    for f in ("retroarch/out/retroarch", "n64/out/mupen64plus_next_libretro.so", "flycast/out/flycast",
              "stella/out/stella_libretro.so"):
        os.makedirs(os.path.dirname(os.path.join(fake, f)), exist_ok=True)
        open(os.path.join(fake, f), "w").close()
    env = stubenv(t, "x86_64", HK_CABINET_USER="coindojo", HK_CABINET_UID="1000", HK_CABINET_HOSTNAME="coin-dojo",
                  IMAGE_BUILD_DIR=fake, HK_ROM_ROOT="/data/roms", HK_IMAGE_FIGHTCADE="false",
                  CABINET_PW="EXAMPLE-not-real", COIN_DOJO_PUBKEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEXAMPLE test",
                  STUB_WORK=os.path.dirname(out))
    env.pop("EDITION", None)   # make exports EDITION (mk/flags.mk): a direct run.sh builds community unless a test asks
    env.update(env_extra)
    r = subprocess.run([BASH, os.path.join(SCRIPTS, "run.sh"), base, out, rootgib], env=env,
                       capture_output=True, text=True, timeout=60)
    return r, out


def test_run_sh_linux_x86_flow_without_keychain():
    with tempfile.TemporaryDirectory() as t:
        r, out = run_sh(t, {})
        calls = log(t)
        check(r.returncode == 0, "run.sh completes on a stubbed x86_64 host with CABINET_PW from the env (rc %d: %s)" % (r.returncode, r.stderr[-300:]))
        check("== EDITION: community" in r.stdout and re.search(r"(?m)^DONE -> .*EDITION=community", r.stdout) is not None,
              "the edition is printed at the start and in the DONE line")
        check("security" not in calls and "find-generic-password" not in r.stdout + r.stderr, "no Keychain call when CABINET_PW is set")
        order = [w for w in re.findall(r"(--install arm64|prep-image\.sh|build\.sh|provision\.sh|verify\.sh)", calls)]
        check(order == ["--install arm64", "prep-image.sh", "build.sh", "provision.sh", "verify.sh"],
              "binfmt -> prep -> build -> provision (the roles, #244) -> verify (%s)" % order)
        check(re.search(r"--platform[^\n]*provision\.sh", calls) is None, "the roles run from the Ansible container on the host's arch")
        check(re.search(r"--platform linux/arm64 [^\n]*build\.sh", calls) is not None, "only the chroot build runs as linux/arm64")
        check(re.search(r"--platform[^\n]*prep-image\.sh", calls) is None, "prep runs on the host's own arch")
        check("/base:ro" in calls, "the base image is mounted read-only from its own folder")
        sha = out + ".sha256"
        check(os.path.isfile(sha) and open(sha).read().endswith("  out.img\n"), "out.img.sha256 written in sha256sum format")
        left = [f for f in os.listdir(os.path.dirname(out)) if f not in ("out.img", "out.img.sha256")]
        check(left == [], "staged scripts, example and config copies removed after the build (%s)" % left)
        check("EXAMPLE-not-real" not in calls, "the password never appears in a docker argv (passed with -e NAME only)")
        check(re.search(r"-e COINDOJO_PASSWORD -e COINDOJO_PUBKEY [^\n]*build\.sh", calls) is not None,
              "the build gets the neutral COINDOJO_* names (#240)")


PINNED_CORES = ["fbneo", "genesis_plus_gx", "mame", "mednafen_pce", "mednafen_saturn", "nestopia", "pcsx_rearmed",
                "snes9x"]


def test_run_sh_release_needs_pinned_cores():
    """#274: HK_LICENCES_STRICT=1 (a release) stops before docker when a pinned core is not built; a personal build
    does not need them (the cores role falls back to the buildbot with a WARN)."""
    with tempfile.TemporaryDirectory() as t:
        r, _ = run_sh(t, {"HK_LICENCES_STRICT": "1"})
        check(r.returncode != 0 and "build/fbneo/out/fbneo_libretro.so is missing" in r.stderr
              and "make -C build fbneo" in r.stderr and "build.sh" not in log(t),
              "BROKEN: a release without the pinned cores stops before docker (%s)" % r.stderr[-200:])
    with tempfile.TemporaryDirectory() as t:
        fake = os.path.join(t, "fakebuild")
        for c in PINNED_CORES:
            os.makedirs(os.path.join(fake, c, "out"))
            open(os.path.join(fake, c, "out", "%s_libretro.so" % c), "w").close()
        r, _ = run_sh(t, {"HK_LICENCES_STRICT": "1"})
        check(r.returncode == 0, "a release with all eight pinned cores built goes on (rc %d: %s)" % (r.returncode,
                                                                                               r.stderr[-200:]))
    with tempfile.TemporaryDirectory() as t:
        r, _ = run_sh(t, {"HK_LICENCES_STRICT": "0"})
        check(r.returncode == 0, "HK_LICENCES_STRICT=0 (personal) needs no pinned core build (%s)" % r.stderr[-200:])


def test_run_sh_edition():
    with tempfile.TemporaryDirectory() as t:
        r, out = run_sh(t, {"EDITION": "personal"})
        check(r.returncode == 0 and "EDITION: PERSONAL" in r.stdout and "never share" in r.stdout,
              "a personal image is announced loudly (%s)" % r.stderr[-160:])
    with tempfile.TemporaryDirectory() as t:
        r, out = run_sh(t, {"EDITION": "bogus"})
        check(r.returncode != 0 and "EDITION='bogus'" in r.stderr and log(t) == "", "BROKEN: an unknown EDITION stops before docker")


def test_run_sh_ssh_password_with_default_login_refused():
    """#265: a community image without a password of yours gets 12345: password SSH with it stops before docker."""
    nopw = {"CABINET_PW": "", "COIN_DOJO_PUBKEY": "", "COIN_DOJO_SSH_PASSWORD": "yes"}
    with tempfile.TemporaryDirectory() as t:
        stubenv(t)
        r, _ = run_sh(t, dict(nopw, PATH=linux_like_path(t)))
        check(r.returncode != 0 and "default password 12345" in r.stderr and "prep-image.sh" not in log(t),
              "BROKEN: community + COIN_DOJO_SSH_PASSWORD=yes + no password -> refused before the build (%s)" % r.stderr[-160:])
    with tempfile.TemporaryDirectory() as t:
        stubenv(t)
        r, _ = run_sh(t, dict(nopw, EDITION="personal", PATH=linux_like_path(t)))
        check(r.returncode == 0, "personal + password SSH: no default password, the opt-in stays possible (%s)" % r.stderr[-160:])
    with tempfile.TemporaryDirectory() as t:
        r, _ = run_sh(t, {"COIN_DOJO_SSH_PASSWORD": "yes"})
        check(r.returncode == 0, "community + password SSH + a password of yours (CABINET_PW): builds (%s)" % r.stderr[-160:])
    with tempfile.TemporaryDirectory() as t:
        cfg = os.path.join(t, "pw.yml")
        open(cfg, "w").write("password_hash: '$6$EXAMPLEsalt$EXAMPLEnotARealHash0123456789'\n")
        r, _ = run_sh(t, {"CONFIG": cfg, "COIN_DOJO_SSH_PASSWORD": "yes"})
        check(r.returncode == 0, "community + password SSH + CONFIG= with password_hash: builds (%s)" % r.stderr[-160:])
    with tempfile.TemporaryDirectory() as t:
        cfg = os.path.join(t, "nopw.yml")
        open(cfg, "w").write("hostname: dojo\n")
        r, _ = run_sh(t, {"CONFIG": cfg, "COIN_DOJO_SSH_PASSWORD": "yes"})
        check(r.returncode != 0 and "default password 12345" in r.stderr,
              "BROKEN: community + password SSH + CONFIG= without a password -> refused")


def test_run_sh_verify_fail_writes_no_sha():
    with tempfile.TemporaryDirectory() as t:
        r, out = run_sh(t, {"STUB_VERIFY": "FAIL: SSH key"})
        check(r.returncode == 1 and "do not flash" in r.stderr, "BROKEN: a verify FAIL line stops run.sh")
        check(not os.path.exists(out + ".sha256"), "no .sha256 for an image that failed verify")


def test_secret_image_owner_only_even_when_verify_fails():
    """#237 round 3: the image is created 0600 before prep writes into it, so a failed verify leaves no readable PSK."""
    with tempfile.TemporaryDirectory() as t:
        good = os.path.join(t, "good.yml")
        open(good, "w").write("wifi_ssid: n\nwifi_psk: EXAMPLE-wkey-0237x\n")
        r, out = run_sh(t, {"CONFIG": good, "STUB_VERIFY": "FAIL: something"})
        check(r.returncode == 1 and os.path.exists(out), "BROKEN: verify fails on a CONFIG= image")
        check(oct(os.stat(out).st_mode & 0o777) == "0o600", "the half-built image is still 0600 (%s)" % oct(os.stat(out).st_mode & 0o777))
    prep = open(os.path.join(SCRIPTS, "prep-image.sh")).read()
    cp = re.search(r'(?m)^cp [^\n]*"\$BASE" "\$OUT"$', prep)
    check(cp is not None and not re.search(r"--preserve|\bcp -[a-z]*[ap]", cp.group(0))
          and not re.search(r'rm -f "\$OUT"', prep), "prep writes into the existing image file (keeps its 0600 mode)")


def test_run_sh_too_big_stops_before_docker():
    with tempfile.TemporaryDirectory() as t:
        r, _ = run_sh(t, {}, rootgib="20")
        check(r.returncode == 1 and "would not fit" in r.stderr, "BROKEN: oversize image refused")
        check(log(t) == "", "refused before any docker call")


HOST_TOOLS = ("dirname", "basename", "python3", "mkdir", "cp", "rm", "grep", "tee", "wc", "id", "df", "awk", "cat",
              "sha256sum", "shasum", "printf", "env", "head", "sed", "tr", "uname", "touch", "chmod",
              "cut", "git")   # #244: ansible/bin/ans (the image tag) and the theme's version stamp


def linux_like_path(t):
    """A PATH with the stub docker and the plain tools run.sh needs, but no macOS `security`: a Linux host."""
    farm = os.path.join(t, "farm")
    os.makedirs(farm, exist_ok=True)
    for tool in HOST_TOOLS:
        src = subprocess.run(["/bin/sh", "-c", "command -v %s" % tool], capture_output=True, text=True).stdout.strip()
        if src and src.startswith("/"):
            os.symlink(src, os.path.join(farm, tool))
    return os.path.join(t, "stubbin") + ":" + farm


def test_run_sh_no_password_no_keychain():
    with tempfile.TemporaryDirectory() as t:
        stubenv(t)
        r, out = run_sh(t, {"CABINET_PW": "", "COIN_DOJO_PUBKEY": "", "PATH": linux_like_path(t)})
        check(r.returncode == 0 and "no first-boot config" in r.stdout and os.path.exists(out + ".sha256"),
              "Linux, no password, no key, no Keychain -> builds an image without credentials (%r)" % r.stderr[-160:])
        check("WARNING" not in r.stdout and os.stat(out).st_mode & 0o077 != 0, "an image without secrets: no warning, normal mode")


def test_run_sh_config():
    with tempfile.TemporaryDirectory() as t:
        bad = os.path.join(t, "bad.yml")
        open(bad, "w").write("password: hunter2EXAMPLE\n")
        r, _ = run_sh(t, {"CONFIG": bad})
        check(r.returncode == 1 and "is malformed" in r.stderr and log(t) == "", "BROKEN: malformed CONFIG= -> refused before docker")
        check("hunter2EXAMPLE" not in r.stdout + r.stderr, "the refusal prints no secret")
    with tempfile.TemporaryDirectory() as t:   # #244 fix round M2: the user is fixed at build time
        other = os.path.join(t, "other.yml")
        open(other, "w").write("user: dojo\nhostname: dojo\n")
        r, _ = run_sh(t, {"CONFIG": other})
        check(r.returncode == 1 and "names another user" in r.stderr and "prep-image.sh" not in log(t),
              "BROKEN: CONFIG= with user: dojo on a coindojo image -> refused before the build (%s)" % r.stderr[-160:])
    with tempfile.TemporaryDirectory() as t:
        good = os.path.join(t, "good.yml")
        open(good, "w").write("hostname: dojo\nwifi_ssid: n\nwifi_psk: EXAMPLE-psk-not-real\n")
        stubenv(t)
        # the stub records whether the config copy is in WORK (mode 600) when the build step runs
        stub = open(os.path.join(t, "stubbin", "docker")).read().replace(
            "      *verify.sh*)", '      *build.sh*) ls -l "$STUB_WORK/.coindojo.yml" >> "$STUB_LOG"; exit 0 ;;\n      *verify.sh*)')
        open(os.path.join(t, "stubbin", "docker"), "w").write(stub)
        r, out = run_sh(t, {"CONFIG": good})
        calls = log(t)
        check(r.returncode == 0 and re.search(r"-rw-------[@+.]? .*\.coindojo\.yml", calls) is not None,
              "CONFIG= is checked, then copied beside the image (mode 600) for the build step")
        check(not os.path.exists(os.path.join(os.path.dirname(out), ".coindojo.yml")), "the config copy is removed after the build")
        modes = [oct(os.stat(f).st_mode & 0o777) for f in (out, out + ".sha256")]
        check(modes == ["0o600", "0o600"], "a CONFIG= image and its .sha256 are owner-only (%s)" % modes)
        check("never share it" in r.stdout and "Wi-Fi password" in r.stdout, "a CONFIG= build warns that the image holds secrets")
        check("security" not in calls, "CONFIG= never asks the Keychain")


def test_run_sh_fightcade_first_launch():
    """#267: with hk_fightcade_install first-launch the image needs no build/fightcade (none of its binaries go in) and
    keeps its data partition size; deploy still needs the downloads and bumps the data partition to 3072 MiB."""
    with tempfile.TemporaryDirectory() as t:
        fake = os.path.join(t, "fakebuild", "box64", "out")
        os.makedirs(fake)
        open(os.path.join(fake, "box64"), "w").close()
        r, _ = run_sh(t, {"HK_IMAGE_FIGHTCADE": "true", "HK_FIGHTCADE_INSTALL": "first-launch"})
        check(r.returncode == 0, "first-launch: run.sh builds without build/fightcade/out (rc %d: %s)" % (r.returncode, r.stderr[-300:]))
        check("install: first-launch" in r.stdout and "3072 MiB" not in r.stdout,
              "first-launch: no data partition bump (the downloads land after the first boot's grow)")
        check("IMAGE_FIGHTCADE_INSTALL" in log(t) or "first-launch" in r.stdout, "verify learns the install mode")
    with tempfile.TemporaryDirectory() as t:
        fake = os.path.join(t, "fakebuild", "box64", "out")
        os.makedirs(fake)
        open(os.path.join(fake, "box64"), "w").close()
        r, _ = run_sh(t, {"HK_IMAGE_FIGHTCADE": "true", "HK_FIGHTCADE_INSTALL": "deploy", "EDITION": "personal"})
        check(r.returncode != 0 and "build/fightcade/out/Fightcade-linux-v2.1.45.tar.gz is missing" in r.stderr + r.stdout,
              "BROKEN: deploy mode (personal) without build/fightcade/out stops before docker")
        check("3072 MiB" in r.stdout, "deploy mode bumps the data partition to 3072 MiB")
    with tempfile.TemporaryDirectory() as t:   # Coordinator 2026-11-19: a community image never bundles Fightcade
        fake = os.path.join(t, "fakebuild")
        for f in ("box64/out/box64", "fightcade/out/Fightcade-linux-v2.1.45.tar.gz"):
            os.makedirs(os.path.dirname(os.path.join(fake, f)), exist_ok=True)
            open(os.path.join(fake, f), "w").close()
        r, _ = run_sh(t, {"HK_IMAGE_FIGHTCADE": "true", "HK_FIGHTCADE_INSTALL": "deploy"})
        out = r.stderr + r.stdout
        check("installs on the first FIGHTCADE pick (first-launch)" in out and "install: first-launch" in out
              and "3072 MiB" not in out,
              "#294: EDITION=community + deploy switches to first-launch (no bundle, no data bump) (rc %d)" % r.returncode)
        check("hk_image_fightcade=true hk_fightcade_install=first-launch]" in log(t),
              "#301: the container's roles get first-launch via HK_SET (log: %s)" % log(t)[-200:])
        r, _ = run_sh(t, {"HK_IMAGE_FIGHTCADE": "true", "HK_FIGHTCADE_INSTALL": "deploy", "EDITION": "personal"})
        check(r.returncode == 0, "EDITION=personal may still bundle Fightcade (rc %d: %s)" % (r.returncode, r.stderr[-300:]))


def test_run_sh_keychain_only_when_no_env_password():
    with tempfile.TemporaryDirectory() as t:
        stubenv(t)
        sec = os.path.join(t, "stubbin", "security")
        open(sec, "w").write('#!/bin/bash\necho "security $*" >> "$STUB_LOG"\necho EXAMPLE-keychain-not-real\n')
        os.chmod(sec, 0o755)
        r, _ = run_sh(t, {"CABINET_PW": ""})
        check(r.returncode == 0 and "security find-generic-password" in log(t), "macOS: no CABINET_PW -> the Keychain item from hk_become_keychain")
    with tempfile.TemporaryDirectory() as t:
        stubenv(t)
        sec = os.path.join(t, "stubbin", "security")
        open(sec, "w").write('#!/bin/bash\necho "security $*" >> "$STUB_LOG"\nexit 44\n')
        os.chmod(sec, 0o755)
        r, _ = run_sh(t, {})
        check("security" not in log(t), "CABINET_PW set -> the Keychain is never asked")


if __name__ == "__main__":
    main(globals())
