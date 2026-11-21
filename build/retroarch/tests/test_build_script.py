#!/usr/bin/env python3
"""Static tests for build/retroarch/build.sh (never runs the real build or docker).

Broken cases run a COPY of build.sh (+ patches/) in a temp dir with one thing broken and assert the
script refuses it with exit 2 before docker is touched: a patch checksum mismatch, an unpinned patch,
a missing patch, an unpinned tag/commit/image. The gitignore broken case builds a throwaway repo whose
.gitignore lacks out/ and asserts the ignore check reports the outputs.
"""
import hashlib, os, pathlib, re, shutil, subprocess, tempfile, unittest

D = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = D / "build.sh"
PIN_TAG = 'RA_TAG="v1.22.2"'
PIN_COMMIT = 'RA_COMMIT="69a4f0ea1e8aaf442ae4858f2e7f2b31a1776576"'
PIN_IMAGE = 'BASE_IMAGE="debian:trixie@sha256:9c6e381421d0f48d1b8133e573f33c185ee16cbf505eda9c4a8a4791b25ea8a6"'
PATCHES = ["0001-egl-loop-only-matched-configs.patch", "0002-wayland-gles2-request-on-gles3-build.patch"]
OUTPUTS = ["out/retroarch", "out/retroarch.sha256", "out/BUILDINFO", "work/x"]
# PATH with only what build.sh needs before its docker check: docker is absent, so a script that got
# past its pin checks exits 3, one that refused a pin exits 2.
TOOLS = ("bash", "dirname", "cat", "basename", "cut", "shasum", "sha256sum")


def nodocker_path(t):
    b = pathlib.Path(t) / "bin"
    b.mkdir()
    for tool in TOOLS:
        src = shutil.which(tool)
        if src:
            os.symlink(src, b / tool)
    return str(b)


def run_copy(edit_script=None, edit_dir=None, env_extra=None):
    """Copy build.sh + patches/ to a temp dir, apply the edits, run it with docker absent."""
    with tempfile.TemporaryDirectory() as t:
        w = pathlib.Path(t) / "build" / "retroarch"
        shutil.copytree(D / "patches", w / "patches")
        shutil.copy(D.parent / "lib.sh", w.parent / "lib.sh")   # build.sh sources ../lib.sh
        s = SCRIPT.read_text()
        if edit_script:
            old, new = edit_script
            assert old in s, old
            s = s.replace(old, new)
        (w / "build.sh").write_text(s)
        if edit_dir:
            edit_dir(w)
        env = {"PATH": nodocker_path(t), "HOME": t, **(env_extra or {})}
        return subprocess.run([shutil.which("bash"), str(w / "build.sh"), "--out", str(w / "out")],
                              capture_output=True, text=True, env=env)


def unignored(repo, paths):
    """Paths (relative to repo) that git does NOT ignore. Empty = all ignored."""
    return [p for p in paths if subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", p]).returncode != 0]


class T(unittest.TestCase):
    def test_pins_present(self):
        s = SCRIPT.read_text()
        for pin in (PIN_TAG, PIN_COMMIT, PIN_IMAGE):
            self.assertIn(pin, s)
        self.assertIn('RA_REPO="https://github.com/libretro/RetroArch.git"', s)
        for flag in ("--disable-x11", "--disable-vulkan", "--enable-wayland", "--enable-opengles3", "--enable-udev"):
            self.assertIn(flag, s)
        self.assertIn('BUILD_EPOCH="1794657600"', s)   # live build day, keeps __DATE__ identical
        self.assertIn('SOURCE_DATE_EPOCH="$BUILD_EPOCH"', s)

    def test_patch_pins_match_files(self):
        s = SCRIPT.read_text()
        pinned = dict(re.findall(r"^(?:RA_PATCHES=\")?([0-9A-Za-z._-]+\.patch)=([0-9a-f]{64})", s, re.M))
        self.assertEqual(sorted(pinned), PATCHES)
        self.assertEqual(sorted(p.name for p in (D / "patches").glob("*.patch")), PATCHES)
        for name, want in pinned.items():
            got = hashlib.sha256((D / "patches" / name).read_bytes()).hexdigest()
            self.assertEqual(got, want, name)

    def test_syntax(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_outputs_gitignored(self):
        self.assertEqual(unignored(D, OUTPUTS), [], "build outputs must be gitignored")

    def test_outputs_not_tracked(self):
        out = subprocess.run(["git", "-C", str(D), "ls-files", "out", "work"], capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), "")

    def test_help(self):
        r = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Usage", r.stdout)

    def test_unknown_option_fails(self):
        r = subprocess.run(["bash", str(SCRIPT), "--bogus"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_out_without_value_fails(self):
        r = subprocess.run(["bash", str(SCRIPT), "--out"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_clean_copy_passes_pins(self):
        """Control: the unmodified copy gets past every pin check and stops only at 'docker not found'."""
        r = run_copy()
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("docker not found", r.stderr)

    # ---- broken cases ---------------------------------------------------------
    def test_patch_checksum_mismatch_fails(self):
        def tamper(w):
            p = w / "patches" / PATCHES[1]
            p.write_text(p.read_text() + "\n")
        r = run_copy(edit_dir=tamper)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("patch checksum mismatch", r.stderr)

    def test_unpinned_patch_fails(self):
        r = run_copy(edit_dir=lambda w: (w / "patches" / "0003-extra.patch").write_text("--- a\n+++ b\n"))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("unpinned patch", r.stderr)

    def test_missing_patch_fails(self):
        r = run_copy(edit_dir=lambda w: (w / "patches" / PATCHES[0]).unlink())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("pinned patch missing", r.stderr)

    def test_branch_instead_of_tag_fails(self):
        r = run_copy(edit_script=(PIN_TAG, 'RA_TAG="master"'))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("not a pinned release tag", r.stderr)

    def test_short_commit_fails(self):
        r = run_copy(edit_script=(PIN_COMMIT, 'RA_COMMIT="69a4f0e"'))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("not a full commit sha", r.stderr)

    def test_image_without_digest_fails(self):
        r = run_copy(edit_script=(PIN_IMAGE, 'BASE_IMAGE="debian:trixie"'))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("not pinned by digest", r.stderr)

    def test_wallclock_epoch_fails(self):
        r = run_copy(edit_script=('BUILD_EPOCH="1794657600"', 'BUILD_EPOCH="now"'))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("not a pinned unix time", r.stderr)

    def test_outputs_not_ignored_detected(self):
        with tempfile.TemporaryDirectory() as t:
            subprocess.run(["git", "init", "-q", t], check=True)
            (pathlib.Path(t) / ".gitignore").write_text("*.log\n")
            self.assertEqual(unignored(t, OUTPUTS), OUTPUTS)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "lib"))
    from hktest import unittest_main  # PASS/FAIL/SKIP per test, counted RESULT line (#137)
    unittest_main()
