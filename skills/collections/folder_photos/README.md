# Folder photos for the nested menus (2026-11-14)

Real images for the GENRES / DECADES / SYSTEMS folders (the maintainer: "themed real photos"):

- **Console folders** (nes, snes, genesis, segacd, saturn, dreamcast, n64, psx, pcengine, neogeo): Evan-Amos product
  photos from Wikimedia Commons, public domain or CC BY-SA 3.0. `commons-fetch.py OUTDIR` downloads 800 px thumbnails
  (https only, <= 5 MB, PNG/JPEG magic check, polite rate) and writes `CREDITS.tsv` (license + source per file).
- **Genres, decades, arcade boards**: a 2x2 grid of in-game screenshots of hand-picked games (`folder_picks.tsv`, one
  regex per slot; edit freely). `resolve-picks.py folder_picks.tsv` runs on the cabinet against a nested plan dir and
  fills a missing pick with the folder's top-rated game (it imports `nested.py` for the virtual-system names: the parent folder in a checkout, or `nested.py` +
  the deploy's `virtual_systems.json` beside it on the cabinet). Vertical games are letterboxed, never stretched.
- `make-art.py S` (Pillow, `~/.venvs/badges`) builds `S/folder_photos/<virtual>/<folder>.png` in the theme frame
  (navy panel, gold border) plus `S/preview-all.png`. White product-shot backgrounds are keyed out.

Outputs are NOT committed: the screenshots are the owner's scraped media, and the photos travel with their CREDITS.tsv.
Deploy: copy to `/data/coin-dojo/folder_photos/` and run `collgen.py nested ... --folder-art /data/coin-dojo/folder_photos`.

The maintainer's picks: `personal/skills/collections/folder_photos/folder_picks.tsv` (#190; the file here is the community example).
Series grids (#55b): `folder_picks.tsv` has 4 picks per series folder (`series<TAB>slug<TAB>regexes`). On the laptop:
dry-run `collgen.py nested --series ... --apply` into a scratch dir, run `resolve-picks.py` with `PICKS_NEST=<scratch>/roms_nested
PICKS_MEDIA=<mirror of /data/media> PICKS_GAMELISTS=<gamelists>`, scp only the resolved screenshots, then `make-art.py`.
MUST PLAY folders reuse `systems/<slug>.png` automatically (no separate art).
