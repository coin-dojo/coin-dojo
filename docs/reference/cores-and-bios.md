# Cores & BIOS for the 5-star target systems

Which Batocera core runs each target system (from the [[emulator-matrix]] 5-star set) and
**which BIOS files it needs** — exact filenames and where they go under `/userdata/bios/`.

**We ship NO BIOS** (ship-no-artifacts, see [[direction]]). The community supplies their own
legally-dumped BIOS. This doc only tells them *what goes where*.

## How Batocera checks BIOS

- **MAIN MENU → SYSTEM SETTINGS → MISSING BIOS** shows every required/optional BIOS, its
  **expected path** and **md5sum**, per system. "Not all 'missing' BIOS are required" — most
  cores run without the optional ones.
- Over SSH: `batocera-systems` prints the same report (takes a minute or two).
- Full reference list ships at `/userdata/bios/readme.txt`. The data behind it is Batocera's
  `batocera-systems` script on GitHub.
- All BIOS live under **`/userdata/bios/`**, some in subfolders (`bios/dc/`, `bios/neocd/`).

Sources: <https://wiki.batocera.org/add_games_bios>, <https://wiki.batocera.org/systems:fbneo>

## Per-system table

| System | ROM folder | Recommended core | BIOS required? | BIOS files (path under `/userdata`) |
|---|---|---|---|---|
| **CPS1** | `roms/fbneo` | **FBNeo** | **No** | Self-contained romsets; no separate BIOS. |
| **CPS2** | `roms/fbneo` | **FBNeo** | **No** | Self-contained romsets; no separate BIOS. |
| **CPS3** (`sfiii3`…) | `roms/fbneo` | **FBNeo** | **No** (bundled) | The CPS3 boot ROM ships *inside* the parent romset (e.g. `sfiii*`); no user-supplied file in `bios/`. **[verify]** on your romset. |
| **Neo Geo** | `roms/fbneo` or `roms/neogeo` | **FBNeo** | **Yes** | `bios/neogeo.zip` (MVS/AES/Unibios inside). FBNeo also reads it from `roms/fbneo`. |
| **Neo Geo CD** | `roms/neogeocd` | **FBNeo** | **Yes** | `bios/neocdz.zip` (and/or `bios/neocd/*` ROMs: `neocd_z.rom` etc). |
| **NAOMI** | `roms/naomi` | **Flycast** | **Yes** | `bios/dc/naomi.zip` |
| **Atomiswave** | `roms/atomiswave` | **Flycast** | **Yes** | `bios/dc/awbios.zip` |
| **Dreamcast** | `roms/dreamcast` | **Flycast** | **Yes** | `bios/dc_boot.bin` + `bios/dc_flash.bin` |
| **PlayStation 1** | `roms/psx` | **Beetle PSX** (accuracy) or **PCSX-ReARMed** (light) | **Recommended** | One region BIOS: `bios/scph5500.bin` (JP), `bios/scph5501.bin` (US), `bios/scph5502.bin` (EU). Also accepted: `scph1001.bin`, `scph101.bin`, `scph7001.bin`, `psxonpsp660.bin`. Beetle PSX **needs** a BIOS; PCSX-ReARMed can HLE-boot without one but BIOS is better. |
| **PSP** | `roms/psp` | **PPSSPP** | **No** | None. PPSSPP has a built-in HLE firmware. |
| **NES / Famicom** | `roms/nes` | **Nestopia** / **FCEUmm** | **No** | None. (FDS disk games need `bios/disksys.rom`.) |
| **SNES** | `roms/snes` | **snes9x** (Pi-friendly) / **bsnes** | **No** | None. (Optional add-on chips are in-cart.) |
| **Game Boy / GBC** | `roms/gb`, `roms/gbc` | **Gambatte** | **No** | None required. Optional boot-logo ROMs: `bios/gb_bios.bin`, `bios/gbc_bios.bin` (cosmetic). |
| **Game Boy Advance** | `roms/gba` | **mGBA** | **No** (optional) | Optional `bios/gba_bios.bin` for accuracy/intro; mGBA has a built-in HLE BIOS. |
| **Genesis / Mega Drive** | `roms/megadrive` | **Genesis Plus GX** | **No** | None. (Sega CD needs `bios/bios_CD_*.bin`; 32X none.) |

## Summary — the only hard BIOS requirements for the 5-star set

Community members must supply **just these** to match the Iconic playability baseline:

- `bios/neogeo.zip` — Neo Geo (FBNeo)
- `bios/dc/naomi.zip` — NAOMI (Flycast)
- `bios/dc_boot.bin` + `bios/dc_flash.bin` — Dreamcast (Flycast)
- A PS1 region BIOS, e.g. `bios/scph5501.bin` — strongly recommended (Beetle PSX requires one)

**No BIOS needed** for CPS1/CPS2/CPS3, PSP, NES, SNES, GB/GBC, GBA, Genesis. Optional extras
(`gba_bios.bin`, FDS `disksys.rom`, Neo Geo CD, Atomiswave, Sega CD) only if those systems are
added.

## Notes

- **Core selection** is per-system in **GAME SETTINGS → EMULATOR / CORE** (or
  `<system>.core=<core>` in `batocera.conf`). The table lists Batocera's sensible Pi-5
  default; FBNeo/Flycast are the ones that matter for this cabinet.
- **Verify md5s** against the MISSING BIOS report — wrong-region or bad dumps are the #1
  "game won't launch" cause. CHDs for Dreamcast/NAOMI/PSX are separate from BIOS.
- If you run a RomM server (optional), it can hold the BIOS files per platform too — useful if syncing a
  library from it.

## Process notes for Staff

none — BIOS requirements were well-documented on the Batocera wiki and cross-checked against
a RomM server's BIOS lists. The only soft spot is CPS3 (bundled-vs-separate boot ROM),
flagged **[verify]** against an actual romset on hardware.
