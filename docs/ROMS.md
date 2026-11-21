# Games and BIOS files: where they go

Nothing here ships a game or a BIOS file. You bring your own, "dumped": copied yourself from cartridges, discs or
arcade boards you own. A BIOS is a small start-up file copied from a console; only some systems need one. This page
says which folder each file goes in, how to name it, and which version each emulator expects.

**For players:** copy everything through the [network folder](#copy-over-the-network-mac-or-windows): drag files in
Finder (Mac) or File Explorer (Windows). The short folder list is in the [start guide](START.md), step 5. The rest of
this page is detail you only need when a game will not start.

Technical: the files live on the SD card's `data` partition, mounted on the cabinet as `/data`. A redeploy never
touches it. Builders can also copy over SSH (`scp`, `rsync`) or mount the `data` partition (ext4: Linux reads it
directly).

## Copy over the network (Mac or Windows)

The cabinet shares exactly two folders on your home network (#266): **`roms`** (`/data/roms`) and **`bios`**
(`/data/bios`). Nothing else on the cabinet can be reached this way. It is on in the community image
(`hk_share_enable`, [CONFIG.md](CONFIG.md#hk_share_enable)).

- **Address:** `coindojo.local` (your cabinet's name, `hk_cabinet_hostname`, plus `.local`). If the name is not
  found, use the cabinet's IP address from your router.
- **Login:** the cabinet user (`hk_cabinet_user`; `coindojo` on a community card) and the cabinet password. A new community card
  uses the default password `12345`: change it (below).

**Mac (Finder).** Finder, menu **Go, Connect to Server** (Cmd+K), type `smb://coindojo.local`, **Connect**. Choose
**Registered User**, enter the user and password, then pick `roms` (or `bios`). The cabinet also shows up in the
Finder sidebar under **Locations**. Drag your files into the system's folder, e.g. `roms/snes/`.

**Windows (File Explorer).** Click the address bar, type `\\coindojo.local\roms` and press Enter. Enter the user
and password when asked (tick **Remember my credentials** if you like). Drag your files into the system's folder.

**Change the password in SETTINGS > PASSWORD.** The network folder keeps its own copy of the password, and
Linux cannot pass it on. SETTINGS > PASSWORD (the last entry on the carousel) sets both at once: the cabinet
login and the folder's password. A `passwd` over SSH changes only the cabinet password: the folder then has no
login (the default `12345` stops working on the network as soon as the cabinet password is no longer `12345`)
until you set the password again in SETTINGS > PASSWORD.

**Blocked in `bios`:** a `mame` folder and `*.lua` files (MAME reads ini files, plugins and Lua scripts there, which
could run code on the cabinet); normal BIOS files copy as usual. Copy MAME's own files over SSH (`hk_share_bios_veto`). **Blocked in `roms`:**
`*.cmd` (MAME command files), `*.lua` and folders named `mame`; no game uses these (`hk_share_roms_veto`).

**Technical: what it is.** Samba (SMB3 only, encrypted when the computer supports it, no guest login) and avahi for the
`.local` name, from Debian. It answers only on the cabinet's own network connections (Ethernet `eth0`, Wi-Fi
`wlan0`) and only to computers on the same local network as the cabinet (the subnet those connections have right
now, plus IPv6 link-local); another network, even a private one, is refused. Files you copy belong to the cabinet
user (files `0644`, folders `0755`). Symlinks inside the folders are not followed. Turn it off with
`hk_share_enable: false` in your local config, then deploy.

## Contents

- [Copy over the network (Mac or Windows)](#copy-over-the-network-mac-or-windows)
- [The data partition](#the-data-partition)
- [Systems and folders](#systems-and-folders)
- [File name rules](#file-name-rules)
- [Arcade romsets: FBNeo, MAME and Fightcade](#arcade-romsets-fbneo-mame-and-fightcade)
- [BIOS files](#bios-files)
- [After you copy](#after-you-copy)

## The data partition

| Folder | What | Who writes it |
|---|---|---|
| `/data/roms/<system>/` | your games, one folder per system (table below) | you |
| `/data/bios/` | BIOS files for RetroArch cores (RetroArch's `system_directory`) | you |
| `/data/bios/dc/` | Dreamcast, NAOMI and Atomiswave BIOS for flycast | you |
| `/data/saves/` | in-game saves (RetroArch `savefile_directory`) | the emulators |
| `/data/states/` | save states (RetroArch `savestate_directory`) | the emulators |
| `/data/media/` | box art, screenshots, videos, marquees (ES-DE's media folder) | ES-DE's scraper, or you |
| `/data/nested/` | the menu tree the main carousel points at (MUST PLAY, genres, decades) | the deploy; don't edit |
| `/data/coin-dojo/` | the menu builder's workspace and the SETTINGS app | the deploy |
| `/data/emulators/` | RetroArch 1.22.2, the GLES N64 core, standalone flycast | the deploy |
| `/data/fightcade/opt/` | Fightcade, Wine, Box64, Electron (about 1.3 GB, #208) | the deploy |
| `/data/fightcade/prefix/` | the base Wine prefix (about 540 MB, #208) | the deploy |
| `/data/fightcade/roms-extra/` | Fightcade-only sets and the lobby's own downloads | you, or the Fightcade lobby |
| `/data/fightcade/fbneo/` | FBNeo's config, recordings, screenshots | Fightcade |
| `/data/fightcade/profile/`, `/data/fightcade/logs/` | the lobby's login session, logs (both private, mode 0700) | Fightcade |

Every path above comes from a config variable (`hk_data_root`, `hk_rom_root`, `hk_bios_root`, `hk_media_root`,
`hk_fightcade_data_dir`): [CONFIG.md](CONFIG.md).

## Systems and folders

The folder name is the ES-DE system name. The emulator is the one the launcher uses
(`skills/collections/launch.py`, `CORE_BY_SYSTEM`).

| Folder (`/data/roms/...`) | System | Emulator | BIOS |
|---|---|---|---|
| `arcade` | Arcade (MAME sets) | RetroArch, MAME core | per game, in the set |
| `fbneo` | Arcade (FBNeo sets) | RetroArch, FBNeo core | per game, in the set |
| `cps1` | CP System I (CPS1) | RetroArch, FBNeo core | none |
| `cps2` | CP System II (CPS2) | RetroArch, FBNeo core | none |
| `cps3` | CP System III (CPS3) | RetroArch, FBNeo core | in the parent set |
| `neogeo` | Neo Geo MVS | RetroArch, FBNeo core | `neogeo.zip` |
| `naomi` | Sega NAOMI | standalone flycast 2.7 | `dc/naomi.zip` |
| `dreamcast` | Sega Dreamcast | standalone flycast 2.7 | `dc/dc_boot.bin`, `dc/dc_flash.bin` |
| `atari2600` | Atari 2600 | RetroArch, Stella 7.0 core | none |
| `nes` | NES / Famicom | RetroArch, Nestopia core | none |
| `snes` | SNES / Super Famicom | RetroArch, Snes9x core | none |
| `genesis` | Mega Drive / Genesis | RetroArch, Genesis Plus GX core | none |
| `segacd` | Mega-CD / Sega CD | RetroArch, Genesis Plus GX core | `bios_CD_U.bin` / `_E` / `_J` |
| `pcengine` | PC Engine / TurboGrafx-16 | RetroArch, Beetle PCE core | CD games: `syscard3.pce` |
| `n64` | Nintendo 64 | RetroArch, Mupen64Plus-Next (GLES build) | none |
| `psx` | PlayStation | RetroArch, PCSX-ReARMed core | one `scph*.bin` (recommended) |
| `saturn` | Sega Saturn | RetroArch, Beetle Saturn core | `sega_101.bin` (JP), `mpr-17933.bin` (US/EU) |

The CRT look (crt-pi) is on for every RetroArch core except N64 and PlayStation (`hk_crt_off_cores`).

## File name rules

- **Arcade (`arcade`, `fbneo`, `cps1`, `cps2`, `cps3`, `neogeo`, `naomi`): never rename the zip.** The emulator
  finds a game by its set name: `sfiii3nr1.zip` works, `Third Game (Japan).zip` does not. Keep the zip as the
  romset made it; do not unpack it.
- **Clones need their parent.** A clone set holds only the files that differ. Put the parent zip in the same
  folder (or, for Fightcade, in any of its ROM folders, see below).
- **Consoles:** any file name works; ES-DE shows the name without the extension. Zipped cartridge games
  (`.zip`, `.7z`) are fine for the 8- and 16-bit systems.
- **Disc games** (`segacd`, `pcengine` CD, `psx`, `saturn`, `dreamcast`): `.chd` is the smallest and works for all
  of them. A game with several files (`.cue` + `.bin`, `.gdi` + tracks) keeps all its files in one folder, with
  their original names.
- **Multi-disc games:** an `.m3u` playlist listing the discs, one per line.
- No spaces or special characters are needed anywhere, but they are allowed in console file names.

## Arcade romsets: FBNeo, MAME and Fightcade

Arcade sets change between emulator versions: a zip made for one version may be missing a file in another.
Match the set to the emulator.

| Emulator | Reads | Set version to match |
|---|---|---|
| RetroArch FBNeo core (`fbneo`, `cps1`, `cps2`, `cps3`, `neogeo`) | `/data/roms/<system>/` | the current FBNeo set (the core is a recent FBNeo build) |
| RetroArch MAME core (`arcade`) | `/data/roms/arcade/` | the MAME version of the installed core: RetroArch shows it under Information, Core Information |
| Fightcade's own FBNeo | `fightcade` first, then the six folders below + `roms-extra` | **Fightcade's FBNeo set** (FBNeo 0.2.97.44, #92 spike, 2026-11-17) |

**Fightcade has its own folder, then reads your existing ones.** Its FBNeo looks in `/data/roms/fightcade` first
(#298: in the network folder as `roms/fightcade`; not a game menu system, so the menu shows no duplicates), then
`/data/roms/arcade`, `fbneo`, `cps1`, `cps2`, `cps3`, `neogeo` and then `/data/fightcade/roms-extra` (#204, #208).
Put Fightcade's version of a set in `roms/fightcade` when it differs from the game menu's; a set that works for both
stays once in its arcade folder. A clone and its parent go in the same folder.

**Example.** Fightcade's room for the CPS3 fighter used in every benchmark here launches `sfiii3nr1`. That is a
clone: Fightcade needs **`sfiii3nr1.zip` and its parent `sfiii3.zip`**, both FBNeo sets. Put both in
`/data/roms/fightcade/`. If a room says the ROM is missing, check the set
name it asks for and that its parent is present.

**Which games fit a stick and six buttons.** Of 594 arcade zips matched against Fightcade's FBNeo set, 554 were
complete and exact, 10 incomplete and 30 not an FBNeo arcade set; filtered to games whose player 1 has a stick,
no analog control and at most six buttons, 532 remained (#92, #204, 2026-11-17). Method:
[reference/fightcade-on-pi5.md](reference/fightcade-on-pi5.md#roms-shared-folders-and-the-stick--6-button-filter).

## BIOS files

Name them exactly as below: the emulators look for the file name, then (most of them) check the md5, a
fingerprint of the file. Checking it yourself is optional: `md5sum <file>` (Linux) or `md5 <file>` (macOS). A wrong md5 usually means another region or a bad
dump, the first thing to check when a game will not start. The md5s are copied from each core's libretro page
(read 2026-11-19, links in [RESOURCES.md](RESOURCES.md)); "not listed" means the upstream page gives none.

| File | Folder | For | md5 |
|---|---|---|---|
| `neogeo.zip` | `/data/roms/neogeo/` | Neo Geo (FBNeo, Fightcade) | not listed (a romset: match the FBNeo version) |
| `dc_boot.bin` | `/data/bios/dc/` | Dreamcast (flycast) | `e10c53c2f8b90bab96ead2d368858623` |
| `dc_flash.bin` | `/data/bios/dc/` | Dreamcast (flycast) | not listed |
| `naomi.zip` | `/data/bios/dc/` | NAOMI (flycast) | not listed (a MAME BIOS set) |
| `naomi2.zip`, `awbios.zip` | `/data/bios/dc/` | NAOMI 2, Atomiswave (optional; no folder in the menu yet) | not listed |
| `scph5501.bin` | `/data/bios/` | PlayStation (US) | `490f666e1afb15b7362b406ed1cea246` |
| `scph1001.bin` | `/data/bios/` | PlayStation (US, older) | `924e392ed05558ffdb115408c263dccf` |
| `scph7001.bin` | `/data/bios/` | PlayStation (US) | `1e68c231d0896b7eadcad1d7d8e76129` |
| `scph101.bin` | `/data/bios/` | PlayStation (PS one) | `6e3735ff4c7dc899ee98981385f6f3d0` |
| `sega_101.bin` | `/data/bios/` | Saturn, Japanese games | `85ec9ca47d8f6807718151cbcca8b964` |
| `mpr-17933.bin` | `/data/bios/` | Saturn, US and European games | `3240872c70984b6cbfda1586cab68dbe` |
| `bios_CD_U.bin` | `/data/bios/` | Sega CD (US) | `854b9150240a198070150e4566ae1290` |
| `bios_CD_E.bin` | `/data/bios/` | Mega-CD (Europe) | `e66fa1dc5820d254611fdcdba0662372` |
| `bios_CD_J.bin` | `/data/bios/` | Mega-CD (Japan) | `278a9397d192149e84e820ac621a8edd` |
| `syscard3.pce` | `/data/bios/` | PC Engine CD games | `38179df8f4ac870017db21ebcbf53114` |

PCSX-ReARMed runs without a BIOS too (it has its own), but games are more compatible with one. CPS1, CPS2, CPS3,
NES, SNES, Mega Drive, Atari 2600 and N64 need no BIOS file. The deploy links `/data/bios/dc/` into flycast's own
data folder, so the files stay in one place (role `dreamcast`).

## After you copy

1. **Show the new games.** On the game menu pick **QUIT TO MAIN MENU**, then **ARCADE**: the game menu looks for
   new games each time it starts.
2. **Box art and game info.** On the game menu press **START** and pick **SCRAPER**.
3. **Rebuild the game lists.** **SETTINGS**, **GAME LISTS**, **REBUILD GAME LISTS** builds MUST PLAY, genres, decades
   and series from your scraped games. Your own lists: [LISTS.md](LISTS.md).

For builders: a personal image's curated collections are rebuilt from the Mac with `make deploy-collections` (a dry
run; add `APPLY=1`), see [skills/collections/SKILL.md](../skills/collections/SKILL.md). Check every system still
starts a game: `make smoke-launch`.
