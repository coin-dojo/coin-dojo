# Two-player support on the cabinet

**Good news: P2 works automatically.** No per-player, per-game setup needed.

## What the cabinet actually does
The cabinet's control panel presents **two identical HID gamepads** — one per player — not a
single combined device. Evidence from the stock image's `flycast/emu.cfg`:
```
maple_sdl_joystick_0 = 0      # SDL joystick 0 -> Dreamcast/NAOMI port 0  (Player 1)
maple_sdl_joystick_1 = 1      # SDL joystick 1 -> port 1                  (Player 2)
device1 = 0 ...               # port 0 controller configured
device2 = 0 ...               # port 1 controller configured
```
Both enumerate as **`USB gamepad`** (VID `0x081f` / PID `0xe401`), the same identity we
baked the global profile for (see [[cabinet-controller]]). MAME per-game configs confirm P2
inputs exist (e.g. `forgottnuaa.cfg` has `P2_BUTTON1`, `P2_DIAL`).

## Why it just works in RetroArch
RetroArch's `udev` input driver enumerates joysticks in device order and **auto-assigns**
pad-1 → Player 1, pad-2 → Player 2. Because both pads share the name/VID/PID, our single
autoconfig `USB gamepad.cfg` applies to **both**, so each player gets the identical 6-button
map. We already set `input_max_users = 4` in `retroarch.cfg`. Nothing per-game.

## What to verify on the cabinet (once SSH is up)
```
cat /proc/bus/input/devices | grep -A4 -i "usb gamepad"   # expect TWO Gamepad entries -> js0 + js1
```
or in RetroArch: Settings → Input → Port 1/2 Controls should both show "USB gamepad" bound.
Then start a 2-player game (e.g. a beat 'em up, SF2, a versus fighter) and confirm P2 moves.

## Gotchas to watch
- **Port order can swap** if the two pads enumerate in a different order across boots. If P1/P2
  ever come up reversed, pin them with RetroArch's `input_playerN_joypad_index` or a udev rule
  keying on the physical USB path (`KERNELS==".../usb.../1-1.2..."`). Not expected, but note it.
- **ES-DE menu nav** only uses P1 — fine; the SDL mapping we ship covers navigation.
- Both pads must be mapped identically (they are — same autoconfig), so Coin/Start work for
  each player independently (btn 8 / btn 9 on each pad).

## Still open (confirm on hardware)
Whether the second stick's **button index order** matches P1 exactly (it should — same
firmware). Verify both players' punch/kick rows feel right in one session; if P1's row order
needs the one-line swap, apply the same to the shared autoconfig and both players inherit it.
