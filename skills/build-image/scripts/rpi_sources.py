#!/usr/bin/env python3
"""Keep a copy of the Raspberry Pi archive source packages a release image ships (#275, COMPLIANCE.md section 6).

snapshot.debian.org does not mirror archive.raspberrypi.com, and that archive drops old versions, so the written
offer in LICENCES.md (GPL-2.0 3(b), three years) needs the release's own copy of every source package whose binary in
the image came from the Raspberry Pi archive: the kernel (source `linux`), the firmware and the Pi tools included.

    rpi_sources.py --packages debian-packages.tsv --out DIR [--suite trixie] [--keyring K] [--apply] [--strict]

Input: the image's /usr/share/doc/coindojo/licences/debian-packages.tsv (licences.py build writes it). Every row
with origin `raspberrypi` names its source package and exact source version; each (source, version) is fetched once.

Dry run (the default) reads only the archive's signed index (InRelease + Sources.gz, about 150 KB) and, for a version
the index no longer lists, that version's .dsc from the pool (a few KB each). It prints one line per source package
with its files and size, and the total; it writes nothing.

--apply downloads every file into DIR/<source>_<version>/ (version without epoch), each checked against its sha256 and
size (a .part file, renamed only when it matches; a file already there and matching is kept), then writes
DIR/SOURCES.tsv (source, version, status, file, size, sha256, url), DIR/SHA256SUMS and the index it was checked
against (DIR/InRelease, DIR/Sources.gz). --max-bytes (default 8 GiB)
refuses a plan larger than that before any download.

How each file is verified (the `status` column):
  index     InRelease (gpgv against --keyring, when given; only its signed block is read, text around it is
            ignored, #291) -> Sources.gz sha256 -> each file's sha256 in Sources
  dsc-only  the index no longer lists this version: the .dsc is fetched from the pool by name (https only, not
            covered by the index) and the other files are checked against the .dsc's Checksums-Sha256
  missing   neither: the release cannot honour the offer for it from this archive (exit 1)

Strict (--strict or HK_LICENCES_STRICT=1 exactly, as licences.py) also refuses an InRelease whose signature was not
checked (no --keyring or no gpgv) and any dsc-only package. Exit: 0 OK, 1 a package is missing or a file failed its
checksum, 2 refused (bad input, broken index, plan over --max-bytes, strict).
Not legal advice; I'm not a lawyer.
"""
import argparse
import gzip
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

MIRROR = "https://archive.raspberrypi.com/debian"
SUITE = "trixie"
COMPONENT = "main"
MAX_BYTES = 8 << 30
SRC_NAME = re.compile(r"^[a-z0-9][a-z0-9+.-]+$")        # Debian policy 5.6.1
VERSION = re.compile(r"^(?:[0-9]+:)?[A-Za-z0-9][A-Za-z0-9.+~-]*$")
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+~_-]*$")
SHA = re.compile(r"^[0-9a-f]{64}$")
CHUNK = 1 << 20


class Refused(Exception):
    """Stops the run (exit 2), nothing written."""


def say(msg):
    print(msg, flush=True)


# ------------------------------------------------------------------------------------------------ input

def read_packages(path):
    """The (source, source_version) pairs of every raspberrypi-origin row in debian-packages.tsv, sorted, unique."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    if not lines or not lines[0].startswith("#"):
        raise Refused("%s: no header line (is it licences.py's debian-packages.tsv?)" % path)
    cols = [c.strip() for c in lines[0].lstrip("#").split("\t")]
    for need in ("source", "source_version", "origin"):
        if need not in cols:
            raise Refused("%s: no %s column" % (path, need))
    out = set()
    for n, ln in enumerate(lines[1:], 2):
        if not ln.strip():
            continue
        row = dict(zip(cols, ln.split("\t")))
        if row.get("origin") != "raspberrypi":
            continue
        src, ver = row.get("source", ""), row.get("source_version", "")
        if not SRC_NAME.match(src) or not VERSION.match(ver):
            raise Refused("%s:%d: source %r version %r is not a Debian source name and version" % (path, n, src, ver))
        out.add((src, ver))
    return sorted(out)


def no_epoch(ver):
    return ver.split(":", 1)[1] if ":" in ver else ver


def pool_dir(src, component=COMPONENT):
    prefix = src[:4] if src.startswith("lib") else src[:1]
    return "pool/%s/%s/%s" % (component, prefix, src)


# ------------------------------------------------------------------------------------------------ the archive

def default_opener(url):
    if not url.startswith("https://"):
        raise Refused("refusing a non-https URL: %s" % url)
    return urllib.request.urlopen(url, timeout=60)  # noqa: S310 (https only, checked above)


def read_all(opener, url, limit=64 << 20):
    with opener(url) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise Refused("%s is larger than %d bytes" % (url, limit))
    return data


def deb822(text):
    """Paragraphs of a deb822 file (Sources, .dsc, Release) as dicts; multi-line fields keep their lines."""
    paras = []
    for block in re.split(r"\n\s*\n", text):
        f, key = {}, None
        for ln in block.splitlines():
            if ln.startswith("-----BEGIN PGP") or ln.startswith("Hash:"):
                continue
            if ln.startswith("-----BEGIN PGP SIGNATURE"):
                break
            if ln[:1] in (" ", "\t") and key:
                f[key] += "\n" + ln.strip()
            elif ":" in ln:
                key, v = ln.split(":", 1)
                key = key.strip()
                f[key] = v.strip()
        if f:
            paras.append(f)
    return paras


SIGNED_BEGIN = "-----BEGIN PGP SIGNED MESSAGE-----"
SIG_BEGIN = "-----BEGIN PGP SIGNATURE-----"


def signed_block(text):
    """The cleartext gpgv verified, and nothing else (#291, #287 review L8): the lines between the one BEGIN PGP SIGNED
    MESSAGE (after its armor headers) and the one BEGIN PGP SIGNATURE, dash-escaping undone. Text before or after the
    signed message is not covered by the signature, so it is never parsed; no signed message, or more than one, is
    refused."""
    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    starts = [i for i, ln in enumerate(lines) if ln == SIGNED_BEGIN]
    sigs = [i for i, ln in enumerate(lines) if ln == SIG_BEGIN]
    if len(starts) != 1 or len(sigs) != 1 or sigs[0] < starts[0]:
        raise Refused("InRelease is not exactly one clear-signed message (%d signed message(s), %d signature(s))"
                      % (len(starts), len(sigs)))
    i = starts[0] + 1
    while i < sigs[0] and lines[i].strip():          # armor headers (Hash: ...) up to the first empty line
        if ":" not in lines[i]:
            raise Refused("InRelease: bad armor header line %r" % lines[i][:60])
        i += 1
    body = [ln[2:] if ln.startswith("- ") else ln for ln in lines[i + 1:sigs[0]]]
    return "\n".join(body) + "\n"


def sha256_list(field):
    """[(sha256, size, name)] from a Checksums-Sha256 / SHA256 field."""
    out = []
    for ln in (field or "").splitlines():
        parts = ln.split()
        if len(parts) == 3 and SHA.match(parts[0]) and parts[1].isdigit():
            out.append((parts[0], int(parts[1]), parts[2]))
    return out


def check_signature(inrelease, keyring, gpgv):
    """True when gpgv accepts InRelease against keyring; Refused when it rejects it; None when it cannot check."""
    if not keyring:
        return None
    if not os.path.isfile(keyring):
        raise Refused("keyring %s does not exist" % keyring)
    gpgv = gpgv or shutil.which("gpgv")
    if not gpgv:
        return None
    r = subprocess.run([gpgv, "--keyring", keyring, inrelease], capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise Refused("InRelease signature check failed: %s" % (r.stderr.strip() or "gpgv exit %d" % r.returncode))
    return True


def load_index(opener, mirror, suite, component, keyring, gpgv, work):
    """{(source, version): {"dir": pool dir, "files": [(sha, size, name)]}} from the archive's signed index, and whether
    the signature was checked. work: a scratch dir for InRelease (gpgv reads a file)."""
    url = "%s/dists/%s/InRelease" % (mirror, suite)
    inrel = read_all(opener, url)
    path = os.path.join(work, "InRelease")
    with open(path, "wb") as f:
        f.write(inrel)
    signed = check_signature(path, keyring, gpgv)
    rel = deb822(signed_block(inrel.decode("utf-8", "replace")))     # only what the signature covers (L8)
    want = "%s/source/Sources.gz" % component
    entry = [e for p in rel for e in sha256_list(p.get("SHA256")) if e[2] == want]
    if not entry:
        raise Refused("%s lists no %s" % (url, want))
    sha, size, _ = entry[0]
    sources_url = "%s/dists/%s/%s" % (mirror, suite, want)
    gz = read_all(opener, sources_url)
    if len(gz) != size or hashlib.sha256(gz).hexdigest() != sha:
        raise Refused("%s does not match the sha256/size InRelease gives" % sources_url)
    index = {}
    for p in deb822(gzip.decompress(gz).decode("utf-8", "replace")):
        if p.get("Package") and p.get("Version") and p.get("Directory"):
            index[(p["Package"], p["Version"])] = {"dir": p["Directory"],
                                                   "files": sha256_list(p.get("Checksums-Sha256"))}
    return index, signed, inrel, gz


def plan(pairs, index, opener, mirror, component):
    """One entry per (source, version): status index | dsc-only | missing, its files [(sha, size, name)], and urls."""
    out = []
    for src, ver in pairs:
        e = index.get((src, ver))
        if e:
            out.append({"source": src, "version": ver, "status": "index", "dir": e["dir"], "files": e["files"],
                        "note": ""})
            continue
        d = pool_dir(src, component)
        dsc_name = "%s_%s.dsc" % (src, no_epoch(ver))
        try:
            dsc = read_all(opener, "%s/%s/%s" % (mirror, d, dsc_name), limit=4 << 20)
        except (OSError, Refused) as exc:
            out.append({"source": src, "version": ver, "status": "missing", "dir": d, "files": [],
                        "note": "not in the index, no %s in the pool (%s)" % (dsc_name, exc)})
            continue
        para = deb822(dsc.decode("utf-8", "replace"))
        files = sha256_list(para[0].get("Checksums-Sha256")) if para else []
        if not para or para[0].get("Source") != src or para[0].get("Version") != ver or not files:
            out.append({"source": src, "version": ver, "status": "missing", "dir": d, "files": [],
                        "note": "%s in the pool is not %s %s with Checksums-Sha256" % (dsc_name, src, ver)})
            continue
        files = [(hashlib.sha256(dsc).hexdigest(), len(dsc), dsc_name)] + files
        out.append({"source": src, "version": ver, "status": "dsc-only", "dir": d, "files": files,
                    "note": "the index lists another version; the .dsc itself is not index-verified"})
    for e in out:
        bad = [n for _s, _z, n in e["files"] if not FILE_NAME.match(n)]
        if bad or not re.match(r"^pool/[A-Za-z0-9/+.~_-]+$", e["dir"]) or ".." in e["dir"].split("/"):
            raise Refused("%s %s: unsafe file name or directory in the archive (%s)" % (e["source"], e["version"],
                                                                                       bad or e["dir"]))
    return out


# ------------------------------------------------------------------------------------------------ download

def fetch_file(opener, url, dest, sha, size):
    """Download url to dest, checking sha256 and size; keep an existing matching file. Returns 'kept'/'fetched'; raises
    ValueError on a mismatch (nothing left at dest)."""
    if os.path.isfile(dest) and os.path.getsize(dest) == size:
        h = hashlib.sha256()
        with open(dest, "rb") as f:
            for block in iter(lambda: f.read(CHUNK), b""):
                h.update(block)
        if h.hexdigest() == sha:
            return "kept"
    part = dest + ".part"
    h, n = hashlib.sha256(), 0
    try:
        with opener(url) as r, open(part, "wb") as f:
            while True:
                block = r.read(CHUNK)
                if not block:
                    break
                n += len(block)
                if n > size:
                    break
                h.update(block)
                f.write(block)
        if n != size or h.hexdigest() != sha:
            raise ValueError("%s: got %d bytes sha256 %s, want %d bytes sha256 %s" % (url, n, h.hexdigest(), size,
                                                                                      sha))
        os.replace(part, dest)
    finally:
        if os.path.exists(part):
            os.remove(part)
    return "fetched"


def human(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return str(n)


def run(pairs, out, opener=None, mirror=MIRROR, suite=SUITE, component=COMPONENT, keyring=None, gpgv=None,
        apply=False, strict=False, max_bytes=MAX_BYTES):
    """Plan (and with apply, fetch) the source packages. Returns the exit code."""
    opener = opener or default_opener
    with tempfile.TemporaryDirectory() as work:
        index, signed, inrel, gz = load_index(opener, mirror, suite, component, keyring, gpgv, work)
    if signed is None:
        msg = "InRelease signature not checked (%s): the index is trusted on https alone" % (
            "no --keyring" if not keyring else "no gpgv")
        if strict:
            raise Refused("strict: " + msg)
        say("WARN: " + msg)
    entries = plan(pairs, index, opener, mirror, component)
    total = sum(z for e in entries for _s, z, _n in e["files"])
    for e in entries:
        size = sum(z for _s, z, _n in e["files"])
        say("%-9s %-32s %-36s %3d files %10s%s" % (e["status"], e["source"], e["version"], len(e["files"]),
                                                   human(size), ("  (%s)" % e["note"]) if e["note"] else ""))
    missing = [e for e in entries if e["status"] == "missing"]
    dsc_only = [e for e in entries if e["status"] == "dsc-only"]
    say("rpi-sources: %d source packages, %d files, %s total (%d index, %d dsc-only, %d missing)"
        % (len(entries), sum(len(e["files"]) for e in entries), human(total),
           len(entries) - len(missing) - len(dsc_only), len(dsc_only), len(missing)))
    if strict and dsc_only:
        raise Refused("strict: not index-verified: %s" % ", ".join("%s %s" % (e["source"], e["version"])
                                                                    for e in dsc_only))
    if not apply:
        say("rpi-sources: dry run, nothing downloaded (--apply fetches into %s)" % out)
        return 1 if missing else 0
    if total > max_bytes:
        raise Refused("the plan is %s, over --max-bytes %s" % (human(total), human(max_bytes)))
    os.makedirs(out, exist_ok=True)
    for name, data in (("InRelease", inrel), ("Sources.gz", gz)):   # the signed index, so anyone can re-check
        with open(os.path.join(out, name), "wb") as f:
            f.write(data)
    rows, failed, done = [], [], 0
    for e in entries:
        if e["status"] == "missing":
            continue
        d = os.path.join(out, "%s_%s" % (e["source"], no_epoch(e["version"])))
        os.makedirs(d, exist_ok=True)
        for sha, size, name in e["files"]:
            url = "%s/%s/%s" % (mirror, e["dir"], name)
            try:
                how = fetch_file(opener, url, os.path.join(d, name), sha, size)
            except (OSError, ValueError) as exc:
                failed.append(str(exc))
                say("FAIL: %s" % exc)
                continue
            done += size
            say("%s %s/%s (%s; %s of %s)" % (how, os.path.basename(d), name, human(size), human(done), human(total)))
            rows.append((e["source"], e["version"], e["status"], "%s/%s" % (os.path.basename(d), name), size, sha,
                         url))
    rows.sort()
    with open(os.path.join(out, "SOURCES.tsv"), "w", encoding="utf-8") as f:
        f.write("# source\tversion\tstatus\tfile\tsize\tsha256\turl\n")
        f.write("".join("\t".join(str(v) for v in r) + "\n" for r in rows))
    with open(os.path.join(out, "SHA256SUMS"), "w", encoding="utf-8") as f:
        f.write("".join("%s  %s\n" % (r[5], r[3]) for r in sorted(rows, key=lambda r: r[3])))
    say("rpi-sources: %s, %d files in %s, %d failed, %d missing"
        % ("FAIL" if failed or missing else "OK", len(rows), out, len(failed), len(missing)))
    return 1 if failed or missing else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--packages", required=True, help="the image's debian-packages.tsv")
    ap.add_argument("--out", required=True, help="the release work dir for the source packages")
    ap.add_argument("--mirror", default=MIRROR)
    ap.add_argument("--suite", default=SUITE)
    ap.add_argument("--component", default=COMPONENT)
    ap.add_argument("--keyring", default=None, help="raspberrypi-archive-keyring.pgp (e.g. from the image root)")
    ap.add_argument("--gpgv", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--max-bytes", type=int, default=MAX_BYTES)
    ap.add_argument("--apply", action="store_true", help="download (default: dry run)")
    ap.add_argument("--strict", action="store_true", help="as HK_LICENCES_STRICT=1")
    a = ap.parse_args(argv)
    strict = a.strict or os.environ.get("HK_LICENCES_STRICT") == "1"
    if not a.mirror.startswith("https://"):
        print("rpi-sources: REFUSED: --mirror must be https", file=sys.stderr)
        return 2
    try:
        pairs = read_packages(a.packages)
        if not pairs:
            say("rpi-sources: no raspberrypi packages in %s, nothing to keep" % a.packages)
            return 0
        return run(pairs, a.out, mirror=a.mirror.rstrip("/"), suite=a.suite, component=a.component,
                   keyring=a.keyring, gpgv=a.gpgv, apply=a.apply, strict=strict, max_bytes=a.max_bytes)
    except (Refused, OSError, ValueError) as exc:
        print("rpi-sources: REFUSED: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
