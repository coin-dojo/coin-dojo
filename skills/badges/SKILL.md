---
name: badges
description: Make a chrome carousel badge (italic gold->teal text with navy outline) for a new collection and drop it into the Coin Dojo theme.
---

# Make a carousel badge for a new collection

Badges are transparent RGBA PNGs, exactly 256 px tall, one per carousel entry in
`themes/coin-dojo/_inc/systems/<name>.png`. Original art (CC BY-SA 4.0); the font is
Archivo Black (OFL, bundled in the theme). Same text + same font = identical bytes.

Run everything from the repo root.

Make: `make -C skills/badges help` (`venv`, `badge OUT= TEXT=`, `chips`, `help-icons`, `scrim`, `deck`, `panels`,
`frames`, `art` = all generators + the theme check; they use the venv python when it exists). Tests: `make -C skills/badges test`.

1. One-time: create a venv with Pillow (the only dependency; never pip-install into system Python).
   ```
   python3 -m venv ~/.venvs/badges && ~/.venvs/badges/bin/pip install pillow
   ```
2. Generate the badge. The file name must equal the collection/system theme name ES-DE looks up
   (e.g. collection `must-play` -> `must-play.png`); the TEXT is what is drawn.
   ```
   ~/.venvs/badges/bin/python skills/badges/badge.py themes/coin-dojo/_inc/systems/must-play.png "MUST PLAY"
   ```
   Several at once: `badge.py OUT1 TEXT1 OUT2 TEXT2 ...`. Or `skills/badges/badge.sh OUT TEXT`
   (uses `$BADGE_VENV`, default `~/.venvs/badges`). Another font: `--font /path/to/font.ttf`.
3. Verify (expect `wrote ... NNNNx256`, then `RESULT: PASS`):
   ```
   ~/.venvs/badges/bin/python skills/badges/tests/test_badge.py
   python3 themes/tests/test_theme_xml.py
   ```
4. Look at it on a dark background before committing (height 256, text readable, nothing clipped).
5. Commit the PNG with the theme. On the cabinet, copy it to `~/ES-DE/themes/coin-dojo/_inc/systems/`
   and restart ES-DE (Coordinator/the maintainer only; the cabinet is not touched from this skill).

**ES-DE auto collection names.** The three automatic collections are looked up by theme name `auto-allgames`,
`auto-favorites`, `auto-lastplayed` (badge `_inc/systems/auto-*.png` + chip). Their `es_settings.xml` keys
(`CollectionSystemsAuto`, comma separated) are `all`, `favorites`, `recent`: different names, same three. Custom
collections use their own name; the group is `custom-collections`. `test_theme_xml.py` fails on `favorites.png`,
`all.png`, `lastplayed.png` and the like (never loaded by ES-DE). Favorites are off on the cabinet (the maintainer,
2026-11-14), so the theme ships no `auto-favorites` badge or chip; `test_theme_xml.py` fails if one comes back.

Exit codes: `2` = bad arguments, missing font or missing Pillow; `1` = render failure.

Other generators here: `make_backdrop.py OUT.png` (neutral 1440x1080 default backdrop) and
`gen_frames.py [OUTDIR]` (cyan/amber gamelist panel frames).

## Category chips + genre pictograms (system view)
`gen_chips.py [THEME_DIR]` writes, for every badge in `_inc/systems/`, a CURATED / DECADE / GENRE / SYSTEM
chip (`_inc/chips/<name>.png`) and, for genre collections, an original geometric pictogram
(`_inc/icons/<name>.png`); ES-DE themes have no per-item variables, so the theme picks them by `${system.theme}`.
Run it after adding a badge (extend `MEMBERS` in the script for a new decade/genre/curated name; unknown names
get the SYSTEM chip). Test: `~/.venvs/badges/bin/python skills/badges/tests/test_gen_chips.py`.

QUIT TO MAIN MENU (#218) has its own `exit` category (EXIT chip, salmon) and an exit pictogram (`icons/quit.png`); its
`_inc/per-system/quit.xml` swaps the "1 GAME" pill for BACK TO THE BOOT MENU. Paths in a per-system file resolve from
`_inc/per-system/`, so write `./../fonts/...`, never `${uiBold}` (`themes/tests/test_theme_xml.py` checks it).

## Boot menu card art (#218, personal)
`gen_bootmenu_art.py [SRC.png] [OUT_DIR]` crops your backdrop into the boot menu's two card illustrations (`esde.png`,
`fightcade.png`, exactly 618x318 = `bootmenu_app.ILL_W/ILL_H`). Defaults: the maintainer's `personal/.../backdrops/default.png` ->
`personal/themes/coin-dojo/_inc/art/bootmenu/` (deployed by the theme's personal overlay). The community edition
ships none; the app draws its own. Test: `skills/badges/tests/test_gen_bootmenu_art.py`.

## Palette art: scrim, deck, panels, frames (#43)
Art-led palette (gold lead, crimson red, cream text, lavender muted, violet-black base). Generated, original art:
- `gen_scrim.py [ART_DIR]` -> `_inc/art/scrim.png` (system view: clear top, ~38% behind the carousel, ~85% footer) and
  `scrim-list.png` (gamelist veil). Both views draw the per-system backdrop (`backdrops/<system>.png`, else `default.png`) under them.
- `gen_deck.py [OUT]` -> translucent footer `deck.png`; `gen_panels.py [ART_DIR]` -> `panel.png` / `panel-red.png`;
  `gen_frames.py DIR` -> lavender/gold panel frames. Tests: `skills/badges/tests/test_gen_art.py`,
  `themes/tests/test_palette_contrast.py` (every list/metadata/help colour >= 4.5:1 worst case, both colour schemes).
- Nested main screen (#38): `genres`, `decades`, `systems` badges + a BROWSE chip (`MEMBERS["browse"]` in gen_chips.py).
- Preview over your own art: copy the theme under `~/.cache/coin-dojo/preview/`, drop your art in as `backdrops/default.png`
  there (never in the repo; backdrops/ is git-ignored), run `preview-esde.sh` on the copy.
- ES-DE's own menus are not themeable; set `MenuColorScheme` = `darkred` in es_settings.xml (UI settings > Menu color scheme: "Dark and red").

## Help-bar panel icons (#57)
`gen_help.py [THEME_DIR]` writes `_inc/help/<button>.png` (white badges, label knocked out; the helpsystem's
`iconColor` tints them gold): LP/MP/HP/LK round, START/SELECT pills, MK/HK, a stick for the d-pad. The mapping
(a=LK b=MK x=MP y=LP l=HP r=HK lr=HP+HK back=SELECT start=START, XBOX and XBOX360 alike) follows **`SDL_GAMECONTROLLERCONFIG` in the cabinet's `~/.bash_profile`** (a:b3 b:b2 x:b0 y:b4 leftshoulder:b5 rightshoulder:b1; panel by joystick index MP=0 HK=1 MK=2 LK=3 LP=4 HP=5). ES-DE does **not** read `~/ES-DE/settings/es_controllers.txt`; the test asserts this table. `hotkeys.png` (same script) is the START+SELECT / +MP / +HP hint strip shown bottom right in game lists (no note line under it since 2026-11-15). One file per
button (ES-DE shows a file once). Wired through `customButtonIcon` in theme.xml; test: `themes/tests/test_theme_xml.py`.

## Community backdrop (Coin Dojo, #235; #222 before)

- `gen_backdrop.py OUT.png`: the community edition's default carousel backdrop, original brand art only (tokens from
  `brand/tokens.json`): a dojo at night, moonlit shoji screens with a bamboo shadow, a round window with the moon behind
  bamboo bars (cool, left), a paper lantern (warm, right), a polished plank floor in perspective with both reflected, dust
  motes and CRT scanlines baked in the overlay's phase (`y % 3 == 0`). No characters, logos, text or game art. The carousel
  band (y 0.40..0.66) stays calm: the test checks it is darker and at most half as busy as the art above, plus the warm
  right / cool left split and the scanline phase, with a broken case for each. It replaced #222's aura clash, which
  replaced the CONFIGURE ME card (#130). The committed
  `themes/coin-dojo/_inc/art/backdrops/default.png` is its output (`tests/test_gen_backdrop.py` checks the bytes);
  `themes/tests/test_community_backdrop.py` (stdlib) checks it is a 1440x1080 PNG, not the old card and no copy of a
  `personal/` file. The `esde_theme` role deploys it first and any personal overlay or `hk_theme_art_dir/backdrops/`
  replaces it. Rerun the script after a colour token change.

## The eye glow (#105, #263)

- `gen_eye_glow.py [BACKDROPS_DIR]` (default `<hk_theme_art_dir>/backdrops`, else the theme's `_inc/art/backdrops`) reads the night art `default.png`
  there and writes `eye-glow.{png,json,xml}` beside it: a soft red glow over the eye socket (mask = the art's own saturated
  reds, colours crimson -> red_light -> salmon from `brand/tokens.json`), a Lottie with the PNG embedded and an eased opacity ramp
  (24 fps, 10 s: 4 s up, 1 s hold, 4 s down, 1 s rest), and the ES-DE include with the `animation` element (system + gamelist,
  zIndex 2.5: over the backdrop, under the scrim). `theme.xml` includes it as `./_inc/art/backdrops/${eyeGlow}.xml`, so a theme
  without the file only logs a debug line (#263: the theme ships it). Box, peak and on/off are config
  (`hk_eye_glow`, read with `tools/config.py get`; `enabled: false` deletes the three files); timing stays a constant. Rerun the
  script after changing the backdrop or the config; `tests/test_gen_eye_glow.py` fails when the committed files are stale (the shipped files are this script's output on the shipped `default.png`).
- Preview: `make preview` on a staged theme dir with the overlay (under `$HOME`); the include's paths are relative to the include.
