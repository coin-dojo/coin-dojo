# The stock look, and CRT shaders that won't tax the Pi 5

Decoded from the stock image's `Arcadia20XX/video.cfg` (the cabinet's real settings) and
translated to RetroArch choices that stay fast on the Pi 5 (VideoCore VII) at 1080p / 2 GB.

## What the stock look actually is (from video.cfg)
The headline: the **heavy CRT shader is OFF by default** (`enable_crt_shader = 0`). The
everyday look is a stack of **light, cheap effects** plus a bezel — they deliberately kept
it light. Global values:

| video.cfg key | value | meaning |
|---|---|---|
| `crt_curvature` | 1 | mild screen curve (ON) |
| `crt_scanlines` / `scanlines` | 1 / 2 | scanlines ON, intensity level 2 |
| `crt_vignette` | 1 | subtle corner darkening |
| `crt_glow` (+`crt_glow_enable`) | 2 | mild bloom/glow |
| `crt_rgb_mask` | 2 | aperture-grille / shadow-mask type 2 |
| `rgb_blur` | 2 | slight horizontal blur (softens pixels) |
| `rf_interference` | 1 | faint analog RF shimmer |
| `rf_noise` | 0 | no static snow |
| `screen_border` | 1 | **bezel frame ON** |
| `fit_to_bezel` / `integer_scaling` | 0 / 0 | fill the ~4:3 bezel window, not integer-scaled |
| `enable_crt_shader` | 0 | the *full* CRT shader is OFF globally |

**Per-game, they turn the full shader ON for just a couple of titles** — `sfiii3ur1`
(CPS3) and `hsf2` (Hyper SF2): `enable_crt_shader = 1`, but **flat** (`crt_curvature = 0`)
with lighter scanlines. So: light look everywhere, "nice" shader reserved for a few games.
That's the model to copy.

## Mapping to RetroArch (Pi 5-safe)
On the Pi 5 the limit is **GPU fill-rate at 1080p**, not RAM — 2D content leaves plenty of
headroom, so a moderate shader is fine; the danger is the reflection/multi-pass monsters.

**Default preset (everyday, all cores):** `crt/crt-geom.slangp`
- Gives curvature + scanlines + mask like the stock look, one pass, cheap on Pi 5.
- Tune to match: mild curvature, scanline weight ~ medium, aperture mask. Keep **not**
  integer-scaled, 4:3, filling the bezel window.
- Even lighter alternatives if you want zero risk: `zfast-crt`, `crt-pi`, `crt-hyllian`.

**Per-game "fancy" preset (a few titles, like stock did for `sfiii3`):**
`crt/crt-guest-advanced-fast.slangp` (or `-hd`) — adds glow/bloom + better mask, still
single-GPU-friendly on Pi 5 for 2D. Apply as a RetroArch **per-game shader preset override**
(same idea as `video.sfiii3ur1.enable_crt_shader=1`).

**Bezel (the 4:3 frame):** use a **PNG overlay** (`input_overlay` / a simple bezel overlay),
*not* Mega Bezel. A PNG frame is essentially free on the GPU and reproduces `screen_border=1`.
See [[display-bezel-monitor]] for the ~4:3 safe-area geometry.

## Shader tiers for Pi 5 (keep to SAFE/OK)
- **SAFE (default-able):** crt-geom, zfast-crt, crt-pi, crt-hyllian, crt-aperture.
- **OK per-game:** crt-guest-advanced-fast, crt-guest-advanced-hd, crt-geom-deluxe.
- **AVOID on Pi 5:** crt-royale (very heavy), **Mega Bezel Reflection** and full
  crt-guest-advanced with reflections (gorgeous but GPU-crushing at 1080p), gtu-v050.

## How we apply it in the image
- Global: set `video_shader` to `crt-geom` in `retroarch.cfg`, aspect 4:3, integer scale off,
  plus a PNG bezel overlay enabled.
- Per-game overrides: drop `<rom>.slangp`/`.cfg` in the core's config/override dir for the
  handful of titles that deserve the fancier shader (`sfiii3`, Hyper SF2, …).
- `rf_interference=1` has no exact cheap RetroArch equivalent; skip it or use a very light
  NTSC pass only if desired — not worth the cost. `rf_noise=0` → add nothing.
See [[emulator-modes-and-flags]] for the non-visual settings (freeplay, native res, etc.).
