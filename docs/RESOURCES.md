# Resources: what it runs, versions, upstreams and licences

This project ships none of the software below. The image build and the deploy fetch each piece from its upstream
(pinned by version or sha256) or build it from pinned source, on your own computer. Each keeps its own licence.
Versions are the ones the cabinet ran on 2026-11-19; where a version follows the base image, the table says
so. The libretro cores are built from pinned commits (`build/<core>`, #274).

## Contents

- [The software stack](#the-software-stack)
- [Emulator cores](#emulator-cores)
- [Build and deploy tools](#build-and-deploy-tools)
- [Documentation upstream](#documentation-upstream)
- [This project's licences](#this-projects-licences)

## The software stack

| Piece | Version | What it does here | Upstream | Licence |
|---|---|---|---|---|
| Raspberry Pi OS Lite (64-bit) | Debian 13 (trixie) base | the operating system | <https://www.raspberrypi.com/software/operating-systems/> | Debian packages: each its own (mostly GPL) |
| Linux kernel | the base image's, 4 KB-page `kernel8.img` | see [TUNING.md](TUNING.md#4-kb-page-kernel) | <https://github.com/raspberrypi/linux> | GPL-2.0 |
| ES-DE | 3.5.0 (AppImage, aarch64) | the game menu | <https://es-de.org/>, <https://gitlab.com/es-de/emulationstation-de> | MIT |
| RetroArch | 1.22.2, built from source (GLES, Wayland) | runs every libretro core | <https://www.retroarch.com/>, <https://github.com/libretro/RetroArch> | GPL-3.0 |
| flycast (standalone) | 2.7, built from source (GLES) | Dreamcast, NAOMI | <https://github.com/flyinghead/flycast> | GPL-2.0 |
| cage | Debian 13 package | the one-app Wayland kiosk | <https://github.com/cage-kiosk/cage> | MIT |
| Xwayland | Debian 13 package | X11 apps (Electron, Wine) inside cage | <https://gitlab.freedesktop.org/xorg/xserver> | MIT/X11 |
| PulseAudio | Debian 13 package | sound over HDMI | <https://www.freedesktop.org/wiki/Software/PulseAudio/> | LGPL-2.1+ |
| NetworkManager | Debian 13 package | Wi-Fi and Ethernet | <https://networkmanager.dev/> | GPL-2.0+ |
| Fightcade | 2.1.45 (sha256-pinned download) | online play lobby and its FBNeo | <https://www.fightcade.com/> | proprietary freeware: its own terms |
| Electron | 9.1.0, official arm64 build | runs the Fightcade lobby natively | <https://www.electronjs.org/>, <https://github.com/electron/electron> | MIT |
| Box64 | 0.4.4 (commit `2f130fab`), built with `-DRPI5ARM64=ON` | runs x86_64 Linux programs on ARM | <https://github.com/ptitSeb/box64> | MIT |
| Wine | 10.0 amd64-wow64 (Kron4ek Wine-Builds) | runs Fightcade's Windows FBNeo | <https://www.winehq.org/>, <https://github.com/Kron4ek/Wine-Builds> | LGPL-2.1+ |
| crt-pi shader | libretro glsl-shaders, pinned commit | the CRT look | <https://github.com/libretro/glsl-shaders> | GPL-2.0+ |

Sources: versions from [README.md](../README.md), [ansible/README.md](../ansible/README.md), `make -C build help`
and [reference/fightcade-on-pi5.md](reference/fightcade-on-pi5.md) (#92, #207, #208).

## Emulator cores

| System(s) | Core | Version | Upstream | Licence |
|---|---|---|---|---|
| Arcade (FBNeo sets), CPS1-3, Neo Geo | FBNeo | pinned commit, built from source | <https://github.com/finalburnneo/FBNeo>, <https://docs.libretro.com/library/fbneo/> | non-commercial (FBNeo's own) |
| Arcade (MAME sets) | MAME | the installed core's | <https://www.mamedev.org/>, <https://github.com/libretro/mame> | GPL-2.0+ |
| Fightcade games | Fightcade's FBNeo | 0.2.97.44 romset | <https://www.fightcade.com/> | FBNeo's, plus Fightcade's terms |
| Dreamcast, NAOMI | flycast | 2.7 (standalone) | <https://docs.libretro.com/library/flycast/> | GPL-2.0 |
| PlayStation | PCSX-ReARMed | pinned commit, built from source | <https://docs.libretro.com/library/pcsx_rearmed/> | GPL-2.0 |
| Saturn | Beetle Saturn | pinned commit, built from source | <https://docs.libretro.com/library/beetle_saturn/> | GPL-2.0 |
| Mega Drive, Sega CD | Genesis Plus GX | pinned commit, built from source | <https://docs.libretro.com/library/genesis_plus_gx/> | non-commercial (its own) |
| PC Engine | Beetle PCE | pinned commit, built from source | <https://docs.libretro.com/library/beetle_pce_fast/> | GPL-2.0 |
| SNES | Snes9x | pinned commit, built from source | <https://docs.libretro.com/library/snes9x/> | non-commercial (Snes9x's own) |
| NES | Nestopia UE | pinned commit, built from source | <https://docs.libretro.com/library/nestopia_ue/> | GPL-2.0 |
| N64 | Mupen64Plus-Next | built from source, GLES3 | <https://docs.libretro.com/library/mupen64plus/> | GPL-2.0 |
| Atari 2600 | Stella | 7.0, built from source | <https://stella-emu.github.io/> | GPL-2.0 |

The BIOS md5s in [ROMS.md](ROMS.md#bios-files) come from these libretro pages (read 2026-11-19).

## Build and deploy tools

| Tool | Used for | Upstream |
|---|---|---|
| Docker (Colima or Docker Desktop on macOS) | the image build, emulator builds, lint, Ansible in a container | <https://docs.docker.com/> |
| qemu user emulation (binfmt) | building arm64 on an x86_64 computer (a digest-pinned binfmt container, #237) | <https://www.qemu.org/>, <https://github.com/tonistiigi/binfmt> |
| Ansible | the deploy over SSH | <https://docs.ansible.com/> |
| Lima | the test VM on a Mac | <https://lima-vm.io/> |
| Raspberry Pi Imager | flashing the card | <https://www.raspberrypi.com/software/> |
| Python 3 | the tools, tests, menu builders and the SETTINGS app | <https://www.python.org/> |

## Documentation upstream

| Topic | Link |
|---|---|
| Raspberry Pi 5 and its config files | <https://www.raspberrypi.com/documentation/computers/raspberry-pi.html>, <https://www.raspberrypi.com/documentation/computers/config_txt.html> |
| ES-DE user guide | <https://gitlab.com/es-de/emulationstation-de/-/blob/master/USERGUIDE.md> |
| RetroArch and its cores | <https://docs.libretro.com/> |
| FBNeo | <https://github.com/finalburnneo/FBNeo> |
| Fightcade | <https://www.fightcade.com/> |
| Box64 | <https://github.com/ptitSeb/box64/tree/main/docs> |
| Wine | <https://wiki.winehq.org/> |

## This project's licences

| What | Licence |
|---|---|
| Code | MIT ([LICENSE](../LICENSE)) |
| Original art and docs | CC BY-SA 4.0 ([LICENSE-art](../LICENSE-art)) |
| Fonts (Archivo Black, Rajdhani, Press Start 2P) | SIL Open Font License ([NOTICE](../NOTICE)) |

No game, BIOS, firmware, emulator binary or disk image is in this repository, by design. Games and BIOS files are
yours to supply, from hardware you own, under the law where you live.
