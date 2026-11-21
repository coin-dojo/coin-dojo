# Controller in Batocera — the Pico HID stick

The cabinet's stick+buttons are a **Raspberry Pi Pico / RP2040 USB HID gamepad** (see
[[hardware-profile]]): two variants, **new `2e8a:112d`** and **old `081f:e401`**. It presents
as a **standard USB joystick (`/dev/input/js0`)**, so Batocera handles it like any generic
gamepad — one mapping, then every core inherits it.

## One-time mapping

- Plug it in. If a controller has **no mapping yet, Batocera auto-opens the configuration
  screen**. Otherwise: **MAIN MENU → CONTROLLER SETTINGS → CONFIGURE A CONTROLLER**.
- **Leave sticks/buttons neutral** before you start — Batocera reads their current state as the
  resting position.
- Walk through each prompt, **holding** the button/direction it asks for. Missing a
  button/axis? **hold any already-mapped button** to skip it.
- One mapping per controller; done once, it applies across EmulationStation **and all
  RetroArch cores** (Batocera translates the ES mapping into each emulator's input at launch —
  this is the "one global map covers all cores" property we want).

## Where the mapping lives

- **`/userdata/system/configs/emulationstation/es_input.cfg`** — holds **all** controller
  configs; a newly mapped pad is appended at the bottom as an `<inputConfig>` block.
- **`/userdata/system/configs/emulationstation/es_last_input.cfg`** — holds **only the most
  recently configured controller** (handy for grabbing exactly what the cabinet produced).
- RetroArch input is **derived at game launch** from `es_input.cfg` — Batocera doesn't rely on
  the user dropping RetroArch `autoconfig` profiles the way standalone RetroArch does. (Per-core
  tweaks still live under `/userdata/system/.config/retroarch/config/…/remaps/` if needed.)

An `es_input.cfg` entry is keyed by **`deviceGUID`** (an SDL joystick GUID encoding
bus/vendor/product/version) and **`deviceName`**, e.g.:

```xml
<inputConfig type="joystick" deviceName="…Pico Arcade…" deviceGUID="03000000…">
  <input name="a"     type="button" id="1" value="1"/>
  <input name="b"     type="button" id="0" value="1"/>
  <input name="up"    type="axis"   id="1" value="-1"/>
  …
</inputConfig>
```

The VID/PID (`2e8a:112d`, `081f:e401`) are embedded in that `deviceGUID`.

## Shipping a prebuilt mapping (two ways)

**Reliable — capture and ship.** Map the stick once on the actual cabinet, then grab the exact
block it produced:

```
ssh root@batocera.local 'cat /userdata/system/configs/emulationstation/es_last_input.cfg'
```

Ship that `<inputConfig>` block as part of the arcade pack; the setup step appends it to
`es_input.cfg` so the stick is pre-mapped on first boot. Do this for **both** VID/PID variants
(`2e8a:112d` and `081f:e401`) so either hardware revision is covered. This is **[verify on
hardware]** but guaranteed correct because the GUID/name come from the real device.

**Best-effort — synthesize the GUID.** The SDL GUID can be computed from bus+VID+PID+version,
but name/axis layout still vary, so a captured block is safer. Don't guess the GUID into the
repo; capture it.

**Upstream it, too.** Batocera's default controller database is
`package/batocera/emulationstation/batocera-emulationstation/controllers/es_input.cfg` on
GitHub — submitting the Pico stick's mapping there (or via their Discord) makes it recognised
out-of-the-box for everyone, which fits the "ship the method" goal.

## Shipping this in the arcade pack

- A captured `<inputConfig>` block per VID/PID variant + a setup step that appends it to
  `/userdata/system/configs/emulationstation/es_input.cfg`.
- Fallback instructions: if the pad isn't recognised, run **CONFIGURE A CONTROLLER** once.
- **No firmware shipped** — the Pico firmware is the user's/vendor's; we only ship the Batocera
  mapping (ship-no-artifacts).

Sources: <https://wiki.batocera.org/configure_a_controller>,
<https://wiki.batocera.org/notable_files>

## Process notes for Staff

The Batocera wiki documents the mapping flow and file locations but **not** the es_input.cfg
GUID encoding or how to preload a mapping — I derived the "capture es_last_input.cfg and ship
the block" approach, which is both reliable and repo-safe (no guessed GUIDs committed). The
only hardware dependency is one physical mapping pass to capture the exact block for each of
the two Pico VID/PID revisions.
