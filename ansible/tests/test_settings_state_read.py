#!/usr/bin/env python3
"""The SETTINGS app's state file, read by cabinet_tweaks, retroarch122 and dreamcast (#197, Security's review of #171).

    python3 ansible/tests/test_settings_state_read.py      # the container part SKIPs when docker is not running

Rules (each also run on a deliberately broken copy that MUST be flagged):
  filter    ansible/filter_plugins/hk_settings_state.py: a JSON object, real booleans only, per known key; missing file,
            unreadable, not base64, too big, not UTF-8, malformed, not an object, duplicate keys, "true", 1, null = {}
  app       the same answer as the SETTINGS app (settings_core._read_json + isinstance bool) on every sample but a
            duplicated key (the app takes the last value, the roles refuse the file)
  roles     one task pattern in all three: slurp as the cabinet user (become_user), no_log, failed_when: false, parsed
            by the filter, each override guarded by `'key' in <prefix>_settings_app`; no regex / b64decode reads left
  emu.cfg   every dreamcast task that writes emu.cfg (it holds the RA token) sets mode 0600
  container the roles' own read tasks in the pinned Ansible (bin/ans) as a non-root user on sample files: valid,
            malformed, duplicate keys, "true", 1, a symlink to /etc/shadow, no file; the content never in the output
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ANS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(ANS)
ROLES = os.path.join(ANS, "roles")
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
sys.path.insert(0, os.path.join(ANS, "filter_plugins"))
sys.path.insert(0, os.path.join(REPO, "skills", "settings"))
from hktest import check, main, skip  # noqa: E402
import hk_settings_state as hk  # noqa: E402

CACHE = os.environ.get("ANS_CACHE", os.path.join(os.environ.get("HK_CACHE", os.path.expanduser("~/.cache/coin-dojo")),
                                                 "ansible"))
PREFIX = {"cabinet_tweaks": "cabinet", "retroarch122": "retroarch122", "dreamcast": "dreamcast"}
OVERRIDES = {"cabinet_tweaks": [("cabinet_benchmark", "benchmark_mode"), ("cabinet_attract_type", "attract_type")],
             "retroarch122": [("retroarch122_cheevos_enable", "cheevos_enable"),
                              ("retroarch122_cheevos_hardcore", "cheevos_hardcore")],
             "dreamcast": [("dreamcast_benchmark", "benchmark_mode"), ("dreamcast_cheevos_enable", "cheevos_enable"),
                           ("dreamcast_cheevos_hardcore", "cheevos_hardcore")]}
LOGIN = ("retroarch122", "dreamcast")

# (name, file bytes or None = no file, what the roles take)
SAMPLES = [
    ("valid", b'{\n  "benchmark_mode": true,\n  "cheevos_enable": false,\n  "cheevos_hardcore": true,\n'
              b'  "cheevos_login": false\n}\n',
     dict(benchmark_mode=True, cheevos_enable=False, cheevos_hardcore=True, cheevos_login=False)),
    ("partial", b'{"cheevos_enable": true, "other": "x"}', dict(cheevos_enable=True)),
    ("attract", b'{"attract_enabled": true, "attract_minutes": 30, "attract_type": "dim"}',
     dict(attract_enabled=True, attract_minutes=30, attract_type="dim")),
    ("attract_off", b'{"attract_enabled": false}', dict(attract_enabled=False)),
    ("attract_minutes_only", b'{"attract_minutes": 5}', dict(attract_minutes=5)),
    ("attract_default0", b'{"attract_minutes": 5}', dict(attract_minutes=5)),      # run with hk_attract_timer_ms: 0
    ("attract_bad", b'{"attract_enabled": "yes", "attract_minutes": 7, "attract_type": "rm -rf"}', {}),
    ("attract_bad2", b'{"attract_minutes": true, "attract_type": ["video"]}', {}),
    ("malformed", b'{"benchmark_mode": true,', {}),
    ("yaml_bool", b'{"benchmark_mode": yes}', {}),
    ("duplicate", b'{"benchmark_mode": false, "benchmark_mode": true}', {}),
    ("nested_dup", b'{"benchmark_mode": true, "x": {"a": 1, "a": 2}}', {}),
    ("string_true", b'{"benchmark_mode": "true", "cheevos_login": "false"}', {}),
    ("number_one", b'{"benchmark_mode": 1, "cheevos_enable": 0}', {}),
    ("null", b'{"benchmark_mode": null}', {}),
    ("array", b'[{"benchmark_mode": true}]', {}),
    ("bare_true", b'true', {}),
    ("empty", b'', {}),
    ("bom", b'\xef\xbb\xbf{"benchmark_mode": true}', {}),
    ("not_utf8", b'{"benchmark_mode": true, "x": "\xff"}', {}),
    ("deep", b'[' * 100000 + b']' * 100000, {}),
    ("too_big", b'{"benchmark_mode": true, "pad": "' + b'x' * (hk.MAX_BYTES + 1) + b'"}', {}),
    ("no_file", None, {}),
]


def slurp(raw):
    """A slurp result as registered with failed_when: false."""
    if raw is None:
        return {"failed": False, "msg": "file not found: /x", "changed": False}
    return {"content": base64.b64encode(raw).decode(), "encoding": "base64", "source": "/x", "changed": False}


def read(*p):
    with open(os.path.join(*p), encoding="utf-8") as f:
        return f.read()


def test_filter_samples():
    for name, raw, want in SAMPLES:
        got = hk.hk_settings_state(slurp(raw))
        check(got == want and all(type(v) is bool for k, v in got.items() if k in hk.KEYS), "filter %s -> %s" % (name, got))
    for why, res in (("not base64", {"content": "!!not b64!!", "encoding": "base64"}),
                     ("no content", {"changed": False}), ("not a dict", "content"), ("None", None),
                     ("another encoding", {"content": "eyJiZW5jaG1hcmtfbW9kZSI6IHRydWV9", "encoding": "utf-8"}),
                     ("a failed read", dict(slurp(b'{"benchmark_mode": true}'), failed=True))):
        check(hk.hk_settings_state(res) == {}, "filter: %s -> {}" % why)
    check(hk.FilterModule().filters() == {"hk_settings_state": hk.hk_settings_state}, "the plugin exports the filter")
    # broken case: a parser without the duplicate check takes the last value
    loose = json.loads('{"benchmark_mode": false, "benchmark_mode": true}')
    check(loose.get("benchmark_mode") is True and hk.parse_state_bytes(b'{"benchmark_mode": false, "benchmark_mode": true}') == {},
          "BROKEN CASE: plain json.loads takes the duplicate's last value; the filter refuses it")


def test_filter_matches_the_app():
    import settings_core as core
    tmp = tempfile.mkdtemp()
    try:
        for name, raw, _want in SAMPLES:
            p = os.path.join(tmp, name + ".json")
            if raw is not None:
                with open(p, "wb") as f:
                    f.write(raw)
            st = core._read_json(p)
            app = {k: st.get(k) for k in hk.KEYS if isinstance(st, dict) and isinstance(st.get(k), bool)}
            if isinstance(st, dict):                       # (#285) the attract choices: the app's own lists
                import attract_apply as aa
                m = st.get("attract_minutes")
                if isinstance(m, int) and not isinstance(m, bool) and m in aa.MINUTES:
                    app["attract_minutes"] = m
                if st.get("attract_type") in aa.TYPES:
                    app["attract_type"] = st["attract_type"]
            roles = hk.hk_settings_state(slurp(raw))
            if name in ("duplicate", "nested_dup"):
                check(roles == {}, "%s: the roles refuse a duplicated key (the app reads %s; documented difference)"
                      % (name, app))
            elif name == "too_big":
                check(roles == {}, "too_big: the roles refuse a file over %d bytes" % hk.MAX_BYTES)
            else:
                check(roles == app, "%s: roles %s = app %s" % (name, roles, app))
        # what the app itself writes is read back
        p = os.path.join(tmp, "s.json")
        core.save_state(p, benchmark_mode=True, cheevos_login=False)
        with open(p, "rb") as f:
            check(hk.parse_state_bytes(f.read()) == dict(benchmark_mode=True, cheevos_login=False),
                  "the app's own save_state file is read back by the filter")
    finally:
        shutil.rmtree(tmp)


def _task(tasks, name):
    i = tasks.find("- name: " + name)
    return (i, tasks[i:tasks.find("\n- name:", i + 10)] if i != -1 else "")


def read_block(tasks):
    """The state-read part of a role: from the slurp through the last override task."""
    i = tasks.find("- name: SETTINGS app state (")
    last = max(tasks.rfind("- name: " + n) for n in ("Benchmark mode from the SETTINGS app",
                                                     "RetroAchievements hardcore from the SETTINGS app",
                                                     "RetroAchievements login owned by the SETTINGS app",
                                                     "Attract mode timer from the SETTINGS app",
                                                     "Attract mode type from the SETTINGS app"))
    if i == -1 or last == -1:
        return ""
    end = tasks.find("\n\n", last)
    return tasks[i:end if end != -1 else len(tasks)] + "\n"


def role_problems(role, tasks):
    p, out = PREFIX[role], []
    _i, sl = _task(tasks, "SETTINGS app state (")
    for want in ("ansible.builtin.slurp:", 'src: "{{ %s_settings_state }}"' % p, "register: %s_settings_state_file" % p,
                 "failed_when: false", "become: true", 'become_user: "{{ cabinet_user }}"', "no_log: true"):
        if "\n  " + want not in sl and "\n    " + want not in sl:
            out.append("%s: the slurp lacks `%s`" % (role, want))
    _j, pa = _task(tasks, "SETTINGS app state parsed")
    if '%s_settings_app: "{{ %s_settings_state_file | hk_settings_state }}"' % (p, p) not in pa:
        out.append("%s: the state is not parsed by the shared hk_settings_state filter" % role)
    if not (-1 < _i < _j):
        out.append("%s: the parse must follow the slurp" % role)
    for fact, key in OVERRIDES[role]:
        k, blk = _task(tasks, {"benchmark_mode": "Benchmark mode", "cheevos_enable": "RetroAchievements on/off",
                               "cheevos_hardcore": "RetroAchievements hardcore",
                               "attract_type": "Attract mode type"}[key] + " from the SETTINGS app")
        if ('%s: "{{ %s_settings_app.%s }}"' % (fact, p, key) not in blk
                or "when: \"'%s' in %s_settings_app\"" % (key, p) not in blk or k < _j):
            out.append("%s: %s is not taken from the parsed state, guarded by its key, after the parse" % (role, fact))
    if role in LOGIN:
        k, blk = _task(tasks, "RetroAchievements login owned by the SETTINGS app")
        if "%s_cheevos_app_login: true" % p not in blk or "when: \"'cheevos_login' in %s_settings_app\"" % p not in blk:
            out.append("%s: the app login is not taken from the parsed cheevos_login" % role)
    block = read_block(tasks)
    if re.search(r"is search\(|b64decode|from_json|from_yaml|regex_search", block):
        out.append("%s: a regex / ad-hoc parse of the state file is left" % role)
    if block.count("become: true") != 1 or "become_user" not in block:
        out.append("%s: only the slurp may become, and only as the cabinet user" % role)
    return out


def canonical(role, tasks):
    """The read block with the role's prefix and its own task/fact names folded, to compare the three roles."""
    b = read_block(tasks).replace(PREFIX[role] + "_settings_", "P_settings_")
    b = re.sub(r"(?m)^# .*\n", "", b)
    return b[:b.find("- name:", b.find("- name: SETTINGS app state parsed") + 10)]


def test_roles_share_one_read_pattern():
    blocks = {}
    for role in PREFIX:
        t = read(ROLES, role, "tasks", "main.yml")
        bad = role_problems(role, t)
        check(not bad, "#197 %s reads the state as the cabinet user, no_log, strict JSON via the filter (%s)"
              % (role, "; ".join(bad)))
        blocks[role] = canonical(role, t)
        _i, sl = _task(t, "SETTINGS app state (")
        for why, broken in (("reading as root (no become_user)", t.replace('  become_user: "{{ cabinet_user }}"\n', "", 1)),
                            ("logging the content (no no_log)", t.replace(sl, sl.replace("  no_log: true\n", ""))),
                            ("failing on a missing file", t.replace(sl, sl.replace("  failed_when: false\n", ""))),
                            ("parsing with a regex", t.replace("| hk_settings_state }}",
                                                               "| b64decode is search('\"benchmark_mode\": true') }}")),
                            ("an override without its key guard",
                             t.replace("  when: \"'%s' in %s_settings_app\"\n" % (OVERRIDES[role][0][1], PREFIX[role]), "")),
                            ("without the parse task", t.replace(_task(t, "SETTINGS app state parsed")[1], ""))):
            check(role_problems(role, broken), "BROKEN CASE: %s %s is flagged" % (role, why))
    check(len(set(blocks.values())) == 1 and all(blocks.values()),
          "the slurp + parse tasks are the same text in all three roles (prefix folded)")
    odd = dict(blocks, dreamcast=blocks["dreamcast"].replace("no_log: true", "no_log: false"))
    check(len(set(odd.values())) != 1, "BROKEN CASE: one role drifting from the shared pattern is flagged")


def attract_timer_problems(tasks):
    """The rules for cabinet_tweaks' attract timer task (#285); [] when it holds them."""
    k, blk = _task(tasks, "Attract mode timer from the SETTINGS app")
    out = []
    if not blk:
        return ["the task is missing"]
    if k < tasks.find("SETTINGS app state parsed"):
        out.append("before the parse")
    if "cabinet_attract_timer_ms:" not in blk:
        out.append("does not set cabinet_attract_timer_ms")
    if "cabinet_settings_app.attract_enabled" not in blk or "is false" not in blk or "{{ 0 if" not in blk:
        out.append("OFF does not give 0")
    if "cabinet_settings_app.attract_minutes | int * 60000" not in blk:
        out.append("minutes are not turned into milliseconds")
    if "when: \"'attract_enabled' in cabinet_settings_app or 'attract_minutes' in cabinet_settings_app\"" not in blk:
        out.append("not guarded by both keys")
    return out


def test_attract_timer_override():
    """#285: the cabinet_tweaks timer task takes the app's choice (OFF = 0), only when the app set one. The Jinja itself is
    evaluated in the container test (test_roles_in_the_container: ON, OFF, minutes only, hk_attract_timer_ms 0)."""
    tasks = read(ROLES, "cabinet_tweaks", "tasks", "main.yml")
    check(attract_timer_problems(tasks) == [], "the real task holds every rule: %s" % attract_timer_problems(tasks))
    _k, blk = _task(tasks, "Attract mode timer from the SETTINGS app")
    for why, broken in (("a guard cut down to one key", tasks.replace(" or 'attract_minutes' in cabinet_settings_app\"", "\"")),
                        ("no OFF branch", tasks.replace("{{ 0 if", "{{ 5 if")),
                        ("minutes not scaled", tasks.replace("| int * 60000", "| int")),
                        ("not setting the fact", tasks.replace("cabinet_attract_timer_ms:", "x_timer_ms:")),
                        ("the task removed", tasks.replace(blk, ""))):
        check(attract_timer_problems(broken) != [], "BROKEN CASE: %s is flagged: %s" % (why, attract_timer_problems(broken)))


def test_attract_entry_checker_flags_a_string_timer():
    """BROKEN CASE (#285 M2): the checker used on the container's rendered entry JSON, run on broken input."""
    check(entry_timer_is_number('{"attract": {"timer_ms": 600000}}') is True, "a number passes")
    for bad in ('{"attract": {"timer_ms": "600000"}}', '{"attract": {}}', '{"attract": {"timer_ms": true}}', "{", '{"attract": 5}'):
        check(entry_timer_is_number(bad) is False, "BROKEN: %s is flagged" % bad)


def test_no_other_state_reads():
    """No task anywhere else in ansible/roles reads settings-state.json (the app's file) except the three above."""
    hits = []
    for role in sorted(os.listdir(ROLES)):
        if role == "settings":
            continue                                   # the app's own role publishes the path, never reads it
        for sub in ("tasks", "defaults", "templates"):
            d = os.path.join(ROLES, role, sub)
            for root, _dirs, files in os.walk(d):
                for f in files:
                    if "settings-state" in read(root, f) or "_settings_state" in read(root, f):
                        hits.append(role)
    check(sorted(set(hits)) == sorted(PREFIX), "only %s read the state file: %s" % (sorted(PREFIX), sorted(set(hits))))


def emu_cfg_problems(tasks):
    blocks = [b for b in tasks.split("\n- name: ") if 'path: "{{ dreamcast_emu_cfg }}"' in b]
    out = [] if len(blocks) == 3 else ["%d tasks write emu.cfg, expected 3" % len(blocks)]
    for b in blocks:
        if 'mode: "0600"' not in b:
            out.append("%s: not mode 0600" % b.splitlines()[0])
    return out


def test_emu_cfg_is_0600():
    t = read(ROLES, "dreamcast", "tasks", "main.yml")
    bad = emu_cfg_problems(t)
    check(not bad, "#197 every emu.cfg write is mode 0600 (it holds the RA token) (%s)" % "; ".join(bad))
    check(emu_cfg_problems(t.replace('mode: "0600"', 'mode: "0644"', 1)), "BROKEN CASE: one emu.cfg write at 0644 is flagged")
    check('{{ dreamcast_emu_cfg | dirname }}' in t, "the role still creates flycast's config dir (for the 0600 file)")


# ---- the real tasks in the pinned container ------------------------------------------------------------------------
def container_samples():
    s = [(n, "file", raw, w) for n, raw, w in SAMPLES if raw is not None and n not in ("deep", "too_big")]
    s.append(("symlink_shadow", "link", "/etc/shadow", {}))
    s.append(("symlink_own", "link", "valid", SAMPLES[0][2]))       # a link to the user's own file is followed
    s.append(("no_file", "none", None, {}))
    return s


HK_TIMER = {"attract_default0": 0}           # per-sample hk_attract_timer_ms (the others: 600000)


def expected_attract(name, want):
    """What the cabinet_tweaks attract tasks must leave in cabinet_attract_timer_ms / _type (#285)."""
    hk = HK_TIMER.get(name, 600000)
    t = "DEFAULT"
    if "attract_enabled" in want or "attract_minutes" in want:
        if not want.get("attract_enabled", hk > 0):
            t = 0
        elif "attract_minutes" in want:
            t = want["attract_minutes"] * 60000
        else:
            t = hk if hk > 0 else 600000
    return t, want.get("attract_type", "DEFAULT")


def entry_timer_is_number(text):
    """The rendered entry JSON: attract.timer_ms is a JSON number (the app reads it as one)."""
    try:
        v = json.loads(text)["attract"]["timer_ms"]
    except (ValueError, KeyError, TypeError):
        return False
    return isinstance(v, int) and not isinstance(v, bool)


def expected(want, name=""):
    d = "DEFAULT"
    t, ty = expected_attract(name, want)
    return {"cabinet_attract_timer_ms": t, "cabinet_attract_type": ty,
            "attract_entry_timer_is_number": True,
            "cabinet_benchmark": want.get("benchmark_mode", d),
            "retroarch122_cheevos_enable": want.get("cheevos_enable", d),
            "retroarch122_cheevos_hardcore": want.get("cheevos_hardcore", d),
            "retroarch122_cheevos_app_login": "cheevos_login" in want,
            "dreamcast_benchmark": want.get("benchmark_mode", d),
            "dreamcast_cheevos_enable": want.get("cheevos_enable", d),
            "dreamcast_cheevos_hardcore": want.get("cheevos_hardcore", d),
            "dreamcast_cheevos_app_login": "cheevos_login" in want}


def test_roles_in_the_container():
    if shutil.which("docker") is None or subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        skip("docker is not running (colima start); the roles' read tasks were not run in the container")
        return
    os.makedirs(CACHE, exist_ok=True)
    b = tempfile.mkdtemp(prefix="t197-state-", dir=CACHE)
    try:
        out_dir = os.path.join(b, "out")
        os.makedirs(out_dir)
        task_files = []
        for role in PREFIX:
            p = os.path.join(b, role + ".yml")
            with open(p, "w", encoding="utf-8") as f:
                f.write(read_block(read(ROLES, role, "tasks", "main.yml")))
            task_files.append(p)
        home = "/home/hkcab/.config/coin-dojo"
        hosts = {}
        for name, kind, data, _w in container_samples():
            path = "%s/%s/settings-state.json" % (home, name)
            hv = {"hk_attract_timer_ms": HK_TIMER.get(name, 600000), "hk_sample_path": path, "hk_sample_kind": kind, "hk_sample_b64": "", "hk_sample_link": "",
                  "cabinet_settings_state": path, "retroarch122_settings_state": path, "dreamcast_settings_state": path}
            if kind == "file":
                hv["hk_sample_b64"] = base64.b64encode(data).decode()
            elif kind == "link":
                hv["hk_sample_link"] = data if data.startswith("/") else "%s/%s/settings-state.json" % (home, data)
            hosts[name] = hv
        inv = {"all": {"hosts": hosts, "vars": {
            "ansible_connection": "local", "ansible_python_interpreter": "/usr/local/bin/python3",
            "ansible_become_method": "su", "cabinet_user": "hkcab", "hk_role_tasks": task_files, "hk_out_dir": out_dir,
            "cabinet_attract_timer_ms": "DEFAULT", "cabinet_attract_type": "DEFAULT",
            "hk_esde_home": "/home/hkcab/ES-DE", "hk_attract_type": "video", "hk_cabinet_home": "/home/hkcab", "coin_dojo_run_dir": "/run/hk",
            "cabinet_benchmark": "DEFAULT", "retroarch122_cheevos_enable": "DEFAULT",
            "retroarch122_cheevos_hardcore": "DEFAULT", "retroarch122_cheevos_app_login": False,
            "dreamcast_benchmark": "DEFAULT", "dreamcast_cheevos_enable": "DEFAULT",
            "dreamcast_cheevos_hardcore": "DEFAULT", "dreamcast_cheevos_app_login": False}}}
        inv_path = os.path.join(b, "inventory.yml")
        with open(inv_path, "w") as f:
            json.dump(inv, f)
        cmd = [os.path.join(ANS, "bin", "ans"), "ansible-playbook", "-i", inv_path, "tests/settings_state_local.yml", "-v"]
        r = subprocess.run(cmd, cwd=ANS, capture_output=True, text=True,
                           env=dict(os.environ, ANSIBLE_FORCE_COLOR="", ANS_WORKSPACE=""))
        log = r.stdout + r.stderr
        with open(os.path.join(CACHE, "t197-last-run.log"), "w") as f:
            f.write(log)
        if not check(r.returncode == 0, "the play ran (rc %d; log %s)" % (r.returncode, os.path.join(CACHE, "t197-last-run.log"))):
            print(log[-3000:])
            return
        for name, kind, _d, want in container_samples():
            p = os.path.join(out_dir, name + ".json")
            got = json.load(open(p)) if os.path.exists(p) else None
            err = (got or {}).pop("read_error", "")
            entry_text = (got or {}).pop("entry_json", "")
            if got is not None:
                got["attract_entry_timer_is_number"] = entry_timer_is_number(entry_text)
            check(got == expected(want, name), "container %s: %s" % (name, got))
            if name == "symlink_shadow":
                check("denied" in err.lower() or "not readable" in err.lower() or "permission" in err.lower(),
                      "symlink to /etc/shadow: the cabinet user cannot read it (%s)" % err)
        valid_b64 = base64.b64encode(SAMPLES[0][1]).decode()
        check(valid_b64 not in log and valid_b64[:24] not in log and "censored" in log,
              "the state content never shows in -v output (no_log)")
    finally:
        shutil.rmtree(b, ignore_errors=True)


if __name__ == "__main__":
    main(globals())
