#!/usr/bin/env python3
"""The FIGHTCADE carousel entry is off (#219): the role passes --fightcade-entry only when
esde_collections_fightcade_entry is true (default false), and a deploy removes what an earlier deploy left. Plain python3.

    python3 ansible/roles/esde_collections/tests/test_fightcade_entry.py

Static (role text): the default is false, the collgen argv gates --fightcade-entry on it (and still on the launcher),
QUIT TO MAIN MENU is not gated by it, the live fightcade gamelist (seeded into the build as previous state) is dropped
from the build, and ES-DE's retired gamelists/fightcade is listed (so the backup runs) and removed only when the build
has no fightcade system.
Pipeline (collgen.py nested, the role's way: fresh build dir, rsync -rlc --delete into nested_out): a deploy with the
entry, then one without, leaves no FIGHTCADE in es_systems.xml, roms_nested or nested_out/gamelists; QUIT stays.
BROKEN cases (must be flagged): the gate removed, the default flipped to true, QUIT gated too, the cleanup task gone
or unconditional, the seeded gamelist kept in the build, the plan line gone; a redeploy that reuses the OLD build dir without --delete keeps the stale
gamelist (why the role builds fresh).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROLE = os.path.dirname(HERE)
REPO = os.path.normpath(os.path.join(ROLE, "..", "..", ".."))
SKILL = os.path.join(REPO, "skills", "collections")
FIX = os.path.join(SKILL, "tests", "fixtures")
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
from hktest import check, main, skip  # noqa: E402

TASKS = open(os.path.join(ROLE, "tasks", "main.yml"), encoding="utf-8").read()
DEFAULTS = open(os.path.join(ROLE, "defaults", "main.yml"), encoding="utf-8").read()

FC_GATE = re.compile(r"\['--fightcade-entry',[^\]]*\]\s*if ([^\]]*?) else \[\]\)", re.S)
QUIT_GATE = re.compile(r"\['--quit-entry',[^\]]*\]\s*if ([^\]]*?) else \[\]\)", re.S)


def problems(tasks, defaults):
    out = []
    if not re.search(r"(?m)^esde_collections_fightcade_entry: false$", defaults):
        out.append("default esde_collections_fightcade_entry is not false")
    m = FC_GATE.search(tasks)
    cond = " ".join(m.group(1).split()) if m else ""
    if not m:
        out.append("--fightcade-entry not found in the collgen argv")
    else:
        if "esde_collections_fightcade_entry | bool" not in cond:
            out.append("--fightcade-entry is not gated by esde_collections_fightcade_entry")
        if "esde_fightcade_bin.stat.exists" not in cond:
            out.append("--fightcade-entry no longer needs the Fightcade launcher")
    q = QUIT_GATE.search(tasks)
    if not q or "fightcade" in q.group(1):
        out.append("QUIT TO MAIN MENU must not depend on the FIGHTCADE switch")
    if not re.search(r"""if \[ \{\{ \('fightcade' in esde_expected_counts\) \| ternary\('built', 'off'\) \}\} = off \] """
                     r"""&& \[ -e "\$G/fightcade" \]; then echo "ES-DE gamelists/fightcade \(retired""", tasks):
        out.append("the plan does not list the retired gamelists/fightcade (no backup before the removal)")
    seed = re.search(r"(?ms)^    - name: Retired FIGHTCADE out of the build[^\n]*\n(.*?)(?=^    - name:)", tasks)
    if not seed or 'path: "{{ esde_coll_t.path }}/nested_out/gamelists/fightcade"' not in seed.group(1) \
            or "when: \"'fightcade' not in esde_expected_counts\"" not in seed.group(1):
        out.append("the live fightcade gamelist seeded into the build is not dropped (it would come back every deploy)")
    elif tasks.find("Retired FIGHTCADE out of the build") > tasks.find("List what would change"):
        out.append("the build drops the seeded fightcade gamelist after the plan")
    rm = re.search(r"(?ms)^    - name: Retired FIGHTCADE system out of ES-DE[^\n]*\n(.*?)(?=^    - name:)", tasks)
    if not rm:
        out.append("no task removes the retired gamelists/fightcade")
    else:
        body = rm.group(1)
        if 'path: "{{ esde_gamelists }}/fightcade"' not in body or "state: absent" not in body:
            out.append("the cleanup task does not remove {{ esde_gamelists }}/fightcade")
        if "when: \"'fightcade' not in esde_expected_counts\"" not in body:
            out.append("the cleanup task is not limited to builds without a fightcade system")
    if tasks.find("Retired FIGHTCADE system out of ES-DE") < tasks.find("Back up everything about to change"):
        out.append("the cleanup runs before the backup")
    return out


def test_role_gate():
    bad = problems(TASKS, DEFAULTS)
    check(not bad, "#219 FIGHTCADE entry off by default, gated, retired gamelist backed up + removed (%s)" % "; ".join(bad))


def test_BROKEN_role_text():
    gate = "\n                        and esde_collections_fightcade_entry | bool"
    check(gate in TASKS, "the gate line is where the broken cases expect it")
    cases = {
        "gate removed": (TASKS.replace(gate, ""), DEFAULTS),
        "default true": (TASKS, DEFAULTS.replace("esde_collections_fightcade_entry: false",
                                                 "esde_collections_fightcade_entry: true")),
        "launcher check dropped": (TASKS.replace("esde_fightcade_bin.stat.exists\n", "true\n"), DEFAULTS),
        "QUIT gated too": (TASKS.replace("'--quit-command', esde_collections_entry_command ~ ' menu'] if ",
                                         "'--quit-command', esde_collections_entry_command ~ ' menu'] if "
                                         "esde_collections_fightcade_entry and "), DEFAULTS),
        "cleanup unconditional": (re.sub(r"(?ms)(Retired FIGHTCADE system out of ES-DE.*?)      when: [^\n]*\n", r"\1",
                                         TASKS, count=1), DEFAULTS),
        "cleanup gone": (re.sub(r"(?ms)^    - name: Retired FIGHTCADE system.*?(?=^    - name:)", "", TASKS), DEFAULTS),
        "seeded gamelist kept in the build": (re.sub(r"(?ms)^    - name: Retired FIGHTCADE out of the build.*?(?=^    - name:)",
                                                     "", TASKS), DEFAULTS),
        "plan line gone": (re.sub(r"(?m)^.*retired, #219.*\n", "", TASKS), DEFAULTS),
    }
    for name, (t, d) in cases.items():
        check(t != TASKS or d != DEFAULTS, "BROKEN CASE %s: the mutation applied" % name)
        check(problems(t, d), "BROKEN CASE: %s is flagged" % name)


def _roms(base):
    roms = os.path.join(base, "roms")
    for system in os.listdir(os.path.join(FIX, "gamelists")):
        os.makedirs(os.path.join(roms, system))
        for line in open(os.path.join(FIX, "gamelists", system, "gamelist.xml"), encoding="utf-8"):
            if "<path>./" in line:
                open(os.path.join(roms, system, line.split("<path>./", 1)[1].split("</path>", 1)[0]), "w").close()
    return roms


def _build(base, out, roms, entry, fightcade):
    argv = [sys.executable, "collgen.py", "nested", "--gamelists", os.path.join(FIX, "gamelists"),
            "--picks", os.path.join(FIX, "picks.html"), "--out", out, "--min-decade", "1",
            "--roms", roms, "--rom-root", roms, "--nested-root", os.path.join(base, "cab", "roms_nested"),
            "--quit-entry", entry] + (["--fightcade-entry", entry] if fightcade else []) + ["--apply"]
    r = subprocess.run(argv, cwd=SKILL, capture_output=True, text=True)
    check(r.returncode == 0, "collgen nested %s --fightcade-entry: rc %d %s"
          % ("with" if fightcade else "without", r.returncode, r.stderr[-300:]))
    return r.stdout


def _deploy(out, cab, delete=True):
    """The role's write step: rsync -rlc --delete per generated subdir into nested_out."""
    for sub in ("roms_nested", "custom_systems", "gamelists"):
        os.makedirs(os.path.join(cab, sub), exist_ok=True)
        subprocess.run(["rsync", "-rlc"] + (["--delete"] if delete else []) + [out + "/" + sub + "/", cab + "/" + sub + "/"],
                       check=True)


def _state(cab):
    es = open(os.path.join(cab, "custom_systems", "es_systems.xml"), encoding="utf-8").read()
    return {"es_fightcade": "<name>fightcade</name>" in es, "es_quit": "<name>quit</name>" in es,
            "roms_fightcade": os.path.lexists(os.path.join(cab, "roms_nested", "fightcade")),
            "gl_fightcade": os.path.exists(os.path.join(cab, "gamelists", "fightcade")),
            "gl_quit": os.path.exists(os.path.join(cab, "gamelists", "quit"))}


def test_redeploy_without_entry_removes_fightcade():
    if shutil.which("rsync") is None:
        skip("no rsync: the deploy pipeline was not replayed")
        return
    base = tempfile.mkdtemp(prefix="hk-219-")
    try:
        roms, entry, cab = _roms(base), os.path.join(base, "esde_entry.sh"), os.path.join(base, "cab")
        open(entry, "w").write("#!/bin/bash\n")
        o1 = _build(base, os.path.join(base, "b1"), roms, entry, fightcade=True)
        _deploy(os.path.join(base, "b1"), cab)
        s1 = _state(cab)
        check(all(s1.values()) and re.search(r"(?m)^\s+fightcade\s+1 entry", o1),
              "deploy 1 (entry on, the #214 state on the cabinet): FIGHTCADE everywhere %s" % s1)
        o2 = _build(base, os.path.join(base, "b2"), roms, entry, fightcade=False)    # the role: a fresh tempfile dir
        _deploy(os.path.join(base, "b2"), cab)
        s2 = _state(cab)
        check(re.search(r"(?m)^\s+fightcade\s+none", o2) and s2 == {"es_fightcade": False, "es_quit": True,
              "roms_fightcade": False, "gl_fightcade": False, "gl_quit": True},
              "deploy 2 (entry off): FIGHTCADE gone from es_systems.xml, roms_nested, gamelists; QUIT stays %s" % s2)

        # BROKEN: reuse the old build dir and copy without --delete: the stale gamelist survives (must be caught)
        cab2 = os.path.join(base, "cab2")
        _deploy(os.path.join(base, "b1"), cab2)
        _build(base, os.path.join(base, "b1"), roms, entry, fightcade=False)
        _deploy(os.path.join(base, "b1"), cab2, delete=False)
        s3 = _state(cab2)
        check(not s3["es_fightcade"] and s3["gl_fightcade"],
              "BROKEN CASE: in-place rebuild without --delete leaves gamelists/fightcade behind %s" % s3)
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main(globals())
