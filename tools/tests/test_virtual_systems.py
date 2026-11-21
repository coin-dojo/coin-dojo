#!/usr/bin/env python3
"""Tests for the single source of the virtual-system names (ticket #94, C7 of #103).

Run: python3 tools/tests/test_virtual_systems.py   (stdlib; the declaration-vs-loader check uses ~/.venvs/coin-dojo, else SKIPs)
hk_virtual_systems in config/variables.d/collections.yml is the one source; skills/collections/nested.py reads it
(env HK_VIRTUAL_SYSTEMS, set by the esde_collections role, else the declaration in a checkout); collgen, controls,
resolve-picks and gamelist_drift take it from there. Broken cases (must FAIL): a planted second copy of the list
in code (also in a YAML role default and a shell script), an unreadable or malformed source, bad names in the env,
a role default or group var that types the list or a nested-args literal.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
sys.path.insert(0, os.path.join(ROOT, "skills", "collections"))
from hktest import check, main, skip  # noqa: E402
import nested  # noqa: E402

DECL = os.path.join(ROOT, "config", "variables.d", "collections.yml")
MIN_NAMES = 5          # this many distinct names close together = a copy of the list


def code_files(root):
    """Tracked files that can hold code: not docs, not tests (they may pin expected output), not the declaration."""
    r = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True)
    paths = r.stdout.split() if r.returncode == 0 and r.stdout.strip() else [
        os.path.relpath(os.path.join(d, f), root) for d, _s, fs in os.walk(root) for f in fs]
    out = []
    for p in paths:
        parts = p.split("/")
        if p == "config/variables.d/collections.yml" or p == "docs/CONFIG.md" or parts[0] in (".git", "docs", ".claude", "memory", "personal"):
            continue
        if p.endswith((".md", ".png", ".jpg", ".svg", ".xml", ".tsv", ".json", ".html", ".ttf", ".otf", ".woff2", ".lock")):
            continue
        if "tests" in parts[:-1] or parts[-1].startswith("test_"):
            continue
        out.append(p)
    return out


def copies(root, names):
    """Files with MIN_NAMES or more of the names in one bracketed group, on one line, or as a run of one-name lines."""
    alt = "|".join(re.escape(n) for n in names)
    tok = re.compile(r"(?<![\w-])(%s)(?![\w-])" % alt)
    group = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")
    only = re.compile(r"^[-\s\"',#]*(%s)[-\s\"',]*$" % alt)
    hits = []
    for p in code_files(root):
        try:
            text = open(os.path.join(root, p), encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        spans = [m.group(0) for m in group.finditer(text)] + text.splitlines()
        bad = any(len(set(tok.findall(sp))) >= MIN_NAMES for sp in spans)
        run = set()
        for line in text.splitlines() + [""]:
            m = only.match(line)
            if m:
                run.add(m.group(1))
                bad = bad or len(run) >= MIN_NAMES
            else:
                run = set()
        if bad:
            hits.append(p)
    return hits


def test_no_second_literal_list():
    check(copies(ROOT, nested.ALL_VIRTUAL) == [], "no tracked code carries a second copy of the virtual-system names: %s" % copies(ROOT, nested.ALL_VIRTUAL))


def test_planted_copy_fails():
    names = list(nested.ALL_VIRTUAL)
    plants = {"x.py": "V = %r\n" % (names,),
              "role/defaults.yml": "virtual: [%s]\n" % ", ".join(names),
              "deploy.sh": "for v in %s; do echo $v; done\n" % " ".join(names),
              "multi.py": "V = (\n" + "".join('    "%s",\n' % n for n in names) + ")\n",
              "block.yml": "virtual:\n" + "".join("  - %s\n" % n for n in names)}
    for rel, body in plants.items():
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, rel.replace("/", "_")), "w") as fh:
                fh.write(body)
            check(copies(d, names) == [rel.replace("/", "_")], "BROKEN: a planted copy in %s FAILs the scan" % rel)
    with tempfile.TemporaryDirectory() as d:        # four of the names (a subset) is not a copy; docs and tests are not code
        with open(os.path.join(d, "ok.py"), "w") as fh:
            fh.write("BROWSE = %r\n" % (names[1:5],))
        os.makedirs(os.path.join(d, "tests"))
        with open(os.path.join(d, "tests", "t.py"), "w") as fh:
            fh.write("V = %r\n" % (names,))
        check(copies(d, names) == [], "a subset and a pinned list in tests/ are not flagged")


def test_loader_reads_the_declaration():
    names = nested.load_virtual_names(env={}, names_file=os.devnull + ".none")
    check(names == nested.ALL_VIRTUAL and len(names) == 10 and names[:2] == ("must-play", "must-play-genres") and names[-4:] == ("fightcade", "trophies", "settings", "quit"),
          "the loader reads hk_virtual_systems from the declaration (checkout): %s" % (names,))
    check(nested.load_virtual_names(env={"HK_VIRTUAL_SYSTEMS": '["a", "b-c"]'}) == ("a", "b-c"), "the env var (the cabinet) overrides it")
    check([v for v, _t in nested.VIRTUAL] == [n for n in names if n not in nested.TOOL_SLUGS]
          and [t for _v, t in nested.VIRTUAL][0] == "MUST PLAY", "carousel entries = the names without the tools (fightcade, trophies, settings, quit), title = slug in capitals")
    check(set(nested.VIRTUAL_SLUGS) | set(nested.TOOL_SLUGS) == set(names), "generated + tool slugs = the configured names")


def test_file_beside_the_tools():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "virtual_systems.json")
        open(f, "w").write('["x-one", "settings"]\n')
        check(nested.load_virtual_names(env={}, names_file=f) == ("x-one", "settings"), "virtual_systems.json beside the tools is read (the cabinet, by hand)")
        check(nested.load_virtual_names(env={"HK_VIRTUAL_SYSTEMS": '["from-env"]'}, names_file=f) == ("from-env",), "env wins over the file")
        open(f, "w").write("not json")
        try:
            nested.load_virtual_names(env={}, names_file=f)
            check(False, "BROKEN: a malformed virtual_systems.json must FAIL")
        except ValueError:
            check(True, "BROKEN: a malformed virtual_systems.json FAILs")
        # no env, no file, no checkout: a clear error that says what to do
        try:
            nested.load_virtual_names(env={}, names_file=os.path.join(d, "absent", "virtual_systems.json"), decl=os.path.join(d, "nope.yml"))
            check(False, "BROKEN: no env, no file, no checkout must FAIL")
        except SystemExit as e:
            check("HK_VIRTUAL_SYSTEMS" in str(e) and "virtual_systems.json" in str(e) and "make deploy" in str(e),
                  "BROKEN: no env, no file, no checkout -> a clear error naming all three")
    # a real run of the copied tools with the file and no env (a cabinet by hand), and with nothing at all
    with tempfile.TemporaryDirectory() as d:
        for fn in ("nested.py",):
            open(os.path.join(d, fn), "w").write(open(os.path.join(ROOT, "skills", "collections", fn)).read())
        env = {k: v for k, v in os.environ.items() if k != "HK_VIRTUAL_SYSTEMS"}
        cmd = [sys.executable, "-c", "import nested; print(nested.ALL_VIRTUAL)"]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=d)
        check(r.returncode != 0 and "virtual-system names are unknown" in r.stderr, "BROKEN: the tools alone (no env, no file, no checkout) FAIL clearly")
        open(os.path.join(d, "virtual_systems.json"), "w").write('["must-play", "settings"]')
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=d)
        check("('must-play', 'settings')" in r.stdout, "the tools plus the role's JSON file run with no env")


def test_broken_sources_fail():
    for raw in ('["Bad Name"]', "[]", '[1, 2]'):
        try:
            nested.load_virtual_names(env={"HK_VIRTUAL_SYSTEMS": raw})
            check(False, "BROKEN: env %s must FAIL" % raw)
        except SystemExit as e:
            check("bad virtual-system names" in str(e), "BROKEN: env %s FAILs" % raw)
    try:
        nested.load_virtual_names(env={"HK_VIRTUAL_SYSTEMS": "not json"})
        check(False, "BROKEN: env that is not JSON must FAIL")
    except ValueError:
        check(True, "BROKEN: env that is not JSON FAILs")
    with tempfile.TemporaryDirectory() as d:
        for label, body in (("missing file", None), ("no default line", "variables:\n  hk_virtual_systems:\n    type: list\n")):
            p = os.path.join(d, "c.yml")
            if body is not None:
                open(p, "w").write(body)
            try:
                nested.load_virtual_names(env={}, names_file=os.path.join(d, "none.json"), decl=p if body is not None else os.path.join(d, "nope.yml"))
                check(False, "BROKEN: %s must FAIL" % label)
            except SystemExit as e:
                check("virtual-system names" in str(e) or "no `default" in str(e), "BROKEN: %s FAILs with a message" % label)


def test_scripts_follow_the_source():
    for name in ("collgen", "controls"):
        sys.path.insert(0, os.path.join(ROOT, "skills", name))
    import collgen
    import controls
    check(tuple(collgen.VIRTUAL_SYSTEMS) + tuple(collgen.TOOL_SYSTEMS) == nested.ALL_VIRTUAL, "collgen skip list = the one source")
    check(controls.VIRTUALS == set(nested.ALL_VIRTUAL), "controls.py skips exactly the configured names")
    env = dict(os.environ, HK_VIRTUAL_SYSTEMS='["alpha", "settings"]')
    r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import nested; print(nested.ALL_VIRTUAL)" % os.path.join(ROOT, "skills", "collections")],
                       capture_output=True, text=True, env=env)
    check("('alpha', 'settings')" in r.stdout, "a fresh interpreter honours HK_VIRTUAL_SYSTEMS")
    drift = os.path.join(ROOT, "ansible", "roles", "esde_collections", "files", "gamelist_drift.py")
    r = subprocess.run([sys.executable, "-I", drift, "--help"], capture_output=True, text=True, env={k: v for k, v in os.environ.items() if k != "HK_VIRTUAL_SYSTEMS"})
    check(r.returncode != 0 and "virtual_systems.json" in r.stderr, "BROKEN: gamelist_drift.py without env and file FAILs loudly")
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "gamelist_drift.py"), "w").write(open(drift).read())
        open(os.path.join(d, "virtual_systems.json"), "w").write('["a"]')
        r = subprocess.run([sys.executable, "-I", os.path.join(d, "gamelist_drift.py"), "--help"], capture_output=True, text=True,
                           env={k: v for k, v in os.environ.items() if k != "HK_VIRTUAL_SYSTEMS"})
        check(r.returncode == 0, "gamelist_drift.py falls back to the file beside it")
    r = subprocess.run([sys.executable, "-I", drift, "--help"], capture_output=True, text=True, env=env)
    check(r.returncode == 0, "gamelist_drift.py runs with the env the role sets")
    picks = os.path.join(ROOT, "skills", "collections", "folder_photos", "resolve-picks.py")
    check("nested.ALL_VIRTUAL" in open(picks).read(), "resolve-picks.py takes the names from nested.py")


def test_role_and_group_vars_hold_no_copy():
    esde = open(os.path.join(ROOT, "ansible", "group_vars", "esde.yml")).read()
    check(not re.search(r"(?m)^esde_virtual_systems:", esde), "group_vars/esde.yml no longer types esde_virtual_systems")
    defaults = open(os.path.join(ROOT, "ansible", "roles", "esde_collections", "defaults", "main.yml")).read()
    check('esde_collections_nested_args: "{{ hk_nested_args }}"' in defaults, "esde_collections_nested_args is a pass-through of hk_nested_args (no hidden knob)")
    check(not re.search(r"(?m)^esde_collections_nested_args: *\[", defaults), "BROKEN shape: a literal nested-args default would not be the pass-through")
    tasks = open(os.path.join(ROOT, "ansible", "roles", "esde_collections", "tasks", "main.yml")).read()
    check(tasks.count('HK_VIRTUAL_SYSTEMS: "{{ esde_virtual_systems | to_json }}"') == 3, "the drift check, the build and the validator get the names via env")
    verify = open(os.path.join(ROOT, "ansible", "roles", "esde_verify", "tasks", "main.yml")).read()
    check(tasks.count("dest: \"{{ coin_dojo_dir }}/virtual_systems.json\"") == 1 and tasks.count("tools/virtual_systems.json") == 1
          and tasks.count("content: \"{{ esde_virtual_systems | to_json }}") == 2, "the role writes virtual_systems.json (build + shipped) from esde_virtual_systems")
    check(verify.count('HK_VIRTUAL_SYSTEMS: "{{ esde_virtual_systems | to_json }}"') == 1, "esde_verify's validator run gets the names via env too")
    check("environment:" not in verify.split("Validate the live nested_out", 1)[0], "BROKEN shape: the env sits on the validator task only")


def test_declaration_matches_config_loader():
    venv = os.path.expanduser("~/.venvs/coin-dojo/bin/python")
    if not os.path.exists(venv):
        skip("PyYAML venv missing (make config-venv): hk_virtual_systems not compared with tools/config.py")
        return
    env = {k: v for k, v in os.environ.items() if not k.startswith("HK_")}
    r = subprocess.run([venv, os.path.join(ROOT, "tools", "config.py"), "get", "--limit", "coin-dojo", "hk_virtual_systems"],
                       capture_output=True, text=True, env=dict(env, HK_CONFIG=os.path.join(ROOT, "config", "local.example.yml")))
    got = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip().startswith("[") else r.stdout.split()
    check(list(got) == list(nested.ALL_VIRTUAL), "tools/config.py and the stdlib loader agree: %s vs %s (%s)" % (got, nested.ALL_VIRTUAL, r.stderr.strip()[:100]))
    decl = open(DECL).read()
    check("exports: [esde_virtual_systems]" in decl and "exports: [esde_collections_nested_args]" in decl, "both variables export their Ansible names")


if __name__ == "__main__":
    main(globals())
