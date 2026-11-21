#!/usr/bin/env python3
"""Tests for build/fetch.py (make build-fetch, #108): stub scp, no cabinet.

Happy path: files land in build/*/out with the pinned sha256. Broken cases: a source whose sha256 differs is
REFUSED (exit 1, nothing left in out/), a failing scp exits 1, and the real defaults file parses into the three
pinned artifacts.
"""
import hashlib, os, pathlib, subprocess, sys, tempfile, unittest

B = pathlib.Path(__file__).resolve().parent.parent
FETCH = B / "fetch.py"
REAL_DEFAULTS = B.parent / "ansible/roles/retroarch122/defaults/main.yml"


def sha(b): return hashlib.sha256(b).hexdigest()


class Fetch(unittest.TestCase):
    def setUp(self):
        self.t = pathlib.Path(tempfile.mkdtemp())
        self.cab = self.t / "cabinet"; self.cab.mkdir()
        self.root = self.t / "repo"; self.root.mkdir()
        self.scp = self.t / "scp"
        # stub scp: `scp HOST:PATH DEST` copies PATH out of the fake cabinet dir; never writes there.
        self.scp.write_text('#!/bin/sh\nsrc=${1#*:}\ncp "$FAKE_CAB$src" "$2"\n')
        self.scp.chmod(0o755)
        self.bin = b"retroarch-bytes"; self.info = b"BUILDINFO text"
        (self.cab / "ra").mkdir()
        (self.cab / "ra/retroarch").write_bytes(self.bin)
        (self.cab / "ra/BUILDINFO").write_bytes(self.info)
        self.defaults = self.t / "defaults.yml"
        self.pin(sha(self.bin), sha(self.info))

    def pin(self, a, b):
        self.defaults.write_text(f'''hk_dir: /x
ra_dir: /ra
ra_root: "{{{{ hk_repo_root }}}}/build"
ra_artifacts:
  - src: "{{{{ ra_root }}}}/retroarch/out/retroarch"
    dest: "{{{{ ra_dir }}}}/retroarch"
    sha256: {a}
    mode: "0755"
  - src: "{{{{ ra_root }}}}/retroarch/out/BUILDINFO"
    dest: "{{{{ ra_dir }}}}/BUILDINFO"
    sha256: {b}
    mode: "0644"
''')
        self.defaults.write_text(self.defaults.read_text().replace("ra_artifacts", "retroarch122_artifacts"))

    def run_fetch(self, *args):
        env = dict(os.environ, BUILD_FETCH_HOST="cap@cabinet.test", BUILD_FETCH_SCP=str(self.scp), FAKE_CAB=str(self.cab),
                   BUILD_FETCH_DEFAULTS=str(self.defaults), BUILD_FETCH_ROOT=str(self.root))
        return subprocess.run([sys.executable, "-I", str(FETCH), *args], env=env, capture_output=True, text=True)

    def test_fetches_and_verifies(self):
        r = self.run_fetch()
        self.assertEqual(r.returncode, 0, r.stderr)
        out = self.root / "build/retroarch/out"
        self.assertEqual((out / "retroarch").read_bytes(), self.bin)
        self.assertTrue(os.access(out / "retroarch", os.X_OK))
        self.assertIn("sha256 OK", r.stdout)
        # second run: already OK, no fetch needed
        self.scp.write_text("#!/bin/sh\nexit 9\n")
        r = self.run_fetch()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("already OK", r.stdout)

    def test_mismatch_refused(self):
        (self.cab / "ra/retroarch").write_bytes(b"tampered")
        r = self.run_fetch()
        self.assertEqual(r.returncode, 1)
        self.assertIn("REFUSED", r.stderr)
        self.assertFalse((self.root / "build/retroarch/out/retroarch").exists())
        self.assertIn("RESULT: FAIL", r.stdout)

    def test_fetch_failure_exits_1(self):
        (self.cab / "ra/BUILDINFO").unlink()
        r = self.run_fetch()
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAILED to fetch", r.stderr)

    def test_dry_run_writes_nothing(self):
        r = self.run_fetch("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would fetch", r.stdout)
        self.assertFalse((self.root / "build").exists())

    def test_real_defaults_parse(self):
        env = dict(os.environ, BUILD_FETCH_HOST="cap@cabinet.test", BUILD_FETCH_DEFAULTS=str(REAL_DEFAULTS))
        r = subprocess.run([sys.executable, "-I", str(FETCH), "--dry-run"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        for want in ("retroarch/out/retroarch", "retroarch/out/BUILDINFO", "n64/out/mupen64plus_next_libretro.so",
                     "/data/emulators/retroarch-1.22/retroarch", "32041d76bcb8"):
            self.assertIn(want, r.stdout)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests", "lib"))
    from hktest import unittest_main  # PASS/FAIL/SKIP per test, counted RESULT line (#137)
    unittest_main()
