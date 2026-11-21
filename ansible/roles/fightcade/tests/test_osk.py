#!/usr/bin/env python3
"""Tests for the Fightcade lobby's on-screen keyboard (#227): files/inject.js + the keyboard part of templates/inject.css.j2.

The JS runs in headless Chrome against a login page we wrote ourselves (fixtures/osk-page.html: same shape as a two-panel
login, none of Fightcade's code or assets); skipped, not failed, when there is no Chrome here (CI without a browser).
Run: python3 ansible/roles/fightcade/tests/test_osk.py   (HK_OSK_SHOTS=<dir> also writes preview PNGs)
Broken cases (test_broken_*): a copy of the script that leaks keys while hidden, one that logs, one that opens on any key.
#262: test_trusted_keys_in_chrome drives the REAL script (trust guard in) with trusted keys through Chrome's DevTools pipe
(Input.dispatchKeyEvent, what a real keyboard gives the page): F23 then Enter, the order stickmouse sends them, must submit
the form natively exactly once; its broken copies (an Enter made by the script, a swallowed Enter, a blur on close) fail."""
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(ROLE)))
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
from hktest import check as _check, main, skip  # noqa: E402

SAMPLE_MACROS = [{"group": "CHALLENGE", "text": "FT2?"}, {"group": "CHALLENGE", "text": "FT3?"}, {"group": "AFTER MATCH", "text": "gg"},
                 {"group": "AFTER MATCH", "text": "rematch?"}, {"group": "STATUS", "text": "brb"}, {"group": "STATUS", "text": "sorry, lag"}]


def render_js(text):
    """The Jinja template with the role's variables filled in (no Jinja here: three known substitutions)."""
    subs = {"fightcade_chat_macros": json.dumps(SAMPLE_MACROS), "fightcade_chat_macros_send": "false",
            "fightcade_macros_file": json.dumps("/data/fightcade/macros.json")}
    return re.sub(r"\{\{ (\w+) \| to_json \}\}", lambda m: subs[m.group(1)], text)


JS_TEMPLATE = open(os.path.join(ROLE, "templates", "inject.js.j2")).read()
JS = render_js(JS_TEMPLATE)
CSS_J2 = open(os.path.join(ROLE, "templates", "inject.css.j2")).read()
PAGE = open(os.path.join(ROLE, "tests", "fixtures", "osk-page.html")).read()
TEST_JS = open(os.path.join(ROLE, "tests", "fixtures", "osk-test.js")).read()
TOKENS = {k: v["hex"] for k, v in json.load(open(os.path.join(REPO, "brand", "tokens.json")))["colors"].items()}
CHROMES = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "chromium", "chromium-browser")


def chrome():
    for c in CHROMES:
        p = c if os.path.isabs(c) else shutil.which(c)
        if p and os.path.exists(p):
            return p
    return None


def osk_css():
    """Only the keyboard part of the CSS template (after the #227 comment), with the brand colours filled in."""
    part = CSS_J2[CSS_J2.index("/* #227"):]
    return re.sub(r"\{\{ fightcade_brand\.(\w+)\.hex \}\}", lambda m: TOKENS[m.group(1)], part)


# The harness dispatches script-made events (isTrusted is false for them): only this one guard line is taken out of the
# page's copy of the script; test_static_rules checks the real script has it and that it is the only difference.
TRUST_GUARD = "if (!e.isTrusted) { return; }"


def build(js=JS, test=TEST_JS, extra=""):
    js = js.replace(TRUST_GUARD, "")
    return PAGE.replace("@@CSS@@", osk_css()).replace("@@JS@@", js).replace("@@TEST@@", test + extra)


def run_chrome(html, shot=None):
    d = tempfile.mkdtemp(prefix="hk-osk-")
    try:
        page = os.path.join(d, "page.html")
        open(page, "w").write(html)
        base = [chrome(), "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars", "--window-size=1440,1080",
                "--user-data-dir=" + os.path.join(d, "profile"), "--virtual-time-budget=4000"]
        if shot:
            subprocess.run(base + ["--screenshot=" + shot, "file://" + page], capture_output=True, timeout=90)
            return None
        r = subprocess.run(base + ["--dump-dom", "file://" + page], capture_output=True, text=True, timeout=90)
        m = re.search(r'<pre id="result">(.*?)</pre>', r.stdout, re.S)
        return json.loads(m.group(1).replace("&quot;", '"')) if m else None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_static_rules():
    for bad in ("console.", "fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "localStorage", "sessionStorage",
                "indexedDB", "document.cookie", "ipcRenderer", "eval(", "new Function", ".src =", "innerHTML", "child_process",
                "http", "net.", "XDomain"):
        _check(bad not in JS, "inject.js never uses %s (what is typed goes into the page's own field only)" % bad)
    _check(set(re.findall(r"require\(([^)]*)\)", JS)) == {"'fs'"}, "the only module it loads is fs (the macro file)")
    _check(JS.count("writeFileSync") == 1 and "MACRO_FILE + '.tmp'" in JS, "one write, to the macro file (via a temp file + rename)")
    _check("fightcade_macros_file" in JS_TEMPLATE and "mode: 384" in JS, "the macro file is written 0600 at the role's data path")
    _check(TRUST_GUARD in JS and JS.count("isTrusted") == 1, "only real key events count (isTrusted guard present)")
    _check("e.key" not in JS and "e.code" in JS, "keys are matched by e.code, never e.key (xkb inet(evdev) rewrites F20-F24 keysyms)")
    codes = dict(re.findall(r"(F\d+): '(\w+)'", JS.split("var KEYS =")[1].split("};")[0]))
    _check(set(codes) == {"F13", "F14", "F15", "F16", "F17", "F18", "F19", "F23", "F24"}, "the page listens to F13-F19, F23, F24 only: %s" % sorted(codes))
    sm = open(os.path.join(REPO, "ansible", "roles", "power", "files", "stickmouse.py")).read()
    sm_codes = re.search(r"KEY_OPEN, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_PRESS, KEY_DELETE, KEY_SUBMIT, KEY_CANCEL = ([\d, ]+)", sm).group(1)
    _check([int(c) - 170 for c in sm_codes.split(",")] == [13, 14, 15, 16, 17, 18, 19, 23, 24],
           "stickmouse sends exactly the keys the page listens to (evdev 183..189, 193, 194 = F13..F19, F23, F24)")
    _check(JS.count("hide()") >= 1 and "case 'submit': refocus(); hide(); break;" in JS and "case 'cancel': hide()" in JS,
           "the page only closes on stickmouse's cancel/submit or a window blur, never on its own keys")
    sub = JS.split("function onKey(e)")[1]
    _check("macroEnter" not in sub and "KeyboardEvent" not in sub and "requestSubmit" not in sub,
           "#262: START (F23) makes no Enter of its own and calls no requestSubmit: stickmouse sends a real Enter")
    _check("Enter" not in JS.split("var KEYS =")[1].split("};")[0], "#262: Enter is not a key of the keyboard (a real Enter passes untouched)")
    _check(re.search(r"KEY_ENTER = 28\b", sm) and "self._osk_close(KEY_SUBMIT)\n                self._tap(KEY_ENTER)" in sm,
           "#262: stickmouse sends F23 (the page restores the focus and hides) and then the real Enter, in that order")
    _check("unlinkSync(tmp)" in JS and "chmodSync(tmp, 384)" in JS, "the macro file's temp copy is removed first and chmod 0600 (really 0600)")
    css = osk_css()
    _check("display: none;" in css.split("#hk-osk {")[1].split("}")[0], "the keyboard is display:none until open")
    _check(not re.search(r"#[0-9a-fA-F]{6}\b", CSS_J2.split("/* #227")[1]), "the keyboard CSS has no literal hex colour (brand tokens only)")


def test_role_never_writes_the_macro_file():
    tasks = open(os.path.join(ROLE, "tasks", "main.yml")).read()
    _check("macros.json" not in tasks and "fightcade_macros_file" not in tasks,
           "no task writes, templates, copies or removes the player's macro file: a deploy never overwrites it")
    _check("fightcade_macros_file" not in open(os.path.join(ROLE, "templates", "coin-dojo-fightcade.conf.j2")).read(), "nor does the launcher config touch it")


def test_in_chrome():
    if not chrome():
        skip("no Chrome here: the in-page keyboard behaviour is not run")
        return
    res = run_chrome(build())
    _check(res is not None, "the harness page ran and reported (headless Chrome)")
    if res is None:
        return
    want = {
        "hidden_untouched": True, "hidden_no_element": True, "opened": True, "login_class": True, "in_left_panel": True,
        "focus_still_in_field": True, "typed": "qw", "after_backspace": "q", "page_saw_nothing": True, "focus_w": "w",
        "focus_row5": "SHIFT", "shift_layer": "W", "shift_typed": "W", "closed_by_escape": True, "password_typed": True,
        "no_storage": True, "no_console": True, "enter_closed": True, "f23_makes_no_enter": True, "f23_keeps_focus": True,
        "f23_refocuses_field": True, "real_enter_passes_closed": True, "real_enter_passes_open": True, "real_enter_keeps_open": True, "closed_untouched": True,
        "close_key_keeps_open": True, "close_key_hint": "press SELECT to close", "enter_key_keeps_open": True,
        "enter_key_hint": "press START to send", "blur_hides": True, "key_while_hidden_reopens": True, "cancel_while_hidden_stays": True,
        "macro_page_focus": "FT2?", "macro_typed": "FT2?", "macro_not_sent": True, "tab_after_first": "rematch?", "tab_status_first": "brb",
        "save_hint": "saved to MINE", "saved_file": '["my own line"]\n', "mine_shows_it": True, "password_value_typed": True,
        "password_not_saved": True, "password_flash": "not saved: password field", "on_mine_line": "my own line", "forgotten": True,
        "lobby_class_off": True, "lobby_bottom_centre": True, "in_column": True,
    }
    for k, v in want.items():
        _check(res.get(k) == v, "chrome: %s == %r (got %r)" % (k, v, res.get(k)))


def test_broken_copies_are_caught():
    if not chrome():
        skip("no Chrome here")
        return
    leaky = JS.replace("if (!a) { return; }", "if (!a) { e.preventDefault(); e.stopImmediatePropagation(); return; }")
    _check(leaky != JS, "premise: the leaky copy differs")
    res = run_chrome(build(js=leaky)) or {}
    _check(res.get("hidden_untouched") is not True, "BROKEN: a copy that swallows keys while hidden fails the hidden check")
    any_key = JS.replace("var a = KEYS[e.code];\n    if (!a) { return; }", "var a = KEYS[e.code] || 'open';")
    _check(any_key != JS, "premise: the any-key copy differs")
    res = run_chrome(build(js=any_key)) or {}
    _check(res.get("hidden_untouched") is not True, "BROKEN: a copy that reacts to any key fails the hidden check")
    self_close = JS.replace("case 'close': flash('press SELECT to close'); break;", "case 'close': hide(); break;")
    _check(self_close != JS, "premise: the self-closing copy differs")
    res = run_chrome(build(js=self_close)) or {}
    _check(res.get("close_key_keeps_open") is not True, "BROKEN: a copy whose CLOSE key hides the page by itself (state mismatch) fails")
    by_key = JS.replace("var a = KEYS[e.code];", "var a = KEYS[e.key];")
    res = run_chrome(build(js=by_key)) or {}
    _check(res.get("opened") is not True, "BROKEN: a copy matching e.key instead of e.code never opens (the xkb mapping bug)")
    synth = JS.replace("case 'submit': refocus(); hide(); break;", "case 'submit': macroEnter(); hide(); break;")
    _check(synth != JS, "premise: the copy that makes its own Enter differs")
    res = run_chrome(build(js=synth)) or {}
    _check(res.get("f23_makes_no_enter") is not True, "BROKEN #262: a copy whose START makes a script Enter fails the no-synthetic check")
    stay = JS.replace("case 'submit': refocus(); hide(); break;", "case 'submit': field(); hide(); break;")
    _check(stay != JS, "premise: the copy that leaves the focus where it is differs")
    res = run_chrome(build(js=stay)) or {}
    _check(res.get("f23_refocuses_field") is not True, "BROKEN #262: a copy whose F23 leaves the focus on a clicked button fails")
    swallow = JS.replace("F24: 'cancel' };", "F24: 'cancel', Enter: 'cancel' };")
    _check(swallow != JS, "premise: the copy that swallows Enter differs")
    res = run_chrome(build(js=swallow)) or {}
    _check(res.get("real_enter_passes_open") is not True, "BROKEN #262: a copy that swallows Enter fails the pass-through check")
    logging = JS.replace("function insert(text) {", "function insert(text) {\n    console.log(text);")
    _check(logging != JS, "premise: the logging copy differs")
    res = run_chrome(build(js=logging)) or {}
    _check(res.get("no_console") is not True, "BROKEN: a copy that logs what is typed fails the no-logging check")
    _check("console." in logging, "and the static rule would flag it")


class ChromePipe:
    """Headless Chrome over --remote-debugging-pipe (fd 3 in, fd 4 out, NUL-terminated JSON): stdlib only, no websocket.
    Input.dispatchKeyEvent gives the page TRUSTED key events, as a real keyboard (or stickmouse's uinput device) does."""

    def __init__(self, page):
        self.d = tempfile.mkdtemp(prefix="hk-osk-pipe-")
        self.buf, self.n = b"", 0
        cin_r, self.cin_w = os.pipe()      # we write, Chrome reads (its fd 3)
        self.cout_r, cout_w = os.pipe()    # Chrome writes (its fd 4), we read
        hi_r, hi_w = fcntl.fcntl(cin_r, fcntl.F_DUPFD, 10), fcntl.fcntl(cout_w, fcntl.F_DUPFD, 10)
        os.close(cin_r)
        os.close(cout_w)

        def child():
            os.dup2(hi_r, 3)
            os.dup2(hi_w, 4)

        self.p = subprocess.Popen([chrome(), "--headless=new", "--disable-gpu", "--no-sandbox", "--window-size=1440,1080",
                                   "--user-data-dir=" + os.path.join(self.d, "profile"), "--remote-debugging-pipe", "about:blank"],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  preexec_fn=child, pass_fds=(3, 4))
        os.close(hi_r)
        os.close(hi_w)
        path = os.path.join(self.d, "page.html")
        open(path, "w").write(page)
        tid = self.call("Target.createTarget", url="file://" + path)["targetId"]
        self.sid = self.call("Target.attachToTarget", targetId=tid, flatten=True)["sessionId"]
        self.call("Emulation.setFocusEmulationEnabled", session=True, enabled=True)
        for _ in range(100):
            if self.js("document.readyState") == "complete":
                break
            time.sleep(0.05)

    def call(self, method, session=False, **params):
        self.n += 1
        msg = {"id": self.n, "method": method, "params": params}
        if session:
            msg["sessionId"] = self.sid
        os.write(self.cin_w, json.dumps(msg).encode() + b"\0")
        deadline = time.time() + 20
        while time.time() < deadline:
            while b"\0" in self.buf:
                raw, self.buf = self.buf.split(b"\0", 1)
                r = json.loads(raw)
                if r.get("id") == self.n:
                    if "error" in r:
                        raise RuntimeError("%s: %s" % (method, r["error"]))
                    return r.get("result", {})
            chunk = os.read(self.cout_r, 65536)
            if not chunk:
                raise RuntimeError("chrome closed the pipe")
            self.buf += chunk
        raise RuntimeError("no answer to %s" % method)

    def js(self, expr):
        return self.call("Runtime.evaluate", session=True, expression=expr, returnByValue=True)["result"].get("value")

    def key(self, code):
        """One trusted press + release: an F key (stickmouse's dedicated keys) or Enter (text CR, as a keyboard types it)."""
        vk = 13 if code == "Enter" else 111 + int(code[1:])          # VK_F1 = 0x70 = 112
        down = {"type": "keyDown", "key": "Enter", "code": "Enter", "text": "\r", "unmodifiedText": "\r"} if code == "Enter" \
            else {"type": "rawKeyDown", "key": code, "code": code}
        self.call("Input.dispatchKeyEvent", session=True, windowsVirtualKeyCode=vk, **down)
        self.call("Input.dispatchKeyEvent", session=True, type="keyUp", key=down["key"], code=code, windowsVirtualKeyCode=vk)

    def close(self):
        try:
            self.p.kill()
            self.p.wait(timeout=10)
        finally:
            os.close(self.cin_w)
            os.close(self.cout_r)
            shutil.rmtree(self.d, ignore_errors=True)


SETUP = ("window.__sub = 0; window.__seen = [];"
         "document.getElementById('f').addEventListener('submit', function (e) { e.preventDefault(); window.__sub++; });"
         "window.addEventListener('keydown', function (e) { window.__seen.push(e.key + ':' + e.isTrusted); }, false);"
         "document.getElementById('pass').focus(); 'ok'")
OPEN = "!!document.getElementById('hk-osk') && document.getElementById('hk-osk').classList.contains('hk-osk-open')"


def trusted_run(js=JS):
    """The REAL script (trust guard in) on the test login page, driven by trusted keys. Returns the facts, or None."""
    page = PAGE.replace("@@CSS@@", osk_css()).replace("@@JS@@", js).replace("@@TEST@@", "")
    c = ChromePipe(page)
    try:
        R = {}
        c.js(SETUP)
        c.key("F13")                                  # SELECT+LK: open
        R["opened"] = c.js(OPEN)
        c.key("F18")                                  # LK: type the focused key (q)
        R["typed"] = c.js("document.getElementById('pass').value")
        c.key("F23")                                  # START release: stickmouse sends F23 ...
        R["f23_closed"] = c.js(OPEN) is False
        R["focus_in_field"] = c.js("document.activeElement === document.getElementById('pass')")
        c.key("Enter")                                # ... then the real Enter
        R["submits"] = c.js("window.__sub")
        R["enter_seen_trusted"] = "Enter:true" in (c.js("window.__seen") or [])
        R["seen_no_untrusted"] = all(not s.endswith(":false") for s in (c.js("window.__seen") or []))
        c.js("window.__sub = 0")
        c.key("F13")
        c.key("Enter")                                # a real Enter while it is open: not swallowed, the page gets it
        R["open_enter_submits"] = c.js("window.__sub")
        R["open_enter_keeps_open"] = c.js(OPEN)
        c.key("F23")                                  # closed again (a lone F23, no Enter)
        # a click moved the focus to another button while the keyboard was open: F23 must bring it back to the field,
        # or the real Enter clicks that button
        c.js("window.__sub = 0; window.__clk = 0; var b = document.createElement('button'); b.id = 'other'; b.type = 'button';"
             "b.textContent = 'OTHER'; b.addEventListener('click', function () { window.__clk++; }); document.body.appendChild(b);"
             "document.getElementById('pass').focus(); 'ok'")
        c.key("F13")
        c.js("document.getElementById('other').focus(); 'ok'")
        c.key("F23")
        R["button_focus_back"] = c.js("document.activeElement === document.getElementById('pass')")
        c.key("Enter")
        R["button_submits"], R["button_clicks"] = c.js("window.__sub"), c.js("window.__clk")
        return R
    finally:
        c.close()


def test_trusted_keys_in_chrome():
    """#262: what stickmouse sends (F23, then a real Enter) submits the login form natively, once; the broken copies fail."""
    if not chrome():
        skip("no Chrome here: the trusted-key run is not done")
        return
    try:
        R = trusted_run()
    except (OSError, RuntimeError) as e:
        _check(False, "chrome over the DevTools pipe ran (%s)" % e)
        return
    _check(R["opened"] is True and R["typed"] == "q", "trusted F13 opens it, trusted F18 types q into the password field (%s)" % R)
    _check(R["f23_closed"] and R["focus_in_field"], "trusted F23 hides it and the focus stays in the field (%s)" % R)
    _check(R["submits"] == 1, "then the real Enter submits the form natively, exactly once (%s)" % R["submits"])
    _check(R["enter_seen_trusted"] and R["seen_no_untrusted"], "the page saw a trusted Enter and no script-made key (%s)" % R)
    _check(R["open_enter_submits"] == 1 and R["open_enter_keeps_open"] is True,
           "a real Enter while it is open passes untouched (the form submits once, the keyboard stays) (%s)" % R)
    _check(R["button_focus_back"] is True and R["button_submits"] == 1 and R["button_clicks"] == 0,
           "focus on a clicked button: F23 puts it back on the field, the Enter submits there and clicks nothing (%s)" % R)
    synth = JS.replace("case 'submit': refocus(); hide(); break;", "case 'submit': macroEnter(); hide(); break;")
    _check(synth != JS, "premise: the copy whose START makes its own Enter differs")
    B = trusted_run(synth)
    _check(B["submits"] != 1 or not B["seen_no_untrusted"], "BROKEN: the old way (script Enter + requestSubmit) and a real Enter double-submit (%s)" % B["submits"])
    stay = JS.replace("case 'submit': refocus(); hide(); break;", "case 'submit': field(); hide(); break;")
    B = trusted_run(stay)
    _check(B["button_clicks"] == 1 and B["button_submits"] == 0,
           "BROKEN: a copy that leaves the focus on the button: the real Enter clicks it (%s)" % B)
    swallow = JS.replace("F24: 'cancel' };", "F24: 'cancel', Enter: 'cancel' };")
    B = trusted_run(swallow)
    _check(B["open_enter_submits"] != 1, "BROKEN: a copy that swallows Enter never submits (%s)" % B["open_enter_submits"])
    blur = JS.replace("case 'submit': refocus(); hide(); break;", "case 'submit': document.activeElement.blur(); hide(); break;")
    _check(blur != JS, "premise: the blurring copy differs")
    B = trusted_run(blur)
    _check(B["submits"] == 0, "BROKEN: a copy that blurs the field on close loses the Enter (%s submits)" % B["submits"])


def test_previews():
    out = os.environ.get("HK_OSK_SHOTS")
    if not out or not chrome():
        return
    os.makedirs(out, exist_ok=True)
    state = {
        "keyboard-login-open": "document.getElementById('user').focus(); window.__hkOsk.open();",
        "keyboard-login-typing": "var u=document.getElementById('user'); u.focus(); window.__hkOsk.open();"
                                 " document.execCommand('insertText', false, 'testuser');",
        "keyboard-lobby-open": "var p=document.getElementById('pass'); p.parentNode.removeChild(p); window.__hkOsk.open();",
        "keyboard-hidden": "document.getElementById('user').focus();",
    }
    for name, js in state.items():
        run_chrome(build(test="", extra="<" + "/script><script>" + js), shot=os.path.join(out, name + ".png"))
    _check(all(os.path.exists(os.path.join(out, n + ".png")) for n in state), "preview PNGs written to %s" % out)


if __name__ == "__main__":
    main(globals())
