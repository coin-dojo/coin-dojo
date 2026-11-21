#!/usr/bin/env python3
"""Tests for build/lib.sh, the skeleton shared by build/{flycast,n64,retroarch,stella}/build.sh (#112).

1. BUILDINFO keys: the `{ ... } > /out/BUILDINFO` block of each build.sh is RUN here with stub tools
   (file, gcc, ldd, dpkg-query, objdump) and /out redirected to a temp dir; the keys it writes, in
   order, must equal the list the scripts wrote before lib.sh existed. No docker, no build.
2. Exit codes per script (broken cases): bad usage = 2, bad pin = 2, docker missing = 3, and a build.sh
   whose lib.sh is gone fails instead of running half a script.
"""
import os, pathlib, re, shutil, subprocess, tempfile, unittest

B = pathlib.Path(__file__).resolve().parent.parent
LIB = B / "lib.sh"

# The keys (and their order) every BUILDINFO had at the commit before lib.sh (d6f5cda).
KEYS = {
    "flycast": ["tag", "commit", "date", "base_image", "flags", "patches", "file", "ldd"],
    "stella": ["tag", "commit", "date", "base_image", "image_digest", "gcc", "flags", "file",
               "glibc_max", "glibcxx_max", "ldd"],
    "n64": ["commit", "date", "source_date_epoch", "arch", "base_image", "image_id", "flags", "gcc",
            "packages", "file", "sha256", "ldd"],
    "retroarch": ["tag", "commit", "patches", "flags", "strip", "source_date_epoch", "date", "arch",
                  "base_image", "image_id", "gcc", "packages", "file", "sha256"],
}
ARTIFACT = {"flycast": "flycast", "stella": "stella_libretro.so", "n64": "mupen64plus_next_libretro.so",
            "retroarch": "retroarch"}
STUBS = {
    "file": 'echo "ELF 64-bit LSB, stub"',
    "gcc": 'echo 14.2.0',
    "ldd": 'echo "	libc.so.6 => /lib/libc.so.6 (0x1)"',
    "dpkg-query": 'echo "  libfoo 1.0"',
    "objdump": 'echo "GLIBC_2.38 GLIBCXX_3.4.33"',
}


def block(name, text=None):
    s = text if text is not None else (B / name / "build.sh").read_text()
    inner = s.split("<<'INNER_EOF'\n")[1].split("\nINNER_EOF")[0]
    m = re.search(r"^\{\n.*?^\} > /out/BUILDINFO$", inner, re.S | re.M)
    assert m, name
    return m.group(0)


def lib_inner():
    return LIB.read_text().split("<<'INNER_LIB_EOF'\n")[1].split("\nINNER_LIB_EOF")[0]


def buildinfo_keys(name, text=None):
    with tempfile.TemporaryDirectory() as t:
        t = pathlib.Path(t)
        (t / "bin").mkdir()
        for tool, body in STUBS.items():
            p = t / "bin" / tool
            p.write_text("#!/bin/sh\n" + body + "\n")
            p.chmod(0o755)
        out = t / "out"
        out.mkdir()
        (out / f"{ARTIFACT[name]}.sha256").write_text("abc123  x\n")
        (out / ARTIFACT[name]).write_text("x")
        script = (
            "set -euo pipefail\n" + lib_inner().replace("/out", str(out)) + "\n"
            + 'TAG=1 FLYCAST_TAG=1 STELLA_TAG=1 RA_TAG=1 GOT=c MUPEN_COMMIT=c STELLA_COMMIT=c RA_COMMIT=c\n'
            + 'BASE_IMAGE=i IMAGE_ID=i IMAGE_DIGEST=i PLATFORM=p CMAKE_FLAGS=f MAKE_FLAGS=f CONFIGURE_FLAGS=f\n'
            + "RA_STRIP=1 SOURCE_DATE_EPOCH=1 BUILD_EPOCH=1 DEPS='a b'\n"
            + "PATCH_INFO='  0001.patch sum\n'\n"
            + block(name, text).replace("/out", str(out)) + "\n")
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                           env={"PATH": f"{t / 'bin'}:/usr/bin:/bin", "HOME": str(t)})
        assert r.returncode == 0, r.stderr
        info = (out / "BUILDINFO").read_text()
    return re.findall(r"^([a-z_0-9]+):", info, re.M)


def make_tree(t, name, mutate=None, with_lib=True):
    d = pathlib.Path(t) / "build"
    shutil.copytree(B / name, d / name, ignore=shutil.ignore_patterns("out", "work", "tests", "__pycache__"))
    if with_lib:
        shutil.copy(LIB, d / "lib.sh")
    p = d / name / "build.sh"
    if mutate:
        s = p.read_text()
        assert mutate[0] in s, mutate[0]
        p.write_text(s.replace(*mutate))
    return p


def run(p, *args, env=None, path=None):
    return subprocess.run([shutil.which("bash"), str(p), *args], capture_output=True, text=True,
                          env=env if env is not None else {"PATH": path or "/usr/bin:/bin", "HOME": str(p.parent)})


def nodocker_bin(t):
    b = pathlib.Path(t) / "nodocker"
    b.mkdir()
    for tool in ("bash", "dirname", "basename", "cat", "cut", "shasum", "sha256sum"):
        src = shutil.which(tool)
        if src:
            os.symlink(src, b / tool)
    return str(b)


class BuildInfoKeys(unittest.TestCase):
    def test_keys_unchanged(self):
        for name, want in KEYS.items():
            with self.subTest(name):
                self.assertEqual(buildinfo_keys(name), want)

    def test_dropped_key_is_detected(self):
        # Broken case: the check can fail. Removing the gcc line from n64 changes the keys.
        s = (B / "n64/build.sh").read_text()
        self.assertIn('  echo "gcc: $(gcc -dumpfullversion)"\n', s)
        got = buildinfo_keys("n64", s.replace('  echo "gcc: $(gcc -dumpfullversion)"\n', ""))
        self.assertNotEqual(got, KEYS["n64"])


class ExitCodes(unittest.TestCase):
    NAMES = ("flycast", "n64", "retroarch", "stella")
    # pin breakage per script: (old text, new text, expected stderr)
    BAD_PINS = {
        "stella": [('STELLA_TAG="7.0"', 'STELLA_TAG="master"', "not a pinned release tag"),
                   ('STELLA_COMMIT="d55b1aec0d067a4c901a6dcdf81cb8f579685659"', 'STELLA_COMMIT="d55b"', "not a full commit sha")],
        "n64": [('MUPEN_COMMIT="15bada85906593376e8fd697f1b0b3af3a20f512"', 'MUPEN_COMMIT="develop"', "not a full commit sha"),
                ('BUILD_EPOCH="1794657600"', 'BUILD_EPOCH="now"', "not a pinned unix time")],
        "retroarch": [('RA_TAG="v1.22.2"', 'RA_TAG="master"', "not a pinned release tag"),
                      ('RA_STRIP="${RA_STRIP:-1}"', 'RA_STRIP=7', "RA_STRIP must be 0 or 1")],
    }

    def test_bad_usage_exits_2(self):
        for name in self.NAMES:
            for args in (["--bogus"], ["--out"]):
                with self.subTest(name=name, args=args), tempfile.TemporaryDirectory() as t:
                    r = run(make_tree(t, name), *args)
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertIn("error:", r.stderr)

    def test_help_exits_0(self):
        for name in self.NAMES:
            with self.subTest(name), tempfile.TemporaryDirectory() as t:
                r = run(make_tree(t, name), "--help")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("Usage", r.stdout)

    def test_bad_pin_exits_2(self):
        for name, cases in self.BAD_PINS.items():
            for old, new, msg in cases:
                with self.subTest(name=name, pin=new), tempfile.TemporaryDirectory() as t:
                    r = run(make_tree(t, name, (old, new)), "--out", f"{t}/out", path=nodocker_bin(t))
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertIn(msg, r.stderr)

    def test_unknown_arch_exits_2(self):
        for name in ("n64", "retroarch"):
            with self.subTest(name), tempfile.TemporaryDirectory() as t:
                p = make_tree(t, name)
                r = run(p, "--out", f"{t}/out", env={"PATH": nodocker_bin(t), "HOME": t, "ARCH": "x86_64"})
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("only arm64", r.stderr)

    def test_good_pins_stop_at_missing_docker_exit_3(self):
        for name in self.NAMES:
            with self.subTest(name), tempfile.TemporaryDirectory() as t:
                r = run(make_tree(t, name), "--out", f"{t}/out", path=nodocker_bin(t))
                self.assertEqual(r.returncode, 3, r.stderr)
                self.assertIn("docker not found", r.stderr)

    def test_out_outside_home_exits_2(self):
        # n64/retroarch mount --out into colima, which only shares $HOME. Needs a docker on PATH, so stub it.
        for name in ("n64", "retroarch"):
            with self.subTest(name), tempfile.TemporaryDirectory() as t:
                p = make_tree(t, name)
                b = pathlib.Path(nodocker_bin(t))
                (b / "docker").write_text('#!/bin/sh\n[ "$1" = info ] && { echo arm64; exit 0; }\nexit 0\n')
                (b / "docker").chmod(0o755)
                for tool in ("mkdir", "rm"):
                    os.symlink(shutil.which(tool), b / tool)
                other = pathlib.Path(t) / "elsewhere"
                home = pathlib.Path(t) / "home"
                home.mkdir()
                r = run(p, "--out", str(other), env={"PATH": str(b), "HOME": str(home)})
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("must be under $HOME", r.stderr)

    def test_missing_lib_fails(self):
        for name in self.NAMES:
            with self.subTest(name), tempfile.TemporaryDirectory() as t:
                r = run(make_tree(t, name, with_lib=False), "--help")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("lib.sh", r.stderr)

    def test_lib_syntax(self):
        subprocess.run(["bash", "-n", str(LIB)], check=True)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests", "lib"))
    from hktest import unittest_main  # PASS/FAIL/SKIP per test, counted RESULT line (#137)
    unittest_main()
