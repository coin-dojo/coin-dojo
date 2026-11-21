#!/usr/bin/env python3
"""Static tests for build/box64/build.sh (#207; never runs the real build). Broken cases: a pin that is not a
commit / digest / job count exits 2 before docker is touched, no docker exits 3, bad usage exits 2."""
import os, pathlib, re, shutil, subprocess, tempfile, unittest

D = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = D / "build.sh"


def run_copy(edit=None, env=None):
    """Copy build.sh + lib.sh, optionally rewrite the script text, run it with a PATH that has no docker."""
    with tempfile.TemporaryDirectory() as t:
        w = pathlib.Path(t) / "build" / "box64"
        w.mkdir(parents=True)
        shutil.copy(D.parent / "lib.sh", w.parent / "lib.sh")
        s = SCRIPT.read_text()
        (w / "build.sh").write_text(edit(s) if edit else s)
        b = pathlib.Path(t) / "bin"
        b.mkdir()
        for tool in ("bash", "dirname", "cat", "basename", "shasum", "sha256sum", "cut", "awk"):
            src = shutil.which(tool)
            if src:
                os.symlink(src, b / tool)
        e = {"PATH": str(b), "HOME": t}
        e.update(env or {})
        return subprocess.run([str(b / "bash"), str(w / "build.sh"), "--out", str(pathlib.Path(t) / "out")],
                              capture_output=True, text=True, env=e)


class T(unittest.TestCase):
    def test_pins_present(self):
        s = SCRIPT.read_text()
        self.assertIn('BOX64_TAG="v0.4.4"', s)
        self.assertIn('BOX64_COMMIT="2f130fab1d6e1a4ee8a71dc60cfdfcc839ad192a"', s)
        self.assertIn('CMAKE_FLAGS="-DRPI5ARM64=ON -DCMAKE_BUILD_TYPE=RelWithDebInfo"', s)
        self.assertRegex(s, r'BASE_IMAGE="debian:trixie@sha256:[0-9a-f]{64}"')
        self.assertIn('BOX64_JOBS="${BOX64_JOBS:-2}"', s)
        self.assertIn("linux/arm64", s)

    def test_source_checked_against_commit(self):
        s = SCRIPT.read_text()
        self.assertIn('[ "$GOT" = "$BOX64_COMMIT" ]', s)
        self.assertIn("--branch \"$BOX64_TAG\"", s)

    def test_outputs_and_buildinfo_keys(self):
        s = SCRIPT.read_text()
        for f in ("/out/box64", "/out/box64.box64rc", "/out/x64lib", "x64lib.sha256"):
            self.assertIn(f, s)
        block = s.split('echo "tag: $BOX64_TAG"')[1].split("} > /out/BUILDINFO")[0]
        keys = re.findall(r'echo "(\w+):|(bi_\w+)', 'echo "tag:' + block)
        flat = [a or b for a, b in keys]
        self.assertEqual(flat, ["tag", "commit", "flags", "make_jobs", "bi_date", "arch", "base_image", "image_id",
                                "gcc", "cmake", "version", "bi_packages", "bi_file", "bi_sha256",
                                "box64rc_sha256", "x64lib_files"])

    def test_syntax(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_out_gitignored(self):
        r = subprocess.run(["git", "-C", str(D), "check-ignore", "-q", str(D / "out" / "box64")])
        self.assertEqual(r.returncode, 0, "build/box64/out must be gitignored")

    def test_help(self):
        r = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Usage", r.stdout)
        self.assertIn("v0.4.4", r.stdout)

    def test_unknown_option_exits_2(self):
        r = subprocess.run(["bash", str(SCRIPT), "--bogus"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown option", r.stderr)

    def test_out_without_value_exits_2(self):
        r = subprocess.run(["bash", str(SCRIPT), "--out"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_lib_missing_fails(self):
        with tempfile.TemporaryDirectory() as t:
            d = pathlib.Path(t) / "build" / "box64"
            d.mkdir(parents=True)
            shutil.copy(SCRIPT, d / "build.sh")
            r = subprocess.run(["bash", str(d / "build.sh"), "--help"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)

    def test_missing_docker_exits_3(self):
        r = run_copy()
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("docker not found", r.stderr)

    def test_short_commit_exits_2(self):
        r = run_copy(lambda s: s.replace("2f130fab1d6e1a4ee8a71dc60cfdfcc839ad192a", "2f130fa"))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("not a full commit sha", r.stderr)

    def test_floating_base_image_exits_2(self):
        r = run_copy(lambda s: re.sub(r'(BASE_IMAGE="debian:trixie)@sha256:[0-9a-f]{64}"', r'\1"', s))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("not pinned by digest", r.stderr)

    def test_bad_jobs_exits_2(self):
        for bad in ("0", "x", "-1", "100"):
            r = run_copy(env={"BOX64_JOBS": bad})
            self.assertEqual(r.returncode, 2, f"{bad!r}: {r.stderr}")
            self.assertIn("not a job count", r.stderr)

    def test_bad_tag_exits_2(self):
        r = run_copy(lambda s: s.replace('BOX64_TAG="v0.4.4"', 'BOX64_TAG="main"'))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("not a pinned release tag", r.stderr)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "lib"))
    from hktest import unittest_main  # PASS/FAIL/SKIP per test, counted RESULT line (#137)
    unittest_main()
