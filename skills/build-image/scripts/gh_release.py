#!/usr/bin/env python3
"""Publish the Coin Dojo image and its sources as a GitHub Release (#287, COMPLIANCE.md section 6).

    gh_release.py --version X.Y.Z --image IMG [--apply [--publish]]
    (make gh-release VERSION=X.Y.Z IMG=... [APPLY=1 [PUBLISH=1]])

Steps (the dry run does 1-5 and prints the upload plan; --apply also does 6-7 and leaves a DRAFT; --apply --publish
re-verifies that draft and un-drafts it, after a person read the draft's notes):
  1. image    refuse unless the image's own record (/usr/share/doc/coindojo/licences/GATE.tsv, written by
              licences.py build at provision) says strict on, 0 warnings, 0 blockers, AND `licences.py build --strict`
              re-run over a read-only mount of it (release-inspect.sh, in the Ansible container) passes and writes the
              same bundle byte for byte, AND the image matches the <image>.sha256 run.sh writes only after verify.
              release.log records which private commit built it (GATE.tsv `version`; not a gate, #291).
  2. sources  <stem>-sources.tar.xz: each built component's upstream tree at its exact commit (components.tsv in the
              image; git, submodules included), build/ (scripts + patches) FROM THE PUBLISHED COMMUNITY TREE (the strict
              export in ~/.cache/coin-dojo/coin-dojo-publish, stamped for the target sha; never the private HEAD), each
              BUILDINFO from the image, the ES-DE libraries' Ubuntu sources (esde_sources.py --apply) and the Raspberry
              Pi archive sources (rpi_sources.py --apply --strict, checked against the image's own archive keyring).
              Everything in it that is not upstream source (build/, buildinfo/, README.md) must pass the edition gate
              scan (tools/edition_export.py scan_tree, the export's patterns and allow list); tar headers say root:root,
              no xattrs, no AppleDouble files. A packed archive is re-used only while its inputs, the gate's
              pattern lists (a digest) included, are the same (#291).
  3. sums     SHA256SUMS over every asset.
  4. notes    release notes: CHANGELOG.md's [VERSION] section, else [Unreleased], plus the versions.lock table,
              scrubbed as the community export scrubs (personas, denylist, private values: tools/edition_export.py);
              then the edition gate scan (decoded forms included) and `licences.py release-check --body` must pass.
  5. size     every asset < 2 GiB (GitHub's per-file limit), checked before anything is uploaded.
  6. upload   a DRAFT release v<VERSION> on github.com/coin-dojo/coin-dojo, target = the published community main sha
              (~/.cache/coin-dojo/edition-published.tsv; it must exist on GitHub and be on main). An asset already there
              with the same size and digest is kept, any other with that name is replaced: a re-run resumes.
  7. verify   every asset's size and GitHub's sha256 digest match the local file (nothing is downloaded); the release
              stays a draft. --publish: verify again, run step 4's gate scan and release-check on the draft's text as
              GitHub holds it (it may have been edited there, #291), refuse if refs/tags/v<VERSION> exists at another
              sha (the draft stays), un-draft, then the tag must point at the target sha. Prints the release URL.

Token: the macOS Keychain item `github-coin-dojo-release` (read only with --apply, held in memory, sent only as an
Authorization header to api.github.com / uploads.github.com only (any other host is refused; a redirect to another
host or to plain http drops the header); never printed, logged or put in argv).
Work folder: <image dir>/<stem>-release/ (re-used: a finished step is skipped when its inputs did not change).
Exit: 0 ok, 1 refused or failed (one `RESULT: FAIL gh-release: why` line), 2 usage.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(SKILL))

SLUG = "coin-dojo/coin-dojo"
PUBLISHED_URLS = ("git@github.com:coin-dojo/coin-dojo.git", "https://github.com/coin-dojo/coin-dojo.git",
                  "https://github.com/coin-dojo/coin-dojo")
KEYCHAIN_SERVICE = "github-coin-dojo-release"
SECURITY = "security"                          # macOS Keychain CLI (tests point it at a fake)
API = os.environ.get("GH_RELEASE_API", "https://api.github.com")              # tests: a local fake server
UPLOADS = os.environ.get("GH_RELEASE_UPLOADS", "https://uploads.github.com")
PUBLISHED = os.environ.get("GH_RELEASE_PUBLISHED", "~/.cache/coin-dojo/edition-published.tsv")
PUBLISH_DIR = os.environ.get("GH_RELEASE_PUBLISH_DIR", "~/.cache/coin-dojo/coin-dojo-publish")   # make edition-publish OUT
API_HOSTS = {"api": "api.github.com", "uploads": "uploads.github.com"}
LOCAL_HOSTS = ("127.0.0.1", "localhost")       # the tests' fake server only
MAX_ASSET = 2 * 1024 ** 3                      # GitHub: each release asset must be under 2 GiB
DOC_DIR = "usr/share/doc/coindojo/licences"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
SHA1 = re.compile(r"^[0-9a-f]{40}$")
CHUNK = 1 << 20
TIMEOUT = 600


class Refused(Exception):
    pass


def say(msg):
    print(msg, flush=True)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, os.path.join(REPO, "tools"))
LIC = _load("hk_licences", os.path.join(HERE, "licences.py"))
_TK = _load("hk_tickets", os.path.join(REPO, "tools", "tickets.py"))       # SameHostRedirect: one copy (#140)


class SafeRedirect(_TK.SameHostRedirect):
    """SameHostRedirect (Authorization dropped when a redirect leaves the host), and also dropped on a redirect to
    anything but https (a local test host aside)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        p = urllib.parse.urlsplit(newurl)
        if new is not None and p.scheme != "https" and p.hostname not in LOCAL_HOSTS:
            new.remove_header("Authorization")
        return new


OPENER = urllib.request.build_opener(SafeRedirect())
_EDITION = {}


def edition():
    """The community export's own module, renames, personas, patterns and allow list (tools/edition_export.py and
    config/edition/), loaded once: the notes scrub and both gate scans use exactly what the export uses."""
    if not _EDITION:
        import pathlib
        E = _load("hk_edition_export", os.path.join(REPO, "tools", "edition_export.py"))
        repo = pathlib.Path(REPO)
        ed = repo / "config" / "edition"
        venv = os.path.expanduser("~/.venvs/coin-dojo/bin/python")
        cfg_py = os.environ.get("CONFIG_PYTHON") or (venv if os.path.isfile(venv) else sys.executable)
        try:
            renames = E.load_renames(ed / "rename.tsv")
            personas = E.load_personas(ed / "personas.tsv")
            patterns = (E.load_denylist(ed / "denylist.txt") + E.load_private_list(ed / "private.txt", renames)
                        + E.private_patterns(repo, renames, (), cfg_py))
            allow = E.load_allow(ed / "allow.tsv", personas)
        except E.ExportError as e:
            raise Refused("cannot load the edition scrub lists: %s" % e)
        _EDITION.update(E=E, renames=renames, personas=personas, patterns=patterns, allow=allow)
    return _EDITION


def edition_module():
    return _load("hk_edition_export", os.path.join(REPO, "tools", "edition_export.py"))


# ------------------------------------------------------------------------------------------------ helpers

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def human(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return str(n)


def run(cmd, log=None, **kw):
    """Run cmd (a list, never a shell string); its output goes to log (a file) when given. Returns the exit code."""
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write("$ %s\n" % " ".join(cmd))
            f.flush()
            return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, **kw).returncode
    return subprocess.run(cmd, **kw).returncode


def stamp_ok(path, want):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read() == want
    except OSError:
        return False


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(path + ".part", path)


def clean_env():
    """The environment for child makes and tools: no parent make flags, no APPLY/VERSION leaking in."""
    return {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "APPLY", "VERSION")}


# ------------------------------------------------------------------------------------------------ 1. the image

def ans_image():
    """The pinned Ansible container (ansible/bin/ans builds it): python3, gpgv, losetup, the image build's own tools."""
    r = subprocess.run(["bash", os.path.join(REPO, "ansible", "bin", "ans"), "--image"], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    image = r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else ""
    if not image:
        raise Refused("cannot name the pinned Ansible container (ansible/bin/ans --image): %s" % r.stderr.strip()[-200:])
    return image


def inspect_image(img, out, log):
    """Run release-inspect.sh in the pinned Ansible container: read-only mount, strict re-run (see its header)."""
    image = ans_image()
    os.makedirs(out, exist_ok=True)
    work = os.path.dirname(os.path.abspath(img))
    cache = os.path.join(work, "cache", "licences")       # run.sh's licence-text cache; the image folder stays :ro
    os.makedirs(cache, exist_ok=True)
    rc = run(["docker", "run", "--rm", "--privileged", "-v", "%s:/work:ro" % work, "-v", "%s:/cache" % cache,
              "-v", "%s:%s:ro" % (REPO, REPO),
              "-v", "%s:/out" % os.path.abspath(out), image, "bash",
              os.path.join(REPO, "skills", "build-image", "scripts", "release-inspect.sh"),
              "/work/" + os.path.basename(img), REPO], log=log, stdin=subprocess.DEVNULL)
    with open(log, encoding="utf-8", errors="replace") as f:
        lines = [ln.strip() for ln in f.read().splitlines() if ln.startswith("INSPECT_")]
    last = lines[-1] if lines else "INSPECT_FAIL no result line (see %s)" % log
    if rc != 0 or last != "INSPECT_OK":
        raise Refused("the image fails the licence gate: %s (log %s)" % (last.replace("INSPECT_FAIL ", ""), log))


def check_gate(gate_path, tag=None):
    """The image's own record: built with HK_LICENCES_STRICT=1, no warnings, no blockers, and (with tag) the version
    the cabinet shows is the release tag (#294). Never the caller's word."""
    g = LIC.read_gate(gate_path)
    if not g:
        raise Refused("the image has no licence gate record (%s/GATE.tsv): rebuild it with HK_LICENCES_STRICT=1 "
                      "(make image ... HK_LICENCES_STRICT=1)" % DOC_DIR)
    if g.get("strict") != "on":
        raise Refused("the image was not built with HK_LICENCES_STRICT=1 (its GATE.tsv says strict %s): rebuild it "
                      "with HK_LICENCES_STRICT=1" % g.get("strict"))
    if g.get("warnings") != "0" or g.get("blockers") != "0":
        raise Refused("the image's licence gate had %s warnings and %s release blockers"
                      % (g.get("warnings"), g.get("blockers")))
    if tag is not None and g.get("version") != tag:
        raise Refused("the image shows version %r, the release is %s: tag the tree it is built from %s first "
                      "(git tag %s), then rebuild" % (g.get("version"), tag, tag, tag))
    return g


def record_build(gate, log, repo=None):
    """#291 (L6): which private commit built the image, from the image's own record (GATE.tsv `version`: run.sh's
    `git describe --tags --always --dirty=+dirty` of this repo at build time), into release.log only (it is private:
    never in the notes or on GitHub). Resolved to a full commit when this checkout knows it. Not a gate: whether that
    commit is older than the published target's source cannot be told (the export records no private commit), see
    SKILL.md "Release". Returns the line."""
    ver = (gate or {}).get("version", "").strip() or "unknown"
    base = ver[:-len("+dirty")] if ver.endswith("+dirty") else ver
    full = ""
    if base != "unknown" and re.match(r"^[A-Za-z0-9._/+-]+$", base) and not base.startswith("-"):
        r = subprocess.run(["git", "-C", repo or REPO, "rev-parse", "--verify", "-q", base + "^{commit}"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        full = r.stdout.strip() if r.returncode == 0 else ""
    line = "build-record: image built from private %s (commit %s%s)" % (
        ver, full or "not in this checkout", ", with uncommitted changes" if ver.endswith("+dirty") else "")
    with open(log, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    say("image: %s" % line.replace("build-record: ", ""))
    return line


def check_image_sum(img):
    """run.sh writes <img>.sha256 only after verify passed: the image must match it."""
    side = img + ".sha256"
    try:
        with open(side, encoding="utf-8") as f:
            want = f.read().split()[0]
    except (OSError, IndexError):
        raise Refused("%s is missing: run.sh writes it only after verify passed (rebuild, do not hand-make it)" % side)
    got = sha256_file(img)
    if got != want:
        raise Refused("%s does not match %s (changed after the build?)" % (img, side))
    return got


# ------------------------------------------------------------------------------------------------ 2. sources

def built_components(licdir):
    """[(id, source url, ref)] of the built components in the image's components.tsv."""
    path = os.path.join(licdir, "components.tsv")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        raise Refused("no components.tsv in the image's licence folder (%s)" % path)
    cols = lines[0].lstrip("# ").split("\t") if lines else []
    out = []
    for ln in lines[1:]:
        row = dict(zip(cols, ln.split("\t")))
        if row.get("origin") != "built":
            continue
        if not SHA1.match(row.get("ref", "")) or not row.get("source", "").startswith("https://"):
            raise Refused("%s: no exact https source and commit in components.tsv (%s %s)"
                          % (row.get("id"), row.get("source"), row.get("ref")))
        out.append((row["id"], row["source"], row["ref"]))
    if not out:
        raise Refused("components.tsv lists no built component (%s)" % path)
    return out


def fetch_upstream(cid, url, ref, dest, log):
    """The upstream tree at ref, submodules included, into dest (re-used when its stamp says the same ref)."""
    stamp = os.path.join(dest, ".hk-ref")
    if stamp_ok(stamp, ref + "\n"):
        return
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    g = ["git", "-C", dest, "-c", "advice.detachedHead=false", "-c", "credential.helper="]
    env = dict(clean_env(), GIT_TERMINAL_PROMPT="0")     # a public upstream needs no login: never ask, never use one
    steps = [["init", "-q"], ["fetch", "-q", "--depth", "1", url, ref], ["checkout", "-q", "FETCH_HEAD"],
             ["submodule", "update", "-q", "--init", "--recursive", "--depth", "1"]]
    for args in steps:
        # like the builds: no .gitmodules = no submodule step (mupen64plus-nx carries a stray gitlink with no url)
        if args[0] == "submodule" and not os.path.isfile(os.path.join(dest, ".gitmodules")):
            continue
        if run(g + args, log=log, stdin=subprocess.DEVNULL, env=env) != 0:
            raise Refused("%s: git %s failed for %s at %s (log %s)" % (cid, args[0], url, ref, log))
    r = subprocess.run(["git", "-C", dest, "rev-parse", "HEAD"], capture_output=True, text=True)
    if r.stdout.strip() != ref:
        raise Refused("%s: fetched %s, want %s" % (cid, r.stdout.strip(), ref))
    write(stamp, ref + "\n")


def run_tool(name, args, log, mount=None):
    """Run one of this folder's tools; with mount (a folder) inside the pinned Ansible container instead, the repo
    read-only and mount read-write at their own paths (rpi_sources.py --strict needs gpgv, which macOS lacks)."""
    tool = os.path.join(HERE, name)
    cmd = [sys.executable, "-I", tool] + args
    if mount:
        cmd = ["docker", "run", "--rm", "-v", "%s:%s:ro" % (REPO, REPO), "-v", "%s:%s" % (mount, mount), ans_image(),
               "python3", "-I", tool] + args
    if run(cmd, log=log, env=clean_env(), stdin=subprocess.DEVNULL) != 0:
        raise Refused("%s failed (log %s)" % (name, log))


def tar_flags():
    """Neutral headers: owner root:0 / root:0, no xattrs or ACLs, no AppleDouble (._*) files. bsdtar (macOS) and GNU
    tar (Linux CI) spell it differently."""
    v = subprocess.run(["tar", "--version"], capture_output=True, text=True).stdout
    if "bsdtar" in v:
        return ["--uid", "0", "--gid", "0", "--uname", "root", "--gname", "root", "--no-xattrs", "--no-acls",
                "--no-mac-metadata"]
    return ["--owner=root:0", "--group=root:0", "--no-xattrs", "--no-acls"]


def tar(parts, out, compress, log):
    """tar the explicit members of each (folder, [members]) in parts into out (xz with compress), via a .part file;
    .git, our stamps and AppleDouble files left out, neutral owner names (tar_flags)."""
    part = out + ".part"
    cmd = ["tar"] + tar_flags() + ["--exclude", ".git", "--exclude", ".hk-ref", "--exclude", "._*",
                                   "-cf", "-" if compress else part]
    for d, members in parts:
        cmd += ["-C", d] + list(members)
    env = dict(os.environ, COPYFILE_DISABLE="1")
    with open(log, "a", encoding="utf-8") as lf:
        lf.write("$ %s%s\n" % (" ".join(cmd), " | xz -T0 -6" if compress else ""))
        lf.flush()
        if compress:
            with open(part, "wb") as f:
                t = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=lf, env=env)
                x = subprocess.Popen(["xz", "-T0", "-6", "-c"], stdin=t.stdout, stdout=f, stderr=lf)
                t.stdout.close()
                rc = x.wait() or t.wait()
        else:
            rc = subprocess.run(cmd, stderr=lf, stdout=lf, env=env).returncode
    if rc != 0:
        if os.path.exists(part):
            os.remove(part)
        raise Refused("tar of %s failed (log %s)" % (out, log))
    os.replace(part, out)


SOURCES_README = """# Sources of the Coin Dojo image {stem}

Everything here is the exact source of what the image {stem}.img.xz on this release page carries (the release's
offer of source, LICENCES.md in the image):

- upstream/<component>-<commit>/  each program we build (RetroArch, the emulators and cores) at the exact commit the
                                  image's components.tsv names, submodules included
- build/                          our build scripts and patches, as in the project's repository
- buildinfo/<component>/BUILDINFO the record each build wrote (commit, patches, flags)
- ubuntu-esde/                    the Ubuntu source packages of the libraries inside the ES-DE AppImage that owe their
                                  source (SOURCES.tsv, SHA256SUMS)
- raspberrypi/                    the Raspberry Pi archive source packages of the image (kernel, firmware, Pi tools;
                                  SOURCES.tsv, SHA256SUMS and the signed index they were checked against)

Debian packages: debian-packages.tsv in the image names each one with its snapshot.debian.org link.

A note on build/ and BUILDINFO: build/ is taken from the project's published repository at commit {target}. That
repository's text files have neutral names in place of private ones (comments, persona names), so a patch file under
build/*/patches can differ in its comment lines, and so in its sha256, from the one BUILDINFO recorded when the image
was built. The code the patch changes is the same. Each BUILDINFO is kept exactly as the build wrote it.
"""


def published_build(target, dest):
    """build/ of the published community tree at target, into dest/build: from the strict export make edition-publish
    left in PUBLISH_DIR, only when its marker is stamped for target and its commit is target. Returns its tree id."""
    E = edition_module()
    d = os.path.expanduser(PUBLISH_DIR)
    try:
        with open(os.path.join(d, E.MARKER), encoding="utf-8") as f:
            marker = f.read().split()
    except OSError:
        marker = []
    head = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if marker[-2:] != [E.STAMP, target] or head != target:
        raise Refused("the published community tree for %s is not at %s (its marker says %s, its commit %s): build/ "
                      "must come from what was published, never the private checkout; run make edition-publish "
                      "(it refreshes that folder and edition-published.tsv), then release" % (target, d,
                                                                                            " ".join(marker[-2:]) or "none", head or "none"))
    tree = subprocess.run(["git", "-C", d, "rev-parse", target + ":build"], capture_output=True, text=True).stdout.strip()
    a = subprocess.run(["git", "-C", d, "archive", "--format=tar", target, "build"], capture_output=True)
    if a.returncode != 0 or not tree or subprocess.run(["tar", "-xf", "-", "-C", dest], input=a.stdout).returncode != 0:
        raise Refused("git archive of build/ at %s in %s failed" % (target, d))
    return tree


def make_sources(stage, stem, licdir, keyring, log, target, patterns=None, allow=None):
    """<stem>-sources.tar.xz from the pieces; a missing piece is named and refused. Our own files (build/ from the
    published tree, buildinfo/, README.md) are assembled fresh in work/own and must pass the edition gate scan."""
    work = os.path.join(stage, "work")
    tree = os.path.join(work, "sources")            # third-party source (upstream/, ubuntu-esde/, raspberrypi/)
    own = os.path.join(work, "own")                 # ours: build/, buildinfo/, README.md, rebuilt every run
    out = os.path.join(stage, "assets", stem + "-sources.tar.xz")
    comps = built_components(licdir)
    libs = os.path.join(licdir, "es-de", "libraries.tsv")
    if os.path.isdir(own):
        shutil.rmtree(own)
    os.makedirs(own)
    build_tree = published_build(target, own)
    if patterns is None:
        ed = edition()
        patterns, allow = ed["patterns"], ed["allow"]
    key = "\n".join(["%s %s %s" % c for c in comps] + [sha256_file(p) if os.path.isfile(p) else "none" for p in (
        os.path.join(licdir, "debian-packages.tsv"), libs)] + [build_tree, target, "gate " + gate_digest(patterns, allow)]
    ) + "\n"
    if os.path.isfile(out) and stamp_ok(out + ".inputs", key):
        say("sources: %s is up to date (same components, packages, published build/ and gate lists), kept"
            % os.path.basename(out))
        return out
    os.makedirs(os.path.join(tree, "upstream"), exist_ok=True)
    missing = []
    for cid, url, ref in comps:
        say("sources: %s %s at %s" % (cid, url, ref[:12]))
        fetch_upstream(cid, url, ref, os.path.join(tree, "upstream", "%s-%s" % (cid, ref)), log)
        bi = os.path.join(licdir, cid, "BUILDINFO")
        if not os.path.isfile(bi):
            missing.append("%s: no BUILDINFO in the image's licence folder" % cid)
            continue
        os.makedirs(os.path.join(own, "buildinfo", cid), exist_ok=True)
        shutil.copyfile(bi, os.path.join(own, "buildinfo", cid, "BUILDINFO"))
    keep = {"%s-%s" % (c[0], c[2]) for c in comps}
    for d in os.listdir(os.path.join(tree, "upstream")):
        if d not in keep:
            shutil.rmtree(os.path.join(tree, "upstream", d))
    say("sources: ES-DE libraries' Ubuntu sources (esde_sources.py --apply)")
    if not os.path.isfile(libs):
        missing.append("es-de/libraries.tsv is not in the image's licence folder")
    else:
        run_tool("esde_sources.py", ["--libraries", libs, "--out", os.path.join(tree, "ubuntu-esde"), "--apply"], log)
    say("sources: Raspberry Pi archive sources (rpi_sources.py --apply --strict)")
    if not keyring:
        missing.append("the image has no raspberrypi-archive-keyring.pgp: the Pi sources cannot be checked")
    else:
        run_tool("rpi_sources.py", ["--packages", os.path.join(licdir, "debian-packages.tsv"), "--out",
                                    os.path.join(tree, "raspberrypi"), "--keyring", keyring, "--apply", "--strict"], log,
                 mount=os.path.abspath(stage))
    if missing:
        raise Refused("sources incomplete: " + "; ".join(missing))
    write(os.path.join(own, "README.md"), SOURCES_README.format(stem=stem, target=target))
    say("sources: the edition gate scan over build/, buildinfo/ and README.md")
    gate_tree(own, patterns, allow)
    say("sources: packing %s (xz)" % os.path.basename(out))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tar([(own, ["README.md", "build", "buildinfo"]), (tree, ["upstream", "ubuntu-esde", "raspberrypi"])], out, True, log)
    write(out + ".inputs", key)
    return out


def git_tree(repo, path):
    r = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD:" + path], capture_output=True, text=True)
    return r.stdout.strip() or "none"


def make_image_asset(stage, stem, img, img_sha, log):
    out = os.path.join(stage, "assets", stem + ".img.xz")
    if os.path.isfile(out) and stamp_ok(out + ".inputs", img_sha + "\n"):
        say("image: %s is up to date (same image sha256), kept" % os.path.basename(out))
        return out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    say("image: compressing %s (xz -T0 -6, a few minutes)" % os.path.basename(img))
    with open(out + ".part", "wb") as f, open(log, "a", encoding="utf-8") as lf:
        rc = subprocess.run(["xz", "-T0", "-6", "-c", img], stdout=f, stderr=lf).returncode
    if rc != 0:
        os.remove(out + ".part")
        raise Refused("xz of the image failed (log %s)" % log)
    os.replace(out + ".part", out)
    write(out + ".inputs", img_sha + "\n")
    return out


# ------------------------------------------------------------------------------------------------ 3-5. sums, notes, size

def write_sums(assets, out):
    lines = ["%s  %s\n" % (sha256_file(a), os.path.basename(a)) for a in sorted(assets)]
    write(out, "".join(lines))
    return out


def scrub(text):
    """The community edition's own scrub (tools/edition_export.py, as for its CHANGELOG.md): rename.tsv, persona
    names -> neutral roles, then every line with a denylist word or private value dropped. Returns (text, dropped)."""
    ed = edition()
    E = ed["E"]
    return E.drop_matching_lines(ed["personas"].apply(E.capitalise_maintainer(E.rename(text, ed["renames"]),
                                                                              "CHANGELOG.md")), ed["patterns"])


def gate_text(text, where, patterns=None):
    """The export's gate scan of one text (raw and decoded forms: base64, unicode tricks, ...): refuse on any hit."""
    E = edition_module()
    hits = ["%s:%d: %s" % (where, ln, w) for ln, w in E.scan_text(text, patterns or edition()["patterns"])]
    if hits:
        raise Refused("the edition gate scan finds private values in %s: %s" % (where, "; ".join(hits[:5])))


def gate_digest(patterns, allow):
    """sha256 over the gate's pattern lists (denylist, private values, allow list), in order (#291): the sources
    archive's cache key holds it, so a changed list re-runs the gate scan instead of keeping an archive the old lists
    passed. Only the digest is stored, never the private values themselves."""
    h = hashlib.sha256()
    for label, rx in patterns or ():
        h.update(("p\0%s\0%s\0%d\n" % (label, getattr(rx, "pattern", rx), getattr(rx, "flags", 0))).encode())
    if allow is not None:
        for name in ("lines", "files", "persona_globs", "persona_names"):
            h.update(("a\0%s\0%r\n" % (name, sorted(getattr(allow, name, ()) or ()))).encode())
    return h.hexdigest()


def gate_tree(top, patterns=None, allow=None):
    """The export's gate scan (scan_tree: paths, text, binaries, decoded forms) over a folder: refuse on any hit."""
    E = edition_module()
    if patterns is None:
        ed = edition()
        patterns, allow = ed["patterns"], ed["allow"]
    hits = ["%s:%s: %s" % h for h in E.scan_tree(top, patterns, allow=allow)]
    if hits:
        raise Refused("the edition gate scan finds %d private value(s) in the sources archive's own files (nothing "
                      "packed): %s" % (len(hits), "; ".join(hits[:5])))


def release_notes(version, target, stem, changelog=None, lock=None):
    """Notes: the CHANGELOG section of version (else [Unreleased]) + the versions.lock table + where things are,
    scrubbed like the community edition (scrub)."""
    tools = os.path.join(REPO, "tools")
    CL = _load("hk_changelog", os.path.join(tools, "changelog.py"))
    V = _load("hk_versions", os.path.join(tools, "versions.py"))
    changelog = changelog or os.path.join(REPO, "CHANGELOG.md")
    lock = lock or os.path.join(REPO, "versions.lock")
    try:
        with open(changelog, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        raise Refused("no CHANGELOG at %s" % changelog)
    body = section_body(text, version) or CL.unreleased_body(text)
    if not body:
        raise Refused("CHANGELOG.md has no [%s] section and an empty [Unreleased]" % version)
    if os.path.isfile(lock):
        with open(lock, encoding="utf-8") as f:
            body = CL._with_versions_table(body, CL.versions_table(list(V.parse_lock(f.read()).values())))
    head = ("Coin Dojo %s: a ready-to-flash card image for a Raspberry Pi 5 arcade cabinet.\n\n"
            "- `%s.img.xz`: the image. Flash it with Raspberry Pi Imager (it reads .img.xz as is).\n"
            "- `%s-sources.tar.xz`: the source of everything the image carries that asks for it (see LICENCES.md "
            "on the card).\n"
            "- `SHA256SUMS`: the checksums of both.\n\n"
            "Built from commit %s.\n\n" % ("v" + version, stem, stem, target))
    return head + body.rstrip("\n") + "\n"


def section_body(text, version):
    m = re.search(r"^## \[%s\][^\n]*\n(.*?)(?=^## \[|^\[[^\]]+\]: |\Z)" % re.escape(version), text, re.M | re.S)
    return (m.group(1).strip() + "\n") if m and m.group(1).strip() else ""


def check_notes(path):
    """licences.py release-check --body: no tip or donation link where FBNeo ships (COMPLIANCE.md, Tip links)."""
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "licences.py"), "release-check", "--body", path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise Refused("the release notes fail licences.py release-check (no tip links on a release page): %s"
                      % " ".join(ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")))


def check_sizes(assets):
    big = ["%s (%s)" % (os.path.basename(a), human(os.path.getsize(a))) for a in assets if os.path.getsize(a) >= MAX_ASSET]
    if big:
        raise Refused("asset of 2 GiB or more (GitHub's per-file limit), nothing uploaded: " + ", ".join(big))


def published_sha(path=None):
    """The community main sha of the last publish (edition-published.tsv), or a refusal naming the fix."""
    p = os.path.expanduser(path or PUBLISHED)
    rows = []
    try:
        with open(p, encoding="utf-8") as f:
            rows = [ln.split("\t") for ln in f.read().splitlines() if ln.strip() and not ln.startswith("#")]
    except OSError:
        pass
    hits = [r[1].strip() for r in rows if len(r) >= 2 and r[0].strip() in PUBLISHED_URLS]
    if not hits or not SHA1.match(hits[-1]):
        raise Refused("unpublished: %s has no published sha for %s (make edition-publish OUT=... REMOTE=... APPLY=1 "
                      "first)" % (p, SLUG))
    return hits[-1]


# ------------------------------------------------------------------------------------------------ 6-7. GitHub

def read_token():
    """The release token from the macOS Keychain. Held in memory only; never printed."""
    try:
        r = subprocess.run([SECURITY, "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL)
        tok = r.stdout.strip() if r.returncode == 0 else ""
    except OSError:
        tok = ""
    if not tok or re.search(r"[^\x21-\x7e]", tok):
        raise Refused("no GitHub token in the Keychain item %s. Make a fine-grained token on the Coin Dojo account "
                      "(repository coin-dojo/coin-dojo only, permission Contents: read and write), then run: "
                      "security add-generic-password -s %s -a coin-dojo -w   (it asks for the token; it never "
                      "lands in your shell history)" % (KEYCHAIN_SERVICE, KEYCHAIN_SERVICE))
    return tok


class GitHub:
    def __init__(self, token, api=None, uploads=None, opener=None):
        self.api = (api or API).rstrip("/")
        self.uploads = (uploads or UPLOADS).rstrip("/")
        for kind, u in (("api", self.api), ("uploads", self.uploads)):
            p = urllib.parse.urlsplit(u)
            ok = (p.scheme == "https" and p.hostname == API_HOSTS[kind]) or \
                (p.scheme in ("http", "https") and p.hostname in LOCAL_HOSTS)
            if not ok:
                raise Refused("the GitHub %s host must be https://%s (got %s): the token goes nowhere else"
                              % (kind, API_HOSTS[kind], u))
        self._auth = "Bearer " + token
        self.opener = opener or OPENER

    def call(self, method, url, body=None, data=None, length=None, ok404=False):
        headers = {"Authorization": self._auth, "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "coin-dojo-release"}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif data is not None:
            headers["Content-Type"] = "application/octet-stream"
            headers["Content-Length"] = str(length)
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=TIMEOUT) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if ok404 and e.code in (404, 422):
                return None
            try:
                msg = json.loads(e.read() or b"{}").get("message", "")
            except ValueError:
                msg = ""
            path = urllib.parse.urlsplit(url).path       # never the headers; the URL carries no secret
            raise Refused("GitHub %s %s: HTTP %d %s" % (method, path, e.code, msg))
        except urllib.error.URLError as e:
            raise Refused("GitHub %s %s: %s" % (method, urllib.parse.urlsplit(url).path, e.reason))

    def repo(self, path=""):
        return "%s/repos/%s%s" % (self.api, SLUG, path)


class Progress:
    """A file object for the upload body that prints a progress line every 10 %."""

    def __init__(self, f, size, name):
        self.f, self.size, self.name, self.done, self.next = f, size, name, 0, 0.1

    def read(self, n=-1):
        b = self.f.read(n if n and n > 0 else CHUNK)
        self.done += len(b)
        if self.size and self.done / self.size >= self.next:
            say("upload: %s %d%% (%s of %s)" % (self.name, int(100 * self.done / self.size), human(self.done),
                                                human(self.size)))
            self.next += 0.1
        return b


def check_target(gh, sha):
    """The target must be a commit on GitHub's main (published), not just a sha in a local file."""
    if gh.call("GET", gh.repo("/commits/" + sha), ok404=True) is None:
        raise Refused("unpublished sha: %s is not on github.com/%s (publish first: make edition-publish)" % (sha, SLUG))
    cmp = gh.call("GET", gh.repo("/compare/%s...main" % sha), ok404=True)
    if not cmp or cmp.get("status") not in ("identical", "ahead"):
        raise Refused("unpublished sha: %s is not on %s main (compare: %s)" % (sha, SLUG, (cmp or {}).get("status")))
    info = gh.call("GET", gh.repo())
    if info.get("private"):
        say("WARN: github.com/%s is private: the release page and its links work only once the repository is public"
            % SLUG)


def find_release(gh, tag):
    page = 1
    while True:
        rels = gh.call("GET", gh.repo("/releases?per_page=100&page=%d" % page)) or []
        for r in rels:
            if r.get("tag_name") == tag:
                return r
        if len(rels) < 100:
            return None
        page += 1


def upload(gh, tag, target, notes, assets, local):
    """Create (or resume) the draft, upload what is missing or different, verify. It stays a draft. Returns it."""
    rel = find_release(gh, tag)
    name = "Coin Dojo " + tag
    if rel is None:
        rel = gh.call("POST", gh.repo("/releases"), body={"tag_name": tag, "target_commitish": target, "name": name,
                                                           "body": notes, "draft": True, "prerelease": False})
        say("upload: created draft release %s (id %s)" % (tag, rel.get("id")))
    elif rel.get("draft"):
        rel = gh.call("PATCH", gh.repo("/releases/%s" % rel["id"]), body={"target_commitish": target, "name": name,
                                                                           "body": notes})
        say("upload: resuming draft release %s (id %s)" % (tag, rel.get("id")))
    else:
        if rel.get("target_commitish") not in (target, None) and not tag_points_at(gh, tag, target):
            raise Refused("release %s is already published for another commit (%s): bump VERSION"
                          % (tag, rel.get("target_commitish")))
        say("upload: release %s is already published: checking its assets" % tag)
    rid = rel["id"]
    remote = {a["name"]: a for a in list_assets(gh, rid)}
    for path in assets:
        n = os.path.basename(path)
        size, sha = local[n]
        have = remote.get(n)
        if have and asset_matches(have, size, sha):
            say("upload: %s already there (same size and digest), kept" % n)
            continue
        if not rel.get("draft"):
            raise Refused("release %s is published and %s is missing or different: fix it on GitHub or bump VERSION"
                          % (tag, n))
        if have:
            say("upload: %s is there but differs (%s), replacing" % (n, have.get("state")))
            gh.call("DELETE", gh.repo("/releases/assets/%s" % have["id"]))
        url = "%s/repos/%s/releases/%s/assets?name=%s" % (gh.uploads, SLUG, rid, urllib.parse.quote(n))
        say("upload: %s (%s)" % (n, human(size)))
        with open(path, "rb") as f:
            gh.call("POST", url, data=Progress(f, size, n), length=size)
    verify(gh, rid, assets, local)
    return rel


def gate_body(body, stage, patterns=None):
    """The draft's notes as GitHub holds them (a person may have edited them there, #291): the edition gate scan and
    licences.py release-check, as step 4 runs them on notes.md. Refused: nothing is un-drafted."""
    gate_text(body, "the draft's notes on GitHub", patterns)
    path = os.path.join(stage, "draft-body.md")
    write(path, body)
    check_notes(path)


def publish(gh, tag, target, notes, assets, local, stage=None, patterns=None):
    """Un-draft after a person read the draft: verify again (nothing uploaded), gate the draft's own text (it may
    have been edited on GitHub), refuse when the tag already exists at another commit (the draft stays), then the tag
    must point at target. Returns the release."""
    rel = find_release(gh, tag)
    if rel is None:
        raise Refused("no release %s on GitHub: run with APPLY=1 first (it uploads and leaves a draft)" % tag)
    if rel.get("target_commitish") not in (target, None):
        raise Refused("the draft %s targets %s, not the published %s" % (tag, rel.get("target_commitish"), target))
    verify(gh, rel["id"], assets, local)
    if (rel.get("body") or "").strip() != notes.strip():
        say("WARN: the draft's notes differ from notes.md (edited on GitHub?): the draft's text is what is published")
    if rel.get("draft"):
        gate_body(rel.get("body") or "", stage or tempfile.mkdtemp(prefix="hk-gh-release."), patterns)
        ref = gh.call("GET", gh.repo("/git/ref/tags/" + tag), ok404=True)
        if ref and not tag_points_at(gh, tag, target):
            raise Refused("refs/tags/%s already exists at %s, not %s: the draft stays a draft (delete that tag or "
                          "bump VERSION)" % (tag, (ref.get("object") or {}).get("sha"), target))
        rel = gh.call("PATCH", gh.repo("/releases/%s" % rel["id"]), body={"draft": False, "make_latest": "true"})
        say("publish: un-drafted %s" % tag)
    else:
        say("publish: %s is already published" % tag)
    if not tag_points_at(gh, tag, target):
        raise Refused("tag %s does not point at %s on GitHub after publishing" % (tag, target))
    return rel


def list_assets(gh, rid):
    return gh.call("GET", gh.repo("/releases/%s/assets?per_page=100" % rid)) or []


def asset_matches(a, size, sha):
    dig = a.get("digest")
    return a.get("state") == "uploaded" and a.get("size") == size and (not dig or dig == "sha256:" + sha)


def verify(gh, rid, assets, local):
    """Each asset: present, uploaded, same size; GitHub's sha256 digest (when it gives one) equal to ours."""
    remote = {a["name"]: a for a in list_assets(gh, rid)}
    bad = []
    for path in assets:
        n = os.path.basename(path)
        size, sha = local[n]
        a = remote.get(n)
        if not a:
            bad.append("%s missing" % n)
        elif not asset_matches(a, size, sha):
            bad.append("%s: state %s, size %s (want %d), digest %s (want sha256:%s)"
                       % (n, a.get("state"), a.get("size"), size, a.get("digest"), sha))
        else:
            say("verify: %s %s %s" % (n, human(size), "digest ok" if a.get("digest") else "size ok (no digest given)"))
    extra = sorted(set(remote) - {os.path.basename(p) for p in assets})
    if extra:
        say("WARN: the release has assets this run did not make: %s" % ", ".join(extra))
    if bad:
        raise Refused("verify failed: " + "; ".join(bad))


def tag_points_at(gh, tag, sha):
    ref = gh.call("GET", gh.repo("/git/ref/tags/" + tag), ok404=True)
    if not ref:
        return False
    obj = ref.get("object", {})
    if obj.get("type") == "tag":                         # an annotated tag: follow it once
        obj = (gh.call("GET", gh.repo("/git/tags/" + obj.get("sha", ""))) or {}).get("object", {})
    return obj.get("sha") == sha


# ------------------------------------------------------------------------------------------------ the pipeline

def pipeline(version, img, apply, stage=None, inspect=None, sources=None, image_asset=None, changelog=None, lock=None,
             gh=None, scrubber=None, publish_draft=False, patterns=None):
    """Steps 1-7. The keyword hooks let the tests replace the heavy steps (docker, git, downloads, xz)."""
    if publish_draft and not apply:
        raise Refused("PUBLISH=1 needs APPLY=1 (it un-drafts a release an APPLY=1 run uploaded)")
    if not VERSION_RE.match(version or ""):
        raise Refused("VERSION=%r: want X.Y.Z" % version)
    img = os.path.abspath(img)
    if not os.path.isfile(img):
        raise Refused("no image at %s" % img)
    stem = re.sub(r"\.img$", "", os.path.basename(img))
    stage = stage or os.path.join(os.path.dirname(img), stem + "-release")
    os.makedirs(os.path.join(stage, "assets"), exist_ok=True)
    log = os.path.join(stage, "release.log")
    tag = "v" + version
    target = published_sha()
    say("gh-release: %s %s, image %s, target %s (%s)" % (SLUG, tag, os.path.basename(img), target,
                                                        ("PUBLISH" if publish_draft else "APPLY") if apply
                                                        else "dry run: nothing is uploaded"))
    t0 = time.time()

    say("== 1. image: the licence gate (read-only re-check in the Ansible container), log %s" % log)
    ins = os.path.join(stage, "inspect")
    (inspect or inspect_image)(img, ins, log)
    gate = check_gate(os.path.join(ins, "GATE.tsv"), tag)
    record_build(gate, log)
    img_sha = check_image_sum(img)
    say("image: strict gate PASS, sha256 %s matches %s.sha256" % (img_sha[:16], os.path.basename(img)))

    say("== 2. assets: image and sources (t+%ds)" % (time.time() - t0))
    licdir = os.path.join(ins, "licences")
    keyring = os.path.join(ins, "keyring.pgp")
    a_img = (image_asset or make_image_asset)(stage, stem, img, img_sha, log)
    a_src = (sources or make_sources)(stage, stem, licdir, keyring if os.path.isfile(keyring) else None, log, target)

    say("== 3. SHA256SUMS (t+%ds)" % (time.time() - t0))
    a_sums = write_sums([a_img, a_src], os.path.join(stage, "assets", "SHA256SUMS"))
    assets = [a_img, a_src, a_sums]

    say("== 4. release notes")
    notes_path = os.path.join(stage, "notes.md")
    notes, dropped = (scrubber or scrub)(release_notes(version, target, stem, changelog, lock))
    if dropped:
        say("WARN: notes: %d line(s) with a denylist word or private value dropped (the edition scrub)" % dropped)
    write(notes_path, notes)
    gate_text(notes, "the release notes", patterns)
    check_notes(notes_path)
    with open(notes_path, encoding="utf-8") as f:
        notes = f.read()

    say("== 5. size limit (each asset < 2 GiB)")
    check_sizes(assets)
    local = {}
    for a in assets:
        local[os.path.basename(a)] = (os.path.getsize(a), sha256_file(a))
    say("plan: release %s on github.com/%s, tag %s -> commit %s, notes %s (%d lines)"
        % ("Coin Dojo " + tag, SLUG, tag, target, notes_path, notes.count("\n")))
    for a in assets:
        n = os.path.basename(a)
        say("plan: upload %-40s %10s  sha256 %s" % (n, human(local[n][0]), local[n][1]))
    if not apply:
        say("RESULT: PASS gh-release dry run %s: %d assets ready in %s, nothing uploaded (APPLY=1 uploads; %ds)"
            % (tag, len(assets), os.path.join(stage, "assets"), time.time() - t0))
        return 0

    say("== 6. upload (token from the Keychain item %s)" % KEYCHAIN_SERVICE)
    gh = gh or GitHub(read_token())
    check_target(gh, target)
    if publish_draft:
        say("== 7. publish: re-verify the draft, check the tag, un-draft")
        rel = publish(gh, tag, target, notes, assets, local, stage, patterns)
        say("RESULT: PASS gh-release %s published: %s (%ds)" % (tag, rel.get("html_url", "?"), time.time() - t0))
        return 0
    rel = upload(gh, tag, target, notes, assets, local)
    say("== 7. verified; the release is a DRAFT")
    say("RESULT: PASS gh-release %s draft uploaded and verified: %s (read its notes, then PUBLISH=1 APPLY=1; %ds)"
        % (tag, rel.get("html_url", "?"), time.time() - t0))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", required=True, help="X.Y.Z (the tag is vX.Y.Z)")
    ap.add_argument("--image", required=True, help="the built community image (.img, with its .sha256 beside it)")
    ap.add_argument("--apply", action="store_true", help="upload, leave a draft (default: dry run)")
    ap.add_argument("--publish", action="store_true", help="with --apply: re-verify the draft and un-draft it")
    a = ap.parse_args(argv)
    try:
        return pipeline(a.version, a.image, a.apply, publish_draft=a.publish)
    except Refused as e:
        say("RESULT: FAIL gh-release: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
