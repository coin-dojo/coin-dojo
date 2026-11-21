# Emulator playability on Raspberry Pi 5

Ratings for the cabinet's Pi 5. Most retro/arcade emulation is **CPU/GPU-bound, not
RAM-bound** — 2GB is plenty for everything this cabinet is about and most systems up to
Dreamcast/PSP-era. RAM is the wall only for the heaviest cores and heavy front-ends.
(Confirm the cabinet's actual RAM via `free -h` once SSH'd in.)

★★★★★ flawless · ★★★★☆ great · ★★★☆☆ playable/tuning · ★★☆☆☆ select titles · ✗ no

| System | Core/Emu | Pi 5 (2GB) | RAM-sensitive | Notes |
|---|---|---|---|---|
| CPS1/2/3 (cabinet games) | FBNeo | ★★★★★ | no | Trivial; CPS3 included |
| Neo Geo / classic arcade | FBNeo | ★★★★★ | no | |
| NAOMI / Atomiswave | Flycast | ★★★★★ | low | Cabinet already runs this |
| MAME (pre-2000 arcade) | MAME | ★★★★☆ | low | A few late-90s 3D boards vary |
| NES/SNES/GB/GBA/Genesis… | various | ★★★★★ | no | |
| PlayStation 1 | Beetle/PCSX-ReARMed | ★★★★★ | no | |
| Nintendo 64 | ParaLLEl/Mupen | ★★★★☆ | low | Most titles; a few problem games |
| Dreamcast | Flycast | ★★★★★ | low | |
| PSP | PPSSPP | ★★★★★ | low | |
| Saturn | Beetle/Kronos | ★★★☆☆ | med | Demanding; tuning needed |
| GameCube | Dolphin | ★★★☆☆ | HIGH | Many titles playable; 4GB+ recommended |
| Wii | Dolphin | ★★☆☆☆ | HIGH | Select titles |
| PS2 | PCSX2/AetherSX2 | ★★☆☆☆ | HIGH | Lighter games; 8GB helps, not reliable |
| 3DS | Citra | ★★☆☆☆ | high | Experimental on ARM |
| Switch / PS3 / Wii U | — | ✗ | — | Out of reach |

## Does more RAM matter for this cabinet?
No for CPS-arcade + similar — 2GB is fine. An 8GB/16GB Pi 5 mainly buys GameCube/PS2-era
headroom, a heavy themed/web front-end, big libraries with video art, and future-proofing.
The **CPU** is the real limiter for GC/PS2 — RAM alone won't make them flawless.

## Footprint notes
- RetroArch + a 2D core (FBNeo/Flycast): ~150-400MB. OS: ~300-600MB.
- A **chromium/web front-end** is the RAM hog (300-800MB+) — on 2GB, OS+chromium+emulator is tight.
  A **native front-end** (SDL/GTK) is far lighter — the safer choice on 2GB.
