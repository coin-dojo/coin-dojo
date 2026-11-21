# Skill: Build the cabinet's ES-DE collections (MUST PLAY, genres, 5-year decades)

Turn the cabinet's scraped ES-DE gamelists plus the Cabinet Picks list into ES-DE custom
collections (`custom-<name>.cfg`, lines `%ROMPATH%/<system>/<romfile>`), entirely on the
laptop. One tool, `collgen.py`, stdlib Python 3 only, plan by default.

**Ship no artifacts:** the repo holds the tool and tiny test fixtures only. Your gamelists, the
libretro dat and the picks file stay on your machine (not committed). No ROMs, no art.

(The tool is `collgen.py`, not `collections.py`: a file with that name next to the script
shadows Python's stdlib `collections` module and breaks every import.)

## What it produces
- **Genre collections:** `fighters shmups beat-em-up run-and-gun platformers puzzle sports racing maze twin-stick`
  (from the free-text ES-DE genre; unknown genres are ignored). One genre per game.
- **`twin-stick` (#59):** a genre text with `twin stick`/`twin-stick` (Cabinet Picks code `tws` -> `Twin Stick`), plus
  every `(system, ROM stem)` in `twin_stick.tsv` whatever its genre says (their scraped genre is just "Shoot'em Up").
  `twin_stick.tsv` must match `skills/controls/controls.py` `TWIN_STICK`; `tests/test_twin_stick.py` cross-checks both
  (SKIP until controls.py has it). Add a game = one row in each. A bad/empty/duplicate row is exit 3.
- **5-year decade collections:** `1980-84`, `1990-94`, ... (from `releasedate`, 1975-2014).
  A bucket is kept only with **25 or more games** (`--min-decade N` to change).
- **`must-play`** (#182, the maintainer 2026-11-17): the **best-rated 25 games of each system** (`--must-play-top N`,
  lever `hk_must_play_top`): rated before unrated, best rating first, a Cabinet Pick (matched by normalised title inside
  the right system) wins a tie, then the name. Real data 2026-11-17: 322 games (was 667 = picks + every CPS game).
- **`mpg-<bucket>`** (#182): every game rated **4.5 stars or more** (`--must-play-genre-min STARS`, lever
  `hk_must_play_genre_min_stars`) in its GENRES bucket; `nested` turns them into the **MUST PLAY GENRES** entry
  (`must-play-genres` in `hk_virtual_systems`; its folders reuse the GENRES titles, blurbs and art). Real data: 166 games.
- **FBNeo arcade gamelist** (`fbneo-gamelist`): name + year from libretro's
  `FBNeo - Arcade Games.dat`, genre borrowed from the picks, arcade titles cleaned
  (region/revision tags such as `(Euro)`, `(Revision B)` dropped, kept only when two sets would
  otherwise show the same name).

Exit codes: `0` ok, `2` usage error, `3` input error (missing file, malformed gamelist, no picks).

## Steps
Make form of every step: `make -C skills/collections help` (`W=` required; plan by default, `APPLY=1` adds
`--apply`; `install-cfg` is the step-6 copy, a dry run unless `APPLY=1`, after `cabinet-guard`).

Run from the repo root. `$W` is a scratch dir outside git, e.g. `W=~/coin-dojo-collections`.

1. **Pull the gamelists from the cabinet (read-only copy):**
   ```
   W=~/coin-dojo-collections; mkdir -p $W/gamelists
   scp -r -i ~/.ssh/id_ed25519 'coindojo@<cabinet host>:ES-DE/gamelists/*' $W/gamelists/
   ```
   VERIFY: `ls $W/gamelists/*/gamelist.xml` lists one file per system (cps1 cps2 cps3 fbneo
   genesis neogeo ...). If the folder is empty the path differs: `ssh coindojo@<cabinet host> ls ~/ES-DE`.
2. **Have the picks file** (`cabinet-picks.html`, the Cabinet Picks page) and, for the arcade
   gamelist, the libretro `FBNeo - Arcade Games.dat` and a text file of the cabinet's FBNeo zip
   names (one per line; `ssh coindojo@<cabinet host> ls /data/roms/fbneo > $W/fbneo_zips.txt`).
3. **(Only if arcade names/years/genres changed) plan the FBNeo gamelist:**
   ```
   python3 skills/collections/collgen.py fbneo-gamelist --dat $W/libretro.dat \
     --zips $W/fbneo_zips.txt --picks $W/cabinet-picks.html --out $W/gamelists/fbneo/gamelist.xml
   ```
   VERIFY: prints `zips: N named: ... with-genre: ...` and `NEW`/`CHANGED`/`SAME`. PASS = exit 0.
   Then add `--apply` to write it (it overwrites only `--out`, locally).
4. **Plan the collections** (writes nothing):
   ```
   python3 skills/collections/collgen.py build --gamelists $W/gamelists \
     --picks $W/cabinet-picks.html --out $W/collections_out
   ```
   REVIEW: the per-collection counts, `(decade ... dropped)` lines, and the
   `NEW`/`CHANGED`/`SAME`/`STALE` list. `STALE` files are old cfgs this run no longer makes; they
   are never deleted for you. STOP if a count is 0 or far from the last run.
5. **Apply locally:** same command plus `--apply`.
   VERIFY: `ls $W/collections_out` shows `custom-must-play.cfg`, the genre and decade cfgs;
   `head -3 $W/collections_out/custom-must-play.cfg` shows `%ROMPATH%/<system>/<romfile>` lines.
6. **Copy to the cabinet** (deploying is Platform's Ansible job; this is the manual form of it):
   ```
   scp -i ~/.ssh/id_ed25519 $W/collections_out/custom-*.cfg coindojo@<cabinet host>:ES-DE/collections/
   ```
   ES-DE picks the new collections up on its next start. Do not restart ES-DE or kill `cage`
   from this skill; the maintainer (or the deploy role) does that.

## Real-data reference (2026-11-14)
12 systems, 413 FBNeo games: decades `1980-84` 97, `1985-89` 156, `1990-94` 366, `1995-99` 174,
`2000-04` 60; `must-play` 462.

## Nested main screen (`collgen.py nested`, ticket #38)
Main carousel = MUST PLAY (one folder per system), GENRES, SERIES, DECADES, SYSTEMS (each but the first opens folders; SERIES only with `--series`). Proof + screenshots: `spike/SPIKE.md`.
```
python3 skills/collections/collgen.py nested --gamelists $W/gamelists --picks $W/cabinet-picks.html \
  --out $W/nested_out [--media $W/downloaded_media] [--folder-art DIR] [--folder-text folder_text.tsv] [--series series.tsv] [--flat-must-play] [--apply]
```
The virtual-system names (must-play ... settings) are the setting `hk_virtual_systems` (`config/variables.d/collections.yml`),
read by `nested.py`: env `HK_VIRTUAL_SYSTEMS` (JSON list), else `virtual_systems.json` next to it (the deploy writes it beside
the tools on the cabinet, so by-hand runs there just work), else that file in a checkout (#94).
Plan by default (counts, NEW/CHANGED/SAME per file, link counts, stale links); `--apply` writes under `--out`:
`custom_systems/es_systems.xml` (`<loadExclusive/>` hides all bundled systems), `gamelists/<virtual>/gamelist.xml`,
`roms_nested/<virtual>/<folder>/<symlinks>` (targets `--rom-root`, default `/data/roms`), `downloaded_media/...` links.
Regenerating keeps `favorite`/`playcount`/`lastplayed` of existing virtual gamelists and removes only stale symlinks.
**Crossover games (#116).** A game in two folders of one virtual system (a versus crossover in two SERIES folders)
may not have two symlinks to one ROM: ES-DE saves a played game by matching `<path>` on `getCanonicalPath()` (symlinks
resolved, ES-DE 3.5 `GamelistFileParser::updateGamelist`), so it deleted the other folder's entry and wrote the played one
twice (VM repro 2026-11-15). The first folder keeps the plain link; each later folder links (relative) to a pointer file
`roms_nested/.crossover/<virtual>/<folder>/<name>` (`COIN-DOJO-LINK <rom path>`), a real file with its own canonical path.
The dir sits beside the virtual systems, outside every ES-DE `<path>`; `launch.py` follows the pointer, so deploy
`launch.py` first (`make deploy-launch`), then collections. Counts stay strict: link set == `<game>` set.
Every virtual system launches via `launch.py %ROM%` (realpath -> emulator command; `--dry-run` prints it; exit 3 ROM
missing/outside the ROM root, 4 unknown system dir, 5 broken table; `--check` checks the tables). Since #62 every
RetroArch system runs `RETROARCH = /data/emulators/retroarch-1.22/retroarch` (Debian's `/usr/bin/retroarch` 1.20
deadlocks, #49, and is rejected), a `CORE_BY_SYSTEM` value may be an absolute core path (n64 = the GLES build in
`/data/emulators/mupen64plus-gles/`; a relative path is an error), and `RETROARCH_BY_SYSTEM` (empty) can give one system
its own binary + `-c cfg`. Dreamcast/naomi go to `flycast-run.sh`. `launch_expected.json` is the exact argv per system
(recorded from the 2026-11-14 cabinet hot-patch); `launch_check.py --launcher F --expected launch_expected.json` checks
any launch.py against it. Deploy + rollback: role `launcher` (`make deploy-launch` / `make launch-rollback`, dry run
unless `APPLY=1`, `LIMIT=coin-dojo-vm` for the VM; ansible/README.md). No ES-DE restart: it runs per game.
Needs on the cabinet: `launch.py` at `--launcher`, `roms_nested` at `--nested-root` (default `/data/nested`), badges
`must-play/genres/series/decades/systems.png` in the theme (Design; a missing `series.png` or folder art breaks nothing).

### MUST PLAY per system (default, ticket #55b)
MUST PLAY has one subfolder per system (folder = system slug, except `neogeo` -> `neo-geo` on disk, see below: `cps1 cps2 cps3 fbneo neogeo genesis segacd snes nes pcengine
psx saturn dreamcast n64 atari2600`; a system with no must-play game gets no folder). The folder reuses the SYSTEMS list:
display name (`SYSTEM_TITLES`), blurb (`folder_text.tsv` row `systems<TAB>slug`; a `must-play<TAB>slug` row wins), info
panel (`SYSTEM_FACTS`: maker / year / kind plus rating, players, developer from its games) and folder art
(`--folder-art DIR`: `DIR/must-play/<slug>.png` if present, else `DIR/systems/<slug>.png`). The list is the same games, only
grouped; a name-collision suffix now only matters inside one folder. `--flat-must-play` keeps the old single list. Grouping
by genre was considered and rejected: GENRES already is that, and per-system tells the player what hardware a game needs.

### MAME BIOS/device names are never folder or file names (2026-11-14)
The virtual systems are `<platform>arcade</platform>`, and on an arcade/neogeo platform ES-DE silently drops every file
**and folder** whose stem is a MAME BIOS or device short name (`FileData::isArcadeAsset`). The folder `neogeo` (MAME's Neo
Geo BIOS) vanished with all its games: SYSTEMS showed 1035 instead of 1143, MUST PLAY 608 instead of 669. So:
- a folder slug that is such a name is written as another folder on disk (`nested.FOLDER_DIRS`: `neogeo` -> `neo-geo`,
  `naomi` -> `sega-naomi`, any other -> `<slug>-games`). Display name, blurb (`folder_text.tsv`), info panel and folder art
  (`--folder-art DIR/<virtual>/<slug>.png`) still go by the slug; the cover link is `covers/neo-geo.png`. Favourites saved
  under the old `./neogeo/...` path are carried over. The plan prints `folder systems/neogeo -> neo-geo/ on disk`.
- a BIOS set (`neogeo.zip`, `naomi.zip`, ...) is never linked (`names` hides it already; this is the second guard), and a
  real game whose stem is a device name gets the ` (system)` link-name suffix. Real data 2026-11-14: only `neogeo.zip`.
- the list is `mame_hidden_names.tsv` (`bios|device<TAB>name`, 95 + 1414), copied from ES-DE v3.5.0
  `resources/MAME/mamebioses.xml` + `mamedevices.xml` (source and commit in its header). Regenerate it when ES-DE is upgraded.
Keeping `arcade` (instead of dropping the platform) keeps ES-DE's MAME name lookup for unnamed zips and arcade scraping.

### Validate before writing to the cabinet (`validate_nested.py`, CI + Ansible pre-write gate)
```
python3 skills/collections/validate_nested.py --out $W/nested_out [--es-systems FILE] [--map /data/roms=$W/roms --map /data/media=$W/media]
```
For every `<system>` in `--es-systems` (default `OUT/custom_systems/es_systems.xml`) it checks: (1) on an arcade/neogeo
platform, no folder or file under `roms_nested/<system>/` has a MAME BIOS/device stem; (2) no symlink under `roms_nested/`
or `downloaded_media/` is dead (targets are cabinet paths: on the cabinet no `--map`; off it map them to a local copy or
placeholder tree); (3) no two links of one system resolve to the same file (the #116 merge) and every `.crossover` pointer names an existing
ROM; (4) the `<game><path>` set of `gamelists/<system>/gamelist.xml` equals its link set (so link count ==
game count; a game hidden in ES-DE keeps its link and counts, the summary shows how many are hidden). Output: a summary
line per system, one `FAIL ...` line per problem, then `RESULT: PASS` (exit 0) or `RESULT: FAIL` (exit 1); exit 2 usage,
3 input error (missing dir/es_systems.xml, malformed XML, empty name list; also prints `RESULT: FAIL`).
Real data 2026-11-14 (twin-stick run): PASS, must-play 669 / genres 1068 (10 folders; twin-stick 6, shmups 291) / series 279 /
decades 1125 / systems 1143 links = games;
the pre-fix output FAILs on `must-play/neogeo` and `systems/neogeo`.

### SERIES folders (`--series series.tsv`, ticket #55)
One folder per franchise (Mega Man, Metal Slug, Streets of Rage, ...), between GENRES and DECADES. A game sits in its
series folder AND its genre/decade folders. Without `--series` there is no SERIES entry and nothing else changes.
`series.tsv` is curated (the maintainer's list: `personal/skills/collections/series.tsv`; the file beside the tools is the community
example, see "Curated lists" below), tab separated, `#` comments: `slug  title  name-regex  systems  developer  min`
(columns 4-6 optional, `-` = none). The name regex is `re.search` on the cleaned display name (case-sensitive unless it
starts with `(?i)`); `systems` is a comma list of system dirs; `developer` is a regex that must match the whole
`<developer>` text; a game must pass all three. A series shows only with **>= 3 games** (`min` column overrides it;
The maintainer's list has one `2` on purpose). Hidden games never match. Toaplan and Cave are matched by the developer field
(Cave minus the DonPachi games, which have their own folder); every other series by explicit title regexes, anchored where
a bare word would misfire (`^Sonic (The Hedgehog|CD|&)` so Sonic Blast Man, Sonic Wings and ChuChu Rocket stay out).
Blurbs: `series<TAB><slug><TAB>text` rows in `folder_text.tsv` (every series.tsv row needs one; a test checks it). The folder
info panel shows genre `Game series` plus the usual rating/year/developer from its games; name clashes get the usual
` (System)` suffix. Exit 3 (nothing written): bad regex, wrong field count, bad slug or min, duplicate slug, empty file,
missing file. Add a franchise = one row + one blurb, then `test_series.py` (cases that name the maintainer's own franchises:
`personal/skills/collections/tests/series_cases.tsv`). Folder art: 4 picks per series in `folder_photos/folder_picks.tsv` (`series<TAB>slug<TAB>4 regexes`; a 3-game series has 3, the
third is centred), built like the genres/decades grids (`folder_photos/README.md`); run `resolve-picks.py` and `make-art.py`
with `PICKS_NEST` / `PICKS_MEDIA` / `PICKS_GAMELISTS` set to run it on the laptop. Real-data counts (2026-11-15, #55c: +18
franchises incl. Marvel/X-Men, Lode Runner, Thunder Force, Punch-Out!!): 55 series, 350 links (was 37 / 278 on the same
data); list them with `nested` plan output (series line) or `build_series`. Guilty Gear has one game (DC), so no folder.
`test_series.py` also checks every series has 3-4 compiling picks and no slug is a MAME BIOS/device name.

## Clean display names (`collgen.py names`, ticket #39, standard: `NAMING.md`)
Rewrites only `<name>`/`<sortname>`/`<hidden>` in each `<system>/gamelist.xml` (text-level: `<path>`, `<desc>`, the
`<alternativeEmulator>` second root and everything else stay byte-identical; files are never renamed). Plan by default.
```
python3 skills/collections/collgen.py names --gamelists $W/gamelists [--dat $W/libretro.dat] [--roms $W/roms] [--show N|-1] \
  [--apply [--backup-dir DIR] [--stamp S]]
```
Rules in order: `[x]` counts as a tag; ` ~ ` dual title keeps the last part; whitespace; ` : ` / first spaced ` - ` -> `: `
(`X-Men`, `Spider-Man` untouched); `Legend, The` -> `The Legend` (+ `<sortname>` without the article); `(...)` tags dropped
unless two games would collide (`clean_titles`). Unnamed games: arcade from the DAT description (`--dat`), consoles from
the file name. Hidden: BIOS sets (built-in list + DAT descriptions ending in BIOS) and `(Disc N)` members of an `.m3u`.
`--roms DIR` (folders `DIR/<system>/` of the cabinet's ROM files; e.g. `ssh ... ls` into empty placeholder files, or a mount) adds a minimal
`<game><path>./file</path><name>..</name></game>` for every ROM file with no entry (arcade name from the DAT, first alias before ` / `;
consoles from the file name; BIOS hidden; a system with no gamelist gets a new one). Saves/art/text files and dot files are
skipped; existing entries are never duplicated. `nested --roms` lists them too.
`--apply` copies each changed gamelist to `gamelist.xml.bak-names-<stamp>` (or `DIR/<stamp>/<system>/`) first; a second
run is `SAME`. `nested` takes the same `--dat` and builds its virtual gamelists from the cleaned names (hidden games skipped).

## Curated lists: personal/ first (#190)
`series.tsv`, `folder_text.tsv`, `duplicates.tsv`, `system_names.tsv` and `folder_photos/folder_picks.tsv` beside the tools
are the community examples. The maintainer's own lists live in `personal/skills/collections/` (same names) and win:
`nested.data_path(name)` picks the personal file when it exists (the `dupes` default, the tests), the
`esde_collections` role ships the personal file instead of the example (not in EDITION=community), and laptop runs pass
`--series personal/skills/collections/series.tsv --folder-text personal/skills/collections/folder_text.tsv`.
`system_names.tsv` (`system  title  maker  year  kind`) renames systems on the carousel and in the info panel; the
example has no rows, so the community edition shows nested.py's neutral board names.

## Curated duplicates (`collgen.py dupes`, ticket #50, list: `duplicates.tsv`)
The maintainer 2026-11-14: "remove the weaker version of games". `duplicates.tsv` (tab separated, `#` comments:
keep-system, keep-path, hide-system, hide-path) lists the 52 home ports hidden in favour of the arcade original
(+ one keep-only note row `fbneo contra.zip - -`: checked to exist, nothing hidden). Games kept on purpose because
they are different games (NES Bionic Commando, Ninja Gaiden, Rygar, Contra, Battletoads, Double Dragon II; SNES
Super Punch-Out!!; Genesis Moonwalker; Neo Geo Double Dragon) are not in the list.
```
python3 skills/collections/collgen.py dupes --gamelists $W/gamelists [--duplicates FILE] [--apply [--backup-dir DIR] [--stamp S]]
python3 skills/collections/collgen.py dupes --gamelists ~/ES-DE/gamelists --roms /data/roms --media /data/media --delete [--show] [--apply]
```
`--delete` (the maintainer 2026-11-14: "nuke them ... save space") removes each hide entry for good: its `<game>` block, the ROM
file and every scraped media file `<media>/<system>/<type>/<stem>.*` (ES-DE does not clean these up itself). It needs
`--roms` (exit 2 without), refuses (exit 3, nothing touched) when a keep entry or its ROM file is missing, and a rerun
counts deleted entries as `already gone`. Done on the cabinet 2026-11-14 (52 entries, ~2.1 GB); copies stay in the
ROM library on the NAS. Regenerate `nested` afterwards so the virtual links go too.
Plan by default (per system: to hide / already hidden, `SAME`/`CHANGED`). `--apply` backs each changed gamelist up
(`gamelist.xml.bak-dupes-<stamp>` or `DIR/<stamp>/<system>/`) and only adds `<hidden>true</hidden>` to the hide entry
(an explicit `<hidden>false</hidden>` is flipped); nothing is ever removed. A second run is `SAME`. On the cabinet all 52
are hidden, so a plan reports `0 to hide, 52 already hidden ... SAME`.
Exit 3 (nothing written): a keep-path or hide-path not found in its system's gamelist, a missing gamelist, a malformed
TSV row, an empty list, or a virtual system named in the list. Rerun it after `names` (names never un-hides).

## Virtual systems in the gamelists dir; name collisions (#50)
`nested` (and `names`/`build`) ignore `must-play`, `genres`, `series`, `decades`, `systems` under `--gamelists`: those are the
generated virtual gamelists that get deployed into `~/ES-DE/gamelists/` and are not real systems (reading them used to
multiply the links, genres 569 -> 2655). In the virtual gamelists only (each must-play/genres/series/decades folder; the flat list with `--flat-must-play`),
when two visible games share a display name (case/punctuation-insensitive: `Out Run` == `OutRun`) the system is appended:
`Bionic Commando (Arcade)` / `Bionic Commando (NES)`; `<sortname>` stays the bare title so the pair sorts together. The
real per-system gamelists and the `systems` folders are never changed. Rule and labels: `NAMING.md` section 5.

## Fill missing metadata + media without re-scraping (`collgen.py metadata`, ticket #60)
Adds only what is MISSING (`desc rating releasedate developer publisher genre players`, `covers`/`screenshots` files) from
data we already have; never overwrites a value or a media file, skips hidden games and the virtual systems. Plan
by default; `--apply` backs each changed gamelist up first (`gamelist.xml.bak-metadata-<stamp>` or
`DIR/<stamp>/<system>/`) and copies media; a second run is `SAME`. Code: `metadata.py`.
1. **Export RomM (optional; your own server; read-only SELECT; the password stays inside the container):**
   ```
   ssh <romm-host> "docker exec -i <db-container> sh -c \
     '<db-client> -u\"\$DB_USER\" -p\"\$DB_PASSWORD\" \"\$DB_NAME\" -B -N -r'" \
     < skills/collections/romm-export.sql > $W/romm.jsonl
   ```
   `$DB_USER`, `$DB_PASSWORD`, `$DB_NAME` stand for the DB container's own credential env vars (named in your RomM
   compose file); `<db-client>` is that container's SQL client.
2. **Plan + want list** (`$W/media` = the cabinet's media root, or a placeholder tree of empty files made from
   `ssh coindojo@... 'cd /data/media && find <systems> -maxdepth 2 -type f'`):
   ```
   python3 skills/collections/collgen.py metadata --gamelists $W/gamelists --romm $W/romm.jsonl --dat $W/libretro.dat \
     --picks $W/cabinet-picks.html --media $W/media --want $W/want.txt
   ```
3. **Stage the RomM art (read-only, into a fresh dir):**
   `rsync -a --files-from=$W/want.txt <romm-host>:<romm-resources-dir>/ $W/staging/`
4. Re-run step 2 with `--staging $W/staging` (no `--want`). REVIEW per system `missing before->after`, `sources:`,
   any `REFUSED (title guard)` line. Then `--apply [--backup-dir DIR]`.

Rules: match by ROM file name in the mapped RomM platform (`PLATFORMS`: fbneo->arcade, neogeo->neogeomvs, dreamcast->dc
...), else by the tag-free stem when exactly one row has it (same extension breaks a `.chd`/`.m3u` tie). Title guard:
the gamelist name must look like RomM's name or one of its alternative names, else the game is `REFUSED` and nothing is
written for it (a gamelist name that is just the file stem leaves the file name match standing). Genre: a matched
Cabinet Pick's code wins (it maps 1:1 to a collection), else RomM's genres joined, minus `Arcade` and a bare `Shooter`
when another entry names a bucket. Rating: RomM's 0-100 average / 100, rounded half up to the nearest
0.05 and written like ES-DE (`80.195` -> `0.8`, `70` -> `0.7`, `100` -> `1`). Arcade year/manufacturer from the dat when RomM has none. Media: the staged file
must be a PNG/JPEG by magic bytes, at most `--max-bytes` (5 MB), not a symlink; the extension follows the real type
(RomM `.jpg` screenshots are often PNG). Exit 3 (nothing written): a malformed RomM line (not JSON, no fs_name, wrong
types, a rating outside 0-100 or not a number, absolute or `..` media path, duplicate).
**Fallback, off by default:** `--thumbnails skills/collections/thumbnails.tsv --fetch-dir $W/thumbs --fetch` downloads
libretro-thumbnails at the pinned commit (https raw.githubusercontent.com only, 5 MB cap, magic check, data only) for
media RomM lacks; `--fetch-dir` must be new or empty; later runs drop `--fetch` and reuse the dir. Arcade thumbnails
use the dat description, consoles the ROM stem. Apply with ES-DE stopped: a running ES-DE rewrites a gamelist from
memory when it saves (e.g. after a launch) and would drop the added fields.

## RetroAchievements progress in the game list (`ra_progress.py`, ticket #46)
ES-DE 3.5 has no RetroAchievements support, so the progress goes into the first line of each matched game's `<desc>`
(the side panel shows it): `🏆 12/40 (Hardcore)`, `🏆 12/40 (Softcore)` (no hardcore unlock), `🏆 12/40 (8 Hardcore)`
(mixed), `🏆 0/40` (the game has a set, nothing unlocked). Stdlib only; header of `ra_progress.py` has the detail.
```
python3 skills/collections/ra_progress.py fetch [--entry trophies.hks] [--out FILE] [--systems cps2,nes,...]
python3 skills/collections/ra_progress.py write --progress FILE --gamelists DIR [--roms /data/roms] \
  [--nested-root /data/nested] [--nested-gamelists DIR] [--pins TSV] [--hashes TSV] [--hash-cache FILE] \
  [--matches FILE] [--backup-dir DIR] [--apply]
```
- **fetch** reads the TROPHIES config the settings role installs (`trophies.hks`: user, `key_file` 0600 written from the
  Keychain with `no_log`, `cache_dir`) through `skills/settings/trophies_core.py` (same `read_key`/`http_get`: the key
  goes only into the request URL, never into output, logs or files; the user name is not written either). Calls
  `API_GetGameList.php` (`h=1` hashes, `f=1` games with a set) per RA console and `API_GetUserCompletionProgress.php`
  (paged), then writes `<cache_dir>/ra-progress.json` atomically. Any failure: exit 4, old file kept. The TROPHIES cache
  (`trophies.json`, 5 recently played games, no hashes) is not enough and is not touched.
- **write** (plan by default) matches each game: a `--pins` row (`system<TAB>./path<TAB>RA game id`) wins, else its RA
  hash inside that system's RA console: arcade systems (fbneo cps1-3 neogeo naomi) = MD5 of the file name without
  extension (no ROM needed); nes/snes/pcengine (header skipped), n64 (byte order normalised), genesis, atari2600 are
  hashed from `--roms` (a `.zip` by its one ROM); discs (psx saturn dreamcast segacd) only via `--hashes`
  (`system<TAB>./path<TAB>md5`, e.g. RAHasher output) or pins. A game whose hash is known but has no set loses its line;
  one that cannot be hashed is left alone (so a laptop run without ROMs removes nothing).
- Only the leading line of `<desc>` (plus one blank line) is ours; it is replaced, never duplicated, and every other byte
  stays (text-level edit; a desc that held only our line is removed again). Virtual gamelists (`must-play`, `genres`, ...)
  are matched through their `roms_nested` links (`--nested-root`; #116 pointer files followed); without it they are
  skipped. `nested` copies `<desc>` from the real gamelists, so a regenerated nested tree carries the lines too.
- `--apply` backs up each changed gamelist (`DIR/<stamp>/<system>/gamelist.xml`, or `.bak-ra-<stamp>` beside it), writes
  atomically, refreshes `--hash-cache` (size + mtime keyed) and `--matches` (`system ./path id total`). A rerun is `SAME`
  and writes nothing. Exit 3 (nothing written): missing/malformed progress file, malformed gamelist, bad pins/hashes row,
  a pin to a game not in the progress file.
- **Has achievements folder:** `collgen.py nested --ra-matches FILE [--ra-folder-in genres]` adds the folder `achievements`
  ("Has Achievements", blurb in `folder_text.tsv`) to that virtual system; games also in another folder go through the #116
  pointer. No carousel entry; the trophy badge/theme is Design's. A malformed matches file is exit 3.
- ES-DE rewrites a played game's entry from memory on exit, which drops its line until the next `write`: run it after a
  game exits (or nightly) with ES-DE idle, like `metadata`.

## Tests
`make -C skills/collections test` runs all of them.
```
python3 skills/collections/tests/test_collgen.py     # also pytest-compatible
python3 skills/collections/tests/test_nested.py      # nested generator, virtual-dir skip, collision suffix, BIOS names
python3 skills/collections/tests/test_validate_nested.py   # validator: neogeo folder, BIOS file, dead link, count mismatch
python3 skills/collections/tests/test_twin_stick.py  # twin-stick genre: list override, broken lists, controls.py cross-check
python3 skills/collections/tests/test_series.py      # series.tsv membership, >=3 rule, false positives, broken rows
python3 skills/collections/tests/test_dupes.py       # duplicates.tsv + dupes subcommand
python3 skills/collections/tests/test_names.py       # NAMING.md rules + broken cases
python3 skills/collections/tests/test_launch.py      # dispatcher; argv per system == the live hot-patch fixture
                                                     #   (LAUNCH_LIVE=<fresh cabinet copy> adds it); /usr/bin/retroarch, relative core fail
python3 skills/collections/tests/test_metadata.py    # metadata fill: adds only, title guard, broken RomM rows
python3 skills/collections/tests/test_ra_progress.py # RA line: idempotent, user text kept, nested links, fake API;
                                                     #   broken progress/gamelist/pins -> byte-identical, no key/user leaks
```
Fixtures in `tests/fixtures/` are tiny hand-made gamelists (one with the `<alternativeEmulator>`
second root), a picks array and a dat. The broken cases (malformed gamelist, missing input,
bad usage) must exit 3 / 3 / 2.

## FIGHTCADE and QUIT TO MAIN MENU (#214)

Two one-entry tool systems of their own (like TROPHIES; never inside `settings`, whose #195 hook fires on cursor stop):
`nested --fightcade-entry FILE` / `--quit-entry FILE` add them (order FIGHTCADE, TROPHIES, SETTINGS, QUIT; slugs
`fightcade`, `quit`; theme badge `<slug>.png`). Launch = `bash /data/coin-dojo/esde_entry.sh fightcade|menu`
(`esde_entry.sh`, shipped by the esde_collections role): refuses while retroarch/retroarch122/flycast runs (log
`~/.cache/coin-dojo/esde-entry.log`, ES-DE has no message box), writes the mode atomically to
`$XDG_RUNTIME_DIR/coin-dojo/next-mode` (dir 0700), then `coin-dojo-session-stop` (session role #209: the session TERMs
ES-DE and reads next-mode; without the helper the script TERMs ES-DE itself). The role passes `--fightcade-entry` only
when `/usr/local/bin/coin-dojo-fightcade` exists, so FIGHTCADE is hidden (not generated) until Fightcade mode is
deployed; run `make deploy-esde` again after it. Tests: `tests/test_esde_entry.py`.
