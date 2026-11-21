# RetroArch 1.22.2 build (#61)

Reproducible build of RetroArch **v1.22.2** (commit `69a4f0e`) plus two local patches, for the cabinet
(Pi 5, Debian 13 arm64). The cabinet runs it for **all** RetroArch systems from
`/data/emulators/retroarch-1.22/` (role `ansible/roles/retroarch122`, #63).

## Why

Debian's RetroArch 1.20.0 with Raspberry Pi's libwayland-client 1.24 deadlocks in `flush_wayland_fd()`
(blocking `wl_display_dispatch`; RetroArch #18148, fixed by 6775559c2 in 1.22.0). N64 (GLideN64) hung at
the first frame. Trixie has no newer RetroArch package.

## Patches (`patches/`, applied in order, each pinned by sha256 in `build.sh`)

- `0001-egl-loop-only-matched-configs.patch`: only loop over the configs `eglChooseConfig` filled.
- `0002-wayland-gles2-request-on-gles3-build.patch`: `--enable-opengles3` drops `HAVE_OPENGLES2`, so a GLES2
  request (every software core) left `attrib_ptr` NULL and segfaulted; serve it with the ES3 attribs.
  Worth upstreaming.

A patch whose sha256 differs from `RA_PATCHES`, a missing pinned patch, or an extra unlisted `*.patch`
stops the build (exit 2) before docker runs. Change a patch = update its sha256 in `build.sh` + the test.

## Build

    make -C build retroarch                  # or RETROARCH_OUT=DIR
    build/retroarch/build.sh [--out DIR]     # RA_STRIP=0 keeps symbols (gdb)

Docker (colima) runs `debian:trixie` pinned by digest, `linux/arm64`. It clones the tag, checks the
commit, applies the patches, configures (GLES/GLES3/3.1, EGL, Wayland, KMS, udev, ALSA, Pulse;
no X11/Qt/Vulkan/FFmpeg/Discord), builds with `SOURCE_DATE_EPOCH` = `BUILD_EPOCH` (pinned to the live
build's day, 2026-11-14 UTC, so the "Built: Nov 14 2026" stamp and therefore the whole file match), strips.
`--out` must be under `$HOME`: colima only mounts `$HOME`, a `/private/tmp` dir silently loses the file.
Exit codes: 0 ok, 1 build failed, 2 bad usage / bad pin / patch mismatch, 3 docker unavailable.

A rebuild is bit-identical to the cabinet's binary (sha256 `32041d76...`, checked 2026-11-15); with the
commit time as epoch instead, only the BuildID and three date stamps (29 bytes) differed.
The BUILDINFO format is newer than the live one (the role pins the old file, `ceef04ec...`), so keep the
pinned BUILDINFO in `out/` and send `--out` elsewhere (`build/retroarch/work/...`) when you rebuild.

Output (`out/`, gitignored): `retroarch`, `retroarch.sha256`, `BUILDINFO` (tag, commit, patch sha256s,
flags, strip, image digest + id, gcc, package versions, file, sha256).

## Rollback

The role pins the live files' sha256s. A rebuild that is not bit-identical does **not** change the pins,
so a deploy refuses it and the cabinet keeps today's files. To go back after a pin bump: `git revert`
the bump and put the old binary (kept in `out/` of the worktree that made it, or copied off the cabinet)
back in `build/retroarch/out/`. The cabinet also keeps `retroarch.unpatched` next to the binary.

## Tests

    make -C build test
    python3 build/retroarch/tests/test_build_script.py
