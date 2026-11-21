# Legal notes in plain language

> **Not legal advice; I'm not a lawyer.** This page explains how the project is set up and points to the sources
> it relies on. It covers **US law only**; other countries (the EU, for example) have their own rules, not covered
> here. If you need a firm answer for your situation, ask a lawyer where you live.

## Summary

- This project ships **method, not content**: scripts, configuration and instructions. The repository holds no
  OS image, no emulator or launcher binaries, no ROMs, no BIOS, no firmware, no game art beyond the one night
  backdrop named below.
- **One exception: the prebuilt community image** on the Releases page (#267), for owners who cannot run the
  build. It holds free and open-source software plus three non-commercial emulator cores, each with its licence
  text and its source ([below](#the-prebuilt-image)). It holds no ROMs, BIOS or game art, and no Fightcade:
  the cabinet downloads that from Fightcade's own site the first time you pick it.
- **One piece of third-party art ships, on purpose.** The night backdrop and its eye-glow overlay (and the boot-menu card crops of it) are official CAPCOM promotional artwork, © CAPCOM CO., LTD. Used as a non-commercial fan tribute without permission or endorsement. This project is not affiliated with Capcom. Removed on request: tell the maintainer through the project's issue tracker and the files go. It is the only such file set (`tests/test_ship_no_artifacts.py` lists it by sha256); the rest of the art is ours. `backdrops/dojo.png` is the original alternative.
- The agent personas are named after fighting-game characters as a fan nod; the project is not affiliated with their owner.
- It runs on hardware **you own**. You put **your own** software on **your own** microSD card. The project
  never writes to the card that came with your cabinet: keep it safe, and you can swap it back.
- When you build it yourself, every third-party program is downloaded from its official source on your machine. Some of them are
  **non-commercial**: never sell, rent out or run paid play on a cabinet set up with them. Details:
  [THIRD_PARTY.md](../THIRD_PARTY.md).
- You bring your own games. **Only use ROM, disc and BIOS dumps you are entitled to**, for example dumps of games
  you own where your law allows that.
- **Free and non-commercial.** This is a free community share: the maintainer sells nothing and nothing is
  behind a paywall. The README has an optional Ko-fi tip link ("tips for tokens": a pun on arcade tokens and
  AI tokens; no token buys any play). Tips are voluntary, support the maintainer's time, unlock nothing, appear nowhere on the cabinet, and
  are not payment for any emulator, core or game. That matches the non-commercial licences of the FBNeo,
  Snes9x and Genesis Plus GX cores the build installs. The repository contains no FBNeo source or
  binary: your machine builds the core from its pinned libretro source ([THIRD_PARTY.md](../THIRD_PARTY.md)). The
  prebuilt image does carry the FBNeo binary, so the image and its download page carry **no tip link** (the
  maintainer, 2026-11-20); only the repository README, where you build it yourself, has one
  ([COMPLIANCE.md](../COMPLIANCE.md#tip-links)).
  Please keep it non-commercial when you share your own version.
- The project is **not affiliated with, endorsed by or sponsored by** Iconic Arcade, Raspberry Pi Ltd, Fightcade,
  or any game publisher. Their names appear only to say what hardware or software this works with.

## Licences

| What | Licence |
|---|---|
| Our code | MIT ([LICENSE](../LICENSE)) |
| Our original art and docs (code blocks in docs: MIT) | CC BY-SA 4.0 ([LICENSE-art](../LICENSE-art)) |
| The night backdrop art (not ours) | Not under our licences: see the summary above and [THIRD_PARTY.md](../THIRD_PARTY.md); removed on request |
| Bundled fonts | SIL Open Font License 1.1, texts next to the fonts |
| Everything the build downloads | Its own licence, listed in [THIRD_PARTY.md](../THIRD_PARTY.md) |

Licence decision (the maintainer, 2026-11-19): code stays MIT, original art and docs stay CC BY-SA 4.0, and
code blocks inside the docs are MIT.

### The prebuilt image

When you download the prebuilt image, you get copies of other people's programs, so their licences travel with
it:

- **What is in it, and under which licence:** `LICENCES.md` on the card's boot partition (readable on any computer
  after flashing), and every licence text in `/usr/share/doc/coindojo/licences/` on the cabinet. The list is
  generated when the image is built, from
  [skills/build-image/licences/components.json](../skills/build-image/licences/components.json).
- **Source code:** the GPL and LGPL programs (RetroArch, Flycast, the GPL cores, the Linux kernel and the Debian
  packages) come with their source: the source of what we build is on the same release page, and `LICENCES.md`
  carries a written offer for the rest, as those licences ask. How we meet each duty:
  [COMPLIANCE.md](../COMPLIANCE.md#6-the-prebuilt-image-source-and-notices).
- **Non-commercial parts:** FBNeo, Snes9x and Genesis Plus GX may be shared for free but never sold or used
  commercially. A free download is fine. Never sell the card, charge for copies of the image, rent the cabinet out
  or run paid play with it.
- **If you pass the image on**, pass it on unchanged and free, with `LICENCES.md`. If you change it and share your
  version, you become its distributor: you then owe the same notices and source yourself.

Until the open blockers in [COMPLIANCE.md](../COMPLIANCE.md#open-blockers-for-a-public-image-2026-11-20-most-serious-first)
are closed, no public image is released.

The short version of each licence: [choosealicense.com](https://choosealicense.com/licenses/),
[Creative Commons BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), and the
[SPDX licence list](https://spdx.org/licenses/) for exact names.

## Your games, BIOS and firmware

- Nothing here downloads or points to games, BIOS files or firmware. You copy your own onto the card's data
  partition.
- Owning a cabinet, a console or a cartridge does not by itself make every download of that game legal. Use dumps
  you made from games you own, or that you are otherwise licensed to use, and check your local law.
- Never share your ROMs, BIOS files or a card image that contains them.

## You own the cabinet: what that means

This section is about a home arcade cabinet that runs on a Raspberry Pi 5 and boots from a microSD card, such as
the Iconic Arcade cabinets. Iconic Arcade built a cabinet the maintainer loves, and this project is an independent,
unofficial way for owners to also run their own software. It is not affiliated with or endorsed by Iconic Arcade,
and nothing here is a comment on their product or their people.

What we checked on one such cabinet (2026): the stock card is a standard, **unencrypted** Raspberry Pi OS card (a FAT boot partition and an ext4 system partition), and the Pi boots a card you built
yourself without any key or signature. The project's tools read the stock card only from **your own backup** of
it, on your own computer.

### Owning the hardware

You bought the cabinet, so the hardware is yours: you can open it, repair it, sell it or give it away. Booting a
different card is using your own computer the way you choose. In most cases this is a question of **ownership**,
not of copyright: swapping a card copies nobody's work and publishes nothing.

### First sale

US copyright law's "first sale" rule ([17 U.S.C. §109](https://www.law.cornell.edu/uscode/text/17/109)) says the
owner of a lawfully made copy may sell or otherwise dispose of that copy without the copyright owner's permission.
It applies only **if you own the copy**: courts have held that software supplied under a licence with real
transfer and use restrictions is licensed, not owned (*Vernor v. Autodesk*, 9th Cir. 2010;
[summary](https://www.fenwick.com/insights/publications/ninth-circuit-rules-on-software-transfers)). So, generally,
the stock card and its software can go with the cabinet if you sell it, unless terms you agreed to say otherwise.
It does not let anyone copy that software or rent it out commercially.

### Backing up the stock card

The tools ask for a backup image of your stock card. For the owner of a copy of a program, US law allows an extra
copy "for archival purposes only" ([17 U.S.C. §117(a)(2)](https://www.law.cornell.edu/uscode/text/17/117)), with
two conditions: destroy the backup if you stop rightfully having the program (for example after selling the
cabinet), and pass it on only together with the original, as part of transferring all your rights. Keep it
private: never share it, it is the vendor's software. Keep the stock card, so the cabinet can always go back to how it
came.

### Right to repair

Right-to-repair laws are about owners being able to fix and modify what they buy. They give useful context, but
they are mostly about access to parts, tools and manuals; no right-to-repair law is needed to boot your own
card. Background:
[EFF on right to repair](https://www.eff.org/issues/right-to-repair).

### DMCA §1201 (anti-circumvention)

[Section 1201](https://www.law.cornell.edu/uscode/text/17/1201) forbids getting around a "technological measure"
that controls access to a copyrighted work; to "circumvent" means "to avoid, bypass, remove, deactivate, or impair"
such a measure without permission. It only matters if something is locked.

- **What we found, on the cabinet we checked:** nothing was locked. Its stock card is not encrypted, and the Pi
  boots your own unsigned card. Swapping an unencrypted card is not circumvention. Later revisions of the
  cabinet may differ: check yours.
- **What the project does not do:** it does not decrypt, crack, patch or bypass any protection. It does not
  touch the stock launcher's account token, update signatures or online service.
- **If that ever changes** (a cabinet that refuses unsigned cards, an encrypted card, a locked bootloader): stop.
  Do not bypass it with this project. Every three years the Librarian of Congress grants temporary exemptions,
  on the US Copyright Office's recommendation; the current list and the rulemaking are on the Office's
  [§1201 page](https://www.copyright.gov/1201/).

Background: [EFF on the DMCA](https://www.eff.org/issues/dmca).

### Fair use: where it applies and where it does not

Fair use is a **defence for using someone else's copyrighted work** without permission, weighed case by case on
four factors (purpose, the nature of the work, how much is used, the effect on the market). See the
[U.S. Copyright Office Fair Use Index](https://www.copyright.gov/fair-use/) and
[EFF on fair use](https://www.eff.org/issues/intellectual-property).

- **Swapping your own card is mostly not a fair-use question.** You are not using anyone's work; you are running
  different software on hardware you own.
- **Where it can matter: interoperability.** To make our setup work like the stock one, we looked at our own
  backup and wrote down **facts**: the stick's USB vendor and product IDs, which button number is which, the screen
  resolution, that audio goes out over HDMI. Facts are not protected by copyright. Courts have also treated
  reverse engineering for compatibility as fair use (for example *Sega v. Accolade* and *Sony v. Connectix*,
  summarised in [EFF's reverse engineering FAQ](https://www.eff.org/issues/coders/reverse-engineering-faq)), and
  §1201(f) has a narrow interoperability exception.
- **Where it does not help:** copying someone's launcher, artwork, scripts or config files into a public project;
  downloading games you have no right to; breaking a contract you agreed to (the same FAQ notes that a licence's
  no-reverse-engineering clause can override fair use).

### Contracts and accounts

Terms you accepted for an online account or service still apply to that service. This project does not use the
stock launcher's online services and never needs their account. If your cabinet came with written terms, read
them; we have not reviewed any.

### Warranty

Under the US Magnuson-Moss Warranty Act, a warrantor generally may not make your warranty depend on using only its
own parts or services, but it may refuse to cover damage caused by something it did not provide. See the FTC's
[guide to federal warranty law](https://www.ftc.gov/business-guidance/resources/businesspersons-guide-federal-warranty-law).
In practice: this project never opens the case or changes hardware, and the stock card stays as it was. Before a
warranty claim, put the stock card back.

## Names and trademarks

- Iconic Arcade, Raspberry Pi, Fightcade and every game title or company named in this project belong to their
  owners. They are used only to say what this works with ("for a cabinet built on a Raspberry Pi 5").
- "Raspberry Pi is a trademark of Raspberry Pi Ltd." Its
  [trademark rules](https://www.raspberrypi.com/trademark-rules/) do not allow "Raspberry Pi" in the name of
  your product or image; wording like "runs on" or "for use with" is allowed.
- If you publish your own build or fork, name it something of your own, and do not use another company's logo or
  imply that they made or approved it.

## Further reading

- U.S. Copyright Office, [Fair Use Index](https://www.copyright.gov/fair-use/)
- U.S. Copyright Office (Library of Congress), [Section 1201 rulemaking and exemptions](https://www.copyright.gov/1201/)
- EFF (a digital-rights advocacy group, so its pages argue a position): [fair use](https://www.eff.org/issues/intellectual-property), [DMCA](https://www.eff.org/issues/dmca),
  [right to repair](https://www.eff.org/issues/right-to-repair),
  [reverse engineering FAQ](https://www.eff.org/issues/coders/reverse-engineering-faq)
- [Raspberry Pi trademark rules](https://www.raspberrypi.com/trademark-rules/)
- [choosealicense.com](https://choosealicense.com/licenses/), [SPDX licence list](https://spdx.org/licenses/),
  [Creative Commons BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)
- Fightcade: fightcade.com publishes no terms page that we could find (checked 2026-11-19); any terms the app shows
  when you sign up apply to your account. The licences of its emulators are on its [about page](https://www.fightcade.com/about).

Not legal advice; I'm not a lawyer.
