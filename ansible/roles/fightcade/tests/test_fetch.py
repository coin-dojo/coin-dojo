#!/usr/bin/env python3
"""Tests for the first-launch download (#267): files/hk-fightcade-fetch, against a LOCAL https server serving fixture
archives (a throwaway self-signed certificate from openssl; never the real servers, never the network).

    python3 ansible/roles/fightcade/tests/test_fetch.py

Happy path: --check says 10, --yes installs the three trees with the deploy's recipe (strip, links, dist, patch,
stamp), the lobby files, the FBNeo base ini, the .exe modes and the prefix; --check says 0; a second run downloads
nothing. Broken cases (each must leave NOTHING installed and a clean retry): a sha256 mismatch, a download bigger
than its pin, an interrupted download (the .part is resumed with a Range request), no network, too little free
space (nothing is requested), an http url / http mirror / redirect to http (refused), a second run while one holds
the lock, a failed prefix build, half-unpacked trees of a killed run, a broken manifest, root. Only the pinned files
are ever requested. The pins come from build/fightcade/pins.json (one source): the role's archives and the manifest
template read it, and no download pin is written anywhere else in the role.
"""
import errno
import fcntl
import hashlib
import http.server
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import zipfile

ROLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANSIBLE = os.path.dirname(os.path.dirname(ROLE))
REPO = os.path.dirname(ANSIBLE)
sys.path.insert(0, os.path.join(REPO, "tests", "lib"))
from hktest import check, main, skip  # noqa: E402

FETCH = os.path.join(ROLE, "files", "hk-fightcade-fetch")
UNPACK = os.path.join(ROLE, "files", "hk-fightcade-unpack")
INI_FILTER = os.path.join(ANSIBLE, "filter_plugins", "hk_fbneo_ini.py")
PINS = os.path.join(REPO, "build", "fightcade", "pins.json")


def _load_engine():
    loader = importlib.machinery.SourceFileLoader("hk_fightcade_fetch", FETCH)
    spec = importlib.util.spec_from_loader("hk_fightcade_fetch", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ENGINE = _load_engine()
DEFAULT_INI = "// FBNeo\r\nnVidSelect 0\r\nbVidVSync 1\r\n"
NATIVEFIER = {"name": "Fightcade", "showMenuBar": True, "maximize": False}
MAIN_JS = "  plugins: true,\n"
CSS, JS = "/* coin-dojo css */\n", "// coin-dojo osk\n"


# ---- fixtures -----------------------------------------------------------------------------------------------------
def _tar(mode, files, top):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as t:
        for rel, (data, perm) in sorted(files.items()):
            ti = tarfile.TarInfo(top + rel)
            if data is None:
                ti.type, ti.mode = tarfile.DIRTYPE, 0o755
                t.addfile(ti)
            else:
                b = data.encode()
                ti.size, ti.mode = len(b), perm
                t.addfile(ti, io.BytesIO(b))
    return buf.getvalue()


def archives():
    wine = _tar("w:xz", {"": (None, 0), "bin/": (None, 0), "bin/wine": ("#!/bin/sh\n", 0o755),
                         "bin/wineserver": ("#!/bin/sh\n", 0o755)}, "wine-10.0-amd64-wow64/")
    zb = io.BytesIO()
    with zipfile.ZipFile(zb, "w") as zf:
        info = zipfile.ZipInfo("electron")
        info.external_attr = 0o755 << 16
        zf.writestr(info, "#!/bin/sh\n")
    app = "fc2-electron/resources/app/"
    fc = _tar("w:gz", {"": (None, 0), app: (None, 0), app + "inject/": (None, 0), app + "inject/_placeholder": ("", 0o644),
                       app + "package.json": ("{}\n", 0o644), app + "nativefier.json": (json.dumps(NATIVEFIER), 0o644),
                       app + "lib/": (None, 0), app + "lib/main.js": (MAIN_JS, 0o644),
                       "emulator/": (None, 0), "emulator/fcade": ("#!/bin/sh\n", 0o755),
                       "emulator/fbneo/": (None, 0), "emulator/fbneo/fcadefbneo.exe": ("MZ", 0o644),
                       "emulator/fbneo/ROMs/": (None, 0), "emulator/fbneo/config/": (None, 0),
                       "emulator/fbneo/config/fcadefbneo.default.ini": (DEFAULT_INI, 0o644)}, "Fightcade/")
    return {"wine-10.0-amd64-wow64.tar.xz": wine, "electron-v9.1.0-linux-arm64.zip": zb.getvalue(),
            "Fightcade-linux-v2.1.45.tar.gz": fc}


class Handler(http.server.BaseHTTPRequestHandler):
    """Serves Rig.files with Range support. Rig.mode[file]: 'cut' (half, then the connection closes), 'flood' (twice
    the bytes), 'redirect-http', 'tamper' (same length, other bytes), 'ignore-range', '416' (a Range request gets 416),
    'bad-range' (a 206 whose Content-Range starts at 0, not at the asked byte), 'range-total' (a 206 whose total is
    not the file's size), 'newer' (a different, longer file behind the pinned url)."""
    rig = None

    def log_message(self, *a):
        pass

    def do_GET(self):
        rig = self.rig
        name = self.path.rsplit("/", 1)[-1]
        rig.seen.append((name, self.headers.get("Range")))
        mode = rig.mode.get(name, "")
        if name not in rig.files:
            self.send_error(404)
            return
        if mode == "redirect-http":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:%d/%s" % (rig.port, name))
            self.end_headers()
            return
        data = rig.files[name]
        if mode == "tamper":
            data = bytes(b ^ 0x5A for b in data)
        if mode == "flood":
            data = data * 2
        if mode == "newer":
            data = data + b"-2.1.46"
        start = 0
        rng = self.headers.get("Range")
        if rng and mode == "416":
            self.send_response(416)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if rng and mode != "ignore-range":
            start = int(re.match(r"bytes=(\d+)-", rng).group(1))
            self.send_response(206)
            first, total = (0, len(data)) if mode == "bad-range" else (start, len(data) + (5 if mode == "range-total" else 0))
            self.send_header("Content-Range", "bytes %d-%d/%d" % (first, len(data) - 1, total))
            if mode == "bad-range":
                start = 0
        else:
            self.send_response(200)
        body = data[start:]
        if mode == "cut":
            body = body[: len(body) // 2]
            self.send_header("Content-Length", str(len(data) - start))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.close_connection = True
            return
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Rig:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="hk-fcfetch-")
        self.files = archives()
        self.mode, self.seen = {}, []
        cert, key = self.path("cert.pem"), self.path("key.pem")
        r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", cert,
                            "-days", "2", "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
                            "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,digitalSignature"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("openssl: " + r.stderr[-300:])
        self.cert = cert
        handler = type("H", (Handler,), {"rig": self})
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.prefix_rc = 0
        self.reset_cabinet()

    def path(self, *p):
        return os.path.join(self.dir, *p)

    def reset_cabinet(self):
        shutil.rmtree(self.path("cab"), ignore_errors=True)
        for d in ("cab/data/opt", "cab/data/logs", "cab/data/fbneo/config", "cab/lib"):
            os.makedirs(self.path(d), exist_ok=True)
        fake_prefix = self.path("cab/lib/hk-fightcade-prefix")
        with open(fake_prefix, "w") as fh:
            fh.write('#!/bin/bash\n[ -n "${FAIL_PREFIX:-}" ] && { echo "wineboot failed" >&2; exit 1; }\n'
                     'mkdir -p "$1/drive_c" && echo x > "$1/system.reg" && printf "%s\\n" "$2" > "$1/.hk-stamp"\n'
                     'echo "conf=$COIN_DOJO_FIGHTCADE_CONF logs=$HK_FC_LOGS" > "$1/env.txt"\n')
        os.chmod(fake_prefix, 0o755)
        self.seen[:] = []

    def manifest(self, **over):
        d = self.path("cab/data")
        opt = d + "/opt"
        app = opt + "/Fightcade/fc2-electron/resources/app"
        pins = json.load(open(PINS))
        arch = []
        for name, dest, strip in (("wine", "wine", 1), ("electron", "electron", 0), ("fightcade", "Fightcade", 1)):
            pin = pins[name]
            data = self.files[pin["file"]]
            a = {"name": name, "title": pin["title"], "url": pin["url"], "file": pin["file"],
                 "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "install_mib": 1, "dest": dest,
                 "strip": strip, "links": [], "dist": [], "patches": []}
            if name == "fightcade":
                a["links"] = [{"path": "emulator/fbneo/config", "to": d + "/fbneo/config"},
                              {"path": "emulator/fbneo/ROMs", "to": d + "/roms-extra"}]
                a["dist"] = ["fc2-electron/resources/app/nativefier.json"]
                a["patches"] = [{"path": "fc2-electron/resources/app/lib/main.js",
                                 "sha256": hashlib.sha256(MAIN_JS.encode()).hexdigest(),
                                 "sed": r"s/^\( *\)plugins: true,$/\1plugins: false,/"}]
            a["stamp"] = "%s recipe-%s" % (a["sha256"], name)
            arch.append(a)
        m = {"version": 1, "install_dir": opt, "data_dir": d, "staging": d + "/.incoming", "logs": d + "/logs",
             "conf": self.path("cab/fightcade.conf"), "unpack": UNPACK, "prefix_cmd": self.path("cab/lib/hk-fightcade-prefix"),
             "ini_module": INI_FILTER, "margin_mib": 1, "archives": arch, "exe_dir": opt + "/Fightcade/emulator",
             "nativefier": {"path": app + "/nativefier.json", "keys": {"showMenuBar": False, "maximize": True}},
             "files": [{"path": app + "/inject/inject.css", "text": CSS}, {"path": app + "/inject/inject.js", "text": JS}],
             "ini": {"dist": opt + "/Fightcade/emulator/fbneo/config.dist/fcadefbneo.default.ini",
                     "base": opt + "/fcadefbneo.ini", "overrides": "nVidSelect 4\nbVidVSync 0\n"},
             "prefix": {"path": d + "/prefix", "stamp": "wine=%s recipe=2" % arch[0]["sha256"], "mib": 1}}
        m.update(over)
        p = self.path("manifest.json")
        with open(p, "w") as fh:
            json.dump(m, fh)
        return p, m

    def run(self, *args, mirror=None, env=None, manifest=None):
        mp = manifest or self.manifest()[0]
        e = dict(os.environ, HK_FC_FETCH_MANIFEST=mp, HK_FC_FETCH_CAFILE=self.cert,
                 HK_FC_FETCH_URL_PREFIX=mirror if mirror is not None else "https://127.0.0.1:%d/mirror" % self.port)
        e.update(env or {})
        r = subprocess.run([sys.executable, FETCH] + list(args), capture_output=True, text=True, env=e, timeout=120)
        evs = []
        for line in r.stdout.splitlines():
            try:
                evs.append(json.loads(line))
            except ValueError:
                pass
        return r.returncode, evs, r.stderr

    def opt(self, *p):
        return self.path("cab/data/opt", *p)

    def trees(self):
        return sorted(f for f in os.listdir(self.opt()) if not f.startswith("."))

    def staging(self):
        s = self.path("cab/data/.incoming")
        return sorted(f for f in os.listdir(s) if f != ".lock") if os.path.isdir(s) else []

    def close(self):
        self.httpd.shutdown()
        shutil.rmtree(self.dir, ignore_errors=True)


def _rig():
    if not shutil.which("openssl"):
        skip("first-launch fetch tests: openssl not found (it makes the throwaway https certificate)")
        return None
    for tool in ("tar", "unzip", "xz"):
        if not shutil.which(tool):
            skip("first-launch fetch tests: %s not found" % tool)
            return None
    return Rig()


def errors(evs):
    return [e for e in evs if e.get("ev") == "error"]


# ---- tests ------------------------------------------------------------------------------------------------------
def test_happy_path_then_nothing_to_do():
    rig = _rig()
    if not rig:
        return
    try:
        rc, _, err = rig.run("--check")
        check(rc == 10, "fresh cabinet: --check says 10 (downloads needed), rc %d %s" % (rc, err[-200:]))
        rc, evs, err = rig.run("--plan")
        check(rc == 0, "--plan works on a fresh cabinet")
        rc, evs, err = rig.run("--yes", "--progress")
        check(rc == 0, "--yes installs (rc %d)%s" % (rc, "" if rc == 0 else "\n" + err[-1500:]))
        kinds = [e["ev"] for e in evs]
        check(kinds[0] == "plan" and "download" in kinds and kinds[-1] == "done", "events: plan, download..., done (%s)" % sorted(set(kinds)))
        plan = evs[0]
        check(plan["download_bytes"] == sum(len(v) for v in rig.files.values()) == plan["todo_bytes"] and plan["need_mib"] > 0,
              "plan: download_bytes = todo_bytes = the three pinned sizes, a space need")
        alltot = [e["all_total"] for e in evs if e["ev"] == "download"]
        check(alltot and set(alltot) == {plan["todo_bytes"]}, "the total bar's all_total = the plan's todo_bytes (the headline's total)")
        check(rig.trees() == ["Fightcade", "electron", "fcadefbneo.ini", "wine"], "three trees + the base ini: %s" % rig.trees())
        check(os.access(rig.opt("wine/bin/wine"), os.X_OK) and os.access(rig.opt("electron/electron"), os.X_OK),
              "wine (strip 1) and electron (zip) unpacked")
        _, m = rig.manifest()
        for a in m["archives"]:
            check(open(rig.opt(a["dest"], ".hk-stamp")).read().strip() == a["stamp"], "%s stamped with the deploy's stamp" % a["dest"])
        app = rig.opt("Fightcade/fc2-electron/resources/app")
        nat = json.load(open(app + "/nativefier.json"))
        check(nat == dict(NATIVEFIER, showMenuBar=False, maximize=True), "nativefier.json = Fightcade's own + the role's keys")
        check(open(app + "/inject/inject.css").read() == CSS and open(app + "/inject/inject.js").read() == JS,
              "the lobby CSS and keyboard script written byte for byte (the launcher checks their sha256)")
        check("plugins: false," in open(app + "/lib/main.js").read(), "the #215 popup patch applied at unpack")
        ini = open(rig.opt("fcadefbneo.ini"), newline="").read()
        check("nVidSelect 4\r\n" in ini and "bVidVSync 0\r\n" in ini, "FBNeo base ini: the role's keys over the default (CRLF kept)")
        exe = rig.opt("Fightcade/emulator/fbneo/fcadefbneo.exe")
        check(stat.S_IMODE(os.stat(exe).st_mode) == 0o755, "#228: fcadefbneo.exe made 0755 (0644 in the archive)")
        check(os.readlink(rig.opt("Fightcade/emulator/fbneo/ROMs")) == rig.path("cab/data/roms-extra"), "links: ROMs -> roms-extra")
        pre = rig.path("cab/data/prefix")
        check(open(pre + "/.hk-stamp").read().strip() == m["prefix"]["stamp"], "base prefix built with its stamp")
        check("conf=%s logs=%s" % (m["conf"], m["logs"]) in open(pre + "/env.txt").read(),
              "the prefix builder gets the launcher config and the logs dir")
        check(rig.staging() == [], "the downloads are deleted after unpacking: %s" % rig.staging())
        check(sorted(n for n, _ in rig.seen) == sorted(rig.files), "only the three pinned files were requested: %s" % rig.seen)
        check(os.path.isfile(rig.path("cab/data/logs/fetch.log")), "a log in the logs dir")
        rc, _, _ = rig.run("--check")
        check(rc == 0, "--check says 0 once installed")
        n = len(rig.seen)
        rc, evs, err = rig.run("--yes", "--progress")
        check(rc == 0 and len(rig.seen) == n and "download" not in [e["ev"] for e in evs],
              "a second run downloads nothing (rc %d, %d new requests)" % (rc, len(rig.seen) - n))
        # local repair: a changed lobby file -> 11, fixed without the network
        open(app + "/inject/inject.css", "w").write("/* changed */")
        rc, _, _ = rig.run("--check")
        check(rc == 11, "a changed lobby CSS: --check says 11 (local steps only), rc %d" % rc)
        rc, _, err = rig.run("--yes", mirror="https://127.0.0.1:1/none")
        check(rc == 0 and open(app + "/inject/inject.css").read() == CSS, "repaired without any download (rc %d %s)" % (rc, err[-300:]))
    finally:
        rig.close()


def test_checksum_mismatch_installs_nothing_then_retry():
    rig = _rig()
    if not rig:
        return
    try:
        rig.mode["Fightcade-linux-v2.1.45.tar.gz"] = "tamper"
        rc, evs, err = rig.run("--yes", "--progress")
        e = (errors(evs) or [{}])[0]
        check(rc == 3 and e.get("kind") == "checksum", "BROKEN: a tampered download = rc 3, checksum (rc %d %s)" % (rc, e.get("kind")))
        check("pinned sha256" in e.get("text", "") and "network" not in e.get("text", ""),
              "the message names the pinned sha256, never the network: %r" % e.get("text"))
        check(rig.trees() == [], "BROKEN: nothing installed after a checksum mismatch (%s)" % rig.trees())
        check("Fightcade-linux-v2.1.45.tar.gz" not in rig.staging() and "Fightcade-linux-v2.1.45.tar.gz.part" not in rig.staging(),
              "the bad download is deleted")
        check(not os.path.exists(rig.path("cab/data/prefix")), "no prefix either")
        rc, _, _ = rig.run("--check")
        check(rc == 10, "--check still says downloads needed")
        del rig.mode["Fightcade-linux-v2.1.45.tar.gz"]
        n = len(rig.seen)
        rc, _, err = rig.run("--yes")
        check(rc == 0, "the retry installs (rc %d %s)" % (rc, err[-300:]))
        check([s for s, _ in rig.seen[n:]] == ["Fightcade-linux-v2.1.45.tar.gz"], "the retry fetched only the bad file again: %s" % rig.seen[n:])
    finally:
        rig.close()


def test_bigger_than_pinned_is_stopped():
    rig = _rig()
    if not rig:
        return
    try:
        rig.mode["wine-10.0-amd64-wow64.tar.xz"] = "flood"
        rc, evs, _ = rig.run("--yes", "--progress")
        e = (errors(evs) or [{}])[0]
        check(rc == 3 and e.get("kind") == "checksum" and "bytes upstream, pinned" in e.get("text", ""),
              "BROKEN: a download whose size is not the pin is stopped before writing (rc %d %r)" % (rc, e.get("text")))
        check(rig.trees() == [] and not [f for f in rig.staging() if f.startswith("wine")], "nothing kept of it")
    finally:
        rig.close()


def test_interrupted_download_resumes():
    rig = _rig()
    if not rig:
        return
    try:
        wine = "wine-10.0-amd64-wow64.tar.xz"
        rig.mode[wine] = "cut"
        rc, evs, err = rig.run("--yes", "--progress")
        check(rc == 4 and (errors(evs) or [{}])[0].get("kind") == "network", "BROKEN: a cut download = rc 4 network (rc %d)" % rc)
        part = rig.path("cab/data/.incoming", wine + ".part")
        half = os.path.getsize(part) if os.path.exists(part) else 0
        check(0 < half < len(rig.files[wine]), "the partial download is kept as .part (%d bytes)" % half)
        check(rig.trees() == [] and wine not in rig.staging(), "no partial install and no unverified final file")
        # a killed unpack of an earlier run leaves <dest>.new.<pid>: removed by the next run
        os.makedirs(rig.opt("wine.new.4242/bin"))
        del rig.mode[wine]
        n = len(rig.seen)
        rc, _, err = rig.run("--yes")
        check(rc == 0, "the retry installs (rc %d %s)" % (rc, err[-300:]))
        check((wine, "bytes=%d-" % half) in rig.seen[n:], "the retry resumed with a Range request from byte %d: %s" % (half, rig.seen[n:]))
        check(not os.path.exists(rig.opt("wine.new.4242")), "a half-unpacked tree of a killed run is removed")
        # a server that ignores Range: the engine starts over instead of appending
        rig.reset_cabinet()
        rig.mode[wine] = "cut"
        rig.run("--yes")
        rig.mode[wine] = "ignore-range"
        rc, _, err = rig.run("--yes")
        check(rc == 0, "a server that ignores Range: the download starts over and installs (rc %d %s)" % (rc, err[-300:]))
    finally:
        rig.close()


def test_no_network_back_to_menu_message():
    rig = _rig()
    if not rig:
        return
    try:
        rc, evs, _ = rig.run("--yes", "--progress", mirror="https://127.0.0.1:1/closed")
        e = (errors(evs) or [{}])[0]
        check(rc == 4 and e.get("kind") == "network", "BROKEN: no network = rc 4, kind network (rc %d)" % rc)
        check("could not reach github.com" in e.get("text", "") and "internet" in e.get("text", ""),
              "the message names the host and says to check the internet: %r" % e.get("text"))
        check(rig.trees() == [] and rig.staging() == [], "nothing installed, nothing half-downloaded")
        rc, _, _ = rig.run("--yes")
        check(rc == 0, "with the network back, the next pick installs")
    finally:
        rig.close()


def test_not_enough_space_requests_nothing():
    rig = _rig()
    if not rig:
        return
    try:
        mp, _ = rig.manifest(margin_mib=10 ** 9)
        rc, evs, _ = rig.run("--yes", "--progress", manifest=mp)
        e = (errors(evs) or [{}])[0]
        check(rc == 5 and e.get("kind") == "space" and "MB free" in e.get("text", ""),
              "BROKEN: too little free space = rc 5 with the MB needed and free (rc %d %r)" % (rc, e.get("text")))
        check(rig.seen == [] and rig.trees() == [], "nothing was requested or installed")
    finally:
        rig.close()


def test_https_only():
    rig = _rig()
    if not rig:
        return
    try:
        rc, evs, _ = rig.run("--yes", "--progress", mirror="http://127.0.0.1:%d/mirror" % rig.port)
        check(rc == 2 and rig.seen == [], "BROKEN: an http mirror is refused before any request (rc %d)" % rc)
        _, m = rig.manifest()
        m["archives"][1]["url"] = "http://github.com/electron/electron-v9.1.0-linux-arm64.zip"
        check(any("not https" in p for p in ENGINE.manifest_problems(m)), "BROKEN: a manifest with an http url is refused")
        mp = rig.path("http-manifest.json")
        json.dump(m, open(mp, "w"))
        rc, _, err = rig.run("--check", manifest=mp)
        check(rc == 2 and "not https" in err, "BROKEN: --check on an http manifest = rc 2")
        rig.mode["electron-v9.1.0-linux-arm64.zip"] = "redirect-http"
        rc, evs, _ = rig.run("--yes", "--progress")
        check(rc == 4 and "not https" in (errors(evs) or [{}])[0].get("text", ""), "BROKEN: a redirect to http is refused (rc %d)" % rc)
        check(rig.trees() == [], "nothing installed after the refused redirect")
        for bad in ({"archives": []}, {"install_dir": "relative/path"}):
            check(ENGINE.manifest_problems(dict(m, **bad)) != [], "BROKEN: manifest %s is refused" % list(bad))
        _, good = rig.manifest()
        good["archives"][0]["sha256"] = "abc"
        check(ENGINE.manifest_problems(good) != [], "BROKEN: a short sha256 pin is refused")
    finally:
        rig.close()


def test_lock_and_prefix_failure_and_root():
    rig = _rig()
    if not rig:
        return
    try:
        os.makedirs(rig.path("cab/data/.incoming"), mode=0o700)
        with open(rig.path("cab/data/.incoming/.lock"), "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            rc, evs, _ = rig.run("--yes", "--progress")
        check(rc == 7 and rig.seen == [], "BROKEN: a second run while one holds the lock = rc 7, nothing requested (rc %d)" % rc)
        rc, evs, _ = rig.run("--yes", "--progress", env={"FAIL_PREFIX": "1"})
        e = (errors(evs) or [{}])[0]
        check(rc == 1 and "preparing Wine failed" in e.get("text", ""), "BROKEN: a failed prefix build = rc 1 with the log (rc %d)" % rc)
        rc, _, _ = rig.run("--check")
        check(rc == 11, "after a failed prefix: --check 11 (no stamp, the launcher does not start)")
        n = len(rig.seen)
        rc, _, _ = rig.run("--yes")
        check(rc == 0 and len(rig.seen) == n, "the retry builds the prefix without downloading again")
    finally:
        rig.close()
    # root refusal (in-process: the tests do not run as root)
    real = ENGINE.os.getuid
    ENGINE.os.getuid = lambda: 0
    try:
        env = os.environ.pop("HK_FC_ALLOW_ROOT", None)
        check(ENGINE.main(["--check"]) == 6, "BROKEN: run as root = rc 6")
        if env is not None:
            os.environ["HK_FC_ALLOW_ROOT"] = env
    finally:
        ENGINE.os.getuid = real
    check(ENGINE.main(["--bogus"]) == 2 and ENGINE.main([]) == 2, "bad usage (or no --yes) = rc 2")


def test_disk_full_mid_download():
    """ENOSPC while writing: the space message, the .part is the only thing left."""
    rig = _rig()
    if not rig:
        return
    try:
        mp, m = rig.manifest()
        real = open

        def fake_open(path, mode="r", *a, **kw):
            if str(path).endswith(".part") and "b" in mode:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real(path, mode, *a, **kw)
        os.environ.update(HK_FC_FETCH_CAFILE=rig.cert, HK_FC_FETCH_URL_PREFIX="https://127.0.0.1:%d/m" % rig.port)
        ENGINE.open = fake_open
        out = ENGINE.Out()
        try:
            ENGINE.install(m, out)
            got = None
        except ENGINE.Fail as e:
            got = e
        finally:
            del ENGINE.open
            del os.environ["HK_FC_FETCH_CAFILE"], os.environ["HK_FC_FETCH_URL_PREFIX"]
        check(got is not None and got.kind == "space" and "filled up" in got.text, "BROKEN: a full disk mid-download = the space message")
        check(rig.trees() == [], "nothing installed")
    finally:
        rig.close()


def test_wine_tampered_is_a_checksum_not_a_release():
    rig = _rig()
    if not rig:
        return
    try:
        rig.mode["wine-10.0-amd64-wow64.tar.xz"] = "tamper"
        rc, evs, _ = rig.run("--yes", "--progress")
        e = (errors(evs) or [{}])[0]
        check(rc == 3 and e.get("kind") == "checksum" and "pinned sha256" in e.get("text", ""),
              "BROKEN: a tampered Wine = checksum with the pinned sha256 (%r)" % e.get("text"))
        check(rig.trees() == [], "nothing installed")
    finally:
        rig.close()


def test_other_file_behind_the_pinned_url():
    """Review #1/#4: a different file (another size) behind a pinned url is stopped from its Content-Length / the
    206's Content-Range total before anything is written: a checksum error, never 'network'."""
    rig = _rig()
    if not rig:
        return
    try:
        fc = "Fightcade-linux-v2.1.45.tar.gz"
        rig.mode[fc] = "newer"
        rc, evs, _ = rig.run("--yes", "--progress")
        e = (errors(evs) or [{}])[0]
        check(rc == 3 and e.get("kind") == "checksum" and "bytes upstream, pinned" in e.get("text", ""),
              "BROKEN: another file (other size) = checksum, named by its size (rc %d %r)" % (rc, e.get("text")))
        check(not [f for f in rig.staging() if f.startswith("Fightcade")] and rig.trees() == [],
              "nothing of it written, nothing installed")
        # the same on a resume: a 206 whose total is not the pinned size
        rig.mode[fc] = "cut"
        rig.run("--yes")
        rig.mode[fc] = "range-total"
        rc, evs, _ = rig.run("--yes", "--progress")
        e = (errors(evs) or [{}])[0]
        check(rc == 3 and e.get("kind") == "checksum" and not [f for f in rig.staging() if f.startswith("Fightcade")],
              "BROKEN: a resume whose Content-Range total differs = checksum, .part deleted (rc %d %r)" % (rc, e.get("text")))
    finally:
        rig.close()


def test_stuck_resume_starts_over():
    """Review #2 and #4: a .part the server will not resume (416) or a 206 that does not start at our byte is deleted
    and fetched again from 0, in the same run."""
    rig = _rig()
    if not rig:
        return
    try:
        wine = "wine-10.0-amd64-wow64.tar.xz"
        for mode in ("416", "bad-range"):
            rig.reset_cabinet()
            rig.mode[wine] = "cut"
            rig.run("--yes")
            part = rig.path("cab/data/.incoming", wine + ".part")
            half = os.path.getsize(part) if os.path.exists(part) else 0
            check(0 < half < len(rig.files[wine]), "%s: a partial download to resume (%d bytes)" % (mode, half))
            rig.mode[wine] = mode
            n = len(rig.seen)
            rc, _, err = rig.run("--yes")
            mine = [r for nm, r in rig.seen[n:] if nm == wine]
            check(rc == 0 and mine == ["bytes=%d-" % half, None],
                  "BROKEN %s: the resume is refused, the .part deleted and fetched again from 0 in the same run (rc %d, %s)%s"
                  % (mode, rc, mine, "" if rc == 0 else " " + err[-300:]))
            check(open(rig.opt("wine", ".hk-stamp")).read().strip().startswith(hashlib.sha256(rig.files[wine]).hexdigest()),
                  "%s: the restarted download installed the pinned file" % mode)
            del rig.mode[wine]
    finally:
        rig.close()


def test_every_pin_is_versioned():
    """#267 review: no moving url is pinned (Fightcade moved from -latest to -v2.1.45); the engine has no moving path."""
    pins = json.load(open(PINS))
    check(all("latest" not in x["url"] for x in pins.values()), "no pinned url is a moving 'latest'")
    check(pins["fightcade"]["url"].endswith("/Fightcade-linux-v%s.tar.gz" % pins["fightcade"]["version"]),
          "Fightcade's url names its pinned version: %s" % pins["fightcade"]["url"])
    check(not re.search(r"\bmoving\b", open(FETCH).read()), "the engine has no moving-url path left")


def test_content_range_parse():
    cr = ENGINE._content_range
    check(cr("bytes 100-199/200") == (100, 199, 200) and cr("bytes 5-9/*") == (5, 9, None), "Content-Range parses")
    for bad in (None, "", "bytes 1-2", "items 1-2/3", "bytes x-2/3"):
        check(cr(bad) is None, "BROKEN: a malformed Content-Range %r is refused" % bad)


# ---- one pin source ---------------------------------------------------------------------------------------------
def pin_source_problems(defaults, template):
    """The role's download pins come only from build/fightcade/pins.json (#267)."""
    p = []
    if "lookup('ansible.builtin.file', fightcade_build_root ~ '/fightcade/pins.json') | from_json" not in defaults:
        p.append("fightcade_pins does not read build/fightcade/pins.json")
    pins = json.load(open(PINS))
    for name in ("wine", "electron", "fightcade"):
        for k in ("url", "sha256", "size", "title"):
            if '%s: "{{ fightcade_pins.%s.%s }}"' % (k, name, k) not in defaults:
                p.append("fightcade_archives[%s].%s is not fightcade_pins.%s.%s" % (name, k, name, k))
        if pins[name]["sha256"] in defaults or pins[name]["url"] in defaults:
            p.append("%s: a pin is written into the role defaults (a second source)" % name)
    for k in ("a.url", "a.sha256", "a.size"):
        if k not in template:
            p.append("the manifest template does not take %s from fightcade_archives" % k)
    return p


def test_one_pin_source():
    d = open(os.path.join(ROLE, "defaults", "main.yml")).read()
    t = open(os.path.join(ROLE, "templates", "coin-dojo-fightcade-fetch.json.j2")).read()
    check(pin_source_problems(d, t) == [], "the role's pins come from pins.json only %s" % pin_source_problems(d, t))
    sha = json.load(open(PINS))["wine"]["sha256"]
    broken = d.replace('sha256: "{{ fightcade_pins.wine.sha256 }}"', "sha256: %s" % sha)
    check(pin_source_problems(broken, t) != [], "BROKEN: a literal Wine pin in the role is flagged")
    check(pin_source_problems(d, t.replace("a.url", "'https://example.org/x'")) != [], "BROKEN: a template with its own url is flagged")
    eng = open(FETCH).read()
    urls = re.findall(r"https?://[^\s\"')]+", eng)
    check(urls == [], "the engine has no download url of its own: %s" % urls)
    check(not re.search(r"(?i)\brom(s)?\b.*(url|download)", eng.split('"""', 2)[2]),
          "the engine code fetches nothing ROM-related")


def test_engine_syntax_bash32_unpack_compat():
    r = subprocess.run([sys.executable, "-m", "py_compile", FETCH], capture_output=True, text=True)
    check(r.returncode == 0, "hk-fightcade-fetch compiles %s" % r.stderr[-200:])
    check(os.access(FETCH, os.X_OK), "hk-fightcade-fetch is executable in git")


if __name__ == "__main__":
    main(globals())
