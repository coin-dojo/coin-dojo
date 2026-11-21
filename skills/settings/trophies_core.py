#!/usr/bin/env python3
"""TROPHIES page data (#176): RetroAchievements web API -> a small model the page draws. Pure stdlib.

Layout after NeoStation's RetroAchievements tab: profile (picture, name, hardcore + softcore points, rank),
ACHIEVEMENT OF THE WEEK, RECENT UNLOCKS, RECENTLY PLAYED (progress per game).

Config (the .hks entry file ES-DE passes, JSON written by the settings role):
    {"user": "<RA user>", "key_file": "~/.config/coin-dojo/trophies-key.json", "cache_dir": "~/.cache/coin-dojo/trophies",
     "settings_state": "~/.config/coin-dojo/settings-state.json",
     "cheevos_files": {"retroarch": "~/.config/retroarch/retroarch.cfg", "flycast": "~/.config/flycast/emu.cfg"}}
User (#196): the one logged in through SETTINGS > RETROACHIEVEMENTS when the app owns the login (settings_state has
"cheevos_login": true; the name from retroarch.cfg cheevos_username, else emu.cfg [achievements] UserName), else
"user" (hk_cheevos_user). Without settings_state / cheevos_files in the entry only "user" counts. Looked up again on
every refresh, so the resident overlay follows a login made while it runs.
The key file (0600, written by the deploy from the Mac's Keychain; never git) is {"web_api_key": "..."}.
The key only ever goes into the request URL the RA web API requires; it is never logged, printed or cached, and
error messages are built without the URL.

The page shows the cached data at once and refreshes in the background; a failed refresh keeps the cache.
"""
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://retroachievements.org/API/"
MEDIA = "https://media.retroachievements.org"
MEDIA_HOST = "media.retroachievements.org"
UA = "coin-dojo-trophies/1"
RECENT_N, PLAYED_N = 5, 5          # rows that fit above the help bar
MAX_IMAGE_BYTES = 512 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
DEFAULT_KEY_FILE = "~/.config/coin-dojo/trophies-key.json"
DEFAULT_CACHE = "~/.cache/coin-dojo/trophies"


class FetchError(Exception):
    """A refresh failed; the message never contains the URL (it carries the key)."""


# ------------------------------------------------------------------------------------------------
# config + key
# ------------------------------------------------------------------------------------------------
def load_config(entry):
    cfg = {}
    if entry and os.path.isfile(entry):
        try:
            with open(entry, encoding="utf-8") as fh:
                txt = fh.read().strip()
            cfg = json.loads(txt) if txt else {}
        except (OSError, ValueError):
            cfg = {}
    cfg = cfg if isinstance(cfg, dict) else {}
    files = cfg.get("cheevos_files") if isinstance(cfg.get("cheevos_files"), dict) else {}
    return {"user": str(cfg.get("user") or ""),
            "key_file": os.path.expanduser(cfg.get("key_file") or DEFAULT_KEY_FILE),
            "cache_dir": os.path.expanduser(cfg.get("cache_dir") or DEFAULT_CACHE),
            "settings_state": os.path.expanduser(cfg["settings_state"]) if isinstance(cfg.get("settings_state"), str) else "",
            "cheevos_files": {k: os.path.expanduser(v) for k, v in files.items()
                              if k in ("retroarch", "flycast") and isinstance(v, str)}}


USER_RE = re.compile(r"[A-Za-z0-9_.\-]{1,32}")          # hk_cheevos_user's pattern (= settings_core.CHEEVOS_USER_RE)


def _read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(1024 * 1024)
    except (OSError, TypeError):
        return None


def app_user(cfg):
    """#196: the user logged in through the SETTINGS app, or '' (then cfg["user"] counts). Only when the app's state
    file says "cheevos_login": true (logged out there, missing, malformed or another value = ''); the name must match
    USER_RE. Reads only the user name line, never the token."""
    import settings_core as sc                       # same dir on the cabinet (settings_app_files)
    st = _read_text(cfg.get("settings_state") or "")
    try:
        st = json.loads(st) if st else None
    except ValueError:
        st = None
    if not isinstance(st, dict) or st.get("cheevos_login") is not True:
        return ""
    files = cfg.get("cheevos_files") or {}
    for name in (sc.retroarch_value(_read_text(files.get("retroarch", "")), sc.RA_KEYS["user"]),
                 sc.ini_value(_read_text(files.get("flycast", "")), sc.FLY_SECTION, sc.FLY_KEYS["user"])):
        if name and USER_RE.fullmatch(name):
            return name
    return ""


def current_user(cfg):
    """The user TROPHIES shows: the app's login (#196), else the deploy's hk_cheevos_user."""
    return app_user(cfg) or cfg.get("user") or ""


def read_key(path):
    """The web API key, or '' when the file is missing, unreadable or malformed."""
    try:
        with open(path, encoding="utf-8") as fh:
            k = json.load(fh).get("web_api_key", "")
    except (OSError, ValueError, AttributeError):
        return ""
    return k if isinstance(k, str) and re.fullmatch(r"[A-Za-z0-9]{8,64}", k) else ""


# ------------------------------------------------------------------------------------------------
# fetching
# ------------------------------------------------------------------------------------------------
def http_get(url, timeout=12, limit=4 * 1024 * 1024):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(limit + 1)[:limit + 1]
    except urllib.error.HTTPError as e:
        raise FetchError("RetroAchievements answered HTTP %d" % e.code) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise FetchError("no connection to RetroAchievements (%s)" % type(e).__name__) from None


def api_url(endpoint, **params):
    return API + endpoint + "?" + urllib.parse.urlencode(params)


def fetch(user, key, get=http_get):
    """-> (summary doc, achievement-of-the-week doc or None). Raises FetchError."""
    if not user or not key:
        raise FetchError("no RetroAchievements user or web API key")
    raw = get(api_url("API_GetUserSummary.php", u=user, y=key, g=PLAYED_N, a=RECENT_N * 2))
    try:
        summary = json.loads(raw)
    except ValueError:
        raise FetchError("RetroAchievements sent something that is not JSON") from None
    if not isinstance(summary, dict) or not summary.get("User"):
        raise FetchError("RetroAchievements did not return a user summary (wrong user or key?)")
    try:
        aotw = json.loads(get(api_url("API_GetAchievementOfTheWeek.php", y=key)))
    except (FetchError, ValueError):
        aotw = None                                   # the page works without it
    return summary, aotw if isinstance(aotw, dict) else None


# ------------------------------------------------------------------------------------------------
# parsing (tolerant: missing fields become defaults)
# ------------------------------------------------------------------------------------------------
def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _str(v):
    return v.strip() if isinstance(v, str) else ""


def parse_summary(doc):
    doc = doc if isinstance(doc, dict) else {}
    awarded = doc.get("Awarded") if isinstance(doc.get("Awarded"), dict) else {}
    profile = {"user": _str(doc.get("User")), "points": _int(doc.get("TotalPoints")),
               "softcore": _int(doc.get("TotalSoftcorePoints")), "rank": _int(doc.get("Rank")),
               "ranked": _int(doc.get("TotalRanked")), "pic": _str(doc.get("UserPic")),
               "presence": _str(doc.get("RichPresenceMsg")), "status": _str(doc.get("Status"))}
    recent = []
    ra = doc.get("RecentAchievements")
    for game in (ra.values() if isinstance(ra, dict) else []):
        for a in (game.values() if isinstance(game, dict) else []):
            if not isinstance(a, dict):
                continue
            recent.append({"title": _str(a.get("Title")), "desc": _str(a.get("Description")),
                           "game": _str(a.get("GameTitle")), "points": _int(a.get("Points")),
                           "date": _str(a.get("DateAwarded")), "badge": _str(a.get("BadgeName")),
                           "hardcore": bool(_int(a.get("HardcoreAchieved")))})
    recent.sort(key=lambda a: a["date"], reverse=True)
    played = []
    for g in (doc.get("RecentlyPlayed") if isinstance(doc.get("RecentlyPlayed"), list) else []):
        if not isinstance(g, dict):
            continue
        aw = awarded.get(str(g.get("GameID")), {}) if isinstance(awarded.get(str(g.get("GameID")), {}), dict) else {}
        total = _int(aw.get("NumPossibleAchievements"), _int(g.get("AchievementsTotal")))
        got = max(_int(aw.get("NumAchieved")), _int(aw.get("NumAchievedHardcore")))
        played.append({"title": _str(g.get("Title")), "console": _str(g.get("ConsoleName")),
                       "icon": _str(g.get("ImageIcon")), "last": _str(g.get("LastPlayed")),
                       "total": total, "got": min(got, total) if total else got})
    return {"profile": profile, "recent": recent[:RECENT_N], "played": played[:PLAYED_N]}


def parse_aotw(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get("Achievement"), dict):
        return None
    a, g, c = doc["Achievement"], doc.get("Game") or {}, doc.get("Console") or {}
    out = {"title": _str(a.get("Title")), "desc": _str(a.get("Description")), "points": _int(a.get("Points")),
           "badge": _str(a.get("BadgeName")), "game": _str(g.get("Title")) if isinstance(g, dict) else "",
           "console": _str(c.get("Title")) if isinstance(c, dict) else ""}
    return out if out["title"] else None


def model(summary, aotw):
    m = parse_summary(summary)
    m["aotw"] = parse_aotw(aotw)
    return m


# ------------------------------------------------------------------------------------------------
# cache (the parsed model only: never the key, never raw URLs with the key)
# ------------------------------------------------------------------------------------------------
def cache_path(cache_dir):
    return os.path.join(cache_dir, "trophies.json")


def save_cache(cache_dir, m, now=None):
    os.makedirs(cache_dir, exist_ok=True)
    data = dict(m, fetched_at=int(now if now is not None else time.time()))
    fd, tmp = tempfile.mkstemp(dir=cache_dir, prefix=".trophies-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, cache_path(cache_dir))


def load_cache(cache_dir):
    try:
        with open(cache_path(cache_dir), encoding="utf-8") as fh:
            m = json.load(fh)
    except (OSError, ValueError):
        return None
    return m if isinstance(m, dict) and isinstance(m.get("profile"), dict) else None


# ------------------------------------------------------------------------------------------------
# images (badges, game icons, the user picture): media host only, PNG only, size-capped, cached
# ------------------------------------------------------------------------------------------------
def image_url(kind, name):
    """kind badge|icon|pic; name = BadgeName, ImageIcon path (/Images/x.png) or UserPic path."""
    if not name:
        return ""
    if kind == "badge":
        return "%s/Badge/%s.png" % (MEDIA, urllib.parse.quote(name)) if re.fullmatch(r"[0-9A-Za-z_]+", name) else ""
    if re.fullmatch(r"/(Images|UserPic)/[0-9A-Za-z_.-]+\.png", name):
        return MEDIA + name
    return ""


def image_file(cache_dir, url):
    if not url:
        return ""
    return os.path.join(cache_dir, "img", re.sub(r"[^0-9A-Za-z_.-]", "_", url.split("://", 1)[-1].split("/", 1)[-1]))


def fetch_image(cache_dir, url, get=None):
    """Download one image into the cache (once). -> local path or '' (any failure: draw a placeholder)."""
    path = image_file(cache_dir, url)
    if not path:
        return ""
    if os.path.isfile(path):
        return path
    if urllib.parse.urlparse(url).hostname != MEDIA_HOST:
        return ""
    try:
        data = (get or (lambda u: http_get(u, timeout=8, limit=MAX_IMAGE_BYTES)))(url)
    except FetchError:
        return ""
    if not data.startswith(PNG_MAGIC) or len(data) > MAX_IMAGE_BYTES:
        return ""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return path


def images_for(m):
    """Every (kind, name) the page shows, in draw order."""
    out = [("pic", m.get("profile", {}).get("pic", ""))]
    if m.get("aotw"):
        out.append(("badge", m["aotw"].get("badge", "")))
    out += [("badge", a.get("badge", "")) for a in m.get("recent", [])]
    out += [("icon", g.get("icon", "")) for g in m.get("played", [])]
    return [(k, n) for k, n in out if n]


# ------------------------------------------------------------------------------------------------
# the page (state + input)
# ------------------------------------------------------------------------------------------------
def ago(stamp, now=None):
    """'2026-11-16 23:41:07' (RA, UTC) -> '5 MIN AGO' / '3 H AGO' / '2 DAYS AGO' / the date."""
    try:
        t = time.mktime(time.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S")) - time.timezone
    except (ValueError, TypeError):
        return ""
    d = max(0, (now if now is not None else time.time()) - t)
    if d < 3600:
        return "%d MIN AGO" % max(1, d // 60)
    if d < 86400:
        return "%d H AGO" % (d // 3600)
    if d < 7 * 86400:
        n = int(d // 86400)
        return "%d DAY%s AGO" % (n, "" if n == 1 else "S")
    return stamp[:10]


class Page:
    """State of the TROPHIES page. refresh() is what the background thread runs."""

    def __init__(self, cfg, get=http_get, now=time.time):
        self.cfg, self.get, self.now = cfg, get, now
        self.key = read_key(cfg["key_file"])
        self.user = current_user(cfg)
        self.data = self._cached()
        self.state = "loading" if (self.key and self.user) else "nokey"
        self.error = ""
        self.quit = False
        self.focus = 0                                  # 0 recent unlocks, 1 recently played
        self.sel = [0, 0]

    def _cached(self):
        """The cache, only if it is this user's (#196: after a login as someone else the old page is not shown)."""
        d = load_cache(self.cfg["cache_dir"])
        cached = (d or {}).get("profile", {}).get("user", "")
        return d if d and isinstance(cached, str) and cached.lower() == self.user.lower() else None

    def refresh(self):
        user = current_user(self.cfg)                   # #196: a login in SETTINGS since the last refresh counts
        if user != self.user:
            self.user = user
            self.data = self._cached()
        if not (self.key and self.user):
            self.state = "nokey"
            return
        try:
            summary, aotw = fetch(self.user, self.key, self.get)
            m = model(summary, aotw)
            save_cache(self.cfg["cache_dir"], m, self.now())
            self.data, self.state, self.error = load_cache(self.cfg["cache_dir"]), "ok", ""
        except FetchError as e:
            self.state, self.error = "offline", str(e)
        except OSError:
            self.state, self.error = "offline", "could not save the trophies cache"

    def handle(self, a):
        if a in ("exit", "back", "esc"):
            self.quit = True
        elif a in ("left", "right"):
            self.focus = 0 if a == "left" else 1
        elif a in ("up", "down") and self.data:
            n = len(self.data.get("recent" if self.focus == 0 else "played", []))
            if n:
                self.sel[self.focus] = max(0, min(n - 1, self.sel[self.focus] + (1 if a == "down" else -1)))

    def view(self):
        d = self.data or {}
        when = d.get("fetched_at")
        if self.state == "nokey":
            status = "NO RETROACHIEVEMENTS KEY"
        elif self.state == "loading":
            status = "UPDATING …" if d else "LOADING …"
        elif self.state == "offline":
            status = "OFFLINE  -  DATA FROM %s" % time.strftime("%d %b %H:%M", time.localtime(when)).upper() if when else "OFFLINE"
        else:
            status = "UPDATED %s" % time.strftime("%H:%M", time.localtime(when)) if when else ""
        return {"state": self.state, "error": self.error, "status": status, "data": d, "focus": self.focus,
                "sel": list(self.sel), "user": self.user, "now": self.now()}
