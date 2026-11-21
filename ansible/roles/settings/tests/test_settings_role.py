#!/usr/bin/env python3
"""Structure + privilege tests for the `settings` role (#34/#35). Pure stdlib; pytest-compatible or direct:

    python3 ansible/roles/settings/tests/test_settings_role.py

Rules (each also run on a deliberately broken copy that MUST be flagged):
  sudoers   exactly one rule: coindojo, runas root, NOPASSWD, the helper's absolute path + an anchored RFC 1123 regex;
            no ALL as command, no '*' wildcard, no second command; visudo validate; mode 0440 root:root
  polkit    only user coindojo, only local && active, only wifi.scan / network-control / settings.modify.system and
            (#206) timedate1.set-timezone;
            no prefix match (indexOf / startsWith / regex) and no unconditional YES
  helper    root:root 0755 at a fixed /usr/local/sbin path
  fake      fake_nmcli.py is installed only when settings_fake_nmcli (the VM), and removed otherwise
  carousel  the standalone es_systems template is the same <system> as nested.systems_xml, its gamelist the same
            as nested.settings_gamelist_xml; it never replaces an es_systems.xml that is not ours
  wiring    site.yml runs settings before esde_common; vm.yml has it; esde_collections passes --settings-entry only
            when the entry file exists; the VM switches are only in inventory/vm.yml
The sudoers regex is also tried on names: the same accept/reject as the app and the helper (via re, POSIX-ERE subset).
"""
import os
import re
import sys

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANS = os.path.dirname(os.path.dirname(ROLE))
REPO = os.path.dirname(ANS)
sys.path.insert(0, os.path.join(REPO, "skills", "collections"))
sys.path.insert(0, os.path.join(REPO, "skills", "settings"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "tests", "lib"))
from hktest import check, main, skip  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)
sys.path.insert(0, os.path.join(ANS, "filter_plugins"))
import hk_settings_state as hkstate  # noqa: E402  (#197: the roles' one parser of the state file)


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


DEFAULTS = read(ROLE, "defaults", "main.yml")
TASKS = read(ROLE, "tasks", "main.yml")
POLKIT = re.sub(r"(?m)^\{% (if|endif).*%\}\n?", "", read(ROLE, "templates", "50-coin-dojo-settings.rules.j2")
                ).replace("{{ settings_user }}", "coindojo")


def sudoers_line(defaults):
    m = re.search(r'(?m)^settings_sudoers_line:\s*"(.*)"\s*$', defaults)
    if not m:
        return None
    line = m.group(1).replace("\\\\", "\\")                                # YAML double-quoted escapes
    line = line.replace("{{ settings_user }}", "coindojo").replace("{{ settings_helper_dest }}",
                                                                  "/usr/local/sbin/coin-dojo-set-hostname")
    return line


def rule_sudoers(defaults, tasks):
    p = []
    line = sudoers_line(defaults)
    want = re.compile(r"^coindojo ALL=\(root\) NOPASSWD: /usr/local/sbin/coin-dojo-set-hostname \^\S+\$$")
    if not line or not want.match(line):
        p.append("sudoers line is not `coindojo ALL=(root) NOPASSWD: <helper> ^regex$`: %r" % line)
    elif re.search(r"NOPASSWD:\s*ALL|\(ALL\)|\*|,\s*/|\bsh\b|hostnamectl", line.replace("\\,", "")):
        p.append("sudoers line too broad (ALL / wildcard / second command / a shell / hostnamectl itself)")
    blk = tasks[tasks.find("- name: sudoers"):]
    blk = blk[:blk.find("\n    - name:", 10)]
    if "validate: /usr/sbin/visudo -cf %s" not in blk:
        p.append("sudoers task has no visudo validate")
    if 'mode: "0440"' not in blk or "owner: root" not in blk:
        p.append("sudoers file is not root 0440")
    return p


def sudoers_regex(defaults):
    line = sudoers_line(defaults)
    rx = line.split(" ", 4)[4].replace("\\,", ",")
    return re.compile(rx)


def rule_polkit(text):
    p = []
    ids = set(re.findall(r'action\.id\s*==\s*"([^"]+)"', text))
    if ids != {"org.freedesktop.NetworkManager.wifi.scan", "org.freedesktop.NetworkManager.network-control",
               "org.freedesktop.NetworkManager.settings.modify.system", "org.freedesktop.timedate1.set-timezone"}:
        p.append("polkit action ids are not exactly scan / network-control / settings.modify.system / "
                 "timedate1.set-timezone (#206): %s" % sorted(ids))
    code = re.sub(r"//[^\n]*", "", text)
    if re.search(r"indexOf|startsWith|\.match\(|\.test\(|RegExp|/\^org", code):
        p.append("polkit rule matches action ids by prefix/regex")
    if (code.count('subject.user == "coindojo"') != code.count("polkit.addRule(") or code.count("subject.local") != code.count("polkit.addRule(")
            or code.count("subject.active") != code.count("polkit.addRule(")):
        p.append("polkit rule is not limited to coindojo in a local, active session")
    if code.count("polkit.Result.YES") != code.count("polkit.addRule(") or re.search(r"addRule\(function\([^)]*\)\s*\{\s*return", code):
        p.append("polkit rule returns YES unconditionally or more than once")
    if "isInGroup" in code:
        p.append("polkit rule grants by group (wider than the one user)")
    return p


def test_sudoers():
    probs = rule_sudoers(DEFAULTS, TASKS)
    check(not probs, "sudoers: one command, one argument shape, visudo-validated, 0440 %s" % (probs or ""))


def test_sudoers_regex_matches_the_app_rules():
    import settings_core as core
    rx = sudoers_regex(DEFAULTS)
    for name in ("coin-dojo", "a", "cab-1", "x" * 63, "", "-a", "a-", "A", "a_b", "x" * 64, "a b", "a;b", "a\n"):
        app_ok = core.hostname_error(name) is None
        check(bool(rx.fullmatch(name)) == app_ok, "sudoers regex and the app agree on %r (%s)" % (name[:16], app_ok))


def test_broken_sudoers_cases():
    for bad, what in (('settings_sudoers_line: "coindojo ALL=(ALL) NOPASSWD: ALL"', "ALL"),
                      ('settings_sudoers_line: "{{ settings_user }} ALL=(root) NOPASSWD: {{ settings_helper_dest }} *"', "wildcard"),
                      ('settings_sudoers_line: "{{ settings_user }} ALL=(root) NOPASSWD: /usr/bin/hostnamectl ^x$"', "hostnamectl itself"),
                      ('settings_sudoers_line: "{{ settings_user }} ALL=(root) NOPASSWD: {{ settings_helper_dest }} ^x$, /bin/sh"', "second command")):
        d = re.sub(r"(?m)^settings_sudoers_line:.*$", lambda _m: bad, DEFAULTS)
        check(rule_sudoers(d, TASKS) != [], "BROKEN CASE: sudoers %s is flagged" % what)
    check(rule_sudoers(DEFAULTS, TASKS.replace("validate: /usr/sbin/visudo -cf %s", "")) != [], "BROKEN CASE: no visudo validate is flagged")


# ---- #281: GAME LISTS --------------------------------------------------------------------------------------------
LISTS_KEYS = ("edition", "tools", "gamelists", "custom_systems", "roms", "media", "nested_out", "nested_root", "launcher", "picks",
              "picks_example", "dat", "folder_art", "settings_entry", "settings_command", "quit_entry", "quit_command",
              "virtual_systems", "state", "backup_root", "run_dir", "game_procs", "game_patterns")


def rule_lists(defaults, tasks):
    p = []
    m = re.search(r"(?ms)^settings_lists:.*?(?=^\S)", defaults)
    blk = m.group(0) if m else ""
    for k in LISTS_KEYS:
        if not re.search(r"(?m)^  %s: " % k, blk):
            p.append("settings_lists has no `%s`" % k)
    if "edition: \"{{ coin_dojo_edition | default('personal') }}\"" not in blk:
        p.append("the rebuild's edition is not coin_dojo_edition (the personal edition must refuse)")
    if "'lists': settings_lists" not in tasks:
        p.append("the entry file does not carry the lists settings")
    if "lists_rebuild.py" not in defaults.split("settings_app_files:", 1)[1].split("\n", 1)[0]:
        p.append("lists_rebuild.py is not an app file (staged, compiled, installed)")
    if "picks_example.html" not in defaults:
        p.append("the example picks file is not shipped")
    return p


def test_game_lists_wiring():
    check(rule_lists(DEFAULTS, TASKS) == [], "GAME LISTS: lists_rebuild.py installed, the entry carries the paths + edition %s" % (rule_lists(DEFAULTS, TASKS) or ""))
    check(rule_lists(DEFAULTS.replace("  edition: \"{{ coin_dojo_edition | default('personal') }}\"", "  edition: community"), TASKS) != [],
          "BROKEN CASE: a hard-coded community edition is flagged")
    check(rule_lists(DEFAULTS, TASKS.replace("'lists': settings_lists", "")) != [], "BROKEN CASE: an entry without the lists settings is flagged")
    check(rule_lists(DEFAULTS.replace("game_procs:", "x:"), TASKS) != [], "BROKEN CASE: a missing key is flagged")
    check("esde_kill" not in DEFAULTS, "no kill command in the config: it is fixed in the helpers")


# ---- #271: the PASSWORD helper -------------------------------------------------------------------------------
PW_HELPER = "/usr/local/sbin/coin-dojo-set-password"


def password_sudoers_line(defaults):
    m = re.search(r"(?m)^settings_password_sudoers_line:\s*'(.*)'(?:\s+#.*)?\s*$", defaults)
    if not m:
        return None
    return m.group(1).replace("{{ settings_user }}", "coindojo").replace("{{ settings_password_helper_dest }}", PW_HELPER)


def rule_password(defaults, tasks):
    p = []
    line = password_sudoers_line(defaults)
    if line != 'coindojo ALL=(root) NOPASSWD: %s ""' % PW_HELPER:
        p.append("password sudoers line is not `coindojo ALL=(root) NOPASSWD: <helper> \"\"` (the empty-argument form): %r" % line)
    if "settings_password_helper_dest: %s" % PW_HELPER not in defaults:
        p.append("the password helper path is not the fixed /usr/local/sbin one")
    if "{{ settings_password_sudoers_line }}" not in tasks[tasks.find("- name: sudoers"):]:
        p.append("the password line is not in the visudo-validated sudoers file")
    blk = tasks[tasks.find("- name: Root helper for the cabinet password"):]
    blk = blk[:blk.find("\n    - name:", 10)]
    if 'mode: "0755"' not in blk or "owner: root" not in blk or "group: root" not in blk:
        p.append("the password helper is not installed root:root 0755")
    if "coin_dojo_set_password.py" not in tasks.split("- name: Stage dirs", 1)[0]:
        p.append("the password helper is not staged")
    if tasks.count("coin_dojo_set_password.py") < 3:
        p.append("the password helper is not staged, compiled and installed")
    if "'password_helper': settings_password_helper_dest" not in tasks:
        p.append("the entry file does not name the password helper")
    return p


def test_password_helper_sudoers_and_install():
    probs = rule_password(DEFAULTS, TASKS)
    check(not probs, "password helper: no-argument sudoers line, root 0755, staged + compiled %s" % (probs or ""))
    check(rule_sudoers(DEFAULTS, TASKS) == [], "the hostname rule is untouched by the password line")


def test_broken_password_cases():
    ok = DEFAULTS
    for bad, what in (("settings_password_sudoers_line: '{{ settings_user }} ALL=(root) NOPASSWD: {{ settings_password_helper_dest }}'", "no empty-argument form (any args would pass)"),
                      ("settings_password_sudoers_line: '{{ settings_user }} ALL=(root) NOPASSWD: {{ settings_password_helper_dest }} *'", "a wildcard"),
                      ("settings_password_sudoers_line: '{{ settings_user }} ALL=(ALL) NOPASSWD: ALL'", "ALL"),
                      ("settings_password_sudoers_line: '{{ settings_user }} ALL=(root) NOPASSWD: /usr/bin/passwd \"\"'", "passwd itself"),
                      ("settings_password_sudoers_line: '{{ settings_user }} ALL=(root) NOPASSWD: {{ settings_password_helper_dest }} \"\", /bin/sh'", "a second command")):
        d = re.sub(r"(?m)^settings_password_sudoers_line:.*$", lambda _m: bad, ok)
        check(rule_password(d, TASKS) != [], "BROKEN CASE: password sudoers %s is flagged" % what)
    check(rule_password(DEFAULTS.replace("settings_password_helper_dest: /usr/local/sbin/coin-dojo-set-password", "settings_password_helper_dest: /tmp/x"), TASKS) != [],
          "BROKEN CASE: a helper outside /usr/local/sbin is flagged")
    check(rule_password(DEFAULTS, TASKS.replace("{{ settings_password_sudoers_line }}", "")) != [], "BROKEN CASE: no password line in the sudoers file is flagged")
    check(rule_password(DEFAULTS, TASKS.replace('mode: "0755"', 'mode: "0777"')) != [], "BROKEN CASE: a world-writable helper is flagged")
    check(rule_password(DEFAULTS, TASKS.replace("coin_dojo_set_password.py", "x.py")) != [], "BROKEN CASE: a helper that is not staged and compiled is flagged")


def rule_root_helpers_from_controller(tasks):
    """#270 L7: each root helper is copied from the controller straight to its place as root: src is the repo file,
    no remote_src (the /tmp stage belongs to the connecting user, who could swap a staged file before a remote copy)."""
    p = []
    for title, name in (("Root helper for the cabinet name", "coin_dojo_set_hostname.py"),
                        ("Root helper for the cabinet password", "coin_dojo_set_password.py")):
        i = tasks.find("- name: " + title)
        if i < 0:
            p.append("no task %r" % title)
            continue
        blk = tasks[i:]
        blk = blk[:blk.find("\n    - name:", 10)] if blk.find("\n    - name:", 10) > 0 else blk
        if 'src: "{{ settings_src }}/%s"' % name not in blk:
            p.append("%s is not copied from the controller ({{ settings_src }})" % name)
        if "remote_src" in blk or "settings_stage" in blk:
            p.append("%s goes through the user-owned stage (remote_src)" % name)
        if "become: true" not in blk:
            p.append("%s is not copied as root" % name)
    return p


def test_root_helpers_copied_from_the_controller():
    check(rule_root_helpers_from_controller(TASKS) == [], "both root helpers from the controller, as root %s" % rule_root_helpers_from_controller(TASKS))
    bad = TASKS.replace('        src: "{{ settings_src }}/coin_dojo_set_password.py"\n',
                        '        src: "{{ settings_stage.path }}/coin_dojo_set_password.py"\n        remote_src: true\n')
    check(bad != TASKS and rule_root_helpers_from_controller(bad) != [], "BROKEN CASE: the password helper from the /tmp stage is flagged")
    bad = TASKS.replace('        src: "{{ settings_src }}/coin_dojo_set_hostname.py"\n',
                        '        src: "{{ settings_stage.path }}/coin_dojo_set_hostname.py"\n        remote_src: true\n')
    check(bad != TASKS and rule_root_helpers_from_controller(bad) != [], "BROKEN CASE: the hostname helper from the /tmp stage is flagged")


def test_polkit():
    probs = rule_polkit(POLKIT)
    check(not probs, "polkit: coindojo, local+active, three NetworkManager actions only %s" % (probs or ""))


def test_timezone_polkit_rule_is_one_action():
    """#206: the time zone rule grants set-timezone only: not set-time, set-ntp, set-local-rtc, and not the whole interface."""
    check(POLKIT.count("org.freedesktop.timedate1.") == 1 and "org.freedesktop.timedate1.set-timezone" in POLKIT,
          "polkit names exactly one timedate1 action: set-timezone")
    for wider in ("org.freedesktop.timedate1.set-time", "org.freedesktop.timedate1.set-ntp",
                  "org.freedesktop.timedate1.set-local-rtc"):
        check(rule_polkit(POLKIT.replace("org.freedesktop.timedate1.set-timezone", wider)) != [],
              "BROKEN CASE: polkit granting %s instead is flagged" % wider.rsplit(".", 1)[1])
    check(rule_polkit(POLKIT.replace('action.id == "org.freedesktop.timedate1.set-timezone"',
                                     'action.id.indexOf("org.freedesktop.timedate1.") == 0')) != [],
          "BROKEN CASE: polkit granting the whole timedate1 interface (prefix) is flagged")
    check(rule_polkit(POLKIT.replace("""subject.user == "coindojo" && subject.local && subject.active &&
        action.id == "org.freedesktop.timedate1""", """action.id == "org.freedesktop.timedate1""")) != [],
          "BROKEN CASE: the time zone rule without coindojo + local + active is flagged")
    check("settings_polkit_timezone" in read(ROLE, "templates", "50-coin-dojo-settings.rules.j2"), "the rule can be switched off")


def test_broken_polkit_cases():
    allnm = POLKIT.replace('(action.id == "org.freedesktop.NetworkManager.wifi.scan" ||',
                           '(action.id.indexOf("org.freedesktop.NetworkManager.") == 0 ||')
    check(rule_polkit(allnm) != [], "BROKEN CASE: polkit granting every NetworkManager action (prefix) is flagged")
    check(rule_polkit('polkit.addRule(function(action, subject) { return polkit.Result.YES; });') != [],
          "BROKEN CASE: polkit granting all actions is flagged")
    check(rule_polkit(POLKIT.replace('subject.user == "coindojo" && ', 'subject.isInGroup("users") && ')) != [],
          "BROKEN CASE: polkit for a whole group is flagged")
    check(rule_polkit(POLKIT.replace("subject.local && subject.active &&", "")) != [],
          "BROKEN CASE: polkit without local+active (e.g. over SSH) is flagged")


def test_polkit_user_is_templated():
    tpl = read(ROLE, "templates", "50-coin-dojo-settings.rules.j2")
    check('subject.user == "{{ settings_user }}"' in tpl and '"coindojo"' not in tpl, "polkit user comes from settings_user")
    check("src: 50-coin-dojo-settings.rules.j2" in TASKS and "ansible.builtin.template:\n        src: 50-coin-dojo-settings" in TASKS,
          "polkit rule installed with the template module")


def test_owners():
    if not os.path.isfile(os.path.join(REPO, ".claude", "team", "owners.tsv")):
        skip("owners.tsv: no .claude/team/owners.tsv here (community export, #146)")
        return
    rows = [ln.split("\t") for ln in read(REPO, ".claude", "team", "owners.tsv").splitlines() if ln and not ln.startswith("#")]
    globs = [r[0] for r in rows]
    own = dict((r[0], r[1]) for r in rows)
    check(own.get("skills/settings/**") == "design" and own.get("ansible/roles/settings/**") == "design"
          and own.get("brand/**") == "design", "owners.tsv: skills/settings, roles/settings, brand -> design")
    check(globs.index("ansible/roles/settings/**") < globs.index("ansible/**")
          and globs.index("skills/settings/**") < globs.index("skills/**"), "owners.tsv: the specific lines come first")


def test_helper_install():
    blk = TASKS[TASKS.find("- name: Root helper"):]
    blk = blk[:blk.find("\n    - name:", 10)]
    check("owner: root" in blk and "group: root" in blk and 'mode: "0755"' in blk, "helper installed root:root 0755")
    check(re.search(r'(?m)^settings_helper_dest: "\{\{ hk_settings_helper_dest \}\}"$', DEFAULTS) is not None,
          "helper path comes from the declared hk_settings_helper_dest (default under /usr/local/sbin: test_settings_config.py)")


def test_fake_nmcli_only_on_the_vm():
    check(re.search(r"(?m)^settings_fake_nmcli: false\b", DEFAULTS) is not None, "fake nmcli off by default")
    check("(['fake_nmcli.py'] if settings_fake_nmcli else [])" in TASKS, "fake nmcli installed only with settings_fake_nmcli")
    check("when: not settings_fake_nmcli and settings_dir_ready" in TASKS, "fake nmcli removed when the switch is off")
    check(len(re.findall(r"(?m)when: settings_dir_ready \| bool$", TASKS)) == 4, "first-install dry run skips only the copies into the app dir (app files, data files, kit, entry file)")
    check(TASKS.count("when: settings_dir_ready | bool and trophies_enable") == 1
          and TASKS.count("when: settings_dir_ready | bool and not (trophies_enable") == 1,
          "#176: the TROPHIES entry file (write / remove) is gated like the other app-dir writes")
    check(not re.search(r"(?m)^(\s+)when:.*\n\1when:", TASKS), "no task with two when: keys")
    inv = read(ANS, "inventory", "vm.yml")
    check("settings_fake_nmcli: true" in inv, "the VM inventory switches it on")
    for f in os.listdir(os.path.join(ANS, "inventory")):
        if f != "vm.yml":
            check("settings_fake_nmcli" not in read(ANS, "inventory", f), "no fake nmcli switch in inventory/%s" % f)
    check("settings_fake_nmcli" not in read(ANS, "group_vars", "esde.yml"), "no fake nmcli switch in group_vars/esde.yml")


def test_carousel_matches_the_nested_build():
    import nested
    tpl = read(ROLE, "templates", "es_systems_standalone.xml.j2")
    blk = re.search(r"  <system>.*?</system>", tpl, re.S).group(0)
    blk = blk.replace("{{ settings_carousel_root }}", "/data/nested").replace("{{ settings_command }}", nested.SETTINGS_COMMAND)
    ref = re.search(r"  <system>\n    <name>settings</name>.*?</system>",
                    nested.systems_xml({"systems": {}}, "/data/nested", "python3 l.py", nested.SETTINGS_COMMAND), re.S).group(0)
    norm = lambda s: re.sub(r"<systemsortname>\d+-", "<systemsortname>N-", s)  # noqa: E731
    check(norm(blk) == norm(ref), "standalone es_systems <system> == nested.systems_xml's SETTINGS block")
    check(read(ROLE, "files", "settings_gamelist.xml") == nested.settings_gamelist_xml({}), "standalone gamelist == nested's")
    check("coin-dojo-settings-standalone" in tpl and "'coin-dojo-settings-standalone' in" in TASKS,
          "standalone mode never replaces an es_systems.xml it did not write")
    check(re.search(r"(?m)^settings_standalone_carousel: false\b", DEFAULTS) is not None, "standalone carousel off by default")
    check(re.search(r'(?m)^settings_command: "python3 \{\{ settings_dir \}\}/settings_app.py"', DEFAULTS) is not None
          and "/data/coin-dojo/settings/settings_app.py" in nested.SETTINGS_COMMAND
          and re.search(r'(?m)^settings_dir: "\{\{ hk_tools_dir \}\}/settings"$', DEFAULTS) is not None,
          "role command == nested.SETTINGS_COMMAND path")


def test_wiring():
    site = read(ANS, "site.yml")
    check(site.find("role: settings") != -1 and site.find("role: settings") < site.find("role: esde_common"),
          "site.yml: settings runs before the ES-DE roles (the nested build sees its entry)")
    check("role: settings" in read(ANS, "vm.yml"), "vm.yml runs the settings role")
    coll = read(ANS, "roles", "esde_collections", "tasks", "main.yml")
    check("['--settings-entry', esde_collections_settings_entry,\n                      '--settings-command', esde_collections_settings_command] if esde_settings_entry.stat.exists else []" in coll,
          "esde_collections passes --settings-entry + --settings-command only when the entry file exists")
    cd = read(ANS, "roles", "esde_collections", "defaults", "main.yml")
    check('esde_collections_settings_entry: "{{ settings_entry }}"' in cd
          and 'esde_collections_settings_command: "{{ settings_command }}"' in cd
          and "/data/coin-dojo/settings" not in cd and "/data/coin-dojo/settings" not in coll,
          "esde_collections takes entry + command from the settings role (the path is typed once)")
    check("../roles/settings/defaults/main.yml" in read(ANS, "tests", "esde_local.yml"),
          "the local ES-DE play loads the settings defaults too")
    decl = read(REPO, "config", "variables.d", "collections.yml")
    check(", settings" in decl.split("hk_virtual_systems:", 1)[1].split("\n", 8)[3],
          "hk_virtual_systems carries the settings gamelist state (lastplayed)")


def trophiesd_unit_problems(unit):
    """#177: the overlay daemon runs as the cabinet user, sandboxed, writing only its cache and the Wayland dir."""
    out = []
    if "User={{ settings_user }}" not in unit or re.search(r"(?m)^User=root", unit):
        out.append("not the cabinet user")
    for want in ("NoNewPrivileges=true", "ProtectSystem=strict", "ProtectHome=read-only", "PrivateTmp=true"):
        if want not in unit:
            out.append("missing " + want)
    rw = re.findall(r"(?m)^ReadWritePaths=(.*)$", unit)
    if rw != ["{{ trophies_cache_dir }} /run/user/{{ trophiesd_uid }}"]:
        out.append("ReadWritePaths is not just the cache + the Wayland dir: %r" % rw)
    if "ExecStart=/usr/bin/python3 {{ settings_dir }}/trophiesd.py" not in unit:
        out.append("ExecStart is not the shipped trophiesd.py")
    return out


def test_trophiesd_unit_and_gates():
    unit = read(ROLE, "templates", "coin-dojo-trophiesd.service.j2")
    bad = trophiesd_unit_problems(unit)
    check(not bad, "#177 trophiesd unit: cabinet user, sandboxed, cache-only writes (%s)" % "; ".join(bad))
    broken = unit.replace("User={{ settings_user }}", "User=root").replace("ProtectHome=read-only\n", "")
    check(len(trophiesd_unit_problems(broken)) == 2,
          "BROKEN CASE: a root unit without ProtectHome is flagged")
    check("bootmenu_app.py" in re.search(r"(?m)^settings_app_files: \[.*\]", DEFAULTS).group(0)
          and "{{ settings_stage.path }}/bootmenu_app.py" in TASKS and "gate-boot.png" in TASKS
          and "settings_gate_boot_png.stat.exists" in TASKS,
          "#210 the boot menu ships with the app and the install gate draws one of its frames first")
    check("bootmenu_app.py" not in re.search(r"(?m)^settings_app_files: \[.*\]",
                                             DEFAULTS.replace("bootmenu_app.py, ", "")).group(0),
          "BROKEN CASE: a files list without bootmenu_app.py is detected")
    check("trophiesd.py" in re.search(r"(?m)^settings_app_files: \[.*\]", DEFAULTS).group(0), "trophiesd.py ships with the app")
    check('trophiesd_enable: "{{ trophies_enable | bool and trophies_user | length > 0 }}"' in DEFAULTS,
          "trophiesd runs exactly when the TROPHIES entry is written")
    check(TASKS.count("when: settings_dir_ready | bool and trophiesd_enable | bool") == 3
          and TASKS.count("when: settings_dir_ready | bool and not trophiesd_enable | bool") == 2,
          "unit + start + cache dir only when on; stop + remove when off")
    check(TASKS.count("notify: Restart trophiesd") == 4, "code, entry, key and unit changes restart trophiesd")
    h = read(ROLE, "handlers", "main.yml")
    code = "\n".join(x for x in h.splitlines() if not x.lstrip().startswith("#"))
    check("when: trophiesd_enable | bool and not ansible_check_mode" in code and "es-de" not in code.lower(),
          "the handler restarts only trophiesd, only when it is on, never in a dry run")


# ---- #189 benchmark mode: the cabinet wins ------------------------------------------------------------------------
ROLES = os.path.dirname(ROLE)
BENCH = {"cabinet_tweaks": ("cabinet", "cabinet_benchmark", "Retroarch.cfg hotkeys"),
         "dreamcast": ("dreamcast", "dreamcast_benchmark", "Set the emu.cfg keys we care about")}
STATE_SAMPLES = ['{"benchmark_mode": true}', '{\n  "benchmark_mode": false,\n  "later": 1\n}', "", "garbage",
                 '{"benchmark_mode": "yes"}', '{"benchmark_mode": yes}', '{"benchmark_mode": 1}', '{"other": true}']


def _task(tasks, name):
    i = tasks.find("- name: " + name)
    return (i, tasks[i:tasks.find("\n- name:", i + 10)] if i != -1 else "")


def _path(defaults, var):
    m = re.search(r'(?m)^%s:\s*"([^"]+)"' % var, defaults)
    return m.group(1).replace("{{ cabinet_home }}", "HOME").replace("{{ hk_cabinet_home }}", "HOME") if m else None


WHEN = 'when: "%s"'        # a set_fact guard as the roles write it (#197)
ERR = "template error"      # a set_fact without its `when:` guard fails on a file that lacks the key


def state_parsed(tasks, prefix, sample):
    """What the role's parse task makes of a state file (None = no file): the shared filter (#197), {} when the role
    does not parse it with that filter (then nothing is overridden and the samples flag it)."""
    _i, blk = _task(tasks, "SETTINGS app state parsed")
    if sample is None or '%s_settings_app: "{{ %s_settings_state_file | hk_settings_state }}"' % (prefix, prefix) not in blk:
        return {}
    return hkstate.parse_state_bytes(sample.encode("utf-8"))


def override(tasks, prefix, name, fact, key, parsed, default):
    """The value set_fact `name` leaves in `fact`: parsed[key] when the task reads that key and is guarded by it."""
    _i, blk = _task(tasks, name)
    if '%s: "{{ %s_settings_app.%s }}"' % (fact, prefix, key) not in blk:
        return default
    if WHEN % ("'%s' in %s_settings_app" % (key, prefix)) not in blk:
        return parsed[key] if key in parsed else ERR
    return parsed.get(key, default)


def deploy_override(tasks, prefix, fact, sample, default):
    """What the deploy makes of a state file (None = no file), from the role's tasks and the shared filter."""
    return override(tasks, prefix, "Benchmark mode from the SETTINGS app", fact, "benchmark_mode",
                    state_parsed(tasks, prefix, sample), default)


def bench_deploy_problems(role, tasks, defaults, settings_defaults):
    prefix, fact, consumer = BENCH[role]
    out = []
    state = _path(defaults, prefix + "_settings_state")
    if state is None or state != _path(settings_defaults, "settings_benchmark_state"):
        out.append("%s reads %s, the app writes %s" % (role, state, _path(settings_defaults, "settings_benchmark_state")))
    keys_file = _path(defaults, prefix + "_benchmark_keys_file")
    app_keys = [k.replace("{{ hk_cabinet_home }}", "HOME") for k in
                re.findall(r'(?m)^  - "([^"]+)"', settings_defaults[settings_defaults.find("settings_benchmark_keys:"):])[:2]]
    if keys_file not in app_keys:
        out.append("%s publishes %s, the app reads %s" % (role, keys_file, app_keys))
    i_slurp, slurp = _task(tasks, "SETTINGS app state")
    if "slurp:" not in slurp or "{{ %s_settings_state }}" % prefix not in slurp or "failed_when: false" not in slurp:
        out.append("%s: no slurp of the state file that tolerates a missing one" % role)
    i_set, setf = _task(tasks, "Benchmark mode from the SETTINGS app")
    if "set_fact:" not in setf or WHEN % ("'benchmark_mode' in %s_settings_app" % prefix) not in setf:
        out.append("%s: %s is not set from the state file only when it has one" % (role, fact))
    i_use, _u = _task(tasks, consumer)
    if not (-1 < i_slurp < i_set < i_use):
        out.append("%s: the override must come before '%s' uses %s" % (role, consumer, fact))
    _k, pub = _task(tasks, "Benchmark keys for the SETTINGS app")
    if "{{ %s_benchmark_keys | to_nice_json }}" % prefix not in pub or "{{ %s_benchmark_keys_file }}" % prefix not in pub:
        out.append("%s: the keys file is not published from %s_benchmark_keys" % (role, prefix))
    for sample, want in zip(STATE_SAMPLES, [True, False, None, None, None, None, None, None]):
        for default in (False, True):
            got = deploy_override(tasks, prefix, fact, sample, default)
            if got != (default if want is None else want):
                out.append("%s: state %r with default %s gives %s" % (role, sample, default, got))
    if deploy_override(tasks, prefix, fact, None, True) is not True:
        out.append("%s: no state file must keep hk_benchmark_mode" % role)
    return out


def test_bench_deploy_honours_the_state_file():
    sd = DEFAULTS
    for role in BENCH:
        t, d = read(ROLES, role, "tasks", "main.yml"), read(ROLES, role, "defaults", "main.yml")
        bad = bench_deploy_problems(role, t, d, sd)
        check(not bad, "#189 %s: the SETTINGS app's state file overrides hk_benchmark_mode, keys published (%s)"
              % (role, "; ".join(bad)))
        prefix, fact, consumer = BENCH[role]
        _i, setf = _task(t, "Benchmark mode from the SETTINGS app")
        check(bench_deploy_problems(role, t.replace(setf, ""), d, sd), "BROKEN CASE: %s without the override is flagged" % role)
        check(bench_deploy_problems(role, t.replace("""  when: "'benchmark_mode' in %s_settings_app"\n""" % prefix, ""), d, sd),
              "BROKEN CASE: %s overriding without a state file is flagged" % role)
        check(bench_deploy_problems(role, t.replace("| hk_settings_state }}", "| b64decode | from_yaml }}"), d, sd),
              "BROKEN CASE: %s parsing without the strict filter (#197) is flagged" % role)
        _j, cons = _task(t, consumer)
        _k, slurp = _task(t, "SETTINGS app state")
        check(bench_deploy_problems(role, t.replace(cons, "").replace(slurp, cons + "\n" + slurp), d, sd),
              "BROKEN CASE: %s applying the keys before the override is flagged" % role)
        check(bench_deploy_problems(role, t, d.replace("settings-state.json", "state.json"), sd),
              "BROKEN CASE: %s reading another state path is flagged" % role)
        check(bench_deploy_problems(role, t, d, sd.replace("benchmark-%s.json" % ("retroarch" if role == "cabinet_tweaks"
                                                                                     else "flycast"), "x.json")),
              "BROKEN CASE: the app reading another keys file than %s publishes is flagged" % role)


def test_bench_keys_have_one_source():
    ct = read(ROLES, "cabinet_tweaks", "defaults", "main.yml")
    blk = ct[ct.find("cabinet_benchmark_keys: >-"):]
    blk = blk[:blk.find("\n\n")]
    for want in ("cabinet_benchmark_on", "cabinet_benchmark_off", "hk_retroarch_cfg", "hk_benchmark_mode",
                 "'format': 'retroarch'", "cabinet_retroarch_dir ~ '/retroarch.cfg'"):
        check(want in blk, "cabinet_benchmark_keys is built from %s (no second copy of the values)" % want)
    check(not re.search(r"fps_show|video_message|0\.9", blk), "no RetroArch value typed into cabinet_benchmark_keys")
    dc = read(ROLES, "dreamcast", "defaults", "main.yml")
    blk = dc[dc.find("dreamcast_benchmark_keys:\n"):]
    blk = blk[:blk.find("\n\n")]
    check('file: "{{ dreamcast_emu_cfg }}"' in blk and "section: config" in blk and "format: ini" in blk
          and '"on": { rend.ShowFPS: "yes" }' in blk and "dreamcast_show_fps | bool" in blk.split('"off":')[-1],
          "dreamcast_benchmark_keys: rend.ShowFPS yes on, hk_flycast_show_fps off, in emu.cfg [config]")
    check("key: rend.ShowFPS, value: \"{{ 'yes' if (dreamcast_show_fps | bool or dreamcast_benchmark | bool)" in dc,
          "the deploy's rend.ShowFPS follows the same two inputs")
    entry = _task(TASKS, "Entry file")[1]
    for k in ("'benchmark_state': settings_benchmark_state", "'benchmark_keys': settings_benchmark_keys",
              "'game_procs': trophiesd_game_procs", "'game_patterns': trophiesd_game_patterns"):
        check(k in entry, "the entry file hands the app %s" % k.split(":")[0])
    sys.path.insert(0, os.path.join(REPO, "skills", "settings"))
    import settings_core as core
    check(core.BENCH_STATE.replace("~", "HOME") == _path(DEFAULTS, "settings_benchmark_state"),
          "the app's built-in state path = the role's")


# ---- #171 RetroAchievements: the cabinet wins ---------------------------------------------------------------------
CH = {"retroarch122": ("RetroAchievements keys in retroarch.cfg", "RetroAchievements login token in retroarch.cfg",
                       "RetroAchievements keys in retroarch.cfg"),
      "dreamcast": ("Set the emu.cfg keys we care about", "RetroAchievements login token in emu.cfg",
                    "RetroAchievements user name in emu.cfg")}
def UNQ(s):
    """YAML double-quoted string -> its text."""
    return s.replace('\\"', '"').replace("\\\\", "\\")


def _when_rx(blk):
    m = re.search(r"is search\('(.+?)'\)\s*$", blk, re.M)
    return m.group(1) if m else None


def cheevos_deploy(tasks, prefix, state_text):
    """What the deploy makes of a state file (None = no file): {enable, hardcore, app_login} as set_fact would leave
    them (None = the hk_* lever stays), from the role's tasks and the shared filter (#197)."""
    parsed = state_parsed(tasks, prefix, state_text)
    out = dict(enable=None, hardcore=None, app_login=False)
    for key, name in (("enable", "RetroAchievements on/off from the SETTINGS app"),
                      ("hardcore", "RetroAchievements hardcore from the SETTINGS app")):
        out[key] = override(tasks, prefix, name, "%s_cheevos_%s" % (prefix, key), "cheevos_" + key, parsed, None)
    _i, blk = _task(tasks, "RetroAchievements login owned by the SETTINGS app")
    if "%s_cheevos_app_login: true" % prefix in blk:
        guarded = WHEN % ("'cheevos_login' in %s_settings_app" % prefix) in blk
        out["app_login"] = "cheevos_login" in parsed or not guarded
    return out


CH_SAMPLES = [(None, dict(enable=None, hardcore=None, app_login=False)),
              ("", dict(enable=None, hardcore=None, app_login=False)),
              ("garbage", dict(enable=None, hardcore=None, app_login=False)),
              ('{"benchmark_mode": true}', dict(enable=None, hardcore=None, app_login=False)),
              ('{"cheevos_enable": "yes", "cheevos_login": 1}', dict(enable=None, hardcore=None, app_login=False)),
              ('{"cheevos_enable": yes, "cheevos_hardcore": yes, "cheevos_login": yes}',
               dict(enable=None, hardcore=None, app_login=False)),
              ('{\n  "cheevos_enable": true,\n  "cheevos_hardcore": false\n}', dict(enable=True, hardcore=False, app_login=False)),
              ('{"cheevos_enable": false, "cheevos_login": true}', dict(enable=False, hardcore=None, app_login=True)),
              ('{"cheevos_login": false}', dict(enable=None, hardcore=None, app_login=True))]


def cheevos_deploy_problems(role, tasks, defaults, settings_defaults):
    consumer, token_task, user_task = CH[role]
    out = []
    state = _path(defaults, role + "_settings_state")
    if state is None or state != _path(settings_defaults, "settings_benchmark_state"):
        out.append("%s reads %s, the app writes %s" % (role, state, _path(settings_defaults, "settings_benchmark_state")))
    if not re.search(r"(?m)^%s_cheevos_app_login: false" % role, defaults):
        out.append("%s: %s_cheevos_app_login has no false default" % (role, role))
    i_slurp, slurp = _task(tasks, "SETTINGS app state")
    if "slurp:" not in slurp or "{{ %s_settings_state }}" % role not in slurp or "failed_when: false" not in slurp:
        out.append("%s: no slurp of the state file that tolerates a missing one" % role)
    idx = [_task(tasks, n)[0] for n in ("RetroAchievements on/off from the SETTINGS app",
                                        "RetroAchievements hardcore from the SETTINGS app",
                                        "RetroAchievements login owned by the SETTINGS app")]
    first_use = min(_task(tasks, n)[0] for n in (consumer, token_task, user_task))
    if not (-1 < i_slurp < min(idx) and -1 not in idx and max(idx) < first_use):
        out.append("%s: the state file must be read before the RetroAchievements keys are written" % role)
    guard = "not %s_cheevos_app_login | bool" % role
    for name in (token_task, user_task):
        _i, blk = _task(tasks, name)
        if guard not in blk:
            out.append("%s: '%s' does not skip when the app owns the login" % (role, name))
    for sample, want in CH_SAMPLES:
        got = cheevos_deploy(tasks, role, sample)
        if got != want:
            out.append("%s: state %r gives %s, want %s" % (role, sample, got, want))
    return out


def test_cheevos_deploy_skips_what_the_app_owns():
    for role in CH:
        t, d = read(ROLES, role, "tasks", "main.yml"), read(ROLES, role, "defaults", "main.yml")
        bad = cheevos_deploy_problems(role, t, d, DEFAULTS)
        check(not bad, "#171 %s: the app's RetroAchievements state overrides hk_cheevos_*, its login skips user + token (%s)"
              % (role, "; ".join(bad)))
        _i, login = _task(t, "RetroAchievements login owned by the SETTINGS app")
        _j, token = _task(t, CH[role][1])
        _k, en = _task(t, "RetroAchievements on/off from the SETTINGS app")
        _c, cons = _task(t, CH[role][0])
        for why, broken in (("without the login override", t.replace(login, "")),
                            ("writing the token although the app owns the login",
                             t.replace(token, token.replace("not %s_cheevos_app_login | bool" % role, "true"))),
                            ("overriding without a state file", t.replace("""  when: "'cheevos_enable' in %s_settings_app"\n"""
                                                                            % role, "")),
                            ("parsing without the strict filter (#197)", t.replace("| hk_settings_state }}",
                                                                                    "| b64decode | from_yaml }}")),
                            ("setting on/off after the keys are written", t.replace(en, "").replace(cons, cons + "\n" + en))):
            check(cheevos_deploy_problems(role, broken, d, DEFAULTS), "BROKEN CASE: %s %s is flagged" % (role, why))
        check(cheevos_deploy_problems(role, t, d.replace("settings-state.json", "state.json"), DEFAULTS),
              "BROKEN CASE: %s reading another state path is flagged" % role)


def test_cheevos_app_and_deploy_agree():
    """The app's own writes, read back by the roles' tasks + the shared filter; the files the app edits = the ones the roles write."""
    import json
    import shutil
    import tempfile
    import settings_core as core
    rd, dd = read(ROLES, "retroarch122", "defaults", "main.yml"), read(ROLES, "dreamcast", "defaults", "main.yml")
    m = re.search(r'(?m)^settings_cheevos_files:.*\n  retroarch: "([^"]+)"\n  flycast: "([^"]+)"', DEFAULTS)
    ra_dir = _path(rd, "retroarch122_cfg_dir")
    check(m and m.group(1).replace("{{ hk_cabinet_home }}", "HOME") == (ra_dir or "") + "/retroarch.cfg"
          and m.group(2).replace("{{ hk_cabinet_home }}", "HOME") == _path(dd, "dreamcast_emu_cfg"),
          "settings_cheevos_files = retroarch122_cfg_dir/retroarch.cfg and dreamcast_emu_cfg")
    check({k: v.replace("~", "HOME") for k, v in core.CHEEVOS_FILES.items()} ==
          {"retroarch": (ra_dir or "") + "/retroarch.cfg", "flycast": _path(dd, "dreamcast_emu_cfg")},
          "the app's built-in paths are the same files")
    api = re.search(r"(?m)^settings_cheevos_api: (\S+)", DEFAULTS)
    check(api and api.group(1) == core.CHEEVOS_API and core.api_url_error(api.group(1)) is None,
          "settings_cheevos_api is the app's https default")
    entry = _task(TASKS, "Entry file")[1]
    check("'cheevos_files': settings_cheevos_files" in entry and "'cheevos_api': settings_cheevos_api" in entry,
          "the entry file hands the app the cfg paths and the login address")
    tmp = tempfile.mkdtemp()
    try:
        ch = core.Cheevos(dict(retroarch=os.path.join(tmp, "r.cfg"), flycast=os.path.join(tmp, "e.cfg")),
                          os.path.join(tmp, "s.json"), busy=lambda: False,
                          login=lambda api, u, p: ("Player_One", "FAKEtoken0123456"))
        steps = [(lambda: ch.set_enable(True), dict(enable=True, hardcore=None, app_login=False)),
                 (lambda: ch.set_hardcore(False), dict(enable=True, hardcore=False, app_login=False)),
                 (lambda: ch.login("player_one", "pw"), dict(enable=True, hardcore=False, app_login=True)),
                 (ch.logout, dict(enable=True, hardcore=False, app_login=True))]
        for step, want in steps:
            ok, msg = step()
            text = read(tmp, "s.json")
            json.loads(text)
            for role in CH:
                got = cheevos_deploy(read(ROLES, role, "tasks", "main.yml"), role, text)
                check(ok and got == want, "%s after the app's '%s': %s" % (role, msg, got))
    finally:
        shutil.rmtree(tmp)


def _nested_task(tasks, name):
    """One task inside the install block (indented 4): its text up to the next task or comment at that indent."""
    i = tasks.find("    - name: " + name)
    if i == -1:
        return ""
    j = min([k for k in (tasks.find("\n    - name:", i + 10), tasks.find("\n    #", i + 10)) if k != -1] or [len(tasks)])
    return tasks[i:j]


def entry_problems(tasks, defaults):
    """#198: the SETTINGS entry file is root:root 0644 (the app only reads it), carries no test hook, and ES-DE's
    command never passes --test-hooks. #196: the TROPHIES entry hands the page the app's state file + cfg paths."""
    out = []
    blk = _nested_task(tasks, "Entry file")
    if not blk:
        return ["no 'Entry file' task"]
    if not (re.search(r"(?m)^        owner: root$", blk) and re.search(r"(?m)^        group: root$", blk)
            and 'mode: "0644"' in blk):
        out.append("the SETTINGS entry file is not root:root 0644")
    if re.search(r"settings_user|settings_group", blk):
        out.append("the SETTINGS entry file is owned by the cabinet user")
    if re.search(r"game_proc'|test_hooks|test-hooks|127\.0\.0\.1|localhost|http:", blk):
        out.append("the SETTINGS entry file carries a test hook (game_proc / loopback address)")
    if "test-hooks" in tasks or "test-hooks" in defaults:
        out.append("--test-hooks appears in the role")
    cmd = re.search(r'(?m)^settings_command: "(.*)"', defaults)
    if not cmd or "--" in cmd.group(1):
        out.append("settings_command has extra flags: %r" % (cmd and cmd.group(1)))
    api = re.search(r"(?m)^settings_cheevos_api: (\S+)", defaults)
    if not api or not api.group(1).startswith("https://retroachievements.org/"):
        out.append("settings_cheevos_api is not https://retroachievements.org/...")
    tro = _nested_task(tasks, "TROPHIES entry file")
    for k in ("'settings_state': settings_benchmark_state", "'cheevos_files': settings_cheevos_files"):
        if k not in tro:
            out.append("the TROPHIES entry misses %s" % k.split(":")[0])
    return out


def test_entry_files():
    check(not entry_problems(TASKS, DEFAULTS), "#198 entry root:root 0644, no test hooks; #196 TROPHIES entry has the app "
          "paths (%s)" % "; ".join(entry_problems(TASKS, DEFAULTS)))
    blk = _nested_task(TASKS, "Entry file")
    broken = [("owned by the cabinet user", TASKS.replace(blk, blk.replace("owner: root", 'owner: "{{ settings_user }}"'))),
              ("group writable by the cabinet group", TASKS.replace(blk, blk.replace("group: root", 'group: "{{ settings_group }}"'))),
              ("mode 0664", TASKS.replace(blk, blk.replace('mode: "0644"', 'mode: "0664"'))),
              ("with a game_proc", TASKS.replace(blk, blk.replace("'game_procs': trophiesd_game_procs,",
                                                                  "'game_procs': trophiesd_game_procs, 'game_proc': '/tmp/p',"))),
              ("with a loopback login address", TASKS.replace(blk, blk.replace("settings_cheevos_api,", "'http://127.0.0.1:8080/x',"))),
              ("without the TROPHIES state path", TASKS.replace("'settings_state': settings_benchmark_state", "'x': 1"))]
    for why, t in broken:
        check(entry_problems(t, DEFAULTS), "BROKEN CASE: the entry file %s is flagged" % why)
    check(entry_problems(TASKS, DEFAULTS.replace('settings_app.py"', 'settings_app.py --test-hooks"')),
          "BROKEN CASE: ES-DE's command with --test-hooks is flagged")
    check(entry_problems(TASKS, DEFAULTS.replace("settings_cheevos_api: https://retroachievements.org/",
                                                 "settings_cheevos_api: https://ra.example/")),
          "BROKEN CASE: another login host in the defaults is flagged")


def test_check_mode_safe():
    for name in ("Stage app", "Stage the style kit", "Stage the theme fonts", "py_compile", "The staged app draws a frame", "Remove the staging dir"):
        i = TASKS.find("- name: " + name)
        blk = TASKS[i:TASKS.find("- name:", i + 10)]
        check(i != -1 and "check_mode: false" in blk and "changed_when: false" in blk,
              "'%s' runs in check mode on /tmp only and never reports a change" % name)
    check("shell:" not in TASKS and "ansible.builtin.shell" not in TASKS, "no shell tasks in the role")


if __name__ == "__main__":
    main(globals())
