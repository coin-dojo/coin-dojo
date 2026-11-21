#!/usr/bin/env python3
"""Gate (#190): the maintainer's game data lives only in personal/, and the community export carries none of it.

    python3 tests/test_personal_data.py

1. The moved lists sit in personal/ with a neutral community example of the same name outside it.
2. The loaders read the personal copy: nested.data_path (collgen dupes default), smoke_launch.TABLE, and the
   esde_collections role (checked in ansible/tests/test_esde_roles.py).
3. The real export (tools/edition_export.py --strict into a temp dir) leaves 0 denylist / private-value hits.
4. BROKEN CASE: a throwaway repo with a game name planted outside personal/ is flagged (exit 1, the path named);
   the same name under personal/ is not.
Game names are built from fragments so this file itself passes the scan. In the community tree (no personal/,
no tools/edition_export.py) every test skips. Pure stdlib.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
from hktest import check, main, skip  # noqa: E402

PERSONAL = os.path.join(ROOT, "personal")
EXPORT = os.path.join(ROOT, "tools", "edition_export.py")
# personal copy -> community example (same path relative to its root)
MOVED = ["skills/collections/series.tsv", "skills/collections/folder_text.tsv", "skills/collections/duplicates.tsv",
         "skills/collections/system_names.tsv", "skills/collections/folder_photos/folder_picks.tsv",
         "tests/smoke/smoke_games.tsv"]
PERSONAL_ONLY = ["docs/reference/iconic-game-roster.md", "docs/reference/arcade-game-roster.md",
                 "skills/collections/tests/series_cases.tsv"]
GAME = "Final " + "Fight"          # a denylisted title, split so this file stays clean


def _private():
    return os.path.isdir(PERSONAL) and os.path.isfile(EXPORT)


def test_moved_lists_live_in_personal_with_an_example_outside():
    if not _private():
        return skip("community tree: no personal/")
    for rel in MOVED:
        check(os.path.isfile(os.path.join(PERSONAL, rel)), "personal/%s exists" % rel)
        check(os.path.isfile(os.path.join(ROOT, rel)), "%s (community example) exists" % rel)
    for rel in PERSONAL_ONLY:
        check(os.path.isfile(os.path.join(PERSONAL, rel)) and not os.path.exists(os.path.join(ROOT, rel)),
              "%s lives only in personal/" % rel)


def test_loaders_read_the_personal_copy():
    if not _private():
        return skip("community tree: no personal/")
    sys.path.insert(0, os.path.join(ROOT, "skills", "collections"))
    import nested
    for name in ("series.tsv", "folder_text.tsv", "duplicates.tsv", "system_names.tsv"):
        check(os.path.samefile(nested.data_path(name), os.path.join(PERSONAL, "skills", "collections", name)),
              "nested.data_path(%r) is the personal copy" % name)
    check(nested.SYSTEM_TITLES["cps1"] != "CP System", "personal system_names.tsv renames the CPS boards (cabinet names)")
    sys.path.insert(0, os.path.join(HERE, "smoke"))
    import smoke_launch
    check(os.path.samefile(smoke_launch.TABLE, os.path.join(PERSONAL, "tests", "smoke", "smoke_games.tsv")),
          "smoke_launch.TABLE is the personal table")


def _export(repo, out, *extra):
    # #240: the tracked private list must load; a toy repo has none and borrows this repo's list
    pl = ["--private-list", os.path.join(ROOT, "config", "edition", "private.txt")] if repo != ROOT else []
    r = subprocess.run([sys.executable, EXPORT, "--repo", repo, "--out", out, "--force", "--strict", *pl, *extra],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def test_real_export_has_zero_hits():
    if not _private():
        return skip("community tree: no export tool")
    out = os.path.join(tempfile.mkdtemp(), "out")
    rc, text = _export(ROOT, out)
    last = [ln for ln in text.splitlines() if ln.startswith("RESULT:")][-1:]
    check(rc == 0 and last and last[0].startswith("RESULT: PASS"), "edition export of this repo: 0 hits (%s)" % last)
    check(not os.path.exists(os.path.join(out, "personal")), "the export has no personal/")
    shutil.rmtree(os.path.dirname(out), ignore_errors=True)


def _toy_repo(files):
    """A throwaway git repo with this repo's edition config (no edits) and the given files."""
    d = tempfile.mkdtemp()
    for name in ("denylist.txt", "rename.tsv", "personas.tsv", "allow.tsv", "publish.tsv"):   # #246, #283
        os.makedirs(os.path.join(d, "config", "edition"), exist_ok=True)
        shutil.copy(os.path.join(ROOT, "config", "edition", name), os.path.join(d, "config", "edition", name))
    files = dict(files, **{"config/edition/edits.tsv": "# none\n"})
    shutil.copy(os.path.join(ROOT, "AGENTS.md"), os.path.join(d, "AGENTS.md"))     # the persona table
    for rel, body in files.items():
        os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
        with open(os.path.join(d, rel), "w", encoding="utf-8") as f:
            f.write(body)
    subprocess.run(["git", "init", "-q", d], check=True)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    return d


def test_planted_game_name_outside_personal_is_flagged():                # BROKEN CASE
    if not _private():
        return skip("community tree: no export tool")
    row = "x\t%s\t%s\n" % (GAME, GAME)
    d = _toy_repo({"skills/x/list.tsv": row, "personal/skills/x/list.tsv": row})
    rc, text = _export(d, os.path.join(d + "-out", "o"))
    hits = [ln for ln in text.splitlines() if ln.split(":")[0].endswith("list.tsv") and ":" in ln]
    check(rc == 1, "BROKEN: the export fails on a planted game name (rc %d)" % rc)
    check(any(ln.startswith("skills/x/list.tsv:1:") for ln in hits), "BROKEN: the planted path is named: %s" % hits)
    check(not any(ln.startswith("personal/") for ln in hits), "the same name under personal/ is never scanned")
    clean = _toy_repo({"personal/skills/x/list.tsv": row, "skills/x/list.tsv": "x\tLast Round\tLast Round\n"})
    rc, text = _export(clean, os.path.join(clean + "-out", "o"))
    check(rc == 0, "the same repo with a neutral example passes (rc %d)" % rc)
    for p in (d, d + "-out", clean, clean + "-out"):
        shutil.rmtree(p, ignore_errors=True)


if __name__ == "__main__":
    main(globals())
