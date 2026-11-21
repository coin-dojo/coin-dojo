#!/usr/bin/env python3
"""coindojo-firstboot - apply /boot/firmware/coindojo.yml once at first boot, then scrub its secrets (#237).

    coindojo-firstboot --check FILE [--user NAME]  validate a config (build time, CONFIG=; exit 0 ok, 1 malformed;
                                            --user: the image's user, any other user: is refused)
    coindojo-firstboot --apply [--root R]  the boot unit (R = / ; tests pass a sandbox root)
    coindojo-firstboot --password-changed [--root R]  the /etc/shadow path unit: the reminder only (never sudo)
    coindojo-firstboot --owner-access --user NAME [--root R]  root only (SETTINGS > PASSWORD's helper, #299): after the
                                            owner set their own password, admin (sudo) and password SSH on; exit 0 on
                                            now, 3 nothing pending, 1 refused (still 12345, or a step failed)
    coindojo-firstboot --sets-password FILE|-  exit 0 when the config gives a password, 1 not, 2 malformed
                                            (run.sh and verify.sh: the same rule as --default-login)
    coindojo-firstboot --default-login --user NAME --edition community|personal [--config FILE] [--root R]
                                            build time (build.sh, in the chroot): the community default login

The image ships with no SSH key and no Wi-Fi, and with no user password, except the community edition's default
login (#265): when the boot config sets no password, --default-login gives the user the password 12345 (a SHA-512
crypt hash made by `openssl passwd -6` with a random salt at build time; the hash is kept in
/etc/coindojo/default-password). While it is 12345, SSH stays key-only, so it is for the console and the network
folder (roms, bios; role share, the local subnets only). A password_hash: or
password: prompt in coindojo.yml, or Imager's userconf.txt, replaces it at first boot. While the password is still
the default, every boot says "Default login NAME / 12345: change it in SETTINGS > PASSWORD" (journal, console, and tty1 before
the frontend: /run/coindojo/default-login). Admin (sudo) is off on such an image (#270): the build takes the user
out of the sudo group (/etc/coindojo/sudo-pending). Only a password from the boot partition, applied by --apply at
boot (coindojo.yml password_hash / password: prompt, Imager's userconf.txt), or SETTINGS > PASSWORD's root helper
(--owner-access, #299, the maintainer 2026-11-20: owners debug without making a key) turns the owner's access on (grant_owner):
/etc/sudoers.d/coindojo-admin and password SSH for that user only (OWNER_SSH, a Match User drop-in checked by sshd -t);
never revoked, and only once the password is no longer 12345. A plain `passwd` at runtime ends the reminder but
grants nothing. The personal edition never gets a default (and never password SSH: it has nothing pending). On every boot the oneshot unit runs --apply:
- /boot/firmware/coindojo.yml present: validate, apply (password, SSH key, hostname, timezone, Wi-Fi),
  then remove it and leave coindojo.applied.yml with the secrets (password_hash, wifi_psk) taken out.
  Malformed: nothing is applied, the secret lines are blanked anyway, the reason goes to coindojo.error.txt
  and to the screen.
- user: must name the image's own user (hk_cabinet_user at make image, #244): the roles baked that name into the
  services, sudoers, polkit and session config, so another name is refused and nothing of the file is applied.
  (rename_user only finishes a rename an older image started before a power cut.)
- Raspberry Pi Imager's userconf.txt (user:hash) is applied the same way when there is no coindojo.yml, only
  for the image's own user. Imager's OS customisation (firstrun.sh) is NOT supported: it renames the uid-1000
  account, the cabinet user since #244. Every boot checks that the image's user still exists; if not, nothing
  is applied and the screen and coindojo.error.txt say how to recover (reflash, skip the customisation).
- No config at all and never configured: /run/coindojo/no-config holds the on-screen hint that the tty1
  login profile shows before the frontend starts.
Format (a flat YAML subset; anything else is refused): see coindojo.example.yml.
Stdlib only; never prints a secret.
"""
import os
import re
import subprocess
import sys
import time
import uuid

KEYS = ("user", "password_hash", "password", "ssh_pubkey", "hostname", "timezone",
        "wifi_ssid", "wifi_psk", "wifi_country")
# Scrub (#237 round 2): only these keys survive on the FAT partition, and only when the line looks like nothing
# secret. Every other line that is not a comment (password, Password_hash:, a typo, a stray value) is dropped.
KEEP = ("user", "ssh_pubkey", "hostname", "timezone", "wifi_ssid", "wifi_country")
SECRETISH = re.compile(r"(?i)pass|psk|secret")
BOM = "\ufeff"
RESERVED_USERS = {"root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news", "uucp", "proxy",
                  "www-data", "backup", "list", "irc", "nobody", "systemd-network", "messagebus", "sshd", "_apt"}
PUBKEY = re.compile(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com|"
                    r"sk-ecdsa-sha2-nistp256@openssh\.com) [A-Za-z0-9+/]+={0,3}( [A-Za-z0-9@._+= -]{1,100})?$")
# A value that carries `<known key>:` lost a line break: a secret may be glued on. Refused for every key (#237 r4).
GLUED = re.compile(r"(?i)(^|[^a-z0-9_])(user|password_hash|password|ssh_pubkey|hostname|timezone|wifi_ssid|"
                   r"wifi_psk|wifi_country)\s*:")
HASH = re.compile(r"^\$(6|5|y|gy|7|2[aby])\$[./A-Za-z0-9$=,]+$")
TZ = re.compile(r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+){0,2}$")
HOST = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")

HINT = """
  COIN DOJO: no first-boot config yet.
  No SSH key, Wi-Fi or password of your own is set (Ethernet and the games still work).
  To set them: put the SD card in a computer, open the boot partition,
  copy coindojo.example.yml to coindojo.yml, fill it in, and boot again.
"""


# The community image's default login (#265). Not a secret: the docs and the cabinet's screen print it.
DEFAULT_PASSWORD = "12345"
DEFAULT_MARK = "/etc/coindojo/default-password"      # its hash (0600), written at build time; gone once changed
DEFAULT_SHOWN = "/run/coindojo/default-login"        # the line tty1 shows before the frontend
EDITIONS = ("community", "personal")
# #270 (lock it down): admin is off with the default login. The build takes the user out of the sudo group and leaves
# SUDO_PENDING naming it; only a boot-partition password applied by --apply (grant_sudo) writes SUDO_RULE. Never
# revoked; never from a runtime password change.
SUDO_PENDING = "/etc/coindojo/sudo-pending"
SUDO_RULE = "/etc/sudoers.d/coindojo-admin"          # no dot in the name: sudo would skip the file
# #299: password SSH for the cabinet user only, written with the admin grant once the password is the owner's own.
# A Match block (scoped to its file) wins over 00-coin-dojo-keyonly.conf's global "no" for that user only; root login
# stays off (PermitRootLogin no is global), keys keep working. The image never ships it (verify.sh).
OWNER_SSH = "/etc/ssh/sshd_config.d/10-coindojo-owner-ssh.conf"
SHA512_CRYPT = re.compile(r"^\$6\$[./A-Za-z0-9]{8,16}\$[./A-Za-z0-9]{86}$")


class ConfigError(Exception):
    pass


def _unquote(raw, lineno):
    v = raw.strip()
    if v.startswith('"'):
        m = re.match(r'^"((?:[^"\\]|\\.)*)"\s*(#.*)?$', v)
        if not m:
            raise ConfigError("line %d: unterminated or malformed double-quoted value" % lineno)
        s = m.group(1)
        if re.search(r'\\[^"\\]', s):
            raise ConfigError("line %d: only \\\" and \\\\ escapes are allowed in double quotes" % lineno)
        return s.replace('\\"', '"').replace("\\\\", "\\")
    if v.startswith("'"):
        m = re.match(r"^'((?:[^']|'')*)'\s*(#.*)?$", v)
        if not m:
            raise ConfigError("line %d: unterminated or malformed single-quoted value" % lineno)
        return m.group(1).replace("''", "'")
    v = re.sub(r"\s+#.*$", "", v)
    if v[:1] in ("[", "{", "&", "*", "!", "|", ">", "@", "`"):
        raise ConfigError("line %d: lists, maps, anchors and block scalars are not supported; use one plain value" % lineno)
    return v


def parse(text):
    """The flat YAML subset: `key: value` per line, comments, blank lines. Returns {key: value}."""
    text = text.lstrip(BOM)               # Notepad writes a UTF-8 byte-order mark
    if "\t" in text:
        raise ConfigError("tabs are not allowed (use spaces)")
    cfg, where = {}, {}
    for n, line in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        if not line.strip() or line.lstrip().startswith("#") or line.strip() in ("---", "..."):
            continue
        if line[0] == " ":
            raise ConfigError("line %d: indented line (nested values are not supported)" % n)
        m = re.match(r"^([a-z_]+):(?:\s+(.*))?$", line.rstrip())
        u = re.match(r"^([A-Za-z_]+):", line)
        if not m and u and u.group(1).lower() in KEYS:
            raise ConfigError("line %d: keys are lower case (%s)" % (n, u.group(1).lower()))
        if not m:
            raise ConfigError("line %d: expected `key: value`" % n)
        key, raw = m.group(1), m.group(2) or ""
        if key not in KEYS:
            raise ConfigError("line %d: unknown key (known: %s)" % (n, ", ".join(KEYS)))
        if key in cfg:
            raise ConfigError("line %d: %s: given twice" % (n, key))
        val = _unquote(raw, n)
        if any(ord(c) < 32 for c in val):
            raise ConfigError("line %d: %s: control characters" % (n, key))
        if val != "":
            cfg[key] = val
            where[key] = n
    try:
        return validate(cfg)
    except ConfigError as e:              # add the line; the message names the key, never the value
        k = str(e).split(":", 1)[0]
        raise ConfigError("line %d: %s" % (where[k], e) if k in where else str(e))


def validate(cfg):
    for k in KEYS:
        if k in cfg and GLUED.search(cfg[k]):
            raise ConfigError("%s: the value contains another key (a line break is missing?)" % k)
    u = cfg.get("user")
    if u is not None and (not USER.match(u) or u in RESERVED_USERS):
        raise ConfigError("user: not a valid login name (a-z, 0-9, _ and -, up to 32, not a system user)")
    if "password_hash" in cfg and "password" in cfg:
        raise ConfigError("give password_hash or password: prompt, not both")
    if "password" in cfg and cfg["password"] != "prompt":
        raise ConfigError("password: only the value `prompt` is allowed (a plain password never goes in this file; "
                          "use password_hash, made with: openssl passwd -6)")
    if "password_hash" in cfg and not HASH.match(cfg["password_hash"]):
        raise ConfigError("password_hash: not a crypt hash ($6$..., $y$...); make one with: openssl passwd -6")
    if "ssh_pubkey" in cfg and not PUBKEY.match(cfg["ssh_pubkey"]):
        raise ConfigError("ssh_pubkey: not an SSH public key (one line, e.g. ssh-ed25519 AAAA... you@laptop)")
    if "hostname" in cfg and not HOST.match(cfg["hostname"]):
        raise ConfigError("hostname: not valid (a-z, 0-9 and -, up to 63, no leading/trailing -)")
    if "timezone" in cfg and (not TZ.match(cfg["timezone"]) or ".." in cfg["timezone"]):
        raise ConfigError("timezone: not a zone name like Europe/Berlin")
    if "wifi_country" in cfg and not re.match(r"^[A-Z]{2}$", cfg["wifi_country"]):
        raise ConfigError("wifi_country: two capital letters (ISO 3166), e.g. US, DE, GB")
    if ("wifi_psk" in cfg or "wifi_country" in cfg) and "wifi_ssid" not in cfg:
        raise ConfigError("wifi_psk/wifi_country without wifi_ssid")
    if "wifi_ssid" in cfg and not 1 <= len(cfg["wifi_ssid"].encode()) <= 32:
        raise ConfigError("wifi_ssid: 1 to 32 bytes")
    psk = cfg.get("wifi_psk")
    if psk is not None and not (8 <= len(psk) <= 63 and all(32 <= ord(c) < 127 for c in psk)
                                or re.match(r"^[0-9a-fA-F]{64}$", psk)):
        raise ConfigError("wifi_psk: 8 to 63 printable ASCII characters (or 64 hex digits)")
    return cfg


# ---------------------------------------------------------------- apply

class Box:
    """Paths and commands relative to a root (/ on the cabinet, a sandbox in tests)."""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.live = self.root == "/"
        self.boot = self.p("/boot/firmware")
        if not os.path.isdir(self.boot) and os.path.isdir(self.p("/boot")):
            self.boot = self.p("/boot")

    def p(self, path):
        return os.path.join(self.root, path.lstrip("/"))

    def run(self, argv, stdin=None, root_flag=True):
        if root_flag and not self.live:
            argv = [argv[0], "--root", self.root] + argv[1:]
        r = subprocess.run(argv, input=stdin, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("%s failed (rc %d)" % (argv[0], r.returncode))   # its stderr may echo input
        return r.stdout


def optional(argv):
    """A best-effort live-system command (hostname, iw, ssh-keygen): missing or failing never stops the apply."""
    try:
        subprocess.run(argv, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        say("note: %s not run" % argv[0])


def say(msg):
    print("coindojo-firstboot: " + msg, flush=True)


def write(path, text, mode=0o644):
    """Root's own files (/etc, /run, the boot partition). Never for a path inside a user's home: user_write."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    if os.path.lexists(tmp):
        os.remove(tmp)                           # a stale or planted temp name: never written through
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    try:
        os.replace(tmp, path)
    except OSError:
        # a bind-mounted file (/etc/hostname, /etc/hosts in a container) cannot be replaced: rewrite it in place
        os.remove(tmp)
        with open(path, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(path, mode)


class UnsafePath(RuntimeError):
    """A path inside the user's home that is (or passes through) a symlink: refused, nothing written."""


def _home_dir_fd(home, parts, make):
    """Open home/parts[0]/... as directory fds without following any symlink (each component O_NOFOLLOW).
    make: create missing directories (0700). Returns the last fd (the caller closes it)."""
    try:
        fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as e:
        raise UnsafePath("the home %s is not a plain folder (%s)" % (home, e.strerror))
    for name in parts:
        try:
            if make:
                try:
                    os.mkdir(name, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            nfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        except OSError as e:
            os.close(fd)
            raise UnsafePath("~/%s is not a plain folder (a symlink?): %s" % ("/".join(parts), e.strerror))
        os.close(fd)
        fd = nfd
    return fd


def _as_user(ent, fn):
    """Run fn() with the user's uid/gid (#270 re-review: root never touches a user's home). Root on a live box:
    in a forked child after setgroups/setresgid/setresuid; otherwise (tests, not root) fn runs here as is."""
    if not ent or os.geteuid() != 0:
        return fn()
    uid, gid = int(ent[2]), int(ent[3])
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:                                       # the child: drop to the user, never come back
        os.close(r)
        code, msg = 0, b""
        try:
            os.setgroups([])
            os.setresgid(gid, gid, gid)
            os.setresuid(uid, uid, uid)
            fn()
        except BaseException as e:                    # noqa: BLE001 - every failure goes back to the parent
            code, msg = 1, str(e).encode()[:500]
        os.write(w, msg)
        os._exit(code)
    os.close(w)
    msg = b""
    while True:
        chunk = os.read(r, 4096)
        if not chunk:
            break
        msg += chunk
    os.close(r)
    _, status = os.waitpid(pid, 0)
    if os.waitstatus_to_exitcode(status) != 0:
        raise UnsafePath(msg.decode(errors="replace") or "the write as %s failed" % ent[0])


def user_read(home, rel):
    """A file in the user's home, read without following a symlink anywhere on the way (None = missing)."""
    parts = rel.split("/")
    try:
        dfd = _home_dir_fd(home, parts[:-1], False)
    except UnsafePath:
        if not os.path.lexists(os.path.join(home, *parts[:-1])):
            return None
        raise
    try:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise UnsafePath("~/%s is not a plain file (a symlink?): %s" % (rel, e.strerror))
    finally:
        os.close(dfd)
    with os.fdopen(fd) as f:
        return f.read()


def user_write(box, ent, rel, text, mode=0o644, dir_mode=None):
    """Write home/rel AS THE USER (#270 re-review: install_key and the rename once followed user-made symlinks as
    root, e.g. ~/.ssh -> /etc). No component may be a symlink; the temp file is made O_EXCL|O_NOFOLLOW after
    removing whatever sits on its name; the rename replaces a symlink at rel itself, never its target."""
    home = box.p(ent[5])
    parts = rel.split("/")

    def do():
        dfd = _home_dir_fd(home, parts[:-1], True)
        try:
            if dir_mode is not None:
                os.fchmod(dfd, dir_mode)
            tmp = parts[-1] + ".tmp"
            try:
                os.unlink(tmp, dir_fd=dfd)            # a planted temp (symlink or file): the link goes, not its target
            except FileNotFoundError:
                pass
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=dfd)
            with os.fdopen(fd, "w") as f:
                f.write(text)
                f.flush()
                os.fchmod(f.fileno(), mode)
                os.fsync(f.fileno())
            os.replace(tmp, parts[-1], src_dir_fd=dfd, dst_dir_fd=dfd)
        finally:
            os.close(dfd)
    _as_user(ent, do)


def passwd_entry(box, name):
    try:
        with open(box.p("/etc/passwd")) as f:
            for line in f:
                parts = line.rstrip("\n").split(":")
                if parts[0] == name:
                    return parts
    except OSError:
        pass
    return None


def built_user(box):
    try:
        return open(box.p("/etc/coindojo/built-user")).read().strip() or "coindojo"
    except OSError:
        return "coindojo"


def group_exists(box, name):
    try:
        with open(box.p("/etc/group")) as f:
            return any(line.split(":", 1)[0] == name for line in f)
    except OSError:
        return False


RENAME_STATE = "/var/lib/coindojo/rename"


def rename_pending(box):
    """(old, new) of a rename a power cut interrupted, else None."""
    try:
        old, new = open(box.p(RENAME_STATE)).read().split()
        return old, new
    except (OSError, ValueError):
        return None


def rename_user(box, old, new):
    """Rename the image user. Every step checks its own state first, so a power cut at any point resumes on
    the next boot (the state file names the rename until the last step is done)."""
    if rename_pending(box) != (old, new):
        if passwd_entry(box, new) or group_exists(box, new):
            raise RuntimeError("user: that name already exists on the system (as a user or a group); pick another")
        write(box.p(RENAME_STATE), "%s %s\n" % (old, new))
    say("renaming user %s -> %s" % (old, new))
    # 1. the login name
    if passwd_entry(box, old) and not passwd_entry(box, new):
        box.run(["usermod", "-l", new, old])
    ent = passwd_entry(box, new)
    if not ent:
        raise RuntimeError("the pending rename %s -> %s finds neither user" % (old, new))
    # 2. the home folder (usermod -m moves it, then records it; a cut can land between the two)
    oldh, newh = "/home/" + old, "/home/" + new
    if ent[5] != newh:
        if os.path.isdir(box.p(oldh)) and not os.path.exists(box.p(newh)):
            box.run(["usermod", "-d", newh, "-m", new])
        else:
            box.run(["usermod", "-d", newh, new])          # moved already, only the record is old
    elif os.path.isdir(box.p(oldh)) and not os.path.exists(box.p(newh)):
        os.rename(box.p(oldh), box.p(newh))                # recorded already, the move did not happen
    # 3. the group
    if group_exists(box, old) and group_exists(box, new):
        raise RuntimeError("the pending rename %s -> %s finds both groups; remove one by hand" % (old, new))
    if group_exists(box, old):
        box.run(["groupmod", "-n", new, old])
    # 4. tty1 autologin and paths inside the user's config
    al = box.p("/etc/systemd/system/getty@tty1.service.d/autologin.conf")
    if os.path.exists(al):
        write(al, open(al).read().replace("--autologin %s " % old, "--autologin %s " % new))
    ent = passwd_entry(box, new)
    for rel in (".config/retroarch/retroarch.cfg", ".bash_profile"):
        try:                                 # as the user, never through a symlink (#270 re-review)
            text = _as_user_read(ent, box.p(ent[5]), rel)
            if text is not None:
                user_write(box, ent, rel, text.replace("%s/" % oldh, "%s/" % newh))
        except UnsafePath as e:
            say("skipped ~/%s: %s" % (rel, e))
    write(box.p("/etc/coindojo/user"), new + "\n")
    os.remove(box.p(RENAME_STATE))


def current_user(box):
    """The cabinet's user now: the last applied rename, else the image's own user."""
    try:
        u = open(box.p("/etc/coindojo/user")).read().strip()
    except OSError:
        u = ""
    return u if u and passwd_entry(box, u) else built_user(box)


def lost_user(box):
    """None, or why the image's user is gone. Raspberry Pi Imager's OS customisation (firstrun.sh from the kernel
    command line) renames the account with uid 1000, which is the cabinet user since #244: units, sudoers, polkit and
    the session still name the old user, so the cabinet would not start. Checked on every boot."""
    if not os.path.exists(box.p("/etc/passwd")):
        return None
    try:
        named = open(box.p("/etc/coindojo/user")).read().strip()
    except OSError:
        named = ""
    want = built_user(box)
    if passwd_entry(box, want) or (named and passwd_entry(box, named)):
        return None
    others = []
    with open(box.p("/etc/passwd")) as f:
        for line in f:
            parts = line.rstrip("\n").split(":")
            if len(parts) > 2 and parts[2].isdigit() and 1000 <= int(parts[2]) < 60000:
                others.append("%s (uid %s)" % (parts[0], parts[2]))
    return ("the cabinet user %s no longer exists%s. Raspberry Pi Imager's OS customisation (a username, firstrun.sh) "
            "renames the first account, but this image was built for %s: its services, sudoers and session still use "
            "that name, so the cabinet cannot start. To recover: flash the image again, choose No / skip at Imager's "
            "customisation question, and put your settings in coindojo.yml on the boot partition (user: stays empty)."
            % (want, (": the account is now " + ", ".join(others)) if others else "", want))


LOST_BANNER = """
  ************************************************************************
  COIN DOJO: THE CABINET USER IS GONE (renamed by Raspberry Pi Imager?)
  ************************************************************************
"""


def resume_rename(box):
    """A rename a power cut interrupted is finished first, whatever the config now says."""
    p = rename_pending(box)
    if p:
        say("finishing the user rename %s -> %s from an earlier boot" % p)
        rename_user(box, *p)


def set_password_prompt(box, user):
    """Ask on the cabinet screen (tty1), 5 minutes, 3 tries. No keyboard = the account keeps no password."""
    if not box.live:
        say("password prompt skipped (not on a live system)")
        return False
    for _ in range(3):
        try:
            with open("/dev/tty1", "r+") as tty:
                tty.write("\nCoin Dojo: set the password for %s\n" % user)
                tty.flush()
                r = subprocess.run(["passwd", user], stdin=tty, stdout=tty, stderr=tty, timeout=300)
            if r.returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            break
    say("no password set for %s (prompt skipped or timed out); SSH by key still works" % user)
    return False


def install_key(box, user, key):
    """~/.ssh/authorized_keys, written as the user and never through a symlink (user_write)."""
    ent = passwd_entry(box, user)
    if not ent:
        raise RuntimeError("the user %s does not exist" % user)
    old = _as_user_read(ent, box.p(ent[5]), ".ssh/authorized_keys")
    have = old.splitlines() if old else []
    if key not in have:
        user_write(box, ent, ".ssh/authorized_keys", "\n".join(have + [key]) + "\n", 0o600, dir_mode=0o700)
    else:
        user_write(box, ent, ".ssh/authorized_keys", old, 0o600, dir_mode=0o700)   # same text, modes fixed


def _as_user_read(ent, home, rel):
    """user_read with the user's rights (a root read through a planted link could copy /etc/shadow into a file the
    user reads). Returns the text or None."""
    if os.geteuid() != 0:
        return user_read(home, rel)
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        code = 0
        try:
            os.setgroups([])
            os.setresgid(int(ent[3]), int(ent[3]), int(ent[3]))
            os.setresuid(int(ent[2]), int(ent[2]), int(ent[2]))
            text = user_read(home, rel)
            data = b"\0" if text is None else b"\1" + text.encode()
        except BaseException as e:                    # noqa: BLE001
            code, data = 1, str(e).encode()[:500]
        os.write(w, data)
        os._exit(code)
    os.close(w)
    data = b""
    while True:
        chunk = os.read(r, 65536)
        if not chunk:
            break
        data += chunk
    os.close(r)
    _, status = os.waitpid(pid, 0)
    if os.waitstatus_to_exitcode(status) != 0:
        raise UnsafePath(data.decode(errors="replace"))
    return None if data == b"\0" else data[1:].decode()


def set_hostname(box, name):
    write(box.p("/etc/hostname"), name + "\n")
    hosts = box.p("/etc/hosts")
    lines = open(hosts).read().splitlines() if os.path.exists(hosts) else ["127.0.0.1 localhost"]
    lines = [l for l in lines if not l.startswith("127.0.1.1")] + ["127.0.1.1 " + name]
    write(hosts, "\n".join(lines) + "\n")
    if box.live:
        optional(["hostname", name])


def set_timezone(box, tz):
    if not os.path.exists(box.p("/usr/share/zoneinfo/" + tz)):
        raise RuntimeError("timezone: not in /usr/share/zoneinfo")
    lt = box.p("/etc/localtime")
    if os.path.lexists(lt):
        os.remove(lt)
    os.symlink("/usr/share/zoneinfo/" + tz, lt)
    write(box.p("/etc/timezone"), tz + "\n")


def _keyfile_str(s):
    """A GKeyFile string: backslashes doubled; leading and trailing spaces as \\s (NM would strip them)."""
    s = s.replace("\\", "\\\\")
    core = s.strip(" ")
    lead, trail = len(s) - len(s.lstrip(" ")), len(s) - len(s.rstrip(" "))
    if not core:
        return "\\s" * len(s)
    return "\\s" * lead + core + "\\s" * trail


def set_wifi(box, ssid, psk, country):
    if re.search(r"[;\\]", ssid) or ssid != ssid.strip():
        ssid_line = "ssid=" + ";".join(str(b) for b in ssid.encode()) + ";"
    else:
        ssid_line = "ssid=" + ssid
    body = ["[connection]", "id=coindojo-wifi", "uuid=%s" % uuid.uuid4(), "type=wifi", "autoconnect=true", "",
            "[wifi]", "mode=infrastructure", ssid_line, ""]
    if psk:
        body += ["[wifi-security]", "key-mgmt=wpa-psk", "psk=" + _keyfile_str(psk), ""]
    body += ["[ipv4]", "method=auto", "", "[ipv6]", "method=auto", ""]
    write(box.p("/etc/NetworkManager/system-connections/coindojo-wifi.nmconnection"), "\n".join(body), 0o600)
    if country:
        cmd = os.path.join(box.boot, "cmdline.txt")
        if os.path.exists(cmd):
            line = open(cmd).read().strip()
            if "cfg80211.ieee80211_regdom=" in line:
                line = re.sub(r"cfg80211\.ieee80211_regdom=\S+", "cfg80211.ieee80211_regdom=" + country, line)
            else:
                line += " cfg80211.ieee80211_regdom=" + country
            write(cmd, line + "\n")
        if box.live:
            optional(["iw", "reg", "set", country])


def _keep_value(key, raw):
    """A KEEP line stays only if its value is valid for that key on its own (a lost newline glues a secret on)."""
    try:
        v = _unquote(raw, 0)
    except ConfigError:
        return False
    if SECRETISH.search(v) or GLUED.search(v) or any(ord(c) < 32 for c in v):
        return False
    return {
        "user": lambda: bool(USER.match(v)) and v not in RESERVED_USERS,
        "hostname": lambda: bool(HOST.match(v)),
        "timezone": lambda: bool(TZ.match(v)) and ".." not in v,
        "wifi_country": lambda: bool(re.match(r"^[A-Z]{2}$", v)),
        "ssh_pubkey": lambda: bool(PUBKEY.match(v)),
        "wifi_ssid": lambda: 1 <= len(v.encode()) <= 32,
    }[key]()


def scrub_text(text, error="", template_comments=frozenset()):
    """What may stay on the FAT partition: comments, blanks and KEEP keys whose value is valid on its own.
    Anything else (password_hash, wifi_psk, password, Password_hash:, a misspelt key, a line without a key, a value
    with something glued on) is replaced by a note. The line or key an error names is dropped too."""
    bad_line = re.match(r"^line (\d+):", error)
    bad_line = int(bad_line.group(1)) if bad_line else None
    bad_key = re.sub(r"^line \d+: ", "", error).split(":", 1)[0] if error else None
    out = []
    for n, line in enumerate(text.lstrip(BOM).replace("\r\n", "\n").split("\n"), 1):
        s = line.strip()
        if not s or s in ("---", "..."):
            out.append(line)
            continue
        if s.startswith("#"):                 # only the example's own comments: a user's comment may hold a secret
            out.append(line if s in template_comments else "# (comment removed: may hold a secret)")
            continue
        m = re.match(r"^([A-Za-z0-9_-]+)\s*:(.*)$", line)
        if (m and m.group(1) in KEEP and n != bad_line and m.group(1) != bad_key
                and _keep_value(m.group(1), m.group(2))):
            v = _unquote(m.group(2), 0)          # rewritten without any trailing comment
            out.append('%s: "%s"' % (m.group(1), v.replace("\\", "\\\\").replace('"', '\\"')) if v else m.group(1) + ":")
        else:   # name the key only when it is a known one: an unknown "key" may itself be the secret
            known = m and m.group(1).lower() in KEYS
            out.append("# %s: removed (may hold a secret)" % (m.group(1).lower() if known else "line %d" % n))
    return "\n".join(out)


class OtherUser(RuntimeError):
    """A config names a user that is not the image's (#244): refused before any change."""
    def __init__(self, want, have):
        RuntimeError.__init__(self, other_user_message(want, have))
        self.want, self.have = want, have


def other_user_message(want, have):
    """A user: that is not the image's user. The roles baked that name into the services, sudoers, polkit rules, the
    session config and ES-DE's paths when the image was built (make image runs them), so a rename here would leave
    the cabinet half on the old name. The name is chosen before the build instead."""
    return ("user: %s is not this image's user (%s). The image was built for %s: its services, sudoers and session "
            "use that name. Remove the user: line (or write user: %s), or build a new image with "
            "hk_cabinet_user: %s in your config (make image). Nothing from this file was applied."
            % (want, have, have, have, want))


def apply_config(box, cfg, source):
    if "timezone" in cfg and not os.path.exists(box.p("/usr/share/zoneinfo/" + cfg["timezone"])):
        raise RuntimeError("timezone: not in /usr/share/zoneinfo")   # before any change
    user = current_user(box)                 # after a rename, a new config changes that user
    if not passwd_entry(box, user):
        raise RuntimeError("the cabinet user %s does not exist (see /etc/coindojo/user)" % user)
    target = cfg.get("user", user)
    if target != user:      # before any change: nothing of this file is applied (#244 fix round)
        raise OtherUser(target, user)
    write(box.p("/etc/coindojo/user"), target + "\n")
    boot_password = False
    if "password_hash" in cfg:
        box.run(["chpasswd", "-e"], stdin="%s:%s\n" % (target, cfg["password_hash"]))
        say("password set for %s (from the hash)" % target)
        boot_password = True
    elif cfg.get("password") == "prompt":
        boot_password = set_password_prompt(box, target)
    if boot_password:                        # #270/#299: an own password turns admin and password SSH on
        try:
            grant_owner(box, target, source)
        except (RuntimeError, OSError) as e:
            say("ERROR: %s" % failure(e))
    if "ssh_pubkey" in cfg:
        install_key(box, target, cfg["ssh_pubkey"])
        say("SSH key installed for %s" % target)
    if "hostname" in cfg:
        set_hostname(box, cfg["hostname"])
        say("hostname %s" % cfg["hostname"])
    if "timezone" in cfg:
        set_timezone(box, cfg["timezone"])
        say("timezone %s" % cfg["timezone"])
    if "wifi_ssid" in cfg:
        set_wifi(box, cfg["wifi_ssid"], cfg.get("wifi_psk"), cfg.get("wifi_country"))
        say("Wi-Fi network added to NetworkManager")    # never the SSID: a glued secret could ride in it
    write(box.p("/var/lib/coindojo/applied"), "%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), source))


def ensure_host_keys(box):
    """The image ships no SSH host keys (they would be the same on every card): make them before sshd starts."""
    if box.live and not os.path.exists("/etc/ssh/ssh_host_ed25519_key"):
        optional(["ssh-keygen", "-A"])


def hint(box, text):
    write(box.p("/run/coindojo/no-config"), text)
    print(text, flush=True)


def failure(e):
    """The text shown for a failure: messages name keys and lines only; OS errors give just their reason."""
    return "file error (%s)" % e.strerror if isinstance(e, OSError) else str(e)


def template_comments(box):
    """The comment lines of the shipped coindojo.example.yml: the only comments a scrub keeps."""
    try:
        return frozenset(l.strip() for l in open(os.path.join(box.boot, "coindojo.example.yml"), encoding="utf-8")
                         if l.strip().startswith("#"))
    except OSError:
        return frozenset()


def scrub_boot_files(box, why):
    """Before ANY early return: no secret may stay on the FAT partition (coindojo.yml scrubbed, userconf.txt gone)."""
    yml = os.path.join(box.boot, "coindojo.yml")
    uc = os.path.join(box.boot, "userconf.txt")
    if os.path.exists(yml):
        write(yml, scrub_text(open(yml, encoding="utf-8", errors="replace").read(), why, template_comments(box)))
    if os.path.exists(uc):
        os.remove(uc)


def shadow_field(box, user):
    """The password field of user in /etc/shadow, else None."""
    try:
        with open(box.p("/etc/shadow")) as f:
            for line in f:
                parts = line.rstrip("\n").split(":")
                if parts[0] == user and len(parts) > 1:
                    return parts[1]
    except OSError:
        pass
    return None


def text_sets_password(text):
    """The one rule (build, run.sh and verify.sh use it): a config gives a password when it has a non-empty
    password_hash: or password: prompt after parsing (an empty or empty-quoted value is no password)."""
    cfg = parse(text)
    return "password_hash" in cfg or "password" in cfg


def config_sets_password(path):
    """True when the boot config at path gives a password (text_sets_password). No file = False."""
    if not path or not os.path.exists(path):
        return False
    return text_sets_password(open(path, encoding="utf-8-sig").read())


def sshd_allows_passwords(box):
    """True when an sshd drop-in turns password, keyboard-interactive or challenge-response login on. The drop-ins
    are what counts: sshd_config Includes them first and sshd keeps the first value (verify.sh checks the same)."""
    d = box.p("/etc/ssh/sshd_config.d")
    files = [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".conf")] if os.path.isdir(d) else []
    for f in files:
        try:
            text = open(f).read()
        except OSError:
            continue
        if re.search(r"(?im)^\s*(PasswordAuthentication|KbdInteractiveAuthentication|ChallengeResponseAuthentication)"
                     r"\s+yes\b", text):
            return True
    return False


def set_default_login(box, user, edition, config=None):
    """Build time (#265): the community image's user gets the password 12345 unless the boot config sets one.
    openssl makes the hash here with a fresh random salt (no hash is committed) and chpasswd -e sets it; the hash is
    also kept in DEFAULT_MARK so each boot can tell whether the password is still the default."""
    if edition not in EDITIONS:
        raise ConfigError("--edition: community or personal")
    if not USER.match(user or "") or user in RESERVED_USERS:
        raise ConfigError("--user: not a valid login name")
    if edition != "community":
        say("personal edition: no default password (the account stays locked until the first boot sets one)")
        return 0
    if config_sets_password(config):
        say("the first-boot config sets the password: no default password")
        return 0
    if not passwd_entry(box, user):
        raise RuntimeError("the user %s does not exist in the image" % user)
    if sshd_allows_passwords(box):
        raise RuntimeError("password SSH is on (COIN_DOJO_SSH_PASSWORD=yes): with the default password %s anyone on "
                           "your network could log in. Keep SSH key-only, or give a password (CONFIG= with "
                           "password_hash, or COINDOJO_PASSWORD)" % DEFAULT_PASSWORD)
    h = box.run(["openssl", "passwd", "-6", "-stdin"], stdin=DEFAULT_PASSWORD, root_flag=False).strip()
    if not SHA512_CRYPT.match(h):
        raise RuntimeError("openssl passwd -6 did not print a SHA-512 crypt hash")
    box.run(["chpasswd", "-e"], stdin="%s:%s\n" % (user, h))
    write(box.p(DEFAULT_MARK), h + "\n", 0o600)
    lock_sudo(box, user)
    say("default login %s / %s set (community image; SSH stays key-only; admin off until the owner sets their own password)"
        % (user, DEFAULT_PASSWORD))
    return 0


def group_members(box, group):
    try:
        with open(box.p("/etc/group")) as f:
            for line in f:
                parts = line.rstrip("\n").split(":")
                if parts[0] == group and len(parts) > 3:
                    return [m for m in parts[3].split(",") if m]
    except OSError:
        pass
    return []


def lock_sudo(box, user):
    """Build time (#270): the default password must not reach root. Take the user out of the sudo group and leave
    SUDO_PENDING; grant_sudo gives it back only for a password from the boot partition."""
    if user in group_members(box, "sudo"):
        box.run(["gpasswd", "-d", user, "sudo"])
    if os.path.exists(box.p(SUDO_RULE)):           # a fresh image has none; never one left from elsewhere
        os.remove(box.p(SUDO_RULE))
    write(box.p(SUDO_PENDING), user + "\n")


def grant_sudo(box, user, source):
    """#270: the sudoers rule for USER, only through grant_owner (which checks the password is no longer 12345).
    Returns True when it granted; nothing pending (personal or configured build: already in the sudo group) = nothing
    to do. Never revoked."""
    pend = box.p(SUDO_PENDING)
    try:
        pending = open(pend).read().strip()
    except OSError:
        return False
    if pending != user:
        return False
    rule = box.p(SUDO_RULE)
    tmp = rule + ".tmp"                              # a dot in the name: sudo ignores it while visudo checks it
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o440)
    with os.fdopen(fd, "w") as f:
        f.write("# coindojo (#270, #299): sudo for %s, granted once its password was its own (%s).\n"
                "%s ALL=(ALL:ALL) ALL\n" % (user, source, user))
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o440)
    try:
        box.run(["visudo", "-cf", tmp], root_flag=False)
    except RuntimeError:
        os.remove(tmp)
        raise RuntimeError("visudo refused the sudo rule for %s; admin stays off" % user)
    os.replace(tmp, rule)
    os.remove(pend)
    say("admin (sudo) on for %s: its own password came from %s" % (user, source))
    return True


def is_default_hash(h):
    """True when the crypt hash h is of DEFAULT_PASSWORD, False when not, None when it cannot be told (fail closed).
    libcrypt (every format, $y$ included) when it loads, else openssl for $1$/$5$/$6$ without rounds=."""
    try:
        import ctypes
        lib = ctypes.CDLL("libcrypt.so.1")
        lib.crypt.restype = ctypes.c_char_p
        lib.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        out = lib.crypt(DEFAULT_PASSWORD.encode(), h.encode())
        if out and not out.startswith(b"*"):
            return out.decode() == h
    except (OSError, AttributeError, UnicodeError):
        pass
    m = re.match(r"^\$(1|5|6)\$([./A-Za-z0-9]{1,16})\$[./A-Za-z0-9]+$", h)
    if not m:
        return None
    try:
        r = subprocess.run(["openssl", "passwd", "-" + m.group(1), "-salt", m.group(2), "-stdin"], input=DEFAULT_PASSWORD,
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() == h if r.returncode == 0 and r.stdout.startswith("$") else None


def owner_pending(box, user):
    try:
        return open(box.p(SUDO_PENDING)).read().strip() == user
    except OSError:
        return False


def grant_owner(box, user, source):
    """#299 (the maintainer 2026-11-20): once USER's password is their own, admin (sudo, grant_sudo) and password SSH
    (enable_password_ssh) go on together, once: only while SUDO_PENDING names USER (a community image that shipped
    with 12345; personal and configured builds have nothing pending and stay key-only). Refused, nothing written,
    while the password is still 12345: the mark still matches /etc/shadow, or the new hash verifies 12345 (then the
    mark is renewed with it, so the reminder and the default network folder carry on), or it cannot be checked.
    SSH first, then sudo; a failed sudo grant removes the SSH drop-in again. Returns True when it granted."""
    if not owner_pending(box, user):
        return False
    h = shadow_field(box, user) or ""
    if not h.startswith("$"):
        raise RuntimeError("%s has no password: admin and password SSH stay off" % user)
    if default_login(box):
        raise RuntimeError("the password is still the default %s: admin and password SSH stay off" % DEFAULT_PASSWORD)
    is_default = is_default_hash(h)
    if is_default:
        write(box.p(DEFAULT_MARK), h + "\n", 0o600)
        raise RuntimeError("the new password is the default %s: admin and password SSH stay off" % DEFAULT_PASSWORD)
    if is_default is None:
        raise RuntimeError("cannot check that the new password is not %s: admin and password SSH stay off"
                           % DEFAULT_PASSWORD)
    enable_password_ssh(box, user)
    try:
        grant_sudo(box, user, source)
    except (RuntimeError, OSError):
        disable_password_ssh(box)
        raise
    return True


def reload_sshd(box):
    """A running sshd picks the drop-in up (reload: open sessions survive); a stopped one (first boot) reads it at start.
    --no-block: at boot this runs inside coindojo-firstboot.service, ordered Before=ssh.service (a waiting job would
    deadlock)."""
    try:
        r = subprocess.run(["systemctl", "--no-block", "try-reload-or-restart", "ssh.service"], capture_output=True,
                           timeout=60)
        if r.returncode != 0:
            say("note: sshd was not reloaded (rc %d); the change applies at the next start" % r.returncode)
    except (OSError, subprocess.TimeoutExpired):
        say("note: systemctl not run; the change applies at sshd's next start")


def enable_password_ssh(box, user):
    """OWNER_SSH: `Match User USER` + PasswordAuthentication yes. sshd -t checks the whole config with it in place;
    any failure removes it again (SSH stays key-only) and raises."""
    if not USER.match(user or "") or user in RESERVED_USERS:
        raise RuntimeError("not a valid login name for password SSH")
    if not os.path.isdir(box.p("/etc/ssh/sshd_config.d")):
        raise RuntimeError("no /etc/ssh/sshd_config.d: password SSH stays off")
    path = box.p(OWNER_SSH)
    write(path, "# coindojo (#299): password SSH for %s only, turned on when the owner set their own password\n"
                "# (not 12345). Root login stays off; keys keep working. Remove this file to go back to key-only.\n"
                "Match User %s\n    PasswordAuthentication yes\n" % (user, user), 0o644)
    try:
        if box.live:                                 # sshd -t wants its privilege-separation dir; at first boot
            os.makedirs("/run/sshd", 0o755, exist_ok=True)   # ssh.service (RuntimeDirectory) has not made it yet
        box.run(["sshd", "-t"], root_flag=False)
    except (RuntimeError, OSError):
        os.remove(path)
        raise RuntimeError("sshd -t refused the config with password SSH; the drop-in was removed, SSH stays key-only")
    reload_sshd(box)
    say("password SSH on for %s (%s)" % (user, OWNER_SSH))


def disable_password_ssh(box):
    if os.path.lexists(box.p(OWNER_SSH)):
        os.remove(box.p(OWNER_SSH))
        reload_sshd(box)


def owner_access(box, user):
    """--owner-access (#299): SETTINGS > PASSWORD's root helper, after chpasswd. 0 on now, 3 nothing pending, 1 refused."""
    if not USER.match(user or "") or user != current_user(box):
        say("refused: %s is not the cabinet user" % user)
        return 1
    if not owner_pending(box, user):
        say("nothing to do: admin is not pending for %s" % user)
        return 3
    try:
        grant_owner(box, user, "SETTINGS > PASSWORD")
    except (RuntimeError, OSError) as e:
        say("ERROR: %s" % failure(e))
        return 1
    finally:
        report_default_login(box)
    return 0


def password_changed(box):
    """The /etc/shadow path unit and the end of every boot: refresh the default-login reminder. Never sudo (#270)."""
    report_default_login(box)


def default_login(box):
    """The line to show while the cabinet user's password is still the community default, else None. Once the
    password changed (passwd, coindojo.yml, userconf.txt) the mark is removed and nothing is shown again."""
    mark = box.p(DEFAULT_MARK)
    try:
        h = open(mark).read().strip()
    except OSError:
        return None
    user = current_user(box)
    have = shadow_field(box, user)
    if have is None:                      # no such user (lost_user reports that): keep the mark, say nothing
        return None
    if h and have == h:
        return "Default login %s / %s: change it in SETTINGS > PASSWORD" % (user, DEFAULT_PASSWORD)
    try:
        os.remove(mark)
    except FileNotFoundError:             # the path unit and --owner-access may both end it (#299)
        pass
    return None


def report_default_login(box):
    """Every boot, after the config: say the default login (journal + console) and leave it for tty1."""
    shown = box.p(DEFAULT_SHOWN)
    try:
        line = default_login(box)
    except OSError:
        line = None
    if line:
        say(line)
        write(shown, "\n  " + line + "\n  (Admin (sudo) is off and SSH stays key-only until you set your own"
                     " password. 12345 also opens the network folder (roms, bios) on your local network.)\n")
    elif os.path.exists(shown):
        os.remove(shown)


def do_apply(box):
    rc = _do_apply(box)
    password_changed(box)
    return rc


def _do_apply(box):
    ensure_host_keys(box)
    yml = os.path.join(box.boot, "coindojo.yml")
    uc = os.path.join(box.boot, "userconf.txt")
    try:
        resume_rename(box)
    except (RuntimeError, OSError) as e:
        scrub_boot_files(box, failure(e))
        hint(box, HINT + "\n  A user rename from an earlier boot could not finish: %s\n"
                         "  (%s names it; log in on the console to fix it, then delete that file).\n"
                         "  coindojo.yml was not applied; its secret lines were removed.\n" % (failure(e), RENAME_STATE))
        return 1
    err = os.path.join(box.boot, "coindojo.error.txt")
    stale = box.p("/run/coindojo/no-config")
    lost = lost_user(box)
    if lost:                                  # before anything else: nothing applies to a cabinet without its user
        scrub_boot_files(box, "")
        write(err, "Nothing was applied: %s\n" % lost)
        hint(box, LOST_BANNER + "\n  " + lost + "\n  (also in coindojo.error.txt on the boot partition)\n")
        return 1
    if os.path.exists(yml):
        text = open(yml, encoding="utf-8", errors="replace").read().lstrip(BOM)
        try:
            cfg = parse(text)
            apply_config(box, cfg, "coindojo.yml")
        except (ConfigError, RuntimeError, OSError) as e:
            e = failure(e)
            write(yml, scrub_text(text, e, template_comments(box)))         # never leave a secret behind, even in a broken file
            write(err, "coindojo.yml was not applied: %s\nEvery line that could hold a secret (password_hash, wifi_psk, "
                       "password, unknown keys) was removed; fix the file, fill them in again and reboot.\n"
                       "Settings applied before the error stay applied; the next good coindojo.yml finishes the rest.\n" % e)
            hint(box, HINT + "\n  coindojo.yml was not applied: %s\n  (details: coindojo.error.txt on the boot partition)\n" % e)
            return 1
        write(os.path.join(box.boot, "coindojo.applied.yml"),
              "# Applied at first boot on %s; secrets removed. Put a new coindojo.yml here to change settings.\n%s"
              % (time.strftime("%Y-%m-%d %H:%M"), scrub_text(text, "", template_comments(box))))
        os.remove(yml)
        for f in (err, stale):
            if os.path.exists(f):
                os.remove(f)
        say("coindojo.yml applied and removed (coindojo.applied.yml keeps the non-secret values)")
        return 0
    if os.path.exists(uc):
        try:
            line = open(uc, encoding="utf-8").read().strip().split("\n")[0]
            name, _, h = line.partition(":")
            cfg = validate({"user": name, "password_hash": h})
            apply_config(box, cfg, "userconf.txt")
        except (ConfigError, RuntimeError, OSError, UnicodeDecodeError) as e:
            if isinstance(e, OtherUser):         # a userconf.txt has no user: line to leave empty
                e = ("it names the user %s, but this image's user is %s (fixed when the image was built). Write "
                     "%s:<hash> in userconf.txt, or use coindojo.yml. Nothing was applied." % (e.want, e.have, e.have))
            else:
                e = "not valid UTF-8" if isinstance(e, UnicodeDecodeError) else failure(e)
            os.remove(uc)                        # it holds a hash: never leave it, applied or not
            write(err, "userconf.txt (Raspberry Pi Imager) was not applied: %s\nIt was removed (it holds a password "
                       "hash); put a fixed one back, or a coindojo.yml, and reboot.\n" % e)
            hint(box, HINT + "\n  userconf.txt (Raspberry Pi Imager) was not applied: %s\n"
                             "  (details: coindojo.error.txt on the boot partition)\n" % e)
            return 1
        os.remove(uc)
        for f in (err, stale):
            if os.path.exists(f):
                os.remove(f)
        say("Raspberry Pi Imager userconf.txt applied and removed")
        return 0
    # Imager's firstrun.sh is not a configuration of this image (#244: it renames the user; lost_user catches that).
    configured = os.path.exists(box.p("/var/lib/coindojo/applied"))
    if not configured:
        hint(box, HINT)
    elif os.path.exists(stale):
        os.remove(stale)
    return 0


def main(argv):
    if len(argv) >= 2 and argv[1] == "--check":
        # --user NAME (make image passes the image's user): a user: line naming anyone else is refused at build time.
        if len(argv) not in (3, 5) or (len(argv) == 5 and argv[3] != "--user"):
            print("usage: coindojo-firstboot --check FILE [--user NAME]", file=sys.stderr)
            return 2
        try:
            cfg = parse(open(argv[2], encoding="utf-8-sig").read())
            if len(argv) == 5 and cfg.get("user", argv[4]) != argv[4]:
                raise ConfigError(other_user_message(cfg["user"], argv[4]))
        except OSError as e:
            print("ERROR: cannot read %s: %s" % (argv[2], e.strerror), file=sys.stderr)
            return 1
        except ConfigError as e:
            print("ERROR: %s: %s" % (argv[2], e), file=sys.stderr)
            return 1
        print("OK: %s (%s)" % (argv[2], ", ".join(sorted(cfg)) or "empty"))
        return 0
    if len(argv) >= 2 and argv[1] == "--password-changed":
        # coindojo-password.service (PathChanged=/etc/shadow): refresh the reminder; never sudo (#270)
        root = argv[3] if len(argv) == 4 and argv[2] == "--root" else "/"
        if len(argv) not in (2, 4) or (len(argv) == 4 and argv[2] != "--root"):
            print("usage: coindojo-firstboot --password-changed [--root R]", file=sys.stderr)
            return 2
        password_changed(Box(root))
        return 0
    if len(argv) >= 2 and argv[1] == "--owner-access":
        opts = dict(zip(argv[2::2], argv[3::2]))
        if len(argv) % 2 or set(opts) - {"--user", "--root"} or "--user" not in opts:
            print("usage: coindojo-firstboot --owner-access --user NAME [--root R]", file=sys.stderr)
            return 2
        try:
            return owner_access(Box(opts.get("--root", "/")), opts["--user"])
        except (RuntimeError, OSError) as e:
            say("ERROR: %s" % failure(e))
            return 1
    if len(argv) >= 2 and argv[1] == "--sets-password":
        # exit 0: the config gives a password, 1: it does not, 2: malformed or unreadable (FILE `-` = stdin)
        if len(argv) != 3:
            print("usage: coindojo-firstboot --sets-password FILE|-", file=sys.stderr)
            return 2
        try:
            text = sys.stdin.read() if argv[2] == "-" else open(argv[2], encoding="utf-8-sig").read()
            return 0 if text_sets_password(text) else 1
        except (ConfigError, OSError, UnicodeDecodeError) as e:
            print("ERROR: %s" % (e if isinstance(e, ConfigError) else "cannot read the config"), file=sys.stderr)
            return 2
    if len(argv) >= 2 and argv[1] == "--default-login":
        opts = dict(zip(argv[2::2], argv[3::2]))
        if len(argv) % 2 or set(opts) - {"--user", "--edition", "--config", "--root"} \
                or not {"--user", "--edition"} <= set(opts):
            print("usage: coindojo-firstboot --default-login --user NAME --edition community|personal "
                  "[--config FILE] [--root R]", file=sys.stderr)
            return 2
        try:
            return set_default_login(Box(opts.get("--root", "/")), opts["--user"], opts["--edition"],
                                     opts.get("--config"))
        except (ConfigError, RuntimeError, OSError) as e:
            print("ERROR: %s" % failure(e), file=sys.stderr)
            return 1
    if len(argv) >= 2 and argv[1] == "--apply":
        root = argv[3] if len(argv) == 4 and argv[2] == "--root" else "/"
        try:
            return do_apply(Box(root))
        except (RuntimeError, OSError) as e:
            say("ERROR: %s" % failure(e))
            return 1
    print(__doc__.strip().split("\n\n")[0], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
