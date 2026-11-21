#!/usr/bin/env python3
"""The Coin Dojo instruction booklet (#272): a retro game-box manual of the setup guide, as one self-contained HTML
file (fonts and pictures embedded, print CSS for A5 pages or US Letter) plus one PNG per page for the README.

    python3 docs/assets/gen_manual.py                build docs/manual/coin-dojo-manual.html (stdlib, deterministic)
    ~/.venvs/badges/bin/python docs/assets/gen_manual.py capture    re-render the game-menu screenshots (heavy)
    ~/.venvs/badges/bin/python docs/assets/gen_manual.py capture-settings   the SETTINGS screens (pages 13-15)
    python3 docs/assets/gen_manual.py pages          render docs/manual/pages/NN-name.png with headless Chrome

One content source: the setup steps are READ from docs/START.md (the plain guide stays the source; the booklet is
another presentation of it), so the two cannot drift (docs/tests/test_manual.py checks every step's text is in the
booklet; paper() turns its links to other guides into page numbers). The rest of the words live in the
constants and build_pages() below. Colours and fonts come from brand/tokens.json; the logo is
brand/logo-icon-512.png; the controller cards are the theme's own (themes/<theme>/_inc/controls, which
skills/controls draws from its LAYOUTS); the cover art is the theme's shipped backdrop, credited on the back page.
Screens: docs/assets/features/ (#264 capture) and docs/manual/src/ (`capture`: the community edition's starter
carousel and a game list from the preview fixture, which is what a new owner sees: no curated lists).
"""
import base64
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "brand"))
import brand  # noqa: E402

START = os.path.join(REPO, "docs", "START.md")
MANUAL = os.path.join(REPO, "docs", "manual")
OUT = os.path.join(MANUAL, "coin-dojo-manual.html")
SRC = os.path.join(MANUAL, "src")
PAGES_DIR = os.path.join(MANUAL, "pages")
FEATURES = os.path.join(HERE, "features")
NAME = "COIN DOJO"
TAGLINE = "FOR ICONIC ARCADE CABINETS (RASPBERRY PI 5)"
ICONIC = "Configured and tested on Iconic Arcade cabinet hardware. Not affiliated with or endorsed by Iconic Arcade."
MAX_BYTES = 4500 * 1000        # under the 5 MB ship-no-artifacts limit, with room
SHOT_SCALE = 1.5               # page PNGs: 148 x 210 mm at 96 dpi x 1.5 = 840 x 1191 px
STEP_RX = re.compile(r"^(\d+)\. \*\*(.+?)\*\*\s*(.*)$")


# ---------------------------------------------------------------- the one content source: docs/START.md
def parse_start(path=START):
    """-> [dict(n, title, lines)]: each numbered step of START.md, its bold title and its markdown lines."""
    steps, cur = [], None
    for line in open(path, encoding="utf-8").read().splitlines():
        m = STEP_RX.match(line)
        if m:
            cur = dict(n=int(m.group(1)), title=m.group(2).rstrip("."), lines=[m.group(3)])
            steps.append(cur)
        elif cur is not None and (line.startswith("   ") or not line.strip()):
            cur["lines"].append(line.strip())
        elif cur is not None:
            cur = None
    return steps


def plain(md):
    """Markdown inline -> plain words (for the tests): links keep their text, ** and ` go."""
    md = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", md)
    md = re.sub(r"<(https?://[^>]+)>", r"\1", md)
    return " ".join(md.replace("**", "").replace("`", "").split())


def inline(md):
    """Markdown inline -> HTML: bold, code, links shown as their text (the booklet has no clickable repo links)."""
    out, i = [], 0
    for m in re.finditer(r"\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\([^)]+\)|<(https?://[^>]+)>", md):
        out.append(html.escape(md[i:m.start()]))
        if m.group(1) is not None:
            out.append("<b>%s</b>" % html.escape(m.group(1)))
        elif m.group(2) is not None:
            out.append("<code>%s</code>" % html.escape(m.group(2)))
        elif m.group(3) is not None:
            out.append('<span class="ref">%s</span>' % html.escape(m.group(3)))
        else:
            out.append('<span class="ref">%s</span>' % html.escape(m.group(4)))
        i = m.end()
    out.append(html.escape(md[i:]))
    return "".join(out)


SUB_RX = re.compile(r"^(- |\d+\. )")


def step_html(step):
    """One START.md step: paragraphs, `- ` bullet lists and `1. ` sub-step lists, in reading order."""
    paras, cur, items, kind = [], [], [], None
    for ln in [paper(x) for x in step["lines"]] + [""]:
        m = SUB_RX.match(ln)
        if m:
            k = "ul" if m.group(1) == "- " else "ol"
            if cur:
                paras.append(("p", " ".join(cur)))
                cur = []
            if items and k != kind:
                paras.append((kind, items))
                items = []
            kind = k
            items.append(ln[m.end():])
        elif not ln:
            if cur:
                paras.append(("p", " ".join(cur)))
                cur = []
            if items:
                paras.append((kind, items))
                items = []
        else:
            if items and cur == []:
                items[-1] += " " + ln
            else:
                cur.append(ln)
    if items:
        paras.append((kind, items))
    body = []
    for k, val in paras:
        if k == "p":
            body.append("<p>%s</p>" % inline(val))
        else:
            body.append("<%s>%s</%s>" % (k, "".join("<li>%s</li>" % inline(x) for x in val), k))
    return ('<div class="step"><div class="num">%d</div><div class="sbody"><h3>%s</h3>%s</div></div>'
            % (step["n"], html.escape(step["title"].upper()), "".join(body)))


def split_steps(steps):
    """The setup steps over two facing pages: the split that balances their text best."""
    size = [len(" ".join(s["lines"])) + 120 for s in steps]
    best = min(range(1, len(steps)), key=lambda k: abs(sum(size[:k]) - sum(size[k:]))) if len(steps) > 1 else 1
    return steps[:best], steps[best:]


def games_step(steps):
    """The number of START.md's 'add your games' step (the network folder), for page references."""
    return next((s["n"] for s in steps if "games" in s["title"].lower()), 4)


# ---------------------------------------------------------------- assets
def theme_dir(b):
    return os.path.join(REPO, os.path.dirname(os.path.dirname(b["fonts"]["dir"])))


def data_uri(path):
    ext = os.path.splitext(path)[1].lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".ttf": "font/ttf"}[ext]
    with open(path, "rb") as f:
        return "data:%s;base64,%s" % (mime, base64.b64encode(f.read()).decode("ascii"))


def controller_cards(b):
    """[(layout, title, used-by, tested, png path)] from skills/controls (the source of truth) and the theme's cards."""
    sys.path.insert(0, os.path.join(REPO, "skills", "controls"))
    import controls
    order = ["arcade6", "fbneo", "neogeo", "genesis6", "snes", "nes", "pcengine", "psx", "saturn", "n64", "dreamcast",
             "atari", "twinstick"]
    out = []
    for lay in order + sorted(set(controls.LAYOUTS) - set(order)):
        spec = controls.LAYOUTS[lay]
        png = os.path.join(theme_dir(b), "_inc", "controls", lay + ".png")
        if not os.path.isfile(png):
            continue
        systems = [controls.SYSTEM_NAME.get(s, spec["title"]) for s, l in sorted(controls.SYSTEM_LAYOUT.items())
                   if l == lay]
        used = ", ".join(dict.fromkeys(systems)) if systems else "selected twin-stick games"
        out.append((lay, spec["title"], used, spec.get("tested", True), png))
    return out


# ---------------------------------------------------------------- paper: START.md words as a printed page reads them
# On paper a file name is a dead end (Everyday Owner review #282, finding 9) and a "TBD" looks broken (finding 2). paper() turns
# START.md's links to other guides into booklet page references and its release-page placeholder into the words the
# booklet prints. It is the ONLY change to START.md's text: docs/tests/test_manual.py compares START.md through
# paper(), so a new sentence in START.md still has to reach the booklet.
PAPER_REFS = {"ROMS.md": "the folder table on page 5", "FIGHTCADE.md": "page 11", "SCRAPING.md": "page 12",
              "START.md": "page 3", "FAQ.md": "the project's page", "BUILD.md": "the project's page"}
RELEASES = "the project's Releases page"


def paper(md):
    md = re.sub(r"\[([^\]]+)\]\[[^\]]*\]", r"\1", md)          # reference-style link: its text
    md = re.sub(r"(?:the )?release page:?\s*TBD\s*\(#\d+\)", RELEASES, md, flags=re.I)
    return re.sub(r"\[[^\]]*\]\(([^)#\s]+\.md)(?:#[^)]*)?\)",
                  lambda m: PAPER_REFS.get(os.path.basename(m.group(1)), "the project's page"), md)


# ---------------------------------------------------------------- pages
def callouts(img, marks, cls="shot"):
    """A screenshot with numbered gold markers at (x %, y %)."""
    dots = "".join('<span class="mark" style="left:%s%%;top:%s%%">%d</span>' % (x, y, n) for n, (x, y) in marks)
    return '<div class="%s callouts"><img src="%s" alt="">%s</div>' % (cls, img, dots)


def combo(keys):
    parts = keys.split("+")
    return '<span class="combo">%s</span>' % '<i>+</i>'.join('<span class="pill">%s</span>' % html.escape(k) for k in parts)


def keytable(rows, cls="combos"):
    """[(keys, words)] -> a two-column table; keys 'A / B' show as two pills."""
    def cell(k):
        return " / ".join(combo(x.strip()) for x in k.split("/"))
    return '<table class="%s">%s</table>' % (cls, "".join("<tr><td>%s</td><td>%s</td></tr>" % (cell(k), v)
                                                         for k, v in rows))


def numbered(items, cls="mini"):
    return '<ol class="%s">%s</ol>' % (cls, "".join("<li>%s</li>" % x for x in items))


COMBOS = [("SELECT+START", "Leave a game (in Fightcade: hold it 2 seconds)"),
          ("START+MP", "The game's own settings menu: save, load, picture"),
          ("START+HP", "The controls card for this game, the game paused"),
          ("SELECT+LK", "On-screen keyboard in the Fightcade lobby"),
          ("SELECT+MK", "Stick pointer on or off in Fightcade"),
          ("LP", "TROPHIES, on the game menu")]

# navigation shortcuts (ES-DE's own keys on this panel: LK=a MK=b MP=x LP=y HP=left shoulder HK=right shoulder,
# SELECT=back; skills/badges/gen_help.py has the map)
NAV_ROW = [("STICK", "Turn the row of systems"), ("LK", "Open the system in the middle"),
           ("START", "The main menu (the scraper is in it, page 12)"), ("LP", "TROPHIES (page 15)")]
NAV_LIST = [("STICK", "Up and down: one game. Left and right: the next system"),
            ("HP / HK", "Jump 10 games up / down"),
            ("LK", "Play the game"), ("MK", "Back to the row of systems"),
            ("MP", "Look at the game's pictures and video (after scraping)"),
            ("SELECT", "Options: sort the list, or jump to a letter")]

POWER_OFF = ["Leave your game first: <b>SELECT+START</b>.",
             "Press the cabinet's <b>power button</b> once. <b>SHUT DOWN?</b> appears.",
             "Press it again within 8 seconds. Do nothing, and it cancels.",
             "Wait until the screen says <b>NO SIGNAL</b>. Now you can switch off at the wall."]

# the network folder's system folders (docs/ROMS.md "Systems"), as an owner sees them in Finder or File Explorer
FOLDERS = [("Arcade (MAME)", "arcade"), ("Arcade (FBNeo)", "fbneo"), ("CP System 1, 2, 3 (CPS)", "cps1 cps2 cps3"),
           ("Neo Geo", "neogeo"), ("Sega NAOMI", "naomi"), ("Sega Dreamcast", "dreamcast"),
           ("Atari 2600", "atari2600"), ("NES / Famicom", "nes"), ("Super Nintendo", "snes"),
           ("Sega Genesis / Mega Drive", "genesis"), ("Sega CD / Mega-CD", "segacd"),
           ("PC Engine / TurboGrafx-16", "pcengine"), ("Nintendo 64", "n64"), ("PlayStation", "psx"),
           ("Sega Saturn", "saturn")]
BIOS = [("Neo Geo", "neogeo.zip", "roms/neogeo"),
        ("PlayStation", "one scph file, e.g. scph5501.bin", "bios"),
        ("Sega Saturn", "sega_101.bin (Japan), mpr-17933.bin (US, Europe)", "bios"),
        ("Sega CD", "bios_CD_U.bin (US), _E (Europe), _J (Japan)", "bios"),
        ("PC Engine CD games", "syscard3.pce", "bios"),
        ("Dreamcast", "dc_boot.bin and dc_flash.bin", "bios/dc"),
        ("Sega NAOMI", "naomi.zip", "bios/dc")]

SCRAPE = ["Make a free account at <b>screenscraper.fr</b> on your computer or phone.",
          "On the game menu, press <b>START</b>. The main menu opens.",
          "Pick <b>SCRAPER</b>.",
          "Set <b>SCRAPE FROM</b> to <b>SCREENSCRAPER</b>.",
          "Open <b>ACCOUNT SETTINGS</b>.",
          "Type your ScreenScraper user name (stick to move, LK to type).",
          "Type your ScreenScraper password.",
          "Make sure <b>USE THIS ACCOUNT FOR SCREENSCRAPER</b> is on. Press MK to go back.",
          "Open <b>SCRAPE THESE SYSTEMS</b> and pick your systems.",
          "Pick <b>START</b> at the bottom of the SCRAPER menu, and wait."]

# the SETTINGS home in the app's order: skills/settings/settings_core.py HOME_ORDER (#285), then BACK.
# docs/tests/test_manual.py checks this list against HOME_ORDER.
SETTINGS_IDS = {"wifi": "WI-FI", "name": "CABINET NAME", "pw": "PASSWORD", "lists": "GAME LISTS",
                "attract": "ATTRACT MODE", "bench": "SHOW FPS", "cheevos": "RETROACHIEVEMENTS", "tz": "TIME ZONE"}
SETTINGS_ORDER = [("WI-FI", "Join your home Wi-Fi: pick your network, type its password."),
                  ("CABINET NAME", "The name your computer sees on the home network."),
                  ("PASSWORD", "Change the password 12345. Page 14."),
                  ("GAME LISTS", "Make MUST PLAY and the other lists again. Page 14."),
                  ("ATTRACT MODE", "The show the cabinet puts on when nobody plays. Page 15."),
                  ("SHOW FPS", "Show the game's speed on screen. Page 15."),
                  ("RETROACHIEVEMENTS", "Trophies for classic games. Page 15."),
                  ("TIME ZONE", "Your clock. Page 15."),
                  ("BACK", "Leave SETTINGS.")]

PRECAUTIONS = [
    "This is a hobby project, so things can go wrong. Use a spare SD card, not one with photos or anything you need.",
    "Use only games you copied yourself from cartridges or discs you own. No games come with Coin Dojo.",
    "Fightcade is made by other people and has its own rules. You sign in with your own account.",
    "Online play: a network cable beats Wi-Fi.",
    "Switch off with the power button: press it, then press it again (page 7). Don't pull the plug while a game "
    "is saving.",
]

BACK_SHOTS = [("ONLINE VERSUS", "fightcade-macros.png"), ("TROPHIES", "trophies.png"), ("CRT LOOK", "src:crt.jpg"),
              ("BOOT MENU", "boot-arcade.png")]


def page(n, cls, body, title=None, sub=None):
    head = ""
    if title:
        head = ('<header><span class="pno">%d</span><div><h2>%s</h2><div class="sub">%s</div></div></header>'
                % (n, html.escape(title), html.escape(sub or "")))
    foot = ('<footer><span>%s &nbsp;-&nbsp; INSTRUCTION BOOKLET</span><span class="px">- %d -</span></footer>'
            % (NAME, n)) if n > 1 else ""
    return '<section class="page %s" id="p%d">%s<div class="content">%s</div>%s</section>' % (cls, n, head, body, foot)


CONTENTS = [("Precautions", 2), ("Setup: from download to your first game", 3), ("Adding games: folders and BIOS", 5),
            ("The game menu", 6), ("Getting around, switching off", 7), ("How to play: the control panel", 8),
            ("Controller layouts", 9), ("Online play", 11), ("Pictures and game info", 12), ("Settings", 13),
            ("Password and game lists", 14), ("Attract mode, FPS, trophies, time", 15), ("Credits", 16)]


def build_pages(b, steps):
    th = theme_dir(b)
    img = lambda p: data_uri(p)  # noqa: E731
    feat = lambda f: img(os.path.join(SRC, f[4:]) if f.startswith("src:") else os.path.join(FEATURES, f))  # noqa: E731
    src = lambda f: img(os.path.join(SRC, f))  # noqa: E731
    logo = img(os.path.join(REPO, "brand", "logo-icon-512.png"))
    first, rest = split_steps(steps)
    gs = games_step(steps)
    gpage = 3 if any(x["n"] == gs for x in first) else 4
    pages = []
    # 1 cover
    pages.append(page(1, "cover", (
        '<div class="frame"><div class="logo"><img src="%s" alt=""><span class="wordmark">%s</span></div>'
        '<div class="tag">%s</div>'
        '<div class="art"><img src="%s" alt=""><span class="badge gold">ONLINE VERSUS</span>'
        '<span class="badge red">1-2 PLAYERS</span></div>'
        '<div class="booklet">INSTRUCTION BOOKLET</div>'
        '<div class="warn"><span class="wlabel">WARNING: READ BEFORE USING</span>'
        '<p>Please read this booklet before you switch the cabinet on.</p>'
        '<p>Use only games you copied yourself from cartridges or discs you own.</p>'
        '<p>Keep it for later: every combo is on page 8.</p></div>'
        '<div class="small">BRING YOUR OWN GAMES &nbsp;-&nbsp; NO GAMES INCLUDED</div></div>')
        % (logo, NAME, TAGLINE, img(os.path.join(th, "_inc", "art", "backdrops", "default.png")))))
    # 2 contents + precautions
    pages.append(page(2, "", (
        '<p class="lead">Thank you for choosing %s. Please read this booklet before you play, and keep it for '
        'later.</p><h3 class="gold">CONTENTS</h3><ol class="toc">%s</ol>'
        '<div class="box"><h3 class="red">PRECAUTIONS</h3><ul>%s</ul></div>')
        % ("Coin Dojo", "".join('<li><span>%s</span><span class="dots"></span><span>%d</span></li>'
                                % (html.escape(t), p) for t, p in CONTENTS),
           "".join("<li>%s</li>" % html.escape(x) for x in PRECAUTIONS)), "WELCOME", "CONTENTS AND PRECAUTIONS"))
    # 3-4 setup: every START.md step, on two facing pages (Everyday Owner #16: the steps stay together)
    rng = lambda ss: "STEPS %d TO %d" % (ss[0]["n"], ss[-1]["n"]) if len(ss) > 1 else "STEP %d" % ss[0]["n"]  # noqa: E731
    glossed = "start-up file" in " ".join(" ".join(x["lines"]) for x in steps)
    pages.append(page(3, "steps", "".join(step_html(s) for s in first), "SETUP", rng(first)))
    pages.append(page(4, "steps", "".join(step_html(s) for s in rest) + ("" if glossed else (
        '<p class="note">New words: a <b>BIOS</b> is a small start-up file copied from a console. "<b>Dumped</b>" means '
        'copied yourself from a cartridge, disc or console you own. Which systems need a BIOS: page 5.</p>')),
        "SETUP", rng(rest)))
    # 5 adding games: the folder table + BIOS
    half = (len(FOLDERS) + 1) // 2
    ftab = lambda rows: '<table class="folders">%s</table>' % "".join(  # noqa: E731
        "<tr><td>%s</td><td><code>%s</code></td></tr>" % (html.escape(s), "</code> <code>".join(f.split()))
        for s, f in rows)
    pages.append(page(5, "games", (
        '<p class="lead">Open the network folder (step %d, page %d), then <b>roms</b>. Each system has its own '
        'folder. Drag each game into its system\'s folder.</p><div class="cols">%s%s</div>'
        '<p class="note">Not sure? Use the folder whose name looks closest. The game menu only shows systems that '
        'have games. Arcade games: keep the zip file as it is. Don\'t rename it or unzip it.</p>'
        '<div class="box"><h3 class="gold2">WHAT IS A BIOS?</h3><p>A small start-up file copied from the console '
        'itself. Only a few systems need one. No BIOS, no problem for the others. Drag it into the folder named '
        'here (for <b>bios/dc</b>, make a folder named <b>dc</b> inside bios).</p>'
        '<table class="bios">%s</table></div>')
        % (gs, gpage, ftab(FOLDERS[:half]), ftab(FOLDERS[half:]),
           "".join("<tr><td>%s</td><td>%s</td><td><code>%s</code></td></tr>" % (html.escape(s), html.escape(f),
                                                                              html.escape(w)) for s, f, w in BIOS)),
        "ADDING GAMES", "WHICH FOLDER EACH GAME GOES IN"))
    # 6 the game menu
    menu = (callouts(src("menu-carousel.jpg"), [(1, (38, 44)), (2, (36, 62)), (3, (86, 90))])
            + callouts(src("menu-list.jpg"), [(4, (45, 79)), (5, (33, 93))])
            + '<ol class="legend"><li><b>The system</b> in the middle. The stick turns the row of systems. SETTINGS '
              'and QUIT TO MAIN MENU are in the row too.</li><li><b>How many games</b> it has. Only systems you added '
              'games to show up.</li><li><b>LP opens TROPHIES</b>, your RetroAchievements.</li><li><b>The combos '
              'bar</b>: the combos that work in a game (SELECT+START leaves it).</li><li><b>The help bar</b>: what '
              'each button does on this screen.</li></ol>')
    pages.append(page(6, "menu", menu, "THE GAME MENU", "THE ROW OF SYSTEMS AND A GAME LIST"))
    # 7 getting around + switching off
    pages.append(page(7, "nav", (
        '<h3 class="gold">ON THE ROW OF SYSTEMS</h3>%s<h3 class="gold">IN A GAME LIST</h3>%s'
        '<div class="box"><h3 class="red">SWITCHING OFF</h3>%s</div>')
        % (keytable(NAV_ROW), keytable(NAV_LIST), numbered(POWER_OFF)), "GETTING AROUND", "SHORTCUTS AND SWITCHING OFF"))
    # 8 how to play: the panel + combos
    btn = lambda t: '<span class="btn">%s</span>' % t  # noqa: E731
    panel = ('<div class="panel"><div class="stick"><span class="ball"></span><span class="rod"></span>'
             '<span class="base"></span></div><div class="rows"><div>%s%s%s</div><div>%s%s%s</div></div>'
             '<div class="pills"><span class="pill">START</span><span class="pill">SELECT</span></div></div>'
             % tuple(btn(x) for x in ("LP", "MP", "HP", "LK", "MK", "HK")))
    keys = ('<ul class="keys"><li><b>STICK</b> move</li><li><b>LK</b> select, OK</li><li><b>MK</b> back</li>'
            '<li><b>START</b> done</li><li><b>SELECT</b> quit an app</li></ul>')
    table = keytable([(k, html.escape(v)) for k, v in COMBOS])
    pages.append(page(8, "play", panel + keys + '<h3 class="gold">COMBOS</h3>' + table, "HOW TO PLAY",
                      "THE CONTROL PANEL"))
    # 9-10 controller layouts
    cards = controller_cards(b)
    half = (len(cards) + 1) // 2
    for k, chunk in ((9, cards[:half]), (10, cards[half:])):
        grid = "".join('<figure><img src="%s" alt=""><figcaption>%s%s</figcaption></figure>'
                       % (img(p), html.escape(used), "" if tested else " <em>(not tested on the panel yet)</em>")
                       for _l, _t, used, tested, p in chunk)
        intro = ('<p class="lead">The same six buttons play every system. Each system gets the layout its games '
                 'expect. START+HP shows the card in a game.</p>' if k == 9 else "")
        pages.append(page(k, "layouts", intro + '<div class="grid">%s</div>' % grid, "CONTROLLER LAYOUTS",
                          "PAGE %d OF 2" % (k - 8)))
    # 11 online play
    pages.append(page(11, "online", (
        '<p class="lead">Fightcade lets you play arcade fighting games online against real people. It hides most of '
        'the delay, so a match with a nearby player feels like playing side by side.</p>%s'
        '<div class="shot"><img src="%s" alt=""></div>%s'
        '<div class="box"><h3 class="gold2">FULL GUIDE</h3><p>Signing in, chat shortcuts and which game files '
        'work: the <b>Online play (Fightcade)</b> guide on the project\'s page. Fightcade\'s own help and free '
        'accounts: <b>www.fightcade.com</b>.</p><p>Fightcade\'s own game files go in the <b>fightcade</b> folder '
        'inside <b>roms</b>.</p><p>Matches never start? Your router may block them. Ask whoever set '
        'up your internet: "please turn on UPnP for my arcade cabinet".</p></div>')
        % (numbered(["On the boot menu (the two big cards), pick <b>FIGHTCADE</b>.",
                     "The first time, it downloads Fightcade. Wait a few minutes. It needs internet.",
                     "Sign in with your Fightcade account. <b>SELECT+LK</b> opens the keyboard.",
                     "Pick a game room and challenge a player."]),
           feat("fightcade-keyboard.png"),
           keytable([("SELECT+LK", "Open the on-screen keyboard"), ("SELECT+MK", "Stick pointer on or off"),
                     ("SELECT+START", "Hold 2 seconds: leave the game, back to the lobby")])),
        "ONLINE PLAY", "FIGHTCADE ON YOUR CABINET"))
    # 12 scraping
    pages.append(page(12, "scrape", (
        '<p class="lead">The cabinet can fetch box art, screenshots, videos and game info by itself. This is called '
        '<b>scraping</b>. You need internet. A few games work without an account; for a big library, make a free '
        'ScreenScraper account (a bigger daily limit, faster). A big library takes hours.</p>%s'
        '<p class="note">Leave the cabinet on until it says it is done. If it stops, start it again. The pictures '
        'and info stay on the cabinet. They also fill MUST PLAY and the genre lists: after scraping, pick SETTINGS, '
        'GAME LISTS, REBUILD GAME LISTS (page 14).</p>'
        '<div class="box"><h3 class="red">YOUR SCREENSCRAPER PASSWORD</h3><p>The cabinet keeps it unprotected. Use '
        'a password you don\'t use anywhere else.</p></div>') % numbered(SCRAPE),
        "PICTURES AND GAME INFO", "SCRAPING: BOX ART, VIDEOS, RATINGS"))
    # 13 settings home
    pages.append(page(13, "settings", (
        '<p class="lead">SETTINGS is in the row of systems. Turn the stick until SETTINGS is in the middle and press '
        '<b>LK</b>.</p><div class="shot half"><img src="%s" alt=""></div>'
        '<table class="order">%s</table>%s')
        % (src("settings-home.jpg"),
           "".join("<tr><td>%s</td><td>%s</td></tr>" % (html.escape(t), html.escape(v)) for t, v in SETTINGS_ORDER),
           keytable([("STICK", "Move"), ("LK", "Select, type a key"), ("MK", "Back"),
                     ("SELECT", "Leave SETTINGS, from any page")])),
        "SETTINGS", "EVERYTHING ON THE STICK"))
    # 14 password + game lists
    pages.append(page(14, "settings", (
        '<div class="two"><div class="shot"><img src="%s" alt=""></div><div>'
        '<h3 class="gold2">PASSWORD</h3><p>Every cabinet starts with the password <b>12345</b>. Change it:</p>%s</div>'
        '</div><p class="note">The new password works for the login and the network folder.</p>'
        '<div class="two"><div class="shot"><img src="%s" alt=""></div><div><h3 class="gold2">GAME LISTS</h3>'
        '<p>MUST PLAY and the genre lists come from the ratings that scraping finds (page 12).</p></div></div>%s')
        % (src("settings-password.jpg"),
           numbered(["Pick <b>PASSWORD</b>, then <b>CHANGE PASSWORD</b>.",
                     "Type a new password with the stick: 6 characters or more, not 12345.",
                     "Type it a second time, the same, and pick <b>DONE</b>."]),
           src("settings-lists.jpg"),
           keytable([("REBUILD", "<b>REBUILD GAME LISTS</b>: make the lists again after you add or scrape games. "
                                 "Then pick YES to restart the game menu."),
                     ("SIZE", "<b>MUST PLAY SIZE</b>: how many games per system: 10, 15, 25 or 50."),
                     ("STARS", "<b>MUST PLAY GENRES MIN STARS</b>: the lowest rating a game needs for the genre "
                               "lists: 4.0, 4.5 or 5.0 stars.")], cls="combos wide")),
        "SETTINGS", "PASSWORD AND GAME LISTS"))
    # 15 attract mode, show fps, retroachievements, time zone
    pages.append(page(15, "settings", (
        '<div class="two"><div class="shot"><img src="%s" alt=""></div><div><h3 class="gold2">ATTRACT MODE</h3>'
        '<p>When nobody plays for a while, the cabinet puts on a show. Any button ends it.</p></div></div>%s'
        '<h3 class="gold2">SHOW FPS</h3><p>Shows the game\'s speed (frames per second) in a corner. <b>TURN ON</b> or '
        '<b>TURN OFF</b>. Leave it off for normal play.</p>'
        '<h3 class="gold2">RETROACHIEVEMENTS</h3><p>Earn trophies in classic games with a free account from '
        '<b>retroachievements.org</b>. <b>LOG IN</b> with the stick keyboard, then <b>TURN ON</b>. <b>HARDCORE</b> '
        'turns off saving and loading in a game, for real bragging rights. Tap <b>LP</b> on the game menu to see '
        'your TROPHIES.</p>'
        '<h3 class="gold2">TIME ZONE</h3><p>Pick yours from the short list, or <b>MORE ZONES</b> for every region and '
        'city.</p>')
        % (src("settings-attract.jpg"),
           keytable([("ON / OFF", "<b>TURN ON</b> or <b>TURN OFF</b>."),
                     ("AFTER", "<b>START AFTER</b>: 5, 10, 30 or 60 minutes without play."),
                     ("SHOW", "<b>SHOW</b>: VIDEO (game videos), SLIDESHOW (pictures), DIM (a darker screen) or "
                              "BLACK (a blank screen)."),
                     ("APPLY", "<b>APPLY NOW</b> shows up after a change: use it right away. The game menu restarts.")],
                    cls="combos wide")),
        "SETTINGS", "ATTRACT MODE, FPS, TROPHIES, TIME"))
    # 16 back page: back-of-the-box shots, credits
    shots = "".join('<figure><img src="%s" alt=""><figcaption>%s</figcaption></figure>' % (feat(f), html.escape(t))
                    for t, f in BACK_SHOTS)
    pages.append(page(16, "back", (
        '<div class="backlogo"><img src="%s" alt=""><span class="wordmark">%s</span><div class="tag">%s</div></div>'
        '<div class="backshots">%s</div><p class="iconic">%s</p>'
        '<div class="box"><h3 class="gold">CREDITS</h3><p>Coin Dojo is free and non-commercial: MIT for the code, '
        'CC BY-SA 4.0 for our art and docs, the fonts under the SIL Open Font License. Emulators and Fightcade come '
        'from their own projects.</p><p>The cover art is the theme\'s night backdrop: third-party art, credited on '
        'the project\'s page and removed on request. Everything else in this booklet is original.</p></div>'
        '<div class="small">INSERT YOUR OWN GAMES</div>')
        % (logo, NAME, TAGLINE, shots, html.escape(ICONIC))))
    assert len(pages) == PAGE_COUNT, len(pages)
    return pages


PAGE_COUNT = 16
PAGE_NAMES = ["cover", "contents", "setup-1", "setup-2", "adding-games", "game-menu", "getting-around",
              "how-to-play", "layouts-1", "layouts-2", "online", "scraping", "settings", "password-lists",
              "attract-more", "back"]


# ---------------------------------------------------------------- CSS + document
def css(b):
    fonts = {"display": "Display", "ui_bold": "UIBold", "pixel": "Pixel"}   # no ui_semi: the 5 MB file budget
    faces = "".join("@font-face{font-family:%s;src:url(%s) format('truetype');}" % (fam, data_uri(b.font(role)))
                    for role, fam in fonts.items())
    var = "".join("--%s:#%s;" % (k.replace("_", "-"), b.hex(k)) for k in sorted(b["colors"]))
    stops = ",".join("#%02X%02X%02X %d%%" % (c[0], c[1], c[2], round(p * 100)) for p, c in b.stops("badge"))
    return faces + ":root{" + var + "--grad:linear-gradient(180deg," + stops + ");}" + CSS


CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html{background:var(--bg-deep)}
body{font-family:UIBold,sans-serif;color:var(--text);background:var(--bg-deep);font-size:10.5pt;line-height:1.3}
.bar{font-family:UIBold;color:var(--muted);padding:8px 14px;text-align:center}
.bar a{color:var(--gold);margin:0 8px}
.page{width:148mm;height:210mm;margin:10px auto;position:relative;overflow:hidden;
  background:var(--bg);background-image:repeating-linear-gradient(0deg,rgba(0,0,0,.18) 0 1px,transparent 1px 3px)}
.single .bar{display:none}.single .page{display:none;margin:0}.single .page.on{display:block}
.page header{display:flex;gap:4mm;align-items:center;background:var(--panel);border-bottom:2mm solid var(--gold);
  padding:4mm 7mm;height:26mm}
.pno{font-family:Display;font-size:34pt;color:var(--gold)}
h2{font-family:Display;font-size:20pt;letter-spacing:.5pt}
.sub{font-family:UIBold;font-size:9.5pt;color:var(--gold-muted);letter-spacing:.5pt}
h3{font-family:UIBold;font-size:11.5pt;letter-spacing:.4pt}
h3.gold{color:var(--gold);font-family:Display;font-size:14pt;margin:4mm 0 2mm}
h3.red{color:var(--red-light)}
.content{padding:5mm 7mm 0}
footer{position:absolute;left:7mm;right:7mm;bottom:5mm;border-top:.3mm solid var(--dim);padding-top:2mm;
  display:flex;justify-content:space-between;font-family:UIBold;font-size:7.5pt;color:var(--dimmed)}
.px{font-family:Pixel;font-size:6.5pt}
code{font-family:UIBold;color:var(--gold);background:var(--card);padding:0 1mm;border-radius:1mm}
b{font-family:UIBold;color:var(--text)}
.ref{color:var(--lavender)}
.lead{color:var(--text-soft);margin-bottom:3mm}
.box{background:var(--card);border:.5mm solid var(--dim);border-radius:3mm;padding:3mm 4mm;margin-top:4mm}
.box ul{margin-left:4mm}.box li{margin:1mm 0}
.small{font-family:UIBold;font-size:8pt;color:var(--gold-muted);text-align:center;letter-spacing:.6pt}
/* steps */
.step{display:flex;gap:3mm;margin-bottom:3.2mm}
.num{flex:0 0 9mm;height:9mm;border-radius:50%;background:var(--gold);color:var(--on-gold);font-family:Display;
  font-size:13pt;display:flex;align-items:center;justify-content:center}
.sbody h3{color:var(--gold);margin:.8mm 0 .8mm}
.sbody p{margin-bottom:1mm}.sbody ul{margin:0 0 1mm 4mm}
.sbody ol{margin:0 0 1mm 5mm}.sbody ol li{margin:.3mm 0}.sbody ol li::marker{color:var(--gold)}
/* cover */
.cover{background:linear-gradient(180deg,var(--panel),var(--bg-deep))}
.cover .content{padding:0;height:100%}
.frame{position:absolute;inset:5mm;border:1.4mm solid var(--gold);outline:.3mm solid var(--gold-muted);
  outline-offset:-3mm;padding:7mm 8mm;text-align:center}
.logo{display:flex;align-items:center;justify-content:center;gap:3mm}
.logo img{width:20mm;height:20mm}
.wordmark{font-family:Display;font-size:33pt;line-height:1;background:var(--grad);-webkit-background-clip:text;
  background-clip:text;color:transparent;-webkit-text-stroke:.45mm var(--badge-outline);display:inline-block;
  transform:skewX(-12deg);padding:0 2mm;paint-order:stroke fill}
.tag{font-family:UIBold;font-size:8.5pt;color:var(--muted);letter-spacing:.5pt;margin-top:1.5mm}
.art{position:relative;margin:4mm 0 0;height:84mm;border:1mm solid var(--gold);overflow:hidden}
.art img{width:100%;height:100%;object-fit:cover;object-position:50% 30%}
.badge{position:absolute;font-family:Display;font-size:9pt;padding:1.2mm 3mm;border-radius:5mm}
.badge.gold{left:3mm;top:3mm;background:var(--gold);color:var(--on-gold)}
.badge.red{right:3mm;bottom:3mm;background:var(--crimson);color:var(--text)}
.booklet{font-family:Display;font-size:21pt;color:var(--gold);margin:5mm 0 4mm}
.warn{position:relative;border:.6mm solid var(--red-light);border-radius:3mm;background:var(--card);
  padding:5mm 4mm 3mm;font-size:9.5pt;margin-bottom:4mm}
.wlabel{position:absolute;top:-3mm;left:50%;transform:translateX(-50%);background:var(--red-light);color:var(--bg);
  font-family:UIBold;font-size:8.5pt;padding:.6mm 3mm;border-radius:3mm;white-space:nowrap}
/* contents */
.toc{list-style:none}.toc li{display:flex;gap:2mm;margin:1.4mm 0;font-family:UIBold}
.toc .dots{flex:1;border-bottom:.4mm dotted var(--dim);margin-bottom:1.2mm}
/* screenshots with callouts */
.shot{position:relative;border:.6mm solid var(--gold);border-radius:1.5mm;overflow:hidden;margin:2mm 0 3mm}
.shot img{display:block;width:100%}
.mark{position:absolute;transform:translate(-50%,-50%);width:6.5mm;height:6.5mm;border-radius:50%;
  background:var(--gold);color:var(--on-gold);font-family:Display;font-size:10pt;display:flex;align-items:center;
  justify-content:center;border:.5mm solid var(--badge-outline)}
.legend{margin-left:5mm;font-size:9.5pt}.legend li{margin:.8mm 0}
.menu .shot{margin:1.2mm auto;width:80%}.menu .legend{font-size:9pt;margin-top:1mm}.menu .legend li{margin:.4mm 0}
/* panel */
.panel{display:flex;align-items:center;gap:6mm;background:var(--card);border:.6mm solid var(--dim);border-radius:3mm;
  padding:5mm 6mm;margin-top:2mm}
.stick{position:relative;width:15mm;height:22mm}
.stick .ball{position:absolute;left:3.5mm;top:0;width:8mm;height:8mm;border-radius:50%;background:var(--crimson)}
.stick .rod{position:absolute;left:6.6mm;top:7mm;width:1.8mm;height:10mm;background:var(--chrome)}
.stick .base{position:absolute;left:0;top:15mm;width:15mm;height:6mm;border-radius:50%;border:.5mm solid var(--dim);
  background:var(--bg-deep)}
.rows div{display:flex;gap:3mm;margin:1.5mm 0}
.btn{width:11mm;height:11mm;border-radius:50%;border:.6mm solid var(--gold);background:var(--button);
  display:flex;align-items:center;justify-content:center;font-family:Display;font-size:9pt}
.pills{display:flex;flex-direction:column;gap:4mm}
.pill{display:inline-block;background:var(--gold);color:var(--on-gold);font-family:UIBold;font-size:8.5pt;
  padding:.6mm 2.4mm;border-radius:3mm}
.keys{list-style:none;display:flex;flex-wrap:wrap;gap:1.5mm 5mm;margin:3mm 0 0;font-size:9.5pt}
.combos{width:100%;border-collapse:collapse;font-size:9.5pt}
.combos td{padding:1.6mm 2mm}.combos tr:nth-child(odd){background:var(--card)}
.combos td:first-child{width:40mm}
.combo i{font-style:normal;font-family:Display;color:var(--gold);margin:0 1mm}
/* layouts */
.grid{display:grid;grid-template-columns:1fr 1fr;gap:3mm}
.grid figure{text-align:center}.grid img{width:100%;display:block}
.grid figcaption{font-family:UIBold;font-size:8pt;color:var(--muted);margin-top:.8mm}
.grid em{color:var(--pending);font-style:normal}
/* features */
.tiles{display:grid;grid-template-columns:1fr 1fr;gap:4mm}
.tile{background:var(--card);border:.6mm solid var(--gold);border-radius:3mm;padding:2.5mm;min-height:66mm}
.tile img,.noimg{width:100%;height:30mm;object-fit:cover;border-radius:1.5mm;display:block}
.noimg{background:var(--panel);display:flex;align-items:center;justify-content:center;font-family:Display;
  font-size:12pt;color:var(--gold);text-align:center;padding:2mm}
.tile h3{color:var(--gold);margin:2mm 0 1mm}.tile p{font-size:9pt;color:var(--text-soft)}
/* back */
.back{background:linear-gradient(180deg,var(--bg),var(--bg-deep))}
.backlogo{text-align:center;margin-top:4mm}.backlogo img{width:32mm;height:32mm;display:block;margin:0 auto 3mm}
.iconic{text-align:center;font-family:UIBold;color:var(--muted);font-size:9pt;margin:6mm 4mm}
/* 16-page edition: notes, tables, settings pages */
.note{font-size:9pt;color:var(--text-soft);background:var(--panel);border-left:1mm solid var(--gold);
  padding:1.6mm 3mm;margin:2mm 0;border-radius:1mm}
h3.gold2{color:var(--gold);margin:2mm 0 1mm}
.box h3.gold2{margin-top:0}.box p{font-size:9pt;margin-bottom:1mm}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:3mm}
.folders,.bios,.order{width:100%;border-collapse:collapse;font-size:8.6pt}
.folders td,.bios td,.order td{padding:.9mm 1.5mm;vertical-align:top}
.folders tr:nth-child(odd),.bios tr:nth-child(odd),.order tr:nth-child(odd){background:var(--card)}
.box .bios tr:nth-child(odd){background:var(--panel)}
.order td:first-child{font-family:Display;font-size:8.5pt;color:var(--gold);width:38mm;white-space:nowrap}
.mini{margin:1mm 0 2mm 5mm;font-size:9.3pt}.mini li{margin:.9mm 0;padding-left:1mm}
.mini li::marker{font-family:Display;color:var(--gold)}
.nav .combos td:first-child{width:30mm}
.combos.wide td:first-child{width:22mm}
.two{display:grid;grid-template-columns:52mm 1fr;gap:4mm;align-items:start;margin-top:1mm}
.two .shot{margin:1mm 0}
.shot.half{width:62%;margin:1mm auto 3mm}
.settings p{font-size:9.3pt;margin-bottom:1mm}.settings h3.gold2{margin-top:2.6mm}
.online .shot{width:66%;margin:1.5mm auto 2mm}.online .combos td{padding:1.1mm 2mm}
.scrape .mini{font-size:9.6pt}.scrape .mini li{margin:1.1mm 0}
.backshots{display:grid;grid-template-columns:repeat(4,1fr);gap:2.5mm;margin:5mm 0 0}
.backshots img{width:100%;height:20mm;object-fit:cover;border:.5mm solid var(--gold);border-radius:1mm;display:block}
.backshots figcaption{font-family:UIBold;font-size:7pt;color:var(--gold-muted);text-align:center;margin-top:.6mm}
@page{size:A5;margin:0}
@media print{html,body{background:var(--bg-deep)}.bar{display:none}
  .page{margin:0;break-after:page;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""

JS = """(function(){var h=location.hash;
if(h.indexOf('letter')>=0){var s=document.createElement('style');s.textContent='@page{size:letter;margin:0}'+
'@media print{.page{zoom:1.25;margin:6mm auto 0}}';document.head.appendChild(s);}
var m=/page=(\\d+)/.exec(h);if(m){document.body.className='single';var p=document.getElementById('p'+m[1]);
if(p){p.className+=' on';}}})();"""


def export_guard(doc):
    """Private repo only (config/edition/ is not exported): the base64 of the fonts and pictures spells a denylisted
    word now and then by chance ("Build" between two digits), which the community export's scan reports. Each such run
    inside a data: URI gets a line break after its first letter (browsers skip whitespace in base64), so it no
    longer reads as the word. The community tree has no denylist and skips this; its rebuild differs from the
    committed file only by these line breaks (docs/tests/test_manual.py compares without them there)."""
    deny = os.path.join(REPO, "config", "edition", "denylist.txt")
    if not os.path.isfile(deny):
        return doc
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import edition_export as ee
    pats = [rx for _label, rx in ee.load_denylist(deny)]
    for _round in range(5):
        spans = [(m.start(), m.end()) for m in re.finditer(r"data:[^)\"']+", doc)]
        cuts = sorted({m.start() + 1 for rx in pats for m in rx.finditer(doc)
                       if any(a <= m.start() and m.end() <= b for a, b in spans)}, reverse=True)
        if not cuts:
            break
        for c in cuts:
            doc = doc[:c] + "\n" + doc[c:]
    return doc


BOOKLET_MD = os.path.join(MANUAL, "README.md")
PAGE_ALT = {"cover": "Cover", "contents": "Welcome: contents and precautions", "setup-1": "Setup, steps 1 to 4",
            "setup-2": "Setup, steps 5 to 7", "adding-games": "Adding games: folders and BIOS",
            "game-menu": "The game menu", "getting-around": "Getting around and switching off",
            "how-to-play": "How to play: the control panel", "layouts-1": "Controller layouts, page 1",
            "layouts-2": "Controller layouts, page 2", "online": "Online play", "scraping": "Pictures and game info",
            "settings": "Settings", "password-lists": "Settings: password and game lists",
            "attract-more": "Settings: attract mode, FPS, trophies, time", "back": "Credits"}


def booklet_md():
    """docs/manual/README.md: the booklet as its page pictures, the way GitHub shows it (it shows an .html as source)."""
    rows = ["# %s instruction booklet" % NAME.title(), "",
            "The setup guide as a retro game-box booklet, 16 pages. Same steps as [Start here](../START.md).", "",
            "To print it: download [coin-dojo-manual.html](coin-dojo-manual.html) (the Raw button, then save), open it "
            "in your browser and print with background graphics on, A5 or US Letter.", ""]
    for i, name in enumerate(PAGE_NAMES, 1):
        rows += ["![Page %d: %s](pages/%02d-%s.png)" % (i, PAGE_ALT[name], i, name), ""]
    return "\n".join(rows)


def build(out=OUT):
    with open(BOOKLET_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write(booklet_md())
    b = brand.load()
    steps = parse_start()
    pages = build_pages(b, steps)
    doc = ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
           'content="width=device-width,initial-scale=1"><title>%s - Instruction Booklet</title><style>%s</style>'
           '</head><body><div class="bar">%s INSTRUCTION BOOKLET &nbsp; Print: <a href="#a5">A5 pages</a> '
           '<a href="#letter">US Letter</a> (choose the mode, reload, then print with background graphics on; '
           'for a folded booklet use your printer\'s booklet option)</div>\n%s\n<script>%s</script></body></html>\n'
           % (NAME.title(), css(b), NAME, "\n".join(pages), JS))
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(export_guard(doc))      # why: base64 can spell a denylisted name by chance; the export scan would hit it
    return out, len(pages)


# ---------------------------------------------------------------- page PNGs (headless Chrome)
CHROMES = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "chromium")


def chrome():
    for c in CHROMES:
        p = c if os.path.isabs(c) else shutil.which(c)
        if p and os.path.exists(p):
            return p
    return None


def render_pages(html_path=OUT, out_dir=PAGES_DIR):
    exe = chrome()
    if not exe:
        raise SystemExit("gen_manual: no Chrome for the page PNGs")
    os.makedirs(out_dir, exist_ok=True)
    w, h = round(148 / 25.4 * 96), round(210 / 25.4 * 96)
    with tempfile.TemporaryDirectory() as tmp:
        for i, name in enumerate(PAGE_NAMES, 1):
            shot = os.path.join(out_dir, "%02d-%s.png" % (i, name))
            subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
                            "--force-device-scale-factor=%s" % SHOT_SCALE, "--window-size=%d,%d" % (w, h),
                            "--user-data-dir=" + os.path.join(tmp, "p"), "--virtual-time-budget=3000",
                            "--screenshot=" + shot, "file://%s#page=%d" % (html_path, i)],
                           capture_output=True, timeout=120, check=True)
            print("wrote %s" % shot)


# ---------------------------------------------------------------- capture: the starter carousel (Pillow, ES-DE)
def capture():
    from PIL import Image
    import gen_features_gif as gf
    os.makedirs(gf.CACHE, exist_ok=True)
    tree = gf.community_tree(gf.CACHE)
    tmp = tempfile.mkdtemp(prefix="manual-", dir=gf.CACHE)
    fx = os.path.join(tmp, "fixture")                 # the preview fixture's systems, NO collections: a new owner's menu
    shutil.copytree(os.path.join(tree, "skills", "local-preview", "fixtures", "esde-preview", "gamelists"),
                    os.path.join(fx, "gamelists"))
    os.makedirs(os.path.join(fx, "collections"))
    theme = gf.stage_theme(tree, tmp)
    raw = os.path.join(tmp, "raw")
    gf._run([os.path.join(REPO, "skills", "local-preview", "preview-esde.sh"), theme, raw, "--view", "both",
             "--keys", "Return", "--fixture", fx])
    os.makedirs(SRC, exist_ok=True)
    with Image.open(os.path.join(raw, "system.png")) as im:
        im.convert("RGB").resize((960, 720), Image.LANCZOS).save(os.path.join(SRC, "menu-carousel.jpg"), quality=86)
    with Image.open(os.path.join(raw, "gamelist.png")) as im:       # the list's lower half: hotkey strip, help bar
        im.convert("RGB").crop((0, 520, 1440, 1080)).resize((960, 373), Image.LANCZOS).save(
            os.path.join(SRC, "menu-list.jpg"), quality=86)
    import brand as br                                              # the CRT illustration, as in the features GIF
    with Image.open(os.path.join(raw, "system.png")) as im:
        crop = im.convert("RGB").crop(gf.CRT_CROP)
    gf._shot(br.load(), "crt", [crop]).save(os.path.join(SRC, "crt.jpg"), quality=86)
    shutil.rmtree(tmp, ignore_errors=True)
    print("capture: wrote %s/menu-carousel.jpg, menu-list.jpg" % SRC)


SETTINGS_SHOTS = [("settings-home.jpg", ""), ("settings-password.jpg", "down,down,ok"),
                  ("settings-lists.jpg", "down,down,down,ok"), ("settings-attract.jpg", "down,down,down,down,ok")]


def capture_settings():
    """The SETTINGS pages (13-15) as a new owner sees them: the community edition's app (skills/settings --png) with
    the fake nmcli and a fake timedatectl, in its own cache folder (never the features GIF's)."""
    from PIL import Image
    import gen_features_gif as gf
    cache = os.path.join(os.path.expanduser("~"), ".cache", "coin-dojo", "manual")
    os.makedirs(cache, exist_ok=True)
    tree = gf.community_tree(cache)
    tmp = tempfile.mkdtemp(prefix="settings-", dir=cache)
    # a new owner's cabinet: the community login, a community GAME LISTS entry, ES-DE's file already applied
    env = dict(os.environ, HOME=os.path.join(tmp, "home"), HK_FAKE_NMCLI_STATE=os.path.join(tmp, "nmcli.json"),
               USER="coindojo", LOGNAME="coindojo")
    os.makedirs(os.path.join(env["HOME"], "ES-DE", "settings"))
    entry = os.path.join(tmp, "settings.hks")
    with open(entry, "w") as f:
        f.write('{"lists": {"edition": "community"}}\n')
    tdc = os.path.join(tmp, "timedatectl")
    with open(tdc, "w") as f:
        f.write("#!/bin/sh\n[ \"$1\" = show ] && echo Europe/London && exit 0\nexit 1\n")
    os.chmod(tdc, 0o755)
    st = os.path.join(tree, "skills", "settings")
    with open(os.path.join(env["HOME"], "ES-DE", "settings", "es_settings.xml"), "w") as f:
        f.write('<?xml version="1.0"?>\n')
    gf._run(["python3", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import attract_apply as a; "
             "a.write_settings(a.load_config(sys.argv[2]))", st, entry], env=env)
    os.makedirs(SRC, exist_ok=True)
    for name, actions in SETTINGS_SHOTS:
        raw = os.path.join(tmp, name + ".png")
        cmd = ["python3", os.path.join(st, "settings_app.py"), entry, "--png", raw, "--hostname", "coindojo",
               "--nmcli", os.path.join(st, "fake_nmcli.py"), "--timedatectl", tdc]
        gf._run(cmd + (["--actions", actions] if actions else []), env=env)
        with Image.open(raw) as im:
            im.convert("RGB").resize((720, 540), Image.LANCZOS).save(os.path.join(SRC, name), quality=84)
    shutil.rmtree(tmp, ignore_errors=True)
    print("capture-settings: wrote %s" % ", ".join(n for n, _a in SETTINGS_SHOTS))


def main(argv):
    cmd = argv[0] if argv else "build"
    if cmd == "capture":
        capture()
        cmd = "build"
    if cmd == "capture-settings":
        capture_settings()
        cmd = "build"
    if cmd in ("build", "all"):
        out, n = build()
        print("wrote %s (%d pages, %d bytes)" % (out, n, os.path.getsize(out)))
    if cmd in ("pages", "all"):
        render_pages()
    if cmd not in ("build", "pages", "all"):
        print("usage: gen_manual.py [build|capture|pages|all]", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
