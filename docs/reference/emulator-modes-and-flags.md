# How the cabinet launches games: modes, flags, per-core tuning

What "start a game" means on the Iconic cabinet, pulled from the stock configs, and how to
reproduce it per-core in RetroArch on the Pi 5 (2 GB). The theme throughout: **free play,
native resolution, fullscreen, light effects** — nothing that over-taxes the GPU/RAM.

## The modes the cabinet uses
- **Free play (no coins).** flycast runs `ForceFreePlay = yes`. A cabinet shouldn't ask for
  quarters. Two ways to get it in RetroArch:
  1. **Map a Coin button** (we already map **btn 8 = Coin/Select**, btn 9 = Start) so a player
     can "insert coin" + start — works for every arcade core, no per-game setup. (See
     [[cabinet-controller]].)
  2. **Free-play DIP per game** where the core supports it (FBNeo/MAME dipswitch → Free Play),
     for games where you want zero button presses to start. Store as a per-game core-option
     override. Do this only for the showcase/favourites; the Coin button covers the rest.
- **Native resolution, fullscreen, no upscale.** flycast is pinned to `rend.Resolution = 480`
  (native) and the launcher runs fullscreen. This is deliberate 2 GB/GPU budgeting — **do not**
  bump internal resolution on flycast/ppsspp/n64.
- **No savestate autoload, no rewind.** `AutoLoadState/AutoSaveState = no`. Keep RetroArch
  rewind off (RAM). Savestates still available on the hotkey.

## Per-core settings (from the stock image, mapped to the libretro cores)

### flycast (NAOMI / Atomiswave / Dreamcast) — the cabinet's real values
Stock `flycast/emu.cfg` → libretro `flycast` core options:
| Stock emu.cfg | Value | libretro core option | Set to |
|---|---|---|---|
| `rend.Resolution` | 480 | `reicast_internal_resolution` | `640x480` (native) |
| `Dynarec.Enabled` | yes | `reicast_enable_dynarec` | enabled |
| `rend.ThreadedRendering` | yes | `reicast_threaded_rendering` | enabled |
| `pvr.MaxThreads` | 3 | `reicast_threaded_rendering` (threads) | on |
| `Sh4Clock` | 200 | (overclock) | leave default unless a game needs it |
| `Dreamcast.RamMod32MB` | no | `reicast_extra_depth_scale`/ram | 16 MB (default) |
| `rend.RenderToTextureBuffer` | no | `reicast_render_to_texture_upscaling` | 1x, off |
| `Dreamcast.Cable` | 3 (VGA) | `reicast_cable_type` | VGA (RGB) |
| `Dreamcast.Region` | 1 | `reicast_region` | USA |
| `Dreamcast.Broadcast` | 0 | `reicast_broadcast` | NTSC |
| `ForceFreePlay` | yes | (NAOMI DIP) | Free Play |
| `Dreamcast.HideLegacyNaomiRoms` | yes | — | N/A (ES-DE lists by folder) |
| 2-player | `maple_sdl_joystick_0=0`, `..._1=1` | ports 0+1 | auto (see [[two-player-support]]) |
Key point: **native 640×480, threaded, dynarec, VGA cable** = the smooth 2 GB profile.

### pcsx_rearmed (PS1)
NEON dynarec on, `pcsx_rearmed_neon_enhancement_enable = disabled` (no upscale — 2 GB),
frameskip 0, bios optional (`scph5501.bin` for the few games that need it). Flawless.

### ppsspp (PSP)
`ppsspp_internal_resolution = 1x` (480×272 — **do not** raise on 2 GB), `ppsspp_cpu_core = JIT`,
`ppsspp_frameskip = off`, `ppsspp_rendering_mode = buffered`. 2D/arcade ports flawless; heavy
3D varies — test per title.

### fbneo (CPS1/2/3, Neo Geo, arcade)
`fbneo-frameskip = 0`, `fbneo-cpu-speed-adjust = 100`, diagnostic/DIP for free play per game,
`fbneo-neogeo-mode` as needed. Flawless; this is the main core.

### mame2003_plus (arcade FBNeo doesn't cover)
Free-play via the game's in-emulator dipswitch menu; `mame2003-plus_skip_disclaimer = enabled`,
`..._skip_warnings = enabled` so a game boots straight in (kiosk-friendly).

## Kiosk launch behaviour (ES-DE → RetroArch)
- ES-DE launches one core per game (peak RAM = OS + one core — fine on 2 GB).
- Boot straight into the game, skip disclaimers/warnings (core options above).
- Fullscreen, 4:3, bezel + light CRT shader on top (see [[stock-look-crt-shaders]]).
- Exit back to the menu via the hotkey (Coin+HK in our map) — no keyboard.

See [[five-star-emulators-pi5-2gb.md]] for which cores to ship and the 2 GB RAM tuning.
