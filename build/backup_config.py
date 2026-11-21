#!/usr/bin/env python3
"""backup-config (#286): copy the cabinet's hand-tuned config into personal/backup/<host>/, secrets redacted.

    make backup-config            dry run: pull, redact, scan, print what would change (nothing written)
    make backup-config APPLY=1    the same, then write personal/backup/<host>/ (never commits)

READ-ONLY on the cabinet: one ssh call runs `find ... | tar -cf -` there and streams the archive to stdout; nothing
is written, nothing launched, no sudo. Steps on this machine:
  1. Unpack into a fresh temp dir. Only regular files whose home-relative path is on the fixed ALLOWLIST below are
     kept: symlinks, hard links, devices, absolute or `..` names, unlisted paths, backups (`*.bak*`, `*~`), logs,
     trophies-key.json, files over 1 MiB and binary files (a NUL byte, or not UTF-8) are dropped and counted, and so
     is a file of 64+ bytes identical to a tracked file outside personal/ ("in-repo": the deploy put it there, and
     the community export's dropped-file check would flag the copy).
  2. Redact in the temp dir: in `key = value` lines (retroarch.cfg, core options, remaps, flycast emu.cfg), XML
     `name="..." value="..."` settings (ES-DE es_settings.xml) and JSON `"key": "value"` pairs, the value of every key
     whose name says password/passwd/passkey/passphrase/token/secret/apikey/stream key/private key/credential, and of
     the credential user names (cheevos_username, webdav_username, UserName, ES-DE Scraper*Username*), becomes
     REDACTED. So does discord_app_id: RetroArch's stock public id, no secret, but gitleaks' discord-client-id rule
     (`make secrets`) flags it, so it never reaches git (a restore drops the line and RetroArch uses its default).
     Empty values and true/false are left alone.
     Also redacted (#286 M1): keys naming nickname/ssid/psk/access key, the addresses netplay_ip_address,
     netplay_custom_mitm_server, cheevos_custom_host and every *_url unless empty or RetroArch's stock value, and the
     user info of any URL (scheme://user:pass@ -> scheme://REDACTED@).
  3. Final scan of the staged files: a secret-named key with a value that is not REDACTED, a PEM private key, a
     token-shaped string (20+ characters of letters, digits, + and = in one unbroken run, with upper, lower and
     digits), a redacted
     value that still appears anywhere, or an excluded file name FAILS the run: nothing is written. A second net
     that does not rely on key names (M2) fails closed on: an IPv4 address outside 127/8, a MAC address, an email
     address, URL user info, 32+ hex digits (except the SDL GUID opening an es_controllers.txt mapping line), a JWT or
     base64url run, and a 16+ letter/digit run under a user/nick/login/auth/server/host/address key.
     Limits (L3-L5): tar member names with control characters are dropped; more than 64 MiB from the cabinet or
     more than 20,000 members, a member that collides with another and a write error fail with a FAIL line.
  4. Diff against personal/backup/<host>/ (added, changed, removed) and, with --apply, mirror the staged tree there
     (files 0644, removed files deleted, empty folders pruned; a symlink in the destination is replaced, never
     followed).
Output: `backup-config:` lines on stdout, WARN/FAIL lines, one final `RESULT: PASS|FAIL`. Exit 0 ok, 1 refused or
failed, 2 setup error. V=1 lists every added file. ES-DE's es_settings.xml is backed up redacted but the
ship-no-artifacts gate (tests/test_ship_no_artifacts.py, #42) refuses that file name in git: a WARN says to keep it
out of the commit. Secret VALUES are never printed, only key names and counts.

Env (tests and one-off overrides): BACKUP_CONFIG_SSH = the ssh command (the remote script is its last argument;
default `ssh -i hk_ssh_key -o BatchMode=yes -o ConnectTimeout=10 hk_ssh_user@hk_cabinet_host` from the config),
BACKUP_CONFIG_HOSTNAME (default hk_cabinet_hostname), BACKUP_CONFIG_ROOT = another repo root.
"""
import fnmatch
import hashlib
import io
import os
import pathlib
import posixpath
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = pathlib.Path(os.environ.get("BACKUP_CONFIG_ROOT") or HERE.parent)
sys.path.insert(0, str(HERE.parent / "tools"))
import hkconfig  # noqa: E402  (tools/hkconfig.py: the one way a script reads the config)

TAG = "backup-config:"
REDACTED = "REDACTED"
MAX_BYTES = 1 << 20

# Home-relative paths on the cabinet: (path, None) = that one file; (path, globs) = files under that folder whose name
# matches one of the globs. Nothing else is ever copied.
ALLOWLIST = (
    (".config/retroarch/retroarch.cfg", None),
    (".config/retroarch/config", ("*.cfg", "*.opt", "*.rmp", "*.glslp", "*.slangp")),   # overrides, core options, remaps
    (".config/retroarch/autoconfig", ("*.cfg",)),
    (".config/flycast/emu.cfg", None),
    (".config/flycast/mappings", ("*.cfg",)),
    ("ES-DE/settings", ("*.xml", "*.txt")),
    ("ES-DE/collections", ("*.cfg",)),
    ("ES-DE/custom_systems", ("*.xml",)),
    (".config/coin-dojo", ("*.json",)),
)
EXCLUDED_NAMES = ("*~", "*.bak", "*.bak*", "*.log", "trophies-key.json")
# File names the repo's ship-no-artifacts gate refuses in git whatever their content (#42): written, never committed.
GATE_REFUSED = ("es_settings.xml*",)
HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

SECRET_NAME = re.compile(r"(?i)(password|passwd|passkey|passphrase|token|secret|api_?key|stream_?key|private_?key|credential"
                         r"|nickname|ssid|psk|access_?key)")
CRED_USER = re.compile(r"(?i)^(cheevos_username|webdav_username|username|user_name|scraper\w*username\w*|discord_app_id)$")
SAFE_VALUES = {"", REDACTED.lower(), "true", "false"}
# Addresses that point at the maintainer's own hosts: redacted unless empty or the stock value RetroArch writes itself (#286 M1).
NET_NAME = re.compile(r"(?i)^(netplay_ip_address|netplay_custom_mitm_server|cheevos_custom_host|\w*_url)$")
STOCK_NET_VALUES = {"http://localhost:4404/", "http://buildbot.libretro.com/assets/", "https://buildbot.libretro.com/assets/",
                    "http://buildbot.libretro.com/nightly/", "https://buildbot.libretro.com/nightly/"}
USERINFO_RE = re.compile(r"(?P<pre>\b[A-Za-z][A-Za-z0-9+.-]*://)(?P<info>[^/\s@\"'<>]+)@")

# key: anything up to the first = (FBNeo dipswitch keys carry commas), not starting like XML, JSON, a section or a comment
INI_RE = re.compile(r'^(?P<pre>[ \t]*(?P<key>[^\s=#;"\'<>{}\[\]][^=\n"]*?)[ \t]*=[ \t]*)(?P<val>.*?)(?P<post>[ \t]*)$')
XML_RE = re.compile(r'(?P<pre>\bname\s*=\s*"(?P<key>[^"]*)"\s+value\s*=\s*")(?P<val>[^"]*)(?P<post>")')
JSON_RE = re.compile(r'(?P<pre>"(?P<key>[^"\\]+)"\s*:\s*")(?P<val>(?:[^"\\]|\\.)*)(?P<post>")')
PEM_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
TOKEN_RE = re.compile(r"(?<![A-Za-z0-9+=])[A-Za-z0-9+=]{20,}(?![A-Za-z0-9+=])")   # one unbroken run: paths and option keys split on / - _ .
SAME_MIN = 64         # tools/edition_export.py PRIVATE_SHA_MIN: a backup file of 64+ bytes with a public file's bytes
#                       is a dropped-file hit in the community export, so it is skipped (the deploy ships it anyway)
MAX_STDOUT = 64 << 20  # L4: the archive the cabinet sends; more = refused, nothing staged
MAX_MEMBERS = 20000    # L4: tar members
# M2: the final scan's second net, independent of the redaction rules above. Each pattern is a hit unless the
# allowance below names it exactly.
IPV4_RE = re.compile(r"(?<![0-9.])((?:25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])(?:\.(?:25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])){3})(?![0-9.])")
MAC_RE = re.compile(r"(?<![0-9A-Fa-f:-])[0-9A-Fa-f]{2}([:-])[0-9A-Fa-f]{2}(?:\1[0-9A-Fa-f]{2}){4}(?![0-9A-Fa-f:-])")
EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+/-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
HEX32_RE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32,}(?![0-9A-Fa-f])")
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*")
B64URL_RE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])")
IDENT_KEY = re.compile(r"(?i)(user|nick|login|auth|server|host|address)")
IDENT_VAL = re.compile(r"[A-Za-z0-9]{16,}")
# The one known 32-hex: the SDL controller GUID that starts each mapping line of ES-DE's es_controllers.txt.
SDL_GUID_LINE = re.compile(r"^[0-9a-f]{32},[^,]*,(?:[a-z0-9]+:[a-z0-9.+-]*,)*platform:[A-Za-z ]+,?$")
LEAK_MIN = 6         # a redacted value this long or longer must not appear anywhere in the staged files


def log(msg):
    print(TAG, msg, flush=True)


def warn(msg):
    print("WARN", TAG, msg, flush=True)


def fail(msg):
    print("FAIL", TAG, msg, flush=True)


# ---------------------------------------------------------------- the allowlist

def safe_rel(name):
    """The member name as a clean home-relative posix path, or None when it is absolute or leaves the tree."""
    if not name or name.startswith("/") or "\\" in name:
        return None
    rel = posixpath.normpath(name)
    while rel.startswith("./"):
        rel = rel[2:]
    if rel in (".", "") or rel == ".." or rel.startswith("../") or "/../" in "/" + rel + "/":
        return None
    return rel


def excluded_name(rel):
    base = posixpath.basename(rel)
    return any(fnmatch.fnmatchcase(base, g) for g in EXCLUDED_NAMES)


def allowed(rel):
    """True when rel (a clean home-relative path) is on the allowlist and not an excluded name."""
    if rel is None or excluded_name(rel):
        return False
    for path, globs in ALLOWLIST:
        if globs is None:
            if rel == path:
                return True
        elif rel.startswith(path + "/") and any(fnmatch.fnmatchcase(posixpath.basename(rel), g) for g in globs):
            return True
    return False


def remote_script():
    """The sh script run on the cabinet: list the allowlisted files (regular files only) and tar them to stdout."""
    roots = " ".join(shlex.quote(p) for p, _g in ALLOWLIST)
    globs = sorted({g for _p, gs in ALLOWLIST for g in (gs or ())} | {posixpath.basename(p) for p, g in ALLOWLIST if g is None})
    names = " -o ".join("-name %s" % shlex.quote(g) for g in globs)
    skip = " ".join("! -name %s" % shlex.quote(g) for g in EXCLUDED_NAMES)
    return ("cd || exit 3; set --; "
            "for p in %s; do if [ -e \"$p\" ]; then set -- \"$@\" \"$p\"; "
            "else echo \"backup-config: not on the cabinet: $p\" >&2; fi; done; "
            "[ $# -gt 0 ] || exit 3; "
            "find \"$@\" -type f \\( %s \\) %s -print0 | tar --null -T - -cf -" % (roots, names, skip))


# ---------------------------------------------------------------- pull + stage

def is_binary(data):
    if b"\0" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


class StageError(Exception):
    """The archive cannot be staged safely (too many members, a colliding name): the run fails, nothing is written."""


def control_chars(name):
    return any(ord(c) < 0x20 or ord(c) == 0x7f for c in name)


def stage(archive, dest, max_members=None):
    """Unpack the allowlisted regular files of a tar archive (bytes) into the fresh dir dest.
    Returns ({rel: bytes written}, {reason: [rel]}) of kept and dropped members. Raises StageError past
    max_members (default MAX_MEMBERS) or on a member that collides with one already staged."""
    kept, dropped = {}, {}
    limit = MAX_MEMBERS if max_members is None else max_members

    def drop(reason, name):
        dropped.setdefault(reason, []).append(name)

    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tf:
        count = 0
        for m in tf:
            count += 1
            if count > limit:
                raise StageError("more than %d tar members" % limit)
            if control_chars(m.name) or control_chars(m.linkname or ""):
                drop("control-char", m.name.encode("unicode_escape").decode("ascii"))
                continue
            if m.isdir():
                continue
            rel = safe_rel(m.name)
            if m.issym() or m.islnk():
                drop("link", m.name)
                continue
            if not m.isfile():
                drop("special", m.name)
                continue
            if rel is None:
                drop("unsafe-path", m.name)
                continue
            if not allowed(rel):
                drop("unlisted", rel)
                continue
            if m.size > MAX_BYTES:
                drop("too-big", rel)
                continue
            data = tf.extractfile(m).read()
            if is_binary(data):
                drop("binary", rel)
                continue
            target = dest / rel
            # dest is a fresh dir we created; refuse anything that would resolve outside it all the same
            try:
                for parent in reversed(target.relative_to(dest).parents):
                    p = dest / parent
                    if p.is_symlink():
                        raise StageError("symlink inside the staging dir: %s" % parent)
                    p.mkdir(mode=0o700, exist_ok=True)
                if not str(target.resolve()).startswith(str(dest.resolve()) + os.sep):
                    drop("unsafe-path", rel)
                    continue
                fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            except OSError as e:     # L5: the same name twice, or a file where a folder is (or the other way round)
                raise StageError("member %s collides with one already staged (%s)" % (rel, e.__class__.__name__))
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            kept[rel] = data
    return kept, dropped


# ---------------------------------------------------------------- redaction + scan

def secret_name(key):
    return bool(SECRET_NAME.search(key) or CRED_USER.match(key))


def needs_redaction(key, val):
    v = val.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        v = v[1:-1]
    if v.lower() in SAFE_VALUES:
        return False
    return secret_name(key) or (bool(NET_NAME.match(key)) and v not in STOCK_NET_VALUES)


def _inner(val):
    v = val.strip()
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] == '"' else v


def redact_text(text):
    """(new text, [(key, original value)]) with every secret value replaced by REDACTED, formats as in the doc."""
    found = []

    def xml_or_json(m):
        if needs_redaction(m.group("key"), m.group("val")):
            found.append((m.group("key"), m.group("val")))
            return m.group("pre") + REDACTED + m.group("post")
        return m.group(0)

    out = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        end = line[len(body):]
        m = INI_RE.match(body)
        if m and needs_redaction(m.group("key"), m.group("val")):
            val = m.group("val").strip()
            found.append((m.group("key"), _inner(val)))
            new = '"%s"' % REDACTED if val.startswith('"') else REDACTED
            body = m.group("pre") + new + m.group("post")
        body = XML_RE.sub(xml_or_json, body)
        body = JSON_RE.sub(xml_or_json, body)

        def userinfo(u):
            if u.group("info") == REDACTED:
                return u.group(0)
            found.append(("url userinfo", u.group("info")))
            return u.group("pre") + REDACTED + "@"

        body = USERINFO_RE.sub(userinfo, body)
        out.append(body + end)
    return "".join(out), found


def scan_text(rel, text, leaked=()):
    """[(line, reason)] of what still looks like a secret in one staged file. Reasons name the key, never a value."""
    hits = []
    if excluded_name(rel):
        hits.append((0, "excluded file name"))
    for ln, line in enumerate(text.splitlines(), 1):
        values = []           # the token check looks at values only (key names are often CamelCase with digits)
        m = INI_RE.match(line)
        if m:
            values.append(m.group("val"))
            if needs_redaction(m.group("key"), m.group("val")) and _inner(m.group("val")) != REDACTED:
                hits.append((ln, "secret key %s has a value" % m.group("key")))
        for rx in (XML_RE, JSON_RE):
            for m in rx.finditer(line):
                values.append(m.group("val"))
                if needs_redaction(m.group("key"), m.group("val")) and m.group("val") != REDACTED:
                    hits.append((ln, "secret key %s has a value" % m.group("key")))
        if PEM_RE.search(line):
            hits.append((ln, "private key block"))
        for chunk in values or [line]:
            for t in TOKEN_RE.finditer(chunk):
                s = t.group(0)
                if re.search(r"[a-z]", s) and re.search(r"[A-Z]", s) and re.search(r"[0-9]", s):
                    hits.append((ln, "token-shaped string (%d chars)" % len(s)))
        for value in leaked:
            if value in line:
                hits.append((ln, "a redacted value appears here"))
                break
        hits.extend((ln, why) for why in second_net(rel, line, values))
    return hits


def ident_pairs(line):
    """(key, value) of every key = value / name+value / "key": "value" pair on the line."""
    m = INI_RE.match(line)
    pairs = [(m.group("key"), _inner(m.group("val")))] if m else []
    for rx in (XML_RE, JSON_RE):
        pairs += [(m.group("key"), m.group("val")) for m in rx.finditer(line)]
    return pairs


def second_net(rel, line, values=()):
    """M2: shapes that are a hit whatever key they sit under, so a gap in the redaction rules fails closed.
    Reasons name the kind, never the value."""
    hits = []
    for m in IPV4_RE.finditer(line):
        if not m.group(1).startswith("127."):
            hits.append("IPv4 address outside loopback")
    if MAC_RE.search(line):
        hits.append("MAC address")
    if EMAIL_RE.search(line):
        hits.append("email address")
    for u in USERINFO_RE.finditer(line):
        if u.group("info") != REDACTED:
            hits.append("URL user info")
    sdl = posixpath.basename(rel) == "es_controllers.txt" and SDL_GUID_LINE.match(line)
    for m in HEX32_RE.finditer(line):
        if not (sdl and m.start() == 0 and m.end() == 32):
            hits.append("32+ hex digits")
    if JWT_RE.search(line):
        hits.append("JWT")
    for chunk in values or [line]:     # values only: option key names are long CamelCase/dash words with digits
        for m in B64URL_RE.finditer(chunk):
            s = m.group(0)
            if re.search(r"[a-z]", s) and re.search(r"[A-Z]", s) and re.search(r"[0-9]", s):
                hits.append("base64url run (%d chars)" % len(s))
    for key, val in ident_pairs(line):
        if IDENT_KEY.search(key) and val != REDACTED and IDENT_VAL.search(val):
            hits.append("16+ letters/digits under %s" % key)
    return hits


def public_shas(root):
    """sha256 of every tracked regular file outside personal/ (git ls-files; none when root is no git repo)."""
    try:
        r = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return set()
    out = set()
    for rel in r.stdout.decode("utf-8", "surrogateescape").split("\0") if r.returncode == 0 else []:
        p = pathlib.Path(root) / rel
        if rel and not rel.startswith("personal/") and not p.is_symlink() and p.is_file():
            out.add(hashlib.sha256(p.read_bytes()).hexdigest())
    return out


def redact_tree(dest, kept):
    """Redact every staged file in place. Returns ({rel: [key, ...]}, set of original secret values)."""
    report, values = {}, set()
    for rel in sorted(kept):
        p = dest / rel
        text = p.read_bytes().decode("utf-8")
        new, found = redact_text(text)
        if found:
            p.write_bytes(new.encode("utf-8"))
            report[rel] = [k for k, _v in found]
            values |= {v for _k, v in found if len(v) >= LEAK_MIN}
    return report, values


def scan_tree(dest, leaked=()):
    """[(rel, line, reason)] over every file under dest (os.walk, links never followed; a non-regular entry is a hit)."""
    hits = []
    for dirpath, dirnames, filenames in os.walk(dest):
        for d in list(dirnames):
            if os.path.islink(os.path.join(dirpath, d)):
                hits.append((os.path.relpath(os.path.join(dirpath, d), dest), 0, "symlink"))
                dirnames.remove(d)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, dest)
            st = os.lstat(full)
            if not stat.S_ISREG(st.st_mode):
                hits.append((rel, 0, "not a regular file"))
                continue
            data = open(full, "rb").read()
            if is_binary(data):
                hits.append((rel, 0, "binary file"))
                continue
            hits.extend((rel, ln, why) for ln, why in scan_text(rel, data.decode("utf-8"), leaked))
    return hits


# ---------------------------------------------------------------- diff + write

def tree_files(base):
    """{rel: bytes or None} of what is under base; None = not a regular file (a symlink: replaced, never followed)."""
    out = {}
    if not base.exists() and not base.is_symlink():
        return out
    for dirpath, dirnames, filenames in os.walk(base):
        for d in list(dirnames):
            if os.path.islink(os.path.join(dirpath, d)):
                out[os.path.relpath(os.path.join(dirpath, d), base)] = None
                dirnames.remove(d)
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = pathlib.Path(os.path.relpath(full, base)).as_posix()
            out[rel] = open(full, "rb").read() if stat.S_ISREG(os.lstat(full).st_mode) else None
    return out


def diff(staged, current):
    added = sorted(r for r in staged if r not in current)
    changed = sorted(r for r in staged if r in current and current[r] != staged[r])
    removed = sorted(r for r in current if r not in staged)
    same = len(staged) - len(added) - len(changed)
    return added, changed, removed, same


def write_tree(base, staged, removed):
    """Mirror staged ({rel: bytes}) into base: remove `removed`, write added/changed files (0644), prune empty dirs."""
    base.mkdir(parents=True, exist_ok=True)
    for rel in removed:
        p = base / rel
        if p.is_symlink() or p.is_file():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p)
    for rel, data in staged.items():
        target = base / rel
        cur = base
        for part in pathlib.PurePosixPath(rel).parts[:-1]:
            cur = cur / part
            if cur.is_symlink() or (cur.exists() and not cur.is_dir()):
                cur.unlink()
            cur.mkdir(exist_ok=True)
        if target.is_symlink():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        if target.is_file() and target.read_bytes() == data:
            continue
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".backup-config-")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    for dirpath, _d, _f in sorted(os.walk(base), key=lambda t: -len(t[0])):
        if dirpath != str(base) and not os.listdir(dirpath):
            os.rmdir(dirpath)


# ---------------------------------------------------------------- main

def settings():
    """(ssh argv, hostname) from the env overrides or the config. SystemExit(2) with a message when unset."""
    try:
        ssh = os.environ.get("BACKUP_CONFIG_SSH", "").strip()
        if ssh:
            ssh = shlex.split(ssh)
        else:
            host = hkconfig.require(hkconfig.get("hk_cabinet_host"), "backup-config: the cabinet", "hk_cabinet_host")
            user, key = hkconfig.get("hk_ssh_user"), hkconfig.get("hk_ssh_key")
            ssh = ["ssh"] + (["-i", key] if key else []) + ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                                                             "%s@%s" % (user, host) if user else host]
        name = hkconfig.first("BACKUP_CONFIG_HOSTNAME", "hk_cabinet_hostname")
    except hkconfig.ConfigUnavailable as e:
        print("%s cannot read the configuration: %s (make config-check)" % (TAG, e), file=sys.stderr)
        raise SystemExit(2)
    if not HOSTNAME_RE.match(name or ""):
        print("%s hk_cabinet_hostname %r is not a host name (one RFC 1123 label)" % (TAG, name), file=sys.stderr)
        raise SystemExit(2)
    return ssh, name


def pull(argv, cap=None):
    """Run argv; (exit code, stdout bytes or None when it passed the cap, stderr bytes). Reads stdout in chunks so a
    runaway cabinet side never fills this machine's memory (L4); stderr goes to a temp file."""
    cap = MAX_STDOUT if cap is None else cap
    with tempfile.TemporaryFile() as errf:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=errf)
        buf, size = [], 0
        try:
            while True:
                chunk = proc.stdout.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > cap:
                    proc.kill()
                    proc.wait(timeout=30)
                    errf.seek(0)
                    return proc.returncode, None, errf.read()
                buf.append(chunk)
            rc = proc.wait(timeout=300)
        finally:
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
        errf.seek(0)
        return rc, b"".join(buf), errf.read()


def run(argv):
    apply = "--apply" in argv
    verbose = os.environ.get("V") == "1"
    ssh, name = settings()
    base = ROOT / "personal" / "backup" / name
    log("pulling the allowlist from %s (read-only: find + tar to stdout)" % ssh[-1])
    try:
        rc, out, err = pull(ssh + [remote_script()])
    except (OSError, subprocess.SubprocessError) as e:
        fail("ssh failed: %s" % e)
        return 1
    if out is None:
        fail("the cabinet sent more than %d MiB: refused, nothing staged" % (MAX_STDOUT >> 20))
        return 1
    for line in err.decode("utf-8", "replace").splitlines():
        if line.startswith(TAG):
            warn(line[len(TAG):].strip())
    if rc != 0:
        fail("the cabinet side exited %d (unreachable, or none of the allowlisted paths exist)" % rc)
        return 1
    with tempfile.TemporaryDirectory(prefix="backup-config-") as td:
        dest = pathlib.Path(td) / "stage"
        dest.mkdir(mode=0o700)
        try:
            kept, dropped = stage(out, dest)
        except (tarfile.TarError, StageError) as e:
            fail("cannot unpack what the cabinet sent: %s" % e)
            return 1
        public = public_shas(ROOT)
        for rel in sorted(kept):
            if len(kept[rel]) >= SAME_MIN and hashlib.sha256(kept[rel]).hexdigest() in public:
                (dest / rel).unlink()
                del kept[rel]
                dropped.setdefault("in-repo", []).append(rel)
        log("pulled %d file(s)%s" % (len(kept), "".join(
            "; dropped %d %s" % (len(v), k) for k, v in sorted(dropped.items()))))
        for reason in ("link", "special", "unsafe-path", "control-char", "unlisted", "binary", "too-big"):
            for rel in dropped.get(reason, [])[:10]:
                warn("dropped (%s): %s" % (reason, rel))
        if not kept:
            fail("nothing to back up")
            return 1
        report, values = redact_tree(dest, kept)
        hits = scan_tree(dest, sorted(values))
        if hits:
            for rel, ln, why in hits:
                fail("still looks secret: %s:%s: %s" % (rel, ln, why))
            fail("final scan found %d hit(s): nothing written" % len(hits))
            return 1
        staged = {rel: (dest / rel).read_bytes() for rel in kept}
    for rel, keys in sorted(report.items()):
        log("redacted %d in %s: %s" % (len(keys), rel, ", ".join(keys)))
    current = tree_files(base)
    added, changed, removed, same = diff(staged, current)
    show = added if verbose or len(added) <= 50 else added[:20]
    for rel in show:
        log("+ " + rel)
    if len(show) < len(added):
        log("+ ... and %d more added (V=1 lists all)" % (len(added) - len(show)))
    for rel in changed:
        log("~ " + rel)
    for rel in removed:
        log("- " + rel)
    total = sum(len(k) for k in report.values())
    rel_base = os.path.relpath(base, ROOT)
    log("summary vs %s/: added %d, changed %d, removed %d, unchanged %d; %d redaction(s) in %d file(s)"
        % (rel_base, len(added), len(changed), len(removed), same, total, len(report)))
    if not apply:
        log("DRY RUN: nothing written; APPLY=1 writes %s/ (commit stays a separate step)" % rel_base)
    elif added or changed or removed:
        try:
            write_tree(base, staged, removed)
        except OSError as e:
            fail("cannot write %s/: %s (%s)" % (rel_base, e.__class__.__name__, getattr(e, "filename", "") or ""))
            return 1
        log("wrote %s/ (not committed: review with git status, then commit by path)" % rel_base)
    else:
        log("%s/ already up to date" % rel_base)
    for rel in sorted(staged):
        if any(fnmatch.fnmatchcase(posixpath.basename(rel), g) for g in GATE_REFUSED):
            warn("%s/%s is redacted, but tests/test_ship_no_artifacts.py refuses that file name in git (#42): "
                 "leave it out of the commit" % (rel_base, rel))
    print("RESULT: PASS")
    return 0


def main(argv):
    try:
        rc = run(argv)
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 2
    if rc:
        print("RESULT: FAIL")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
