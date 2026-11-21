#!/usr/bin/env python3
"""Wi-Fi power save + reconnect polkit rule of role `cabinet_tweaks` (#213). Pure stdlib; pytest-compatible or direct:

    python3 ansible/roles/cabinet_tweaks/tests/test_wifi_powersave.py

Rules (each also run on a deliberately broken copy that MUST be flagged):
  dropin    the NM conf.d drop-in renders as INI: [connection] wifi.powersave = 2 and nothing else; root 0644; it
            notifies the conf reload; hk_wifi_powersave true removes it
  nm        no NetworkManager -> every write is skipped and a SKIP message says why (the test VM has no NM)
  link      the role never drops the link: no `connection up`, `device reapply/disconnect`, NM restart, `networking
            off` in the tasks or handlers; the reload is `nmcli general reload conf`
  polkit    one rule: user coindojo, exactly org.freedesktop.NetworkManager.network-control, one YES, no prefix match,
            no group grant; braces balance; with JavaScriptCore (macOS jsc) or node the rule is EVALUATED against a
            polkit stub (SKIP otherwise); a syntax error must fail that evaluation
  levers    the defaults are pass-throughs to hk_wifi_powersave / hk_wifi_reconnect_polkit; network.yml declares them
            (power save default false = off, the polkit rule default true); no SSID in any of these files
"""
import configparser
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(ROLE)))
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
from hktest import check, main, skip  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


WIFI = read(ROLE, "tasks", "wifi.yml")
MAIN = read(ROLE, "tasks", "main.yml")
HANDLERS = read(ROLE, "handlers", "main.yml")
DEFAULTS = read(ROLE, "defaults", "main.yml")
POLKIT_SRC = read(ROLE, "templates", "51-coin-dojo-net.rules.j2")
POLKIT = POLKIT_SRC.replace("{{ cabinet_user }}", "coindojo")
NETWORK_YML = read(REPO, "config", "variables.d", "network.yml")
NM_ACTION = "org.freedesktop.NetworkManager.network-control"


def tasks(text):
    """[(name, body)] of the top-level `- name:` tasks of a task file."""
    parts = re.split(r"(?m)^- name: ", text)[1:]
    return [(p.split("\n", 1)[0].strip(), p) for p in parts]


def task(text, start):
    for name, body in tasks(text):
        if name.startswith(start):
            return body
    return ""


def dropin_content(wifi):
    """The `content: |` block of the drop-in task, dedented."""
    body = task(wifi, "Wi-Fi power save off for every")
    m = re.search(r"(?ms)^    content: \|\n(.*?)(?=^    \S)", body)
    if not m:
        return None
    return "".join(l[6:] + "\n" for l in m.group(1).splitlines())


# ---- rules over text: each returns a list of problems (empty = ok) --------------------------------------------------
def rule_dropin(wifi):
    p = []
    text = dropin_content(wifi)
    if text is None:
        return ["no drop-in content block"]
    ini = configparser.ConfigParser(interpolation=None)
    try:
        ini.read_string(text)
    except configparser.Error as exc:
        return ["drop-in is not valid INI: %s" % exc]
    if ini.sections() != ["connection"]:
        p.append("drop-in sections are not exactly [connection]: %s" % ini.sections())
    elif dict(ini["connection"]) != {"wifi.powersave": "2"}:
        p.append("drop-in [connection] is not exactly wifi.powersave = 2: %s" % dict(ini["connection"]))
    body = task(wifi, "Wi-Fi power save off for every")
    if "dest: \"{{ cabinet_nm_dropin }}\"" not in body or 'mode: "0644"' not in body or "owner: root" not in body:
        p.append("drop-in is not root 0644 at cabinet_nm_dropin")
    if "notify: reload networkmanager conf" not in body:
        p.append("drop-in change does not reload the NM conf")
    if "not cabinet_wifi_powersave | bool" not in body:
        p.append("drop-in is not guarded by hk_wifi_powersave (false = off)")
    off = task(wifi, "No power-save drop-in")
    if "state: absent" not in off or "cabinet_wifi_powersave | bool" not in off:
        p.append("hk_wifi_powersave true does not remove the drop-in")
    if not re.search(r"(?m)^cabinet_nm_dropin: /etc/NetworkManager/conf\.d/\S+\.conf\s", DEFAULTS):
        p.append("cabinet_nm_dropin is not a .conf under /etc/NetworkManager/conf.d")
    return p


WRITE_MODULES = ("ansible.builtin.copy", "ansible.builtin.template", "ansible.builtin.file",
                 "ansible.builtin.lineinfile", "ansible.builtin.command", "ansible.builtin.shell")


def rule_nm_absent(wifi):
    """Every task after the NM probe that writes or runs nmcli is guarded by cabinet_nm_present; a SKIP says why."""
    p = []
    probe = task(wifi, "Is NetworkManager installed?")
    if "path: \"{{ cabinet_nm_binary }}\"" not in probe or "register: cabinet_nm_stat" not in probe:
        p.append("no NetworkManager probe (stat cabinet_nm_binary)")
    msg = task(wifi, "Say that the Wi-Fi tuning is skipped")
    if "when: not cabinet_nm_present" not in msg or "SKIP" not in msg or "not installed" not in msg:
        p.append("no SKIP message when NetworkManager is missing")
    for name, body in tasks(wifi):
        if any(m + ":" in body for m in WRITE_MODULES):
            when = re.search(r"(?m)^  when: (.*)$", body)
            if not when or not re.match(r"cabinet_nm_present\b", when.group(1)):
                p.append("task %r runs without NetworkManager (when must start with cabinet_nm_present)" % name)
    if "ansible.builtin.import_tasks: wifi.yml" not in MAIN:
        p.append("tasks/main.yml does not import wifi.yml")
    return p


LINK_DROPS = re.compile(r"nmcli\s+(c|con|connection)\s+(up|down|reload)\b|nmcli\s+(d|dev|device)\s+"
                        r"(reapply|disconnect|connect|wifi\s+connect)\b|nmcli\s+(n|networking)\s+off|nmcli\s+radio"
                        r"|systemctl\s+(restart|stop|reload)\s+NetworkManager|ansible\.builtin\.(service|systemd)")


def rule_no_link_drop(wifi, handlers):
    p = []
    # debug messages may NAME the reconnect command for a person; only module bodies count
    code = "\n".join(b for _n, b in tasks(wifi) + tasks(handlers) if "ansible.builtin.debug" not in b)
    hit = LINK_DROPS.search(code)
    if hit:
        p.append("the role can drop the Wi-Fi link: %r" % hit.group(0))
    h = task(handlers, "reload networkmanager conf")
    if "cmd: nmcli general reload conf" not in h:
        p.append("handler `reload networkmanager conf` is not `nmcli general reload conf`")
    return p


def rule_polkit(text):
    p = []
    ids = re.findall(r'action\.id\s*==\s*"([^"]+)"', text)
    if ids != [NM_ACTION]:
        p.append("polkit action ids are not exactly network-control: %s" % ids)
    code = re.sub(r"//[^\n]*", "", text)
    if re.search(r"indexOf|startsWith|\.match\(|\.test\(|RegExp|/\^org|action\.id\s*!=", code):
        p.append("polkit rule matches action ids by prefix/regex/negation")
    if 'subject.user == "coindojo"' not in code:
        p.append("polkit rule is not limited to the cabinet user")
    if code.count("polkit.Result.YES") != 1 or re.search(r"addRule\(function\([^)]*\)\s*\{\s*return", code):
        p.append("polkit rule returns YES unconditionally or more than once")
    if re.search(r"isInGroup|\|\|", code):
        p.append("polkit rule grants by group or has an `||` branch (wider than one user + one action)")
    if code.count("polkit.addRule(") != 1:
        p.append("not exactly one polkit.addRule")
    for a, b in ("()", "{}", "[]"):
        if code.count(a) != code.count(b):
            p.append("unbalanced %s%s in the polkit rule" % (a, b))
    return p


def js_engine():
    for c in (os.environ.get("HK_JSC", ""),
              "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc",
              shutil.which("node") or "", shutil.which("jsc") or ""):
        if c and os.access(c, os.X_OK):
            return c
    return None


# A polkit stub: the rule file runs as-is, then each case prints "<case>=<result>" (undefined = falls through).
HARNESS = """
var rules = [];
var polkit = { Result: { YES: "yes", NO: "no", AUTH_ADMIN: "auth_admin" },
               addRule: function (f) { rules.push(f); }, log: function () {} };
%s
var cases = [
  ["ssh",      "%s", "coindojo", false, false],
  ["seat",     "%s", "coindojo", true,  true ],
  ["other",    "%s", "pi",     false, false],
  ["modify",   "org.freedesktop.NetworkManager.settings.modify.system", "coindojo", false, false],
  ["prefix",   "org.freedesktop.NetworkManager.network-control.x", "coindojo", false, false],
  ["netoff",   "org.freedesktop.NetworkManager.enable-disable-network", "coindojo", false, false],
  ["share",    "org.freedesktop.NetworkManager.wifi.share.open", "coindojo", false, false]
];
var out = [];
for (var i = 0; i < cases.length; i++) {
  var c = cases[i], r;
  for (var j = 0; j < rules.length; j++) {
    r = rules[j]({ id: c[1] }, { user: c[2], local: c[3], active: c[4], isInGroup: function () { return true; } });
    if (r !== undefined) break;
  }
  out.push(c[0] + "=" + r);
}
var line = out.join(" ");
if (typeof print === "function") print(line); else console.log(line);
""" % ("%s", NM_ACTION, NM_ACTION, NM_ACTION)


def eval_rule(engine, rule_text):
    """Run the rule under a polkit stub; returns (rc, stdout+stderr)."""
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "rule.js")
        with open(f, "w") as fh:
            fh.write(HARNESS % rule_text)
        r = subprocess.run([engine, f], capture_output=True, text=True, timeout=30)
        return r.returncode, " | ".join((r.stdout + r.stderr).strip().splitlines())


WANT = "ssh=yes seat=yes other=undefined modify=undefined prefix=undefined netoff=undefined share=undefined"


# ---- the real files ----------------------------------------------------------------------------------------------
def test_dropin():
    probs = rule_dropin(WIFI)
    check(not probs, "drop-in: [connection] wifi.powersave = 2 only, root 0644, reload notified %s" % (probs or ""))


def test_nm_absent_skips():
    probs = rule_nm_absent(WIFI)
    check(not probs, "no NetworkManager: every write skipped, a SKIP message says why %s" % (probs or ""))


def test_no_link_drop():
    probs = rule_no_link_drop(WIFI, HANDLERS)
    check(not probs, "the role never drops the link; the reload is `nmcli general reload conf` %s" % (probs or ""))


def test_polkit_text():
    probs = rule_polkit(POLKIT)
    check(not probs, "polkit: coindojo, network-control only, one YES %s" % (probs or ""))
    t = task(WIFI, "Polkit - the cabinet user")
    check('mode: "0644"' in t and "owner: root" in t and "dest: \"{{ cabinet_net_polkit_file }}\"" in t,
          "polkit rule is root 0644 at cabinet_net_polkit_file")
    check(re.search(r"(?m)^cabinet_net_polkit_file: /etc/polkit-1/rules\.d/5[1-9]-coin-dojo-net\.rules\s", DEFAULTS),
          "polkit rule file sorts after role settings' 50-coin-dojo-settings.rules")
    off = task(WIFI, "No reconnect polkit rule")
    check("state: absent" in off and "not cabinet_wifi_reconnect_polkit | bool" in off,
          "hk_wifi_reconnect_polkit false removes the rule")


def test_polkit_evaluates():
    engine = js_engine()
    if not engine:
        skip("no JavaScript engine (macOS jsc or node): the polkit rule is checked by text only")
        return
    rc, out = eval_rule(engine, POLKIT)
    check(rc == 0 and out == WANT, "polkit rule evaluated (%s): %s" % (os.path.basename(engine), out))
    # With role settings' seat rule in front, nothing more than its three actions + this one becomes YES.
    seat = os.path.join(REPO, "ansible", "roles", "settings", "templates", "50-coin-dojo-settings.rules.j2")
    if os.path.exists(seat):
        # Drop Jinja block tags ({% if %}/{% endif %} lines) so every optional block of the seat rule is in play.
        seat_js = re.sub(r"(?m)^\s*\{%.*?%\}\s*$\n?", "", read(seat)).replace("{{ settings_user }}", "coindojo")
        both = seat_js + "\n" + POLKIT
        rc, out = eval_rule(engine, both)
        check(rc == 0 and out == WANT,
              "with the settings seat rule first: SSH gets network-control only (%s)" % out)


def test_scope_is_stated():
    """Security #213: the rule and the lever say what network-control really allows, and what it does not."""
    for fname, text in (("rule", POLKIT_SRC), ("network.yml", NETWORK_YML)):
        flat = " ".join(text.split())
        for words in ("SAVED connection", "unmanaged", "locks you out", "IP, DNS, routes", "does NOT"):
            check(words in flat, "%s states the network-control scope: %r" % (fname, words))


def test_levers():
    for name, var in (("cabinet_wifi_powersave", "hk_wifi_powersave"),
                      ("cabinet_wifi_reconnect_polkit", "hk_wifi_reconnect_polkit")):
        check(re.search(r'(?m)^%s: "\{\{ %s \}\}"\s*$' % (name, var), DEFAULTS),
              "%s is a pass-through to %s" % (name, var))
    check(re.search(r"(?m)^  hk_wifi_powersave:\n    type: bool\n    default: false\s*$", NETWORK_YML),
          "hk_wifi_powersave: bool, default false (= power save off)")
    check(re.search(r"(?m)^  hk_wifi_reconnect_polkit:\n    type: bool\n    default: true\s*$", NETWORK_YML),
          "hk_wifi_reconnect_polkit: bool, default true")
    for fname, text in (("wifi.yml", WIFI), ("rule", POLKIT_SRC), ("network.yml", NETWORK_YML)):
        check(not re.search(r"(?i)\bssid\s*[:=]", text), "%s names no SSID" % fname)


# ---- broken cases: each MUST be flagged ----------------------------------------------------------------------------
def test_broken_dropin():
    for bad, good, what in (("      wifi.powersave = 2", "      wifi.powersave = 3", "power save ON"),
                      ("      [connection]", "      [connection-wifi]", "wrong section"),
                      ("      [connection]\n", "", "no section header (INI error)"),
                      ("      wifi.powersave = 2\n", "      wifi.powersave = 2\n      wifi.cloned-mac-address = random\n",
                       "a second key")):
        check(rule_dropin(WIFI.replace(bad, good, 1)) != [], "BROKEN CASE: drop-in with %s is flagged" % what)
    check(rule_dropin(WIFI.replace("notify: reload networkmanager conf", "", 1)) != [],
          "BROKEN CASE: drop-in without the reload is flagged")


def test_broken_nm_absent():
    unguarded = WIFI.replace("  when: cabinet_nm_present and not cabinet_wifi_powersave | bool\n  notify",
                             "  notify", 1)
    check(unguarded != WIFI and rule_nm_absent(unguarded) != [],
          "BROKEN CASE: the drop-in written without NetworkManager is flagged")
    nomsg = WIFI.replace("SKIP Wi-Fi power save", "Wi-Fi power save", 1)
    check(rule_nm_absent(nomsg) != [], "BROKEN CASE: no SKIP message is flagged")
    later = re.sub(r"(?m)^  when: cabinet_nm_present( and not coin_dojo_image[^\n]*)?\n", "  when: true\n", WIFI, count=1)
    check(later != WIFI and rule_nm_absent(later) != [], "BROKEN CASE: the nmcli read without NetworkManager is flagged")


def test_broken_link_drop():
    for planted in ("    cmd: nmcli connection up uuid x", "    cmd: nmcli device reapply wlan0",
                    "    cmd: systemctl restart NetworkManager", "  ansible.builtin.service:\n    name: NetworkManager"):
        bad = HANDLERS + "\n- name: planted\n  ansible.builtin.command:\n%s\n" % planted
        if "service:" in planted:
            bad = HANDLERS + "\n- name: planted\n%s\n    state: restarted\n" % planted
        check(rule_no_link_drop(WIFI, bad) != [], "BROKEN CASE: %r is flagged" % planted.strip())
    check(rule_no_link_drop(WIFI, HANDLERS.replace("nmcli general reload conf", "nmcli general reload")) != [],
          "BROKEN CASE: a full `nmcli general reload` (not conf only) is flagged")


def test_broken_polkit():
    for bad, good, what in (('action.id == "%s"' % NM_ACTION, 'action.id.indexOf("org.freedesktop.NetworkManager.") == 0',
                       "prefix match"),
                      ('subject.user == "coindojo" &&', 'subject.isInGroup("netdev") &&', "group grant"),
                      ('action.id == "%s"' % NM_ACTION,
                       '(action.id == "%s" || action.id == "org.freedesktop.NetworkManager.settings.modify.system")'
                       % NM_ACTION, "a second action"),
                      ("    if (", "    return polkit.Result.YES;\n    if (", "unconditional YES"),
                      ("    }\n});", "    }\n);", "unbalanced braces")):
        check(POLKIT.replace(bad, good, 1) != POLKIT and rule_polkit(POLKIT.replace(bad, good, 1)) != [],
              "BROKEN CASE: polkit %s is flagged" % what)


def test_broken_polkit_evaluates():
    engine = js_engine()
    if not engine:
        skip("no JavaScript engine: the evaluated broken cases do not run")
        return
    rc, out = eval_rule(engine, POLKIT.replace("    }\n});", "    }\n);", 1))
    check(rc != 0 or out != WANT, "BROKEN CASE: a polkit syntax error fails the evaluation (%s)" % out[:80])
    wide = POLKIT.replace('subject.user == "coindojo" &&', "true &&", 1)
    rc, out = eval_rule(engine, wide)
    check(out != WANT, "BROKEN CASE: a rule for every user is caught by the evaluation (%s)" % out)


if __name__ == "__main__":
    main(globals())
