# ansible/ — deploy the cabinet (Debian 13 + ES-DE) over SSH

Config, launcher, ES-DE theme and collections for the **running cabinet** (`coin-dojo`, user `coindojo`, key
auth, sudo), no SD-card swapping.

## Prerequisites

- **Docker (Colima)** on the laptop, nothing else: Ansible runs in a pinned container (`bin/ans`, `Dockerfile`,
  `requirements.txt` = ansible-core, `requirements.yml` = exact collection pins). The image
  `coin-dojo-ansible:<hash of those 3 files>` builds on first use (~30 s) and after a pin change;
  `make -C ansible ansible-image` builds it ahead of time.
- **A reachable cabinet**: host, user and key come from your config (`hk_cabinet_host`, `hk_cabinet_user`,
  `hk_ssh_key`; `../docs/SETUP.md`), rendered into the inventory by `bin/deploy`. The sudo password, when a task
  needs it, comes from `COIN_DOJO_BECOME_PASS` or the Keychain item named by `hk_become_keychain` (never in git).

## Deploy flow

With make (from the repo root or here; ARCH/EDITION become `-e coin_dojo_arch=... -e coin_dojo_edition=...`):

    make deploy                                   # bin/deploy -- --check --diff -e ... (dry run, default)
    make deploy TAGS=dreamcast,cabinet_tweaks BECOME=1
    make deploy APPLY=1                           # writes; runs cabinet-guard first (no game running)
    make -C ansible ansible-ping | ansible-syntax | test
    make deploy LIMIT=coin-dojo-vm [APPLY=1]       # the test VM on the Mac (#82, vm.yml); vm-guard, never cabinet-guard

**Test VM** (`coin-dojo-vm`, #82): try risky cabinet changes on a Debian 13 VM first: `make vm-up vm-provision
vm-smoke`, then `make vm-down`. Inventory `inventory/vm.yml` (group `vm`), play `vm.yml` (imported last by
`site.yml`), role `vm_base`. Details, skipped roles and limits: `vm/README.md`.

**Every run is limited to one host** (`--limit coin-dojo` by default; `LIMIT=coin-dojo-vm` / `DEPLOY_LIMIT=coin-dojo-vm`
for the test VM; any other value is refused). `bin/deploy` check/apply/ping add the same limit;
`bin/deploy -- <raw args>` passes args as given.

The plain `bin/deploy` commands still work:

    bin/deploy ping                 # SSH reachability (ansible -m ping)
    bin/deploy check                # DRY-RUN everything (--check --diff). Always start here.
    bin/deploy apply                # apply everything
    bin/deploy check dreamcast      # dry-run one role by tag

## Secrets (never in git)

Nothing secret lives in this repo. At deploy time `bin/deploy` reads the sudo password from env
`COIN_DOJO_BECOME_PASS` or the macOS Keychain item named by `hk_become_keychain`, and passes it to Ansible via a `0600`
temp extra-vars file (deleted on exit), so it never appears in the process list.

## Host keys

`ansible.cfg` uses `StrictHostKeyChecking=accept-new`: a cabinet reflashed to a spare card is
trusted on first contact, but a *changed* key on a known host still fails (reflash → clear its
line in `~/.ssh/known_hosts`).

## Layout

    ansible.cfg              # one host, rsync-friendly ssh
    site.yml                 # the playbook (play 1: the cabinet, group `pios`; then vm.yml)
    image.yml                # #244 the same roles into an image (make image; chroot, inventory/image.yml)
    requirements.yml         # galaxy collections
    inventory/hosts.yml      # the cabinet (`coin-dojo`); inventory/vm.yml = the test VM; inventory/image.yml = the image
    group_vars/all.yml       # build flags (coin_dojo_arch, coin_dojo_edition, coin_dojo_image); esde.yml = ES-DE paths
    bin/deploy               # wrapper: check / apply / ping
    roles/                   # cores dreamcast cabinet_tweaks retroarch122 launcher fightcade power sshd_keyonly
                             # journald diskhygiene session share settings esde_common esde_theme esde_collections esde_verify
                             # vm_base
    tests/                   # structure + secrets lint, "no orphan role" (python3, no deps) + the broken-case doc

## Image build (#244, `image.yml`)

`make image` (skills/build-image) runs `image.yml` into the image's root filesystem over a chroot connection: the
cabinet roles of `site.yml`, same order, minus `sshd_keyonly` (a live sshd switch) and `esde_verify` (a running
ES-DE); `fightcade` only with `hk_image_fightcade: true`. `coin_dojo_image: true` (default false, `group_vars/all.yml`)
is the one switch a role reads to skip what needs the running cabinet: unit start/restart/reload, `sysctl --system`,
`modprobe`/udev, binfmt registration, `nmcli`, `timedatectl`, the ES-DE restart, live-kernel asserts. Those happen
at the image's first boot from the files the roles write. `esde_collections` writes the starter carousel instead of
the nested screen (SETTINGS + QUIT TO MAIN MENU from nested.py, no `<loadExclusive/>`). A new role task that acts on
the live system needs the guard: `tests/test_image_play.py` fails otherwise. Role `cores` (#244, also in `site.yml`):
the cores and ROM folders `skills/collections/launch.py` names (`files/launcher_table.py`), only the missing ones.
A missing core comes from your own build of its pinned commit (`build/<core>/out`, checked against its .sha256;
`make -C build cores` builds the eight, Stella has `build/stella`), for `make deploy` and the image alike (#274).
Without that build output a release stops; any other run falls back to the libretro buildbot (unpinned `latest`)
with one WARN line per core. It also creates missing launcher ROM folders; it never deletes or replaces anything.

## Debian cabinet roles (group `pios`)

`dreamcast` (standalone flycast: binary copied from `dreamcast_flycast_binary_src`, launcher, exit
combo, BIOS symlinks, emu.cfg keys, ES-DE rule) and `cabinet_tweaks` (4 KB kernel in
`/boot/firmware/config.txt`, zram swap ensured, RAM only with no SD-card swap file (#52, #91: `cabinet_swap_mechanism: zram`), RetroArch hotkeys/core options, per-core remaps
copied from `skills/controls/remaps` with `remap_save_on_exit` off (#56; refuses to run while RetroArch
is running), blank cursor, ES-DE collection keys (es_settings.xml: lineinfile only, `no_log`, mode 0600,
no backup copy: it holds the ScreenScraper password, #42), `netplay_public_announce = "false"` (#42), the
stock `pi` user out of the `sudo` group (`ansible.builtin.user`, its other groups kept) and
`/etc/apt/apt.conf.d/50-coin-dojo-nocache` (`APT::Keep-Downloaded-Packages "false"`; unattended upgrades
stay off))
run in the cabinet play of `site.yml` against `coin-dojo` (user `coindojo`, sudo: pass
`--ask-become-pass`). The kernel change only prints "REBOOT REQUIRED"; nothing reboots. A changed ES-DE
key or gamelist pin notifies `restart es-de` (below). Dry-run first: `make deploy TAGS=cabinet_tweaks BECOME=1`.

### Network for online play (#213, `cabinet_tweaks`, config area `network`)

- **Ethernet is the recommendation for online play** (Fightcade, netplay): jitter about 1 ms and no loss. Rollback
  netcode hides steady latency; jitter and loss are what make characters stutter and teleport.
- **On Wi-Fi, use 5 GHz** and keep power save off. `cabinet_tweaks` writes
  `/etc/NetworkManager/conf.d/90-coin-dojo-wifi.conf` (`[connection] wifi.powersave = 2`, `hk_wifi_powersave: false`,
  the default): the default for every Wi-Fi connection, also ones the SETTINGS app joins later. Measured on the
  cabinet 2026-11-18 (60 s, `make net-check`): public host 7.5 % loss, avg/max/mdev 21.9/130.7/29.8 ms with power
  save on; 0 %, 13.8/56.3/8.1 ms off. Wi-Fi to the router stays about 8 ms with spikes either way.
- **Applying it.** The deploy writes the drop-in and runs `nmcli general reload conf` (no link drop). A connection
  takes the new default at its next activation (reboot, reconnect, a network joined in SETTINGS); the deploy prints
  a `WARN Wi-Fi <dev>: power save is still on` line with the exact command for each live link that still has it on.
  It never reconnects by itself: the deploy may run over that Wi-Fi. To apply at once without sudo (the link drops
  for a few seconds), on the cabinet: `nohup nmcli connection up uuid <uuid> >/dev/null 2>&1 &` (nohup: the SSH
  session may drop and come back). `nmcli device reapply` refuses a powersave change.
- **No sudo for that reconnect**: `/etc/polkit-1/rules.d/51-coin-dojo-net.rules` (`hk_wifi_reconnect_polkit: true`)
  lets the cabinet user run `org.freedesktop.NetworkManager.network-control` from any session, SSH included. Its real
  scope: bring any saved connection up or down, disconnect a device or set it unmanaged (over Wi-Fi SSH that locks
  you out until you fix it at the seat or reboot), change a device at runtime (IP, DNS, routes; not saved); it never
  creates or saves a connection. One action only; scan, saving and forgetting networks stay with role `settings`'
  `50-coin-dojo-settings.rules`, which grants them only at the cabinet's own seat (local, active session).
- **Router**: Fightcade does its own NAT traversal; nothing to open for normal matches. If matches will not connect,
  enable UPnP on the router or forward the UDP port Fightcade shows. No SSID or network name lives in this repo.
- **Measure**: `make net-check` (60 s ping from the cabinet to `hk_net_check_host`, default 1.1.1.1, and its
  gateway; avg/max/mdev/loss, WARN over 1 % loss or 10 ms mdev; `NET_SECONDS=n`, `NET_LOCAL=1` = this machine). Run
  it before and after a network change and put both on the ticket.
- No NetworkManager (the test VM): the Wi-Fi tasks print `SKIP ... NetworkManager is not installed` and write nothing.
  Revert: `hk_wifi_powersave: true` removes the drop-in; `hk_wifi_reconnect_polkit: false` removes the rule.
  Tests: `ansible/roles/cabinet_tweaks/tests/test_wifi_powersave.py`, `tools/tests/test_net_check.py`.

`retroarch122` (#63, tags `retroarch122`, `n64`): RetroArch 1.22.2 for **all** RetroArch systems
(`/data/emulators/retroarch-1.22/{retroarch,BUILDINFO}`; launch.py points at it, #62), the GLES N64 core
(`/data/emulators/mupen64plus-gles/mupen64plus_next_libretro.so`), main `retroarch.cfg` `input_driver` /
`input_joypad_driver = udev`, the N64 core override `Mupen64Plus-Next.cfg`, the panel remap
`Mupen64Plus-Next.rmp` and `.opt` keys `cpucore=dynamic_recompiler`, `rdp-plugin=gliden64`. Refuses to run
while `retroarch` or `flycast` is running. **Artifacts** (never in git): `build/retroarch/out/{retroarch,BUILDINFO}`
from `build/retroarch/build.sh` and `build/n64/out/mupen64plus_next_libretro.so` from `build/n64/build.sh` (#61);
they must sit inside the checkout (bin/ans mounts only the repo). Each is sha256-pinned in
`roles/retroarch122/defaults/main.yml` (the live files of 2026-11-14): a different local build fails before
any copy; with no local build the role skips the copy but still asserts the cabinet file matches its pin.
No NAS copy of these builds exists yet. Dry run: `make deploy TAGS=retroarch122 BECOME=1`.
CRT (#102): `auto_shaders_enable` + `video_shader_enable = true`, `crt/crt-pi.glslp`, `crt/shaders/crt-pi.glsl` and
`stock.glsl` downloaded on the cabinet from libretro/glsl-shaders at a pinned commit (sha256 in
`defaults/main.yml`; never in git), a `stock.glslp` pass-through, then the auto presets: `config/global.glslp` ->
crt-pi and `config/<Core>/<Core>.glslp` -> stock for `retroarch122_crt_off_cores` (default Mupen64Plus-Next,
PCSX-ReARMed; names = RetroArch `library_name`, the same as the folders under `config/`). Presets only `#reference`
the shader files (path relative to the preset). Why: RA 1.22 loads shaders only from presets (`video_shader =` is
ignored), found in `rgui_config_directory`, core before global.

`fightcade` (#208, tag `fightcade`, also in `vm.yml`): Fightcade mode (#204). Installs Box64 0.4.4, Wine 10.0
amd64-wow64, the arm64 Electron 9.1.0 and Fightcade 2.1.45 under `hk_fightcade_install_dir` (default
`/data/fightcade/opt`, ~1.3 GB; not `/opt`: the root filesystem is 6.9 GB with ~2.8 GB free; the role refuses a
first install with less than 1500 MiB free on the install dir's filesystem), its data under `hk_fightcade_data_dir`
(`/data/fightcade`: base Wine prefix ~540 MB, lobby profile 0700, `roms-extra`, FBNeo config, logs 0700).
**Two privilege zones** (Security, #208): root only installs apt packages, `/usr/local/bin/coin-dojo-fightcade`,
`/usr/local/etc/coin-dojo-fightcade.conf` and the helpers in `/usr/local/lib/coin-dojo-fightcade` (unpack, prefix,
the wine/wineserver/fcade-url wrapper; root-owned on the root fs, run as coindojo). Everything under `/data` and the
home-dir files are written by coindojo (`become_user`): staging, sha256 check, unpack, Box64, lobby patches, ini,
prefix. Root never writes, unpacks, chowns, executes or follows a path there; the role asserts that no root-run path
lies under the data partition, and the helpers, the wine/wineserver/fcade-url wrapper and the launcher refuse to
run as root (the launcher's read-only `--check` may; Security #215). Those binaries only ever run as coindojo.
**Artifacts** (never in git): `make -C build box64` (checked against the `.sha256` files its build wrote: Box64 is
not bit-reproducible) and `make -C build fightcade` (sha256 pins equal to `build/fightcade/fetch.sh`). An archive is
uploaded and unpacked only when its stamp differs (all or nothing, sha256 checked again on the cabinet, setuid bits
stripped), so a second dry run is ZERO. Every deploy re-applies the lobby patches (nativefier.json: no menu bar,
internal links anchored to `https://(web|replay).fightcade.com/`, webPreferences with contextIsolation on, no node,
no remote, no plugins; the inject CSS that draws the lobby in the 1440 px bezel column). Popups get the same
webPreferences from a sed patch of Fightcade's `lib/main.js` at unpack (nativefier's DEFAULT_WINDOW_OPTIONS, pinned to
the upstream file's sha256, #215). Links that are not internal go to `xdg-open` (any scheme): fcade:// is how a match
starts; the role installs no browser, and other schemes reach whatever xdg handler the image has. The role builds the FBNeo base ini
(option B, #92) over Fightcade's own default ini; the launcher copies it fresh on every start (FBNeo rewrites it)
and builds the read-only RAM prefix in `$XDG_RUNTIME_DIR/coin-dojo/fcprefix` (z: stays `/`: Wine registers the fonts
at `Z:\usr\share\fonts`). Electron runs with `--no-sandbox`: with Chromium's sandbox, Electron 9 aborts ("GPU
process isn't usable", test VM 2026-11-18). The base prefix is built once per Wine build (`wineboot` under Box64,
csmt=0). Refuses to run while Fightcade mode runs (live session only). No account, no login and no ROMs are
shipped. Dry run: `make deploy TAGS=fightcade BECOME=1`. Rollback: remove `/usr/local/bin/coin-dojo-fightcade` (the
session then falls back to ES-DE); `/data/fightcade` can stay or be deleted.

**Fightcade cabinet proof (#233).**

Frame pacing (`bForce60Hz 1`) and the Fightcade audio sink (`hk_fightcade_audio_sink`), after
`make deploy TAGS=fightcade BECOME=1 APPLY=1`. Read-only checks unless a step says otherwise.

1. **Force60 offline vs online.** In FBNeo, the stats overlay (Backspace) shows the fps. TRAINING or TEST GAME on
   sfiii3: 60 fps, smooth. Then play **one replay to the end** and **spectate one live match**: each should show
   ~59.6 fps (Force60 ignored, as for online games). Watch for a desync (players drifting apart, FBNeo's desync
   notice). The replay's winner should match the one the lobby lists.
2. **Clear the earlier hand move first.** The boot menu must be up (no ES-DE, no Fightcade). Put the default back with
   `pactl set-default-sink combined`. module-stream-restore may also remember a stream's earlier sink, and pactl has no
   command to clear its entries. So back up and delete its database:
   `cp ~/.config/pulse/*-stream-volumes.tdb ~/.config/pulse/t233-bak/` (make the folder first),
   then `pulseaudio -k`, `rm ~/.config/pulse/*-stream-volumes.tdb`, `pulseaudio --start`.
3. **Negative control.** Set `hk_fightcade_audio_sink: default` (personal/config/local.yml) and deploy. Start a game in
   TRAINING. `pactl list sink-inputs` then shows FBNeo's stream (`application.process.binary`) on the `combined` sink's
   index (`pactl list short sinks`), and the audio is uneven again.
4. **Fix.** Go back to `auto` and deploy. The launcher logs
   `audio: PULSE_SINK=alsa_output.platform-107c706400.hdmi.hdmi-stereo`, and FBNeo's stream is on that sink. The audio is clean, and ES-DE/RetroArch still play through `combined`.

`launcher` (#62, tag `launcher`, also in `vm.yml`): installs `skills/collections/launch.py` as
`~/ES-DE/scripts/launch.py`, the command of every virtual ES-DE system. Game guard; `launch.py --check` locally; on the
host, in a `/tmp` staging dir: `python3 -m py_compile`, `--check` and `launch_check.py` (exact argv per system ==
`launch_expected.json`); the 1.22 binary and GLES core must exist; only if the file changes: the live one goes to
`~/ES-DE/themes/_backups/<stamp>-launch/launch.py` **and** `launch.py.prev`; install; post-check (a failure puts
`.prev` back). No ES-DE restart (ES-DE runs a new `python3 launch.py` per game). Check mode writes only the `/tmp` dir.

    make deploy-launch [LIMIT=coin-dojo-vm] [APPLY=1]     # = make deploy TAGS=launcher
    make launch-rollback [LIMIT=coin-dojo-vm] [APPLY=1]   # launch.py.prev -> launch.py (the replaced file -> <stamp>-launch-rollback/)

`session` (#209, tag `session`, also in `vm.yml`): the screen session loop on tty1. Contract: #204 "Interfaces v2";
the loop's rules are in the header of `roles/session/files/coin-dojo-session`.
- **Scripts** (`/usr/local/bin`, root 0755) read one config, `/usr/local/etc/coin-dojo-session.conf`:
  `coin-dojo-session` (boot menu -> ES-DE or Fightcade -> back to the menu), `coin-dojo-cleanup` (TERM, then KILL
  after 3 s, every emulator/app of the cabinet user, plus a pgrep proof) and `coin-dojo-session-stop`.
- **`.bash_profile`**: only the `exec cage -d -- /usr/local/bin/es-de ...` line changes, and only when exactly one
  such line exists. Its lockout guard: the line runs the session only when it is executable and passes `bash -n`
  and `--check`; otherwise it runs plain ES-DE with the old flags. `lineinfile` keeps a dated backup.
- **Rollback**: set `hk_session_enable: false` and deploy, then log in again on tty1.
- **Also**: it removes the dead `if [ -f /data/launcher/USE_LAUNCHER ]; then exec cage ...; fi` profile line (backup
  kept), backs up and removes the old `/data/launcher` USE_LAUNCHER leftovers (the rest of power_dir stays), and sets
  `vm.swappiness = hk_vm_swappiness` in `/etc/sysctl.d/99-zz-coin-dojo.conf` (after `98-rpi.conf`; applied at once).
- **State** (`/run/user/<uid>/coin-dojo`): `current-mode` (`menu`, `esde` or `fightcade`), `next-mode` (a request)
  and `session.pid`. The deploy game guards and `make cabinet-guard` treat `fightcade` like a running game, but only
  while the pid in `session.pid` is a live coin-dojo-session (a stale file counts as no session). While the boot
  menu shows, the `restart es-de` handler restarts nothing: no ES-DE runs, and it starts with the new files when chosen.
- **Start**: the loop starts at the next tty1 login. **A deploy that restarts ES-DE is such a login**: any changed
  ES-DE file fires the handler, ES-DE dies, cage ends, and autologin starts the session loop during that same deploy
  (as does `make esde-restart APPLY=1`). Otherwise a reboot.
- **Stop hook** `coin-dojo-session-stop`, for the stickmouse exit combo (#211) or ssh:
  - It runs as the cabinet user with no sudo. It works under NoNewPrivileges and from any cgroup.
  - It writes `menu` to `next-mode` unless a request is already there.
  - It sends SIGUSR1 to the pid in `session.pid`, after checking that the pid is coin-dojo-session.
  - The session then TERMs the current mode (KILL after 5 s), cleans up and shows the menu. The boot menu ignores it.
  - It returns at once, exits 0 (also when no session runs), and is safe to call twice.

`share` (#266, tag `share`, also in `image.yml` and `vm.yml`): the network folder. `hk_share_enable` (community
default true; `personal/config/local.yml` sets false) installs `samba`, `samba-vfs-modules` and `avahi-daemon` from
the image's Debian sources and shares exactly `roms` (`hk_rom_root`) and `bios` (`hk_bios_root`).
- **smb.conf** (`templates/smb.conf.j2`) is installed only after `files/smbconf_check.py` and `testparm` pass on the
  render (template `validate:`): exactly the two shares, `server min protocol = SMB3`, `map to guest = never`,
  `restrict anonymous = 2`, `interfaces = lo eth0 wlan0` + `bind interfaces only = yes`, port 445 only
  (`disable netbios`, nmbd masked), no printers, no user shares, `follow symlinks` and `wide links` off, files as
  the cabinet user 0644/0755, macOS `fruit` with `fruit:resource = stream` (forks in xattrs, no `._` files;
  `xattr` refused Finder's resource-fork write on the VM).
- **Who may connect: the live local subnets only.** smb.conf has `hosts deny = ALL`, `hosts allow = 127.0.0.1 ::1`
  and then `include = /run/coin-dojo-share/hosts.conf`, which `files/hosts_allow.py` writes from the IPv4 subnets
  `eth0`/`wlan0` have NOW (private ranges only; a public address adds nothing) plus `fe80::/10`. It runs as smbd's
  `ExecStartPre` (drop-in `smbd.service.d/50-coin-dojo-hosts.conf`) and from the NetworkManager dispatcher on
  up/down/address changes, followed by `smbcontrol smbd reload-config` (no restart, open copies keep going). A
  missing file = loopback only. VM proof: a client from another private subnet is refused, IPv6 prefixes match.
- **bios vetoes** (`hk_share_bios_veto`, default `[mame, "*.lua"]`): `veto files` in `[bios]` hides and refuses the
  MAME core's `mame/` system subtree (ini files can name an autoboot Lua script; plugins, Lua) and Lua files at any
  depth. Normal BIOS files copy. `[roms]` vetoes `hk_share_roms_veto` (default `[mame, "*.lua", "*.cmd"]`: MAME
  command files can name an `-autoboot_script` Lua; no launcher system uses `.cmd`), renames included.
- **Fail closed:** the smbd drop-in runs `smbconf_check.py --hosts-only` on the written hosts file right after
  `hosts_allow.py`; a bad or missing file fails `ExecStartPre` and smbd does not start.
- **Login.** Samba keeps its own NT hash, so a Linux password cannot be copied into it. `coin-dojo-share-sync` (role,
  boot oneshot after `coindojo-firstboot`, and `coin-dojo-share-sync.path` on `/etc/shadow`) gives Samba the community
  default `12345` only while the Linux password is still that default (#265's hash in `/etc/coindojo/default-password`
  equals the shadow field) AND the user has no Samba entry: a password set with `sudo smbpasswd -a` is never replaced.
  The hashes go to `/etc/coindojo/share-default-login` (0600). Once the Linux password changes, the default Samba
  login is removed (an own one stays). State for #265's sudo side: `/run/coindojo/default-login-state`
  (`linux_default=yes|no`, `share_default=yes|no`).
- **Interfaces.** smbd binds at start: on `up` the dispatcher restarts it once, only if it does not listen on the
  interface's address yet. No NetworkManager (the test VM): no dispatcher.
- **Off** (`hk_share_enable: false`): smbd and the sync units stopped and disabled, the mDNS entry, dispatcher,
  smbd drop-in, state and sync files removed; packages and smb.conf stay. Image mode: units enabled only, nothing started.
- Dry run: `make deploy TAGS=share BECOME=1`. Tests: `roles/share/tests/test_share.py`,
  `skills/build-image/tests/test_verify_share.py` (the image verifier's checks).

## ES-DE deploys: theme, collections, gamelists (#89)

Every cabinet ES-DE deploy is Ansible now. **The hand scripts (`deploy1-apply.sh`, `deploy2-apply.sh` and any
scp/ssh copy of theme or collections files) are retired**: do not run them, run these (dry run unless `APPLY=1`):

    make deploy-theme                       # TAGS=esde_theme
    make deploy-collections [W=DIR]         # TAGS=collections (+ edited gamelists from W)
    make deploy-esde [W=DIR]                # theme + collections + verify, at most one ES-DE restart
    make esde-verify                        # read-only: counts, validator, ES-DE log; FAILs on a mismatch
    make -C ansible pull-gamelists W=~/coin-dojo-gamelists   # W/gamelists + W/pulled.md5

Roles (cabinet play, in this order; tag `esde` = all):
- `esde_common`: **game guard** (no write while `retroarch`, the 1.22 AppImage or `flycast` runs; fails closed
  without pgrep) and one backup stamp. Its handler `restart es-de` is the only ES-DE restart: once, at the end,
  only if something ES-DE reads changed; `pkill -9 -x AppRun` (so ES-DE cannot save its old state over the
  deploy), re-checks the guard, waits for AppRun, resets + restarts a failed `getty@tty1` (needs `BECOME=1`).
- `esde_theme`: `theme.xml`, `capabilities.xml`, `colors.xml`, the aspect-ratio xmls and the named `_inc` dirs
  (`esde_theme_files` / `esde_theme_dirs`), then `personal/themes/` laid over them (skipped with
  `EDITION=community`). **Never the whole theme folder** (its placeholder art would replace the real art):
  `_inc/art/backdrops` comes only from `personal/`. Staged + XML-checked on the laptop, rsync -c on the cabinet
  without `--delete`; files that differ are backed up first; retired files (`esde_theme_absent`) removed.
- `esde_collections`: ships the `skills/collections` tools to `/data/coin-dojo`; builds the nested main screen
  in a **temp dir** from the live gamelists (+ `W` edits) with the repo's tools, the live virtual gamelists as
  previous state (favorite/playcount/lastplayed kept); **`validate_nested.py` gate: FAIL = abort, nothing
  written**; then backs up and writes `nested_out` (rsync -c --delete per generated dir), `es_systems.xml`, the
  virtual gamelists, `/data/nested` and missing `/data/media/<virtual>` links. With `W=DIR` the edited real
  gamelists go first through a **drift check** (`gamelist_drift.py`: the cabinet's file must still match
  `pulled.md5`, else abort). `es_settings.xml` is never touched here.
- `esde_verify`: flushes the restart, then per virtual system ES-DE's gamelist games == links == what the run
  built, `validate_nested.py` on the live `nested_out`, and new `Error:` lines in `es_log.txt` vs `.bak`.
  FAILs the play after an apply (or with `make esde-verify`); a plain dry run reports only.

Backups: `~/ES-DE/themes/_backups/<stamp>-theme/` (the files that changed) and `<stamp>-collections/`
(`gamelists.tar`, `custom_systems/`, `nested_out.tar`, `tools.tar`). Roll back = untar/copy them back, then
`make esde-restart APPLY=1`. Idempotent: a dry run right after an apply shows `changed=0` (proven by
`tests/test_esde_local.py`). Check mode builds and validates in a cabinet `/tmp` dir (removed at the end)
and writes nothing else.

## Tests

    make -C ansible test            # the files below + roles/*/tests + ansible-syntax (container)

    python3 tests/test_ansible_structure.py
    python3 tests/test_cabinet_roles.py     # dreamcast + cabinet_tweaks (incl. broken-case controls)
    python3 tests/test_esde_roles.py        # ES-DE roles: no whole-theme copy, no es_settings, guard, validator gate,
                                            #   restart only via the handler, backups, verify (+ broken cases)
    python3 tests/test_esde_local.py        # the real ES-DE roles in the container on a fake tree: check writes
                                            #   nothing, apply, 2nd run changed=0, one restart; broken guard,
                                            #   drift, validator (~2 min; SKIP without docker)

See `tests/README.md` for the deliberately-broken case. `make -C ansible ansible-syntax` runs
`ansible-playbook site.yml --syntax-check` in the pinned container (SKIP when docker is not running);
`mk/tests/test_make.py` checks it fails on a planted bad playbook. `make lint` adds ansible-lint (same image).

## Design decisions

The cabinet is our own arcade OS: ES-DE on Debian 13 (trixie) arm64, crt-pi CRT look, deployed by this
`ansible/` layer over SSH (no card swapping). Ship no artifacts: users bring ROMs/BIOS/art.

- **Native wins:** native ES-DE custom collections, no custom launcher. Carousel = `MUST PLAY` (startup system,
  462 games), genres (fighters, shmups, beat-em-up, platformers, run-and-gun, sports, puzzle, maze, racing) and
  5-year decades 1980-84..2000-04 (buckets under 25 games dropped); no Favorites auto-collection.
  Generated by `../skills/collections/collgen.py`; badges by `../skills/badges/badge.py`.
- **PSP removed:** 16:9 output doesn't fit the 4:3 1440x1080 play area.
- **Dreamcast = standalone flycast v2.7**, built by `../build/flycast/build.sh` (arm64 trixie container, GLES) and
  deployed by role `dreamcast`; ES-DE alternative emulator "Flycast (Standalone)". Needs the 4 KB-page kernel
  (`kernel=kernel8.img` in `/boot/firmware/config.txt`, role `cabinet_tweaks`). Why not RetroArch: its GL and
  Vulkan drivers were dead ends for Dreamcast 3D on the Pi 5. Open: 4:3 pillarbox (#23), real-pad exit test (#26).
- **RetroArch exit:** Start+Select (hotkey = Start, button 9; exit = Select, button 8).
- Research: `../docs/reference/README.md`. Test locally first
  (`../skills/local-preview`) before the cabinet.
