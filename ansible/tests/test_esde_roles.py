#!/usr/bin/env python3
"""Policy checks for the ES-DE deploy roles (#89): esde_common, esde_theme, esde_collections, esde_verify.

Pure stdlib, rules over file TEXT, so each one also runs on a deliberately broken copy (test_broken_*):

    python3 ansible/tests/test_esde_roles.py

Rules: the theme goes as named files + _inc dirs then the personal overlay, never the whole folder (and no
overlay in EDITION=community); es_settings.xml is never touched by these roles; the game guard runs before
any ES-DE role (retroarch, retroarch-1.22, flycast); validate_nested.py gates every collections write; the
ES-DE restart is a handler only (pkill -9 -x AppRun, never in check mode) that writes notify; backups
before writes; esde_verify flushes the handler first and FAILs on a mismatch.
"""
import os
import re
import sys

ANSIBLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests", "lib"))
from hktest import check, main  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)
ESDE_ROLES = ("esde_common", "esde_theme", "esde_collections", "esde_verify")


def read(*parts):
    with open(os.path.join(ANSIBLE, *parts), encoding="utf-8") as f:
        return f.read()


def role_text(role, part):
    p = os.path.join(ANSIBLE, "roles", role, part, "main.yml")
    return read("roles", role, part, "main.yml") if os.path.isfile(p) else ""


def yaml_list(text, key):
    m = re.search(r"(?m)^%s:\n((?:[ \t]+-.*\n?)+)" % re.escape(key), text)
    return [re.sub(r"\s+#.*$", "", l.strip()[1:].strip()).strip("\"'") for l in m.group(1).splitlines()] if m else None


# ---- rules ----------------------------------------------------------------------------------------
def rule_theme(tasks, defaults):
    p = []
    dirs, files = yaml_list(defaults, "esde_theme_dirs"), yaml_list(defaults, "esde_theme_files")
    if not dirs or not files:
        return ["esde_theme_dirs / esde_theme_files missing"]
    for d in dirs:
        if d.strip("/") in ("", ".", "_inc", "_inc/art") or d.startswith("/") or ".." in d:
            p.append("whole-folder copy: %r in esde_theme_dirs" % d)
        if d.strip("/").startswith("_inc/art/backdrops"):
            p.append("_inc/art/backdrops must come from personal/themes only (repo placeholders)")
    if "theme.xml" not in files:
        p.append("theme.xml not deployed")
    # the stage script copies each named entry; never the src root itself
    if re.search(r"cp -R?a? *\{\{ *esde_theme_src[^}]*\}\}/?\.?[ \"]", tasks) or \
       re.search(r"(?m)^\s+src: *\"?\{\{ *esde_theme_src *\}\}/?\"?\s*$", tasks):
        p.append("whole-folder copy of esde_theme_src")
    if "{% for d in esde_theme_dirs %}" not in tasks or "{% for f in esde_theme_files %}" not in tasks:
        p.append("theme stage does not loop over the named files/dirs")
    m = re.search(r"\{% if coin_dojo_edition != 'community'[^%]*%\}(.*?)\{% endif %\}", tasks, re.S)
    if not m or "esde_theme_overlay" not in m.group(1):
        p.append("personal overlay not skipped in EDITION=community")
    if "esde_theme_overlay" in tasks.replace(m.group(1) if m else "", ""):
        p.append("personal overlay used outside the community guard")
    if re.search(r"(?m)^[^#\n]*rsync[^\n]*--delete", tasks):
        p.append("theme rsync with --delete (would remove cabinet files the repo does not name)")
    return p


def rule_absent(defaults, overlay_backdrops):
    """esde_theme_absent (#150): plain relative file names (no wildcard, no .., not the default or the glow files),
    and none of them may still exist in the personal overlay (it would be copied back and removed every run)."""
    p = []
    names = yaml_list(defaults, "esde_theme_absent") or []
    for n in names:
        if n.startswith("/") or ".." in n.split("/") or any(c in n for c in "*?[]{}$"):
            p.append("esde_theme_absent: %r is not a plain relative file name" % n)
        if n.startswith("_inc/art/backdrops/") and (n.endswith("/default.png") or "eye-glow" in n):
            p.append("esde_theme_absent: %r must stay" % n)
        if n.startswith("_inc/art/backdrops/") and os.path.exists(os.path.join(overlay_backdrops, os.path.basename(n))):
            p.append("esde_theme_absent: %r still exists in personal/themes" % n)
    return p


def rule_no_es_settings(texts):
    return ["es_settings touched by %s" % name for name, t in texts.items() if "es_settings" in t]


def rule_guard(site, common):
    p = []
    m = re.search(r"(?m)^esde_game_procs: *\[([^\]]*)\]", read("group_vars", "esde.yml"))
    procs = [x.strip() for x in m.group(1).split(",")] if m else []
    for proc in ("retroarch", "flycast"):
        if proc not in procs:
            p.append("guard misses %s" % proc)
    if "retroarch-1.22" not in read("group_vars", "esde.yml"):
        p.append("guard misses retroarch-1.22")
    if "pgrep" not in common or "ansible.builtin.assert" not in common or "esde_game_running.stdout" not in common:
        p.append("esde_common has no game guard (pgrep + assert)")
    if "command -v {{ esde_pgrep | quote }}" not in common or "NO PGREP" not in common:
        p.append("game guard does not fail closed without pgrep")
    order = [m.group(1) for m in re.finditer(r"role: (esde_\w+)", site)]
    if order != list(ESDE_ROLES):
        p.append("site.yml esde role order %s (want %s)" % (order, list(ESDE_ROLES)))
    m = re.search(r"role: esde_common, tags: \[([^\]]*)\]", site)
    tags = set(t.strip() for t in m.group(1).split(",")) if m else set()
    for role in ("esde_theme", "esde_collections"):
        mm = re.search(r"role: %s, tags: \[([^\]]*)\]" % role, site)
        rtags = set(t.strip() for t in mm.group(1).split(",")) if mm else {"?"}
        if not rtags <= tags:
            p.append("%s tags %s not all on esde_common (guard skipped)" % (role, sorted(rtags - tags)))
    return p


WRITE_RX = re.compile(r"(?m)^\s+notify: restart es-de|remote_src: true|- name: Ship the tools")


def rule_validator_gate(tasks):
    p = []
    i = tasks.find("validate_nested.py")
    j = tasks.find("ABORT when the validator fails")
    w = WRITE_RX.search(tasks)
    b = tasks.find("- name: Back up")
    if i < 0 or j < 0:
        return ["no validate_nested.py pre-write gate"]
    if "'RESULT: PASS' in esde_validate.stdout_lines" not in tasks or "esde_validate.rc == 0" not in tasks:
        p.append("validator result not asserted (rc 0 + RESULT: PASS)")
    if w and w.start() < j:
        p.append("a cabinet write comes before the validator gate")
    if b < 0 or (w and w.start() < b):
        p.append("collections write before its backup")
    if "--out', esde_coll_t.path" not in tasks:
        p.append("nested must build into the temp dir, not in place")
    if "gamelist_drift.py" not in tasks or "ABORT on drift" not in tasks:
        p.append("no gamelist drift check")
    return p


def rule_restart(texts, handlers, theme, coll):
    p = []
    for name, t in texts.items():
        if name != "esde_common/handlers" and re.search(r"pkill|AppRun|esde_kill_cmd", t):
            p.append("ES-DE restarted outside the handler: %s" % name)
    m = re.search(r"- name: Restart ES-DE.*?(?=\n- name:|\Z)", handlers, re.S)
    if not m or "esde_kill_cmd" not in m.group(0) or "listen: restart es-de" not in m.group(0):
        p.append("no `restart es-de` handler with esde_kill_cmd")
    elif "when: not ansible_check_mode" not in m.group(0):
        p.append("restart handler runs in check mode")
    if "pkill -9 -x AppRun" not in read("group_vars", "esde.yml"):
        p.append("esde_kill_cmd is not pkill -9 -x AppRun")
    if "systemctl reset-failed" not in handlers:
        p.append("handler does not reset a failed getty@tty1")
    for name, t in (("esde_theme", theme), ("esde_collections", coll)):
        if "notify: restart es-de" not in t:
            p.append("%s writes do not notify restart es-de" % name)
    return p


def rule_backups(theme, coll):
    p = []
    for name, t in (("esde_theme", theme), ("esde_collections", coll)):
        if "esde_backup_root" not in t or "esde_stamp" not in t:
            p.append("%s has no backup into <esde_backup_root>/<stamp>-<what>/" % name)
    if theme.find("- name: Back up") < 0 or theme.find("- name: Back up") > theme.find("- name: Sync the theme"):
        p.append("theme sync before its backup")
    return p


def rule_verify(verify, site):
    p = []
    lines = [l for l in verify.splitlines() if l.startswith("- ")]
    if "flush_handlers" not in (lines[0] if lines else "") and "flush_handlers" not in verify.split("- name:", 2)[1]:
        p.append("esde_verify does not flush the restart handler first")
    for s in ("esde_verify.py counts", "--expect", "validate_nested.py", "esde_verify.py log", "ansible.builtin.assert"):
        if s not in verify:
            p.append("esde_verify misses %s" % s)
    if not re.search(r"role: esde_verify\b.*\n\s*$", site.split("\n- name:")[-1] if "\n- name:" in site else site):
        pass
    return p


def texts():
    out = {}
    for role in ESDE_ROLES:
        for part in ("tasks", "handlers", "defaults"):
            t = role_text(role, part)
            if t:
                out["%s/%s" % (role, part)] = t
    return out


# ---- tests ------------------------------------------------------------------------------------------
def T(role, part="tasks"):
    return role_text(role, part)


def test_layout():
    for role in ESDE_ROLES:
        check(os.path.isfile(os.path.join(ANSIBLE, "roles", role, "tasks", "main.yml")), "%s/tasks/main.yml" % role)
    for f in ("roles/esde_common/handlers/main.yml", "roles/esde_common/files/esde_verify.py",
              "roles/esde_collections/files/gamelist_drift.py", "group_vars/esde.yml"):
        check(os.path.isfile(os.path.join(ANSIBLE, f)), "exists: %s" % f)
    files = []
    for role in ESDE_ROLES:
        for d, _x, fs in os.walk(os.path.join(ANSIBLE, "roles", role)):
            files += fs
    check(not any("es_settings" in f for f in files), "no es_settings file shipped in an esde role")


def test_rules_pass():
    site = read("site.yml")
    for name, r in (("theme", rule_theme(T("esde_theme"), T("esde_theme", "defaults"))),
                    ("no es_settings", rule_no_es_settings(texts())),
                    ("guard", rule_guard(site, T("esde_common"))),
                    ("validator gate", rule_validator_gate(T("esde_collections"))),
                    ("restart via handler", rule_restart(texts(), T("esde_common", "handlers"), T("esde_theme"), T("esde_collections"))),
                    ("backups", rule_backups(T("esde_theme"), T("esde_collections"))),
                    ("verify", rule_verify(T("esde_verify"), site))):
        check(r == [], "%s %s" % (name, r))


COLL = os.path.join(os.path.dirname(ANSIBLE), "skills", "collections")


def rule_tools_shipped(defaults, sources):
    """Every data file the collections tools load (a '<name>.tsv' literal) is in esde_collections_tools."""
    tools = set(yaml_list(defaults, "esde_collections_tools") or [])
    want = set()
    for text in sources:
        want |= set(re.findall(r"[\"']([\w-]+\.tsv)[\"']", text))
    p = ["%s is loaded by the tools but not shipped (esde_collections_tools)" % f for f in sorted(want - tools)]
    p += ["%s is shipped but missing in skills/collections" % f for f in sorted(tools)
          if not os.path.isfile(os.path.join(COLL, f))]
    return p


def _tool_sources():
    return [open(os.path.join(COLL, f), encoding="utf-8").read()
            for f in ("collgen.py", "nested.py", "validate_nested.py", "metadata.py", "folder_art.py")]


def test_tools_shipped():
    d = T("esde_collections", "defaults")
    r = rule_tools_shipped(d, _tool_sources())
    check(r == [], "every .tsv the collections tools load is shipped %s" % r)
    bad = d.replace("  - twin_stick.tsv", "  - nothing.txt")
    check(any("twin_stick.tsv is loaded" in x for x in rule_tools_shipped(bad, _tool_sources())),
          "BROKEN: a tool data file left out of esde_collections_tools is flagged")


def rule_personal_lists(tasks):
    """#190: the tools copy ships personal/skills/collections/<name> when it exists, never in EDITION=community."""
    bad = []
    for task in ("The repo's tools into the build dir", "Ship the tools"):     # the build dir AND the cabinet copy
        m = re.search(re.escape(task) + r".*?\n\s+src: \"([^\n]*)\"", tasks, re.S)
        src = m.group(1) if m else ""
        if not ("esde_collections_personal" in src and "coin_dojo_edition != 'community'" in src and " is file" in src):
            bad.append("%s: the copy does not prefer personal/skills/collections (or ignores EDITION=community)" % task)
    return bad


def test_personal_lists_shipped_outside_community():
    t = T("esde_collections")
    check(rule_personal_lists(t) == [], "the collections tools copy prefers the maintainer's lists in personal/ (#190)")
    bad = t.replace("coin_dojo_edition != 'community' and ", "")
    check(rule_personal_lists(bad) != [], "BROKEN: a copy that would ship personal/ lists in community is flagged")
    i = t.index("Ship the tools")
    half = t[:i] + t[i:].replace("esde_collections_personal", "esde_collections_src", 2)
    check(any(x.startswith("Ship the tools") for x in rule_personal_lists(half)),
          "BROKEN: the cabinet copy shipping the examples (non-idempotent deploy) is flagged")
    check("esde_collections_personal:" in T("esde_collections", "defaults"), "esde_collections_personal has a default")


def test_broken_whole_theme_copy():
    t, d = T("esde_theme"), T("esde_theme", "defaults")
    bad_d = d.replace("  - _inc/help\n", "  - _inc\n")
    check(any("whole-folder copy" in x for x in rule_theme(t, bad_d)), "BROKEN: _inc as a named dir is flagged")
    bad_d = d.replace("  - _inc/art/frames\n", "  - _inc/art\n")
    check(any("whole-folder copy" in x for x in rule_theme(t, bad_d)), "BROKEN: _inc/art as a named dir is flagged")
    bad_d = d.replace("  - _inc/art/frames\n", "  - _inc/art/backdrops\n")
    check(any("personal/themes only" in x for x in rule_theme(t, bad_d)), "BROKEN: repo backdrops (placeholders) flagged")
    bad_t = t.replace("cd {{ esde_theme_src | quote }}", "cp -R {{ esde_theme_src | quote }}/. \"$S/\"\n          cd /")
    check("whole-folder copy of esde_theme_src" in rule_theme(bad_t, d), "BROKEN: cp -R of the theme root is flagged")
    bad_t = t + "\n- name: planted\n  ansible.builtin.copy:\n    src: \"{{ esde_theme_src }}/\"\n    dest: x\n"
    check("whole-folder copy of esde_theme_src" in rule_theme(bad_t, d), "BROKEN: copy src of the theme root is flagged")
    bad_t = t.replace("{% if coin_dojo_edition != 'community' and", "{% if true or").replace("{% if coin_dojo_edition != 'community' %}", "{% if true %}")
    check("personal overlay not skipped in EDITION=community" in rule_theme(bad_t, d), "BROKEN: overlay in community flagged")
    bad_t = t.replace("rsync -rc {{ '--dry-run'", "rsync -rc --delete {{ '--dry-run'")
    check(any("--delete" in x for x in rule_theme(bad_t, d)), "BROKEN: theme rsync --delete is flagged")


def test_absent_backdrops():
    d = T("esde_theme", "defaults")
    ob = os.path.join(os.path.dirname(ANSIBLE), "personal", "themes", "coin-dojo", "_inc", "art", "backdrops")
    check(rule_absent(d, ob) == [], "retired backdrops are plain names, gone from personal/themes %s" % rule_absent(d, ob))
    check(len([n for n in yaml_list(d, "esde_theme_absent") if "/backdrops/" in n]) == 13, "13 retired backdrops are listed")
    bad = d.replace("  - _inc/art/backdrops/snes.png\n", "  - _inc/art/backdrops/*.png\n")
    check(any("plain relative" in x for x in rule_absent(bad, ob)), "BROKEN: a wildcard in esde_theme_absent is flagged")
    bad = d.replace("  - _inc/art/backdrops/snes.png\n", "  - _inc/art/backdrops/default.png\n")
    check(any("must stay" in x for x in rule_absent(bad, ob)), "BROKEN: removing default.png is flagged")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, "snes.png"), "wb").close()
        check(any("still exists" in x for x in rule_absent(d, tmp)), "BROKEN: a listed file still in the overlay is flagged")


def test_broken_es_settings_copied():
    tx = texts()
    tx["esde_theme/tasks"] += "\n- name: planted\n  ansible.builtin.copy:\n    src: es_settings.xml\n    dest: \"{{ esde_home }}/settings/es_settings.xml\"\n"
    check(rule_no_es_settings(tx) == ["es_settings touched by esde_theme/tasks"], "BROKEN: es_settings copy in an esde role is flagged")


def test_broken_no_game_guard():
    site, common = read("site.yml"), T("esde_common")
    open_ = common.replace('echo "NO PGREP: cannot tell whether a game runs"; ', "")
    check("game guard does not fail closed without pgrep" in rule_guard(site, open_), "BROKEN: guard that passes without pgrep is flagged")
    bad = common.replace("ansible.builtin.assert", "ansible.builtin.debug")
    check("esde_common has no game guard (pgrep + assert)" in rule_guard(site, bad), "BROKEN: guard without assert is flagged")
    bad_site = site.replace("    - { role: esde_common, tags: [esde, esde_theme, collections, gamelists, esde_verify] }\n", "")
    check(any("order" in x for x in rule_guard(bad_site, common)), "BROKEN: esde_common dropped from site.yml is flagged")
    bad_site = site.replace("role: esde_common, tags: [esde, esde_theme, collections,", "role: esde_common, tags: [esde, esde_theme,")
    check(any("not all on esde_common" in x for x in rule_guard(bad_site, common)), "BROKEN: guard skipped for TAGS=collections flagged")
    moved = site.replace("    - { role: esde_common, tags: [esde, esde_theme, collections, gamelists, esde_verify] }\n", "")
    moved = moved.replace("    - { role: esde_collections,", "    - { role: esde_common, tags: [esde, esde_theme, collections, gamelists, esde_verify] }\n    - { role: esde_collections,")
    check(any("order" in x for x in rule_guard(moved, common)), "BROKEN: guard after the theme role is flagged")


def test_broken_no_validator_gate():
    t = T("esde_collections")
    i = t.index("    - name: Pre-write gate - validate_nested.py")
    j = t.index("    - name: Game counts the build makes")
    check("no validate_nested.py pre-write gate" in rule_validator_gate(t[:i] + t[j:]), "BROKEN: validator gate removed is flagged")
    weak = t.replace("          - \"'RESULT: PASS' in esde_validate.stdout_lines\"\n", "")
    check(any("not asserted" in x for x in rule_validator_gate(weak)), "BROKEN: validator result not asserted is flagged")
    early = t.replace("    - name: Cabinet build dir\n",
                      "    - name: planted early write\n      ansible.builtin.copy:\n        src: x\n        dest: y\n        remote_src: true\n      notify: restart es-de\n\n    - name: Cabinet build dir\n", 1)
    check("a cabinet write comes before the validator gate" in rule_validator_gate(early), "BROKEN: write before the gate is flagged")
    inplace = t.replace("'--out', esde_coll_t.path ~ '/nested_out'", "'--out', esde_nested_out")
    check(any("temp dir" in x for x in rule_validator_gate(inplace)), "BROKEN: nested --apply in place is flagged")


def test_broken_restart_not_via_handler():
    tx = texts()
    bad = dict(tx)
    bad["esde_collections/tasks"] += "\n- name: planted\n  ansible.builtin.shell: pkill -9 -x AppRun\n"
    check(any("outside the handler" in x for x in rule_restart(bad, T("esde_common", "handlers"), T("esde_theme"), T("esde_collections"))),
          "BROKEN: pkill in a task (not the handler) is flagged")
    h = T("esde_common", "handlers")
    m = re.search(r"- name: Restart ES-DE.*?(?=\n- name:|\Z)", h, re.S).group(0)
    h_check = h.replace(m, re.sub(r"  when: not ansible_check_mode[^\n]*\n", "", m))   # #209: the line may go on
    check("restart handler runs in check mode" in rule_restart(tx, h_check, T("esde_theme"), T("esde_collections")),
          "BROKEN: restart handler without the check-mode guard is flagged")
    no_notify = T("esde_theme").replace("notify: restart es-de", "notify: nothing")
    check("esde_theme writes do not notify restart es-de" in rule_restart(tx, h, no_notify, T("esde_collections")),
          "BROKEN: theme writes without notify are flagged")
    check("handler does not reset a failed getty@tty1" in rule_restart(tx, h.replace("systemctl reset-failed", "true"), T("esde_theme"), T("esde_collections")),
          "BROKEN: no getty reset-failed is flagged")


def test_broken_backups_and_verify():
    th, co = T("esde_theme"), T("esde_collections")
    check(any("no backup" in x for x in rule_backups(th.replace("esde_backup_root", "x"), co)), "BROKEN: theme without backup is flagged")
    i = th.index("    - name: Back up the theme files")
    j = th.index("    - name: Sync the theme")
    moved = th[:i] + th[j:]
    k = moved.index("    - name: Remove retired theme files")
    moved = moved[:k] + th[i:j] + moved[k:]
    check("theme sync before its backup" in rule_backups(moved, co), "BROKEN: backup after the sync is flagged")
    v = T("esde_verify")
    check(any("flush" in x for x in rule_verify(v.replace("ansible.builtin.meta: flush_handlers", "ansible.builtin.debug: {}"), read("site.yml"))),
          "BROKEN: verify without flush_handlers is flagged")
    check(any("assert" in x for x in rule_verify(v.replace("ansible.builtin.assert", "ansible.builtin.debug"), read("site.yml"))),
          "BROKEN: verify that never fails is flagged")


def rule_notify_has_handler(playbook, notifiers):
    """#106: every play that runs a role notifying `restart es-de` also runs esde_common (the handler's home)."""
    bad = []
    for i, play in enumerate(re.split(r"(?m)^- (?=name:|hosts:)", playbook)[1:]):
        roles = set(re.findall(r"role:\s*([A-Za-z0-9_]+)", play))
        hit = sorted(roles & set(notifiers))
        if hit and "esde_common" not in roles:
            bad.append("play %d runs %s (notify restart es-de) without esde_common" % (i + 1, ", ".join(hit)))
    return bad


def _notifiers():
    out = []
    for r in sorted(os.listdir(os.path.join(ANSIBLE, "roles"))):
        if re.search(r"notify:\s*restart es-de", role_text(r, "tasks")):
            out.append(r)
    return out


def test_notifying_roles_have_the_handler():
    n = _notifiers()
    check({"cabinet_tweaks", "esde_theme", "esde_collections"} <= set(n), "notifier roles found: %s" % ", ".join(n))
    for pb in ("site.yml", "vm.yml"):
        check(rule_notify_has_handler(read(pb), n) == [], "%s: every play with a restart es-de notifier has esde_common" % pb)
    inv = read("inventory", "hosts.yml")
    check(re.search(r"(?m)^    esde:\n      children:\n        pios:\n        vm:", inv) is not None,
          "inventory: group esde = pios + vm")
    check(os.path.isfile(os.path.join(ANSIBLE, "group_vars", "esde.yml")) and not os.path.exists(os.path.join(ANSIBLE, "group_vars", "pios.yml")),
          "group_vars/esde.yml exists, pios.yml is gone")


def test_broken_notify_without_handler():
    n = _notifiers()
    planted = "- name: planted\n  hosts: vm\n  roles:\n    - { role: cabinet_tweaks, tags: [x] }\n"
    check(any("without esde_common" in x for x in rule_notify_has_handler(planted, n)), "BROKEN: a play with a notifier and no esde_common is flagged")
    vm = read("vm.yml").replace("role: esde_common", "role: esde_other")
    check(rule_notify_has_handler(vm, n) != [], "BROKEN: vm.yml without esde_common is flagged")


# ---- the eye glow follows the art (#263) --------------------------------------------------------------
GUARD = re.compile(r'(?m)^\s*(if \[ -d "\$A/backdrops" \] && \[ ! -f "\$A/backdrops/eye-glow\.xml" \]; then rm -f "\$S"/_inc/art/backdrops/eye-glow\.\*; fi)')


def rule_art_glow(tasks):
    """Own art (esde_theme_art_dir) without its own eye-glow.xml must not get the night art's glow drawn on it."""
    m = GUARD.search(tasks)
    if not m:
        return ["theme staging: no guard that removes the staged eye-glow.* when the art dir has no eye-glow.xml"]
    if tasks.index(m.group(1)) < tasks.index('cp -R "$A/backdrops/." '):
        return ["theme staging: the glow guard runs before the art copy (it must run after it)"]
    return []


def run_guard(files_in_art, art_dir_exists=True):
    """Run the real guard line on a staged dir holding the night art's glow, with an art dir holding files_in_art."""
    import subprocess
    import tempfile
    line = GUARD.search(role_text("esde_theme", "tasks")).group(1)
    with tempfile.TemporaryDirectory() as d:
        s_back = os.path.join(d, "S", "_inc", "art", "backdrops")
        os.makedirs(s_back)
        for ext in ("png", "json", "xml"):
            open(os.path.join(s_back, "eye-glow." + ext), "w").write("night")
        a = os.path.join(d, "A")
        if art_dir_exists:
            os.makedirs(os.path.join(a, "backdrops"))
        for name in files_in_art:
            open(os.path.join(a, "backdrops", name), "w").write("own")
            if name.startswith("eye-glow."):
                open(os.path.join(s_back, name), "w").write("own")          # the art copy that ran before the guard
        r = subprocess.run(["sh", "-c", line], env=dict(os.environ, A=a, S=os.path.join(d, "S")), capture_output=True, text=True)
        return r.returncode, sorted(n for n in os.listdir(s_back) if n.startswith("eye-glow."))


def test_own_art_without_a_glow_gets_none():
    check(rule_art_glow(role_text("esde_theme", "tasks")) == [], "the role drops the staged glow for art without one")
    rc, left = run_guard(["default.png"])
    check(rc == 0 and left == [], "own art with only default.png: no eye-glow files staged (%s)" % left)
    rc, left = run_guard(["default.png", "cps2.png"])
    check(rc == 0 and left == [], "own art with per-system backdrops and no glow: none staged (%s)" % left)


def test_own_art_with_a_glow_keeps_it():
    rc, left = run_guard(["default.png", "eye-glow.png", "eye-glow.json", "eye-glow.xml"])
    check(rc == 0 and left == ["eye-glow.json", "eye-glow.png", "eye-glow.xml"], "own eye-glow files are used (%s)" % left)
    rc, left = run_guard([], art_dir_exists=False)
    check(rc == 0 and len(left) == 3, "no backdrops folder in the art dir: the theme keeps its own glow (%s)" % left)


def test_broken_glow_guard_missing_or_early():
    tasks = role_text("esde_theme", "tasks")
    gone = GUARD.sub("", tasks)
    check(rule_art_glow(gone) != [], "BROKEN: a role without the glow guard is reported")
    line = GUARD.search(tasks).group(1)
    early = tasks.replace("          " + line + "\n", "").replace('          if [ -d "$A/backdrops" ]; then cp -R', "          " + line + '\n          if [ -d "$A/backdrops" ]; then cp -R', 1)
    check(rule_art_glow(early) != [], "BROKEN: a guard that runs before the art copy is reported")


if __name__ == "__main__":
    main(globals())
