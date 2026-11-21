# Build your own theme

The cabinet's front end is ES-DE 3.5 with the **Coin Dojo** theme (`themes/coin-dojo/`). This page is
for people who want their own look: copy the theme, rename it, change colours and art, preview it on your computer,
test it, and put it on a cabinet. You need no game art for any of it, and you must not ship any (the shipped theme carries one credited night backdrop, see
[THIRD_PARTY.md](../THIRD_PARTY.md); yours stays on your machine).

The theme's own README (`coin-dojo/README.md`) says what it looks like and what is licensed how. The style kit
behind it is `brand/BRAND.md` (rules) and `brand/tokens.json` (values).

## Folder layout

```
themes/coin-dojo/
├── capabilities.xml        # theme name shown in ES-DE, aspect ratios, colour schemes, variants
├── theme.xml               # variables (fonts, art paths) and every element of the system and gamelist views
├── colors.xml              # colour schemes: "indigo" (default) and "crimson"; ${...} tokens used by theme.xml
├── aspect-ratio-4-3.xml    # the cabinet layout (4:3) + font size tuning
├── aspect-ratio-16-9.xml   # what moves on a 16:9 monitor
├── README.md, LICENSE
└── _inc/
    ├── fonts/              # OFL fonts + their OFL-*.txt licences
    ├── art/                # original art: scrim, deck, panels, scanlines, frames/
    │   └── backdrops/      # default.png (the night art), dojo.png (original alternative), eye-glow.*, YOUR art, git-ignored
    ├── systems/            # one carousel badge per system/collection (skills/badges/badge.sh)
    ├── chips/ icons/       # category chips and genre pictograms (skills/badges/gen_chips.py)
    ├── help/               # help-bar button icons, white masks the theme tints (skills/badges/gen_help.py)
    ├── controls/           # controls cards per system (skills/controls)
    └── per-system/         # overrides for one carousel entry: settings.xml, quit.xml, trophies.xml
```

Paths in `theme.xml` start at the theme folder (`./_inc/...`); paths in a `_inc/per-system/*.xml` file start at
`_inc/per-system/` (`./../fonts/...`), and a `${variable}` does not work there.

## Copy the theme and rename it

1. `cp -R themes/coin-dojo themes/my-theme` (lowercase, digits and `-` only: `hk_theme_name` checks it).
2. In `themes/my-theme/capabilities.xml` set `<themeName>My Theme</themeName>`: that is the name ES-DE lists.
3. Change the title and credits in `README.md` and `LICENSE`. Keep the licence lines for the fonts and any art you kept.
4. Tell the deploy which theme to use: `hk_theme_name: my-theme` in `config/local.yml` (start from
   `config/local.example.yml`; reference `docs/CONFIG.md`).

## Colours and the style kit

- `colors.xml` holds every colour the theme draws, per scheme. The lead accent is gold `FFC94D` (selector, labels,
  counters, help icons); crimson `E23B3B` / `FF6B5A` are secondary, cream `FFF4DC` is text, lavender `C9C0EA` is
  muted text, violet-black `0E0822` is the base.
- `brand/tokens.json` holds the same values with their roles, plus fonts, type scale, spacing and the 4:3 safe area.
  The generators in `skills/badges/` (badges, chips, help icons, backdrop) and the SETTINGS app read it, so a token
  change followed by a rerun of the generators restyles the generated art.
- Rules (`brand/BRAND.md`): one accent (gold leads, red follows), every text colour at least **4.5:1** against what
  it sits on, panel button names (`LP MP HP LK MK HK START SELECT`, never A/B/X/Y).

## SETTINGS, QUIT and the boot menu

- `_inc/per-system/settings.xml`, `quit.xml`, `trophies.xml` change one carousel entry each: SETTINGS hides the game
  counter, QUIT shows BACK TO THE BOOT MENU instead of "1 GAME".
- **The SETTINGS gamelist must stay a plain background** (#225). When you pick SETTINGS, ES-DE draws that game list
  for one frame and keeps it on screen until the app returns; the app fades to the same flat `baseColor`. So every
  gamelist element is hidden in `settings.xml`. If you add an element to the gamelist view, hide it there too, or
  `themes/tests/test_theme_xml.py` fails.
- The SETTINGS app and the boot menu (ES-DE or Fightcade) draw themselves from `brand/tokens.json`; the boot menu
  cards show two crops of the night art (`_inc/art/bootmenu/`, `skills/badges/gen_bootmenu_art.py`), your own art when you
  have some, and draw their own when the files are missing.

## The 4:3 safe area

The panel is 1920x1080, the bezel shows the centred **1440x1080**. ES-DE runs at 1440x1080, so in
`aspect-ratio-4-3.xml` and `theme.xml` a position of `0..1` is the visible 4:3 area. Keep text and the carousel
neighbours **at least 0.03 (43 px) from each side** (#221: the carousel test checks the left and right neighbours).
`aspect-ratio-16-9.xml` only nudges things for a wide monitor; the cabinet is 4:3.

## Backdrops

`_inc/art/backdrops/default.png` (1440x1080) sits behind the carousel and, under a veil, behind the game lists. The
shipped one is the night art (with its eye glow `eye-glow.*` and the boot-menu card crops in `_inc/art/bootmenu/`, #263); `dojo.png` next to it is an original coin dojo, generated by `skills/badges/gen_backdrop.py` from the brand colours (copy it over `default.png` to use it). Your own:

- `backdrops/default.png` for everything, or `backdrops/<system>.png` per system (`cps2.png`, `neogeo.png`).
- Keep the band from 40 % to 66 % of the height calm and fairly dark: the selected badge, its chip and the counter sit
  there. Put the bright parts above it.
- Art you did not make (game art, characters, logos) stays on your machine: `backdrops/` is git-ignored except
  the files this repo lists (`default.png`, `dojo.png`, `eye-glow.*`), or point `hk_theme_art_dir` at a folder outside the repo. The deploy lays it over the theme.

## Fonts and licences

- Fonts: only fonts whose licence allows redistribution (the theme ships OFL fonts: Archivo Black, Rajdhani,
  Press Start 2P). Ship each font's licence file next to it and list it in `NOTICE`.
- Your original art: CC BY-SA 4.0 (`LICENSE-art`). Theme XML: MIT (`LICENSE`).
- **Never ship game art**: no characters, logos, marquees, box art or screenshots, not even as a placeholder. The one
  exception is the credited night art of this repo (THIRD_PARTY.md); `tests/test_ship_no_artifacts.py` guards the repo and
  lists it by sha256.

## Preview on your computer

`make preview` renders the theme in ES-DE inside a container (needs Colima/Docker) and writes screenshots
(`PREVIEW_OUT=`, `VIEW=system|gamelist|both`). Details, including how to preview over your own art without
committing it: `skills/local-preview/SKILL.md` (mode 2) and `skills/badges/SKILL.md`. Look at 4:3 first.

## Tests and the brand lint

| Command | Checks |
|---|---|
| `python3 themes/tests/test_theme_xml.py` | every theme XML under `themes/` parses, every literal asset path exists, the SETTINGS rule, carousel margins |
| `python3 themes/tests/test_palette_contrast.py` | every list/metadata/help colour in `colors.xml` >= 4.5:1, both schemes |
| `python3 brand/brand.py check [tokens.json]` | the brand lint: tokens complete, every contrast pair >= 4.5:1 |
| `make test-area A=theme,brand` | all theme and brand tests |

Most theme tests point at `themes/coin-dojo`; to check your copy the same way, point their `THEME` path at it
while you work. A broken case to try: delete a closing tag in `theme.xml` and watch `test_theme_xml.py` fail.

## Put it on a cabinet

1. The first time, copy the folder once to the cabinet's `~/ES-DE/themes/my-theme/` (the deploy updates a theme that
   is already there and stops if it is missing).
2. Dry run, then write: `make deploy TAGS=esde_theme`, then `make deploy TAGS=esde_theme APPLY=1`. It copies the XML
   and the generated `_inc` folders, then your art, checks every XML parses before it writes, backs up what it
   replaces and restarts ES-DE once.
3. On the cabinet: START (menu) > **UI settings > Theme** > your theme. Under the same menu: colour scheme, aspect
   ratio **4:3**, font size.

## Worked example: a jade accent and your own backdrop, in 5 steps

1. **Copy:** `cp -R themes/coin-dojo themes/my-theme`, set `<themeName>My Theme</themeName>` in its
   `capabilities.xml`, and `hk_theme_name: my-theme` in `config/local.yml`.
2. **Check the colour first:** copy `brand/tokens.json` to a scratch file, change `gold` to `5EE6C8`, run
   `python3 brand/brand.py check <scratch>.json`. Every line must say PASS (jade on the violet-black base is 12.7:1).
3. **Recolour:** in `themes/my-theme/colors.xml` replace `FFC94D` with `5EE6C8` (both schemes; `FFC94DFF` too, the
   `FF` is opacity). The help icons follow, the theme tints them.
4. **Backdrop:** save a 1440x1080 PNG as `themes/my-theme/_inc/art/backdrops/default.png` (your own art, or after a
   token change `python3 skills/badges/gen_backdrop.py themes/my-theme/_inc/art/backdrops/default.png`). That file
   is committed: art you did not make goes in `hk_theme_art_dir` instead. Keep the middle band calm. The badges and
   chips under `_inc/` are pre-rendered too: after a token change rerun their generators (`skills/badges/SKILL.md`).
5. **Look, test, deploy:** `make preview`, the tests above, then `make deploy TAGS=esde_theme` and `APPLY=1`, and pick
   **My Theme** under UI settings > Theme.
