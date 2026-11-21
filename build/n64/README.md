# N64 core build: Mupen64Plus-Next GLES3 (#49, #61)

`build.sh` builds libretro `mupen64plus-libretro-nx` at a pinned commit (`15bada85`, develop 2026-11-14)
with `platform=rpi5_64_mesa FORCE_GLES3=1`, unstripped, in a digest-pinned `debian:trixie` arm64
container. These are the flags of the core that runs on the cabinet
(`/data/emulators/mupen64plus-gles/mupen64plus_next_libretro.so`, role `retroarch122`, #63).

Why: Debian's core asks RetroArch for desktop GL; the Pi 5 is GLES-only. Trixie has no standalone
mupen64plus packages. The earlier hang at "Enabled TLB" was RetroArch 1.20's Wayland deadlock, fixed by
RetroArch 1.22.2 (`build/retroarch`); with both, N64 runs (Smash Bros. playable 2026-11-14).

## Build

    make -C build n64                  # or N64_OUT=DIR
    build/n64/build.sh [--out DIR]

A rebuild is bit-identical to the live core (sha256 `4bc7731e...`, checked 2026-11-15).
`SOURCE_DATE_EPOCH` is pinned (`BUILD_EPOCH`, 2026-11-14 UTC) so no wall-clock date can creep in.
A branch name or short sha as `MUPEN_COMMIT`, an image without `@sha256:` or flags without
`FORCE_GLES3=1` are refused (exit 2) before docker runs. `--out` must be under `$HOME` (colima).
Exit codes: 0 ok, 1 build failed, 2 bad usage or bad pin, 3 docker unavailable.

Output (`out/`, gitignored, never committed): `mupen64plus_next_libretro.so`, its `.sha256`, `BUILDINFO`
(commit, flags, image digest + id, gcc, package versions, file, sha256, ldd).

Rollback: as in `build/retroarch/README.md` (the role's sha256 pin keeps the live core until bumped).

## Tests

    python3 build/n64/tests/test_build_script.py
