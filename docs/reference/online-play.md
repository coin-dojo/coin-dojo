# Online / netplay options

> Update 2026-11-19: Fightcade 2 runs on the Pi 5 (Box64 + Wine for its emulator, a native arm64 lobby): full
> speed offline, and one online match played against a remote player with no desync. It is built into the deploy.
> See [fightcade-on-pi5.md](fightcade-on-pi5.md). The table below is the earlier research.

## The stock lobby
The stock image has its own lobby and netcode (`api.iconicarcade.com/Lobby`), tied to its launcher, so a
custom OS does not carry it over. What a custom OS adds instead: the options below.

## Options on a Pi 5 custom OS

| Option | On Pi 5 (ARM) | 2GB | Notes |
|---|---|---|---|
| **RetroArch Netplay** (built-in) | ✅ native | ✅ | Public **lobby browser** + host/join. FBNeo supports **rollback**. Exposed in Batocera's UI. The practical pick. |
| **FightCade** (GGPO rollback, enthusiast standard) | ❌ x86-only | — | Best online fighting experience, but **no ARM/Pi build** — not available on this cabinet. |
| Stock lobby | tied to the stock launcher | — | Not carried over to a custom OS. |

## For good fighting netcode (what matters)
- **Rollback** beats delay-based. RetroArch's FBNeo netplay supports rollback — enable it and
  tune input latency / delay frames per connection.
- **Wire the cabinet to ethernet** (or strong 5GHz) — netcode quality is mostly the connection.
- Target systems are light (CPS2/Neo Geo) so **CPU/RAM is a non-issue on Pi 5 2GB**; the limiter is the network.

## Takeaway
A custom OS gives **working online play via RetroArch Netplay**, ARM-native, fine on 2GB, and
with rollback for FBNeo. FightCade-grade polish isn't reachable on ARM, but rollback netplay is.
