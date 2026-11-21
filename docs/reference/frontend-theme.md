# Front-end & theme — category-first, favorites, video-snap, attract

Goal (from [[frontend-inspiration]]): a **category-first** layout with **★ Favorites** and
**⏱ Recently Played** rows, **video-snap showcase** on hover, and an **idle attract /
screensaver**. What's realistic on Batocera and how to wire it.

## Which front-end

| Front-end | On Batocera | Fit for our goal |
|---|---|---|
| **Batocera EmulationStation** (the built-in fork) | **default, no install** | Does favorites/last-played collections, dynamic genre collections, video previews, and a video screensaver out of the box. **The pragmatic pick.** |
| **Pegasus** | selectable in Batocera | Fully custom QML themes — best for a bespoke "own style" wheel/coverflow look; more work. A later option if the built-in theme isn't bespoke enough. |
| **ES-DE** (EmulationStation Desktop Edition) | **not the Batocera front-end** | ES-DE is its own thing (RetroBat / Knulli / standalone / Android / Steam). Great reference for UX, but on Batocera you use Batocera-ES, not ES-DE. Don't conflate them. |

Decision: build the pack on **Batocera-ES** (default), keep **Pegasus** as the "bespoke
style" stretch goal. [[direction]]'s "ES-DE/Pegasus" line should be read as *ES-style
front-end*, which Batocera already ships.

## Themes (where the category-first look comes from)

- Install via **MAIN MENU → UPDATES & DOWNLOADS → THEMES** (the Content Downloader). Themes
  live in **`/userdata/themes/<theme>/`**.
- Pick a theme that supports **custom/dynamic collections as top-level rows** and **video
  views** — good references: themes in the "ES-DE-style" family and Batocera's bundled modern
  themes. A theme that renders collections as its main carousel gives the category-first feel
  (Fighting / Beat-'em-ups / Shooters rows) instead of platform-first.
- Set theme + options under **MAIN MENU → USER INTERFACE SETTINGS → THEME** and
  **THEME CONFIGURATION**.

## Favorites + Recently Played rows

Batocera-ES has **automatic collections**:

- **MAIN MENU → GAME COLLECTION SETTINGS → AUTOMATIC GAME COLLECTIONS** — enable
  **Favorites**, **Last Played**, **All Games** (and more). They then appear as their own
  selectable "systems"/rows on the home carousel.
- Mark a favorite: highlight a game → press the **Y** button (toggles `<favorite>` in that
  system's `gamelist.xml`). "Last Played" is driven automatically by the last-played date.
- These depend on metadata/gamelists existing — scrape first (below) or they'll be empty.

## Category-first collections (Fighting, Shooters…)

Use **dynamic collections** (Batocera 5.26+), stored as `.xcc` XML under
`/userdata/system/configs/emulationstation/collections/`:

- **GAME COLLECTION SETTINGS → CREATE NEW DYNAMIC COLLECTION** → filter by **genre**
  (Fighting, Beat-'em-up, Shoot-'em-up, Puzzle…), publisher, players, system, favorites,
  rating, played status. Edit later: select it → **SELECT → EDIT DYNAMIC COLLECTION FILTERS**.
- Each is a `<filter name="…">` block (e.g. `Arcade.xcc`) — hand-editable over SSH, so we can
  **ship a prebuilt set of `.xcc` files** (Fighting/Beat-'em-ups/Shooters/Puzzle) in the pack.
- Static custom collections (`.cfg`, one ROM path per line, in the same folder) still work in
  v36 but are **deprecated** and the `batocera-create-collection` helper was removed — prefer
  dynamic. Big libraries: filter loading can be slow (it parses all `gamelist.xml`).
- Keep **platform** (CPS1/2/3, NAOMI) as a *filter/alternate view*, not the main rows — matches
  the decided IA in [[frontend-inspiration]].

## Video-snap showcase (hover previews)

- Needs **video media** per game. Scrape it: **MAIN MENU → SCRAPER** (Batocera's built-in;
  ScreenScraper supports video snaps) or import an existing media set. Media lands next to
  gamelists under each system (`media/videos/…`), recorded in `gamelist.xml`.
- Enable video in the view: **USER INTERFACE SETTINGS** → enable **"Show game video"** /
  the theme's video view, and set the **video delay** (how long hover waits before playing).
- Recorded snaps (vs Iconic's *live* attract) are the standard, reliable approach —
  [[frontend-inspiration]] already picked this.
- A **RomM** server, if you run one (optional), can also provide artwork/metadata (it scrapes
  ScreenScraper/IGDB), so you need not re-scrape on the cabinet.

## Idle attract / screensaver

**MAIN MENU → USER INTERFACE SETTINGS → SCREENSAVER SETTINGS** (menu label **[verify]** by
version):

- **Screensaver type:** `dim`, `black`, **`random video`** (plays random game video snaps —
  this is the attract mode), or `slideshow` (images).
- **Start-after** idle timeout, and per-type options (video audio on/off, info overlay,
  "favorites only"/per-system source **[verify]**).
- `random video` + a good scraped video set reproduces Iconic's idle attract randomizer.
- Config keys live in `es_settings.cfg` (`ScreenSaverBehavior`, timeout, etc.) under
  `/userdata/system/configs/emulationstation/` — hand-editable to ship defaults. **[verify]**
  exact key names on the target build.

## Shipping this in the arcade pack

- A chosen **theme** name (user downloads it — themes are redistributable, but we reference,
  not bundle, to stay lean).
- Prebuilt **dynamic collection `.xcc`** files (genre rows) + automatic collections enabled.
- `es_settings.cfg` defaults: video view on, `random video` screensaver, sensible timeouts.
- A scrape/metadata step in the setup guide (or RomM media import) — **no artwork shipped**.

Sources: <https://wiki.batocera.org/game_collection_settings>,
<https://wiki.batocera.org/the_scraper>,
<https://www.joeysretrohandhelds.com/guides/es-de-emulationstation-android-setup-guide/>
(ES-DE, for UX reference only)

## Process notes for Staff

One recurring confusion worth nailing in docs: **Batocera's front-end is its own
EmulationStation fork, not ES-DE** — much of the web writing about "EmulationStation
collections/screensaver" is about ES-DE or the RetroPie fork and doesn't map 1:1. I kept the
doc to Batocera-native menus and flagged the few screensaver key names [verify], since the
menu wording shifts between versions.
