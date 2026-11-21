# Start here

No typing needed. You need an Iconic Arcade cabinet, a spare microSD card (16 GB or more) and a computer on your
home network.

1. **Download the image** from the project's [Releases page][releases].

2. **Write it to the card** with the free Raspberry Pi Imager
   (<https://www.raspberrypi.com/software/>):
   1. Pick **Raspberry Pi 5**, click **Next**.
   2. Scroll down, click **Use Custom**, pick the downloaded file, click **Next**.
   3. Put the SD card in your computer. Pick it, click **Next**.
   4. Click **Skip customisation** (settings added there stop the cabinet from starting).
   5. Click **Write**, then **"I understand, erase and write"**. Wait until done.

3. **Start the cabinet.** Put the card in the Raspberry Pi's card slot (short edge, opposite the USB ports). Plug
   in a network cable if you have one, then switch on. The first start takes a few minutes.

4. **Join Wi-Fi** (skip with a cable). On the game menu, move the stick until **SETTINGS** is in the middle,
   press **LK**. Pick **WI-FI**, then your network. Type its password with the stick.

5. **Add your games** through the cabinet's network folder.
   - Mac: in Finder, **Go**, **Connect to Server**, type `smb://coindojo.local`, click **Connect**.
   - Windows: in File Explorer's address bar type `\\coindojo.local\roms`, press Enter.

   Log in as `coindojo`, password `12345`. Drag each game into its folder in `roms`:
   - Arcade: `fbneo`, `arcade` (MAME sets), `cps1`, `cps2`, `cps3`, `neogeo`, `naomi`
   - Nintendo: `nes`, `snes` (Super Nintendo), `n64`
   - Sega: `genesis` (Mega Drive), `segacd`, `saturn`, `dreamcast`
   - Others: `psx` (PlayStation), `pcengine` (TurboGrafx-16), `atari2600`

   Use only games you copied ("dumped") from cartridges, discs or boards you own. Some systems also need a
   BIOS, a start-up file copied from the console: Neo Geo, NAOMI, Dreamcast, Sega CD, Saturn, PlayStation,
   PC Engine CD. BIOS files go into `bios`, except Neo Geo's `neogeo.zip`: into `roms/neogeo`
   ([names](ROMS.md#bios-files)).

6. **Play.** On the game menu pick **QUIT TO MAIN MENU**, then **ARCADE** on the next screen (two big cards):
   new games show there. Leave a game with **SELECT+START**. Online play: **FIGHTCADE**
   ([guide](FIGHTCADE.md)).

7. **Change the password.** **SETTINGS**, **PASSWORD**, **CHANGE PASSWORD**: type a new one twice (6 characters or
   more). The network folder uses it too.

**Box art and game lists.** Fetch box art and game info first: [SCRAPING.md](SCRAPING.md). Then **SETTINGS**,
**GAME LISTS**, **REBUILD GAME LISTS** builds MUST PLAY, genres, decades and series. Your own: [LISTS.md](LISTS.md).

Build the image yourself (optional): [BUILD.md](BUILD.md).

[releases]: https://github.com/coin-dojo/coin-dojo/releases/latest
