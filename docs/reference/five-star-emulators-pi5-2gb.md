# 5-star emulators for the cabinet (Raspberry Pi 5, 2 GB)

The target hardware is a **Pi 5 with 2 GB RAM**. That rules out the heavy 3D systems
regardless of core quality — budget accordingly. "5-star" = runs *flawlessly* at full
speed, 1080p, with the stock look's light CRT on top. Our image ([[build-image]])
installs the cores below from the libretro aarch64 buildbot.

## The approved set (install + tune these)

| System | Core (installed) | Pi 5 / 2 GB | BIOS needed | Notes |
|---|---|---|---|---|
| CPS1 / CPS2 / CPS3 | **fbneo** | ★★★★★ | none | the cabinet's bread and butter; flawless |
| Neo Geo (MVS/AES) | **fbneo** | ★★★★★ | `neogeo.zip` | flawless |
| Arcade (classic pre-CPS) | **fbneo**, else **mame2003_plus** | ★★★★★ | per-set | FBNeo first; mame2003_plus for sets FBNeo lacks |
| NAOMI / Atomiswave | **flycast** | ★★★★★ | `naomi.zip`, `awbios.zip` | 2D fighters (CvS2, MvC2, KoF) flawless at native res |
| Sega Dreamcast | **flycast** | ★★★★☆ | `dc_boot.bin`+`dc_flash.bin` | 2D/light-3D flawless; heavy 3D can dip — keep native res |
| Sony PS1 | **pcsx_rearmed** | ★★★★★ | optional `scph5501.bin` | flawless; NEON dynarec |
| Sony PSP | **ppsspp** | ★★★★☆ | none | most 2D/arcade ports flawless at 1x; heavy 3D varies |
| Super Nintendo | **snes9x** | ★★★★★ | none | flawless (incl. most SuperFX) |
| Genesis / MD / CD | **genesis_plus_gx** | ★★★★★ | Sega CD: `bios_CD_*.bin` | flawless |
| NES / Famicom | **nestopia** | ★★★★★ | none | flawless |
| Game Boy / Color | **gambatte** | ★★★★★ | none | flawless |
| Game Boy Advance | **mgba** | ★★★★★ | optional `gba_bios.bin` | flawless |
| Nintendo 64 | **mupen64plus_next** | ★★★☆☆ | none | **hit-or-miss on 2 GB** — many games fine, some glitch/stutter; treat as bonus, not core |

## Do NOT ship/attempt on Pi 5 / 2 GB (will disappoint)
GameCube/Wii (Dolphin), PS2 (PCSX2/play!), Saturn (full-speed is marginal), Wii U, 3DS
(Citra 2D only, shaky), PS3/Xbox/Switch. These are **RAM- and GPU-bound**; 2 GB makes them
worse. Leave them off so the menu only shows what actually plays well. (If the maintainer ever moves
to an 8/16 GB Pi 5, Saturn via Kronos/YabaSanshiro and light GameCube become reasonable.)

## 2 GB RAM tuning (do this in the image)
- **zram swap**: enable a compressed swap (~50% of RAM) so the odd spike (PPSSPP/N64/DC
  heavy scenes) doesn't OOM-kill the emulator. Pi OS: `zram-tools` or a small systemd unit.
- **RetroArch**: keep **Rewind OFF** (large ring buffer eats RAM), **Run-Ahead OFF** for the
  heavy cores (flycast/ppsspp/n64), leave it optional for 2D cores. Threaded video ON.
- **No supersampling / internal upscales** on flycast/ppsspp — native res (see
  [[emulator-modes-and-flags]]); upscaling is the fastest way to blow the GPU + RAM budget.
- One core loaded at a time (ES-DE launches per-game), so peak RAM = OS (~250 MB) + one core.

## Why this set
It mirrors what the Iconic OS actually shipped (its launcher bundled only **two** cores:
`coindojo` for CPS/arcade and `flycast` for NAOMI/DC) and rated 5-star on this exact board,
then extends it with the light console cores that are free wins at 2 GB. See
[[emulator-modes-and-flags]] for per-core settings lifted from the stock image, and
[[stock-look-crt-shaders]] for the look that stays safe on the Pi 5 GPU.
