"""Nested main screen for ES-DE: MUST PLAY / GENRES / SERIES / DECADES / SYSTEMS (stdlib only).

Imported by collgen.py (`collgen.py nested`). ES-DE has no nested carousel, so the 4 entries are
virtual systems (custom es_systems.xml with a top-level <loadExclusive/>, which hides every bundled
system). Each virtual system's ROM dir holds subfolders of symlinks to the real ROMs; one dispatcher
(launch.py) resolves the symlink at launch time. Proven in the preview harness: skills/collections/spike/SPIKE.md.

Pure functions build a model; `render()` turns it into {path: text} files, `link_plan()` into symlinks.
"""
import glob
import html as htmlmod
import json
import os
import re
import xml.etree.ElementTree as ET

# The virtual-system names have ONE source: hk_virtual_systems in config/variables.d/collections.yml (#94). Lookup order:
# env HK_VIRTUAL_SYSTEMS (JSON list) > virtual_systems.json next to this file (the esde_collections role writes it from
# esde_virtual_systems, next to the tools on the cabinet; never tracked) > the declared default in a checkout.
# Nothing else in the repo types the list (tools/tests/test_virtual_systems.py).
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_DECL = os.path.join(HERE, "..", "..", "config", "variables.d", "collections.yml")
NAMES_FILE = os.path.join(HERE, "virtual_systems.json")
ENV_NAMES = "HK_VIRTUAL_SYSTEMS"


def load_virtual_names(env=None, decl=None, names_file=None):
    """The configured virtual-system slugs (stdlib only: no PyYAML on the cabinet)."""
    raw = (os.environ if env is None else env).get(ENV_NAMES)
    nf = NAMES_FILE if names_file is None else names_file
    if not raw and os.path.isfile(nf):
        raw = open(nf, encoding="utf-8").read()
    if raw:
        names = json.loads(raw)
    else:
        try:
            text = open(decl or CONFIG_DECL, encoding="utf-8").read()
        except OSError:
            raise SystemExit("nested: the virtual-system names are unknown: no env %s, no %s, no checkout (%s). "
                             "Run `make deploy` (the role writes the file) or set the env to a JSON list."
                             % (ENV_NAMES, nf, decl or CONFIG_DECL))
        m = re.search(r"^  hk_virtual_systems:\n(?:    .*\n|\n)*?    default: \[([^\]]*)\]", text, re.M)
        if not m:
            raise SystemExit("nested: no `default: [...]` for hk_virtual_systems in %s" % (decl or CONFIG_DECL))
        names = [n.strip().strip("\"'") for n in m.group(1).split(",") if n.strip()]
    if not names or not all(isinstance(n, str) and re.fullmatch(r"[a-z0-9-]+", n) for n in names):
        raise SystemExit("nested: bad virtual-system names %r (want slugs: lowercase, digits, -)" % (names,))
    return tuple(names)


ALL_VIRTUAL = load_virtual_names()          # every generated system, the SETTINGS entry included
# The SETTINGS app (#34, skills/settings; Design's) as the LAST carousel entry: one more virtual system whose only
# link points at the entry file the `settings` role installs (a JSON config the app reads). Emitted only with
# `nested --settings-entry FILE` (role esde_collections passes it when FILE exists on the host), so a build without
# the app has no SETTINGS entry and no dead link. Its gamelist sits in ~/ES-DE/gamelists/settings: readers skip it.
SETTINGS_SLUG, SETTINGS_TITLE, SETTINGS_LINK = "settings", "SETTINGS", "Settings.hks"
# The TROPHIES page (#176, skills/settings/trophies_app.py): the same pattern, just before SETTINGS. Emitted only with
# `nested --trophies-entry FILE` (the settings role installs it when RetroAchievements is configured).
TROPHIES_SLUG, TROPHIES_TITLE, TROPHIES_LINK = "trophies", "TROPHIES", "Trophies.hks"
TROPHIES_COMMAND = "python3 /data/coin-dojo/settings/trophies_app.py"
# (slug, carousel title) in list order; the slug is the theme badge name (<slug>.png); the title is the slug in capitals
# FIGHTCADE and QUIT TO MAIN MENU (#214, phase 2 of #210): two more tool systems of their own (NOT inside `settings`:
# its #195 hook fires on cursor stop, so scrolling onto them would run them). Each has one entry whose launch is
# esde_entry.sh <mode>: it asks the session (#209) for the next mode and ends ES-DE. FIGHTCADE sits before TROPHIES,
# QUIT TO MAIN MENU after SETTINGS. Emitted only with `nested --fightcade-entry/--quit-entry FILE`.
FIGHTCADE_SLUG, FIGHTCADE_TITLE, FIGHTCADE_LINK = "fightcade", "FIGHTCADE", "Fightcade.hks"
QUIT_SLUG, QUIT_TITLE, QUIT_LINK = "quit", "QUIT TO MAIN MENU", "Quit.hks"
ENTRY_SCRIPT = "/data/coin-dojo/esde_entry.sh"
FIGHTCADE_COMMAND = "bash %s fightcade" % ENTRY_SCRIPT
QUIT_COMMAND = "bash %s menu" % ENTRY_SCRIPT
# (slug, carousel title) in list order; the slug is the theme badge name (<slug>.png); the title is the slug in capitals
VIRTUAL = [(v, v.upper().replace("-", " ")) for v in ALL_VIRTUAL
           if v not in (SETTINGS_SLUG, TROPHIES_SLUG, FIGHTCADE_SLUG, QUIT_SLUG)]
VIRTUAL_SLUGS = tuple(v for v, _t in VIRTUAL)
SETTINGS_COMMAND = "python3 /data/coin-dojo/settings/settings_app.py"
TOOL_SLUGS = (FIGHTCADE_SLUG, TROPHIES_SLUG, SETTINGS_SLUG, QUIT_SLUG)
GENRE_SLUGS = ("fighters", "shmups", "beat-em-up", "run-and-gun", "platformers", "puzzle", "sports",
               "racing", "maze", "twin-stick")
GENRE_TITLES = {"fighters": "Fighters", "shmups": "Shmups", "beat-em-up": "Beat 'em Up",
                "run-and-gun": "Run and Gun", "platformers": "Platformers", "puzzle": "Puzzle",
                "sports": "Sports", "racing": "Racing", "maze": "Maze",
                "twin-stick": "Twin-Stick"}
# cps1-3 carry neutral names here; system_names.tsv (data_path, #190) may rename any system (load_system_names)
SYSTEM_TITLES = {"cps1": "CP System", "cps2": "CP System II", "cps3": "CP System III",
                 "fbneo": "FinalBurn Neo Arcade", "neogeo": "SNK Neo Geo", "arcade": "Arcade",
                 "genesis": "Sega Genesis", "dreamcast": "Sega Dreamcast", "naomi": "Sega NAOMI",
                 "saturn": "Sega Saturn", "snes": "Super Nintendo", "nes": "Nintendo NES",
                 "n64": "Nintendo 64", "pcengine": "PC Engine", "psx": "Sony PlayStation",
                 "segacd": "Sega CD", "atari2600": "Atari 2600"}
# ES-DE per-game state that must survive a regeneration (kept from the existing gamelist.xml)
KEEP_TAGS = ("favorite", "playcount", "lastplayed", "hidden", "completed", "broken", "kidgame")
# metadata that is NOT copied from the real gamelist into the virtual one
DROP_TAGS = KEEP_TAGS + ("path",)
# system -> label for the ' (System)' suffix on a display-name collision (virtual gamelists only)
SYSTEM_LABELS = {"cps1": "Arcade", "cps2": "Arcade", "cps3": "Arcade", "fbneo": "Arcade", "neogeo": "Neo Geo",
                 "nes": "NES", "snes": "SNES", "genesis": "Genesis", "pcengine": "PC Engine", "psx": "PS1",
                 "saturn": "Saturn", "dreamcast": "Dreamcast", "n64": "N64", "segacd": "Sega CD",
                 "atari2600": "Atari 2600"}
# when two collided games are both "Arcade", tell the arcade systems apart
ARCADE_LABELS = {"cps1": "CPS1", "cps2": "CPS2", "cps3": "CPS3", "fbneo": "FBNeo"}
SYSTEM_VIRTUALS = ("systems", "must-play")     # folders named after a real system (must-play unless --flat-must-play)
# MUST PLAY GENRES (#182): the best-rated games in genre folders. Its folders are the GENRES buckets and reuse their
# titles, blurbs and art (like must-play/<system> reuses SYSTEMS). collgen names its collections "mpg-<bucket>".
MUST_PLAY_GENRES = "must-play-genres"
MPG_PREFIX = "mpg-"
GENRE_VIRTUALS = ("genres", MUST_PLAY_GENRES)
SUFFIX_VIRTUALS = ("must-play", "genres", "series", "decades") + (MUST_PLAY_GENRES,)   # by-system folders never collide
LINE_RE = re.compile(r"^%ROMPATH%/([^/]+)/(.+)$")

# #202: ES-DE 3.5.0 runs event scripts (on since #195) through system() = sh -c, each arg in double quotes
# (Scripting.cpp:67-100, PlatformUtil.cpp:99-131); game-select passes the game path and <name>. Inside double quotes
# these four stay active, so a scraped name with $( or a backtick would run as the cabinet user. Display names are
# made safe here (shell_safe); paths and system names are refused by validate_nested.py.
SHELL_ACTIVE = '`$"\\'
_SHELL_SAFE = str.maketrans({'"': "'", "`": "'", "$": "S", "\\": "/"})


def shell_safe(text):
    """A display name with no shell-active character (#202): " and ` -> ', $ -> S, backslash -> /."""
    return (text or "").translate(_SHELL_SAFE)


# ------------------------------------------------------- MAME BIOS/device names
# ES-DE drops every file AND folder whose stem is a MAME BIOS or device short name when the system's platform is
# arcade/neogeo (FileData::isArcadeAsset). Our virtual systems are <platform>arcade</platform>, so the folder
# `neogeo` (MAME's Neo Geo BIOS name) vanished with its 108 games. List + source: mame_hidden_names.tsv.
HIDDEN_NAMES_TSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mame_hidden_names.tsv")
# folder slug -> on-disk folder name when the slug is a hidden name (display name, blurb, facts, art keep the slug)
FOLDER_DIRS = {"neogeo": "neo-geo", "naomi": "sega-naomi"}
_HIDDEN = {}


def load_hidden_names(path=HIDDEN_NAMES_TSV):
    """mame_hidden_names.tsv -> {lowercase name: 'bios'|'device'}. Raises ValueError on a malformed row or
    an empty list (an empty list would silently pass every check)."""
    names = {}
    with open(path, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 2 or parts[0] not in ("bios", "device") or not parts[1].strip():
                raise ValueError("%s:%d: want <bios|device><TAB><name>" % (path, n))
            names[parts[1].strip().lower()] = parts[0]
    if not names:
        raise ValueError("%s: no names" % path)
    return names


def hidden_names():
    if not _HIDDEN:
        _HIDDEN.update(load_hidden_names())
    return _HIDDEN


def hidden_kind(name):
    """'bios' / 'device' when ES-DE would hide a file or folder with this stem on an arcade platform, else None."""
    return hidden_names().get(name.lower())


def folder_dir(folder):
    """On-disk folder name for a folder slug: the slug itself unless ES-DE would hide it ('neogeo' -> 'neo-geo')."""
    if not folder or not hidden_kind(folder):
        return folder
    d = FOLDER_DIRS.get(folder.lower(), folder + "-games")
    assert not hidden_kind(d), d
    return d


def is_bios_file(fn):
    """A MAME BIOS set (neogeo.zip, naomi.zip ...) is never a game: it is not linked into a virtual system."""
    return hidden_kind(os.path.splitext(fn)[0]) == "bios"


def folder_title(virtual, folder, titles=None):
    if folder == ACHIEVEMENTS_SLUG:
        return ACHIEVEMENTS_TITLE
    if virtual == "series":
        return (titles or {}).get(folder, folder.replace("-", " ").title())
    if virtual in GENRE_VIRTUALS:
        return GENRE_TITLES.get(folder, folder.replace("-", " ").title())
    if virtual in SYSTEM_VIRTUALS:
        return SYSTEM_TITLES.get(folder, folder.upper())
    return folder


def system_alias(virtual):
    """Virtual lists whose folders are real systems reuse the SYSTEMS list's blurbs, facts and folder art; MUST PLAY
    GENRES reuses the GENRES ones."""
    if virtual == MUST_PLAY_GENRES:
        return "genres"
    return "systems" if virtual in SYSTEM_VIRTUALS else virtual


def load_folder_text(path):
    """folder_text.tsv -> {(virtual, folder): text}. Tab separated: virtual, folder, text; '#' comments.
    Raises ValueError on a malformed row, an unknown virtual system or a duplicate key."""
    texts = {}
    with open(path, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 3 or not all(p.strip() for p in parts):
                raise ValueError("%s:%d: want 3 tab-separated fields (virtual, folder, text)" % (path, n))
            v, folder, text = (p.strip() for p in parts)
            if v not in VIRTUAL_SLUGS:
                raise ValueError("%s:%d: unknown virtual system %r" % (path, n, v))
            if (v, folder) in texts:
                raise ValueError("%s:%d: duplicate entry %s/%s" % (path, n, v, folder))
            texts[(v, folder)] = text
    return texts


def folder_desc(virtual, folder, count, texts=None):
    """Folder <desc>: the curated blurb (if any) plus the game count."""
    n = "%d game%s" % (count, "" if count == 1 else "s")
    texts = texts or {}
    text = texts.get((virtual, folder)) or texts.get((system_alias(virtual), folder))
    return "%s\n\n%s" % (text, n) if text else n


# systems folders: (maker, launch year, kind) shown in the folder info panel
SYSTEM_FACTS = {
    "cps1": ("CP System", "1988", "Arcade board"), "cps2": ("CP System", "1993", "Arcade board"),
    "cps3": ("CP System", "1996", "Arcade board"), "fbneo": ("Various", "", "Arcade"),
    "neogeo": ("SNK", "1990", "Arcade and home system"), "nes": ("Nintendo", "1983", "Home console"),
    "snes": ("Nintendo", "1990", "Home console"), "genesis": ("Sega", "1988", "Home console"),
    "segacd": ("Sega", "1991", "Console add-on"), "pcengine": ("NEC / Hudson Soft", "1987", "Home console"),
    "psx": ("Sony", "1994", "Home console"), "saturn": ("Sega", "1994", "Home console"),
    "dreamcast": ("Sega", "1998", "Home console"), "n64": ("Nintendo", "1996", "Home console"),
    "atari2600": ("Atari", "1977", "Home console"),
}


def data_path(name):
    """A curated list by file name (#190): personal/skills/collections/<name> in a private checkout, else the file
    beside the tools (the community example, or on the cabinet the copy the esde_collections role put there)."""
    mine = os.path.normpath(os.path.join(HERE, "..", "..", "personal", "skills", "collections", name))
    return mine if os.path.isfile(mine) else os.path.join(HERE, name)


def load_system_names(path):
    """system_names.tsv -> {system: (title, maker, year, kind)}. Tab separated, '#' comments, 5 fields; overrides
    SYSTEM_TITLES / SYSTEM_FACTS (apply_system_names). A missing file is no override; a bad row raises ValueError."""
    rows = {}
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) != 5 or not all(parts[:2]) or not parts[4] or (parts[3] and not parts[3].isdigit()):
                raise ValueError("%s:%d: want system, title, maker, year (digits or empty), kind" % (path, n))
            rows[parts[0]] = tuple(parts[1:])
    return rows


def apply_system_names(rows):
    for system, (title, maker, year, kind) in rows.items():
        SYSTEM_TITLES[system] = title
        SYSTEM_FACTS[system] = (maker, year, kind)


apply_system_names(load_system_names(data_path("system_names.tsv")))


def _child(src, tag):
    if src is None:
        return ""
    e = src.find(tag)
    return (e.text or "").strip() if e is not None else ""


def folder_meta(virtual, folder, pairs, games, titles=None):
    """Info-panel fields for a folder, computed from its games (deterministic; empty values are left out).
    rating = mean of the games' ratings (0-1, nearest 0.05); players = most common; releasedate = earliest
    year; developer = up to 3 most frequent; systems folders use SYSTEM_FACTS for maker/year/kind."""
    srcs = [games.get(s, {}).get(fn) for s, fn in pairs]
    meta = {}
    ratings = []
    for src in srcs:
        try:
            r = float(_child(src, "rating"))
        except ValueError:
            continue
        if 0.0 <= r <= 1.0:
            ratings.append(r)
    if ratings:
        meta["rating"] = ("%.2f" % (round(sum(ratings) / len(ratings) * 20) / 20)).rstrip("0").rstrip(".")
    counts = {}
    for src in srcs:
        p = _child(src, "players")
        if p:
            counts[p] = counts.get(p, 0) + 1
    if counts:
        meta["players"] = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    years = sorted(y for y in (_child(src, "releasedate")[:4] for src in srcs) if re.match(r"^(19|20)\d\d$", y))
    if years:
        meta["releasedate"] = years[0] + "0101T000000"
    devs = {}
    for src in srcs:
        d = _child(src, "developer")
        if d:
            devs[d] = devs.get(d, 0) + 1
    if devs:
        meta["developer"] = ", ".join(d for d, _n in sorted(devs.items(), key=lambda kv: (-kv[1], kv[0]))[:3])
    if virtual in GENRE_VIRTUALS:
        meta["genre"] = folder_title(virtual, folder, titles)
    elif virtual in SYSTEM_VIRTUALS and folder in SYSTEM_FACTS:
        maker, year, kind = SYSTEM_FACTS[folder]
        meta["publisher"] = maker
        meta["genre"] = kind
        if year:
            meta["releasedate"] = year + "0101T000000"
    elif virtual == "series":
        meta["genre"] = "Game series"
    elif virtual == "decades":
        meta["genre"] = "Games from " + folder
    return meta


# ------------------------------------------------------------- achievements

# 'Has achievements' folder (#46): the games ra_progress.py matched to a RetroAchievements set, as one more folder of
# an existing virtual system (default GENRES; no carousel entry). Input: its --matches TSV (system, ./path, id, total).
ACHIEVEMENTS_SLUG, ACHIEVEMENTS_TITLE = "achievements", "Has Achievements"


def load_ra_matches(path):
    """ra_progress.py --matches file -> {(system, file name)}. Raises ValueError on a malformed row or OSError."""
    out = set()
    with open(path, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            cols = line.split("\t")
            if (len(cols) != 4 or not cols[0] or not cols[1].startswith("./")
                    or not re.fullmatch(r"[1-9][0-9]*", cols[2]) or not re.fullmatch(r"[0-9]+", cols[3])):
                raise ValueError("%s:%d: want system, ./path, RA game id, total (tab separated)" % (path, n))
            if cols[0] in VIRTUAL_SLUGS + TOOL_SLUGS:
                raise ValueError("%s:%d: %s is a virtual system" % (path, n, cols[0]))
            out.add((cols[0], os.path.basename(cols[1])))
    return out


def add_achievements(model, matches, systems, virtual="genres"):
    """Add the ACHIEVEMENTS_SLUG folder (visible, non-BIOS games in `matches`) to model[virtual]. -> game count
    (0 = no folder: nothing matched or the virtual system is not in the model)."""
    pairs = sorted((s, r["fn"]) for s in systems for r in systems[s]
                   if (s, r["fn"]) in matches and not is_bios_file(r["fn"]))
    if not pairs or virtual not in model:
        return 0
    model[virtual][ACHIEVEMENTS_SLUG] = pairs
    return len(pairs)


# ------------------------------------------------------------------- series

DEFAULT_SERIES_MIN = 3        # a series folder needs this many games (series.tsv `min` column overrides it)
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def load_series(path):
    """series.tsv -> [dict(slug, title, rx, systems, dev, min)] in file order. Tab separated:
    slug, title, name regex[, systems[, developer regex[, min]]]; '-' = none/default; '#' comments.
    Raises ValueError (file:line) on a malformed row, a bad regex, a bad slug/min or a duplicate slug."""
    rules, seen = [], set()
    with open(path, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = [p.strip() for p in line.split("\t")]
            where = "%s:%d" % (path, n)
            if not 3 <= len(parts) <= 6 or not all(parts[:3]):
                raise ValueError("%s: want 3 to 6 tab-separated fields (slug, title, name regex, systems, developer, min)" % where)
            parts += ["-"] * (6 - len(parts))
            slug, title, rx, systems, dev, mn = parts
            if not SLUG_RE.match(slug):
                raise ValueError("%s: bad slug %r (lowercase letters, digits, single dashes)" % (where, slug))
            if slug in seen:
                raise ValueError("%s: duplicate series %s" % (where, slug))
            seen.add(slug)
            try:
                rx_c = re.compile(rx)
                dev_c = None if dev in ("", "-") else re.compile(dev)
            except re.error as e:
                raise ValueError("%s: bad regex in series %s: %s" % (where, slug, e))
            sysl = None if systems in ("", "-") else frozenset(s.strip() for s in systems.split(",") if s.strip())
            if mn in ("", "-"):
                mn_i = DEFAULT_SERIES_MIN
            elif mn.isdigit() and int(mn) >= 1:
                mn_i = int(mn)
            else:
                raise ValueError("%s: bad min %r (a whole number >= 1, or -)" % (where, mn))
            rules.append(dict(slug=slug, title=title, rx=rx_c, systems=sysl, dev=dev_c, min=mn_i))
    if not rules:
        raise ValueError("%s: no series rows" % path)
    return rules


def series_titles(rules):
    return {r["slug"]: r["title"] for r in rules}


def build_series(rules, games):
    """rules (load_series) + games {system: {fn: ET.Element}} (raw_games) -> {slug: [(system, fn)]}.
    A game matches when the name regex (re.search on the display name) AND the system list AND the developer
    regex (fullmatch) all pass; hidden games never match. A series below its `min` is dropped. Deterministic."""
    out = {}
    for r in rules:
        pairs = []
        for system in sorted(games):
            if r["systems"] is not None and system not in r["systems"]:
                continue
            for fn in sorted(games[system]):
                g = games[system][fn]
                if _child(g, "hidden").lower() == "true" or is_bios_file(fn):
                    continue
                name = _child(g, "name") or os.path.splitext(fn)[0]
                if not r["rx"].search(name):
                    continue
                if r["dev"] is not None and not r["dev"].fullmatch(_child(g, "developer")):
                    continue
                pairs.append((system, fn))
        if len(pairs) >= r["min"]:
            out[r["slug"]] = pairs
    return dict(sorted(out.items()))


def split_line(line):
    m = LINE_RE.match(line)
    if not m:
        raise ValueError("bad collection line: %r" % line)
    return m.group(1), m.group(2)


def build_model(cols, systems, flat_must_play=False):
    """cols {collection: [%ROMPATH% lines]} (collgen.build_collections) + systems {sys: rows}
    -> {virtual: {folder: [(system, fn)]}} ('' = flat). Sorted, so the output is deterministic.
    must-play gets one folder per system (folder = the system slug); flat_must_play keeps the old single list."""
    model = {v: {} for v, _t in VIRTUAL}
    for name, lines in cols.items():
        pairs = sorted(p for p in (split_line(l) for l in lines) if not is_bios_file(p[1]))
        if name == "must-play":
            if flat_must_play:
                model["must-play"][""] = pairs
            else:
                for system, fn in pairs:
                    model["must-play"].setdefault(system, []).append((system, fn))
        elif name in GENRE_SLUGS:
            model["genres"][name] = pairs
        elif name.startswith(MPG_PREFIX) and name[len(MPG_PREFIX):] in GENRE_SLUGS:
            if MUST_PLAY_GENRES in model:               # only when the slug is on the carousel (hk_virtual_systems)
                model[MUST_PLAY_GENRES][name[len(MPG_PREFIX):]] = pairs
        elif re.fullmatch(r"\d{4}-\d{2}|\d{3}0s", name):        # 1990-94, and the single 1970s bucket
            model["decades"][name] = pairs
    for s in sorted(systems):
        if systems[s]:
            model["systems"][s] = sorted((s, r["fn"]) for r in systems[s] if not is_bios_file(r["fn"]))
    return {v: dict(sorted(f.items())) for v, f in model.items() if f}


def link_names(pairs):
    """[(system, fn)] -> [(link name, system, fn)]; a name clash across systems gets ' (system)' before the
    extension (the dispatcher uses the realpath, so the link name is free). A game whose stem is a MAME device
    name (ES-DE would hide it on an arcade platform) gets the suffix too."""
    seen, out = set(), []
    for system, fn in pairs:
        name = fn
        if name.lower() in seen or hidden_kind(os.path.splitext(fn)[0]):
            stem, ext = os.path.splitext(fn)
            name = "%s (%s)%s" % (stem, system, ext)
        seen.add(name.lower())
        out.append((name, system, fn))
    return out


# ---------------------------------------------------------------- gamelists

def raw_games(directory, texts=None):
    """{system: {fn: ET.Element}} from <directory>/<system>/gamelist.xml (second root tolerated).
    texts: optional {system: text} (the cleaned names from `collgen.py names`) instead of the file."""
    res = {}
    for gl in sorted(glob.glob(os.path.join(directory, "*", "gamelist.xml"))):
        sysname = os.path.basename(os.path.dirname(gl))
        if sysname in VIRTUAL_SLUGS + TOOL_SLUGS:      # deployed virtual gamelists are not real systems
            continue
        if texts is not None and sysname in texts:
            raw = texts[sysname]
        else:
            with open(gl, encoding="utf-8") as f:
                raw = f.read()
        text = re.sub(r"^\s*<\?xml[^>]*\?>", "", raw).strip()
        root = ET.fromstring("<wrap>" + text + "</wrap>")
        games = {}
        for g in root.findall(".//game"):
            p = (g.findtext("path") or "").strip()
            if p:
                games[os.path.basename(p)] = g
        res[os.path.basename(os.path.dirname(gl))] = games
    return res


def existing_state(path):
    """{virtual path ./folder/name: {tag: text}} for KEEP_TAGS already in a virtual gamelist.xml."""
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        root = ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", f.read()).strip() or "<gameList/>")
    st = {}
    for g in root.findall("game"):
        p = (g.findtext("path") or "").strip()
        kept = {t: g.findtext(t) for t in KEEP_TAGS if g.find(t) is not None}
        if p and kept:
            st[p] = kept
    return st


def name_key(name):
    """Collision key: case-insensitive, punctuation and spaces ignored ('Out Run' == 'OutRun')."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower().replace("&", "and"))


def system_label(system, specific=False):
    if specific and system in ARCADE_LABELS:
        return ARCADE_LABELS[system]
    return SYSTEM_LABELS.get(system, system.upper())


def collision_suffixes(pairs, games):
    """[(system, fn)] of ONE virtual list -> {(system, fn): ' (Label)'} for the games whose display
    name collides with another game of that list. Others are absent (they stay bare)."""
    groups = {}
    for system, fn in pairs:
        src = games.get(system, {}).get(fn)
        nm = src.findtext("name") if src is not None else None
        groups.setdefault(name_key((nm or os.path.splitext(fn)[0]).strip()), []).append((system, fn))
    out = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        labels = [system_label(s) for s, _f in members]
        for (system, fn), lab in zip(members, labels):
            if labels.count(lab) > 1:                 # two arcade systems: CPS1/CPS2/CPS3/FBNeo
                lab = system_label(system, specific=True)
            out[(system, fn)] = " (%s)" % lab
    return out


def gamelist_xml(virtual, folders, games, state, texts=None, titles=None):
    esc = lambda s: htmlmod.escape(s or "", quote=False)
    out = ['<?xml version="1.0"?>', "<gameList>"]
    for folder, pairs in folders.items():
        sfx = collision_suffixes(pairs, games) if virtual in SUFFIX_VIRTUALS else {}
        if folder:
            out.append("\t<folder>\n\t\t<path>./%s</path>\n\t\t<name>%s</name>\n\t\t<desc>%s</desc>\n\t</folder>"
                       % (esc(folder_dir(folder)), esc(shell_safe(folder_title(virtual, folder, titles))),
                          esc(folder_desc(virtual, folder, len(pairs), texts))))
            fm = folder_meta(virtual, folder, pairs, games, titles)
            if fm:
                out[-1] = out[-1].replace("\n\t</folder>", "".join(
                    "\n\t\t<%s>%s</%s>" % (t, esc(fm[t]), t)
                    for t in ("rating", "releasedate", "developer", "publisher", "genre", "players") if t in fm)
                    + "\n\t</folder>")
        for name, system, fn in link_names(pairs):
            rel = "./%s/%s" % (folder_dir(folder), name) if folder else "./" + name
            old = "./%s/%s" % (folder, name) if folder else rel        # favourites kept across the neogeo rename
            e = ["\t<game>", "\t\t<path>%s</path>" % esc(rel)]
            src = games.get(system, {}).get(fn)
            if src is None:
                e.append("\t\t<name>%s</name>" % esc(shell_safe(os.path.splitext(fn)[0] + sfx.get((system, fn), ""))))
            else:
                suffix = sfx.get((system, fn), "")
                for child in src:
                    if child.tag in DROP_TAGS:
                        continue
                    text = (child.text or "").strip()
                    if child.tag == "name":
                        text = shell_safe(text)
                    if suffix and child.tag == "name":
                        e.append("\t\t<name>%s</name>" % esc(text + suffix))
                        if src.find("sortname") is None:         # the pair sorts together on the bare title
                            e.append("\t\t<sortname>%s</sortname>" % esc(text))
                        continue
                    e.append("\t\t<%s>%s</%s>" % (child.tag, esc(text), child.tag))
            for t, v in sorted(state.get(rel, state.get(old, {})).items()):
                e.append("\t\t<%s>%s</%s>" % (t, esc(v), t))
            e.append("\t</game>")
            out.append("\n".join(e))
    out.append("</gameList>")
    return "\n".join(out) + "\n"


def _tool_block(slug, title, nested_root, command, order):
    """A tool system (#34, #176): one .hks entry, platform `ignore` = never scraped, no MAME name rules."""
    return ("  <system>\n    <name>%s</name>\n    <fullname>%s</fullname>\n    <path>%s/%s</path>\n"
            "    <extension>.hks</extension>\n    <command>%s %%ROM%%</command>\n    <platform>ignore</platform>\n"
            "    <theme>%s</theme>\n    <systemsortname>%d-%s</systemsortname>\n  </system>"
            % (slug, title, nested_root.rstrip("/"), slug, command, slug, order, slug))


def systems_xml(model, nested_root, launcher, settings_command=None, trophies_command=None,
                fightcade_command=None, quit_command=None):
    exts = set()
    for folders in model.values():
        for pairs in folders.values():
            for _s, fn in pairs:
                e = os.path.splitext(fn)[1]
                if e:
                    exts.update((e.lower(), e.upper()))
    ext = " ".join(sorted(exts))
    blocks = []
    for i, (slug, title) in enumerate((v, t) for v, t in VIRTUAL if v in model):   # numbered by what is shown
        blocks.append(
            "  <system>\n    <name>%s</name>\n    <fullname>%s</fullname>\n    <path>%s/%s</path>\n"
            "    <extension>%s</extension>\n    <command>%s %%ROM%%</command>\n    <platform>arcade</platform>\n"
            "    <theme>%s</theme>\n    <systemsortname>%d-%s</systemsortname>\n  </system>"
            % (slug, title, nested_root.rstrip("/"), slug, ext, launcher, slug, i, slug))
    if fightcade_command:             # FIGHTCADE (#214): before TROPHIES
        blocks.append(_tool_block(FIGHTCADE_SLUG, FIGHTCADE_TITLE, nested_root, fightcade_command, len(blocks)))
    if trophies_command:              # TROPHIES (#176): just before SETTINGS
        blocks.append(_tool_block(TROPHIES_SLUG, TROPHIES_TITLE, nested_root, trophies_command, len(blocks)))
    if settings_command:              # SETTINGS last (#34)
        blocks.append(_tool_block(SETTINGS_SLUG, SETTINGS_TITLE, nested_root, settings_command, len(blocks)))
    if quit_command:                  # QUIT TO MAIN MENU (#214): the very last entry
        blocks.append(_tool_block(QUIT_SLUG, QUIT_TITLE, nested_root, quit_command, len(blocks)))
    # <loadExclusive/> must sit OUTSIDE <systemList> (inside it ES-DE 3.5.0 ignores it)
    return '<?xml version="1.0"?>\n<loadExclusive/>\n<systemList>\n%s\n</systemList>\n' % "\n".join(blocks)


def settings_gamelist_xml(state):
    """gamelists/settings/gamelist.xml: the one SETTINGS entry (+ its kept ES-DE state, e.g. lastplayed)."""
    rel = "./" + SETTINGS_LINK
    # #195: the app opens by itself when SETTINGS is entered; this one entry shows after leaving it
    e = ["\t<game>", "\t\t<path>%s</path>" % rel, "\t\t<name>Settings</name>",
         "\t\t<desc>Wi-Fi, cabinet name, benchmark mode and RetroAchievements. LK opens the settings again, MK goes "
         "back to the main menu.</desc>",
         "\t\t<genre>Settings</genre>",
         # ES-DE 3.5: not counted AND left out of LAST PLAYED / the auto collections (tested in coin-dojo-vm 2026-11-15)
         "\t\t<nogamecount>true</nogamecount>"]
    for t, v in sorted(state.get(rel, {}).items()):
        e.append("\t\t<%s>%s</%s>" % (t, htmlmod.escape(v or "", quote=False), t))
    e.append("\t</game>")
    return '<?xml version="1.0"?>\n<gameList>\n%s\n</gameList>\n' % "\n".join(e)


def trophies_gamelist_xml(state):
    """gamelists/trophies/gamelist.xml: the one TROPHIES entry (#176; + its kept ES-DE state)."""
    rel = "./" + TROPHIES_LINK
    e = ["\t<game>", "\t\t<path>%s</path>" % rel, "\t\t<name>RetroAchievements</name>",
         "\t\t<desc>Your RetroAchievements: points and rank, the achievement of the week, recent unlocks and the games "
         "you played lately with their progress. STICK browse, MK back, START+SELECT exit.</desc>",
         "\t\t<genre>Trophies</genre>", "\t\t<nogamecount>true</nogamecount>"]
    for t, v in sorted(state.get(rel, {}).items()):
        e.append("\t\t<%s>%s</%s>" % (t, htmlmod.escape(v or "", quote=False), t))
    e.append("\t</game>")
    return '<?xml version="1.0"?>\n<gameList>\n%s\n</gameList>\n' % "\n".join(e)


def _entry_gamelist_xml(link, name, desc, genre, state):
    rel = "./" + link
    e = ["\t<game>", "\t\t<path>%s</path>" % rel, "\t\t<name>%s</name>" % name,
         "\t\t<desc>%s</desc>" % htmlmod.escape(desc, quote=False),
         "\t\t<genre>%s</genre>" % genre, "\t\t<nogamecount>true</nogamecount>"]
    for t, v in sorted(state.get(rel, {}).items()):
        e.append("\t\t<%s>%s</%s>" % (t, htmlmod.escape(v or "", quote=False), t))
    e.append("\t</game>")
    return '<?xml version="1.0"?>\n<gameList>\n%s\n</gameList>\n' % "\n".join(e)


def fightcade_gamelist_xml(state):
    """gamelists/fightcade/gamelist.xml: the one FIGHTCADE entry (#214)."""
    return _entry_gamelist_xml(FIGHTCADE_LINK, "Fightcade",
                               "Online fighting games with Fightcade. Ends ES-DE and starts Fightcade mode; quit it "
                               "to come back to the main menu. Not while a game runs.", "Online", state)


def quit_gamelist_xml(state):
    """gamelists/quit/gamelist.xml: the one QUIT TO MAIN MENU entry (#214)."""
    return _entry_gamelist_xml(QUIT_LINK, "Quit to main menu",
                               "Ends ES-DE and goes back to the cabinet's main menu. Not while a game runs.",
                               "Menu", state)


# -------------------------------------------------------------------- links

# A game listed in two folders of ONE virtual system (a crossover: one game in two SERIES folders)
# must not have two symlinks to the same ROM (#116). ES-DE saves a played game by matching `<path>` entries on
# getCanonicalPath(), which resolves every symlink (FileSystemUtil.cpp), so both entries match the ROM, the first
# one found is deleted and the played one is appended again: the other folder loses its entry (name, desc, art,
# playcount) and the played folder gets two. So the 2nd and later folder of a crossover link to a tiny pointer file
# under roms_nested/.crossover/ instead (a real file: its canonical path is its own, so every folder's entry is
# unique) and launch.py follows the pointer to the ROM. The dir sits NEXT to the virtual systems, outside every
# system <path>, so ES-DE never scans it whatever ShowHiddenFiles says.
POINTER_DIR = ".crossover"
POINTER_MAGIC = "COIN-DOJO-LINK "          # first bytes of a pointer file; launch.py repeats it (single file on the cabinet)


def link_entries(model):
    """-> [(virtual, folder, link name, system, fn, indirect)] in model order. indirect = this game already has a
    link earlier in the same virtual system (another folder), so it must go through a pointer file."""
    out = []
    for v, folders in model.items():
        seen = set()
        for folder, pairs in folders.items():
            for name, system, fn in link_names(pairs):
                out.append((v, folder, name, system, fn, (system, fn) in seen))
                seen.add((system, fn))
    return out


def pointer_path(out_roms, v, folder, name):
    return os.path.join(out_roms, POINTER_DIR, v, folder_dir(folder), name)


def symlink_plan(model, rom_root, out_roms):
    """{link path: target} for the ROM symlinks. Direct links point at the ROM; a crossover's later folders point
    (relative) at their pointer file (see POINTER_DIR)."""
    plan = {}
    for v, folder, name, system, fn, indirect in link_entries(model):
        link = os.path.join(out_roms, v, folder_dir(folder), name)
        if indirect:
            plan[link] = os.path.relpath(pointer_path(out_roms, v, folder, name), os.path.dirname(link))
        else:
            plan[link] = "%s/%s/%s" % (rom_root.rstrip("/"), system, fn)
    return plan


def pointer_plan(model, rom_root, out_roms):
    """{pointer file path: text} for the crossover entries of symlink_plan."""
    return {pointer_path(out_roms, v, folder, name): "%s%s/%s/%s\n" % (POINTER_MAGIC, rom_root.rstrip("/"), system, fn)
            for v, folder, name, system, fn, indirect in link_entries(model) if indirect}


def plan_pointers(plan, out_roms):
    """-> (counts {new,same,changed}, stale [paths]); stale = pointer files under out_roms/.crossover the plan no longer makes."""
    counts = {"new": 0, "same": 0, "changed": 0}
    for path, text in plan.items():
        if not os.path.isfile(path):
            counts["new"] += 1
        else:
            with open(path, encoding="utf-8", errors="replace") as f:
                counts["same" if f.read() == text else "changed"] += 1
    stale = []
    root = os.path.join(out_roms, POINTER_DIR)
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            if p not in plan and not os.path.islink(p):
                with open(p, encoding="utf-8", errors="replace") as f:
                    if f.read(len(POINTER_MAGIC)) == POINTER_MAGIC:      # only ever remove our own pointer files
                        stale.append(p)
    return counts, sorted(stale)


def apply_pointers(plan, stale, out_roms):
    for path, text in plan.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.islink(path) or (os.path.exists(path) and not os.path.isfile(path)):
            raise OSError("refusing to replace a link or directory with a pointer file: %s" % path)
        if os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                if f.read() == text:
                    continue
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    stop = os.path.abspath(out_roms)
    for p in stale:
        os.remove(p)
        d = os.path.dirname(p)
        while os.path.abspath(d) != stop and os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)


def media_plan(model, media_dir, media_target, out_media, folder_art=None):
    """{link path: target}: scraped media of the real game symlinked per virtual system/folder, so ES-DE
    finds downloaded_media/<virtual>/<type>/<folder>/<stem>.<ext>. media_dir = local copy of the cabinet's
    downloaded_media (<system>/<type>/<file>); targets use media_target (the path on the cabinet)."""
    plan = {}
    if not media_dir or not os.path.isdir(media_dir):
        return plan
    for v, folders in model.items():
        for folder, pairs in folders.items():
            for name, system, fn in link_names(pairs):
                real_stem, link_stem = os.path.splitext(fn)[0], os.path.splitext(name)[0]
                for mfile in sorted(glob.glob(os.path.join(media_dir, system, "*", glob.escape(real_stem) + ".*"))):
                    mtype = os.path.basename(os.path.dirname(mfile))
                    ext = os.path.splitext(mfile)[1]
                    rel = os.path.join(folder_dir(folder), link_stem + ext) if folder else link_stem + ext
                    plan[os.path.join(out_media, v, mtype, rel)] = "%s/%s/%s/%s" % (
                        media_target.rstrip("/"), system, mtype, os.path.basename(mfile))
    if folder_art:  # optional pictograms: <folder_art>/<virtual>/<slug>.png -> covers/<folder_dir(slug)>.png
        for v, folders in model.items():
            for folder in folders:
                art = os.path.join(folder_art, v, folder + ".png")
                if not os.path.isfile(art):      # must-play/<system> shares the SYSTEMS photo
                    art = os.path.join(folder_art, system_alias(v), folder + ".png")
                if folder and os.path.isfile(art):
                    plan[os.path.join(out_media, v, "covers", folder_dir(folder) + ".png")] = os.path.abspath(art)
    return plan


def plan_links(plan, root):
    """-> (counts {new,same,changed}, stale [paths]); stale = symlinks under root the plan no longer makes."""
    counts = {"new": 0, "same": 0, "changed": 0}
    for path, target in plan.items():
        if not os.path.lexists(path):
            counts["new"] += 1
        elif os.path.islink(path) and os.readlink(path) == target:
            counts["same"] += 1
        else:
            counts["changed"] += 1
    stale = []
    if os.path.isdir(root):
        for dp, _dn, fns in os.walk(root):
            for fn in fns:
                p = os.path.join(dp, fn)
                if os.path.islink(p) and p not in plan:
                    stale.append(p)
    return counts, sorted(stale)


def apply_links(plan, stale, stop_at):
    for path, target in plan.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.lexists(path):
            if not os.path.islink(path):
                raise OSError("refusing to replace a real file with a symlink: %s" % path)
            if os.readlink(path) == target:
                continue
            os.remove(path)
        os.symlink(target, path)
    for p in stale:  # only symlinks are ever removed; empty dirs are pruned
        os.remove(p)
        d = os.path.dirname(p)
        while os.path.abspath(d) != os.path.abspath(stop_at) and os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)
