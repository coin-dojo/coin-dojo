# Batocera on the Pi 5 — image, first boot, SSH, 1080p, /userdata

Base OS for the arcade pack (see [[direction]]). Batocera is open-source and redistributable,
but we ship **config/theme only, no image and no artifacts**. This doc is how *you* put
Batocera on your own card and reach the parity baseline on a cabinet like the Iconic SF2.

> Verify-on-hardware items are marked **[verify]**. The cabinet's monitor is a Realtek
> RTD2662 scaler whose panel is natively 1366x768 but which Iconic drives at 1080p
> (see [[display-bezel-monitor]]) — the one real unknown here is whether its EDID offers a
> clean 1080p mode to a fresh OS.

## 1. Get and flash the image

- Batocera publishes a **separate image per Raspberry Pi model**; the Pi 5 image is the
  `bcm2712` build. Download from <https://batocera.org/download> (pick Raspberry Pi 5).
  The Pi 5 build has been a current target since Batocera v38/v39-era releases.
- The Pi 5 image is **not** in Raspberry Pi Imager's bundled OS list. Flash it as a *custom
  image*: Raspberry Pi Imager → "Use custom", or **Balena Etcher**, onto the SD card (or
  USB/NVMe). The image is `.img.gz` — Imager/Etcher decompress it on the fly.
- Batocera **auto-expands** the data partition to fill the card on first boot; use a large
  card (64 GB+) since ROMs/BIOS/artwork live on the same card unless you mount a share.

Sources: <https://wiki.batocera.org/install_batocera>, <https://batocera.org/download>

## 2. First boot

- Plug in **HDMI, the Pico controller, and Ethernet** (wired is strongly preferred for
  netplay — see [[netplay]]), then power on. First boot expands the filesystem and can take
  a couple of minutes.
- Batocera boots straight into its EmulationStation front-end (boot-to-frontend; no login
  chain like the stock Iconic OS). No systems show in the menu until their ROM folders have
  games (see [[cores-and-bios]] / [[frontend-theme]]).
- Set the display first if the picture is wrong (next section), then controller
  (see [[controller-batocera]]).

## 3. SSH access

- **SSH is enabled by default.** User is **`root`** (the only account); default password is
  **`linux`**. Reach it at `ssh root@batocera.local` (or `ssh root@<ip>`; the IP is under
  **MAIN MENU → NETWORK SETTINGS**).
- The toggle lives in `/userdata/system/batocera.conf` as `system.ssh.enabled=1` (no leading
  `#`). It is also under **MAIN MENU → SYSTEM SETTINGS → SECURITY**.
- **Change the password:** turn on **Enforce Security** under **SYSTEM SETTINGS → SECURITY**
  and set a password (this password also gates Samba/network-share access). There is no
  documented `passwd` CLI route; use the UI.
- **Key-based auth:** Batocera's SSH daemon is **Dropbear**. Put your public key in
  `/userdata/system/.ssh/authorized_keys` (that path *is* `~/.ssh/authorized_keys` since
  `~` = `/userdata/system`). The data partition must be **Linux-native (ext4/btrfs)** for
  this to persist. Note: **`ssh-copy-id` does not work with Dropbear** — append the key
  manually (`cat id.pub | ssh root@batocera.local 'cat >> /userdata/system/.ssh/authorized_keys'`).

Sources: <https://wiki.batocera.org/access_the_batocera_via_ssh>

## 4. Force 1080p HDMI output (our RTD2662 case)

The panel's EDID may default a fresh OS to 1366x768 (the panel's native res) rather than the
1920x1080 Iconic uses. Batocera's resolution controls:

- **Primary (v31+):** edit `/boot/batocera-boot.conf` and set
  ```
  es.resolution=max-1920x1080
  ```
  This caps output at 1080p. (On v29/v30 the key was `system.es.maxresolution=1920x1080`.)
- **Exact mode:** any mode the display reports can be forced, e.g.
  `es.resolution=1920x1080.60`. List what the panel advertises over SSH with
  `batocera-resolution listModes`. If 1080p is **not** in that list, the EDID isn't offering
  it — see EDID override below.
- **In-menu:** **MAIN MENU → GAME SETTINGS → VIDEO MODE** (per-emulator) and the ES video
  mode setting also exist; `batocera-boot.conf` sets the front-end/boot resolution.
- **EDID override [verify]:** if the panel reports a bad/limited EDID, the Pi can be told to
  ignore it. On legacy-firmware Pis this was `hdmi_ignore_edid=0xa5000080` + `hdmi_group=1` +
  `hdmi_mode=...` in `config.txt`. The **Pi 5 uses the KMS stack**, where those legacy
  `hdmi_*` keys may be ignored; prefer `es.resolution` first, and if a forced mode is still
  needed use a `video=HDMI-A-1:1920x1080@60` kernel cmdline argument. **Confirm on the actual
  cabinet** which path the RTD2662 needs — this is the single display risk in the OS swap.
- `/boot` is normally mounted read-only; remount to edit: `mount -o remount,rw /boot`, edit,
  then `batocera-save-overlay` is **not** needed for `/boot` (it's a real FAT partition), but
  reboot to apply.

Sources: <https://wiki.batocera.org/display_issues>, [[display-bezel-monitor]]

## 5. The `/userdata` folder layout

Batocera has two partitions: a small read-only **BOOT/system** partition (the OS squashfs,
`/boot/batocera-boot.conf`, `config.txt`) and a large writable **SHARE** partition mounted at
**`/userdata`** that holds everything you change. Config *persists because it lives on
`/userdata`* — the OS image itself is read-only and swapping/upgrading it keeps `/userdata`.

| Path | Holds |
|---|---|
| `/userdata/roms/<system>/` | games, one folder per system (`fbneo`, `dreamcast`, `psx`, `snes`…). A system only appears in ES once it has ROMs. Each folder has an `_info.txt` listing accepted extensions. |
| `/userdata/bios/` | BIOS/firmware, some in subfolders (`bios/dc/`, `bios/neocd/`). See [[cores-and-bios]]. |
| `/userdata/saves/` | in-game saves + save states (per system) |
| `/userdata/cheats/` | cheat files |
| `/userdata/decorations/` | bezels/overlays (The Bezel Project + custom). See [[crt-bezel-setup]]. |
| `/userdata/shaders/` | custom shader `.glslp`/`.slangp` presets and custom shader *sets* (`shaders/configs/<set>/`) |
| `/userdata/themes/` | front-end themes. See [[frontend-theme]]. |
| `/userdata/music/` | front-end background music |
| `/userdata/splash/` | custom boot splash |
| `/userdata/screenshots/` | screenshots |
| `/userdata/system/` | **all config** (see below) |

Inside `/userdata/system/`:

- `batocera.conf` — the main config file (all `global.*`, `<system>.*`, per-game keys).
- `.ssh/authorized_keys` — your SSH public key.
- `configs/emulationstation/es_settings.cfg` — ES settings.
- `configs/emulationstation/es_input.cfg` — controller mappings (see [[controller-batocera]]).
- `configs/emulationstation/collections/` — custom/dynamic collections (`.cfg`, `.xcc`).
- `.config/retroarch/retroarchcustom.cfg` — RetroArch main config.
- `configs/retroarch/cores/retroarch-core-options.cfg` — per-core options.
- `.config/retroarch/config/` and `.../config/remaps/` — per-core overrides and input remaps.

> Directory-name note: the wiki shows RetroArch config under both `.config/retroarch/` and
> `configs/retroarch/` in different places — **[verify]** the exact paths on your build before
> scripting edits; prefer editing through the ES menus where possible.

### How config persists / how to edit

- Everything under `/userdata` survives reboots and OS-image upgrades (it's its own
  partition). **Back up `/userdata` before big changes** — e.g. tar it to the NAS.
- Edit `batocera.conf` over SSH or via **MAIN MENU → SYSTEM SETTINGS → ... → EDIT batocera.conf**.
  After hand-editing, run `batocera-save-overlay` only if your build uses an overlay for the
  system partition; `/userdata` edits are written directly.

Sources: <https://wiki.batocera.org/add_games_bios>,
<https://wiki.batocera.org/batocera.linux_architecture>,
<https://wiki.batocera.org/notable_files>

## Our-cabinet checklist (flash a spare card first — reversible)

1. Flash Batocera Pi 5 to a **spare** card (keep the Iconic card imaged on the NAS).
2. Boot on the cabinet; confirm **1080p** on the RTD2662 (section 4).
3. `ssh root@batocera.local`, drop the maintainer's key, enable Enforce Security password.
4. Map the Pico stick ([[controller-batocera]]).
5. Apply the arcade pack: BIOS ([[cores-and-bios]]), CRT+bezel ([[crt-bezel-setup]]),
   theme ([[frontend-theme]]), netplay ([[netplay]]).

## Process notes for Staff

The Batocera wiki is well-organised but several topic pages I expected (`notable_files_folders`,
`batocera_and_bios`) are empty placeholders — the real content is under slightly different slugs
(`notable_files`, `add_games_bios`, `batocera.linux_architecture`), which cost a couple of extra
fetches. The one genuine unknown is the **RTD2662 EDID + Pi 5 KMS** 1080p path — legacy
`config.txt hdmi_*` overrides may not apply on the Pi 5, so I led with `es.resolution` and
flagged the EDID override **[verify on hardware]**. That boot-a-spare-card test is the gating
step for the whole OS swap.
