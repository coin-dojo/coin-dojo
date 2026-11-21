# vm: coin-dojo-vm, the cabinet stack in a VM on the Mac (#82)

A throwaway **Debian 13 arm64 VM** (lima, Apple `vz`) that runs the cabinet's software stack:
**cage -> ES-DE -> RetroArch**, provisioned by the same `ansible` roles as the cabinet. Use it
to try risky cabinet changes (first: #62, RetroArch 1.20 -> 1.22 per system) **before** they reach
the cabinet (the maintainer, 2026-11-14). It is a VM, never the cabinet: host name `coin-dojo-vm`, its own
inventory group `vm`, and nothing here talks to <cabinet host>. (Colima's containers stay
"containers"; this is the only VM.)

## Commands (repo root)

    make build-fetch    # once: pinned RetroArch 1.22.2 + N64 core into build/*/out, read-only from the cabinet, sha256-checked
    make vm-up          # create (first time, ~1 min + image download) or start; waits for cpu-gate
    make vm-provision   # Ansible vm.yml on the VM (applied; vm-guard runs first)
    make vm-smoke       # cage+ES-DE, then RetroArch+core+test ROM, then the same ROM through ~/ES-DE/scripts/launch.py
                        # (must pick the 1.22 binary itself, #62); PASS = alive at 10 s + screenshots
    make vm-watch       # watch it: ES-DE session + wayvnc, opens vnc://127.0.0.1:5924 in Screen Sharing
    make vm-watch-stop  # stop the session (the VM keeps running)
    make vm-ssh         # shell as coindojo
    make vm-status      # lima status; when running: load, memory, the session's processes
    make vm-down        # STOP it when you are done (the maintainer's CPU rule)
    make vm-destroy     # dry run; APPLY=1 deletes the VM and its disk (vm-up recreates it)

    make deploy LIMIT=coin-dojo-vm [TAGS=...] [APPLY=1]   # any play/tag against the VM, dry run by default

`make vm-provision` always applies (the VM is disposable); `make deploy LIMIT=coin-dojo-vm` keeps the
usual dry-run-unless-APPLY=1 rule. Screenshots from `vm-smoke` land in `~/.cache/coin-dojo/vm/smoke/`
(`VM_OUT=`).

**CPU rule (the maintainer: the Mac stays under 80% CPU).** `vm-up`, `vm-provision` and `vm-smoke` wait for
`tools/cpu-gate.sh` first (`CPU_GATE_ON_TIMEOUT=fail`: with `CPU_GATE_TRIES=n` it gives up instead of starting). Only one heavy thing at a time: the VM busy or a Colima build, not both.
A running ES-DE session redraws all the time on llvmpipe (~1-2 cores); `hk-session` caps llvmpipe at
2 threads (`LP_NUM_THREADS`), `vm-smoke` stops the session when it ends, and `make vm-down` when done.

## What is in it

| Piece | File | Notes |
|---|---|---|
| VM | `coin-dojo-test.yaml` | lima template: vz, aarch64, 4 CPU / 4 GiB / 20 GiB; official Debian 13 genericcloud arm64 qcow2 pinned by URL (dated build dir) + sha512; user `coindojo` uid 1001 like the cabinet; key = `~/.ssh/*.pub` (on this Mac only `id_ed25519.pub`); **no host mounts**; SSH on the Mac's `127.0.0.1:60124`; only port 5900 (wayvnc) forwarded, to `127.0.0.1:5924`; every other guest port ignored |
| Make | `mk/vm.mk` | the `vm-*` targets above |
| Inventory | `ansible/inventory/vm.yml` | host `coin-dojo-vm`, group `vm`, `host.lima.internal:60124` (Ansible runs in the Colima container; Colima resolves that name to the Mac) |
| Play | `ansible/vm.yml` | imported last by `site.yml`; `hosts: vm`; asserts hostname `coin-dojo-vm`, not <cabinet host>, no Raspberry Pi device-tree model |
| Guard | `vm_guard.py` | `make deploy/ping LIMIT=coin-dojo-vm` checks `ansible-inventory --list` first: the host must be in `vm`, in no cabinet group, with an address that is not (and does not resolve to) the cabinet's |
| VM base role | `ansible/roles/vm_base` | VM-only: what `skills/build-image/scripts/chroot-setup.sh` gives the card (packages, ES-DE 3.5.0 AppImage sha256-pinned, `/data`, seed `retroarch.cfg` / `.bash_profile` / `es_settings.xml` / find rules, zram) + the guest scripts |
| Guest scripts | `guest/` | `hk-session` (headless cage session: start/run/shot/vnc/status/stop), `smoke.sh`, `mknesrom.py` (test ROM), `shotcheck.py` |
| CPU gate | `tools/cpu-gate.sh` | waits until the Mac is under 80% CPU |
| Tests | `tests/test_vm.py` | template pin, inventory separation, planted guard cases, make targets, guest helpers |

## Roles: run, switched, skipped

| Role | In the VM | Why |
|---|---|---|
| `vm_base` | runs (VM only) | asserts it is on `coin-dojo-vm` |
| `cabinet_tweaks` | runs, with switches in `config/targets/vm.yml` (and `inventory/vm.yml`) | `hk_kernel_4k: false` (no `/boot/firmware/config.txt`: no Pi firmware; the vz kernel uses 4 KB pages anyway); `cabinet_swap_packages: [systemd-zram-generator]` (`rpi-swap` is Pi OS only; vm_base writes the zram-generator config, so the "zram active" assert still runs); `hk_alt_emulators: nes only` (Debian has no fbneo core, so cps1/cps2 are cabinet-only). Everything else (RetroArch keys, remaps, cursor, ES-DE keys, gamelist pins) runs unchanged |
| `retroarch122` | runs unchanged | needs the pinned local builds in this checkout (`build/retroarch/out/{retroarch,BUILDINFO}`, `build/n64/out/mupen64plus_next_libretro.so`, git-ignored, from `build/retroarch` / `build/n64`); without them the role fails on the VM, as it would on a fresh card |
| `journald` | runs unchanged | |
| `sshd_keyonly` | runs unchanged | its lockout check reconnects through the lima forward |
| `dreamcast` | **skipped** | flycast is a Pi build (GLES on v3d) and needs the Dreamcast BIOS, which never goes into a VM |
| `power` | **skipped** | GPIO power button + its overlay: Pi hardware |

Pi-only, not reproduced at all: GPIO power button, firmware `config.txt`, the 4 KB-page kernel line,
the KMS display (the VM has no GPU: Apple's virtualization gives Linux guests no 3D), HDMI audio,
the Pico pad (no USB passthrough; the uinput pad from the test-ROM plan is a later ticket).

## How the screen works without a GPU

The cabinet runs `exec cage -d -- es-de --resolution 1440 1080 --fullscreen-padding 1` on tty1 with
KMS. In the VM, `hk-session start` runs the same command under cage with `WLR_BACKENDS=headless`,
`WLR_RENDERER=pixman` and a 1920x1080 headless output; ES-DE and RetroArch render with Mesa
**llvmpipe** (software GL / GLES). `grim` takes screenshots through wlr-screencopy. `hk-session run`
starts RetroArch as a second client on the same display, the way ES-DE starts it on the cabinet.

## Watching (make vm-watch)

`wayvnc` runs in the VM on **127.0.0.1:5900** against the headless cage session; lima forwards it
to the Mac's **127.0.0.1:5924** only (`hostIP: 127.0.0.1` in the template; never 0.0.0.0), and
`open vnc://127.0.0.1:5924` shows it in macOS Screen Sharing, nothing to install. **The VNC server
has no password, which is only acceptable because it is reachable from this Mac's loopback alone.**
Never change the forward to 0.0.0.0 or another interface without adding wayvnc auth first.

**XQuartz fallback: does not work** (tested 2026-11-14). cage's x11 backend over `ssh -X` into
XQuartz fails with `X11 does not support Present extension` (wlroots needs Present; XQuartz has no
Present and no DRI3), with `WLR_RENDERER=pixman` too. So there is no `vm-watch-x11`; VNC is the way.
(To repeat it: start XQuartz, then `DISPLAY=:0 ssh -F ~/.lima/coin-dojo-vm/ssh.config -o ControlPath=none
-X -o XAuthLocation=/opt/X11/bin/xauth lima-coin-dojo-vm`, then `WLR_BACKENDS=x11 cage -- ...`.)

## Measured (2026-11-14, MacBook, Colima busy beside it)

| Step | Time |
|---|---|
| `vm-up` first create (incl. the ~400 MB image download) | ~60 s |
| `vm-up` create from the cached image | 41 s |
| `vm-provision` on a fresh VM (apt + ES-DE download + all roles) | 69 s |
| `vm-provision` again (nothing to do) | ~30 s |
| `vm-smoke` | 21 s |
| destroy -> up -> provision -> smoke PASS | ~3 min (plus cpu-gate waits) |

VM load: ~1.9 cores with ES-DE alone on llvmpipe, ~3-3.6 cores with ES-DE + RetroArch; ~0 when no
session runs. Guest memory with ES-DE running: ~550 MB used of 4 GiB.

Smoke results: ES-DE 3.5.0 starts and draws its system carousel (NES, LAST PLAYED from
cabinet_tweaks, 4:3 padded); RetroArch **1.22.2** (the pinned cabinet build, GLES) and Debian's
**1.20.0** both run Nestopia + the test ROM (73% of the 1920x1080 frame is the ROM's red = the 4:3
viewport). Debian 13 ships libwayland 1.23, so the Pi's 1.20 + libwayland 1.24 deadlock (#49) is
not reproduced here; a VM PASS for 1.20 says nothing about that bug.

Known differences from the cabinet in a dry run after provisioning (`make deploy LIMIT=coin-dojo-vm`):
`StartupSystem` changes back to `must-play` each time, because ES-DE drops it on exit while the
collgen virtual systems (#38) are not deployed to the VM; and `sshd_keyonly`'s timestamped backup
dir is "changed" on every run (on the cabinet too).

What the VM cannot test: GPU paths (v3d GLES, Vulkan, the GLES N64 core's speed), flycast /
Dreamcast, KMS/DRM display modes and the physical bezel crop, HDMI audio, the Pico pad and GPIO
power button, the Pi kernel/firmware, and real performance (llvmpipe on 4 vCPUs is not a Pi 5).
