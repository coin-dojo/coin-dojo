#!/usr/bin/env python3
"""Static tests for build/flycast/build.sh (never runs the real build)."""
import hashlib, os, pathlib, re, shutil, subprocess, tempfile, unittest

D = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = D / "build.sh"
PATCHES = ["0001-gui-inside-4-3-area.patch", "0002-fps-top-right.patch"]
# Each coin-dojo patch is inert unless its env var is 1, so the build behaves like upstream by default.
PATCH_ENV = {PATCHES[0]: "FLYCAST_GUI_43", PATCHES[1]: "FLYCAST_FPS_TOPRIGHT"}


def run_copy(edit_dir=None):
    """Copy build.sh + lib.sh + patches/ to a temp dir, break one thing, run it with an empty PATH dir
    (no docker). The patch check runs before the docker check, so a bad patch must exit 2."""
    with tempfile.TemporaryDirectory() as t:
        w = pathlib.Path(t) / "build" / "flycast"
        shutil.copytree(D / "patches", w / "patches")
        shutil.copy(D.parent / "lib.sh", w.parent / "lib.sh")
        shutil.copy(SCRIPT, w / "build.sh")
        if edit_dir:
            edit_dir(w)
        b = pathlib.Path(t) / "bin"
        b.mkdir()
        for tool in ("bash", "dirname", "cat", "basename", "shasum", "sha256sum", "cut", "awk"):
            src = shutil.which(tool)
            if src:
                os.symlink(src, b / tool)
        return subprocess.run([str(b / "bash"), str(w / "build.sh"), "--out", str(pathlib.Path(t) / "out")],
                              capture_output=True, text=True, env={"PATH": str(b), "HOME": t})


class T(unittest.TestCase):
    def test_pins_present(self):
        s = SCRIPT.read_text()
        self.assertIn('FLYCAST_TAG="v2.7"', s)
        self.assertIn('BASE_IMAGE="debian:trixie"', s)
        for f in ("-DUSE_GLES=ON", "-DLIBRETRO=OFF", "-DCMAKE_BUILD_TYPE=Release"):
            self.assertIn(f, s)
        self.assertIn("--recursive --shallow-submodules", s)
        self.assertIn("linux/arm64", s)

    def test_syntax(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_out_gitignored(self):
        out = D / "out" / "flycast"
        r = subprocess.run(["git", "-C", str(D), "check-ignore", "-q", str(out)])
        self.assertEqual(r.returncode, 0, "out/flycast must be gitignored")

    def test_help(self):
        r = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Usage", r.stdout)

    def test_unknown_option_fails(self):
        r = subprocess.run(["bash", str(SCRIPT), "--bogus"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown option", r.stderr)

    def test_out_without_value_fails(self):
        r = subprocess.run(["bash", str(SCRIPT), "--out"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_lib_missing_fails(self):
        # Broken case: build.sh without its lib.sh must fail loudly, not run a half script.
        with tempfile.TemporaryDirectory() as t:
            d = pathlib.Path(t) / "build" / "flycast"
            d.mkdir(parents=True)
            shutil.copy(SCRIPT, d / "build.sh")
            r = subprocess.run(["bash", str(d / "build.sh"), "--help"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)

    def test_patch_pins_match_files(self):
        s = SCRIPT.read_text()
        pinned = dict(re.findall(r"^(?:FC_PATCHES=\")?([0-9A-Za-z._-]+\.patch)=([0-9a-f]{64})", s, re.M))
        self.assertEqual(sorted(pinned), PATCHES)
        self.assertEqual(sorted(p.name for p in (D / "patches").glob("*.patch")), PATCHES)
        for name, want in pinned.items():
            self.assertEqual(hashlib.sha256((D / "patches" / name).read_bytes()).hexdigest(), want, name)
        self.assertIn('build_check_patches "$HERE/patches" "$FC_PATCHES"', s)
        self.assertIn('-v "$HERE/patches:/patches:ro"', s)
        self.assertIn('git -C /src apply "/patches/$name"', s)

    def test_patches_gated_by_env(self):
        for name, var in PATCH_ENV.items():
            added = "\n".join(l for l in (D / "patches" / name).read_text().splitlines() if l.startswith("+"))
            self.assertIn(f'std::getenv("{var}")', added, name)
            self.assertIn("v[0] == '1'", added, name)

    def test_fps_stays_beside_notifications(self):
        # #199: an RA leaderboard/challenge tracker never ends; the top-right counter must not wait for it.
        added = [l[1:].strip() for l in (D / "patches" / PATCHES[1]).read_text().splitlines() if l.startswith("+")]
        self.assertIn("if (!notified || topRight)", added)
        removed = [l[1:].strip() for l in (D / "patches" / PATCHES[1]).read_text().splitlines()
                   if l.startswith("-") and not l.startswith("---")]
        self.assertIn("if (!toast.draw())", removed, "the FPS block must no longer hang off toast.draw() alone")

    def test_patches_touch_only_gui(self):
        for name in PATCHES:
            files = re.findall(r"^\+\+\+ b/(\S+)", (D / "patches" / name).read_text(), re.M)
            self.assertEqual(files, ["core/ui/gui.cpp"], name)

    def test_missing_docker_exits_3(self):
        # Good patches, PATH without docker: passes the patch check, then stops at docker with 3.
        r = run_copy()
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("docker not found", r.stderr)

    def test_patch_checksum_mismatch_fails(self):
        r = run_copy(lambda w: (w / "patches" / PATCHES[1]).write_text("tampered\n"))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("patch checksum mismatch", r.stderr)

    def test_unpinned_patch_fails(self):
        r = run_copy(lambda w: (w / "patches" / "0003-extra.patch").write_text("--- a\n+++ b\n"))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("unpinned patch", r.stderr)

    def test_missing_patch_fails(self):
        r = run_copy(lambda w: (w / "patches" / PATCHES[0]).unlink())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("pinned patch missing", r.stderr)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "lib"))
    from hktest import unittest_main  # PASS/FAIL/SKIP per test, counted RESULT line (#137)
    unittest_main()
