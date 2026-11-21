#!/usr/bin/env python3
"""Deterministic ES-DE collections generator for the coin-dojo cabinet.

Subcommands (plan is the default, --apply writes):

  fbneo-gamelist  libretro "FBNeo - Arcade Games.dat" + zip list + Cabinet Picks
                  -> ES-DE gamelist.xml for the FBNeo arcade set
                  (name + year from the dat, genre borrowed from the picks)
  build           real ES-DE gamelists + Cabinet Picks
                  -> custom-<name>.cfg collections (genres, 5-year decades,
                  must-play = picks matched by title + all CPS1/2/3)

  names           ES-DE gamelists -> clean <name>/<sortname>/<hidden> per NAMING.md
                  (never <path>, never renames files; backup first on --apply)
  nested          virtual MUST PLAY/GENRES/DECADES/SYSTEMS systems (uses the same names)
  metadata        fill MISSING desc/genre/releasedate/developer/publisher/players + covers/screenshots
                  from a RomM export, the libretro dat and the picks (metadata.py; never overwrites)

Exit codes: 0 ok, 2 usage error, 3 input error (missing/malformed input).
Stdlib only. Never touches the cabinet: it reads and writes local files.
"""
import argparse
import datetime
import glob
import html as htmlmod
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher

EXIT_OK, EXIT_USAGE, EXIT_INPUT = 0, 2, 3

MIN_DECADE_GAMES = 25          # a 5-year bucket needs this many games to be kept
YEAR_MIN, YEAR_MAX = 1970, 2015  # releasedate window for decade buckets
SEVENTIES, SEVENTIES_MIN = "1970s", 5   # one bucket for 1970-79; few games exist, so a smaller threshold
FUZZY_MIN = 0.86               # picks -> gamelist title match threshold
MUST_PLAY_TOP = 25             # #182: MUST PLAY = the best-rated N games per system (hk_must_play_top)
MUST_PLAY_GENRE_MIN = 0.9      # #182: MUST PLAY GENRES = rating >= this (0-1; 0.9 = 4.5 stars, hk_must_play_genre_min_stars)
MPG_PREFIX = "mpg-"            # = nested.MPG_PREFIX (collections of the MUST PLAY GENRES entry)
CPS_SYSTEMS = ("cps1", "cps2", "cps3")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nested  # noqa: E402  (next to this file; not a stdlib name; the one source of the virtual-system names, #94)

# slugs of the generated virtual systems (nested.VIRTUAL). Their gamelists get deployed into
# ~/ES-DE/gamelists/, so every reader of a gamelists dir must skip them (they are not real systems).
VIRTUAL_SYSTEMS = nested.VIRTUAL_SLUGS
# the SETTINGS entry (#34, nested.TOOL_SLUGS): also a generated gamelist, also skipped by every reader
TOOL_SYSTEMS = nested.TOOL_SLUGS
SKIP_SYSTEMS = VIRTUAL_SYSTEMS + TOOL_SYSTEMS
DUPLICATES_TSV = nested.data_path("duplicates.tsv")    # the maintainer's list in personal/ when present (#190)
# (system, ROM stem) always in the twin-stick genre (#59); must match skills/controls/controls.py TWIN_STICK
TWIN_STICK_TSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "twin_stick.tsv")

# Cabinet Picks system code -> ES-DE systems searched (in order)
SYSMAP = {"arc": ["fbneo", "cps1", "cps2", "cps3"], "tcv": ["fbneo"], "ng": ["neogeo"],
          "snes": ["snes"], "gen": ["genesis"], "pce": ["pcengine"], "psx": ["psx"],
          "sat": ["saturn"], "dc": ["dreamcast"], "n64": ["n64"], "nes": ["nes"],
          "psp": ["psp"], "scd": ["segacd"], "a26": ["atari2600"]}

# Cabinet Picks genre code -> ES-DE genre string (pty/pin/cls have no bucket)
PICK_GENRE = {"fig": "Fighting", "bmu": "Beat'em Up", "rng": "Run and Gun",
              "shm": "Shoot'em Up", "tws": "Twin Stick", "plt": "Platform",
              "maz": "Maze", "puz": "Puzzle", "spt": "Sports", "rac": "Racing"}

PICKS_RE = re.compile(r'\["((?:[^"\\]|\\.)*)","([a-z0-9]+)","([a-z]+)","([0-9]+s)",([01])\]')
ARTICLES = {"the", "a", "an"}


class InputError(Exception):
    """Missing or malformed input (exit 3)."""


class UsageError(Exception):
    """Bad option combination (exit 2)."""


# ---------------------------------------------------------------- matching

def norm(s):
    """Normalise a title for matching: case, tags, punctuation, roman numerals."""
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"\[[^]]*\]", " ", s)
    s = s.replace("'", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = " " + s.strip() + " "
    for k, v in {" iii": " 3", " ii": " 2", " iv": " 4", " vi": " 6", " v": " 5"}.items():
        s = s.replace(k + " ", v + " ")
    return " ".join(t for t in s.split() if t not in ARTICLES)


def genre_bucket(g):
    """Free-text ES-DE genre -> collection bucket, or None (unknown genres ignored)."""
    g = (g or "").lower()
    if "twin stick" in g or "twin-stick" in g:
        return "twin-stick"
    if "run and gun" in g or "shooter scrolling" in g:
        return "run-and-gun"
    if "shoot" in g or g.strip() == "shooter":
        return "shmups"
    if "beat" in g or "fighter scrolling" in g:
        return "beat-em-up"
    if "fighting" in g:
        return "fighters"
    if "puzzle" in g:
        return "puzzle"
    if "platform" in g:
        return "platformers"
    if "sport" in g:
        return "sports"
    if "racing" in g or "driving" in g:
        return "racing"
    if "labyrinth" in g or "maze" in g:
        return "maze"
    return None


def decade_label(year):
    """1993 -> '1990-94'; 1970-79 -> '1970s' (one bucket); None outside the YEAR_MIN..YEAR_MAX window."""
    if not (YEAR_MIN <= year < YEAR_MAX):
        return None
    if year < 1980:
        return SEVENTIES
    lo = (year // 5) * 5
    return "%d-%02d" % (lo, (lo + 4) % 100)


# ------------------------------------------------------------ title cleanup

_TAG_RE = re.compile(r"\s*\(((?:[^()]|\([^()]*\))*)\)")   # one nesting level


def _split_tags(name):
    """'Title (Euro) (set 1)' -> ('Title', ['Euro', 'set 1'])."""
    tags = _TAG_RE.findall(name)
    base = _TAG_RE.sub("", name)
    return re.sub(r"\s+", " ", base).strip(), tags


def clean_titles(names):
    """Arcade display names: drop (region/revision) tags unless that would make
    two names identical; then the colliding ones keep their first tag, and if
    that still collides, their full original name. Input order is preserved.
    A name without tags keeps the bare title in a collision."""
    parsed = [_split_tags(n) for n in names]
    groups = {}
    for i, (base, _tags) in enumerate(parsed):
        groups.setdefault(base.lower(), []).append(i)
    out = [None] * len(names)
    for idxs in groups.values():
        if len(idxs) == 1:
            i = idxs[0]
            out[i] = parsed[i][0] or names[i]
            continue
        cand = {}
        for i in idxs:
            base, tags = parsed[i]
            cand[i] = "%s (%s)" % (base, tags[0]) if tags else base
        seen = {}
        for i in idxs:
            seen.setdefault(cand[i].lower(), []).append(i)
        for same in seen.values():
            for i in same:
                out[i] = cand[i] if len(same) == 1 else names[i]
    return out


# ----------------------------------------------------------------- parsing

def parse_gamelist(text, label="gamelist"):
    """ES-DE gamelist text -> [dict(fn, name, genre, year)].
    ES-DE writes <alternativeEmulator> as a second top-level element on some
    systems: strip the xml declaration and wrap in a dummy root so it parses."""
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text).strip()
    try:
        root = ET.fromstring("<wrap>" + text + "</wrap>")
    except ET.ParseError as e:
        raise InputError("%s: malformed XML (%s)" % (label, e))
    rows = []
    for g in root.findall(".//game"):
        p = (g.findtext("path") or "").strip()
        if not p or (g.findtext("hidden") or "").strip().lower() == "true":
            continue                      # hidden (BIOS sets, disc members) never go in collections
        fn = os.path.basename(p)
        name = (g.findtext("name") or os.path.splitext(fn)[0]).strip()
        rd = (g.findtext("releasedate") or "").strip()
        year = int(rd[:4]) if rd[:4].isdigit() else 0
        try:
            rating = float((g.findtext("rating") or "").strip())
        except ValueError:
            rating = None                 # unrated (or junk): ranked after every rated game
        if rating is not None and not 0 <= rating <= 1:
            rating = None
        rows.append(dict(fn=fn, name=name, nname=norm(name),
                         genre=g.findtext("genre"), year=year, rating=rating))
    return rows


def load_gamelists(directory, texts=None):
    """{system: rows}. texts (optional {system: gamelist text}) overrides the files, used by
    `nested` to read the cleaned names without touching the gamelists on disk."""
    paths = [p for p in sorted(glob.glob(os.path.join(directory, "*", "gamelist.xml")))
             if os.path.basename(os.path.dirname(p)) not in SKIP_SYSTEMS]
    if not paths:
        raise InputError("no <system>/gamelist.xml under %s" % directory)
    systems = {}
    for gl in paths:
        sysname = os.path.basename(os.path.dirname(gl))
        try:
            if texts is not None and sysname in texts:
                systems[sysname] = parse_gamelist(texts[sysname], gl)
                continue
            with open(gl, encoding="utf-8") as f:
                systems[sysname] = parse_gamelist(f.read(), gl)
        except OSError as e:
            raise InputError("%s: %s" % (gl, e))
    return systems


def parse_picks(text):
    """Cabinet Picks array rows -> [(title, sys, genre, decade, top)]."""
    return [(m.group(1), m.group(2), m.group(3), m.group(4), m.group(5))
            for m in PICKS_RE.finditer(text)]


def read_text(path, what):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError as e:
        raise InputError("%s: %s" % (what, e))


def load_picks(path):
    picks = parse_picks(read_text(path, "picks"))
    if not picks:
        raise InputError("%s: no picks rows found" % path)
    return picks


def parse_dat(text):
    """libretro dat -> {zipname: (title, year-or-None)}."""
    zip2meta = {}
    for blk in text.split("game (")[1:]:
        nm = re.search(r'name\s+"((?:[^"\\]|\\.)*)"', blk)
        yr = re.search(r'releaseyear\s+"(\d{4})"', blk)
        rz = re.search(r'rom\s*\(\s*name\s+(\S+\.zip)', blk)
        if rz:
            zip2meta[rz.group(1)] = (nm.group(1) if nm else None, yr.group(1) if yr else None)
    return zip2meta


# ------------------------------------------------------------------- build

def match_pick(title, candidates_systems, systems):
    """Best (system, romfile) for a pick title, or None below FUZZY_MIN."""
    nt = norm(title)
    best = (None, None, 0.0)
    for cs in candidates_systems:
        for r in systems.get(cs, []):
            nn = r["nname"]
            if nn == nt:
                return cs, r["fn"]
            if len(nt) >= 5 and (nt in nn or nn in nt) and nn:
                sc = 0.80 + 0.19 * (min(len(nt), len(nn)) / max(len(nt), len(nn)))
            else:
                sc = SequenceMatcher(None, nt, nn).ratio()
            if sc > best[2]:
                best = (cs, r["fn"], sc)
    return (best[0], best[1]) if best[1] and best[2] >= FUZZY_MIN else None


def rom_line(system, fn):
    return "%%ROMPATH%%/%s/%s" % (system, fn)


def load_twin_stick(path=None):
    """twin_stick.tsv -> frozenset {(system, rom stem)}. InputError on a missing file, a bad row, a duplicate
    or an empty list (a silently empty list would quietly drop the twin-stick folder)."""
    path = path or TWIN_STICK_TSV
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        raise InputError("twin-stick list: %s" % e)
    out = set()
    for n, line in enumerate(lines, 1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 2 or not all(p.strip() == p and p for p in parts) or "/" in parts[1]:
            raise InputError("%s:%d: want 'system<TAB>rom stem', got %r" % (path, n, line))
        if tuple(parts) in out:
            raise InputError("%s:%d: duplicate %r" % (path, n, line))
        out.add(tuple(parts))
    if not out:
        raise InputError("%s: no twin-stick games" % path)
    return frozenset(out)


def stars_to_rating(stars):
    """--must-play-genre-min in stars (0-5) -> ES-DE's 0-1 rating. Raises InputError outside 0-5."""
    if not 0 <= stars <= 5:
        raise InputError("--must-play-genre-min: %s is not 0-5 stars" % stars)
    return stars / 5.0


def positive_int(text):
    v = int(text)
    if v < 1:
        raise argparse.ArgumentTypeError("must be >= 1, got %d" % v)
    return v


def must_play_rank(row, picked):
    """Sort key for MUST PLAY (#182): rated before unrated, best rating first, a Cabinet Pick before a non-pick on a
    tie, then the name."""
    r = row.get("rating")
    return (r is None, -(r or 0.0), not picked, row.get("nname") or norm(row.get("name") or ""), row["fn"])


def build_collections(systems, picks, min_decade=MIN_DECADE_GAMES, twin_stick=None,
                      must_play_top=MUST_PLAY_TOP, must_play_genre_min=MUST_PLAY_GENRE_MIN):
    """-> (collections {name: sorted unique lines}, stats dict). twin_stick: {(system, rom stem)} that go to
    the twin-stick genre whatever their genre text says (default: twin_stick.tsv).
    must-play (#182) = the top `must_play_top` games of each system (must_play_rank); "mpg-<bucket>" = every game rated
    at least `must_play_genre_min` (0-1, 0.9 = 4.5 stars) in its genre bucket (the MUST PLAY GENRES entry)."""
    if twin_stick is None:
        twin_stick = load_twin_stick()
    genres, decades = {}, {}
    for sysname in sorted(systems):
        for r in systems[sysname]:
            b = genre_bucket(r["genre"])
            if (sysname, os.path.splitext(r["fn"])[0]) in twin_stick:
                b = "twin-stick"
            if b:
                genres.setdefault(b, set()).add(rom_line(sysname, r["fn"]))
            lbl = decade_label(r["year"]) if r["year"] else None
            if lbl:
                decades.setdefault(lbl, set()).add(rom_line(sysname, r["fn"]))
    need = lambda k: min(min_decade, SEVENTIES_MIN) if k == SEVENTIES else min_decade
    dropped = {k: len(v) for k, v in decades.items() if len(v) < need(k)}
    decades = {k: v for k, v in decades.items() if len(v) >= need(k)}

    rec = set()
    hits = 0
    for title, sysc, _g, _d, _t in picks:
        if sysc not in SYSMAP:
            continue
        m = match_pick(title, SYSMAP[sysc], systems)
        if m:
            rec.add(rom_line(*m))
            hits += 1
    matched = len(rec)
    must, mpg = set(), {}
    for sysname in sorted(systems):
        ranked = sorted(systems[sysname], key=lambda r: must_play_rank(r, rom_line(sysname, r["fn"]) in rec))
        for r in ranked[:must_play_top]:
            must.add(rom_line(sysname, r["fn"]))
        for r in systems[sysname]:
            if r.get("rating") is None or r["rating"] < must_play_genre_min - 1e-9:
                continue
            b = genre_bucket(r["genre"])
            if (sysname, os.path.splitext(r["fn"])[0]) in twin_stick:
                b = "twin-stick"
            if b:
                mpg.setdefault(MPG_PREFIX + b, set()).add(rom_line(sysname, r["fn"]))

    cols = {}
    for k, v in genres.items():
        cols[k] = sorted(v)
    for k, v in decades.items():
        cols[k] = sorted(v)
    cols["must-play"] = sorted(must)
    for k, v in mpg.items():
        cols[k] = sorted(v)
    stats = dict(picks_matched=hits, picks_unique=matched, dropped_decades=dropped)
    return cols, stats


def cfg_text(lines):
    return "\n".join(lines) + "\n"


def plan_writes(outdir, files):
    """files {path: text} -> list of (status, path); status new/changed/same."""
    res = []
    for path in sorted(files):
        if not os.path.exists(path):
            res.append(("new", path))
        elif read_text(path, "existing") == files[path]:
            res.append(("same", path))
        else:
            res.append(("changed", path))
    return res


def cmd_build(args):
    systems = load_gamelists(args.gamelists)
    picks = load_picks(args.picks)
    cols, stats = build_collections(systems, picks, args.min_decade, must_play_top=args.must_play_top,
                                    must_play_genre_min=stars_to_rating(args.must_play_genre_min))
    files = {os.path.join(args.out, "custom-%s.cfg" % k): cfg_text(v) for k, v in cols.items()}
    plan = plan_writes(args.out, files)

    print("systems: %s" % ", ".join("%s=%d" % (s, len(systems[s])) for s in sorted(systems)))
    print("picks: %d rows, %d matched (%d unique) -> MUST PLAY tie-break; must-play = top %d per system"
          % (len(picks), stats["picks_matched"], stats["picks_unique"], args.must_play_top))
    for k in sorted(cols):
        print("  %-12s %4d" % (k, len(cols[k])))
    for k in sorted(stats["dropped_decades"]):
        print("  (decade %s dropped: %d < %d games)" % (k, stats["dropped_decades"][k], args.min_decade))
    for status, path in plan:
        print("  %-7s %s" % (status.upper(), path))
    if os.path.isdir(args.out):
        stale = sorted(set(glob.glob(os.path.join(args.out, "custom-*.cfg"))) - set(files))
        for s in stale:
            print("  STALE   %s (not generated now; left alone)" % s)
    if not args.apply:
        print("plan only; re-run with --apply to write %d file(s) to %s" % (len(files), args.out))
        return EXIT_OK
    os.makedirs(args.out, exist_ok=True)
    for path, text in files.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    print("applied: wrote %d cfg(s) to %s" % (len(files), args.out))
    return EXIT_OK


# ----------------------------------------------------------- fbneo-gamelist

def make_genre_lookup(picks):
    pick_genre = {}
    for title, _s, g, _d, _t in picks:
        if g in PICK_GENRE:
            pick_genre.setdefault(norm(title), PICK_GENRE[g])

    def genre_for(title):
        nt = norm(title)
        if nt in pick_genre:
            return pick_genre[nt]
        best = (None, 0.0)
        for pt, g in pick_genre.items():
            if len(nt) >= 5 and (nt in pt or pt in nt):
                sc = 0.80 + 0.19 * (min(len(nt), len(pt)) / max(len(nt), len(pt)))
                if sc > best[1]:
                    best = (g, sc)
        return best[0] if best[1] >= 0.90 else None
    return genre_for, len(pick_genre)


def build_fbneo_gamelist(zips, zip2meta, genre_for):
    """-> (xml text, stats)."""
    zips = sorted(set(zips))
    titles = []
    for z in zips:
        t, _y = zip2meta.get(z, (None, None))
        titles.append(t or os.path.splitext(z)[0])
    names = clean_titles(titles)
    esc = lambda s: htmlmod.escape(s, quote=False)
    rows, named, yeared, genred = [], 0, 0, 0
    for z, raw, name in zip(zips, titles, names):
        title, year = zip2meta.get(z, (None, None))
        g = genre_for(title) if title else None
        e = ["\t<game>", "\t\t<path>./%s</path>" % z, "\t\t<name>%s</name>" % esc(name)]
        if title:
            named += 1
        if year:
            e.append("\t\t<releasedate>%s0101T000000</releasedate>" % year)
            yeared += 1
        if g:
            e.append("\t\t<genre>%s</genre>" % esc(g))
            genred += 1
        e.append("\t</game>")
        rows.append("\n".join(e))
    xml = '<?xml version="1.0"?>\n<gameList>\n' + "\n".join(rows) + "\n</gameList>\n"
    renamed = sum(1 for a, b in zip(titles, names) if a != b)
    return xml, dict(zips=len(zips), named=named, yeared=yeared, genred=genred, renamed=renamed)


def cmd_fbneo(args):
    zips = [z.strip() for z in read_text(args.zips, "zip list").splitlines() if z.strip()]
    if not zips:
        raise InputError("%s: empty zip list" % args.zips)
    zip2meta = parse_dat(read_text(args.dat, "dat"))
    if not zip2meta:
        raise InputError("%s: no game entries found" % args.dat)
    picks = load_picks(args.picks)
    genre_for, npg = make_genre_lookup(picks)
    xml, st = build_fbneo_gamelist(zips, zip2meta, genre_for)
    print("zips: %d  named: %d  with-year: %d  with-genre: %d  renamed-by-cleanup: %d"
          % (st["zips"], st["named"], st["yeared"], st["genred"], st["renamed"]))
    print("dat entries: %d  pick-genre titles: %d" % (len(zip2meta), npg))
    status = "new"
    if os.path.exists(args.out):
        status = "same" if read_text(args.out, "existing") == xml else "changed"
    print("  %-7s %s" % (status.upper(), args.out))
    if not args.apply:
        print("plan only; re-run with --apply to write %s" % args.out)
        return EXIT_OK
    d = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(d, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(xml)
    print("applied: wrote %s" % args.out)
    return EXIT_OK


# ------------------------------------------------------------------- names
# NAMING.md section 3: display names, applied in a fixed order.

ARCADE_SYSTEMS = ("fbneo", "cps1", "cps2", "cps3", "neogeo", "naomi", "atomiswave", "arcade", "mame")
# BIOS / device sets that sit next to the games but are not games (hidden in the gamelist).
BIOS_SETS = frozenset("""neogeo pgm qsound decocass bubsys isgsm skns konamigx cchip nmk004 ym2608 ym2610
midssio namcoc69 namcoc70 namcoc75 namcona1 taitofx1 hng64 stvbios naomi naomi2 awbios airlbios hod2bios
f355bios cpzn1 cpzn2 coh1000c coh3002c coh3002t megaplay megatech playch10 nss cvs vsbios acpsx
atpsx tps taitogn kinst atlantis maxaflex""".split())
_ARTICLE_RE = re.compile(r"^(?P<t>[^:()\[\]]+?), (?P<a>The|A|An)(?P<rest>(?=$|:| \(| \[).*)$")
_SUBSEP_RE = re.compile(r" +[-–—] +")
_M3U_DISC_RE = re.compile(r"^(?P<base>.*?)\s*\((?:Disc|Disk|CD)\s*\d+[^)]*\)", re.I)


def _depth0(text, idx):
    pre = text[:idx]
    return pre.count("(") == pre.count(")") and pre.count("[") == pre.count("]")


def _ws(s):
    s = re.sub(r"\s+", " ", s.replace(" ", " ")).strip()
    return re.sub(r" +([:,!?])", r"\1", s)


def _subtitle(s):
    """' : ' and a title/subtitle ' - ' become ': '. 'X-Men', 'Spider-Man' (no spaces
    around the hyphen) are never touched; only the first separator is converted and
    only when no ': ' is there already."""
    s = re.sub(r" +: *", ": ", s)
    if ": " in s:
        return s
    for m in _SUBSEP_RE.finditer(s):
        left, right = s[:m.start()], s[m.end():]
        if _depth0(s, m.start()) and len(left.strip()) >= 2 and len(right.strip()) >= 2 \
                and not right.startswith(("(", "[")):
            return left + ": " + right
    return s


def _pre_name(raw):
    """Rules 3.3-3.5 before tag stripping: dual title, whitespace, subtitle, article."""
    s = _ws(raw)
    s = re.sub(r"\[([^]]*)\]", r"(\1)", s)           # [!] [T-En ...] behave like (tags)
    cuts = [m.end() for m in re.finditer(r" ~ ", s) if _depth0(s, m.start())]   # not inside (NGM-033 ~ NGH-033)
    if cuts:
        s = s[cuts[-1]:]
    s = _subtitle(_ws(s))
    m = _ARTICLE_RE.match(s)
    if m:
        s = "%s %s%s" % (m.group("a"), m.group("t"), m.group("rest"))
    return _ws(s)


def _dat_primary(desc):
    """MAME/FBNeo descriptions list aliases as 'Western title / Other title': keep the first."""
    for m in re.finditer(r" +/ +", desc):
        if _depth0(desc, m.start()) and desc[:m.start()].strip():
            return desc[:m.start()]
    return desc


def display_names(raws):
    """Raw source names (list, one gamelist) -> display names, same order."""
    return [_ws(n) for n in clean_titles([_pre_name(r) for r in raws])]


def sort_name(name):
    m = re.match(r"^(The|A|An) (.+)$", name)
    return m.group(2) if m else None


def parse_dat_bios(text):
    """Zip names whose libretro description ends in BIOS (dat has no isbios flag)."""
    out = set()
    for blk in text.split("game (")[1:]:
        nm = re.search(r'name\s+"((?:[^"\\]|\\.)*)"', blk)
        rz = re.search(r'rom\s*\(\s*name\s+(\S+\.zip)', blk)
        if nm and rz and re.search(r"\bBIOS\s*$", nm.group(1)):
            out.add(rz.group(1))
    return out


def is_bios(system, fn, dat_bios=frozenset()):
    return system in ARCADE_SYSTEMS and (fn in dat_bios or os.path.splitext(fn)[0].lower() in BIOS_SETS)


_GAME_RE = re.compile(r"<game>.*?</game>", re.S)


def _tag(block, tag):
    m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), block, re.S)
    return htmlmod.unescape(m.group(1)) if m else None


def _set_tag(block, tag, value, after):
    """Replace <tag> or insert it after <after>; text-level so nothing else moves."""
    esc = htmlmod.escape(value, quote=False)
    pat = re.compile(r"<%s>.*?</%s>" % (tag, tag), re.S)
    if pat.search(block):
        return pat.sub(lambda _m: "<%s>%s</%s>" % (tag, esc, tag), block, count=1)
    am = re.search(r"([ \t]*)<%s>.*?</%s>" % (after, after), block, re.S)
    ind = am.group(1) if am else "\t\t"
    return block[:am.end()] + "\n%s<%s>%s</%s>" % (ind, tag, esc, tag) + block[am.end():]


def names_for_text(system, text, zip2title=None, dat_bios=frozenset()):
    """-> (new text, changes). changes = [(fn, before, after, hidden_set)]; only
    <name>, <sortname>, <hidden> are touched, never <path>."""
    zip2title = zip2title or {}
    blocks = _GAME_RE.findall(text)
    rows = []
    for b in blocks:
        p = (_tag(b, "path") or "").strip()
        fn = os.path.basename(p)
        nm = _tag(b, "name")
        src = (nm or "").strip()
        if not src and zip2title.get(fn):
            src = _dat_primary(zip2title[fn])          # DAT alias titles: 'Western / Japanese' -> Western
        src = src or os.path.splitext(fn)[0]
        rows.append((b, p, fn, nm, src))
    vis = [i for i, r in enumerate(rows) if r[1] and not is_bios(system, r[2], dat_bios)]
    disp = {}
    for i, n in zip(vis, display_names([rows[i][4] for i in vis])):
        disp[i] = n
    m3u = {os.path.splitext(r[2])[0].lower() for r in rows if r[2].lower().endswith(".m3u")}
    changes, new_blocks = [], []
    for i, (b, p, fn, nm, src) in enumerate(rows):
        nb, hid = b, False
        if not p:
            new_blocks.append(b)
            continue
        if i in disp:
            nb = _set_tag(nb, "name", disp[i], "path")
            so = sort_name(disp[i])
            if so:
                nb = _set_tag(nb, "sortname", so, "name")
        else:                                           # BIOS set: hidden, name left as is
            nb = _set_tag(nb, "hidden", "true", "name" if nm is not None else "path")
            hid = True
        dm = _M3U_DISC_RE.match(os.path.splitext(fn)[0])
        if dm and not fn.lower().endswith(".m3u") and dm.group("base").strip().lower() in m3u:
            nb = _set_tag(nb, "hidden", "true", "name" if nm is not None else "path")
            hid = True
        before = nm if nm is not None else ""
        after = disp.get(i, nm or src)
        if hid or before != after:
            changes.append((fn, before, after, hid))
        new_blocks.append(nb)
    it = iter(new_blocks)
    return _GAME_RE.sub(lambda _m: next(it), text), changes


_NOT_ROM_EXT = frozenset(".xml .txt .srm .sav .state .png .jpg .jpeg .gif .cfg .ini .bak .md .db .nfo .sfv .sha1 .md5 .log .sub .json".split())
EMPTY_GAMELIST = '<?xml version="1.0"?>\n<gameList>\n</gameList>\n'


def list_roms(roms_dir, system):
    """Top-level ROM files of <roms_dir>/<system> (sorted); [] when the dir is missing.
    Skips dot files, folders, saves/art/text, and .bin/.img next to a .cue of the same name."""
    d = os.path.join(roms_dir, system)
    if not os.path.isdir(d):
        return []
    files = sorted(f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)) and not f.startswith("."))
    cues = {os.path.splitext(f)[0] for f in files if f.lower().endswith(".cue")}
    return [f for f in files if os.path.splitext(f)[1].lower() not in _NOT_ROM_EXT
            and not (os.path.splitext(f)[1].lower() in (".bin", ".img") and os.path.splitext(f)[0] in cues)]


def add_missing(text, roms):
    """Append a minimal <game><path/></game> for every ROM file without an entry.
    Names are filled in by names_for_text. -> (text, [added file names])."""
    have = {os.path.basename((_tag(b, "path") or "").strip()) for b in _GAME_RE.findall(text)}
    missing = [f for f in roms if f not in have]
    if not missing:
        return text, []
    block = "".join("\t<game>\n\t\t<path>./%s</path>\n\t</game>\n" % htmlmod.escape(f, quote=False) for f in missing)
    i = text.rfind("</gameList>")
    if i < 0:
        raise InputError("gamelist has no </gameList>")
    return text[:i] + block + text[i:], missing


def _system_list(gamelists_dir, roms_dir):
    systems = {os.path.basename(os.path.dirname(p)): p
               for p in glob.glob(os.path.join(gamelists_dir, "*", "gamelist.xml"))
               if os.path.basename(os.path.dirname(p)) not in SKIP_SYSTEMS}
    if roms_dir:
        if not os.path.isdir(roms_dir):
            raise InputError("--roms is not a directory: %s" % roms_dir)
        for s in sorted(os.listdir(roms_dir)):
            if s not in systems and list_roms(roms_dir, s):
                systems[s] = os.path.join(gamelists_dir, s, "gamelist.xml")    # no gamelist yet: new one
    return sorted(systems.items())


def cleaned_texts(gamelists_dir, dat_path=None, roms_dir=None):
    """{system: gamelist text with NAMING.md names applied}, files untouched."""
    zip2title, dat_bios = {}, set()
    if dat_path:
        dat = read_text(dat_path, "dat")
        zip2title = {z: t for z, (t, _y) in parse_dat(dat).items() if t}
        dat_bios = parse_dat_bios(dat)
    out = {}
    for system, gl in _system_list(gamelists_dir, roms_dir):
        text = read_text(gl, "gamelist") if os.path.isfile(gl) else EMPTY_GAMELIST
        parse_gamelist(text, gl)
        if roms_dir:
            text, _added = add_missing(text, list_roms(roms_dir, system))
        out[system], _c = names_for_text(system, text, zip2title if system in ARCADE_SYSTEMS else {}, dat_bios)
    return out


def cmd_names(args):
    paths = _system_list(args.gamelists, args.roms)
    if not paths:
        raise InputError("no <system>/gamelist.xml under %s" % args.gamelists)
    zip2title, dat_bios = {}, set()
    if args.dat:
        dat = read_text(args.dat, "dat")
        zip2title = {z: t for z, (t, _y) in parse_dat(dat).items() if t}
        dat_bios = parse_dat_bios(dat)
    stamp = args.stamp or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    tot = dict(games=0, renamed=0, hidden=0, files=0)
    tot["added"] = 0
    for system, gl in paths:
        is_new = not os.path.isfile(gl)
        try:
            text = EMPTY_GAMELIST if is_new else open(gl, encoding="utf-8").read()
            parse_gamelist(text, gl)                 # malformed -> exit 3
        except OSError as e:
            raise InputError("%s: %s" % (gl, e))
        orig = text
        added = []
        if args.roms:
            text, added = add_missing(text, list_roms(args.roms, system))
        new, changes = names_for_text(system, text, zip2title if system in ARCADE_SYSTEMS else {}, dat_bios)
        n_games = len(_GAME_RE.findall(text))
        ren = [c for c in changes if c[1] != c[2] and not c[3] and c[0] not in added]
        adds = [c for c in changes if c[0] in added]
        tot["added"] += len(added)
        hid = [c for c in changes if c[3]]
        tot["games"] += n_games
        tot["renamed"] += len(ren)
        tot["hidden"] += len(hid)
        state = "NEW" if is_new and added else ("SAME" if new == orig else "CHANGED")
        print("%-10s %4d games  renamed %4d  added %4d  hidden %2d  %s" % (system, n_games, len(ren), len(added), len(hid), state))
        shown = (adds + [c for c in changes if c[0] not in added]) if args.show < 0 else \
            (adds[:args.show] + [c for c in changes if c[0] not in added][:args.show])
        for fn, a, b, h in shown:
            print("    %s  %s -> %r%s" % (fn, "(no entry)" if fn in added else repr(a), b, "  [hidden]" if h else ""))
        if new != orig and args.apply:
            if is_new:
                os.makedirs(os.path.dirname(gl), exist_ok=True)
            elif args.backup_dir:
                bak = os.path.join(args.backup_dir, stamp, system, "gamelist.xml")
                os.makedirs(os.path.dirname(bak), exist_ok=True)
            else:
                bak = gl + ".bak-names-" + stamp
            if not is_new:
                shutil.copy2(gl, bak)
            with open(gl, "w", encoding="utf-8") as f:
                f.write(new)
            tot["files"] += 1
    print("total: %(games)d games, %(renamed)d renamed, %(added)d added, %(hidden)d hidden" % tot)
    print("applied: %d gamelist(s) rewritten (backup first)" % tot["files"] if args.apply
          else "plan only; re-run with --apply to write")
    return EXIT_OK


# ------------------------------------------------------------------ duplicates

def parse_duplicates(text, label="duplicates"):
    """duplicates.tsv -> [(keep_sys, keep_path, hide_sys, hide_path)]. Tab separated, '#' comments and
    blank lines skipped. A row whose hide columns are '-' is a keep-only note (the game is checked to
    exist, nothing is hidden). Anything else with != 4 columns is an input error."""
    rows = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = line.rstrip("\r\n").split("\t")
        if len(cols) != 4 or not all(c.strip() for c in cols):
            raise InputError("%s:%d: want 4 tab-separated columns, got %r" % (label, n, line))
        rows.append(tuple(c.strip() for c in cols))
    return rows


def _block_for(text, path):
    """-> (start, end, block) of the <game> whose <path> is `path`, or None."""
    for m in _GAME_RE.finditer(text):
        if (_tag(m.group(0), "path") or "").strip() == path:
            return m.start(), m.end(), m.group(0)
    return None


def hide_in_text(text, path):
    """Set <hidden>true</hidden> on the game with this <path>. -> (new text, state) with state
    'hide' (changed), 'same' (already hidden) or None (no such path). Text-level: nothing else moves,
    nothing is removed (an explicit <hidden>false</hidden> is flipped to true)."""
    hit = _block_for(text, path)
    if hit is None:
        return text, None
    a, b, blk = hit
    if (_tag(blk, "hidden") or "").strip().lower() == "true":
        return text, "same"
    nb = _set_tag(blk, "hidden", "true", "name" if _tag(blk, "name") is not None else "path")
    return text[:a] + nb + text[b:], "hide"


def media_files(media_dir, system, path):
    """Scraped media of one game: <media>/<system>/<type>/<stem>.* (ES-DE downloaded_media layout)."""
    if not media_dir:
        return []
    stem = os.path.splitext(os.path.basename(path))[0]
    base = os.path.join(media_dir, system)
    out = []
    if os.path.isdir(base):
        for typ in sorted(os.listdir(base)):
            d = os.path.join(base, typ)
            if os.path.isdir(d) and not os.path.islink(d):
                out += sorted(os.path.join(d, f) for f in os.listdir(d)
                              if os.path.splitext(f)[0] == stem and os.path.isfile(os.path.join(d, f)))
    return out


def cmd_dupes(args):
    if args.delete and not args.roms:
        raise UsageError("--delete needs --roms (the ROM root, files are removed from <roms>/<system>/)")
    rows = parse_duplicates(read_text(args.duplicates, "duplicates file"), args.duplicates)
    if not rows:
        raise InputError("%s has no rows" % args.duplicates)
    texts, orig = {}, {}

    def load(system):
        if system in SKIP_SYSTEMS:
            raise InputError("%s is a generated virtual system, not a real one" % system)
        if system not in texts:
            gl = os.path.join(args.gamelists, system, "gamelist.xml")
            if not os.path.isfile(gl):
                raise InputError("no gamelist for system %r: %s" % (system, gl))
            texts[system] = orig[system] = read_text(gl, "gamelist")
            parse_gamelist(texts[system], gl)               # malformed -> exit 3
        return texts[system]

    def rom(system, path):
        return os.path.join(args.roms, system, path[2:] if path.startswith("./") else path)

    missing, per, doomed = [], {}, []
    for ksys, kpath, hsys, hpath in rows:
        if _block_for(load(ksys), kpath) is None or (args.roms and not os.path.exists(rom(ksys, kpath))):
            missing.append("keep %s %s: not in %s gamelist/ROMs" % (ksys, kpath, ksys))
        if (hsys, hpath) == ("-", "-"):
            continue
        c = per.setdefault(hsys, dict(hide=0, same=0, delete=0, gone=0))
        hit = _block_for(load(hsys), hpath)
        if hit is None:
            # already removed (an earlier --delete): fine only when the ROM file is gone too
            if args.roms and not os.path.exists(rom(hsys, hpath)):
                c["gone"] += 1
                continue
            missing.append("hide %s %s: not in %s gamelist" % (hsys, hpath, hsys))
            continue
        if args.delete:
            a, b, _blk = hit
            t = texts[hsys]
            # drop the whole <game> block plus the line break before it
            while a > 0 and t[a - 1] in " \t":
                a -= 1
            if a > 0 and t[a - 1] == "\n":
                a -= 1
            texts[hsys] = t[:a] + t[b:]
            files = [rom(hsys, hpath)] if os.path.exists(rom(hsys, hpath)) else []
            doomed.append((hsys, hpath, files + media_files(args.media, hsys, hpath)))
            c["delete"] += 1
        else:
            new, state = hide_in_text(texts[hsys], hpath)
            texts[hsys] = new
            c[state] += 1
    if missing:                                              # nothing is written on any error
        raise InputError("duplicates.tsv does not match the gamelists:\n  " + "\n  ".join(missing))

    stamp = args.stamp or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    tot = dict(hide=0, same=0, delete=0, gone=0, keeps=sum(1 for r in rows if (r[2], r[3]) == ("-", "-")))
    files = 0
    for system in sorted(per):
        c = per[system]
        for k in ("hide", "same", "delete", "gone"):
            tot[k] += c[k]
        state = "SAME" if texts[system] == orig[system] else "CHANGED"
        print("%-10s hide %3d  already hidden %3d  delete %3d  already gone %3d  %s"
              % (system, c["hide"], c["same"], c["delete"], c["gone"], state))
        if state == "CHANGED" and args.apply:
            gl = os.path.join(args.gamelists, system, "gamelist.xml")
            if args.backup_dir:
                bak = os.path.join(args.backup_dir, stamp, system, "gamelist.xml")
                os.makedirs(os.path.dirname(bak), exist_ok=True)
            else:
                bak = gl + ".bak-dupes-" + stamp
            shutil.copy2(gl, bak)
            with open(gl, "w", encoding="utf-8") as f:
                f.write(texts[system])
            files += 1
    n_files, n_bytes = 0, 0
    for hsys, hpath, paths in doomed:
        for fp in paths:
            n_bytes += os.path.getsize(fp)
            n_files += 1
            if args.apply:
                os.remove(fp)
        if args.show:
            print("    delete %s %s (%d file(s))" % (hsys, hpath, len(paths)))
    changed = tot["hide"] + tot["delete"]
    print("total: %(hide)d to hide, %(same)d already hidden, %(delete)d to delete, %(gone)d already gone, "
          "%(keeps)d keep-only note(s)  " % tot + ("SAME" if changed == 0 else "CHANGED"))
    if args.delete:
        print("delete: %d file(s) (ROMs + scraped media), %.1f MB" % (n_files, n_bytes / 1048576.0))
    print("applied: %d gamelist(s) rewritten (backup first)%s" % (files, ", %d file(s) deleted" % n_files if args.delete else "")
          if args.apply else "plan only; re-run with --apply to write")
    return EXIT_OK


# ------------------------------------------------------------------- nested

def cmd_nested(args):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import nested  # noqa: E402  (next to this file; not a stdlib name)
    try:
        nested.hidden_names()
    except (OSError, ValueError) as e:
        raise InputError("MAME BIOS/device list: %s" % e)
    texts = cleaned_texts(args.gamelists, args.dat, args.roms)   # same display names as `names`
    systems = load_gamelists(args.gamelists, texts)
    picks = load_picks(args.picks)
    cols, stats = build_collections(systems, picks, args.min_decade, must_play_top=args.must_play_top,
                                    must_play_genre_min=stars_to_rating(args.must_play_genre_min))
    try:
        games = nested.raw_games(args.gamelists, texts)
    except ET.ParseError as e:
        raise InputError("gamelist: malformed XML (%s)" % e)
    model = nested.build_model(cols, systems, args.flat_must_play)
    titles = {}
    if args.series:
        try:
            rules = nested.load_series(args.series)
        except (OSError, ValueError) as e:
            raise InputError("--series: %s" % e)
        titles = nested.series_titles(rules)
        folders = nested.build_series(rules, games)
        if folders:
            model["series"] = folders
            model = {v: model[v] for v, _t in nested.VIRTUAL if v in model}
    if args.ra_matches:                  # 'Has achievements' folder (#46, ra_progress.py write --matches)
        try:
            ra = nested.load_ra_matches(args.ra_matches)
        except (OSError, ValueError) as e:
            raise InputError("--ra-matches: %s" % e)
        if args.ra_folder_in not in nested.VIRTUAL_SLUGS:
            raise UsageError("--ra-folder-in %s is not a virtual system (%s)" % (args.ra_folder_in, " ".join(nested.VIRTUAL_SLUGS)))
        n_ra = nested.add_achievements(model, ra, systems, args.ra_folder_in)
        print("achievements: %s" % ("%d game(s) -> %s/%s" % (n_ra, args.ra_folder_in, nested.ACHIEVEMENTS_SLUG) if n_ra
                                    else "no folder (no matched visible game, or no %s entry)" % args.ra_folder_in))
    out = args.out
    roms_dir = os.path.join(out, "roms_nested")
    media_dir = os.path.join(out, "downloaded_media")
    if args.media and not os.path.isdir(args.media):
        raise InputError("--media is not a directory: %s" % args.media)
    settings_cmd = args.settings_command if args.settings_entry else None
    trophies_cmd = args.trophies_command if args.trophies_entry else None
    fightcade_cmd = args.fightcade_command if args.fightcade_entry else None
    quit_cmd = args.quit_command if args.quit_entry else None
    files = {os.path.join(out, "custom_systems", "es_systems.xml"):
             nested.systems_xml(model, args.nested_root, args.launcher, settings_cmd, trophies_cmd,
                                fightcade_cmd, quit_cmd)}
    try:
        ftexts = nested.load_folder_text(args.folder_text) if args.folder_text else {}
    except (OSError, ValueError) as e:
        raise InputError("--folder-text: %s" % e)
    for v, folders in model.items():
        gl = os.path.join(out, "gamelists", v, "gamelist.xml")
        files[gl] = nested.gamelist_xml(v, folders, games, nested.existing_state(gl), ftexts, titles)
    links = nested.symlink_plan(model, args.rom_root, roms_dir)
    pointers = nested.pointer_plan(model, args.rom_root, roms_dir)      # crossover games in 2+ folders (#116)
    if args.settings_entry:              # SETTINGS (#34): one link to the entry file the settings role installs
        sgl = os.path.join(out, "gamelists", nested.SETTINGS_SLUG, "gamelist.xml")
        files[sgl] = nested.settings_gamelist_xml(nested.existing_state(sgl))
        links[os.path.join(roms_dir, nested.SETTINGS_SLUG, nested.SETTINGS_LINK)] = args.settings_entry
    if args.trophies_entry:              # TROPHIES (#176): one link to the entry file the settings role installs
        tgl = os.path.join(out, "gamelists", nested.TROPHIES_SLUG, "gamelist.xml")
        files[tgl] = nested.trophies_gamelist_xml(nested.existing_state(tgl))
        links[os.path.join(roms_dir, nested.TROPHIES_SLUG, nested.TROPHIES_LINK)] = args.trophies_entry
    for entry, slug, link, make in ((args.fightcade_entry, nested.FIGHTCADE_SLUG, nested.FIGHTCADE_LINK,
                                     nested.fightcade_gamelist_xml),
                                    (args.quit_entry, nested.QUIT_SLUG, nested.QUIT_LINK, nested.quit_gamelist_xml)):
        if entry:                        # FIGHTCADE / QUIT TO MAIN MENU (#214): one link to the entry script each
            egl = os.path.join(out, "gamelists", slug, "gamelist.xml")
            files[egl] = make(nested.existing_state(egl))
            links[os.path.join(roms_dir, slug, link)] = entry
    media = nested.media_plan(model, args.media, args.media_target or args.media, media_dir, args.folder_art)

    print("virtual systems:")
    for v, _t in nested.VIRTUAL:
        if v in model:
            n = sum(len(p) for p in model[v].values())
            print("  %-10s %2d folder(s)  %5d link(s)" % (v, len([f for f in model[v] if f]), n))
    for slug, entry, flag in ((nested.FIGHTCADE_SLUG, args.fightcade_entry, "--fightcade-entry"),
                              (nested.QUIT_SLUG, args.quit_entry, "--quit-entry")):
        print("  %-10s %s" % (slug, ("1 entry -> %s" % entry) if entry else "none (no %s)" % flag))
    print("  %-10s %s" % (nested.TROPHIES_SLUG, ("1 entry -> %s" % args.trophies_entry) if args.trophies_entry
                          else "none (no --trophies-entry)"))
    print("  %-10s %s" % (nested.SETTINGS_SLUG, ("1 entry -> %s" % args.settings_entry) if args.settings_entry
                          else "none (no --settings-entry)"))
    for v, folders in model.items():       # ES-DE hides MAME BIOS/device names on arcade platforms (#nested-bios)
        for f in folders:
            if nested.folder_dir(f) != f:
                print("  folder %s/%s -> %s/ on disk (MAME %s name; ES-DE would hide it)"
                      % (v, f, nested.folder_dir(f), nested.hidden_kind(f)))
    bios = sorted("%s/%s" % (s, r["fn"]) for s in systems for r in systems[s] if nested.is_bios_file(r["fn"]))
    if bios:
        print("  not linked (MAME BIOS sets): %s" % " ".join(bios))
    for status, path in plan_writes(out, files):
        print("  %-7s %s" % (status.upper(), path))
    rc, rstale = nested.plan_links(links, roms_dir)
    mc, mstale = nested.plan_links(media, media_dir)
    pc, pstale = nested.plan_pointers(pointers, roms_dir)
    print("  rom links:   new %(new)d same %(same)d changed %(changed)d" % rc + "  stale %d" % len(rstale))
    print("  crossover pointers: new %(new)d same %(same)d changed %(changed)d" % pc + "  stale %d" % len(pstale))
    if args.media:
        print("  media links: new %(new)d same %(same)d changed %(changed)d" % mc + "  stale %d" % len(mstale))
    else:
        print("  media links: skipped (no --media)")
    if not args.apply:
        print("plan only; re-run with --apply to write %d file(s) + %d link(s) under %s"
              % (len(files), len(links) + len(pointers) + len(media), out))
        return EXIT_OK
    for path, text in files.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    nested.apply_links(links, rstale, roms_dir)
    nested.apply_pointers(pointers, pstale, roms_dir)
    nested.apply_links(media, mstale if args.media else [], media_dir)
    print("applied: %d file(s), %d rom link(s), %d media link(s) under %s" % (len(files), len(links), len(media), out))
    return EXIT_OK


# ----------------------------------------------------------------- metadata

def cmd_metadata(args):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import metadata  # noqa: E402  (next to this file; not a stdlib name)
    import types
    return metadata.run(args, types.SimpleNamespace(**globals()))   # works however collgen was loaded


# --------------------------------------------------------------------- cli

def nested_settings_command():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import nested  # noqa: E402
    return nested.SETTINGS_COMMAND


def nested_trophies_command():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import nested  # noqa: E402
    return nested.TROPHIES_COMMAND


def make_parser():
    p = argparse.ArgumentParser(prog="collgen.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="gamelists + picks -> custom-*.cfg collections")
    b.add_argument("--gamelists", required=True, help="dir with <system>/gamelist.xml")
    b.add_argument("--picks", required=True, help="cabinet-picks.html")
    b.add_argument("--out", default="collections_out", help="output dir (default ./collections_out)")
    b.add_argument("--min-decade", type=int, default=MIN_DECADE_GAMES,
                   help="min games for a 5-year decade bucket (default %d)" % MIN_DECADE_GAMES)
    b.add_argument("--must-play-top", type=positive_int, default=MUST_PLAY_TOP,
                   help="MUST PLAY: the best-rated N games per system (default %d)" % MUST_PLAY_TOP)
    b.add_argument("--must-play-genre-min", type=float, default=MUST_PLAY_GENRE_MIN * 5,
                   help="MUST PLAY GENRES: minimum rating in stars, 0-5 (default %.1f)" % (MUST_PLAY_GENRE_MIN * 5))
    b.add_argument("--apply", action="store_true", help="write the files (default: plan only)")
    b.set_defaults(fn=cmd_build)
    f = sub.add_parser("fbneo-gamelist", help="libretro dat + zip list -> gamelist.xml")
    f.add_argument("--dat", required=True, help="libretro 'FBNeo - Arcade Games.dat'")
    f.add_argument("--zips", required=True, help="text file, one zip name per line")
    f.add_argument("--picks", required=True, help="cabinet-picks.html (genre source)")
    f.add_argument("--out", default="fbneo_gamelist.xml", help="output file")
    f.add_argument("--apply", action="store_true", help="write the file (default: plan only)")
    f.set_defaults(fn=cmd_fbneo)
    s = sub.add_parser("names", help="clean <name>/<sortname>/<hidden> in gamelists (NAMING.md)")
    s.add_argument("--gamelists", required=True, help="dir with <system>/gamelist.xml")
    s.add_argument("--dat", help="libretro 'FBNeo - Arcade Games.dat' (names for unnamed arcade sets, BIOS)")
    s.add_argument("--roms", help="dir with <system>/ ROM folders: every ROM file without a <game> entry gets one")
    s.add_argument("--show", type=int, default=5, help="before->after lines per system (-1 = all, default 5)")
    s.add_argument("--backup-dir", help="back up to DIR/<stamp>/<system>/gamelist.xml (default: gamelist.xml.bak-names-<stamp> beside it)")
    s.add_argument("--stamp", help="backup stamp (default: now)")
    s.add_argument("--apply", action="store_true", help="rewrite the gamelists (default: plan only)")
    s.set_defaults(fn=cmd_names)
    d = sub.add_parser("dupes", help="hide the weaker duplicate versions listed in duplicates.tsv")
    d.add_argument("--gamelists", required=True, help="dir with <system>/gamelist.xml")
    d.add_argument("--duplicates", default=DUPLICATES_TSV, help="TSV keep-system keep-path hide-system hide-path (default: personal/skills/collections/duplicates.tsv in a private checkout, else duplicates.tsv beside collgen.py)")
    d.add_argument("--backup-dir", help="back up to DIR/<stamp>/<system>/gamelist.xml (default: gamelist.xml.bak-dupes-<stamp> beside it)")
    d.add_argument("--stamp", help="backup stamp (default: now)")
    d.add_argument("--roms", help="ROM root (<roms>/<system>/<file>); lets an already deleted hide count as gone")
    d.add_argument("--media", help="downloaded_media root; with --delete its <system>/<type>/<stem>.* files go too")
    d.add_argument("--delete", action="store_true", help="delete the hide entries (gamelist <game>, ROM file, media) instead of hiding")
    d.add_argument("--show", action="store_true", help="list each deletion")
    d.add_argument("--apply", action="store_true", help="rewrite the gamelists / delete the files (default: plan only)")
    d.set_defaults(fn=cmd_dupes)
    n = sub.add_parser("nested", help="gamelists + picks -> MUST PLAY/GENRES/SERIES/DECADES/SYSTEMS virtual systems")
    n.add_argument("--gamelists", required=True, help="dir with <system>/gamelist.xml")
    n.add_argument("--picks", required=True, help="cabinet-picks.html")
    n.add_argument("--out", default="nested_out", help="output dir (default ./nested_out)")
    n.add_argument("--rom-root", default="/data/roms", help="real ROM root on the cabinet (symlink targets)")
    n.add_argument("--nested-root", default="/data/nested", help="where roms_nested/ is deployed on the cabinet")
    n.add_argument("--launcher", default="python3 " + os.path.join(os.path.expanduser("~"), "ES-DE", "scripts", "launch.py"),
                   help="ES-DE <command> prefix (the dispatcher; %%ROM%% is appended)")
    n.add_argument("--media", help="local copy of the cabinet's downloaded_media (<system>/<type>/<file>)")
    n.add_argument("--media-target", help="downloaded_media path on the cabinet (default: --media)")
    n.add_argument("--folder-art", help="dir <virtual>/<folder>.png of folder pictograms (optional)")
    n.add_argument("--folder-text", help="folder_text.tsv: virtual<TAB>folder<TAB>blurb for the folder <desc> (optional)")
    n.add_argument("--series", help="series.tsv: franchise folders for the SERIES entry (optional; without it there is no SERIES)")
    n.add_argument("--flat-must-play", action="store_true",
                   help="one flat MUST PLAY list (default: one folder per system)")
    n.add_argument("--roms", help="dir with <system>/ ROM folders (adds entries for ROMs without one, as in `names`)")
    n.add_argument("--dat", help="libretro FBNeo dat (names for unnamed arcade sets, as in `names`)")
    n.add_argument("--min-decade", type=int, default=MIN_DECADE_GAMES)
    n.add_argument("--must-play-top", type=positive_int, default=MUST_PLAY_TOP,
                   help="MUST PLAY: the best-rated N games per system (default %d)" % MUST_PLAY_TOP)
    n.add_argument("--must-play-genre-min", type=float, default=MUST_PLAY_GENRE_MIN * 5,
                   help="MUST PLAY GENRES: minimum rating in stars, 0-5 (default %.1f)" % (MUST_PLAY_GENRE_MIN * 5))
    n.add_argument("--settings-entry", help="SETTINGS app entry file on the cabinet (#34): adds the SETTINGS system "
                   "last, linking to it (omit = no SETTINGS entry)")
    n.add_argument("--settings-command", default=nested_settings_command(),
                   help="ES-DE <command> prefix of the SETTINGS system (%%ROM%% is appended)")
    n.add_argument("--trophies-entry", help="TROPHIES page entry file on the cabinet (#176): adds the TROPHIES system "
                   "before SETTINGS, linking to it (omit = no TROPHIES entry)")
    n.add_argument("--trophies-command", default=nested_trophies_command(),
                   help="ES-DE <command> prefix of the TROPHIES system (%%ROM%% is appended)")
    n.add_argument("--fightcade-entry", help="esde_entry.sh on the cabinet (#214): adds the FIGHTCADE system before "
                   "TROPHIES, linking to it (omit = no FIGHTCADE entry; the role omits it without Fightcade mode)")
    n.add_argument("--fightcade-command", default=nested.FIGHTCADE_COMMAND,
                   help="ES-DE <command> prefix of the FIGHTCADE system (%%ROM%% is appended)")
    n.add_argument("--quit-entry", help="esde_entry.sh on the cabinet (#214): adds the QUIT TO MAIN MENU system last, "
                   "linking to it (omit = no QUIT entry)")
    n.add_argument("--quit-command", default=nested.QUIT_COMMAND,
                   help="ES-DE <command> prefix of the QUIT TO MAIN MENU system (%%ROM%% is appended)")
    n.add_argument("--ra-matches", help="ra_progress.py --matches file: adds a 'Has achievements' folder (#46, optional)")
    n.add_argument("--ra-folder-in", default="genres", help="virtual system that gets that folder (default genres)")
    n.add_argument("--apply", action="store_true", help="write the files + links (default: plan only)")
    n.set_defaults(fn=cmd_nested)
    m = sub.add_parser("metadata", help="fill missing metadata + media from RomM / dat / picks (adds only)")
    m.add_argument("--gamelists", required=True, help="dir with <system>/gamelist.xml")
    m.add_argument("--romm", required=True, help="RomM export, JSON Lines (romm-export.sql)")
    m.add_argument("--dat", help="libretro FBNeo dat (arcade year + manufacturer when RomM has none)")
    m.add_argument("--picks", help="cabinet-picks.html (genre; the maintainer's code wins over RomM's)")
    m.add_argument("--media", help="media root (<media>/<system>/<covers|screenshots>/<stem>.<ext>); without it media is skipped")
    m.add_argument("--staging", help="local copy of RomM resources (<staging>/<RomM path>) to copy media from")
    m.add_argument("--want", help="write the RomM resource paths still to stage (one per line) to FILE")
    m.add_argument("--max-bytes", type=int, default=5 * 1024 * 1024, help="refuse media files bigger than this")
    m.add_argument("--thumbnails", help="libretro-thumbnails fallback (off by default): TSV system repo sha")
    m.add_argument("--fetch-dir", help="dir for downloaded thumbnails (<dir>/<system>/<type>/<name>.png)")
    m.add_argument("--fetch", action="store_true", help="download the thumbnails (--fetch-dir must be new or empty)")
    m.add_argument("--show", type=int, default=10, help="unmatched games listed (-1 = all, default 10)")
    m.add_argument("--backup-dir", help="back up to DIR/<stamp>/<system>/gamelist.xml (default: gamelist.xml.bak-metadata-<stamp> beside it)")
    m.add_argument("--stamp", help="backup stamp (default: now)")
    m.add_argument("--apply", action="store_true", help="rewrite the gamelists + copy media (default: plan only)")
    m.set_defaults(fn=cmd_metadata)
    return p


def main(argv=None):
    args = make_parser().parse_args(argv)   # argparse exits 2 on usage errors
    try:
        return args.fn(args)
    except InputError as e:
        print("error: %s" % e, file=sys.stderr)
        return EXIT_INPUT
    except UsageError as e:
        print("usage error: %s" % e, file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
