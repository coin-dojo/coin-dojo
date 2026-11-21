#!/usr/bin/env python3
"""Static tests for build/stella/build.sh (never runs the real build)."""
import os, pathlib, shutil, subprocess, tempfile, unittest

D = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = D / "build.sh"
PIN_TAG = 'STELLA_TAG="7.0"'
PIN_COMMIT = 'STELLA_COMMIT="d55b1aec0d067a4c901a6dcdf81cb8f579685659"'


def copy_script(t, text):
    """Lay out t/build/stella/build.sh next to t/build/lib.sh (build.sh sources ../lib.sh)."""
    d = pathlib.Path(t) / "build"
    (d / "stella").mkdir(parents=True)
    shutil.copy(D.parent / "lib.sh", d / "lib.sh")
    p = d / "stella" / "build.sh"
    p.write_text(text)
    return p


def run_patched(old, new, env=None):
    """Run a copy of build.sh with one pin line replaced (the real script is untouched)."""
    s = SCRIPT.read_text()
    assert old in s, old
    with tempfile.TemporaryDirectory() as t:
        p = copy_script(t, s.replace(old, new))
        return subprocess.run(["bash", str(p), "--out", str(pathlib.Path(t) / "out")],
                              capture_output=True, text=True, env=env)


class T(unittest.TestCase):
    def test_pins_present(self):
        s = SCRIPT.read_text()
        self.assertIn(PIN_TAG, s)
        self.assertIn(PIN_COMMIT, s)
        self.assertIn('STELLA_REPO="https://github.com/stella-emu/stella.git"', s)
        self.assertIn('BASE_IMAGE="debian:trixie"', s)
        self.assertIn('MAKE_FLAGS="platform=unix"', s)
        self.assertIn("linux/arm64", s)
        self.assertIn("make -C /src/src/os/libretro", s)
        self.assertIn("strip --strip-unneeded", s)
        self.assertIn("bi_check_so", s)   # symbol checks live in build/lib.sh
        for sym in ("retro_init", "retro_load_game"):
            self.assertIn(sym, (D.parent / "lib.sh").read_text())

    def test_syntax(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_out_gitignored(self):
        for f in ("stella_libretro.so", "stella_libretro.so.sha256", "BUILDINFO"):
            r = subprocess.run(["git", "-C", str(D), "check-ignore", "-q", str(D / "out" / f)])
            self.assertEqual(r.returncode, 0, f"out/{f} must be gitignored")

    def test_help(self):
        r = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Usage", r.stdout)
        self.assertIn("stella_libretro.so", r.stdout)

    def test_unknown_option_fails(self):
        r = subprocess.run(["bash", str(SCRIPT), "--bogus"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown option", r.stderr)

    def test_out_without_value_fails(self):
        r = subprocess.run(["bash", str(SCRIPT), "--out"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    # ---- broken cases: a bad pin is refused before docker is touched ----
    def test_branch_instead_of_tag_refused(self):
        r = run_patched(PIN_TAG, 'STELLA_TAG="master"')
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("not a pinned release tag", r.stderr)

    def test_empty_tag_refused(self):
        r = run_patched(PIN_TAG, 'STELLA_TAG=""')
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_short_commit_refused(self):
        r = run_patched(PIN_COMMIT, 'STELLA_COMMIT="d55b1aec"')
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("not a full commit sha", r.stderr)

    def test_missing_docker_exits_3(self):
        # PATH holds only the tools build.sh needs before its docker check, so docker is absent.
        with tempfile.TemporaryDirectory() as t:
            for tool in ("bash", "dirname", "cat", "basename"):
                src = shutil.which(tool)
                self.assertIsNotNone(src, tool)
                os.symlink(src, os.path.join(t, tool))
            env = {"PATH": t, "HOME": os.environ.get("HOME", "/tmp")}
            p = copy_script(t, SCRIPT.read_text())
            r = subprocess.run([os.path.join(t, "bash"), str(p), "--out", os.path.join(t, "out")],
                               capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("docker not found", r.stderr)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "lib"))
    from hktest import unittest_main  # PASS/FAIL/SKIP per test, counted RESULT line (#137)
    unittest_main()
