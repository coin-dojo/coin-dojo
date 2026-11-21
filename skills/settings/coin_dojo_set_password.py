#!/usr/bin/python3 -I
"""Change the cabinet password (root helper for the SETTINGS app's PASSWORD page, #271). Installed root:root 0755 as
/usr/local/sbin/coin-dojo-set-password; the cabinet user may run it with `sudo -n` and NO arguments
(/etc/sudoers.d/coin-dojo-settings, the empty-argument form `"" `), the new password is the one line on STDIN.

    printf '%s\\n' "$PASSWORD" | sudo -n /usr/local/sbin/coin-dojo-set-password

The password never appears in argv (so not in `ps`, sudo's log or the journal), is never written to a file, never
printed, and is not in any error text (messages name the rule that failed, never the value; a crash prints one
generic line, no traceback; core dumps are off).

1. NO arguments, running as root, called through sudo by a real login user: SUDO_UID names a user with uid >= 1000
   whose login name is SUDO_USER and whose shell is a real one. root and system users are refused. The password
   that changes is that user's own (the cabinet user), never another account's.
   ADMIN IS ON (#270): when that user is an admin (in the sudo group, or /etc/sudoers.d/coindojo-admin exists), the
   login password is also the sudo password, and this NOPASSWD helper would let any program running as the user
   replace it without knowing it (a route to root). Then it refuses with exit 2, runs nothing, and says
   "ADMIN IS ON: CHANGE IT WITH passwd" (passwd asks for the current password). Since #299 the FIRST change through
   this helper is what turns admin on (step 4), so the helper sets the owner's password once; every later change is
   `passwd` (over SSH or the console), which asks for the current one. The intent is unchanged: no change of an
   admin's password without knowing it.
2. stdin: exactly one line (the final newline is dropped), 6 to 128 printable ASCII characters (space included),
   and not 12345. Empty input, a second line, control characters, longer input: refused with exit 2.
3. One audit line goes to the system log (`logger -p auth.notice -t coin-dojo-set-password`): "password changed via
   SETTINGS for USER", or one failure line naming the step; never the value. Refusals before any change log nothing.
   Both steps run under an exclusive flock on /run/coindojo/password.lock (a root directory, not /run/lock), the
   same lock role share's
   coin-dojo-share-sync takes before it reads anything (#270 M2: the sync could otherwise read the old Samba entry,
   let this helper set the new one, then delete it). Samba first: only when Samba is installed (smbpasswd and
   /etc/samba/smb.conf exist), `smbpasswd -s -a USER` sets the network folder's password; without Samba that step is
   skipped and said so. Then `chpasswd` sets the Linux password (the pipe, no argv, a fixed environment), so the
   /etc/shadow change that starts the sync comes after the Samba entry is already the new one. A failed Samba step
   still sets the Linux password and exits 3; a failed chpasswd exits 1.
4. #299 (the maintainer 2026-11-20: owners debug without making an SSH key): after chpasswd succeeded, still under the lock,
   `coindojo-firstboot --owner-access --user USER` (the image's firstboot script, run with /usr/bin/python3 -I) turns
   the owner's access on: the #270 admin grant (sudoers.d/coindojo-admin) and password SSH for USER only (a Match User
   sshd drop-in, checked with sshd -t, removed again on failure). It re-checks on its own that the password in
   /etc/shadow is no longer 12345 and that the grant is pending (a community image), else it changes nothing. One
   stdout line says the outcome (`admin and SSH: on`, `... skipped ...` or `... off (...)` on stderr); it never
   changes the exit code: the password did change. Without that script (a VM, a deployed cabinet) it is skipped.
Exit: 0 ok, 2 refused (bad caller, admin on, usage or password), 1 failure (or the lock is busy), 3 Linux ok but
the network folder failed.
Stdout: one line per step, e.g. `linux: ok` and `network folder: ok` or `network folder: skipped (Samba not installed)`.
"""
import errno
import fcntl
import grp
import os
import pwd
import resource
import stat
import subprocess
import sys
import time

CHPASSWD = "/usr/sbin/chpasswd"
LOGGER = "/usr/bin/logger"
SMBPASSWD = "/usr/bin/smbpasswd"
SMBCONF = "/etc/samba/smb.conf"
MIN_LEN, MAX_LEN = 6, 128
DEFAULT_PASSWORD = "12345"          # the default login of a fresh card: never accepted as the new one
MIN_UID = 1000
FIXED_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
BAD_SHELLS = ("nologin", "false", "")
ADMIN_RULE = "/etc/sudoers.d/coindojo-admin"     # what coindojo-firstboot's grant_sudo writes (#270)
SUDO_GROUP = "sudo"
ADMIN_ON = "ADMIN IS ON: CHANGE IT WITH passwd"  # the PASSWORD page shows this line as it is
LOCK = "/run/coindojo/password.lock"             # shared with role share's coin-dojo-share-sync (#270 M2); its dir
                                                  # is root's, never world-writable like /run/lock or /tmp (N2)
LOCK_WAIT = 30                                    # seconds; the sync takes well under one
PYTHON = "/usr/bin/python3"
FIRSTBOOT = "/usr/local/sbin/coindojo-firstboot"  # the image's script (skills/build-image): owner access, #299


class LockBusy(Exception):
    pass


def is_admin(user, getpwnam, getgrnam, admin_rule):
    """True when USER has sudo: the admin rule exists, or USER is in the sudo group (listed, or as primary group)."""
    if admin_rule(ADMIN_RULE):
        return True
    try:
        g = getgrnam(SUDO_GROUP)
    except KeyError:
        return False
    if user in g.gr_mem:
        return True
    try:
        return getpwnam(user).pw_gid == g.gr_gid
    except KeyError:
        return False


def take_lock(path, wait=LOCK_WAIT, sleep=time.sleep, clock=time.monotonic):
    """-> an fd holding an exclusive flock on PATH. Never follows a link (O_NOFOLLOW); the file must be a regular
    file of our own uid (root on the cabinet) in a directory of our own uid that nobody else may write (made 0755
    on demand; /run/coindojo also holds the tty1 reminder the user reads, so not 0700), else LockBusy. Waits up to
    WAIT seconds."""
    parent = os.path.dirname(path)
    try:
        os.mkdir(parent, 0o755)
        os.chmod(parent, 0o755)                         # not the helper's umask 077: tty1 reads the reminder there
    except FileExistsError:
        pass
    except OSError as e:
        raise LockBusy("cannot make the lock directory (%s)" % (errno.errorcode.get(e.errno, "error"))) from None
    pst = os.lstat(parent)
    if not stat.S_ISDIR(pst.st_mode) or pst.st_uid != os.geteuid() or pst.st_mode & 0o022:
        raise LockBusy("the lock directory is not root's alone")
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), 0o600)
    except OSError as e:
        raise LockBusy("cannot open the lock file (%s)" % (errno.errorcode.get(e.errno, "error"))) from None
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid():
        os.close(fd)
        raise LockBusy("the lock file is not root's")
    end = clock() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if clock() >= end:
                os.close(fd)
                raise LockBusy("busy: the network folder sync or another change is running") from None
            sleep(0.1)


def password_error(pw):
    """None when PW may be set, else a short reason (never the value)."""
    if not pw:
        return "empty password"
    if pw == DEFAULT_PASSWORD:
        return "that is the default password"
    if len(pw) < MIN_LEN:
        return "too short (at least %d characters)" % MIN_LEN
    if len(pw) > MAX_LEN:
        return "too long (at most %d characters)" % MAX_LEN
    if any(not (32 <= ord(c) <= 126) for c in pw):
        return "only printable ASCII characters (letters, digits, symbols, space)"
    return None


def read_password(raw):
    """-> (password, None) from the raw stdin bytes, or (None, reason). One line; the final newline is dropped."""
    if len(raw) > MAX_LEN + 3:
        return None, "too long (at most %d characters)" % MAX_LEN
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return None, "only printable ASCII characters (letters, digits, symbols, space)"
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    if "\n" in text or "\r" in text or "\0" in text:
        return None, "exactly one line, please"
    return text, None


def caller(env, geteuid, getpwuid):
    """-> (login name, None) of the user who ran us through sudo, or (None, reason)."""
    if geteuid() != 0:
        return None, "must run as root through sudo"
    uid_s, name = env.get("SUDO_UID", ""), env.get("SUDO_USER", "")
    if not uid_s.isdigit() or not name:
        return None, "must be run through sudo by a login user"
    uid = int(uid_s)
    if uid < MIN_UID:
        return None, "root and system accounts are refused"
    try:
        ent = getpwuid(uid)
    except KeyError:
        return None, "unknown user"
    shell = os.path.basename(ent.pw_shell or "")
    if ent.pw_name != name or shell in BAD_SHELLS:
        return None, "not a login user"
    return ent.pw_name, None


def run_with_input(run, argv, data):
    return run(argv, input=data, capture_output=True, text=True, env=FIXED_ENV, timeout=60)


def audit(run, text):
    """One line to the system log (never the password; a missing logger is not an error)."""
    try:
        run([LOGGER, "-p", "auth.notice", "-t", "coin-dojo-set-password", text], capture_output=True, text=True,
            env=FIXED_ENV, timeout=10)
    except Exception:  # noqa: BLE001 - the audit line must never break or reveal the change
        pass


def clean(text, pw):
    """Last non-empty line of a tool's output with the password (if it echoed it) blanked out."""
    lines = [ln for ln in (text or "").replace(pw, "***").splitlines() if ln.strip()]
    return lines[-1].strip()[:200] if lines else ""


def main(argv, stdin=None, env=None, geteuid=os.geteuid, getpwuid=pwd.getpwuid, run=subprocess.run,
         exists=os.path.exists, out=None, err=None, getpwnam=pwd.getpwnam, getgrnam=grp.getgrnam,
         admin_rule=os.path.lexists, lock_path=LOCK, lock_wait=LOCK_WAIT, firstboot=FIRSTBOOT, fb_exists=os.path.exists):
    out = out or sys.stdout
    err = err or sys.stderr
    stdin = stdin if stdin is not None else sys.stdin.buffer
    env = os.environ if env is None else env
    if argv:
        print("usage: coin-dojo-set-password   (no arguments; the new password is the one line on stdin)", file=err)
        return 2
    user, why = caller(env, geteuid, getpwuid)
    if why:
        print("refused: %s" % why, file=err)
        return 2
    if is_admin(user, getpwnam, getgrnam, admin_rule):
        print(ADMIN_ON, file=err)
        return 2
    pw, why = read_password(stdin.read(MAX_LEN + 4))
    if why is None:
        why = password_error(pw)
    if why:
        print("refused: %s" % why, file=err)
        return 2
    try:
        fd = take_lock(lock_path, lock_wait)
    except LockBusy as e:
        print("refused: %s" % e, file=err)
        return 1
    try:
        rc = change(run, exists, user, pw, out, err)
        if rc in (0, 3):                                # the Linux password is the owner's own now
            owner_access(run, user, firstboot, fb_exists, out, err)
        return rc
    finally:
        os.close(fd)


def change(run, exists, user, pw, out, err):
    """Samba first, then Linux (the caller holds the lock)."""
    folder = None                                       # None: skipped, True: ok, int: smbpasswd exit
    if exists(SMBPASSWD) and exists(SMBCONF):
        # -a on purpose: it also ADDS the Samba user when there is none, so a person can switch network folder access
        # on with their own password (the folder is off for a user until a password is set, #271).
        r = run_with_input(run, [SMBPASSWD, "-s", "-a", user], "%s\n%s\n" % (pw, pw))
        folder = True if r.returncode == 0 else r.returncode
    r = run_with_input(run, [CHPASSWD], "%s:%s\n" % (user, pw))
    if r.returncode != 0:
        print("chpasswd failed (exit %d)%s" % (r.returncode, ": " + clean(r.stderr or r.stdout, pw) if (r.stderr or r.stdout) else ""),
              file=err)
        audit(run, "password change FAILED via SETTINGS for %s (chpasswd exit %d%s)"
              % (user, r.returncode, ", network folder already changed" if folder is True else ""))
        return 1
    print("linux: ok", file=out)
    if folder is None:
        print("network folder: skipped (Samba not installed)", file=out)
    elif folder is True:
        print("network folder: ok", file=out)
    else:
        print("network folder: FAILED (smbpasswd exit %d)" % folder, file=err)
        audit(run, "password changed via SETTINGS for %s, network folder FAILED (smbpasswd exit %d)" % (user, folder))
        return 3
    audit(run, "password changed via SETTINGS for %s" % user)
    return 0


def owner_access(run, user, firstboot, fb_exists, out, err):
    """Step 4 (#299): admin and password SSH on, decided and done by coindojo-firstboot --owner-access. One line."""
    if not fb_exists(firstboot):
        print("admin and SSH: skipped (no coindojo-firstboot on this system)", file=out)
        return
    try:
        r = run([PYTHON, "-I", "-B", firstboot, "--owner-access", "--user", user], capture_output=True, text=True,
                env=FIXED_ENV, timeout=60)
    except Exception as e:  # noqa: BLE001 - the password did change; say why access stayed off, nothing more
        print("admin and SSH: off (%s)" % type(e).__name__, file=err)
        return
    if r.returncode == 0:
        print("admin and SSH: on", file=out)
        audit(run, "admin and password SSH on for %s (own password set via SETTINGS)" % user)
    elif r.returncode == 3:
        print("admin and SSH: skipped (nothing pending)", file=out)
    else:
        why = [ln for ln in (r.stdout or "").splitlines() if "ERROR" in ln or "refused" in ln]
        print("admin and SSH: off (%s)" % (why[-1].split(": ", 1)[-1][:160] if why else "exit %d" % r.returncode),
              file=err)


def _safe_main():
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))       # a crash leaves no core file holding the password
        os.umask(0o077)
        return main(sys.argv[1:])
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001 - one generic line: no traceback, the value cannot leak
        print("unexpected error (%s)" % type(e).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_safe_main())
