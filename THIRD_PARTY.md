# Third-party software and assets

What the build downloads or installs, under which licence, how it reaches **your** card, and what that means for
you. The build and the deploy fetch every row below from its upstream source on your own machine. The prebuilt
community image (#267) is the one exception: it redistributes what [The prebuilt image](#the-prebuilt-image-what-it-redistributes)
lists, with each licence text and its exact source; Fightcade, Wine and Electron are never in it. Plain-language
overview: [docs/LEGAL.md](docs/LEGAL.md).

Not legal advice; I'm not a lawyer. Licences change: the upstream text always wins over this table. Licence names
use [SPDX identifiers](https://spdx.org/licenses/) where one exists. "Checked" means the licence file or the source
headers were read at the upstream source on 2026-11-19; "upstream" means taken from the project's own statement and not re-read that day.

## Read this first: non-commercial components

Four emulator cores and the Fightcade client are **not free for commercial use**. They are fine for a private
cabinet at home. They mean that **a cabinet set up with this project must never be sold, rented out, run for paid
play, or bundled into a commercial product with these components installed**, and nobody may ask for money for a
build that includes them.

| Component | Restriction (quoted or summarised from the upstream licence) |
|---|---|
| FBNeo (`fbneo` core, and the FBNeo inside Fightcade) | "You may not sell, lease, rent or otherwise seek to gain monetary profit from FB Neo"; no donations for projects using its source (this project contains no FBNeo source or binary: your own build compiles the core from its pinned source; its optional Ko-fi tips unlock nothing, see [docs/LEGAL.md](docs/LEGAL.md#summary)); MAME-derived parts add "Redistributions may not be sold, nor may they be used in a commercial product or activity." |
| MAME 2003-Plus (`mame2003_plus` core) | MAME 0.78 licence: "strictly a non-profit project"; selling it is not allowed; no commercial product without the authors' written authorisation. Not installed by the image or the cores role since #244 (arcade sets run on current MAME, GPL-2.0); listed for cabinets that still have it. |
| Snes9x (`snes9x` core) | "freeware for PERSONAL USE only. Commercial users should seek permission of the copyright holders first." |
| Genesis Plus GX (`genesis_plus_gx` core) | "Redistributions may not be sold, nor may they be used in a commercial product or activity." |
| Fightcade client | Proprietary. No public licence or terms page found (see the Fightcade row). Treat as all rights reserved. |

## Base system

| Component | Licence | How it reaches the card | Your obligations |
|---|---|---|---|
| Raspberry Pi OS Lite (arm64), Debian based, with its kernel and firmware | Mostly free software (each Debian package keeps its own licence, mostly GPL/LGPL/BSD/MIT), plus firmware blobs under their own terms (checked 2026-11-20 in the image's copyright files, see [Firmware](#firmware-in-the-image)) | You download the base image yourself (`make base-image`, sha256-pinned from `downloads.raspberrypi.com`); the image build starts from it | Your own build: none for private use. The community image carries every package's notice and source duties: see [The prebuilt image](#the-prebuilt-image-what-it-redistributes). "Raspberry Pi" is a trademark of Raspberry Pi Ltd: don't put it in the name of your image or product ([trademark rules](https://www.raspberrypi.com/trademark-rules/)); "runs on Raspberry Pi 5" style wording is the allowed form |
| Debian packages installed by the build and the deploy (cage, seatd, Mesa, PulseAudio, Xwayland, X11 utils, GTK/NSS libs for Electron, zram, curl, unzip ...) | Each package's own licence (upstream; `/usr/share/doc/<pkg>/copyright` on the card, kept by the `diskhygiene` role's dpkg rules) | `apt` from the Debian / Raspberry Pi archives, on the card | None for private use |

## Front end and emulators

| Component | Licence | How it reaches the card | Your obligations |
|---|---|---|---|
| ES-DE (EmulationStation Desktop Edition) 3.5.0 | MIT (checked: `LICENSE` at tag v3.5.0 of `gitlab.com/es-de/emulationstation-de`); the AppImage also bundles 201 third-party libraries, each under its own licence: listed in `skills/build-image/licences/esde-libraries.tsv` (#276; Ubuntu 24.04 packages plus ES-DE's own SDL 2.32.10 build; 75 are GPL, LGPL, MPL or CDDL, e.g. its ffmpeg is a GPL build) | AppImage downloaded from `packages.es-de.org` by the image build or deploy | Keep its notices if you pass it on; the AppImage carries each library's copyright file (`usr/share/doc/<package>/copyright`); passing it on also brings the GPL/LGPL source duty for those libraries |
| RetroArch | GPL-3.0-or-later (checked) | 1.22.2 built from `github.com/libretro/RetroArch` (commit `69a4f0ea`) on your laptop (`build/retroarch`) with our patches, then copied to your card; Debian's 1.20 package is installed too but not used by the launcher | If you give a binary to anyone: offer the matching source, including our patches |
| libretro core info files | per file (upstream) | Debian `libretro-core-info` | None for private use |
| `fbneo` core | FBNeo non-commercial licence (checked: `src/license.txt` at libretro/FBNeo `5f6bdff9`, 2026-11-19: "You may not sell, lease, rent or otherwise seek to gain monetary profit from FB Neo"; "You must include, verbatim, the full text of this license"; plus the MAME non-commercial licence) | Built from a pinned commit (`build/fbneo`, `CORE_COMMIT`); a personal build without that build output falls back to the libretro buildbot nightly (`buildbot.libretro.com`) with a WARN, a release never does (#274) | **Non-commercial**: see the top table |
| `snes9x` core | Snes9x licence, personal use only (checked: `LICENSE` at libretro/snes9x `204ea1c0`: distribution "for non-commercial purposes ... providing that this license information and copyright notice appear with all copies"; bundled JMA GPL/LGPL, snes_ntsc LGPL, xBRZ GPL-3.0) | Built from a pinned commit (`build/snes9x`, `CORE_COMMIT`); a personal build without that build output falls back to the libretro buildbot nightly (`buildbot.libretro.com`) with a WARN, a release never does (#274) | **Non-commercial** |
| `genesis_plus_gx` core | Genesis Plus GX non-commercial licence (checked: `LICENSE.txt` at libretro/Genesis-Plus-GX `b9c23164`: "Redistributions may not be sold, nor may they be used in a commercial product or activity"; "must reproduce the above copyright notice"; bundled Nuked OPN2 is LGPL-2.1-or-later, Tremor BSD, minimp3 CC0) | Built from a pinned commit (`build/genesis_plus_gx`, `CORE_COMMIT`); a personal build without that build output falls back to the libretro buildbot nightly (`buildbot.libretro.com`) with a WARN, a release never does (#274) | **Non-commercial** |
| `mame` core (current MAME) | GPL-2.0-only (checked: `COPYING` at libretro/mame `5f5a0774`: "MAME as a whole is made available under the terms of the GNU General Public License ... version 2"; single files may carry less restrictive licences, e.g. BSD-3-Clause, per their headers) | Built from a pinned commit (`build/mame`, `CORE_COMMIT`); a personal build without that build output falls back to the libretro buildbot nightly (`buildbot.libretro.com`) with a WARN, a release never does (#274) | GPL source offer if you pass a binary on |
| `mednafen_pce` core (Beetle PCE) | GPL-2.0-or-later (checked: `COPYING` is GPLv2 and `mednafen/pce/pce.cpp` says "version 2 of the License, or (at your option) any later version", libretro/beetle-pce-libretro `0c9db4d5`) | Built from a pinned commit (`build/mednafen_pce`, `CORE_COMMIT`); a personal build without that build output falls back to the libretro buildbot nightly (`buildbot.libretro.com`) with a WARN, a release never does (#274) | Same |
| `mednafen_saturn` core (Beetle Saturn) | GPL-2.0-or-later (checked: `COPYING` is GPLv2 and `mednafen/ss/scu.inc` says "version 2 of the License, or (at your option) any later version", libretro/beetle-saturn-libretro `7126d65b`) | Built from a pinned commit (`build/mednafen_saturn`, `CORE_COMMIT`); a personal build without that build output falls back to the libretro buildbot nightly (`buildbot.libretro.com`) with a WARN, a release never does (#274) | Same |
| `pcsx_rearmed`, `nestopia` cores | GPL-2.0-or-later (checked: `COPYING` and source headers at libretro/pcsx_rearmed `7b0e8f8f` and libretro/nestopia `f1e02d12`) | Built from pinned commits (`build/pcsx_rearmed`, `build/nestopia`, `CORE_COMMIT`); a personal build without them falls back to the libretro buildbot nightly (`buildbot.libretro.com`) with a WARN, a release never does (#274). Debian's `libretro-nestopia` may be installed too | GPL source offer if you pass a binary on |
| Standalone Flycast 2.7 | GPL-2.0-or-later (checked: source headers say "version 2 ... or (at your option) any later version") | Built from `github.com/flyinghead/flycast` tag v2.7 (commit `5aa091fd`, `build/flycast`) | GPL source offer if you pass a binary on |
| `mupen64plus_next` core (GLES build) | GPL-2.0-or-later (checked: source headers) | Built from `github.com/libretro/mupen64plus-libretro-nx` (`build/n64`) | Same |
| `stella` core | GPL-2.0 (checked: `License.txt` is the GPLv2 text, and the source headers name no version, so neither `-only` nor `-or-later` is stated; the image bundle records it as GPL-2.0-only, the stricter reading) | Built from `github.com/stella-emu/stella` (`build/stella`) | Same |
| CRT shaders (`crt-pi` and the pass-through `stock`) | GPL-2.0-or-later for `crt-pi` (checked); the others per file header (upstream) | Downloaded from `libretro/glsl-shaders` (`github.com/libretro/glsl-shaders`): a pinned commit with sha256 checks in the deploy, the current tree in the image build | Keep the file headers |

### Cores no longer installed (#244)

The image and the `cores` role install exactly the cores `skills/collections/launch.py` runs. These were installed
by earlier builds and may still be on an older cabinet; their licences stay listed for that case.

| Component | Licence | How it reached the card | Your obligations |
|---|---|---|---|
| `mame2003_plus` core | MAME 0.78 non-commercial licence (checked) | libretro buildbot | **Non-commercial** |
| `flycast` core (libretro) | GPL-2.0-or-later (checked) | libretro buildbot; the cabinet runs standalone Flycast instead | GPL source offer if you pass a binary on |
| `ppsspp` core | GPL-2.0-or-later (checked: "version 2.0 or later versions"), with BSD-licensed PSPSDK parts | libretro buildbot | Same |
| `mgba` core | MPL-2.0 (checked) | libretro buildbot | Changed mGBA files stay MPL if you pass them on |
| `gambatte` core | GPL-2.0-only (checked: source headers say "version 2" with no later-version clause) | libretro buildbot | GPL source offer if you pass a binary on |

## Fightcade mode

| Component | Licence | How it reaches the card | Your obligations |
|---|---|---|---|
| Fightcade client (Linux build: Electron app, FightcadeFBNeo, Flycast-Dojo, Snes9x builds) | **Proprietary.** fightcade.com publishes no terms of service or EULA that we could find on 2026-11-19 (the site's pages are home, about, FAQ, help, changelog, replays, register), and the Linux archive carries no licence for the client itself. Any terms shown inside the app at sign-up were not reviewed. Its emulators are based on FBNeo, Snes9x and Flycast and inherit their licences, so FBNeo's **non-commercial** terms apply | Your laptop downloads the pinned `Fightcade-linux-v2.1.45.tar.gz` from the official `web.fightcade.com/download/` URL (`build/fightcade/fetch.sh`); the deploy copies **your** download to **your** cabinet, or with `hk_fightcade_install: first-launch` the cabinet downloads it itself from the same URL (#267); no image or card that holds it is shared | Never share the archive, an unpacked copy, or a card or image that contains it. You need a Fightcade account, under whatever terms Fightcade sets in the app |
| Our changes to your Fightcade copy: the `lib/main.js` patch, the injected stylesheet, the wrappers | Ours (MIT). The files they touch stay Fightcade's | All applied at deploy time to your own unpacked copy only. The patch (after a sha256 check of the original) turns off plugins and Node in pop-up windows, a security hardening. `inject.css` (from `ansible/roles/fightcade/templates/inject.css.j2`) is placed where the app's nativefier wrapper loads it, and fits the lobby into the bezel opening. Our own launcher and helper scripts (`ansible/roles/fightcade/files/`) start the app and run its Windows emulator through Wine and Box64 | Nothing patched or unpacked is ever published. We found no published Fightcade terms, so nothing we found forbids or allows these changes to your own copy. We did **not** read the terms the app may show when you sign up; read them yourself. If Fightcade objects, drop the changes |
| nativefier (generated the Electron wrapper inside Fightcade) | MIT, Copyright (c) 2016 Goh Jia Hao (checked) | Part of Fightcade's archive; a 13-line excerpt of its window options is quoted in our test fixture (`ansible/roles/fightcade/tests/test_fightcade.py`) to test the patch | Keep this attribution next to the excerpt |
| Electron 9.1.0 (arm64) | MIT, plus the Chromium licences in `LICENSES.chromium.html` (checked in the release archive) | Downloaded from `github.com/electron/electron/releases` by `build/fightcade/fetch.sh` | Keep both licence files with it |
| Wine 10.0 (Kron4ek build) | LGPL-2.1-or-later (upstream) | Downloaded from `github.com/Kron4ek/Wine-Builds` by `build/fightcade/fetch.sh` | Source offer if you pass it on |
| Box64 0.4.4 | MIT, Copyright (c) 2020 ptitSeb (checked) | Built from `github.com/ptitSeb/box64` (`build/box64`) | Keep its notice |
| Box64's `x64lib/`: prebuilt x86_64 OpenSSL 1.0.1 and 1.1.1n, Mbed TLS 2.28.3, GCC runtime (libstdc++.so.6, libgcc_s), GCC 3.3 libstdc++.so.5, libpng 1.2.54, libunwind 1.6.2 | OpenSSL (OpenSSL + SSLeay); Apache-2.0; GPL-3.0-or-later WITH GCC-exception-3.1; GPL-2.0-or-later with the libstdc++ runtime exception; Libpng; MIT (each text in the image, #300) | Part of the Box64 source at `2f130fab` (`x64lib/`), copied with Box64 | Keep each notice; GPL source offer for the GCC runtime parts |

## Online services

| Service | Terms | Notes |
|---|---|---|
| RetroAchievements (`retroachievements.org`) | The service's own terms, accepted with your own account | Optional; your login stays in your Keychain or the cabinet's settings, never in this repo |
| Fightcade lobby | Fightcade's terms in the app | Optional |

## The prebuilt image: what it redistributes

The community image on the Releases page (#267) is a copy of the software below, so for it **we** are the
distributor and carry each licence's duties. Machine-readable list: `skills/build-image/licences/components.json`
(licence texts pinned by URL and sha256, read 2026-11-19). `skills/build-image/scripts/licences.py` turns it into the
image's licence bundle at build time; `skills/build-image/tests/test_licences.py` checks that every core the launcher
runs has an entry and that the build pins match.

| Component | Version | Licence (SPDX) | Source | Origin |
|---|---|---|---|---|
| Coin Dojo (our code, art, theme) | the image's tag | MIT AND CC-BY-SA-4.0 | this repository | ours |
| Theme fonts (Archivo Black, Rajdhani, Press Start 2P) | vendored | OFL-1.1 | this repository | ours |
| ES-DE | 3.5.0 | MIT; its 201 bundled libraries each under their own licence (`skills/build-image/licences/esde-libraries.tsv`, in the image as `es-de/libraries.tsv`) | `gitlab.com/es-de/emulationstation-de` tag v3.5.0; each library's Ubuntu source package (Launchpad) | upstream AppImage |
| RetroArch | 1.22.2 | GPL-3.0-or-later | `github.com/libretro/RetroArch` `69a4f0ea` + our 2 patches | built by us |
| Mupen64Plus-Next (GLES3) | `15bada85` | GPL-2.0-or-later | `github.com/libretro/mupen64plus-libretro-nx` | built by us |
| Flycast (standalone) | 2.7 | GPL-2.0-or-later | `github.com/flyinghead/flycast` `5aa091fd` + our patches (off by default) | built by us |
| Stella core | 7.0 | GPL-2.0-only | `github.com/stella-emu/stella` `d55b1aec` | built by us |
| FBNeo core | `5f6bdff9` | LicenseRef-FBNeo (**non-commercial**) | `github.com/libretro/FBNeo` | built by us |
| MAME core | `5f5a0774` | GPL-2.0-only | `github.com/libretro/mame` | built by us |
| Beetle PCE core | `0c9db4d5` | GPL-2.0-or-later | `github.com/libretro/beetle-pce-libretro` | built by us |
| Beetle Saturn core | `7126d65b` | GPL-2.0-or-later | `github.com/libretro/beetle-saturn-libretro` | built by us |
| Genesis Plus GX core | `b9c23164` | LicenseRef-GenesisPlusGX (**non-commercial**) AND LGPL-2.1-or-later AND BSD-3-Clause AND CC0-1.0 | `github.com/libretro/Genesis-Plus-GX` | built by us |
| Snes9x core | `204ea1c0` | LicenseRef-Snes9x (**non-commercial**) | `github.com/libretro/snes9x` | built by us |
| Nestopia UE core | `f1e02d12` | GPL-2.0-or-later | `github.com/libretro/nestopia` | built by us |
| PCSX-ReARMed core | `7b0e8f8f` | GPL-2.0-or-later | `github.com/libretro/pcsx_rearmed` | built by us |
| GLSL shaders | pinned files + the tree | per file (crt-pi GPL-2.0-or-later) | `github.com/libretro/glsl-shaders` | source form |
| Box64 (only with `hk_image_fightcade`; a first-launch image carries it, never Fightcade itself) | 0.4.4 | MIT | `github.com/ptitSeb/box64` `2f130fab` | built by us |
| Box64's `x64lib/` x86_64 libraries (with Box64) | OpenSSL 1.0.1, 1.1.1n; Mbed TLS 2.28.3; GCC 15.2.0 runtime (Debian gcc-15 15.2.0-12); GCC 3.3.6 libstdc++.so.5; libpng 1.2.54; libunwind 1.6.2 (Debian 1.6.2-3) | OpenSSL; Apache-2.0; GPL-3.0-or-later WITH GCC-exception-3.1; GPL-2.0-or-later with the libstdc++ exception; Libpng; MIT | `github.com/openssl/openssl`, `github.com/Mbed-TLS/mbedtls`, `github.com/gcc-mirror/gcc`, `github.com/pnggroup/libpng`, `github.com/libunwind/libunwind` (exact refs in `components.json`) | prebuilt, in the Box64 source |
| Raspberry Pi OS / Debian packages (kernel, firmware, Mesa, cage, PulseAudio, Debian's RetroArch 1.20 ...) | as installed | each its own | snapshot.debian.org; the Raspberry Pi archive's source packages are kept with the release (`skills/build-image/scripts/rpi_sources.py`, #275) | Debian / Raspberry Pi |

**In the image** (generated, never hand-copied): `/usr/share/doc/coindojo/licences/` holds every component's licence
text byte for byte, `components.tsv` (exact ref and the sha256 of each shipped file), `BUILDINFO` for what we build
(commit, patches, flags), and `debian-packages.tsv` (every installed package, its exact version, its source package
and a link to it on snapshot.debian.org or archive.raspberrypi.com). `LICENCES.md` on the boot partition is the
summary anyone can read on a computer after flashing, with the non-commercial components first and the written
offer. For a release (`HK_LICENCES_STRICT=1`) `verify.sh` fails an image without them and the generator refuses a
binary no component accounts for; other builds get WARN lines and still finish.

**Corresponding source** (GPL-2.0 section 3, GPL-3.0 section 6, LGPL-2.1 section 6): see
[COMPLIANCE.md](COMPLIANCE.md#6-the-prebuilt-image-source-and-notices). A release runs the build with
`HK_LICENCES_STRICT=1`, which refuses every open blocker.

### Firmware in the image

The image ships the binary firmware that Raspberry Pi OS ships, unmodified, as Debian packages. Their terms (read
2026-11-20 in each package's `/usr/share/doc/<package>/copyright` in a built image) allow passing them on in binary
form when the copyright notice and disclaimer go with them, which the image does by keeping those files (a release
refuses a package without its copyright file):

| Package (source) | What | Terms |
|---|---|---|
| `raspi-firmware` | GPU firmware and bootloader blobs on the boot partition (`start*.elf`, `fixup*.dat`) | Broadcom / Raspberry Pi: "Redistribution and use in binary form, without modification, are permitted" provided the software is only used "for the purposes of developing for, running or using a Raspberry Pi device", the notice and disclaimer are reproduced, and Broadcom's name is not used to endorse |
| `firmware-brcm80211` and the other `firmware-nonfree` packages (`firmware-atheros`, `-libertas`, `-mediatek`, `-realtek`) | Wi-Fi firmware | Per file in the package's copyright file. The Pi's Wi-Fi chips: `*43455*` (Pi 4, Pi 5) under `binary-redist-Cypress`, `*43456*` under the Synaptics licence ("Copy and redistribute the Software Libraries in object code only ... for use in connection with Synaptics integrated circuits"), other `brcmfmac*.bin` under `binary-redist-Broadcom-wifi`: each allows unmodified binary redistribution with its notice |
| `bluez-firmware` | Bluetooth firmware (`broadcom/`) | Broadcom (`BCM_LEGAL`) and the Cypress licence, confirmed for Raspberry Pi use (in the copyright file) |

No source is owed for these blobs (none is published); the release keeps their source packages anyway with the
other Raspberry Pi archive sources (#275). Not legal advice; I'm not a lawyer.

## In this repository

| What | Licence | Notes |
|---|---|---|
| Our code | MIT ([LICENSE](LICENSE)) | |
| Our original art (theme backgrounds, panels, badges, chips, pictograms, help icons, specimen) and docs | CC BY-SA 4.0 ([LICENSE-art](LICENSE-art)) | Every image is drawn by our own generators (`skills/badges`, `skills/controls`, `brand/`) or hand-made SVG; no third-party art, logos or box art, except the night art in the next row |
| Night backdrop and its eye-glow overlay (`themes/coin-dojo/_inc/art/backdrops/default.png`, `eye-glow.*`) and the two boot-menu card crops of it (`_inc/art/bootmenu/`) | Not ours and not under our licences: official CAPCOM promotional artwork, © CAPCOM CO., LTD. Used as a non-commercial fan tribute without permission or endorsement. This project is not affiliated with Capcom. | Removed on request (tell the maintainer through the project's issue tracker). Listed by sha256 in `tests/test_ship_no_artifacts.py`: nothing else of this kind may ship |
| Fonts: Archivo Black (Omnibus-Type), Rajdhani (Indian Type Foundry), Press Start 2P (CodeMan38) | OFL-1.1 | Their licence texts ship next to them in the theme's `_inc/fonts/` |
| Font: Yuji Syuku (Kinutafontfactory, [source](https://github.com/Kinutafontfactory/Yuji)), a 3 KB subset of two glyphs only, 道 U+9053 and 場 U+5834 | OFL-1.1 | `skills/badges/fonts/` with `OFL-YujiSyuku.txt` (no Reserved Font Name). Only `gen_backdrop.py` reads it, to draw the dojo scroll into the backdrop PNG; the font itself is not shipped to the cabinet. Original: https://github.com/google/fonts/tree/main/ofl/yujisyuku |
| Test fixtures (game lists, ROM set names) | Ours (MIT) | Names and short ROM set names are used only to identify games a user may own; descriptions are synthetic placeholder text |

## Not installed, never shipped

ROMs, BIOS files, firmware dumps, box art, videos and any game media: you bring your own, from games you own or
are otherwise entitled to use. See [docs/LEGAL.md](docs/LEGAL.md).
