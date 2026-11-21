# Project direction (decided): community arcade pack on a custom OS

## Decision
Build a **custom-OS arcade pack with the stock look** (Path B) as the primary direction — not a
mod of Iconic's closed launcher (Path A, now secondary/reference only).

## What we ship (method + config only — ZERO artifacts)
An **empty, reusable arcade build** others apply to their own hardware:
- Base: **Batocera** (Pi 5), themeable ES-DE/Pegasus front-end, RetroArch under the hood.
- **Our "arcade pack"**: CRT shader preset + **bezel overlay** tuned to the ~4:3 stock look;
  a **category-first theme** (Favorites / Recently Played / genre rows / search) with video-snap
  showcase + idle attract; **controller mapping** for the Pico HID stick (+ generic fallback);
  **1080p HDMI** / RTD2662 monitor notes; **RetroArch Netplay** enabled; de-bloated (no telemetry).
- Folder structure + a **setup guide**: "drop your ROMs here, add BIOS, scrape artwork."

## What the community brings (never us)
ROMs, BIOS, CHDs, box art / video snaps. We ship **no** image, launcher, ROMs, artwork, or BIOS
(see [[direction]] + the ship-no-artifacts principle). Batocera itself is open-source/redistributable,
but we prefer shipping a **config/theme pack + guide** over a full image to stay lean and clean.

## Why this is better
- Legal + clean (no copyrighted content).
- Useful to anyone with this cabinet (or a similar Pi build), not just the maintainer.
- Open ecosystem: all emulators (see [[emulator-matrix]]), open netplay, community themes.
- Fully reversible for the maintainer: the Iconic card is imaged; swap cards to go back.

## Parity targets (match or beat Iconic)
- 4:3 virtual bezel + per-game CRT shader (see [[display-bezel-monitor]]).
- Category menu + hover showcase + attract randomizer (see [[frontend-inspiration]]).
- Pico stick works (standard HID; see [[hardware-profile]]); one-time global controller map covers all cores.
- Online play via RetroArch Netplay (Pi 5 2GB is fine for CPS2/Neo Geo).

## Refinements (the maintainer, this session)
- **Frontend = ES-DE** (lightest-feel, has attract/screensaver, a RomM server can feed it). This means the
  base is **Raspberry Pi OS lite + ES-DE + RetroArch** (preferred, lightest) or **RetroPie + ES-DE** —
  NOT Batocera (Batocera is locked to its own EmulationStation). The ansible deploy is path-configurable.
- **ROM workflow**: a **curated local set on the Pi** (offline-ready favorites) PLUS, if you run a RomM
  server, **browse it and pull more on demand**. Local set is easy; the on-cabinet "browse RomM catalog + download" piece has no
  mature ES-DE/Pi tool today (it exists for muOS via `grout`) — curate via RomM web UI + sync for now,
  and building an on-device RomM browser is a community-contribution opportunity (see the RomM-browser issue).
- Save-sync back to your file server or RomM is a bonus worth wiring in.

## Validate on real hardware
Flash a **spare card** (Pi OS+ES-DE or RetroPie), boot on the cabinet: confirm 1080p on the RTD2662
monitor, Pico controller detection/mapping, then apply the pack and tune bezel/CRT to the stock look.
