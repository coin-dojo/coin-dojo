"""Fill missing ES-DE metadata + media from data we already have (stdlib only; ticket #60).

Imported by collgen.py (`collgen.py metadata`). Sources, in order:
  1. a RomM export (JSON Lines, one object per ROM; `romm-export.sql` beside this file makes it),
     matched by ROM file name (then by the tag-free file stem, unique only), guarded by a title check
  2. the libretro FBNeo dat: year + manufacturer for arcade sets RomM has no data for
  3. Cabinet Picks: genre (the maintainer's curated code wins over RomM's free-text genres, see SKILL.md)
  4. optional, off by default: libretro-thumbnails pinned to one commit per repo (--thumbnails)

It only ADDS: a field is written only when the <game> has no such element (or an empty one), a media
file only when <media>/<system>/<type>/<stem>.* does not exist. Hidden games and the virtual systems
are skipped. Gamelist edits are text-level (nothing else in the file moves). Plan by default.
"""
import datetime
import html as htmlmod
import json
import os
import re
import shutil
import urllib.parse
import urllib.request
from difflib import SequenceMatcher

# cabinet system dir -> RomM platform fs_slugs searched in order
PLATFORMS = {"fbneo": ["arcade"], "cps1": ["cps1", "arcade"], "cps2": ["cps2", "arcade"],
             "cps3": ["cps3", "arcade"], "neogeo": ["neogeomvs", "arcade"], "snes": ["snes"],
             "genesis": ["genesis"], "n64": ["n64"], "psx": ["psx"], "dreamcast": ["dc"],
             "saturn": ["saturn"], "segacd": ["segacd"], "nes": ["nes"], "pcengine": ["tg16", "pcengine"]}
ARCADE_DAT_SYSTEMS = ("fbneo", "cps1", "cps2", "cps3", "neogeo")
FIELDS = ("desc", "rating", "releasedate", "developer", "publisher", "genre", "players")
# ES-DE's element order inside <game>; new elements go after the closest earlier one present
ORDER = ("path", "name", "sortname", "desc", "rating", "releasedate", "developer", "publisher",
         "genre", "players")
MEDIA = ("covers", "screenshots")
MAX_BYTES = 5 * 1024 * 1024
MAGIC = ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"))
TITLE_MIN = 0.60               # title guard: SequenceMatcher ratio below this (and no overlap) = wrong match
THUMB_HOST = "raw.githubusercontent.com"
THUMB_DIRS = {"covers": "Named_Boxarts", "screenshots": "Named_Snaps"}
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_PLAYERS = re.compile(r"^\d{1,2}(-\d{1,2})?$")


# ------------------------------------------------------------------ sources

def _str_or_none(v):
    return v is None or isinstance(v, str)


def _list_of_str(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def _rel_ok(p):
    """A RomM resource path: relative, no '..', no backslash, no NUL."""
    return (isinstance(p, str) and p and not p.startswith("/") and "\\" not in p and "\0" not in p
            and ".." not in p.split("/"))


def parse_romm(text, cg, label="romm"):
    """RomM JSON Lines -> {platform: {fs_name: row}}. Any malformed line is an input error (nothing is
    written on any error): not a JSON object, missing platform/fs_name, wrong field types, a media path
    that is absolute or climbs out ('..'), or a duplicate (platform, fs_name)."""
    out = {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        where = "%s:%d" % (label, n)
        try:
            d = json.loads(line)
        except ValueError as e:
            raise cg.InputError("%s: not JSON (%s)" % (where, e))
        if not isinstance(d, dict):
            raise cg.InputError("%s: not a JSON object" % where)
        plat, fn = d.get("platform"), d.get("fs_name")
        if not (isinstance(plat, str) and plat and isinstance(fn, str) and fn and "/" not in fn):
            raise cg.InputError("%s: needs string platform and fs_name (no '/')" % where)
        bad = [k for k in ("name", "summary", "cover") if not _str_or_none(d.get(k))]
        bad += [k for k in ("alt_names", "genres", "developers", "publishers", "screenshots")
                if d.get(k) is not None and not _list_of_str(d.get(k))]
        if d.get("release_ms") is not None and (isinstance(d["release_ms"], bool)
                                                or not isinstance(d["release_ms"], int)):
            bad.append("release_ms")
        r = d.get("rating")
        if r is not None and (isinstance(r, bool) or not isinstance(r, (int, float)) or not 0 <= r <= 100):
            bad.append("rating (want 0-100 or null)")
        rc = d.get("rating_count")
        if rc is not None and (isinstance(rc, bool) or not isinstance(rc, int) or rc < 0):
            bad.append("rating_count")
        if d.get("players") is not None and not isinstance(d["players"], (str, int)):
            bad.append("players")
        paths = ([d["cover"]] if d.get("cover") else []) + list(d.get("screenshots") or [])
        if any(not _rel_ok(p) for p in paths):
            bad.append("media path")
        if bad:
            raise cg.InputError("%s: bad field(s) %s" % (where, ", ".join(bad)))
        if fn in out.setdefault(plat, {}):
            raise cg.InputError("%s: duplicate %s/%s" % (where, plat, fn))
        out[plat][fn] = d
    if not out:
        raise cg.InputError("%s: no rows" % label)
    return out


def parse_dat_meta(text):
    """libretro dat -> {zip: (year or None, publisher or None)}."""
    out = {}
    for blk in text.split("game (")[1:]:
        rz = re.search(r'rom\s*\(\s*name\s+(\S+\.zip)', blk)
        if not rz:
            continue
        yr = re.search(r'releaseyear\s+"(\d{4})"', blk)
        pb = re.search(r'publisher\s+"((?:[^"\\]|\\.)*)"', blk)
        pub = pb.group(1).strip() if pb else None
        if pub and (pub.endswith("?") or pub.lower() in ("bootleg", "<unknown>", "unknown")):
            pub = None
        out[rz.group(1)] = (yr.group(1) if yr else None, pub)
    return out


def parse_pins(text, cg, label="pins"):
    """libretro-thumbnails pins TSV: system<TAB>repo<TAB>40-hex commit sha ('#' comments)."""
    pins = {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = line.rstrip("\r\n").split("\t")
        if len(cols) != 3 or not re.match(r"^[A-Za-z0-9_.-]+$", cols[1]) or not _SHA.match(cols[2]):
            raise cg.InputError("%s:%d: want system<TAB>repo<TAB>40-hex sha, got %r" % (label, n, line))
        pins[cols[0]] = (cols[1], cols[2])
    return pins


# ----------------------------------------------------------------- matching

def _stem_key(fn, cg):
    return cg.norm(os.path.splitext(fn)[0])


def _alike(a, b, cg):
    na, nb = cg.norm(a), cg.norm(b)
    if not na or not nb:
        return False
    if na == nb or (min(len(na), len(nb)) >= 4 and (na in nb or nb in na)):
        return True
    ta, tb = set(na.split()), set(nb.split())
    if len(ta & tb) / float(len(ta | tb)) >= 0.5:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= TITLE_MIN


def title_ok(title, fn, row, cg):
    """Guard against a wrong match: the gamelist title must look like RomM's name or one of its
    alternative names ('Jackal' / 'Top Gunner'). A gamelist title that is just the file stem carries
    no independent evidence, so the file name match stands; so does a RomM row with no names."""
    names = [row.get("name") or ""] + list(row.get("alt_names") or [])
    if not any(n.strip() for n in names) or title.strip() == os.path.splitext(fn)[0]:
        return True
    return any(_alike(title, n, cg) for n in names if n.strip())


def find_row(system, fn, title, romm, cg):
    """-> (row, how) with how 'file', 'stem', 'mismatch' (found but the title guard refused it) or
    (None, None). File name first, then the tag-free stem when exactly one RomM row has it."""
    for plat in PLATFORMS.get(system, [system]):
        row = romm.get(plat, {}).get(fn)
        if row is not None:
            return (row, "file") if title_ok(title, fn, row, cg) else (row, "mismatch")
    key = _stem_key(fn, cg)
    for plat in PLATFORMS.get(system, [system]):
        hits = [r for f, r in sorted(romm.get(plat, {}).items()) if _stem_key(f, cg) == key]
        if len(hits) > 1:                                  # 'X (USA).chd' + 'X (USA).m3u': same extension wins
            ext = os.path.splitext(fn)[1].lower()
            hits = [r for r in hits if os.path.splitext(r["fs_name"])[1].lower() == ext]
        if len(hits) == 1:
            row = hits[0]
            return (row, "stem") if title_ok(title, fn, row, cg) else (row, "mismatch")
    return None, None


def es_rating(r):
    """RomM 0-100 average -> ES-DE <rating>: 0-1 rounded to the nearest 0.05, written like ES-DE
    ('0.8', '0.65', '1', '0'). None for no rating."""
    if r is None:
        return None
    v = round(int(float(r) / 5.0 + 0.5) * 0.05, 2)       # half up (12.5 -> 0.15), not Python's half-even
    return ("%.2f" % v).rstrip("0").rstrip(".") or "0"


def release(ms):
    """RomM epoch milliseconds -> ES-DE releasedate, or None outside 1971..2039."""
    if ms is None:
        return None
    d = datetime.datetime(1970, 1, 1) + datetime.timedelta(milliseconds=ms)
    return d.strftime("%Y%m%dT000000") if 1971 <= d.year < 2040 else None


def romm_genre(genres, cg):
    """RomM genre list -> one ES-DE string, entries joined so genre_bucket's priority decides the
    bucket. IGDB's generic tags are dropped when something more specific is there: 'Arcade' (not a
    genre) always, and a bare 'Shooter' when another entry already names a bucket (IGDB tags
    Elevator Action / Robocop 'Shooter, Platform', which would otherwise land in shmups)."""
    gs = list(dict.fromkeys(g.strip() for g in (genres or []) if g.strip()))
    if len(gs) > 1:
        gs = [g for g in gs if g.lower() != "arcade"] or gs
    rest = [g for g in gs if g.lower() != "shooter"]
    if len(rest) < len(gs) and any(cg.genre_bucket(g) for g in rest):
        gs = rest
    return ", ".join(gs) or None


def values_for(system, fn, row, dat, pick_genre, cg):
    """-> {field: (value, source)} for every field we can offer this game."""
    v = {}
    if row:
        if (row.get("summary") or "").strip():
            v["desc"] = (_CTRL.sub("", row["summary"].strip()), "romm")
        if release(row.get("release_ms")):
            v["releasedate"] = (release(row["release_ms"]), "romm")
        devs = [x.strip() for x in row.get("developers") or [] if x.strip()]
        pubs = [x.strip() for x in row.get("publishers") or [] if x.strip()]
        if devs:
            v["developer"] = (devs[0], "romm")
        if pubs:
            v["publisher"] = (pubs[0], "romm")
        g = romm_genre(row.get("genres"), cg)
        if g:
            v["genre"] = (g, "romm")
        pl = row.get("players")
        if pl is not None and _PLAYERS.match(str(pl)):
            v["players"] = (str(pl), "romm")
        if es_rating(row.get("rating")) is not None:
            v["rating"] = (es_rating(row["rating"]), "romm")
    if system in ARCADE_DAT_SYSTEMS and fn in dat:
        yr, pub = dat[fn]
        if yr and "releasedate" not in v:
            v["releasedate"] = ("%s0101T000000" % yr, "dat")
        if pub and "developer" not in v:
            v["developer"] = (pub, "dat")
        if pub and "publisher" not in v:
            v["publisher"] = (pub, "dat")
    pg = pick_genre.get((system, fn))
    if pg:
        v["genre"] = (pg, "picks")       # curated code, maps 1:1 to a collection bucket
    return v


def pick_genres(picks, systems, cg):
    """{(system, fn): ES-DE genre} for every Cabinet Pick that matches a visible game."""
    out = {}
    for title, sysc, g, _d, _t in picks:
        if sysc in cg.SYSMAP and g in cg.PICK_GENRE:
            m = cg.match_pick(title, cg.SYSMAP[sysc], systems)
            if m:
                out.setdefault(m, cg.PICK_GENRE[g])
    return out


# ------------------------------------------------------------- gamelist text

def _has(block, tag, cg):
    """True when <tag> exists with a non-blank value."""
    v = cg._tag(block, tag)
    return v is not None and v.strip() != ""


def add_tag(block, tag, value):
    """Insert <tag>value</tag> (or fill an empty <tag></tag> / <tag/>). Never replaces a value."""
    esc = htmlmod.escape(value, quote=False)
    empty = re.compile(r"<%s\s*/>|<%s>\s*</%s>" % (tag, tag, tag))
    if empty.search(block):
        return empty.sub(lambda _m: "<%s>%s</%s>" % (tag, esc, tag), block, count=1)
    at, ind = None, "\t\t"
    for prev in ORDER[:ORDER.index(tag)]:
        m = re.search(r"([ \t]*)<%s>.*?</%s>" % (prev, prev), block, re.S)
        if m:
            at, ind = m.end(), m.group(1) or ind
    if at is None:
        raise ValueError("game block has no <path>")
    return block[:at] + "\n%s<%s>%s</%s>" % (ind, tag, esc, tag) + block[at:]


# --------------------------------------------------------------------- media

def sniff(path, max_bytes=MAX_BYTES):
    """-> real extension ('.png'/'.jpg') of a staged image, or None (missing, link, too big, not an image)."""
    if not os.path.isfile(path) or os.path.islink(path):
        return None
    if os.path.getsize(path) > max_bytes or os.path.getsize(path) == 0:
        return None
    with open(path, "rb") as f:
        head = f.read(8)
    for magic, ext in MAGIC:
        if head.startswith(magic):
            return ext
    return None


def has_media(media_root, system, typ, stem):
    d = os.path.join(media_root, system, typ)
    return os.path.isdir(d) and any(os.path.splitext(f)[0] == stem for f in os.listdir(d))


def thumb_name(stem):
    """libretro-thumbnails file name rule: &*/:`<>?\\| become '_'."""
    return re.sub(r'[&*/:`<>?\\|"]', "_", stem) + ".png"


def thumb_url(pin, typ, stem):
    repo, sha = pin
    return "https://%s/libretro-thumbnails/%s/%s/%s/%s" % (
        THUMB_HOST, repo, sha, THUMB_DIRS[typ], urllib.parse.quote(thumb_name(stem)))


def fetch(url, dest, max_bytes=MAX_BYTES, opener=None):
    """Download one thumbnail into dest (untrusted data: size cap, magic check, never executed).
    -> True when a valid image was saved."""
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or u.hostname != THUMB_HOST:
        return False
    opener = opener or urllib.request.urlopen
    try:
        with opener(url, timeout=30) as r:
            data = r.read(max_bytes + 1)
    except Exception:  # noqa: BLE001  (404, network: just a miss)
        return False
    if len(data) > max_bytes or not any(data.startswith(m) for m, _e in MAGIC):
        return False
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)
    return True


# ----------------------------------------------------------------------- run

def run(args, cg):
    romm = parse_romm(cg.read_text(args.romm, "romm export"), cg, args.romm)
    dat_text = cg.read_text(args.dat, "dat") if args.dat else ""
    dat = parse_dat_meta(dat_text) if args.dat else {}
    dat_names = {z: t for z, (t, _y) in cg.parse_dat(dat_text).items() if t} if args.dat else {}
    if args.dat and not dat:
        raise cg.InputError("%s: no game entries found" % args.dat)
    if args.media and not os.path.isdir(args.media):
        raise cg.InputError("--media is not a directory: %s" % args.media)
    if args.staging and not os.path.isdir(args.staging):
        raise cg.InputError("--staging is not a directory: %s" % args.staging)
    pins = parse_pins(cg.read_text(args.thumbnails, "pins"), cg, args.thumbnails) if args.thumbnails else {}
    if args.thumbnails and not args.fetch_dir:
        raise cg.UsageError("--thumbnails needs --fetch-dir")
    if args.fetch and not args.thumbnails:
        raise cg.UsageError("--fetch needs --thumbnails")
    if args.fetch and os.path.isdir(args.fetch_dir) and os.listdir(args.fetch_dir):
        raise cg.UsageError("--fetch-dir must be a new or empty directory: %s" % args.fetch_dir)
    systems = cg.load_gamelists(args.gamelists)
    pick_genre = pick_genres(cg.load_picks(args.picks), systems, cg) if args.picks else {}

    stamp = args.stamp or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    want, copies, fetches, unmatched, refused = [], [], [], [], []
    texts, tot = {}, dict(files=0)
    rows_out = []
    for system in sorted(systems):
        gl = os.path.join(args.gamelists, system, "gamelist.xml")
        orig = cg.read_text(gl, "gamelist")
        c = dict(games=0, file=0, stem=0, nomatch=0, mismatch=0)
        before = {f: 0 for f in FIELDS + MEDIA}
        add = {f: 0 for f in FIELDS + MEDIA}
        src = {}
        stage = dict(pull=0, bad=0, thumb=0)
        new_blocks = []
        for b in cg._GAME_RE.findall(orig):
            p = (cg._tag(b, "path") or "").strip()
            if not p or (cg._tag(b, "hidden") or "").strip().lower() == "true":
                new_blocks.append(b)
                continue
            fn = os.path.basename(p)
            stem = os.path.splitext(fn)[0]
            title = (cg._tag(b, "name") or stem).strip()
            c["games"] += 1
            row, how = find_row(system, fn, title, romm, cg)
            if how == "mismatch":
                c["mismatch"] += 1
                refused.append("%s/%s: %r vs RomM %r (%s)" % (system, fn, title, row.get("name"), row.get("fs_name")))
                row = None                                      # a wrong match never writes
            elif how:
                c[how] += 1
            else:
                c["nomatch"] += 1
            vals = values_for(system, fn, row, dat, pick_genre, cg)
            nb = b
            for f in FIELDS:
                if _has(nb, f, cg):
                    continue
                before[f] += 1
                if f in vals:
                    nb = add_tag(nb, f, vals[f][0])
                    add[f] += 1
                    src[(f, vals[f][1])] = src.get((f, vals[f][1]), 0) + 1
            if args.media:
                for typ in MEDIA:
                    if has_media(args.media, system, typ, stem):
                        continue
                    before[typ] += 1
                    rel = None
                    if row:
                        rel = row.get("cover") if typ == "covers" else (row.get("screenshots") or [None])[0]
                    if rel:
                        sp = os.path.join(args.staging, rel) if args.staging else None
                        ext = sniff(sp, args.max_bytes) if sp else None
                        if ext:
                            copies.append((sp, system, typ, stem, ext))
                            add[typ] += 1
                            continue
                        if sp and os.path.exists(sp):
                            stage["bad"] += 1                   # staged but not a PNG/JPEG under the cap
                            continue
                        stage["pull"] += 1
                        want.append(rel)
                        continue
                    if system in pins:
                        # arcade thumbnails are named after the dat description, consoles after the ROM stem
                        tn = dat_names.get(fn, stem) if system in ARCADE_DAT_SYSTEMS else stem
                        tp = os.path.join(args.fetch_dir, system, typ, thumb_name(tn))
                        if args.fetch and not os.path.exists(tp):
                            fetches.append(fetch(thumb_url(pins[system], typ, tn), tp, args.max_bytes))
                        ext = sniff(tp, args.max_bytes)
                        if ext:
                            copies.append((tp, system, typ, stem, ext))
                            add[typ] += 1
                            stage["thumb"] += 1
            if how is None:
                unmatched.append("%s/%s" % (system, fn))
            new_blocks.append(nb)
        it = iter(new_blocks)
        new = cg._GAME_RE.sub(lambda _m: next(it), orig)
        texts[system] = (gl, orig, new)
        state = "SAME" if new == orig else "CHANGED"
        print("%-10s %4d games  romm: file %3d stem %3d none %3d refused %2d  %s"
              % (system, c["games"], c["file"], c["stem"], c["nomatch"], c["mismatch"], state))
        cols = FIELDS + (MEDIA if args.media else ())
        print("    missing before->after: " + "  ".join(
            "%s %d->%d" % (f, before[f], before[f] - add[f]) for f in cols))
        if args.media and (stage["pull"] or stage["bad"] or stage["thumb"]):
            print("    media: %d not staged yet, %d staged but refused (not PNG/JPEG or > %d bytes), %d from thumbnails"
                  % (stage["pull"], stage["bad"], args.max_bytes, stage["thumb"]))
        if src:
            print("    sources: " + "  ".join("%s<-%s %d" % (f, s, n) for (f, s), n in sorted(src.items())))
        rows_out.append((system, before, add))
    if args.show:
        for u in unmatched[:args.show if args.show > 0 else None]:
            print("  unmatched %s" % u)
    for r in refused:
        print("  REFUSED (title guard) %s" % r)
    if args.want:
        with open(args.want, "w", encoding="utf-8") as f:
            f.write("".join("%s\n" % w for w in sorted(set(want))))
        print("want: %d RomM file(s) to stage -> %s" % (len(set(want)), args.want))
    if args.fetch:
        print("thumbnails: %d fetched, %d missed" % (sum(fetches), len(fetches) - sum(fetches)))
    total_add = sum(sum(a.values()) for _s, _b, a in rows_out)
    print("total: %d field(s) + %d media file(s) to add  %s"
          % (sum(sum(a[f] for f in FIELDS) for _s, _b, a in rows_out), len(copies),
             "SAME" if total_add == 0 else "CHANGED"))
    if not args.apply:
        print("plan only; re-run with --apply to write")
        return cg.EXIT_OK
    for system in sorted(texts):
        gl, orig, new = texts[system]
        if new == orig:
            continue
        if args.backup_dir:
            bak = os.path.join(args.backup_dir, stamp, system, "gamelist.xml")
            os.makedirs(os.path.dirname(bak), exist_ok=True)
        else:
            bak = gl + ".bak-metadata-" + stamp
        shutil.copy2(gl, bak)
        with open(gl, "w", encoding="utf-8") as f:
            f.write(new)
        tot["files"] += 1
    n = 0
    for sp, system, typ, stem, ext in copies:
        dest = os.path.join(args.media, system, typ, stem + ext)
        if has_media(args.media, system, typ, stem):   # never overwrite (any extension)
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(sp, dest)
        n += 1
    print("applied: %d gamelist(s) rewritten (backup first), %d media file(s) copied" % (tot["files"], n))
    return cg.EXIT_OK
