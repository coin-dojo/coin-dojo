# coin-dojo style kit (brand)

Every screen, card, badge and app on the cabinet follows this kit. It is **not a new look**: it is the
Coin Dojo theme (`themes/coin-dojo`) written down as tokens, so agents and people
make new UI that matches what already ships (the maintainer, 2026-11-15: "use our style kit, we should commit a
stylekit/brand for agents to follow in future").

| File | What |
|---|---|
| `tokens.json` | the values: colours with roles, fonts, type scale, spacing, radii, borders, badge pill, card, help-bar hierarchy, contrast pairs, 4:3 safe area |
| `brand.py` | stdlib loader (`brand.load()`), contrast math, `brand.py check` |
| `specimen.py` -> `specimen.png` | one rendered sheet of the tokens; re-render after a token change and look at it |
| `specimen.png` | the same sheet titled COIN DOJO (`specimen.py --name "Coin Dojo"`); the community export ships it as `specimen.png` (#235, `tests/test_community_images.py`) |
| `tests/test_brand.py` | tokens complete, contrast pairs pass, theme `colors.xml` and the generators agree, broken cases |
| `gen_logo.py` -> `logo.png`, `logo-light.png`, `logo-icon-{512,64,32}.png` | the Coin Dojo logo (#258): lockup for dark and light pages, the token icon; rules in [Logo](#logo); `tests/test_logo.py` |

Owner: Design (`brand/**`).

## Rules

1. **Use tokens, never literals.** New code takes colours and fonts from `tokens.json` (via `brand.py`).
   No hex colour and no font file name typed into a new UI file. `skills/settings` is checked for it
   (`skills/settings/tests/test_settings.py`).
2. **4:3 safe area.** The panel is 1920x1080, the bezel shows a centred **1440x1080**. Everything a player
   must read sits inside it. A full-screen app draws its own 1440x1080 frame centred on the output.
3. **Gold leads, red follows.** `gold` = the one accent (selection, labels, focus). `crimson`/`red_light`/
   `salmon` = secondary and errors. Cream `text` for primary text, lavender `muted` for secondary.
   `cyan` only for the GENRE chip.
4. **Contrast >= 4.5:1** for every text colour on the fill it sits on (`contrast.pairs`; the same threshold as
   `themes/tests/test_palette_contrast.py`). A new text/fill pair goes into `contrast.pairs` first.
5. **Fonts.** `display` (Archivo Black) for titles and round button badges; `ui_bold` (Rajdhani Bold) for
   pill badges, tags and values; `ui_semi` for body text and list rows; `pixel` (Press Start 2P) only for
   small retro accents (clock, counters). Sizes from `type_scale` (px on a 1080-tall screen).
6. **Two rows of hints, two weights.** The primary help bar (24 px, full `gold` badges, `text` words) says what
   the buttons do *here*. The secondary hotkey strip (32 px tall, `gold_muted` badges, `muted` words) is
   global info (START+SELECT exits). The secondary row is always smaller and quieter than the primary.
7. **Badges.** Panel buttons are drawn as the gen_help badge: gold, label knocked out (or `bg`-coloured).
   Round for LP MP HP LK MK HK, pill for START, SELECT and combos (`badge_pill`).
8. **Cards.** `card` fill, `radius.card` 24, `border.card` 5; gold border = tested, `pending` border plus a
   NOT TESTED tag = not verified on the real panel yet (`card`).

## Button names on panel UI

- Use the **panel names**: `LP MP HP` (punches), `LK MK HK` (kicks), `START`, `SELECT`, `STICK`.
- **Never** console letters or shapes on panel UI (no A/B/X/Y, no Cross/Circle). The mapping from SDL's
  names is in `skills/badges/gen_help.py` (a=LK b=MK x=MP y=LP l=HP r=HK back=SELECT start=START).
- Combos with `+`: `START+SELECT`, `HP+HK`.

Typical roles in an app: `LK` = select / OK, `MK` = back / delete, `START` = done, `START+SELECT` = exit.

## On-screen text

- Short. Labels and buttons in CAPS (`WI-FI`, `CABINET NAME`, `CONNECT`); sentences in plain case only for
  help and errors.
- Say what happened and what to do next: `WRONG PASSWORD - TRY AGAIN`, not codes.
- Show tool errors as they are (e.g. nmcli's message), one line, `red_light`.
- No marketing words, no emoji.

## Do / don't

| Do | Don't |
|---|---|
| `b.rgb("gold")`, `b.font("ui_bold")` | `(255, 201, 77)`, `"Rajdhani-Bold.ttf"` in new code |
| keep UI inside 1440x1080 | put a button hint in the 240 px side bars |
| gold for the focused item, cream for the rest | two accents fighting (gold and red both as focus) |
| LK / MK / START badges | "Press A", "Press X" |
| add a pair to `contrast.pairs` before using it | muted text on `panel` without checking |
| re-render `specimen.png` and look at it | change a token without looking |

## Logo

The Coin Dojo logo (#258, the maintainer's pick 2026-11-20) is an arcade token: a reeded gold rim, GOOD FOR ONE PLAY around
the top, a torii gate over a low red sun on violet. Beside it sits the COIN DOJO wordmark (Archivo Black, slanted,
the `badge` gradient, `badge_outline` stroke) and the tagline FOR ICONIC ARCADE CABINETS (RASPBERRY PI 5). It is
drawn from the tokens by `gen_logo.py` (Pillow venv: `make -C brand logo`); never edit the PNGs by hand.

| File | Use |
|---|---|
| `logo.png` | the lockup on dark pages (tagline in `muted`); transparent background |
| `logo-light.png` | the lockup on light pages (tagline in `dim`) |
| `logo-icon-512.png`, `logo-icon-64.png` | the token alone: app icons, the manual cover and back page |
| `logo-icon-32.png` | favicon and tab icon: the token without the ring text, a heavier gate |

- **Minimum size.** The ring text needs 64 px: below that use `logo-icon-32.png`, never a scaled-down 512.
  The lockup needs 320 px of width for the tagline to read.
- **Clear space.** Keep a margin of a quarter of the token's diameter on every side, free of text and edges.
- **Colours.** Use the files as they are, on `bg`/`bg_deep`/`panel` or on cream (`text`). Don't recolour the
  token: no gold-on-black restyle, no metallic shine, no currency or crypto look (no value, no $ or other sign,
  no "token" wording). It is an arcade token, good for one play.
- **Don't** stretch it, add a drop shadow or outline, put it over busy art without a `card` panel behind it, or
  set the wordmark in another font.
- The name itself is text: in prose write Coin Dojo, in UI COIN DOJO.
- **Pick it in code with `brand.logo_file(width, light=False)` / `brand.logo_image(width)`**: the one place a page,
  banner or sheet gets the logo (lockup at >= 320 px, the token at 64-319, the favicon below 64). Today: the community
  specimen (token, top right) and the theme's community logo (`logo-wordmark.png`, a byte copy of `logo.png`,
  `tests/test_community_images.py`). A new logo = re-run `gen_logo.py` (and copy `logo.png` over the theme's copy).

## Use from Python

```python
import os, sys
sys.path.insert(0, "<repo>/brand")       # or the folder the role installed it to
import brand
b = brand.load()                         # brand.load(path, font_dir=...) ; $HK_BRAND_TOKENS / $HK_BRAND_FONT_DIR
gold = b.rgb("gold")                     # (255, 201, 77)
fill = b.rgba("navy", b["alpha"]["chip_fill"])
title_font = b.font("display")           # absolute path to the TTF
px = b["type_scale"]["heading"]
```

`python3 brand/brand.py check` prints every contrast pair and `RESULT: PASS|FAIL`.
Re-render the sheet: `~/.venvs/badges/bin/python brand/specimen.py` (Pillow venv: `skills/badges/SKILL.md`).

## Who reads the tokens today

- `skills/badges/gen_help.py`: gold/lavender, hotkey-strip colours, the badge pill spec, both font files.
- `skills/badges/gen_chips.py`: chip labels + colours, navy, pictogram gradient, chip alpha, font.
- `skills/controls/controls.py` (render): card/panel/button/gold/chrome/muted/dim/pending/well colours, fonts.
- `skills/settings/settings_app.py`: everything (it has no colour or font of its own).

Their PNG output stayed **byte-identical** when they moved to tokens (`tests/test_brand.py` regenerates and
compares). Not moved yet (follow-ups): `badge.py` (badge gradient/outline; tokens exist), `gen_scrim.py`,
`gen_deck.py`, `gen_panels.py`, `gen_frames.py`, the stick shaft grey in `controls.py`, and the power
overlay's own palette (`ansible/roles/power/files/overlay.py`, an older indigo/cyan set).
