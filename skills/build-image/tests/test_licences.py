#!/usr/bin/env python3
"""The image's licence bundle (#254, #267): scripts/licences.py and licences/components.json.

  1. the manifest is complete: every component has a licence (SPDX + text) and an exact source entry, every core the
     launcher runs has a component, every built component pins the same commit as its build/*/build.sh;
  2. THIRD_PARTY.md names every component, and the non-commercial ones are flagged in the manifest too;
  3. the generator writes the licence folder, components.tsv, debian-packages.tsv (snapshot.debian.org / Raspberry Pi
     archive links, exact versions) and LICENCES.md on the boot partition, and is deterministic (same inputs, same
     bytes; manifest order does not matter);
  4. provision.sh runs it at image build and verify.sh checks what it wrote.
  5. the ES-DE AppImage's libraries (#276): the inventory (licences/esde-libraries.tsv) is well-formed, every
     copyleft library owes its source, the bundle gets es-de/libraries.tsv, and the image's AppImage matches it.
Broken cases (test_broken_*): a planted unlisted library in the AppImage, a changed one, a missing copyright file, a
malformed inventory row, a planted unlisted core, a core without a component, a component without a licence or a
source, a moved build pin, a changed licence text, a BUILDINFO from another commit, a listed component missing from
the image, a package without its copyright file, Fightcade binaries in a release image. Plain python (hktest).
"""
import copy
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(SKILL))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
sys.path.insert(0, os.path.join(SKILL, "scripts"))
from hktest import check, main, skip  # noqa: E402

import licences as L  # noqa: E402

ELF = b"\x7fELF\x02\x01\x01"
COMMIT_RX = L.COMMIT
STATUS = """Package: retroarch
Status: install ok installed
Architecture: arm64
Version: 1.20.0+dfsg-1
Description: x

Package: libfoo1
Status: install ok installed
Architecture: arm64
Source: foo (1:2.0-3)
Version: 1:2.0-3+b1

Package: raspi-utils
Status: install ok installed
Architecture: arm64
Version: 20250101-1+rpt1

Package: gone
Status: deinstall ok config-files
Architecture: arm64
Version: 1.0
"""


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def fixture_manifest():
    """The real manifest, with each pinned licence text replaced by a placeholder of known sha256 (no network)."""
    man = copy.deepcopy(L.load_manifest())
    texts = {}
    for c in man["components"]:
        for lf in c["licence_files"]:
            if "url" in lf:
                data = ("placeholder licence text for %s/%s\n" % (c["id"], lf["name"])).encode()
                lf["sha256"] = hashlib.sha256(data).hexdigest()
                texts[lf["sha256"]] = data
    return man, texts


def make_cache(dirpath, texts):
    os.makedirs(dirpath, exist_ok=True)
    for sha, data in texts.items():
        with open(os.path.join(dirpath, sha), "wb") as f:
            f.write(data)


def make_root(dirpath, man, skip_optional=True):
    """A fake image root: every listed path (ELF), a dpkg database, apt lists, copyright files, a boot partition."""
    for c in man["components"]:
        if c.get("optional") and skip_optional:
            continue
        for p in c["paths"]:
            host = os.path.join(dirpath, p.lstrip("/"))
            os.makedirs(os.path.dirname(host), exist_ok=True)
            with open(host, "wb") as f:
                f.write(ELF + c["id"].encode())
    es = os.path.join(dirpath, "opt", "ES-DE", "squashfs-root", "usr", "lib")
    os.makedirs(es, exist_ok=True)
    with open(os.path.join(es, "libbundled.so.1"), "wb") as f:   # covered by es-de's "covers"
        f.write(ELF)
    info = os.path.join(dirpath, "var", "lib", "dpkg", "info")
    os.makedirs(info)
    with open(os.path.join(dirpath, "var", "lib", "dpkg", "status"), "w") as f:
        f.write(STATUS)
    core_dir = os.path.join(dirpath, man["core_dir"].lstrip("/"))
    with open(os.path.join(core_dir, "debian_libretro.so"), "wb") as f:   # a Debian core: dpkg owns it
        f.write(ELF)
    with open(os.path.join(info, "libretro-debian:arm64.list"), "w") as f:
        f.write("/.\n%s/debian_libretro.so\n" % man["core_dir"])
    lists = os.path.join(dirpath, "var", "lib", "apt", "lists")
    os.makedirs(lists)
    with open(os.path.join(lists, "archive.raspberrypi.com_debian_dists_trixie_main_binary-arm64_Packages"), "w") as f:
        f.write("Package: raspi-utils\nVersion: 20250101-1+rpt1\n\n")
    with open(os.path.join(lists, "deb.debian.org_debian_dists_trixie_main_binary-arm64_Packages"), "w") as f:
        f.write("Package: retroarch\nVersion: 1.20.0+dfsg-1\n\nPackage: libfoo1\nVersion: 1:2.0-3+b1\n\n")
    doc = os.path.join(dirpath, "usr", "share", "doc")
    for pkg in ("retroarch", "raspi-utils", "foo-common"):
        os.makedirs(os.path.join(doc, pkg))
        with open(os.path.join(doc, pkg, "copyright"), "w") as f:
            f.write("copyright of %s\n" % pkg)
    os.symlink("foo-common", os.path.join(doc, "libfoo1"))   # Debian's doc-dir symlink, resolved inside the root
    os.makedirs(os.path.join(dirpath, "boot", "firmware"))
    return dirpath


ESDE = os.path.join("opt", "ES-DE", "squashfs-root")
LIB_HEAD = "# file\tpackage\tversion\tsource\tsource_version\tspdx\towes\tsource_url\tsha256\n"


def lib_row(name, data, pkg="libbundled1", spdx="LGPL-2.1-or-later", owes="source"):
    return "\t".join(("usr/lib/" + name, pkg, "1.0-1", "bundled", "1.0-1", spdx, owes,
                      "https://launchpad.net/ubuntu/+source/bundled/1.0-1", hashlib.sha256(data).hexdigest())) + "\n"


def make_esde_libs(tmp, root, man):
    """The es-de component's inventory points at a fixture TSV listing the AppImage's one planted library."""
    path = os.path.join(tmp, "esde-libraries.tsv")
    with open(os.path.join(root, ESDE, "usr", "lib", "libbundled.so.1"), "rb") as f:
        data = f.read()
    with open(path, "w") as f:
        f.write(LIB_HEAD + lib_row("libbundled.so.1", data))
    doc = os.path.join(root, ESDE, "usr", "share", "doc", "libbundled1")
    os.makedirs(doc, exist_ok=True)
    with open(os.path.join(doc, "copyright"), "w") as f:
        f.write("copyright of libbundled1\n")
    for c in man["components"]:
        if c["id"] == "es-de":
            c["libraries"] = path
    return path


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = L.main(argv)
    return rc, out.getvalue() + err.getvalue()


def tree_digest(top):
    h = hashlib.sha256()
    for dirpath, dirs, files in os.walk(top):
        dirs.sort()
        for fn in sorted(files):
            p = os.path.join(dirpath, fn)
            h.update(os.path.relpath(p, top).encode() + b"\0")
            with open(p, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


class Image:
    """A temp dir with a fixture manifest file, cache and root."""

    def __init__(self, man=None):
        self.tmp = tempfile.mkdtemp(prefix="hk-lic-")
        base, texts = fixture_manifest()
        self.man = man or base
        self.manifest = os.path.join(self.tmp, "components.json")
        self.write()
        self.cache = os.path.join(self.tmp, "cache")
        make_cache(self.cache, texts)
        self.root = make_root(os.path.join(self.tmp, "root"), self.man)
        self.build_dir = os.path.join(self.tmp, "build")   # no BUILDINFO unless a test writes one
        self.libs = make_esde_libs(self.tmp, self.root, self.man)
        self.write()

    def write(self):
        with open(self.manifest, "w") as f:
            json.dump(self.man, f)

    def build(self, *extra):
        return run_cli(["build", "--manifest", self.manifest, "--root", self.root, "--cache", self.cache,
                        "--build-dir", self.build_dir, "--version", "v9.9.9"] + list(extra))

    def doc(self, *p):
        return os.path.join(self.root, L.DOC_DIR, *p)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# --------------------------------------------------------------------------------------------- the manifest

def test_manifest_is_complete():
    man = L.load_manifest()
    cores = L.launcher_cores()
    check(len(cores) >= 8, "the launcher's cores are read from launch.py (%d)" % len(cores))
    probs = L.manifest_problems(man, ROOT, cores)
    check(probs == [], "components.json: every component has a licence and a source, every core is listed %s"
          % (probs or ""))
    rc, out = run_cli(["check"])
    check(rc == 0, "licences.py check passes on the repo (%s)" % out.strip())


def test_every_component_has_licence_and_source_entry():
    for c in L.load_manifest()["components"]:
        has_text = bool(c["licence_files"]) or c["origin"] == "source"
        check(c["spdx"] and has_text and c["source"], "%s: SPDX %s, licence text, source %s" % (c["id"], c["spdx"],
                                                                                             c["source"]))


def test_noncommercial_cores_flagged():
    nc = sorted(c["core"] for c in L.load_manifest()["components"] if c.get("noncommercial"))
    check(nc == ["fbneo", "genesis_plus_gx", "snes9x"], "the image's non-commercial cores are flagged (%s)" % nc)


def test_third_party_names_every_component():
    third = _read(os.path.join(ROOT, "THIRD_PARTY.md"))
    for c in L.load_manifest()["components"]:
        key = "`%s`" % c["core"] if c.get("core") else c["source"].replace("https://", "")
        if c["origin"] == "ours":
            key = "LICENSE"
        check(key in third, "THIRD_PARTY.md names %s (%s)" % (c["id"], key))
    check("snapshot.debian.org" in third and "LICENCES.md" in third, "THIRD_PARTY.md explains the image's bundle")


# --------------------------------------------------------------------------------------------- the generator

def test_generator_writes_the_bundle():
    im = Image()
    try:
        rc, out = im.build()
        check(rc == 0, "build exits 0 without --strict (%s)" % out.strip().splitlines()[-1:])
        check("WARN: release blocker: fbneo: no BUILDINFO" in out, "a pinned core without its build record is a WARN")
        check("no exact source ref" not in out, "every core names its exact source ref (#274)")
        for c in im.man["components"]:
            if c.get("optional"):
                check(not os.path.exists(im.doc(c["id"])), "optional %s left out when absent" % c["id"])
                continue
            for lf in c["licence_files"]:
                check(os.path.isfile(im.doc(c["id"], lf["name"])), "%s/%s copied" % (c["id"], lf["name"]))
        lic = _read(im.doc("LICENCES.md"))
        check(_read(os.path.join(im.root, "boot", "firmware", "LICENCES.md")) == lic,
              "LICENCES.md on the boot partition is the same text")
        check(L.CAVEAT in lic and "v9.9.9" in lic and "Written offer" in lic, "LICENCES.md: caveat, version, offer")
        nc = lic.split("## Non-commercial")[1].split("## Components")[0]
        check(all(n in nc for n in ("FinalBurn Neo", "Snes9x", "Genesis Plus GX")) and "RetroArch" not in nc,
              "LICENCES.md lists exactly the non-commercial components first")
        rows = _read(im.doc("components.tsv")).splitlines()
        ra = [r for r in rows if r.startswith("retroarch\t")]
        check(len(ra) == 1 and "69a4f0ea1e8aaf442ae4858f2e7f2b31a1776576" in ra[0] and "sha256" not in ra[0] and
              "/data/emulators/retroarch-1.22/retroarch=" in ra[0], "components.tsv: exact ref and file sha256")
        deb = _read(im.doc("debian-packages.tsv"))
        check("libfoo1\t1:2.0-3+b1\tarm64\tfoo\t1:2.0-3\tdebian\thttps://snapshot.debian.org/package/foo/1%3A2.0-3/"
              "\tyes" in deb, "a binNMU maps to its source package and version on snapshot.debian.org")
        check("raspi-utils\t20250101-1+rpt1\tarm64\traspi-utils\t20250101-1+rpt1\traspberrypi\t"
              "https://archive.raspberrypi.com/debian/pool/main/r/raspi-utils/\tyes" in deb,
              "a Raspberry Pi archive package points to that archive")
        check("\ngone\t" not in deb, "removed packages (config-files only) are not listed")
    finally:
        im.close()


def test_generator_is_deterministic():
    a, b = Image(), Image()
    try:
        b.man["components"].reverse()
        b.write()
        for im in (a, b):
            rc, out = im.build()
            check(rc == 0, "build ok (%s)" % out.strip().splitlines()[-1:])
        check(tree_digest(a.doc()) == tree_digest(b.doc()), "two builds (manifest order reversed) are byte-identical")
        first = tree_digest(a.doc())
        with open(a.doc("stale.txt"), "w") as f:
            f.write("old")
        a.build()
        check(tree_digest(a.doc()) == first, "a rebuild over an old folder gives the same tree (stale files go)")
    finally:
        a.close()
        b.close()


def test_strict_mode_lists_release_blockers():
    im = Image()
    try:
        rc, out = im.build("--strict")
        check(rc == 1, "--strict exits 1 while release blockers remain (rc %s)" % rc)
        check("es-de:" not in out, "the inventoried ES-DE AppImage is no release blocker (#276)")
        for want in ("fbneo: no BUILDINFO", "retroarch: no BUILDINFO"):
            check("FAIL: release blocker: " + want in out, "--strict names: %s" % want)
        check("glsl-shaders:" not in out, "glsl-shaders is pinned in chroot-setup.sh too (#277): no blocker")
    finally:
        im.close()


def test_gate_record_says_how_the_image_was_built():
    """#287: GATE.tsv is the image's own record of the gate; the release pipeline reads it, never the caller."""
    im = Image()
    try:
        rc, _out = im.build()
        g = L.read_gate(im.doc(L.GATE_FILE))
        check(rc == 0 and g.get("strict") == "off" and int(g.get("blockers", "0")) > 0 and g.get("version"),
              "a non-strict build records strict off and its blocker count (%s)" % g)
        rc, _out = im.build("--strict")
        g = L.read_gate(im.doc(L.GATE_FILE))
        check(rc == 1 and g.get("strict") == "on" and g.get("blockers") != "0",
              "BROKEN: a strict build with blockers records them (exit 1, blockers %s)" % g.get("blockers"))
        check(L.read_gate(os.path.join(im.root, "nope")) == {}, "no record reads as {}")
    finally:
        im.close()


def test_buildinfo_is_copied_when_its_commit_matches():
    im = Image()
    try:
        os.makedirs(os.path.join(im.build_dir, "retroarch", "out"))
        with open(os.path.join(im.build_dir, "retroarch", "out", "BUILDINFO"), "w") as f:
            f.write("tag: v1.22.2\ncommit: 69a4f0ea1e8aaf442ae4858f2e7f2b31a1776576\npatches:\n  0001-x.patch\n")
        rc, out = im.build()
        check(rc == 0 and "patches:" in _read(im.doc("retroarch", "BUILDINFO")), "a matching BUILDINFO is copied")
        check("retroarch: no BUILDINFO" not in out, "and is no longer a blocker")
    finally:
        im.close()


def test_fetch_checks_sha256():
    man, texts = fixture_manifest()
    by_url = {lf["url"]: texts[lf["sha256"]] for c in man["components"] for lf in c["licence_files"] if "url" in lf}
    with tempfile.TemporaryDirectory() as t:
        n, warns = L.fetch(man, t, opener=lambda u: by_url[u])
        check(n == len(set(texts)) and warns == [], "fetch stores each text once by sha256 (%d)" % n)
        check(L.fetch(man, t, opener=lambda u: 1 / 0) == (0, []), "a second fetch downloads nothing")


def test_built_components_pin_licence_urls_to_their_commit():
    for c in L.load_manifest()["components"]:
        if c["origin"] == "built":
            urls = [lf["url"] for lf in c["licence_files"] if "url" in lf]
            check(urls and all(c["ref"] in u for u in urls), "%s: licence URL uses the commit, not a tag" % c["id"])


def test_broken_licence_url_at_another_ref():
    """#278: the commit (or tag) inside a raw licence URL must be the component's ref."""
    check(L.licence_url_ref("https://raw.githubusercontent.com/o/r/%s/COPYING" % ("a" * 40)) == "a" * 40
          and L.licence_url_ref("https://gitlab.com/g/p/-/raw/v3.5.0/LICENSE") == "v3.5.0"
          and L.licence_url_ref("https://example.invalid/LICENSE") is None, "the ref is read from GitHub and GitLab raw URLs")
    man = L.load_manifest()
    check(not any("is read at" in p for p in L.manifest_problems(man, ROOT)), "every licence URL is read at its ref")
    ra = [c for c in man["components"] if c["id"] == "retroarch"][0]
    ra["licence_files"][0]["url"] = ra["licence_files"][0]["url"].replace(ra["ref"], "b" * 40)
    esde = [c for c in man["components"] if c["id"] == "es-de"][0]
    esde["licence_files"][0]["url"] = esde["licence_files"][0]["url"].replace("/raw/v3.5.0/", "/raw/v3.4.0/")
    probs = L.manifest_problems(man, ROOT)
    check(any(p.startswith("retroarch: licence") and "b" * 40 in p for p in probs),
          "BROKEN: a GitHub licence URL at another commit than the ref is refused")
    check(any(p.startswith("es-de: licence") and "v3.4.0" in p for p in probs),
          "BROKEN: a GitLab licence URL at another tag than the ref is refused")


def _with_env(value, fn):
    old = os.environ.get("HK_LICENCES_STRICT")
    try:
        if value is None:
            os.environ.pop("HK_LICENCES_STRICT", None)
        else:
            os.environ["HK_LICENCES_STRICT"] = value
        return fn()
    finally:
        if old is None:
            os.environ.pop("HK_LICENCES_STRICT", None)
        else:
            os.environ["HK_LICENCES_STRICT"] = old


def test_only_hk_licences_strict_1_is_strict():
    im = Image()
    try:
        for value in (None, "", "0", "true", "yes"):
            rc, out = _with_env(value, im.build)
            check(rc == 0 and "strict off" in out and "WARN: release blocker" in out,
                  "HK_LICENCES_STRICT=%r: strict off, blockers are WARN (rc %s)" % (value, rc))
        rc, out = _with_env("1", im.build)
        check(rc == 1 and "strict on" in out and "FAIL: release blocker" in out, "HK_LICENCES_STRICT=1: strict on")
        shutil.rmtree(im.cache)
        os.makedirs(im.cache)
        rc, out = _with_env("0", im.build)
        check(rc == 0, "HK_LICENCES_STRICT=0 with an empty cache: WARN only (rc %s)" % rc)
        rc, out = _with_env("1", im.build)
        check(rc == 2 and "REFUSED" in out, "BROKEN: HK_LICENCES_STRICT=1 with an empty cache refuses (rc %s)" % rc)
    finally:
        im.close()


def test_offline_non_strict_build_never_fails():
    im = Image()
    try:
        man = json.load(open(im.manifest))
        n, warns = L.fetch(man, os.path.join(im.tmp, "offline"), opener=lambda u: (_ for _ in ()).throw(OSError("no net")))
        check(n == 0 and warns and all("cannot download" in w for w in warns),
              "offline fetch: every text is a warning, nothing raised (%d warnings)" % len(warns))
        shutil.rmtree(im.cache)
        os.makedirs(im.cache)                       # no network and no cache: an empty cache dir
        rc, out = im.build()
        check(rc == 0 and "WARN: licences: retroarch: licence text COPYING is missing" in out,
              "BROKEN: no cache (offline) is a WARN without strict, rc 0 (rc %s)" % rc)
        check(os.path.isfile(im.doc("LICENCES.md")) and os.path.isfile(os.path.join(im.root, "boot", "firmware",
                                                                                     "LICENCES.md")),
              "the bundle is still written (LICENCES.md, from what is there)")
        rows = [r.split("\t") for r in _read(im.doc("components.tsv")).splitlines() if not r.startswith("#")]
        ra = [r for r in rows if r[0] == "retroarch"][0]
        nd = [r for r in rows if r[0] == "coindojo"][0]
        check(ra[7] == "missing" and nd[7] == "LICENSE,LICENSE-art", "components.tsv lists only the texts copied")
        rc, out = im.build("--strict")
        check(rc == 2, "BROKEN: the same offline build refuses under strict (rc %s)" % rc)
    finally:
        im.close()


def test_buildinfo_is_normalised():
    a, b = Image(), Image()
    try:
        for im, date, addr in ((a, "2026-11-01T10:00:00Z", "0x0000ffff8a2b0000"), (b, "2026-11-19T22:13:07Z",
                                                                                 "0x0000ffffa1c40000")):
            os.makedirs(os.path.join(im.build_dir, "stella", "out"))
            with open(os.path.join(im.build_dir, "stella", "out", "BUILDINFO"), "w") as f:
                f.write("tag: 7.0\ncommit: d55b1aec0d067a4c901a6dcdf81cb8f579685659\ndate: %s\nldd:\n"
                        "  libc.so.6 => /lib/aarch64-linux-gnu/libc.so.6 (%s)\n" % (date, addr))
            check(im.build()[0] == 0, "build ok")
        got = _read(a.doc("stella", "BUILDINFO"))
        check(got == _read(b.doc("stella", "BUILDINFO")) and "date:" not in got and "0x" not in got
              and "commit: d55b1aec" in got, "BUILDINFO is copied without its date and ldd addresses")
        check(tree_digest(a.doc()) == tree_digest(b.doc()), "two builds of the same outputs at other times are "
              "byte-identical")
    finally:
        a.close()
        b.close()


def test_broken_component_id_is_refused_everywhere():
    man = L.load_manifest()
    evil = copy.deepcopy([c for c in man["components"] if c["id"] == "box64"][0])
    evil["id"] = "../../etc"
    evil["licence_files"][0]["url"] = "https://example.invalid/evil"
    man["components"].append(evil)
    check(any("id '../../etc' does not match" in p for p in L.manifest_problems(man, ROOT)),
          "BROKEN: a component id with a path in it fails check")
    asked = []
    with tempfile.TemporaryDirectory() as t:
        try:
            L.fetch(man, t, opener=lambda u: asked.append(u) or b"", strict=True)
            check(False, "BROKEN: strict fetch refuses a broken manifest before any download")
        except L.Refused:
            check(asked == [], "BROKEN: strict fetch refuses a broken manifest before any download")
        L.fetch(man, t, opener=lambda u: asked.append(u) or b"x")
        check("https://example.invalid/evil" not in asked, "non-strict fetch skips the unsafe component's URL")
    http = L.load_manifest()
    http["components"][0]["licence_files"] = [{"name": "LICENSE", "url": "http://plain.example/LICENSE",
                                               "sha256": "0" * 64}]
    check(any("https url" in p for p in L.manifest_problems(http, ROOT)), "BROKEN: a plain http licence URL fails")
    im = Image()
    try:
        bad = copy.deepcopy([c for c in im.man["components"] if c["id"] == "coindojo"][0])
        bad["id"] = "../escape"
        im.man["components"].append(bad)
        im.write()
        rc, out = im.build()
        check(rc == 0 and "does not match" in out and not os.path.exists(os.path.join(im.root, "usr", "share", "doc",
                                                                                       "coindojo", "escape")),
              "BROKEN: build warns and writes nothing for a bad id (non-strict)")
        check(im.build("--strict")[0] == 2, "BROKEN: strict build refuses a bad id")
    finally:
        im.close()


# --------------------------------------------------------------------------------------------- integration

def test_provision_and_verify_use_it():
    prov = _read(os.path.join(SKILL, "scripts", "provision.sh"))
    run = _read(os.path.join(SKILL, "scripts", "run.sh"))
    check('LIC="$REPO/skills/build-image/scripts/licences.py"' in prov and '"$LIC" fetch' in prov
          and '"$LIC" build' in prov, "provision.sh fetches and builds the bundle")
    check(0 < prov.find('"$LIC" build') < prov.find("apt-get clean"), "it runs before the seal")
    check('[ "${HK_LICENCES_STRICT:-}" = 1 ]' in prov and "${HK_LICENCES_STRICT:+" not in prov,
          "provision.sh: only HK_LICENCES_STRICT=1 is strict (=0 is not)")
    check(prov.count('|| echo "WARN: licences') == 2, "provision.sh: without strict a failing licence step only warns")
    check(run.count("-e HK_LICENCES_STRICT") == 2, "HK_LICENCES_STRICT reaches provision.sh and verify.sh")
    ver = _read(os.path.join(SKILL, "scripts", "verify.sh"))
    for want in ("usr/share/doc/coindojo/licences", "components.tsv", "debian-packages.tsv", "LICENCES.md"):
        check(want in ver, "verify.sh checks %s" % want)
    lic = ver.split("# >>> licences")[1].split("# <<< licences")[0]
    check('[ "${HK_LICENCES_STRICT:-}" = 1 ]' in lic and 'echo "FAIL' not in lic.replace('then echo "FAIL: $*"', ""),
          "verify.sh: licence problems FAIL only under HK_LICENCES_STRICT=1, else WARN")


# --------------------------------------------------------------------------------------------- broken cases

def test_broken_planted_unlisted_core():
    im = Image()
    try:
        planted = os.path.join(im.root, im.man["core_dir"].lstrip("/"), "planted_libretro.so")
        with open(planted, "wb") as f:
            f.write(ELF)
        rc, out = im.build("--strict")
        check(rc == 2 and "planted_libretro.so" in out, "BROKEN: strict: a planted core without a manifest entry "
              "stops the build (rc %s)" % rc)
        check(not os.path.exists(im.doc("LICENCES.md")), "and nothing is written")
        rc, out = im.build()
        check(rc == 0 and "WARN: licences: not in the licence manifest" in out and "planted_libretro.so" in out,
              "BROKEN: not strict: the planted core is a WARN, the build goes on (rc %s)" % rc)
        os.remove(planted)
        emu = os.path.join(im.root, "data", "emulators", "newemu", "newemu")
        os.makedirs(os.path.dirname(emu))
        with open(emu, "wb") as f:
            f.write(ELF)
        rc, out = im.build("--strict")
        check(rc == 2 and "/data/emulators/newemu/newemu" in out, "BROKEN: an unlisted emulator binary stops it too")
    finally:
        im.close()


def test_broken_core_without_component():
    man = L.load_manifest()
    probs = L.manifest_problems(man, ROOT, L.launcher_cores() + ["planted"])
    check(probs == ["core planted is installed by the image but has no component (licence, source)"],
          "BROKEN: a launcher core with no component is caught (%s)" % probs)
    man["components"] = [c for c in man["components"] if c["id"] != "snes9x"]
    check(any("core snes9x" in p for p in L.manifest_problems(man, ROOT, L.launcher_cores())),
          "BROKEN: removing a core's component is caught")


def test_broken_component_without_licence_or_source():
    man = L.load_manifest()
    nest = [c for c in man["components"] if c["id"] == "nestopia"][0]
    nest["licence_files"] = []
    nest["source"] = "github.com/libretro/nestopia"
    probs = L.manifest_problems(man, ROOT)
    check(any("nestopia: no licence text" in p for p in probs), "BROKEN: a component without a licence text")
    check(any("nestopia: source" in p for p in probs), "BROKEN: a source that is not an https URL")
    nest["spdx"] = "GPL-2+"
    check(any("unknown SPDX id 'GPL-2+'" in p for p in L.manifest_problems(man, ROOT)), "BROKEN: a non-SPDX licence")


def test_broken_build_pin_moved():
    man = L.load_manifest()
    [c for c in man["components"] if c["id"] == "stella"][0]["ref"] = "0" * 40
    probs = L.manifest_problems(man, ROOT)
    check(any("stella: build/stella/build.sh pins STELLA_COMMIT=" in p for p in probs),
          "BROKEN: a manifest ref that is not the build script's pin is caught (%s)" % probs)
    man = L.load_manifest()
    fb = [c for c in man["components"] if c["id"] == "fbneo"][0]
    fb["origin"], fb["ref"] = "buildbot", "latest"
    check(any("fbneo: a buildbot nightly has no exact ref" in p for p in L.manifest_problems(man, ROOT)),
          "BROKEN: a buildbot core claiming an exact ref is caught")


def test_broken_changed_licence_text():
    im = Image()
    try:
        lf = [c for c in im.man["components"] if c["id"] == "retroarch"][0]["licence_files"][0]
        with open(os.path.join(im.cache, lf["sha256"]), "w") as f:
            f.write("tampered")
        rc, out = im.build("--strict")
        check(rc == 2 and "retroarch: licence text COPYING" in out, "BROKEN: strict: a changed licence text stops it")
        rc, out = im.build()
        check(rc == 0 and "WARN: licences: retroarch: licence text COPYING" in out
              and not os.path.exists(im.doc("retroarch", "COPYING")), "not strict: WARN, the changed text is not copied")
        man, _texts = fixture_manifest()
        n, warns = L.fetch(man, os.path.join(im.tmp, "c2"), opener=lambda u: b"moved upstream")
        check(n == 0 and warns and all("pinned" in w for w in warns), "BROKEN: fetch keeps no text whose sha256 moved")
        try:
            L.fetch(man, os.path.join(im.tmp, "c3"), opener=lambda u: b"moved upstream", strict=True)
            check(False, "BROKEN: strict fetch refuses a text whose sha256 moved")
        except L.Refused as exc:
            check("pinned" in str(exc), "BROKEN: strict fetch refuses a text whose sha256 moved")
    finally:
        im.close()


def test_broken_buildinfo_from_another_commit():
    im = Image()
    try:
        os.makedirs(os.path.join(im.build_dir, "flycast", "out"))
        with open(os.path.join(im.build_dir, "flycast", "out", "BUILDINFO"), "w") as f:
            f.write("tag: v2.7\ncommit: %s\n" % ("1" * 40))
        rc, out = im.build("--strict")
        check(rc == 2 and "flycast" in out and "pins 5aa091fd" in out, "BROKEN: strict: a stale BUILDINFO stops it")
        rc, out = im.build()
        check(rc == 0 and "WARN: licences: flycast:" in out and "stale build" in out
              and not os.path.exists(im.doc("flycast", "BUILDINFO")), "BROKEN: not strict: a stale BUILDINFO is a "
              "WARN and is not copied (rc %s)" % rc)
    finally:
        im.close()


def test_broken_listed_component_missing():
    im = Image()
    try:
        os.remove(os.path.join(im.root, "data", "emulators", "flycast", "flycast"))
        rc, out = im.build("--strict")
        check(rc == 2 and "flycast is listed but not in the image" in out, "BROKEN: strict: a missing listed component")
        rc, out = im.build()
        check(rc == 0 and "WARN: licences: flycast is listed but not in the image" in out
              and not os.path.exists(im.doc("flycast")), "BROKEN: not strict: WARN, the component is left out")
    finally:
        im.close()


def test_broken_package_without_copyright_and_fightcade():
    im = Image()
    try:
        os.remove(os.path.join(im.root, "usr", "share", "doc", "foo-common", "copyright"))
        fc = os.path.join(im.root, "data", "fightcade", "opt", "wine", "bin")
        os.makedirs(fc)
        with open(os.path.join(fc, "wine"), "wb") as f:
            f.write(ELF)
        rc, out = im.build("--strict")
        check(rc == 1 and "packages without /usr/share/doc/<pkg>/copyright: libfoo1" in out,
              "BROKEN: a package without its copyright file is a release blocker")
        check("Fightcade binaries are in the image" in out, "BROKEN: Fightcade binaries in the image are a blocker")
        check("libfoo1\t" in _read(im.doc("debian-packages.tsv")) and "\tmissing\n" in _read(
            im.doc("debian-packages.tsv")), "the list marks it missing")
    finally:
        im.close()


def test_no_copyright_exceptions():
    """#293: packages that ship no copyright file upstream, exact version only."""
    real = L.no_copyright_exceptions()
    check(("raspberrypi-net-mods", "1.4.4") in real, "the real list exempts raspberrypi-net-mods 1.4.4")
    check(("raspberrypi-net-mods", "1.4.5") not in real, "BROKEN: another version is not exempt")
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "nc.tsv")
        with open(p, "w") as f:
            f.write("# c\nfoo\t1.0\tno file upstream\thttps://example.invalid/foo/\n")
        check(L.no_copyright_exceptions(p) == {("foo", "1.0")}, "a row is read")
        with open(p, "w") as f:
            f.write("foo\t1.0\t\thttps://example.invalid/foo/\n")
        try:
            L.no_copyright_exceptions(p)
            check(False, "BROKEN: a row without a reason is refused")
        except L.Refused:
            check(True, "BROKEN: a row without a reason is refused")
        check(L.no_copyright_exceptions(os.path.join(t, "none.tsv")) == set(), "no file: no exceptions")


def _plant(root, img_path, data=ELF):
    host = os.path.join(root, img_path.lstrip("/"))
    os.makedirs(os.path.dirname(host), exist_ok=True)
    with open(host, "wb") as f:
        f.write(data)
    return host


def _box64_image():
    """An image with our Box64 build and every inventoried x64lib file under /data/fightcade (a first-launch image)."""
    im = Image()
    for c in im.man["components"]:
        if c["id"] == "box64" or c["id"].startswith("box64-x64lib-"):
            for p in c["paths"]:
                _plant(im.root, p, ELF + c["id"].encode())
    return im


def test_x64lib_is_inventoried():
    """#300: every file Box64 ships in x64lib/ (build/box64/build.sh copies the whole folder) has a component with
    a licence text; the libraries' families are each their own component (their licences sit at their own refs)."""
    man = L.load_manifest()
    x64 = [c for c in man["components"] if c["id"].startswith("box64-x64lib-")]
    files = sorted(os.path.basename(p) for c in x64 for p in c["paths"])
    want = sorted(["libcrypto.so.1.0.0", "libssl.so.1.0.0", "libcrypto.so.1.1", "libssl.so.1.1",
                   "libmbedcrypto.so.7", "libmbedcrypto.so.3", "libmbedtls.so.14", "libmbedtls.so.12",
                   "libmbedx509.so.1", "libmbedx509.so.0", "libstdc++.so.5", "libstdc++.so.6", "libgcc_s.so.1",
                   "libpng12.so.0", "libunwind.so.8"])
    check(files == want, "components.json lists every x64lib file (%s)" % files)
    for c in x64:
        check(all(p.startswith("/data/fightcade/opt/box64/x64lib/") for p in c["paths"]) and c.get("optional")
              and c["licence_files"] and COMMIT_RX.match(c["ref"]) and "ptitSeb/box64/tree/" in c["download"],
              "%s: optional, under x64lib/, licence text, exact upstream ref, names the Box64 path" % c["id"])
    gcc = [c for c in x64 if c["id"] == "box64-x64lib-gcc-runtime"][0]
    check(gcc["spdx"] == "GPL-3.0-or-later WITH GCC-exception-3.1"
          and {lf["name"] for lf in gcc["licence_files"]} == {"COPYING3", "COPYING.RUNTIME"},
          "the GCC runtime ships the GPL-3.0 and the Runtime Library Exception texts")
    md = L.summary_md("v9", x64, [], "x")
    check("every component in `components.tsv`" in md, "the written offer covers the x64lib components")
    box64 = [c for c in man["components"] if c["id"] == "box64"][0]
    check(not box64.get("covers"), "box64 claims only its own binary, not the whole opt/box64 tree")


def test_box64_under_data_fightcade_is_not_a_fightcade_binary():
    """#298, #300: a first-launch image carries our Box64 build in /data/fightcade/opt/box64; that is not bundled
    Fightcade. Only the files a present component claims are exempt; any other program file there still blocks."""
    im = _box64_image()
    try:
        rc, out = im.build("--strict")
        check("Fightcade binaries are in the image" not in out, "the inventoried x64lib files are no blocker")
        tsv = _read(im.doc("components.tsv"))
        check("box64-x64lib-gcc-runtime\t" in tsv and "/data/fightcade/opt/box64/x64lib/libstdc++.so.6=" in tsv,
              "components.tsv lists the x64lib files with their sha256")
        check(os.path.isfile(im.doc("box64-x64lib-gcc-runtime", "COPYING.RUNTIME")), "the exception text ships")
        os.symlink("libssl.so.1.1", os.path.join(im.root, "data/fightcade/opt/box64/x64lib/libssl.so"))
        rc, out = im.build("--strict")
        check("Fightcade binaries are in the image" not in out, "a symlink to an inventoried library is no blocker")
    finally:
        im.close()


def test_broken_uninventoried_elf_under_box64_blocks():
    im = _box64_image()
    try:
        _plant(im.root, "/data/fightcade/opt/box64/x64lib/libnew.so.1")
        rc, out = im.build("--strict")
        check(rc == 1 and "Fightcade binaries are in the image" in out
              and "/data/fightcade/opt/box64/x64lib/libnew.so.1" in out,
              "BROKEN: an ELF under opt/box64 no component claims still blocks, and is named")
    finally:
        im.close()
    im = _box64_image()
    try:
        _plant(im.root, "/data/fightcade/opt/box64/box64-helper")
        rc, out = im.build("--strict")
        check(rc == 1 and "/data/fightcade/opt/box64/box64-helper" in out,
              "BROKEN: an extra ELF beside box64 blocks too")
    finally:
        im.close()
    im = _box64_image()   # a component only partly in the image is not present, so it exempts nothing
    try:
        os.remove(os.path.join(im.root, "data", "fightcade", "opt", "box64", "x64lib", "libssl.so.1.1"))
        rc, out = im.build("--strict")
        check(rc == 1 and "/data/fightcade/opt/box64/x64lib/libcrypto.so.1.1" in out,
              "BROKEN: libcrypto.so.1.1 without libssl.so.1.1: its component is not present and the file blocks")
    finally:
        im.close()


def test_broken_wine_under_data_fightcade_blocks():
    im = _box64_image()
    try:
        _plant(im.root, "/data/fightcade/opt/wine/bin/wine")
        rc, out = im.build("--strict")
        check(rc == 1 and "Fightcade binaries are in the image" in out and "/data/fightcade/opt/wine/bin/wine" in out,
              "BROKEN: Wine next to Box64 still blocks")
    finally:
        im.close()
    im = _box64_image()
    try:
        _plant(im.root, "/data/fightcade/Fightcade/emulator/fbneo/fcadefbneo")
        rc, out = im.build("--strict")
        check(rc == 1 and "Fightcade binaries are in the image" in out,
              "BROKEN: a Fightcade emulator elsewhere under /data/fightcade blocks")
    finally:
        im.close()


def test_broken_tip_link_in_image_or_release_body():
    im = Image()
    try:
        rc, out = im.build("--strict")
        check("tip link" not in out, "a clean image has no tip link")
        fb = im.doc("fbneo")
        rc, out = run_cli(["release-check", "--root", im.root])
        check(rc == 0 and os.path.isdir(fb), "release-check passes a clean image (FBNeo's licence text is not read)")
        readme = os.path.join(im.root, "data", "coin-dojo", "README.md")
        os.makedirs(os.path.dirname(readme))
        with open(readme, "w") as f:
            f.write("# Coin Dojo\n\nLike it? Tips for tokens: `https://ko-fi.com/someone`\n")
        rc, out = im.build("--strict")
        check(rc == 1 and "tip link in the image: /data/coin-dojo/README.md:3" in out,
              "BROKEN: a README with the tip link in the image is a release blocker")
        rc, out = run_cli(["release-check", "--root", im.root])
        check(rc == 1 and "/data/coin-dojo/README.md:3: ko-fi.com" in out, "BROKEN: release-check refuses it")
        os.remove(readme)
        with open(os.path.join(im.root, "boot", "firmware", "START-HERE.txt"), "w") as f:
            f.write("support: https://www.patreon.com/x\n")
        rc, out = run_cli(["release-check", "--root", im.root])
        check(rc == 1 and "START-HERE.txt" in out, "BROKEN: a donation link on the boot partition is refused")
        body = os.path.join(im.tmp, "body.md")
        with open(body, "w") as f:
            f.write("## [1.0.0]\n\n### Added\n\n- The image.\n"
                    "- The image's README drops the \"Tips for tokens\" heading; the repo README keeps it.\n")
        check(run_cli(["release-check", "--body", body])[0] == 0, "a release body without a tip link passes, even "
              "when a changelog line names the README's \"Tips for tokens\" heading (only URLs/domains count)")
        check(L.tip_link_problems("Like it? Tips for tokens.", "x") == [], "the phrase alone is not a tip link")
        with open(body, "a") as f:
            f.write("\nSupport the project: https://ko-fi.com/someone\n")
        rc, out = run_cli(["release-check", "--body", body])
        check(rc == 1 and "body.md:8: ko-fi.com" in out, "BROKEN: a release body with the Ko-fi link is refused")
        check(run_cli(["release-check"])[0] == 2, "release-check with nothing to read is a usage error")
    finally:
        im.close()


def test_repo_readme_may_keep_the_tip_link():
    readme = _read(os.path.join(ROOT, "README.md"))
    # The repo README may carry a tip link (building from source does not distribute FBNeo). It has none until the
    # maintainer adds one; whenever it does, the release-check pattern must see it.
    if "ko-fi.com" in readme:
        check(L.tip_link_problems(readme, "README.md") != [], "the repo README's tip link is seen by the pattern")
    check("Tips are voluntary and unlock nothing" in readme, "the README keeps the voluntary-tips line")
    check(L.tip_link_problems("**Buy me a coffee:** https://ko-fi.com/someone", "README.md") != [],
          "BROKEN: a README-style tip line with a Ko-fi link is seen by the pattern")
    comp = _read(os.path.join(ROOT, "COMPLIANCE.md"))
    section = " ".join(comp.split("### Tip links")[1].split("\n### ")[0].split())
    check("release-check" in section and "Not legal advice" in section,
          "COMPLIANCE.md explains the tip-link rule with the caveat")


def test_broken_symlink_escape_stays_in_root():
    with tempfile.TemporaryDirectory() as t:
        os.makedirs(os.path.join(t, "usr", "share", "doc"))
        os.symlink("/etc", os.path.join(t, "usr", "share", "doc", "evil"))
        check(not L.exists_in_root(t, "/usr/share/doc/evil/passwd"),
              "BROKEN: an absolute symlink resolves inside the image, not on the build host")



# --------------------------------------------------------------------------------------------- ES-DE's libraries

def test_esde_inventory_is_complete():
    es = [c for c in L.load_manifest()["components"] if c["id"] == "es-de"][0]
    rows, probs = L.load_libraries(os.path.join(ROOT, es["libraries"]))
    check(probs == [] and len(rows) == 201, "esde-libraries.tsv: 201 libraries, well-formed %s" % (probs or ""))
    check(all(r["spdx"] and r["version"] and r["source_url"].startswith("https://") for r in rows),
          "every library has a licence, an exact version and a source")
    av = [r for r in rows if r["file"].startswith("usr/lib/libav")]
    check(av and all(r["spdx"] == "GPL-2.0-or-later" and r["owes"] == "source" for r in av),
          "Ubuntu's ffmpeg is a GPL build (--enable-gpl): libav* owe their source")
    copyleft = [r["file"] for r in rows if r["owes"] == "notice" and
                all(any(t in a for t in ("GPL-", "MPL-", "CDDL-")) for a in r["spdx"].split(" OR "))]
    check(copyleft == [], "no copyleft-only library is marked notice %s" % (copyleft or ""))
    tree = os.environ.get("HK_ESDE_TREE")   # an extracted AppImage (squashfs-root), when one is at hand
    if not tree:
        skip("HK_ESDE_TREE not set: the inventory is not compared with a real AppImage tree")
        return
    probs = L.library_problems(os.path.abspath(tree), dict(es, libraries_root="/"), rows)
    check(probs == [], "the real AppImage tree (%s) matches the inventory %s" % (tree, probs[:5] or ""))


def test_bundle_gets_the_library_list():
    im = Image()
    try:
        rc, out = im.build()
        check(rc == 0 and "libbundled" not in out, "a listed library passes (%s)" % out.strip().splitlines()[-1:])
        lib = _read(im.doc("es-de", "libraries.tsv"))
        check(lib.startswith("# file\tpackage") and "usr/lib/libbundled.so.1\tlibbundled1\t1.0-1\t" in lib,
              "es-de/libraries.tsv lists each library with package, version, licence, source")
        md = _read(im.doc("LICENCES.md"))
        check("## Libraries bundled in ES-DE" in md and "1 libraries of its own (1 of them GPL" in md
              and "every library listed in a `libraries.tsv`" in md, "LICENCES.md names them and the offer covers them")
    finally:
        im.close()


def test_broken_unlisted_or_changed_library_in_the_appimage():
    im = Image()
    try:
        lib = os.path.join(im.root, ESDE, "usr", "lib")
        with open(os.path.join(lib, "libplanted.so.3"), "wb") as f:
            f.write(ELF + b"planted")
        rc, out = im.build("--strict")
        check(rc == 2 and "es-de bundles /opt/ES-DE/squashfs-root/usr/lib/libplanted.so.3, which its inventory" in out,
              "BROKEN: strict: a planted unlisted library stops the build (rc %s)" % rc)
        rc, out = im.build()
        check(rc == 0 and "WARN: licences: es-de bundles" in out, "BROKEN: not strict: a WARN")
        os.remove(os.path.join(lib, "libplanted.so.3"))
        with open(os.path.join(lib, "libbundled.so.1"), "ab") as f:
            f.write(b"another build")
        rc, out = im.build("--strict")
        check(rc == 2 and "libbundled.so.1 changed" in out, "BROKEN: a changed library (another ES-DE) is refused")
        os.remove(os.path.join(lib, "libbundled.so.1"))
        rc, out = im.build("--strict")
        check(rc == 2 and "in the inventory but not in the image" in out, "BROKEN: a listed library gone is refused")
    finally:
        im.close()


def test_broken_library_without_copyright_file():
    im = Image()
    try:
        os.remove(os.path.join(im.root, ESDE, "usr", "share", "doc", "libbundled1", "copyright"))
        rc, out = im.build("--strict")
        check(rc == 2 and "no usr/share/doc/libbundled1/copyright in the bundle" in out,
              "BROKEN: a library whose copyright file is gone is refused")
    finally:
        im.close()


def test_broken_inventory_rows():
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "libs.tsv")
        cases = [
            (lib_row("libx.so.1", b"x", spdx="GPL-2.0-or-later", owes="notice"), "the release owes its source"),
            (lib_row("libx.so.1", b"x", spdx="Beerware-ish"), "unknown SPDX id"),
            (lib_row("../../etc/x.so", b"x"), "not a plain path under usr/lib"),
            (lib_row("libx.so.1", b"x").replace("https://", "http://"), "is not https"),
            (lib_row("libx.so.1", b"x").rsplit("\t", 1)[0] + "\tnope\n", "not 64 hex digits"),
            ("usr/lib/libx.so.1\tonly-two\n", "columns, want 9"),
            (lib_row("libx.so.1", b"x", owes="maybe"), "owes 'maybe'"),
            (lib_row("libx.so.1", b"x") * 2, "listed twice"),
            ("", "lists nothing"),
        ]
        for body, want in cases:
            with open(p, "w") as f:
                f.write(LIB_HEAD + body)
            _rows, probs = L.load_libraries(p)
            check(any(want in x for x in probs), "BROKEN: inventory %r: %s (%s)" % (body[:40], want, probs))
        with open(p, "w") as f:
            f.write(LIB_HEAD + lib_row("libx.so.1", b"x", spdx="Apache-2.0 OR GPL-2.0-or-later", owes="notice"))
        check(L.load_libraries(p)[1] == [], "a permissive alternative (OR) may be notice only")
        man, _texts = fixture_manifest()
        es = [c for c in man["components"] if c["id"] == "es-de"][0]
        es["libraries"] = os.path.join(t, "nope.tsv")
        check(any("es-de: library inventory" in x for x in L.manifest_problems(man, ROOT)),
              "BROKEN: licences.py check refuses a missing inventory")


if __name__ == "__main__":
    main(globals())
