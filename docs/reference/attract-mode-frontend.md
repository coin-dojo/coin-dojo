# Attract mode + the Iconic front-end feel (ES-DE)

How to get the Iconic experience — **category rows, a hover video showcase, and an idle
attract reel** — out of ES-DE on the cabinet. See [[frontend-inspiration]] for the goals and
[[stock-look-crt-shaders]] for the in-game look.

## Attract mode = ES-DE's Video screensaver
ES-DE's screensaver, set to **Video**, plays random game **video snaps fullscreen** when the
cabinet sits idle — that's the arcade attract reel.

Menu path (reliable): **Main menu → UI settings → Screensaver settings**
- **Start screensaver after**: ~90 seconds (matches `ScreensaverTimer` we pre-set).
- **Screensaver type**: **Video**.
- **Render scanlines / blur**: optional light touch to match the CRT vibe.
- **Screensaver controls**: ON — a button press on the stick jumps straight into the shown
  game (classic "attract → play").
- **Game info**: show title/system briefly at start.

Pre-seeded in the image (`~/ES-DE/settings/es_settings.xml`, confirm key names on-device):
`ScreensaverType = video`, `ScreensaverTimer = 90000`, `ScreensaverControls = true`.

**Attract needs video snaps.** ES-DE plays what's in the **MediaDirectory** (`/data/media`).
So scrape first (below) — until then the screensaver falls back to dim/slideshow.

## Category rows (the Iconic "category-first" menu)
ES-DE **Collections** give the row structure:
- **Automatic collections** (enable in UI settings): **Favorites**, **Last played**, **All games**.
- **Custom collections** by genre/theme: Fighters, Beat-em-ups, Shmups, Neo Geo, Classics, …
  (Main menu → Game collection settings → Custom collections → create + add games.)
Map these to your own game roster. Favorites + Last-played + genre rows ≈ the
Iconic category layout.

## The hover showcase (video preview of the selected game)
This comes from the **theme** + scraped **video snaps**: as you move across tiles, the theme
plays that game's video. Pick a theme that shows a large video preview:
- ES-DE bundles a theme set; good video-forward choices: **ESonline**, **Modern-DE**,
  **Art Book Next**, **Ruckus** (dark, grid/carousel, big video pane — arcade-cabinet vibe).
- Set in **UI settings → Theme**. This is a taste call — the maintainer picks; all are Pi 5-fine
  (video decode is light at 1080p; keep one video playing, not a wall of them).

## Scraping media (one-time, enables showcase + attract)
Main menu → **Scraper**. Source **ScreenScraper** (best arcade video coverage; free account) or
TheGamesDB. Scrape **video snaps + marquees + box art** into `/data/media`. Do it once over the
network after the roster is on `/data/roms`. This is what makes both the hover showcase and the
video attract reel light up. (Can also be driven from a RomM server's media, if you run one.)

## Parity checklist vs Iconic
- [x] Category rows → ES-DE automatic + custom collections
- [x] Hover video showcase → video-forward theme + scraped snaps
- [x] Idle attract → Video screensaver (with "controls" = press-to-play)
- [x] 4:3 bezel + light CRT in game → RetroArch ([[stock-look-crt-shaders]])
- [x] Stick-only navigation → SDL controller map ([[cabinet-controller]])
- [ ] Confirm on hardware (needs the cabinet booted + scraped) — the one thing a Mac can't do.
