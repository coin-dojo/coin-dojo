# Box64 build (Fightcade mode, #207 / #204)

Reproducible build of **Box64 v0.4.4** (commit `2f130fab1d6e1a4ee8a71dc60cfdfcc839ad192a`) for the Pi 5. Box64 runs
the x86_64 Wine + FBNeo that Fightcade needs on the arm64 cabinet.

## Why this exact build

The #92 spike (a CPS3 fighting game at 59.59 fps, max frame 28 ms) was measured with these versions only:

- Debian's `box64` 0.3.4 crashes. The Fightcade stack needs 0.4.x.
- x86 Electron crashes under Box64 (tcmalloc), so the lobby runs a native arm64 Electron 9.1.0
  (`build/fightcade/fetch.sh`), and only Wine/FBNeo go through Box64.
- Wine 11.22 asserts under Box64; Wine 10.0 wow64 works (also from `build/fightcade/fetch.sh`).
- `-DRPI5ARM64=ON` selects the Pi 5 (Cortex-A76) code paths; `RelWithDebInfo` is what the spike ran.

## Build

    make -C build box64              # same script

    build/box64/build.sh [--out DIR]

Needs docker (colima, arm64). Runs a digest-pinned `debian:trixie` arm64 container, clones the tag, checks the
commit against the pin, runs `cmake` and `make -j2`. `make -j4` ran out of memory in a 2 GiB VM; give docker
at least ~4 GB, or set `BOX64_JOBS=N`. `DIR` must be under `$HOME` (colima only mounts `$HOME`).
Exit codes: 0 ok, 1 build failed, 2 bad usage or bad pin (checked before docker runs), 3 docker unavailable.

## Output (default `build/box64/out/`, gitignored)

| File | What |
|---|---|
| `box64` (+ `.sha256`) | the aarch64 binary |
| `box64.box64rc` (+ `.sha256`) | the build's `system/box64.box64rc` (per-program tweaks), needed at runtime |
| `x64lib/` (+ `x64lib.sha256`) | Box64's bundled x86_64 libs (libgcc_s, libstdc++ ...). Run with `BOX64_LD_LIBRARY_PATH` pointing here |
| `BUILDINFO` | tag, commit, flags, jobs, date, base image digest, gcc/cmake, `box64 --version`, sha256s |

## Never commit the output

The binary is built from upstream source by whoever installs it; the repo ships the method only (`AGENTS.md`,
"ship no artifacts").

## Tests

    python3 build/box64/tests/test_build_script.py
