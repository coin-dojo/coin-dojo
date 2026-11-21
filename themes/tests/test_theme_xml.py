#!/usr/bin/env python3
"""Well-formedness guard for the ES-DE theme packs under themes/.

Why this exists: ES-DE does NOT error out on a malformed theme. If any theme
XML fails to parse it silently falls back to its built-in default theme, so a
broken edit looks like "the theme disappeared" with no message. This test turns
that silent failure into a red build (the maintainer, 2026-11-13, after a stray regex edit
left a <text> block with a swallowed close tag and the cabinet dropped to the
default carousel).

Pure stdlib (no pip installs). pytest-compatible, or run directly:

    python3 themes/tests/test_theme_xml.py
    pytest themes/tests/

Checks:
  1. Every *.xml under themes/ is well-formed (parses with ElementTree).
  2. Every LITERAL asset path (<path>/<staticImage>/<defaultImage>/<fontPath>
     that is not a ${variable}) points to a file that exists.
  3. The parser actually rejects malformed XML (deliberately-broken control),
     using the exact shape of the bug that caused the fallback: a <text> element
     whose close tag was swallowed, leaving mismatched tags.

The deliberately-BROKEN case (must FAIL): see tests/README.md — break any theme
XML (e.g. delete a closing tag in coin-dojo/theme.xml) and
`test_theme_xml_is_wellformed` MUST go red. Restore it to go green.
"""
import os
import re
import sys
import xml.etree.ElementTree as ET

THEMES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# tags whose text content is a file path ES-DE loads
PATH_TAGS = ("path", "staticImage", "defaultImage", "fontPath", "customButtonIcon")

# help-bar buttons the cabinet panel has (physical names via gen_help.py): a/b/x/y, start/back (#57)
REQUIRED_HELP_BUTTONS = ("button_a_XBOX", "button_b_XBOX", "button_x_XBOX", "button_y_XBOX",
                         "button_start_XBOX", "button_back_XBOX")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tests", "lib"))
from hktest import check as _check, main, skip  # noqa: E402  (shared helper; SKIPs counted in RESULT, #152)


def _xml_files():
    out = []
    for root, _dirs, files in os.walk(THEMES):
        if os.sep + "tests" in root + os.sep:   # don't scan this test dir
            continue
        for fn in files:
            if fn.endswith(".xml"):
                out.append(os.path.join(root, fn))
    return sorted(out)


def test_themes_present():
    xmls = _xml_files()
    _check(len(xmls) > 0, "found theme XML to validate (%d files)" % len(xmls))


def test_theme_xml_is_wellformed():
    """The core guard: a malformed theme makes ES-DE fall back silently."""
    for path in _xml_files():
        rel = os.path.relpath(path, THEMES)
        try:
            ET.parse(path)
            _check(True, "well-formed XML: %s" % rel)
        except ET.ParseError as e:
            _check(False, "well-formed XML: %s (%s)" % (rel, e))


def test_literal_asset_paths_exist():
    """A missing referenced asset is another silent visual failure."""
    for path in _xml_files():
        rel = os.path.relpath(path, THEMES)
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            continue  # reported by the well-formedness test
        pack_root = os.path.dirname(path)
        for el in tree.iter():
            if el.tag not in PATH_TAGS:
                continue
            ref = (el.text or "").strip()
            if not ref or "${" in ref:      # skip theme variables
                continue
            target = os.path.normpath(os.path.join(pack_root, ref))
            _check(os.path.exists(target),
                   "asset exists for <%s>%s</%s> in %s" % (el.tag, ref, el.tag, rel))


def images_without_default(theme_xml):
    """#150: a per-system image (<path> with ${system.theme}) needs a <default> next to it: per-system art is
    optional, ES-DE shows the default when the file is missing. -> list of problems (empty = ok)."""
    out = []
    for el in ET.parse(theme_xml).iter():
        path = el.find("path")
        if path is not None and "${system.theme}" in (path.text or "") and el.find("default") is None:
            out.append("<%s name=%s> %s has no <default>" % (el.tag, el.get("name"), path.text.strip()))
    return out


def test_per_system_images_have_a_default():
    root = os.path.join(THEMES, "coin-dojo")
    xml = os.path.join(root, "theme.xml")
    probs = images_without_default(xml)
    _check(not probs, "every ${system.theme} image has a <default> %s" % (probs or ""))
    t = ET.parse(xml)
    paths = {(e.find("path").text or "").strip() for e in t.iter() if e.find("path") is not None
             and "${system.theme}" in (e.find("path").text or "") and e.find("default") is not None}
    for e in t.iter():
        d = e.find("default")
        if d is not None and e.find("path") is not None and "${system.theme}" in (e.find("path").text or ""):
            _check(os.path.isfile(os.path.join(root, d.text.strip())), "default exists: %s" % d.text.strip())
    _check(len(paths) >= 2, "found the per-system image paths (%d)" % len(paths))
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "theme.xml")
        open(f, "w").write("<theme><view><image name=\"x\"><path>./a/${system.theme}.png</path></image></view></theme>")
        _check(images_without_default(f) != [], "BROKEN CASE: a ${system.theme} image without <default> is flagged")


def per_system_problems(theme_xml_text, per_system_dir, systems_dir):
    """The per-system override include (#34): last in theme.xml (outside the variant, so it wins), and every
    _inc/per-system/<name>.xml names a carousel entry that has a badge. -> list of problems."""
    p = []
    inc = "<include>./_inc/per-system/${system.theme}.xml</include>"
    body = theme_xml_text.rsplit("</theme>", 1)[0]
    if inc not in body or body.rfind(inc) < body.rfind("</variant>"):
        p.append("theme.xml: the per-system include is missing or not after the last </variant>")
    for f in sorted(os.listdir(per_system_dir)) if os.path.isdir(per_system_dir) else []:
        if f.endswith(".xml") and not os.path.isfile(os.path.join(systems_dir, f[:-4] + ".png")):
            p.append("_inc/per-system/%s: no carousel entry named %s" % (f, f[:-4]))
    return p


def test_per_system_overrides():
    root = os.path.join(THEMES, "coin-dojo")
    text = open(os.path.join(root, "theme.xml"), encoding="utf-8").read()
    probs = per_system_problems(text, os.path.join(root, "_inc", "per-system"), os.path.join(root, "_inc", "systems"))
    _check(not probs, "per-system include last, every override names a carousel entry %s" % (probs or ""))
    s = ET.parse(os.path.join(root, "_inc", "per-system", "settings.xml")).getroot()
    gc = s.find("./view[@name='system']/text[@name='gameCounter']/visible")
    _check(gc is not None and gc.text.strip() == "false", "SETTINGS: no game counter pill (#34)")
    bad = text.replace("<include>./_inc/per-system/${system.theme}.xml</include>", "")
    _check(per_system_problems(bad, "/nonexistent", "/nonexistent") != [], "BROKEN CASE: include removed is flagged")
    inside = text.replace("    </variant>\n", "    <include>./_inc/per-system/${system.theme}.xml</include>\n    </variant>\n")
    inside = inside.replace("    <include>./_inc/per-system/${system.theme}.xml</include>\n</theme>", "</theme>")
    _check(per_system_problems(inside, "/nonexistent", "/nonexistent") != [],
           "BROKEN CASE: include inside the variant (ES-DE 3.5 ignored it there, VM 2026-11-15) is flagged")


# #225: what hides each element type in ES-DE 3.5.0 (ThemeData.cpp sElementMap): visible where it exists, opacity
# for clock/systemstatus/helpsystem, fully transparent colours for textlist (it has neither)
TEXTLIST_COLOURS = ("primaryColor", "secondaryColor", "selectedColor", "selectedSecondaryColor", "selectorColor",
                    "selectorColorEnd", "selectedBackgroundColor", "selectedSecondaryBackgroundColor")


def _names(el):
    return [n.strip() for n in el.get("name", "").split(",") if n.strip()]


def gamelist_elements(xml_files):
    """{(tag, name)} of every element in a view named gamelist (alone or in a list) in these files."""
    out = set()
    for f in xml_files:
        for v in ET.parse(f).getroot().iter("view"):
            if "gamelist" in [x.strip() for x in v.get("name", "").split(",")]:
                for el in v:
                    out.update((el.tag, n) for n in _names(el))
    return out


def _hidden(el):
    if el.tag == "textlist":
        return all((el.findtext(c) or "").strip().upper() == "00000000" for c in TEXTLIST_COLOURS)
    if el.tag in ("clock", "systemstatus", "helpsystem"):
        return (el.findtext("opacity") or "").strip() in ("0", "0.0")
    return (el.findtext("visible") or "").strip() == "false"


def settings_gamelist_problems(override_root, theme_elements):
    """#225: the SETTINGS gamelist (ES-DE's frame while the app runs and until the Back) draws only baseFill. Every
    other gamelist element of the theme must be hidden by the override; baseFill must not be. -> list of problems."""
    hid = set()
    for v in override_root.iter("view"):
        if v.get("name", "").strip() != "gamelist":
            continue
        for el in v:
            for n in _names(el):
                if _hidden(el):
                    hid.add((el.tag, n))
    p = ["%s %s still drawn on the SETTINGS gamelist" % k for k in sorted(theme_elements - hid - {("image", "baseFill")})]
    if ("image", "baseFill") in hid:
        p.append("baseFill hidden: the SETTINGS gamelist must keep the plain background")
    return p


def theme_xml_files(root):
    files = [os.path.join(d, f) for d, _s, fs in os.walk(root) for f in fs if f.endswith(".xml")]
    personal = os.path.normpath(os.path.join(THEMES, "..", "personal", "themes", os.path.basename(root)))
    # a personal overlay's art animations (art/backdrops/*.xml, e.g. an old-named eye glow) are never included by the
    # theme (theme.xml includes only ${eyeGlow}.xml, shipped in the theme itself since #263): not part of the view
    files += [os.path.join(d, f) for d, _s, fs in os.walk(personal) for f in fs
              if f.endswith(".xml") and os.sep + os.path.join("art", "backdrops") not in d + os.sep]
    return [f for f in files if os.sep + "per-system" + os.sep not in f]


def test_settings_gamelist_is_plain_background():
    """#225: leaving SETTINGS, ES-DE's last frame (this gamelist) is the same plain bg the app fades to."""
    root = os.path.join(THEMES, "coin-dojo")
    els = gamelist_elements(theme_xml_files(root))
    s = ET.parse(os.path.join(root, "_inc", "per-system", "settings.xml")).getroot()
    _check(len(els) > 20 and ("textlist", "gamelistTextlist") in els, "found the theme's gamelist elements (%d)" % len(els))
    probs = settings_gamelist_problems(s, els)
    _check(not probs, "SETTINGS gamelist: only baseFill is drawn %s" % (probs or ""))
    colors = open(os.path.join(root, "colors.xml"), encoding="utf-8").read()
    base = re.search(r"<baseColor>([0-9A-Fa-f]{6})", colors)
    import json
    tokens = json.load(open(os.path.join(THEMES, "..", "brand", "tokens.json"), encoding="utf-8"))
    _check(base is not None and base.group(1).upper() == tokens["colors"]["bg"]["hex"].upper(),
           "baseFill colour (colors.xml baseColor) == brand bg, the colour the SETTINGS app fades to")
    # broken cases: one element left visible, a textlist colour left opaque, baseFill hidden
    for label, mutate in (
            ("the game list left visible", lambda r: [t.find("selectedColor").__setattr__("text", "FFFFFFFF")
                                                      for t in r.iter("textlist")]),
            ("the description left visible", lambda r: [e.find("visible").__setattr__("text", "true")
                                                        for e in r.iter("text") if "description" in _names(e)]),
            ("the help bar left visible", lambda r: [e.find("opacity").__setattr__("text", "1")
                                                     for e in r.iter("helpsystem")]),
            ("baseFill hidden too", lambda r: r.find("./view[@name='gamelist']").append(
                ET.fromstring("<image name=\"baseFill\"><visible>false</visible></image>")))):
        bad = ET.fromstring(ET.tostring(s))
        mutate(bad)
        _check(settings_gamelist_problems(bad, els) != [], "BROKEN CASE: %s is flagged" % label)
    _check(settings_gamelist_problems(s, els | {("image", "newArt")}) != [],
           "BROKEN CASE: a new gamelist element the override does not hide is flagged")


def per_system_path_problems(per_system_dir):
    """Paths inside _inc/per-system/*.xml resolve from that folder (#218: ${uiBold} = ./_inc/fonts/... pointed into
    _inc/per-system/_inc and ES-DE fell back to its own font). A file or font path there must not use a theme
    variable or a ./_inc path. -> list of problems."""
    out = []
    for f in sorted(os.listdir(per_system_dir)) if os.path.isdir(per_system_dir) else []:
        if not f.endswith(".xml"):
            continue
        for e in ET.parse(os.path.join(per_system_dir, f)).iter():
            if e.tag in ("path", "fontPath", "default") and e.text:
                t = e.text.strip()
                if t.startswith("${") or t.startswith("./_inc/"):
                    out.append("%s: <%s>%s</%s> resolves from _inc/per-system/" % (f, e.tag, t, e.tag))
    return out


def test_quit_entry_override():
    """#218: QUIT TO MAIN MENU shows a pill that says where it goes instead of the "1 GAME" counter."""
    root = os.path.join(THEMES, "coin-dojo")
    per = os.path.join(root, "_inc", "per-system")
    q = ET.parse(os.path.join(per, "quit.xml")).getroot()
    gc = q.find("./view[@name='system']/text[@name='gameCounter']/visible")
    _check(gc is not None and gc.text.strip() == "false", "QUIT: no game counter pill")
    hint = q.find("./view[@name='system']/text[@name='quitHint']")
    _check(hint is not None and "boot menu" in (hint.findtext("text") or "").lower(), "QUIT: the pill says BACK TO THE BOOT MENU")
    font = (hint.findtext("fontPath") or "").strip() if hint is not None else ""
    _check(font.startswith("./") and os.path.isfile(os.path.normpath(os.path.join(per, font))),
           "QUIT pill font resolves from _inc/per-system/ (%s)" % font)
    probs = per_system_path_problems(per)
    _check(not probs, "per-system paths resolve from their folder %s" % (probs or ""))
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, "quit.xml"), "w").write(
            "<theme><view name=\"system\"><text name=\"x\"><fontPath>${uiBold}</fontPath></text></view></theme>")
        _check(per_system_path_problems(tmp) != [], "BROKEN CASE: a ${uiBold} font path in a per-system file is flagged")


def test_parser_rejects_malformed_xml():
    """Deliberately-broken control: the exact failure shape must be rejected.

    A <text name="tagline"> whose own close tag was swallowed (leaving only the
    nested <text>...</text>) produces a mismatched-tag error -- precisely the
    bug that dropped the cabinet to the default theme.
    """
    broken = (
        '<theme><view name="system">'
        '<text name="tagline"><text>INSTANT HELL CABINET</text>'
        '<color>#36D6E7</color>'
        '</view></theme>'            # <text name="tagline"> never closed
    )
    try:
        ET.fromstring(broken)
        _check(False, "parser rejects malformed theme XML (broken-case control)")
    except ET.ParseError:
        _check(True, "parser rejects malformed theme XML (broken-case control)")


def missing_companions(theme_dir):
    """Carousel entries (_inc/systems/<n>.png) need a category chip (_inc/chips/<n>.png), the
    chip + icon defaults must exist, and a pictogram under _inc/icons/<n>.png must have a badge.
    Returns a list of problems (empty = ok). ES-DE does not warn about a missing chip: the system
    view would just show the SYSTEM default for a DECADE entry."""
    inc = os.path.join(theme_dir, "_inc")
    badges = {f[:-4] for f in os.listdir(os.path.join(inc, "systems")) if f.endswith(".png")}
    chips = {f[:-4] for f in os.listdir(os.path.join(inc, "chips"))} if os.path.isdir(
        os.path.join(inc, "chips")) else set()
    icons = {f[:-4] for f in os.listdir(os.path.join(inc, "icons"))} if os.path.isdir(
        os.path.join(inc, "icons")) else set()
    out = ["no chip for " + n for n in sorted(badges - chips)]
    out += ["no chips/default.png"] * ("default" not in chips)
    out += ["no icons/default.png"] * ("default" not in icons)
    out += ["pictogram without badge: " + n for n in sorted(icons - badges - {"default"})]
    out += ["stale badge: recommended (renamed must-play)"] * ("recommended" in badges)
    return out


def test_theme_has_chip_for_every_badge():
    problems = missing_companions(os.path.join(THEMES, "coin-dojo"))
    _check(not problems, "every carousel badge has a chip, defaults exist (%s)" % "; ".join(problems))


def test_missing_chip_is_detected():
    """Deliberately-broken control: a copy without one chip MUST be reported."""
    import shutil
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        t = os.path.join(d, "t")
        shutil.copytree(os.path.join(THEMES, "coin-dojo", "_inc"), os.path.join(t, "_inc"))
        os.remove(os.path.join(t, "_inc", "chips", "fighters.png"))
        _check("no chip for fighters" in missing_companions(t),
               "broken-case control: missing chip is reported")


AUTO_NAMES = {"auto-allgames", "auto-favorites", "auto-lastplayed"}
AUTO_FIX = {"all": "auto-allgames", "favorites": "auto-favorites", "recent": "auto-lastplayed",
            "lastplayed": "auto-lastplayed", "allgames": "auto-allgames"}


def bad_auto_names(theme_dir):
    """ES-DE looks the three automatic collections up by theme name auto-allgames / auto-favorites /
    auto-lastplayed (their es_settings keys are all / favorites / recent). A badge or chip under any
    other name is never loaded. Returns problems (empty = ok)."""
    inc = os.path.join(theme_dir, "_inc")
    out = []
    for sub in ("systems", "chips"):
        for f in sorted(os.listdir(os.path.join(inc, sub))):
            n = f[:-4]
            if f.endswith(".png") and n.startswith("auto-") and n not in AUTO_NAMES:
                out.append("%s/%s: not an ES-DE auto name (use one of %s)" % (sub, f, sorted(AUTO_NAMES)))
            elif f.endswith(".png") and n in AUTO_FIX:
                out.append("%s/%s: ES-DE looks it up as %s.png" % (sub, f, AUTO_FIX[n]))
    return out


def test_auto_collection_names():
    problems = bad_auto_names(os.path.join(THEMES, "coin-dojo"))
    _check(not problems, "auto-collection badges/chips use ES-DE's auto-* names (%s)" % "; ".join(problems))


def test_wrong_auto_name_is_detected():
    """Deliberately-broken control: favorites.png / lastplayed.png must fail and name the auto- file."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        for sub in ("systems", "chips"):
            os.makedirs(os.path.join(d, "_inc", sub))
        for bad in ("favorites", "lastplayed", "auto-recent"):
            open(os.path.join(d, "_inc", "systems", bad + ".png"), "wb").close()
        msgs = "; ".join(bad_auto_names(d))
        _check("auto-favorites.png" in msgs and "auto-lastplayed.png" in msgs and "auto-recent" in msgs,
               "wrong auto badge names are rejected with a pointer (broken-case control): " + msgs)


def missing_help_icons(theme_xml):
    """Required panel buttons without a <customButtonIcon> in theme.xml (empty = ok). A missing one
    silently falls back to the Xbox glyph on the cabinet."""
    have = {el.get("button") for el in ET.parse(theme_xml).iter("customButtonIcon")}
    return [b for b in REQUIRED_HELP_BUTTONS if b not in have]


def test_help_bar_uses_panel_icons():
    problems = missing_help_icons(os.path.join(THEMES, "coin-dojo", "theme.xml"))
    _check(not problems, "help bar has panel icons for a/b/x/y/start/back (%s)" % ", ".join(problems))


def test_missing_help_icon_is_detected():
    """Deliberately-broken control: a theme without the START icon MUST be reported."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "theme.xml")
        open(f, "w").write("<theme><customButtonIcon button=\"button_a_XBOX\">x.png</customButtonIcon></theme>")
        miss = missing_help_icons(f)
        _check("button_start_XBOX" in miss and "button_a_XBOX" not in miss,
               "broken-case control: missing help icon is reported")


# The truth: SDL_GAMECONTROLLERCONFIG in the cabinet's ~/.bash_profile (ES-DE ignores es_controllers.txt):
# a:b3 b:b2 x:b0 y:b4 leftshoulder:b5 rightshoulder:b1; panel by joystick index MP=0 HK=1 MK=2 LK=3 LP=4 HP=5.
EXPECTED_HELP = {"button_a": "LK", "button_b": "MK", "button_x": "MP", "button_y": "LP",
                 "button_l": "HP", "button_r": "HK"}


def help_mapping_problems(round_labels, pill_labels):
    """round_labels/pill_labels: gen_help.ROUND / PILL. Returns problems (empty = ok)."""
    out = []
    for base, label in EXPECTED_HELP.items():
        keys = [base] if base in ("button_l", "button_r") else [base + "_XBOX", base + "_XBOX360"]
        for k in keys:
            if round_labels.get(k) != label:
                out.append("%s should be %s, is %s" % (k, label, round_labels.get(k)))
    if pill_labels.get("button_lr") != "HP+HK":
        out.append("button_lr should be HP+HK, is %s" % pill_labels.get("button_lr"))
    return out


def _gen_help():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "skills", "badges"))
    import gen_help
    return gen_help


def test_help_icon_mapping_matches_cabinet():
    g = _gen_help()
    problems = help_mapping_problems(g.ROUND, g.PILL)
    _check(not problems, "help icons a=LK b=MK x=MP y=LP l=HP r=HK lr=HP+HK (%s)" % "; ".join(problems))
    have = {el.get("button"): el.text for el in ET.parse(
        os.path.join(THEMES, "coin-dojo", "theme.xml")).iter("customButtonIcon")}
    bad = [b for b in list(g.ROUND) + list(g.PILL)
           if have.get(b) != "./_inc/help/%s.png" % b
           or not os.path.isfile(os.path.join(THEMES, "coin-dojo", "_inc", "help", b + ".png"))]
    _check(not bad, "every mapped help button is wired to its own file, XBOX and XBOX360 (%s)" % ", ".join(bad))


def test_swapped_help_mapping_is_detected():
    """Deliberately-broken control: the old (wrong) table a=LP b=MP x=HP y=LK MUST fail."""
    old = {"button_a_XBOX": "LP", "button_b_XBOX": "MP", "button_x_XBOX": "HP", "button_y_XBOX": "LK",
           "button_l": "MK", "button_r": "HK"}
    miss = help_mapping_problems(old, {"button_lr": "MK+HK"})
    _check(any("button_a_XBOX should be LK" in m for m in miss) and len(miss) >= 6,
           "broken-case control: swapped help mapping is reported (%d problems)" % len(miss))


def test_hotkey_strip_is_an_image():
    hint = [e for e in ET.parse(os.path.join(THEMES, "coin-dojo", "theme.xml")).iter()
            if e.get("name") == "controlsHint"]
    _check(hint and all(e.tag == "image" for e in hint) and os.path.isfile(
        os.path.join(THEMES, "coin-dojo", "_inc", "help", "hotkeys.png")),
        "hotkey hint is the hotkeys.png badge strip")


def trophies_hint_problems(text):
    """#180: the LP Trophies shortcut sits in the main menu's help bar row: one marked block in the system view only
    (esde_theme strips it whole, #181) with the help bar's LP icon + a TROPHIES label at the row's right end, and the
    system view's help bar moved left of it (no overlap). -> problems (empty = ok)."""
    out = []
    m = re.search(r"<!-- trophies-hint:begin.*?-->(.*?)<!-- trophies-hint:end -->", text, re.S)
    if not m or text.count("trophies-hint:begin") != 1:
        return ["exactly one trophies-hint:begin ... end block"]
    sysview = re.search(r'<view name="system">(.*?)</view>', text, re.S)
    if not sysview or m.group(0) not in sysview.group(1):
        out.append("the block must sit in <view name=\"system\">")
    blk = ET.fromstring("<x>" + m.group(1) + "</x>")
    help_pos = blk.find("helpsystem/pos")
    icon = blk.find("image[@name='trophiesHintIcon']")
    label = blk.find("text[@name='trophiesHintLabel']")
    if help_pos is None or icon is None or label is None:
        return out + ["block needs the help bar pos, trophiesHintIcon and trophiesHintLabel"]
    hx, hy = map(float, help_pos.text.split())
    ix, iy = map(float, icon.findtext("pos").split())
    lx, ly = map(float, label.findtext("pos").split())
    if not (hy == iy == ly == 0.945):
        out.append("not on the help bar row (y 0.945)")
    if not hx < ix < lx:
        out.append("help bar must end left of the icon, the icon left of the label")
    if icon.findtext("path") != "./_inc/help/button_y_XBOX.png" or icon.findtext("color") != "${helpIcon}":
        out.append("the icon must be the help bar's LP icon tinted ${helpIcon}")
    if label.findtext("color") != "${helpColor}" or label.findtext("fontSize") != "0.022":
        out.append("the label must use the help bar's colour and size")
    if lx + float(label.findtext("size").split()[0]) > 0.975:
        out.append("the label runs past the right margin")
    return out


def test_trophies_hint_in_the_help_row():
    text = open(os.path.join(THEMES, "coin-dojo", "theme.xml")).read()
    bad = trophies_hint_problems(text)
    _check(not bad, "LP Trophies hint in the main menu's help bar row (%s)" % "; ".join(bad))
    _check(trophies_hint_problems(text.replace("<pos>0.866 0.945</pos>", "<pos>0.97 0.945</pos>")),
           "BROKEN CASE: a help bar left at 0.97 (over the hint) is flagged")
    _check(trophies_hint_problems(text.replace("trophies-hint:end", "trophies-hint:stop")),
           "BROKEN CASE: a block without its end marker is flagged")
    _check(not os.path.exists(os.path.join(THEMES, "coin-dojo", "_inc", "help", "trophies-hint.png")),
           "the old trophies-hint.png strip is gone")


def legend_leftovers(root):
    """#172 (the maintainer 2026-11-17: "i just want the buttons for light medium heavy gone"): no button-colour legend."""
    names = {"dotLight", "dotMedium", "dotHeavy", "legendLight", "legendMedium", "legendHeavy"}
    return sorted(e.get("name") for e in root.iter() if e.get("name") in names)


def test_no_light_medium_heavy_legend():
    root = ET.parse(os.path.join(THEMES, "coin-dojo", "theme.xml")).getroot()
    left = legend_leftovers(root)
    _check(not left, "the LIGHT / MEDIUM / HEAVY legend is gone from the game lists (%s)" % left)
    for v in root.iter("view"):
        if v.get("name") == "gamelist":
            v.append(ET.fromstring('<text name="legendLight"><text>LIGHT</text></text>'))
    _check(set(legend_leftovers(root)) == {"legendLight"}, "BROKEN CASE: a legend element put back is flagged")


# Footer hierarchy (#57 rows): the help bar (last row) is primary, the hotkey strip above it is secondary.
# ES-DE draws a help icon ~0.93 x fontSize x screen height (measured on the 4:3 preview: 0.018 -> 18 px,
# 0.022 -> 21 px); the strip's badge is HK_BADGE / png height of the element's rendered height.
HELP_ICON_PER_FONT = 0.93
HIERARCHY_MIN = 1.25          # help icon must be >= 25% taller than a hotkey badge


def _png_height(path):
    with open(path, "rb") as f:
        head = f.read(24)
    return int.from_bytes(head[20:24], "big")


def footer_hierarchy(help_font, hint_h, png_h, badge_px, screen_h=1080):
    """-> (help icon px, hotkey badge px, ok)."""
    help_icon = HELP_ICON_PER_FONT * help_font * screen_h
    badge = badge_px / float(png_h) * hint_h * screen_h
    return help_icon, badge, help_icon >= HIERARCHY_MIN * badge


def _footer_values(theme_xml):
    root = ET.parse(theme_xml).getroot()
    help_el = [e for e in root.iter("helpsystem") if e.get("name") == "help"][0]
    hint = [e for e in root.iter("image") if e.get("name") == "controlsHint"][0]
    return float(help_el.findtext("fontSize")), float(hint.findtext("size").split()[1])


def test_help_bar_outranks_hotkey_strip():
    g = _gen_help()
    theme = os.path.join(THEMES, "coin-dojo")
    font, hint_h = _footer_values(os.path.join(theme, "theme.xml"))
    png_h = _png_height(os.path.join(theme, "_inc", "help", "hotkeys.png"))
    _check(png_h == g.HK_HEIGHT, "hotkeys.png is regenerated (%d px tall, gen_help says %d)" % (png_h, g.HK_HEIGHT))
    icon, badge, ok = footer_hierarchy(font, hint_h, png_h, g.HK_BADGE)
    _check(ok, "help bar icon %.1f px >= %.2f x hotkey badge %.1f px (last row is the pronounced one)"
           % (icon, HIERARCHY_MIN, badge))
    _check(abs(hint_h * 1080 - png_h / 2.0) < 0.5, "hotkey strip shown at exactly half its 2x art (%.2f px)"
           % (hint_h * 1080))
    _check(all(c < 250 for c in g.HK_GOLD[:3]) and g.HK_GOLD[:3] != g.GOLD[:3],
           "hotkey badges use the muted gold, not the help bar's full gold")


def note_row_pixels(path, badge_h):
    """Count non-transparent pixels below the badge row of a hotkey strip (the old Dreamcast note lived there)."""
    from PIL import Image
    im = Image.open(path).convert("RGBA")
    a = im.getchannel("A").crop((0, badge_h + 1, im.width, im.height))
    return sum(1 for v in a.getdata() if v > 0)


def test_hotkey_strip_has_no_note_line():
    g = _gen_help()
    _check(not hasattr(g, "DC_NOTE"), "gen_help has no Dreamcast note text (the maintainer 2026-11-15)")
    try:
        import PIL  # noqa: F401
    except ImportError:
        skip("hotkey strip pixel check: no Pillow")
        return
    path = os.path.join(THEMES, "coin-dojo", "_inc", "help", "hotkeys.png")
    _check(note_row_pixels(path, g.HK_BADGE) == 0, "hotkeys.png: nothing drawn below the badge row")
    from PIL import Image, ImageDraw
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        im = Image.open(path).convert("RGBA")
        ImageDraw.Draw(im).text((10, g.HK_BADGE + 10), "NOTE", fill=(255, 255, 255, 255))
        im.save(os.path.join(d, "bad.png"))
        _check(note_row_pixels(os.path.join(d, "bad.png"), g.HK_BADGE) > 0,
               "BROKEN CASE: a strip with a note line under the badges is flagged")


def test_BROKEN_old_footer_hierarchy_is_rejected():
    """Deliberately-broken control: the pre-fix footer (help 0.018, strip 0.040 of a 96 px PNG with 56 px badges:
    18 px help icons under 25 px hotkey badges) MUST fail, and so must equal sizes."""
    _, _, ok_old = footer_hierarchy(0.018, 0.040, 96, 56)
    _, _, ok_same = footer_hierarchy(0.022, 0.02963, 64, 64 * 0.93 * 0.022 / 0.02963)
    _check(not ok_old and not ok_same, "broken-case control: old/equal-size footer rows are rejected")


# Favorites are off on the cabinet (the maintainer 2026-11-14, cabinet_tweaks 7c2a182). ES-DE's gamelistinfo element always
# appends the favorites count ("star 0"), so the theme must not define it; no favorite badge slot (old favorite flags
# in gamelists would otherwise show), and no auto-favorites badge/chip art.
def favorites_leftovers(theme_dir):
    """-> problems (empty = ok)."""
    out = []
    for f in sorted(os.listdir(theme_dir)):
        if not f.endswith(".xml"):
            continue
        root = ET.parse(os.path.join(theme_dir, f)).getroot()
        for el in root.iter():
            if el.tag == "gamelistinfo":
                out.append("%s: <gamelistinfo name=%r> shows the favorites count" % (f, el.get("name")))
            if el.tag == "systemdata" and "favorites" in (el.text or "").lower():
                out.append("%s: <systemdata>%s</systemdata> is a favorites count" % (f, el.text.strip()))
            if el.tag == "slots" and "favorite" in [s.strip() for s in (el.text or "").split(",")]:
                out.append("%s: badges slot 'favorite'" % f)
    for sub in ("systems", "chips"):
        if os.path.isfile(os.path.join(theme_dir, "_inc", sub, "auto-favorites.png")):
            out.append("_inc/%s/auto-favorites.png can never show" % sub)
    return out


def test_no_favorites_ui():
    problems = favorites_leftovers(os.path.join(THEMES, "coin-dojo"))
    _check(not problems, "no favorites count / badge / auto-favorites art (%s)" % "; ".join(problems))


def test_BROKEN_favorites_ui_is_detected():
    """Deliberately-broken control: a theme with gamelistinfo, a favorites count, a favorite slot and
    auto-favorites art MUST report all four."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        for sub in ("systems", "chips"):
            os.makedirs(os.path.join(d, "_inc", sub))
        open(os.path.join(d, "_inc", "systems", "auto-favorites.png"), "wb").close()
        open(os.path.join(d, "theme.xml"), "w").write(
            "<theme><view name=\"gamelist\"><gamelistinfo name=\"gamelistInfo\"/>"
            "<text name=\"n\"><systemdata>gamecountFavorites</systemdata></text>"
            "<badges name=\"b\"><slots>favorite, completed</slots></badges></view></theme>")
        msgs = "; ".join(favorites_leftovers(d))
        _check(all(k in msgs for k in ("gamelistinfo", "gamecountFavorites", "slot 'favorite'", "auto-favorites")),
               "broken-case control: favorites UI leftovers are reported (%s)" % msgs)


CAROUSEL_SAFE = 0.03   # 43 px of 1440: the side margin the rest of the theme keeps (gamelist panels at 0.03, 0.97)
CAROUSEL_GAP = 0.01    # a neighbour must also end this far before the selected badge's box


def carousel_props(theme_xml_text, aspect_xml_text=""):
    """systemCarousel props from theme.xml, then the aspect-ratio file's overrides (ES-DE's merge order)."""
    props = {}
    for text in (theme_xml_text, aspect_xml_text):
        if not text:
            continue
        for car in ET.fromstring(text).iter("carousel"):
            if car.get("name") == "systemCarousel":
                for el in car:
                    props[el.tag] = (el.text or "").strip()
    return props


def carousel_edge_problems(props):
    """#221: on a horizontal carousel with N items the slots sit size.x/N apart, the selected one centred; a
    neighbour (scaled 1/itemScale back to itemSize) spans centre -/+ itemSize.x/2 after selectedItemMargins push it
    out (CarouselComponent.h, ES-DE 3.5.0). All in screen-width fractions. -> problems (empty = ok)."""
    try:
        pos_x, size_x = float(props["pos"].split()[0]), float(props["size"].split()[0])
        n = int(props["maxItemCount"])
        iw = float(props["itemSize"].split()[0])
        scale = float(props.get("itemScale", "1.2"))
        m_left, m_right = (float(v) for v in props.get("selectedItemMargins", "0 0").split())
    except (KeyError, ValueError) as e:
        return ["carousel geometry unreadable: %r" % (e,)]
    if props.get("type", "horizontal") != "horizontal" or n != 3:
        return ["the check models a horizontal carousel with maxItemCount 3"]
    centre, step = pos_x + size_x / 2, size_x / n
    sel_half = iw * scale / 2
    left = (centre - step - m_left - iw / 2, centre - step - m_left + iw / 2)
    right = (centre + step + m_right - iw / 2, centre + step + m_right + iw / 2)
    out = []
    if left[0] < CAROUSEL_SAFE:
        out.append("left neighbour starts at %.3f, inside the %.2f side margin" % (left[0], CAROUSEL_SAFE))
    if right[1] > 1 - CAROUSEL_SAFE:
        out.append("right neighbour ends at %.3f, past %.2f" % (right[1], 1 - CAROUSEL_SAFE))
    if left[1] > centre - sel_half - CAROUSEL_GAP or right[0] < centre + sel_half + CAROUSEL_GAP:
        out.append("a neighbour runs into the selected badge (%.3f..%.3f)" % (centre - sel_half, centre + sel_half))
    return out


def _sgs(name):
    return open(os.path.join(THEMES, "coin-dojo", name)).read()


def test_carousel_neighbours_inside_safe_margin():
    base = _sgs("theme.xml")
    for aspect in ("aspect-ratio-4-3.xml", "aspect-ratio-16-9.xml"):
        bad = carousel_edge_problems(carousel_props(base, _sgs(aspect)))
        _check(not bad, "%s: carousel neighbours inside the side margin (%s)" % (aspect, "; ".join(bad)))
    p169 = carousel_props(base, _sgs("aspect-ratio-16-9.xml"))
    got = (p169.get("itemSize"), p169.get("itemScale"), p169.get("selectedItemMargins"))
    _check(got == ("0.165 0.062", "2.2", "0.045 0.045"), "16:9 carousel geometry unchanged by #221 %s" % (got,))


def test_BROKEN_carousel_neighbour_at_the_edge_is_flagged():
    """Deliberately-broken controls: the pre-#221 4:3 geometry (left title at x=0), a title past the right edge,
    and a neighbour pushed into the selected badge MUST be reported."""
    old = {"pos": "0 0.40", "size": "1 0.26", "maxItemCount": "3", "itemSize": "0.22 0.062", "itemScale": "2.2",
           "selectedItemMargins": "0.06 0.06"}
    _check(any("left neighbour" in p for p in carousel_edge_problems(old)),
           "BROKEN CASE: the old 4:3 geometry (title at x=0) is flagged")
    _check(any("right neighbour" in p for p in carousel_edge_problems(dict(old, selectedItemMargins="0.02 0.09"))),
           "BROKEN CASE: a right title past the edge is flagged")
    _check(any("selected badge" in p for p in carousel_edge_problems(dict(old, selectedItemMargins="-0.05 -0.05"))),
           "BROKEN CASE: a neighbour over the selected badge is flagged")
    _check(carousel_edge_problems({"pos": "0 0.4"}), "BROKEN CASE: missing geometry is reported, not passed")


if __name__ == "__main__":
    main(globals())
