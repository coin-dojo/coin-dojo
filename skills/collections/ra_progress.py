#!/usr/bin/env python3
"""RetroAchievements progress in the ES-DE gamelists (ticket #46). Stdlib only, plan by default.

ES-DE 3.5 has no RetroAchievements support, so this writes one line into each matched game's <desc>:

    🏆 12/40 (Hardcore)        12 of 40 unlocked, all in hardcore
    🏆 12/40 (Softcore)        none of them in hardcore
    🏆 12/40 (8 Hardcore)      mixed
    🏆 0/40                    the game has a set, nothing unlocked yet

Subcommands:

  fetch   RA web API -> a progress file (no gamelist is touched). Reads the TROPHIES config the settings role
          installs (trophies.hks: user, key_file, cache_dir; skills/settings/trophies_core.py) and reuses its
          http_get/api_url/read_key, so the key handling is the same: the key only goes into the request URL, it is
          never printed, logged or written, and the user name is not written either. Calls
          API_GetGameList.php (h=1 hashes, f=1 only games with a set) per RA console of the cabinet's systems and
          API_GetUserCompletionProgress.php (paged). Writes <cache_dir>/ra-progress.json atomically, and only when
          every call succeeded: a failed fetch keeps the old file (exit 4).
          The TROPHIES cache (trophies.json) holds only the 5 recently played games and no hashes, so it is not
          enough for this; it is left alone.
  write   progress file + gamelists -> the trophy line in each matched <desc> (plan; --apply writes, backup first).

Matching (write): a game's RA game id comes from, in order, a pin (--pins TSV system, ./path, game id), else its RA
hash looked up among the set's hashes of that system's RA console. Hashes: arcade systems = MD5 of the ROM file
name without extension (rcheevos' arcade rule, no file needed); NES (iNES header skipped), SNES (512-byte copier
header skipped), PC Engine HuCard (512-byte header skipped), N64 (byte order normalised to .z64), Genesis and
Atari 2600 (whole file) are computed from --roms (a .zip is hashed by its one ROM inside); disc systems (psx,
saturn, dreamcast, segacd, CD images) are not computed: give them with --hashes (TSV system, ./path, md5, e.g. from
RAHasher) or pin them. --hash-cache keeps computed hashes keyed by size + mtime.

What gets written: only the line at the very start of <desc> (plus one blank line before the rest), text-level,
so every other byte of the gamelist stays as it was. A rerun replaces the line (never a second one); a game whose
hash is known but has no set loses its line; a game that cannot be hashed (no ROM, no method) is left untouched.
Virtual gamelists (must-play, genres, ... from `collgen.py nested`) are matched through their roms_nested links
(--nested-root; a crossover pointer file is followed). --matches FILE (written on --apply) lists the matched games
with a set (system, ./path, game id, total) for `collgen.py nested --ra-matches` (the 'Has achievements' folder).

Exit codes: 0 ok, 2 usage, 3 input error (missing/malformed progress, gamelist, pins, hashes: nothing written),
4 fetch failed (old progress file kept).
"""
import argparse
import datetime
import glob
import hashlib
import html as htmlmod
import json
import os
import re
import shutil
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nested  # noqa: E402  (next to this file)

EXIT_OK, EXIT_USAGE, EXIT_INPUT, EXIT_FETCH = 0, 2, 3, 4
TROPHY = "\U0001F3C6"
DEFAULT_ENTRY = "/data/coin-dojo/settings/trophies.hks"
PROGRESS_FILE = "ra-progress.json"
SETTINGS_DIRS = (os.path.join(HERE, "..", "settings"), "/data/coin-dojo/settings")
PAGE, MAX_PAGES = 500, 40

# ES-DE system dir -> RetroAchievements console id
CONSOLE = {"fbneo": 27, "cps1": 27, "cps2": 27, "cps3": 27, "neogeo": 27, "naomi": 27, "arcade": 27,
           "nes": 7, "snes": 3, "genesis": 1, "megadrive": 1, "n64": 2, "atari2600": 25, "pcengine": 8,
           "segacd": 9, "psx": 12, "saturn": 39, "dreamcast": 40}
# how the RA hash of a ROM file is computed (rcheevos rc_hash); systems not listed: --hashes or --pins only
METHOD = {"fbneo": "name", "cps1": "name", "cps2": "name", "cps3": "name", "neogeo": "name", "naomi": "name",
          "arcade": "name", "nes": "nes", "snes": "snes", "pcengine": "pce", "n64": "n64",
          "genesis": "file", "megadrive": "file", "atari2600": "file"}
DISC_EXT = {".cue", ".chd", ".iso", ".bin", ".gdi", ".cdi", ".m3u", ".pbp", ".ccd", ".img"}
MAX_ROM = 128 * 1024 * 1024
MD5_RE = re.compile(r"^[0-9a-f]{32}$")
# our line: at the start of the raw <desc> text, then a blank line (or the end)
OWN_RE = re.compile(r"\A" + TROPHY + r" \d+/\d+(?: \((?:Hardcore|Softcore|\d+ Hardcore)\))?(?:\n\n|\n|\Z)")
GAME_RE = re.compile(r"<game(?:\s[^>]*)?>.*?</game>", re.S)
DESC_RE = re.compile(r"<desc>(.*?)</desc>|<desc\s*/>", re.S)


class InputError(Exception):
    pass


class UsageError(Exception):
    pass


# ------------------------------------------------------------------------------------------------ the line

def trophy_line(game):
    """game {total, awarded, awarded_hc} -> the desc line, or None when the game has no set."""
    total, got, hc = game["total"], game["awarded"], game["awarded_hc"]
    if total <= 0:
        return None
    got, hc = min(got, total), min(hc, total)
    if got <= 0 and hc <= 0:
        return "%s 0/%d" % (TROPHY, total)
    if hc >= got:
        return "%s %d/%d (Hardcore)" % (TROPHY, hc, total)
    if hc == 0:
        return "%s %d/%d (Softcore)" % (TROPHY, got, total)
    return "%s %d/%d (%d Hardcore)" % (TROPHY, got, total, hc)


def strip_own(raw):
    """Raw <desc> text -> (text without our leading line(s), how many were removed)."""
    n = 0
    while True:
        m = OWN_RE.match(raw)
        if not m:
            return raw, n
        raw, n = raw[m.end():], n + 1


def set_desc_line(block, line):
    """One <game> block (text) -> block with `line` at the start of <desc> (None = remove our line).
    Text-level: the rest of the desc and of the block keep their bytes."""
    m = DESC_RE.search(block)
    if m is None:
        if line is None:
            return block
        am = None
        for tag in ("name", "path"):
            am = re.search(r"([ \t]*)<%s>.*?</%s>" % (tag, tag), block, re.S)
            if am:
                break
        if am is None:
            return block
        return block[:am.end()] + "\n%s<desc>%s</desc>" % (am.group(1) or "\t\t", line) + block[am.end():]
    raw = m.group(1) or ""
    rest, removed = strip_own(raw)
    if line is None:
        if not removed:
            return block
        if rest:
            return block[:m.start()] + "<desc>%s</desc>" % rest + block[m.end():]
        a = m.start()                       # the desc held only our line: drop the element and its line
        while a > 0 and block[a - 1] in " \t":
            a -= 1
        if a > 0 and block[a - 1] == "\n":
            a -= 1
        return block[:a] + block[m.end():]
    new = line + ("\n\n" + rest if rest else "")
    if m.group(1) is not None and new == raw:
        return block
    return block[:m.start()] + "<desc>%s</desc>" % new + block[m.end():]


def game_path(block):
    m = re.search(r"<path>(.*?)</path>", block, re.S)
    if not m:
        return ""
    return htmlmod.unescape(m.group(1)).strip()


def check_xml(text, label):
    body = re.sub(r"^\s*<\?xml[^>]*\?>", "", text).strip()
    try:
        ET.fromstring("<wrap>" + body + "</wrap>")
    except ET.ParseError as e:
        raise InputError("%s: malformed XML (%s)" % (label, e))


# ------------------------------------------------------------------------------------------------ hashing

def _md5(data):
    return hashlib.md5(data).hexdigest()


def hash_bytes(method, data):
    if method == "nes":
        return _md5(data[16:] if data[:4] == b"NES\x1a" else data)
    if method in ("snes", "pce"):
        return _md5(data[512:] if len(data) % 8192 == 512 else data)
    if method == "n64":
        head = data[:4]
        if head == b"\x37\x80\x40\x12":                         # .v64: 16-bit byte swapped
            b = bytearray(data)
            b[0::2], b[1::2] = data[1::2], data[0::2]
            data = bytes(b)
        elif head == b"\x40\x12\x37\x80":                       # .n64: 32-bit little endian
            b = bytearray(data)
            b[0::4], b[1::4], b[2::4], b[3::4] = data[3::4], data[2::4], data[1::4], data[0::4]
            data = bytes(b)
        return _md5(data)
    return _md5(data)


def rom_bytes(path):
    """Bytes of a ROM file; a .zip gives its one ROM (the biggest member). None when unreadable or too big."""
    try:
        if path.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                infos = [i for i in z.infolist() if not i.is_dir()]
                if not infos:
                    return None
                big = max(infos, key=lambda i: (i.file_size, i.filename))
                if big.file_size > MAX_ROM:
                    return None
                return z.read(big)
        if os.path.getsize(path) > MAX_ROM:
            return None
        with open(path, "rb") as fh:
            return fh.read()
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError):
        return None


class Hasher:
    """RA hash of (system, ./path): --hashes rows, the cache, or computed from --roms. None = unknown."""

    def __init__(self, roms=None, given=None, cache=None):
        self.roms, self.given, self.cache = roms, given or {}, dict(cache or {})
        self.dirty = False

    def __call__(self, system, path):
        rel = path[2:] if path.startswith("./") else path
        if (system, "./" + rel) in self.given:
            return self.given[(system, "./" + rel)]
        method = METHOD.get(system)
        if method == "name":
            return _md5(os.path.splitext(os.path.basename(rel))[0].encode("utf-8"))
        if method is None or not self.roms or os.path.splitext(rel)[1].lower() in DISC_EXT:
            return None
        fp = os.path.join(self.roms, system, rel)
        try:
            st = os.stat(fp)
        except OSError:
            return None
        key = "%s/%s" % (system, rel)
        hit = self.cache.get(key)
        if isinstance(hit, list) and len(hit) == 3 and hit[:2] == [st.st_size, st.st_mtime_ns]:
            return hit[2]
        data = rom_bytes(fp)
        if data is None:
            return None
        h = hash_bytes(method, data)
        self.cache[key] = [st.st_size, st.st_mtime_ns, h]
        self.dirty = True
        return h


# ------------------------------------------------------------------------------------------------ inputs

def _int(v, what):
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise InputError("progress: %s must be a whole number >= 0, got %r" % (what, v))
    return v


def load_progress(path):
    """The fetch output -> {game id: {title, console, total, awarded, awarded_hc, hashes}}. Strict: exit 3."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except OSError as e:
        raise InputError("progress file: %s" % e.strerror)
    except ValueError:
        raise InputError("progress file %s is not JSON" % path)
    if not isinstance(doc, dict) or doc.get("version") != 1 or not isinstance(doc.get("games"), dict):
        raise InputError("progress file %s: want {\"version\": 1, \"games\": {...}}" % path)
    games = {}
    for gid, g in doc["games"].items():
        if not re.fullmatch(r"[1-9][0-9]*", gid) or not isinstance(g, dict):
            raise InputError("progress: bad game entry %r" % gid)
        hashes = g.get("hashes", [])
        if not isinstance(hashes, list) or not all(isinstance(h, str) and MD5_RE.match(h) for h in hashes):
            raise InputError("progress: game %s: hashes must be lowercase MD5 strings" % gid)
        games[int(gid)] = {"title": g.get("title") if isinstance(g.get("title"), str) else "",
                           "console": _int(g.get("console", 0), "game %s console" % gid),
                           "total": _int(g.get("total"), "game %s total" % gid),
                           "awarded": _int(g.get("awarded", 0), "game %s awarded" % gid),
                           "awarded_hc": _int(g.get("awarded_hc", 0), "game %s awarded_hc" % gid),
                           "hashes": hashes}
    return games


def hash_index(games):
    idx = {}
    for gid in sorted(games):
        for h in games[gid]["hashes"]:
            idx.setdefault((games[gid]["console"], h), gid)
    return idx


def _tsv(path, what, ncols, check):
    rows = {}
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError as e:
        raise InputError("%s: %s" % (what, e.strerror))
    for n, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) != ncols or not all(c.strip() for c in cols):
            raise InputError("%s:%d: want %d tab-separated columns" % (what, n, ncols))
        system, p, val = cols[0].strip(), cols[1].strip(), cols[2].strip().lower()
        if not p.startswith("./") or not check(val):
            raise InputError("%s:%d: bad row (path must start with ./, value %r)" % (what, n, val))
        if (system, p) in rows:
            raise InputError("%s:%d: duplicate %s %s" % (what, n, system, p))
        rows[(system, p)] = val
    return rows


def load_pins(path):
    return {k: int(v) for k, v in _tsv(path, "pins", 3, lambda v: re.fullmatch(r"[1-9][0-9]*", v)).items()}


def load_hashes(path):
    return _tsv(path, "hashes", 3, lambda v: MD5_RE.match(v))


def load_cache(path):
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}                      # a cache only: missing or broken = start over
    return doc if isinstance(doc, dict) else {}


def atomic_write(path, text):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".ra-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        if os.path.exists(path):
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


# ------------------------------------------------------------------------------------------------ write

def link_target(nested_root, rom_root, virtual, path):
    """A virtual gamelist <path> -> (system, ./path) of the real ROM, via its roms_nested link (or None)."""
    rel = path[2:] if path.startswith("./") else path
    link = os.path.join(nested_root, virtual, rel)
    try:
        target = os.readlink(link)
    except OSError:
        return None
    if not os.path.isabs(target):                          # crossover: relative link to a pointer file (#116)
        try:
            with open(os.path.normpath(os.path.join(os.path.dirname(link), target)), encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            return None
        if not text.startswith(nested.POINTER_MAGIC):
            return None
        target = text[len(nested.POINTER_MAGIC):].strip()
    root = rom_root.rstrip("/") + "/"
    if not target.startswith(root):
        return None
    system, _sep, fn = target[len(root):].partition("/")
    return (system, "./" + fn) if system and fn else None


def plan_text(text, resolve):
    """Gamelist text -> (new text, counts). resolve(path) -> ('match', game) | ('none', None) | ('unknown', None)."""
    counts = {"match": 0, "none": 0, "unknown": 0, "changed": 0}
    shown = []
    out, pos = [], 0
    for m in GAME_RE.finditer(text):
        block = m.group(0)
        state, game = resolve(game_path(block))
        counts[state] += 1
        if state == "unknown":
            continue
        line = trophy_line(game) if state == "match" else None
        nb = set_desc_line(block, line)
        if nb != block:
            counts["changed"] += 1
            out.append(text[pos:m.start()])
            out.append(nb)
            pos = m.end()
            shown.append((game_path(block), line or "(line removed)"))
    out.append(text[pos:])
    return "".join(out), counts, shown


def cmd_write(args):
    games = load_progress(args.progress)
    idx = hash_index(games)
    pins = load_pins(args.pins) if args.pins else {}
    for (system, p), gid in sorted(pins.items()):
        if gid not in games:
            raise InputError("pins: %s %s -> game %d is not in the progress file" % (system, p, gid))
    given = load_hashes(args.hashes) if args.hashes else {}
    cache = load_cache(args.hash_cache) if args.hash_cache else {}
    hasher = Hasher(args.roms, given, cache)
    if not os.path.isdir(args.gamelists):
        raise InputError("--gamelists is not a directory: %s" % args.gamelists)
    dirs = [args.gamelists] + ([args.nested_gamelists] if args.nested_gamelists else [])
    paths = []
    for d in dirs:
        paths += sorted(glob.glob(os.path.join(d, "*", "gamelist.xml")))
    if not paths:
        raise InputError("no <system>/gamelist.xml under %s" % " ".join(dirs))
    texts = {}
    for gl in paths:                                       # read + validate everything before any write
        try:
            with open(gl, encoding="utf-8", newline="") as fh:
                texts[gl] = fh.read()
        except (OSError, UnicodeDecodeError) as e:
            raise InputError("%s: %s" % (gl, e))
        check_xml(texts[gl], gl)

    memo = {}

    def real(system, path):
        key = (system, path)
        if key not in memo:
            gid = pins.get(key)
            if gid is None:
                h = hasher(system, path)
                if h is None:
                    memo[key] = ("unknown", None, None)
                    return memo[key]
                gid = idx.get((CONSOLE.get(system, -1), h))
            g = games.get(gid) if gid is not None else None
            memo[key] = ("match", g, gid) if g is not None and g["total"] > 0 else ("none", None, None)
        return memo[key]

    results = {}
    stamp = args.stamp or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    tot = {"match": 0, "none": 0, "unknown": 0, "changed": 0}
    print("progress: %d game(s) with data, %d hash(es); pins %d; given hashes %d"
          % (len(games), len(idx), len(pins), len(given)))
    for gl in paths:
        system = os.path.basename(os.path.dirname(gl))
        if system in nested.TOOL_SLUGS:
            continue
        if system in nested.VIRTUAL_SLUGS + (nested.MUST_PLAY_GENRES,):
            if not args.nested_root:
                print("%-12s skipped (virtual system; needs --nested-root)" % system)
                continue

            def resolve(path, v=system):
                t = link_target(args.nested_root, args.rom_root, v, path)
                return real(*t)[:2] if t else ("unknown", None)
        else:
            def resolve(path, s=system):
                return real(s, path)[:2]
        new, c, shown = plan_text(texts[gl], resolve)
        state = "SAME" if new == texts[gl] else "CHANGED"
        for k in tot:
            tot[k] += c[k]
        print("%-12s matched %4d  no set %4d  unknown %4d  lines to write %4d  %s"
              % (system, c["match"], c["none"], c["unknown"], c["changed"], state))
        for p, line in shown[:args.show if args.show >= 0 else None]:
            print("    %s: %s" % (p, line))
        if state == "CHANGED":
            results[gl] = new
    print("total: matched %(match)d, no set %(none)d, unknown %(unknown)d, lines to write %(changed)d" % tot
          + ("  SAME" if not results else "  CHANGED"))
    matches = sorted((s, p, gid, games[gid]["total"]) for (s, p), (st, _g, gid) in memo.items()
                     if st == "match" and s not in nested.VIRTUAL_SLUGS)
    if not args.apply:
        print("plan only; re-run with --apply to write %d gamelist(s)" % len(results))
        return EXIT_OK
    for gl in sorted(results):
        if args.backup_dir:
            rel = os.path.relpath(gl, os.path.dirname(os.path.dirname(gl)))
            bak = os.path.join(args.backup_dir, stamp, rel)
            os.makedirs(os.path.dirname(bak), exist_ok=True)
        else:
            bak = gl + ".bak-ra-" + stamp
        shutil.copy2(gl, bak)
        atomic_write(gl, results[gl])
    if args.hash_cache and hasher.dirty:
        atomic_write(args.hash_cache, json.dumps(hasher.cache, sort_keys=True, indent=0) + "\n")
    if args.matches:
        body = "".join("%s\t%s\t%d\t%d\n" % m for m in matches)
        old = None
        if os.path.isfile(args.matches):
            with open(args.matches, encoding="utf-8") as fh:
                old = fh.read()
        text = "# system\tpath\tra_game_id\ttotal (ra_progress.py write; for collgen.py nested --ra-matches)\n" + body
        if text != old:
            atomic_write(args.matches, text)
    print("applied: %d gamelist(s) rewritten (backup first); %d matched game(s) with a set"
          % (len(results), len(matches)))
    return EXIT_OK


# ------------------------------------------------------------------------------------------------ fetch

def trophies_core(settings_dir=None):
    for d in ([settings_dir] if settings_dir else []) + list(SETTINGS_DIRS):
        if d and os.path.isfile(os.path.join(d, "trophies_core.py")):
            sys.path.insert(0, os.path.abspath(d))
            import trophies_core as tc  # noqa: E402
            return tc
    raise InputError("trophies_core.py not found (--settings-dir)")


def _num(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return max(0, n)


def fetch_progress(tc, user, key, consoles, get=None, now=None):
    """RA web API -> the progress doc (no user, no key in it). Raises tc.FetchError."""
    get = get or tc.http_get
    if not user or not key:
        raise tc.FetchError("no RetroAchievements user or web API key")

    def call(endpoint, **params):
        try:
            return json.loads(get(tc.api_url(endpoint, **params)))
        except ValueError:
            raise tc.FetchError("RetroAchievements sent something that is not JSON (%s)" % endpoint) from None

    games = {}
    for cid in sorted(set(consoles)):
        doc = call("API_GetGameList.php", i=cid, h=1, f=1, y=key)
        if not isinstance(doc, list):
            raise tc.FetchError("RetroAchievements game list of console %d is not a list" % cid)
        for g in doc:
            gid = _num(g.get("ID")) if isinstance(g, dict) else 0
            if not gid:
                continue
            hs = g.get("Hashes") if isinstance(g.get("Hashes"), list) else []
            games[str(gid)] = {"title": g.get("Title") if isinstance(g.get("Title"), str) else "",
                               "console": cid, "total": _num(g.get("NumAchievements")),
                               "awarded": 0, "awarded_hc": 0,
                               "hashes": sorted({h.lower() for h in hs if isinstance(h, str) and MD5_RE.match(h.lower())})}
    offset = 0
    for _page in range(MAX_PAGES):
        doc = call("API_GetUserCompletionProgress.php", u=user, y=key, c=PAGE, o=offset)
        if not isinstance(doc, dict) or not isinstance(doc.get("Results"), list):
            raise tc.FetchError("RetroAchievements did not return completion progress (wrong user or key?)")
        res = doc["Results"]
        for r in res:
            gid = _num(r.get("GameID")) if isinstance(r, dict) else 0
            if not gid:
                continue
            g = games.setdefault(str(gid), {"title": r.get("Title") if isinstance(r.get("Title"), str) else "",
                                            "console": _num(r.get("ConsoleID")), "total": 0,
                                            "awarded": 0, "awarded_hc": 0, "hashes": []})
            g["total"] = max(g["total"], _num(r.get("MaxPossible")))
            g["awarded"] = _num(r.get("NumAwarded"))
            g["awarded_hc"] = _num(r.get("NumAwardedHardcore"))
        offset += len(res)
        if not res or offset >= _num(doc.get("Total")):
            break
    else:
        raise tc.FetchError("RetroAchievements completion progress has more than %d pages" % MAX_PAGES)
    return {"version": 1, "fetched_at": int(now if now is not None else time.time()),
            "games": dict(sorted(games.items(), key=lambda kv: int(kv[0])))}


def cmd_fetch(args, get=None):
    tc = trophies_core(args.settings_dir)
    entry = args.entry or os.environ.get("RA_PROGRESS_ENTRY") or DEFAULT_ENTRY
    if not os.path.isfile(entry):
        raise InputError("TROPHIES config not found: %s (the settings role writes it when RetroAchievements is set up)" % entry)
    cfg = tc.load_config(entry)
    key = tc.read_key(cfg["key_file"])
    out = args.out or os.path.join(cfg["cache_dir"], PROGRESS_FILE)
    systems = args.systems.split(",") if args.systems else sorted(CONSOLE)
    bad = [s for s in systems if s not in CONSOLE]
    if bad:
        raise UsageError("unknown system(s) %s; known: %s" % (",".join(bad), ",".join(sorted(CONSOLE))))
    try:
        doc = fetch_progress(tc, cfg["user"], key, [CONSOLE[s] for s in systems], get)
    except tc.FetchError as e:
        print("fetch failed: %s; %s kept as it was" % (e, out), file=sys.stderr)
        return EXIT_FETCH
    atomic_write(out, json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) + "\n")
    n = len(doc["games"])
    played = sum(1 for g in doc["games"].values() if g["awarded"] or g["awarded_hc"])
    print("fetched: %d game(s) with a set or progress, %d with unlocks -> %s" % (n, played, out))
    return EXIT_OK


# ------------------------------------------------------------------------------------------------ cli

def make_parser():
    p = argparse.ArgumentParser(prog="ra_progress.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="RA web API -> progress file (no gamelist touched)")
    f.add_argument("--entry", help="TROPHIES config (default $RA_PROGRESS_ENTRY, else %s)" % DEFAULT_ENTRY)
    f.add_argument("--out", help="progress file (default <cache_dir from the config>/%s)" % PROGRESS_FILE)
    f.add_argument("--systems", help="comma list of ES-DE systems whose RA console lists to fetch (default: all known)")
    f.add_argument("--settings-dir", help="dir with trophies_core.py (default ../settings, else /data/coin-dojo/settings)")
    f.set_defaults(fn=cmd_fetch)
    w = sub.add_parser("write", help="progress file -> trophy line in the gamelists' <desc> (plan; --apply writes)")
    w.add_argument("--progress", required=True, help="progress file from `fetch`")
    w.add_argument("--gamelists", required=True, help="dir with <system>/gamelist.xml (virtual ones need --nested-root)")
    w.add_argument("--nested-gamelists", help="a second gamelists dir, e.g. nested_out/gamelists on the laptop")
    w.add_argument("--nested-root", help="roms_nested root of the virtual systems (cabinet: /data/nested)")
    w.add_argument("--rom-root", default="/data/roms", help="ROM root the nested links point into (default /data/roms)")
    w.add_argument("--roms", help="ROM root to hash console ROMs from (<roms>/<system>/<file>); arcade needs none")
    w.add_argument("--hashes", help="TSV system, ./path, md5: hashes from elsewhere (e.g. RAHasher for discs)")
    w.add_argument("--pins", help="TSV system, ./path, RA game id: wins over the hash")
    w.add_argument("--hash-cache", help="JSON cache of computed hashes (read; rewritten on --apply)")
    w.add_argument("--matches", help="write the matched games (system, ./path, id, total) here on --apply")
    w.add_argument("--show", type=int, default=5, help="changed lines listed per gamelist (-1 = all, default 5)")
    w.add_argument("--backup-dir", help="back up to DIR/<stamp>/<system>/gamelist.xml (default: gamelist.xml.bak-ra-<stamp> beside it)")
    w.add_argument("--stamp", help="backup stamp (default: now)")
    w.add_argument("--apply", action="store_true", help="rewrite the gamelists (default: plan only)")
    w.set_defaults(fn=cmd_write)
    return p


def main(argv=None):
    args = make_parser().parse_args(argv)
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
