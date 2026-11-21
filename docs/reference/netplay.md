# Netplay in Batocera (RetroArch) — fighting-game online play

Enabling RetroArch Netplay on the cabinet for online fighting games. Context in
[[online-play]]: it adds rollback-capable online play on our own OS, and the target
systems (CPS2/Neo Geo via FBNeo) are light enough that **the network, not the Pi 5, is the
limiter**.

## Requirements

- **Batocera 5.25+** (any current build qualifies).
- **All players on the same Batocera release, same core version, same ROM** — RetroArch
  compares **md5 checksums**; a mismatched romset won't connect.
- **Wired Ethernet** strongly preferred over Wi-Fi (plug the cabinet into the switch). Netcode
  quality is mostly the connection.

## Enable it

- **MAIN MENU → GAME SETTINGS → NETPLAY SETTINGS** → turn the toggle on.
- (Older/scripted: `global.netplay=1` in `/userdata/system/batocera.conf`.)
- Settings in that menu:
  - **Enable** (on/off)
  - **Nickname** — your lobby name.
  - **Port** — default **`55435`**; leave empty if your router does **UPnP** (it opens the port
    automatically).
  - **Use Relay Server (MITM)** — routes through a relay for networks that can't open a port;
    **adds latency**, so leave **off** unless UPnP/port-forward isn't possible.
  - **Check missing indexes at startup / Reindex all games / Index missing games** — builds the
    md5 index so the lobby can match your games to opponents'.

## Host / join

- **See the lobby:** on the **systems list** in EmulationStation, press **`X`** to list
  available Netplay servers and games in progress. **Greyed-out entries can't be joined**
  (version/core/rom mismatch or full).
- **Host:** in the **games menu**, highlight a game and press **`X`** to launch a Netplay
  server for it. Others then see it in the lobby.
- RetroArch also has its own public **lobby browser** under the in-game Netplay menu.

## FBNeo / fighting games

- **FBNeo netplay is supported** for **Arcade, Neo Geo, and Neo Geo CD** — i.e. the cabinet's
  core fighting lineup (CPS2, Neo Geo).
- **Rollback caveat:** RetroArch's netplay is primarily a **delay-based + stall/rewind** model
  with an input-latency buffer; it is *not* full GGPO/FightCade-grade rollback. [[online-play]]
  frames FBNeo as "rollback-capable" — treat the exact behaviour as **[verify on hardware]**,
  and even delay-based RetroArch netplay on a wired line is playable. FightCade (true
  GGPO) remains **x86-only / not on ARM**, so it's out on this Pi 5 cabinet.

## Low-latency tuning (for fighters)

RetroArch exposes netplay latency controls (in the in-game **Netplay** options and
`retroarchcustom.cfg`) — tune per connection:

- **Input latency frames (min / range):** `netplay_input_latency_frames_min` and
  `netplay_input_latency_frames_range`. Lower = snappier but needs a better connection; a
  small non-zero range lets RetroArch absorb jitter. Start low on LAN/good lines, raise if the
  game stutters/desyncs.
- **Check frames:** `netplay_check_frames` — how often peers verify sync (catches desyncs).
- **Stateless/"frame" settings** and **Favor hosting** are in the same menu. **[verify]** exact
  key names on the build.
- General latency wins that matter more than any toggle: **wired Ethernet**, **UPnP/port-
  forward instead of relay**, integer/low **video latency** settings, and keeping both ends on
  identical versions.

## Shipping this in the arcade pack

- `batocera.conf` default `global.netplay=1` + a placeholder nickname the user changes.
- Setup-guide note: wire the cabinet to Ethernet, enable UPnP or forward **55435**, keep
  Batocera/core/romset versions matched with opponents.
- A short "fighting-game latency" tuning section (the input-latency-frames guidance above).

Sources: <https://wiki.batocera.org/netplay>,
<https://docs.libretro.com/guides/netplay/>

## Process notes for Staff

The Batocera netplay wiki page is thin and a bit dated ("up in the air, early 2023") and
doesn't document rollback or the per-frame latency keys — I filled those from RetroArch/
libretro knowledge and flagged the exact key names and the rollback-vs-delay question
**[verify]**. Worth a real two-cabinet test to settle the rollback claim in [[online-play]].
