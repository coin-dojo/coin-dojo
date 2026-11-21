# Fightcade 2 on a Raspberry Pi 5: how it was built and benchmarked

Fightcade 2 is the usual place to play arcade fighters online with rollback netcode. It ships for x86 PCs only:
its launcher is an x86 Electron app, and its emulator (`fcadefbneo.exe`) is a 32-bit Windows program that even the
Linux version runs under Wine. When this work started (2026-11-14), we found no report of anyone running it on a
Pi 5. This note shows how this project got it to full speed on a 2 GB Pi 5: the stack, each optimisation step
with its before and after numbers, every benchmark table, the dead ends and the gotchas.

Every number names the run it came from: a ticket in this project (`#N`), the date and, where there was one, the
UTC time. If a number was never measured, the note says "not measured". The raw per-frame logs behind the
per-frame tables are in [fightcade-bench/](fightcade-bench/), so you can recompute them.

**Status (2026-11-19).** Offline play runs at the game's full rate on the heaviest boards tested. Fightcade mode
is built into the cabinet's deploy (#208). On the cabinet, the lobby starts games through `fcade://` (TRAINING and
online matches), both sticks work in FBNeo (#230), SELECT+START closes only the game (#231, #241), and frame
pacing and audio are smooth (#233). **One online match was played on the Pi against a remote player, with no
desync** (sfiii3nr1, 2026-11-19 00:05, safe Box64 flags). Still **not proven**: whether netplay stays in sync with
x86 players when the Box64 speed flags are on (see [Open questions](#open-questions)).

## Contents

- [The stack](#the-stack)
- [How it was optimised, step by step](#how-it-was-optimised-step-by-step)
- [Why option B won: input lag decides](#why-option-b-won-input-lag-decides)
- [Benchmark tables](#benchmark-tables)
- [What ships: the settings](#what-ships-the-settings)
- [The pieces around the game](#the-pieces-around-the-game)
- [First launch: the download on the cabinet](#first-launch-the-download-on-the-cabinet)
- [ROMs: shared folders and the stick + 6-button filter](#roms-shared-folders-and-the-stick--6-button-filter)
- [Dead ends](#dead-ends)
- [Gotchas](#gotchas)
- [Open questions](#open-questions)
- [Reproduce it](#reproduce-it)
- [Sources](#sources)

## The stack

| Piece | What runs | Why | Source |
|---|---|---|---|
| Hardware | Raspberry Pi 5, 2 GB (1979 MB usable), 4 KB-page kernel (`kernel=kernel8.img`) | Box64 and Wine need 4 KB pages on a Pi 5 | #92 |
| Lobby (launcher) | Fightcade 2.1.45's app (`resources/app`, a nativefier wrapper of the Fightcade web app) on the **official arm64 Electron 9.1.0** | The x86_64 Electron crashes under Box64. The app has no native modules, so the same JavaScript runs on arm64 Electron with no translation | #92 VM spike 2026-11-17 |
| x86 translator | **Box64 v0.4.4** (commit `2f130fab`), built from source with `-DRPI5ARM64=ON`, `RelWithDebInfo` | Debian's Box64 0.3.4 crashes; `-DRPI5ARM64` picks the Cortex-A76 code paths | #92, #207 (`build/box64/`) |
| Windows layer | **Wine 10.0 amd64-wow64** (Kron4ek Wine-Builds) under Box64 | Runs the 32-bit `fcadefbneo.exe` through wow64 on a 64-bit Wine. Wine 11.22 asserts under Box64 | #92, #207 (`build/fightcade/`) |
| Emulator | Fightcade's FBNeo (`fcadefbneo.exe`, PE32 i386) | The only emulator Fightcade's rooms launch | #92 |
| Display | Xwayland inside cage (the cabinet's Wayland kiosk compositor) | Electron 9 and Wine are X11 clients | #92, #208 |
| Game link handler | `fcade://` links open Fightcade's `fcade` helper (x86_64 Linux) under Box64 | See [The fcade:// handler](#the-fcade-handler) | #208 |

All three downloads are sha256-pinned in one file, `build/fightcade/pins.json` (url, file, sha256, size; read by
`build/fightcade/fetch.sh`, the `fightcade` role and the cabinet's first-launch download, #267), and Box64 is built
from pinned source (`build/box64/build.sh`). The project ships none of them: each owner fetches and builds them, or
(`hk_fightcade_install: first-launch`) the cabinet downloads them itself on the first FIGHTCADE pick:
[First launch](#first-launch-the-download-on-the-cabinet).

## How it was optimised, step by step

The first Pi run reached only 84 % of full speed. Each step below names what it fixed and the measured change.
"sfiii3" is the CPS3 fighting game used as the main test (59.59 Hz). The tables are in
[Benchmark tables](#benchmark-tables).

1. **Run the lobby natively.** The x86 Electron crashed under Box64 (tcmalloc "free invalid pointer"). The arm64
   Electron 9.1.0 runs the same app with no translation. Electron then used 155 MB RSS on the Pi (#92, 2026-11-18).
2. **Pick versions that work.** Box64 0.4.4 built with `-DRPI5ARM64` (Debian's 0.3.4 crashes); Wine 10.0
   amd64-wow64 (11.22 asserts). With `WINEDLLOVERRIDES=mscoree,mshtml=d`, the Wine prefix is ready in 8 s
   instead of hanging for 5 min (#92 VM spike, 2026-11-17).
3. **Find the real bottleneck: drawing, not emulation.** Native arm64 FBNeo (libretro, no video) runs sfiii3 at
   1,130 fps; Fightcade's FBNeo under Box64 + Wine runs at 407-524 fps unthrottled ("turbo"). But with its default
   drawing it showed only 49.87 fps, 84 % (#92, 2026-11-18; [table 3](#3-emulation-capacity),
   [table 2](#2-first-pi-run-config-matrix)).
4. **Try the draw modes.** DirectDraw in a 1x window reached 59.44 fps at 60 % CPU. But a large DirectDraw window
   is stretched on the CPU in Wine: full screen it fell to 43.4 fps. Wine's Vulkan renderer drew nothing under
   Xwayland, so its 59.46 fps is invalid. D3D7 was slower (38.86 fps) (#92; [table 5](#5-full-screen-draw-modes)).
5. **Full screen D3D9, scaled on the GPU.** FBNeo `nVidSelect 4` (D3D9) full screen at 1920x1080 with
   `bVidCorrectAspect 1` on the 16:9 panel: 59.23 fps at 50 % CPU, with the 4:3 picture at x=240..1679, exactly the
   cabinet's bezel opening (#92, 2026-11-18). Wine's GL renderer draws it on the Pi's V3D GPU (Wine reports
   `renderer: V3D 7.1.10.2`, not llvmpipe).
6. **Gamma off.** `bVidUseHardwareGamma 0` took CPU from 60 % to 45 % in a window (#92; [#205 source notes](#sources)).
7. **Kill the ~25 s hitch.** Every run had one slow stretch about 25 s in, in every game. It was two things in a
   row ([table 7](#7-the-25-s-hitch-step-by-step)):
   - wineserver's pages had gone to zram swap (`vm.swappiness` 60) and were swapped back in: a 144 ms frame.
     **Fix: `vm.swappiness = 1`**, now permanent (#209).
   - Then wineserver saved the registry to the SD card: 157-228 ms. Wine touches some keys (CurrentVersion,
     Fonts, winebth) on every start, so the registry is always "dirty" and a save always comes. With the prefix
     in RAM (tmpfs) the save cost 70 ms. **Fix: a read-only RAM prefix**, so no save starts: max frame 28 ms.
8. **Tune what is left (options A-D).** With the hitch gone, four D3D9 variants were run per frame. Option B
   (Wine `csmt` off, vsync off) won: 59.59 fps, max 28 ms, 0 frames over 33 ms
   ([table 8](#8-options-a-d-after-the-hitch-fix)). Why B and not the vsync variant: next section.
9. **Check the heaviest boards.** With option B, the four heaviest boards tested all run at their own full rate
   with at most one frame over 33 ms in 3,000-3,300 ([table 9](#9-final-heaviest-boards-option-b)).

## Why option B won: input lag decides

The owner's decision on 2026-11-18 (#92, #204): **option B, because input lag is the deciding factor.** For
competitive online play every millisecond counts.

| Option | What it is | Verdict |
|---|---|---|
| A | D3D9, Wine `csmt` on (Wine's default: Direct3D on a separate thread) | Out: one frame over 33 ms (max 45 ms) and 0.04 fps short |
| **B** | **D3D9, `csmt` off, FBNeo vsync off** | **Shipped**: 59.59 fps, max 28 ms, 0 frames over 33 ms |
| C | B + FBNeo vsync on | As smooth (max 27 ms) and less CPU (40 % vs 50 %), but vsync adds up to one frame (up to ~16 ms) of input lag. On the 60 Hz panel it also repeats a frame about every 2.4 s, because the game runs at 59.59 Hz. Its gain, no tearing, does not matter here: cage composites whole frames anyway |
| D | B + software vertex processing (`bVidHardwareVertex 0`) | Same as B; no reason to change |

The only thing B gives up against C is CPU (50 % vs 40 %). B still leaves 2.5x (umk3) to 8.3x (sfiii3) turbo
headroom for rollback's re-simulated frames. Revisit C only if online matches show rollback slowdowns.

## Benchmark tables

### Method

- **Per-frame logs** (tables 6, 8, 9): a Lua script run by FBNeo (`fcadefbneo.exe <rom> -lua frametime.lua`,
  [frametime.lua](fightcade-bench/frametime.lua)) writes the wall time of every frame in ms. It uses `os.clock()`,
  which under Wine is msvcrt `clock()`: wall time with **1 ms resolution**. Each run logged 3,900 frames (umk3:
  3,600) of the attract demo. The first 600 frames are dropped as warm-up, so 3,300 frames are analysed (umk3:
  3,000). Thresholds: "> 20 ms" and "> 33 ms" (more than 33.4 ms, two 60 Hz frames). The stats script is in
  [Reproduce it](#reproduce-it).
- **Average logs** (tables 2, 4): a Lua script logs frames against the clock every 300 frames; the first 300 frames
  are dropped. "min 5-s window" is the slowest of those windows.
- **Turbo** (tables 3, 4): FBNeo's Lua `emu.speedmode("turbo")`, frames against the clock every 600 frames: how fast
  the emulator could run with no frame limit. This is the headroom rollback needs.
- CPU is `fcadefbneo.exe`'s share of one core (100 % = one core busy). ES-DE was stopped for every Pi run.

### 1. VM spike: memory (2026-11-17)

Lima VM on an Apple M1 Max (Debian 13 arm64, 4 KB pages, 2 GiB RAM + 2 GiB swap, no Rosetta). The VM cannot
predict Pi speed (M1 cores are about 2x a Pi 5 core and the VM has no GPU), so only memory and "does it run" were
measured here (#92).

| Process | Memory |
|---|---|
| Lobby + FBNeo + Wine services, total | ~1.4 GB PSS |
| fcadefbneo.exe | 572 MB RSS |
| electron | 311 MB RSS |
| winedevice | 239 MB RSS |
| explorer | 180 MB RSS |
| fcadefbneo.exe CPU | ~110 % of an M1 Max core |

The Pi has 1979 MB, and ES-DE with its services used ~616 MB. So Fightcade needs its own mode with ES-DE stopped
(#92, #204).

### 2. First Pi run: config matrix

2026-11-18 02:42 and 02:48 UTC (#92). sfiii3 attract demo, average log, first 300 frames dropped, a 1x window,
target 59.6 fps. Box64 0.4.4 (`-DRPI5ARM64`), Wine 10.0 wow64, arm64 Electron 9.1.0.

| Config | Avg fps | % of full speed |
|---|---|---|
| base (`nVidSelect 4` = D3D9) | 49.87 | 84 % |
| + Box64 fast flags | 50.00 | 84 % |
| DirectDraw (`nVidSelect 0`) | 54.41 | 91 % |
| **DirectDraw + fast flags** | **55.25** | **93 %** |
| D3D7 + fast flags | 38.86 | 65 % |
| lobby running alongside (base) | ~49.2 | 83 % |

Same runs: CPU at 2400 MHz (the ondemand governor ramps fully), 59-60 °C, `throttled=0x0` throughout.
`fcadefbneo.exe` at ~100-110 % (one core pegged). At this point the reading was "not playable online": 93 % is
a steady shortfall, and rollback needs headroom above 100 %. Steps 4-7 above fixed that.

Fast flags: `BOX64_DYNAREC_STRONGMEM=0 BIGBLOCK=3 SAFEFLAGS=0 FASTNAN=1 FASTROUND=1 CALLRET=1 FORWARD=1024`
(see [Box64 dynarec settings](#box64-dynarec-settings)).

### 3. Emulation capacity

sfiii3, 2026-11-18 (#92; numbers recorded in the [#205 source notes](#sources)).

| What | fps |
|---|---|
| Native arm64 FBNeo (libretro core, no video, no audio, unthrottled) | 1,130 |
| Fightcade FBNeo under Box64 + Wine, turbo | 407-524 |
| Fightcade FBNeo, default drawing (table 2 "base") | 49.87 |

The emulator was never the limit. How the frame reached the screen was.

### 4. Heaviest boards: 1x window, DirectDraw

2026-11-18 03:27-03:44 UTC (#92). Fightcade FBNeo, 1x window, DirectDraw, gamma off, Box64 fast flags; ROMs read in
place (see [ROMs](#roms-shared-folders-and-the-stick--6-button-filter)). Target = each game's own rate from FBNeo.

| Game (board) | Target | Avg fps | Min 5-s window | CPU | Turbo |
|---|---|---|---|---|---|
| sfiii3 (CPS3) | 59.59 | 59.43 | 57.87 | 50 % | 524 fps |
| umk3 (Midway Wolf + DCS) | 54.71 | 54.59 | 53.47 | 60 % | 153 fps |
| ddp3 (IGS PGM) | 59.18 | 58.99 | 57.10 | 60 % | 360 fps |
| s1945iii (Psikyo SH-2) | 60.00 | 59.90 | 58.82 | 36 % | 274 fps |

Each run had one ~97 % window about 25 s in, the same in every game: the hitch of step 7.

Not in this table: DirectDraw in a 1x window with sfiii3 alone gave 59.44 fps at 60 % CPU, and turning gamma off
took CPU from 60 % to 45 % (#92; [#205 source notes](#sources); exact run time not recorded).

### 5. Full-screen draw modes

2026-11-18 (#92). sfiii3 full screen inside the bezel opening: Wine GL renderer, gamma off, `bVidAutoSwitchFull 1`,
1920x1080, `bVidCorrectAspect 1`, monitor aspect 16:9. Average log.

| `nVidSelect` | Result |
|---|---|
| 0 DirectDraw | visible but slow and laggy: 43.4 fps (Wine stretches DirectDraw on the CPU) |
| 2 | 42.29 fps, CPU 130 %, looked slow |
| 3 | error window, no samples |
| **4 D3D9, GPU-scaled** | **59.23 fps, CPU 50 %**, picture at x=240..1679 (1440 px wide, measured on a screenshot) |
| Wine `renderer=vulkan` (any mode) | **invalid**: never presents under Xwayland here. The windowed screenshot showed only the menu bar; full screen was black with audio. Its 59.46 fps measured nothing |

Wine's GDI renderer and FBNeo at 16-bit colour were tried in round 2 (2026-11-18) and were slower; their numbers
were not recorded.

### 6. Before the hitch fixes: options A-D, round 5

2026-11-18, logs fetched about 04:21 UTC (#92). sfiii3, D3D9 full screen (option keys as in table 8), Box64 fast
flags, Wine prefix on the SD card. Per-frame log. Computed from the raw logs in
[fightcade-bench/round5-before-hitch-fix/](fightcade-bench/round5-before-hitch-fix/). `vm.swappiness` at the time:
not recorded.

| Option | fps (target 59.59) | p50 ms | p99 ms | max ms | > 20 ms | > 33 ms | Worst frame at |
|---|---|---|---|---|---|---|---|
| A csmt on | 59.46 | 17 | 19 | 140 | 11 | 1 | 24 s |
| B csmt off | 59.46 | 17 | 19 | 131 | 11 | 1 | 21 s |
| C B + vsync | 59.43 | 17 | 19 | 183 | 11 | 1 | 23 s |
| D B + sw vertex | 59.43 | 17 | 19 | 173 | 9 | 1 | 23 s |

The one bad frame per run (131-183 ms, 21-24 s in) is the hitch. Compare table 8.

### 7. The ~25 s hitch, step by step

2026-11-18, sfiii3, option B (#92; numbers recorded in the [#205 source notes](#sources)). The cause was found by
sampling every process's swap use and major page faults once a second through the hitch.

| Step | Cause found | Worst frame |
|---|---|---|
| Start | wineserver pages in zram swap (`vm.swappiness` 60), swapped back in | 144 ms |
| `vm.swappiness = 1` | next cause: wineserver saves the registry to the SD card | 157-228 ms |
| Registry in a tmpfs (RAM) prefix | the save still runs, now to RAM | 70 ms |
| **RAM prefix made read-only** | no save starts | **28 ms** |

### 8. Options A-D after the hitch fix

2026-11-18 04:29-04:35 UTC (#92, #204). sfiii3, D3D9 (`nVidSelect 4`) full screen 1920x1080, 16:9 aspect with
`bVidCorrectAspect 1`, gamma off, Box64 fast flags, read-only RAM prefix rebuilt per option, `vm.swappiness` 1.
Per-frame log; raw logs in [fightcade-bench/options-abcd/](fightcade-bench/options-abcd/).

| Option | fps (target 59.59) | p50 ms | p99 ms | max ms | > 20 ms | > 33 ms | CPU |
|---|---|---|---|---|---|---|---|
| A csmt on | 59.55 | 17 | 20 | 45 | 12 | 1 | 60 % |
| **B csmt off** | **59.59** | 17 | 19 | **28** | 9 | **0** | 50 % |
| C B + vsync | 59.59 | 17 | 19 | 27 | 9 | 0 | 40 % |
| D B + sw vertex | 59.59 | 17 | 19 | 28 | 9 | 0 | 50 % |

The ticket comment says "3,900 frames after warm-up"; the logs hold 3,900 frames, of which 3,300 are analysed
after the 600-frame warm-up.

### 9. Final: heaviest boards, option B

2026-11-18, logs fetched 04:24-04:29 UTC (#92; [#205 source notes](#sources)). Option B as shipped (D3D9 full
screen centred, `csmt` off, vsync off, gamma off, read-only RAM prefix, `vm.swappiness` 1) plus the Box64 fast
flags. Per-frame log; raw logs in [fightcade-bench/heaviest-boards/](fightcade-bench/heaviest-boards/).

| Game (board) | Target | fps | p50 ms | p99 ms | max ms | > 20 ms | > 33 ms | Frames |
|---|---|---|---|---|---|---|---|---|
| sfiii3 (CPS3) | 59.59 | 59.59 | 17 | 19 | 28 | 8 | 0 | 3,300 |
| umk3 (Midway Wolf + DCS) | 54.71 | 54.71 | 18 | 19 | 36 | 4 | 1 | 3,000 |
| ddp3 (IGS PGM) | 59.18 | 59.15 | 17 | 18 | 31 | 6 | 0 | 3,300 |
| s1945iii (Psikyo SH-2) | 60.00 | 60.00 | 17 | 18 | 26 | 6 | 0 | 3,300 |

Same runs: turbo headroom 2.5x (umk3) to 8.3x (sfiii3); 55-57 °C, not throttled. Per-game CPU and turbo for this
run: not recorded (table 4 has them for the window run).

### 10. Memory on the Pi

2026-11-18 02:42-02:48 UTC (#92), ES-DE stopped.

| What | Value |
|---|---|
| RAM used | 0.9-1.45 GB |
| Swap used (phase 2) | up to ~850 MB |
| fcadefbneo.exe RSS | 277-352 MB |
| electron RSS | 155 MB |

### 11. Network: Wi-Fi power save

2026-11-18 (#213). The cabinet was on Wi-Fi only. 60 s of pings before and after turning NetworkManager's Wi-Fi
power save off for the connection.

| | Before (power save on, the default) | After (off) |
|---|---|---|
| Public host 1.1.1.1: loss | 7.5 % | **0 %** |
| 1.1.1.1 avg / max / mdev | 21.9 / 130.7 / 29.8 ms | **13.8 / 56.3 / 8.1 ms** |
| Gateway avg / max / mdev | 8.3 / 29.8 / 5.7 ms | 7.9 / 61.3 / 6.9 ms |

An earlier read-only check the same day (`ping -c 20 -i 0.3 1.1.1.1`) gave avg 20.7 ms, max 115.7 ms, mdev 23.2 ms,
5 % loss (#213). Rollback hides steady latency, but jitter and loss turn into stutter and teleporting. Wi-Fi power
save is now always off (`hk_wifi_powersave`, a NetworkManager `conf.d` drop-in). Ethernet is still recommended;
`make net-check` measures your own line. Wired numbers: not measured.

## What ships: the settings

The Ansible role `fightcade` (#208) installs all of this. The game settings are the measured winners, not levers.

**FBNeo** (`fcadefbneo.ini`, keys set over Fightcade's own default ini; a fresh copy every launch, because FBNeo
rewrites the file on exit):

| Key | Value | Why |
|---|---|---|
| `nVidSelect` | 4 | D3D9, scaled on the GPU (DirectDraw stretches on the CPU: 43 fps) |
| `bVidAutoSwitchFull` | 1 | start full screen |
| `nVidHorWidth` x `nVidHorHeight` | 1920 x 1080 | the panel |
| `nVidScrnAspectX` : `nVidScrnAspectY` | 16 : 9 | the panel's aspect |
| `bVidCorrectAspect` | 1 | 4:3 picture centred at x=240..1679 |
| `bVidFullStretch` | 0 | no stretch to 16:9 |
| `bVidUseHardwareGamma` | 0 | 60 % -> 45 % CPU |
| `bVidVSync` | 0 | option B: no vsync input lag |
| `bForce60Hz` | 1 | 59.59 Hz games at 60 on the 60 Hz panel, offline only ([frame pacing](#frame-pacing)) |
| `bModelessMenu` | 1 | what Fightcade's own start script forces |
| `szAppRomPaths[0..6]` | the shared ROM folders + `roms-extra` | [ROMs](#roms-shared-folders-and-the-stick--6-button-filter) |

**Wine:** `HKCU\Software\Wine\Direct3D` `csmt` = 0 (dword), baked into the base prefix. `WINEDEBUG=-all`,
`WINEDLLOVERRIDES=mscoree,mshtml=d`.

**Box64:** `BOX64_LD_LIBRARY_PATH` = Box64's bundled `x64lib/`, `BOX64_RCFILE` = the build's `box64.box64rc`,
`BOX64_LOG=0`. The fast flags are **off by default** (`hk_fightcade_box64_fast: false`): see below.

**System:** `vm.swappiness = 1` in `/etc/sysctl.d/99-zz-coin-dojo.conf` (#209); Wi-Fi power save off (#213).

### Frame pacing

TRAINING on a CPS-3 fighter (sfiii3) looked choppy at full speed (#233, 2026-11-18): about 50 % of one core, 2.4 GHz, no
throttling, so not CPU-bound. The #92 tables measure emulation frame times, not what reaches the screen. Two fixes,
both proven on the cabinet with the maintainer the same night by hand and now shipped by the role:

- **`bForce60Hz 1`**. CPS-3 runs at 59.59949 Hz (FBNeo `cps3run.cpp`, `BurnSetRefreshRate`). On a 60 Hz panel with
  vsync off, that repeats a frame about every 2.5 s. Force60 makes FBNeo run any game between 50 and 60 Hz at exactly
  60 (`burn.cpp` `BurnSetRefreshRate`; vector, 30 Hz and PAL games keep their rate). The game runs ~0.7 % fast, with
  no added input lag (no vsync). **Online, per Fightcade's ChangeLog, it is ignored**: FcadeFBNeo v0.2.97.44-53,
  "Disabled "force 60Hz" framerate for online games: all games run at their original framerate now"
  (`Fightcade/ChangeLog.txt` line 113 in the pinned 2.1.45 tarball; a test reads it). Replays and spectating are not
  proven yet (steps: "Fightcade cabinet proof" in [ansible/README.md](../../ansible/README.md)). It has to be
  ignored online: CPS-3 gives the SH-2 `25000000 * 100 / nBurnFPS` cycles per frame (`cps3run.cpp` `Cps3Frame`), so
  peers at 60 and 59.59 would emulate each frame differently. So matches keep the 59.59 stutter. Online so far: set to 1 in the ini;
  Fightcade ignores it online (ChangeLog:113); one online match played, no desync (2026-11-19 00:05). One ini serves
  every verb; the launcher does not pick one per `fcade://` link.
- **Audio straight to the HDMI sink.** The image's default PulseAudio sink is `combined` (`module-combine-sink` over
  both HDMI outputs, `skills/build-image/scripts/chroot-setup.sh`). Its rate adaptation made FBNeo's audio uneven,
  and it was clean on `alsa_output.platform-107c706400.hdmi.hdmi-stereo`. The launcher sets `PULSE_SINK` for the
  lobby and FBNeo only (Wine's default device connects without a sink name, so libpulse takes `PULSE_SINK`: wine-10.0
  `dlls/winepulse.drv/pulse.c` 1070-1077). `hk_fightcade_audio_sink: auto` picks the only HDMI sink, or the one on the
  connected port (`/sys/class/drm/card*-HDMI-A-N` = ALSA card `vc4-hdmi-(N-1)`). The cabinet has one HDMI sink
  (`vc4-hdmi-1`, HDMI-A-2 connected; capture of 2026-11-19 in the role's test fixtures). `pactl` gets 3 s and never
  autospawns a daemon. ES-DE and RetroArch keep `combined`: no complaint there, and that would be an image change.

The pacing numbers per variant and the maintainer's pick are in the #233 ticket comments. Not tried yet: vsync on (option C,
up to a frame of lag), hiding the lobby's rendering during a match.

### The read-only RAM Wine prefix

The deploy builds a **base prefix** once per Wine build (`hk-fightcade-prefix`: `wineboot -i` with Mono and Gecko
off, `csmt` = 0, `wineserver -w`, then only drives `c:` and `z:` are kept). Every launch, `coin-dojo-fightcade` then
builds a throwaway prefix in the session's tmpfs run dir (`$XDG_RUNTIME_DIR/coin-dojo/fcprefix`):

1. copy the base prefix's `*.reg` files (about 3.7 MB) and `.update-timestamp`;
2. link `dosdevices/c:` to the base prefix's `drive_c`, and `dosdevices/z:` to `/`;
3. `chmod a-w` the prefix and its `dosdevices`.

Wine cannot write its registry back, so the save that caused the 157-228 ms hitch never starts. Nothing is written
to the SD card, and every start is clean. The prefix is deleted when the mode ends. (The spike used `/dev/shm`;
the role uses the per-user run dir, after a security review: `/dev/shm` is world-shared.)

### vm.swappiness = 1

With the default swappiness, the kernel pushed idle wineserver pages to zram, and pulling them back cost a 144 ms
frame. Swappiness 1 swaps only under real memory pressure. It is set globally and permanently (it also helps
ES-DE mode). A per-mode limit (`MemorySwapMax=0` on the Fightcade systemd scope) was the first plan, but the Pi
kernel has no memory cgroup (`cgroup.controllers` = cpuset cpu io pids), so it would do nothing (#204, #208).

### Box64 dynarec settings

| Variable | Value | What it does (roughly) |
|---|---|---|
| `BOX64_DYNAREC_STRONGMEM` | 0 | no extra barriers to imitate x86's strong memory ordering |
| `BOX64_DYNAREC_BIGBLOCK` | 3 | translate the largest code blocks |
| `BOX64_DYNAREC_SAFEFLAGS` | 0 | do not compute every CPU flag at calls and returns |
| `BOX64_DYNAREC_FASTNAN` | 1 | do not imitate x86 NaN details |
| `BOX64_DYNAREC_FASTROUND` | 1 | do not imitate x86 rounding exactly |
| `BOX64_DYNAREC_CALLRET` | 1 | optimise call/return pairs |
| `BOX64_DYNAREC_FORWARD` | 1024 | look up to 1024 bytes ahead to keep a block going over a forward jump |

Measured gain: +0.13 fps with D3D9 in a window (49.87 -> 50.00) and +0.84 fps with DirectDraw (54.41 -> 55.25)
(table 2). Every later Pi table ran with these flags on.

**Why they are off by default.** These flags trade exact x86 behaviour for speed. A second-opinion review on #92
pointed out that they (and Box64's 64-bit x87) can give results that are repeatable but different from a real x86
PC, and Fightcade's GGPO release builds do not check for desyncs. Rollback netplay needs both players' emulators
to compute exactly the same frames. They stay off (`hk_fightcade_box64_fast: false`) until a replay test passes:
play one match on Windows x86 and on the Pi, hash the game's main RAM each frame through Lua, and diff the logs,
with and without the flags. Option B on sfiii3 with the safe default flags on the Pi: not measured in a posted
run (the config description says it reaches 59.59 fps either way).

## The pieces around the game

### Boot menu and session

ES-DE and Fightcade never run together (memory, table 1). A session loop (#209) runs inside one cage:

- At power-on a boot menu (#210) shows ARCADE and FIGHTCADE; after 5 s it starts ARCADE (ES-DE). After either
  mode exits, the menu comes back and waits.
- ES-DE has a QUIT TO MAIN MENU entry (#214). A FIGHTCADE entry in ES-DE was added and later removed (#219): the
  boot menu is the way in.
- Leaving a mode stops everything (`wineserver -k`, Electron, `fcade`, any `.exe`; TERM, then KILL after 3 s) and
  logs a `pgrep` count of 0. A crash, a broken menu or a missing Fightcade install falls back to the menu or ES-DE,
  never a black screen.
- The persistent wineserver (#257). When Fightcade mode starts, once the RAM prefix is built, `coin-dojo-fightcade`
  runs `wineserver -p` in the background (bounded 3 s, the lobby never waits for it), so the first game does not pay
  Wine's cold start (VM: the first wine call took 1156 ms cold, 255 ms warm; FBNeo's own start showed no difference
  in 3 runs each, so the cabinet decides; `hk_fightcade...` lever: `HK_FC_WINESERVER_PERSIST=0` in
  `coin-dojo-fightcade.conf` turns it off). Journal lines (`journalctl --user -t coin-dojo-fightcade` or the session
  log): `persistent wineserver up (pid N ...)`, `WARN: persistent wineserver did not come up in 3 s` (the first game
  starts wine's own, as before), `WARN: the persistent wineserver was gone before the end` (it died mid-session).
  - One instance: before the RAM prefix is rebuilt, every process whose environment has `WINEPREFIX=<the RAM prefix>`
    from an earlier run (a crash, a KILLed launcher) is ended (TERM, KILL after 1 s) and that old prefix's own socket
    `/tmp/.wine-<uid>/server-<dev>-<ino>` is removed (never another prefix's): `WARN: stale wine processes ...`.
  - Every way out ends it: the exit trap (lobby quits or crashes, TERM from SELECT+START, the launcher's own error
    exits) kills the background start's whole process group, stops the lobby, runs `wineserver -k`, waits up to 1.5 s,
    then scans, TERM/KILLs and scans again (up to 3 rounds, so a `wineserver -p` that was still forking its daemon is
    caught) inside a 4 s budget (the session KILLs at 5 s) and logs `finish took N ms`. A TERM during the setup exits
    before the wineserver and the lobby start. Verdicts: `Fightcade mode ended: no wine processes left`,
    `WARN: leak: ... wine/box64 processes alive (reaped above)`, `WARN: processes from this mode were still running
    (not wine ...)` for Electron leftovers, or `ERROR: leak: ... still running` if a process survived KILL (the session's
    `coin-dojo-cleanup` then ends it). If the launcher itself is KILLed, the scope (`coin-dojo-fightcade.scope`) and
    `coin-dojo-cleanup` (runs after every mode and before the next, TERM then KILL by name, proof `pgrep count=0`)
    still end everything before ARCADE starts; the next Fightcade start also reaps it.
  - Closing a game (SELECT+START) only touches the game's pids; the wineserver is never in that set, so the next game
    reuses it.
- In Fightcade mode: no ES-DE attract mode, no X screen saver or DPMS blanking (`xset s off s noblank -dpms`).

### The lobby window

The lobby is maximised to the 1920x1080 panel (cage maximises it anyway), and an injected stylesheet draws the
Fightcade web app in a centred 1440x1080 column (x=240..1679) on black, with no menu bar. The pointer uses a
visible cursor theme in the lobby only; the game gets the cabinet's blank cursor back.

Electron 9 runs with `--no-sandbox`: with Chromium's sandbox it aborts ("GPU process isn't usable", test VM
2026-11-18). This is an accepted risk: the lobby runs as the cabinet user with no root path, links are limited to
Fightcade's two origins, and hardened `webPreferences` are set (#208, follow-ups in #215).

### The fcade:// handler

In the lobby, joining a match opens an `fcade://` link. On the cabinet:

1. A desktop entry registers `x-scheme-handler/fcade` (in `mimeapps.list`), so Electron's `xdg-open` calls the
   wrapper `fcade-url`.
2. `fcade-url` runs Fightcade's `fcade` helper (an x86_64 Linux program) under Box64, in its own folder, as
   Fightcade's `fcade.sh` does. The link itself is not logged (it can carry a match id).
3. `fcade` executes `fcadefbneo.exe` directly. A `binfmt_misc` rule (`/etc/binfmt.d/coin-dojo-wine.conf`, magic
   `MZ`, #228) hands any Windows `.exe` to the `wine` wrapper, which runs Box64 + Wine 10.0 with the lobby's RAM
   prefix and Box64 settings. Without the rule nothing happens in the lobby, and `fcade.log` logs "Can't execute fcadefbneo.exe". The
   role also makes the emulator `.exe` files executable (mode 0755), which the rule needs.

Proven on the test VM (#208) and on the Pi (#228, 2026-11-18: TRAINING starts from the lobby; an online match on 2026-11-19 00:05).
Fightcade's self-updater (`fcade-upd`, x86_64) cannot run; updates come only by re-pinning the download.

### Stick-as-mouse

The lobby is a mouse-driven web page, and the cabinet has a stick and six buttons. `coin-dojo-stickmouse` (#211)
reads the stick through evdev and drives a virtual pointer through `/dev/uinput`:

- stick = pointer (faster the longer it is held), LK = left click, MK = right click;
- the pointer switches off on its own while a game (FBNeo) runs and ALWAYS comes back when it ends (#257; on
  2026-11-19 it stayed off); only an OFF you set by hand in the lobby BEFORE the game is kept, and SELECT+MK is ignored
  while a game runs (SELECT is the coin button). In the lobby SELECT+MK switches it off and on by hand (#231; it was SELECT+LK until #227 gave that combo to the keyboard);
- SELECT+LK opens the on-screen keyboard (below), in the lobby only: with a game running the combo does nothing;
- SELECT+START held for 2 s: in a game it closes only the game and the lobby stays; in the lobby it leaves Fightcade
  mode for the boot menu (#231). The close (#257) asks the game's X window first, like clicking its X
  (WM_DELETE_WINDOW from `stickmouse.py --xclose`: plain libX11 through ctypes, no package; only top-level windows
  whose `_NET_WM_PID` is one of the game's pids, never the lobby). Wine turns it into the WM_CLOSE that taskkill would
  post, so FBNeo quits as from its menu, but without starting taskkill.exe (a cold Box64 + Wine start, 2-6 s on the
  Pi). VM, Xvfb + Openbox, real FBNeo with sfiii3: window close 105-549 ms, taskkill 393-667 ms (a fast Mac; the Pi's
  cold start is what the window close skips). Only when no window took it (helper rc 3 or 4) or after
  `X_GRACE` 3 s does `wine taskkill` (no `/f`) follow, then SIGTERM 10 s after taskkill starts and SIGKILL 2 s later,
  only to the game's process tree (#241: 10 s covers the slowest taskkill close measured, 6.0 s);
- after a game has closed (by the combo or on its own) SELECT+START does NOT leave Fightcade mode for 5 s
  (`EXIT_GRACE`; logged "ignored (grace 5 s)"), so a second press cannot throw you to the boot menu. Holding it
  through the grace does nothing either: release and press again once the 5 s are over;
- it never grabs the stick exclusively, and runs only while Fightcade mode runs (started and stopped by
  `coin-dojo-fightcade`). A narrow udev rule gives the input group access to `/dev/uinput`.

Fightcade keeps the login session after the first sign-in. No account or session is ever shipped.

### The on-screen keyboard (#227)

Signing in, chatting and searching need text, and the cabinet has no keyboard. The lobby has an on-screen keyboard
drawn into the page itself (cage shows only the newest window fullscreen, so a separate overlay window cannot sit
below Fightcade):

- **Where it lives.** The role writes `resources/app/inject/inject.js` next to `inject.css`; nativefier's own
  `lib/preload.js` runs that file on every page (`INJECT_JS_PATH`, `preload.js` line 21 and `injectScripts()`;
  nativefier injects only CSS by itself). The page DOM is shared with the script, its JavaScript is not. The launcher
  config carries the file's sha256 (`HK_FC_JS_SHA256`) and the launcher refuses to start when it differs, like the CSS.
- **How the stick reaches it.** `coin-dojo-stickmouse` already reads the stick (evdev) and is the ONLY owner of whether the
  keyboard is open. While it is open it sends the stick, LK, MK, START and SELECT to the page as dedicated keys on its
  uinput device (`stickmouse.py`, `_osk_*`): F13 open, F14-F17 directions, F18 = LK types, F19 = MK deletes, F23 = START
  closes (on release, so the SELECT+START exit combo never sends a chat line), F24 = SELECT closes. Right after F23,
  START sends a **real Enter** (`KEY_ENTER` on the same uinput device, #262), exactly what a USB keyboard's Enter is:
  the page script makes no Enter of its own. It used to dispatch a script-made `KeyboardEvent` Enter, and the lobby
  chat ignored it (the maintainer on the cabinet, 2026-11-19: START did nothing; script-made events have `isTrusted` false). The
  order matters: on F23 the page focuses its field again (also when a click had moved the focus to a button, where a
  real Enter would click it) and hides, never blurring the field; then the Enter reaches that field. Whatever the page
  does with a USB keyboard's Enter, it now does with START. Before sending, stickmouse checks `/proc` for a game once
  more, so an Enter never reaches FBNeo; a scan that fails or cannot list `/proc` counts as "a game may run" (fail
  closed: the keyboard closes, no key). Closed, in a game, outside Fightcade mode, or once SELECT joins START (the exit
  combo, in either release order, held or not) there is no Enter. Proven on the test VM (2026-11-19): fake stick on `/dev/uinput`,
  `stickmouse.py`, cage's libinput backend, Xwayland and Fightcade's own Electron 9 binary with the test login page
  (no Fightcade page, no account): START submitted the login form once, and a chat-like field that acts only on a
  trusted Enter got one; with the old code it got only the untrusted one. The page
  matches them by `KeyboardEvent.code`: xkb's `inet(evdev)` rewrites the keysyms of F13-F18 and F20-F23 (XF86Tools, XF86Launch5-9,
  mic mute, touchpad keys; only F19 and F24 keep their names), so `e.key` is not reliable, and F20-F22 are never used. Nothing else is a key of the keyboard, so a stray one does
  nothing in the lobby or in FBNeo. Closed, in a game, or outside Fightcade mode stickmouse sends none of them; a game
  starting closes the keyboard with no key at all (a cancel would reach the game) and the page hides on window blur. A
  key that reaches a hidden page reopens it (a reloaded page agrees with stickmouse again); the on-screen CLOSE and ENTER
  keys only say "press SELECT / START". Only real key events count (`isTrusted`). (The Gamepad API was not used: it would
  not stop the stick also clicking the pointer, and the page cannot know a game runs.) Proven on the test VM with a fake
  stick on `/dev/uinput`, `stickmouse.py`, cage's libinput backend and the real lobby (the VM needs the non-cloud kernel
  for `uinput`).
- **Combo.** SELECT+LK opens and closes it. The pointer toggle that used SELECT+LK moved to SELECT+MK, so the two never
  collide; SELECT+START (2 s) is unchanged. While it is open the pointer is off.
- **Placement.** On the login screen: the empty left panel under the Fightcade logo (x 0-637 of the 1440 column, from
  y=700). On any other page: bottom centre of the column (not yet checked against the logged-in lobby).
- **Keys.** Letters, digits, `- . _ @`, SHIFT (one shot), 123 (symbols and accents), SPACE, DEL, ENTER, CLOSE, and a
  MACROS page. It types into the focused field with `execCommand('insertText')`, like a real key; it picks the first
  visible field when none is focused. It never logs, never touches the network, and writes one file only: the macro list.
- **Macros.** One press on a macro types a chat line (START sends it). Groups CHALLENGE, AFTER, STATUS come from
  `hk_fightcade_chat_macros` (a list of `{group, text}` in config; the default lines are the maintainer's Fightcade lingo, not
  verified against Fightcade's own help). MINE is the player's own: SAVE stores the text now in the field as a line,
  FORGET removes the one the focus was last on. They live in `/data/fightcade/macros.json` (0600, 20 lines, 60
  characters), a file no deploy writes and a Fightcade update never touches. SAVE refuses a password field.
- **Preview and tests.** `ansible/roles/fightcade/tests/test_osk.py` drives the real script in headless Chrome against a
  login page written for the test (none of Fightcade's code); its trusted-key run (#262) sends real key events through
  Chrome's DevTools pipe (`Input.dispatchKeyEvent`) and checks that F23 then Enter submits the form natively, once. `ansible/roles/power/tests/test_stickmouse.py` covers
  the stick side, incl. the broken cases (inert in a game, no keys when hidden, no password logging or saving).

## First launch: the download on the cabinet

`hk_fightcade_install` (#267) decides how Wine, Electron and Fightcade reach the cabinet. `deploy` (the default, and
The maintainer's cabinet) is everything above: the deploy uploads your `make -C build fightcade` downloads, unpacks them and
builds the prefix. `first-launch` is for an image other people flash: the image gets Box64, the launcher, its
helpers, the stick presets and the configs, but **none of the three third-party downloads and no prefix** (Fightcade
is proprietary; `skills/build-image/scripts/verify.sh` FAILs a first-launch image that holds any of their files).

The role then also installs, root-owned on the root fs: `hk-fightcade-fetch` (the engine, python3 stdlib),
`hk_fc_firstrun.py` (its screen), a copy of the `hk_fbneo_ini` filter, and `/usr/local/etc/coin-dojo-fightcade-fetch.json`,
the role's own facts as JSON: the pins (from `pins.json`), the unpack recipe and stamps, the lobby CSS and keyboard
script, the nativefier keys, the FBNeo ini keys and the prefix stamp. So the cabinet repeats the deploy's steps
exactly, without Ansible.

What happens when the player picks FIGHTCADE (`coin-dojo-fightcade`, before its own install check):

1. `hk-fightcade-fetch --check` (a fraction of a second): 0 = installed and current, the lobby starts as usual. 10 =
   something must be downloaded, 11 = only local steps (a finished download to unpack, a lobby file a new deploy
   changed, the prefix).
2. Otherwise the full-screen first-launch screen opens (the boot menu's SDL + cairo look, `settings_app.py`): what
   will be downloaded, from which host, the sizes ("Downloading Fightcade, Wine and Electron (about 307 MB)", one row per file with its host), the
   free space needed and the one-line note that Fightcade's own terms apply (wording from [LEGAL.md](../LEGAL.md)).
   START downloads, SELECT goes back to the menu (exit 0). A local-only repair skips this screen.
3. The engine (`--yes --progress`, its own process group): one run at a time (a lock); leftovers of a killed run
   removed; free space checked first (the missing downloads + each archive unpacked + the 540 MB prefix + 128 MiB; a
   fresh install needs about 2.1 GiB) and refused with the numbers otherwise; then each download from its pinned
   https url only (a redirect to anything else is refused), into `<file>.part` in `/data/fightcade/.incoming` (0700),
   resumed with a Range request next time (the 206's Content-Range must start at that byte; a 416 or a wrong start
   deletes the `.part` and fetches it again from 0), stopped at the pinned size, sha256-checked, renamed. Every pin is
   a versioned url: Fightcade's is `Fightcade-linux-v2.1.45.tar.gz` (#267 review, 2026-11-19: the same file as the old
   moving `Fightcade-linux-latest.tar.gz`, sha256 `9880cbfa...` confirmed by `fetch.sh`), so a new Fightcade release
   never breaks a first launch; it reaches cabinets only through a tested re-pin. A size or sha256 mismatch is a
   checksum error (damaged or changed file), never a network error. Every download is
   verified before anything is unpacked. Then `hk-fightcade-unpack` (the deploy's helper: all or nothing, sha256
   again, the #215 patch), the `.exe` modes, `nativefier.json`, the lobby files, the FBNeo base ini, and
   `hk-fightcade-prefix` (the deploy's prefix builder, about a minute on the Pi).
4. On an error (no network, a checksum or size mismatch, a full disk, a failed unpack or prefix) the screen says
   what happened in one sentence and goes back to the boot menu on any button (or after 60 s). Nothing is ever
   half-installed: each tree is swapped in whole with its stamp, the prefix stamp is written last, and the launcher
   starts nothing until `--check` says 0. The next pick resumes.

Proof on the test VM (2026-11-19, `make deploy LIMIT=coin-dojo-vm TAGS=fightcade hk_fightcade_install=first-launch`,
the real pinned files served by a local https mirror through the engine's test hooks, never the real servers): a
closed port gave the network message and left only Box64; a mirror that stopped Fightcade half-way left the two
verified downloads and a 82306877-byte `.part`, nothing unpacked; the screen in the headless cage session then
resumed it (`Range: bytes=82306877-`), unpacked the three trees (8 s), built the prefix (21 s) and exited 0, and
`coin-dojo-fightcade --check` said `install ok`. On disk: 1257 MB install + 541 MB prefix (`du -sm`). Not yet proven:
the same on the Pi with the real servers (the Pi's prefix build and the download speed).

Progress, the reason of a failure and every step go to `/data/fightcade/logs/fetch.log`. Over SSH, as the cabinet
user: `/usr/local/lib/coin-dojo-fightcade/hk-fightcade-fetch --plan` (what is missing, as JSON) and `--yes` (the
download, consent given by running it). A new Fightcade release is a re-pin of the versioned url in
`pins.json` (`build/fightcade/README.md`); cabinets keep getting the tested version until then.

## ROMs: shared folders and the stick + 6-button filter

**No second copy.** FBNeo reads the ROMs ES-DE already uses, through Wine's `Z:` drive:
`szAppRomPaths[0..5]` = `Z:\data\roms\{arcade,fbneo,cps1,cps2,cps3,neogeo}\` (the Neo Geo BIOS is in `neogeo/`).
`szAppRomPaths[6]` = `Z:\data\fightcade\roms-extra\`, a Fightcade-only folder that ES-DE does not scan. It holds
room clones (sets Fightcade rooms use that are not in the main folders) and the lobby's ROM downloads, so ES-DE
gets no duplicate entries. Clone zips there can rely on their parents in the main folders (#204, #208).

**Which games fit the cabinet** (#92 spike, 2026-11-17; method, counts only):

1. Match: each arcade zip's ROM CRCs (with its parent and BIOS) against the DAT of Fightcade's FBNeo romset
   (0.2.97.44). Of 594 zips: 554 complete and exact, 10 incomplete, 30 not an FBNeo arcade set
   ([#205 source notes](#sources)).
2. Filter: parse Fightcade FBNeo's driver sources for player 1's inputs. Keep games whose player 1 has a stick
   (up, down, left and right inputs), no analog or special control (dial, paddle, trackball, gun, wheel, pedal,
   spinner) and at most 6 buttons. Result: **532 games** (#204, 2026-11-17).

The button count has a trap: see the CPS2 volume "buttons" below.

## Dead ends

| What | Result | Source |
|---|---|---|
| Debian's `box64` 0.3.4 | crashes; the stack needs 0.4.x | #92 |
| x86_64 Electron (Fightcade's own launcher) under Box64 0.3.4 and 0.4.4 | crashes in tcmalloc ("free invalid pointer"); `BOX64_MALLOC_HACK` 1 and 2 do not help | #92 VM spike, 2026-11-17 |
| Wine 11.22 wow64 under Box64 | asserts in `virtual.c set_mem_area_state` | #92 VM spike |
| Wine's Vulkan renderer under Xwayland | presents nothing (black screen with audio); its fps is invalid | #92, table 5 |
| D3D7 (with the fast flags) | 38.86 fps, 65 % | #92, table 2 |
| Wine's GDI renderer, and FBNeo at 16-bit colour (`nVidDepth=16`) | slower; numbers not recorded | #92 ([#205 source notes](#sources)) |
| DirectDraw full screen | 43.4 fps: Wine stretches it on the CPU | #92, table 5 |
| Overclock to 2.8 GHz (`arm_freq=2800`) | considered (+16 % CPU, ~64 fps capacity) when the best was 93 %; not needed after the draw-path fixes, not tried | #92 |
| Box86 / armhf route | cannot be tested in the VM (the M1 has no AArch32); not tried on the Pi | #92 |
| A native arm64 build of Fightcade's FBNeo | offline only: Fightcade's netplay library `ggponet.dll` is closed-source x86 code, so a native build cannot play online. Not built; benchmark: not measured | #92 (option 1, a hypothesis then), #205 |
| `MemorySwapMax=0` for the Fightcade scope | no effect: the Pi kernel has no memory cgroup | #204 |

## Gotchas

- **wineboot hangs on the hidden Mono/Gecko dialog.** It waits ~5 min and leaves `syswow64` empty ("could not load
  kernel32.dll"). Set `WINEDLLOVERRIDES="mscoree,mshtml=d"`; the prefix is then ready in 8 s with 777 wow64 DLLs
  (#92 VM spike, 2026-11-17). The deploy's prefix builder checks that `kernel32.dll` exists in both `system32` and
  `syswow64`.
- **Xwayland needs `xkbcomp`.** Install `x11-xkb-utils` next to `xwayland`; Xwayland uses `xkbcomp` to build its
  keymap (#92: both were added to the cabinet before the first Pi run).
- **The CPS2 volume "buttons".** Fightcade FBNeo's CPS2 fighting input list declares 8 `p1 fire` inputs; two of
  them are the board's volume controls (#205). A plain count says 8 buttons and drops every CPS2 fighter from a
  "6 buttons or fewer" rule. Count them as 6 (the spike's parser showed 8 for the CPS2 fighting list; the fitted
  list has them at 6).
- **Building Box64 needs memory.** About 2 GB of swap at `make -j2`; `-j4` ran out of memory in a 2 GiB VM (#92).
  `build/box64/build.sh` defaults to `-j2` (`BOX64_JOBS`).
- **The first wineboot of a deploy can die once.** On 2026-11-18 the first deploy's `wineboot -i` exited with
  SIGKILL (rc 137) after 72 s; by hand it finished in 49 s, and the re-run passed. Cause: not located (#216 keeps
  the log next time).
- **FBNeo rewrites its ini on exit.** Copy a fresh one before each launch, or a second deploy dry run shows drift
  (#204).
- **The test VM has no GPU.** Electron draws black there unless started with `--disable-gpu` (#208).
- **Disk space.** The unpacked install is about 1.3 GB (test VM, 2026-11-18). The cabinet's root filesystem is
  6.9 GB, so the role installs under `/data` and refuses a first install with less than 1500 MiB free (#208).

## Open questions

| Question | State (2026-11-19) |
|---|---|
| An online match on the Pi | one match played, no desync, safe Box64 flags (2026-11-19 00:05); latency not measured |
| Netplay determinism with the Box64 fast flags (replay hash test) | not measured; fast flags off until it passes (#92) |
| Option B on sfiii3 with the default (safe) Box64 flags | not measured in a posted run |
| Per-frame benchmark on the cabinet after the #208 deploy | frame-pacing variants compared on the cabinet in #233; the Lua per-frame benchmark not re-run |
| Lobby -> game through `fcade://` on the Pi | proven: TRAINING (#228, 2026-11-18), an online match (2026-11-19) |
| Wired Ethernet ping numbers | not measured (#213) |
| Popup and link hardening of the lobby | open (#215) |

## Reproduce it

The role and the build scripts are the recipe. In this repo:

    make -C build box64                    # Box64 v0.4.4 for the Pi 5, in a pinned arm64 container
    make -C build fightcade                # sha256-pinned Wine 10.0, Electron 9.1.0, Fightcade 2.1.45
    make deploy TAGS=fightcade BECOME=1    # dry run; add APPLY=1 to install

Then pick FIGHTCADE in the boot menu and sign in with your own Fightcade account. Without `make -C build fightcade`:
add `HK_SET=hk_fightcade_install=first-launch` (or set it in `config/local.yml`) and the first FIGHTCADE pick
downloads it on the cabinet ([First launch](#first-launch-the-download-on-the-cabinet)).

To measure a game: copy [frametime.lua](fightcade-bench/frametime.lua) into FBNeo's folder, start
`wine fcadefbneo.exe <rom> -lua frametime.lua` (with ES-DE stopped), let the attract demo run about 70 s, and run
this on the `frametime.txt` it writes (it is how tables 6, 8 and 9 were computed):

```python
import sys

for path in sys.argv[1:]:
    ms = [float(x) for x in open(path) if x.strip()][600:]   # drop the first 600 frames (warm-up)
    s = sorted(ms)
    n = len(s)
    print(f"{path}: frames {n}  fps {n * 1000 / sum(ms):.2f}  p50 {s[n // 2]:.0f}  p99 {s[int(n * .99)]:.0f}"
          f"  max {s[-1]:.0f}  >20ms {sum(x > 20 for x in ms)}  >33ms {sum(x > 33.4 for x in ms)}")
```

Run on the files in [fightcade-bench/](fightcade-bench/), it prints the numbers in tables 6, 8 and 9.

## Sources

All in this project's tracker, 2026-11-14 to 2026-11-18:

- **#92** the spike: VM run, Pi runs (tables 1, 2, 4, 5, 8, 10), the decision for option B, the second-opinion
  review.
- **#204** the Fightcade mode design and its interface contract; ROM folders; the 532-game count.
- **#205** this note. Its first comment holds the spike's run notes that were not on a ticket before: tables 3, 7
  and 9, the gamma, GDI and 16-bit results, and the ROM match counts.
- **#207** the Box64 build and the pinned downloads (`build/box64/`, `build/fightcade/`).
- **#208** the `fightcade` Ansible role (`ansible/roles/fightcade/`), the security review, the VM proof.
- **#209** the session loop, cleanup and `vm.swappiness`. **#210** the boot menu. **#211** stick-as-mouse.
- **#213** Wi-Fi power save and `net-check` (table 11). **#214**, **#219** the ES-DE entries.
- **#215**, **#216** open follow-ups (lobby hardening, the one-off wineboot SIGKILL).
- Raw per-frame logs: [fightcade-bench/](fightcade-bench/) (one frame time in ms per line, 12 runs).
