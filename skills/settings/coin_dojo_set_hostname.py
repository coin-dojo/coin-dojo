#!/usr/bin/python3 -I
"""Rename the cabinet on the network (root helper for the SETTINGS app, #34). Installed root:root 0755 as
/usr/local/sbin/coin-dojo-set-hostname; coindojo may run it with `sudo -n` and exactly one argument
(/etc/sudoers.d/coin-dojo-settings, regex-limited to the same RFC 1123 label checked below).

    coin-dojo-set-hostname NAME

1. NAME must be an RFC 1123 label: a-z 0-9 -, 1-63 characters, no '-' at either end (checked again here,
   whatever sudo allowed). Nothing else is accepted: no options, no second argument, no environment.
2. `hostnamectl set-hostname NAME` (static + transient name; hostnamed writes /etc/hostname).
3. /etc/hosts: every `127.0.1.1` line is replaced by ONE `127.0.1.1<TAB>NAME` line where the first one was
   (appended when there was none); every other line stays byte-identical. Written atomically, mode kept.
4. avahi-daemon is restarted if it runs, so NAME.local answers at once.
Nothing on the screen changes (ES-DE does not show the host name). Exit 0 ok, 2 bad name/usage, 1 failure.
"""
import os
import re
import subprocess
import sys
import tempfile

NAME_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")      # fullmatch: "$" would let "name\n" through
HOSTS = "/etc/hosts"
HOSTNAMECTL = "/usr/bin/hostnamectl"
SYSTEMCTL = "/usr/bin/systemctl"


def valid(name):
    return isinstance(name, str) and bool(NAME_RE.fullmatch(name))


def rewrite_hosts(text, name):
    """-> new /etc/hosts text: one 127.0.1.1 line naming NAME, everything else unchanged."""
    lines = text.splitlines(keepends=True)
    out, placed = [], False
    for ln in lines:
        f = ln.split()
        if f and f[0] == "127.0.1.1":
            if not placed:
                out.append("127.0.1.1\t%s\n" % name)
                placed = True
            continue
        out.append(ln)
    if not placed:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append("127.0.1.1\t%s\n" % name)
    return "".join(out)


def write_atomic(path, text):
    st = os.stat(path)
    fd, tmp = tempfile.mkstemp(prefix=".hosts.", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())                       # on disk before the rename (power cut safe)
        os.chmod(tmp, st.st_mode & 0o7777)
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except PermissionError:
            pass
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv, hosts=HOSTS, hostnamectl=HOSTNAMECTL, systemctl=SYSTEMCTL, run=subprocess.run):
    if len(argv) != 1 or not valid(argv[0]):
        print("usage: coin-dojo-set-hostname NAME   (a-z 0-9 -, 1-63, no '-' at either end)", file=sys.stderr)
        return 2
    name = argv[0]
    try:                                               # read + compute /etc/hosts first: a bad file stops us early
        with open(hosts, encoding="utf-8") as f:
            old = f.read()
        new = rewrite_hosts(old, name)
    except (OSError, ValueError) as e:
        print("cannot read %s: %s" % (hosts, e), file=sys.stderr)
        return 1
    r = run([hostnamectl, "set-hostname", name], capture_output=True, text=True)      # argv list, no shell
    if r.returncode != 0:
        print("hostnamectl failed: %s" % (r.stderr or r.stdout).strip(), file=sys.stderr)
        return 1
    try:
        if new != old:
            write_atomic(hosts, new)
    except (OSError, ValueError) as e:
        print("cannot update %s: %s" % (hosts, e), file=sys.stderr)
        return 1
    run([systemctl, "try-restart", "avahi-daemon.service"], capture_output=True, text=True)
    print("hostname set to %s (%s.local)" % (name, name))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
