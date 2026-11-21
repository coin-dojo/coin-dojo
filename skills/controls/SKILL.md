# Skill: controls helper (#57)

Shows the button layout for the highlighted game in the ES-DE info panel, and lets the player bring the same
card up in game with **START + HP**. RetroArch pauses while the card is open, and START + HP again closes
it and resumes the game. The design the maintainer approved on 2026-11-14 is the "Controls Helper" canvas
(boards A to D).

## One rule (the maintainer, 2026-11-14)

The panel is Xbox X Y RB / A B RT (Jab Strong Fierce / Short Forward Roundhouse). Each system's standard Xbox
mapping is projected onto the panel by position. PS1, Neo Geo, N64, Genesis/Sega CD and Atari 2600 use the remaps in
`skills/controls/remaps` (each file's header says why; Ansible `cabinet_tweaks` `cabinet_remaps` deploys every one);
Saturn needs none. Dreamcast uses the flycast mapping file in `skills/controls/flycast` (see the Dreamcast section at
the end).

## How it works

| Piece | Where | Made by |
|---|---|---|
| Layout table: buttons per system, tested or not, ES-DE controller type | `controls.py` `LAYOUTS`, `SYSTEM_LAYOUT`, `GAME_LAYOUT` | hand-edited |
| N64 per-game action labels (in-game overlay only) | `n64_games.tsv` | hand-edited (plain text) |
| Card per layout (info panel) | theme `_inc/controls/<layout>.png` | `controls.py render` (committed, original art) |
| Which card a game shows | `<controller>` in each real gamelist; nested lists copy it | `controls.py gamelists` |
| In-game overlay, one per game | `/data/coin-dojo/controls/overlays/<system>/<rom stem>.png` + `.cfg` | `controls.py render` (not committed: game names) |
| Overlay loaded for that game | `~/.config/retroarch/config/<Core>/<rom stem>.cfg` (per-game override, merged) | `controls.py overrides` |
| START+HP = next overlay page + pause | `retroarch.cfg` `input_overlay_next_btn=5`, `input_pause_toggle_btn=5` | Ansible `cabinet_tweaks` |

ES-DE can only show a per-game picture through game metadata, so the card rides on the **controller badge**.
The theme's `controlsCard` element maps each controller type to our card with `customControllerIcon`.
**In-game overlays are OFF** (`OVERLAYS_ON = False`): RetroArch 1.20 deadlocks under Wayland with an overlay loaded
(RA #18148). Another session moved every RetroArch system to a patched RetroArch 1.22.2
(`/data/emulators/retroarch-1.22/retroarch`, main `retroarch.cfg`). Overlays come back on only after the maintainer's play test.
Each overlay has two pages: page 0 is blank, page 1 is the card. Both hotkeys sit on the same button, so
each press flips the page and the pause together.

Systems with no layout get no card and no overlay. FBNeo games (fbneo dir) use Batocera's default (the maintainer 2026-11-15, tested): no remap,
RetroPad device, which FBNeo turns into its "Classic" pad (Batocera 43.1 `libretroConfig.py` L241-243; FBNeo
`retro_input.cpp` L2980-2990). The card shows that numbering: Fire 1 = LK, 2 = MK, 3 = LP, 4 = MP, 5 = HK, 6 = HP.
Games whose P1 inputs are Weak/Medium/Strong Punch and Kick get FBNeo's six-button fighter layout by themselves (LP MP HP /
LK MK HK, no remap); in the fbneo dir that is `sf` and `gblchmp`, which get the ARCADE 6-BUTTON card through
`GAME_LAYOUT`. cps1/2/3 and neogeo run the same core, so **never add a core-wide `FinalBurn Neo.rmp`**: it would change
their tested layouts (neogeo has its own content-dir remap). Mortal Kombat 1/2/UMK3 have their own FBNeo layout
(top HP HK Block, bottom LP LK Block 2/Run) and still show the numbered card (follow-up: an MK card needs a theme
controller type). Test: `python3 skills/controls/tests/test_fbneo_layout.py`. The twin-stick games are mapped per game.

## Twin stick (#59)

One player, two sticks: the P1 stick moves, the P2 stick fires. `controls.py` `TWIN_STICK` lists the games
(card `twinstick`); each one has a game remap `remaps/<core dir>/<rom stem>.rmp` with
`input_remap_port_p2 = "0"` (user 2 feeds core port 1) and the P2 d-pad on the game's fire inputs:

| Game | Remap | P2 d-pad goes to | Status |
|---|---|---|---|
| Robotron: 2084 (fbneo `robotron`) | `FinalBurn Neo/robotron.rmp` | P1 right analog (FBNeo special case) | first test, card tested |
| Black Widow (fbneo `bwidow`) | `FinalBurn Neo/bwidow.rmp` | P1 right analog (same FBNeo special case) | not tested |
| Smash T.V. (fbneo `smashtv`) | `FinalBurn Neo/smashtv.rmp` | P1 right analog (FBNeo generic twin-stick rule, "Fire Up") | not tested |
| Total Carnage (fbneo `totcarn`) | `FinalBurn Neo/totcarn.rmp` | P1 right analog (generic rule, "Right Stick Up") | not tested |
| Robotron 64 (n64) | `Mupen64Plus-Next/Robotron 64.rmp` | right analog = C buttons; P1 buttons repeat the N64 core remap | not tested |
| Super Smash T.V. (snes) | `Snes9x/Super Smash T.V. (USA).rmp` | X up, B down, Y left, A right | not tested |

RetroArch ids: 20 R-X+, 21 R-X-, 22 R-Y+, 23 R-Y-. While a twin-stick remap is in place the game is one player
only. Not mapped yet: NES Smash T.V. (test whether it fires from controller 2 by itself, no remap) and NARC
(one stick + buttons, not a twin stick). Adding a game: add it to `TWIN_STICK`, add its remap, list the remap
in `cabinet_remaps`; `test_twin_stick_games_have_their_remap` fails if one of the two is missing.
Dreamcast gets the card only, because standalone flycast has no RetroArch overlay. Untested layouts show a grey **NOT TESTED** tag. Once the maintainer confirms a layout in game, set
`"tested": True` and re-run steps 2 to 6.

## N64 per-game labels (#57, #64)

N64 games are different games with different buttons, so the in-game overlay names what each panel button DOES
in that game ("Jump", "Ground pound") and prints the N64 pad button under it (B, C-UP, R / A, Z, C-DN). The data
is `skills/controls/n64_games.tsv`: one row per ROM stem (the file name without
`.zip`, so `Super Smash Bros.` keeps its dot), columns `stem layout Jab Strong Fierce Short Forward Roundhouse
confidence note`. Panel order Jab Strong Fierce / Short Forward Roundhouse is LP MP HP / LK MK HK. An empty label
draws an empty ring (unused button). Labels are action words, at most 14 characters.

- `layout n64` = the default remap works; the pad button is printed under each action.
- `layout n64-remap` = the game waits on its own remap (#64); the panel position (LP..HK) is printed instead,
  because the pad buttons are not decided yet.
- Per-game N64 remaps (#64, not tested), in `remaps/Mupen64Plus-Next/<rom stem>.rmp` (a game remap replaces the
  core remap for that game, so it repeats what it keeps):
  - **Killer Instinct Gold**: six attacks on the panel (QP MP FP / QK MK FK). Needs the game options file
    `core-options/Mupen64Plus-Next/Killer Instinct Gold.opt` (`mupen64plus-alt-map = "True"`, "Independent
    C-button Controls"; a whole copy of the core .opt, because a game .opt replaces it) deployed to
    `~/.config/retroarch/config/Mupen64Plus-Next/`. The manual does not say which C arrow is MP/MK/FP/FK; the
    remap assumes C-Left MP, C-Up FP, C-Down MK, C-Right FK. Check on KI Gold's Options > Button Setup.
  - **Worms Armageddon**: removed from the cabinet 2026-11-15 (the maintainer: "too complicated"); the NAS keeps it.
  - **Micro Machines 64 Turbo**: solo only (the maintainer: no 4-player), default panel.
  - **WWF No Mercy** (the maintainer 2026-11-15, not tested): `remaps/Mupen64Plus-Next/WWF No Mercy.rmp` = Strike (B),
    Block/Counter (R), Turn/Weapon (C-Left) / Grapple (A), Reverse/Pin (L), Run (C-Down); SELECT = C-Up (climb
    through ropes). Roles from the game's controls list (FAQ by isv666, video-games-museum). The game moves with
    the d-pad and does taunts + finishers with the analog stick, so the stick must be both: the panel has no hat
    (axes only), the autoconfig puts the axes on the RetroPad d-pad, and the Ansible `cabinet_tweaks`
    `cabinet_game_overrides` merge `input_playerN_l_[xy]_(minus|plus)_axis` into the game override
    `config/Mupen64Plus-Next/WWF No Mercy.cfg` (a user/override bind wins over the autoconfig per bind, so both
    fire; the overlay lines from `controls.py overrides` stay). Mupen64Plus-Next has no "d-pad as analog" option.
    Rollback: delete the four `l_*_axis` lines per player from that override and `WWF No Mercy.rmp` on the cabinet.
    Test: `python3 skills/controls/tests/test_n64_nomercy.py`.
  - Mace, F-Zero X: default panel, labels only.
- A game with no row, or a row with no labels, uses its layout's buttons. Robotron 64 is in `TWIN_STICK` (card
  `twinstick`, remap `Mupen64Plus-Next/Robotron 64.rmp`).
- Action words wrap to two lines inside the circle (also after a hyphen); all six use one text size, set by the
  longest word, down to 12 px.
- The ES-DE card stays the system card ("NINTENDO 64"): ES-DE cannot show a per-game picture. Theme and
  `_inc/controls/*.png` are not touched by this.
- `controls.py` refuses a malformed row (wrong column count, unknown layout or confidence, label over 14 chars,
  duplicate stem) with `n64_games.tsv:<line>: ...`. Run `controls.py render` only after the tests pass.
- Overlays stay OFF (`OVERLAYS_ON = False`); the files are rendered and the override is written with
  `input_overlay_enable = "false"` until the maintainer's play test on RetroArch 1.22.2 (on the cabinet since #62). Core dir: `Mupen64Plus-Next`.
- Preview: `... controls.py render ... --only n64`, then open a few `overlays/n64/*.png`.

Titles: the info panel title is a fixed 2-line box, and the few that still overflow scroll. The overlay
title keeps one size and only splits at the first `: `, ` - ` or ` / ` into a title and a smaller subtitle
when the whole name does not fit on one line.

## Steps
Steps 1-3 as make targets: `make -C skills/controls gamelists|plan|render W=<dir>`; tests: `make -C skills/controls test`.


Cabinet: `CAB="ssh -i ~/.ssh/id_ed25519 coindojo@<cabinet host>"`. `W` = a new empty scratch dir on the laptop.

1. **Copy the gamelists to the laptop** (read-only):
   `mkdir -p $W/gl && $CAB 'cd ~/ES-DE/gamelists && tar cf - */gamelist.xml' | tar xf - -C $W/gl`
2. **Plan**: `python3 skills/controls/controls.py plan --gamelists $W/gl`
   Gate: every system has the layout you expect, and the "games with a card" count is plausible.
3. **Render** (needs Pillow: `~/.venvs/badges/bin/python`):
   `~/.venvs/badges/bin/python -I skills/controls/controls.py render --gamelists $W/gl --fonts themes/coin-dojo/_inc/fonts --out $W/out`
   If the cards changed, copy `$W/out/cards/*.png` to `themes/coin-dojo/_inc/controls/` and commit.
   Gate: open a few `$W/out/overlays/<system>/*.png` and check the long titles.
4. **Copy the overlays and the theme to the cabinet**:
   `$CAB 'mkdir -p /data/coin-dojo/controls' && tar cf - -C $W/out overlays | $CAB 'tar xf - -C /data/coin-dojo/controls'`.
   Deploy the theme with `make deploy-theme` (dry run, then `APPLY=1`): it copies only the named theme files and
   lays `personal/themes/` over them. Never copy the theme folder by hand (`AGENTS.md`, "Deploying the theme").
   Also copy `controls.py` and `$W/out/overrides` to `/data/coin-dojo/controls/`.
5. **On the cabinet, with no game running** (`pgrep -x retroarch || pgrep -x flycast` prints nothing):
   - Back up first: `B=/data/coin-dojo/backups/controls-57/$(date +%Y%m%d-%H%M%S); mkdir -p $B && cp -a ~/ES-DE/gamelists ~/.config/retroarch/config ~/.config/retroarch/retroarch.cfg $B/`
   - Dry runs: `python3 /data/coin-dojo/controls/controls.py gamelists --gamelists ~/ES-DE/gamelists` and
     `python3 /data/coin-dojo/controls/controls.py overrides --src /data/coin-dojo/controls/overrides --dest ~/.config/retroarch/config`
     ("merge into existing override" lines name overrides that already had settings; those lines are kept).
   - Same two commands with `--apply`.
   - Hotkeys: run the Ansible `cabinet_tweaks` role, or set `input_overlay_next_btn = "5"` and
     `input_pause_toggle_btn = "5"` in `retroarch.cfg`.
   - Re-run nested so the virtual lists copy `<controller>`: `make deploy-collections` (dry run, then `APPLY=1`).
   - Restart ES-DE: `make esde-restart APPLY=1`.
6. **Verify** (the maintainer at the cabinet; the card in the info panel works today, the in-game part only once overlays are
   switched back on after his play test on RetroArch 1.22.2): highlight a Genesis game and check the card shows in the info panel.
   Launch it, press START + HP (the card shows and the game pauses), then START + HP again (the card
   goes and the game resumes).

Undo: restore the gamelists, `config/` and `retroarch.cfg` from `$B`, then `make esde-restart APPLY=1`.

## Check without the cabinet

- **ES-DE card**: `skills/local-preview/preview-esde.sh themes/coin-dojo <out> --view gamelist --fixture <copy of skills/local-preview/fixtures/esde-preview>`
  after `controls.py gamelists --apply` on the fixture copy. The fixture copy needs a `media/<sys>/screenshots/<stem>.png`
  per game, or the theme uses its narrow "no media" layout and hides the card.
- **In-game overlay + pause**: `skills/controls/preview/overlay-preview.sh <core dir> <rom> <overlays dir> <override.cfg> <out>`.
  It runs Debian 13's RetroArch 1.20.0, not the cabinet's 1.22.2 (that one: `skills/local-preview/SKILL.md`
  Mode 3), and you supply the core and ROM yourself (copy them from the
  cabinet into `~/.cache`). It copies the cabinet's 1920x1080 panel and 4:3 viewport and puts both hotkeys on the `o` key.
  PASS = the card shows, the game is frozen while it is up, and the game moves again after the second press.
  Proven 2026-11-14 with Eternal Champions. What still needs the cabinet: the real pad combo START + HP.

Tests: `python3 skills/controls/tests/test_controls.py` (including the broken case: an unsafe ROM stem is refused).

Genesis layout confirmations (the maintainer): Eternal Champions, and Streets of Rage 2 (LK special, MK punch, HK jump = A B C).
In-game hotkeys shown in the overlay footer: START+HP close, START+MP RetroArch menu, START+SELECT exit
(`input_enable_hotkey_btn = 9` Start, `input_exit_emulator_btn = 8` Select, `input_menu_toggle_btn = 0` MP,
`input_overlay_next_btn = 5` and `input_pause_toggle_btn = 5` HP).

Dreamcast/Naomi (standalone flycast 2.7, #88): START+SELECT exits too, on a tap, also from flycast's own menu.
BACK (Select) alone opens flycast's settings menu (`EMU_BTN_MENU`), nothing is bound to Exit. The exit is the
watcher `ansible/roles/dreamcast/files/flycast-exit-combo.py` (reads the raw js buttons 8+9, SIGTERMs
flycast), started by `flycast-run.sh`. flycast 2.7 could do it natively (`[combo] bind0 = 8,9:btn_escape:0`), but
the watcher stays. Test: `python3 ansible/roles/dreamcast/tests/test_exit_combo.py`.

Fightcade mode (stick-as-mouse `ansible/roles/power/files/stickmouse.py`, #211, #231): stick = pointer, LK left
click, MK right click, SELECT+MK pointer off/on by hand, SELECT+LK the lobby's on-screen keyboard (#227: the stick, LK, MK, START and SELECT then reach the page as dedicated F13-F24 keys; START on release closes it and sends a real Enter, #262). The pointer switches off on its own while a game (FBNeo,
found in /proc by its cmdline in the `coin-dojo-fightcade.scope`) runs and comes back when it ends. SELECT+START held
2 s: in a game it closes only the game and the lobby stays (`wine taskkill /im fcadefbneo.exe` without `/f`, SIGTERM
after 6 s, SIGKILL 2 s later, only to the game's process tree, never `wineserver -k`); in the lobby it leaves
Fightcade mode for the boot menu. Test: `python3 ansible/roles/power/tests/test_stickmouse.py`.

**Dreamcast pad mapping** (the maintainer 2026-11-15, a CPS3 fighter port: "LP is MP, MP is LP"). Without a file flycast uses
its built-in SDL default (`sdl_gamepad.cpp` `DefaultInputMapping`), which puts panel LP (SDL y, js 4) on DC Y and MP
(SDL x, js 0) on DC X; Six-button DC fighters want LP = X, MP = Y. `skills/controls/flycast/SDL_USB gamepad.cfg` is
that default, bind for bind, with only js 0 and js 4 swapped (top LP MP HP = X Y LT, bottom LK MK HK = A B RT;
BACK menu, START, stick, dead zone kept; no `[combo]`). Both pads are named `USB gamepad`, so one file covers P1
and P2. `SDL_USB gamepad_arcade.cfg` is flycast's unchanged arcade default: without it, Naomi/Atomiswave fall back
to a copy of the Dreamcast file (`gamepad_device.cpp` `find_mapping`). Deployed by the Ansible `dreamcast` role to
`~/.config/flycast/mappings/` (only while flycast is stopped and only if `~/.bash_profile` still has the SDL
config the files were written for: they use raw js codes). flycast writes these files only when someone changes
controls in its own menu, never on start or exit; a role re-run puts ours back (`backup: true` keeps theirs).
Rollback: delete both files (built-in default again). Test: `python3 skills/controls/tests/test_flycast_mapping.py`.

**Dreamcast stick: analog or d-pad, per game** (the maintainer 2026-11-15: Ikaruga's ship does not move). The panel stick
reports only two axes (no hat: `/sys/class/input/event1/device/capabilities/abs` = 3), so with the base file it is the
DC **analog** stick in every game. A disc whose IP.BIN peripheral field has no analog-stick bit (21/22; Marcus
Comstedt's ip0000.bin page: group B = minimum controller) must work with the digital DC Arcade Stick, i.e. it reads
the **d-pad**; Ikaruga reads only the d-pad. flycast binds one axis direction to one DC key, so the stick cannot be
both: `skills/controls/flycast/dc_games.tsv` decides per game (`dpad` / `analog`), and `dcpad.py render` writes
`SDL_USB gamepad_<game ID>.cfg` (= base with axes 0/1 on `btn_dpad1_*`; LP/MP swap, menu, START unchanged), which
flycast loads before the base file for that game ID (IP.BIN product number, shown in `/tmp/flycast-last.log` as
`Game ID is [...]`). Adding a game: `dcpad.py ipbin <copy of the .chd>` (laptop, needs `chdman` from `rom-tools`;
copy the CHD off the cabinet first), add a row, `dcpad.py render ... > SDL_USB gamepad_<ID>.cfg`, list the file in the
dreamcast role's `dreamcast_mapping_files`. Discs that ask for an analog stick (some fighters set the bits even for
fighters: SF III, Mars Matrix, Cannon Spike; Psyvariar 2) stay on the base file. Rollback for one game: delete its
`SDL_USB gamepad_<ID>.cfg` on the cabinet and its row/list entry. Test: `python3 skills/controls/tests/test_dc_stick.py`.
