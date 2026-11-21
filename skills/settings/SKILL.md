---
name: settings
description: The cabinet's SETTINGS app (last carousel entry) - join or forget Wi-Fi, rename the cabinet on the network, switch benchmark mode (FPS counter) and RetroAchievements (on/off, casual/hardcore, log in/out), with only the arcade stick + panel buttons or a USB keyboard. Page template for new settings. Build, test, preview, install (role settings), privilege design.
---

# SETTINGS app (Wi-Fi, cabinet name, benchmark mode, RetroAchievements), tickets #32-#35, #189, #171

A full-screen app that opens the moment you press A on **SETTINGS**, the last entry on the main carousel (after SYSTEMS;
#195, "Opens directly" below). After it exits you are back on the carousel with SETTINGS selected (#224, "Back to
the carousel" below). QUIT TO MAIN MENU on the carousel also works with one press (#224).
Look: the style kit only (`brand/BRAND.md`, `brand/tokens.json`); no colour or font of its own (a test checks it).

| Screen | What |
|---|---|
| SETTINGS | current Wi-Fi (network, signal, IP), the `<name>.local` name; WI-FI, CABINET NAME, one row per page in the order PASSWORD, GAME LISTS, ATTRACT MODE: ON/OFF, SHOW FPS: ON/OFF, RETROACHIEVEMENTS: ON/OFF - user, TIME ZONE: zone (`HOME_ORDER` in settings_core.py), BACK |
| ATTRACT MODE | ON/OFF + STARTS AFTER card; TURN ON/OFF, START AFTER (5/10/30/60 MIN), SHOW (VIDEO/SLIDESHOW/DIM/BLACK), APPLY NOW when ES-DE's file differs, BACK (section "Attract mode (#285)") |
| SHOW FPS | FPS COUNTER ON/OFF card (was BENCHMARK MODE, renamed #285; internal names `hk_benchmark_mode`, `benchmark_mode`, `BENCH_*` unchanged); TURN ON / TURN OFF, BACK (below) |
| GAME LISTS | card (last rebuild); REBUILD GAME LISTS, MUST PLAY SIZE (10/15/25/50), MUST PLAY GENRES MIN STARS (4.0/4.5/5.0), RELOAD ES-DE, BACK. Section "Game lists (#281)" |
| TIME ZONE | TIME ZONE + LOCAL TIME card; the short list (US Eastern / Central / Mountain / Pacific, UTC, London, Paris / Berlin, Tokyo, Sydney, Sao Paulo; the current one marked NOW), MORE ZONES ... (region, then city), BACK (below) |
| PASSWORD | LOGIN card (the cabinet user, also changes the NETWORK FOLDER); CHANGE PASSWORD: the new password twice on the masked keyboard (6 to 128 characters, not 12345, ASCII), then one root helper sets the Linux and the Samba password (below); BACK |
| RETROACHIEVEMENTS | ACCOUNT (user / NOT LOGGED IN) + ON/OFF card, MODE card; TURN ON/OFF, SWITCH TO HARDCORE / CASUAL (SOFTCORE), LOG IN / LOG OUT user (confirm, NO first), BACK |
| log in | user keyboard (A-Z 0-9 _ . -), then the password keyboard (masked; SHOW); DONE logs in. A failure stays there, field emptied |
| WI-FI | SCAN AGAIN, every visible network (signal bars, lock = secured, CONNECTED / SAVED), BACK |
| a network | CONNECT (saved) / ENTER PASSWORD (secured) / CONNECT (open), FORGET THIS NETWORK, NEW PASSWORD, BACK |
| keyboard | password: 3 pages (abc, ABC via SHIFT, `#+=` symbols): all 95 printable ASCII characters; SHOW/HIDE; DEL; DONE. Cabinet name: a-z 0-9 - only |
| confirm | FORGET / RENAME ask first, cursor on NO |

**Controls** (both at once, any moment; a keyboard plugged in while the app runs works at once):

| | Arcade panel | USB keyboard |
|---|---|---|
| move | stick (held = repeat) | arrows |
| select / type the key | LK | Enter (in a list) / just type (in a field) |
| back / delete | MK (deletes in a field; empty field = back) | Backspace = delete, Esc = back |
| done / connect | START | Enter |
| next field | | Tab (jumps to DONE) |
| quit the app (from any page, #224) | **SELECT** (help bar, right: `SELECT QUIT`, on every screen); START+SELECT too (as in games); BACK on the first screen | Esc on the first screen |

BACK (MK) goes up one page; QUIT (SELECT, on its release) leaves the whole app from any depth, even the keyboard
screen (what was typed and not DONE is dropped). The maintainer 2026-11-18: "make sure settings menu shows quit is to back out
all the way". SELECT was used by nothing else in the app (only in START+SELECT); `Input(select_quits=True)` is the
SETTINGS app's alone, so TROPHIES and the boot menu keep SELECT for the combo. Tests: `test_select_quits_from_any_depth`.

Typed characters come through SDL text input with the compositor's keymap, so shifted symbols and non-US
characters arrive as typed. START fires on release, so START+SELECT never also means DONE.

## Files

| File | What |
|---|---|
| `settings_core.py` | stdlib: keyboard model, nmcli wrapper (argv lists), host-name rules, screens (`App.handle/view`) |
| `settings_app.py` | ctypes into libSDL2 (window, pad via SDL game controller + `SDL_GAMECONTROLLERCONFIG`, keyboard text, hot-plug) and libcairo + libfreetype (drawing). No new packages on the cabinet. `--png OUT` renders without a window |
| `bootmenu_app.py` | boot menu (#210): ARCADE / FIGHTCADE cards, countdown bar, same stack and look as `settings_app.py` (`BootPainter`). Prints exactly `esde` or `fightcade`, exit 0. `--countdown N` (0 = wait; any input stops it; expiry = ARCADE), `--png OUT [--actions a,b] [--at S] [--moved S]` renders without a window, exit 3 no display/library, 2 usage, SIGTERM/SIGINT: prints nothing, exit 143 (the session treats "no answer" as esde). Installed to `{{ settings_dir }}` by the role. **Art + motion (#218):** each card has an illustration of what it does (a pair: ARCADE = local versus, P1 and P2 sticks on one control panel; FIGHTCADE = online versus, two sticks apart on a link under a signal; P1/P2 outlined so they read over any art). `--art-dir` (default `~/ES-DE/themes/coin-dojo/_inc/art/bootmenu`) may hold `esde.png` / `fightcade.png` at 618x318 (`skills/badges/gen_bootmenu_art.py`; only the personal overlay ships them): they go under the pictogram; a missing/empty/corrupt file falls back to the drawn art (logged as `art`). Motion is decoration: the gold border moves on the first frame after an input (a cached card layer, ~1.1 ms on the Mac vs 1.9 ms for the old full redraw), then the illustration slides in for 0.16 s and the glow pulses at 12 fps for 6 s after opening or the last input (`PULSE_FOR`), then holds still: an idle menu only waits for events. The note line is per card (ARCADE: QUIT on the carousel; FIGHTCADE: START + SELECT hold 2 s) |
| `tests/test_bootmenu.py` | boot menu tests: stick/keyboard/LK selection, countdown expiry and stop-on-input, PNG frames, the live loop on SDL's dummy video driver (skips where SDL2 is missing), BROKEN: no display, SIGTERM, bad arguments; #218: card PNG used, BROKEN missing/empty/corrupt art falls back, no card art in the community theme, the border is lit on the first frame after an input (fake SDL front) |
| `esde_return.py` | #224: run by the ES-DE hook after the app: waits until the stick is released and quiet, flushes the presses ES-DE queued while it waited, sends one Back (uinput keyboard, Backspace). #225 `--hold`: started by the hook before the app, makes that keyboard early and hands it to the plain run (never sends a key). "Back to the carousel" below. `--dry-run` reports sticks + uinput |
| `tests/test_esde_return.py` | its tests on a fake Hw + simulated clock, fake sysfs, fake ioctl; BROKEN: no stick / no uinput / no MSC_SCAN / not writable / held past the limit send nothing; a press during the wait moves the Back after it |
| `coin_dojo_set_hostname.py` | the root helper, installed as `/usr/local/sbin/coin-dojo-set-hostname` |
| `fake_nmcli.py` | TEST ONLY: answers the app's nmcli calls from a JSON file (tests, the VM, previews); never on the cabinet |
| `fake_ra.py` | TEST ONLY (#171): fake RetroAchievements login endpoint on 127.0.0.1 (tests, previews); never installed |
| `tests/test_settings.py` | all app tests (below) |
| `trophies_core.py` | TROPHIES page (#176), stdlib: RetroAchievements web API (user summary, achievement of the week), tolerant parsing, cache, badge/icon fetch (media host only, PNG-checked, size-capped), `Page.refresh/handle/view` |
| `trophies_app.py` | TROPHIES page front end (same stack as `settings_app.py`, `TrophyPainter`); `--png OUT [--fixture S.json,A.json]` renders without a window or network |
| `tests/test_trophies.py` | TROPHIES tests: fixtures, offline cache, the key never leaks, image host allowlist, PNG render |
| `trophiesd.py` | TROPHIES overlay (#177): resident daemon (unit `coin-dojo-trophiesd`, cabinet user, sandboxed); tap LP while no game runs shows the pre-rendered page through the power role's `overlay.py`, any button closes; refreshes every 15 min and after a game |
| `tests/test_trophiesd.py` | overlay tests: LP opens / any button closes, never during a game, refresh schedule, frame copy, no compositor |

## Attract mode (#285): the cabinet wins

- State file keys (the same file as the FPS counter): `attract_enabled` (bool), `attract_minutes` (5/10/30/60),
  `attract_type` (video/slideshow/dim/black). The roles' shared state-file filter (`ansible/filter_plugins`) takes only those values; anything
  else = not set, the hk_* default applies. OFF = ES-DE `ScreensaverTimer` 0. Choosing START AFTER turns it ON.
- Role `cabinet_tweaks` (tasks "Attract mode timer/type from the SETTINGS app"): the saved choice overrides
  `hk_attract_timer_ms` / `hk_attract_type` on EVERY deploy, so a redeploy keeps the owner's choice (decision, #285).
  `ScreensaverControls` (start by hand) stays the config's.
- Apply: the page saves the choice, asks "APPLY NOW?", and on YES the app quits and a detached
  `attract_apply.py apply --delay 3 ENTRY` (entry key `attract`: `settings_attract`) refuses while a game or Fightcade
  runs, writes the two keys into es_settings.xml (atomic, mode kept: it holds a scraper password) and then restarts ES-DE
  like the deploy handler (`next-mode` esde + `pkill -9 -x AppRun`, so ES-DE cannot save its old values over ours).
  NO leaves the choice saved; the page says "SAVED - APPLIES WHEN ES-DE RELOADS" and offers APPLY NOW.
- Hardening (security review): the kill command is a constant in the helper (`pkill -9 -x AppRun`; an entry file never
  names a command); `timer_ms` must be a number (a digit string is converted), `game_procs` / `game_patterns` lists of
  strings, else the defaults; es_settings.xml is opened without following a symlink, must be a regular file and is
  written flat 0600; an unreadable process table counts as a game running (applies and rebuilds wait).
- Test: `skills/settings/tests/test_attract.py` (every destructive call and every spawn guarded to its scratch root).

## Benchmark mode (#189): the cabinet wins

The maintainer 2026-11-17: what is set on the cabinet wins over the laptop's config. `hk_benchmark_mode` is only the
first-install default.

| File (cabinet user's home) | Written by | Read by |
|---|---|---|
| `~/.config/coin-dojo/settings-state.json` `{"benchmark_mode": true}` | the app (TURN ON/OFF); other keys kept | the app; roles `cabinet_tweaks` + `dreamcast` on every deploy |
| `~/.config/coin-dojo/benchmark-retroarch.json` | role `cabinet_tweaks` (`cabinet_benchmark_keys`, from `cabinet_benchmark_on`/`_off` + `hk_retroarch_cfg`) | the app |
| `~/.config/coin-dojo/benchmark-flycast.json` | role `dreamcast` (`dreamcast_benchmark_keys`: `rend.ShowFPS`, off = `hk_flycast_show_fps`) | the app |

- Keys file: `{file, format: retroarch|ini, [section], default, on: {key: value}, off: {key: value}}`. The values
  live only in the roles' defaults; the app never has its own copy. Keys/values are a narrow charset (no quote,
  no newline); a broken keys file refuses the toggle and writes nothing. No keys files yet = "NOT SET UP YET".
- The toggle writes only those keys: retroarch.cfg `key = "value"` on the LAST matching line (as `lineinfile`),
  emu.cfg `key = value` under `[config]` (as `ini_file`); a missing key is appended; every other byte stays. Each
  file: temp file, fsync, rename. Then the state file. A missing retroarch.cfg is skipped (RetroArch writes it on
  first start; the next deploy applies the state).
- Only while no game runs: `trophiesd.game_running(..., me=<app pid>)` with the deploy guard's list (`game_procs`,
  `game_patterns` in the entry file); the app's own line of processes under ES-DE is not a game. Busy = refused,
  files untouched.
- Deploy: each role slurps the state file and sets `cabinet_benchmark` / `dreamcast_benchmark` from
  `"benchmark_mode": true|false` before it writes the keys; missing, malformed or another value = `hk_benchmark_mode`.
  (The deploy matches the key with a regex; the app needs valid JSON. The app is the only writer.)
- Preview the screen: an entry file with `benchmark_state` / `benchmark_keys` pointing at test files, then
  `settings_app.py ENTRY --png OUT --nmcli fake_nmcli.py --actions down,down,ok`.

## Pages (#171): a new setting is one class

WI-FI and CABINET NAME are built in. Every other home row is a `settings_core.Page`: `id`, `title`, `label()` (the
home row, may carry the value), `cards()` (status cards: caption + value left, optional right pair), `text()`,
`items()`, `choose(app, what)`, and `submit` / `confirm` for keyboards and YES/NO screens it pushes. A write goes
through `app.run(busy_text, fn)`: busy frame, `fn() -> (ok, message)`, toast, every label refreshed. Register it in
`settings_app.build()` (`pages=[...]`). Settings the deploy also writes follow the cabinet-wins rule below: one key in
the state file, the owning role reads it.

## Game lists (#281): rebuild on the cabinet, no Mac

- Page GAME LISTS runs `lists_rebuild.py rebuild` (stdlib, a child of the app, no shell) with the `lists` block of
  `settings.hks` (role `settings`, `settings_lists`). It reads ES-DE's real scraped gamelists (names-only ones count as
  "NO SCRAPED GAMES YET - SCRAPE FIRST"; a broken XML is named: "BAD GAMELIST: SYSTEM"), builds with the shipped
  `collgen.py nested --apply` into `.lists-build-<pid>` beside `nested_out`, gates on `validate_nested.py` (`RESULT: PASS`),
  and only then swaps: each folder / file is journalled (`<backup_root>/swap.journal.json`) and backed up under
  `<backup_root>/<ts>/`; any failure rolls back at once, a killed run is rolled back by the next run (`recover`). Old
  lists stay untouched on every error. One rebuild at a time (flock), refused while a game or Fightcade runs.
- MUST PLAY SIZE and MUST PLAY GENRES MIN STARS are saved in the state file (`must_play_top`,
  `must_play_genre_min_stars`); anything outside the choices falls back to 25 / 4.5.
- RELOAD ES-DE (or YES after a rebuild): the app quits and a detached `reload --delay 3` writes `next-mode` esde and runs
  the deploy's kill command (same guard as the `restart es-de` handler).
- Personal edition (`lists.edition`): the page is visible but refuses ("MANAGED FROM THE MAC"); the Mac's
  `make deploy-collections` stays the owner of those lists. The community picks come from `picks_example.html` unless you
  put a `cabinet-picks.html` in the tools folder.
- Test: `skills/settings/tests/test_lists.py` (broken cases: no scrape, malformed gamelist, failing build/validator, swap
  failing or killed half way, game running, lock, personal).

## Password (#271): one password for the login and the network folder

- App: PASSWORD > CHANGE PASSWORD asks twice (masked keyboard, SHOW toggles). Refused on the screen, before any helper
  runs: a mismatch (back to an empty first field), under 6 characters, `12345`, over 128, non-ASCII. Then
  `sudo -n /usr/local/sbin/coin-dojo-set-password` runs with NO arguments and the password as the one line on STDIN
  (`Password.set` in `settings_core.py`); both keyboard fields are emptied, the screens keep nothing, the log gets the
  command and the exit code only.
- Helper `coin_dojo_set_password.py` (root:root 0755, installed like the hostname helper): refuses an argument, a caller
  that is not root-through-sudo, `SUDO_UID` < 1000 (root, system users), a name that does not match the uid and a
  nologin account; **refuses an admin** (#270: the caller is in the `sudo` group, or `/etc/sudoers.d/coindojo-admin`
  exists) with exit 2, nothing run, and the line `ADMIN IS ON: CHANGE IT WITH passwd`, which the page shows as it is
  (an admin's login password is the sudo password; this NOPASSWD helper must not replace it without the old one);
  re-checks the password (one line, 6..128 printable ASCII, not `12345`). Then, holding an exclusive flock on
  `/run/coindojo/password.lock` (a root dir, never /run/lock or /tmp) (role share's `coin-dojo-share-sync` takes the same lock before it reads anything,
  so it never deletes the new Samba password; a busy or foreign lock file = exit 1, nothing changed): first, if
  `smbpasswd` and `/etc/samba/smb.conf` exist, `smbpasswd -s -a USER` sets the network folder's password, else that step
  prints `skipped (Samba not installed)`; then `chpasswd` sets the Linux password of the CALLER (the cabinet user,
  nobody else's) through a pipe with a fixed environment. Exit 0 ok, 2 refused, 1 failed, 3 Linux ok but the network
  folder failed. No traceback and no value in any message (a tool that echoes it is scrubbed), core dumps off.
  Installed from the controller straight to `/usr/local/sbin` as root (never via the `/tmp` stage, #270 L7).
- sudoers (`/etc/sudoers.d/coin-dojo-settings`, visudo-validated): `coindojo ALL=(root) NOPASSWD: <helper> ""`: the empty
  string means NO arguments, so `sudo coin-dojo-set-password anything` is refused by sudo itself. The password is never in
  argv, so not in `ps`, sudo's log or the journal.
- Audit: the helper writes one line to the system log (`logger -p auth.notice -t coin-dojo-set-password`): `password changed via SETTINGS for USER`, or one failure line naming the step; never the value. `smbpasswd -a` is on purpose: it also adds the Samba user, so a person turns network folder access on by setting their own password.
- Forgot it? The recovery path is the boot-partition password (`password_hash` in `coindojo.yml`), applied at the next boot.
- Compatible with the default-login reminder (#265, compares `/etc/coindojo/default-password` with shadow) and the
  sudo grant (#270): both read the password that this helper sets; neither file is touched here.
- Tests: `tests/test_password.py` (helper, app flow, leaks, broken cases), role rules in `test_settings_role.py`.

## Time zone (#206): hk_timezone, the cabinet wins

- Config: `hk_timezone` (example default `Etc/UTC`; yours in your local config, e.g. `Europe/Berlin`). Role
  `settings` slurps the state file as the cabinet user, takes its `timezone` (parsed by the shared state-file filter:
  a string of a zone name's shape, else ignored) or else `hk_timezone`, and runs
  `timedatectl set-timezone -- ZONE` only when `timedatectl show -p Timezone` differs, and takes the state zone only if it is on `timedatectl list-timezones` (else a WARN and `hk_timezone`) (a second run is ZERO).
- App: the TIME ZONE page calls `timedatectl set-timezone ZONE` itself, as the cabinet user, and writes
  `timezone` into `settings-state.json`; a zone is accepted only if its name has the shape `Area/City` (no `.`, so
  no `..` or path; no leading `-`), has a zoneinfo file AND is on `timedatectl list-timezones`.
- **Root step = polkit, not a helper (#206).** The second block of `50-coin-dojo-settings.rules` lets coindojo at the seat
  (local + active, like the Wi-Fi block) run exactly `org.freedesktop.timedate1.set-timezone`; not the clock, NTP or
  RTC mode. Narrower than a root helper plus a sudoers regex: no new root code to keep, no new sudo path (sudo works
  from any ssh session; polkit here only for the seat), and `systemd-timedated` validates the name against tzdata
  itself, on top of the app's check. The cost: the rule cannot restrict WHICH zone (any real one), which a harmless
  setting does not need. Switch: `settings_polkit_timezone`; off on the test VM with the rest (`hk_settings_polkit`).

## RetroAchievements (#171): the cabinet wins

| File (cabinet user's home) | Written by | Read by |
|---|---|---|
| `~/.config/coin-dojo/settings-state.json` `cheevos_enable`, `cheevos_hardcore` (bool), `cheevos_login` (true = logged in here, false = logged out here) | the app; other keys kept | the app; roles `retroarch122` + `dreamcast` on every deploy |
| `~/.config/retroarch/retroarch.cfg` `cheevos_enable`, `cheevos_hardcore_mode_enable`, `cheevos_username`, `cheevos_token`, `cheevos_password` (always "") | app + `retroarch122` | RetroArch |
| `~/.config/flycast/emu.cfg` `[achievements]` `Enabled`, `HardcoreMode`, `UserName`, `Token` | app + `dreamcast` | flycast |

- Deploy: each role slurps the state file; `cheevos_enable` / `cheevos_hardcore` true|false override
  `hk_cheevos_enable` / `hk_cheevos_hardcore`; any `cheevos_login` bool makes it skip the user name and token tasks
  (the Keychain token is not written any more). Missing, malformed or another value = the levers, as before (#45).
  Per-core hardcore exceptions (`hk_cheevos_hardcore_by_core`) still apply on top.
- TROPHIES (#196): page and overlay show the user logged in here when the state file says `"cheevos_login": true`
  (name from retroarch.cfg `cheevos_username`, else emu.cfg `UserName`; never the token), else `hk_cheevos_user`.
  The TROPHIES entry carries `settings_state` + `cheevos_files`; the overlay looks again on every refresh, and a
  cached page of another user is not shown. The TROPHIES entry itself still needs `hk_cheevos_user` set.
- Shown state: the state file, else what retroarch.cfg says, else emu.cfg, else off. Logged in = a user AND a token.
- LOG IN: `POST r=login2&u=USER&p=PASSWORD` (form body) to `cheevos_api` (entry file; default
  `https://retroachievements.org/dorequest.php`), TLS verified, no proxy (`ProxyHandler({})`), redirects refused,
  answer capped at 64 KB. Only `https://retroachievements.org` (default port, no user info; #198). Plain http to
  127.0.0.1/localhost (the tests' `fake_ra.py`) and the entry's `game_proc` count only with `settings_app.py
  --test-hooks`, which ES-DE's command and the role never pass. The entry file is root:root 0644 (the app only reads it).
  An answer http.client cannot parse (bad status line, ...) is LOGIN FAILED: NO CONNECTION (type), never a crash. The answer's `User` + `Token` are checked
  (`[A-Za-z0-9_.-]{1,32}`, `[A-Za-z0-9]{8,64}`) before they go into the files. Any failure writes nothing.
- LOG OUT clears user, token and password keys in both files; `cheevos_login: false` keeps a deploy from logging back in.
- Writes: only those lines (same rules as benchmark mode), temp file 0600, fsync, rename; an existing file keeps its
  mode, a file the app creates is 0600, the state file 0600 in a 0700 dir. Only while no game runs (refused before the
  network call too).
- Log (`~/.cache/coin-dojo/settings.log`): `cheevos_login` (user, ok, RA's error text with the password redacted),
  `cheevos_logout`, `cheevos_enable`, `cheevos_hardcore`, refusals. Never the password (also redacted URL-encoded:
  `quote_plus`, `quote`), never the token.
- Preview: an entry file with `cheevos_files`, `cheevos_api` (a running `fake_ra.py`), `benchmark_state` and
  `game_proc` (a fake process table with cage + es-de: macOS has no /proc, so every write would be refused), then
  `--test-hooks --actions down,down,down,ok`. `HK_SETTINGS_SHOTS=DIR python3 skills/settings/tests/test_settings.py` keeps every
  RA screen as a PNG.

## Security (Security reviews)

- Every command is an **argv list**; no shell anywhere (a test greps the code, with planted broken cases).
- The Wi-Fi password goes to nmcli as **one argv element**, byte for byte: `nmcli --wait 30 device wifi connect
  <SSID> password <PW> ifname wlan0` (ArkOS's menu broke on special characters; tested with `p@ss "word" $HOME 'x'`
  through both the on-screen and the USB keyboard). It is never logged (`~/.cache/coin-dojo/settings.log` redacts
  it as `***`; the log starts over above 1 MB), never shown unless SHOW (masked again after a failed connect), and
  redacted from any nmcli error shown. NetworkManager stores it.
  **No grim / proof screenshots while the password keyboard is up with SHOW on** (a screenshot is a copy of it).
  Note: ticket #34 asked for "never on argv" (`/proc/<pid>/cmdline` is readable by local users while nmcli runs,
  up to ~45 s: `--wait 30` plus the timeout); the maintainer's 2026-11-15 brief chose the argv element. The cabinet has one login user (coindojo). Moving to
  `nmcli connection up ... passwd-file /dev/stdin` would take it off argv; it needs a real Wi-Fi test first.
- **Cabinet name** = a root helper with fixed paths, run as `sudo -n /usr/local/sbin/coin-dojo-set-hostname NAME`.
  `/etc/sudoers.d/coin-dojo-settings` (0440, visudo-validated):

      coindojo ALL=(root) NOPASSWD: /usr/local/sbin/coin-dojo-set-hostname ^[a-z0-9]([a-z0-9-]{0\,61}[a-z0-9])?$

  One command, one argument, regex-limited by sudo (1.9.16 on the cabinet; regex args need >= 1.9.10). The helper
  checks the name again (`fullmatch`, so `name\n` fails), computes the new `/etc/hosts` first (stops before
  `hostnamectl` if it cannot read it), runs `hostnamectl set-hostname NAME`, makes `/etc/hosts`
  have exactly one `127.0.1.1<TAB>NAME` line (the cabinet had two: `raspberrypi` and `coin-dojo`), every other line
  untouched (written to a temp file, fsync, rename), and restarts avahi-daemon so **NAME.local** answers at once. Nothing on the screen changes.
- **Wi-Fi permissions**: `/etc/polkit-1/rules.d/50-coin-dojo-settings.rules` (template, user = `settings_user`): coindojo, local + active session
  (the tty1 autologin that runs ES-DE), actions `wifi.scan`, `network-control`, `settings.modify.system` only.
  (Pi OS already allows `settings.modify.system` to sudo/netdev users; the rule adds nothing wider.) Over SSH
  (not active) nmcli still asks for authentication. The same file has the TIME ZONE rule (section above).

## Steps

1. **Tests** (laptop, plain python3, seconds): `make -C skills/settings test`
   PASS = `RESULT: PASS skills/settings/tests/test_settings.py`. Also run
   `python3 ansible/roles/settings/tests/test_settings_role.py` and
   `python3 skills/collections/tests/test_settings_system.py`.
2. **Preview a screen** (laptop; needs Homebrew cairo + freetype): 
   `make -C skills/settings preview OUT=/tmp/s.png ACTIONS=ok,down,down,ok,ok,text:abc` then look at the PNG.
3. **Test VM** (`make vm-up` first; cpu-gate):
   `make deploy LIMIT=coin-dojo-vm TAGS=settings APPLY=1` installs it with the fake nmcli and a standalone SETTINGS
   system. Screenshot: in the VM `hk-session start`, then run the app under the session
   (`WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/<uid> python3 /data/coin-dojo/settings/settings_app.py
   /data/coin-dojo/settings/settings.hks --actions ok &`), `hk-session shot`. `make vm-down` at the end.
4. **Cabinet dry run**: `make deploy TAGS=settings BECOME=1` (BECOME reads the sudo password from the Keychain).
   REVIEW the diff: the app files, entry, helper, sudoers line, polkit rule. Only Coordinator applies (`APPLY=1`).
5. **Carousel entry on the cabinet**: after the first install, `make deploy-esde BECOME=1 [APPLY=1]`: the nested
   build sees `/data/coin-dojo/settings/settings.hks` and adds the SETTINGS system last (`collgen.py nested
   --settings-entry`), with its link and gamelist; `validate_nested.py` checks it like the others. The role prints
   whether `es_systems.xml` has it yet.

## Opens directly (#195): an ES-DE game-select script

The maintainer 2026-11-17: SETTINGS should open the app, not a list with one entry. ES-DE 3.5.0 (source at tag v3.5.0,
`50e4b600`) has no "launch on enter" option and no "system entered" event:

| ES-DE 3.5.0 | What it does | Source |
|---|---|---|
| `system-select` | fires on every carousel cursor change (scroll / focus), args: system name, full name, folder | `es-app/src/views/SystemView.cpp:251-273` (`onCursorChanged`) |
| `game-select` | fires when a gamelist is SHOWN (entering it: `goToGamelist` -> `onShow` -> `updateView(CURSOR_STOPPED)` -> next `update()`, only for the current view) and on each cursor stop in a gamelist; args: game path, name, system name, system full name; a fast scroll fires it with none | `ViewController.cpp:871,896,1324`, `GamelistView.cpp:83-109,451-465,640-665` |
| `game-end` | after ES-DE's own launch returns; then `setPlayMetadata` saves lastplayed and the gamelist fires `game-select` again | `FileData.cpp:2353-2357`, `ViewController.cpp:1266` |
| gates | `CustomEventScripts` (all events) and `CustomEventScriptsBrowsing` (the two select events) default off; `CustomEventScriptsBrowsingNonBlocking` default ON (detached thread via `system()`, max 8) | `es-core/src/Settings.cpp:314-316`, `Scripting.cpp:36-49`, `PlatformUtil.cpp:99-131` |
| scripts | every file in `~/ES-DE/scripts/<event>/`, sorted, run as one `sh -c` string, args in double quotes | `es-core/src/Scripting.cpp:52-120` |
| native options | none: no per-system auto-launch, no single-game skip, no launch-on-enter setting (folder links only launch from inside a gamelist) | `Settings.cpp`, USERGUIDE.md (not located otherwise) |

So the SETTINGS gamelist's own `game-select` is the hook (it holds one entry): role `settings` installs
`templates/esde-settings-hook.sh.j2` as `~/ES-DE/scripts/game-select/50-coin-dojo-settings.sh` and the same file under
`scripts/game-end/`. It:

- returns at once for any other system, a non-`.hks` path or a fast scroll (only assignments and `[`/`case` before
  that: ES-DE waits for it on every gamelist cursor stop);
- runs the command ES-DE runs for the entry (`python3 /data/coin-dojo/settings/settings_app.py <settings.hks>`) and
  waits for it: with `CustomEventScriptsBrowsingNonBlocking` off ES-DE is paused exactly as during a launch (no
  attract mode or video behind the app);
- one copy: a lock dir in `$XDG_RUNTIME_DIR` with its pid; a lock whose pid is gone is taken over;
- `game-end` of SETTINGS (ES-DE's own launch, A on the list) leaves a mark, so the `game-select` ES-DE fires right
  after (lastplayed saved) does not reopen the app; a mark older than 10 s is dropped.

- after the app (#224) runs `esde_return.py` in the foreground (`settings_hook_back`, default on): see "Back to the
  carousel" below;
- QUIT (#224): the same script, entered on system `quit` (`settings_quit_system`) with a `.hks` path, runs
  `bash /data/coin-dojo/esde_entry.sh menu <path>` (= `nested.QUIT_COMMAND`, `settings_quit_script`; Emulation's script,
  unchanged): ES-DE ends and the boot menu comes, one press. It refuses while a game runs (ES-DE then shows the
  one-entry QUIT list). The game-end copy never runs it.

es_settings keys (`hk_esde_settings`, role `cabinet_tweaks`, in place): `CustomEventScripts` true,
`CustomEventScriptsBrowsing` true, `CustomEventScriptsBrowsingNonBlocking` false. Tests:
`ansible/roles/settings/tests/test_settings_hook.py` (runs the rendered script the way ES-DE does, broken copies).

Preview proof (2026-11-17, ES-DE 3.5.0 AppImage in the preview container, `--debug`, xdotool keys, fake app logging
its argv): 12 carousel moves across SETTINGS fired `system-select` 5 times for it and launched nothing; Return on
SETTINGS fired `game-select` and ran the app once while ES-DE waited; after it exited ES-DE showed the SETTINGS list
and fired nothing; Return on the list (ES-DE's own launch) ran it once, `game-end` + `game-select` followed and nothing
reopened; MK/Backspace went back to the carousel; browsing a CPS1 list launched nothing.

Known limits (check on the cabinet):
- Input pressed while the app runs is queued for ES-DE and handled after the hook returns: that was #220, fixed by
  the flush in "Back to the carousel" (#224).
- ES-DE does not reset its idle clock after a script (it does after a launch: `normalizeNextUpdate`), so after more
  than `hk_attract_timer_ms` (10 min) in the app, attract mode may start on return; any button ends it.
- `CustomEventScripts` makes ES-DE run `sh -c` with game paths and names in double quotes for `game-select` and
  `game-end` (only events with a scripts dir run): a name with a backtick or `$(` in the gamelist would be expanded
  by that shell (ES-DE's quoting, `Scripting.cpp:66-101`), not by this script.

## Back to the carousel (#224): flush, then one Back

The maintainer 2026-11-18 picked it (emulation's #223 research: ES-DE 3.5.0 has no way for a script to send it to the carousel).
Why a flush is needed too (#220, ES-DE source at `50e4b600`): the hook is a blocking `system()`
(`es-core/src/utils/PlatformUtil.cpp:129-131`) and ES-DE pumps no events meanwhile. ES-DE's SDL reads the stick from
evdev (`SDL_HINT_JOYSTICK_HIDAPI` 0, `es-app/src/main.cpp:638`), so every press made in SETTINGS waits in ES-DE's
kernel evdev buffer and is handled once the hook returns (`main.cpp:508-549`). ES-DE's own launch drops those
(`PlatformUtil.cpp:289` "Drop all events", `setBlockInput`, `ViewController.cpp:1084-1086`); a script gets nothing
like it. So the LK that left SETTINGS, or a quick second one, landed on the one-entry list and launched it again.

`esde_return.py` (installed with the app, run by the hook after the app):

1. takes the keyboard the `--hold` copy made before the app (#225, below), or makes a one-key uinput keyboard
   `coin-dojo-esde-back` (Backspace) itself, and opens the stick(s) read-write
   (`/sys/class/input`: X/Y or hat axes + buttons 288..319; never a `coin-dojo-*` device);
2. waits until cage holds the keyboard's `/dev/input/eventN` open (polled in `/proc/<cage pid>/fd`, at most 2 s; if
   never it logs `cage never opened the keyboard` and tries anyway) AND no button/direction is held and no press
   came for 0.3 s (at most 5 s). Only button events, hat changes and analog moves past half deflection count, so
   analog jitter cannot hold it up;
3. writes 2048 no-op `EV_MSC/MSC_SCAN + SYN_REPORT` pairs into each stick's evdev node: every reader's buffer
   overflows, the kernel keeps only the newest events + `SYN_DROPPED`, SDL (2.32.10 on the cabinet) re-reads the real
   state without replaying, so ES-DE sees no press from inside SETTINGS (SDL ignores `EV_MSC`);
4. sends exactly one Backspace down + up (ES-DE's keyboard B, `es-core/src/InputManager.cpp:575`) and returns; a
   forked child removes the keyboard 1.5 s later (the hook does not wait for it). ES-DE handles the Back after the
   hook: SETTINGS list -> carousel, SETTINGS still selected.

### No stale frame on the way out (#225)

The maintainer 2026-11-18: "quiting setting has a lil pop up i see for some microsections". ES-DE's last frame before the
blocking hook is the SETTINGS gamelist, and it is what shows between the app's window closing and ES-DE handling the
Back (the theme's `gamelistToSystem` is `instant`: `capabilities.xml`, first `<transitions>` = `automatic`; global
`ThemeTransitions` stays). The fix makes that frame the same picture the app ends on:

- **Theme:** `themes/coin-dojo/_inc/per-system/settings.xml` gives the SETTINGS gamelist only `baseFill` (the
  plain theme bg, `baseColor` = brand `bg`): every other gamelist element is hidden (`visible` false; `opacity` 0 for
  clock/systemstatus/helpsystem; all textlist colours `00000000`, ES-DE 3.5.0 has no `visible` for it).
  `themes/tests/test_theme_xml.py` fails when a new gamelist element is not hidden there.
- **App:** on quit `settings_app.py` fades its last frame to that bg (6 veils, ~0.15 s), then closes. (Entering
  SETTINGS now also shows the plain bg for the moment before the app opens.)
- **Early keyboard:** the hook starts `esde_return.py --hold &` before the app. It makes the keyboard at once (cage
  opens it while SETTINGS is in use), listens on `$XDG_RUNTIME_DIR/coin-dojo-esde-back.sock` (0600) and hands the open
  fd to the plain run (SCM_RIGHTS), which then skips the up-to-2 s cage poll. The holder never writes a key, so a
  holder that is silent (1 s timeout), dead (stale socket) or missing cannot cause a second Back: the plain run makes
  its own keyboard as in #224. The holder exits after the hand-over or when the hook (its parent) is gone.
- Release + 0.3 s quiet wait and the flush are unchanged.

Fail safe, each case logged: no writable `/dev/uinput` (root:input 0660, role power #211; coindojo is in `input`) =
nothing written at all (the old one-entry list); no stick = Back only (keyboard-only use); a stick without
`MSC_SCAN` or not writable = the others are flushed and that one is named in the log; a button held 5 s = flushed,
no Back (the held press would land on the carousel). Always exit 0. Adds about 0.3-0.5 s after the app; nothing
anywhere else (the hook's fast path is unchanged). Log: `~/.cache/coin-dojo/settings-return.log` (+ ES-DE's stderr).
Env overrides for tests: see the script header.

Cabinet facts (Coordinator, 2026-11-18, read-only `/proc/bus/input/devices` + `/proc/<cage>/fd`): cage already holds
keyboard devices open (event0 `pwr_button`, event3 `vc4-hdmi-0`, event5 `vc4-hdmi-1`: kbd/CEC), so the Wayland seat
has a keyboard before ours appears and ES-DE gets keyboard focus as usual. The two sticks are event1/event2
(`USB gamepad`, js0/js1); cage does not open them, so the flush reaches only ES-DE's (and SDL's) readers.

**Cabinet proof (Coordinator, after `make deploy TAGS=settings BECOME=1 APPLY=1`; no game running):**

```sh
ssh coindojo@<cabinet>
python3 /data/coin-dojo/settings/esde_return.py --dry-run; tail -1 ~/.cache/coin-dojo/settings-return.log
#   want: sticks [('/dev/input/event1', True, 'USB gamepad'), ('/dev/input/event2', True, 'USB gamepad')],
#         uinput /dev/uinput writable
grep -B5 'Handlers=.*kbd' /proc/bus/input/devices | grep Name   # keyboards the seat has (pwr_button, vc4-hdmi-*)
ls -l /proc/$(pgrep -x cage)/fd | grep /dev/input      # cage has keyboard(s) open (event0/3/5), not the sticks
tail -f ~/.cache/coin-dojo/settings-return.log ~/.cache/coin-dojo/esde-entry.log   # keep open while testing
```

On the cabinet: (1) carousel -> SETTINGS, LK: the app opens (one press). (2) Move around, open WI-FI, LK a few
times, then SELECT: the app closes and ES-DE shows the carousel with SETTINGS selected; the log says
`flushed 2 stick(s), Back sent (keyboard /dev/input/eventN early, cage ready)` (`early` = the held keyboard, #225;
`new` = it made its own, then a `no early keyboard (...)` line above it says why). (3) **Press LK ~10 times fast inside
SETTINGS, then SELECT quit: land on the carousel, SETTINGS not reopened.** (4) Open again, exit with BACK on the
first screen (LK) and tap LK again fast: carousel, SETTINGS not reopened (one `Back sent` line per exit). (5) Exit
with START+SELECT and hold SELECT 2 s: the Back waits for the release. (6) Carousel -> QUIT TO MAIN MENU, LK: the boot
menu comes (esde-entry.log `requested menu`). (7) Browse a game list: nothing in either log. Failure signs: `nothing
sent` / `not flushed` / `no Back` lines (the reason is on the line); `cage never opened the keyboard` (then ES-DE
may stay on the one-entry list = Back lost); SETTINGS reopened = a press slipped past the flush.

**#225 proof (Coordinator, after `make deploy TAGS=settings,esde_theme BECOME=1 APPLY=1`; the theme deploy lays `personal/themes/`
over the repo theme as usual).** (8) Each exit logs exactly one `Back sent (keyboard /dev/input/eventN early, cage
ready)` and no `no early keyboard`; `pgrep -f 'esde_return.py --hold'` prints nothing once you are on the carousel.
(9) Grim burst during one exit (start it, then press SELECT in SETTINGS within 2 s):

```sh
ssh coindojo@<cabinet>
export WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/$(id -u); d=/tmp/hk225-$(date +%H%M%S); mkdir -p $d
sleep 2; for i in $(seq -w 1 60); do grim -t jpeg -q 60 $d/$i.jpg; done; ls $d | wc -l   # ~60 frames, ~3-4 s
tail -3 ~/.cache/coin-dojo/settings-return.log
```

Copy `$d` to the laptop. Want: SETTINGS -> SETTINGS darkening -> plain dark bg -> carousel with SETTINGS selected,
and **no frame of the one-entry SETTINGS list** (no list row, title, art or help bar). grim samples every ~50-70 ms,
so it proves the long stale frame is gone; a single 16 ms frame can fall between shots. Also: LK ~10x fast before
SELECT still lands on the carousel (no reopen). `rm -r $d` after.

## Carousel entry (how it stays)

`collgen.py nested --settings-entry FILE` (Emulation's tools; minimal change) appends one `<system>` after SYSTEMS:
name `settings`, `<platform>ignore</platform>` (never scraped, no MAME name rules), extension `.hks`, command
`python3 /data/coin-dojo/settings/settings_app.py %ROM%`, one link `roms_nested/settings/Settings.hks -> FILE`, a
one-game gamelist (ES-DE's lastplayed kept). Role `esde_collections` passes the flag only when FILE exists, so
every regeneration keeps SETTINGS and a cabinet without the app has no dead link. The `settings` gamelist is
skipped by every gamelist reader (`collgen.TOOL_SYSTEMS`, `nested.TOOL_SLUGS`). Badge `_inc/systems/settings.png`
(badge.py), chip SETUP (`gen_chips.py`, token `chips.setup`).

Not a game (tested on ES-DE 3.5.0 in coin-dojo-vm, 2026-11-15, by launching SETTINGS from ES-DE itself):
- the entry has `<nogamecount>true</nogamecount>`: without it, one launch put SETTINGS into LAST PLAYED ("1 GAME");
  with it, LAST PLAYED stays `<NO ENTRIES FOUND>` and SETTINGS still shows on the carousel.
- no "1 GAME" pill under SETTINGS: theme.xml ends with `<include>./_inc/per-system/${system.theme}.xml</include>`
  (outside the `<variant>`: inside it ES-DE ignored the include) and `_inc/per-system/settings.xml` sets the
  system view's `gameCounter` to `<visible>false</visible>`. Other systems have no file (ES-DE logs a debug line).
