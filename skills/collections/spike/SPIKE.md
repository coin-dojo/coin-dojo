# Spike: nested main screen (ticket #38), ES-DE 3.5.0 in the preview harness

All five pieces work. Everything below was run with `spike/run_spike.sh` / `spike/e2e.py` (synthetic fixtures:
empty ROMs, placeholder PNGs) and the screenshots were looked at. Re-run: `spike/run_spike.sh exclusive OUT "shot:a Right Return shot:b"`.

| | Result |
|---|---|
| a. custom systems with folders | WORKS. A custom `es_systems.xml` system whose `<path>` dir holds subfolders of symlinks to real ROMs shows the folders in its gamelist. `<folder>` entries in the virtual gamelist.xml give folder names/descriptions. `<path>` may be absolute (`/data/nested/genres`). |
| b. hide the real systems | WORKS two ways: (1) top-level `<loadExclusive/>` in custom `es_systems.xml` (log: "Only loading custom file as the <loadExclusive> tag is present", `loaded 4 systems`); it must sit OUTSIDE `<systemList>`, inside it is silently ignored. (2) set `ROMDirectory` to the nested root so no real system dir exists (`loaded 4 systems`). Also exists: a `noload.txt` in a system dir. Overriding the real systems by name with an empty dir did NOT hide them (cps1 still showed 3 games). Chosen: `<loadExclusive/>` (no es_settings.xml edit, which holds a scraper password). |
| c. media for symlinked games | WORKS: `downloaded_media/<virtual>/<type>/<folder>/<game stem>.<ext>` (e.g. `genres/covers/fighters/sfa2.png`); flat system: `<type>/<stem>.<ext>`. Folder picture: `covers/<folder>.png` (shown in the folder's detail pane). |
| d. carousel badges | WORKS: `badge.py` PNGs named after the system `<theme>` (`genres.png`, `decades.png`, `systems.png`, `must-play.png`) in a COPY of the theme's `_inc/systems/`. Without `must-play.png` the theme draws plain text. |
| e. launch | WORKS: every virtual system `<command>` is the dispatcher; ES-DE logged `Launching game ... from system "GENRES (genres)"`, the dispatcher resolved the symlink and printed the real command for a game inside a folder, and for a path with a space. |

Extra: `<systemsortname>0-must-play</systemsortname>` orders the carousel MUST PLAY, GENRES, DECADES, SYSTEMS
(otherwise alphabetical). ES-DE counts a game once per virtual system entry: GENRES "5 games" = the symlinks.

Screenshots: `<worktree dir>/spike-out/` (not in git). `exclusive/01*.png` the 4 carousel entries,
`exclusive-genres/10-genres-folders.png`, `11-fighters-games.png`, `exclusive-systems/21-dreamcast-games.png`,
`hide-override/05-after4rights.png` (override fails), `e2e/03-fighters.png` (generated tree with media).

## Harness changes for Coordinator (not made here)
`preview-esde.sh` / `make_home.py` assume bundled systems + collections. To use the generator output they need:
a `--home-extra DIR` that is copied over `home/ES-DE` (custom_systems/, gamelists/, downloaded_media/), a read-only
mount of the symlink tree at the `--nested-root`, a Python-capable image (add `python3` to `esde/Dockerfile`),
and `check_log.py` must not treat `loaded 4 systems` as an error. The spike uses its own `Dockerfile`/`run-steps.sh`
(multi-step key + screenshot runner) which could be promoted into the harness.
