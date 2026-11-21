# Skill: Build the Coin Dojo arcade OS image (Linux or macOS, no cabinet)

Assemble a complete, flashable arcade OS **image** from a stock Raspberry Pi OS Lite
(arm64) base, entirely in Docker containers on Linux (x86_64 or arm64) or macOS, then flash
it to a card and boot-test on the cabinet. No hardware is needed to *build*; only the final
flash + boot test touches the cabinet.

**Ship no artifacts:** you supply your own base Pi OS image and your own ROMs/BIOS. This
skill bundles none.

## What it produces
The same cabinet a deploy makes (#244): after the base OS, `make image` runs the cabinet's own Ansible roles
(`ansible/image.yml`) into the image through a chroot. One source of truth: the image keeps no list of its own.
A 3-partition image (`boot` / `root` / `data`):
- **root (ext4):** the OS — ES-DE (boot-to-frontend via `cage`), de-bloated (no apt-daily / unattended-upgrades),
  SSH key-only; then everything the roles install: the session loop and boot menu, SETTINGS and QUIT TO MAIN MENU on
  the carousel, the theme, RetroArch 1.22 + flycast + exactly the cores `skills/collections/launch.py` runs (role
  `cores`), power button and stickmouse, 4 KB-page kernel, zram, `vm.swappiness`, Wi-Fi power save off, polkit,
  journald, and Fightcade mode with `hk_image_fightcade: true`. Last, `scripts/licences.py` writes the licence
  bundle (`/usr/share/doc/coindojo/licences/`, `LICENCES.md` on boot) from `licences/components.json` and the
  image's dpkg database. With `HK_LICENCES_STRICT=1` (a release) it stops on a binary no entry accounts for and
  on any open blocker; otherwise every problem is a WARN and the build (offline too) still finishes (#254,
  COMPLIANCE.md section 6).
- **data (ext4, label `coin-dojo-data`, mounted `/data`):** `roms/ bios/ saves/ states/ media/`
  — **survives OS reflashes.** ES-DE's ROM/media dirs and RetroArch's BIOS/save/state dirs
  all point here. Expands to fill the whole card on first boot.

Hardware parity baked in (measured on the cabinet (facts: USB IDs, button numbers, HDMI audio)):
- **Controller:** one global RetroArch autoconfig + SDL mapping for the cabinet's Pico stick
  (`USB gamepad`, VID 0x081f / PID 0xe401, 6 buttons + Coin/Start). Works for every core and
  ES-DE menu nav — no per-game setup. See [[cabinet-controller]].
- **Audio:** Pi 5 has no analog jack, so sound rides **HDMI → monitor → aux → speakers**.
  The standard PulseAudio **combine-sink** (both HDMI outs merged), RetroArch → pulse.
- **Display:** both HDMI ports forced 1080p, overscan off, console cursor hidden.

## Prereqs
- **Docker** with privileged containers and loop devices:
  - Linux x86_64 or arm64: Docker Engine (`sudo systemctl start docker`, your user in the `docker` group).
  - macOS (Apple Silicon or Intel): Colima (`brew install colima docker; colima start`) or Docker Desktop.
    Colima shares only `$HOME`, so keep `IMAGE_OUT` (and the base image) under `$HOME`.
- **No spaces in `BASE_IMG`, `IMAGE_OUT` or `CONFIG` paths** when you go through `make` (make splits words on
  spaces); `scripts/run.sh` itself takes quoted paths with spaces.
- **arm64 containers.** Native on arm64 hosts (Apple Silicon, Linux arm64). On an **x86_64** docker host the
  build registers the qemu-aarch64 binfmt handler through the digest-pinned `tonistiigi/binfmt` image
  (`HK_BINFMT=auto`, the default; it says what it changes and how to undo it). `HK_BINFMT=no` refuses instead
  and prints the one command to run yourself. Emulated builds are several times slower.
- `python3` (the size check and the config loader), about 9 GiB free beside `IMAGE_OUT`.
- A **base Raspberry Pi OS Lite arm64** image: `make base-image` (below), or one you downloaded (`.img`, decompressed).
- Nothing secret is needed to build: no password, key or Keychain (see "First-boot config"). **But an image
  built with `CONFIG=` (or the Keychain flow) carries that config's secrets on its boot partition until the
  first boot:** the Wi-Fi password in plain text and the password hash. `run.sh` then makes the `.img` and its
  `.sha256` mode 600 and warns; never share or upload such an image. An image built without `CONFIG=` has none.
- No macOS-only tool is on the build path. `run.sh` checks all of this first and stops with one `ERROR:` line.

## Card sizes
The image is small; the **data** partition (ROMs, BIOS, saves) is last and **grows to fill the card at first
boot** (`firstboot-expand.sh`), whatever its size. Root stays at `ROOT_GIB`.

| Card | Root | Data after first boot | Notes |
|---|---|---|---|
| 16 GB (minimum, `MIN_CARD_GB=16`) | 7 GiB | 7505 MiB (7.3 GiB) | the build refuses an image that would not fit |
| 32 GB (recommended, the default) | 7 GiB | 22839 MiB (22.3 GiB) | defaults: `ROOT_GIB=7 DATA_MIB=512` |
| 64 GB | 7 GiB | 53347 MiB (52.1 GiB) | nothing to change |

Measured 2026-11-19 with `make -C skills/build-image prove-grow` on the built image (16 GB = 15,931,539,456 B and 32 GB =
32,010,928,128 B, real cards' capacities; 64 GB = 64e9 B). Root then holds 3.9 GiB of 6.9 GiB (`verify.sh`; 3.3 GiB with the roles and Fightcade since #244: diskhygiene prunes it). With `hk_image_fightcade` the image is 10760 MiB (data 3072 MiB), still a 16 GB card.
- The default image is **8200 MiB** (boot 512 MiB + root 7 GiB + data 512 MiB, plus the 8 MiB before boot), so
  the **minimum card is 16 GB**; an 8 GB card (about 7.4 GiB) is too small.
- A card sold as N GB holds at least `N x 10^9 x 0.93` bytes in this check (the smallest 16 GB cards hold about
  14.9e9). `scripts/imgsize.py` reads the base image's partition table and refuses before any docker step when
  the image would not fit `MIN_CARD_GB`.
- Proof on sparse cards: `make -C skills/build-image prove-grow IMG=<built.img>` (16 GB and 64 GB loop devices,
  the real `firstboot-expand.sh`; `BROKEN=1` is the control that must fail).

## Base image (make base-image)
`make base-image [BASE_DIR=~/.cache/coin-dojo/base-image]` downloads the official Raspberry Pi OS Lite (trixie)
arm64 `.img.xz` from downloads.raspberrypi.com, checks it against the sha256 pinned in `scripts/base-image.sh`
(the release's own published `.sha256`), decompresses it (`xz`, else Python's `lzma`) and prints
`BASE_IMG=<path>`. A mismatch deletes the download. Pin today: **2026-10-06** release. For a newer release set
both `BASE_URL=` (its `.img.xz`) and `BASE_SHA256=` (its published sha256). Nothing is redistributed.

## Build time
| Host | Time | Notes |
|---|---|---|
| Apple Silicon Mac, Colima 6 CPU / 12 GiB (arm64 native), full stack with Fightcade (#244) | **478 s** | prep 8 s, base 147 s, roles 309 s, verify 12 s (2026-11-19, `run.sh`, `hk_image_fightcade=true`, `~/.cache/coin-dojo/platform-244/build-proof.log`; emulator builds already in build/*/out) |
| same, second run (#244 fix round) | **341 s** | prep 8 s, base 92 s, roles 228 s, verify 11 s (2026-11-19, same flags, `build-proof2.log`; apt and downloads warm) |
| Apple Silicon Mac, Colima, base only (before #244) | 159 s | prep 10 s, chroot build 125 s, verify 23 s (2026-11-19) |
| Linux arm64 | about the same as the Mac | native, not measured yet |
| Linux x86_64 (qemu-user binfmt) | not measured yet | expect several times slower: every arm64 program in the chroot is emulated |

Downloads (apt, ES-DE, cores, shaders, Fightcade's packages) and the Wine prefix are most of it; `<workdir>/cache` skips the ES-DE download (and `glsl-shaders-<commit>.tar.gz` the shader one, sha256-checked, #277). First time only: `make -C build all` (~15 min) and, for Fightcade, `make -C build box64 fightcade` (~10 min).

## Build (one command)
`make image BASE_IMG=<base-pi-os-lite.img> IMAGE_OUT=<out.img> [CONFIG=coindojo.yml] [ROOT_GIB=7] [DATA_MIB=512] [MIN_CARD_GB=16]`
(from the root) runs the same script; `EDITION=community` runs `edition-check` first. Tests: `make -C skills/build-image test`.
`run.sh` prints the edition at the start and in its `DONE` line. **A personal-edition image (`EDITION=personal`) holds
your personal art and lists from `personal/`: never share or publish it.** A direct `run.sh` builds `community`.
```
skills/build-image/scripts/run.sh <base-pi-os-lite.img> <out.img> [ROOT_GIB=7] [DATA_MIB=512]
```
`run.sh` checks the host (docker, arm64, the shared folder, free space, the card size), stages the scripts
beside `<out.img>`, mounts the base image's folder read-only and runs three container steps (the pinned
`debian:12` image; only the build step runs as `linux/arm64`):
1. **prep-image.sh** — copies base → out, repartitions to boot/root/data **preserving the MBR
   disk-id** (so `root=PARTUUID=` keeps working), grows root to `ROOT_GIB`, adds the data partition.
2. **build.sh** — mounts the 3 partitions, binds `/dev /proc /sys`, chroots in and runs
   **chroot-setup.sh** (the base OS: packages, user, ES-DE, kiosk, first boot), then seeds the `/data` skeleton.
   Auto-detects partition offsets. Uses an optional offline cache at `<workdir>/cache` (ES-DE AppImage) to skip
   downloads.
3. **provision.sh** (#244) — in the pinned Ansible container (`ansible/Dockerfile`, built by `ansible/bin/ans`):
   mounts the image again, renders the config like `make deploy` (`EDITION`, your local file, `HK_SET`) and runs
   `ansible/image.yml` over a chroot connection. `coin_dojo_image=true` makes the roles skip what needs the running
   cabinet (unit starts, `sysctl --system`, `modprobe`, binfmt registration, NetworkManager, timedated, ES-DE
   restarts); those take effect at the first boot. No secret reaches the play.
4. **verify.sh** — structural gate: 3 partitions, ES-DE, controller profile, `/data` repointing, audio
   combine-sink, both-HDMI 1080p, firstboot service, SSH, no baked secrets, and (#244) the cabinet stack: every
   core, emulator and ROM folder `launch.py` names, session, boot menu, SETTINGS/QUIT links, theme, units, polkit,
   sudoers, sysctl, swap, Fightcade when on.

**Inputs from your own build folder** (never in git, never downloaded pre-built): `make -C build all` (RetroArch
1.22, the GLES N64 core, flycast, Stella; ~15 min the first time). `run.sh` stops at once when one is missing.
**The other eight libretro cores** (#274) come from pinned commits (`make -C build cores`, `build/cores/README.md`;
MAME takes hours). A release (`HK_LICENCES_STRICT=1`) needs all eight and `run.sh` stops at once without one; any
other build (and `make deploy`) uses a built core when `build/<core>/out` has it, else the libretro buildbot's
nightly with a WARN line per core (not source-pinned: fine for personal use, not for a release).
**Fightcade mode** (on by default since #294; `hk_image_fightcade: false` leaves it out):
also `make -C build box64 fightcade`; the data partition then starts at 3072 MiB (still grows at first boot).
**An image you give away** adds `hk_fightcade_install: first-launch` (#267): only `make -C build box64` is needed, no
Fightcade, Wine or Electron file goes in (verify FAILs one, by its pinned file name or a file only their trees have),
no prefix is built and the data partition keeps its size; the cabinet downloads them on the first FIGHTCADE pick.
`EDITION=community` (the default) switches `deploy` to `first-launch` by itself in `run.sh` (#294): only a personal
image may bundle them.

| In the image | At the first boot |
|---|---|
| packages, files, units enabled, sysctl.d / modules-load.d / binfmt.d / udev rules, the theme, the starter carousel, the Wine prefix | units start, swappiness and uinput apply, the `.exe` handler registers, zram0 appears, NetworkManager reads the power-save drop-in, SSH host keys and your `coindojo.yml` |

The carousel of a new image is the **starter**: SETTINGS and QUIT TO MAIN MENU plus every ES-DE system you drop
ROMs into. The nested main screen (MUST PLAY / GENRES / ...) needs your picks and dat, so it comes with the first
`make deploy-esde`. Not in the image on purpose: `sshd_keyonly` (it switches a live sshd; chroot-setup.sh writes the
same drop-in) and `esde_verify` (it reads a running ES-DE).

**Verify gate:** every line of `verify.sh` must read `PASS`; `run.sh` stops on any `FAIL` line and only then
writes `<out.img>.sha256` (sha256sum format). On Linux the image is chowned back to you. Do not flash otherwise.

## First-boot config (no baked credentials, #237)
The image ships with **no SSH key, no Wi-Fi and no SSH host keys**, and every account is locked, with one
exception: **the community default login (#265)**. With `EDITION=community` (the default) and no password in the
boot config (no `password_hash:` / `password: prompt`, no `COINDOJO_PASSWORD`), build.sh runs
`coindojo-firstboot --default-login` in the chroot: the user gets the password `12345`, hashed there by
`openssl passwd -6` with a new random salt every build (no hash is committed), set with `chpasswd -e`, and the hash
is kept in `/etc/coindojo/default-password` (0600). Every boot compares it with `/etc/shadow`: while it is still
the default, the journal, the console and tty1 (before the frontend, `/run/coindojo/default-login`, 5 s) say
`Default login <user> / 12345: change it in SETTINGS > PASSWORD`; once changed, the mark is removed. **No sudo with the
default (#270):** `--default-login` also takes the user out of the `sudo` group and leaves
`/etc/coindojo/sudo-pending`. **Only a boot-partition password applied by `--apply`** (`password_hash:`, an
answered `password: prompt`, Imager's `userconf.txt`) makes `grant_sudo` write `/etc/sudoers.d/coindojo-admin`
(`<user> ALL=(ALL:ALL) ALL`, 0440, `visudo -cf` on `coindojo-admin.tmp` first) and remove the pending file. A
runtime change (`passwd`, `chpasswd`, the SETTINGS PASSWORD page #271) NEVER grants it (security review:
code running as the user knows 12345 and can run `passwd`); writing `/boot/firmware` needs the card or root.
`coindojo-password.path` (`PathChanged=/etc/shadow`, the same signal as role share's `coin-dojo-share-sync.path`,
#266) only ends the reminder. Never revoked. `make deploy BECOME=1` works once admin is on. Not promised: sudo
still trusts whoever types the password (a process as the user can keylog it via the `input` group). verify.sh:
with the default, no sudo group, no all-commands rule, the grant pending, the path unit enabled, and the SETTINGS
password helper's `<user> ALL=(root) NOPASSWD: /usr/local/sbin/coin-dojo-set-password ""` present (#271, no
arguments; it must stay, so SETTINGS > PASSWORD works without admin). On every image sudoers is an ALLOWLIST (#270
security review): each line sudo reads in `/etc/sudoers` and `sudoers.d/*` (continuations joined; `#include` and
`#<uid>` are not comments) is stock Pi OS (`SUDO_STOCK`) or one of our rules (poweroff, the two settings helpers,
`Defaults:<user> timestamp_timeout=0`, and `coindojo-admin` only once admin is on); only `/etc/sudoers` includes,
and only `/etc/sudoers.d`; both helpers are root:root and not group/other-writable. Otherwise the
user is in the `sudo` group; `/boot/firmware`'s fstab line must be root-only (no `uid=`/`gid=` but 0, every
`umask`/`fmask`/`dmask` keeps 022). Files in the user's home (`~/.ssh/authorized_keys`, the rename's dotfiles)
are written AS THE USER (a forked child after `setresuid`) and never through a symlink (each component
`O_NOFOLLOW`, temp `O_EXCL`): a planted `~/.ssh -> /etc` could otherwise hand `/etc` to the user. Proof: `make -C skills/build-image prove-firstboot` step 10 (real systemd, sudo,
the real `passwd` on a pty). A `password_hash:` or
`password: prompt` in `coindojo.yml`, or Imager's `userconf.txt`, replaces it at first boot. SSH stays key-only
(the password is for the console and, while it is 12345, also the network folder `roms`/`bios` from the local
subnets, role share; never sudo, below); `COIN_DOJO_SSH_PASSWORD=yes` with the default password is refused
by run.sh and by `--default-login`. `EDITION=personal` never gets a default.
Your settings live in **`coindojo.yml`** on the FAT boot partition (readable on any PC), applied once at first
boot by the `coindojo-firstboot` oneshot unit (before getty, sshd and NetworkManager):

- Start from `coindojo.example.yml` (in this folder, and on every image's boot partition). Keys, all optional:
  `user` (only the image's own user: see below), `password_hash` (`openssl passwd -6`; never a plain password) or
  `password: prompt` (asked on the cabinet screen, 5 minutes), `ssh_pubkey`, `hostname`, `timezone`,
  `wifi_ssid`, `wifi_psk`, `wifi_country`. A flat YAML subset: one `key: value` per line; anything else is refused.
- Either `make image ... CONFIG=my.yml` (checked before the build and copied to the boot partition), or copy
  the file onto the card's boot partition after flashing.
- After applying it the unit **deletes `coindojo.yml`** and leaves `coindojo.applied.yml` with `password_hash`
  and `wifi_psk` removed. The Wi-Fi PSK then lives only in a 0600 NetworkManager keyfile, the hash only in
  `/etc/shadow`. A new `coindojo.yml` on a later boot is applied the same way, to the user the cabinet has
  now (`/etc/coindojo/user`); without a `user:` line that user stays.
- **The user is fixed at build time (#244):** `make image` runs the roles, which write the user's name into units,
  sudoers, polkit, the session config and ES-DE's paths, and its uid into `/run/user/<uid>`. Set `hk_cabinet_user`
  and `hk_cabinet_uid` in your config before `make image`. chroot-setup.sh removes Pi OS's stock `pi` (uid 1000)
  and makes the user with `hk_cabinet_uid`; provision.sh stops when the uid differs; verify checks the session's
  runtime dir. A `user:` naming anyone else is refused before any change: nothing of the file is applied, the
  reason goes to `coindojo.error.txt` and the screen. `make image CONFIG=` refuses such a file already
  (`--check --user`). Same for an Imager `userconf.txt` with another name.
- Error messages (in `coindojo.error.txt`, on screen, in the journal) name the key and the line, never a value.
- A value that contains another key (`wifi_ssid: home wifi_psk: ...`, a lost line break) is refused for every
  key. The SSH key's comment may hold only letters, digits, spaces and `@._+=-` (up to 100). The SSID is never
  printed. What stays on boot afterwards is rewritten without inline comments, and only the comments of
  `coindojo.example.yml` itself are kept (a comment of yours may hold a secret).
- A **malformed** file is not applied: every line that could hold a secret is removed anyway (`password_hash`,
  `wifi_psk`, a plain `password`, keys in another case such as `Password_hash:`, unknown or misspelt keys,
  commented-out secrets; a Notepad UTF-8 BOM is fine), `coindojo.error.txt` says why, and the screen shows it.
- **Half-applied:** an error after some settings were applied (an unknown timezone is checked first, but e.g.
  a full disk) leaves those settings applied and the rest not; the secrets are removed all the same, so put a
  fixed `coindojo.yml` back to finish. A **power cut** during a user rename that an image from before #244
  started is resumed on the next boot (`/var/lib/coindojo/rename` names it); a new image never starts one.
- **No config at all:** the cabinet still boots and plays; before the frontend starts, tty1 shows a "no
  first-boot config" hint for 15 s (`/run/coindojo/no-config`, plus the default-login line on a community image).
- **Raspberry Pi Imager:** answer **No** / skip at its OS-customisation question and use `coindojo.yml`. Imager's
  OS customisation (a username, password, Wi-Fi, SSH) is **not supported**: its `firstrun.sh` renames the first
  account, and that is the cabinet user, whose name the image's services, sudoers and session already use. The
  cabinet then cannot start: every boot detects it and shows how to recover (flash again, skip the customisation).
  `coindojo-firstboot` checks on every boot that the image's user (`/etc/coindojo/built-user`) still exists; if
  not, nothing is applied and tty1 plus `coindojo.error.txt` say so. Imager's `firstrun.sh` no longer counts as
  "configured" (the no-config hint stays). An older Imager's `userconf.txt` is applied only for the image's own
  user (`<built-user>:<hash>`); another name is refused with a reason in `coindojo.error.txt`. Imager 2.x's cloud-
  init customisation is never applied: the image masks cloud-init (it costs 1-2 min per boot).
- The Keychain flow: without `CONFIG=`, `run.sh` generates the file from `COINDOJO_PASSWORD` (alias
  `CABINET_PW`, else the macOS Keychain item named by `hk_become_keychain`, only when `security` exists) and
  `COINDOJO_PUBKEY` (alias `COIN_DOJO_PUBKEY`; else `~/.ssh/id_ed25519.pub` in the Keychain flow).
  The password is hashed in the chroot from stdin; only the hash reaches the boot partition.
- Proof: `make -C skills/build-image prove-firstboot` runs the unit under real systemd in a container (full config,
  next boot, malformed, no config); `tests/test_coindojo.py` covers the parser and the scrub.

## Release on GitHub (make gh-release, #287)
The public download: the community image + its sources as a release on github.com/coin-dojo/coin-dojo
(COMPLIANCE.md section 6). One command, from the checkout whose `build/*/out` built the image:

    make gh-release VERSION=0.3.0 IMG=/path/to/coindojo.img          # dry run: builds the assets, prints the plan
    make gh-release VERSION=0.3.0 IMG=/path/to/coindojo.img APPLY=1  # uploads a DRAFT (re-uses the dry run's assets)
    make gh-release VERSION=0.3.0 IMG=/path/to/coindojo.img APPLY=1 PUBLISH=1  # after reading the draft: un-draft

`scripts/gh_release.py` (header = the full contract), work folder `<image dir>/<stem>-release/` (log `release.log`):
1. **Image:** its own record `/usr/share/doc/coindojo/licences/GATE.tsv` (written by `licences.py build`: strict
   on/off, warnings, blockers) must say strict on, 0, 0; `scripts/release-inspect.sh` attaches the image read-only
   in the Ansible container and re-runs `licences.py build --strict` over a tmpfs overlay (same bundle byte for byte)
   and `release-check --root`; the image must match the `.sha256` run.sh writes after verify. So: build the image
   with `HK_LICENCES_STRICT=1` (an image from before #287 has no record and is refused). `release.log` records
   which private commit built the image (`build-record:` line, from GATE.tsv's `version`, run.sh's `git describe`;
   resolved to a full sha when this checkout has it, `+dirty` named). **Known gap (#291, L6):** nothing refuses an
   image built from a private commit older than the published target's source, because the community export is one
   squashed commit that records no private commit, so "older" cannot be told reliably. Check it by eye: build the
   image from the same private commit you ran `make edition-publish` from.
2. **Sources** `<stem>-sources.tar.xz`: each built component of the image's `components.tsv` at its commit (git,
   submodules, no credential helper), `build/` from the published community tree (the strict export
   `make edition-publish` leaves in `~/.cache/coin-dojo/coin-dojo-publish`, stamped for the target sha; never the
   private checkout), each BUILDINFO, the ES-DE libraries' Ubuntu sources (`scripts/esde_sources.py`, from
   Launchpad, sha256-checked, ~375 MiB) and the Raspberry Pi archive sources (`scripts/rpi_sources.py --strict`, in
   the Ansible container for `gpgv`, ~775 MiB). A missing piece is named and refused. Our own files in it (`build/`,
   `buildinfo/`, `README.md`) must pass the edition gate scan; tar headers are root:root, no xattrs, no `._*`.
   A packed archive is re-used only while its inputs match, a digest of the gate's lists (denylist, private values,
   allow list) included: a changed list re-runs the scan (#291).
3. `SHA256SUMS`; 4. notes from `CHANGELOG.md` `[VERSION]` (else `[Unreleased]`) + the versions.lock table, scrubbed
   like the community export, must pass the edition gate scan (decoded forms too) and `licences.py release-check
   --body`; 5. every asset < 2 GiB.
6. **Upload** (APPLY=1 only): the token is the Keychain item `github-coin-dojo-release` (a fine-grained token,
   coin-dojo/coin-dojo only, Contents read/write; add it with `security add-generic-password -s
   github-coin-dojo-release -a coin-dojo -w`, which prompts). Target = the sha in
   `~/.cache/coin-dojo/edition-published.tsv`, which must be on GitHub's main. A draft is made, assets already there
   with the same size and digest are kept (a re-run resumes), then 7. sizes and GitHub's sha256 digests are checked;
   the release stays a draft. `PUBLISH=1` (with `APPLY=1`) re-verifies, runs the step-4 gate scan and release-check on
   the draft's text as GitHub holds it (edits made there are gated too, #291), refuses when `refs/tags/v<VERSION>` exists at
   another sha (the draft stays), un-drafts and checks the tag. The token goes only to api.github.com and
   uploads.github.com; a redirect elsewhere or to plain http drops it. `make edition-publish` keeps release tags
   `vX.Y.Z` on the remote (#287); main must still be the last publish.

## Flash + hardware test (the only step that needs the cabinet)
Check the image first: `sha256sum -c <out.img>.sha256` (macOS: `shasum -a 256 -c <out.img>.sha256`).
Use a **spare card** (16 GB or more) so the stock card stays untouched.
- **Raspberry Pi Imager:** "Choose OS" > "Use custom" > `<out.img>`, choose the card, write. Answer **No** to
  Imager's customisation (it renames the cabinet user: not supported, see above); put `coindojo.yml` on the boot partition.
- **Linux:** `lsblk` (find the card, confirm its size), `sudo umount /dev/sdX?*`, then
  `sudo dd if=<out.img> of=/dev/sdX bs=4M conv=fsync status=progress`.
- **macOS:**
```
diskutil list                       # find the card's /dev/diskN  (confirm size!)
diskutil unmountDisk /dev/diskN
sudo dd if=<out.img> of=/dev/rdiskN bs=4m status=progress
```
Then copy `coindojo.yml` onto the boot partition if you did not build it in. Boot on the cabinet, then confirm
the three hardware-only things that can't be checked offline:
1. **1080p** on the RTD2662 panel, **audio** out the speakers.
2. **Stick**: all 6 buttons + Coin/Start; fix the one open question — top-row vs bottom-row
   punch/kick order — by swapping the `input_*_btn` lines in `~/.config/retroarch/autoconfig/udev/USB gamepad.cfg`.
3. GPU speed on the heavier cores (flycast/PPSSPP).

## Network (first boot)
No network is required to play — the image boots to ES-DE and runs ROMs offline. Network is only
needed to scrape art/videos or update.
- **Ethernet (recommended): zero config.** Plug in and it just works.
- **WiFi: `wifi_ssid`/`wifi_psk`/`wifi_country` in `coindojo.yml`** (above), or the older way:
- **WiFi: edit `wifi.conf` on the boot partition.** After flashing, the card's FAT **boot** volume
  (mounts on any Mac/PC) has `wifi.conf`. Fill in `SSID=` and `PSK=` with your own network, save,
  boot. The `coin-dojo-wifi` service imports it into NetworkManager on boot, then **blanks the file**
  so the password doesn't linger in plaintext. The radio/regdom are pre-enabled. **No credentials are
  baked into the image** — each user supplies their own.

## SSH access (key-only by default, #41, #240)
- The image ships `/etc/ssh/sshd_config.d/00-coin-dojo-keyonly.conf`: `PasswordAuthentication no`,
  `KbdInteractiveAuthentication no`, `PermitRootLogin no`. sshd keeps the first value it reads, so the `00-` name
  wins over every later drop-in. The legacy `99-arcade.conf` (`PasswordAuthentication yes`) is removed.
- **Your key arrives at first boot** (`ssh_pubkey` in `coindojo.yml`); the image has none, and no SSH host keys
  (each card makes its own at first boot). The password is for the local console and the network folder (`sudo` only once admin is on), never for SSH.
  The stock `pi` user (when the base has one) leaves sudo and its password is locked; root is locked.
- `verify.sh` fails the build on any baked secret: a hash in `/etc/shadow` (except the community default login:
  then the user's hash must be exactly the recorded default, every other account locked, and SSH must refuse
  passwords; whether the boot config gives a password is asked of the image's own `coindojo-firstboot
  --sets-password`, the build's rule, so an empty `password_hash: ""` counts as none everywhere), an `authorized_keys`, SSH host keys, private key material, the build password or the boot config's
  secrets anywhere in the root filesystem. The user's uid is checked against `hk_cabinet_uid`.
- **Opt-in, not recommended:** `COIN_DOJO_SSH_PASSWORD=yes skills/build-image/scripts/run.sh ...` builds an
  image that also accepts password SSH (a LAN reader who guesses the password gets root via sudo).
  `verify.sh` then prints `WARN: password SSH enabled` instead of the key-only `PASS` lines.
- The live cabinet gets the same drop-in from the Ansible role `ansible/roles/sshd_keyonly`.
- Check: `ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no coindojo@<cabinet>` must fail
  with `Permission denied (publickey)`.

## Why a Mac can't boot-test it
Pi firmware won't boot an ARM image in a VM/container, so rendering (GL/EGL), the monitor, and
the controller can only be validated on the cabinet. Everything else is validated by `verify.sh`.

## Files
`scripts/run.sh` (host orchestrator) · `lib-host.sh` (host checks, pinned images) · `imgsize.py` (card size) · `base-image.sh` (pinned base fetch) · `prep-image.sh` · `build.sh` · `chroot-setup.sh`
(the base OS) · `provision.sh` (the roles, `ansible/image.yml` + `ansible/inventory/image.yml`) · `firstboot-expand.sh` (grow data partition on first boot) ·
`wifi-import.sh` (import user WiFi from boot-partition `wifi.conf`, Ethernet-first) · `coindojo-firstboot.py`
(first-boot config) · `verify.sh` · `../coindojo.example.yml` · release: `gh_release.py`, `release-inspect.sh`,
`esde_sources.py`, `rpi_sources.py`, `licences.py`.
Tests in `tests/`.
