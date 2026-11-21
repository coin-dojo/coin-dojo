#!/usr/bin/env python3
"""Static tests for the eight pinned image cores (#274): build/<core>/build.sh + build/cores/core.sh.

    python3 build/cores/tests/test_core_builds.py

Never runs a real build (no docker needed):
  1. each build/<core>/build.sh pins a full commit, a libretro GitHub repo and a makefile, names its own folder,
     and passes `bash -n` and `--help`;
  2. the pins agree with the readers: the cores role copies build/<core>/out/<core>_libretro.so, and the licence
     manifest (skills/build-image/licences/components.json) has origin built, build build/<core>, pin CORE_COMMIT and
     the same commit as ref and in its licence URL;
  3. the BUILDINFO block of core.sh, run with stub tools, writes the `commit:` line licences.py reads.
Broken cases: a branch, a short sha, a non-libretro repo, a make dir with `..`, shell in the make flags, a CORE that is
not the folder's name, a missing core.sh: each exits 2 (or fails) before docker; no docker exits 3.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

B = pathlib.Path(__file__).resolve().parents[2]          # build/
REPO = B.parent
CORES = ["fbneo", "genesis_plus_gx", "mame", "mednafen_pce", "mednafen_saturn", "nestopia", "pcsx_rearmed", "snes9x"]
sys.path.insert(0, str(REPO / "tests" / "lib"))
from hktest import check, main  # noqa: E402


def pins(core, text=None):
    s = text if text is not None else (B / core / "build.sh").read_text()
    return dict(re.findall(r'(?m)^(CORE[A-Z_]*)="([^"]*)"', s))


def tree(t, core, text=None, with_core_sh=True):
    """Lay out t/build/{lib.sh,cores/core.sh,<core>/build.sh} (the real files, or text as build.sh)."""
    d = pathlib.Path(t) / "build"
    (d / core).mkdir(parents=True)
    (d / "cores").mkdir()
    shutil.copy(B / "lib.sh", d / "lib.sh")
    if with_core_sh:
        shutil.copy(B / "cores" / "core.sh", d / "cores" / "core.sh")
    p = d / core / "build.sh"
    p.write_text(text if text is not None else (B / core / "build.sh").read_text())
    return p


def run(core, old=None, new=None, env=None, with_core_sh=True, args=None):
    s = (B / core / "build.sh").read_text()
    if old is not None:
        assert old in s, old
        s = s.replace(old, new)
    with tempfile.TemporaryDirectory() as t:
        p = tree(t, core, s, with_core_sh)
        a = args if args is not None else ["--out", str(pathlib.Path(t) / "out")]
        r = subprocess.run(["bash", str(p)] + a, capture_output=True, text=True, env=env)
    return r


def no_docker_env(t):
    """A PATH with only what build.sh needs before its docker check (no docker)."""
    bindir = os.path.join(t, "bin")
    os.makedirs(bindir)
    for tool in ("bash", "dirname", "basename", "cat"):
        os.symlink(shutil.which(tool), os.path.join(bindir, tool))
    return {"PATH": bindir, "HOME": os.environ.get("HOME", t)}


# ---------------------------------------------------------------------------------------------------- pins

def test_each_core_pins_a_commit():
    for core in CORES:
        p = pins(core)
        check(p.get("CORE") == core, "%s: CORE names its folder" % core)
        check(re.fullmatch(r"[0-9a-f]{40}", p.get("CORE_COMMIT", "")) is not None, "%s: a full commit sha" % core)
        check(re.fullmatch(r"https://github\.com/libretro/[A-Za-z0-9_.-]+\.git", p.get("CORE_REPO", "")) is not None,
              "%s: a libretro GitHub repo" % core)
        check(p.get("CORE_MAKEFILE") in ("Makefile", "Makefile.libretro"), "%s: a makefile" % core)
        check("source \"$CORE_DIR/../cores/core.sh\"" in (B / core / "build.sh").read_text(), "%s: uses core.sh" % core)
        r = subprocess.run(["bash", "-n", str(B / core / "build.sh")])
        check(r.returncode == 0, "%s: bash -n" % core)
        r = subprocess.run(["bash", str(B / core / "build.sh"), "--help"], capture_output=True, text=True)
        check(r.returncode == 0 and "%s_libretro.so" % core in r.stdout and p["CORE_COMMIT"][:12] in r.stdout,
              "%s: --help names the core and its commit" % core)
    subprocess.run(["bash", "-n", str(B / "cores" / "core.sh")], check=True)


def test_out_gitignored():
    for core in CORES:
        r = subprocess.run(["git", "-C", str(B), "check-ignore", "-q", str(B / core / "out" / ("%s_libretro.so" % core))])
        check(r.returncode == 0, "%s: out/ is gitignored" % core)


def test_readers_agree():
    defaults = (REPO / "ansible" / "roles" / "cores" / "defaults" / "main.yml").read_text()
    man = json.loads((REPO / "skills" / "build-image" / "licences" / "components.json").read_text())
    by_core = {c.get("core"): c for c in man["components"]}
    for core in CORES:
        commit = pins(core)["CORE_COMMIT"]
        want = '  %s: "{{ hk_repo_root }}/build/%s/out/%s_libretro.so"' % (core, core, core)
        check(want in defaults.splitlines(), "%s: the cores role copies build/%s/out" % (core, core))
        c = by_core.get(core, {})
        check(c.get("origin") == "built" and c.get("build") == "build/" + core and c.get("pin") == "CORE_COMMIT",
              "%s: components.json says built from build/%s (pin CORE_COMMIT)" % (core, core))
        check(c.get("ref") == commit, "%s: components.json ref is the pinned commit" % core)
        repo = pins(core)["CORE_REPO"][len("https://github.com/"):-len(".git")]
        check(c.get("source") == "https://github.com/" + repo, "%s: components.json source is the built repo" % core)
        urls = [lf["url"] for lf in c.get("licence_files", []) if "url" in lf]
        check(urls and all(commit in u and repo in u for u in urls), "%s: licence text read at that commit" % core)


def test_buildinfo_has_the_commit_licences_reads():
    s = (B / "cores" / "core.sh").read_text()
    inner = s.split("<<'INNER_EOF'\n")[1].split("\nINNER_EOF")[0]
    blk = re.search(r"(?ms)^\{\n.*?^\} > /out/BUILDINFO$", inner).group(0)
    lib = (B / "lib.sh").read_text().split("<<'INNER_LIB_EOF'\n")[1].split("\nINNER_LIB_EOF")[0]
    with tempfile.TemporaryDirectory() as t:
        t = pathlib.Path(t)
        (t / "bin").mkdir()
        for tool, body in {"file": 'echo "ELF 64-bit LSB shared object, stub"', "gcc": "echo 14.2.0",
                           "ldd": 'echo "\tlibc.so.6 => /lib/libc.so.6 (0x1)"', "dpkg-query": 'echo "  libfoo 1.0"',
                           "objdump": 'echo "GLIBC_2.17 GLIBC_2.38"'}.items():
            (t / "bin" / tool).write_text("#!/bin/sh\n" + body + "\n")
            (t / "bin" / tool).chmod(0o755)
        out = t / "out"
        out.mkdir()
        (out / "x_libretro.so").write_text("x")
        (out / "x_libretro.so.sha256").write_text("abc123  x_libretro.so\n")
        script = ("set -euo pipefail\n" + lib.replace("/out", str(out)) + "\n"
                  + "CORE=x GOT=%s CORE_REPO=r SUBMODULES='abc lib/a' SOURCE_DATE_EPOCH=1 PLATFORM=p\n" % ("c" * 40)
                  + "BASE_IMAGE=i IMAGE_ID=i CORE_MAKE_DIR= CORE_MAKEFILE=Makefile CORE_MAKE_FLAGS= JOBS=2 DEPS='a b'\n"
                  + "SO_NAME=x_libretro.so\n" + blk.replace("/out", str(out)) + "\n")
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                           env={"PATH": "%s:/usr/bin:/bin" % (t / "bin"), "HOME": str(t)})
        check(r.returncode == 0, "the BUILDINFO block runs (%s)" % r.stderr.strip())
        info = (out / "BUILDINFO").read_text() if r.returncode == 0 else ""
    sys.path.insert(0, str(REPO / "skills" / "build-image" / "scripts"))
    try:
        import licences  # noqa: PLC0415
    finally:
        sys.path.pop(0)
    check(re.search(r"(?m)^commit: c{40}$", info) is not None, "BUILDINFO has commit: <40 hex>")
    check("submodules:\n  abc lib/a\n" in info and "make: ./Makefile" in info, "BUILDINFO lists submodules and make")
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "BUILDINFO")
        with open(p, "w") as f:
            f.write(info)
        check(licences.read_buildinfo(p)[1] == "c" * 40, "licences.py reads that commit")


# ---------------------------------------------------------------------------------------------------- usage

def test_usage():
    r = run("nestopia", args=["--bogus"])
    check(r.returncode == 2 and "unknown option" in r.stderr, "an unknown option exits 2")
    r = run("nestopia", args=["--out"])
    check(r.returncode == 2, "--out without a value exits 2")


# ---------------------------------------------------------------------------------------------------- broken pins

def test_broken_pins_refused_before_docker():
    c = pins("snes9x")["CORE_COMMIT"]
    cases = [
        ('CORE_COMMIT="%s"' % c, 'CORE_COMMIT="master"', "not a full commit sha"),
        ('CORE_COMMIT="%s"' % c, 'CORE_COMMIT="%s"' % c[:12], "not a full commit sha"),
        ('CORE_REPO="https://github.com/libretro/snes9x.git"', 'CORE_REPO="http://example.invalid/snes9x.git"',
         "libretro/<repo>.git"),
        ('CORE_MAKE_DIR="libretro"', 'CORE_MAKE_DIR="../libretro"', "relative folder"),
        ('CORE_MAKE_DIR="libretro"', 'CORE_MAKE_DIR="/libretro"', "relative folder"),
        ('CORE_MAKE_FLAGS=""', 'CORE_MAKE_FLAGS="x=1;rm"', "NAME=value"),
        ('CORE_MAKE_FLAGS=""', 'CORE_MAKE_FLAGS="clean"', "not NAME=value"),
        ('CORE_DEPS=""', 'CORE_DEPS="$(id)"', "apt package names"),
        ('CORE_JOBS=""', 'CORE_JOBS="0"', "job count"),
        ('CORE="snes9x"', 'CORE="nestopia"', "not this folder's name"),
    ]
    for old, new, msg in cases:
        r = run("snes9x", old, new)
        check(r.returncode == 2 and msg in r.stderr, "BROKEN: %s is refused, rc 2 (%s %s)" % (new, r.returncode,
                                                                                          r.stderr.strip()[-80:]))


def test_broken_missing_core_sh_fails():
    r = run("nestopia", with_core_sh=False)
    check(r.returncode != 0 and "docker" not in r.stderr, "BROKEN: a build.sh whose core.sh is gone fails (rc %s)"
          % r.returncode)


def test_missing_docker_exits_3():
    with tempfile.TemporaryDirectory() as t:
        env = no_docker_env(t)
        p = tree(t, "nestopia")
        r = subprocess.run([os.path.join(t, "bin", "bash"), str(p), "--out", os.path.join(t, "out")],
                           capture_output=True, text=True, env=env)
    check(r.returncode == 3 and "docker not found" in r.stderr, "no docker exits 3 (%s)" % r.stderr.strip())


if __name__ == "__main__":
    main(globals())
