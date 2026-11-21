# Changelog

All notable changes to the coin-dojo cabinet are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

Every branch with a change you can see or feel on the cabinet adds a bullet under `[Unreleased]`.
A release moves them into the new version.

## [Unreleased]

## [0.3.1] - 2026-11-20

### Fixed

- Tapping LP on the game menu opens TROPHIES again. In 0.3.0 the cabinet always thought a game was running, so it
  ignored LP: the boot menu (ARCADE / FIGHTCADE) starts the game menu one step further down than the trophies
  screen looked for it.

### Changed

- The docs say that SHOW FPS hides the achievement popups: while it is ON, RetroArch games show no unlocks and no
  RetroAchievements banner (they still count on your account). The FAQ has a new entry, "Achievements stopped
  popping up".
- The README and the start guide name the cabinet it is built for and tested on, and the FAQ shows how to take
  the card out and put it back (tweezers help).

## [0.3.0] - 2026-11-20

### Added

- A game menu that shows only the systems you have games for: arcade (FBNeo, MAME, CPS1/2/3, Neo Geo, NAOMI),
  NES, SNES, Genesis, Sega CD, N64, PlayStation, Saturn, Dreamcast, PC Engine and Atari 2600.
- A boot menu: ARCADE for your games, FIGHTCADE for online matches. Fightcade is downloaded from its official
  sources the first time you pick it; nothing of it ships in the image. Its games go in `roms/fightcade`.
- SETTINGS on the stick: Wi-Fi, cabinet name, password, game lists, attract mode, show FPS, RetroAchievements, time zone.
- A network folder (`smb://coindojo.local`, login `coindojo` / `12345`): drag your games in from a Mac or PC.
- Box art, videos and game info by scraping (docs/SCRAPING.md), then MUST PLAY and genre lists (GAME LISTS, REBUILD).
- After you change the default password: SSH by password and `sudo` for fixing things (docs/FAQ.md).
- Trophies from RetroAchievements, a CRT look, attract mode, and one combo to leave any game: SELECT+START.
- The emulators are built from pinned source; every licence notice is in the image and the source is in
  `coin-dojo-v0.3.0-sources.tar.xz` next to the image.







### Known issues

- The first Fightcade install can fail once; pick FIGHTCADE again and it finishes.
- Change the default password 12345 first: until you do, a program on the cabinet could set its own password.
- Later password changes after the first one are done over SSH (`passwd`, then `sudo smbpasswd -a coindojo`).
- No games, BIOS files or artwork of games are included: bring your own.

[Unreleased]: https://example.invalid/compare/HEAD
[0.3.1]: https://github.com/coin-dojo/coin-dojo/releases/tag/v0.3.1
[0.3.0]: https://github.com/coin-dojo/coin-dojo/releases/tag/v0.3.0
