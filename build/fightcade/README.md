# Fightcade downloads (Fightcade mode, #207 / #204)

`fetch.sh` downloads the three third-party pieces the cabinet's Fightcade mode needs, each **sha256- and
size-pinned**. A download is checked before it is moved into `out/`: a mismatch deletes it and exits 1, nothing is
unpacked. Box64 itself is built from source by `build/box64/build.sh`.

**`pins.json` is the one source of the pins** (#267): url, file, sha256, size (and Fightcade's version, Electron's
SHASUMS256.txt url). `fetch.sh`, the `fightcade` role (`fightcade_pins`) and the cabinet's first-launch download
(`hk_fightcade_install: first-launch`, which fetches the same official urls on the cabinet) all read it; tests
fail if a pin is written anywhere else.

| File | Pin | Source |
|---|---|---|
| `wine-10.0-amd64-wow64.tar.xz` | sha256 `aeebbbf2...5c255f` | Kron4ek Wine-Builds release 10.0 |
| `electron-v9.1.0-linux-arm64.zip` | sha256 `b34c7906...a163269`, and equal to the release's `SHASUMS256.txt` | electron/electron v9.1.0 |
| `Fightcade-linux-v2.1.45.tar.gz` | sha256 `9880cbfa...c50be`, 164613755 bytes = Fightcade **2.1.45** | web.fightcade.com/download/ (versioned url) |

Full values are in `pins.json`. Other outputs: `VERSION.txt` (the Fightcade version, read out of the
tarball and checked against the pin) and `SHA256SUMS`.

## Why these versions

The #92 spike measured 59.59 fps (max frame 28 ms) with exactly these. x86 Electron crashes under Box64
(tcmalloc), hence the native arm64 Electron 9.1.0 for the lobby. Wine 11.22 asserts under Box64. Debian's
`box64` 0.3.4 crashes (see `build/box64/README.md`).

## Run

    build/fightcade/fetch.sh [--out DIR]     # default build/fightcade/out/ (gitignored)

Files that already match their pin are kept (no network, except that a re-download of Electron also reads
`SHASUMS256.txt`). Exit codes: 0 ok, 1 download failed or sha256 mismatch, 2 bad usage or bad pin, 3 curl missing.

## Re-pinning Fightcade (a new version)

The pin is a **versioned** url, `https://web.fightcade.com/download/Fightcade-linux-v<version>.tar.gz` (#267 review,
2026-11-19: `-v2.1.45` is the same file as the old moving `Fightcade-linux-latest.tar.gz`, sha256 checked by this
script). It never changes under us, so a new Fightcade release only reaches a cabinet when somebody re-pins it after
testing:

1. Download the new version by hand into a scratch folder: `curl -fLO https://web.fightcade.com/download/Fightcade-linux-v<new>.tar.gz`
   (the `-latest` url shows what is current), and read its version: `tar -xzOf Fightcade-linux-v<new>.tar.gz Fightcade/VERSION.txt`.
2. Test it on the cabinet (the #204 recipe); note `shasum -a 256` and the size in bytes (`wc -c`).
3. In `pins.json` (`fightcade`): change the version in `url` and `file`, and set `sha256`, `size`, `version` and
   `title`. Update the table above and `build/fightcade/tests/test_fetch_script.py` (`test_pins_present`); run
   `make -C build fightcade` once (it checks sha256, size and VERSION.txt). Commit. The role, the image and the
   cabinet's first-launch download follow from that one file. (The role's `install_mib` for the unpacked size, in
   `ansible/roles/fightcade/defaults/main.yml`, is an estimate for the space check: update it if the tree grows.)

## Never commit the downloads

Third-party software is fetched from upstream by the user, never redistributed (`AGENTS.md`, "ship no
artifacts"). `out/` is gitignored.

## Tests

    python3 build/fightcade/tests/test_fetch_script.py

They run offline against fake files served through `FETCH_URL_PREFIX=file://...` (a test hook: a temp copy of
`pins.json` holds the fake files' sha256 and size).
