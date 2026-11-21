#!/usr/bin/env python3
"""The GitHub Release pipeline (#287): scripts/gh_release.py and scripts/esde_sources.py.

  1. the image gate: the image's own GATE.tsv record must say strict on, 0 warnings, 0 blockers; the image must match
     the .sha256 run.sh writes after verify;
  2. refusals before any upload: an asset of 2 GiB or more, a tip link in the notes, an unpublished target sha, a
     missing Keychain token (with how to add it), a sources piece that is missing (named);
  3. a dry run on tiny fake files makes the assets and SHA256SUMS, prints the plan, reads no token, opens no socket;
  4. --apply against a local fake GitHub: draft, upload, verify (size + digest), publish, tag -> target; a re-run
     keeps what is there; a different asset of the same name is replaced; the token goes only into the header;
  5. esde_sources.py fetches each Ubuntu source package from Launchpad's file list, checks sha256 and size, refuses a
     changed file and a package with no publication.
  6. #291: the draft text on GitHub is gated again before the un-draft; the sources cache key holds the gate lists;
     release.log records the private commit that built the image.
No network (a fake HTTP server on 127.0.0.1 and fake openers); everything in tmp dirs. Plain python (hktest).
"""
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(SKILL))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
sys.path.insert(0, os.path.join(SKILL, "scripts"))
from hktest import check, main  # noqa: E402

import esde_sources as E  # noqa: E402
import gh_release as G  # noqa: E402

TARGET = "4a83c36a899c7338b4692eb5760d793a8dddb740"
FAKE_TOKEN = "FAKE-test-token-not-real-0000"
PRIVATE = [("private", re.compile(r"(?i)zorblax"))]      # the fixture's private value (a made-up word)
CHANGELOG = """# Changelog

All notable changes. https://keepachangelog.com/en/1.1.0/ and https://semver.org/spec/v2.0.0.html

## [Unreleased]

### Added

- A thing you can see on the cabinet (#1).

## [0.1.0] - 2026-11-14

### Added

- The first one.
"""


class Tmp:
    def __init__(self):
        self._td = tempfile.TemporaryDirectory(prefix="hk-ghrel-")
        self.d = self._td.name

    def p(self, *parts):
        return os.path.join(self.d, *parts)

    def write(self, rel, text, mode=None):
        path = self.p(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        if mode:
            os.chmod(path, mode)
        return path

    def close(self):
        self._td.cleanup()


def quiet(fn, *a, **kw):
    """(result or the Refused, stdout)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            r = fn(*a, **kw)
        except G.Refused as e:
            r = e
    return r, out.getvalue()


def gate(strict="on", warnings=0, blockers=0):
    return "# x\nstrict\t%s\nwarnings\t%s\nblockers\t%s\nversion\tv0.3.0\n" % (strict, warnings, blockers)


def fixture(t, gate_text=None, changelog=CHANGELOG):
    """A tiny 'image' with its .sha256, a published tsv, a CHANGELOG and versions.lock, and the step hooks."""
    img = t.write("work/coindojo-test.img", "not really an image\n" * 50)
    with open(img, "rb") as f:
        t.write("work/coindojo-test.img.sha256", "%s  coindojo-test.img\n" % hashlib.sha256(f.read()).hexdigest())
    pub = t.write("published.tsv", "git@github.com:coin-dojo/coin-dojo.git\t%s\n" % TARGET)
    cl = t.write("CHANGELOG.md", changelog)
    lock = t.p("versions.lock")                     # absent: no table, still fine

    def inspect(_img, out, _log):
        os.makedirs(os.path.join(out, "licences"), exist_ok=True)
        with open(os.path.join(out, "GATE.tsv"), "w") as f:
            f.write(gate() if gate_text is None else gate_text)

    def sources(stage, stem, _licdir, _keyring, _log, target):
        assert target == TARGET
        out = os.path.join(stage, "assets", stem + "-sources.tar.xz")
        with open(out, "wb") as f:
            f.write(b"tiny sources\n")
        return out
    return img, pub, dict(inspect=inspect, sources=sources, changelog=cl, lock=lock, scrubber=lambda s: (s, 0),
                          patterns=PRIVATE)


def published_tree(t, files, stamp_for=None):
    """A fake strict export (make edition-publish's OUT): one commit holding files, the marker stamped for it.
    Returns its commit sha; G.PUBLISH_DIR points at it."""
    d = t.p("publish")
    for rel, text in files.items():
        t.write("publish/" + rel, text)
    env = dict(os.environ, GIT_AUTHOR_NAME="x", GIT_AUTHOR_EMAIL="x@example.invalid", GIT_COMMITTER_NAME="x",
               GIT_COMMITTER_EMAIL="x@example.invalid", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    for a in (["init", "-q"], ["add", "-A"], ["commit", "-q", "--no-verify", "--no-gpg-sign", "-m", "x"]):
        subprocess.run(["git", "-C", d, *a], check=True, env=env, capture_output=True)
    sha = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    t.write("publish/.edition-export", "community edition export\nstrict-pass %s\n" % (stamp_for or sha))
    G.PUBLISH_DIR = d
    return sha


@contextlib.contextmanager
def published(path):
    old = G.PUBLISHED
    G.PUBLISHED = path
    try:
        yield
    finally:
        G.PUBLISHED = old


@contextlib.contextmanager
def no_network():
    import socket
    real = socket.socket.connect

    def refuse(*_a, **_k):
        raise AssertionError("network used in a dry run")
    socket.socket.connect = refuse
    try:
        yield
    finally:
        socket.socket.connect = real


# ------------------------------------------------------------------------------------------------ 1. image gate

def test_gate_record_is_the_judge():
    t = Tmp()
    try:
        for text, want in ((gate(strict="off"), "not built with HK_LICENCES_STRICT=1"),
                           (gate(blockers=2), "2 release blockers"), (gate(warnings=1), "1 warnings")):
            r, _ = quiet(G.check_gate, t.write("g.tsv", text))
            check(isinstance(r, G.Refused) and want in str(r), "BROKEN: GATE.tsv %r refused (%s)" % (want, r))
        r, _ = quiet(G.check_gate, t.p("none.tsv"))
        check(isinstance(r, G.Refused) and "rebuild it with HK_LICENCES_STRICT=1" in str(r),
              "BROKEN: an image without a gate record is refused, with the fix")
        r, _ = quiet(G.check_gate, t.write("g.tsv", gate()))
        check(isinstance(r, dict) and r["strict"] == "on", "strict on, 0 warnings, 0 blockers passes")
        r, _ = quiet(G.check_gate, t.write("g.tsv", gate()), "v0.3.0")
        check(isinstance(r, dict), "#294: the image's version equals the release tag")
        r, _ = quiet(G.check_gate, t.write("g.tsv", gate().replace("v0.3.0", "a1b2c3d")), "v0.3.0")
        check(isinstance(r, G.Refused) and "shows version 'a1b2c3d'" in str(r),
              "BROKEN: an image showing a commit id, not v0.3.0, is refused (%s)" % r)
    finally:
        t.close()


def test_not_strict_image_stops_the_pipeline_before_any_asset():
    t = Tmp()
    try:
        img, pub, hooks = fixture(t, gate_text=gate(strict="off"))
        with published(pub), no_network():
            r, out = quiet(G.pipeline, "0.3.0", img, False, **hooks)
        check(isinstance(r, G.Refused) and "HK_LICENCES_STRICT=1" in str(r), "BROKEN: a non-strict image is refused")
        check(not os.listdir(t.p("work", "coindojo-test-release", "assets")), "no asset was made")
    finally:
        t.close()


def test_image_must_match_its_verify_checksum():
    t = Tmp()
    try:
        img, _pub, _h = fixture(t)
        check(quiet(G.check_image_sum, img)[0] is not None, "the image matches its .sha256")
        with open(img, "a") as f:
            f.write("changed\n")
        r, _ = quiet(G.check_image_sum, img)
        check(isinstance(r, G.Refused) and "does not match" in str(r), "BROKEN: a changed image is refused")
        os.remove(img + ".sha256")
        r, _ = quiet(G.check_image_sum, img)
        check(isinstance(r, G.Refused) and "only after verify" in str(r), "BROKEN: no .sha256 (verify never passed)")
    finally:
        t.close()


def test_inspect_script_mounts_read_only():
    src = open(os.path.join(SKILL, "scripts", "release-inspect.sh")).read()
    check(src.count("losetup -r ") == 3, "release-inspect.sh attaches all three partitions read-only")
    check("build --strict" in src and "release-check --root" in src and "diff -r" in src,
          "it re-runs build --strict, release-check and compares the bundle")


# ------------------------------------------------------------------------------------------------ 2. refusals

def test_asset_of_2_gib_is_refused():
    t = Tmp()
    try:
        big, ok = t.p("big.img.xz"), t.p("ok.tar.xz")
        for p, n in ((big, G.MAX_ASSET), (ok, G.MAX_ASSET - 1)):
            with open(p, "wb") as f:
                f.truncate(n)                      # sparse: no real 2 GiB written
        r, _ = quiet(G.check_sizes, [ok, big])
        check(isinstance(r, G.Refused) and "big.img.xz" in str(r) and "ok.tar.xz" not in str(r),
              "BROKEN: an asset of exactly 2 GiB is refused (%s)" % r)
        check(quiet(G.check_sizes, [ok])[0] is None, "2 GiB minus one byte passes")
    finally:
        t.close()


def test_tip_link_in_the_notes_is_refused():
    t = Tmp()
    try:
        img, pub, hooks = fixture(t, changelog=CHANGELOG.replace("(#1).", "(#1). Tips: https://ko-fi.com/someone"))
        with published(pub), no_network():
            r, _ = quiet(G.pipeline, "0.3.0", img, False, **hooks)
        check(isinstance(r, G.Refused) and "release-check" in str(r) and "ko-fi.com" in str(r),
              "BROKEN: a tip link in the release notes is refused (%s)" % r)
    finally:
        t.close()


def test_unpublished_sha_is_refused():
    t = Tmp()
    try:
        for text in ("", "git@github.com:someone/else.git\t%s\n" % TARGET, "git@github.com:coin-dojo/coin-dojo.git\tnot-a-sha\n"):
            r, _ = quiet(G.published_sha, t.write("p.tsv", text))
            check(isinstance(r, G.Refused) and "unpublished" in str(r), "BROKEN: no published sha in %r" % text)
        r, _ = quiet(G.published_sha, t.p("missing.tsv"))
        check(isinstance(r, G.Refused), "BROKEN: no edition-published.tsv at all")
        tsv = t.write("p.tsv", "git@github.com:coin-dojo/coin-dojo.git\t%s\ngit@github.com:coin-dojo/coin-dojo.git\t%s\n"
                      % ("1" * 40, TARGET))
        check(G.published_sha(tsv) == TARGET, "the last publish wins")
        with FakeGitHub(commits=set()) as gh:
            r, _ = quiet(G.check_target, gh.client(), TARGET)
            check(isinstance(r, G.Refused) and "unpublished sha" in str(r), "BROKEN: a sha GitHub does not have")
        with FakeGitHub(compare="diverged") as gh:
            r, _ = quiet(G.check_target, gh.client(), TARGET)
            check(isinstance(r, G.Refused) and "not on" in str(r), "BROKEN: a sha that is not on main")
    finally:
        t.close()


def test_missing_token_says_how_to_add_it():
    t = Tmp()
    old = G.SECURITY
    try:
        G.SECURITY = t.write("security", "#!/bin/sh\necho 'security: item not found' >&2\nexit 44\n", 0o755)
        r, _ = quiet(G.read_token)
        check(isinstance(r, G.Refused) and "security add-generic-password -s github-coin-dojo-release" in str(r),
              "BROKEN: no Keychain item: refused with the command to add it")
        G.SECURITY = t.write("security", "#!/bin/sh\necho 'two words'\n", 0o755)
        r, _ = quiet(G.read_token)
        check(isinstance(r, G.Refused) and "two words" not in str(r), "BROKEN: a malformed token is refused, not echoed")
        G.SECURITY = t.p("no-such-binary")
        check(isinstance(quiet(G.read_token)[0], G.Refused), "BROKEN: no security binary at all")
        G.SECURITY = t.write("security", "#!/bin/sh\n[ \"$3\" = github-coin-dojo-release ] && [ \"$4\" = -w ] && "
                             "echo %s\n" % FAKE_TOKEN, 0o755)
        r, out = quiet(G.read_token)
        check(r == FAKE_TOKEN and FAKE_TOKEN not in out, "the token is read from that item and not printed")
    finally:
        G.SECURITY = old
        t.close()


def sources_fixture(t, build_text="#!/bin/sh\necho build\n"):
    lic = t.p("lic")
    t.write("lic/components.tsv", "# id\tversion\tspdx\torigin\tsource\tref\n"
            "stella\t7\tGPL-2.0\tbuilt\thttps://github.com/stella-emu/stella\t%s\n"
            "fonts\t1\tOFL-1.1\tours\trepo\t-\n" % ("d" * 40))
    t.write("lic/debian-packages.tsv", "# package\n")
    sha = published_tree(t, {"build/stella/build.sh": build_text, "build/README.md": "builds\n",
                             "docs/other.md": "not in the archive\n"})
    return lic, sha


@contextlib.contextmanager
def fake_fetchers(t):
    saved = (G.fetch_upstream, G.run_tool, G.PUBLISH_DIR)

    def fetch(cid, url, ref, dest, log):
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "main.c"), "w") as f:
            f.write("int main(void) { return 0; }\n")
        with open(os.path.join(dest, "._main.c"), "w") as f:      # an AppleDouble file: must not be packed
            f.write("junk")

    def tool(name, args, log, mount=None):
        out = args[args.index("--out") + 1]
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "SOURCES.tsv"), "w") as f:
            f.write("# %s\n" % name)
    G.fetch_upstream, G.run_tool = fetch, tool
    try:
        yield
    finally:
        G.fetch_upstream, G.run_tool, G.PUBLISH_DIR = saved


def test_sources_name_each_missing_piece():
    t = Tmp()
    try:
        lic, sha = sources_fixture(t)
        with fake_fetchers(t):
            r, _ = quiet(G.make_sources, t.p("stage"), "coindojo-test", lic, None, t.p("log"), sha, PRIVATE)
        msg = str(r)
        check(isinstance(r, G.Refused) and "stella: no BUILDINFO" in msg and "es-de/libraries.tsv" in msg
              and "raspberrypi-archive-keyring" in msg, "BROKEN: every missing sources piece is named (%s)" % msg)
        t.write("lic/components.tsv", "# id\tversion\tspdx\torigin\tsource\tref\nmame\t1\tGPL-2.0\tbuilt\thttps://x\tunpinned\n")
        r, _ = quiet(G.built_components, lic)
        check(isinstance(r, G.Refused) and "mame" in str(r), "BROKEN: a built component without an exact commit")
    finally:
        t.close()


def complete_lic(t):
    t.write("lic/stella/BUILDINFO", "tag: 7.0\ncommit: %s\n" % ("d" * 40))
    t.write("lic/es-de/libraries.tsv", "# file\n")
    return t.write("keyring.pgp", "fake keyring\n")


def test_sources_come_from_the_published_tree_and_pass_the_gate():
    t = Tmp()
    try:
        lic, sha = sources_fixture(t)
        kr = complete_lic(t)
        stage = t.p("stage")
        with fake_fetchers(t):
            G.PUBLISH_DIR = t.p("publish")
            r, out = quiet(G.make_sources, stage, "coindojo-test", lic, kr, t.p("log"), sha, PRIVATE)
            check(isinstance(r, str) and os.path.isfile(r), "a complete set packs the archive (%s)" % r)
            with tarfile.open(r) as tf:
                ms = tf.getmembers()
            names = {m.name.lstrip("./") for m in ms}
            for want in ("README.md", "build/stella/build.sh", "buildinfo/stella/BUILDINFO",
                         "upstream/stella-%s/main.c" % ("d" * 40), "ubuntu-esde/SOURCES.tsv", "raspberrypi/SOURCES.tsv"):
                check(want in names, "the archive holds %s" % want)
            check("docs/other.md" not in names, "only build/ of the published tree goes in")
            check(not [n for n in names if os.path.basename(n).startswith("._")], "BROKEN: no AppleDouble ._ files")
            check(all(m.uid == 0 and m.gid == 0 and m.uname == "root" and m.gname == "root" for m in ms),
                  "every tar header says root:root, uid/gid 0 (not the Mac login)")
            check(not [m.name for m in ms if any("xattr" in k.lower() for k in m.pax_headers)],
                  "no xattr records in the tar headers")
            readme = [m for m in ms if m.name.lstrip("./") == "README.md"]
            with tarfile.open(r) as tf:
                text = tf.extractfile(readme[0]).read().decode()
            check(sha in text and "BUILDINFO is kept exactly" in text, "the README explains the patch sha note")

            os.remove(r)
            t2 = Tmp()
            try:
                lic2, sha2 = sources_fixture(t2, "#!/bin/sh\n# built by zorblax\n")
                kr2 = complete_lic(t2)
                r, out = quiet(G.make_sources, t2.p("stage"), "coindojo-test", lic2, kr2, t2.p("log"), sha2, PRIVATE)
                check(isinstance(r, G.Refused) and "edition gate scan" in str(r) and "build/stella/build.sh" in str(r),
                      "BROKEN: a private value in the published build/ is refused (%s)" % r)
                check(not os.path.exists(t2.p("stage", "assets", "coindojo-test-sources.tar.xz")), "no asset written")
            finally:
                t2.close()

            G.PUBLISH_DIR = t.p("publish")
            t.write("lic/stella/BUILDINFO", "commit: %s\nhost: zorblax-mac\n" % ("d" * 40))
            r, _ = quiet(G.make_sources, stage, "coindojo-test", lic, kr, t.p("log"), sha, PRIVATE)
            check(isinstance(r, G.Refused) and "buildinfo/stella/BUILDINFO" in str(r),
                  "BROKEN: a private value in a BUILDINFO is refused too")
            r, _ = quiet(G.make_sources, stage, "coindojo-test", lic, kr, t.p("log"), "e" * 40, PRIVATE)
            check(isinstance(r, G.Refused) and "never the private checkout" in str(r),
                  "BROKEN: a published tree not stamped for the target sha is refused")
    finally:
        t.close()


def test_tar_headers_are_neutral():
    t = Tmp()
    try:
        t.write("src/a/file.txt", "x\n")
        t.write("src/a/._file.txt", "apple double\n")
        if shutil.which("xattr"):
            subprocess.run(["xattr", "-w", "com.example.test", "1", t.p("src/a/file.txt")], capture_output=True)
        out = t.p("o.tar.xz")
        G.tar([(t.p("src"), ["a"])], out, True, t.p("log"))
        with tarfile.open(out) as tf:
            ms = tf.getmembers()
        check(sorted(m.name for m in ms) == ["a", "a/file.txt"], "explicit members, no ._ file (%s)"
              % [m.name for m in ms])
        check(all((m.uid, m.gid, m.uname, m.gname) == (0, 0, "root", "root") for m in ms), "root:root headers")
        check(not [k for m in ms for k in m.pax_headers if "xattr" in k.lower()], "BROKEN: no xattr in the headers")
    finally:
        t.close()


# ------------------------------------------------------------------------------------------------ 3. dry run

def test_dry_run_on_tiny_files():
    t = Tmp()
    old = G.SECURITY
    try:
        G.SECURITY = t.write("security", "#!/bin/sh\ntouch %s\necho %s\n" % (t.p("TOKEN-READ"), FAKE_TOKEN), 0o755)
        img, pub, hooks = fixture(t)
        with published(pub), no_network():
            r, out = quiet(G.pipeline, "0.3.0", img, False, **hooks)
        check(r == 0, "dry run exits 0 (%s)" % (r if r else out.strip().splitlines()[-1:]))
        assets = t.p("work", "coindojo-test-release", "assets")
        names = sorted(n for n in os.listdir(assets) if not n.endswith(".inputs"))
        check(names == ["SHA256SUMS", "coindojo-test-sources.tar.xz", "coindojo-test.img.xz"],
              "assets: image .img.xz, sources .tar.xz, SHA256SUMS (%s)" % names)
        sums = open(os.path.join(assets, "SHA256SUMS")).read().splitlines()
        want = ["%s  %s" % (G.sha256_file(os.path.join(assets, n)), n) for n in sorted(names) if n != "SHA256SUMS"]
        check(sums == want, "SHA256SUMS covers both other assets with their sha256")
        for n in names:
            check(("plan: upload %s" % n) in out, "the plan names %s with its size and sha256" % n)
        check("tag v0.3.0 -> commit %s" % TARGET in out, "the plan names the tag and the published target sha")
        check(out.strip().splitlines()[-1].startswith("RESULT: PASS gh-release dry run v0.3.0"), "last line: RESULT PASS")
        check(not os.path.exists(t.p("TOKEN-READ")), "a dry run never reads the Keychain")
        notes = open(t.p("work", "coindojo-test-release", "notes.md")).read()
        check("A thing you can see on the cabinet" in notes and TARGET in notes and "The first one" not in notes,
              "notes: [Unreleased] (no [0.3.0] yet) and the target commit")
        with published(pub), no_network():
            r, out2 = quiet(G.pipeline, "0.3.0", img, False, **hooks)
        check(r == 0 and "is up to date (same image sha256), kept" in out2, "a re-run keeps the compressed image")
        r, _ = quiet(G.pipeline, "0.3", img, False, **hooks)
        check(isinstance(r, G.Refused) and "X.Y.Z" in str(r), "BROKEN: VERSION=0.3 is refused")
    finally:
        G.SECURITY = old
        t.close()


def test_notes_use_the_version_section_when_there_is_one():
    t = Tmp()
    try:
        cl = t.write("CHANGELOG.md", CHANGELOG)
        notes = G.release_notes("0.1.0", TARGET, "coindojo-v0.1.0", cl, t.p("none.lock"))
        check("The first one" in notes and "A thing you can see" not in notes, "[0.1.0] section, not [Unreleased]")
        r, _ = quiet(G.release_notes, "0.2.0", TARGET, "x", t.write("C2.md", CHANGELOG.replace(
            "### Added\n\n- A thing you can see on the cabinet (#1).\n\n", "")), t.p("none.lock"))
        check(isinstance(r, G.Refused), "BROKEN: no section and an empty [Unreleased] is refused")
    finally:
        t.close()


def test_notes_pass_the_edition_gate_scan():
    t = Tmp()
    try:
        check(quiet(G.gate_text, "clean notes\n", "notes", PRIVATE)[0] is None, "clean notes pass the gate")
        for text, kind in (("by z\u200borblax\n", "unicode"), ("data: " + __import__("base64").b64encode(
                b"a zorblax b").decode() + "\n", "base64")):
            r, _ = quiet(G.gate_text, text, "notes", PRIVATE)
            check(isinstance(r, G.Refused) and "private@" + kind in str(r),
                  "BROKEN: a %s-hidden private value in the notes is refused (%s)" % (kind, r))
        img, pub, hooks = fixture(t, changelog=CHANGELOG.replace("(#1).", "(#1) z\u200borblax."))
        with published(pub), no_network():
            r, _ = quiet(G.pipeline, "0.3.0", img, False, **hooks)
        check(isinstance(r, G.Refused) and "edition gate scan" in str(r), "BROKEN: the pipeline stops on it")
    finally:
        t.close()


# ------------------------------------------------------------------------------------------------ 4. apply (fake GitHub)

class FakeGitHub:
    """A tiny GitHub REST fake on 127.0.0.1 (the endpoints gh_release.py uses)."""

    def __init__(self, commits=None, compare="identical", digests=True):
        self.commits = {TARGET} if commits is None else commits
        self.compare, self.digests = compare, digests
        self.releases, self.assets, self.tags, self.auth, self.calls = {}, {}, {}, set(), []
        self.next_id = 1

    def __enter__(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _send(self, code, obj=None):
                data = json.dumps(obj).encode() if obj is not None else b""
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def do_GET(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

            def do_PATCH(self):
                self._route("PATCH")

            def do_DELETE(self):
                self._route("DELETE")

            def _route(self, method):
                fake.auth.add(self.headers.get("Authorization"))
                u = urllib.parse.urlsplit(self.path)
                p, q = u.path, urllib.parse.parse_qs(u.query)
                fake.calls.append((method, p))
                base = "/repos/coin-dojo/coin-dojo"
                body = self._body()
                if method == "GET" and p == base:
                    return self._send(200, {"private": False})
                if method == "GET" and p.startswith(base + "/commits/"):
                    return self._send(200 if p.rsplit("/", 1)[1] in fake.commits else 404, {"message": "Not Found"})
                if method == "GET" and p.startswith(base + "/compare/"):
                    return self._send(200, {"status": fake.compare})
                if method == "GET" and p == base + "/releases":
                    return self._send(200, list(fake.releases.values()) if q.get("page") == ["1"] else [])
                if method == "POST" and p == base + "/releases":
                    d = json.loads(body)
                    rid = fake.next_id
                    fake.next_id += 1
                    fake.releases[rid] = dict(d, id=rid, html_url="https://example.invalid/releases/%s" % d["tag_name"])
                    return self._send(201, fake.releases[rid])
                if p.startswith(base + "/releases/assets/") and method == "DELETE":
                    fake.assets.pop(int(p.rsplit("/", 1)[1]), None)
                    return self._send(204)
                if p.startswith(base + "/releases/") and p.endswith("/assets"):
                    rid = int(p.split("/")[-2])
                    if method == "GET":
                        return self._send(200, [a for a in fake.assets.values() if a["rid"] == rid])
                    aid = fake.next_id
                    fake.next_id += 1
                    a = {"id": aid, "rid": rid, "name": q["name"][0], "state": "uploaded", "size": len(body)}
                    if fake.digests:
                        a["digest"] = "sha256:" + hashlib.sha256(body).hexdigest()
                    fake.assets[aid] = a
                    return self._send(201, a)
                if method == "PATCH" and p.startswith(base + "/releases/"):
                    r = fake.releases[int(p.rsplit("/", 1)[1])]
                    r.update(json.loads(body))
                    if r.get("draft") is False:
                        fake.tags[r["tag_name"]] = r["target_commitish"]
                    return self._send(200, r)
                if method == "GET" and p.startswith(base + "/git/ref/tags/"):
                    tag = p.rsplit("/", 1)[1]
                    if tag in fake.tags:
                        return self._send(200, {"object": {"type": "commit", "sha": fake.tags[tag]}})
                    return self._send(404, {"message": "Not Found"})
                return self._send(404, {"message": "no route %s %s" % (method, p)})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_a):
        self.srv.shutdown()
        self.srv.server_close()

    def client(self):
        return G.GitHub(FAKE_TOKEN, api=self.url, uploads=self.url)


def test_apply_against_a_fake_github():
    t = Tmp()
    try:
        img, pub, hooks = fixture(t)
        with published(pub), FakeGitHub() as fake:
            r, out = quiet(G.pipeline, "0.3.0", img, True, gh=fake.client(), **hooks)
            check(r == 0, "apply exits 0 (%s)" % (r if r else out.strip().splitlines()[-1:]))
            rel = list(fake.releases.values())
            check(len(rel) == 1 and rel[0]["tag_name"] == "v0.3.0" and rel[0]["target_commitish"] == TARGET
                  and rel[0]["draft"] is True, "APPLY=1: one DRAFT release v0.3.0 at the published sha")
            check("v0.3.0" not in fake.tags and "draft uploaded and verified" in out, "no tag yet, the draft is verified")
            r2, out2 = quiet(G.pipeline, "0.3.0", img, True, gh=fake.client(), publish_draft=True, **hooks)
            check(r2 == 0 and rel[0]["draft"] is False, "PUBLISH=1 APPLY=1 un-drafts it (%s)" % out2.strip()[-200:])
            out += out2
            check(sorted(a["name"] for a in fake.assets.values()) ==
                  ["SHA256SUMS", "coindojo-test-sources.tar.xz", "coindojo-test.img.xz"], "all three assets uploaded")
            check(fake.tags.get("v0.3.0") == TARGET, "the tag points at the target")
            check("RESULT: PASS gh-release v0.3.0 published: https://example.invalid/releases/v0.3.0" in out2,
                  "the release URL is printed")
            check(fake.auth == {"Bearer " + FAKE_TOKEN}, "every call carries the token in the Authorization header")
            check(FAKE_TOKEN not in out, "the token is never printed")
            check(not [c for c in fake.calls if FAKE_TOKEN in c[1]], "the token never appears in a URL")
            n_up = len(fake.assets)
            r, out = quiet(G.pipeline, "0.3.0", img, True, gh=fake.client(), **hooks)
            check(r == 0 and out.count("already there (same size and digest), kept") == 3 and len(fake.assets) == n_up,
                  "a re-run uploads nothing (idempotent)")
    finally:
        t.close()


def test_apply_resumes_a_draft_and_replaces_a_bad_asset():
    t = Tmp()
    try:
        img, pub, hooks = fixture(t)
        with published(pub), FakeGitHub(digests=False) as fake:
            fake.releases[1] = {"id": 1, "tag_name": "v0.3.0", "draft": True, "target_commitish": TARGET,
                                "html_url": "https://example.invalid/r"}
            fake.assets[50] = {"id": 50, "rid": 1, "name": "coindojo-test.img.xz", "state": "starter", "size": 3}
            fake.next_id = 100
            r, out = quiet(G.pipeline, "0.3.0", img, True, gh=fake.client(), **hooks)
            check(r == 0 and "resuming draft release v0.3.0" in out, "an earlier draft is resumed, not duplicated")
            check(50 not in fake.assets and "is there but differs (starter), replacing" in out,
                  "BROKEN: a half-uploaded asset of the same name is replaced")
            check("size ok (no digest given)" in out, "without a digest from GitHub the size is checked")
            check(len(fake.releases) == 1 and fake.releases[1]["draft"] is True, "and it stays a draft")
    finally:
        t.close()


def test_publish_refuses_a_tag_at_another_commit():
    t = Tmp()
    try:
        img, pub, hooks = fixture(t)
        with published(pub), FakeGitHub() as fake:
            r, _ = quiet(G.pipeline, "0.3.0", img, True, publish_draft=True, gh=fake.client(), **hooks)
            check(isinstance(r, G.Refused) and "APPLY=1 first" in str(r), "BROKEN: PUBLISH with no draft is refused")
            check(quiet(G.pipeline, "0.3.0", img, True, gh=fake.client(), **hooks)[0] == 0, "APPLY=1 makes the draft")
            fake.tags["v0.3.0"] = "f" * 40
            r, _ = quiet(G.pipeline, "0.3.0", img, True, publish_draft=True, gh=fake.client(), **hooks)
            check(isinstance(r, G.Refused) and "already exists at " + "f" * 40 in str(r),
                  "BROKEN: refs/tags/v0.3.0 at another sha refuses the publish (%s)" % r)
            check(list(fake.releases.values())[0]["draft"] is True, "and the draft stays a draft")
        r, _ = quiet(G.pipeline, "0.3.0", img, False, publish_draft=True, **hooks)
        check(isinstance(r, G.Refused) and "needs APPLY=1" in str(r), "BROKEN: PUBLISH=1 without APPLY=1")
    finally:
        t.close()


def test_publish_gates_the_draft_text_on_github():
    """#291: the draft's notes as GitHub holds them (edited there after APPLY=1) pass the edition gate scan and the
    tip-link check before the un-draft; a private value or a tip link leaves it a draft."""
    t = Tmp()
    try:
        img, pub, hooks = fixture(t)
        with published(pub), FakeGitHub() as fake:
            check(quiet(G.pipeline, "0.3.0", img, True, gh=fake.client(), **hooks)[0] == 0, "APPLY=1 makes the draft")
            rel = list(fake.releases.values())[0]
            clean = rel["body"]
            for bad, why in (("Thanks to Zorblax for testing.", "edition gate scan"),
                             ("Tip jar: https://ko-fi.com/example", "release-check")):
                rel["body"] = clean + "\n" + bad + "\n"
                r, _ = quiet(G.pipeline, "0.3.0", img, True, publish_draft=True, gh=fake.client(), **hooks)
                check(isinstance(r, G.Refused) and why in str(r) and rel["draft"] is True and "v0.3.0" not in fake.tags,
                      "BROKEN: a draft edited on GitHub with %r is refused, it stays a draft (%s)" % (bad, r))
            rel["body"] = clean + "\nA line a person added on GitHub.\n"
            r, out = quiet(G.pipeline, "0.3.0", img, True, publish_draft=True, gh=fake.client(), **hooks)
            check(r == 0 and rel["draft"] is False and "the draft's text is what is published" in out,
                  "a clean edit on GitHub passes the gate and is published (%s)" % out.strip()[-160:])
    finally:
        t.close()


def test_sources_cache_key_holds_the_gate_lists():
    """#291: the sources archive is re-used only while the gate's pattern lists are the same; a new private value
    re-runs the scan instead of keeping an archive the old lists passed."""
    t = Tmp()
    try:
        lic, sha = sources_fixture(t, "#!/bin/sh\necho build by quuxbot\n")
        kr = complete_lic(t)
        stage = t.p("stage")
        with fake_fetchers(t):
            G.PUBLISH_DIR = t.p("publish")
            r, _ = quiet(G.make_sources, stage, "coindojo-test", lic, kr, t.p("log"), sha, PRIVATE)
            check(isinstance(r, str) and os.path.isfile(r), "the archive is packed (%s)" % r)
            r, out = quiet(G.make_sources, stage, "coindojo-test", lic, kr, t.p("log"), sha, PRIVATE)
            check(isinstance(r, str) and "is up to date" in out, "same inputs and same lists: kept")
            more = PRIVATE + [("private", re.compile(r"(?i)quuxbot"))]
            r, out = quiet(G.make_sources, stage, "coindojo-test", lic, kr, t.p("log"), sha, more)
            check(isinstance(r, G.Refused) and "edition gate scan" in str(r) and "is up to date" not in out,
                  "BROKEN: a private value added to the lists is caught, not skipped by the cache (%s)" % r)
        a = G.gate_digest(PRIVATE, None)
        check(a == G.gate_digest(list(PRIVATE), None) and a != G.gate_digest(more, None),
              "the digest follows the pattern list")
        ed = G.edition_module()
        allow = ed.Allow()
        allow.lines.append(("README.md", "word", "a line"))
        check(G.gate_digest(PRIVATE, ed.Allow()) != G.gate_digest(PRIVATE, allow), "and the allow list")
        with open(t.p("stage", "assets", "coindojo-test-sources.tar.xz.inputs")) as f:
            check("quuxbot" not in f.read().lower(), "the key stores a digest, never the values")
    finally:
        t.close()


def test_release_log_records_the_building_commit():
    """#291 (L6): release.log names the private commit the image's own record (GATE.tsv version, run.sh's git describe)
    points at, resolved to a full sha when this checkout has it; a dirty or unknown build says so. Nothing refused."""
    t = Tmp()
    try:
        repo = t.p("repo")
        os.makedirs(repo)
        env = dict(os.environ, GIT_AUTHOR_NAME="x", GIT_AUTHOR_EMAIL="x@example.invalid", GIT_COMMITTER_NAME="x",
                   GIT_COMMITTER_EMAIL="x@example.invalid", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        for a in (["init", "-q"], ["commit", "-q", "--allow-empty", "--no-gpg-sign", "-m", "x"], ["tag", "v0.2.0"],
                  ["commit", "-q", "--allow-empty", "--no-gpg-sign", "-m", "y"]):
            subprocess.run(["git", "-C", repo, *a], check=True, env=env, capture_output=True)
        head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        log = t.p("release.log")
        desc = "v0.2.0-1-g" + head[:7]
        line, _ = quiet(G.record_build, {"version": desc}, log, repo)
        check(head in line and "uncommitted" not in line, "a describe of this checkout resolves to its full sha (%s)" % line)
        line, _ = quiet(G.record_build, {"version": desc + "+dirty"}, log, repo)
        check(head in line and "uncommitted changes" in line, "a +dirty build is named as such")
        for v in ({"version": "unknown"}, {}, {"version": "--upload-pack=touch x"}, {"version": "v9.9.9-1-gabcdef1"}):
            line, _ = quiet(G.record_build, v, log, repo)
            check("not in this checkout" in line, "BROKEN: %r is recorded, not resolved, nothing refused" % v)
        with open(log) as f:
            check(f.read().count("build-record: ") == 6, "every call lands in release.log")
        img, pub, hooks = fixture(t)
        with published(pub):
            r, _ = quiet(G.pipeline, "0.3.0", img, False, **hooks)
        with open(t.p("work", "coindojo-test-release", "release.log")) as f:
            check(r == 0 and "build-record: image built from private v0.3.0" in f.read(), "the pipeline records it")
    finally:
        t.close()


def test_api_hosts_are_github_only():
    for api, up in (("http://api.github.com", "https://uploads.github.com"),
                    ("https://api.example.invalid", "https://uploads.github.com"),
                    ("https://api.github.com", "https://uploads.github.com.example.invalid")):
        r, _ = quiet(G.GitHub, FAKE_TOKEN, api=api, uploads=up)
        check(isinstance(r, G.Refused), "BROKEN: %s / %s refused (the token goes to GitHub only)" % (api, up))
    check(isinstance(G.GitHub(FAKE_TOKEN, api="https://api.github.com", uploads="https://uploads.github.com"),
                     G.GitHub), "api.github.com + uploads.github.com are allowed")


def test_redirects_drop_the_token():
    h = G.SafeRedirect()
    for new, keep in (("https://api.github.com/y", True), ("https://objects.example.invalid/y", False),
                      ("http://api.github.com/y", False)):
        req = __import__("urllib.request").request.Request("https://api.github.com/x",
                                                          headers={"Authorization": "Bearer " + FAKE_TOKEN})
        r = h.redirect_request(req, None, 302, "Found", {}, new)
        check(r is not None and r.has_header("Authorization") == keep,
              "a redirect to %s %s the token" % (new, "keeps" if keep else "BROKEN: drops"))


# ------------------------------------------------------------------------------------------------ 5. esde_sources

LIBS = """# header
# file\tpackage\tversion\tsource\tsource_version\tspdx\towes\tsource_url\tsha256
usr/lib/libfoo.so.1\tlibfoo1\t1.0-1\tfoo\t1.0-1\tLGPL-2.1-or-later\tsource\thttps://launchpad.net/ubuntu/+source/foo/1.0-1\t%s
usr/lib/libbar.so.1\tlibbar1\t2.0-1\tbar\t2.0-1\tMIT\tnotice\thttps://launchpad.net/ubuntu/+source/bar/2.0-1\t%s
""" % ("a" * 64, "b" * 64)


def lp_fake(files, missing=()):
    """A fake opener for Launchpad: getPublishedSources, sourceFileUrls(include_meta) and the files."""
    meta = [{"url": "https://launchpad.net/f/" + n, "size": len(d), "sha256": hashlib.sha256(d).hexdigest()}
            for n, d in files.items()]
    served = dict(files)

    def opener(url):
        if "getPublishedSources" in url:
            src = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["source_name"][0]
            entries = [] if src in missing else [{"self_link": "https://api.launchpad.net/pub/1",
                                                  "distro_series_link": "https://api.launchpad.net/1.0/ubuntu/noble",
                                                  "source_package_version": "1.0-1"}]
            return io.BytesIO(json.dumps({"entries": entries}).encode())
        if "sourceFileUrls" in url:
            return io.BytesIO(json.dumps(meta).encode())
        return io.BytesIO(served[url.rsplit("/", 1)[1]])
    return opener, served


def test_esde_sources_fetch_and_verify():
    t = Tmp()
    try:
        orig = b"upstream tarball\n"
        dsc = ("Format: 3.0 (quilt)\nSource: foo\nChecksums-Sha256:\n %s %d foo_1.0.orig.tar.xz\n"
               % (hashlib.sha256(orig).hexdigest(), len(orig))).encode()
        libs = t.write("libs.tsv", LIBS)
        pairs = E.read_libraries(libs)
        check(pairs == [("foo", "1.0-1")], "only the rows that owe source are fetched (%s)" % pairs)
        opener, served = lp_fake({"foo_1.0-1.dsc": dsc, "foo_1.0.orig.tar.xz": orig})
        r, out = quiet(E.run, pairs, t.p("dry"), opener)
        check(r == 0 and not os.path.exists(t.p("dry")), "dry run lists and writes nothing")
        r, out = quiet(E.run, pairs, t.p("out"), opener, True)
        check(r == 0 and os.path.isfile(t.p("out", "foo_1.0-1", "foo_1.0.orig.tar.xz")), "apply fetches the files")
        check("foo_1.0-1/foo_1.0.orig.tar.xz" in open(t.p("out", "SHA256SUMS")).read(), "SHA256SUMS written")
        served["foo_1.0.orig.tar.xz"] = b"tampered!\n"
        os.remove(t.p("out", "foo_1.0-1", "foo_1.0.orig.tar.xz"))
        r, out = quiet(E.run, pairs, t.p("out"), opener, True)
        check(r == 1 and "FAIL" in out, "BROKEN: a file that does not match Launchpad's sha256 fails")
        opener, _ = lp_fake({}, missing=("foo",))
        r, out = quiet(E.run, pairs, t.p("out2"), opener, True)
        check(r == 1 and "MISSING foo 1.0-1" in out, "BROKEN: a package Launchpad does not publish fails, named")
        opener, _ = lp_fake({"foo_1.0-1.dsc": b"Format: 3.0 (quilt)\nSource: foo\n", "foo_1.0.orig.tar.xz": orig})
        r, out = quiet(E.run, pairs, t.p("out3"), opener, True)
        check(r == 1 and "no Checksums-Sha256" in out, "BROKEN: a .dsc without checksums fails")
        opener, _ = lp_fake({"foo_1.0.orig.tar.xz": orig})
        r, out = quiet(E.run, pairs, t.p("out4"), opener, True)
        check(r == 1 and "no .dsc checksums" in out, "BROKEN: no .dsc at all fails")
        bad = t.write("bad.tsv", LIBS.replace("\tfoo\t1.0-1\t", "\tFoo Bar\t1.0-1\t"))
        check(E.main(["--libraries", bad, "--out", t.p("x")]) == 2, "BROKEN: a malformed row is refused (exit 2)")
    finally:
        t.close()


def test_esde_inventory_is_readable():
    pairs = E.read_libraries(os.path.join(SKILL, "licences", "esde-libraries.tsv"))
    check(len(pairs) >= 50, "the real inventory names the source packages owed (%d)" % len(pairs))


def test_upstream_fetch_never_asks_for_a_login():
    t = Tmp()
    saved = G.run
    seen = []

    def fake_run(cmd, log=None, **kw):
        seen.append((cmd, kw.get("env") or {}))
        return 1
    try:
        G.run = fake_run
        r, _ = quiet(G.fetch_upstream, "x", "https://github.com/x/y", "a" * 40, t.p("up"), t.p("log"))
        cmd, env = seen[0]
        check(isinstance(r, G.Refused) and "credential.helper=" in cmd and env.get("GIT_TERMINAL_PROMPT") == "0",
              "git runs with no credential helper and no terminal prompt")
    finally:
        G.run = saved
        t.close()


def test_submodule_step_only_with_gitmodules():
    """#302: like the builds, the sources fetch runs the submodule step only when the tree has a .gitmodules
    (mupen64plus-nx has a stray gitlink with no url; a recursive init fails on it)."""
    for has_gitmodules in (False, True):
        t = Tmp()
        saved = G.run
        seen = []

        def fake_run(cmd, log=None, **kw):
            seen.append(cmd)
            if "checkout" in cmd:
                dest = cmd[cmd.index("-C") + 1]
                if has_gitmodules:
                    open(os.path.join(dest, ".gitmodules"), "w").close()
            return 0
        try:
            G.run = fake_run
            quiet(G.fetch_upstream, "x", "https://github.com/x/y", "a" * 40, t.p("up"), t.p("log"))
            ran = any("submodule" in c for c in seen)
            check(ran == has_gitmodules, "submodule step %s with%s .gitmodules"
                  % ("runs" if has_gitmodules else "is skipped", "" if has_gitmodules else "out"))
        finally:
            G.run = saved
            t.close()


def test_make_target_and_files():
    mk = open(os.path.join(SKILL, "Makefile")).read()
    check("gh-release:" in mk and "gh_release.py --version" in mk and "$(APPLY_FLAG)" in mk and "--publish" in mk,
          "make gh-release runs gh_release.py, dry run unless APPLY=1, PUBLISH=1 un-drafts")
    src = open(os.path.join(SKILL, "scripts", "release-inspect.sh")).read()
    py = open(os.path.join(SKILL, "scripts", "gh_release.py")).read()
    check("/work:ro" in py and "--cache /cache" in src, "the image folder is mounted read-only, the cache apart")
    for f in ("gh_release.py", "esde_sources.py", "release-inspect.sh"):
        mode = os.stat(os.path.join(SKILL, "scripts", f)).st_mode
        check(mode & stat.S_IXUSR, "%s is executable" % f)
    src = open(os.path.join(SKILL, "scripts", "gh_release.py")).read()
    check("set -x" not in src and "curl" not in src.split('"""', 2)[2], "no set -x, no curl argv for the token")


if __name__ == "__main__":
    main(globals())
