# Cabinet hardware profile (Iconic SF2, Pi 5)

Learned from the stock image. Confirm live details via SSH once the custom card is in.

## Board / GPU
- Raspberry Pi 5 (bcm2712). GPU via `dtoverlay=vc4-kms-v3d` (KMS/GLES on hardware).
- **RAM: unknown from the image** — read with `free -h` / `/proc/meminfo` over SSH. Likely 2GB.

## Display
- Panel **1920x1080** HDMI (`video=HDMI-A-1:1920x1080`), game/UI area ~**4:3 (1500x1125) centered**
  behind Iconic's physical **bezel** (see [[bezel-4-3]]). Standard monitor (EDID; `/usr/bin/decode-edid`
  present, no forced EDID). Any OS detects it at 1080p.

## Controller — Raspberry Pi Pico HID gamepad
- The stick + buttons are a **Pico/RP2040 USB HID gamepad** with custom firmware.
- Two hardware variants: old `081f:e401`, new `2e8a:112d` (2e8a = Raspberry Pi).
- Firmware: `firmware.uf2` (bundled at `usr/lib/launcher/assets/joystick/firmware.uf2`),
  flashed by `usr/lib/pico-arcade-controller/update.sh` (picocom 1200-baud touch → BOOTSEL →
  copy uf2 to the RPI-RP2 drive).
- **Presents as a standard USB joystick (`/dev/input/js0`)** — so it works on any Linux
  (Batocera/RetroPie included); just needs button-mapping in the new front-end.

## Boot / autostart (stock OS)
- Raspberry Pi OS desktop: `getty@tty1` **autologin** (user `coindojo`) → LXDE session
  (`/etc/xdg/lxsession/LXDE-pi`) → the launcher AppImage autostarts.
- The launcher is a Lazarus/GTK2/X11 app; it shells out to `arcadia` (SDL2+GL libretro
  front-end; `coindojo`/FBNeo + `flycast` cores) to run games and the live showcase.
- A replacement OS (Batocera/RetroPie) swaps this whole chain for boot-to-front-end.

## Implication for the "swap OS" option
- Controller + display + boot are all **standard** — a stock emulation OS will run on this
  hardware. Fully reversible: keep the original card imaged, swap in a new card to try.
