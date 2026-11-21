# The image's libretro cores, built from pinned commits

The image (and a fresh cabinet) runs nine libretro cores from `ansible/roles/cores`: Stella from `build/stella`, and
these eight from pinned commits (#274), each in its own `build/<core>/build.sh`:

| Core | Upstream (libretro) | Commit (master, 2026-11-19) | Makefile | Built 2026-11-20 (colima, 6 cpus) |
|---|---|---|---|---|
| `fbneo` | FBNeo | `5f6bdff9` | `src/burner/libretro/Makefile` | yes, 230 s |
| `genesis_plus_gx` | Genesis-Plus-GX | `b9c23164` | `Makefile.libretro` | yes, 24 s |
| `mame` | mame | `5f5a0774` | `Makefile.libretro` (4 jobs, hours) | started only (genie + first drivers fine; ~6 h at 4 jobs) |
| `mednafen_pce` | beetle-pce-libretro | `0c9db4d5` | `Makefile` | yes, 23 s |
| `mednafen_saturn` | beetle-saturn-libretro | `7126d65b` | `Makefile` | yes, 91 s |
| `nestopia` | nestopia | `f1e02d12` | `libretro/Makefile` | yes, 27 s |
| `pcsx_rearmed` | pcsx_rearmed (with submodules) | `7b0e8f8f` | `Makefile.libretro` | yes, 24 s |
| `snes9x` | snes9x | `204ea1c0` | `libretro/Makefile` | yes, 56 s |

## Why build them

They used to come from the libretro buildbot's `latest` nightlies, which name no commit. GPL-2.0 cores need their exact
corresponding source for a shared image (#267, `COMPLIANCE.md`); Genesis Plus GX links LGPL code; FBNeo's terms ask
to distribute or link the source. A pinned build names it: the commit is the `ref` of the core in
`skills/build-image/licences/components.json`, and the build's `BUILDINFO` goes into the image's licence bundle.

## Build

    make -C build cores              # all eight, one after the other (MAME alone takes hours)
    make -C build nestopia           # one core
    build/nestopia/build.sh [--out DIR]

Each `build.sh` holds only its pins (`CORE_COMMIT`, repo, makefile, flags, extra packages); the steps are in
`build/cores/core.sh`: a digest-pinned `debian:trixie` arm64 container (the same image as `build/n64`), a clone at the
pinned commit (checked), its submodules at the commits that tree records, `make` with `SOURCE_DATE_EPOCH` set to the
commit's own time, `strip --strip-unneeded`, then the checks every core gets (aarch64 ELF, libretro entry points).
A branch name, a short sha, a repo outside `github.com/libretro`, or shell in the flags is refused before docker runs.

Output (default `build/<core>/out/`, gitignored, under `$HOME` for colima): `<core>_libretro.so`, its `.sha256` and
`BUILDINFO` (core, commit, repo, submodules, date, source_date_epoch, base image and id, makefile, flags, gcc, packages,
file, sha256, max GLIBC, ldd). Exit codes: 0 ok, 1 build failed, 2 bad usage or bad pin, 3 docker unavailable.

## Where it goes

The cores role (`cores_built` in `ansible/roles/cores/defaults/main.yml`) copies `build/<core>/out/<core>_libretro.so`
into `/usr/lib/aarch64-linux-gnu/libretro` after checking it against its `.sha256`, only where the core is missing
(a cabinet's own cores stay). A core with no build output is downloaded from the libretro buildbot instead, with
one WARN line ("buildbot core: not source-pinned, fine for personal use, not for a release"), for `make deploy` and
personal or offline images. A release (`HK_LICENCES_STRICT=1`) takes built cores only: `run.sh` stops before the
build when one is missing, and the role fails on one too. MAME's long build runs on a bigger build machine, not
the laptop.

## Changing a pin

Change `CORE_COMMIT` in `build/<core>/build.sh`, then the core's `ref`, `version` and licence URL in
`skills/build-image/licences/components.json` (re-read its licence text at the new commit). `licences.py check` and
`build/cores/tests/test_core_builds.py` fail while they disagree.

## Never commit the binary

Ship no artifacts (`AGENTS.md`): the user builds the cores from upstream source; `out/` is gitignored.

## Tests

    python3 build/cores/tests/test_core_builds.py
