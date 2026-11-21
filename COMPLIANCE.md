# Compliance

The rules that keep this project legal to publish, and how each one is checked. Plain-language legal background:
[docs/LEGAL.md](docs/LEGAL.md). Not legal advice; I'm not a lawyer.

## 1. Ship no artifacts of any kind

The project ships only the method: automation and instructions for your own cabinet. Never in git, never in a
release: OS or disk images, launcher or emulator binaries, ROMs, BIOS, firmware, game art, box art, logos.
The one exception is the night backdrop art with its eye glow and boot-menu card crops (#263): shipped on purpose, credited
in [THIRD_PARTY.md](THIRD_PARTY.md), removed on request, and listed by sha256 in the gate below; any other image fails it.
Third-party software is downloaded from upstream on the user's machine and never redistributed.

- Checked by `tests/test_ship_no_artifacts.py` (extensions, BIOS names, binaries, secret files) on every run.
- A built image or a downloaded archive (Fightcade, Electron, Wine) stays on the machine that built it. Never
  attach one to a release, an issue or a chat.
- **One exception (the maintainer, 2026-11-19, #267):** the community image, built by the release pipeline with
  `HK_LICENCES_STRICT=1` and published with its licence bundle and its sources (section 6). It never holds ROMs,
  BIOS, game art, a personal edition's files, or Fightcade, Wine and Electron (those are fetched on first launch).
  Never git, never a personal image.

## 2. Licences

- Our code: MIT ([LICENSE](LICENSE)). Our original art and docs: CC BY-SA 4.0 ([LICENSE-art](LICENSE-art)).
  Fonts: OFL 1.1, texts next to the fonts. Summary: [NOTICE](NOTICE).
- Everything the build installs, with its licence and duties: [THIRD_PARTY.md](THIRD_PARTY.md). A new download
  (core, package, archive) gets its row in the same change. Checked by `tests/test_third_party.py` (every libretro
  core the image build fetches has a row; the non-commercial ones are flagged).
- Four cores (FBNeo, MAME 2003-Plus, Snes9x, Genesis Plus GX) and the Fightcade client are non-commercial or
  proprietary: a cabinet set up with them is never sold, rented or run for paid play. The image installs three
  of them (FBNeo, Snes9x, Genesis Plus GX; MAME 2003-Plus left with #244).
- What the image redistributes, with licence and exact source: `skills/build-image/licences/components.json`. A
  core added to the launcher without an entry fails `skills/build-image/tests/test_licences.py`, and an unlisted
  binary in a built image stops a release build (`scripts/licences.py`, `HK_LICENCES_STRICT=1`; other builds warn).
- Licence changes are the maintainer's decision only.

## 3. Clean room toward the stock cabinet

- We automate what an owner does to their own cabinet. From the stock card we record **facts** (USB IDs, button
  numbers, resolutions, which audio sink is used), never its files: no copied configs, scripts, art or launcher
  code.
- Nothing decrypts, patches or bypasses a protection. If a cabinet ever locks its boot or encrypts its card, the
  project stops there (docs/LEGAL.md, "DMCA §1201").
- Vendor names are used only to say what the project works with; never to imply endorsement or affiliation.

## 4. Names and trademarks

- The community tree does not use a publisher's, series' or character's name as its own: not in its docs, theme
  text, file names, art or project name. The private repository's export renames or refuses them, and its scan fails
  the release on any hit left (the denylist: the publisher, the series, the fighter roster and their moves, and the
  maintainer's own values).
- **Named exceptions**, each one listed by exact line or file in the export's allow list, nothing by folder:
  - the credit lines of the night backdrop (THIRD_PARTY.md, docs/LEGAL.md; #263);
- **Game titles in the collection lists** (`skills/collections/*.tsv`) and **platform names** on badges and control
  cards are nominative: a title names the game an entry points at, a platform name the hardware it runs on, as a
  library catalogue does. They are not used as our marks, no list ships a ROM or a box image, and badges are drawn
  in our own style without logos. *Decided (#246 item 4): maintainer decision 2026-11-20: ship as
  is.*
- **What the scan reads** (#240, #246): every path, every line of text, symlink targets, PNG text chunks, font name
  tables, the members of archives, UTF-16 text and printable runs of other binaries. It also reads each line decoded:
  Unicode look-alikes and zero-width characters folded, URL and HTML escapes, `\x` escapes, base64 and hex payloads,
  words split into pieces or spelled out letter by letter, and (for the maintainer's values) rot13. A PNG with a
  chunk no writer of ours makes, or bytes after its end, fails. Only listed paths may ship at all, binaries only in
  the image and font folders.
- **What it cannot see: text drawn inside an image.** An image with a name in it must be generated from a text
  source the export renames, then regenerated in the export, or checked by eye before a release. An image the export
  leaves out that comes back under another name fails by its bytes, and re-encoded by its pixels.
- "Raspberry Pi" never goes into the name of an image or product (docs/LEGAL.md).

## 5. Private material

Personal material (curated game lists, the maintainer's own art and settings) lives only under `personal/`, which is
never exported or published. The night art that ships (section 1) is the one copyrighted set outside it.

## 6. The prebuilt image: source and notices

The community image (#267) is a copy of other people's software, so its release carries what their licences ask.
The simplest way that complies, and how each part is checked:

1. **Notices in the image.** `skills/build-image/scripts/licences.py`, run by `provision.sh` at every image build,
   writes `/usr/share/doc/coindojo/licences/` (each licence text byte for byte, pinned by sha256;
   `components.tsv`; `BUILDINFO` of what we build; `debian-packages.tsv` from the image's own dpkg database) and
   `LICENCES.md` on the boot partition; for the ES-DE AppImage, `es-de/libraries.tsv` (each bundled library's
   package, exact version, licence, source and what the release owes; from `licences/esde-libraries.tsv`, checked
   against the AppImage in the image). Debian's own `/usr/share/doc/<pkg>/copyright` files stay (the `diskhygiene`
   dpkg rules keep them). Checked by `skills/build-image/tests/test_licences.py` and `verify.sh`.
2. **Source of what we build** (RetroArch, Mupen64Plus-Next, Flycast, Stella): a `<image>-sources.tar.xz` asset on
   the **same release page** as the image, made from the same pins: each upstream tree at the exact commit
   (Flycast with its submodules), our patches, `build/` (the build scripts) and each `BUILDINFO`. Offering the
   source from the same place as the binary is what GPL-2.0 section 3 (last paragraph) and GPL-3.0 section 6(d)
   allow. Keep it up as long as the image is up. The tool that makes it is part of the release pipeline (#267).
3. **Debian and Raspberry Pi OS packages**: `debian-packages.tsv` names each package's exact source version, with
   a link to snapshot.debian.org or archive.raspberrypi.com. GPL-2.0 does not let us rely on someone else's server
   alone, so `LICENCES.md` also carries a **written offer** (GPL-2.0 section 3(b), GPL-3.0 section 6(b), LGPL-2.1
   section 6): for three years, the source of any GPL/LGPL program in the image, for no more than our cost.
   snapshot.debian.org does not mirror the Raspberry Pi archive, so the release keeps a copy of those source
   packages itself (kernel included): `skills/build-image/scripts/rpi_sources.py --packages
   <image>/usr/share/doc/coindojo/licences/debian-packages.tsv --out <work dir>` (dry run: the list with sizes;
   `--apply` downloads; `--keyring` = the image's `raspberrypi-archive-keyring.pgp`, checked with `gpgv`). Every
   file is checked against the archive's signed index (InRelease, Sources.gz) or, for a version the index already
   dropped, its `.dsc`; strict refuses an unchecked signature and such a `.dsc`-only version, so the release runs it
   right after the image build. The release uploads that folder (with its `SOURCES.tsv`, `SHA256SUMS` and the index)
   next to the image (#287). Size for the 2026-11-13 image: 50 source packages, 143 files, 773.5 MiB (kernel
   150 MiB, firmware-nonfree 331 MiB). Tests: `skills/build-image/tests/test_rpi_sources.py`.
4. **Non-commercial cores** (FBNeo, Snes9x, Genesis Plus GX): a free download is allowed by each licence's wording
   (Snes9x: "for non-commercial purposes ... providing that this license information and copyright notice appear
   with all copies"; Genesis Plus GX: "may not be sold, nor ... used in a commercial product or activity", notice
   reproduced; FBNeo: no "monetary profit", licence included verbatim). The image carries each text, and
   `LICENCES.md` names them first.
5. **The release gate:** the pipeline builds with `HK_LICENCES_STRICT=1` (exactly `1`; `0`, empty or unset is
   off, and then the licence step never fails a build: every problem is a WARN line, so offline personal builds
   keep working). Strict mode refuses a broken manifest, an unlisted binary, a missing listed component, a licence
   text that could not be fetched, a stale `BUILDINFO`, an ES-DE library its inventory does not list or that
   changed; it also refuses what it can see: blockers 1 and 6 below (the `ref` and `blockers` fields of
   `components.json`), a package without its copyright file, a missing `BUILDINFO`, Fightcade binaries in the
   image, and a tip link in the image's docs. Blockers 2, 7 and 8 are for the release checklist.
6. **Written-offer contact** (the maintainer, 2026-11-20): "open an issue on the project's repo", the default of
   `licences.py --offer-contact`.

### Tip links

The maintainer's decision (2026-11-20): **no tip or Ko-fi link on the downloadable release page or inside the
image** (an image README, anything on the boot partition, docs under `/home` or `/data`). The repository README
keeps its "Buy me a coffee" line and Ko-fi badge, linked to the maintainer's Ko-fi page, and
`.github/FUNDING.yml` adds the same page as GitHub's Sponsor button on the repository page.

Why: FBNeo's licence (`src/license.txt`) says "You may not sell, lease, rent or otherwise seek to gain monetary
profit from FB Neo" and "You may not ask for donations to support your work on any project that uses the FB Neo
source code". The image and its release page hand out the FBNeo binary, so a tip link next to them could read as
seeking money from FBNeo. Building from source is different: the repository holds no FBNeo source or binary, the
user's own machine downloads the core from libretro, so the repository README is not distributing FBNeo. Not legal
advice; I'm not a lawyer.

Checked by: `licences.py release-check --body <file> --root <image>` (exit 1 on a link or domain: ko-fi.com,
buymeacoffee.com, patreon.com, paypal.me, liberapay.com, opencollective.com, github.com/sponsors; a changelog line
that only names the README's "Buy me a coffee" line is not a link); `licences.py build
--strict` lists a link in the image as a release blocker. Tests: `skills/build-image/tests/test_licences.py`.

### Open blockers for a public image (2026-11-20, most serious first)

| # | Blocker | Why it blocks | Fix |
|---|---|---|---|
| 1 | The eight buildbot cores (fbneo, mame, mednafen_pce, mednafen_saturn, genesis_plus_gx, snes9x, nestopia, pcsx_rearmed) are nightlies with no recorded commit | The GPL cores need their exact corresponding source; Genesis Plus GX links LGPL code; FBNeo's Final Burn terms say "either distribute or link to the source code". A nightly's commit is unknown and the buildbot keeps only `latest` | Build each from a pinned commit in `build/` (as Stella is), then set its `ref`: #274 |
| 2 | Raspberry Pi archive packages (kernel, firmware, Pi tools) are not on snapshot.debian.org | The written offer must be honoured for three years; that archive may drop old versions | `rpi_sources.py` fetches them (part 3 above; the kernel is the archive's source package `linux`, 150 MiB); left: the release uploads them (#287), firmware terms in `THIRD_PARTY.md`: #275 |
| 3 | ~~The ES-DE AppImage bundles libraries that are not inventoried~~ | Any GPL/LGPL library in it brings its own source duty | Done (#276): `licences/esde-libraries.tsv` lists all 201 (Ubuntu 24.04 packages by build-id, SDL 2.32.10 built by ES-DE); 75 owe source (53 Ubuntu source packages, about 375 MiB, ffmpeg is a GPL build); strict refuses an unlisted or changed one. Left: those sources in the release's sources asset (#287) |
| 4 | ~~Written-offer contact~~ | Decided 2026-11-20 (item 6 above) | Done |
| 5 | ~~FBNeo vs the tip link~~ | Decided 2026-11-20 ([Tip links](#tip-links)) | Done: enforced by `release-check` |
| 6 | `chroot-setup.sh` copies the unpinned master tarball of `libretro/glsl-shaders` | The shipped shader files (source form, per-file licences) are not known exactly | Pin it to the commit the `retroarch122` role uses: #277 |
| 7 | No sources asset tool yet | Part 2 above | Release pipeline, #267 |
| 8 | NOTICE, AGENTS.md and README still say "no Pi image" | They would contradict the release | Update with the release (Compliance, Docs) |
