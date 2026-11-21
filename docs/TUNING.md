# Why it is tuned this way, with the numbers

Every setting on this page was chosen by a measurement, and **input lag is the deciding factor**: between a
smoother picture and a faster response, the faster response wins (decision 2026-11-18, #92, #204). Each decision
names its number and where it came from: a ticket here (`#N`) and the date. "Not measured" means nobody measured
it. The full Fightcade story, with every table, the raw per-frame logs, dead ends and gotchas, is in
[reference/fightcade-on-pi5.md](reference/fightcade-on-pi5.md).

## Contents

- [The decisions at a glance](#the-decisions-at-a-glance)
- [4 KB-page kernel](#4-kb-page-kernel)
- [Memory: zram and vm.swappiness = 1](#memory-zram-and-vmswappiness--1)
- [Wi-Fi power save off](#wi-fi-power-save-off)
- [Fightcade drawing: option B](#fightcade-drawing-option-b)
- [Force 60 Hz and the direct HDMI sink](#force-60-hz-and-the-direct-hdmi-sink)
- [The read-only RAM Wine prefix](#the-read-only-ram-wine-prefix)
- [Box64: safe flags by default](#box64-safe-flags-by-default)
- [cage, a one-app kiosk](#cage-a-one-app-kiosk)
- [The boot menu and the session loop](#the-boot-menu-and-the-session-loop)
- [No background apt runs](#no-background-apt-runs)
- [How we measure](#how-we-measure)
- [Benchmark tables](#benchmark-tables)

## The decisions at a glance

| Decision | Measured effect | Source |
|---|---|---|
| 4 KB-page kernel (`kernel8.img`) | flycast's JIT fails on the default 16 KB-page kernel; Box64 and Wine need 4 KB pages | #92, 2026-11-14 |
| zram swap only, `vm.swappiness = 1` | worst frame 144 ms -> gone (the swap-in part of the ~25 s hitch) | #92, #209, 2026-11-18 |
| Wi-Fi power save off | loss 7.5 % -> 0 %; avg 21.9 -> 13.8 ms; jitter (mdev) 29.8 -> 8.1 ms | #213, 2026-11-18 |
| Option B: D3D9, vsync off, `csmt` off | 59.59 fps, max frame 28 ms, 0 frames over 33 ms; no vsync lag (up to ~16 ms saved) | #92, #204, 2026-11-18 |
| Force 60 Hz (offline) + direct HDMI sink | 60.00 fps, max 29 ms; the 59.59-vs-60 Hz stutter gone; audio crackle gone | #233, 2026-11-18 |
| Read-only RAM Wine prefix | registry save stall 157-228 ms -> none; max frame 28 ms | #92, 2026-11-18 |
| Box64 safe flags by default | 59.57 fps, max 28 ms, 0 frames over 33 ms with safe flags; fast flags gain +0.13 fps (D3D9) | #92, #233, 2026-11-18 |
| cage kiosk | one full-screen app, whole frames, no desktop; ES-DE and services ~616 MB | #92, #208 |
| Boot menu, one mode at a time | Fightcade ~1.4 GB PSS + ES-DE ~616 MB would not fit 1979 MB | #92, #204, #209, 2026-11-17 |
| No apt-daily, no unattended upgrades | no background package runs during play; effect not measured | image build (`skills/build-image`), role `cabinet_tweaks`; no ticket |

## 4 KB-page kernel

**What:** `kernel=kernel8.img` in `/boot/firmware/config.txt` (role `cabinet_tweaks`). **Why:** the Pi 5's default
kernel (`kernel_2712.img`) uses 16 KB memory pages. flycast's JIT then fails (`mprotect failed errno 22`), and Box64
and Wine need 4 KB pages too (#92, 2026-11-14). With it, standalone flycast ran a Dreamcast shooter at 59.9 fps
(2026-11-14). **Cost:** a 16 KB kernel can be a little faster for other work; not measured here. **Check:**
`make cabinet-status` prints the page size (must be 4096).

## Memory: zram and vm.swappiness = 1

**zram only.** Swap lives in compressed RAM (zstd, size = RAM x1, at most 2 GiB: `hk_swap`), never in a file on
the SD card. The old 811 MB `/var/swap` file was removed (2026-11-15, #91): SD swap is slow and wears the card.

**vm.swappiness = 1** (`hk_vm_swappiness`, `/etc/sysctl.d/99-zz-coin-dojo.conf`, #209). With the default 60, the
kernel moved idle Wine server pages to zram, and pulling them back cost one **144 ms** frame about 25 s into every
game (#92, 2026-11-18, [table 7](reference/fightcade-on-pi5.md#7-the-25-s-hitch-step-by-step)). At 1 the kernel swaps
only under real memory pressure. It is global and permanent, so ES-DE games get it too. A per-mode limit was the
first plan, but the Pi kernel has no memory cgroup, so it would have done nothing (#204, #208).

## Wi-Fi power save off

Rollback netcode hides steady latency, but jitter and packet loss turn into stutter and teleporting characters.
The Pi's Wi-Fi chip saves power by default. 60 s of pings on the cabinet, 2026-11-18 (#213, `make net-check`):

| | Power save on (default) | Off |
|---|---|---|
| Public host: loss | 7.5 % | **0 %** |
| Public host: avg / max / mdev | 21.9 / 130.7 / 29.8 ms | **13.8 / 56.3 / 8.1 ms** |
| Gateway: avg / max / mdev | 8.3 / 29.8 / 5.7 ms | 7.9 / 61.3 / 6.9 ms |

It is off for every Wi-Fi network (`hk_wifi_powersave: false`, a NetworkManager drop-in). **Ethernet is still the
recommendation** for online play; wired numbers: not measured (#213). On Wi-Fi, use 5 GHz. Measure your own line
with `make net-check` before and after a change.

## Fightcade drawing: option B

Fightcade's FBNeo is a 32-bit Windows program running through Box64 and Wine. Emulation was never the limit (1,130
fps native, 407-524 fps under Box64 + Wine); getting the frame to the screen was (#92, 2026-11-18). Full screen
D3D9 scaled on the GPU reached 59.23 fps at 50 % CPU, where DirectDraw full screen managed 43.4 fps. With the
hitch fixed, four D3D9 variants were run per frame (sfiii3, 3,300 frames each, #92, #204, 2026-11-18 04:29-04:35 UTC):

| Option | What | fps (target 59.59) | max ms | frames > 33 ms | CPU |
|---|---|---|---|---|---|
| A | D3D9, Wine `csmt` on | 59.55 | 45 | 1 | 60 % |
| **B** | **D3D9, `csmt` off, FBNeo vsync off** | **59.59** | **28** | **0** | 50 % |
| C | B + vsync | 59.59 | 27 | 0 | 40 % |
| D | B + software vertex processing | 59.59 | 28 | 0 | 50 % |

**B ships.** C is as smooth and uses less CPU, but vsync adds up to one frame (~16 ms) of input lag, so it loses
on the deciding factor. B still leaves 2.5x to 8.3x turbo headroom for rollback's re-simulated frames. Settings:
[reference/fightcade-on-pi5.md, What ships](reference/fightcade-on-pi5.md#what-ships-the-settings).

## Force 60 Hz and the direct HDMI sink

On 2026-11-18 the game looked choppy at full speed (#233). It was not the CPU (46-53 % of one core, 2.4 GHz,
not throttled). Per-frame runs, 75 s each, lobby up, safe Box64 flags (#233, 2026-11-18 23:3x-23:5x):

| Run | fps | p50 ms | p99 ms | max ms | > 20 ms | > 33 ms |
|---|---|---|---|---|---|---|
| A: option B as shipped | 59.57 | 17 | 19 | 28 | 15 | 0 |
| C: vsync on | 59.59 | 17 | 19 | 31 | 18 | 0 |
| D: Force 60 Hz (`bForce60Hz 1`) | 60.00 | 17 | 19 | 29 | 15 | 0 |

Emulation pacing was fine in all three, so the stutter was **presentation**: 59.59 fps content on a 60 Hz panel
repeats a frame about every 2.4 s. Force 60 Hz runs the game at exactly the panel's rate, with no vsync lag. The
owner's verdict in TRAINING: "Game feels great". Audio then crackled; the default audio output was a combined sink
over both HDMI ports, and sending sound straight to the HDMI sink gave "Perfect audio now!" (#233, 2026-11-18).

**What ships** (#233, 2026-11-19): `bForce60Hz 1` in the FBNeo ini, and Fightcade mode's audio goes straight to the
HDMI sink (`hk_fightcade_audio_sink: auto`; ES-DE and RetroArch keep the combined sink). Force 60 Hz helps
offline only: Fightcade ignores it online, so both players emulate at the game's own rate and stay in sync (its
ChangeLog, FcadeFBNeo v0.2.97.44-53). One online match was played with no desync (2026-11-19). Matches online
keep the 59.59-vs-60 Hz repeat. Details: [reference/fightcade-on-pi5.md](reference/fightcade-on-pi5.md#frame-pacing).

## The read-only RAM Wine prefix

With swappiness fixed, the hitch had a second cause: the Wine server saved its registry to the SD card, a 157-228
ms frame. Wine touches some keys on every start, so a save always comes (#92, 2026-11-18):

| Step | Worst frame |
|---|---|
| Wine server pages in zram (`vm.swappiness` 60) | 144 ms |
| `vm.swappiness = 1`: the registry save to the SD card shows | 157-228 ms |
| Prefix in RAM (tmpfs): the save still runs, to RAM | 70 ms |
| **Prefix in RAM, made read-only: no save starts** | **28 ms** |

Each launch builds a throwaway prefix in the session's RAM folder from a base prefix built once by the deploy,
then makes it read-only. Nothing is written to the SD card, every start is clean, and it is deleted when the mode
ends (#208).

## Box64: safe flags by default

Box64's dynarec "fast" flags (`STRONGMEM=0 BIGBLOCK=3 SAFEFLAGS=0 FASTNAN=1 FASTROUND=1 CALLRET=1 FORWARD=1024`)
gained only +0.13 fps with D3D9 (49.87 -> 50.00) and +0.84 fps with DirectDraw (#92, 2026-11-18). They trade exact
x86 behaviour for speed, and rollback netplay needs both players' emulators to compute exactly the same frames.
So they are **off** (`hk_fightcade_box64_fast: false`) until a replay test proves they stay in sync with an x86 PC
(#92, open). With the safe flags, option B measured 59.57 fps, max 28 ms, 0 frames over 33 ms (#233, 2026-11-18).

## cage, a one-app kiosk

The cabinet has no desktop. `cage` is a Wayland compositor that shows one full-screen app: the boot menu, ES-DE
or Fightcade. Fightcade's Electron lobby and Wine are X11 programs, so they run in Xwayland inside cage (#92,
#208). cage composites whole frames, which is why vsync's one gain, no tearing, does not matter here (#92). ES-DE
and its services use about 616 MB (#92, 2026-11-17). Tearing or frame cost of cage itself: not measured.

## The boot menu and the session loop

ES-DE and Fightcade never run at the same time. In the test VM, Fightcade with Wine used about 1.4 GB PSS; the Pi
has 1979 MB and ES-DE with its services takes about 616 MB (#92, #204, 2026-11-17). So one loop on the console
runs one mode at a time (#209):

- At power-on the **boot menu** shows ARCADE and FIGHTCADE and starts ARCADE after 5 s
  (`hk_session_countdown`, #210). A power cut never leaves the cabinet stuck in Fightcade.
- Leaving a mode stops everything (TERM, then KILL after 3 s) and logs a process count of 0 (#209).
- A crash, a broken menu or a missing Fightcade install falls back to the menu or ES-DE, never a black screen.
- In Fightcade mode: no screen saver, no display blanking.

## No background apt runs

The image masks `apt-daily` and `apt-daily-upgrade` timers, and unattended upgrades stay off (the image build script,
role `cabinet_tweaks`). A package run in the background would use CPU and SD-card bandwidth at a random moment,
possibly mid-match. Updates happen when you choose: rebuild or redeploy. Effect on frame times: not measured.

## How we measure

- **Per-frame log** (`frametime.lua`): FBNeo runs a Lua script that writes the wall time of every frame in ms
  (1 ms resolution under Wine). 3,900 frames of the attract demo; the first 600 are dropped as warm-up. Reported:
  fps, p50, p99, max, and frames over 20 ms and over 33 ms (two 60 Hz frames). Script and stats code:
  [reference/fightcade-on-pi5.md, Reproduce it](reference/fightcade-on-pi5.md#reproduce-it), raw logs in
  [reference/fightcade-bench/](reference/fightcade-bench/).
- **Turbo**: FBNeo's Lua `emu.speedmode("turbo")` runs with no frame limit: the headroom rollback needs.
- **CPU**: the emulator's share of one core (100 % = one core busy). ES-DE stopped, governor ondemand, 2.4 GHz,
  temperature and `throttled=0x0` recorded with each run.
- **Pacing on screen**: per-frame logs measure emulation, not presentation (#233). The 2026-11-18 stutter was seen,
  then fixed by matching the panel's 60 Hz. An on-screen pacing measurement: not measured.
- **Network**: `make net-check`, 60 s of pings to a public host and the gateway: avg, max, mdev, loss (#213).

## Benchmark tables

The tables above are the ones each decision rests on. Every table (VM memory, the first Pi run, emulation
capacity, the window and full-screen draw modes, the heaviest boards, memory on the Pi) is in
[reference/fightcade-on-pi5.md](reference/fightcade-on-pi5.md#benchmark-tables). The final run on the four heaviest
boards tested, option B (#92, 2026-11-18):

| Board | Target fps | fps | max ms | > 33 ms | Frames |
|---|---|---|---|---|---|
| CPS3 (sfiii3) | 59.59 | 59.59 | 28 | 0 | 3,300 |
| Midway Wolf + DCS (umk3) | 54.71 | 54.71 | 36 | 1 | 3,000 |
| IGS PGM (ddp3) | 59.18 | 59.15 | 31 | 0 | 3,300 |
| Psikyo SH-2 (s1945iii) | 60.00 | 60.00 | 26 | 0 | 3,300 |

Open, as of 2026-11-18: an online match on the Pi, netplay determinism with the fast flags, and wired network
numbers ([Open questions](reference/fightcade-on-pi5.md#open-questions)).
