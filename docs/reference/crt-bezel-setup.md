# Reproducing the stock look — CRT shader + 4:3 bezel in Batocera

How to match the stock look (virtual bezel + CRT shader, see
[[display-bezel-monitor]]) in RetroArch under Batocera. Two independent layers:

1. **CRT shader** — scanlines, mask, curvature, glow, blur, vignette, (RF noise).
2. **Decoration / bezel** — the 4:3 artwork border that fills the 16:9 1080p sides.

Both can be set **globally** and **per-game**, which is exactly how Iconic's `video.cfg`
works (global defaults + `video.sf2ceuc.*` per-game overrides).

> Shader-parameter names differ between the RetroArch *menu labels* and the preset's
> `.slangp`/`.glslp` text — a well-known gotcha. Treat the parameter names below as the
> mapping *targets* and **[verify]** the exact token against the shader file / live menu when
> tuning on the cabinet.

## A. CRT shader

### Quick route (menu, built-in sets)

**MAIN MENU → GAME SETTINGS → SHADER SET** picks a bundled set globally. Options include
**None, Scanlines, Retro, Enhanced, Curvature, Flatten-glow, Zfast, sharp-bilinear-simple**
(default). Per-system/per-game: open the gamelist, **SELECT → ADVANCED SYSTEM SETTINGS**, or
the per-game config. This gets you 80% of the look in one click; use it as the baseline.

### Full route (crt-guest-advanced for the stock look)

For the tunable stock look use **crt-guest-advanced** (a.k.a. GDV — the modern
standard CRT shader; `crt-royale` is the heavier alternative). On the **Pi 5** it needs the
**glcore** RetroArch video driver (slang shaders) and a **"Full" game aspect ratio**; if the
GL driver or performance fights back, fall back to a glsl CRT preset
(`crt/crt-guest-dr-venom`, `crt/crt-pi-curvature`). **[verify on hardware]** which driver the
RTD2662 + Pi 5 run best.

Set a preset per system or per game in `/userdata/system/batocera.conf` (extension omitted;
presets live under `/usr/share/batocera/shaders/`, custom ones under `/userdata/shaders/`):

```
# global-ish: all arcade on the GDV preset
fbneo-renderer.shader=crt/crt-guest-advanced
# per game (note the quoted ROM filename)
fbneo["sf2ceuc.zip"]-renderer.shader=crt/crt-guest-advanced
```

> The wiki notes the `.shader` key "currently only works with the common-shaders (glsl) set";
> slang presets like crt-guest-advanced are selected via the **Shader Set** menu / a custom
> shader set. The robust, scriptable approach is a **custom shader set** under
> `/userdata/shaders/configs/<set-name>/` with a `rendering-defaults.yml` (`default:` block:
> `shader:` + `scanline:`), which then appears in the SHADER SET menu. **[verify]**

### Iconic `video.cfg` → crt-guest-advanced parameter map

| Iconic option (`video.cfg`) | crt-guest-advanced parameter (approx.) | Notes |
|---|---|---|
| `enable_crt_shader` | select the preset / Shader Set | master on/off |
| `scanlines` / `crt_scanlines` | `SCANLINE_WEIGHT`, beam `beam_min`/`beam_max`; scan type `gsl` | gsl: 0 scanlines / 1 interlace / 2 off |
| `crt_curvature` | curvature `warpX` / `warpY`, `cornersize`, `cornersmooth` | geometry/warp pass |
| `crt_rgb_mask` | `shadowMask` (mask type), `maskstr` (strength), `masksize`, `mcut` | choose aperture/slot/shadow mask |
| `crt_vignette` | `vignette` (on), `vstr` (strength), `vpower` | darkened edges |
| `crt_glow` / `crt_glow_enable` | `glow`, `bloom`, `halation`, `GLOW_WHITEPOINT` | phosphor glow/bloom |
| `rgb_blur` / `crt_rgb_blur` | horizontal blur `HSHARPNESS`/`SIGMA_HOR`, deconvergence (RGB offsets) | softness / RGB separation |
| `rf_noise` / `rf_interference` | **NTSC variant** `crt-guest-advanced-ntsc` (NTSC/IRE params) or an added noise pass | plain GDV has no RF noise; use the NTSC preset |
| `integer_scaling` (+`_720p`) | RetroArch **Integer Scale = ON** (`video_scale_integer`) | see below |
| `fit_to_bezel` / `screen_border` | bezel/overlay + custom viewport | see section B |

Save tuned values as a **shader preset** (RetroArch Quick Menu → Shaders → Save Preset →
Game/Core Preset) so it reloads automatically — the per-game-override equivalent of Iconic's
`video.sf2ceuc.*`.

### Integer scaling

**GAME SETTINGS → INTEGER SCALE (pixel perfect) → ON** (RetroArch `video_scale_integer = true`).
Keeps scanlines even and the mask crisp — important for the CRT look on a fixed-pixel LCD.

Sources: <https://wiki.batocera.org/emulationstation:shaders_set>,
<https://forums.libretro.com/t/new-crt-shader-from-guest-crt-guest-advanced-updates/25444>,
<https://emulation.gametechwiki.com/index.php/Shader_Presets>

## B. Bezel / decoration (the 4:3 border)

Decorations fill the empty sides when a 4:3 game shows on a 16:9 1080p screen — exactly
Iconic's virtual bezel. Supported since v33 on x86_64 and Pi 4+ (Pi 5 included).

### The Bezel Project (fastest)

**MAIN MENU → UPDATES & DOWNLOADS → THE BEZEL PROJECT** → enable per-system packs. These are
per-system (and often per-game) bezels **designed for integer scaling on a 16:9 1080p display**
— a good default for the cabinet. Select with **GAME SETTINGS → DECORATIONS**.

### Custom 4:3 bezel (to match the stock look precisely)

Put files under **`/userdata/decorations/<yourset>/`**. Each bezel is a **`.png` + matching
`.info`** pair. Resolution-match the panel: **1920x1080 PNG**, transparent where the game shows,
artwork on the sides.

Priority (highest first):
1. `games/<system>/<rom>.png` — this game on this system
2. `games/<rom>.png` — this game on any system
3. `systems/<system>.png` — whole system
4. `default.png` — everything

The `.info` sets the **viewport** the emulator renders into — this is how you place the 4:3
game window precisely inside the bezel:

```
# <rom>.info
{ "width":1920, "height":1080,
  "top":0, "left":240, "bottom":0, "right":240,   # ~4:3 window centered in 1080p
  "opacity":1.0, "messagex":0.5, "messagey":0.5 }
```

(1920 − 2×240 = 1440 ≈ 4:3 at 1080 tall; tune the margins to the Iconic window.) A folder
literally named `default` *merges* with Batocera's built-in defaults, your files winning.

### Options

- **STRETCH BEZELS** (GAME SETTINGS → DECORATIONS) or `global.bezel_stretch=1` /
  `mame.bezel_stretch=1` — stretches a bezel to the output res (perf cost).
- **Tattoo overlay** (v33+): `global.bezel.tattoo=system` overlays control maps;
  `=custom` + `global.bezel.tattoo_file=/userdata/system/my_logo.png`;
  `global.bezel.tattoo_corner=NW`. Per-system e.g. `snes.bezel.tattoo=0`.
- Bezel + shader stack cleanly: run the CRT shader on the game viewport, the bezel around it.
  (Mega-Bezel/Koko-AIO shaders bundle bezel+CRT in one slang preset but need decorations set
  to NONE and more GPU — **[verify]** they're viable on the Pi 5; the shader+Bezel-Project
  combo is the safer Pi path.)

Sources: <https://wiki.batocera.org/decoration>

## Shipping this in the arcade pack

- A **custom shader set** (`/userdata/shaders/configs/iconic-crt/`) + saved per-game presets.
- A **custom decoration set** (`/userdata/decorations/iconic/`) with `default.png`/`.info`
  tuned to the 1080p RTD2662 4:3 window — **art is the user's**, we ship only the geometry/
  `.info` + instructions (ship-no-artifacts).
- `batocera.conf` snippets wiring `fbneo`/`flycast` systems to the shader + bezel.

## Process notes for Staff

Shader parameter naming is the real friction: the menu labels, the glsl preset tokens, and the
slang preset tokens differ, and the libretro docs don't publish one canonical GDV parameter
list. I mapped against the shader thread + gametech wiki and flagged exact tokens **[verify]**.
Final tuning is inherently a sit-at-the-cabinet job.
