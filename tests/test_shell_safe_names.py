#!/usr/bin/env python3
"""Gate (#202): no shell-active character in any name or path ES-DE hands to an event script.

    python3 tests/test_shell_safe_names.py

Why: #195 turned on ES-DE custom event scripts. ES-DE v3.5.0 builds `"<script>" "<arg1>" ... "<arg4>"`
(es-core/src/Scripting.cpp:67-100; no quotes at all when an arg already starts with `"`) and runs it with
system() = /bin/sh -c (es-core/src/utils/PlatformUtil.cpp:99-131). game-select passes the raw game path and the
gamelist <name> (es-app/src/views/GamelistView.cpp:458-462), game-start/game-end the name too (FileData.cpp:2174,
2356). Inside double quotes ` $ " and backslash stay active, so a name like `$(cmd)` runs as the cabinet user.

1. The committed curated TSVs (community examples and personal/), in every column that becomes a folder name, a
   folder title or a ROM path, hold none of them; an unknown TSV fails until its columns are declared here.
2. The built-in names (virtual systems, tool systems, genre/system titles and labels) hold none of them.
3. nested.shell_safe() removes all four; a scraped name with `$(` comes out of `collgen.py nested` safe and the
   tree passes validate_nested.py (the deploy's pre-write gate, check 5).
4. The ES-DE command line, rebuilt as Scripting.cpp builds it and run through sh -c, expands a planted `$(...)`
   (the risk is real) and passes a shell_safe() name through literally.
BROKEN CASES: a planted `$(` title in a TSV copy (file:line named), a `$(` <name> written into a generated gamelist,
a ROM file name with `$(`, a backtick in an es_systems <fullname>: each is refused with its path named.
Pure stdlib; builds trees from skills/collections/tests/fixtures in temp dirs.
"""
import glob
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
from hktest import check, main  # noqa: E402

SKILL = os.path.join(ROOT, "skills", "collections")
FIX = os.path.join(SKILL, "tests", "fixtures")
GL, PICKS = os.path.join(FIX, "gamelists"), os.path.join(FIX, "picks.html")
sys.path.insert(0, SKILL)
import nested  # noqa: E402
import validate_nested as vn  # noqa: E402
_spec = importlib.util.spec_from_file_location("collgen", os.path.join(SKILL, "collgen.py"))
cg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cg)

ACTIVE = "`$\"\\"                 # the four characters still active inside sh double quotes
PLANT = "Evil $(touch pwned)"     # never run by this file except as `printf` in test 4's own sh

# TSV basename -> 0-based columns that end up in a folder dir, folder title or ROM path (regex/blurb columns are
# never passed to scripts). Every TSV under the two dirs must be listed.
TSV_COLUMNS = {
    "series.tsv": (0, 1),               # slug (folder dir), title (folder <name>); col 2 is a regex
    "system_names.tsv": (0, 1),         # system (folder dir), title (folder <name>); maker/year/kind = info panel
    "folder_text.tsv": (0, 1),          # virtual, folder; col 2 is the <desc> blurb
    "folder_picks.tsv": (0, 1),         # virtual, folder; col 2 is a regex
    "twin_stick.tsv": (0, 1),           # system, ROM stem
    "duplicates.tsv": (0, 1, 2, 3),     # system, ROM path, system, ROM path
    "mame_hidden_names.tsv": (1,),      # kind, MAME short name
    "thumbnails.tsv": (),               # libretro repo + sha: not a name
}
TSV_DIRS = [os.path.join(ROOT, "skills", "collections"), os.path.join(ROOT, "personal", "skills", "collections")]


def bad(value):
    return "".join(sorted(set(c for c in value if c in ACTIVE)))


def tsv_files():
    out = []
    for d in TSV_DIRS:
        for p in sorted(glob.glob(os.path.join(d, "**", "*.tsv"), recursive=True)):
            if "tests" not in os.path.relpath(p, d).split(os.sep):      # test case tables are not shipped data
                out.append(p)
    return out


def tsv_problems(path, cols):
    """-> ['path:line: column N 'value' has X'] for the declared columns of one TSV."""
    probs = []
    with open(path, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            for c in cols:
                if c < len(parts) and bad(parts[c]):
                    probs.append("%s:%d: column %d %r has %s" % (os.path.relpath(path, ROOT), n, c + 1,
                                                                 parts[c].strip(), bad(parts[c])))
    return probs


def call(fn, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = fn(list(argv))
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


def build(edit=None):
    """A nested tree from a copy of the fixture gamelists (edit(gamelists_dir) may plant data first); ROM targets
    are empty placeholder files so no link is dead. -> (base, out, roms, nested rc, stderr)."""
    base = tempfile.mkdtemp(prefix="hk202-")
    gls, out, roms = os.path.join(base, "gamelists"), os.path.join(base, "out"), os.path.join(base, "roms")
    shutil.copytree(GL, gls)
    if edit:
        edit(gls)
    for system in os.listdir(gls):
        os.makedirs(os.path.join(roms, system))
        with open(os.path.join(gls, system, "gamelist.xml"), encoding="utf-8") as f:
            for fn in re.findall(r"<path>\./([^<]+)</path>", f.read()):
                open(os.path.join(roms, system, fn.replace("&quot;", '"')), "w").close()
    rc, _o, err = call(cg.main, ["nested", "--gamelists", gls, "--picks", PICKS, "--out", out, "--min-decade", "1",
                                 "--rom-root", "/data/roms", "--apply"])
    return base, out, roms, rc, err


def validate(out, roms):
    return call(vn.main, ["--out", out, "--map", "/data/roms=" + roms])


def plant_name(gls, name=PLANT, system="cps2", path="./19xx.zip"):
    p = os.path.join(gls, system, "gamelist.xml")
    with open(p, encoding="utf-8") as f:
        text = f.read()
    i = text.index("<path>%s</path>" % path)
    j = text.index("<name>", i)
    k = text.index("</name>", j)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text[:j + 6] + name.replace("&", "&amp;").replace("<", "&lt;") + text[k:])


def generated_names(out):
    """[(gamelist rel path, tag, value)] for every <path>/<name> in the generated gamelists."""
    res = []
    for gl in sorted(glob.glob(os.path.join(out, "gamelists", "*", "gamelist.xml"))):
        for e in vn.parse_xml(gl).iter():
            if e.tag in ("game", "folder"):
                for tag in ("path", "name"):
                    res.append((os.path.relpath(gl, out), tag, (e.findtext(tag) or "").strip()))
    return res


# ------------------------------------------------------------------------------------------------ 1. TSVs

def test_committed_tsvs_are_shell_safe():
    files = tsv_files()
    check(len(files) >= 7, "found the curated TSVs (%d)" % len(files))
    unknown = sorted(set(os.path.basename(p) for p in files) - set(TSV_COLUMNS))
    check(not unknown, "every TSV has its name/path columns declared here (unknown: %s)" % ", ".join(unknown))
    probs = [x for p in files for x in tsv_problems(p, TSV_COLUMNS.get(os.path.basename(p), ()))]
    check(not probs, "no shell-active character in a TSV name/path column%s" % (": " + "; ".join(probs[:5]) if probs else ""))


def test_planted_tsv_title_is_refused():                                 # BROKEN CASE
    src = os.path.join(SKILL, "series.tsv")
    d = tempfile.mkdtemp(prefix="hk202-")
    dst = os.path.join(d, "series.tsv")
    with open(src, encoding="utf-8") as f:
        lines = f.read().splitlines()
    n = next(i for i, l in enumerate(lines) if l.strip() and not l.startswith("#"))
    parts = lines[n].split("\t")
    parts[1] = PLANT
    lines[n] = "\t".join(parts)
    with open(dst, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    probs = tsv_problems(dst, TSV_COLUMNS["series.tsv"])
    check(len(probs) == 1 and (":%d: column 2" % (n + 1)) in probs[0] and "series.tsv" in probs[0] and "$" in probs[0],
          "BROKEN: a $( series title is refused with file:line named (%s)" % probs)
    parts[1], parts[2] = "Fine", r"^Mega\$"                               # a regex column may hold $ and \
    lines[n] = "\t".join(parts)
    with open(dst, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    check(tsv_problems(dst, TSV_COLUMNS["series.tsv"]) == [], "a $ or backslash in the regex column is not flagged")
    shutil.rmtree(d)


# ------------------------------------------------------------------------------------- 2. built-in names

def test_builtin_names_are_shell_safe():
    names = [v for v, _t in nested.VIRTUAL] + [t for _v, t in nested.VIRTUAL] + list(nested.TOOL_SLUGS)
    names += [nested.SETTINGS_TITLE, nested.TROPHIES_TITLE, nested.SETTINGS_LINK, nested.TROPHIES_LINK,
              nested.ACHIEVEMENTS_SLUG, nested.ACHIEVEMENTS_TITLE, nested.POINTER_DIR]
    for table in (nested.GENRE_TITLES, nested.SYSTEM_TITLES, nested.SYSTEM_LABELS, nested.ARCADE_LABELS,
                  nested.FOLDER_DIRS):
        names += list(table) + list(table.values())
    hits = sorted(n for n in names if bad(n))
    check(not hits, "no shell-active character in %d built-in names (%s)" % (len(names), hits))
    check(set(nested.SHELL_ACTIVE) == set(ACTIVE), "nested.SHELL_ACTIVE is exactly ` $ \" backslash")


# ------------------------------------------------------------------------- 3. generator + deploy gate

def test_shell_safe_removes_all_four():
    s = nested.shell_safe('Ca$h "Q" `id` a\\b $(x)')
    check(not bad(s), "shell_safe leaves no active character (%r)" % s)
    check(nested.shell_safe("19XX : The War Against Destiny") == "19XX : The War Against Destiny",
          "a normal name is unchanged")
    check(nested.shell_safe(s) == s and nested.shell_safe(None) == "", "idempotent, None -> ''")


def test_scraped_name_comes_out_safe_and_tree_passes():
    base, out, roms, rc, err = build(lambda g: plant_name(g, 'Evil $(touch pwned) "q" `id` \\'))
    check(rc == 0, "nested builds with a planted scraped name (%s)" % err.strip()[-200:])
    names = generated_names(out)
    hits = [x for x in names if bad(x[2])]
    check(names and not hits, "no generated <path>/<name> has an active character (%d fields; %s)" % (len(names), hits[:3]))
    # collgen drops "(...)" tags from names, so "(touch pwned)" goes; the $ before it stays and is replaced
    check(any(t == "name" and v.startswith("Evil S") and v.endswith("'q' 'id' /") for _g, t, v in names),
          "the planted name is there, made safe (%s)" % [v for _g, t, v in names if v.startswith("Evil")][:1])
    vrc, stdout, verr = validate(out, roms)
    ok = vrc == 0 and stdout.rstrip().endswith("RESULT: PASS")
    check(ok, "validate_nested passes the tree" + ("" if ok else " (%r %r)" % (stdout[-300:], verr[-200:])))
    shutil.rmtree(base)


def test_planted_generated_name_is_refused():                            # BROKEN CASE
    base, out, roms, rc, _e = build()
    gl = os.path.join(out, "gamelists", "genres", "gamelist.xml")
    with open(gl, encoding="utf-8") as f:
        text = f.read()
    i = text.index("<game>")
    j, k = text.index("<name>", i) + 6, text.index("</name>", i)
    with open(gl, "w", encoding="utf-8") as f:
        f.write(text[:j] + PLANT + text[k:])
    vrc, stdout, _v = validate(out, roms)
    check(rc == 0 and vrc == 1 and stdout.rstrip().endswith("RESULT: FAIL"), "BROKEN: validate_nested fails (exit %s)" % vrc)
    check("FAIL shell: gamelists/genres/gamelist.xml <game> <name> '%s' has $" % PLANT in stdout,
          "BROKEN: the $( name is refused with its gamelist path named")
    shutil.rmtree(base)


def test_planted_rom_file_name_is_refused():                             # BROKEN CASE: a path, not a name
    def edit(gls):
        p = os.path.join(gls, "cps2", "gamelist.xml")
        with open(p, encoding="utf-8") as f:
            text = f.read()
        with open(p, "w", encoding="utf-8") as f:
            f.write(text.replace("<path>./19xx.zip</path>", "<path>./19xx $(id).zip</path>"))
    base, out, roms, rc, err = build(edit)
    vrc, stdout, _v = validate(out, roms)
    check(rc == 0 and vrc == 1, "BROKEN: a $( ROM file name fails validate_nested (nested %s %s, validate %s)"
          % (rc, err[-150:], vrc))
    check(re.search(r"FAIL shell: roms_nested/systems/cps2/19xx \$\(id\)\.zip has \$", stdout) is not None,
          "BROKEN: the link path under roms_nested is named")
    check(re.search(r"FAIL shell: gamelists/systems/gamelist.xml <game> <path> '\./cps2/19xx \$\(id\)\.zip'", stdout)
          is not None, "BROKEN: the gamelist <path> is named")
    shutil.rmtree(base)


def test_planted_system_fullname_is_refused():                           # BROKEN CASE
    base, out, roms, rc, _e = build()
    es = os.path.join(out, "custom_systems", "es_systems.xml")
    with open(es, encoding="utf-8") as f:
        text = f.read()
    with open(es, "w", encoding="utf-8") as f:
        f.write(text.replace("<fullname>GENRES</fullname>", "<fullname>GENRES `id`</fullname>", 1))
    vrc, stdout, _v = validate(out, roms)
    check(rc == 0 and vrc == 1 and "<system> <fullname> 'GENRES `id`' has `" in stdout and es in stdout,
          "BROKEN: a backtick in an es_systems fullname is refused with the file named (exit %s)" % vrc)
    shutil.rmtree(base)


# --------------------------------------------------------------------- 4. the ES-DE command line itself

def esde_command(script, *args):
    """The string Scripting.cpp:67-100 (v3.5.0) builds: each non-empty arg in double quotes unless it starts with one."""
    parts = ['"%s"' % script]
    for a in args:
        q = '"' if a and not a.startswith('"') else ""
        parts.append(q + a + q)
    return " ".join(parts)


def test_esde_command_line_expands_unsafe_and_keeps_safe_names():
    d = tempfile.mkdtemp(prefix="hk202-")
    script = os.path.join(d, "hook.sh")
    with open(script, "w") as f:
        f.write('#!/bin/sh\nprintf "%s\\n" "$2"\n')
    os.chmod(script, 0o755)
    unsafe = "Evil $(printf INJECTED)"
    r = subprocess.run(["/bin/sh", "-c", esde_command(script, "/data/nested/genres/x.zip", unsafe, "genres", "GENRES")],
                       capture_output=True, text=True, timeout=10)
    check(r.stdout.strip() == "Evil INJECTED", "the risk is real: sh -c ran the planted $(...) (%r)" % r.stdout)
    safe = nested.shell_safe(unsafe)
    r = subprocess.run(["/bin/sh", "-c", esde_command(script, "/data/nested/genres/x.zip", safe, "genres", "GENRES")],
                       capture_output=True, text=True, timeout=10)
    check(r.stdout.strip() == safe, "a shell_safe name reaches the script literally (%r)" % r.stdout)
    shutil.rmtree(d)


if __name__ == "__main__":
    main(globals())
