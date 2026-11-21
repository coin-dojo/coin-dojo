#!/usr/bin/env python3
"""Controls helper (#57): one controls card per layout for the ES-DE info panel, plus a per-game in-game
overlay for RetroArch (START+HP shows it and pauses; START+HP again hides it and resumes).

How the pieces fit:
  * ES-DE can only show a per-game picture through game metadata, so every game gets a <controller>
    type in its gamelist (LAYOUTS[..]["controller"]) and the theme maps each controller type to our card
    PNG (customControllerIcon). Nested lists copy <controller> from the real gamelists.
  * RetroArch: a per-game override (config/<Core>/<rom stem>.cfg) loads an overlay with two pages,
    page 0 empty and page 1 the card; input_overlay_next + input_pause_toggle both sit on HP (with the START hotkey enabler).

Commands (dry run unless --apply; stdlib only except `render`, which needs Pillow):
  plan       --gamelists DIR                       layout per game, counts per system/layout
  gamelists  --gamelists DIR [--apply]             set <controller> in the real system gamelists
  render     --gamelists DIR --fonts DIR --out DIR theme cards + overlay PNG/cfg + override fragments
  overrides  --src DIR --dest DIR [--apply]        merge the override fragments into RetroArch's config dir
"""
import argparse
import html
import os
import re
import sys

# --- what each layout is -------------------------------------------------------------------------------
# buttons: the six cabinet buttons in panel order, top row LP MP HP then bottom row LK MK HK ("" = unused).
# tested: the maintainer played it with this layout. Untested cards carry a NOT TESTED tag.
LAYOUTS = {
    "arcade6": {"title": "ARCADE 6-BUTTON", "controller": "joystick_arcade_6_buttons", "tested": True,
                "buttons": ["LP", "MP", "HP", "LK", "MK", "HK"], "note": "Punches on top, kicks below",
                "select": "COIN"},
    # confirmed twice by the maintainer: Eternal Champions, and Streets of Rage 2 (LK special, MK punch, HK jump = A B C)
    "genesis6": {"title": "GENESIS 6-BUTTON", "controller": "gamepad_sega_md_6_buttons", "tested": True,
                 "buttons": ["X", "Y", "Z", "A", "B", "C"], "note": "START start / pause"},
    "atari": {"title": "ATARI JOYSTICK", "controller": "joystick_arcade_1_button", "tested": True,
              "buttons": ["FIRE"] * 6, "note": "Any button fires · SELECT game select · START reset",
              "start": "RESET", "select": "GAME"},
    "twinstick": {"title": "TWIN STICK", "controller": "joystick_arcade_no_buttons_twin", "tested": True,
                  "buttons": [], "note": "Left stick moves · right stick shoots",
                  "select": "COIN"},
    "snes": {"title": "SUPER NINTENDO", "controller": "gamepad_nintendo_snes", "tested": True,
             "buttons": ["Y", "X", "L", "B", "A", "R"], "note": "START start / pause"},
    # The maintainer's one rule (2026-11-14): panel = Xbox X Y RB / A B RT, each system's standard Xbox mapping on top.
    # Neo Geo: remaps/FinalBurn Neo/neogeo.rmp puts A B C on the top row and D on Short.
    "neogeo": {"title": "NEO GEO", "controller": "joystick_arcade_4_buttons", "tested": True,
               "buttons": ["A", "B", "C", "D", "", ""], "note": "SELECT coin · START start",
               "select": "COIN"},
    # N64 (remaps/Mupen64Plus-Next): separate RetroArch 1.22, so card only for now
    "n64": {"title": "NINTENDO 64", "controller": "gamepad_nintendo_64", "tested": False,
            "buttons": ["B", "C-UP", "R", "A", "Z", "C-DN"], "note": "START start"},
    # Batocera's default for FBNeo (the maintainer 2026-11-15: "assume from Batocera"): no remap, RetroPad device 1, which FBNeo
    # turns into its "Classic" pad. Generic game: Fire1 B, Fire2 A, Fire3 Y, Fire4 X, Fire5 R, Fire6 L (FBNeo
    # retro_input.cpp RETRO_DEVICE_ID_FIRE01..06); panel Y X L / B A R. Tested by the maintainer 2026-11-15.
    "fbneo": {"title": "ARCADE BUTTONS", "controller": "joystick_arcade_3_buttons", "tested": True,
              "buttons": ["3", "4", "6", "1", "2", "5"], "note": "Most games use 1 to 3 · SELECT coin",
              "select": "COIN"},
    # Nestopia / Beetle PCE defaults: retropad B and A (bottom left pair)
    "nes": {"title": "NES", "controller": "gamepad_nintendo_nes", "tested": True,
            "buttons": ["TB", "TA", "", "B", "A", ""], "note": "Top row = turbo B / turbo A · SELECT select"},
    "pcengine": {"title": "PC ENGINE", "controller": "gamepad_generic", "tested": True,
                 "buttons": ["", "", "", "II", "I", ""], "note": "SELECT select · START run",
                 "start": "RUN"},
    "saturn": {"title": "SATURN", "controller": "gamepad_sega_md_3_buttons", "tested": True,
               "buttons": ["X", "Y", "Z", "A", "B", "C"], "note": "START start / pause"},
    "psx": {"title": "PLAYSTATION", "controller": "gamepad_playstation", "tested": True,
            "buttons": ["SQUARE", "TRIANGLE", "R1", "CROSS", "CIRCLE", "R2"], "note": "START start / pause"},
    # standalone flycast 2.7 with skills/controls/flycast/SDL_USB gamepad.cfg (built-in default with LP/MP swapped
    # onto X/Y; HP/HK = the L/R triggers). Tested by the maintainer 2026-11-15 on a CPS3 fighter port (tests check the file).
    "dreamcast": {"title": "DREAMCAST", "controller": "gamepad_sega_dreamcast", "tested": True,
                  "buttons": ["X", "Y", "LT", "A", "B", "RT"], "note": "SELECT flycast menu · START start",
                  "select": "MENU",
                  "overlay": False},                         # standalone flycast: no RetroArch overlay
}
POSITIONS = ["LP", "MP", "HP", "LK", "MK", "HK"]
# real system dir -> layout. Systems not listed get no card and no overlay.
SYSTEM_LAYOUT = {"cps1": "arcade6", "cps2": "arcade6", "cps3": "arcade6", "genesis": "genesis6",
                 "segacd": "genesis6", "atari2600": "atari", "snes": "snes", "neogeo": "neogeo",
                 "saturn": "saturn", "psx": "psx", "dreamcast": "dreamcast", "fbneo": "fbneo",
                 "nes": "nes", "pcengine": "pcengine", "n64": "n64"}
# (system, rom stem) -> layout, wins over SYSTEM_LAYOUT
# FBNeo's six-button fighter layout (P1 Weak/Medium/Strong Punch + Kick -> Y X L / B A R = LP MP HP / LK MK HK, no
# remap): every fbneo-dir game that triggers it on the cabinet (scan of FBNeo's drivers, 2026-11-15).
GAME_LAYOUT = {("fbneo", "sf"): "arcade6", ("fbneo", "gblchmp"): "arcade6"}
# Twin-stick games (#59): P1 stick moves, P2 stick fires. Each one needs its game remap
# remaps/<core dir>/<rom stem>.rmp (user 2 feeds core port 1; tests check both exist together).
TWIN_STICK = {("fbneo", "robotron"), ("fbneo", "smashtv"), ("fbneo", "totcarn"), ("fbneo", "bwidow"),
              ("n64", "Robotron 64"), ("snes", "Super Smash T.V. (USA)")}
GAME_LAYOUT.update({g: "twinstick" for g in TWIN_STICK})
# libretro core per system -> RetroArch's config dir name (the core's library_name). No entry = no
# RetroArch overlay (dreamcast runs standalone flycast).
CORE_DIR = {"cps1": "FinalBurn Neo", "cps2": "FinalBurn Neo", "cps3": "FinalBurn Neo",
            "fbneo": "FinalBurn Neo", "neogeo": "FinalBurn Neo", "genesis": "Genesis Plus GX",
            "segacd": "Genesis Plus GX", "snes": "Snes9x", "saturn": "Beetle Saturn",
            "psx": "PCSX-ReARMed", "atari2600": "Stella", "nes": "Nestopia", "pcengine": "Beetle PCE",
            "n64": "Mupen64Plus-Next"}
SYSTEM_NAME = {"cps1": "CPS-1", "cps2": "CPS-2", "cps3": "CPS-3", "fbneo": "Arcade", "neogeo": "Neo Geo",
               "genesis": "Genesis", "segacd": "Sega CD", "snes": "SNES", "saturn": "Saturn",
               "psx": "PlayStation", "n64": "Nintendo 64", "dreamcast": "Dreamcast", "atari2600": "Atari 2600"}
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collections")); import nested; VIRTUALS = set(nested.ALL_VIRTUAL)  # noqa: E702,E401  generated lists (#94): never edited here
MARK = "# controls helper (coin-dojo #57): generated by skills/controls/controls.py"
OVERRIDE_KEYS = ("input_overlay_enable", "input_overlay", "input_overlay_opacity", "input_overlay_show_inputs",
                 "input_overlay_hide_in_menu", "input_overlay_auto_scale", "input_overlay_behind_menu")
SEPARATORS = (": ", " - ", " / ")


def layout_for(system, rom_stem):
    return GAME_LAYOUT.get((system, rom_stem)) or SYSTEM_LAYOUT.get(system)


# --- per-game action labels (N64) ----------------------------------------------------------------------
# n64_games.tsv: what each panel button DOES in that game. Used on the in-game overlay only; the ES-DE card
# stays the system card (ES-DE cannot show per-game pictures).
N64_TSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "n64_games.tsv")
N64_COLUMNS = ["stem", "layout", "Jab", "Strong", "Fierce", "Short", "Forward", "Roundhouse", "confidence", "note"]
N64_LAYOUTS = ("n64", "n64-remap")
N64_CONFIDENCE = ("played", "known", "guess")
N64_PAD = ["B", "C-UP", "R", "A", "Z", "C-DN"]        # the default panel remap (LAYOUTS["n64"]["buttons"])
LABEL_MAX = 14


def parse_n64_games(text, source="n64_games.tsv"):
    """{rom stem: {"layout", "labels": [6 str], "confidence", "note"}}. Raises ValueError("<source>:<line>: ...")
    on a malformed row: wrong column count, bad layout/confidence, label too long, duplicate stem."""
    out, header = {}, False
    for no, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.startswith("#"):
            continue
        cols = raw.split("\t")
        if not header:
            if cols[:len(N64_COLUMNS)] != N64_COLUMNS:
                raise ValueError("%s:%d: header must be %s" % (source, no, "<TAB>".join(N64_COLUMNS)))
            header = True
            continue
        if len(cols) != len(N64_COLUMNS):
            raise ValueError("%s:%d: %d columns, need %d (6 labels, empty = unused): %r"
                             % (source, no, len(cols), len(N64_COLUMNS), raw[:60]))
        stem, layout, labels, conf, note = cols[0], cols[1], cols[2:8], cols[8], cols[9]
        if not stem.strip() or stem != stem.strip():
            raise ValueError("%s:%d: empty or padded ROM stem" % (source, no))
        if layout not in N64_LAYOUTS:
            raise ValueError("%s:%d: layout %r, need one of %s" % (source, no, layout, "|".join(N64_LAYOUTS)))
        if conf not in N64_CONFIDENCE:
            raise ValueError("%s:%d: confidence %r, need one of %s" % (source, no, conf, "|".join(N64_CONFIDENCE)))
        for lab in labels:
            if len(lab) > LABEL_MAX or lab != lab.strip():
                raise ValueError("%s:%d: label %r is padded or over %d chars" % (source, no, lab, LABEL_MAX))
        if stem in out:
            raise ValueError("%s:%d: duplicate stem %r" % (source, no, stem))
        out[stem] = {"layout": layout, "labels": labels, "confidence": conf, "note": note}
    if not header:
        raise ValueError("%s: no header row" % source)
    return out


_N64_CACHE = {}


def n64_games(path=None):
    path = path or N64_TSV
    if path not in _N64_CACHE:
        with open(path, encoding="utf-8") as f:
            _N64_CACHE[path] = parse_n64_games(f.read(), os.path.basename(path))
    return _N64_CACHE[path]


def game_labels(system, rom_stem, table=None):
    """(labels, positions) for the in-game overlay when the game has per-game action labels, else None
    (the overlay then shows the system layout's buttons). Positions are what is printed under each button:
    the N64 pad button for the default remap, the panel position (LP..HK) for games waiting on a #64 remap,
    because their pad buttons are not decided yet. A row with no label at all (e.g. twin stick) gives None."""
    if system != "n64":
        return None
    row = (table if table is not None else n64_games()).get(rom_stem)
    if not row or not any(row["labels"]):
        return None
    return row["labels"], (N64_PAD if row["layout"] == "n64" else POSITIONS)


def split_title(name):
    """'Avenue Fighter III: Third Round - Fight for the Future' -> ('Avenue Fighter III', 'Third Round - ...').
    Splits at the earliest of ': ', ' - ', ' / '; no separator (or nothing before it) -> (name, '')."""
    hits = sorted((name.find(s), s) for s in SEPARATORS if name.find(s) > 0)
    if not hits:
        return name.strip(), ""
    i, s = hits[0]
    return name[:i].strip(), name[i + len(s):].strip()


# --- gamelists (text edits: keep ES-DE's formatting and every other field as it is) ---------------------
GAME_RE = re.compile(r"(<game>)(.*?)(</game>)", re.S)


def _field(body, tag):
    m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), body, re.S)
    return html.unescape(m.group(1).strip()) if m else ""


def read_games(path):
    """[(rom stem, {field: value})] for one gamelist.xml."""
    out = []
    for m in GAME_RE.finditer(open(path, encoding="utf-8").read()):
        body = m.group(2)
        rel = _field(body, "path")
        stem = os.path.splitext(os.path.basename(rel))[0]
        out.append((stem, {t: _field(body, t) for t in ("name", "releasedate", "developer", "genre",
                                                         "players", "rating", "controller")}))
    return out


def system_gamelists(root):
    """{system: gamelist path} for the real systems under an ES-DE gamelists dir."""
    out = {}
    for s in sorted(os.listdir(root)):
        p = os.path.join(root, s, "gamelist.xml")
        if s not in VIRTUALS and os.path.isfile(p):
            out[s] = p
    return out


def set_controllers(text, system):
    """Return (new text, changed count): <controller> set from the layout table on every <game>, removed
    where the game has no layout (so a layout taken out of the table also goes away)."""
    changed = [0]

    def fix(m):
        body = m.group(2)
        stem = os.path.splitext(os.path.basename(_field(body, "path")))[0]
        lay = layout_for(system, stem)
        want = LAYOUTS[lay]["controller"] if lay else None
        have = _field(body, "controller") or None
        if want == have:
            return m.group(0)
        changed[0] += 1
        if have is not None:
            body = re.sub(r"\n?[ \t]*<controller>.*?</controller>", "", body, count=1, flags=re.S)
        if want:
            indent = re.search(r"\n([ \t]*)<path>", body)
            ind = indent.group(1) if indent else "\t\t"
            close = re.search(r"\n[ \t]*$", body)
            pos = close.start() if close else len(body)
            body = body[:pos] + "\n%s<controller>%s</controller>" % (ind, want) + body[pos:]
        return m.group(1) + body + m.group(3)

    return GAME_RE.sub(fix, text), changed[0]


def cmd_plan(args):
    counts = {}
    for system, path in system_gamelists(args.gamelists).items():
        for stem, _f in read_games(path):
            lay = layout_for(system, stem) or "-"
            counts[(system, lay)] = counts.get((system, lay), 0) + 1
    for (system, lay), n in sorted(counts.items()):
        print("%-10s %-10s %4d" % (system, lay, n))
    print("games with a card: %d, without: %d" % (sum(n for (s, l), n in counts.items() if l != "-"),
                                                  sum(n for (s, l), n in counts.items() if l == "-")))
    return 0


def cmd_gamelists(args):
    total = 0
    for system, path in system_gamelists(args.gamelists).items():
        text = open(path, encoding="utf-8").read()
        new, n = set_controllers(text, system)
        if n:
            print("%-10s %4d games change" % (system, n))
            total += n
            if args.apply:
                tmp = path + ".controls-tmp"
                open(tmp, "w", encoding="utf-8").write(new)
                os.replace(tmp, path)
    print("%s: %d <controller> changes" % ("APPLIED" if args.apply else "PLAN (dry run)", total))
    return 0


# --- RetroArch overrides -------------------------------------------------------------------------------
# In-game overlays OFF (2026-11-14): RetroArch 1.20 + libwayland 1.24 deadlocks in flush_wayland_fd (RA #18148)
# with an overlay loaded (Alien Crush froze). Turn back on only once the cabinet runs software cores on 1.22+.
OVERLAYS_ON = False


def override_text(overlay_cfg, on=None):
    on = OVERLAYS_ON if on is None else on
    return "\n".join([MARK,
                      'input_overlay_enable = "%s"' % ("true" if on else "false"),
                      'input_overlay = "%s"' % overlay_cfg,
                      'input_overlay_opacity = "1.000000"',
                      'input_overlay_show_inputs = "0"',
                      'input_overlay_hide_in_menu = "true"',
                      'input_overlay_auto_scale = "false"',
                      'input_overlay_behind_menu = "false"']) + "\n"


def merge_override(existing, fragment):
    """Keep every line of an existing override except our own keys (and our marker), then add ours."""
    keep = [ln for ln in (existing or "").splitlines()
            if ln.strip() != MARK and ln.split("=", 1)[0].strip() not in OVERRIDE_KEYS]
    while keep and not keep[-1].strip():
        keep.pop()
    return ("\n".join(keep) + "\n" if keep else "") + fragment


def overlay_cfg_text(png_name):
    return "\n".join(["overlays = 2",
                      'overlay0_overlay = "../blank.png"', "overlay0_full_screen = false", "overlay0_descs = 0",
                      # full_screen false = drawn in the game's 4:3 viewport (cabinet: 1440x1080 at x 240 on a
                      # 1920x1080 panel); true would stretch the card across the whole panel
                      'overlay1_overlay = "%s"' % png_name, "overlay1_full_screen = false",
                      "overlay1_descs = 0"]) + "\n"


def cmd_overrides(args):
    n_new = n_merge = n_same = 0
    for core in sorted(c for c in os.listdir(args.src)
                       if not c.startswith(".") and os.path.isdir(os.path.join(args.src, c))):
        for f in sorted(x for x in os.listdir(os.path.join(args.src, core))
                        if x.endswith(".cfg") and not x.startswith(".")):   # skip macOS ._ files
            frag = open(os.path.join(args.src, core, f), encoding="utf-8").read()
            dest = os.path.join(args.dest, core, f)
            old = open(dest, encoding="utf-8").read() if os.path.exists(dest) else None
            new = merge_override(old, frag)
            if new == old:
                n_same += 1
                continue
            if old is None:
                n_new += 1
            else:
                n_merge += 1
                print("merge into existing override: %s/%s" % (core, f))
            if args.apply:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                open(dest, "w", encoding="utf-8").write(new)
    print("%s: %d new, %d merged, %d unchanged" % ("APPLIED" if args.apply else "PLAN (dry run)",
                                                   n_new, n_merge, n_same))
    return 0


# --- render (Pillow) -----------------------------------------------------------------------------------
# Colours + font files come from the style kit (brand/tokens.json, brand/BRAND.md; Design's).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "brand"))
import brand as _brand  # noqa: E402
BRAND = _brand.load()
NAVY, PANEL, BTN = BRAND.rgba("card"), BRAND.rgba("panel"), BRAND.rgba("button")
GOLD, CHROME, MUTED, DIM = BRAND.rgba("gold_card"), BRAND.rgba("chrome"), BRAND.rgba("muted_card"), BRAND.rgba("dim")
PENDING = BRAND.rgba("pending")
WELL = BRAND.rgba("bg_deep")
SHAPES = {"SQUARE", "TRIANGLE", "CROSS", "CIRCLE"}


class Fonts:
    def __init__(self, d):
        from PIL import ImageFont
        self.F = ImageFont
        self.d = d
        self.cache = {}

    def get(self, kind, px):
        f = BRAND["fonts"][{"disp": "display", "bold": "ui_bold", "semi": "ui_semi"}[kind]]["file"]
        k = (f, px)
        if k not in self.cache:
            self.cache[k] = self.F.truetype(os.path.join(self.d, f), px)
        return self.cache[k]


def _w(draw, text, font):
    b = draw.textbbox((0, 0), text, font=font)
    return b[2] - b[0]


def wrap(draw, text, font, width, max_lines):
    """Greedy word wrap; returns lines or None when it needs more than max_lines."""
    lines, cur = [], ""
    for word in text.split():
        t = (cur + " " + word).strip()
        if _w(draw, t, font) <= width or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines if len(lines) <= max_lines and all(_w(draw, ln, font) <= width for ln in lines) else None


def fit_lines(draw, fonts, kind, text, width, px, min_px, max_lines):
    """Biggest size from px down to min_px that wraps into max_lines; at min_px the last line is cut."""
    for size in range(px, min_px - 1, -2):
        f = fonts.get(kind, size)
        lines = wrap(draw, text, f, width, max_lines)
        if lines:
            return f, lines
    f = fonts.get(kind, min_px)
    lines = wrap(draw, text, f, width, 99) or [text]
    lines = lines[:max_lines]
    while _w(draw, lines[-1] + "…", f) > width and " " in lines[-1]:
        lines[-1] = lines[-1].rsplit(" ", 1)[0]
    lines[-1] += "…"
    return f, lines


def draw_stick(d, cx, cy, s, label, fonts):
    d.ellipse([cx - s, cy - s * 0.4, cx + s, cy + s * 1.2], fill=WELL, outline=DIM, width=3)
    d.line([cx, cy + s * 0.4, cx, cy - s * 0.7], fill=(201, 205, 224, 255), width=max(4, int(s * 0.18)))
    r = s * 0.42
    d.ellipse([cx - r, cy - s * 0.7 - r, cx + r, cy - s * 0.7 + r], fill=GOLD)
    if label:
        f = fonts.get("bold", max(14, int(s * 0.42)))
        d.text((cx, cy + s * 1.2 + 6), label, font=f, fill=MUTED, anchor="mt")


def draw_shape(d, kind, cx, cy, r, color):
    w = max(3, int(r * 0.16))
    if kind == "SQUARE":
        d.rectangle([cx - r, cy - r, cx + r, cy + r], outline=color, width=w)
    elif kind == "CIRCLE":
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=w)
    elif kind == "TRIANGLE":
        d.polygon([(cx, cy - r), (cx + r, cy + r * 0.8), (cx - r, cy + r * 0.8)], outline=color, width=w)
    else:
        d.line([cx - r, cy - r, cx + r, cy + r], fill=color, width=w)
        d.line([cx - r, cy + r, cx + r, cy - r], fill=color, width=w)


def action_fit(d, fonts, label, r, px):
    """Per-game action word inside a button circle: up to 2 lines (may break after a hyphen), biggest size
    from px down to 12 that fits. Returns (font, lines)."""
    f, lines = fit_lines(d, fonts, "bold", label.replace("-", "- "), r * 1.45, px, 12, 2)
    return f, [ln.replace("- ", "-") for ln in lines]


def draw_button(d, cx, cy, r, label, pos, ring, fonts, pos_px, action=False, max_px=0):
    """action=True: label is a per-game action word ("Jump"), wrapped to up to 2 lines inside the circle."""
    if not label:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=DIM, width=3)
    else:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=BTN, outline=ring, width=max(3, int(r * 0.09)))
        if action:
            f, lines = action_fit(d, fonts, label, r, max_px or int(r * 0.7))
            lh = int(f.size * 1.05)
            y = cy - lh * (len(lines) - 1) / 2
            for ln in lines:
                d.text((cx, y), ln, font=f, fill=CHROME, anchor="mm")
                y += lh
        elif label in SHAPES:
            draw_shape(d, label, cx, cy, r * 0.42, CHROME)
        else:
            px = int(r * 0.85)
            while px > 10 and _w(d, label, fonts.get("disp", px)) > r * 1.5:
                px -= 2
            d.text((cx, cy), label, font=fonts.get("disp", px), fill=CHROME, anchor="mm")
    if pos_px and label != pos:
        d.text((cx, cy + r + 4), pos, font=fonts.get("bold", pos_px), fill=MUTED, anchor="mt")


def draw_controls(d, lay, x0, y0, w, h, fonts, big, labels=None, positions=None):
    """Stick + the six buttons (or two sticks for twin stick) inside the box (x0, y0, w, h).
    labels/positions: per-game action words and what is printed under each button (see game_labels)."""
    L = LAYOUTS[lay]
    ring = GOLD if L["tested"] else PENDING
    if not L["buttons"]:
        s = h * 0.24
        draw_stick(d, x0 + w * 0.3, y0 + h * 0.36, s, "MOVE", fonts)
        draw_stick(d, x0 + w * 0.7, y0 + h * 0.36, s, "SHOOT (P2 stick)", fonts)
        return
    pos_px = int(h * 0.09) if big else 0
    r = min(h / (4.6 if big else 4.2), w / 11.5)
    gap = r * 0.55
    grid_w = 6 * r + 2 * gap
    stick_w = 3.2 * r
    left = x0 + (w - stick_w - grid_w) / 2
    draw_stick(d, left + r * 1.2, y0 + h * 0.42, r * 0.95, "", fonts)
    gx = left + stick_w
    row_h = 2 * r + (pos_px + 10 if big else gap)
    gy = y0 + (h - (2 * row_h - (pos_px + 10 if big else gap) + (pos_px + 6 if big else 0))) / 2
    # one text size for all six action words, so the card reads evenly (the longest word sets it)
    act_px = min((action_fit(d, fonts, x, r, int(r * 0.7))[0].size for x in labels if x), default=0) if labels else 0
    for i, label in enumerate(labels or L["buttons"]):
        cx = gx + r + (i % 3) * (2 * r + gap)
        cy = gy + r + (i // 3) * row_h
        draw_button(d, cx, cy, r, label, (positions or POSITIONS)[i], ring, fonts, pos_px, action=bool(labels), max_px=act_px)


def render_card(lay, fonts, W=706, H=340):
    """Theme card for the ES-DE info panel (shown at about half this size)."""
    from PIL import Image, ImageDraw
    L = LAYOUTS[lay]
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    border = GOLD if L["tested"] else PENDING
    d.rounded_rectangle([2, 2, W - 3, H - 3], radius=24, fill=NAVY, outline=border, width=5)
    d.text((26, 22), L["title"], font=fonts.get("disp", 30), fill=GOLD)
    if not L["tested"]:
        f = fonts.get("bold", 26)
        tw = _w(d, "NOT TESTED", f)
        d.rounded_rectangle([W - 26 - tw - 20, 20, W - 26, 58], radius=8, fill=PENDING)
        d.text((W - 26 - tw / 2 - 10, 39), "NOT TESTED", font=f, fill=NAVY, anchor="mm")
    # stick + six buttons with room to the edges; START and SELECT/COIN as two pills on the right
    pill_w = 132
    draw_controls(d, lay, 30, 74, W - 60 - pill_w - 16, H - 100, fonts, big=False)
    pf = fonts.get("bold", 28)
    px = W - 30 - pill_w
    for i, (txt, sub) in enumerate(((L.get("start", "START"), "START"), (L.get("select", "SELECT"), "SELECT"))):
        y = 96 + i * 104
        d.rounded_rectangle([px, y, px + pill_w, y + 50], radius=25, fill=BTN, outline=MUTED, width=3)
        d.text((px + pill_w / 2, y + 25), txt, font=pf, fill=CHROME, anchor="mm")
        if txt != sub:
            d.text((px + pill_w / 2, y + 58), sub, font=fonts.get("bold", 20), fill=MUTED, anchor="mt")
    return img


def draw_stars(d, x, y, size, rating):
    full = int(round(float(rating or 0) * 5))
    for i in range(5):
        cx, cy, r = x + i * size * 1.15 + size / 2, y + size / 2, size / 2
        pts = []
        for k in range(10):
            import math
            a = -math.pi / 2 + k * math.pi / 5
            rr = r if k % 2 == 0 else r * 0.45
            pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
        d.polygon(pts, fill=GOLD if i < full else DIM)
    return x + 5 * size * 1.15


def render_overlay(lay, system, game, fonts, W=1440, H=1080, labels=None, positions=None):
    """Full-screen in-game overlay: dim scrim + centred panel (title + subtitle, metadata, controls)."""
    from PIL import Image, ImageDraw
    L = LAYOUTS[lay]
    img = Image.new("RGBA", (W, H), (5, 4, 16, 150))
    d = ImageDraw.Draw(img)
    pw, x0 = 1040, (W - 1040) // 2
    inner = pw - 112
    name = (game.get("name") or "").strip()
    main, sub = (name, "") if _w(d, name.upper(), fonts.get("disp", 52)) <= inner else split_title(name)
    tf, tlines = fit_lines(d, fonts, "disp", main.upper(), inner, 52, 34, 2)
    sf, slines = (fit_lines(d, fonts, "semi", sub, inner, 34, 26, 2) if sub else (None, []))
    th = len(tlines) * int(tf.size * 1.22)
    sh = len(slines) * int(sf.size * 1.2) + 4 if sub else 0
    ctrl_h = 330
    ph = 48 + th + sh + 16 + 40 + 24 + ctrl_h + 44 + 70 + 36
    y0 = (H - ph) // 2
    d.rounded_rectangle([x0, y0, x0 + pw, y0 + ph], radius=28, fill=NAVY, outline=GOLD, width=6)
    y = y0 + 44
    for ln in tlines:
        d.text((x0 + 56, y), ln, font=tf, fill=CHROME)
        y += int(tf.size * 1.22)
    for ln in slines:
        d.text((x0 + 56, y + 4), ln, font=sf, fill=MUTED)
        y += int(sf.size * 1.2)
    y += 16 + (4 if sub else 0)
    year = (game.get("releasedate") or "")[:4]
    bits = [SYSTEM_NAME.get(system, system)] + [b for b in (year, game.get("developer"), game.get("genre")) if b]
    if game.get("players"):
        bits.append(game["players"] + (" player" if game["players"] == "1" else " players"))
    mf = fonts.get("semi", 30)
    meta = " · ".join(bits)
    while _w(d, meta, mf) > inner - 190 and len(bits) > 2:
        bits.pop(-2 if len(bits) > 3 else -1)
        meta = " · ".join(bits)
    d.text((x0 + 56, y), meta, font=mf, fill=MUTED)
    if game.get("rating"):
        draw_stars(d, x0 + pw - 56 - 5 * 30 * 1.15, y + 4, 30, game["rating"])
    y += 40 + 24
    d.rounded_rectangle([x0 + 40, y, x0 + pw - 40, y + ctrl_h], radius=18, fill=PANEL,
                        outline=GOLD if L["tested"] else PENDING, width=3)
    lf = fonts.get("disp", 24)
    d.text((x0 + 64, y + 18), L["title"], font=lf, fill=GOLD)
    if not L["tested"]:
        d.text((x0 + pw - 64, y + 18), "NOT TESTED YET", font=fonts.get("bold", 26), fill=PENDING, anchor="ra")
    draw_controls(d, lay, x0 + 50, y + 56, pw - 100, ctrl_h - 70, fonts, big=True, labels=labels,
                  positions=positions)
    y += ctrl_h + 14
    nf = fonts.get("semi", 28)
    note = ("Player 1 shown · Player 2 identical · " if L["buttons"] else "") + L["note"]
    if _w(d, note, nf) > inner:
        note = L["note"] + (" · P2 identical" if L["buttons"] else "")
    d.text((W / 2, y + 8), note, font=nf, fill=MUTED, anchor="mt")
    y += 56
    d.line([x0 + 56, y, x0 + pw - 56, y], fill=DIM, width=2)
    hf, hb = fonts.get("semi", 30), fonts.get("bold", 30)
    # card labels: HP = Fierce (button 5, close), MP = Strong (button 0, RetroArch menu)
    parts = [("START+HP", " close"), ("START+MP", " menu"), ("START+SELECT", " exit"), ("", "game paused")]
    widths = [_w(d, a, hb) + _w(d, b, hf) for a, b in parts]
    if sum(widths) + 60 * (len(parts) - 1) > inner:          # too wide: drop "game paused"
        parts, widths = parts[:-1], widths[:-1]
    x = (W - (sum(widths) + 60 * (len(parts) - 1))) / 2
    for (a, b), wd in zip(parts, widths):
        d.text((x, y + 20), a, font=hb, fill=GOLD)
        d.text((x + _w(d, a, hb), y + 20), b, font=hf, fill=CHROME if a else MUTED)
        x += wd + 60
    return img


def safe_stem(stem):
    if "/" in stem or stem in ("", ".", "..") or "\x00" in stem:
        raise ValueError("unsafe ROM stem %r" % stem)
    return stem


def cmd_render(args):
    from PIL import Image
    fonts = Fonts(args.fonts)
    cards = os.path.join(args.out, "cards")
    os.makedirs(cards, exist_ok=True)
    for lay in LAYOUTS:
        render_card(lay, fonts).save(os.path.join(cards, lay + ".png"))
    Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(os.path.join(cards, "none.png"))
    ov_root = os.path.join(args.out, "overlays")
    os.makedirs(ov_root, exist_ok=True)
    Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(os.path.join(ov_root, "blank.png"))
    n = 0
    for system, path in system_gamelists(args.gamelists).items():
        core = CORE_DIR.get(system)
        if not core or (args.only and system not in args.only):
            continue
        for stem, game in read_games(path):
            lay = layout_for(system, stem)
            if not lay:
                continue
            stem = safe_stem(stem)
            sd = os.path.join(ov_root, system)
            os.makedirs(sd, exist_ok=True)
            gl = game_labels(system, stem)
            render_overlay(lay, system, game, fonts, labels=gl[0] if gl else None,
                           positions=gl[1] if gl else None).save(os.path.join(sd, stem + ".png"), optimize=True)
            open(os.path.join(sd, stem + ".cfg"), "w", encoding="utf-8").write(overlay_cfg_text(stem + ".png"))
            od = os.path.join(args.out, "overrides", core)
            os.makedirs(od, exist_ok=True)
            target = "%s/%s/%s.cfg" % (args.overlay_root.rstrip("/"), system, stem)
            open(os.path.join(od, stem + ".cfg"), "w", encoding="utf-8").write(override_text(target))
            n += 1
    print("rendered %d cards, %d overlays into %s" % (len(LAYOUTS), n, args.out))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("plan")
    p.add_argument("--gamelists", required=True)
    p = sp.add_parser("gamelists")
    p.add_argument("--gamelists", required=True)
    p.add_argument("--apply", action="store_true")
    p = sp.add_parser("render")
    p.add_argument("--gamelists", required=True)
    p.add_argument("--fonts", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--overlay-root", default="/data/coin-dojo/controls/overlays")
    p.add_argument("--only", nargs="*", help="limit overlays to these systems (quick previews)")
    p = sp.add_parser("overrides")
    p.add_argument("--src", required=True)
    p.add_argument("--dest", required=True)
    p.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    return {"plan": cmd_plan, "gamelists": cmd_gamelists, "render": cmd_render, "overrides": cmd_overrides}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
