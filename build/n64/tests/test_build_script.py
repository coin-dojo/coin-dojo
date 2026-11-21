#!/usr/bin/env python3
"""Static tests for build/n64/build.sh (never runs the real build or docker).

Broken cases run a COPY of build.sh with one pin broken (branch instead of commit, short sha, image
without digest, FORCE_GLES3 dropped) and assert exit 2 before docker is touched. The gitignore broken
case builds a throwaway repo whose .gitignore lacks out/ and asserts the ignore check reports it.
"""
import os, pathlib, shutil, subprocess, tempfile, unittest

D = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = D / "build.sh"
PIN_COMMIT = 'MUPEN_COMMIT="15bada85906593376e8fd697f1b0b3af3a20f512"'
PIN_IMAGE = 'BASE_IMAGE="debian:trixie@sha256:9c6e381421d0f48d1b8133e573f33c185ee16cbf505eda9c4a8a4791b25ea8a6"'
PIN_FLAGS = 'MAKE_FLAGS="platform=rpi5_64_mesa FORCE_GLES3=1"'   # = the live core's BUILDINFO
OUTPUTS = ["out/mupen64plus_next_libretro.so", "out/mupen64plus_next_libretro.so.sha256", "out/BUILDINFO", "work/x"]
TOOLS = ("bash", "dirname", "cat", "basename")


def run_copy(old=None, new=None, env_extra=None):
    with tempfile.TemporaryDirectory() as t:
        b = pathlib.Path(t) / "bin"
        b.mkdir()
        for tool in TOOLS:
            os.symlink(shutil.which(tool), b / tool)
        s = SCRIPT.read_text()
        if old:
            assert old in s, old
            s = s.replace(old, new)
        d = pathlib.Path(t) / "build"
        (d / "n64").mkdir(parents=True)
        shutil.copy(D.parent / "lib.sh", d / "lib.sh")   # build.sh sources ../lib.sh
        p = d / "n64" / "build.sh"
        p.write_text(s)
        return subprocess.run([str(b / "bash"), str(p), "--out", str(pathlib.Path(t) / "out")],
                              capture_output=True, text=True, env={"PATH": str(b), "HOME": t, **(env_extra or {})})


def unignored(repo, paths):
    return [p for p in paths if subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", p]).returncode != 0]


class T(unittest.TestCase):
    def test_pins_present(self):
        s = SCRIPT.read_text()
        for pin in (PIN_COMMIT, PIN_IMAGE, PIN_FLAGS):
            self.assertIn(pin, s)
        self.assertIn('MUPEN_REPO="https://github.com/libretro/mupen64plus-libretro-nx.git"', s)
        self.assertNotIn("platform=unix", s)   # #61: the live core is rpi5_64_mesa, not unix
        self.assertNotIn("strip", s.split("INNER_EOF")[1])   # the live core is unstripped

    def test_syntax(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
        subprocess.run(["bash", "-n", str(D.parent / "lib.sh")], check=True)

    def test_outputs_gitignored(self):
        self.assertEqual(unignored(D, OUTPUTS), [], "build outputs must be gitignored")

    def test_help(self):
        r = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("rpi5_64_mesa", r.stdout)

    def test_unknown_option_fails(self):
        r = subprocess.run(["bash", str(SCRIPT), "--bogus"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_out_without_value_fails(self):
        r = subprocess.run(["bash", str(SCRIPT), "--out"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_clean_copy_passes_pins(self):
        r = run_copy()
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("docker not found", r.stderr)

    # ---- broken cases ---------------------------------------------------------
    def test_branch_instead_of_commit_fails(self):
        r = run_copy(PIN_COMMIT, 'MUPEN_COMMIT="develop"')
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("not a full commit sha", r.stderr)

    def test_short_commit_fails(self):
        r = run_copy(PIN_COMMIT, 'MUPEN_COMMIT="15bada85"')
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_image_without_digest_fails(self):
        r = run_copy(PIN_IMAGE, 'BASE_IMAGE="debian:trixie"')
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("not pinned by digest", r.stderr)

    def test_desktop_gl_flags_fail(self):
        r = run_copy(PIN_FLAGS, 'MAKE_FLAGS="platform=unix"')
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("FORCE_GLES3=1", r.stderr)

    def test_unknown_arch_fails(self):
        r = run_copy(env_extra={"ARCH": "x86_64"})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("only arm64", r.stderr)

    def test_unpinned_epoch_fails(self):
        r = run_copy('BUILD_EPOCH="1794657600"', 'BUILD_EPOCH=""')
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
