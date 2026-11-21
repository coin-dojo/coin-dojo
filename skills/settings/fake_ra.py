#!/usr/bin/env python3
"""TEST ONLY (#171): a fake RetroAchievements login endpoint (dorequest.php r=login2) on 127.0.0.1. Never on the
cabinet; the app never talks to the real RA API in tests or previews.

    fake_ra.py [PORT]        serve until killed; prints the URL (http://127.0.0.1:PORT/dorequest.php)

Accounts: user FAKE_USER (any case) with password FAKE_PASSWORD -> Success + FAKE_TOKEN; anything else -> 401 with
RA's error JSON. Path /broken/... answers 500 with HTML, /redirect/... a 302 to /dorequest.php, /echo/... a 401 whose
Error repeats the password (the app must redact it), /echoenc/... the same with the password URL-encoded (quote_plus
and quote, #198), /badstatus/... a line that is not HTTP and /short/... a body cut off before its Content-Length
(both: http.client.HTTPException in the client, #198). Requests are kept in Server.requests (method, path, query,
form, user agent) so tests can check where the password travelled.
"""
import json
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FAKE_USER = "Player_One"
FAKE_PASSWORD = "p@ss \"word\" $HOME 'x' é&r=1"      # quotes, spaces, $, &, = and a non-ASCII letter
FAKE_TOKEN = "FAKEtoken0123456"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):               # quiet: never print a request (it carries the password)
        pass

    def _send(self, code, body, ctype="application/json", headers=()):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._record({})
        self._send(405, json.dumps({"Success": False, "Error": "POST only"}))

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8"), keep_blank_values=True).items()}
        self._record(form)
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/broken"):
            return self._send(500, "<html>Internal Server Error</html>", "text/html")
        if path.startswith("/redirect"):
            return self._send(302, "", headers=[("Location", "/dorequest.php")])
        if path.startswith("/badstatus"):
            self.close_connection = True
            self.wfile.write(b"NOT-HTTP garbage\r\n\r\n")
            return None
        if path.startswith("/short"):
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b'{"Succ')
            return None
        if path.startswith("/echoenc"):
            p = form.get("p", "")
            return self._send(401, json.dumps({"Success": False, "Error": "bad password %s / %s / %s" % (
                urllib.parse.quote_plus(p), urllib.parse.quote(p), urllib.parse.quote(p, safe=""))}))
        if path.startswith("/echo"):
            return self._send(401, json.dumps({"Success": False, "Error": "bad password %s" % form.get("p", "")}))
        if form.get("r") != "login2":
            return self._send(400, json.dumps({"Success": False, "Error": "Unknown request"}))
        if form.get("u", "").lower() == FAKE_USER.lower() and form.get("p") == FAKE_PASSWORD:
            return self._send(200, json.dumps({"Success": True, "User": FAKE_USER, "Token": FAKE_TOKEN, "Score": 1,
                                               "SoftcoreScore": 0, "Messages": 0, "Permissions": 1}))
        self._send(401, json.dumps({"Success": False, "Status": 401, "Code": "invalid_credentials",
                                    "Error": "Invalid user/password combination. Please try again."}))

    def _record(self, form):
        u = urllib.parse.urlsplit(self.path)
        self.server.requests.append(dict(method=self.command, path=u.path, query=u.query, form=form,
                                         ua=self.headers.get("User-Agent", "")))


class Server:
    """with Server() as s: s.url -> http://127.0.0.1:PORT/dorequest.php (a thread; closed on exit)."""

    def __init__(self, port=0):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.requests = self.requests = []
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.url = self.base + "/dorequest.php"

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


if __name__ == "__main__":
    s = Server(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    print(s.url, flush=True)
    try:
        s.httpd.serve_forever()
    except KeyboardInterrupt:
        pass
