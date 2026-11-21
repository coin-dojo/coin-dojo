# Reference

Look-up tables: make targets, config variables, the first-boot file, the SETTINGS app, buttons and combos, the
theme, log files and `fcade://` links. `make help` lists every target and flag and wins over this page.

## Contents

- [Make targets](#make-targets)
- [Config variables](#config-variables)
- [First-boot file](#first-boot-file)
- [SETTINGS app](#settings-app)
- [Buttons and combos](#buttons-and-combos)
- [Themes](#themes)
- [Log files](#log-files)
- [fcade:// links](#fcade-links)

## Make targets

Run from the repo root. Targets that touch the cabinet or a host are **dry runs unless `APPLY=1`**. Flags
(`APPLY=1`, `BECOME=1`, `TAGS=`, `LIMIT=`, `V=1`, `hk_<name>=value` and the rest) are listed at the end of `make help`.
The descriptions below are `make help`'s own words.

<!-- make-targets: generated from `make help`; docs/tests/test_customer_docs.py fails when a target is missing -->
| Target | What it does |
|---|---|
| `make commit` | Guided Conventional Commit (gum): type, scope, #ticket, summary, description; refuses with nothing staged |
| `make hooks` | Install the commit-msg hook (git config core.hooksPath .githooks; worktrees share it) |
| `make test` | Run every test: each area's `make test`, then the repo-wide ones (takes the heavy CI lock, #119) |
| `make test-list-all` | Print every test file `make test` runs (all areas) |
| `make preview` | Local ES-DE theme preview (skills/local-preview esde; PREVIEW_OUT=, VIEW=) |
| `make setup` | One-time: venv ~/.venvs/coin-dojo (PyYAML + Pillow), the commit hook, config/local.yml from the example when you have no local config |
| `make setup-config` | Copy config/local.example.yml to config/local.yml unless a local config exists (never overwrites) |
| `make doctor` | Check this Mac: python, venv, config resolves, ssh key, docker/colima, limactl, gum, Keychain item present (never its value) |
| `make rollout` | Dry run; APPLY=1: + apply, 2nd dry run must recap changed=0 (tools/recap.py), esde-verify, SMOKE=1 smoke; logs in HK_CACHE/rollout |
| `make base-image` | Fetch the official Raspberry Pi OS Lite arm64 image, sha256-pinned (BASE_DIR=; prints BASE_IMG=) |
| `make image` | Build the OS image (skills/build-image image; BASE_IMG=, IMAGE_OUT=, CONFIG=, MIN_CARD_GB=) |
| `make build` | Build flycast + the Stella core in arm64 containers (build all) |
| `make config-check` | Validate the configuration (unknown name with did-you-mean, type, range, missing host); runs before every deploy |
| `make config` | Show every effective variable and where its value comes from (LIMIT=coin-dojo-vm for the VM) |
| `make config-docs` | Regenerate docs/CONFIG.md from config/variables.d/*.yml (deterministic) |
| `make config-venv` | One-time: create the PyYAML venv CONFIG_VENV (~/.venvs/coin-dojo) the config loader needs |
| `make help` | List targets and flags |
| `make py-test` | Run this folder's python tests (plain python3; FAIL on exit != 0 or a FAIL line) |
| `make py-list` | Print the test files `make test` runs here (paths from the repo root) |
| `make edition-files` | List the git-tracked files of EDITION (community = without personal/**) |
| `make edition-check` | Check EDITION: community has no personal/** files, and the ship/no-artifacts gate passes |
| `make vm-up` | Create (first time) or start coin-dojo-vm, the test VM (lima/vz, 4 CPU / 4 GiB; waits for cpu-gate) |
| `make vm-down` | Stop coin-dojo-vm (the screen session first); always leave it stopped when done |
| `make vm-ssh` | Shell in coin-dojo-vm as coindojo (limactl shell) |
| `make vm-status` | lima status of coin-dojo-vm; when running also load, memory and the screen session |
| `make vm-provision` | Ansible: vm.yml on coin-dojo-vm (vm_base + cabinet roles), applied; vm-guard runs first |
| `make vm-smoke` | Smoke test in the VM: cage+ES-DE, then RetroArch+core+test ROM; alive at 10 s + screenshots |
| `make vm-watch` | Watch the VM screen on this Mac: ES-DE session + wayvnc, opens vnc://127.0.0.1:5924 (Screen Sharing) |
| `make vm-watch-stop` | Stop the VM's screen session and wayvnc (the VM keeps running; make vm-down stops it) |
| `make vm-destroy` | Delete coin-dojo-vm and its disk (dry run unless APPLY=1); vm-up recreates it from the template |
| `make smoke-launch` | RUNS emulators: one game per system via launch.py on the live session, alive at 10 s + grim shot, contact sheet; LIMIT=coin-dojo-vm for the VM |
| `make cabinet-guard` | Stop unless the cabinet is reachable and no game (retroarch, retroarch-1.22, flycast) is running |
| `make esde-restart` | Restart ES-DE on the cabinet (pkill -9 -x AppRun, check AppRun + getty); APPLY=1 |
| `make cabinet-status` | Read-only health in one ssh call: host, uptime, ES-DE, running game, page size, df, zram, deployed REV vs this checkout |
| `make net-check` | Read-only 60 s ping from the cabinet to a public host + its gateway: avg/max/mdev/loss (#213; NET_LOCAL=1 = this machine, NET_SECONDS=n) |
| `make cabinet-ssh` | Interactive shell on the cabinet (key auth; CABINET_SSH_TTY) |
| `make cabinet-shot` | Full-size screenshot of the live screen (grim to stdout, nothing written on the cabinet) -> HK_CACHE/shots/<stamp>.png |
| `make lint` | shellcheck, yamllint, ruff, ansible-lint (+ site.yml syntax, through LINT_ANS like the rest, #133) in the pinned image; LINT_ONLY=shell\|yaml\|python\|ansible |
| `make secrets` | gitleaks on a bare clone made in the pinned image: history (HEAD; GITLEAKS_ALL_REFS=1 = all refs) + working copy |
| `make test-changed` | BASE=origin/main: only the tests the changed paths need (ci/rules.tsv), plus the always list |
| `make test-file` | F=path/test_x.py: one test file (light lane, seconds) |
| `make test-area` | A=ansible\|collections\|controls\|theme\|brand\|preview\|settings\|build\|vm\|smoke\|tools\|docs\|always (comma list ok) |
| `make test-list` | BASE=origin/main: show the rule that matched each changed path, the areas, lane and test files |
| `make ci` | [BASE=origin/main] [FULL=1]: lint + security + the tests a change needs (FULL: all tests + community edition + config + changelog) |
| `make versions` | Regenerate `versions.lock` from the pin sources (every pinned third-party component, #278) |
| `make versions-show` | Every pinned component with the file:line of each pin |
| `make bump` | C=<component> V=<version> [SHA=] [COMMIT=] [URL=]: change a pin everywhere + versions.lock + the CHANGELOG line ([skills/versions](../skills/versions/SKILL.md)) |
| `make deploy` | Run site.yml: --check --diff unless APPLY=1 (TAGS=, BECOME=1, LIMIT=, W=) |
| `make deploy-theme` | Theme: named files + _inc dirs, then personal/themes (dry run unless APPLY=1) |
| `make deploy-collections` | Nested main screen (+ W=DIR edited gamelists): build, validator gate, write (dry run unless APPLY=1) |
| `make deploy-esde` | Theme + collections + verify in one run (one ES-DE restart at most) |
| `make deploy-launch` | launch.py: py_compile + argv gate, backup + launch.py.prev, install, post-check (dry run unless APPLY=1) |
| `make launch-rollback` | Put launch.py.prev back as launch.py (dry run unless APPLY=1; LIMIT=) |
| `make esde-verify` | Read-only: per-system counts, validator on the live nested_out, ES-DE log; FAILs on a mismatch |

**Maintenance** (not needed to build or run a cabinet):

| Target | What it does |
|---|---|
| `make cache-migrate` | Move the old ~/.cache/coin-dojo-{ansible,preview,vm,ws} folders under HK_CACHE (~/.cache/coin-dojo); dry run unless APPLY=1 |
| `make backup-config` | Pull the cabinet's config allowlist (read-only) into `personal/backup/<host>/`, secrets REDACTED; dry run unless APPLY=1; never commits |
| `make build-fetch` | Fetch the pinned RetroArch + N64 build outputs from the cabinet (read-only, sha256-checked) for the VM role (DRY=1) |
| `make gh-release` | VERSION=x.y.z IMG=FILE: check a strict-built image, build its sources archive, SHA256SUMS and notes (all gate-scanned); APPLY=1 uploads to a draft GitHub Release, APPLY=1 PUBLISH=1 publishes it; dry run by default |

**Emulator and Fightcade builds** (`make -C build help`; each runs in a pinned arm64 container, nothing is
committed):

| Target | What it does |
|---|---|
| `make -C build test` | build.sh tests for every build here |
| `make -C build all` | Build everything below (one after the other) |
| `make -C build fetch` | Fetch the pinned RetroArch 1.22.2 + N64 core into build/*/out from the cabinet (read-only), sha256-checked (DRY=1 lists) |
| `make -C build flycast` | Standalone flycast v2.7 (build/flycast/build.sh, ~10 min) |
| `make -C build stella` | Stella 7.0 libretro core (build/stella/build.sh, ~80 s) |
| `make -C build retroarch` | RetroArch v1.22.2 + patches/, GLES/Wayland (build/retroarch/build.sh, ~6 min) |
| `make -C build n64` | GLES3 Mupen64Plus-Next core, rpi5_64_mesa (build/n64/build.sh, ~4 min) |
| `make -C build box64` | Box64 v0.4.4 for the Pi 5 (build/box64/build.sh, ~10 min; needs ~4 GB docker memory) |
| `make -C build cores` | The 8 image cores from pinned commits, one after the other (build/cores/README.md; MAME takes hours) |
| `make -C build fightcade` | Fetch the pinned Wine 10.0, Electron 9.1.0 and Fightcade 2.1.45 into build/fightcade/out (sha256-checked) |

Each skill folder has its own `make help` too (for example `make -C skills/badges help`).

## Config variables

Every variable, its type, default, allowed range and which role reads it: [CONFIG.md](CONFIG.md). That page is
generated from `config/variables.d/*.yml` by `make config-docs`; never edit it by hand. Your own values go in
`config/local.yml` (`make setup` creates it). Useful commands:

| Command | What |
|---|---|
| `make config` | every effective value and where it comes from |
| `make config-check` | type, range, unknown names (with a did-you-mean); runs before every deploy |
| `make config hk_cabinet_uid=1002` | any target takes a one-off override |

## First-boot file

`coindojo.yml` on the card's `boot` partition, applied once at first boot (#237). Start from
`skills/build-image/coindojo.example.yml` (also on every image's boot partition), or build it in with
`make image ... CONFIG=coindojo.yml`. One `key: value` per line; every key is optional; anything else is refused.

| Key | What | Example |
|---|---|---|
| `user` | leave empty: only the image's own user (`hk_cabinet_user` at build time) is accepted; another name is refused and nothing applies (#244) | `coindojo` |
| `password_hash` | the password as a crypt hash, never plain text: `openssl passwd -6`. Empty: a community image keeps its default login `coindojo` / `12345` (change it in SETTINGS > PASSWORD) with admin (`sudo`) off and SSH key-only; your own password (a hash here, or SETTINGS > PASSWORD) turns admin and SSH by password on (#299) | `$6$...` |
| `password: prompt` | instead of a hash: asked on the cabinet screen at first boot (a keyboard, 5 minutes) | `prompt` |
| `ssh_pubkey` | your SSH public key, one line; keys always work, passwords only once you set your own | `ssh-ed25519 AAAA... me@laptop` |
| `hostname` | the cabinet's network name | `dojo` |
| `timezone` | a zone from `/usr/share/zoneinfo` | `Europe/Berlin` |
| `wifi_ssid`, `wifi_psk` | Wi-Fi network and password (WPA2/WPA3 personal; empty psk = open network) | |
| `wifi_country` | two letters; the radio stays off in some countries without it | `DE` |

After it is applied, `coindojo.yml` is deleted and `coindojo.applied.yml` stays, with `password_hash` and
`wifi_psk` removed. A broken file is not applied, its secret lines are removed anyway, and `coindojo.error.txt`
names the key and line (never a value). A new `coindojo.yml` on a later boot is applied the same way. Without
any file the cabinet still boots, after a 15 s hint. An older Raspberry Pi Imager's `userconf.txt` works for the image's own
user only; Imager's OS customisation is not supported (it renames the cabinet user: answer No in Imager). All the rules: [skills/build-image/SKILL.md](../skills/build-image/SKILL.md).
[BUILD.md](BUILD.md#5-first-boot) shows where it fits.

## SETTINGS app

The last entry on the game menu's carousel. One press opens it; leaving returns to the carousel with SETTINGS
selected (#224). It works with the stick and panel buttons or a USB keyboard. Details:
[skills/settings/SKILL.md](../skills/settings/SKILL.md).

| Page | Options |
|---|---|
| SETTINGS (first page) | current Wi-Fi (network, signal, address), the cabinet's network name; WI-FI, CABINET NAME, PASSWORD, GAME LISTS, ATTRACT MODE, SHOW FPS, RETROACHIEVEMENTS, TIME ZONE, BACK |
| WI-FI | SCAN AGAIN, every visible network (signal bars, lock = secured, CONNECTED / SAVED), BACK |
| a network | CONNECT / ENTER PASSWORD, FORGET THIS NETWORK, NEW PASSWORD, BACK |
| CABINET NAME | rename the cabinet on the network (a-z, 0-9, `-`), asks first |
| ATTRACT MODE | ON / OFF (OFF stops the automatic start), START AFTER 5 / 10 / 30 / 60 MIN, SHOW video / slideshow / dim / black; asks to reload ES-DE to apply. Your choice wins over `hk_attract_timer_ms` / `hk_attract_type` on every deploy (those are only the first-install defaults) |
| SHOW FPS | frames-per-second counter ON / OFF in the corner of RetroArch and Dreamcast games (`hk_benchmark_mode` is only the first-install default) |
| RETROACHIEVEMENTS | ON / OFF, casual or hardcore, LOG IN / LOG OUT (your own account) |
| TIME ZONE | a short list of common zones, MORE ZONES (region, then city) |
| PASSWORD | CHANGE PASSWORD: type the new password twice (hidden), at least 6 characters and not 12345; it changes the cabinet login and the network folder together |
| GAME LISTS | REBUILD GAME LISTS (MUST PLAY, genres, decades, series, systems from your scraped games, then asks to reload ES-DE), MUST PLAY SIZE (10 / 15 / 25 / 50 per system), MUST PLAY GENRES MIN STARS (4.0 / 4.5 / 5.0), RELOAD ES-DE. Needs a scrape first; refuses while a game runs. A personal image is managed from the Mac and refuses |

| In the app | Arcade panel | USB keyboard |
|---|---|---|
| move | stick (hold to repeat) | arrows |
| select, type a key | LK | Enter, or just type in a field |
| back, delete | MK | Esc = back, Backspace = delete |
| done, connect | START | Enter |
| quit the app from any page | SELECT (or SELECT+START) | Esc on the first page |

## Buttons and combos

Panel names: punches **LP MP HP**, kicks **LK MK HK**, **START**, **SELECT**, the **STICK**. A `+` means pressed
together.

| Where | Combo | Does |
|---|---|---|
| Boot menu | STICK, then LK | pick ARCADE or FIGHTCADE; any input stops the 5 s countdown (#210) |
| Game menu (ES-DE) | LK / MK | open / back |
| Game menu | QUIT TO MAIN MENU on the carousel | back to the boot menu (#214, #224) |
| Game menu, no game running | tap LP | the TROPHIES overlay (RetroAchievements); any button closes it |
| RetroArch game | SELECT+START | quit the game, back to the menu |
| RetroArch game | START+MP | RetroArch's own menu |
| flycast game (Dreamcast, NAOMI) | SELECT+START | quit the game |
| flycast game | START+MP | flycast's own menu |
| Fightcade lobby | STICK | move the pointer (faster the longer it is held) (#211) |
| Fightcade lobby | LK / MK | left click / right click (#211) |
| Fightcade, anywhere | SELECT+MK | pointer off / on by hand; it switches off by itself in a game (#231; was SELECT+LK until #227) |
| Fightcade lobby | SELECT+LK | open / close the on-screen keyboard; does nothing in a game (#227) |
| Fightcade keyboard (open) | STICK / LK / MK | move the focus / type the key / delete; the pointer is off meanwhile (#227) |
| Fightcade keyboard (open) | START / SELECT | close and send a real Enter, as a USB keyboard does / close (#227, #262) |
| Fightcade game | hold SELECT+START 2 s | close the game only (its window is asked first: measured 0.1-0.5 s on the VM, Pi to be measured), back to the lobby, pointer back on (#231, #257) |
| Fightcade lobby | hold SELECT+START 2 s | leave Fightcade, back to the boot menu; not within 5 s after a game closed (#231, #257) |
| SETTINGS | SELECT | quit the app from any page (#224) |
| Power button | press, then press again within the countdown | safe power-off (one press shows a confirm screen) |

Per-game button layouts (controls cards): [skills/controls/SKILL.md](../skills/controls/SKILL.md).

## Themes

The game menu's theme lives in `themes/` and uses the style kit in [brand/BRAND.md](../brand/BRAND.md) (colours,
fonts, the 4:3 safe area: everything a player reads sits inside the centred 1440x1080). Preview it on your computer
with `make preview` ([skills/local-preview/SKILL.md](../skills/local-preview/SKILL.md)), deploy it with
`make deploy-theme`.

Make your own theme: [themes/README.md](../themes/README.md) (#235): folder layout, copying the theme to a new name, the
per-system art, safe margins, fonts and licences, tests, deploying, and selecting it in ES-DE.

## Log files

On the cabinet (`~` = the cabinet user's home):

| What | Log |
|---|---|
| ES-DE | `~/ES-DE/logs/es_log.txt` |
| Session loop (boot menu, mode switches, cleanup) | `~/.cache/coin-dojo/session.log` |
| Boot menu | `~/.cache/coin-dojo/bootmenu.log` |
| SETTINGS app, return to the carousel | `~/.cache/coin-dojo/settings.log`, `~/.cache/coin-dojo/settings-return.log` |
| QUIT / FIGHTCADE entries in ES-DE | `~/.cache/coin-dojo/esde-entry.log` |
| flycast (last game) | `/tmp/flycast-last.log` |
| Fightcade lobby | `/data/fightcade/logs/lobby.log` |
| `fcade://` links | `/data/fightcade/logs/fcade-url.log` (the link itself is never logged) |
| Fightcade's own helper | `/data/fightcade/opt/Fightcade/emulator/fcade.log` |
| Wine prefix build (deploy) | `/data/fightcade/logs/wineboot.log` |
| System services (power button, TROPHIES, errors) | `journalctl -b` (errors only: `journalctl -b -p err`) |

The Fightcade log folder is private (mode 0700): its logs may name match links. On the computer you deploy from,
`make rollout` keeps its logs in `~/.cache/coin-dojo/rollout/`. Read-only health in one call: `make cabinet-status`.

## fcade:// links

Joining a match in the Fightcade lobby opens an `fcade://` link (#208, #228):

1. A desktop entry registers the `x-scheme-handler/fcade` type, so the lobby's link opens the wrapper `fcade-url`.
2. `fcade-url` runs Fightcade's own `fcade` helper (an x86_64 Linux program) under Box64, from its own folder, as
   Fightcade's `fcade.sh` does. The link is not logged: it can carry a match id.
3. `fcade` starts the emulator `.exe`. A `binfmt_misc` entry (`/etc/binfmt.d/coin-dojo-wine.conf`) sends Windows
   programs to the Wine wrapper, which runs Box64 + Wine 10.0 with the RAM prefix (#228).

Links the lobby does not own (other schemes and sites) go to `xdg-open`; no web browser is installed. Fightcade's
self-updater cannot run on ARM: updates come by re-pinning the download (`build/fightcade/`).
