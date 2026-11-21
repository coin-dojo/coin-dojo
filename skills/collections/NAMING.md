# Game naming standard (cabinet + community image)

Decided 2026-11-14 (the maintainer: "fix game names, spaces, whitespace; a standard that works for
ES-DE and the core emulators"). Two layers, never mixed:

| Layer | What it is | Who reads it | Rule |
|---|---|---|---|
| **File name** | the ROM/disc file on disk | the emulator core, scrapers, thumbnails, RetroAchievements | keep the **canonical set name**; never "prettify" |
| **Display name** | `<name>` in `ES-DE/gamelists/<system>/gamelist.xml` | the player | clean, consistent, written by `collgen.py names` |

ES-DE shows the file name (minus extension) when a game has no `<name>`, so **every game
gets a `<name>`**. Then file names can stay technical.

## 1. File names (do not rename to make them look nice)

- **Arcade (`fbneo`, `cps1`, `cps2`, `cps3`, `neogeo`, `naomi`, `atomiswave`): MAME/FBNeo short
  name, unchanged** (`sf2ce.zip`, `mslug.zip`). The core identifies the game *by this name*;
  renaming breaks it. Same for BIOS zips (`neogeo.zip`, `qsound.zip`): they live in the ROM
  folder or BIOS dir but are **not games** → hidden in the gamelist (`<hidden>true</hidden>`).
- **Cartridge consoles (`nes`, `snes`, `genesis`, `pcengine`, `n64`, `gb*`): No-Intro name**
  (`Super Metroid (Japan, USA) (En,Ja).zip`). Spaces, `(Region)`, `(Rev 1)`, `~` and `'` are
  fine: libretro thumbnails and scrapers match on these names/hashes.
- **Disc systems (`psx`, `saturn`, `dreamcast`): Redump name, one `.chd` per game**; multi-disc
  games as `Game (USA) (Disc 1).chd` … plus `Game (USA).m3u` listing them (the `.m3u` is the
  only entry shown; the discs are hidden).
- Allowed in file names: what No-Intro/Redump use. Forbidden: leading/trailing space, double
  space, tabs/newlines, `/` `\` `:` `*` `?` `"` `<` `>` `|`, non-UTF-8 bytes.

## 2. Launching (spaces must always work)

- Every launch command and script quotes the ROM path: ES-DE passes `%ROM%` already escaped;
  our own scripts (`flycast-run.sh`, the nested-menu dispatcher #38) use `"$1"` and never
  `$1`/`$*` unquoted or `eval`.
- Tests for every launcher include a path with spaces, `'`, `(`, `&` and `~` (broken case:
  unquoted variant must fail the test).

## 3. Display names (`<name>`)

Applied in order, deterministic:

1. **Source:** arcade → libretro FBNeo/MAME DAT description (alias titles `Western / Japanese` keep the first part, as `~` keeps the last); consoles → scraped name, else the
   No-Intro title.
2. **Strip tags:** remove `(…)` and `[…]` groups (region, revision, language, `(Disc 1)`,
   `[!]`) — **unless** two games in the same list would then collide; then keep the first
   distinguishing tag (`Galaga (Rev A)`), as `clean_titles()` already does.
3. **Whitespace:** trim, collapse runs of spaces to one, no space before `:` `,` `!` `?`.
4. **Subtitle separator: `Title: Subtitle`** (colon, no space before). Converts ` : `, ` - `
   and No-Intro's ` ~ ` dual titles: `Daimakaimura ~ Ghouls'n Ghosts` → `Ghouls'n Ghosts`
   (the English/western title wins, the last part after `~`).
5. **Articles:** `Legend of Zelda, The` → `The Legend of Zelda` (ES-DE sorts by `<sortname>`
   if needed; we set `<sortname>` only when the article would mis-sort).
6. **Keep** the official spelling and punctuation otherwise (`Ms. Pac-Man`, `19XX`,
   `Ghouls'n Ghosts`, roman numerals). No ALL CAPS rewrite, no title-casing.
7. Max ~40 chars shown nicely in the theme; longer names are fine (the theme scrolls them).

## 4. Where it is enforced

- `skills/collections/collgen.py names [--apply]` (plan by default): rewrites only `<name>`/`<sortname>`/
  `<hidden>` in gamelists (never `<path>`, never renames files), backs up each gamelist first.
- Test fixtures cover each rule plus broken cases (double space, ` : `, unhidden BIOS).
- Audit 2026-11-14 (cabinet): arcade file names clean; fbneo display names still carry tags
  (335, cleanup not deployed yet); cps1 uses ` : `; 25 neogeo sets have no `<name>` (show as
  `mslug`-style short names); snes has no scraped names (shows `ActRaiser (USA)`);
  4 genesis files use No-Intro `~` dual titles.

## 5. Collisions in the virtual lists (`nested`, #50)

Only in the generated MUST PLAY / GENRES / DECADES lists (never in the real per-system gamelists, never in the
`systems` folders): when two visible games in the same list (the flat must-play list, or one genre/decade folder)
have the same name (compared case-insensitively, punctuation and spaces ignored: `Out Run` == `OutRun`), the system
is appended: `Bionic Commando (Arcade)`, `Bionic Commando (NES)`. Only on collision; a unique name stays bare.
`<sortname>` stays the bare title so the pair sorts together. Labels: cps1/cps2/cps3/fbneo Arcade (if two arcade
systems collide: CPS1/CPS2/CPS3/FBNeo), neogeo Neo Geo, nes NES, snes SNES, genesis Genesis, pcengine PC Engine,
psx PS1, saturn Saturn, dreamcast Dreamcast, n64 N64.
