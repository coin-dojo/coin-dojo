#!/usr/bin/env python3
"""Structure + secrets + correctness lint for the build-image skill.

Pure stdlib (no pip). pytest discovers test_* functions, or run directly:
    python3 skills/build-image/tests/test_build_image.py

Checks:
  1. Required scripts + SKILL.md exist and shell scripts are valid bash (`bash -n`).
  2. No secrets committed: no private key, no cabinet-password literal; secrets come
     from env (COINDOJO_PASSWORD / COINDOJO_PUBKEY) or the Keychain, never a literal.
  3. The cabinet controller profile is exact: device "USB gamepad", vendor 2079,
     product 58369 (the stock Pico stick) — so the global pad map is correct.
  4. Hardware parity is wired: /data repointing, HDMI combine-sink audio, both-HDMI 1080p.
  5. The secret scanner actually catches a planted secret (deliberately-broken control).

The deliberately-BROKEN case: put a real password or `-----BEGIN ... PRIVATE KEY-----`
literal into any script here, or change the controller vendor/product id, and this test
MUST go red.
"""
import json
import os
import re
import subprocess
import sys

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(SKILL, "scripts")
SHELL = ["run.sh", "prep-image.sh", "build.sh", "chroot-setup.sh", "provision.sh",
         "firstboot-expand.sh", "wifi-import.sh", "verify.sh", "lib-host.sh", "base-image.sh"]

SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key material"),
    # a CABINET_PW / COINDOJO_PASSWORD assigned a literal value (not read from env/keychain)
    (re.compile(r"""(?:CABINET_PW|COINDOJO_PASSWORD)\s*=\s*["']?[A-Za-z0-9]{12,}["']?"""), "hardcoded cabinet password"),
]


def _read(name):
    with open(os.path.join(SCRIPTS, name), encoding="utf-8") as f:
        return f.read()


def test_files_exist():
    assert os.path.isfile(os.path.join(SKILL, "SKILL.md")), "SKILL.md missing"
    for s in SHELL:
        assert os.path.isfile(os.path.join(SCRIPTS, s)), f"missing script {s}"


def test_shell_syntax_valid():
    for s in SHELL:
        r = subprocess.run(["bash", "-n", os.path.join(SCRIPTS, s)],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"{s} failed bash -n: {r.stderr}"


def test_no_committed_secrets():
    for s in SHELL:
        body = _read(s)
        for rx, why in SECRET_PATTERNS:
            m = rx.search(body)
            assert m is None, f"{s}: committed secret ({why}): {m.group(0)[:40]!r}"


def test_controller_profile_exact():
    body = _read("chroot-setup.sh")
    assert 'input_device = "USB gamepad"' in body, "controller device name wrong"
    assert 'input_vendor_id = "2079"' in body, "controller vendor id must be 2079 (0x081f)"
    assert 'input_product_id = "58369"' in body, "controller product id must be 58369 (0xe401)"
    # SDL mapping for ES-DE nav uses the cabinet's GUID
    assert "0300cb231f08000001e4000010010000" in body, "SDL controller GUID missing"
    # Button layout captured from the panel + verified in a CPS3 fighter (JAB=4..ROUNDHOUSE=1)
    for line in ('input_y_btn = "4"', 'input_x_btn = "0"', 'input_l_btn = "5"',
                 'input_b_btn = "3"', 'input_a_btn = "2"', 'input_r_btn = "1"'):
        assert line in body, f"button map regressed: expected {line}"


def test_hardware_parity_wired():
    body = _read("chroot-setup.sh")
    assert "/data/roms" in body and "/data/bios" in body, "ROMs/BIOS not repointed to /data"
    assert "module-combine-sink" in body, "HDMI combine-sink audio missing"
    assert 'audio_driver = "pulse"' in body, "RetroArch not set to pulse"
    assert "video=HDMI-A-2:1920x1080" in body, "second HDMI not forced to 1080p"
    assert "coin-dojo-data" in body, "/data fstab label missing"


def test_emulator_tuning_pi5_2gb():
    body = _read("chroot-setup.sh")
    assert "crt-geom.glslp" in body, "light CRT shader (crt-geom glsl) not set"
    assert 'rewind_enable = "false"' in body, "rewind must be off for 2GB"
    assert "retroarch-core-options.cfg" in body, "per-core options file missing"
    assert 'flycast_internal_resolution = "640x480"' in body, "flycast must stay native res on 2GB"
    # #244: ONE zram mechanism, Pi OS's rpi-swap, pinned by role cabinet_tweaks (it ran next to zram-tools before)
    assert "zram-tools" not in body and "/etc/default/zramswap" not in body, "chroot-setup must not add zram-tools"


def glsl_problems(body, role_defaults):
    """#277: the shader tree chroot-setup.sh ships is the role's pinned commit, sha256-checked ([] = OK)."""
    out = []
    if re.search(r"refs/heads/|/tar\.gz/(master|main)\b", body):
        out.append("a branch tarball (refs/heads/...) instead of the pinned commit")
    role = re.search(r"(?m)^retroarch122_shader_commit: ([0-9a-f]{40})$", role_defaults)
    pin = re.search(r'(?m)^GLSL_COMMIT="([0-9a-f]{40})"$', body)
    if not pin:
        out.append("no GLSL_COMMIT pin (full sha)")
    elif not role or pin.group(1) != role.group(1):
        out.append("GLSL_COMMIT %s is not retroarch122_shader_commit" % pin.group(1))
    if not re.search(r'(?m)^GLSL_SHA256="[0-9a-f]{64}"$', body) or "sha256sum -c" not in body:
        out.append("the tarball is not sha256-checked")
    if 'GLSL_URL="https://codeload.github.com/libretro/glsl-shaders/tar.gz/$GLSL_COMMIT"' not in body:
        out.append("the download URL is not the pinned commit's tarball")
    return out


def _glsl_block(body):
    start = body.index('GLSL_COMMIT="')
    end = body.index('rm -f "$GLSL_TGZ"', start) + len('rm -f "$GLSL_TGZ"')
    return body[start:end]


def _run_glsl(t, tarball_bytes, pin_sha=None):
    """Run chroot-setup.sh's real shader block with CACHE/RA in t, curl absent (offline), the cache holding
    tarball_bytes. pin_sha replaces GLSL_SHA256 (to accept a planted tarball). Returns (rc, out, installed?)."""
    import shutil
    body = _read("chroot-setup.sh")
    block = _glsl_block(body).replace("GLSL_TGZ=/tmp/glsl-shaders.tar.gz", "GLSL_TGZ=%s/dl.tar.gz" % t)
    if pin_sha:
        block = re.sub(r'GLSL_SHA256="[0-9a-f]{64}"', 'GLSL_SHA256="%s"' % pin_sha, block)
    commit = re.search(r'GLSL_COMMIT="([0-9a-f]{40})"', block).group(1)
    os.makedirs(os.path.join(t, "cache"))
    with open(os.path.join(t, "cache", "glsl-shaders-%s.tar.gz" % commit), "wb") as f:
        f.write(tarball_bytes)
    bindir = os.path.join(t, "bin")
    os.makedirs(bindir)
    for tool in ("bash", "cp", "rm", "mkdir", "tar", "gzip", "sha256sum", "shasum", "perl"):
        src = shutil.which(tool)
        if src:
            os.symlink(src, os.path.join(bindir, tool))
    script = 'set -euo pipefail\nLOG(){ echo ">> $*"; }\nCACHE=%s/cache\nRA=%s/ra\n%s\n' % (t, t, block)
    r = subprocess.run([os.path.join(bindir, "bash"), "-c", script], capture_output=True, text=True,
                       env={"PATH": bindir})
    return r.returncode, r.stdout + r.stderr, os.path.isdir(os.path.join(t, "ra", "shaders", "shaders_glsl"))


def _fake_tarball():
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"// obviously fake shader\n"
        info = tarfile.TarInfo("glsl-shaders-x/crt/fake.glsl")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_glsl_shaders_pinned():
    import hashlib
    import tempfile
    body = _read("chroot-setup.sh")
    role = open(os.path.join(SKILL, "..", "..", "ansible", "roles", "retroarch122", "defaults", "main.yml"),
                encoding="utf-8").read()
    assert glsl_problems(body, role) == [], glsl_problems(body, role)
    # BROKEN: the old branch URL, another commit, no sha256 check: each is caught
    master = body.replace('/tar.gz/$GLSL_COMMIT"', '/tar.gz/refs/heads/master"')
    assert any("refs/heads" in p for p in glsl_problems(master, role)), "BROKEN: a branch tarball must be caught"
    other = body.replace('GLSL_COMMIT="a3506', 'GLSL_COMMIT="b3506')
    assert any("not retroarch122_shader_commit" in p for p in glsl_problems(other, role)), "BROKEN: another commit"
    assert any("sha256" in p for p in glsl_problems(body.replace("sha256sum -c", "true"), role)), "BROKEN: no sha check"
    # the real block: a tarball that is not the pinned one installs nothing; the pinned one is unpacked
    fake = _fake_tarball()
    with tempfile.TemporaryDirectory() as t:
        rc, out, installed = _run_glsl(t, fake)
        assert rc == 0 and not installed and "not the pinned" in out, "BROKEN: a wrong tarball is refused (%s)" % out
        assert not os.path.exists(os.path.join(t, "dl.tar.gz")), "the download is removed"
    with tempfile.TemporaryDirectory() as t:
        rc, out, installed = _run_glsl(t, fake, pin_sha=hashlib.sha256(fake).hexdigest())
        assert rc == 0 and installed, "a tarball matching the pin is unpacked (%s)" % out
        assert os.path.isfile(os.path.join(t, "ra", "shaders", "shaders_glsl", "crt", "fake.glsl")), "strip-components=1"


def test_display_fits_bezel():
    """Game + menu must be framed to the cabinet's centered 4:3 bezel (verified on hw)."""
    body = _read("chroot-setup.sh")
    # RetroArch custom viewport = centered 1440x1080 at x=240
    assert 'custom_viewport_width = "1440"' in body, "game viewport width wrong"
    assert 'custom_viewport_x = "240"' in body, "game viewport not centered at x=240"
    assert 'aspect_ratio_index = "22"' in body, "RetroArch not set to custom viewport"
    # RetroArch MENU must also fit the bezel: RGUI with aspect_ratio_lock=0 inherits the
    # game's custom viewport (240..1680). Ozone/XMB/other locks overflow the bezel.
    assert 'menu_driver = "rgui"' in body, "RA menu must be rgui to fit the bezel"
    assert 'rgui_aspect_ratio_lock = "0"' in body, \
        "rgui lock must be 0 (None) so the menu inherits the game viewport"
    # ES-DE letterboxes ITSELF into the centered 1440x1080 bezel box (cage can't):
    # --fullscreen-padding renders the whole UI padded (not scaled) inside the bezel.
    assert "--resolution 1440 1080 --fullscreen-padding 1" in body, \
        "ES-DE not launched with 1440x1080 fullscreen-padding (menu won't fit bezel)"
    # ES-DE menu uses its 4:3 theme layout so it stays inside the bezel
    assert '"ThemeAspectRatio" value="4:3"' in body, "ES-DE not set to 4:3 theme layout"


def test_wifi_ethernet_first_no_baked_creds():
    """Community build: WiFi is user-provided via boot-partition wifi.conf; the
    image itself ships NO network credentials (Ethernet needs no setup at all)."""
    body = _read("chroot-setup.sh")
    assert "coin-dojo-wifi.service" in body, "WiFi import service not registered"
    assert "/boot/firmware/wifi.conf" in body, "wifi.conf template not shipped on boot partition"
    # The shipped template MUST be empty -- a baked SSID/PSK here breaks this (the control).
    assert "SSID=\nPSK=" in body, "wifi.conf template must ship with empty SSID/PSK (no baked network)"
    wifi = _read("wifi-import.sh")
    assert "nmcli connection add type wifi" in wifi, "wifi-import must create a NetworkManager profile"
    assert 'ssid "$SSID"' in wifi and 'wifi-sec.psk "$PSK"' in wifi, \
        "wifi-import must take SSID/PSK from the file, never a literal"
    # Must scrub the plaintext creds off the FAT partition after importing.
    assert "SSID=\nPSK=" in wifi, "wifi-import must blank wifi.conf after importing"


def test_boot_fixes_present():
    """The three live-diagnosed boot bugs must stay fixed in the build."""
    body = _read("chroot-setup.sh")
    # 1. getty@tty1 must be enabled, else autologin drop-in is inert -> no frontend
    assert "systemctl enable getty@tty1" in body, "getty@tty1 not enabled (frontend won't start)"
    # 2. ES-DE desktop-GL needs the Mesa 3.3 override on Pi 5 v3d (EGL_BAD_MATCH otherwise)
    assert "MESA_GL_VERSION_OVERRIDE=3.3COMPAT" in body, "ES-DE GL override missing"
    assert "MESA_GLSL_VERSION_OVERRIDE=330" in body, "ES-DE GLSL override missing"
    # 3. WiFi regulatory domain, else the brcmfmac radio stays rfkill-blocked
    assert "cfg80211.ieee80211_regdom=US" in body, "wifi regdom not set on cmdline"
    assert "WirelessEnabled=true" in body, "NetworkManager wifi not enabled by default"


def test_prep_preserves_diskid():
    # must reuse the existing MBR disk-id so root=PARTUUID= keeps working
    body = _read("prep-image.sh")
    assert "sfdisk --disk-id" in body, "prep must read the existing disk-id"
    assert "label-id: $DISKID" in body, "prep must write back the preserved disk-id"


def ssh_keyonly_problems(body, verify):
    """#41: the builder must default to key-only SSH; password SSH only as an explicit opt-in."""
    p = []
    # any literal `PasswordAuthentication yes` / `KbdInteractiveAuthentication yes` (outside comments)
    for key in ("PasswordAuthentication", "KbdInteractiveAuthentication"):
        if re.search(r"(?m)^[^#\n]*%s\s+yes" % key, body):
            p.append("literal %s yes" % key)
    if "/etc/ssh/sshd_config.d/00-coin-dojo-keyonly.conf" not in body:
        p.append("key-only drop-in 00-coin-dojo-keyonly.conf not written")
    if "PasswordAuthentication $SSH_PW_AUTH" not in body:
        p.append("PasswordAuthentication not driven by SSH_PW_AUTH")
    if '"${COIN_DOJO_SSH_PASSWORD:-no}" = "yes"' not in body or "SSH_PW_AUTH=no" not in body:
        p.append("default is not key-only (COIN_DOJO_SSH_PASSWORD must default to no)")
    if "KbdInteractiveAuthentication no" not in body:
        p.append("KbdInteractiveAuthentication no missing")
    if "rm -f /etc/ssh/sshd_config.d/99-arcade.conf" not in body:
        p.append("legacy 99-arcade.conf not removed")
    if "PermitRootLogin no" not in body:
        p.append("PermitRootLogin no not stated (#240)")
    if "sshd key-only (PasswordAuthentication no)" not in verify:
        p.append("verify.sh does not gate on key-only")
    return p


def test_ssh_key_only_default():
    probs = ssh_keyonly_problems(_read("chroot-setup.sh"), _read("verify.sh"))
    assert not probs, f"builder SSH not key-only by default: {probs}"
    # the opt-in reaches the chroot and the verify gate
    assert 'COIN_DOJO_SSH_PASSWORD="${COIN_DOJO_SSH_PASSWORD:-no}"' in _read("build.sh"), \
        "build.sh does not pass COIN_DOJO_SSH_PASSWORD into the chroot"
    run = _read("run.sh")
    assert run.count("-e COIN_DOJO_SSH_PASSWORD") == 2, "run.sh must pass the opt-in to build + verify"
    assert "wifi-import.sh" in run, "run.sh must stage wifi-import.sh (build.sh copies it)"


def test_broken_ssh_password_yes_fails():
    """Deliberately-broken control: the pre-#41 line MUST be flagged."""
    body = _read("chroot-setup.sh")
    verify = _read("verify.sh")
    old = ("mkdir -p /etc/ssh/sshd_config.d; printf 'PasswordAuthentication yes\\n' "
           "> /etc/ssh/sshd_config.d/99-arcade.conf\n")
    assert ssh_keyonly_problems(body + old, verify), "PasswordAuthentication yes not flagged"
    assert ssh_keyonly_problems(body.replace("SSH_PW_AUTH=no", "SSH_PW_AUTH=yes"), verify), \
        "default flipped to yes not flagged"
    assert ssh_keyonly_problems(body.replace(":-no}\" = \"yes\"", ":-yes}\" = \"yes\""), verify), \
        "opt-in defaulting to yes not flagged"
    assert ssh_keyonly_problems(body, verify.replace("sshd key-only", "sshd")), \
        "verify without key-only gate not flagged"


def _run_ssh_section(tmp, env_extra):
    """Run chroot-setup.sh's `# ---- ssh ----` section with /etc and $HOME_DIR redirected to tmp."""
    body = _read("chroot-setup.sh")
    m = re.search(r"\n# ---- ssh ----\n(.*?)\n# ---- data partition", body, re.S)
    assert m, "ssh section not found in chroot-setup.sh"
    snippet = m.group(1).replace("/etc/", tmp + "/etc/")
    os.makedirs(os.path.join(tmp, "etc", "ssh", "sshd_config.d"))
    open(os.path.join(tmp, "etc", "ssh", "sshd_config.d", "99-arcade.conf"), "w").write(
        "PasswordAuthentication yes\n")
    open(os.path.join(tmp, "etc", "hosts"), "w").close()
    script = ("set -euo pipefail\nLOG(){ echo \">> $*\"; }\nsystemctl(){ :; }\n"
              "HOME_DIR=%s/home\nmkdir -p \"$HOME_DIR/.ssh\"\necho 'ssh-ed25519 AAAAstale' > \"$HOME_DIR/.ssh/authorized_keys\"\n%s\n" % (tmp, snippet))
    env = dict(os.environ)
    env.pop("COIN_DOJO_SSH_PASSWORD", None)   # the default case must not inherit the caller's opt-in
    env.update(env_extra)
    r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"ssh section failed: {r.stderr}"
    d = os.path.join(tmp, "etc", "ssh", "sshd_config.d")
    return {f: open(os.path.join(d, f)).read() for f in os.listdir(d)}


def test_ssh_section_behaviour():
    """Execute the real ssh section: default -> key-only + legacy gone; opt-in -> password yes."""
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        files = _run_ssh_section(t, {})
        assert "99-arcade.conf" not in files, "legacy 99-arcade.conf left behind"
        conf = files.get("00-coin-dojo-keyonly.conf", "")
        assert "PasswordAuthentication no\n" in conf, f"default build not key-only: {conf!r}"
        assert "KbdInteractiveAuthentication no\n" in conf
        assert "PermitRootLogin no\n" in conf, "#240: PermitRootLogin no must be explicit"
        assert not os.path.exists(os.path.join(t, "home", ".ssh", "authorized_keys")), "#237: the image must ship no SSH key"
    with tempfile.TemporaryDirectory() as t:
        conf = _run_ssh_section(t, {"COIN_DOJO_SSH_PASSWORD": "yes"})["00-coin-dojo-keyonly.conf"]
        assert "PasswordAuthentication yes\n" in conf, "opt-in did not enable password SSH"
        assert "KbdInteractiveAuthentication no\n" in conf


def test_bad_pubkey_refused():
    """Key-only SSH: the first-boot config refuses a garbled key (moved from chroot-setup to coindojo-firstboot, #237)."""
    fb = os.path.join(SCRIPTS, "coindojo-firstboot.py")
    import tempfile
    for key, ok in (("ssh-ed25519 AAAAC3fake c", True), ("not-a-key", False),
                    ("-----BEGIN OPENSSH PRIVATE KEY-----", False)):
        with tempfile.TemporaryDirectory() as t:
            f = os.path.join(t, "n.yml")
            open(f, "w").write('ssh_pubkey: "%s"\n' % key)
            r = subprocess.run([sys.executable, fb, "--check", f], capture_output=True, text=True)
        assert (r.returncode == 0) == ok, f"pubkey check wrong for {key[:20]!r}: rc={r.returncode} {r.stderr}"


OLD_KEY = "OLD_OWNER_PUBKEY"     # a retired per-owner key alias (#240)


def test_no_baked_credentials():
    """#237: the chroot sets no password and writes no key; every account is locked; no shared host keys."""
    body = _read("chroot-setup.sh")
    code = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
    for word in ("CABINET_PW", OLD_KEY, "chpasswd", "authorized_keys\""):
        assert word not in code.replace('rm -f "$HOME_DIR/.ssh/authorized_keys"', ""), f"chroot-setup still bakes a credential ({word})"
    assert 'passwd -l "$USER_NAME"' in body and "passwd -l root" in body, "user and root must be locked"
    assert "rm -f /etc/ssh/ssh_host_*" in body, "SSH host keys must not ship in the image"
    assert "coindojo-firstboot.service" in body and "systemctl enable coindojo-firstboot.service" in body
    assert "/run/coindojo/no-config" in body, "no on-screen hint when there is no config"
    build = _read("build.sh")
    assert "openssl passwd -6 -stdin" in build, "the personal-flow password must be hashed from stdin"
    assert "CABINET_PW" not in build and OLD_KEY not in build, "build.sh uses the neutral COINDOJO_* names (#240)"
    run = _read("run.sh")
    assert "-e " + OLD_KEY not in run and "-e CABINET_PW" not in run, "no old secret names into the containers"
    assert "${CABINET_PW:-}" in run and "${COIN_DOJO_PUBKEY:-}" in run, "old names stay as Keychain-flow aliases"
    assert OLD_KEY not in run, "#240: the alias named after the maintainer is gone"


VERIFY_ENV = ("EDITION", "CABINET_PW", "CU", "VERIFY_FIRSTBOOT", "VERIFY_DATA", "EXPECT_PATHS", "IMAGE_FIGHTCADE",
              "VERIFY_ROOT_UID", "VERIFY_ROOT_GID")


FSTAB = "proc /proc proc defaults 0 0\nPARTUUID=x-01  /boot/firmware  vfat  defaults  0 2\n"


def _run_verify_secrets(tmp, setup, env_extra=None):
    """Run verify.sh's secrets block on a fixture root/boot; setup(root, boot) plants files."""
    m = re.search(r"\n# >>> secrets.*?\n(.*?)\n# <<< secrets", _read("verify.sh"), re.S)
    assert m, "secrets block not found in verify.sh"
    root, boot = os.path.join(tmp, "root"), os.path.join(tmp, "boot")
    for d in ("etc/ssh/sshd_config.d", "etc/sudoers.d", "home/coindojo/.ssh", "root", "usr/local/sbin",
              "etc/systemd/system/multi-user.target.wants"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    os.makedirs(boot)
    open(os.path.join(root, "etc/shadow"), "w").write("root:!:1::::::\ncoindojo:!:1::::::\n")
    open(os.path.join(root, "etc/group"), "w").write("root:x:0:\nsudo:x:27:coindojo\ncoindojo:x:1000:\n")   # #270: sudo
    open(os.path.join(root, "etc/fstab"), "w").write(FSTAB)
    open(os.path.join(root, "etc/ssh/sshd_config.d/00-coin-dojo-keyonly.conf"), "w").write("PermitRootLogin no\n")
    open(os.path.join(root, "usr/local/sbin/coindojo-firstboot"), "w").write("#\n")
    for h in ("coin-dojo-set-password", "coin-dojo-set-hostname"):       # #270 M3: the sudo helpers, "root:root" 0755
        open(os.path.join(root, "usr/local/sbin", h), "w").write("#\n")
        os.chmod(os.path.join(root, "usr/local/sbin", h), 0o755)
    os.symlink("/x", os.path.join(root, "etc/systemd/system/multi-user.target.wants/coindojo-firstboot.service"))
    with open(os.path.join(SKILL, "coindojo.example.yml")) as f:
        open(os.path.join(boot, "coindojo.example.yml"), "w").write(f.read())
    setup(root, boot)
    pre = ('R=%s; B=%s; CU=coindojo\nchk(){ [ -e "$R/$1" ] && echo "PASS: $2" || echo "FAIL: $2 ($1)"; }\n'
           'grp(){ grep -q "$1" "$2" 2>/dev/null && echo "PASS: $3" || echo "FAIL: $3"; }\n' % (root, boot))
    # #265: verify asks the image's coindojo-firstboot whether the config sets a password; here the repo's copy
    # The caller's edition and build settings never leak in: `make test` exports EDITION (mk/flags.mk reads
    # hk_edition, personal on the maintainer's Mac), and a case without EDITION must mean run.sh's default.
    env = {k: v for k, v in os.environ.items()
           if k not in VERIFY_ENV and not k.startswith(("COIN_DOJO_", "COINDOJO_", "COIN_DOJO_", "HK_"))}
    env["VERIFY_FIRSTBOOT"] = os.path.join(SCRIPTS, "coindojo-firstboot.py")
    env["VERIFY_ROOT_UID"], env["VERIFY_ROOT_GID"] = str(os.getuid()), str(os.getgid())   # the fixture's "root"
    r = subprocess.run(["bash", "-c", pre + m.group(1)], capture_output=True, text=True, env=dict(env, **(env_extra or {})))
    return r.stdout


def _put(path, text):
    return lambda root, boot: open(os.path.join(root if not path.startswith("BOOT/") else boot,
                                                path.replace("BOOT/", "")), "w").write(text)


PERSONAL = {"EDITION": "personal"}   # #265: only a personal image (or a configured password) has every account locked


def test_verify_secret_gates():
    """#237/#240: verify FAILs on any baked secret; a clean image PASSes. Each planted case is a broken control."""
    import tempfile
    put = _put
    with tempfile.TemporaryDirectory() as t:
        out = _run_verify_secrets(t, lambda r, b: None, {"COINDOJO_PASSWORD": "EXAMPLE-pw-not-real", **PERSONAL})
        assert "FAIL" not in out and "PASS: no first-boot secret" in out, out
    fake_hash = "$6$EXAMPLEsalt$" + "x" * 20
    cases = [
        ("password hash in shadow", put("etc/shadow", "coindojo:%s:1::::::\n" % fake_hash), "FAIL: a usable or empty password"),
        ("empty password field", put("etc/shadow", "root:!:1::::::\ncoindojo::1::::::\n"), "FAIL: a usable or empty password"),
        ("NM profile with a psk", lambda r, b: (os.makedirs(os.path.join(r, "etc/NetworkManager/system-connections")),
                                                 open(os.path.join(r, "etc/NetworkManager/system-connections/x.nmconnection"), "w").write("[wifi-security]\npsk=EXAMPLEpsk1\n")),
         "FAIL: a Wi-Fi PSK is baked"),
        ("wpa_supplicant.conf on boot", put("BOOT/wpa_supplicant.conf", "network={}\n"), "FAIL: wpa_supplicant.conf on the boot partition"),
        ("userconf.txt on boot", put("BOOT/userconf.txt", "pi:x\n"), "FAIL: userconf.txt on the boot partition"),
        ("authorized_keys", put("home/coindojo/.ssh/authorized_keys", "ssh-ed25519 AAAAx k\n"), "FAIL: SSH key baked in"),
        ("host key", put("etc/ssh/ssh_host_ed25519_key", "x\n"), "FAIL: SSH host keys baked in"),
        ("private key", put("root/id", "-----BEGIN OPENSSH " + "PRIVATE KEY-----\n"), "FAIL: private key material"),
        ("no PermitRootLogin", put("etc/ssh/sshd_config.d/00-coin-dojo-keyonly.conf", "PasswordAuthentication no\n"), "FAIL: sshd PermitRootLogin no"),
        ("pi password", put("etc/shadow", "pi:%s:1::::::\n" % fake_hash), "FAIL: pi has a usable password"),
        ("plaintext password in rootfs", put("etc/leak.conf", "pw=EXAMPLE-pw-not-real\n"), "FAIL: a first-boot secret sits"),
        ("example filled in", put("BOOT/coindojo.example.yml", "wifi_psk: EXAMPLEpsk123\n"), "FAIL: coindojo.example.yml missing or filled in"),
    ]
    for name, setup, want in cases:
        with tempfile.TemporaryDirectory() as t:
            out = _run_verify_secrets(t, setup, {"COINDOJO_PASSWORD": "EXAMPLE-pw-not-real", **PERSONAL})
            assert want in out, f"BROKEN case not caught: {name}\n{out}"
    # the boot config's psk copied into the root filesystem is caught (scan reads the values from coindojo.yml)
    def leak(root, boot):
        open(os.path.join(boot, "coindojo.yml"), "w").write('wifi_ssid: n\nwifi_psk: "EXAMPLEpsk-not-real"\n')
        open(os.path.join(root, "etc", "copy"), "w").write("EXAMPLEpsk-not-real\n")
    with tempfile.TemporaryDirectory() as t:
        assert "FAIL: a first-boot secret sits" in _run_verify_secrets(t, leak, PERSONAL), "BROKEN: psk copied into the rootfs not caught"
    # a personal image (CONFIG=) reports its boot secrets as information, not as a failure
    def personal(root, boot):
        open(os.path.join(boot, "coindojo.yml"), "w").write('wifi_ssid: n\nwifi_psk: "EXAMPLEpsk-not-real"\n')
    with tempfile.TemporaryDirectory() as t:
        out = _run_verify_secrets(t, personal, PERSONAL)
        assert "INFO: CONFIG secrets present on boot" in out and "FAIL" not in out, out
    with tempfile.TemporaryDirectory() as t:
        assert "INFO: CONFIG" not in _run_verify_secrets(t, lambda r, b: None, PERSONAL), "no INFO without a config"


OWNER_SSH = "etc/ssh/sshd_config.d/10-coindojo-owner-ssh.conf"    # #299: coindojo-firstboot's OWNER_SSH
DEFAULT_HASH = "$6$EXAMPLEsalt$" + "A" * 86     # the shape of a default-login hash (not a real one)
KEYONLY = "PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\n"
# The settings role's sudoers file (#34 hostname helper, #271 password helper), as ansible/roles/settings renders it
HOSTNAME_RULE = "coindojo ALL=(root) NOPASSWD: /usr/local/sbin/coin-dojo-set-hostname ^[a-z0-9]([a-z0-9-]{0\\,61}[a-z0-9])?$"
PASSWORD_RULE = 'coindojo ALL=(root) NOPASSWD: /usr/local/sbin/coin-dojo-set-password ""'
SETTINGS_SUDOERS = "# coin-dojo SETTINGS app\n%s\n# PASSWORD page (#271)\n%s\n" % (HOSTNAME_RULE, PASSWORD_RULE)


def _community_default(root, boot, h=DEFAULT_HASH, mark=DEFAULT_HASH):
    """A community image as build.sh leaves it (#265): coindojo has the default hash, recorded in the mark; #270: out
    of the sudo group, the admin grant pending, the password path unit enabled."""
    open(os.path.join(root, "etc/shadow"), "w").write("root:!:1::::::\ncoindojo:%s:1::::::\n" % h)
    os.makedirs(os.path.join(root, "etc/coindojo"), exist_ok=True)
    if mark is not None:
        open(os.path.join(root, "etc/coindojo/default-password"), "w").write(mark + "\n")
    open(os.path.join(root, "etc/ssh/sshd_config.d/00-coin-dojo-keyonly.conf"), "w").write(KEYONLY)
    open(os.path.join(root, "etc/group"), "w").write("root:x:0:\nsudo:x:27:\ncoindojo:x:1000:\n")
    open(os.path.join(root, "etc/coindojo/sudo-pending"), "w").write("coindojo\n")
    os.makedirs(os.path.join(root, "etc/sudoers.d"), exist_ok=True)
    open(os.path.join(root, "etc/sudoers.d/coin-dojo-power"), "w").write("coindojo ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff\n")
    open(os.path.join(root, "etc/sudoers.d/coin-dojo-settings"), "w").write(SETTINGS_SUDOERS)
    open(os.path.join(root, "etc/sudoers.d/coin-dojo-sudo-timeout"), "w").write("Defaults:coindojo timestamp_timeout=0\n")
    wants =os.path.join(root, "etc/systemd/system/multi-user.target.wants/coindojo-password.path")
    if not os.path.lexists(wants):
        os.symlink("/etc/systemd/system/coindojo-password.path", wants)


def test_verify_boot_partition_root_only():
    """#270 re-review: admin comes from the boot partition, so only root may write it. Broken mount options FAIL."""
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        out = _run_verify_secrets(t, lambda r, b: None, PERSONAL)
        assert "PASS: /boot/firmware is mounted root-only (defaults)" in out and "FAIL" not in out, out
    good = "PARTUUID=x-01 /boot/firmware vfat defaults,uid=0,gid=0,umask=0022,fmask=0133 0 2\n"
    with tempfile.TemporaryDirectory() as t:
        out = _run_verify_secrets(t, _put("etc/fstab", good), PERSONAL)
        assert "PASS: /boot/firmware is mounted root-only" in out, out
    for opts, want in (("defaults,uid=1000", "uid=1000"), ("gid=1000", "gid=1000"), ("umask=000", "umask=000"),
                       ("fmask=0002", "fmask=0002"), ("dmask=0020", "dmask=0020"), ("umask=x", "umask=x")):
        with tempfile.TemporaryDirectory() as t:
            out = _run_verify_secrets(t, _put("etc/fstab", "PARTUUID=x-01 /boot/firmware vfat %s 0 2\n" % opts), PERSONAL)
            assert "FAIL: /boot/firmware mount options let a user write it: " + want in out, f"BROKEN not caught: {opts}\n{out}"
    for name, text in (("no line", "proc /proc proc defaults 0 0\n"), ("commented out", "#PARTUUID=x /boot/firmware vfat defaults 0 2\n")):
        with tempfile.TemporaryDirectory() as t:
            out = _run_verify_secrets(t, _put("etc/fstab", text), PERSONAL)
            assert "FAIL: /boot/firmware is not in /etc/fstab" in out, f"BROKEN not caught: {name}\n{out}"


def test_verify_no_sudo_with_the_default():
    """#270: while the default password is active the user has no sudo; with a password of its own it has. Broken
    forms must FAIL."""
    import tempfile
    put, then = _put, (lambda *fs: (lambda r, b: [f(r, b) for f in fs]))
    with tempfile.TemporaryDirectory() as t:
        out = _run_verify_secrets(t, _community_default, {"EDITION": "community"})
        assert "PASS: coindojo has no sudo while the default password is active" in out and "FAIL" not in out, out
        assert "PASS: no owner password-SSH drop-in in the image" in out, out
    with tempfile.TemporaryDirectory() as t:     # #299: personal images never ship it either (empty file, a symlink)
        out = _run_verify_secrets(t, put(OWNER_SSH, ""), PERSONAL)
        assert "FAIL: the image already has the owner's password-SSH drop-in" in out, "BROKEN: personal, empty drop-in\n" + out
    with tempfile.TemporaryDirectory() as t:
        out = _run_verify_secrets(t, lambda r, b: os.symlink("/nowhere", os.path.join(r, OWNER_SSH)), PERSONAL)
        assert "FAIL: the image already has the owner's password-SSH drop-in" in out, "BROKEN: a dangling symlink\n" + out
    cases = [
        ("in the sudo group", put("etc/group", "root:x:0:\nsudo:x:27:pi,coindojo\n"), "FAIL: coindojo is in the sudo group"),
        ("admin rule already there", put("etc/sudoers.d/coindojo-admin", "coindojo ALL=(ALL:ALL) ALL\n"),
         "FAIL: a sudoers rule gives coindojo every command"),
        ("an ALL rule by another name", put("etc/sudoers.d/90-x", "coindojo ALL=(ALL) NOPASSWD: ALL\n"),
         "FAIL: a sudoers rule gives coindojo every command"),
        ("no admin grant pending", lambda r, b: os.remove(os.path.join(r, "etc/coindojo/sudo-pending")),
         "FAIL: no admin grant pending"),
        ("path unit not enabled", lambda r, b: os.remove(os.path.join(r, "etc/systemd/system/multi-user.target.wants/coindojo-password.path")),
         "FAIL: coindojo-password.path enabled"),
        # #299: the owner's password-SSH drop-in is made on the cabinet, never baked into the image
        ("owner SSH drop-in baked in", put(OWNER_SSH, "Match User coindojo\n    PasswordAuthentication yes\n"),
         "FAIL: the image already has the owner's password-SSH drop-in"),
        ("owner SSH drop-in baked in (seen by the 12345 check too)", put(OWNER_SSH, "Match User coindojo\n    PasswordAuthentication yes\n"),
         "FAIL: password SSH is possible on an image with the default password 12345"),
    ]
    for name, plant, want in cases:
        with tempfile.TemporaryDirectory() as t:
            out = _run_verify_secrets(t, then(_community_default, plant), {"EDITION": "community"})
            assert want in out, f"BROKEN case not caught: {name}\n{out}"
    stock = ('Defaults env_keep += "DPKG_DEB_THREADS_MAX"\n', 'Defaults env_keep += "http_proxy HTTP_PROXY"\n'
             'Defaults env_keep += "RSYNC_PROXY"\nDefaults env_keep += "no_proxy NO_PROXY"\n')
    with tempfile.TemporaryDirectory() as t:     # #293: Pi OS trixie's stock 010_dpkg-threads / 010_proxy are allowed
        out = _run_verify_secrets(t, then(_community_default, put("etc/sudoers.d/010_dpkg-threads", stock[0]),
                                          put("etc/sudoers.d/010_proxy", stock[1])), {"EDITION": "community"})
        assert "FAIL: a sudoers line outside the allowlist" not in out, out
    with tempfile.TemporaryDirectory() as t:     # BROKEN: any other env_keep (LD_PRELOAD) is not stock
        out = _run_verify_secrets(t, then(_community_default, put("etc/sudoers.d/010_proxy",
                                                                  'Defaults env_keep += "LD_PRELOAD"\n')), {"EDITION": "community"})
        assert "FAIL: a sudoers line outside the allowlist" in out, out
    with tempfile.TemporaryDirectory() as t:     # the power rule (one command) is not "every command"
        out = _run_verify_secrets(t, _community_default, {"EDITION": "community"})
        assert "every command" not in out, out
    # a password of its own (configured, or personal): sudo from the first boot
    with tempfile.TemporaryDirectory() as t:
        out = _run_verify_secrets(t, lambda r, b: None, PERSONAL)
        assert "PASS: coindojo is in the sudo group" in out, out
    with tempfile.TemporaryDirectory() as t:
        out = _run_verify_secrets(t, put("etc/group", "root:x:0:\nsudo:x:27:\n"), PERSONAL)
        assert "FAIL: coindojo is not in the sudo group" in out, "BROKEN: personal image without sudo not caught\n" + out
    with tempfile.TemporaryDirectory() as t:
        def pending(r, b):
            os.makedirs(os.path.join(r, "etc/coindojo"), exist_ok=True)
            open(os.path.join(r, "etc/coindojo/sudo-pending"), "w").write("coindojo\n")
        out = _run_verify_secrets(t, pending, PERSONAL)
        assert "FAIL: coindojo is not in the sudo group, or an admin grant is pending" in out, "BROKEN: pending on a personal image\n" + out


STOCK_SUDOERS = ("Defaults\tenv_reset\nDefaults\tmail_badpass\n"
                 'Defaults\tsecure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"\nDefaults\tuse_pty\n'
                 "# User privilege specification\nroot\tALL=(ALL:ALL) ALL\n\n%sudo\tALL=(ALL:ALL) ALL\n\n"
                 "# See sudoers(5) for more information on \"@include\" directives:\n\n@includedir /etc/sudoers.d\n")
# security review of 8500fb0 (probe y270): lines the old NARROW check let through; each must FAIL the allowlist
REVIEW_LINES = [
    "coindojo ALL=(root) NOPASSWD: /bin/bash",
    "coindojo ALL=(root) NOPASSWD: /usr/bin/",
    "coindojo ALL=(root) NOPASSWD: /usr/bin/*",
    "coindojo ALL=(root) NOPASSWD: /usr/bin/vi *",
    "coindojo ALL=(root) NOPASSWD: /var/tmp/x.sh",
    "coindojo ALL=(root) NOPASSWD: /data/launcher/x.py",
    "%%coindojo ALL=(ALL) NOPASSWD: ALL",
    "%coindojo ALL=(ALL) NOPASSWD: ALL",
    "%%video ALL=(ALL) NOPASSWD: ALL",
    "%video ALL=(ALL) NOPASSWD: ALL",
    "ALL ALL=(ALL) NOPASSWD: ALL",
    "#1000 ALL=(ALL) NOPASSWD: ALL",
    "User_Alias A = coindojo\nA ALL=(ALL) NOPASSWD: ALL",
    "coindojo,root ALL=(ALL) NOPASSWD: ALL",
    "Defaults:coindojo !env_reset, !secure_path",
    'coindojo ALL=(root) NOPASSWD: /usr/local/sbin/coin-dojo-set-passwor? ""',
    'coindojo ALL=(root) NOPASSWD: /usr/local/sbin/* ""',
    '"coindojo" ALL=(ALL) NOPASSWD: ALL',
    # the ones NARROW did catch stay caught
    "Cmnd_Alias C = /bin/sh\ncoindojo ALL=(root) NOPASSWD: C",
    "Cmnd_Alias C = /bin/sh\ncoindojo ALL=(root) NOPASSWD: /usr/bin/id \\\n , C",
    "coindojo ALL = (root) NOPASSWD: ALL",
    "coindojo ALL=(root) NOPASSWD:ALL",
    "coindojo ALL=NOPASSWD: ALL",
    "coindojo ALL=(root) NOPASSWD: SETENV: /usr/bin/id",
    "coindojo ALL=(root) NOPASSWD: sudoedit /etc/hosts",
    "coindojo ALL=(root) NOPASSWD: /usr/local/sbin/coin-dojo-set-password",
    "coindojo ALL=(root) /usr/bin/apt",
    # re-review N1: a comment ending in a backslash must not swallow the rule after it
    "# note \\\ncoindojo ALL=(ALL) NOPASSWD: ALL",
    "#\\\ncoindojo ALL=(ALL) NOPASSWD: ALL",
    "   # indented \\\ncoindojo ALL=(root) NOPASSWD: /bin/sh",
]


def test_verify_sudoers_allowlist():
    """#270 M3 (security review of 8500fb0): every line sudo reads must be stock Pi OS or one of our rules; only
    /etc/sudoers may include, and only /etc/sudoers.d; both helpers root:root and not group/other-writable. The
    #271 password helper rule must be there exactly (SETTINGS > PASSWORD works without admin)."""
    import tempfile
    put, then = _put, (lambda *fs: (lambda r, b: [f(r, b) for f in fs]))
    stock = put("etc/sudoers", STOCK_SUDOERS)
    with tempfile.TemporaryDirectory() as t:
        out = _run_verify_secrets(t, then(_community_default, stock), {"EDITION": "community"})
        for want in ("PASS: SETTINGS > PASSWORD may run its one helper (no arguments) without admin",
                     "PASS: sudoers includes nothing but /etc/sudoers.d",
                     "PASS: every sudoers line is stock Pi OS or one of our rules",
                     "PASS: /usr/local/sbin/coin-dojo-set-password is root:root",
                     "PASS: /usr/local/sbin/coin-dojo-set-hostname is root:root"):
            assert want in out, want + "\n" + out
        assert "FAIL" not in out, out
    with tempfile.TemporaryDirectory() as t:      # admin on (a boot-partition password): coindojo-admin is allowed then
        out = _run_verify_secrets(t, then(stock, put("etc/sudoers.d/coindojo-admin", "coindojo ALL=(ALL:ALL) ALL\n")), PERSONAL)
        assert "PASS: every sudoers line is stock Pi OS or one of our rules" in out, out
    for i, line in enumerate(REVIEW_LINES):
        with tempfile.TemporaryDirectory() as t:
            out = _run_verify_secrets(t, then(_community_default, stock, put("etc/sudoers.d/90-x", line + "\n")), {"EDITION": "community"})
            assert "FAIL: a sudoers line outside the allowlist: /etc/sudoers.d/90-x:" in out, \
                "BROKEN review line %d not caught: %r\n%s" % (i, line, out)
    with tempfile.TemporaryDirectory() as t:      # a wide rule hidden in the main file
        out = _run_verify_secrets(t, then(_community_default, put("etc/sudoers", STOCK_SUDOERS + "coindojo ALL=(ALL:ALL) ALL\n")),
                                  {"EDITION": "community"})
        assert "FAIL: a sudoers line outside the allowlist: /etc/sudoers: coindojo ALL=(ALL:ALL) ALL" in out, out
    sett = "etc/sudoers.d/coin-dojo-settings"
    for name, plant, want in (
            ("include in sudoers.d", put("etc/sudoers.d/90-x", "@include /etc/extra-sudoers\n"), "FAIL: sudoers includes more than"),
            ("old-style include in sudoers.d", put("etc/sudoers.d/90-x", "#include /etc/extra\n"), "FAIL: sudoers includes more than"),
            ("includedir in sudoers.d", put("etc/sudoers.d/90-x", "@includedir /etc/sudoers.d\n"), "FAIL: sudoers includes more than"),
            ("another includedir in sudoers", put("etc/sudoers", STOCK_SUDOERS + "@includedir /var/tmp\n"), "FAIL: sudoers includes more than"),
            ("an include in sudoers", put("etc/sudoers", STOCK_SUDOERS + "#include /etc/x\n"), "FAIL: sudoers includes more than"),
            ("password rule missing", put(sett, HOSTNAME_RULE + "\n"), "FAIL: no exact sudoers rule for the SETTINGS password helper"),
            ("settings file missing", lambda r, b: os.remove(os.path.join(r, sett)), "FAIL: no exact sudoers rule for the SETTINGS password helper"),
            ("admin rule while the default is active", put("etc/sudoers.d/coindojo-admin", "coindojo ALL=(ALL:ALL) ALL\n"),
             "FAIL: a sudoers line outside the allowlist: /etc/sudoers.d/coindojo-admin"),
            ("a directory in sudoers.d", lambda r, b: os.makedirs(os.path.join(r, "etc/sudoers.d/d")), "FAIL: a sudoers line outside the allowlist: /etc/sudoers.d/d: not a regular file"),
            ("helper group-writable", lambda r, b: os.chmod(os.path.join(r, "usr/local/sbin/coin-dojo-set-password"), 0o775),
             "FAIL: /usr/local/sbin/coin-dojo-set-password missing, a link, not root:root, or group/other-writable"),
            ("helper other-writable", lambda r, b: os.chmod(os.path.join(r, "usr/local/sbin/coin-dojo-set-hostname"), 0o757),
             "FAIL: /usr/local/sbin/coin-dojo-set-hostname missing"),
            ("helper missing", lambda r, b: os.remove(os.path.join(r, "usr/local/sbin/coin-dojo-set-password")),
             "FAIL: /usr/local/sbin/coin-dojo-set-password missing"),
            ("helper is a link", lambda r, b: (os.remove(os.path.join(r, "usr/local/sbin/coin-dojo-set-password")),
                                               os.symlink("/bin/sh", os.path.join(r, "usr/local/sbin/coin-dojo-set-password"))),
             "FAIL: /usr/local/sbin/coin-dojo-set-password missing, a link")):
        with tempfile.TemporaryDirectory() as t:
            out = _run_verify_secrets(t, then(_community_default, stock, plant), {"EDITION": "community"})
            assert want in out, f"BROKEN case not caught: {name}\n{out}"
    with tempfile.TemporaryDirectory() as t:      # BROKEN owner: "root" is someone else -> the real find -user check FAILs
        out = _run_verify_secrets(t, then(_community_default, stock), {"EDITION": "community", "VERIFY_ROOT_UID": str(os.getuid() + 4242)})
        assert "FAIL: /usr/local/sbin/coin-dojo-set-password missing, a link, not root:root" in out, out
    # verify's lines are the settings role's lines: the role defaults rendered for coindojo
    repo = os.path.dirname(os.path.dirname(SKILL))
    with open(os.path.join(repo, "ansible/roles/settings/defaults/main.yml"), encoding="utf-8") as fh:
        d = fh.read()
    pw_dest = re.search(r"(?m)^settings_password_helper_dest: (\S+)", d).group(1)
    line = re.search(r"(?m)^settings_password_sudoers_line: '(.*)'", d).group(1)
    assert line.replace("{{ settings_user }}", "coindojo").replace("{{ settings_password_helper_dest }}", pw_dest) == PASSWORD_RULE, \
        "settings role's password sudoers line drifted from verify.sh"
    hline = re.search(r'(?m)^settings_sudoers_line: "(.*)"', d).group(1).replace("\\\\", "\\")
    with open(os.path.join(repo, "config/variables.d/settings.yml"), encoding="utf-8") as fh:
        hdest = re.search(r"hk_settings_helper_dest:\s*\n\s*type: path\s*\n\s*default: (\S+)", fh.read()).group(1)
    assert hline.replace("{{ settings_user }}", "coindojo").replace("{{ settings_helper_dest }}", hdest) == HOSTNAME_RULE, \
        "settings role's hostname sudoers line drifted from verify.sh: %r" % hline


def test_verify_community_default_login():
    """#265: a community image without a configured password has exactly one password, the cabinet user's default
    (12345, recorded at build time), and SSH refuses passwords. Every broken form below must FAIL."""
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        out = _run_verify_secrets(t, _community_default, {"EDITION": "community"})
        assert "network folder: roms and bios from the local subnets" in out, "#270 L5: verify says 12345 opens the folder\n" + out
        assert "FAIL" not in out and "PASS: coindojo has a password set: the community default login coindojo / 12345" in out \
            and "PASS: SSH refuses passwords" in out and "but coindojo's default login" in out, out
    with tempfile.TemporaryDirectory() as t:       # no EDITION in the env: run.sh's default, community
        assert "PASS: coindojo has a password set" in _run_verify_secrets(t, _community_default), "EDITION unset = community"
    then = lambda *fs: (lambda r, b: [f(r, b) for f in fs])
    cases = [
        ("user locked", lambda r, b: _community_default(r, b, h="!"), "FAIL: coindojo has no default password"),
        ("user empty", lambda r, b: _community_default(r, b, h=""), "FAIL: coindojo has no default password"),
        ("no mark", lambda r, b: _community_default(r, b, mark=None), "FAIL: coindojo has no default password"),
        ("hash is not the recorded one", lambda r, b: _community_default(r, b, h="$6$EXAMPLEother$" + "B" * 86),
         "FAIL: coindojo has no default password"),
        ("root has a password too", then(_community_default, _put("etc/shadow", "root:%s:1::::::\ncoindojo:%s:1::::::\n"
                                                                   % (DEFAULT_HASH, DEFAULT_HASH))), "FAIL: a usable or empty password in /etc/shadow (root"),
        ("another drop-in turns password SSH on", then(_community_default, _put("etc/ssh/sshd_config.d/50-x.conf", "PasswordAuthentication yes\n")),
         "FAIL: password SSH is possible"),
        ("a drop-in turns challenge-response on", then(_community_default, _put("etc/ssh/sshd_config.d/50-x.conf", "ChallengeResponseAuthentication yes\n")),
         "FAIL: password SSH is possible"),
        # critic round: an empty-quoted password_hash is NO password for the build (default set), so verify must
        # expect the default too: a locked user then FAILs, the default PASSes (below)
        ("empty-quoted password_hash, user locked", then(lambda r, b: _community_default(r, b, h="!", mark=None),
                                                         _put("BOOT/coindojo.yml", 'password_hash: ""\n')),
         "FAIL: coindojo has no default password"),
        ("malformed boot config", then(_community_default, _put("BOOT/coindojo.yml", "Password_hash: x\n")),
         "FAIL: cannot tell whether the first-boot config sets a password"),
        ("key-only drop-in missing PasswordAuthentication no", then(_community_default, _put("etc/ssh/sshd_config.d/00-coin-dojo-keyonly.conf", "PermitRootLogin no\n")),
         "FAIL: password SSH is possible"),
    ]
    for name, setup, want in cases:
        with tempfile.TemporaryDirectory() as t:
            out = _run_verify_secrets(t, setup, {"EDITION": "community"})
            assert want in out, f"BROKEN case not caught: {name}\n{out}"
    with tempfile.TemporaryDirectory() as t:
        out = _run_verify_secrets(t, _community_default, {"EDITION": "community", "COIN_DOJO_SSH_PASSWORD": "yes"})
        assert "FAIL: password SSH is possible" in out, "BROKEN: the password-SSH opt-in with the default password not caught\n" + out
    # personal: the default login is never there
    with tempfile.TemporaryDirectory() as t:
        out = _run_verify_secrets(t, _community_default, PERSONAL)
        assert "FAIL: a usable or empty password" in out and "FAIL: a default-password mark" in out, \
            "BROKEN: a personal image with the default login not caught\n" + out
    with tempfile.TemporaryDirectory() as t:
        out = _run_verify_secrets(t, lambda r, b: None, PERSONAL)
        assert "PASS: no default login" in out and "FAIL" not in out, out
    # empty or empty-quoted password_hash: the build set the default, and verify agrees (same parser, no FAIL)
    for empty in ('password_hash: ""\n', "password_hash: ''\n", "password_hash:\nhostname: dojo\n"):
        with tempfile.TemporaryDirectory() as t:
            out = _run_verify_secrets(t, then(_community_default, _put("BOOT/coindojo.yml", empty)), {"EDITION": "community"})
            assert "FAIL" not in out and "PASS: coindojo has a password set" in out, "%r:\n%s" % (empty, out)
    # community with a configured password: the account stays locked until first boot, no default expected
    with tempfile.TemporaryDirectory() as t:
        out = _run_verify_secrets(t, _put("BOOT/coindojo.yml", "password_hash: '%s'\n" % DEFAULT_HASH), {"EDITION": "community"})
        assert "PASS: no default login" in out and "every account locked" in out and "FAIL: coindojo" not in out, out


def test_build_sets_the_default_login():
    """#265: build.sh asks coindojo-firstboot for the default login with the edition and the boot config; run.sh
    gives the build and verify containers EDITION and refuses password SSH with the default password."""
    build = _read("build.sh")
    assert re.search(r"coindojo-firstboot --default-login --user \"\$\{HK_CABINET_USER:-coindojo\}\"\s*\\\n\s*"
                     r"--edition \"\$\{EDITION:-community\}\" --config /boot/firmware/coindojo\.yml", build), \
        "build.sh runs --default-login with the user, the edition and the boot config"
    assert build.index("--default-login") > build.index("coindojo-firstboot --check"), "after the config is in place"
    assert re.search(r"--default-login[^\n]*\\\n[^\n]*\n\s*\|\| \{[^}]*exit 1", build), "a refusal stops the build"
    assert "12345" not in re.sub(r"(?m)^\s*#.*$", "", build), "no password or hash typed into build.sh (the script makes it)"
    run = _read("run.sh")
    assert re.search(r"-e EDITION [^\n]*\n?[^\n]*/work/build\.sh", run) and re.search(r"-e EDITION [^\n]*\n?[^\n]*/work/verify\.sh", run), \
        "EDITION reaches the build and verify containers"
    # the /etc/shadow path unit (the reminder only, never sudo: #270), enabled in the image
    setup = _read("chroot-setup.sh")
    path = re.search(r"coindojo-password\.path <<'UNIT'\n(.*?)\nUNIT", setup, re.S)
    svc = re.search(r"coindojo-password\.service <<'UNIT'\n(.*?)\nUNIT", setup, re.S)
    assert path and "PathChanged=/etc/shadow" in path.group(1) and "Unit=coindojo-password.service" in path.group(1)
    assert svc and "ExecStart=/usr/local/sbin/coindojo-firstboot --password-changed" in svc.group(1) \
        and "Type=oneshot" in svc.group(1)
    unit = svc.group(1)                  # #270 L6: no start limit, in [Unit] (a burst of passwd never stops it)
    assert re.search(r"(?m)^StartLimitIntervalSec=0$", unit) and unit.index("StartLimitIntervalSec=0") < unit.index("[Service]"), unit
    assert "systemctl enable coindojo-password.path" in setup, "the path unit is enabled"
    verify = _read("verify.sh")
    assert "--sets-password" in verify and "--sets-password" in run, "run.sh and verify.sh ask the build's own parser"
    assert not re.search(r"\(password_hash\|password\)", verify + run), "no second password regex beside the parser"
    fb = open(os.path.join(SCRIPTS, "coindojo-firstboot.py")).read()
    assert not re.search(r"\$6\$[./A-Za-z0-9]{8,16}\$[./A-Za-z0-9]{86}", fb), "no fixed SHA-512 hash committed in coindojo-firstboot"


def loop_cleanup_problems(text):
    """#237 round 2: a script that attaches loop devices must detach them on ANY exit."""
    p = []
    if "losetup --find" in text:
        if not re.search(r"(?m)^trap cleanup EXIT$", text):
            p.append("no `trap cleanup EXIT`")
        m = re.search(r"(?m)^cleanup\(\)\{(.*)\}$", text)
        if not m or "losetup -d" not in m.group(1):
            p.append("cleanup() does not losetup -d")
    return p


def test_loop_devices_cleaned_on_exit():
    for name in ("prep-image.sh", "verify.sh"):
        assert not loop_cleanup_problems(_read(name)), (name, loop_cleanup_problems(_read(name)))
    m = re.search(r"(?ms)^cleanup\(\)\{.*?^\}$|^cleanup\(\)\{.*\}$", _read("build.sh"))
    assert "trap cleanup EXIT" in _read("build.sh") and m and "losetup -d" in m.group(0), "build.sh cleanup"
    broken = _read("prep-image.sh").replace("trap cleanup EXIT", "")
    assert loop_cleanup_problems(broken), "BROKEN: prep without its trap is not caught"
    broken = _read("verify.sh").replace('losetup -d "$L"', "true")
    assert loop_cleanup_problems(broken), "BROKEN: verify cleanup without losetup -d is not caught"


# A secret expanded into an argv word is visible in `ps` (#42 MED-5): `env KEY=$VAL cmd`, or the value
# inside a `bash -c "..."` string. Shell prefix assignments (`KEY="$KEY" cmd`) and builtins piped into
# chpasswd are fine: the value stays in the environment / a pipe.
SECRET_VARS = r"(?:CABINET_PW|COINDOJO_PASSWORD|PW)"
ARGV_LEAKS = [
    (re.compile(r"\benv\b[^\n|;]*\b" + SECRET_VARS + r"="), "secret passed as an `env` argument"),
    (re.compile(r"""\bbash\s+-c\s+"[^"]*\$\{?""" + SECRET_VARS + r"\b"), "secret expanded inside bash -c"),
]
KEYCHAIN_ITEM = re.compile(r"find-generic-password\s+(?:-[a-z]\s+\S+\s+)*?-s\s+([A-Za-z0-9_.-]+)")
REPO = os.path.dirname(os.path.dirname(SKILL))
# docs/history/ is not in the community edition (#240): there the list is SKILL.md alone.
SECRET_DOCS = [p for p in (os.path.join(SKILL, "SKILL.md"), os.path.join(REPO, "docs", "history", "arcade-image.md"))
               if os.path.isfile(p)]


def argv_leaks(text):
    return [why for rx, why in ARGV_LEAKS if rx.search(text)]


def test_secrets_never_in_argv():
    for path in [os.path.join(SCRIPTS, s) for s in SHELL] + SECRET_DOCS:
        with open(path, encoding="utf-8") as f:
            hits = argv_leaks(f.read())
        assert not hits, f"{os.path.relpath(path, REPO)}: {hits}"


def test_no_literal_keychain_item_for_the_cabinet_password():
    # docs/history is a frozen record (exempt from the local-values gate), so only the live files are held to this.
    for path in [os.path.join(SCRIPTS, s) for s in SHELL] + SECRET_DOCS[:1]:
        with open(path, encoding="utf-8") as f:
            items = set(KEYCHAIN_ITEM.findall(f.read()))
        assert not items, f"{os.path.relpath(path, REPO)} names a literal Keychain item {items}: it comes from hk_become_keychain"


def test_argv_leak_scanner_has_teeth():
    """Deliberately-broken control: the old build.sh and SKILL forms MUST be caught; the fixed forms pass."""
    assert argv_leaks('chroot /mnt/root /usr/bin/env CABINET_PW="$CABINET_PW" bash x.sh')
    assert argv_leaks('''sudo chroot /mnt/arcade /bin/bash -c "echo 'coindojo:$PW' | chpasswd"''')
    assert not argv_leaks('CABINET_PW="$CABINET_PW" \\\n  chroot /mnt/root /bin/bash /root/chroot-setup.sh')
    assert not argv_leaks("printf 'coindojo:%s\\n' \"$PW\" | sudo chroot /mnt/arcade chpasswd")
    assert set(KEYCHAIN_ITEM.findall("security find-generic-password -s arcade-coindojo-password -w")) \
        == {"arcade-coindojo-password"}


def test_netplay_not_announced_publicly():
    assert 'netplay_public_announce = "false"' in _read("chroot-setup.sh"), \
        "image must not announce netplay sessions on the public lobby"


def test_secret_scanner_has_teeth():
    """Deliberately-broken control: a planted secret MUST be caught."""
    planted = 'CABINET_PW="EXAMPLEfakepw0123"\n-----BEGIN OPENSSH PRIVATE KEY-----\n'
    hits = [why for rx, why in SECRET_PATTERNS if rx.search(planted)]
    assert len(hits) == 2, f"secret scanner failed to catch planted secrets: {hits}"


def _run_verify_hardening(tmp, group, es_settings):
    """Run verify.sh's hardening block on a fixture root ($R) with the given /etc/group and es_settings.xml."""
    m = re.search(r"\n# >>> hardening.*?\n(.*?)\n# <<< hardening", _read("verify.sh"), re.S)
    assert m, "hardening block not found in verify.sh"
    os.makedirs(os.path.join(tmp, "etc"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "home", "coindojo", "ES-DE", "settings"))
    open(os.path.join(tmp, "etc", "group"), "w").write(group)
    open(os.path.join(tmp, "home", "coindojo", "ES-DE", "settings", "es_settings.xml"), "w").write(es_settings)
    r = subprocess.run(["bash", "-c", "R=%s\nCU=coindojo\n%s\n" % (tmp, m.group(1))], capture_output=True, text=True)
    return r.stdout


def test_verify_hardening_gates():
    """#168: verify FAILs on pi in sudo and on a baked scraper account; a clean image PASSes."""
    import tempfile
    clean_group = "root:x:0:\nsudo:x:27:coindojo\npi:x:1000:\n"
    clean_es = '<string name="ROMDirectory" value="/data/roms" />\n<string name="ScraperUsernameScreenScraper" value="" />\n'
    with tempfile.TemporaryDirectory() as t:
        out = _run_verify_hardening(t, clean_group, clean_es)
        assert "PASS: pi not in the sudo group" in out and "PASS: no scraper account" in out and "FAIL" not in out, out
    for group in ("sudo:x:27:pi\n", "sudo:x:27:coindojo,pi\n", "sudo:x:27:pi,coindojo\n"):
        with tempfile.TemporaryDirectory() as t:
            assert "FAIL: pi is in the sudo group" in _run_verify_hardening(t, group, clean_es), "BROKEN: pi in sudo not caught: " + group
    with tempfile.TemporaryDirectory() as t:
        assert "PASS: pi not in the sudo group" in _run_verify_hardening(t, "sudo:x:27:coindojo,pine\n", clean_es), "pine is not pi"
    with tempfile.TemporaryDirectory() as t:
        out = _run_verify_hardening(t, clean_group, clean_es)
        assert "PASS: no passwordless sudo rule for pi" in out, out
    with tempfile.TemporaryDirectory() as t:   # BROKEN: Pi OS's 010_pi-nopasswd left behind
        os.makedirs(os.path.join(t, "etc", "sudoers.d"))
        open(os.path.join(t, "etc", "sudoers.d", "010_pi-nopasswd"), "w").write("pi ALL=(ALL) NOPASSWD: ALL\n")
        assert "FAIL: /etc/sudoers.d/010_pi-nopasswd" in _run_verify_hardening(t, clean_group, clean_es), "BROKEN: sudoers rule not caught"
    # built at run time: a literal non-empty scraper value here would trip tests/test_ship_no_artifacts.py's scan
    planted = lambda key, val: '<string name="Scraper%sScreenScraper" value="%s" />\n' % (key, val)
    for es in (planted("Password", "fake-not-real"), planted("Username", "someone")):
        with tempfile.TemporaryDirectory() as t:
            assert "FAIL: es_settings.xml carries a scraper" in _run_verify_hardening(t, clean_group, es), "BROKEN: baked scraper value not caught"


def test_chroot_hardening_locks_root():
    """#168/#240: root's password is locked. The stock pi is no longer hardened in place: the user block removes it
    (test_image_user_gets_hk_cabinet_uid), with its passwordless sudoers rule."""
    m = re.search(r"\n# ---- hardening: .*?\n# ---- end hardening ----", _read("chroot-setup.sh"), re.S)
    assert m and "passwd -l root" in m.group(0), "chroot-setup.sh must lock root's password"
    user = re.search(r"\n# ---- user ----\n(.*?)\nfor g in _seatd", _read("chroot-setup.sh"), re.S).group(1)
    assert "rm -f /etc/sudoers.d/010_pi-nopasswd" in user, "the pi removal must drop Pi OS's passwordless sudo rule"


def _run_user_section(passwd, user="coindojo", uid="1000", edit=None):
    """Run chroot-setup.sh's user block with id/getent/useradd/userdel stubbed over a fake passwd (name:uid lines)."""
    import tempfile
    m = re.search(r"\n# ---- user ----\n(.*?)\nfor g in _seatd", _read("chroot-setup.sh"), re.S)
    assert m, "user block not found in chroot-setup.sh"
    with tempfile.TemporaryDirectory() as t:
        pw, log = os.path.join(t, "passwd"), os.path.join(t, "log")
        open(pw, "w").write("".join("%s:%s\n" % kv for kv in passwd))
        stubs = r"""
LOG(){ :; }
id(){ if [ "$1" = -u ]; then awk -F: -v u="$2" '$1==u{print $2; f=1} END{exit !f}' "$PW"; else awk -F: -v u="$1" '$1==u{f=1} END{exit !f}' "$PW"; fi; }
getent(){ case "$1" in passwd) awk -F: -v n="$2" '$2==n{print $1; f=1} END{exit !f}' "$PW" ;; group) return 1 ;; esac; }
userdel(){ echo "userdel $*" >> "$LOGF"; n=${@: -1}; grep -v "^$n:" "$PW" > "$PW.n"; mv "$PW.n" "$PW"; }
groupdel(){ echo "groupdel $*" >> "$LOGF"; }
groupadd(){ echo "groupadd $*" >> "$LOGF"; }
useradd(){ echo "useradd $*" >> "$LOGF"; u=; a=("$@"); for i in "${!a[@]}"; do [ "${a[$i]}" = -u ] && u=${a[$((i+1))]}; done
  [ -n "$u" ] || u=$(awk -F: 'BEGIN{m=999} $2>=1000 && $2>m{m=$2} END{print m+1}' "$PW"); echo "${a[${#a[@]}-1]}:$u" >> "$PW"; }
"""
        body = edit(m.group(1)) if edit else m.group(1)
        script = "set -euo pipefail\nUSER_NAME=%s\n%s\n%s\n" % (user, stubs, body)
        e = dict(os.environ, PW=pw, LOGF=log, HK_CABINET_UID=uid)
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=e)
        users = dict(l.strip().split(":", 1) for l in open(pw) if ":" in l)
        return r.returncode, (open(log).read() if os.path.exists(log) else ""), r.stderr, users


def test_image_user_gets_hk_cabinet_uid():
    """M1 (#244 fix round): the stock pi (uid 1000) goes, the cabinet user is made with hk_cabinet_uid."""
    good = lambda rc, users, uid: rc == 0 and "pi" not in users and users.get("coindojo") == uid
    rc, log, err, users = _run_user_section([("root", "0"), ("pi", "1000")])
    assert good(rc, users, "1000") and "userdel -r pi" in log, (rc, users, err)
    rc, log, err, users = _run_user_section([("root", "0"), ("pi", "1000")], uid="1001")
    assert good(rc, users, "1001"), (rc, users, err)
    rc, log, err, users = _run_user_section([("root", "0"), ("coindojo", "1001")], uid="1000")
    assert rc == 2 and "already exists with uid 1001" in err, ("BROKEN: a user with the wrong uid must stop the build", err)
    rc, log, err, users = _run_user_section([("root", "0"), ("bob", "1000")], uid="1000")
    assert rc == 2 and "taken by bob" in err, ("BROKEN: a uid held by another user must stop the build", err)
    rc, _, err, _ = _run_user_section([("root", "0")], uid="10x0")
    assert rc == 2 and "not a number" in err, err
    # BROKEN: without the pi removal (the pre-fix image) uid 1000 stays pi's: the build stops, coindojo is not made
    rc, log, err, users = _run_user_section([("root", "0"), ("pi", "1000")],
                                            edit=lambda b: b.replace('if [ "$USER_NAME" != pi ] && id pi', "if false && id pi"))
    assert rc == 2 and "taken by pi" in err and not good(rc, users, "1000"), ("BROKEN: keeping pi must fail", rc, users, err)
    # BROKEN: useradd without -u takes the next free uid (1000 once pi is gone), not hk_cabinet_uid 1001
    rc, log, err, users = _run_user_section([("root", "0"), ("pi", "1000")], uid="1001",
                                            edit=lambda b: b.replace('-u "$USER_UID" ', ""))
    assert users.get("coindojo") == "1000" and not good(rc, users, "1001"), ("BROKEN: no -u must give the wrong uid", users)


def test_provision_refuses_wrong_uid():
    """M1: provision.sh stops before the play when the image user's uid is not hk_cabinet_uid."""
    import json, tempfile
    prov = _read("provision.sh")
    m = re.search(r"\n(CU=\$\(cat .*?\n)echo \">> image user", prov, re.S)
    assert m, "uid check not found in provision.sh"
    assert prov.index(m.group(1)) < prov.index("ansible-playbook"), "the uid check must run before the play"
    for have, want, rc_ok in (("1000", 1000, True), ("1001", 1000, False), ("", 1000, False)):
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "etc", "coindojo"))
            open(os.path.join(t, "etc", "coindojo", "built-user"), "w").write("coindojo\n")
            json.dump({"hk_cabinet_uid": want}, open(os.path.join(t, "effective-coin-dojo.json"), "w"))
            stub = 'chroot(){ [ -n "%s" ] && echo %s || return 1; }\n' % (have, have)
            script = "set -euo pipefail\nR=%s; CFG=%s\n%s%secho OK\n" % (t, t, stub, m.group(1))
            r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            assert (r.returncode == 0 and "OK" in r.stdout) == rc_ok, ("BROKEN case" if not rc_ok else "good case", have, r.stderr)


# ---- #244: the image runs the cabinet's roles; cores and ROM folders come from launch.py only -------------------
TABLE = os.path.join(REPO, "ansible", "roles", "cores", "files", "launcher_table.py")
IMAGE_SCRIPTS = ["run.sh", "build.sh", "chroot-setup.sh", "provision.sh", "verify.sh"]


def _launcher(fmt):
    r = subprocess.run([sys.executable, "-I", TABLE, "--format", fmt], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.split()


def typed_list_problems(name, body, systems, cores):
    """A line naming 3+ launcher systems or 2+ of its cores is a hand-typed copy of launch.py's table."""
    p = []
    for i, line in enumerate(body.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        words = set(re.findall(r"[a-z0-9_]+", line))
        if len(words & set(systems)) >= 3 or len(words & set(cores)) >= 2:
            p.append("%s:%d types a system/core list: %s" % (name, i, line.strip()[:80]))
    return p


def test_image_types_no_launcher_list():
    systems, cores = _launcher("systems"), _launcher("cores")
    probs = [x for s in IMAGE_SCRIPTS for x in typed_list_problems(s, _read(s), systems, cores)]
    assert not probs, probs
    old = "for s in arcade neogeo cps1 cps2 cps3 naomi dreamcast psx psp snes genesis nes gb gba n64; do mkdir -p x; done\n"
    assert typed_list_problems("build.sh", _read("build.sh") + old, systems, cores), "BROKEN: the old folder list not caught"
    old = "  for c in fbneo flycast pcsx_rearmed ppsspp mame2003_plus snes9x; do curl x; done\n"
    assert typed_list_problems("chroot-setup.sh", _read("chroot-setup.sh") + old, systems, cores), "BROKEN: old core list"


def fightcade_folder_problems(firstboot, build):
    """#298: first boot makes roms/fightcade on its own line (not in the launcher-systems loop) and README.txt names it."""
    p = []
    loop = re.search(r"(?m)^for s in ([^;]*); do", firstboot)
    if loop and "fightcade" in loop.group(1).split():
        p.append("fightcade is in the launcher-systems loop (it is not an ES-DE system)")
    if not re.search(r'(?m)^mkdir -p "\$DATA/roms/fightcade"$', firstboot):
        p.append("firstboot-expand.sh does not make $DATA/roms/fightcade")
    readme = re.search(r"(?ms)README\.txt <<'RM'\n(.*?)^RM$", build)
    if not readme or "roms/fightcade" not in readme.group(1):
        p.append("the share's README.txt does not name roms/fightcade")
    return p


def test_fightcade_rom_folder():
    fb, b = _read("firstboot-expand.sh"), _read("build.sh")
    assert fightcade_folder_problems(fb, b) == [], fightcade_folder_problems(fb, b)
    assert fightcade_folder_problems(fb.replace('mkdir -p "$DATA/roms/fightcade"\n', ""), b), "BROKEN: no fightcade mkdir"
    assert fightcade_folder_problems(fb.replace("for s in arcade ", "for s in fightcade arcade "), b), "BROKEN: fightcade in the loop"
    assert fightcade_folder_problems(fb, b.replace("roms/fightcade", "roms/arcade")), "BROKEN: README.txt without roms/fightcade"


def test_image_runs_the_roles():
    run = _read("run.sh")
    assert "provision.sh" in run and '"$ANS_IMAGE" bash /work/provision.sh' in run, "run.sh must run the roles (provision.sh)"
    assert "launcher_table.py\" --format paths" in run and ".expect-paths" in run, "verify's list comes from launch.py"
    prov = _read("provision.sh")
    assert "ansible-playbook -i inventory/image.yml image.yml" in prov, "provision.sh runs ansible/image.yml"
    for bad in ("COINDOJO_PASSWORD", "COIN_DOJO_BECOME_PASS", "cheevos_token", "security find-generic-password"):
        assert bad not in prov, "#237: no secret reaches the play (%s)" % bad
    assert "rm -f \"$R\"/etc/ssh/ssh_host_*" in prov, "no SSH host key may survive the play"
    m = re.search(r"(?ms)^cleanup\(\)\{.*?^\}$", prov)
    assert "trap cleanup EXIT" in prov and m and "losetup -d" in m.group(0), "provision.sh must detach its loops on any exit"


def _run_verify_stack(tmp, plant, paths=None, env=None):
    """Run verify.sh's stack block on a fixture root/boot/data; plant(root, data, boot) builds the image."""
    m = re.search(r"\n# >>> stack.*?\n(.*?)\n# <<< stack", _read("verify.sh"), re.S)
    assert m, "stack block not found in verify.sh"
    root, data, boot = (os.path.join(tmp, x) for x in ("root", "data", "boot"))
    for d in (root, data, boot, os.path.join(root, "etc")):
        os.makedirs(d, exist_ok=True)
    open(os.path.join(root, "etc", "passwd"), "w").write("root:x:0:0::/root:/bin/sh\ncoindojo:x:%d:%d::%s:/bin/bash\n"
                                                         % (os.getuid(), os.getgid(), os.path.join("/home", "coindojo")))
    plant(root, data, boot)
    exp = os.path.join(tmp, "expect")
    lines = paths if paths is not None else _launcher("paths")
    open(exp, "w").write("".join(l + "\n" for l in lines))
    pre = ('R=%s; B=%s; IMG=%s/x.img; CU=coindojo\nchk(){ [ -e "$R/$1" ] && echo "PASS: $2" || echo "FAIL: $2 ($1)"; }\n'
           'grp(){ grep -q "$1" "$2" 2>/dev/null && echo "PASS: $3" || echo "FAIL: $3"; }\n' % (root, boot, tmp))
    e = dict(os.environ, VERIFY_DATA=data, EXPECT_PATHS=exp, HK_CABINET_UID=str(os.getuid()))
    e.update(env or {})
    r = subprocess.run(["bash", "-c", pre + m.group(1)], capture_output=True, text=True, env=e)
    return r.stdout + r.stderr


def _full_image(root, data, boot, fightcade=False):
    def put(base, rel, text="x\n"):
        p = os.path.join(base, rel.lstrip("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(text)
    for p in _launcher("paths"):
        if p.startswith("/data/roms/"):
            os.makedirs(os.path.join(data, p[len("/data/"):]), exist_ok=True)
        elif p.startswith("/data/"):
            put(data, p[len("/data/"):])
        else:
            put(root, p)
    h = "home/coindojo"
    put(root, "usr/local/bin/coin-dojo-session")
    put(root, "usr/local/etc/coin-dojo-session.conf", "HK_RUN_DIR=/run/user/%d/coin-dojo\n" % os.getuid())
    put(data, "emulators/flycast/flycast-run.sh", "export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/%d}\n" % os.getuid())
    put(root, h + "/.bash_profile", "exec cage -d -- /usr/local/bin/coin-dojo-session; exec cage\n")
    for f in ("coin-dojo/settings/bootmenu_app.py", "coin-dojo/settings/settings_app.py", "launcher/stickmouse.py",
              "coin-dojo/settings/settings.hks", "coin-dojo/esde_entry.sh"):
        put(data, f)
    put(root, h + "/ES-DE/custom_systems/es_systems.xml", "<name>settings</name>\n<name>quit</name>\n")
    for slug, link, target in (("settings", "Settings.hks", "/data/coin-dojo/settings/settings.hks"),
                               ("quit", "Quit.hks", "/data/coin-dojo/esde_entry.sh")):
        d = os.path.join(data, "coin-dojo", "nested_out", "roms_nested", slug)
        os.makedirs(d, exist_ok=True)
        os.symlink(target, os.path.join(d, link))
    put(root, h + "/ES-DE/scripts/game-select/50-coin-dojo-settings.sh")
    put(root, h + "/ES-DE/settings/es_settings.xml", '<string name="Theme" value="coin-dojo" />\n')
    put(root, h + "/ES-DE/themes/coin-dojo/theme.xml")
    put(root, h + "/ES-DE/scripts/launch.py")
    os.makedirs(os.path.join(root, "etc/systemd/system/multi-user.target.wants"), exist_ok=True)
    os.symlink("/x", os.path.join(root, "etc/systemd/system/multi-user.target.wants/coin-dojo-powerd.service"))
    for f in ("etc/systemd/user/coin-dojo-stickmouse.service", "etc/udev/rules.d/61-coin-dojo-stickmouse.rules",
              "etc/modules-load.d/coin-dojo-stickmouse.conf", "etc/polkit-1/rules.d/51-coin-dojo-net.rules",
              "etc/polkit-1/rules.d/50-coin-dojo-settings.rules", "etc/sudoers.d/coin-dojo-power",
              "etc/sudoers.d/coin-dojo-settings", "etc/sudoers.d/coin-dojo-sudo-timeout",
              "etc/systemd/journald.conf.d/50-coin-dojo.conf"):
        put(root, f)
    put(boot, "config.txt", "[all]\nkernel=kernel8.img\n")
    put(root, "etc/rpi/swap.conf.d/50-coin-dojo.conf", "[Main]\nMechanism=zram\n")
    put(root, "etc/sysctl.d/99-zz-coin-dojo.conf", "vm.swappiness = 1\n")
    put(root, "etc/NetworkManager/conf.d/90-coin-dojo-wifi.conf", "[connection]\nwifi.powersave = 2\n")
    if fightcade:
        put(root, "usr/local/bin/coin-dojo-fightcade")
        put(root, "etc/binfmt.d/coin-dojo-wine.conf", ":hk-wine:M::MZ::/usr/local/lib/coin-dojo-fightcade/wine:\n")
        put(data, "fightcade/opt/box64/box64")
    if fightcade == "first-launch":   # #267: coin-dojo's pieces only, the manifest names the pinned files
        put(root, "usr/local/lib/coin-dojo-fightcade/hk-fightcade-fetch")
        put(root, "usr/local/lib/coin-dojo-fightcade/hk_fc_firstrun.py")
        put(root, "usr/local/etc/coin-dojo-fightcade.conf", "HK_FC_INSTALL=first-launch\n")
        put(root, "usr/local/etc/coin-dojo-fightcade-fetch.json", json.dumps({"archives": [
            {"name": n, "url": "https://example.org/dl/" + f, "file": f} for n, f in FC_FILES]}, indent=2) + "\n")
    elif fightcade:
        put(data, "fightcade/prefix/.hk-stamp")


FC_FILES = [("wine", "wine-10.0-amd64-wow64.tar.xz"), ("electron", "electron-v9.1.0-linux-arm64.zip"),
            ("fightcade", "Fightcade-linux-v2.1.45.tar.gz")]


def test_verify_stack_gates():
    """#244: verify FAILs when the image misses what the roles and launch.py need; a complete image PASSes."""
    import tempfile
    stack = lambda out: [l for l in out.splitlines() if l.startswith(("PASS", "FAIL"))]
    with tempfile.TemporaryDirectory() as t:
        out = _run_verify_stack(t, _full_image)
        assert stack(out) and not [l for l in stack(out) if l.startswith("FAIL")], out
        assert "PASS: every core, emulator and ROM folder launch.py names" in out, out
        assert "PASS: no Fightcade mode" in out, out
    with tempfile.TemporaryDirectory() as t:
        out = _run_verify_stack(t, lambda r, d, b: _full_image(r, d, b, True), env={"IMAGE_FIGHTCADE": "true"})
        assert not [l for l in stack(out) if l.startswith("FAIL")], out
    fl_env = {"IMAGE_FIGHTCADE": "true", "IMAGE_FIGHTCADE_INSTALL": "first-launch"}
    with tempfile.TemporaryDirectory() as t:   # #267: a shareable image, no Fightcade binaries
        out = _run_verify_stack(t, lambda r, d, b: _full_image(r, d, b, "first-launch"), env=fl_env)
        assert not [l for l in stack(out) if l.startswith("FAIL")], out
        assert "PASS: #267 no Fightcade, Wine or Electron file in the image" in out and "PASS: #267 every download url is https" in out, out

    def fl_plus(rel, text="x\n", where="data"):
        def plant(r, d, b):
            _full_image(r, d, b, "first-launch")
            p = os.path.join(d if where == "data" else r, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w").write(text)
        return plant
    for name, plant, want in [
        ("#267 the Fightcade tree in a first-launch image", fl_plus("fightcade/opt/Fightcade/emulator/fbneo/fcadefbneo.exe"),
         "FAIL: #267 third-party Fightcade files"),
        ("#267 a staged Wine download", fl_plus("fightcade/.incoming/wine-10.0-amd64-wow64.tar.xz"), "FAIL: #267 third-party Fightcade files"),
        ("#267 a partial Electron download", fl_plus("tmp/electron-v9.1.0-linux-arm64.zip.part", where="root"), "FAIL: #267 third-party Fightcade files"),
        ("#267 a Wine tree", fl_plus("fightcade/opt/wine/lib/wine/x86_64-unix/ntdll.so"), "FAIL: #267"),
        ("#267 a built prefix", fl_plus("fightcade/prefix/.hk-stamp"), "FAIL: #267 /data/fightcade/prefix/.hk-stamp"),
        ("#267 an http url", fl_plus("usr/local/etc/coin-dojo-fightcade-fetch.json", '{"archives": [{"url": "http://x/y", "file": "y"}]}\n', "root"),
         "FAIL: #267 a download url that is not https"),
    ]:
        with tempfile.TemporaryDirectory() as t:
            out = _run_verify_stack(t, plant, env=fl_env)
            assert want in out, "BROKEN case not caught: %s\n%s" % (name, out)
    with tempfile.TemporaryDirectory() as t:
        out = _run_verify_stack(t, lambda r, d, b: (_full_image(r, d, b, "first-launch"),
                                                    os.remove(os.path.join(r, "usr/local/lib/coin-dojo-fightcade/hk-fightcade-fetch"))), env=fl_env)
        assert "FAIL: #267 first-launch download engine" in out, "BROKEN case not caught: no engine\n" + out
    core = [p for p in _launcher("paths") if p.endswith("/mame_libretro.so")][0]
    cases = [
        ("a launcher core missing", lambda r, d, b: (_full_image(r, d, b), os.remove(os.path.join(r, core.lstrip("/")))),
         "FAIL: launcher needs " + core),
        ("a launcher ROM folder missing", lambda r, d, b: (_full_image(r, d, b), os.rmdir(os.path.join(d, "roms", "saturn"))),
         "FAIL: launcher needs /data/roms/saturn"),
        ("no QUIT", lambda r, d, b: (_full_image(r, d, b), open(os.path.join(r, "home/coindojo/ES-DE/custom_systems/es_systems.xml"), "w").write("<name>settings</name>\n")),
         "FAIL: QUIT TO MAIN MENU on the carousel"),
        ("dead SETTINGS link", lambda r, d, b: (_full_image(r, d, b), os.remove(os.path.join(d, "coin-dojo/settings/settings.hks"))),
         "FAIL: carousel entry settings/Settings.hks"),
        ("no 4 KB kernel", lambda r, d, b: (_full_image(r, d, b), open(os.path.join(b, "config.txt"), "w").write("[all]\n")),
         "FAIL: 4 KB-page kernel"),
        ("zram-tools back", lambda r, d, b: (_full_image(r, d, b), os.makedirs(os.path.join(r, "etc/default")),
                                             open(os.path.join(r, "etc/default/zramswap"), "w").write("x")),
         "FAIL: zram-tools config"),
        ("Fightcade launcher with the switch off", lambda r, d, b: _full_image(r, d, b, True), "FAIL: Fightcade launcher present"),
        # M1 (#244 fix round): the built image had coindojo at 1001 (Pi OS's pi held 1000) but the roles rendered 1000.
        ("uid differs from hk_cabinet_uid", _full_image, "FAIL: coindojo has uid", {"HK_CABINET_UID": str(os.getuid() + 1)}),
        ("session runs in another uid's dir", lambda r, d, b: (_full_image(r, d, b), open(os.path.join(r, "usr/local/etc/coin-dojo-session.conf"), "w").write("HK_RUN_DIR=/run/user/%d/coin-dojo\n" % (os.getuid() + 1))),
         "FAIL: session HK_RUN_DIR"),
        ("flycast in another uid's dir", lambda r, d, b: (_full_image(r, d, b), open(os.path.join(d, "emulators/flycast/flycast-run.sh"), "w").write("XDG_RUNTIME_DIR=/run/user/%d\n" % (os.getuid() + 1))),
         "FAIL: another uid's runtime dir in the image: /run/user/%d" % (os.getuid() + 1)),
    ]
    for case in cases:
        name, plant, want = case[:3]
        with tempfile.TemporaryDirectory() as t:
            out = _run_verify_stack(t, plant, env=case[3] if len(case) > 3 else None)
            assert want in out, "BROKEN case not caught: %s\n%s" % (name, out)
    with tempfile.TemporaryDirectory() as t:   # the pre-#244 image: its folders, not the launcher's
        def old(r, d, b):
            _full_image(r, d, b)
            for s in ("cps1", "pcengine", "saturn", "segacd", "atari2600"):
                os.rmdir(os.path.join(d, "roms", s))
        out = _run_verify_stack(t, old)
        assert "FAIL: launcher needs /data/roms/cps1" in out and "FAIL: launcher needs /data/roms/atari2600" in out, out
    with tempfile.TemporaryDirectory() as t:
        assert "FAIL: no launcher table" in _run_verify_stack(t, _full_image, paths=[]), "BROKEN: an empty table must fail"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    print("RESULT: %s (%d/%d)" % ("FAIL" if failed else "PASS", len(fns) - failed, len(fns)))
    sys.exit(1 if failed else 0)
