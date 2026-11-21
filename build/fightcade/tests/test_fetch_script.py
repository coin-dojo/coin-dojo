#!/usr/bin/env python3
"""Tests for build/fightcade/fetch.sh (#207) and its one pin source build/fightcade/pins.json (#267): no network.
A temp copy of pins.json holds the sha256 and size of small fake files, served through FETCH_URL_PREFIX=file://DIR.
Broken cases: a wrong sha256 (each of the three downloads), a wrong size pin, an Electron pin that disagrees with
SHASUMS256.txt, a moved Fightcade 'latest', a wrong version inside the tarball, curl missing (3), bad usage (2), a
bad pin (2), an http url (2), a pin written into fetch.sh again (a second source)."""
import hashlib, io, json, os, pathlib, re, shutil, subprocess, tarfile, tempfile, unittest

D = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = D / "fetch.sh"
PINS = D / "pins.json"
WINE, ELEC, FC = "wine-10.0-amd64-wow64.tar.xz", "electron-v9.1.0-linux-arm64.zip", "Fightcade-linux-v2.1.45.tar.gz"


def sha(b):
    return hashlib.sha256(b).hexdigest()


def fc_tarball(version="2.1.45", extra=b""):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        data = version.encode() + extra
        ti = tarfile.TarInfo("Fightcade/VERSION.txt")
        ti.size = len(data)
        t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def one_source_problems(script_text):
    """fetch.sh must read pins.json and carry no sha256 pin of its own (#267: one source for fetch.sh, the role and
    the cabinet's first-launch download)."""
    p = []
    if "pins.json" not in script_text:
        p.append("fetch.sh does not read pins.json")
    hexes = re.findall(r"\b[0-9a-f]{64}\b", script_text)
    if hexes:
        p.append("fetch.sh has its own sha256 pins: %s" % hexes)
    return p


class Fetch(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.t = pathlib.Path(self._t.name)
        self.srv = self.t / "srv"
        self.srv.mkdir()
        self.files = {WINE: b"wine-bytes", ELEC: b"electron-bytes", FC: fc_tarball()}
        self.serve()
        w = self.t / "build" / "fightcade"
        w.mkdir(parents=True)
        shutil.copy(D.parent / "lib.sh", w.parent / "lib.sh")
        shutil.copy(SCRIPT, w / "fetch.sh")
        self.script = w / "fetch.sh"
        self.pins = w / "pins.json"
        self.out = self.t / "out"
        self.set_pins()

    def tearDown(self):
        self._t.cleanup()

    def serve(self, **bad):
        for name, data in self.files.items():
            (self.srv / name).write_bytes(bad.get(name, data))
        (self.srv / "SHASUMS256.txt").write_text(
            f"{'0' * 64} *chromedriver-v9.1.0-linux-arm64.zip\n{sha(self.files[ELEC])} *{ELEC}\n")

    def set_pins(self, **over):
        """The real pins.json with each sha256/size set to the fake file's; over: {"wine.sha256": "abc", ...}."""
        p = json.loads(PINS.read_text())
        for name, f in (("wine", WINE), ("electron", ELEC), ("fightcade", FC)):
            p[name]["sha256"] = sha(self.files[f])
            p[name]["size"] = len(self.files[f])
        for k, v in over.items():
            name, key = k.split(".")
            p[name][key] = v
        self.pins.write_text(json.dumps(p))

    def run_fetch(self, *args, path=None, prefix=True):
        env = {"PATH": path or os.environ["PATH"], "HOME": str(self.t)}
        if prefix:
            env["FETCH_URL_PREFIX"] = "file://" + str(self.srv)
        return subprocess.run(["bash", str(self.script), "--out", str(self.out), *args],
                              capture_output=True, text=True, env=env)

    def kept(self):
        return sorted(p.name for p in self.out.iterdir()) if self.out.exists() else []

    # ---- the real script ----
    def test_pins_present(self):
        p = json.loads(PINS.read_text())
        self.assertEqual(p["wine"]["sha256"], "aeebbbf239e548f0136f1cd72a2e109dd9a572a8f703da3c09fa40d74d5c255f")
        self.assertEqual(p["wine"]["url"], "https://github.com/Kron4ek/Wine-Builds/releases/download/10.0/wine-10.0-amd64-wow64.tar.xz")
        self.assertEqual(p["wine"]["size"], 65074156)
        self.assertTrue(p["electron"]["url"].startswith("https://github.com/electron/electron/releases/download/v9.1.0/"))
        self.assertEqual(p["electron"]["file"], "electron-v9.1.0-linux-arm64.zip")
        self.assertEqual(p["electron"]["sha256"], "b34c7906eff2f2a87eb849291a69907f93daee2730b2e4953b05d9cc9a163269")
        self.assertEqual(p["electron"]["size"], 76823422)
        self.assertTrue(p["electron"]["shasums_url"].endswith("/v9.1.0/SHASUMS256.txt"))
        self.assertEqual(p["fightcade"]["version"], "2.1.45")
        self.assertEqual(p["fightcade"]["sha256"], "9880cbfa9b6e4a4eceab750c1d825d3c753ae4b1f84b53df421829e1423c50be")
        self.assertEqual(p["fightcade"]["size"], 164613755)
        self.assertEqual(p["fightcade"]["url"], "https://web.fightcade.com/download/Fightcade-linux-v2.1.45.tar.gz")
        self.assertEqual(p["fightcade"]["file"], "Fightcade-linux-v2.1.45.tar.gz")
        self.assertNotIn("moving", p["fightcade"])
        for name, pin in p.items():
            self.assertNotIn("latest", pin["url"], name + ": a pinned url names its version, never a moving 'latest'")
        for name, pin in p.items():
            self.assertTrue(pin["url"].startswith("https://"), name + ": https only")
            self.assertEqual(os.path.basename(pin["url"]), pin["file"], name + ": file = the url's last part")

    def test_one_pin_source(self):
        """#267: pins.json is the only place with the download pins; fetch.sh and the fightcade role read it."""
        self.assertEqual(one_source_problems(SCRIPT.read_text()), [])
        broken = SCRIPT.read_text() + 'WINE_SHA256="aeebbbf239e548f0136f1cd72a2e109dd9a572a8f703da3c09fa40d74d5c255f"\n'
        self.assertNotEqual(one_source_problems(broken), [], "BROKEN: a sha256 pin back in fetch.sh is flagged")
        self.assertNotEqual(one_source_problems(SCRIPT.read_text().replace("pins.json", "pins.txt")), [],
                            "BROKEN: fetch.sh that does not read pins.json is flagged")

    def test_syntax(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_out_gitignored(self):
        r = subprocess.run(["git", "-C", str(D), "check-ignore", "-q", str(D / "out" / "x")])
        self.assertEqual(r.returncode, 0, "build/fightcade/out must be gitignored")

    def test_help(self):
        r = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Usage", r.stdout)

    def test_unknown_option_exits_2(self):
        r = subprocess.run(["bash", str(SCRIPT), "--bogus"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown option", r.stderr)

    def test_out_without_value_exits_2(self):
        r = subprocess.run(["bash", str(SCRIPT), "--out"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    # ---- happy path ----
    def test_fetch_ok_then_kept(self):
        r = self.run_fetch()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.kept(), sorted([WINE, ELEC, FC, "VERSION.txt", "SHA256SUMS"]))
        self.assertEqual((self.out / "VERSION.txt").read_text(), "2.1.45\n")
        sums = (self.out / "SHA256SUMS").read_text()
        self.assertIn(f"{sha(self.files[WINE])}  {WINE}", sums)
        self.assertIn(f"{sha(self.files[FC])}  {FC}", sums)
        # second run needs no server: files that already match are kept
        shutil.rmtree(self.srv)
        r = self.run_fetch()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("ok (kept)"), 3)

    # ---- broken cases ----
    def test_wrong_wine_sha_refused(self):
        self.serve(**{WINE: b"tampered"})
        r = self.run_fetch()
        self.assertEqual(r.returncode, 1)
        self.assertIn("sha256 mismatch for " + WINE, r.stderr)
        self.assertEqual(self.kept(), [], "nothing may be left in out/ after a mismatch")

    def test_wrong_electron_sha_refused(self):
        self.serve(**{ELEC: b"tampered"})
        (self.srv / "SHASUMS256.txt").write_text(f"{sha(self.files[ELEC])} *{ELEC}\n")
        r = self.run_fetch()
        self.assertEqual(r.returncode, 1)
        self.assertIn("sha256 mismatch for " + ELEC, r.stderr)
        self.assertNotIn(ELEC, self.kept())

    def test_electron_pin_disagrees_with_shasums(self):
        (self.srv / "SHASUMS256.txt").write_text(f"{'1' * 64} *{ELEC}\n")
        r = self.run_fetch()
        self.assertEqual(r.returncode, 1)
        self.assertIn("differs from SHASUMS256.txt", r.stderr)
        self.assertNotIn(ELEC, self.kept())

    def test_electron_missing_from_shasums(self):
        (self.srv / "SHASUMS256.txt").write_text(f"{'1' * 64} *other.zip\n")
        r = self.run_fetch()
        self.assertEqual(r.returncode, 1)
        self.assertIn("not listed", r.stderr)

    def test_moved_fightcade_latest_explains_repin(self):
        self.serve(**{FC: fc_tarball("2.1.46")})
        r = self.run_fetch()
        self.assertEqual(r.returncode, 1)
        self.assertIn("sha256 mismatch for " + FC, r.stderr)
        self.assertIn("re-pin", r.stderr)
        self.assertNotIn(FC, self.kept())
        self.assertNotIn("VERSION.txt", self.kept())

    def test_wrong_version_inside_tarball(self):
        self.files[FC] = fc_tarball("2.1.46")
        self.serve()
        self.set_pins()  # sha matches the new tarball, the pinned version does not
        r = self.run_fetch()
        self.assertEqual(r.returncode, 1)
        self.assertIn("VERSION.txt says '2.1.46'", r.stderr)

    def test_download_failure_exits_1(self):
        (self.srv / WINE).unlink()
        r = self.run_fetch()
        self.assertEqual(r.returncode, 1)
        self.assertIn("download failed", r.stderr)

    def test_bad_pin_exits_2(self):
        self.set_pins(**{"wine.sha256": "abc"})
        r = self.run_fetch()
        self.assertEqual(r.returncode, 2)
        self.assertIn("is not a sha256", r.stderr)

    def test_http_pin_exits_2(self):
        self.set_pins(**{"fightcade.url": "http://web.fightcade.com/download/Fightcade-linux-v2.1.45.tar.gz"})
        r = self.run_fetch()
        self.assertEqual(r.returncode, 2)
        self.assertIn("not https", r.stderr)

    def test_missing_pin_key_exits_2(self):
        p = json.loads(self.pins.read_text())
        del p["electron"]["size"]
        self.pins.write_text(json.dumps(p))
        r = self.run_fetch()
        self.assertEqual(r.returncode, 2)
        self.assertIn("cannot read the pins", r.stderr)

    def test_wrong_size_pin_refused(self):
        self.set_pins(**{"wine.size": len(self.files[WINE]) + 1})
        r = self.run_fetch()
        self.assertEqual(r.returncode, 1)
        self.assertIn("fix the size pin", r.stderr)
        self.assertNotIn(WINE, self.kept())

    def test_missing_curl_exits_3(self):
        b = self.t / "bin"
        b.mkdir()
        for tool in ("bash", "dirname", "cat"):
            os.symlink(shutil.which(tool), b / tool)
        r = self.run_fetch(path=str(b))
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("curl not found", r.stderr)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "lib"))
    from hktest import unittest_main  # PASS/FAIL/SKIP per test, counted RESULT line (#137)
    unittest_main()
