# Standalone flycast build

Reproducible build of standalone flycast (pinned **v2.7**) for the cabinet (Pi 5, Debian 13 arm64).

## Why standalone

- The cabinet's RetroArch is a GLES-only build.
- Its flycast core asks for a desktop-GL context and is refused.
- The core's Vulkan path renders a grey screen on the Pi 5 (v3dv).
- Standalone flycast brings its own SDL2 + GLES3 and renders correctly
  (Ikaruga at 2x, 59.9 fps, 2026-11-14).

## Patches (#188)

`patches/` holds two small patches to `core/ui/gui.cpp`, applied in order after the clone. Each is
pinned by sha256 in `FC_PATCHES` at the top of `build.sh`: a changed, unpinned or missing patch stops
the build with exit 2 before docker runs. Both do nothing unless their env var is `1`, so the binary
behaves like upstream by default:

| Patch | Env var | Effect |
|---|---|---|
| 0001-gui-inside-4-3-area | `FLYCAST_GUI_43=1` | Menu, settings and achievement popups stay inside the centred 4:3 area (the side margins become flycast's safe-area insets; the cabinet bezel shows x=240..1680 of 1920). |
| 0002-fps-top-right | `FLYCAST_FPS_TOPRIGHT=1` | The FPS counter (`rend.ShowFPS`) moves from bottom left to top right inside that area, cyan `#36d6e7` on indigo like RetroArch's benchmark counter, and stays on while achievement popups or leaderboard/challenge trackers show (they sit bottom left; upstream hides the counter for them, #199). |

The `dreamcast` role's launcher sets them (#187). `BUILDINFO` lists the applied patches.

## Build

    make -C build flycast            # same script (or `make build` for all)

    build/flycast/build.sh [--out DIR]

Needs docker (colima on the Mac is aarch64; on an x86 host it warns and builds under
`--platform linux/arm64` emulation, much slower). It runs a `debian:trixie` arm64 container,
installs the build deps, clones flycast at the pinned tag, builds with ninja and removes the
container. Pins (tag, base image, cmake flags) sit at the top of `build.sh`.

Output (default `build/flycast/out/`, gitignored): `flycast`, `flycast.sha256`, `BUILDINFO`
(tag, commit, date, flags, `ldd` list). Takes about 10 min on colima with 6 cpus.
Exit codes: 0 ok, 1 build failed, 2 bad usage, 3 docker unavailable.

## Runtime deps on the cabinet

`libzip5` and `libminiupnpc18`; everything else is already present.

## Never commit the binary

The binary is built by the user from upstream source; this repo ships the method only
(see `AGENTS.md`, "ship no artifacts"). `out/` is gitignored.

## Tests

    make -C build test

    python3 build/flycast/tests/test_build_script.py
