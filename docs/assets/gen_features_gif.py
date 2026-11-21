#!/usr/bin/env python3
"""The README features GIF (#264): a short looping tour, one feature per scene, a big title in the brand fonts.

    ~/.venvs/badges/bin/python docs/assets/gen_features_gif.py [--out FILE]      build features.gif from the sources
    ~/.venvs/badges/bin/python docs/assets/gen_features_gif.py capture           re-render the sources (heavy, below)

Two steps, so the GIF is byte-reproducible without docker or a browser:
  capture  renders the REAL screens into docs/assets/features/*.png and re-renders the README shots
           (docs/assets/shot-{carousel,bootmenu,settings}.png): the ES-DE carousel and a list (skills/local-preview,
           the `make preview` renderer: the STARTER carousel a new card shows, systems only, no curated lists), the boot menu, SETTINGS and TROPHIES apps (skills/settings --png),
           and the Fightcade on-screen keyboard on the test login page of ansible/roles/fightcade/tests/test_osk.py
           (a page we wrote, never Fightcade's own page, no account). Needs Colima + your ES-DE AppImage (see
           skills/local-preview/SKILL.md), Chrome, cairo; ~2 min. In the private repo it renders an edition export
           (tools/edition_export.py, report-only, into the cache) so the screens carry the community names.
  build    (the default) lays each source on the theme's carousel backdrop (_inc/art/backdrops/default.png) with the
           scene title, quantizes every frame to ONE palette that holds the brand colours exactly (gold and violet
           stay clean, no dither), and writes a looping GIF. Pillow only; same inputs, same bytes
           (docs/tests/test_features_gif.py rebuilds it and compares).

A new backdrop (#263) means: run `capture`, then `build`, commit the sources, the shots and the GIF.
No game footage and no in-game screenshots: games appear only as the theme lists them (fixture names), the trophy
page uses neutral made-up titles, and the CRT scene is an illustration on a menu frame (scanlines + glow in Pillow,
not the crt-pi shader's output); the SELECT+START card is drawn in the panel-badge style. Colours and fonts come from brand/tokens.json (brand/BRAND.md rule 1).
"""
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "brand"))
import brand  # noqa: E402

SRC = os.path.join(HERE, "features")          # committed sources (capture writes, build reads)
OUT = os.path.join(HERE, "features.gif")
W, H = 960, 720                               # canvas (4:3, like the cabinet's safe area)
BOX = (120, 160, 720, 540)                    # x, y, w, h of the screen shot on the canvas
SCENE_MS = 2500                               # each scene's time on screen, split over its frames
MAX_BYTES = 3 * 1000 * 1000                   # the README budget (#264)

# One scene per feature. `alt` is the plain-words phrase the README alt text must contain (docs test). Each frame is
# (layout, sources): "full" = one 720x540 source in the box; "crt" = a native crop shown plain over CRT-treated;
# "combo" = a 720x540 source dimmed, with the exit combo drawn as panel badges (brand/BRAND.md rule 7) on a card.
# The combo is drawn, not cropped: the theme's hotkey strip (skills/badges/gen_help.py) reads START + SELECT, and the
# README and docs name it SELECT+START (coordinator, 2026-11-19). The base is the carousel, which shows no strip.
SCENES = [
    dict(title="THE CAROUSEL", sub="Every system you add games to, one turn of the stick",
         alt="the game menu carousel, one system per turn of the stick",
         frames=[("full", ["carousel.png"]), ("full", ["carousel-next.png"])]),
    dict(title="BOOT MENU", sub="Choose ARCADE or FIGHTCADE when the cabinet starts",
         alt="the boot menu with ARCADE and FIGHTCADE",
         frames=[("full", ["boot-arcade.png"]), ("full", ["boot-fightcade.png"])]),
    dict(title="ONLINE WITH FIGHTCADE", sub="On-screen keyboard and chat macros, all on the stick",
         alt="online play with Fightcade: the lobby's on-screen keyboard and chat macros",
         frames=[("full", ["fightcade-keyboard.png"]), ("full", ["fightcade-macros.png"])]),
    dict(title="SETTINGS", sub="Wi-Fi, cabinet name, RetroAchievements, time zone",
         alt="SETTINGS for Wi-Fi, the cabinet name and RetroAchievements",
         frames=[("full", ["settings.png"]), ("full", ["settings-cheevos.png"])]),
    dict(title="TROPHIES", sub="Your RetroAchievements at a glance",
         alt="trophies from RetroAchievements",
         frames=[("full", ["trophies.png"])]),
    dict(title="CRT LOOK", sub="Scanlines on the RetroArch systems (illustration)",
         alt="the CRT scanline look",
         frames=[("crt", ["crt-crop.png"])]),
    dict(title="SELECT+START", sub="One combo leaves any game, hold 2 s in Fightcade",
         alt="one combo to exit a game: SELECT+START",
         frames=[("combo", ["carousel.png"])]),
]


def frame_count(scenes=SCENES):
    return sum(len(s["frames"]) for s in scenes)


def frame_durations(scenes=SCENES):
    """Per frame, in ms: each scene's SCENE_MS split evenly (GIF delays are in 10 ms steps)."""
    out = []
    for s in scenes:
        n = len(s["frames"])
        out += [SCENE_MS // n] * n
    return out


def readme_alt(scenes=SCENES):
    """The README alt text: the full feature list in plain words."""
    return "Features tour: " + "; ".join(s["alt"] for s in scenes) + "."


def sources(scenes=SCENES):
    return sorted({f for s in scenes for _k, fs in s["frames"] for f in fs})


# ---------------------------------------------------------------- GIF reader (stdlib, for the tests)
def gif_info(path):
    """-> dict(width, height, frames, durations [ms], loop [None = no loop block]). Raises ValueError on a bad file."""
    with open(path, "rb") as f:
        d = f.read()
    if d[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError("not a GIF")
    w, h, packed = struct.unpack("<HHB", d[6:11])
    i = 13 + (3 * 2 ** ((packed & 7) + 1) if packed & 0x80 else 0)
    frames, durs, loop, delay = 0, [], None, 0

    def skip_blocks(j):
        while d[j]:
            j += d[j] + 1
        return j + 1

    while i < len(d):
        b = d[i]
        if b == 0x3B:
            break
        if b == 0x21:
            label = d[i + 1]
            if label == 0xF9:
                delay = struct.unpack("<H", d[i + 4:i + 6])[0] * 10
            elif label == 0xFF and d[i + 3:i + 14] == b"NETSCAPE2.0":
                loop = struct.unpack("<H", d[i + 16:i + 18])[0]
            i = skip_blocks(i + 2)
        elif b == 0x2C:
            p = d[i + 9]
            i += 10 + (3 * 2 ** ((p & 7) + 1) if p & 0x80 else 0)
            i = skip_blocks(i + 1)
            frames += 1
            durs.append(delay)
            delay = 0
        else:
            raise ValueError("bad block 0x%02x at %d" % (b, i))
    return dict(width=w, height=h, frames=frames, durations=durs, loop=loop)


# ---------------------------------------------------------------- build (Pillow)
def theme_dir(b):
    return os.path.join(REPO, os.path.dirname(os.path.dirname(b["fonts"]["dir"])))


def backdrop_path(b):
    return os.path.join(theme_dir(b), "_inc", "art", "backdrops", "default.png")


def _cover(img, w, h):
    from PIL import Image
    s = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


def _fit_font(draw, text, path, size, width):
    from PIL import ImageFont
    while True:
        f = ImageFont.truetype(path, size)
        if draw.textlength(text, font=f) <= width or size <= 20:
            return f
        size -= 2


def _crt(img):
    """An illustration of the CRT look: soft glow, then every third line darkened (no shader output)."""
    from PIL import Image, ImageChops, ImageFilter
    glow = img.filter(ImageFilter.GaussianBlur(3))
    out = ImageChops.screen(img, Image.blend(Image.new("RGB", img.size), glow, 0.45))
    lines = Image.new("L", img.size, 255)
    px = lines.load()
    for y in range(2, img.height, 3):
        for x in range(img.width):
            px[x, y] = 90
    return Image.composite(out, Image.new("RGB", img.size), lines)


def _pill(d, b, xy, text, font, fill, ink):
    x, y = xy
    tw = int(d.textlength(text, font=font))
    h = font.size + 12
    d.rounded_rectangle([x, y, x + tw + 24, y + h], radius=h // 2, fill=b.rgb(fill))
    d.text((x + 12, y + h // 2), text, font=font, fill=b.rgb(ink), anchor="lm")


def _shot(b, layout, imgs):
    """The 720x540 picture for one frame."""
    from PIL import Image, ImageDraw, ImageFont
    bw, bh = BOX[2], BOX[3]
    if layout == "full":
        return imgs[0].convert("RGB").resize((bw, bh), Image.LANCZOS) if imgs[0].size != (bw, bh) else imgs[0].convert("RGB")
    lab = ImageFont.truetype(b.font("ui_bold"), 22)
    if layout == "crt":
        crop = imgs[0].convert("RGB")
        half = crop.resize((bw, bh // 2), Image.LANCZOS) if crop.size != (bw, bh // 2) else crop
        pic = Image.new("RGB", (bw, bh), b.rgb("bg_deep"))
        pic.paste(half, (0, 0))
        pic.paste(_crt(half), (0, bh // 2))
        d = ImageDraw.Draw(pic)
        d.line([(0, bh // 2), (bw, bh // 2)], fill=b.rgb("gold"), width=3)
        _pill(d, b, (16, 16), "PLAIN", lab, "button", "text")
        _pill(d, b, (16, bh // 2 + 16), "CRT", lab, "gold", "on_gold")
        return pic
    if layout == "combo":
        base = imgs[0].convert("RGB").resize((bw, bh), Image.LANCZOS) if imgs[0].size != (bw, bh) else imgs[0].convert("RGB")
        pic = Image.blend(base, Image.new("RGB", (bw, bh), b.rgb("bg_deep")), 0.6)
        d = ImageDraw.Draw(pic)
        cw, ch = 600, 300
        cx, cy = (bw - cw) // 2, (bh - ch) // 2
        d.rounded_rectangle([cx, cy, cx + cw, cy + ch], radius=b["radius"]["card"], fill=b.rgb("card"),
                            outline=b.rgb("gold"), width=b["border"]["card"])
        badge = ImageFont.truetype(b.font("ui_bold"), 44)
        plus = ImageFont.truetype(b.font("display"), 44)
        word = ImageFont.truetype(b.font("ui_semi"), 30)
        parts = [("SELECT", "pill"), ("+", "plus"), ("START", "pill")]
        widths = [int(d.textlength(t, font=badge)) + 56 if k == "pill" else int(d.textlength(t, font=plus)) + 32
                  for t, k in parts]
        x, y, h = (bw - sum(widths)) // 2, cy + 56, 76
        for (t, k), w in zip(parts, widths):
            if k == "pill":
                d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=b.rgb("gold"))
                d.text((x + w // 2, y + h // 2), t, font=badge, fill=b.rgb("on_gold"), anchor="mm")
            else:
                d.text((x + w // 2, y + h // 2), t, font=plus, fill=b.rgb("gold"), anchor="mm")
            x += w
        lines = [("EXIT GAME", "text"), ("RetroArch and flycast: press both", "muted"),
                 ("Fightcade: hold 2 seconds", "muted")]
        ty = y + h + 34
        for t, c in lines:
            d.text((bw // 2, ty), t, font=word, fill=b.rgb(c), anchor="mt")
            ty += 42
        return pic
    raise ValueError("unknown layout %r" % layout)


def compose(b, scenes=SCENES, src=SRC):
    """-> list of RGB frames (W x H), in GIF order."""
    from PIL import Image, ImageDraw, ImageFont
    with Image.open(backdrop_path(b)) as bd:
        back = _cover(bd.convert("RGB"), W, H)
    back = Image.blend(back, Image.new("RGB", (W, H), b.rgb("bg_deep")), 0.72)
    kicker = ImageFont.truetype(b.font("pixel"), 14)
    sub = ImageFont.truetype(b.font("ui_semi"), 28)
    x, y, bw, bh = BOX
    frames = []
    for n, s in enumerate(scenes, 1):
        for layout, names in s["frames"]:
            imgs = []
            for name in names:
                with Image.open(os.path.join(src, name)) as im:
                    imgs.append(im.convert("RGB"))
            img = back.copy()
            d = ImageDraw.Draw(img)
            d.text((x, 24), "%02d / %02d" % (n, len(scenes)), font=kicker, fill=b.rgb("gold_muted"))
            title = _fit_font(d, s["title"], b.font("display"), 60, bw)
            d.text((x + 3, 46 + 3), s["title"], font=title, fill=b.rgb("bg_deep"))
            d.text((x, 46), s["title"], font=title, fill=b.rgb("gold"))
            d.text((x, 116), s["sub"], font=sub, fill=b.rgb("text"))
            d.rectangle([x + 8, y + 8, x + bw + 8, y + bh + 8], fill=b.rgb("bg_deep"))
            img.paste(_shot(b, layout, imgs), (x, y))
            d.rectangle([x - 3, y - 3, x + bw + 2, y + bh + 2], outline=b.rgb("gold"), width=3)
            frames.append(img)
    return frames


def palette(b, frames):
    """256 colours: every brand token exactly, then the median cut of all frames for the rest (deterministic)."""
    from PIL import Image
    fixed = []
    for name in sorted(b["colors"]):
        c = b.rgb(name)
        if c not in fixed:
            fixed.append(c)
    sheet = Image.new("RGB", (W // 2, H // 2 * len(frames)))
    for i, f in enumerate(frames):
        sheet.paste(f.resize((W // 2, H // 2), Image.BILINEAR), (0, i * (H // 2)))
    q = sheet.quantize(colors=256 - len(fixed), method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    rest = q.getpalette()[:3 * (256 - len(fixed))]
    flat = [v for c in fixed for v in c] + rest
    flat += [0] * (768 - len(flat))
    pal = Image.new("P", (1, 1))
    pal.putpalette(flat)
    return pal


def build(out=OUT, scenes=SCENES, src=SRC):
    b = brand.load()
    frames = compose(b, scenes, src)
    pal = palette(b, frames)
    pframes = [f.quantize(palette=pal, dither=0) for f in frames]
    pframes[0].save(out, save_all=True, append_images=pframes[1:], duration=frame_durations(scenes), loop=0,
                    optimize=False, disposal=1)
    return out


# ---------------------------------------------------------------- capture (real renders; heavy)
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "coin-dojo", "features-gif")  # under $HOME: Colima mounts it
CRT_CROP = (360, 440, 1080, 710)        # the centre of the carousel (title, chip, count), native pixels
SHOTS = {"shot-carousel.png": "carousel.png", "shot-bootmenu.png": "boot-arcade.png", "shot-settings.png": "settings.png"}
DEMO_GAMES = ["Demo Shooter", "Demo Platformer", "Demo Run and Gun", "Demo Racer", "Demo Puzzle", "Demo Flyer"]
OSK_HIDE_LOGO = "document.querySelector('.logo').style.visibility='hidden';"   # the harness's placeholder box
OSK_MACROS = (OSK_HIDE_LOGO + "var p=document.getElementById('pass'); p.parentNode.removeChild(p); window.__hkOsk.open();"
              "function k(c){(document.activeElement||document.body).dispatchEvent(new KeyboardEvent('keydown',"
              "{key:'Unidentified',code:c,bubbles:true,cancelable:true}));}"
              "k('F15');k('F15');k('F15');k('F15');k('F17');k('F18');")
OSK_TYPING = (OSK_HIDE_LOGO + "var u=document.getElementById('user'); u.focus(); window.__hkOsk.open();"
              " document.execCommand('insertText', false, 'player1');")


def _run(cmd, **kw):
    print("capture: %s" % " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)), flush=True)
    r = subprocess.run(cmd, **kw)
    if r.returncode != 0:
        raise SystemExit("capture: failed (%d): %s" % (r.returncode, " ".join(cmd)))
    return r


def community_tree(cache):
    """The tree whose screens we render: this repo, or (private repo, it has personal/) a fresh edition export."""
    if not os.path.isdir(os.path.join(REPO, "personal")):
        return REPO
    out = os.path.join(cache, "tree")
    force = ["--force"] if os.path.exists(os.path.join(out, ".edition-export")) else []
    if os.path.isdir(out) and not force:
        shutil.rmtree(out)
    _run([sys.executable, os.path.join(REPO, "tools", "edition_export.py"), "--out", out, "--report-only"] + force,
         stdout=subprocess.DEVNULL)
    return out


def neutral_trophies(tree, tmp):
    """The trophies test fixture with neutral titles: no game or achievement names in the README art."""
    fx = os.path.join(tree, "skills", "settings", "tests", "fixtures")
    summ = json.load(open(os.path.join(fx, "ra_summary.json")))
    aotw = json.load(open(os.path.join(fx, "ra_aotw.json")))
    names = {}
    for g in summ.get("RecentlyPlayed", []):
        names[str(g["GameID"])] = DEMO_GAMES[len(names) % len(DEMO_GAMES)]
        g["Title"] = names[str(g["GameID"])]
    n = 0
    for gid in sorted(summ.get("RecentAchievements", {}), key=int):
        for aid in sorted(summ["RecentAchievements"][gid], key=int):
            n += 1
            a = summ["RecentAchievements"][gid][aid]
            a["GameTitle"] = names.get(str(a["GameID"]), "Demo Game")
            a["Title"], a["Description"] = "Stage %d Clear" % n, "Clear stage %d" % n
    summ["RichPresenceMsg"] = "Playing %s" % DEMO_GAMES[0]
    aotw["Game"]["Title"] = names.get(str(aotw["Game"]["ID"]), "Demo Game")
    aotw["Achievement"]["Title"], aotw["Achievement"]["Description"] = "Speed Run", "Clear the first stage fast"
    paths = [os.path.join(tmp, "ra_summary.json"), os.path.join(tmp, "ra_aotw.json")]
    for p, data in zip(paths, (summ, aotw)):
        with open(p, "w") as f:
            json.dump(data, f)
    return ",".join(paths)


def capture(src=SRC):
    from PIL import Image
    os.makedirs(CACHE, exist_ok=True)
    tree = community_tree(CACHE)
    tmp = tempfile.mkdtemp(prefix="features-", dir=CACHE)
    raw = os.path.join(tmp, "raw")
    os.makedirs(raw)
    env = dict(os.environ, HOME=os.path.join(tmp, "home"), HK_FAKE_NMCLI_STATE=os.path.join(tmp, "nmcli.json"))
    os.makedirs(env["HOME"])
    tdc = os.path.join(tmp, "timedatectl")
    with open(tdc, "w") as f:
        f.write("#!/bin/sh\n[ \"$1\" = show ] && echo Europe/London && exit 0\nexit 1\n")
    os.chmod(tdc, 0o755)
    st = os.path.join(tree, "skills", "settings")
    py = "python3"
    app = lambda name, out, *a: _run([py, os.path.join(st, name), "--png", os.path.join(raw, out)] + list(a), env=env)  # noqa: E731
    themes = os.path.join(tree, "themes")
    theme_name = [t for t in sorted(os.listdir(themes)) if os.path.isfile(os.path.join(themes, t, "theme.xml"))][0]
    boot = ["--hostname", "coindojo", "--log", "", "--art-dir", os.path.join(themes, theme_name, "_inc", "art", "bootmenu")]
    app("bootmenu_app.py", "boot-arcade.png", "--countdown", "10", "--at", "6", *boot)   # the card art the theme ships
    app("bootmenu_app.py", "boot-fightcade.png", "--actions", "right", "--at", "3", "--moved", "2", *boot)
    common = ["--hostname", "coindojo", "--nmcli", os.path.join(st, "fake_nmcli.py"), "--timedatectl", tdc]
    app("settings_app.py", "settings.png", *common)
    app("settings_app.py", "settings-cheevos.png", "--actions", "down,down,down,ok", *common)
    app("trophies_app.py", "trophies.png", "--fixture", neutral_trophies(tree, tmp), "--now", "1795000000")
    # the on-screen keyboard on the test login page (test_osk.py's harness: our page, none of Fightcade's)
    sys.path.insert(0, os.path.join(tree, "ansible", "roles", "fightcade", "tests"))
    import test_osk as osk
    if not osk.chrome():
        raise SystemExit("capture: no Chrome for the keyboard render")
    for name, js in (("fightcade-keyboard.png", OSK_TYPING), ("fightcade-macros-full.png", OSK_MACROS)):
        osk.run_chrome(osk.build(test="", extra="<" + "/script><script>" + js), shot=os.path.join(raw, name))
    # ES-DE: a theme copy with the build stamp hidden (`visible` false: an empty text is an ES-DE error); the preview
    # fixture's systems with NO collections, i.e. the starter carousel a new owner sees (curated lists are their own);
    # the second shot is the carousel one turn to the right
    fx = os.path.join(tmp, "fixture")
    shutil.copytree(os.path.join(tree, "skills", "local-preview", "fixtures", "esde-preview", "gamelists"),
                    os.path.join(fx, "gamelists"))
    os.makedirs(os.path.join(fx, "collections"))
    theme = os.path.join(tmp, "theme", theme_name)
    shutil.copytree(os.path.join(themes, theme_name), theme)
    xml = open(os.path.join(theme, "theme.xml")).read()
    with open(os.path.join(theme, "theme.xml"), "w") as f:
        f.write(re.sub(r"<text>REV @[A-Z_]+@</text>", "<text>REV</text><visible>false</visible>", xml))
    _run([os.path.join(REPO, "skills", "local-preview", "preview-esde.sh"), theme, raw, "--view", "both",
          "--keys", "Right", "--fixture", fx])
    shutil.move(os.path.join(raw, "system.png"), os.path.join(raw, "carousel.png"))
    shutil.move(os.path.join(raw, "gamelist.png"), os.path.join(raw, "carousel-next.png"))
    # sources: 720x540 (half size) except the native crops
    os.makedirs(src, exist_ok=True)
    for f in sorted(os.listdir(src)):
        if f.endswith(".png"):
            os.remove(os.path.join(src, f))

    def save(img, name):
        img.convert("RGB").save(os.path.join(src, name), optimize=True)

    for n in ("carousel", "carousel-next", "boot-arcade", "boot-fightcade", "fightcade-keyboard", "settings",
              "settings-cheevos", "trophies"):
        with Image.open(os.path.join(raw, n + ".png")) as im:
            save(im.convert("RGB").resize((BOX[2], BOX[3]), Image.LANCZOS), n + ".png")
    with Image.open(os.path.join(raw, "fightcade-macros-full.png")) as im:
        save(im.crop((360, 540, 1080, 1080)), "fightcade-macros.png")
    with Image.open(os.path.join(raw, "carousel.png")) as im:
        save(im.crop(CRT_CROP).resize((BOX[2], BOX[3] // 2), Image.LANCZOS), "crt-crop.png")
    for shot, name in SHOTS.items():
        shutil.copyfile(os.path.join(src, name), os.path.join(HERE, shot))
        print("capture: wrote %s" % os.path.join(HERE, shot))
    shutil.rmtree(tmp, ignore_errors=True)
    missing = [s for s in sources() if not os.path.isfile(os.path.join(src, s))]
    if missing:
        raise SystemExit("capture: sources missing: %s" % missing)
    print("capture: %d sources in %s" % (len(sources()), src))


def main(argv):
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("gen_features_gif: Pillow missing: run with ~/.venvs/badges/bin/python (make -C skills/badges venv)",
              file=sys.stderr)
        return 2
    if argv[:1] == ["capture"]:
        capture()
        argv = argv[1:]
    out = argv[argv.index("--out") + 1] if "--out" in argv else OUT
    missing = [s for s in sources() if not os.path.isfile(os.path.join(SRC, s))]
    if missing:
        print("gen_features_gif: sources missing (run `capture` first): %s" % missing, file=sys.stderr)
        return 2
    build(out)
    info = gif_info(out)
    print("wrote %s (%d frames, %dx%d, %d bytes)" % (out, info["frames"], info["width"], info["height"],
                                                    os.path.getsize(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
