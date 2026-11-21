#!/usr/bin/env python3
"""SETTINGS opens directly (#195): the ES-DE game-select script of the `settings` role. Pure stdlib + /bin/sh.

    python3 ansible/roles/settings/tests/test_settings_hook.py

The template is rendered with test paths (PYTHON = /bin/sh, APP = a fake app that logs its argv and sleeps), then
run the way ES-DE 3.5.0 runs it (Scripting.cpp: one `sh -c` string, each argument in double quotes). Rules, each also
run on a deliberately broken copy that MUST be flagged:
  enter     the SETTINGS gamelist's game-select (system `settings`, a .hks path) runs the app once, on the entry file
  others    any other system, a fast-scroll event (no args), a system-select style call (system name as $1, its folder
            as $3), a non-.hks path and a missing entry file never run anything
  one copy  a second event while the app runs starts no second app; a lock left by a dead hook is taken over; the
            lock is gone after the app exits; a lock without a pid yet means "another hook is starting": no launch
  game-end  after ES-DE's own launch of the entry (its game-end copy runs), the one game-select that follows does not
            reopen the app; entering again later does; a game-end of another system or an old mark skips nothing
  cheap     before the system check the script uses only variable assignments and builtins (ES-DE waits for it on
            every gamelist cursor stop)
  back      (#224) after the app exits, the return helper (esde_return.py: flush + one Back) runs exactly once, in the
            foreground, after the app; never when the app did not run (other system, lock held, game-end mark,
            game-end copy), never with settings_hook_back off
  hold      (#225) the early-keyboard holder (`esde_return.py --hold`) starts once per app run, in the background (the
            hook never waits for it), before the app; never with settings_hook_back off, never when the app did not run
  quit      (#224) entering QUIT TO MAIN MENU (system `quit`, a .hks path) runs `bash <esde_entry.sh> menu <path>` once;
            its game-end copy, a non-.hks path, a missing script run nothing; it never opens SETTINGS
  wiring    the role installs it under ES-DE's scripts/game-select/ and scripts/game-end/, mode 0755; settings_system_name == the nested
            build's slug; the app command == es_systems' <command>; es_settings gets the three keys (hk_esde_settings)
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROLE = os.path.dirname(HERE)
ANS = os.path.dirname(os.path.dirname(ROLE))
REPO = os.path.dirname(ANS)
sys.path.insert(0, os.path.join(REPO, "skills", "collections"))
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
from hktest import check, main  # noqa: E402

TEMPLATE = os.path.join(ROLE, "templates", "esde-settings-hook.sh.j2")
NESTED_PATH = "/data/nested/settings/Settings.hks"


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


FAKE_APP = """#!/bin/sh
echo "$$ $*" >> "$HOOK_TEST_LOG"
sleep "${HOOK_TEST_SLEEP:-0}"
echo app-done >> "$HOOK_TEST_ORDER"
"""
FAKE_RETURN = """#!/bin/sh
if [ "$1" = --hold ]; then echo hold >> "$HOOK_TEST_HOLD"; sleep "${HOOK_TEST_HOLD_SLEEP:-0}"; exit 0; fi
sleep "${HOOK_TEST_RETURN_SLEEP:-0}"
echo back >> "$HOOK_TEST_ORDER"
"""
FAKE_QUIT = """#!/bin/sh
echo "$*" >> "$HOOK_TEST_QUIT"
"""
QUIT_PATH = "/data/nested/quit/Quit.hks"
QUIT = (QUIT_PATH, "Quit to main menu", "quit", "QUIT TO MAIN MENU")


class Env:
    """A rendered hook + fake app + lock dir in a temp dir."""

    def __init__(self, text=None, entry=True, back="on", quit_script=True):
        self.d = tempfile.mkdtemp(prefix="hk-hook-")
        self.hold_sleep = 0
        self.ret = os.path.join(self.d, "esde_return.py")
        self.quit = os.path.join(self.d, "esde_entry.sh")
        self.order = os.path.join(self.d, "order.log")
        self.quit_log = os.path.join(self.d, "quit.log")
        self.hold = os.path.join(self.d, "hold.log")
        with open(self.ret, "w") as f:
            f.write(FAKE_RETURN)
        if quit_script:
            with open(self.quit, "w") as f:
                f.write(FAKE_QUIT)
        self.app = os.path.join(self.d, "settings_app.py")
        self.entry = os.path.join(self.d, "settings.hks")
        self.log = os.path.join(self.d, "launch.log")
        self.run_dir = os.path.join(self.d, "run")
        os.mkdir(self.run_dir)
        with open(self.app, "w") as f:
            f.write(FAKE_APP)
        if entry:
            with open(self.entry, "w") as f:
                f.write("{}\n")
        body = render(text if text is not None else read(TEMPLATE), self.app, self.entry, self.ret, self.quit, back)
        self.hooks = {}
        for ev in ("game-select", "game-end"):                     # a space in the path, as a real one could have
            self.hooks[ev] = os.path.join(self.d, "ES DE", "scripts", ev, "50-coin-dojo-settings.sh")
            os.makedirs(os.path.dirname(self.hooks[ev]))
            with open(self.hooks[ev], "w") as f:
                f.write(body)
            os.chmod(self.hooks[ev], 0o755)
        self.ended = os.path.join(self.run_dir, "coin-dojo-settings-hook.ended")
        self.lock = os.path.join(self.run_dir, "coin-dojo-settings-hook.lock")

    def env(self, sleep=0, ret_sleep=0):
        e = dict(os.environ, HOOK_TEST_LOG=self.log, HOOK_TEST_SLEEP=str(sleep), XDG_RUNTIME_DIR=self.run_dir,
                 HOOK_TEST_ORDER=self.order, HOOK_TEST_QUIT=self.quit_log, HOOK_TEST_RETURN_SLEEP=str(ret_sleep),
                 HOOK_TEST_HOLD=self.hold, HOOK_TEST_HOLD_SLEEP=str(self.hold_sleep))
        return e

    def cmd(self, ev, *args):
        """The string ES-DE hands to system(): the script and every argument, non-empty ones in double quotes."""
        out = '"%s"' % self.hooks[ev]
        for a in (list(args) + [""] * 4)[:4]:
            out += " " + ('"%s"' % a if a and not a.startswith('"') else a)
        return out

    def fire(self, *args, sleep=0, wait=True, ev="game-select", ret_sleep=0):
        p = subprocess.Popen(["/bin/sh", "-c", self.cmd(ev, *args)], env=self.env(sleep, ret_sleep),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if wait:
            p.wait(timeout=20)
        return p

    def launches(self):
        try:
            with open(self.log) as f:
                return [ln.split(" ", 1)[1] if " " in ln else "" for ln in f.read().splitlines()]
        except FileNotFoundError:
            return []

    def lines(self, path):
        try:
            with open(path) as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def close(self):
        shutil.rmtree(self.d, ignore_errors=True)


def render(text, app, entry, ret="/r", quit_script="/q", back="on"):
    """The template with test values in place of its Jinja lines (shlex.quote = Ansible's `quote`)."""
    subs = {
        "{{ settings_system_name | quote }}": shlex.quote("settings"),
        "{{ settings_quit_system | quote }}": shlex.quote("quit"),
        "{{ settings_python | quote }}": shlex.quote("/bin/sh"),
        "{{ (settings_dir ~ '/settings_app.py') | quote }}": shlex.quote(app),
        "{{ (settings_dir ~ '/esde_return.py') | quote }}": shlex.quote(ret),
        "{{ settings_hook_back | bool | ternary('on', 'off') }}": back,
        "{{ settings_entry | quote }}": shlex.quote(entry),
        "{{ settings_quit_script | quote }}": shlex.quote(quit_script),
        "{{ settings_hook_file }}": "~/ES-DE/scripts/game-select/50-coin-dojo-settings.sh",
    }
    for k, v in subs.items():
        text = text.replace(k, v)
    return text


ENTER = (NESTED_PATH, "Settings", "settings", "SETTINGS")


def _cheap(text, bad):
    """Nothing but assignments (and comments) before the system check: no fork on other systems' events."""
    body = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    try:
        upto = body[:next(i for i, ln in enumerate(body) if ln.startswith(('[ "$3" =', 'case "$3" in')))]
    except StopIteration:
        upto = body
        bad.append("cheap: no `case \"$3\" in` system check")
    for ln in upto:
        if not re.fullmatch(r'[A-Z_]+=("[^"$`]*(\$\{XDG_RUNTIME_DIR:-/tmp\})?[^"$`]*"|\{\{[^}]*\}\})', ln):
            bad.append("cheap: before the system check: %s" % ln)


def _enter(text, bad):
    e = Env(text)
    try:
        e.fire(*ENTER)
        if e.launches() != [e.entry]:
            bad.append("enter: entering SETTINGS ran %r, want one launch on the entry file" % e.launches())
        if os.path.exists(e.lock):
            bad.append("enter: lock left after the app exited")
    finally:
        e.close()


def _game_end(text, bad):
    e = Env(text)
    try:
        e.fire(*ENTER, ev="game-end")        # ES-DE launched the entry itself and is back
        e.fire(*ENTER)                       # ...and re-fires game-select while saving lastplayed
        if e.launches():
            bad.append("game-end: the game-select after ES-DE's own launch reopened the app")
        e.fire(*ENTER)                       # the user enters SETTINGS again later
        if e.launches() != [e.entry]:
            bad.append("game-end: entering after the skipped event ran %r (want one launch)" % e.launches())
    finally:
        e.close()
    e = Env(text)
    try:
        e.fire("/data/nested/cps2/game.zip", "Some Game", "cps2", "CPS2", ev="game-end")
        e.fire(*ENTER)
        if e.launches() != [e.entry]:
            bad.append("game-end: another system's game-end blocked entering SETTINGS (%r)" % e.launches())
    finally:
        e.close()
    for name, mark in (("old mark (60 s)", str(int(time.time()) - 60)), ("garbage mark", "x;y")):
        e = Env(text)
        try:
            with open(e.ended, "w") as f:
                f.write(mark + "\n")
            e.fire(*ENTER)
            if e.launches() != [e.entry] or os.path.exists(e.ended):
                bad.append("game-end: %s skipped entering or stayed (%r)" % (name, e.launches()))
        finally:
            e.close()


def _others(text, bad):
    for name, args in (("another system", ("/data/nested/cps2/game.zip", "Some Game", "cps2", "CPS2")),
                       ("another system's .hks", ("/data/nested/trophies/Trophies.hks", "RetroAchievements", "trophies",
                                                  "TROPHIES")),
                       ("fast scroll (no args)", ()),
                       ("system-select style call", ("settings", "SETTINGS", "/data/nested/settings")),
                       ("non-.hks path in settings", ("/data/nested/settings/readme.txt", "x", "settings", "SETTINGS"))):
        e = Env(text)
        try:
            e.fire(*args)
            if e.launches():
                bad.append("others: %s ran %r" % (name, e.launches()))
        finally:
            e.close()
    e = Env(text, entry=False)
    try:
        e.fire(*ENTER)
        if e.launches():
            bad.append("others: a missing entry file still ran the app")
    finally:
        e.close()


def _one_copy(text, bad):
    e = Env(text)
    try:
        running = e.fire(*ENTER, sleep=0.5, wait=False)
        t0 = time.time()
        while not e.launches() and time.time() - t0 < 5:
            time.sleep(0.05)
        e.fire(*ENTER)                       # second event while the first app runs
        still = running.poll() is None
        running.wait(timeout=20)
        if len(e.launches()) != 1 or not still:
            bad.append("one copy: %d launches while the app ran (want 1)" % len(e.launches()))
        if os.path.exists(e.lock):
            bad.append("one copy: lock left after the app exited")
    finally:
        e.close()
    e = Env(text)
    try:
        os.mkdir(e.lock)
        dead = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
        dead.wait()
        with open(os.path.join(e.lock, "pid"), "w") as f:
            f.write("%d\n" % dead.pid)
        e.fire(*ENTER)
        if e.launches() != [e.entry]:
            bad.append("one copy: a lock from a dead hook was not taken over (%r)" % e.launches())
    finally:
        e.close()
    e = Env(text)
    try:
        os.mkdir(e.lock)                     # no pid yet: another hook is starting
        e.fire(*ENTER)
        if e.launches():
            bad.append("one copy: launched while another hook held the lock without a pid yet")
    finally:
        e.close()


def _back(text, bad):
    e = Env(text)
    try:
        e.fire(*ENTER, ret_sleep=0.3)
        got = e.lines(e.order)
        if got != ["app-done", "back"]:
            bad.append("back: after the app the return helper ran %r (want app-done then one back, in the foreground)"
                       % got)
    finally:
        e.close()
    for name, how in (("another system", lambda e: e.fire("/data/nested/cps2/g.zip", "G", "cps2", "CPS2")),
                      ("game-end copy", lambda e: e.fire(*ENTER, ev="game-end")),
                      ("quit system", lambda e: e.fire(*QUIT))):
        e = Env(text)
        try:
            how(e)
            if "back" in e.lines(e.order):
                bad.append("back: %s sent a Back" % name)
        finally:
            e.close()
    e = Env(text)
    try:
        os.mkdir(e.lock)                     # another hook holds the app: this one launches nothing, sends nothing
        e.fire(*ENTER)
        if e.lines(e.order):
            bad.append("back: a hook that did not run the app sent a Back (%r)" % e.lines(e.order))
    finally:
        e.close()
    e = Env(text, back="off")
    try:
        e.fire(*ENTER)
        if e.lines(e.order) != ["app-done"]:
            bad.append("back: settings_hook_back off still ran the helper (%r)" % e.lines(e.order))
    finally:
        e.close()


def _hold(text, bad):
    e = Env(text)
    try:
        e.hold_sleep = 3
        t0 = time.time()
        e.fire(*ENTER)
        took = time.time() - t0
        deadline = time.time() + 5
        while not e.lines(e.hold) and time.time() < deadline:
            time.sleep(0.05)
        if e.lines(e.hold) != ["hold"]:
            bad.append("hold: the holder started %r (want once)" % e.lines(e.hold))
        if took >= 2.5:
            bad.append("hold: the hook waited for the holder (%.1f s): it must run in the background" % took)
        if e.lines(e.order) != ["app-done", "back"]:
            bad.append("hold: the app + plain Back run changed (%r)" % e.lines(e.order))
    finally:
        e.close()
    lines = [ln.strip() for ln in text.splitlines()]
    hl = [i for i, ln in enumerate(lines) if "--hold" in ln and not ln.startswith("#")]
    app = [i for i, ln in enumerate(lines) if ln.startswith('"$PYTHON" "$APP"')]
    if not hl or not app or hl[0] > app[0]:
        bad.append("hold: the holder does not start before the app")
    for name, kw, how in (("settings_hook_back off", {"back": "off"}, lambda e: e.fire(*ENTER)),
                          ("another system", {}, lambda e: e.fire("/data/nested/cps2/g.zip", "G", "cps2", "CPS2")),
                          ("game-end copy", {}, lambda e: e.fire(*ENTER, ev="game-end"))):
        e = Env(text, **kw)
        try:
            how(e)
            time.sleep(0.2)
            if e.lines(e.hold):
                bad.append("hold: %s started the holder" % name)
        finally:
            e.close()


def _quit(text, bad):
    e = Env(text)
    try:
        e.fire(*QUIT)
        if e.lines(e.quit_log) != ["menu " + QUIT_PATH]:
            bad.append("quit: entering QUIT ran %r (want one `menu <path>`)" % e.lines(e.quit_log))
        if e.launches():
            bad.append("quit: entering QUIT opened SETTINGS")
    finally:
        e.close()
    for name, kw, how in (("game-end copy", {}, lambda e: e.fire(*QUIT, ev="game-end")),
                          ("non-.hks path", {}, lambda e: e.fire("/data/nested/quit/x.txt", "x", "quit", "Q")),
                          ("missing script", {"quit_script": False}, lambda e: e.fire(*QUIT)),
                          ("settings entered", {}, lambda e: e.fire(*ENTER))):
        e = Env(text, **kw)
        try:
            how(e)
            if e.lines(e.quit_log):
                bad.append("quit: %s ran the quit command (%r)" % (name, e.lines(e.quit_log)))
        finally:
            e.close()


def violations(text, first=False):
    """Every rule the hook text breaks (the real template); first=True stops at the first one (broken copies:
    process starts are slow on macOS, so the static and the most telling checks go first)."""
    bad = []
    if "{{" in render(text, "/x", "/y"):
        bad.append("template has a Jinja expression the test does not know")
    for section in (_cheap, _enter, _game_end, _others, _one_copy, _back, _hold, _quit):
        if first and bad:
            break
        section(text, bad)
    return bad


def test_hook_rules():
    bad = violations(read(TEMPLATE))
    check(not bad, "the game-select hook keeps every rule%s" % ("" if not bad else ": " + "; ".join(bad)))


def test_broken_copies_are_flagged():
    t = read(TEMPLATE)
    cases = {
        "no system check": t.replace('  *) exit 0 ;;\nesac\n', '  *) ;;\nesac\n'),
        "matches any system starting with settings": t.replace('  "$SYSTEM") ;;', '  "$SYSTEM"*) ;;')
                                                      .replace('  *) exit 0 ;;\nesac\n', '  *) ;;\nesac\n'),
        "no .hks check": t.replace('esac\ncase "$1" in *.hks) ;; *) exit 0 ;; esac\n', "esac\n"),
        "app on $1 instead of the entry": t.replace('"$PYTHON" "$APP" "$ENTRY"', '"$PYTHON" "$APP" "$1"'),
        "no lock": t.replace('if ! mkdir "$LOCK" 2>/dev/null; then', "if false; then"),
        "lock never released": t.replace("trap 'rm -rf \"$LOCK\"' EXIT\n", ""),
        "no stale-lock takeover": t.replace('rm -rf "$LOCK"\n  mkdir', "exit 0\n  mkdir"),
        "empty pid = stale": t.replace('[ -z "$pid" ] || kill', "kill"),
        "no entry-file check": t.replace('[ -f "$ENTRY" ] || exit 0\n', ""),
        "external command before the system check": t.replace('case "$3" in',
                                                              'NOW=$(date +%s)\ncase "$3" in'),
        "app in the background": t.replace('"$PYTHON" "$APP" "$ENTRY"', '"$PYTHON" "$APP" "$ENTRY" &'),
        "no game-end branch": t.replace('  */game-end/*) date +%s > "$ENDED"; exit 0 ;;\n', ""),
        "game-end mark never consumed": t.replace('  rm -f "$ENDED"\n', ""),
        "game-end mark never expires": t.replace("-le 10 ] && exit 0", "-le 999999999 ] && exit 0"),
        "game-end of any system marks": t.replace('case "$0" in\n  */game-end/*) date +%s > "$ENDED"; exit 0 ;;\nesac\n', "")
                                         .replace('case "$3" in\n',
                                                  'case "$0" in */game-end/*) date +%s > "$ENDED"; exit 0 ;; esac\n'
                                                  'case "$3" in\n'),
        # #224
        "no Back after the app": t.replace('if [ "$BACK" = on ]; then "$PYTHON" "$RETURN"; fi', ''),
        "Back twice": t.replace('if [ "$BACK" = on ]; then "$PYTHON" "$RETURN"; fi',
                                'if [ "$BACK" = on ]; then "$PYTHON" "$RETURN"; "$PYTHON" "$RETURN"; fi'),
        "Back in the background": t.replace('then "$PYTHON" "$RETURN"; fi', 'then "$PYTHON" "$RETURN" & fi'),
        "Back before the app": t.replace('"$PYTHON" "$APP" "$ENTRY"\n', '"$PYTHON" "$RETURN"\n"$PYTHON" "$APP" "$ENTRY"\n'),
        "Back ignores the switch": t.replace('if [ "$BACK" = on ]; then "$PYTHON" "$RETURN"; fi',
                                             'if true; then "$PYTHON" "$RETURN"; fi'),
        # #225
        "holder in the foreground": t.replace('"$RETURN" --hold & fi', '"$RETURN" --hold; fi'),
        "holder ignores the switch": t.replace('if [ "$BACK" = on ]; then "$PYTHON" "$RETURN" --hold & fi',
                                               'if true; then "$PYTHON" "$RETURN" --hold & fi'),
        "holder after the app": t.replace('if [ "$BACK" = on ]; then "$PYTHON" "$RETURN" --hold & fi\n"$PYTHON" "$APP" "$ENTRY"\n',
                                          '"$PYTHON" "$APP" "$ENTRY"\nif [ "$BACK" = on ]; then "$PYTHON" "$RETURN" --hold & fi\n'),
        "no holder": t.replace('if [ "$BACK" = on ]; then "$PYTHON" "$RETURN" --hold & fi\n', ''),
        "no quit branch": t.replace('    exec bash "$QUIT_SCRIPT" menu "$1" ;;', '    exit 0 ;;'),
        "quit on its game-end": t.replace('    case "$0" in */game-end/*) exit 0 ;; esac\n    [ -f "$QUIT_SCRIPT" ]',
                                          '    [ -f "$QUIT_SCRIPT" ]'),
        "quit without the .hks check": t.replace('  "$QUIT_SYSTEM")\n    case "$1" in *.hks) ;; *) exit 0 ;; esac\n',
                                                 '  "$QUIT_SYSTEM")\n'),
        "quit runs fightcade": t.replace('"$QUIT_SCRIPT" menu "$1"', '"$QUIT_SCRIPT" fightcade "$1"'),
    }
    # each copy runs in its own temp dir: in parallel (the scenarios wait on /bin/sh, not the CPU)
    with ThreadPoolExecutor(max_workers=len(cases)) as pool:
        found = dict(zip(cases, pool.map(lambda x: violations(x, first=True), cases.values())))
    for name, text in cases.items():
        check(text != t, "broken copy '%s' differs from the template (the replace hit)" % name)
        check(found[name], "broken copy '%s' is flagged" % name)


def test_role_wiring():
    import nested
    defaults = read(ROLE, "defaults", "main.yml")
    tasks = read(ROLE, "tasks", "main.yml")
    check(re.search(r"(?m)^settings_system_name: %s\b" % re.escape(nested.SETTINGS_SLUG), defaults) is not None,
          "settings_system_name == nested.SETTINGS_SLUG")
    check(re.search(r'(?m)^settings_hook_file: "\{\{ settings_esde_home \}\}/scripts/game-select/[\w.-]+\.sh"', defaults)
          is not None, "the hook goes into ES-DE's scripts/game-select/")
    check(re.search(r'(?m)^settings_hook_end_file: "\{\{ settings_esde_home \}\}/scripts/game-end/[\w.-]+\.sh"', defaults)
          is not None, "its game-end copy goes into ES-DE's scripts/game-end/")
    i = tasks.find("src: esde-settings-hook.sh.j2")
    blk = tasks[tasks.rfind("- name:", 0, i):tasks.find("- name:", i)]
    check(i != -1 and "ansible.builtin.template:" in blk and 'dest: "{{ item }}"' in blk
          and 'loop: ["{{ settings_hook_file }}", "{{ settings_hook_end_file }}"]' in blk
          and 'mode: "0755"' in blk and "owner: \"{{ settings_user }}\"" in blk,
          "the role templates the hook to settings_hook_file and settings_hook_end_file, 0755, the cabinet user")
    # the app command the hook runs == the es_systems <command> ES-DE runs for the entry
    t = read(TEMPLATE)
    cmd = re.search(r'(?m)^"\$PYTHON" "\$APP" "\$ENTRY"$', t)
    py = re.search(r'(?m)^settings_python: (\S+)', defaults).group(1)
    check(re.search(r"(?m)^settings_quit_system: %s\b" % re.escape(nested.QUIT_SLUG), defaults) is not None,
          "settings_quit_system == nested.QUIT_SLUG")
    check(re.search(r'(?m)^settings_quit_script: "\{\{ hk_tools_dir \}\}/esde_entry.sh"', defaults) is not None
          and nested.ENTRY_SCRIPT == "/data/coin-dojo/esde_entry.sh"
          and nested.QUIT_COMMAND == "bash %s menu" % nested.ENTRY_SCRIPT
          and 'exec bash "$QUIT_SCRIPT" menu "$1"' in t,
          "quit: bash <hk_tools_dir>/esde_entry.sh menu = nested.QUIT_COMMAND")
    check(re.search(r"(?m)^settings_hook_back: true\b", defaults) is not None
          and re.search(r"(?ms)^settings_app_files: \[[^]]*esde_return\.py[^]]*\]", defaults) is not None,
          "settings_hook_back on by default; esde_return.py is installed with the app files")
    check(cmd is not None and nested.SETTINGS_COMMAND == "%s /data/coin-dojo/settings/settings_app.py" % py
          and "APP={{ (settings_dir ~ '/settings_app.py') | quote }}" in t,
          "hook command = settings_python + settings_dir/settings_app.py = nested.SETTINGS_COMMAND")


def test_es_settings_keys():
    cfg = read(REPO, "config", "variables.d", "esde.yml")
    for key, want in (("CustomEventScripts", "true"), ("CustomEventScriptsBrowsing", "true"),
                      ("CustomEventScriptsBrowsingNonBlocking", "false")):
        check(re.search(r"(?m)^      %s: \{type: bool\}$" % key, cfg) is not None, "hk_esde_settings allows %s (bool)" % key)
        check(re.search(r"(?m)^      %s: %s$" % (key, want), cfg) is not None, "hk_esde_settings default %s: %s" % (key, want))
    check("consumers: [ansible/roles/cabinet_tweaks]" in cfg, "cabinet_tweaks writes hk_esde_settings")


def test_broken_es_settings_is_flagged():
    cfg = read(REPO, "config", "variables.d", "esde.yml")
    broken = cfg.replace("CustomEventScriptsBrowsingNonBlocking: false", "CustomEventScriptsBrowsingNonBlocking: true")
    check(broken != cfg and re.search(r"(?m)^      CustomEventScriptsBrowsingNonBlocking: false$", broken) is None,
          "broken copy (non-blocking browsing events: ES-DE would not wait for the app) fails the default check")


if __name__ == "__main__":
    main(globals())
