#!/usr/bin/env python3
"""The release's own copy of the Raspberry Pi archive source packages (#275): scripts/rpi_sources.py.

A fake archive (InRelease -> Sources.gz -> pool files) served from a dict, no network. Checks: only raspberrypi rows
of debian-packages.tsv are picked, once per (source, version); the dry run downloads nothing and writes nothing;
--apply fetches every file checked against its sha256 and size, keeps matching files on a re-run, and writes
SOURCES.tsv, SHA256SUMS and the index; a version the index dropped is fetched by its .dsc (dsc-only), which strict
refuses; the gpgv signature check.
Broken cases (test_broken_*): a pool file whose bytes changed, a source package the archive no longer has, a
Sources.gz that does not match InRelease, an unsafe name in the tsv or the archive, a plan over --max-bytes, a bad
signature, a non-https mirror, index text outside InRelease's signed block (#291). Plain python (hktest).
"""
import gzip
import hashlib
import io
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(SKILL))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
sys.path.insert(0, os.path.join(SKILL, "scripts"))
from hktest import check, main  # noqa: E402

import rpi_sources as R  # noqa: E402

M = "https://archive.example.test/debian"
TSV_HEAD = "# package\tversion\tarch\tsource\tsource_version\torigin\tsource_url\tcopyright\n"
TSV_ROWS = [
    ("linux-image-rpi-v8", "1:6.1.0-1+rpt1", "arm64", "linux", "1:6.1.0-1+rpt1", "raspberrypi"),
    ("linux-libc-dev", "1:6.1.0-1+rpt1", "arm64", "linux", "1:6.1.0-1+rpt1", "raspberrypi"),
    ("raspi-utils", "20250101-1", "arm64", "raspi-utils", "20250101-1", "raspberrypi"),
    ("libcamera0.5", "0.5.0+rpt1-1", "arm64", "libcamera", "0.5.0+rpt1-1", "raspberrypi"),
    ("bash", "5.2.37-2", "arm64", "bash", "5.2.37-2", "debian"),
]


def sha(b):
    return hashlib.sha256(b).hexdigest()


def dsc_text(src, ver, files):
    lines = ["Format: 3.0 (quilt)", "Source: %s" % src, "Version: %s" % ver, "Checksums-Sha256:"]
    lines += [" %s %d %s" % (sha(b), len(b), n) for n, b in files]
    return ("-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA256\n\n" + "\n".join(lines)
            + "\n\n-----BEGIN PGP SIGNATURE-----\nZmFrZQ==\n-----END PGP SIGNATURE-----\n").encode()


class Archive:
    """A fake Raspberry Pi archive: linux and raspi-utils in the index, libcamera's version only in the pool."""

    def __init__(self):
        self.files = {}
        self.calls = []
        self.pkgs = {}
        self.add("linux", "1:6.1.0-1+rpt1", "pool/main/l/linux",
                 [("linux_6.1.0.orig.tar.xz", b"kernel tree " * 50), ("linux_6.1.0-1+rpt1.debian.tar.xz", b"deb")])
        self.add("raspi-utils", "20250101-1", "pool/main/r/raspi-utils",
                 [("raspi-utils_20250101.orig.tar.gz", b"utils"), ("raspi-utils_20250101-1.debian.tar.xz", b"d")])
        self.add("libcamera", "0.6.0+rpt1-1", "pool/main/libc/libcamera", [("libcamera_0.6.0.orig.tar.xz", b"new")])
        old = [("libcamera_0.5.0+rpt1.orig.tar.xz", b"old camera"), ("libcamera_0.5.0+rpt1-1.debian.tar.xz", b"od")]
        for n, b in old:
            self.files["%s/pool/main/libc/libcamera/%s" % (M, n)] = b
        self.files["%s/pool/main/libc/libcamera/libcamera_0.5.0+rpt1-1.dsc" % M] = dsc_text("libcamera",
                                                                                          "0.5.0+rpt1-1", old)
        self.publish()

    def add(self, src, ver, d, files):
        dsc_name = "%s_%s.dsc" % (src, R.no_epoch(ver))
        dsc = dsc_text(src, ver, files)
        allf = [(dsc_name, dsc)] + files
        for n, b in allf:
            self.files["%s/%s/%s" % (M, d, n)] = b
        self.pkgs[(src, ver)] = (d, allf)

    def publish(self, sources_gz=None):
        paras = []
        for (src, ver), (d, allf) in sorted(self.pkgs.items()):
            paras.append("Package: %s\nBinary: %s\nVersion: %s\nDirectory: %s\nChecksums-Sha256:\n%s\n" % (
                src, src, ver, d, "\n".join(" %s %d %s" % (sha(b), len(b), n) for n, b in allf)))
        gz = sources_gz if sources_gz is not None else gzip.compress("\n".join(paras).encode(), mtime=0)
        self.files["%s/dists/trixie/main/source/Sources.gz" % M] = gz
        good = gzip.compress("\n".join(paras).encode(), mtime=0)
        self.files["%s/dists/trixie/InRelease" % M] = (
            "-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\nOrigin: Raspberry Pi Foundation\nCodename: trixie\n"
            "SHA256:\n %s %d main/binary-arm64/Packages\n %s %d main/source/Sources.gz\n\n"
            "-----BEGIN PGP SIGNATURE-----\nZmFrZQ==\n-----END PGP SIGNATURE-----\n"
            % ("0" * 64, 10, sha(good), len(good))).encode()

    def opener(self, url):
        self.calls.append(url)
        if url not in self.files:
            raise OSError("HTTP Error 404: Not Found: %s" % url)
        return io.BytesIO(self.files[url])


def write_tsv(tmp, rows=TSV_ROWS, head=TSV_HEAD):
    p = os.path.join(tmp, "debian-packages.tsv")
    with open(p, "w", encoding="utf-8") as f:
        f.write(head + "".join("\t".join(r + ("https://x/", "yes")) + "\n" for r in rows))
    return p


def run(archive, pairs, out, **kw):
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        try:
            rc = R.run(pairs, out, opener=archive.opener, mirror=M, **kw)
        except R.Refused as exc:
            print("REFUSED: %s" % exc)
            rc = 2
    return rc, buf.getvalue()


def cli(argv, archive):
    buf = io.StringIO()
    saved = R.default_opener
    R.default_opener = archive.opener
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = R.main(argv)
    finally:
        R.default_opener = saved
    return rc, buf.getvalue()


def test_reads_only_raspberrypi_rows_once():
    with tempfile.TemporaryDirectory() as t:
        pairs = R.read_packages(write_tsv(t))
        check(pairs == [("libcamera", "0.5.0+rpt1-1"), ("linux", "1:6.1.0-1+rpt1"), ("raspi-utils", "20250101-1")],
              "raspberrypi rows only, one per (source, version), sorted: %s" % pairs)
        check(R.read_packages(write_tsv(t, rows=TSV_ROWS[4:])) == [], "no raspberrypi rows: nothing to keep")


def test_dry_run_downloads_and_writes_nothing():
    a = Archive()
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, "out")
        rc, log = cli(["--packages", write_tsv(t), "--out", out, "--mirror", M], a)
        check(rc == 0, "dry run exits 0 (rc %s)" % rc)
        check(not os.path.exists(out), "the dry run writes nothing")
        check(all(u.endswith(("InRelease", "Sources.gz", ".dsc")) for u in a.calls),
              "the dry run reads only the index and dropped versions' .dsc: %s" % a.calls)
        check("index     linux" in log and "dsc-only  libcamera" in log and "3 source packages, 9 files" in log
              and "dry run, nothing downloaded" in log, "the plan names each package, its status and the total")
        check("WARN: InRelease signature not checked (no --keyring)" in log, "an unchecked signature is a WARN")


def test_apply_fetches_verified_and_keeps_on_rerun():
    a = Archive()
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, "out")
        pairs = R.read_packages(write_tsv(t))
        rc, log = run(a, pairs, out, apply=True)
        check(rc == 0 and "rpi-sources: OK, 9 files" in log, "apply fetches all 9 files (rc %s)" % rc)
        kern = os.path.join(out, "linux_6.1.0-1+rpt1", "linux_6.1.0.orig.tar.xz")
        check(os.path.isfile(kern) and open(kern, "rb").read() == b"kernel tree " * 50,
              "the kernel source sits in <source>_<version without epoch>/")
        tsv = open(os.path.join(out, "SOURCES.tsv"), encoding="utf-8").read()
        check("linux\t1:6.1.0-1+rpt1\tindex\tlinux_6.1.0-1+rpt1/linux_6.1.0-1+rpt1.dsc\t" in tsv
              and "libcamera\t0.5.0+rpt1-1\tdsc-only\t" in tsv, "SOURCES.tsv records each file and how it was checked")
        sums = open(os.path.join(out, "SHA256SUMS"), encoding="utf-8").read()
        check("%s  linux_6.1.0-1+rpt1/linux_6.1.0.orig.tar.xz\n" % sha(b"kernel tree " * 50) in sums,
              "SHA256SUMS is in sha256sum -c format")
        check(os.path.isfile(os.path.join(out, "InRelease")) and os.path.isfile(os.path.join(out, "Sources.gz")),
              "the index it was checked against is kept with it")
        check(not [f for _d, _s, fs in os.walk(out) for f in fs if f.endswith(".part")], "no .part file is left")
        a.calls.clear()
        rc, log = run(a, pairs, out, apply=True)
        check(rc == 0 and "fetched" not in log and log.count("\nkept ") + log.startswith("kept ") >= 8
              and not [u for u in a.calls if "/pool/" in u and not u.endswith(".dsc")],
              "a re-run keeps every matching file, downloads none")


def test_strict_refuses_unchecked_signature_and_dsc_only():
    a = Archive()
    with tempfile.TemporaryDirectory() as t:
        pairs = R.read_packages(write_tsv(t))
        rc, log = run(a, pairs, os.path.join(t, "out"), strict=True)
        check(rc == 2 and "strict: InRelease signature not checked" in log, "strict refuses an unchecked InRelease")
        gpgv = os.path.join(t, "gpgv")
        with open(gpgv, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(gpgv, 0o755)
        key = os.path.join(t, "keyring.pgp")
        open(key, "wb").close()
        rc, log = run(a, pairs, os.path.join(t, "out"), strict=True, keyring=key, gpgv=gpgv)
        check(rc == 2 and "strict: not index-verified: libcamera 0.5.0+rpt1-1" in log,
              "strict refuses a version only the pool still has (rc %s)" % rc)
        rc, log = run(a, pairs[1:], os.path.join(t, "out"), strict=True, keyring=key, gpgv=gpgv, apply=True)
        check(rc == 0 and "WARN" not in log, "strict passes a signed index with every version in it")


def test_broken_changed_pool_file():
    a = Archive()
    a.files["%s/pool/main/l/linux/linux_6.1.0.orig.tar.xz" % M] = b"tampered tree " * 50
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, "out")
        rc, log = run(a, [("linux", "1:6.1.0-1+rpt1")], out, apply=True)
        check(rc == 1 and "FAIL: " in log and "linux_6.1.0.orig.tar.xz" in log, "BROKEN: a changed file FAILs (rc %s)"
              % rc)
        check(not os.path.exists(os.path.join(out, "linux_6.1.0-1+rpt1", "linux_6.1.0.orig.tar.xz")),
              "BROKEN: the changed file is not kept")
        a.files["%s/pool/main/l/linux/linux_6.1.0.orig.tar.xz" % M] = b"kernel tree " * 51
        rc, log = run(a, [("linux", "1:6.1.0-1+rpt1")], out, apply=True)
        check(rc == 1 and "FAIL: " in log, "BROKEN: a longer file FAILs on its size")


def test_broken_missing_source_package():
    a = Archive()
    with tempfile.TemporaryDirectory() as t:
        rc, log = run(a, [("raspi-firmware", "1:1.20250101-1")], os.path.join(t, "out"))
        check(rc == 1 and "missing   raspi-firmware" in log and "1 missing" in log,
              "BROKEN: a source the archive dropped is missing, exit 1 (rc %s)" % rc)
        rc, log = run(a, [("raspi-firmware", "1:1.20250101-1"), ("raspi-utils", "20250101-1")],
                      os.path.join(t, "out"), apply=True)
        check(rc == 1 and "rpi-sources: FAIL" in log, "BROKEN: --apply fetches the rest and still FAILs")


def test_broken_index_mismatch():
    a = Archive()
    a.publish(sources_gz=gzip.compress(b"Package: evil\n", mtime=0))
    with tempfile.TemporaryDirectory() as t:
        rc, log = run(a, [("linux", "1:6.1.0-1+rpt1")], os.path.join(t, "out"))
        check(rc == 2 and "does not match the sha256/size InRelease gives" in log,
              "BROKEN: a Sources.gz that does not match InRelease is refused")


def test_broken_unsafe_names():
    a = Archive()
    with tempfile.TemporaryDirectory() as t:
        bad = write_tsv(t, rows=[("x", "1", "arm64", "../etc", "1", "raspberrypi")])
        rc, log = cli(["--packages", bad, "--out", os.path.join(t, "out"), "--mirror", M], a)
        check(rc == 2 and "is not a Debian source name" in log, "BROKEN: a path in the source column is refused")
        rc, log = cli(["--packages", write_tsv(t, head="package\tversion\n"), "--out", t, "--mirror", M], a)
        check(rc == 2 and "no header line" in log, "BROKEN: a file that is not debian-packages.tsv is refused")
        d, allf = a.pkgs[("raspi-utils", "20250101-1")]
        a.pkgs[("raspi-utils", "20250101-1")] = (d, allf + [("../../evil", b"x")])
        a.publish()
        rc, log = run(a, [("raspi-utils", "20250101-1")], os.path.join(t, "out"), apply=True)
        check(rc == 2 and "unsafe file name" in log and not os.path.exists(os.path.join(t, "evil")),
              "BROKEN: a ../ file name in the archive is refused before any download")
        rc, log = cli(["--packages", write_tsv(t), "--out", t, "--mirror", "http://archive.example.test"], a)
        check(rc == 2 and "must be https" in log, "BROKEN: a plain-http mirror is refused")


def test_broken_over_max_bytes():
    a = Archive()
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, "out")
        rc, log = run(a, [("linux", "1:6.1.0-1+rpt1")], out, apply=True, max_bytes=100)
        check(rc == 2 and "over --max-bytes" in log and not os.path.exists(out),
              "BROKEN: a plan over --max-bytes is refused before any download")


def test_signed_block_only():
    """#291 (L8): only the clear-signed block of InRelease is parsed, dash-escaping undone."""
    text = ("-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\nOrigin: x\n- -dashed: y\n\n"
            "-----BEGIN PGP SIGNATURE-----\nZmFrZQ==\n-----END PGP SIGNATURE-----\n")
    check(R.signed_block(text) == "Origin: x\n-dashed: y\n\n", "the signed block, dash-escaping undone")
    check(R.signed_block(text.replace("\n", "\r\n")) == "Origin: x\n-dashed: y\n\n", "CRLF line ends read the same")


def test_broken_unsigned_text_around_inrelease():
    """#291 (L8): an index entry outside the signed block (before it or after the signature) is never used: a forged
    Sources.gz listed there is refused, as are an InRelease with no signed message and one with two."""
    a = Archive()
    evil = gzip.compress(b"Package: linux\nVersion: 1:6.1.0-1+rpt1\nDirectory: pool/main/l/linux\n", mtime=0)
    forged = "SHA256:\n %s %d main/source/Sources.gz\n\n" % (sha(evil), len(evil))
    good = a.files["%s/dists/trixie/InRelease" % M].decode()
    a.files["%s/dists/trixie/main/source/Sources.gz" % M] = evil
    for name, text in (("before", forged + good), ("after", good + "\n" + forged)):
        a.files["%s/dists/trixie/InRelease" % M] = text.encode()
        with tempfile.TemporaryDirectory() as t:
            rc, log = run(a, [("linux", "1:6.1.0-1+rpt1")], os.path.join(t, "out"))
        check(rc == 2 and "does not match the sha256/size InRelease gives" in log,
              "BROKEN: a Sources.gz entry %s the signed block is ignored, the forged index refused" % name)
    for name, text in (("no signed message", forged), ("two signed messages", good + good)):
        a.files["%s/dists/trixie/InRelease" % M] = text.encode()
        with tempfile.TemporaryDirectory() as t:
            rc, log = run(a, [("linux", "1:6.1.0-1+rpt1")], os.path.join(t, "out"))
        check(rc == 2 and "not exactly one clear-signed message" in log, "BROKEN: InRelease with %s is refused" % name)


def test_broken_bad_signature():
    a = Archive()
    with tempfile.TemporaryDirectory() as t:
        gpgv = os.path.join(t, "gpgv")
        with open(gpgv, "w") as f:
            f.write("#!/bin/sh\necho 'BAD signature' >&2\nexit 1\n")
        os.chmod(gpgv, 0o755)
        key = os.path.join(t, "keyring.pgp")
        open(key, "wb").close()
        rc, log = run(a, [("linux", "1:6.1.0-1+rpt1")], os.path.join(t, "out"), keyring=key, gpgv=gpgv)
        check(rc == 2 and "signature check failed: BAD signature" in log, "BROKEN: a bad InRelease signature is refused")
        rc, log = run(a, [("linux", "1:6.1.0-1+rpt1")], os.path.join(t, "out"), keyring=os.path.join(t, "nope"))
        check(rc == 2 and "does not exist" in log, "BROKEN: a keyring that is not there is refused")


if __name__ == "__main__":
    main(globals())
