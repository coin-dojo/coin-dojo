# FAQ and troubleshooting

Short answers. The first part is for players: no typing, no computer skills beyond dragging files. The second part
is for people who build the image themselves; skip it if you downloaded the image.

## Contents

- [For players](#for-players)
  - [Getting started](#getting-started)
  - [Games](#games)
  - [SETTINGS](#settings)
  - [Online play (Fightcade)](#online-play-fightcade)
  - [Picture, sound, buttons](#picture-sound-buttons)
- [For builders](#for-builders)

## For players

### Getting started

**Can I download a ready-made image?** Yes. Download the latest image from the project's [Releases page][releases];
the [start guide](START.md) walks you through it. You can also build it yourself ([BUILD.md](BUILD.md)), but you
don't need to.

**Is it safe to use my only SD card?** Use a spare card. This is a hobby project, so things can go wrong. Writing
the image wipes the card, so pick one with no photos or anything else you need on it.

**Taking the card out and putting it back.** The card sits deep in a narrow gap, so use tweezers.

1. Switch off (see **How do I switch off?** below), then unplug the cabinet.
2. To take it out, grip the card with the tweezers and pull it straight out.
3. To put it back, hold it with the tweezers the same way round it came out. Slide it in slowly and straight.
4. Watch the gap: the card can easily slip between the Raspberry Pi and the cabinet's frame instead of into the
   slot. If it doesn't go in with a light push, take it out and line it up again.
5. Before you plug in, check that the card is all the way in the slot.

**What is the login?** User `coindojo`, password `12345`. Change it on the cabinet: **SETTINGS**, **PASSWORD**,
**CHANGE PASSWORD**, then type the new one twice with the stick. The cabinet reminds you at every start until you
do. The new password works for the network folder too. Do this first: until you change `12345`, a program running
on the cabinet could set a password of its own and become admin (full control of the cabinet). Once admin is on,
it stays on, even after you change the password again.

**How do I get into the cabinet to fix something?** This is optional, for people who want to type commands. It is
called remote login, or SSH: you type commands on the cabinet from your computer.

1. Change the password on the cabinet: **SETTINGS**, **PASSWORD**, **CHANGE PASSWORD**. Pick a strong one: anyone
   on your network can try it.
2. On a Mac, open the **Terminal** app. On Windows, open **PowerShell**.
3. Type `ssh coindojo@coindojo.local` and press Enter.
4. The first time, it asks if you trust the cabinet. Type `yes` and press Enter.
5. Type your password and press Enter. Nothing shows while you type; that is normal.

Commands that need admin rights start with `sudo` and ask for the same password. While the password is still
`12345`, remote login and `sudo` stay off. To change the password again later, type `passwd`, then
`sudo smbpasswd -a coindojo` so the network folder gets it too. The PASSWORD page then says ADMIN IS ON.

**How do I switch off?** Press the cabinet's power button once. A screen asks you to confirm: press the button
again before the countdown ends. Wait until the screen goes dark, then switch off at the wall.

### Games

**Which folder does my game go in?** The list is in the [start guide](START.md), step 5. Every system has its own
folder inside `roms`, for example Super Nintendo games go into `roms/snes`.

**What is a BIOS, and do I need one?** A BIOS is a small start-up file copied from a console. Only some systems need
one (Neo Geo, NAOMI, Dreamcast, Sega CD, Saturn, PlayStation, PC Engine CD games). The exact file names are in
[the games guide](ROMS.md#bios-files). If a game for one of those systems will not start, a missing or wrong BIOS
is the usual reason.

**What does "dumped" mean?** A copy of a game you made yourself from a cartridge, disc or arcade board you own. Use
only those.

**I copied a game and it is not in the menu.** The game menu looks for new games when it starts again. On the game
menu pick **QUIT TO MAIN MENU**, then **ARCADE**. Still missing? Check that it is in the right folder (start guide,
step 5).

**My games have no box art, and MUST PLAY is empty.** The cabinet fetches pictures and game info from the internet
with a free account: [the scraping guide](SCRAPING.md). Then rebuild the lists in **SETTINGS**, **GAME LISTS**.

**An arcade game will not start.** Keep the zip file exactly as you got it: don't rename it and don't unpack it.
Some games are a "clone" (a variant of another game) and also need the original game's zip in the same folder.
More: [the games guide](ROMS.md#arcade-romsets-fbneo-mame-and-fightcade).

**How do I leave a game?** Press **SELECT+START**. In Fightcade, hold **SELECT+START** for 2 seconds.

**A game is stuck and SELECT+START does nothing.** Switch off with the power button (above), wait 10 seconds, switch
on again.

**The network folder refuses my password.** Use the cabinet's password (`12345` until you change it). Changed it
and it still fails? Set it once more on the cabinet: **SETTINGS**, **PASSWORD**, **CHANGE PASSWORD**.

**My computer cannot find `coindojo.local`.** Use the cabinet's number address instead. On the cabinet open
**SETTINGS**: the first page shows it under **IP ADDRESS**. On a Mac type `smb://` and that number; on Windows type
`\\`, that number, then `\roms`.

### SETTINGS

On the game menu, move the stick until **SETTINGS** is in the middle and press **LK**. The stick moves, **LK**
picks, **MK** goes back, **SELECT** closes the app. The entries, in order:

- **WI-FI**: join a network. No Wi-Fi in the list? Use a network cable.
- **CABINET NAME**: rename the cabinet. Your computer then finds it as the new name plus `.local`.
- **PASSWORD**: change the cabinet's password.
- **GAME LISTS**: **REBUILD GAME LISTS** builds MUST PLAY, genres, decades and series from your scraped games
  (scrape first: [the scraping guide](SCRAPING.md)). Your own lists: [LISTS.md](LISTS.md).
- **ATTRACT MODE**: turn it **OFF**, or pick when it starts (5, 10, 30 or 60 minutes) and what it shows. Say yes to
  the reload.
- **SHOW FPS**: shows the game's speed (frames per second) in a corner, from the next game on. While it is
  **ON**, RetroArch games show no popups: no achievement unlocks, no RetroAchievements banner. Leave it **OFF**
  unless you are measuring speed.
- **RETROACHIEVEMENTS**: sign in with your own RetroAchievements account to earn trophies.
- **TIME ZONE**: pick your zone.
- **BACK**: leave SETTINGS.

### Online play (Fightcade)

**How do I sign in without a keyboard?** In the lobby press **SELECT+LK** for the on-screen keyboard
([Fightcade guide](FIGHTCADE.md#first-time-sign-in)). A USB keyboard works too. Fightcade remembers you afterwards.

**A room says the ROM is missing.** Fightcade wants the game's zip under the exact name the room shows, and often
the original game's zip too: [Adding games for Fightcade](FIGHTCADE.md#adding-roms-for-fightcade).

**Matches stutter or characters jump around.** Use a network cable if you can. On Wi-Fi, keep the cabinet near the
router. Pick opponents who live near you.

**A challenge never connects.** Your router may be blocking online matches. Look in its settings for a switch
called **UPnP** and turn it on. Not sure how? Ask whoever set up your internet, or your internet provider. Say:
"please turn on UPnP for my arcade cabinet".

### Picture, sound, buttons

**The picture has black bars left and right.** That is on purpose: the games are drawn in the shape of an old
arcade screen (4:3), centred where the cabinet's bezel opening is.

**No sound.** Sound goes to the screen over its HDMI cable. Check the screen's volume and that it is on the right
input.

**Buttons are in the wrong order.** Each system has its own button layout, chosen to match the original. The
layouts are in [skills/controls/SKILL.md](../skills/controls/SKILL.md).

**Achievements stopped popping up.** Check **SETTINGS > SHOW FPS**: while it is **ON**, RetroArch games hide
their popups, so unlocks and the RetroAchievements banner no longer show (they still count on your account).
Turn it **OFF** and start the game again.

**Black screen after an update.** Switch off and on once. Still black? Ask someone who builds these images.

## For builders

Skip this part if you downloaded the image. These answers need a terminal. Log files:
[REFERENCE.md](REFERENCE.md#log-files).

**The build stops, or a verify line is not `PASS`.** Do not flash. The line names what is missing. Re-run the
build; build steps and the verify list: [skills/build-image/SKILL.md](../skills/build-image/SKILL.md).

**Can I build on Linux or Windows?** Linux (x86_64 or arm64) with Docker Engine, and macOS with Colima or Docker
Desktop (#237). On x86_64 the build emulates arm64 and is several times slower. Windows: not tested.

**Which SD card size?** 16 GB at least, 32 GB recommended; bigger cards just work (the data partition grows
at first boot). [BUILD.md](BUILD.md#sd-card-sizes).

**Does it need a 2 GB Pi 5?** It was built and measured on one (#92, 2026-11-18). A Pi 5 with more memory should
work the same; not tested.

**Was AI used to build this?** Yes; see the note at the top of the [README](../README.md).

**Default login and SSH.** User `coindojo`, password `12345`, when your `coindojo.yml` sets no password (#265,
#270). While it is still `12345` it opens the console and the network folder (`roms`, `bios`) for any computer on
the cabinet's local subnets, and SSH accepts only your key, so change it soon (SETTINGS > PASSWORD). Once it is your
own password, SSH also accepts it for that user (never root) and `sudo` works (#299). Keys keep working.
[BUILD.md](BUILD.md#5-first-boot).

**`sudo` says I am not in the sudoers file.** Admin (sudo) is off while the password is the default `12345`
(#270). Set your own in SETTINGS > PASSWORD, or `password_hash` in `coindojo.yml`
([BUILD.md](BUILD.md#part-d-your-settings-file-wi-fi-ssh-key) shows how): either turns admin on. A plain `passwd`
does not. `sudo` still trusts whoever types the password at your account, so it is no shield against a program that
already runs as your user ([BUILD.md](BUILD.md#5-first-boot)).

**A `passwd` over SSH broke the network folder.** `passwd` changes only the login and leaves the folder without a
password. Set the password again in SETTINGS > PASSWORD: it sets both
([ROMS.md](ROMS.md#copy-over-the-network-mac-or-windows)).

**`dd` wrote to the wrong disk.** `dd` cannot be undone. Restore that disk from your backup. This is why BUILD.md
says to read the size column twice and to back up first.

**I set a user, Wi-Fi or SSH in Raspberry Pi Imager and nothing happened.** Imager 2.x writes those settings as
cloud-init, which this image does not apply. Put them in `coindojo.yml` instead
([BUILD.md](BUILD.md#4-flash), [REFERENCE.md](REFERENCE.md#first-boot-file)); a `userconf.txt` (user and hashed
password, as older Imager versions write it) on the boot partition also works.

**The first boot shows no Wi-Fi.** Use Ethernet for the first boot, or the SETTINGS app, or put `wifi_ssid`,
`wifi_psk` and `wifi_country` in `coindojo.yml` ([REFERENCE.md](REFERENCE.md#first-boot-file)). A redeploy keeps
the ATTRACT MODE and SHOW FPS choices made in SETTINGS.

**A game is not in the menu after a deploy.** Is it in the right `/data/roms/<system>/` folder
([ROMS.md](ROMS.md#systems-and-folders))? Then rebuild the menus ([ROMS.md](ROMS.md#after-you-copy)).

**An arcade game will not start (logs).** Almost always the romset: the zip was renamed, its parent zip is missing,
or it was made for another emulator version ([ROMS.md](ROMS.md#arcade-romsets-fbneo-mame-and-fightcade)). Check
`~/ES-DE/logs/es_log.txt`.

**A console BIOS does not load.** Check the file name and md5 (a fingerprint of the file) against
[ROMS.md](ROMS.md#bios-files): a wrong region or a bad dump is the most common cause.

**A game hangs and the combo does nothing.** From another computer, `make cabinet-status` shows which game runs.

**Is online play proven on the Pi?** Offline play runs at full speed on the heaviest boards tested (#92,
2026-11-18). One online match was played on the Pi against a remote player on 2026-11-19 (sfiii3nr1, safe Box64
flags, no desync). Netplay determinism with the Box64 fast flags is not measured yet
([reference/fightcade-on-pi5.md](reference/fightcade-on-pi5.md#open-questions)).

**FIGHTCADE in the boot menu opens the game menu instead.** Fightcade is not installed:
[BUILD.md](BUILD.md#7-the-full-cabinet-deploy). Logs: `/data/fightcade/logs/`.

**Measuring the connection.** `make net-check`. Wi-Fi power save is already off
([TUNING.md](TUNING.md#wi-fi-power-save-off)). Matches that never connect: enable UPnP on the router, or forward the
UDP port Fightcade shows.

**The game looks choppy at full speed.** That was the 59.59 fps game on a 60 Hz panel; Force 60 Hz fixed it
([TUNING.md](TUNING.md#force-60-hz-and-the-direct-hdmi-sink)); it ships on, for offline play. Online, Fightcade
runs every game at its own rate, so matches keep it. Fightcade audio crackle: same link.

**Where the 4:3 area comes from.** The cabinet draws 1440x1080 centred on the 1920x1080 panel
([brand/BRAND.md](../brand/BRAND.md)).

[releases]: https://github.com/coin-dojo/coin-dojo/releases/latest
