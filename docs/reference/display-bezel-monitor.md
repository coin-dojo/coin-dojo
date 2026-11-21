# Display, virtual bezel & the special monitor

## The "4:3 arcade look" is software (virtual bezel + CRT shader)
Not a physical border. It's Iconic's `arcadia` video pipeline, persisted in
`~/.local/share/DeadSignalLabs/Arcadia20XX/video.cfg`, adjustable in the **in-game pause
menu**, with **per-game overrides** (keys like `video.sf2ceuc.*`, `video.hsf2.*`, `video.sfiii3ur1.*`).

Adjustable options (from `video.cfg`):
`fit_to_bezel`, `screen_border`, `integer_scaling` (+`_720p`), `enable_crt_shader`,
`scanlines`/`crt_scanlines`, `crt_curvature`, `crt_rgb_mask`, `crt_vignette`,
`crt_glow`/`crt_glow_enable`, `rgb_blur`/`crt_rgb_blur`, `rf_noise`, `rf_interference`.

### RetroArch/Batocera parity (Path B can match it, per-game)
| Iconic option | RetroArch equivalent |
|---|---|
| fit_to_bezel / screen_border | bezel overlay + aspect/custom viewport |
| integer_scaling | integer scale |
| enable_crt_shader + scanlines/curvature/rgb_mask/vignette/glow/blur/rf_* | `crt-guest-advanced` / `crt-royale` shader params |
| per-game overrides | RetroArch per-game config + shader presets |

## The special monitor (RTD2662 scaler)
- LCD driven by a **Realtek RTD2662** scaler. Panel native **1366x768**; Iconic runs the Pi at
  **1920x1080** (`assets/monitor/set-fhd.sh` edits `cmdline.txt` + the **kanshi** Wayland output config).
- Monitor **firmware lives in the monitor's own SPI flash**, flashed over **I2C (DDC, bus 13)** by
  `assets/monitor/flash.py` (a standalone RTD2662 flasher; `setup.sh` installs `i2c-tools`/`smbus`).
  `firmware.bin` = 512KB.
- **No per-boot monitor init.** Boot = labwc (Wayland) autostart: `swaybg` splash + the launcher.
  The monitor just works as a 1080p HDMI display.

### OS-swap impact (does the monitor block Batocera/RetroPie?)
- **No.** The firmware is in the monitor, untouched by swapping the SD card. A new OS only needs to
  **force 1080p HDMI output** (the panel's EDID defaults to 1366x768). The I2C flash tool is only for
  *updating* monitor firmware and is portable (Python + i2c-tools) if ever needed.
- **Verify on real hardware**: boot a spare-card Batocera and confirm the panel accepts 1080p cleanly.

## Display stack (stock)
labwc (Wayland) + kanshi (both HDMI outputs @ 1920x1080) + swaybg splash; the GTK2/X11 launcher
runs via Xwayland; `arcadia` (SDL2+GL) renders games with the CRT/bezel pipeline.
