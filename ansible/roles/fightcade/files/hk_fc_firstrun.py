#!/usr/bin/env python3
"""hk_fc_firstrun.py (#267): the first-launch screen of Fightcade mode (hk_fightcade_install: first-launch).

coin-dojo-fightcade runs it under the session's cage when hk-fightcade-fetch --check says Fightcade is not installed
(or not current). It draws in the boot menu's look (settings_app.py's SDL + cairo Painter, brand/tokens.json, the 4:3
safe area) and drives hk-fightcade-fetch --yes --progress, which does the real work:

    consent   what will be downloaded, from where, how big, the free space it needs, Fightcade's terms; START downloads,
              SELECT cancels (shown only when something must be downloaded; a local-steps-only repair skips it)
    working   per-file and overall progress bars (MB), then "Unpacking ...", "Preparing Wine ..."; SELECT cancels
              (finished and partial downloads are kept, the next pick resumes)
    error     what went wrong in one plain sentence; any button (or ERROR_WAIT s) goes back to the boot menu
    done      "Fightcade is ready", then exit 0: the launcher starts the lobby

    hk_fc_firstrun.py [--actions a,b] [--error-wait S]               full screen (SDL)
    hk_fc_firstrun.py --png OUT [--state consent|working|error|done] [--plan FILE] [--event JSON]...   one frame (tests)

Env: HK_FC_FETCH (the engine), HK_FC_UI_LIB (settings_app.py + brand + fonts), HK_FC_FETCH_MANIFEST (passed on).
Exit codes: 0 installed, 1 install failed (message shown), 2 usage, 3 no display / library missing, 4 cancelled by
the player, 143 TERM (the engine is stopped first: its whole process group).
"""
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time

LIB = os.environ.get("HK_FC_UI_LIB", "/data/coin-dojo/settings")
FETCH = os.environ.get("HK_FC_FETCH", "/usr/local/lib/coin-dojo-fightcade/hk-fightcade-fetch")
ERROR_WAIT = 60.0          # s the error stays up without input, then back to the menu
GRACE = 0.5                # s after the window opens in which a held-over press (LK/START from the boot menu) is ignored
MB = 1000 * 1000
# docs/LEGAL.md "Further reading": the app's own terms; coin-dojo redistributes nothing.
LEGAL = ("Fightcade is third-party software from fightcade.com, not part of coin-dojo, and is downloaded from its "
         "official sources. Fightcade's own terms apply: any terms the app shows when you sign up apply to your account.")


def mb(n):
    return "%d MB" % int(round(n / float(MB))) if n >= MB else "%.1f MB" % (n / float(MB))


def gb(mib):
    return "%.1f GB" % (mib * 1024 * 1024 / 1e9)


class FirstRun:
    """The state: no drawing, no SDL. handle(action) -> None | 'begin' | 'cancel' | 'leave'."""

    def __init__(self, plan):
        self.plan = plan
        self.state = "consent" if plan.get("downloads_needed") else "working"
        self.dl = {}               # the last download event
        self.step = None           # the current step text (unpack, setup, prefix)
        self.error = None
        self.rate = None           # bytes/s over the last few seconds
        self._rate_mark = None

    def handle(self, action, now=0.0):
        if self.state == "consent":
            if action in ("start", "enter", "ok"):
                self.state = "working"
                return "begin"
            if action in ("exit", "esc", "back"):
                self.state = "cancelled"
                return "cancel"
        elif self.state == "working":
            if action in ("exit", "esc", "back"):
                self.state = "cancelled"
                return "cancel"
        elif self.state == "error":
            if action in ("start", "enter", "ok", "back", "exit", "esc", "select"):
                return "leave"
        return None

    def on_event(self, ev, now=0.0):
        kind = ev.get("ev")
        if kind == "plan":
            self.plan = dict(self.plan, **{k: v for k, v in ev.items() if k != "ev"})
        elif kind == "download":
            self.dl = ev
            self.step = None
            mark = (now, ev.get("all_done", 0))
            if self._rate_mark and now - self._rate_mark[0] >= 1.0:
                self.rate = (mark[1] - self._rate_mark[1]) / (now - self._rate_mark[0])
                self._rate_mark = mark
            elif not self._rate_mark:
                self._rate_mark = mark
        elif kind == "step":
            self.step = ev.get("text")
        elif kind == "error":
            self.state, self.error = "error", ev.get("text") or "the install failed"
        elif kind == "done":
            self.state = "done"

    def engine_ended(self, rc):
        if self.state in ("working", "consent") and rc != 0:
            self.state = "error"
            self.error = self.error or "the download stopped unexpectedly (rc %s). Pick FIGHTCADE again to retry." % rc
        elif self.state == "working" and rc == 0:
            self.state = "done"

    def view(self, now=0.0):
        return dict(state=self.state, plan=self.plan, dl=self.dl, step=self.step, error=self.error, rate=self.rate, t=now)


def headline(plan):
    """The total matches the "All downloads" bar: a resume says what is left of the whole (about 82 MB left of 307 MB)."""
    total = plan.get("todo_bytes") or plan.get("total_bytes", 0)
    left = plan.get("download_bytes", total)
    # The hosts are on the rows below (Fightcade from web.fightcade.com, Wine and Electron from github.com).
    if total and 0 < left < total:
        return "Downloading Fightcade, Wine and Electron (about %s left of %s)" % (mb(left), mb(total))
    return "Downloading Fightcade, Wine and Electron (about %s)" % mb(total or left)


# ---- drawing (settings_app.Painter) -----------------------------------------------------------------------------
def make_painter(sa):
    W, H = sa.W, sa.H
    M = 96

    class FirstRunPainter(sa.Painter):
        def wrap(self, text, role, px, maxw):
            lines, cur = [], ""
            for word in text.split():
                t = (cur + " " + word).strip()
                if cur and self.width(t, role, px) > maxw:
                    lines.append(cur)
                    cur = word
                else:
                    cur = t
            return lines + ([cur] if cur else [])

        def para(self, x, y, text, role, px, colour, maxw, lh=None):
            lh = lh or int(px * 1.35)
            for ln in self.wrap(text, role, px, maxw):
                self.text(x, y, ln, role, px, colour)
                y += lh
            return y

        def bar(self, x, y, w, frac, h=14, colour="gold"):
            self.rrect(x, y, w, h, h / 2.0, fill="panel")
            if frac > 0:
                self.rrect(x, y, max(h, w * min(1.0, frac)), h, h / 2.0, fill=colour)

        def help(self, items):
            ts = self.b["type_scale"]
            y = H - 64
            self.rect(0, y - 34, W, 98, "card", 0.85)
            x = M
            for badge, word in items:
                x += self.badge(x, y, badge, 36) + 12
                x += self.text(x, y + 9, word, "ui_bold", ts["help"], "text") + 40

        def draw(self, v):
            b, ts = self.b, self.b["type_scale"]
            plan = v["plan"]
            self.rect(0, 0, W, H, "bg")
            self.rrect(M - 40, 70, W - 2 * M + 80, H - 220, b["radius"]["card"], fill="card")
            self.text(M, 160, "FIGHTCADE", "display", 64, "gold")
            tw = self.width("FIGHTCADE", "display", 64)
            self.text(M + tw + 28, 160, "FIRST LAUNCH", "ui_bold", ts["label"], "muted")
            self.rect(M, 188, W - 2 * M, b["border"]["rule"], "dim")
            self.rect(M, 188, 120, 4, "gold")
            maxw = W - 2 * M
            st = v["state"]
            if st == "consent":
                y = self.para(M, 260, headline(plan), "ui_bold", ts["heading"], "text", maxw)
                y += 16
                for p in plan.get("pieces", []):
                    if p.get("installed"):
                        continue
                    self.text(M, y, p["title"], "ui_bold", ts["body"], "text")
                    self.text(M + 420, y, "from " + p["host"], "ui_semi", ts["body"], "text_soft")
                    self.text(W - M, y, mb(p["size"] - p.get("have", 0)) + (" left" if p.get("have") else ""),
                              "ui_bold", ts["body"], "gold", "r")
                    y += 48
                y += 12
                y = self.para(M, y, "The first launch takes a few minutes and needs internet. It needs about %s free "
                              "on the data partition (%s free). Every file is checked against its pinned sha256."
                              % (gb(plan.get("need_mib", 0)), gb(plan.get("free_mib", 0))),
                              "ui_semi", ts["body"], "text_soft", maxw)
                y += 18
                self.para(M, y, LEGAL, "ui_semi", ts["label"], "muted", maxw)
                self.help([("START", "DOWNLOAD"), ("SELECT", "CANCEL")])
            elif st in ("working", "done"):
                y = self.para(M, 260, headline(plan) if plan.get("downloads_needed") else "Setting up Fightcade",
                              "ui_bold", ts["heading"], "text", maxw)
                dl = v["dl"]
                y += 30
                if dl:
                    self.text(M, y, "%s  from %s" % (dl.get("title", ""), dl.get("host", "")), "ui_bold", ts["body"], "text")
                    self.text(W - M, y, "%s / %s" % (mb(dl.get("done", 0)), mb(dl.get("total", 0))), "ui_bold",
                              ts["body"], "gold", "r")
                    self.bar(M, y + 22, maxw, dl.get("done", 0) / float(dl.get("total") or 1))
                    y += 110
                    allt = dl.get("all_total") or 1
                    self.text(M, y, "All downloads", "ui_semi", ts["label"], "text_soft")
                    rate = (" · %.1f MB/s" % (v["rate"] / MB)) if v.get("rate") else ""
                    self.text(W - M, y, "%s / %s%s" % (mb(dl.get("all_done", 0)), mb(allt), rate), "ui_semi",
                              ts["label"], "text_soft", "r")
                    self.bar(M, y + 18, maxw, dl.get("all_done", 0) / float(allt), 10, "gold_muted")
                    y += 90
                if st == "done":
                    self.text(M, y + 20, "Fightcade is ready. Starting the lobby...", "ui_bold", ts["heading"], "gold")
                elif v.get("step"):
                    self.text(M, y + 20, v["step"] + "...", "ui_bold", ts["heading"], "text")
                    phase = (v.get("t", 0.0) % 1.6) / 1.6                     # a sliding segment: it is still working
                    self.rrect(M, y + 50, maxw, 10, 5, fill="panel")
                    self.rrect(M + (maxw - 200) * phase, y + 50, 200, 10, 5, fill="gold")
                if st == "working":
                    self.text(M, H - 190, "Finished and partial downloads are kept: cancelling now resumes next time.",
                              "ui_semi", ts["label"], "dimmed")
                    self.help([("SELECT", "CANCEL")])
            elif st == "error":
                self.text(M, 270, "FIGHTCADE COULD NOT BE INSTALLED", "ui_bold", ts["heading"], "crimson")
                msg = v.get("error") or ""
                y = self.para(M, 330, msg[:1].upper() + msg[1:], "ui_semi", ts["body"], "text", maxw)
                self.para(M, y + 24, "Nothing was installed half-way. You can pick FIGHTCADE again in the boot menu.",
                          "ui_semi", ts["label"], "muted", maxw)
                self.help([("ANY BUTTON", "BACK TO THE MENU")])
            self.scanlines()
            self.c.cairo_surface_flush(self.surf)

    return FirstRunPainter


# ---- the engine -------------------------------------------------------------------------------------------------
class Engine:
    """hk-fightcade-fetch --yes --progress in its own process group; its JSON events into a queue."""

    def __init__(self, cmd):
        self.p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=None, text=True, start_new_session=True)
        self.q = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            try:
                self.q.put(json.loads(line))
            except ValueError:
                pass
        self.q.put(None)

    def events(self):
        out = []
        while True:
            try:
                out.append(self.q.get_nowait())
            except queue.Empty:
                return out

    def poll(self):
        return self.p.poll()

    def stop(self, grace=3.0):
        if self.p.poll() is not None:
            return
        try:
            os.killpg(self.p.pid, signal.SIGTERM)
        except OSError:
            return
        t = time.monotonic()
        while self.p.poll() is None and time.monotonic() - t < grace:
            time.sleep(0.05)
        if self.p.poll() is None:
            try:
                os.killpg(self.p.pid, signal.SIGKILL)
            except OSError:
                pass
            self.p.wait()


def fetch_cmd():
    return [sys.executable, FETCH]


def get_plan():
    r = subprocess.run(fetch_cmd() + ["--plan"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        msg = (r.stderr.strip().splitlines() or ["rc %d" % r.returncode])[-1]
        return None, msg.split(": ", 1)[-1] if "ERROR" in msg else msg
    return json.loads(r.stdout), None


# ---- main -------------------------------------------------------------------------------------------------------
class Usage(SystemExit):
    def __init__(self, msg):
        print(msg, file=sys.stderr)
        super().__init__(2)


USAGE = ("usage: hk_fc_firstrun.py [--actions a,b] [--error-wait S] | --png OUT [--state S] [--plan FILE] "
         "[--event JSON]...")


def parse_args(argv):
    o = dict(png=None, state=None, plan=None, events=[], actions=[], error_wait=ERROR_WAIT)
    a = list(argv)
    while a:
        x = a.pop(0)
        if x not in ("--png", "--state", "--plan", "--event", "--actions", "--error-wait") or not a:
            raise Usage(USAGE)
        v = a.pop(0)
        if x == "--event":
            o["events"].append(json.loads(v))
        elif x == "--actions":
            o["actions"] = [s for s in v.split(",") if s]
        elif x == "--error-wait":
            o["error_wait"] = float(v)
        else:
            o[x[2:]] = v
    return o


def _term(signum, frame):
    raise SystemExit(128 + signum)


def load_sa():
    sys.path.insert(0, LIB)
    import settings_app  # noqa: E402  (also puts LIB/brand on the path)
    return settings_app


def main(argv=None):
    opts = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        sa = load_sa()
        fonts = os.path.join(LIB, "fonts")
        b = sa.brand.load(font_dir=fonts if os.path.isdir(fonts) else None)
        Painter = make_painter(sa)
    except (ImportError, OSError) as e:
        print("hk_fc_firstrun: cannot load the drawing kit from %s: %s" % (LIB, e), file=sys.stderr)
        return 3

    if opts["png"]:
        plan = {}
        if opts["plan"]:
            with open(opts["plan"], encoding="utf-8") as fh:
                plan = json.load(fh)
        fr = FirstRun(plan)
        if opts["state"] in ("working", "error", "done"):
            fr.state = "working"
        for ev in opts["events"]:
            fr.on_event(ev)
        if opts["state"]:
            fr.state = opts["state"]
        try:
            painter = Painter(b)
        except OSError as e:
            print("hk_fc_firstrun: %s" % e, file=sys.stderr)
            return 3
        painter.draw(fr.view())
        return 0 if painter.png(opts["png"]) else 1

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    plan, err = get_plan()
    fr = FirstRun(plan or {})
    if plan is None:
        fr.state, fr.error = "error", err
    try:
        front = sa.SDLFront()
        painter = Painter(b)
    except OSError as e:
        print("hk_fc_firstrun: no display: %s" % e, file=sys.stderr)
        return 3
    try:
        inp = sa.Input(select_quits=True)   # SELECT alone = "exit" (cancel), START = "start" on release
    except TypeError:                       # a settings_app.py from before #224: SELECT+START and MK still cancel
        inp = sa.Input()
    script = list(opts["actions"])
    eng = None
    rc = None
    t_open = time.monotonic()
    err_at = None
    done_at = None
    try:
        if fr.state == "working":
            eng = Engine(fetch_cmd() + ["--yes", "--progress"])
        while rc is None:
            now = time.monotonic()
            scripted = bool(script)
            acts = [script.pop(0)] if scripted else front.events(inp, 100)
            for act in acts:
                if act == "redraw" or (isinstance(act, tuple)):
                    continue
                if not scripted and now - t_open < GRACE and act in ("start", "ok", "enter"):
                    continue                                    # the press that picked FIGHTCADE in the boot menu
                what = fr.handle(act, now)
                if what == "begin":
                    eng = Engine(fetch_cmd() + ["--yes", "--progress"])
                elif what == "cancel":
                    if eng:
                        eng.stop()
                    rc = 4
                elif what == "leave":
                    rc = 1
            if eng:
                for ev in eng.events():
                    if ev is None:
                        continue
                    fr.on_event(ev, now)
                code = eng.poll()
                if code is not None and fr.state in ("working",):
                    for ev in eng.events():
                        if ev is not None:
                            fr.on_event(ev, now)
                    fr.engine_ended(code)
            if fr.state == "error":
                err_at = err_at or now
                if now - err_at >= opts["error_wait"]:
                    rc = 1
            if fr.state == "done":
                done_at = done_at or now
                if now - done_at >= 1.0:
                    rc = 0
            painter.draw(fr.view(now))
            front.present(painter)
            if script:
                time.sleep(0.05)
    finally:
        if eng:
            eng.stop()
        front.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
