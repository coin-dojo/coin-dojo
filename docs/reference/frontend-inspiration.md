# Front-end inspiration

Launchers/arcades to study for UX ideas for our own menu.

| Front-end | Why look at it |
|---|---|
| **ES-DE** (EmulationStation Desktop Edition) | Modern RetroPie/Batocera UI. Video snaps on hover (= our showcase), system→game views, favorites / last-played / most-played rows, built-in attract/screensaver. Best all-round reference. |
| **Pegasus** | QML-themed, fully custom looks — best fit for a bespoke "own style", web-like theming. |
| **AttractMode** | Classic MAME-cabinet front-end: wheel UI, marquee art, dynamic layouts. Very "real arcade". |
| **HyperSpin / LaunchBox BigBox** (Windows) | Flashy gold standard — wheel + video + marquee + themes. Visual ambition reference. |
| **RetroArch Ozone/XMB** | The libretro front-end our cores run under; clean, playlist/thumbnail driven. |
| **Console homes + Netflix** | The row + hover-video-preview pattern Iconic already borrows and does well. |

## Ideas to borrow
- Video/gameplay snap on hover (the showcase) — Iconic does **live** attract; ES-DE/HyperSpin use **recorded** snaps (more reliable, standard).
- Marquee strip up top; metadata panel (year / players / manufacturer / genre).
- Favorites + Recently Played + Most Played rows; collections / custom lists.
- Attract/screensaver cycling snaps after idle (keep Iconic's randomizer).
- Wheel / coverflow navigation (HyperSpin, AttractMode).

## Menu information architecture (decided direction)
- **Category-primary** rows (Fighting, Beat-'em-ups, Shooters, Puzzle…); **platform** (CPS1/2/3, NAOMI)
  as a **filter / alternate view**, not the main rows — people pick by what they want to play.
- Personal rows on top: **★ Favorites** (pinned), **⏱ Recently Played** (auto, ties to logging).
- **All Games / Search** as the library grows (28+ and counting).
- Keep the **hover-showcase** and **idle attract randomizer** over any layout.
