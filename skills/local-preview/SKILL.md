# Skill: Local preview on a Mac (ES-DE theme, RetroArch overlay)

Mode 2 renders the ES-DE theme (`make preview`, the one used day to day); Mode 3 checks the in-game
overlay.

# Mode 2: ES-DE theme preview (`preview-esde.sh`, headless, no VNC)

Renders **ES-DE 3.5.0** (the cabinet's front-end) with your theme + sample collections in an arm64
Debian 13 container and saves `system.png` / `gamelist.png`. Use it to verify theme, collection and
gamelist changes **before** they reach the cabinet. Deterministic: same inputs, same screenshots.

## Prereqs (one-time)
- Colima running (docker runtime). No sudo, no PulseAudio, no VNC.
- Your own ES-DE AppImage cached at `$COIN_DOJO_PREVIEW_CACHE/es-de/ES-DE_aarch64.AppImage` (`make preview` sets
  `COIN_DOJO_PREVIEW_CACHE=~/.cache/coin-dojo/preview`; the script alone defaults to the same folder)
  (same build as the cabinet: `scp coindojo@<cabinet>:/opt/ES-DE/ES-DE_aarch64.AppImage` there).
  It is extracted once (no FUSE needed) and never committed. Override: `--appimage FILE`, `COIN_DOJO_PREVIEW_CACHE`.
- Theme dir and cache must be under `$HOME` (Colima shares only `$HOME`).

## Run
```
skills/local-preview/preview-esde.sh <theme-dir> <out-dir> [--view system|gamelist|both]
                                     [--keys "Left Return Return"] [--fixture DIR] [--keep]
```
- `--view` (default `both`): `system` = the system carousel; `gamelist` = press `--keys` (default `Return`)
  to enter the first system. Keys are xdotool names; `Left Return Return` opens the "fighters" collection
  (custom collections sit in a "Custom collections" folder at the end of the carousel, so `Left` wraps to it).
- Exit codes: **0** rendered with the theme and no theme errors; **1** theme fell back to the built-in one or
  logged theme errors (the detector prints the offending log lines); **2** bad usage/missing prereq; **3** ES-DE
  crashed or timed out. First run ~1.5 min (image build + extraction); reruns ~25 s.
- Fixture: `skills/local-preview/fixtures/esde-preview/` = trimmed real gamelists (cps1, cps2, neogeo, 12 games each, synthetic
  descriptions) + collections `fighters`, `must-play`, `shmups`. ROMs are **empty placeholder files**; no art.
  Add systems/collections by adding `gamelists/<sys>/gamelist.xml` / `collections/custom-<name>.cfg`.
  The fixture can also carry **placeholder media** (`media/<sys>/screenshots/<stem>.png`, synthetic images only).
  Without media the theme uses its narrow "no media" variant and hides the info column and the controls card, so
  add some to see them.
- The ES-DE preview runs with the **screensaver off**, so a screenshot is never a screensaver frame.

## Why each piece
- **Xvfb + llvmpipe (software GL), not a Wayland compositor:** ES-DE uses SDL2, which runs on X11 unchanged;
  Xvfb needs no DRM/seat/privileges, and `xdotool` + ImageMagick `import` give reliable key injection and
  screenshots. A headless wlroots (cage/sway) adds seat/input plumbing for no extra theme fidelity.
- **Fallback detection:** ES-DE silently loads its built-in *Linear* theme when ours is missing or broken (even
  a malformed XML include still logs "Finished loading theme"). `esde/check_log.py` fails when the log does not
  say `Finished loading theme "<our theme>"` or contains any theme Error/Warn (ThemeData parse errors,
  missing capabilities.xml, "does not exist, loading default theme").
- **Settings:** `esde/make_home.py` writes a fresh `es_settings.xml` (theme key is `Theme`, not `ThemeSet`;
  template keys only; the cabinet's file holds a scraper password and is never read or copied).
- 1440x1080 (4:3) screen like the cabinet's `--resolution 1440 1080`; the cabinet's `--fullscreen-padding 1`
  (a 1920x1080 panel with a centered 4:3 area) is not simulated.

## Tests
`python3 skills/local-preview/tests/test_esde_preview.py` runs the fast checks (detector on fixture logs incl. fallback / malformed /
no-theme logs that MUST fail, fixture builder, CLI usage; ~1 s). The full render (good theme PASS, copy with
malformed XML MUST FAIL) is the opt-in smoke: `ESDE_PREVIEW_SMOKE=1 python3 skills/local-preview/tests/test_esde_preview.py`.

## Does NOT cover (still needs the cabinet)
- GPU/Vulkan paths and real GLES: this is llvmpipe, so speed, vsync, tearing and GPU-specific bugs are invisible.
- Real controller / pad input (xdotool sends keyboard events only), audio, video snaps, launching games
  (no RetroArch, no cores, no ROMs), box art / media (none in the fixture), and the cage/Wayland session itself.
- Games: the ES-DE preview launches nothing. In-game overlays have their own checks, below.
- Fonts or shaders that behave differently on V3D; treat a pixel-level match as "very likely", not proof.

---

# Mode 3: RetroArch overlay check (#57, in-game controls card)

Proves the controls overlay shows, freezes the game while open, and the game moves again after closing.
PASS rule (both checks): card shown vs running > 100000 pixels changed, 0 changed while open, > 0 after closing.
Bring your own core, ROM, overlays and override.cfg under `$HOME`; nothing is committed (screenshots of games stay local).

- **1.20 on X11** (`skills/controls/preview/overlay-preview.sh`): Debian's RetroArch 1.20 on Xvfb, keys via xdotool. PASSED.
  It is not the cabinet binary; 1.20 deadlocks with overlays under Wayland, which is why the cabinet overlays are off.
- **1.22.2 on Wayland** (`skills/local-preview/retroarch-122/overlay-preview-122.sh <ra122 dir> <core dir> <rom> <overlays dir> <override.cfg> <out dir>`):
  runs the cabinet's patched 1.22.2 (Wayland/EGL/GLES, no X11; copy `retroarch` + `BUILDINFO` from
  `/data/emulators/retroarch-1.22/` on the cabinet into `~/.cache/coin-dojo/preview/ra122/`) in a headless wlroots
  session (cage, pixman renderer), screenshots with grim, drives it over the network command port (UDP 55355,
  `OVERLAY_NEXT`, `PAUSE_TOGGLE`). PASSED 2026-11-17 (#175, Eternal Champions: 1.52e6 / 0 / 1.26e6).
  - Use the **patched** build (BUILDINFO `patches: 0001-egl-loop-only-matched-configs ...`, sha256 `32041d76...`, the
    cabinet's `retroarch`, or `build/retroarch/out/`), never the cabinet's `retroarch.unpatched`: that one segfaults
    at EGL here and cage quits with it. The wrapper refuses a BUILDINFO that is not v1.22.2 or lacks that patch (exit 2).
  - Liveness is `VERSION` on the command port; never send `GET_STATUS`: it segfaults 1.22.2 in this container (#175).
  - Startup smoke: before any screenshot the run waits (`READY_WAIT`, default 60 s) for cage's Wayland socket and a
    `VERSION` answer, else exits **3** with a named error in `<out>/startup.txt`: `CAGE_START_FAILED`, `RA_EXITED`
    (139 = segfault), `RA_NO_ANSWER`, `SHOT_FAILED`; the wrapper adds `NO_VERDICT` and `DIFF_FAILED`. Logs:
    `<out>/cage.log`, `<out>/ra.log`, `<out>/ra.exit`. Exit 1 = the overlay PASS rule failed.
  - Fast checks (fake cage/RetroArch/docker, broken cases incl. cage exit 1, ~6 s):
    `python3 skills/local-preview/tests/test_ra122_preview.py`; real run opt-in: `RA122_PREVIEW_SMOKE=1
    RA122_SMOKE_RA=<patched dir> RA122_SMOKE_DIR=<dir with core/ rom/ overlays/ override.cfg>` (~1 min).
