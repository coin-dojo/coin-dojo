# Stella libretro core build

Reproducible build of the Stella (Atari 2600) libretro core (pinned **7.0**, commit
`d55b1ae`) for the cabinet (Pi 5, Debian 13 arm64).

## Why build it

- Debian 13 has no `libretro-stella` package.
- ES-DE's `atari2600` system runs `stella_libretro.so` by default (first command, label "Stella").
- Tag 7.0 is the newest Stella release; tags 7.0a-7.0c only respin the Windows installer.

## Build

    make -C build stella            # same script (or `make build` for all)

    build/stella/build.sh [--out DIR]

Needs docker (colima on the Mac is aarch64; on an x86 host it warns and builds under
`--platform linux/arm64` emulation, much slower). It runs a `debian:trixie` arm64 container,
installs the build deps, clones Stella at the pinned tag, checks the commit sha, runs
`make -C src/os/libretro platform=unix`, strips the core, checks it is an aarch64 ELF that
exports `retro_init`/`retro_load_game`, and removes the container. Pins (tag, commit, base
image, make flags) sit at the top of `build.sh`; a branch name or short sha is refused.

Output (default `build/stella/out/`, gitignored): `stella_libretro.so`,
`stella_libretro.so.sha256`, `BUILDINFO` (tag, commit, date, image digest, gcc, max
GLIBC symbol version, `ldd` list). Takes about 80 s on colima with 6 cpus.
Exit codes: 0 ok, 1 build failed, 2 bad usage or bad pin, 3 docker unavailable.

## Runtime deps on the cabinet

None beyond libc/libm: libstdc++ and libgcc are linked statically (upstream Makefile).
Needs GLIBC 2.38 or newer (trixie has 2.41).

## Where it goes

ES-DE finds RetroArch cores via `CORE_RETROARCH`; the cabinet's
`~/ES-DE/custom_systems/es_find_rules.xml` sets it to `/usr/lib/aarch64-linux-gnu/libretro`
only, which is also RetroArch's `libretro_directory`. The core must land there as
`stella_libretro.so` (placement is the coordinator's call).

## Never commit the binary

The core is built by the user from upstream source (GPL-2.0); this repo ships the method only
(see `AGENTS.md`, "ship no artifacts"). `out/` is gitignored.

## Tests

    make -C build test

    python3 build/stella/tests/test_build_script.py
