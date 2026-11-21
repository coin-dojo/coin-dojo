#!/usr/bin/env python3
"""The image's licence folder and source list (#254, #267): what a built image redistributes, under which licence,
and where its exact source is. Generated at image build from ../licences/components.json and the image's own dpkg
database, never hand-copied.

    licences.py check [--repo DIR]                       the manifest is complete (every launcher core, build pins)
    licences.py fetch --cache DIR [--repo DIR] [--strict]   download the pinned licence texts (sha256-checked)
    licences.py build --root R --cache DIR [--repo DIR] [--boot B] [--version V] [--offer-contact TEXT] [--strict]
    licences.py release-check [--body FILE ...] [--root R [--boot B]]   no tip/donation link where FBNeo ships (exit 1)

build writes, into the image root R (the data partition mounted at R/data, as provision.sh mounts it):
  R/usr/share/doc/coindojo/licences/LICENCES.md          the summary (also R/boot/firmware/LICENCES.md, or --boot)
  .../components.tsv                                       id, version, SPDX, origin, source, exact ref, sha256
  .../debian-packages.tsv                                  every installed package, exact version, its source link
  .../<id>/<licence file>                                  each component's licence texts, byte for byte
  .../<id>/BUILDINFO                                       for what we build: the build's own record (commit, patches)
  .../GATE.tsv                                             the gate it ran under: strict on/off, warnings, blockers (#287)
  .../<id>/libraries.tsv                                   for a bundle (the ES-DE AppImage, #276): every library in it,
                                                           its package, version, licence, what the release owes, source
The output depends only on its inputs: sorted, no dates (BUILDINFO's `date:` line and ldd addresses are dropped), so
two builds of the same inputs are byte-identical.

Strict mode (a public release) is --strict or HK_LICENCES_STRICT=1 exactly (0, empty or unset: off).
Without strict, the licence step never fails a build (offline personal builds keep working): every problem is a WARN
line and the bundle is written from what is there. Strict refuses (exit 2, nothing written) an ELF file under the
manifest's scan dirs that no component and no package accounts for (a core added without a licence row), a listed
component missing from the image, a licence text missing from the cache or changed, a BUILDINFO from another commit,
a broken manifest or component id; and (exit 1) what blocks a public download: a component without an exact source
ref (buildbot nightlies), open "blockers", a package without its copyright file, Fightcade binaries in the image, a
missing BUILDINFO, a tip or donation link in the image's docs (FBNeo's licence; see TIP_LINK).
A component with "libraries" (a TSV next to the manifest, see LIB_COLS) is a bundle of other people's libraries: each
ELF file under its "libraries_root" must have a row whose sha256 matches (an unlisted or changed library is refused
like an unlisted binary: a new ES-DE release needs a new inventory), and each row's copyright file must be in the
bundle (usr/share/doc/<package>/copyright).
Not legal advice; I'm not a lawyer.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(SKILL))
MANIFEST = os.path.join(SKILL, "licences", "components.json")
DOC_DIR = "usr/share/doc/coindojo/licences"
GATE_FILE = "GATE.tsv"     # #287: strict on/off, warnings, blockers, version: what the release pipeline checks
ORIGINS = ("ours", "built", "buildbot", "download", "source")
SPDX_KNOWN = {"MIT", "CC-BY-SA-4.0", "OFL-1.1", "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-or-later",
              "LGPL-2.1-or-later", "BSD-3-Clause", "CC0-1.0", "MPL-2.0",
              # the ES-DE AppImage's libraries (#276)
              "GPL-3.0-only", "LGPL-2.0-only", "LGPL-2.0-or-later", "LGPL-2.1-only", "LGPL-3.0-or-later",
              "MPL-1.1", "CDDL-1.0", "AFL-2.1", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause-Clear",
              "BSD-3-Clause-Attribution", "ISC", "Zlib", "Libpng", "libtiff", "IJG", "ICU", "OLDAP-2.8", "curl",
              "bzip2-1.0.6", "WTFPL", "GCC-exception-3.1", "PCRE2-exception",
              # Box64's bundled x86_64 libraries (#300)
              "OpenSSL"}
SHA = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
ID = re.compile(r"^[a-z0-9_-]+$")          # component ids name folders in the image
NAME = re.compile(r"^[A-Za-z0-9._-]+$")    # licence file names, likewise
LIB_FILE = re.compile(r"^usr/lib/[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)*$")   # a library's path inside its bundle
LIB_COLS = ("file", "package", "version", "source", "source_version", "spdx", "owes", "source_url", "sha256")
OWES = ("source", "notice")   # source: GPL/LGPL/MPL/CDDL, the written offer and the sources asset; notice: the text
CAVEAT = "Not legal advice; I'm not a lawyer."
DEFAULT_CONTACT = "open an issue on the project's repo"   # the maintainer 2026-11-20 (#254)


class Refused(Exception):
    """A problem that stops the build (exit 2)."""


# ------------------------------------------------------------------------------------------------ manifest

def load_manifest(path=MANIFEST):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def spdx_problems(expr):
    out = []
    for tok in re.split(r"\s+(?:AND|OR|WITH)\s+", expr.strip("() ")):
        tok = tok.strip("() ")
        if not tok or (tok not in SPDX_KNOWN and not re.match(r"^LicenseRef-[A-Za-z0-9.-]+$", tok)):
            out.append("unknown SPDX id %r" % tok)
    return out


def build_pin(repo, build, var):
    """The value of VAR="..." in <repo>/<build>/build.sh, or None."""
    try:
        with open(os.path.join(repo, build, "build.sh"), encoding="utf-8") as f:
            m = re.search(r'^%s="([^"]+)"' % re.escape(var), f.read(), re.M)
    except OSError:
        return None
    return m.group(1) if m else None


def component_safety(c):
    """What makes a component unsafe to fetch or write ([] = safe): its id and licence file names become paths in
    the image, its URLs are downloaded."""
    bad = []
    if not ID.match(str(c.get("id", ""))):
        bad.append("id %r does not match %s" % (c.get("id"), ID.pattern))
    for lf in c.get("licence_files", []):
        if not NAME.match(str(lf.get("name", ""))) or lf.get("name") in (".", ".."):
            bad.append("licence file name %r is not a plain file name" % lf.get("name"))
        if "repo" not in lf and not (str(lf.get("url", "")).startswith("https://")
                                     and SHA.match(str(lf.get("sha256", "")))):
            bad.append("licence file %s needs an https url and a sha256" % lf.get("name"))
    return bad


RAW_URL_REF = (re.compile(r"^https://raw\.githubusercontent\.com/[^/]+/[^/]+/([^/]+)/"),   # <owner>/<repo>/<ref>/<path>
               re.compile(r"^https://[^/]+/.+?/-/raw/([^/]+)/"))                          # GitLab: <project>/-/raw/<ref>/<path>


def licence_url_ref(url):
    """The commit or tag a raw licence URL reads (GitHub raw, GitLab raw), or None for any other URL."""
    for rx in RAW_URL_REF:
        m = rx.match(url or "")
        if m:
            return m.group(1)
    return None


def manifest_problems(man, repo=REPO, launcher_cores=None):
    """Everything wrong with the manifest ([] = OK). launcher_cores: the cores the image installs (bare names)."""
    bad = []
    ids = set()
    for c in man.get("components", []):
        cid = c.get("id", "?")
        if cid in ids:
            bad.append("%s: duplicate id" % cid)
        ids.add(cid)
        bad += ["%s: %s" % (cid, p) for p in component_safety(c)]
        for key in ("name", "version", "spdx", "origin", "source", "paths", "licence_files"):
            if key not in c or (key in ("name", "version", "spdx", "source") and not c[key]):
                bad.append("%s: no %s" % (cid, key))
        bad += ["%s: %s" % (cid, p) for p in spdx_problems(c.get("spdx", ""))]
        origin = c.get("origin")
        if origin not in ORIGINS:
            bad.append("%s: origin %r is not one of %s" % (cid, origin, ", ".join(ORIGINS)))
        if origin != "ours" and not str(c.get("source", "")).startswith("https://"):
            bad.append("%s: source %r is not an https URL" % (cid, c.get("source")))
        if not c.get("licence_files") and origin != "source":
            bad.append("%s: no licence text (licence_files is empty)" % cid)
        for lf in c.get("licence_files", []):
            if "repo" in lf and not os.path.isfile(os.path.join(repo, lf["repo"])):
                bad.append("%s: licence file %s is not in the repo" % (cid, lf["repo"]))
        if origin in ("built", "source") and not COMMIT.match(c.get("ref", "")):
            bad.append("%s: %s from source needs the exact commit as ref" % (cid, origin))
        if origin == "built":
            pin = build_pin(repo, c.get("build", ""), c.get("pin", ""))
            if pin is None:
                bad.append("%s: %s/build.sh has no %s" % (cid, c.get("build"), c.get("pin")))
            elif pin not in (c.get("ref"), c.get("tag")):
                bad.append("%s: %s/build.sh pins %s=%s, the manifest says %s" % (cid, c["build"], c["pin"], pin,
                                                                              c.get("tag") or c.get("ref")))
        for lf in c.get("licence_files", []):     # #278: a licence text read at another commit/tag than the ref
            at = licence_url_ref(lf.get("url", ""))
            if at and c.get("ref") and at != c["ref"]:
                bad.append("%s: licence %s is read at %s, but ref is %s (re-read it at the ref)"
                           % (cid, lf.get("name", "?"), at, c["ref"]))
        if origin == "buildbot" and c.get("ref"):
            bad.append("%s: a buildbot nightly has no exact ref; leave ref empty until it is built from a pin" % cid)
        if "libraries" in c:
            if not str(c.get("libraries_root", "")).startswith("/"):
                bad.append("%s: libraries needs an absolute libraries_root" % cid)
            _rows, lprobs = load_libraries(os.path.join(repo, c["libraries"]))
            bad += ["%s: %s" % (cid, p) for p in lprobs]
    if launcher_cores is not None:
        listed = {c["core"] for c in man.get("components", []) if c.get("core")}
        for core in sorted(set(launcher_cores) - listed):
            bad.append("core %s is installed by the image but has no component (licence, source)" % core)
        for core in sorted(listed - set(launcher_cores)):
            bad.append("component for core %s, which the image does not install" % core)
    return bad


def load_libraries(path):
    """The rows of a bundle's library inventory (LIB_COLS, tab-separated, '#' lines are comments), and its problems."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as exc:
        return [], ["library inventory %s: %s" % (path, exc)]
    rows, bad, seen = [], [], set()
    name = os.path.basename(path)
    for n, ln in enumerate(lines, 1):
        if not ln.strip() or ln.startswith("#"):
            continue
        vals = ln.split("\t")
        if len(vals) != len(LIB_COLS):
            bad.append("%s:%d: %d columns, want %d (%s)" % (name, n, len(vals), len(LIB_COLS), " ".join(LIB_COLS)))
            continue
        r = dict(zip(LIB_COLS, vals))
        where = "%s:%d %s" % (name, n, r["file"])
        if not LIB_FILE.match(r["file"]) or ".." in r["file"].split("/"):
            bad.append("%s: not a plain path under usr/lib" % where)
        if r["file"] in seen:
            bad.append("%s: listed twice" % where)
        seen.add(r["file"])
        bad += ["%s: %s" % (where, p) for p in spdx_problems(r["spdx"])]
        if r["owes"] not in OWES:
            bad.append("%s: owes %r is not one of %s" % (where, r["owes"], ", ".join(OWES)))
        elif r["owes"] == "notice" and re.search(r"\b(?:L?GPL|MPL|CDDL)-", r["spdx"]) and " OR " not in r["spdx"]:
            bad.append("%s: %s is copyleft, so the release owes its source (owes = source)" % (where, r["spdx"]))
        if not r["source_url"].startswith("https://"):
            bad.append("%s: source_url %r is not https" % (where, r["source_url"]))
        if not SHA.match(r["sha256"]):
            bad.append("%s: sha256 %r is not 64 hex digits" % (where, r["sha256"]))
        if not r["package"] or not r["version"]:
            bad.append("%s: no package or version" % where)
        rows.append(r)
    if not rows and not bad:
        bad.append("library inventory %s lists nothing" % name)
    return sorted(rows, key=lambda r: r["file"]), bad


def library_problems(root, c, rows):
    """What the bundle in the image says against its inventory: unlisted or changed ELF files, listed files missing,
    a package whose copyright file is not in the bundle ([] = it matches)."""
    base = in_root(root, c["libraries_root"])
    if base is None or not os.path.isdir(base):
        return ["%s: %s is not in the image" % (c["id"], c["libraries_root"])]
    listed = {r["file"]: r for r in rows}
    bad = []
    lib = os.path.join(base, "usr", "lib")
    for dirpath, dirs, files in os.walk(lib):
        dirs.sort()
        for fn in sorted(files):
            host = os.path.join(dirpath, fn)
            rel = os.path.relpath(host, base)
            if os.path.islink(host) or not is_elf(host):
                continue
            r = listed.get(rel)
            if r is None:
                bad.append("%s bundles %s/%s, which its inventory (%s) does not list" % (
                    c["id"], c["libraries_root"], rel, c["libraries"]))
            elif sha256_file(host) != r["sha256"]:
                bad.append("%s: %s/%s changed (sha256 %s, the inventory pins %s): another build? re-inventory it" % (
                    c["id"], c["libraries_root"], rel, sha256_file(host), r["sha256"]))
    for r in rows:
        if not os.path.isfile(os.path.join(base, r["file"])):
            bad.append("%s: %s/%s is in the inventory but not in the image" % (c["id"], c["libraries_root"],
                                                                               r["file"]))
        if not os.path.isfile(os.path.join(base, "usr", "share", "doc", r["package"], "copyright")):
            bad.append("%s: no usr/share/doc/%s/copyright in the bundle (the notice for %s)" % (
                c["id"], r["package"], r["file"]))
    return bad


def launcher_cores(repo=REPO):
    """Bare names of every libretro core the launcher runs (launch.py, via the cores role's table)."""
    sys.path.insert(0, os.path.join(repo, "ansible", "roles", "cores", "files"))
    try:
        import launcher_table  # noqa: PLC0415
    finally:
        sys.path.pop(0)
    t = launcher_table.table(launcher_table.load(os.path.join(repo, "skills", "collections", "launch.py")))
    return sorted(set(t["cores"]) | {os.path.basename(p)[:-len("_libretro.so")] for p in t["abs_cores"]})


# ------------------------------------------------------------------------------------------------ fetch

def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def url_files(man):
    return sorted({(lf["sha256"], lf["url"]) for c in man["components"] for lf in c["licence_files"] if "url" in lf})


def fetch(man, cache, opener=None, repo=REPO, strict=False):
    """Download each pinned licence text into cache/<sha256> (kept when it is already there and matches).

    The manifest is validated first: strict refuses a broken one before any download; otherwise only components that
    pass component_safety() (id, plain names, https + sha256) are fetched. A failed or changed download is a warning
    (offline personal builds keep working) and Refused under strict. Returns (downloaded, warnings)."""
    probs = manifest_problems(man, repo)
    if strict and probs:
        raise Refused("the manifest is broken: " + "; ".join(probs))
    warnings = ["manifest: %s" % p for p in probs]
    safe = {"components": [c for c in man.get("components", []) if not component_safety(c)]}
    opener = opener or (lambda u: urllib.request.urlopen(u, timeout=60).read())
    os.makedirs(cache, exist_ok=True)
    got = 0
    for sha, url in url_files(safe):
        dest = os.path.join(cache, sha)
        if os.path.isfile(dest) and sha256_file(dest) == sha:
            continue
        data, err = None, None
        for _try in range(3):
            try:
                data = opener(url)
                break
            except OSError as exc:
                err = exc
        if data is None:
            warnings.append("cannot download %s: %s" % (url, err))
            continue
        if sha256_bytes(data) != sha:
            warnings.append("%s has sha256 %s, pinned %s (the upstream text changed: read it, then re-pin)"
                            % (url, sha256_bytes(data), sha))
            continue
        with open(dest + ".part", "wb") as f:
            f.write(data)
        os.replace(dest + ".part", dest)
        got += 1
    if strict and warnings:
        raise Refused("; ".join(warnings))
    return got, warnings


# ------------------------------------------------------------------------------------------------ the image

def in_root(root, path):
    """Resolve an image path inside root (symlinks are followed inside the image, never on the build host)."""
    parts = [p for p in path.split("/") if p]
    cur = ""
    hops = 0
    while parts:
        cur = cur + "/" + parts.pop(0)
        host = os.path.join(root, cur.lstrip("/"))
        if os.path.islink(host):
            hops += 1
            if hops > 40:
                return None
            target = os.readlink(host)
            base = "/" if target.startswith("/") else os.path.dirname(cur)
            parts = [p for p in os.path.normpath(os.path.join(base, target)).split("/") if p] + parts
            cur = ""
    return os.path.join(root, cur.lstrip("/"))


def exists_in_root(root, path):
    p = in_root(root, path)
    return p is not None and os.path.exists(p)


def is_elf(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def dpkg_owned(root):
    """Every path a dpkg package installed (var/lib/dpkg/info/*.list)."""
    owned = set()
    info = os.path.join(root, "var", "lib", "dpkg", "info")
    for name in sorted(os.listdir(info)) if os.path.isdir(info) else []:
        if name.endswith(".list"):
            with open(os.path.join(info, name), encoding="utf-8", errors="replace") as f:
                owned.update(ln.rstrip("\n") for ln in f if ln.strip())
    return owned


def scan_unlisted(root, man, present, owned):
    """ELF files under the scan dirs that no present component's paths/covers and no package accounts for."""
    claimed = set()
    prefixes = []
    for c in present:
        claimed.update(c["paths"])
        prefixes += [p.rstrip("/") + "/" for p in c.get("covers", [])]
    out = []
    for top in man.get("scan", []):
        base = os.path.join(root, top.lstrip("/"))
        for dirpath, dirs, files in os.walk(base):
            dirs.sort()
            for fn in sorted(files):
                host = os.path.join(dirpath, fn)
                img = "/" + os.path.relpath(host, root)
                if img in claimed or img in owned or any(img.startswith(p) for p in prefixes):
                    continue
                if os.path.islink(host) or not is_elf(host):
                    continue
                out.append(img)
    return sorted(out)


def fightcade_binaries(root, present):
    """ELF files under /data/fightcade that no present component claims by its paths or covers (#267, #300).
    A first-launch image carries our Box64 build there (box64 and its x64lib/ libraries, each inventoried); anything
    else, Wine or Fightcade's own programs included, is a bundled Fightcade. Symlinks are skipped (a target inside the
    tree is checked as itself)."""
    fc = os.path.join(root, "data", "fightcade")
    claimed, prefixes = set(), []
    for c in present:
        claimed.update(c["paths"])
        prefixes += [p.rstrip("/") + "/" for p in c.get("covers", [])]
    out = []
    for dirpath, dirs, files in os.walk(fc):
        dirs.sort()
        for fn in sorted(files):
            host = os.path.join(dirpath, fn)
            img = "/" + os.path.relpath(host, root)
            if img in claimed or any(img.startswith(p) for p in prefixes):
                continue
            if os.path.islink(host) or not is_elf(host):
                continue
            out.append(img)
    return sorted(out)


def parse_dpkg_status(text):
    """Installed packages from a dpkg status file: [{package, version, arch, source, source_version}]."""
    pkgs = []
    for block in text.split("\n\n"):
        f = {}
        for ln in block.splitlines():
            if ln[:1] in (" ", "\t") or ":" not in ln:
                continue
            k, v = ln.split(":", 1)
            f[k.strip()] = v.strip()
        if not f.get("Package") or "install ok installed" not in f.get("Status", ""):
            continue
        src, sver = f.get("Source", f["Package"]), f.get("Version", "")
        m = re.match(r"^(\S+)\s+\((.+)\)$", src)
        if m:
            src, sver = m.group(1), m.group(2)
        pkgs.append({"package": f["Package"], "version": f.get("Version", ""), "arch": f.get("Architecture", ""),
                     "source": src, "source_version": sver})
    return sorted(pkgs, key=lambda p: (p["package"], p["arch"]))


def apt_origins(root):
    """(package, version) -> 'raspberrypi' | 'debian', from the image's apt lists (plain Packages files)."""
    out = {}
    lists = os.path.join(root, "var", "lib", "apt", "lists")
    for name in sorted(os.listdir(lists)) if os.path.isdir(lists) else []:
        if not name.endswith("_Packages"):
            continue
        origin = "raspberrypi" if "raspberrypi" in name else "debian"
        pkg = None
        with open(os.path.join(lists, name), encoding="utf-8", errors="replace") as f:
            for ln in f:
                if ln.startswith("Package: "):
                    pkg = ln[9:].strip()
                elif ln.startswith("Version: ") and pkg:
                    out.setdefault((pkg, ln[9:].strip()), origin)
    return out


NO_COPYRIGHT = os.path.join(SKILL, "licences", "no-copyright.tsv")


def no_copyright_exceptions(path=NO_COPYRIGHT):
    """{(package, version)} that ship no copyright file upstream (#293); exact versions only."""
    out = set()
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as f:
        for ln in f:
            if ln.startswith("#") or not ln.strip():
                continue
            cols = ln.rstrip("\n").split("\t")
            if len(cols) != 4 or not all(c.strip() for c in cols):
                raise Refused("%s: a row needs package, version, reason, source_url: %r" % (path, ln.strip()))
            out.add((cols[0], cols[1]))
    return out


def source_url(origin, src, sver):
    if origin == "raspberrypi":
        prefix = src[:4] if src.startswith("lib") else src[:1]
        return "https://archive.raspberrypi.com/debian/pool/main/%s/%s/" % (prefix, src)
    return "https://snapshot.debian.org/package/%s/%s/" % (src, urllib.parse.quote(sver, safe=""))


def debian_rows(root):
    path = os.path.join(root, "var", "lib", "dpkg", "status")
    if not os.path.isfile(path):
        raise Refused("no dpkg database in the image (%s)" % path)
    with open(path, encoding="utf-8", errors="replace") as f:
        pkgs = parse_dpkg_status(f.read())
    origins = apt_origins(root)
    rows = []
    for p in pkgs:
        origin = origins.get((p["package"], p["version"]))
        if origin is None:
            origin = "raspberrypi" if re.search(r"\+rp[it]", p["version"]) else "debian"
        has_cr = exists_in_root(root, "/usr/share/doc/%s/copyright" % p["package"])
        rows.append(dict(p, origin=origin, url=source_url(origin, p["source"], p["source_version"]),
                         copyright="yes" if has_cr else "missing"))
    return rows


def normalise_buildinfo(text):
    """BUILDINFO without what changes from one run of the same build to the next: the `date:` line (build/lib.sh
    bi_date) and ldd's load addresses, so the bundle is byte-reproducible from the same build outputs."""
    lines = [re.sub(r"\s+\(0x[0-9a-fA-F]+\)", "", ln) for ln in text.splitlines() if not ln.startswith("date:")]
    return "\n".join(lines) + "\n"


def read_buildinfo(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        text = normalise_buildinfo(f.read())
    m = re.search(r"^commit:\s*([0-9a-f]{40})\s*$", text, re.M)
    return text, (m.group(1) if m else None)


# ------------------------------------------------------------------------------------------------ build

def _tsv(rows):
    return "".join("\t".join(str(v).replace("\t", " ").replace("\n", " ") for v in r) + "\n" for r in rows)


def _md_cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def summary_md(version, comps, deb, contact, libs=None):
    nc = [c for c in comps if c.get("noncommercial")]
    lines = [
        "# Licences in this image",
        "",
        "Coin Dojo image %s. It holds free and open-source software, some **non-commercial** software, and the "
        "Debian / Raspberry Pi OS packages it is built on. Each keeps its own licence; the texts are in "
        "`/%s/` on the root partition." % (version, DOC_DIR),
        "",
        CAVEAT,
        "",
        "## Non-commercial components: never sell this card",
        "",
        "This image is a free download. The components below forbid commercial use: never sell, rent out or run "
        "paid play on a card or cabinet that has them, and never charge for copies of this image.",
        "",
        "| Component | Licence |",
        "|---|---|",
    ]
    lines += ["| %s | %s |" % (_md_cell(c["name"]), _md_cell(c["spdx"])) for c in nc]
    lines += [
        "",
        "## Components",
        "",
        "| Component | Version | Licence (SPDX) | Origin | Source | Exact ref |",
        "|---|---|---|---|---|---|",
    ]
    for c in comps:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            _md_cell(c["name"]), _md_cell(c["version"]), _md_cell(c["spdx"]), c["origin"], _md_cell(c["source"]),
            _md_cell(c.get("ref") or "not pinned (see components.tsv: sha256 of the shipped file)")))
    n_rpi = sum(1 for r in deb if r["origin"] == "raspberrypi")
    lines += [
        "",
        "## Debian and Raspberry Pi OS packages",
        "",
        "%d packages (%d of them from the Raspberry Pi archive). The full list, with exact versions and a link to "
        "each source package, is `/%s/debian-packages.tsv`; each package's licence is "
        "`/usr/share/doc/<package>/copyright`." % (len(deb), n_rpi, DOC_DIR),
        "",
    ]
    for c in comps:
        rows = (libs or {}).get(c["id"])
        if rows:
            lines += [
                "## Libraries bundled in %s" % c["name"],
                "",
                "%s carries %d libraries of its own (%d of them GPL, LGPL or MPL: their source is covered by the "
                "written offer below). Each one's package, exact version, licence and source is "
                "`/%s/%s/libraries.tsv`; each package's licence is `%s/usr/share/doc/<package>/copyright`."
                % (c["name"], len(rows), sum(1 for r in rows if r["owes"] == "source"), DOC_DIR, c["id"],
                   c["libraries_root"]),
                "",
            ]
    lines += [
        "## Corresponding source (GPL, LGPL)",
        "",
        "The exact source of every GPL- or LGPL-licensed program in this image that we build, with our patches and "
        "build scripts, is published on the same release page as this image (GPL-2.0 section 3, last paragraph; "
        "GPL-3.0 section 6(d)).",
        "",
        "Written offer: for at least three years after this image was last published, anyone may ask for the "
        "complete corresponding source of any GPL- or LGPL-licensed program in this image, including every Debian "
        "and Raspberry Pi OS package listed in `debian-packages.tsv`, every component in `components.tsv` (Box64's "
        "bundled x86_64 libraries included) and every library listed in a `libraries.tsv`, as a download or on a "
        "physical medium for no more than our cost of providing it (GPL-2.0 section 3(b), GPL-3.0 section 6(b), LGPL-2.1 section 6). "
        "To ask: %s." % contact,
        "",
        CAVEAT,
        "",
    ]
    return "\n".join(lines)


def build(man, root, cache, repo=REPO, boot=None, version="unknown", contact=DEFAULT_CONTACT, build_dir=None,
          strict=False):
    """Write the licence folder into root. Returns (warnings, blockers).

    warnings are the problems strict refuses (Refused, nothing written): a broken manifest, a listed component missing
    from the image, an unlisted binary, a licence text missing from the cache (offline) or changed, a BUILDINFO from
    another commit, no dpkg database. Without strict they never stop the build: the bundle is written from what is
    there and each one is a warning. blockers are what a public release must not ship with (strict: exit 1)."""
    build_dir = build_dir or os.path.join(repo, "build")
    warnings = ["manifest: %s" % p for p in manifest_problems(man, repo)]
    comps = sorted((c for c in man.get("components", []) if not component_safety(c)), key=lambda c: c["id"])
    present, blockers = [], []
    for c in comps:
        missing = [p for p in c.get("paths", []) if not exists_in_root(root, p)]
        if missing and c.get("optional"):
            continue
        if missing:
            warnings.append("%s is listed but not in the image: %s" % (c["id"], ", ".join(missing)))
            continue
        present.append(c)
    libs = {}
    for c in present:
        if "libraries" in c:
            rows, _p = load_libraries(os.path.join(repo, c["libraries"]))
            libs[c["id"]] = rows
            warnings += library_problems(root, c, rows)
    unlisted = scan_unlisted(root, man, present, dpkg_owned(root))
    if unlisted:
        warnings.append("not in the licence manifest (skills/build-image/licences/components.json): %s"
                        % ", ".join(unlisted))
    try:
        deb = debian_rows(root)
    except Refused as exc:
        warnings.append(str(exc))
        deb = []

    # plan: which licence texts and BUILDINFO each component gets (nothing is written yet)
    plan = []
    for c in present:
        files, info = [], None
        for lf in c.get("licence_files", []):
            src = os.path.join(repo, lf["repo"]) if "repo" in lf else os.path.join(cache, lf["sha256"])
            if not os.path.isfile(src) or ("sha256" in lf and sha256_file(src) != lf["sha256"]):
                warnings.append("%s: licence text %s is missing from %s or changed (run licences.py fetch)"
                                % (c["id"], lf["name"], cache if "repo" not in lf else repo))
                blockers.append("%s: licence text %s not in the image" % (c["id"], lf["name"]))
                continue
            files.append((src, lf["name"]))
        if c["origin"] == "built":
            bi = os.path.join(build_dir, os.path.basename(c.get("build", "")), "out", "BUILDINFO")
            if os.path.isfile(bi):
                text, commit = read_buildinfo(bi)
                if commit == c["ref"]:
                    info = text
                else:
                    warnings.append("%s: %s says commit %s, the manifest pins %s (stale build: not copied)"
                                    % (c["id"], bi, commit, c["ref"]))
                    blockers.append("%s: BUILDINFO is not the pinned build" % c["id"])
            else:
                blockers.append("%s: no BUILDINFO at %s (the build's record of commit and patches)" % (c["id"], bi))
        plan.append((c, files, info))
    if strict and warnings:
        raise Refused("; ".join(warnings))

    out = os.path.join(root, DOC_DIR)
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    comp_rows = []
    for c, files, info in plan:
        d = os.path.join(out, c["id"])
        os.makedirs(d)
        for src, name in files:
            shutil.copyfile(src, os.path.join(d, name))
        if info is not None:
            with open(os.path.join(d, "BUILDINFO"), "w", encoding="utf-8") as f:
                f.write(info)
        if c["id"] in libs:
            with open(os.path.join(d, "libraries.tsv"), "w", encoding="utf-8") as f:
                f.write("# %s (in %s; each package's licence: %s/usr/share/doc/<package>/copyright)\n"
                        % ("\t".join(LIB_COLS), c["libraries_root"], c["libraries_root"]))
                f.write(_tsv([r[k] for k in LIB_COLS] for r in libs[c["id"]]))
        if c["origin"] in ("buildbot", "download") and not c.get("ref"):
            blockers.append("%s: no exact source ref (%s binary): its corresponding source cannot be named"
                            % (c["id"], c["origin"]))
        blockers += ["%s: %s" % (c["id"], b) for b in c.get("blockers", [])]
        sums = ",".join("%s=%s" % (p, sha256_file(in_root(root, p))) for p in c["paths"]
                        if os.path.isfile(in_root(root, p)))
        listed = ",".join(name for _src, name in files)
        if not c.get("licence_files"):
            listed = "per-file headers"
        comp_rows.append((c["id"], c["version"], c["spdx"], c["origin"], c["source"], c.get("ref") or "unpinned",
                          "noncommercial" if c.get("noncommercial") else "-", listed or "missing", sums or "-"))
    with open(os.path.join(out, "components.tsv"), "w", encoding="utf-8") as f:
        f.write("# id\tversion\tspdx\torigin\tsource\tref\tcommercial\tlicence_files\tsha256\n" + _tsv(comp_rows))
    with open(os.path.join(out, "debian-packages.tsv"), "w", encoding="utf-8") as f:
        f.write("# package\tversion\tarch\tsource\tsource_version\torigin\tsource_url\tcopyright\n")
        f.write(_tsv((r["package"], r["version"], r["arch"], r["source"], r["source_version"], r["origin"],
                      r["url"], r["copyright"]) for r in deb))
    exempt = no_copyright_exceptions()
    for r in deb:
        if r["copyright"] != "yes" and (r["package"], r["version"]) in exempt:
            r["copyright"] = "none upstream (licences/no-copyright.tsv)"
    no_cr = [r["package"] for r in deb if r["copyright"] not in ("yes", "none upstream (licences/no-copyright.tsv)")]
    if no_cr:
        blockers.append("packages without /usr/share/doc/<pkg>/copyright: %s" % ", ".join(no_cr))
    fc_bins = fightcade_binaries(root, present)
    if fc_bins:
        blockers.append("Fightcade binaries are in the image (/data/fightcade): a public image must fetch them on "
                        "first launch (#267): %s%s" % (", ".join(fc_bins[:5]),
                                                       " and %d more" % (len(fc_bins) - 5) if len(fc_bins) > 5 else ""))
    text = summary_md(version, present, deb, contact, libs)
    with open(os.path.join(out, "LICENCES.md"), "w", encoding="utf-8") as f:
        f.write(text)
    boot = boot if boot is not None else os.path.join(root, "boot", "firmware")
    if os.path.isdir(boot):
        with open(os.path.join(boot, "LICENCES.md"), "w", encoding="utf-8") as f:
            f.write(text)
    blockers += ["tip link in the image: %s" % h for h in image_tip_links(root, boot)]
    # #287: the image's own record of the licence gate it was built under (GATE_FILE); the release pipeline
    # (gh_release.py) refuses an image whose record is not strict with 0 warnings and 0 blockers.
    with open(os.path.join(out, GATE_FILE), "w", encoding="utf-8") as f:
        f.write(gate_record(strict, warnings, blockers, version))
    return warnings, blockers


def gate_record(strict, warnings, blockers, version):
    """The text of GATE_FILE: key<TAB>value lines, no date (the bundle stays byte-identical for the same inputs)."""
    return "# the licence gate this image was built under (#287; licences.py build)\n" + "".join(
        "%s\t%s\n" % kv for kv in (("strict", "on" if strict else "off"), ("warnings", len(warnings)),
                                    ("blockers", len(blockers)), ("version", version)))


def read_gate(path):
    """GATE_FILE -> dict (missing file: {})."""
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return dict(ln.split("\t", 1) for ln in f.read().splitlines() if ln and not ln.startswith("#") and "\t" in ln)


# ------------------------------------------------------------------------------------------------ tip links

# FBNeo (src/license.txt): "You may not sell, lease, rent or otherwise seek to gain monetary profit from FB Neo" and
# "You may not ask for donations to support your work on any project that uses the FB Neo source code". The image and
# its release page carry the FBNeo binary, so neither may carry a tip or donation link (the maintainer 2026-11-20, #254). The
# repository README may: building from source is not us distributing FBNeo. COMPLIANCE.md section 6.
# Links and domains only: a changelog line that names the README's "Tips for tokens" heading is not a tip link.
TIP_LINK = re.compile(r"(?i)\b(?:ko-fi\.com|buymeacoffee\.com|patreon\.com|paypal\.me|liberapay\.com|"
                      r"opencollective\.com|github\.com/sponsors)\b")
TIP_SCAN = ("home", "data", DOC_DIR)   # image dirs whose README*/*.md/*.txt files are read (licence texts are not)


def tip_link_problems(text, where):
    """'where:line: match' for every tip/donation link in text."""
    return ["%s:%d: %s" % (where, n, m.group(0)) for n, ln in enumerate(text.splitlines(), 1)
            for m in [TIP_LINK.search(ln)] if m]


def _doc_like(name):
    low = name.lower()
    return low.startswith("readme") or low.endswith((".md", ".txt", ".html"))


def image_tip_links(root, boot=None):
    """Tip links in the image's own docs: the boot partition and README/markdown/text files under TIP_SCAN, minus
    the third-party licence texts (FBNeo's own licence quotes the word donations, not a link, but stays out)."""
    lic = os.path.join(root, DOC_DIR)
    tops = [boot or os.path.join(root, "boot", "firmware")] + [os.path.join(root, t) for t in TIP_SCAN]
    seen, hits = set(), []
    for top in tops:
        for dirpath, dirs, files in os.walk(top):
            dirs.sort()
            if dirpath != lic and dirpath.startswith(lic + os.sep):
                continue
            for fn in sorted(files):
                p = os.path.join(dirpath, fn)
                if p in seen or not _doc_like(fn) or os.path.islink(p) or os.path.getsize(p) > (1 << 20):
                    continue
                seen.add(p)
                with open(p, encoding="utf-8", errors="replace") as f:
                    hits += tip_link_problems(f.read(), "/" + os.path.relpath(p, root))
    return hits


# ------------------------------------------------------------------------------------------------ cli

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rc = sub.add_parser("release-check", help="refuse a release body or image with a tip/donation link")
    rc.add_argument("--body", action="append", default=[], help="a release body file (repeatable)")
    rc.add_argument("--root", default=None, help="a mounted image root")
    rc.add_argument("--boot", default=None)
    for name in ("check", "fetch", "build"):
        p = sub.add_parser(name)
        p.add_argument("--repo", default=REPO)
        p.add_argument("--manifest", default=None)
        if name in ("fetch", "build"):
            p.add_argument("--cache", required=True)
            p.add_argument("--strict", action="store_true", help="as HK_LICENCES_STRICT=1")
        if name == "build":
            p.add_argument("--root", required=True)
            p.add_argument("--boot", default=None)
            p.add_argument("--build-dir", default=None)
            p.add_argument("--version", default="unknown")
            p.add_argument("--offer-contact", default=DEFAULT_CONTACT)
    a = ap.parse_args(argv)
    if a.cmd == "release-check":
        if not a.body and not a.root:
            print("licences: release-check needs --body FILE and/or --root DIR", file=sys.stderr)
            return 2
        hits = []
        try:
            for b in a.body:
                with open(b, encoding="utf-8", errors="replace") as f:
                    hits += tip_link_problems(f.read(), b)
        except OSError as exc:
            print("licences: REFUSED: %s" % exc, file=sys.stderr)
            return 2
        if a.root:
            hits += image_tip_links(a.root, a.boot)
        for h in hits:
            print("FAIL: tip link where FBNeo is distributed: %s" % h)
        print("licences: release-check %s" % ("FAIL" if hits else "PASS"))
        return 1 if hits else 0
    strict = strict_mode(a)
    try:
        man = load_manifest(a.manifest or os.path.join(a.repo, "skills", "build-image", "licences",
                                                       "components.json"))
        if a.cmd == "check":
            probs = manifest_problems(man, a.repo, launcher_cores(a.repo))
            for p in probs:
                print("FAIL: %s" % p)
            print("licences: manifest %s (%d components)" % ("OK" if not probs else "BROKEN", len(man["components"])))
            return 2 if probs else 0
        if a.cmd == "fetch":
            n, warnings = fetch(man, a.cache, repo=a.repo, strict=strict)
            for w in warnings:
                print("WARN: licences fetch: %s" % w)
            print("licences: %d downloaded into %s, %d warnings" % (n, a.cache, len(warnings)))
            return 0
        warnings, blockers = build(man, a.root, a.cache, a.repo, a.boot, a.version, a.offer_contact, a.build_dir,
                                   strict=strict)
    except Exception as exc:  # noqa: BLE001  (non-strict: the licence step never stops a build)
        if a.cmd == "check" or strict:
            print("licences: REFUSED: %s" % exc, file=sys.stderr)
            return 2
        print("WARN: licences %s failed, the image has no complete licence bundle: %s: %s"
              % (a.cmd, type(exc).__name__, exc))
        return 0
    for w in warnings:
        print("WARN: licences: %s" % w)
    for b in blockers:
        print("%s: release blocker: %s" % ("FAIL" if strict else "WARN", b))
    print("licences: wrote /%s (%d warnings, %d release blockers, strict %s)"
          % (DOC_DIR, len(warnings), len(blockers), "on" if strict else "off"))
    return 1 if strict and blockers else 0


def strict_mode(a):
    """Strict (a public release) only with --strict or HK_LICENCES_STRICT=1 exactly; 0, empty or unset is off."""
    return bool(getattr(a, "strict", False)) or os.environ.get("HK_LICENCES_STRICT") == "1"


if __name__ == "__main__":
    sys.exit(main())
