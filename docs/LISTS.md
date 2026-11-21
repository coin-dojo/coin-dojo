# Make your own game lists

The cabinet builds MUST PLAY, genres, decades and series by itself from your scraped games: **SETTINGS**,
**GAME LISTS**, **REBUILD GAME LISTS**. This optional page adds your own folders to the main screen, for example
"My Shooters".

## The list file

Your folders live in one small text file, `series.tsv`. Each line makes one folder:

```
# folder-name	Title on screen	Game titles	Systems (optional)
my-shooters	My Shooters	(?i)gradius|r-type|thunder force
bomberman	Bomberman	(?i)bomberman
tetris	Tetris	(?i)tetris
mega-man	Mega Man	(?i)^(mega man|rockman)	nes,snes
```

The parts of a line are separated by a **Tab** (the Tab key), not spaces:

1. **folder-name**: small letters, digits and dashes only, a different one on each line.
2. **Title on screen**: what the main screen shows.
3. **Game titles**: words a game's title must contain, as the menu shows the title. `|` means "or". `(?i)` at the
   start ignores capitals. `^` means "the title starts with".
4. **Systems** (optional): folder names from the [start guide](START.md) step 5, with commas. Leave it out for
   every system.

A line starting with `#` is a note. A folder shows only when 3 or more of your games match.

## Let an AI assistant write it

Copy this into any AI chat and add your game titles at the end:

```
Make me a series.tsv file for my arcade cabinet. Rules:
- One line per folder. Fields separated by a single TAB character, never spaces.
- Fields: folder-name, title on screen, a Python regular expression, and optionally
  a comma list of systems (nes, snes, n64, genesis, segacd, saturn, dreamcast, psx,
  pcengine, atari2600, arcade, fbneo, cps1, cps2, cps3, neogeo, naomi).
- folder-name: lowercase letters, digits and single dashes, unique.
- The regular expression is searched in the game's full title (not the file name).
  Start it with (?i) so capitals don't matter.
- Only add a folder when at least 3 of my games fit it.
- Put the whole file in one code block, nothing else in it.
I want folders for: <say what you want, e.g. "each game series", "shooters", "two-player games">
My games:
<paste your game titles here>
```

To get your titles: open a system's folder in the network folder, select all files, copy and paste them into the
chat. Arcade zips have short names (like `mslug`): type those games' full titles instead.

## Put it on the cabinet

This step is for advanced users: the network folder cannot reach the list file yet. Copy `series.tsv` to
`/data/coin-dojo/series.tsv` on the cabinet with SSH (a way to type commands on the cabinet from another computer).
Not sure how? Ask whoever built your image. Your file replaces the example list.

Then on the cabinet: **SETTINGS**, **GAME LISTS**, **REBUILD GAME LISTS**, and say yes to the reload.

A broken line is safe: the rebuild stops, says what is wrong, and changes nothing. Fix the line and rebuild.

Full format (for builders): `series.tsv` in [skills/collections/SKILL.md](../skills/collections/SKILL.md).
