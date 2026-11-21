#!/usr/bin/env python3
"""GAME LISTS on the cabinet (#281): rebuild the ES-DE main screen (MUST PLAY, GENRES, SERIES, DECADES, SYSTEMS) from the
cabinet's own scraped gamelists, with no Mac. The SETTINGS app's GAME LISTS page runs it as the cabinet user; stdlib only.

    lists_rebuild.py rebuild [ENTRY.hks]     build, validate, swap in, report one JSON line
    lists_rebuild.py reload [--delay S]      restart ES-DE the way a deploy does (guarded)
    lists_rebuild.py status [ENTRY.hks]      what the last rebuild made

What `rebuild` does, in order (the same tools and flags as role esde_collections / `make deploy-collections`):
 1. refuses in the personal edition ("managed from the Mac": the maintainer's curated lists are never overwritten) and
    while a game runs; takes a lock; rolls back a swap that an earlier crash left half done.
 2. needs scraped games: no real gamelist, or none with metadata, is the error "no scraped games yet: scrape first"; a
    gamelist that is not XML is named ("bad gamelist: <system>").
 3. builds into a temp dir beside nested_out (`collgen.py nested --apply`, MUST PLAY size and MUST PLAY GENRES stars from the
    cabinet's state file, the live virtual gamelists as the previous state so favourites and play counts carry over) and
    runs the existing gate `validate_nested.py` on it. Anything failing or interrupted up to here has changed NOTHING.
 4. swaps in (after a journal and a backup of every old piece, so a failure or a killed process rolls back to the old
    lists): nested_out/{roms_nested,downloaded_media,custom_systems,gamelists}, ES-DE's custom_systems/es_systems.xml and
    gamelists/<virtual>/gamelist.xml (each file replaced atomically), the /data/nested link and the /data/media links.
 5. records the result in the state file (counts, time). ES-DE is NOT restarted here: the app asks first (`reload`).
Exit: 0 ok, 1 refused or failed (one line says why on stderr), 2 usage.
"""
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, ".config", "coin-dojo", "settings-state.json")
SIZES = [10, 15, 25, 50]                  # MUST PLAY per system (hk_must_play_top's default is 25)
STARS = (4.0, 4.5, 5.0)                   # MUST PLAY GENRES minimum (hk_must_play_genre_min_stars's default is 4.5)
DEFAULT_TOP, DEFAULT_STARS = 25, 4.5
SCRAPED_TAGS = ("desc", "rating", "genre", "releasedate", "developer", "publisher", "image", "players")
SWAP_DIRS = ("roms_nested", "downloaded_media", "custom_systems", "gamelists")
TOOL_TIMEOUT = 900
KEEP_BACKUPS = 3
DEFAULTS = dict(
    edition="personal",                   # fails closed (#281 M1): only an entry that says "community" rebuilds
    tools="/data/coin-dojo", gamelists=os.path.join(HOME, "ES-DE", "gamelists"),
    custom_systems=os.path.join(HOME, "ES-DE", "custom_systems"),
    roms="/data/roms", media="/data/media", nested_out="/data/coin-dojo/nested_out", nested_root="/data/nested",
    launcher="python3 " + os.path.join(HOME, "ES-DE", "scripts", "launch.py"),
    picks="/data/coin-dojo/cabinet-picks.html", picks_example=os.path.join(HERE, "picks_example.html"),
    dat="/data/coin-dojo/libretro.dat", folder_art="/data/coin-dojo/folder_photos",
    settings_entry="", settings_command="", trophies_entry="", trophies_command="",
    quit_entry="", quit_command="", virtual_systems=[], state=STATE,
    backup_root="/data/coin-dojo/lists-backup", run_dir="", game_procs=["retroarch", "flycast"], game_patterns=[],
    nested_args=[],
)


KILL = ["pkill", "-9", "-x", "AppRun"]       # fixed here, like the deploy handler: a config file never names a command
PROC = "/proc"                               # tests point this at a fake process table
MANAGED = "MANAGED FROM THE MAC (MAKE DEPLOY-COLLECTIONS)"


class ListsError(Exception):
    """A refusal or failure with the line the screen shows (upper case, short)."""


# --------------------------------------------------------------------------------------------------- config + state
def load_config(entry=None, overrides=None):
    """DEFAULTS + the entry file's `lists` block. Fails closed (#281 M1): an entry that is given but missing, not JSON, or
    without a `lists` block that names an edition raises the MANAGED refusal; no entry at all = the personal default."""
    cfg = dict(DEFAULTS)
    if entry:
        try:
            with open(entry, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError, RecursionError):
            raise ListsError(MANAGED)
        lists = data.get("lists") if isinstance(data, dict) else None
        if not isinstance(lists, dict) or lists.get("edition") not in ("community", "personal"):
            raise ListsError(MANAGED)
        cfg.update(lists)
    cfg.update(overrides or {})
    cfg["virtual_systems"] = virtual_names(cfg)
    return cfg


def virtual_names(cfg):
    """The virtual-system names have ONE source (hk_virtual_systems, #94). Here: the entry file's list (the role writes
    esde_virtual_systems into it), else env HK_VIRTUAL_SYSTEMS, else virtual_systems.json beside the tools. Never typed
    in this file. A bad or missing list gives [] and the rebuild refuses."""
    def ok(v):
        return isinstance(v, list) and v and all(isinstance(x, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", x) for x in v)
    if ok(cfg.get("virtual_systems")):
        return list(cfg["virtual_systems"])
    for load in (lambda: json.loads(os.environ.get("HK_VIRTUAL_SYSTEMS", "null")),
                 lambda: json.load(open(os.path.join(cfg["tools"], "virtual_systems.json"), encoding="utf-8"))):
        try:
            v = load()
        except (OSError, ValueError, RecursionError, TypeError):
            continue
        if ok(v):
            return list(v)
    return []


def read_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


def write_state(path, **values):
    st = read_state(path)
    st.update(values)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(st, indent=2, sort_keys=True) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def chosen(cfg):
    """(MUST PLAY per system, MUST PLAY GENRES stars) as the SETTINGS app saved them; anything else = the defaults."""
    st = read_state(cfg["state"])
    top, stars = st.get("must_play_top"), st.get("must_play_genre_min_stars")
    top = top if isinstance(top, int) and not isinstance(top, bool) and top in SIZES else DEFAULT_TOP
    stars = float(stars) if isinstance(stars, (int, float)) and not isinstance(stars, bool) and float(stars) in STARS else DEFAULT_STARS
    return top, stars


# --------------------------------------------------------------------------------------------------- the checks
def game_running(cfg, proc=None):
    """True while a game runs (the deploy guard's list): the rebuild and the reload both stay out of its way. Fails
    closed (#285 L4): an unreadable process table or a bad list counts as busy."""
    proc = proc or PROC
    try:
        procs, pats = set(cfg["game_procs"]), list(cfg["game_patterns"])
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except (OSError, TypeError, ValueError):
        return True
    for pid in pids:
        try:
            with open(os.path.join(proc, pid, "comm"), encoding="utf-8", errors="replace") as f:
                comm = f.read().strip()
            if comm in procs:
                return True
            if pats:
                with open(os.path.join(proc, pid, "cmdline"), "rb") as f:
                    cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
                if any(re.search(p, cmd) for p in pats):
                    return True
        except (OSError, re.error, TypeError):
            continue
    return False


def busy(cfg, proc=None):
    """The one guard for rebuild and reload (#281 L3): a game runs, or the session is in Fightcade -> the line, else None."""
    if game_running(cfg, proc):
        return "A GAME IS RUNNING"
    rd = cfg.get("run_dir")
    if rd:
        try:
            with open(os.path.join(rd, "current-mode"), encoding="utf-8") as f:
                if f.read().strip() == "fightcade":
                    return "FIGHTCADE IS RUNNING"
        except OSError:
            pass
    return None


def take_lock(cfg):
    """The rebuild lock, never waiting. ListsError when another rebuild (or reload) holds it."""
    os.makedirs(os.path.dirname(cfg["state"]), mode=0o700, exist_ok=True)
    lock = open(os.path.join(os.path.dirname(cfg["state"]), "lists.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        raise ListsError("A REBUILD IS ALREADY RUNNING")
    return lock


def scan_gamelists(cfg):
    """-> (systems, games, scraped games) of the REAL gamelists; raises ListsError for none, none scraped or a bad one."""
    base, virtual = cfg["gamelists"], set(cfg["virtual_systems"])
    systems = games = scraped = 0
    try:
        names = sorted(os.listdir(base))
    except OSError:
        raise ListsError("NO SCRAPED GAMES YET - SCRAPE FIRST")
    for name in names:
        path = os.path.join(base, name, "gamelist.xml")
        if name in virtual or not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                raw = f.read()
            if re.search(rb"<!(DOCTYPE|ENTITY)", raw, re.I):        # (#281 L2) no DTD, so no entity expansion tricks
                raise ET.ParseError("dtd")
            # ES-DE writes <alternativeEmulator> as a second top-level element before <gameList> (cabinet_tweaks
            # pins CPS1/2/3, NAOMI ... that way, #295): parse the file's elements under one wrapper root.
            body = re.sub(rb"\A\s*(\xef\xbb\xbf)?\s*<\?xml[^>]*\?>", b"", raw)
            root = ET.fromstring(b"<hk-gamelist-file>" + body + b"</hk-gamelist-file>")
            if root.find("gameList") is None:
                raise ET.ParseError("no gameList")
        except (ET.ParseError, OSError):
            raise ListsError("BAD GAMELIST: %s" % name.upper()[:30])
        n = s = 0
        for g in root.iter("game"):
            n += 1
            if any((g.findtext(t) or "").strip() for t in SCRAPED_TAGS):
                s += 1
        if n:
            systems, games, scraped = systems + 1, games + n, scraped + s
    if not games or not scraped:
        raise ListsError("NO SCRAPED GAMES YET - SCRAPE FIRST")
    return systems, games, scraped


def count_games(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return len(re.findall(r"<game[ >]", f.read()))
    except OSError:
        return 0


# --------------------------------------------------------------------------------------------------- the build
def tool(cfg, name):
    p = os.path.join(cfg["tools"], name)
    if not os.path.isfile(p):
        raise ListsError("GAME LIST TOOLS MISSING (%s) - RUN A DEPLOY" % name)
    return p


def nested_argv(cfg, out, top, stars, picks):
    a = [sys.executable, tool(cfg, "collgen.py"), "nested", "--gamelists", cfg["gamelists"], "--picks", picks,
         "--roms", cfg["roms"], "--media", cfg["media"], "--rom-root", cfg["roms"], "--nested-root", cfg["nested_root"],
         "--launcher", cfg["launcher"], "--out", out, "--must-play-top", str(top), "--must-play-genre-min", str(stars)]
    if os.path.isfile(cfg["dat"]):
        a += ["--dat", cfg["dat"]]
    if os.path.isdir(cfg["folder_art"]):
        a += ["--folder-art", cfg["folder_art"]]
    for flag, name in (("--folder-text", "folder_text.tsv"), ("--series", "series.tsv")):
        if os.path.isfile(os.path.join(cfg["tools"], name)):
            a += [flag, name]
    for kind in ("settings", "trophies", "quit"):
        entry = cfg.get(kind + "_entry") or ""
        if entry and os.path.isfile(entry):
            a += ["--%s-entry" % kind, entry, "--%s-command" % kind, cfg.get(kind + "_command") or ""]
    return a + list(cfg["nested_args"]) + ["--apply"]


def run(argv, cwd, env, timeout=TOOL_TIMEOUT):
    try:
        r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ListsError("TOOK TOO LONG - NOTHING WAS CHANGED")
    except OSError as e:
        raise ListsError("CANNOT RUN THE TOOLS: %s" % (e.strerror or "error").upper()[:40])
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def last_line(text):
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    return lines[-1] if lines else ""


def build(cfg, tmp, top, stars):
    """Generate the lists into TMP/out and validate them. Touches nothing outside TMP."""
    picks = cfg["picks"] if os.path.isfile(cfg["picks"]) else cfg["picks_example"]
    if not os.path.isfile(picks):
        raise ListsError("PICKS FILE MISSING - RUN A DEPLOY")
    out = os.path.join(tmp, "out")
    os.makedirs(os.path.join(out, "gamelists"))
    for v in cfg["virtual_systems"]:                      # the live virtual gamelists = previous state (favourites, play counts)
        live = os.path.join(cfg["gamelists"], v, "gamelist.xml")
        if os.path.isfile(live):
            os.makedirs(os.path.join(out, "gamelists", v))
            shutil.copy2(live, os.path.join(out, "gamelists", v, "gamelist.xml"))
    env = dict(os.environ, HK_VIRTUAL_SYSTEMS=json.dumps(cfg["virtual_systems"]), PYTHONDONTWRITEBYTECODE="1")
    rc, text = run(nested_argv(cfg, out, top, stars, picks), cfg["tools"], env)
    if rc != 0:
        raise ListsError(("THE BUILD FAILED: %s" % last_line(text).upper())[:90])
    rc, text = run([sys.executable, tool(cfg, "validate_nested.py"), "--out", out], cfg["tools"], env)
    if rc != 0 or "RESULT: PASS" not in text:
        bad = [x for x in text.splitlines() if x.startswith("FAIL")]
        raise ListsError(("CHECK FAILED: %s" % (bad[0] if bad else last_line(text)).upper())[:90])
    return out


def summary(out):
    """Counts of the built lists: games (SYSTEMS holds every game once), MUST PLAY, genres, decades."""
    def n(v):
        return count_games(os.path.join(out, "gamelists", v, "gamelist.xml"))
    return dict(games=n("systems"), must_play=n("must-play"), genres=n("genres"), decades=n("decades"),
                must_play_genres=n("must-play-genres"), series=n("series"))


# --------------------------------------------------------------------------------------------------- the swap
class Swap:
    """Replace the old lists piece by piece with a journal and a backup of each, so any failure (or a process killed
    half way: recover() on the next run) puts the old lists back."""

    def __init__(self, cfg, out, stamp):
        self.cfg, self.out = cfg, out
        self.root = os.path.join(cfg["backup_root"], stamp)
        self.journal = os.path.join(cfg["backup_root"], "swap.journal.json")
        self.ops = []

    def _log(self, op):
        self.ops.append(op)
        self._flush()

    def _done(self):
        self.ops[-1]["state"] = "done"
        self._flush()

    def _flush(self):
        os.makedirs(os.path.dirname(self.journal), exist_ok=True)
        tmp = self.journal + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict(root=self.root, ops=self.ops), f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.journal)

    def dirs(self):
        nout = self.cfg["nested_out"]
        os.makedirs(nout, exist_ok=True)
        for sub in SWAP_DIRS:
            new, old = os.path.join(self.out, sub), os.path.join(nout, sub)
            if not os.path.isdir(new):
                continue
            keep = os.path.join(self.root, "nested_out", sub)
            had = os.path.lexists(old)
            self._log(dict(kind="dir", dst=old, bk=keep, had=had, state="start"))
            if had:
                os.makedirs(os.path.dirname(keep), exist_ok=True)
                os.rename(old, keep)
            os.rename(new, old)
            self._done()

    def file(self, src, dst):
        roots = [os.path.realpath(cfg_root) for cfg_root in (self.cfg["gamelists"], self.cfg["custom_systems"])]
        here = os.path.realpath(os.path.dirname(dst))
        if not any(here == r or here.startswith(r + os.sep) for r in roots):      # (#281 L1) a symlinked folder
            raise ListsError("UNSAFE PATH IN ES-DE FOLDER: %s" % os.path.basename(os.path.dirname(dst)).upper()[:30])
        had = os.path.isfile(dst)
        keep = os.path.join(self.root, "files", re.sub(r"[^A-Za-z0-9._-]", "_", dst))
        if had:
            os.makedirs(os.path.dirname(keep), exist_ok=True)
            shutil.copy2(dst, keep)
        self._log(dict(kind="file", dst=dst, bk=keep, had=had, state="start"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = os.path.join(os.path.dirname(dst), ".%s.new-%d" % (os.path.basename(dst), os.getpid()))
        try:
            os.unlink(tmp)
        except OSError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
            shutil.copyfileobj(inp, out)
        os.chmod(tmp, os.stat(dst).st_mode & 0o7777 if had else 0o664)
        os.replace(tmp, dst)
        self._done()

    def link(self, target, dst, replace):
        old = os.readlink(dst) if os.path.islink(dst) else None
        if os.path.lexists(dst) and old is None:
            return                                         # a real file or folder is never replaced by a link
        if old is not None and not replace:
            return
        if old == target:
            return
        self._log(dict(kind="link", dst=dst, old=old, state="start"))
        tmp = "%s.new-%d" % (dst, os.getpid())
        if os.path.lexists(tmp):
            os.unlink(tmp)
        os.symlink(target, tmp)
        os.replace(tmp, dst)
        self._done()

    def finish(self):
        try:
            os.unlink(self.journal)
        except OSError:
            pass


def undo(ops):
    """Put every journalled piece back, newest first. Safe to run twice."""
    for op in reversed(ops):
        try:
            if op["kind"] == "dir":
                dst, bk = op["dst"], op["bk"]
                if op["had"]:
                    if os.path.lexists(bk):
                        if os.path.lexists(dst):
                            side = bk + ".failed"
                            if os.path.lexists(side):
                                shutil.rmtree(side, ignore_errors=True)
                            os.rename(dst, side)
                        os.rename(bk, dst)
                elif op["state"] == "done" and os.path.lexists(dst):
                    shutil.rmtree(dst, ignore_errors=True)
            elif op["kind"] == "file":
                dst = op["dst"]
                if op["had"] and os.path.isfile(op["bk"]):
                    tmp = dst + ".undo-%d" % os.getpid()
                    shutil.copy2(op["bk"], tmp)
                    os.replace(tmp, dst)
                elif not op["had"] and os.path.isfile(dst):
                    os.unlink(dst)
            elif op["kind"] == "link":
                dst = op["dst"]
                if os.path.islink(dst):
                    os.unlink(dst)
                if op["old"] is not None:
                    os.symlink(op["old"], dst)
        except (OSError, ValueError, KeyError, TypeError):
            continue


def allowed_dsts(cfg):
    """Every path a swap may touch, from the config: nothing else in a journal is ever acted on (#281 M2)."""
    nout = cfg["nested_out"]
    ok = {os.path.normpath(os.path.join(nout, d)) for d in SWAP_DIRS}
    ok.add(os.path.normpath(os.path.join(cfg["custom_systems"], "es_systems.xml")))
    ok.add(os.path.normpath(cfg["nested_root"]))
    for v in cfg["virtual_systems"]:
        ok.add(os.path.normpath(os.path.join(cfg["gamelists"], v, "gamelist.xml")))
        ok.add(os.path.normpath(os.path.join(cfg["media"], v)))
    return ok


def _journal_ops(doc):
    """The ops of a well-formed journal, or None."""
    if not isinstance(doc, dict) or not isinstance(doc.get("ops"), list):
        return None
    for op in doc["ops"]:
        if not isinstance(op, dict) or op.get("kind") not in ("dir", "file", "link") or not isinstance(op.get("dst"), str):
            return None
        if op["kind"] in ("dir", "file") and not (isinstance(op.get("bk"), str) and isinstance(op.get("had"), bool)):
            return None
        if op["kind"] == "link" and not ("old" in op and (op["old"] is None or isinstance(op["old"], str))):
            return None
        if any(isinstance(op.get(k), str) and "\0" in op[k] for k in ("dst", "bk", "old")):
            return None
        if op["kind"] == "dir" and not isinstance(op.get("state"), str):
            return None
    return doc["ops"]


def recover(cfg, say=lambda text: None):
    """A killed rebuild left a journal: roll its swap back. Only ops on paths the config allows, with backups under
    backup_root, are acted on; a malformed journal is renamed swap.journal.bad-<ts> and the run carries on.
    -> True when something was put back."""
    j = os.path.join(cfg["backup_root"], "swap.journal.json")
    if not os.path.isfile(j):
        return False
    try:
        with open(j, encoding="utf-8") as f:
            ops = _journal_ops(json.load(f))
    except (OSError, ValueError, RecursionError):
        ops = None
    if ops is None:
        bad = "%s.bad-%s" % (j[:-len(".json")], time.strftime("%Y%m%d-%H%M%S"))
        try:
            os.rename(j, bad)
        except OSError:
            try:
                os.unlink(j)
            except OSError:
                pass
        say("an earlier rebuild left an unreadable journal: kept as %s, carrying on" % os.path.basename(bad))
        return False
    allowed, bk_root = allowed_dsts(cfg), os.path.realpath(cfg["backup_root"])
    safe = []
    for op in ops:
        if os.path.normpath(op["dst"]) not in allowed:
            say("journal entry for a path outside the lists skipped: %s" % op["dst"][:80])
            continue
        if op["kind"] in ("dir", "file") and not (os.path.realpath(op["bk"]) + os.sep).startswith(bk_root + os.sep):
            say("journal entry with a backup outside the backup folder skipped")
            continue
        safe.append(op)
    undo(safe)
    try:
        os.unlink(j)
    except OSError:
        pass
    return bool(safe)


def prune(cfg, keep=KEEP_BACKUPS):
    root = cfg["backup_root"]
    try:
        names = sorted(n for n in os.listdir(root) if re.fullmatch(r"\d{8}-\d{6}", n))
    except OSError:
        return
    for n in names[:-keep]:
        shutil.rmtree(os.path.join(root, n), ignore_errors=True)


def swap_in(cfg, out, stamp):
    sw = Swap(cfg, out, stamp)
    try:
        sw.dirs()
        nout = cfg["nested_out"]                           # the new folders are in place now: copy ES-DE's files from there
        es = os.path.join(nout, "custom_systems", "es_systems.xml")
        if os.path.isfile(es):
            sw.file(es, os.path.join(cfg["custom_systems"], "es_systems.xml"))
        for v in cfg["virtual_systems"]:
            g = os.path.join(nout, "gamelists", v, "gamelist.xml")
            if os.path.isfile(g):
                sw.file(g, os.path.join(cfg["gamelists"], v, "gamelist.xml"))
        sw.link(os.path.join(cfg["nested_out"], "roms_nested"), cfg["nested_root"], replace=True)
        for v in cfg["virtual_systems"]:                    # a missing <media>/<virtual> link, as the role makes it
            if os.path.isdir(os.path.join(cfg["nested_out"], "downloaded_media", v)):
                sw.link(os.path.join(cfg["nested_out"], "downloaded_media", v), os.path.join(cfg["media"], v), replace=False)
    except BaseException:
        undo(sw.ops)
        sw.finish()
        raise
    sw.finish()
    prune(cfg)


# --------------------------------------------------------------------------------------------------- the entry points
def rebuild(cfg, say=lambda text: None, now=time.time):
    """-> the summary dict; raises ListsError with the line to show. Old lists are untouched unless every check passed."""
    if cfg["edition"] != "community":
        raise ListsError(MANAGED)
    if not cfg["virtual_systems"]:
        raise ListsError("NO VIRTUAL SYSTEM LIST - RUN A DEPLOY")
    lock = take_lock(cfg)
    try:
        if recover(cfg, say):
            say("an earlier rebuild was interrupted: the old lists are back")
        why = busy(cfg)
        if why:
            raise ListsError("QUIT THE GAME FIRST" if why.startswith("A GAME") else "QUIT FIGHTCADE FIRST")
        say("reading the scraped gamelists")
        scan_gamelists(cfg)
        top, stars = chosen(cfg)
        t = int(now())
        while os.path.exists(os.path.join(cfg["backup_root"], time.strftime("%Y%m%d-%H%M%S", time.localtime(t)))):
            t += 1                                          # two rebuilds in one second: never reuse a backup folder
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(t))
        parent = os.path.dirname(cfg["nested_out"]) or "."
        if os.path.isdir(parent):                           # leftovers of a rebuild that was killed (nothing was swapped)
            for old in os.listdir(parent):
                if old.startswith(".lists-build-"):
                    shutil.rmtree(os.path.join(parent, old), ignore_errors=True)
        tmp = os.path.join(parent, ".lists-build-%d" % os.getpid())
        os.makedirs(tmp)
        try:
            say("building MUST PLAY %d per system, genres from %.1f stars" % (top, stars))
            out = build(cfg, tmp, top, stars)
            res = summary(out)
            if not res["games"]:
                raise ListsError("NO SCRAPED GAMES YET - SCRAPE FIRST")
            say("checked; swapping in")
            swap_in(cfg, out, stamp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        res.update(top=top, stars=stars, when=stamp)
        write_state(cfg["state"], lists_last=res)
        return res
    finally:
        lock.close()


def reload_esde(cfg, run=subprocess.run, proc=None):
    """Restart ES-DE like the deploy handler (ask the session loop for ES-DE, then kill -9 so it cannot save its old state
    over the new lists). Same guard and lock as the rebuild. -> (ok, message)."""
    if cfg["edition"] != "community":
        return False, MANAGED
    why = busy(cfg, proc)
    if why:
        return False, why + " - RELOAD LATER"
    try:
        lock = take_lock(cfg)
    except ListsError:
        return False, "A REBUILD IS RUNNING - RELOAD LATER"
    try:
        rd = cfg["run_dir"]
        if rd and os.path.isdir(rd):
            try:
                with open(os.path.join(rd, "next-mode"), "w", encoding="utf-8") as f:
                    f.write("esde\n")
            except OSError:
                pass
        r = run(list(KILL), capture_output=True, text=True)
        return (True, "ES-DE RELOADS") if r.returncode in (0, 1) else (False, "COULD NOT RESTART ES-DE")
    finally:
        lock.close()


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] not in ("rebuild", "reload", "status"):
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return 2
    cmd, rest = a[0], a[1:]
    delay = 0.0
    if cmd == "reload" and rest[:1] == ["--delay"] and len(rest) >= 2:
        try:
            delay, rest = float(rest[1]), rest[2:]
        except ValueError:
            return 2
    if len(rest) > 1:
        return 2
    try:
        cfg = load_config(rest[0] if rest else None)
    except ListsError as e:
        print(str(e), file=sys.stderr)
        return 1
    if cmd == "status":
        print(json.dumps(read_state(cfg["state"]).get("lists_last") or {}))
        return 0
    if cmd == "reload":
        time.sleep(delay)
        ok, msg = reload_esde(cfg)
        print(msg)
        return 0 if ok else 1
    try:
        res = rebuild(cfg, say=lambda t: print(t, file=sys.stderr, flush=True))
    except ListsError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
