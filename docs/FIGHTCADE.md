# Playing online with Fightcade

Fightcade lets you play arcade fighting games online against real people, for free. It hides most of the delay,
so a match with a nearby player feels like playing side by side. On this cabinet you need no keyboard and no mouse:
the stick and the six buttons do everything. This page tells you what to expect, how to sign in, how to find a
match, and where your games go.

The lobby is Fightcade's own page: its menus and button names can differ a little from this guide, and can change
when Fightcade updates it.

## Contents

- [What to expect](#what-to-expect)
- [Before you start](#before-you-start)
- [The very first launch: the download](#the-very-first-launch-the-download)
- [The controls](#the-controls)
- [First time: sign in](#first-time-sign-in)
- [Find a game room](#find-a-game-room)
- [Challenge someone, or accept a challenge](#challenge-someone-or-accept-a-challenge)
- [During and after a match](#during-and-after-a-match)
- [Chat shortcuts (macros)](#chat-shortcuts-macros)
- [Adding ROMs for Fightcade](#adding-roms-for-fightcade)
- [When something goes wrong](#when-something-goes-wrong)
- [Technical notes](#technical-notes)

## What to expect

- **It feels like the arcade.** Offline the games run at full speed on the heaviest boards tested (CPS3 included).
  Online, Fightcade hides most of the delay: with a good connection and a nearby opponent a match plays like a
  local one. When the connection is poor you see the game correct itself (a character "teleports")
  rather than slow down.
- **One mode at a time.** Fightcade and the game menu (ARCADE) never run together: the cabinet has 2 GB of memory
  and each needs most of it. You pick one in the boot menu; leaving one brings the boot menu back.
- **Starting a game takes a few seconds.** That is normal.
- **The lobby is a column in the middle of the screen**, 4:3 like the games, on black.
- **No web browser.** Links in the lobby to outside web pages open nothing on the cabinet. Do account things
  (sign up, password reset, profile) on a phone or computer.

## Before you start

1. **A Fightcade account.** Sign up for free at fightcade.com on a phone or computer. Your account is yours: nothing
   in this project ships, stores or asks for anyone's login.
2. **A network cable if you can.** Online play depends on the connection more than anything else. A cable is best.
   On Wi-Fi, keep the cabinet near the router.
3. **The games you want to play**, as Fightcade expects them: [Adding games](#adding-roms-for-fightcade).
4. **Internet and a few minutes for the very first launch**: the downloaded image fetches Fightcade the first
   time ([the next section](#the-very-first-launch-the-download)).

## The very first launch: the download

The image you downloaded does not contain Fightcade: Fightcade is someone else's software, and this project never
hands it out. The first time you pick **FIGHTCADE** the cabinet
fetches it from the official sources instead:

1. A screen lists what will be downloaded: Fightcade 2.1.45 from fightcade.com, and the Wine 10.0 and Electron 9.1.0
   it runs on from github.com, about 307 MB in all, and the free space it needs on the card (about 2.2 GB).
   It also says that Fightcade's own terms apply to you.
2. Press **START** to download, or **SELECT** to go back to the boot menu without downloading anything.
3. Bars show each file and the total. Then the cabinet unpacks them and prepares Wine: **a few minutes in all**
   (about a minute of that is "Preparing Wine"). **SELECT** stops it; what was already downloaded is kept and the
   next pick goes on from there.
4. The lobby opens. From then on FIGHTCADE starts straight away, with no download.

Every file is checked against the exact version this project tested before anything is installed. A file that
does not match is deleted. Nothing else is downloaded, never a game.

| The screen says | What to do |
|---|---|
| could not reach github.com / web.fightcade.com | No internet: connect a cable or set up Wi-Fi (SETTINGS), then pick FIGHTCADE again |
| ... does not match its pinned sha256 / ... bytes upstream, pinned ... | The check (a fingerprint of the file) did not match: it arrived damaged or was changed at its source. Nothing was installed: pick FIGHTCADE again. If it stays, download the latest image |
| Fightcade needs about ... MB free | Free space on the card (or use a bigger one), then pick FIGHTCADE again |
| preparing Wine failed | Pick FIGHTCADE again. If it keeps failing, report it ([Technical notes](#technical-notes)) |

Any button on that screen goes back to the boot menu. Nothing is ever left half-installed: the next pick tries
again cleanly.

## The controls

Panel names: punches **LP MP HP**, kicks **LK MK HK**, plus **START** and **SELECT** (the coin button).

| Where | Press | Does |
|---|---|---|
| Boot menu | STICK, then LK | pick FIGHTCADE (ARCADE starts by itself after 5 s if you do nothing) |
| Lobby | STICK | move the pointer (it speeds up the longer you hold) |
| Lobby | LK / MK | left click / right click |
| Lobby | SELECT+LK | open or close the on-screen keyboard |
| Lobby | SELECT+MK | pointer off or on |
| Keyboard open | STICK / LK / MK | move between keys / type the key / delete |
| Keyboard open | START / SELECT | close and send (a real Enter, as from a USB keyboard) / close |
| In a game | the stick and buttons | play; the pointer switches off by itself |
| In a game | hold SELECT+START 2 s | close the game, back to the lobby |
| Lobby | hold SELECT+START 2 s | leave Fightcade, back to the boot menu |

Every combo on the cabinet: [REFERENCE.md](REFERENCE.md).

## First time: sign in

1. In the boot menu, move the stick to **FIGHTCADE** and press **LK**. The lobby opens on Fightcade's sign-in page
   (the very first time on a downloaded image, after [the download](#the-very-first-launch-the-download)).
2. Move the pointer onto the username field and press **LK** to select it.
3. Press **SELECT+LK**. The on-screen keyboard opens in the empty panel under the Fightcade logo.
4. Type your username: the stick moves between keys, **LK** types the key, **MK** deletes. **SHIFT** gives one capital
   letter; digits are on the top row, and **123** switches to symbols and accents.
5. Press **SELECT** to close the keyboard. Point at the password field, press **LK**, then **SELECT+LK** and type
   the password the same way.
6. Press **START** to send. You're in.

Fightcade remembers the sign-in on the cabinet, so next time the lobby opens signed in. The on-screen keyboard never
logs or saves what you type, and its macro SAVE button refuses a password field.

## Find a game room

Every game has its own room (Fightcade calls them channels): a chat, a list of players in that room and their
connection to you.

1. Search for the game, or browse Fightcade's list of games, and join its room.
2. Rooms you have joined usually stay listed in the lobby, so you can click back into them.
3. Before you play online, start the game offline once from its room (Fightcade's TEST GAME or TRAINING). It runs
   on the cabinet alone: a quick way to see whether your set loads. Close it with SELECT+START (2 s).

## Challenge someone, or accept a challenge

**Ask first.** The polite way is to say hello in the room's chat and ask for a set, for example "FT3?" (first to 3
wins). The [chat shortcuts](#chat-shortcuts-macros) have the usual lines ready.

**Pick a good opponent.** The player list usually shows each player's ping (the connection time to you). Lower is better:
nearby players in your region give the smoothest matches.

**Challenge.** Click the player's name (LK, or MK for a menu) and look for the challenge option. When they accept, the
game starts on both cabinets or PCs by itself.

**Accept.** When someone challenges you, Fightcade shows the challenge in the lobby. Click accept and the game
starts.

**Watch.** Fightcade also lets you spectate matches in progress. It is a good way to check a game runs and to
learn.

**Words you will see in chat:**

| Term | Usually means |
|---|---|
| FT2, FT3, FT5, FT10 | "first to N": the set ends when one player has won N games |
| ranked | the set counts toward your Fightcade rank |
| unranked, casuals | just for fun, nothing recorded |
| gg, ggs | "good game(s)", said after a set |
| rematch?, one more? | another set? |
| glhf | "good luck, have fun", before a set |
| brb, afk | "be right back", "away from keyboard" |

## During and after a match

- The game fills the screen height (4:3, with black bars at the sides). The pointer is off while you play, so the stick only moves your character.
- When the set is over, **hold SELECT+START for 2 s** to close the game (it may also close by itself when your
  opponent leaves). Only the game closes: you are back in the lobby with the pointer on (unless you had switched it off yourself
  before the game).
- For 5 seconds after a game closes, SELECT+START does nothing. That stops a late press from throwing you out of
  Fightcade. To leave Fightcade, wait a moment, then hold SELECT+START for 2 s in the lobby.

## Chat shortcuts (macros)

Typing on the stick is slow, so the on-screen keyboard has a **MACROS** page with ready lines in tabs:

- **CHALLENGE**: FT2?, FT3?, FT5?, FT10?, ranked?, unranked?, casuals?
- **AFTER**: gg, ggs, rematch?, one more?, ty, glhf
- **STATUS**: brb, afk, "sorry, lag"
- **MINE**: your own lines.

Open the keyboard in the chat field (SELECT+LK), go to MACROS, press **LK** on a line to type it, then **START** to
send it. A macro only types; nothing is sent until you press START.

**Your own lines.** Type a line in the chat field, then press **SAVE** (on any MACROS tab); it goes to MINE.
**FORGET** removes the MINE line you last moved onto. Lines are up to 60 characters; the MINE tab shows 12 of them.
They are kept on the cabinet, and an update never touches them.


## Adding ROMs for Fightcade

This project ships no games and does not link to, download or point you at any place to get them. Use only games
you copied ("dumped") yourself from boards you own and are entitled to use ([LEGAL.md](LEGAL.md)).

**Fightcade has its own folder: `roms/fightcade`.** Fightcade runs its own copy of the arcade emulator. It may
want a different version of a game's zip than the game menu does. One folder can't hold two versions of the same
file. So put Fightcade's versions in `roms/fightcade`. Fightcade looks there first.

**Then it looks in your arcade folders:** `roms/arcade`, `roms/fbneo`, `roms/cps1`, `roms/cps2`, `roms/cps3` and
`roms/neogeo`, the same folders the game menu uses. If one zip works for both, keeping it once in its arcade folder
is enough. You need no second copy.

**Copy the files** the same way as your other games: open the cabinet's network folder from your computer
([start guide](START.md), step 5) and drag the zip into its folder, for example `roms/fightcade`. The zips go
straight into `roms/fightcade`, not into a folder of their own inside it.

**Keep the exact file name.** Fightcade looks for the name the room shows (for example `sfiii3nr1.zip`). A renamed
zip is not found. Don't unpack it either.

**Use the version Fightcade expects.** Arcade game zips come in versions that change over the years. Fightcade
wants the version made for its own emulator (FinalBurn Neo, the version listed in [Technical notes](#technical-notes)).
A zip made for another version may be missing a file, and the game then fails to start.

**Some games need two zips.** Many rooms use a "clone": a variant of a game (another region or revision). A clone's
zip holds only what differs, so the original game's zip (the "parent") must be there too, in the same folder.

> **Example.** The room asks for `sfiii3nr1`. That is a clone of `sfiii3`.
> Put **both** `sfiii3nr1.zip` and `sfiii3.zip` into `roms/fightcade`.

**Neo Geo games** also need `neogeo.zip` (the Neo Geo BIOS, a start-up file) in `roms/neogeo`.

**Test it first.** Start the game offline from its room once ([Find a game room](#find-a-game-room), step 3) before
you challenge anyone.

## When something goes wrong

| Problem | What to do |
|---|---|
| The room says the ROM is missing | Check the exact set name the room asks for, that it is Fightcade's version (in `roms/fightcade`), and that the parent zip is there |
| The game starts, then closes straight away | Usually an incomplete or wrong-version set: same checks as above |
| Players lag or teleport | Use a network cable. Pick opponents with a lower ping. On Wi-Fi, keep the cabinet near the router |
| A challenge never connects | Your router may be blocking online matches. Look in its settings for a switch called **UPnP** and turn it on. Not sure how? Ask whoever set up your internet, or your internet provider. Say: "please turn on UPnP for my arcade cabinet" |
| A game is stuck | Hold SELECT+START for 2 s. Still stuck: switch off with the power button, wait 10 seconds, switch on again |
| FIGHTCADE in the boot menu opens the game menu instead | Fightcade is not installed on this cabinet (a self-built image): [BUILD.md](BUILD.md#7-the-full-cabinet-deploy) |
| FIGHTCADE shows a download screen | The first launch of an image without Fightcade: [the download](#the-very-first-launch-the-download) |

More answers: [FAQ.md](FAQ.md#online-play-fightcade).

Fightcade is not affiliated with this project. Not legal advice; I'm not a lawyer.

## Technical notes

For people who build the image or report a bug; players can skip this.

- Written against Fightcade 2.1.45 (the version the build pins), with online matches played on a Pi 5 cabinet in
  November 2026. How it was built and benchmarked: [reference/fightcade-on-pi5.md](reference/fightcade-on-pi5.md).
- Fightcade uses rollback netcode (GGPO). The game is a Windows program running through Box64 and Wine on the Pi's
  ARM processor; the lobby itself is native.
- A self-built image without Fightcade (`hk_fightcade_install: first-launch`) downloads it at first launch like the
  release image. Over SSH the same download runs with `/usr/local/lib/coin-dojo-fightcade/hk-fightcade-fetch --yes`.
  Each file is checked against its pinned sha256 checksum.
- Folders on the cabinet: Fightcade's FBNeo reads `/data/roms/fightcade` first (#298: its own versions; not a game
  menu system), then `/data/roms/arcade`, `fbneo`, `cps1`, `cps2`, `cps3`, `neogeo`, then
  `/data/fightcade/roms-extra/` (the lobby's own downloads; not in the network folder). Romset version:
  FBNeo 0.2.97.44.
- Change the ready chat lines with `hk_fightcade_chat_macros` in your config ([CONFIG.md](CONFIG.md)).
- Logs for a bug report: `/data/fightcade/logs/` on the cabinet (`fetch.log` for the first-launch download,
  `wineboot.log` for "preparing Wine failed").
