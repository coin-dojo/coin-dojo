# arm64 emulation proof (what we validated on the Mac, and what needs the cabinet)

## Validated on an arm64 container (= Pi 5 architecture)
- **RetroArch v1.14.0 runs natively on arm64** (installed + launched).
- **A real libretro core runs**: the free, ROM-less `2048` core (arm64 `.so` from the libretro
  buildbot) loaded and ran — live window "RetroArch 2048 v1.0". So the libretro stack that Batocera
  uses works on the Pi's architecture.
- Earlier: the stock Iconic **launcher (GTK2) and `arcadia` (SDL2+GL libretro front-end) run** from
  the card image in the same arm64 container.

## Caveat (not a real-hardware problem)
Capturing GL output from inside a **headless software-GL** container is unreliable — GL apps render
to a buffer the X screen-grab can't read (blank screenshots), even while the app runs fine. The real
Pi 5 has a GPU (`vc4-kms-v3d`), so rendering/shaders are a non-issue there.

## Still needs the real cabinet (spare card)
- Actually booting **Batocera** on the Pi 5 and confirming the RTD2662 monitor accepts 1080p.
- Final **bezel/CRT alignment** to the physical panel (match Iconic's look using the saved values in
  [[iconic-video-settings]]).
- The **Pico controller** button indices via `jstest` (to finalize the mapping).
- Per-core full-speed checks for the heavier 5-star systems (Dreamcast/PSP) on real hardware.

## Conclusion
The emulation layer is confirmed Pi-compatible. The remaining unknowns are hardware-specific
(display accept, bezel alignment, controller indices) and are quick to settle on a spare card.
