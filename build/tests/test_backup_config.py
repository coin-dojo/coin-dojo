#!/usr/bin/env python3
"""Tests for build/backup_config.py (make backup-config, #286): a fake cabinet home behind a stub ssh, tmp dirs only.

Happy path: a dry run writes nothing; APPLY=1 writes the allowlisted files under personal/backup/<host>/ with every
planted secret replaced by REDACTED (retroarch.cfg, a core .opt, flycast emu.cfg, ES-DE es_settings.xml, a coin-dojo
JSON); a second run changes nothing; the summary counts added/changed/removed against the last backup.
Broken cases (must fail or be dropped): a secret the redactor misses (a redacted value copied under an innocent key,
a PEM private key block, a token-shaped value) makes the final scan refuse with nothing written; the scan finds
every planted secret in unredacted text; a symlink (file or folder) in the cabinet tree, a symlink/hard link/`..`/
absolute tar member and a symlink in the destination never escape; unlisted files (also when the cabinet side
sends its whole home), backups, logs, binaries and trophies-key.json are never copied; an unreachable cabinet or a
bad host name fails. Export: the community export's excludes (from `make -n edition-export`) drop personal/backup,
and a copy of a backup file outside personal/ is a dropped-file hit. The committed backup passes the final scan.
Fake secrets are built at run time and are obviously fake; no secret literal sits in this file.
"""
import hashlib
import io
import os
import pathlib
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests", "lib"))
from hktest import check, main  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOL = ROOT / "build" / "backup_config.py"
sys.path.insert(0, str(ROOT / "build"))
sys.path.insert(0, str(ROOT / "tools"))
import backup_config as B  # noqa: E402
try:
    import edition_export as E  # noqa: E402
except ImportError:      # the community tree has no export tool (#292)
    E = None

HOST = "testcab"
CLEAN = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MAKELEVEL", "MFLAGS", "V", "APPLY")
         and not k.startswith("BACKUP_CONFIG_")}


def fake(n):
    """An obviously fake secret value, unique per n, long enough for the leak check."""
    return "FAKE%02d" % n + "-zz" * 3


PEM = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
SS = ("Scr" + "aper", "Screen" + "Scraper")   # the ES-DE key split, so tests/test_ship_no_artifacts.py's scraper-cred rule never matches this file
TOKENISH = "Zz9" * 8          # an unbroken mixed-case run with digits, built here so no literal sits in the file


def write(base, rel, body):
    p = pathlib.Path(base) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(body, bytes):
        p.write_bytes(body)
    else:
        p.write_text(body)
    return p


class Cab:
    """A tmp tree: home/ (the fake cabinet), repo/ (the destination root), outside/ (what links point at), a stub ssh."""

    def __init__(self, d, whole_home=False):
        self.d = pathlib.Path(d)
        self.home, self.repo, self.outside = self.d / "home", self.d / "repo", self.d / "outside"
        for p in (self.home, self.repo, self.outside):
            p.mkdir()
        self.ssh = self.d / "ssh"
        if whole_home:   # a cabinet side that ignores the remote script and sends the whole home
            body = '#!/bin/sh\ncd "$FAKE_HOME" || exit 9\nCOPYFILE_DISABLE=1 exec tar -cf - .\n'
        else:            # runs the remote script (its last argument) with HOME = the fake home
            body = ('#!/bin/sh\nfor a; do last=$a; done\ncd "$FAKE_HOME" || exit 9\n'
                    'HOME="$FAKE_HOME" COPYFILE_DISABLE=1 exec /bin/sh -c "$last"\n')
        self.ssh.write_text(body)
        self.ssh.chmod(0o755)
        self.dest = self.repo / "personal" / "backup" / HOST
        self.populate()

    def populate(self):
        h = self.home
        write(h, ".config/retroarch/retroarch.cfg",
              'video_driver = "gl"\ncheevos_username = "%s"\ncheevos_password = "%s"\ncheevos_token = "%s"\n'
              'twitch_stream_key = "%s"\nnetplay_show_passworded = "true"\ncontent_show_settings_password = ""\n'
              'input_player1_a = "x"\ndiscord_app_id = "%s"\n' % (fake(1), fake(2), fake(3), fake(4), "1" * 18))
        write(h, ".config/retroarch/retroarch.cfg.bak-menu", 'cheevos_token = "%s"\n' % fake(3))
        write(h, ".config/retroarch/config/Core/Core.opt", 'core_region = "auto"\ncore_api_key = "%s"\n' % fake(5))
        write(h, ".config/retroarch/config/Core/Game.cfg", 'video_shader_enable = "true"\n')
        write(h, ".config/retroarch/config/Core/Game.cfg.123.2026-11-15@02:10:45~", 'x = "1"\n')
        write(h, ".config/retroarch/config/remaps/Core/Core.rmp", 'input_libretro_device_p1 = "1"\n')
        write(h, ".config/retroarch/autoconfig/udev/Pad.cfg", 'input_device = "Pad"\n')
        write(h, ".config/retroarch/saves/Game.srm", b"\0save")
        write(h, ".config/retroarch/logs/retroarch.log", "log line\n")
        write(h, ".config/flycast/emu.cfg", "[achievements]\nEnabled = yes\nToken = %s\nUserName = %s\nPassword = %s\n"
                                            "[config]\nrend.vsync = no\n" % (fake(6), fake(7), fake(8)))
        write(h, ".config/flycast/mappings/SDL_Pad.cfg", "[digital]\nbind0 = 1:btn_a\n")
        write(h, "ES-DE/settings/es_settings.xml",
              '<?xml version="1.0"?>\n<string name="%sUsername%s" value="%s" />\n'
              '<string name="%sPassword%s" value="%s" />\n<string name="SomeApiToken" value="%s" />\n'
              '<bool name="VirtualKeyboard" value="true" />\n<string name="ThemeSet" value="dojo" />\n'
              % (SS[0], SS[1], fake(9), SS[0], SS[1], fake(10), fake(11)))
        write(h, "ES-DE/collections/custom-x.cfg", "%ROMPATH%/md/The Secret of X.zip\n")
        write(h, "ES-DE/custom_systems/es_systems.xml", "<systemList/>\n")
        write(h, "ES-DE/custom_systems/es_find_rules.xml.bak-20261114", "<ruleList/>\n")
        write(h, ".config/coin-dojo/settings-state.json", '{"api_token": "%s", "volume": 3}\n' % fake(12))
        write(h, ".config/coin-dojo/trophies-key.json", '{"key": "%s"}\n' % fake(13))
        write(h, ".config/coin-dojo/blob.json", b"{\0}")
        write(self.outside, "secret.json", '{"note": "OUTSIDE-MARKER"}\n')
        write(self.outside, "dir/x.cfg", 'outside = "OUTSIDE-MARKER"\n')
        os.symlink(self.outside / "secret.json", h / ".config/coin-dojo/link.json")
        os.symlink(self.outside / "dir", h / ".config/retroarch/config/Evil")
        write(h, ".ssh/id_fake", "not a key, but never copied\n")

    def run(self, *args, host=HOST, ssh=None):
        env = dict(CLEAN, FAKE_HOME=str(self.home), BACKUP_CONFIG_ROOT=str(self.repo), BACKUP_CONFIG_HOSTNAME=host,
                   BACKUP_CONFIG_SSH=ssh or "%s cap@cabinet.test" % shlex.quote(str(self.ssh)))
        return subprocess.run([sys.executable, "-I", str(TOOL), *args], env=env, capture_output=True, text=True)

    def written(self):
        return {p.relative_to(self.dest).as_posix(): p for p in self.dest.rglob("*") if p.is_file() or p.is_symlink()}


def all_text(files):
    return "\n".join(p.read_text(errors="replace") for p in files.values())


def brief(r):
    """The tail of a run's output on one line (a raw RESULT/FAIL line must not land mid-output of this file)."""
    return "" if r.returncode == 0 else (r.stdout + r.stderr)[-300:].replace("\n", " | ").replace("RESULT:", "result:")


def last_result(out):
    lines = [l for l in out.splitlines() if l.startswith("RESULT:")]
    return lines[-1] if lines else ""


def test_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as d:
        c = Cab(d)
        r = c.run()
        check(r.returncode == 0 and last_result(r.stdout) == "RESULT: PASS", "dry run passes: " + brief(r))
        check("DRY RUN" in r.stdout and not (c.repo / "personal").exists(), "dry run writes nothing")
        check("redacted 5 in .config/retroarch/retroarch.cfg" in r.stdout, "dry run lists the retroarch.cfg redactions")
        check(not any(fake(n) in r.stdout + r.stderr for n in range(1, 14)), "no secret value is ever printed")
        check(re.search(r"(?m)^WARN .*es_settings\.xml .*refuses that file name in git", r.stdout),
              "a WARN says es_settings.xml must stay out of the commit (ship-no-artifacts gate, #42)")


def test_apply_redacts_every_format():
    with tempfile.TemporaryDirectory() as d:
        c = Cab(d)
        r = c.run("--apply")
        check(r.returncode == 0, "APPLY passes: " + brief(r))
        check("wrote personal/backup/" in r.stdout and "already up to date" not in r.stdout,
              "BROKEN (for-else slip): a first APPLY says it wrote, never 'already up to date'")
        files = c.written()
        want = {".config/retroarch/retroarch.cfg", ".config/retroarch/config/Core/Core.opt",
                ".config/retroarch/config/Core/Game.cfg", ".config/retroarch/config/remaps/Core/Core.rmp",
                ".config/retroarch/autoconfig/udev/Pad.cfg", ".config/flycast/emu.cfg",
                ".config/flycast/mappings/SDL_Pad.cfg", "ES-DE/settings/es_settings.xml",
                "ES-DE/collections/custom-x.cfg", "ES-DE/custom_systems/es_systems.xml",
                ".config/coin-dojo/settings-state.json"}
        check(set(files) == want, "exactly the allowlisted files are written (extra %s, missing %s)"
              % (sorted(set(files) - want), sorted(want - set(files))))
        text = all_text(files)
        leaked = [n for n in range(1, 14) if fake(n) in text]
        check(not leaked, "every planted secret is gone from the backup (left: %s)" % leaked)
        ra = files[".config/retroarch/retroarch.cfg"].read_text()
        for key in ("cheevos_username", "cheevos_password", "cheevos_token", "twitch_stream_key", "discord_app_id"):
            check('%s = "REDACTED"' % key in ra, "retroarch.cfg: %s = \"REDACTED\"" % key)
        check('netplay_show_passworded = "true"' in ra and 'content_show_settings_password = ""' in ra
              and 'video_driver = "gl"' in ra, "retroarch.cfg: booleans, empty values and normal keys are kept")
        check('core_api_key = "REDACTED"' in files[".config/retroarch/config/Core/Core.opt"].read_text(), "core .opt redacted")
        fly = files[".config/flycast/emu.cfg"].read_text()
        check("Token = REDACTED" in fly and "UserName = REDACTED" in fly and "Password = REDACTED" in fly
              and "rend.vsync = no" in fly, "flycast emu.cfg: Token, UserName, Password redacted, the rest kept")
        es = files["ES-DE/settings/es_settings.xml"].read_text()
        check(es.count('value="REDACTED"') == 3 and 'name="VirtualKeyboard" value="true"' in es
              and 'value="dojo"' in es, "es_settings.xml: scraper user + password and the token setting redacted")
        check('"api_token": "REDACTED"' in files[".config/coin-dojo/settings-state.json"].read_text(), "coin-dojo JSON redacted")
        check("The Secret of X.zip" in files["ES-DE/collections/custom-x.cfg"].read_text(), "a game called 'Secret' is kept")
        check(all(oct(p.stat().st_mode & 0o777) == "0o644" for p in files.values()), "files are written 0644")
        for line in ("redacted 1 in .config/retroarch/config/Core/Core.opt",
                     "redacted 3 in .config/flycast/emu.cfg", "redacted 3 in ES-DE/settings/es_settings.xml",
                     "redacted 1 in .config/coin-dojo/settings-state.json"):
            check(line in r.stdout, "summary line: " + line)
        r = c.run("--apply")
        check(r.returncode == 0 and "already up to date" in r.stdout and "changed 0, removed 0" in r.stdout,
              "a second APPLY changes nothing")


def test_summary_counts_added_changed_removed():
    with tempfile.TemporaryDirectory() as d:
        c = Cab(d)
        c.run("--apply")
        write(c.home, ".config/retroarch/config/Core/Game.cfg", 'video_shader_enable = "false"\n')
        (c.home / ".config/flycast/mappings/SDL_Pad.cfg").unlink()
        write(c.home, "ES-DE/collections/custom-y.cfg", "%ROMPATH%/md/Y.zip\n")
        r = c.run()
        check("added 1, changed 1, removed 1" in r.stdout and "+ ES-DE/collections/custom-y.cfg" in r.stdout
              and "~ .config/retroarch/config/Core/Game.cfg" in r.stdout and "- .config/flycast/mappings/SDL_Pad.cfg" in r.stdout,
              "dry run summary: added 1, changed 1, removed 1 with their paths")
        check((c.dest / ".config/flycast/mappings/SDL_Pad.cfg").exists(), "the dry run removed nothing")
        r = c.run("--apply")
        check(r.returncode == 0 and not (c.dest / ".config/flycast/mappings").exists()
              and (c.dest / "ES-DE/collections/custom-y.cfg").exists(), "APPLY mirrors: removed file and empty folder gone, new file in")


def test_missed_secret_refused_nothing_written():
    cases = (
        (".config/coin-dojo/notes.json", '{"note": "%s"}\n' % fake(3), "a redacted value appears here"),
        (".config/retroarch/config/Core/Leak.cfg", PEM + "\n", "private key block"),
        (".config/retroarch/config/Core/Odd.cfg", 'remember_me = "%s"\n' % TOKENISH, "token-shaped string"),
        ("ES-DE/collections/custom-z.cfg", TOKENISH + "\n", "token-shaped string"),
    )
    for rel, body, why in cases:
        with tempfile.TemporaryDirectory() as d:
            c = Cab(d)
            write(c.home, rel, body)
            r = c.run("--apply")
            check(r.returncode == 1 and last_result(r.stdout) == "RESULT: FAIL" and why in r.stdout,
                  "BROKEN: %s (%s) makes the final scan refuse" % (rel, why))
            check(not (c.repo / "personal").exists(), "BROKEN: nothing written after a refused scan (%s)" % rel)
            check(fake(3) not in r.stdout + r.stderr, "the refusal never prints the value")


def test_scan_finds_unredacted_secrets_in_every_format():
    samples = {
        "retroarch.cfg": 'cheevos_token = "%s"\n' % fake(3),
        "emu.cfg": "[achievements]\nToken = %s\n" % fake(6),
        "es_settings.xml": '<string name="%sPassword%s" value="%s" />\n' % (SS[0], SS[1], fake(10)),
        "x.json": '{"api_token": "%s"}\n' % fake(12),
    }
    for rel, text in samples.items():
        check(B.scan_text(rel, text) != [], "BROKEN: the scan finds the unredacted secret in " + rel)
        new, found = B.redact_text(text)
        check(len(found) == 1 and B.scan_text(rel, new) == [], "after redaction the scan is clean: " + rel)
    check(B.scan_text("trophies-key.json", "{}") != [], "BROKEN: an excluded file name is a scan hit")
    check(B.scan_text("x.cfg", 'mupen64plus-EnableN64DepthCompare = "False"\n') == [], "a CamelCase key name is no token")
    check(B.scan_text("x.cfg", 'input_overlay = "/data/x/controls/overlays/atari2600/Joust (USA).cfg"\n') == [],
          "a long path is no token")


def tar_of(members):
    """A tar (bytes) of (name, kind, data/target) members: kind file, symlink, hardlink."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, kind, data in members:
            ti = tarfile.TarInfo(name)
            if kind == "file":
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
            else:
                ti.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                ti.linkname = data
                tf.addfile(ti)
    return buf.getvalue()


def test_stage_drops_links_and_escapes():
    with tempfile.TemporaryDirectory() as d:
        dest = pathlib.Path(d) / "stage" / "in"
        dest.mkdir(parents=True)
        good = ".config/coin-dojo/ok.json"
        archive = tar_of([
            (good, "file", b'{"a": 1}\n'),
            (".config/coin-dojo/evil.json", "symlink", "/etc/passwd"),
            (".config/coin-dojo/hard.json", "hardlink", good),
            ("../escape.json", "file", b"x"),
            ("/abs/.config/coin-dojo/abs.json", "file", b"x"),
            (".config/coin-dojo/../../../up.json", "file", b"x"),
        ])
        kept, dropped = B.stage(archive, dest)
        check(set(kept) == {good}, "BROKEN: only the regular allowlisted member is kept (%s)" % sorted(kept))
        check(len(dropped.get("link", [])) == 2 and len(dropped.get("unsafe-path", [])) == 3,
              "BROKEN: symlink + hard link dropped as link, `..` and absolute names as unsafe-path (%s)" % dropped)
        outside = [p for p in pathlib.Path(d).rglob("*") if p.is_file() and dest not in p.parents]
        check(outside == [], "BROKEN: nothing was written outside the staging dir (%s)" % outside)


def test_symlinks_in_cabinet_tree_never_escape():
    with tempfile.TemporaryDirectory() as d:
        c = Cab(d)
        r = c.run("--apply")
        files = c.written()
        check(".config/coin-dojo/link.json" not in files and not any("Evil" in f for f in files),
              "BROKEN: a symlinked file and a symlinked folder on the cabinet are not copied")
        check("OUTSIDE-MARKER" not in all_text(files), "BROKEN: nothing a link points at reaches the backup")
    with tempfile.TemporaryDirectory() as d:
        c = Cab(d, whole_home=True)
        r = c.run("--apply")
        files = c.written()
        check(r.returncode == 0 and "OUTSIDE-MARKER" not in all_text(files) and "dropped" in r.stdout,
              "BROKEN: links inside a whole-home archive are dropped, not followed")


def test_unlisted_files_never_copied():
    with tempfile.TemporaryDirectory() as d:
        c = Cab(d, whole_home=True)   # the cabinet side sends everything: the local allowlist must hold on its own
        r = c.run("--apply")
        files = c.written()
        bad = [f for f in files if f.startswith(".ssh/") or "/saves/" in f or "/logs/" in f or ".bak" in f
               or f.endswith("~") or f.endswith("trophies-key.json") or f.endswith("blob.json")]
        check(r.returncode == 0 and not bad, "BROKEN: unlisted, backup, log, binary and key files are not copied (%s)" % bad)
        check("dropped" in r.stdout and "unlisted" in r.stdout, "the dropped unlisted files are counted")
    for rel in (".ssh/id_x", ".config/retroarch/saves/a.cfg", ".config/retroarch/retroarch.cfg.bak",
                ".config/coin-dojo/trophies-key.json", ".config/retroarch/config/x.cfg.1.2026@1~", "ES-DE/gamelists/x.xml",
                ".config/retroarch/config/Core/state.state1"):
        check(not B.allowed(rel), "BROKEN: %s is not on the allowlist" % rel)
    for rel in (".config/retroarch/retroarch.cfg", ".config/retroarch/config/FinalBurn Neo/sf2.cfg",
                "ES-DE/settings/es_settings.xml", ".config/coin-dojo/settings-state.json"):
        check(B.allowed(rel), "%s is on the allowlist" % rel)


def test_file_identical_to_a_public_repo_file_is_skipped():
    """A remap the deploy copied from the repo comes back byte for byte: skipped, or the community export's
    dropped-file check flags the public original (#240). The same bytes under personal/ do not count."""
    body = 'input_libretro_device_p1 = "1"\n' + "# shipped by the deploy\n" * 4
    with tempfile.TemporaryDirectory() as d:
        c = Cab(d)
        write(c.home, ".config/retroarch/config/remaps/Core/Shipped.rmp", body)
        write(c.home, ".config/retroarch/config/remaps/Core/Mine.rmp", body.replace("deploy", "owner"))
        write(c.repo, "skills/controls/remaps/Core/Shipped.rmp", body)
        write(c.repo, "personal/notes/Mine.rmp", body.replace("deploy", "owner"))
        for args in (["init", "-q"], ["add", "-A"]):
            subprocess.run(["git", "-C", str(c.repo)] + args, check=True, capture_output=True)
        r = c.run("--apply")
        files = c.written()
        check(r.returncode == 0 and ".config/retroarch/config/remaps/Core/Shipped.rmp" not in files
              and "dropped 1 in-repo" in r.stdout, "BROKEN: a copy of a tracked public file is skipped (in-repo)")
        check(".config/retroarch/config/remaps/Core/Mine.rmp" in files, "a file only personal/ holds is still backed up")


IP_FAKE = ".".join(["10", "0", "0", "77"])            # private range, built here so the file holds no address
MAC_FAKE = ":".join(["02", "00", "5e", "10", "00", "01"])  # locally administered, not a real NIC
MAIL_FAKE = "player" + "@" + "example.invalid"
HEX_FAKE = "ab12" * 8                                  # 32 hex digits
JWT_FAKE = "eyJ" + "hbGciOiJub25lIn0" + "." + "eyJzdWIiOiJ4In0x" + "."
B64_FAKE = "aZ9_" * 8 + "Q1"                           # base64url run, upper + lower + digits
GUID_LINE = HEX_FAKE + ",USB gamepad,a:b4,b:b0,start:b9,dpup:h0.1,leftx:a0,platform:Linux,"


def test_m1_new_names_addresses_and_userinfo_redacted():
    cases = {
        'wifi_ssid = "%s"\n' % fake(20): "wifi_ssid",
        "psk = %s\n" % fake(21): "psk",
        'netplay_nickname = "%s"\n' % fake(22): "netplay_nickname",
        'cloud_access_key = "%s"\n' % fake(23): "cloud_access_key",
        'netplay_ip_address = "%s"\n' % IP_FAKE: "netplay_ip_address",
        'netplay_custom_mitm_server = "%s"\n' % fake(24): "netplay_custom_mitm_server",
        'cheevos_custom_host = "%s"\n' % fake(25): "cheevos_custom_host",
        'webdav_url = "https://dav.example.invalid/%s"\n' % fake(26): "webdav_url",
        '<string name="ProxyAccessKey" value="%s" />\n' % fake(27): "ProxyAccessKey",
        '{"home_ssid": "%s"}\n' % fake(28): "home_ssid",
    }
    for text, key in cases.items():
        check(B.scan_text("x.cfg", text) != [], "BROKEN: the scan finds the unredacted %s" % key)
        new, found = B.redact_text(text)
        check([k for k, _v in found] == [key] and "REDACTED" in new and B.scan_text("x.cfg", new) == [],
              "M1: %s is redacted and the result scans clean" % key)
    for text in ('ai_service_url = "http://localhost:4404/"\n',
                 'core_updater_buildbot_assets_url = "http://buildbot.libretro.com/assets/"\n',
                 'netplay_ip_address = ""\n', 'cheevos_custom_host = ""\n'):
        check(B.redact_text(text)[1] == [], "M1: a stock or empty address is kept: " + text.split(" =")[0])
    line = 'menu_wallpaper = "ftp://%s:%s@files.example.invalid/x.png"\n' % ("u" + "ser", fake(29))
    check(B.scan_text("x.cfg", line) != [], "BROKEN: URL user info under an innocent key is a scan hit")
    new, found = B.redact_text(line)
    check('"ftp://REDACTED@files.example.invalid/x.png"' in new and fake(29) not in new and B.scan_text("x.cfg", new) == [],
          "M1: URL user info is always redacted (scheme://REDACTED@host)")


def test_m2_second_net_fails_closed():
    planted = {
        "IPv4 address outside loopback": 'video_shader_dir = "/mnt/%s/shaders"\n' % IP_FAKE,
        "MAC address": "bt_pad = %s\n" % MAC_FAKE,
        "email address": 'core_note = "%s"\n' % MAIL_FAKE,
        "URL user info": "%ROMPATH%/smb://x" + ":y@nas/game.zip\n",
        "32+ hex digits": 'audio_device = "%s"\n' % HEX_FAKE,
        "JWT": 'cache_blob = "%s"\n' % JWT_FAKE,
        "base64url run": 'core_blob = "%s"\n' % B64_FAKE,
        "16+ letters/digits under": 'netplay_mitm_server = "%s"\n' % ("Zq" * 9),
    }
    for kind, text in planted.items():         # raw text: the second net must catch it with no help from the redactor
        hits = B.scan_text("x.cfg", text)
        check(any(kind in why for _ln, why in hits), "BROKEN M2: %s under a key the redactor does not know is a hit" % kind)
    check(B.scan_text("x.cfg", 'video_shader_dir = "/mnt/127.0.0.1/shaders"\n') == [], "M2: loopback is fine")
    check(B.scan_text("x.cfg", 'menu_wallpaper = "ftp://REDACTED@files.example.invalid/x.png"\n') == [],
          "M2: redacted URL user info is not an email address")
    check(B.scan_text("ES-DE/settings/es_controllers.txt", GUID_LINE) == [],
          "M2: the SDL GUID opening an es_controllers.txt mapping line is allowed")
    check(B.scan_text("ES-DE/settings/other.txt", GUID_LINE) != [], "BROKEN M2: the same line in another file is a hit")
    check(B.scan_text("ES-DE/settings/es_controllers.txt", GUID_LINE.replace("platform:Linux,", "note:" + HEX_FAKE + ",platform:Linux,")) != [],
          "BROKEN M2: a second 32-hex on a controller line is a hit")
    check(B.scan_text("ES-DE/settings/es_controllers.txt", "token:" + HEX_FAKE + "\n") != [],
          "BROKEN M2: a 32-hex that is not a mapping line's GUID is a hit, even in es_controllers.txt")
    check(B.scan_text("x.opt", 'fbneo-dipswitch-1941-2C_to_Start,_1_to_Cont = "Off"\n') == [],
          "M2: a long core option key name is no base64url run")
    with tempfile.TemporaryDirectory() as d:
        c = Cab(d)
        write(c.home, ".config/retroarch/config/Core/Net.cfg", 'video_shader_dir = "/mnt/%s/s"\n' % IP_FAKE)
        r = c.run("--apply")
        check(r.returncode == 1 and "IPv4 address outside loopback" in r.stdout and not (c.repo / "personal").exists()
              and IP_FAKE not in r.stdout, "BROKEN M2: a planted address refuses the whole run, nothing written, value not printed")


def stub_cat(c, archive):
    """A stub ssh that sends a prebuilt archive, whatever the remote script says."""
    f = c.d / "archive.tar"
    f.write_bytes(archive)
    s = c.d / "ssh-cat"
    s.write_text("#!/bin/sh\nexec cat %s\n" % shlex.quote(str(f)))
    s.chmod(0o755)
    return str(s) + " cap@cabinet.test"


def test_l3_control_characters_in_names_dropped():
    good = ".config/coin-dojo/ok.json"
    with tempfile.TemporaryDirectory() as d:
        dest = pathlib.Path(d) / "stage"
        dest.mkdir()
        kept, dropped = B.stage(tar_of([(good, "file", b"{}\n"), (".config/coin-dojo/a\nb.json", "file", b"{}\n"),
                                        (".config/coin-dojo/esc\x1b[2J.json", "file", b"{}\n"),
                                        (".config/coin-dojo/del\x7f.json", "file", b"{}\n")]), dest)
        check(set(kept) == {good} and len(dropped.get("control-char", [])) == 3,
              "BROKEN L3: names with a newline, an escape or DEL are dropped (%s)" % dropped)
        check(all("\n" not in n and "\x1b" not in n for n in dropped["control-char"]), "dropped names are printed escaped")


def test_l4_caps_fail_cleanly():
    rc, out, _err = B.pull(["sh", "-c", "head -c 300000 /dev/zero"], cap=100000)
    check(out is None, "BROKEN L4: output past the cap is refused (None), not buffered")
    rc, out, _err = B.pull(["sh", "-c", "printf abc"], cap=100000)
    check(rc == 0 and out == b"abc", "L4: output under the cap is returned whole")
    with tempfile.TemporaryDirectory() as d:
        dest = pathlib.Path(d) / "stage"
        dest.mkdir()
        try:
            B.stage(tar_of([(".config/coin-dojo/%d.json" % i, "file", b"{}\n") for i in range(5)]), dest, max_members=4)
            raised = False
        except B.StageError:
            raised = True
        check(raised, "BROKEN L4: more members than the cap raise StageError")
    with tempfile.TemporaryDirectory() as d:
        c = Cab(d)
        many = tar_of([(".config/coin-dojo/%05d.json" % i, "file", b"") for i in range(B.MAX_MEMBERS + 1)])
        r = c.run("--apply", ssh=stub_cat(c, many))
        check(r.returncode == 1 and "more than %d tar members" % B.MAX_MEMBERS in r.stdout and "Traceback" not in r.stderr
              and not (c.repo / "personal").exists(), "BROKEN L4: %d+ members fail with a FAIL line, nothing written" % B.MAX_MEMBERS)


def test_l5_collision_is_a_fail_line():
    for name, members in (
            ("the same file twice", [(".config/coin-dojo/a.json", "file", b"{}\n")] * 2),
            ("a file, then a folder of that name", [(".config/retroarch/config/X.cfg", "file", b"x = 1\n"),
                                                    (".config/retroarch/config/X.cfg/y.cfg", "file", b"y = 1\n")])):
        with tempfile.TemporaryDirectory() as d:
            c = Cab(d)
            r = c.run("--apply", ssh=stub_cat(c, tar_of(members)))
            check(r.returncode == 1 and re.search(r"(?m)^FAIL .*collides", r.stdout) and "Traceback" not in r.stderr
                  and not (c.repo / "personal").exists(), "BROKEN L5: %s prints a FAIL line, no traceback" % name)


def test_destination_symlink_not_followed():
    with tempfile.TemporaryDirectory() as d:
        c = Cab(d)
        target = c.outside / "victim.cfg"
        target.write_text("untouched\n")
        link = c.dest / ".config/retroarch/retroarch.cfg"
        link.parent.mkdir(parents=True)
        os.symlink(target, link)
        r = c.run("--apply")
        check(r.returncode == 0 and target.read_text() == "untouched\n", "BROKEN: a symlink in the backup is not written through")
        check(not link.is_symlink() and "REDACTED" in link.read_text(), "the symlink is replaced by the real file")


def test_unreachable_and_bad_host_fail():
    with tempfile.TemporaryDirectory() as d:
        c = Cab(d)
        bad = c.d / "ssh-down"
        bad.write_text("#!/bin/sh\necho 'ssh: connect to host: timed out' >&2\nexit 255\n")
        bad.chmod(0o755)
        r = c.run("--apply", ssh=str(bad) + " cap@cabinet.test")
        check(r.returncode == 1 and last_result(r.stdout) == "RESULT: FAIL" and not (c.repo / "personal").exists(),
              "BROKEN: an unreachable cabinet fails and writes nothing")
        r = c.run("--apply", host="../escape")
        check(r.returncode == 2 and last_result(r.stdout) == "RESULT: FAIL" and not (c.repo / "personal").exists()
              and not (c.d / "escape").exists(), "BROKEN: a host name that is not one label is refused")


def test_remote_script_only_reads():
    s = B.remote_script()
    check("find " in s and "tar --null -T - -cf -" in s, "the cabinet runs find + tar to stdout")
    check("! -name trophies-key.json" in s, "trophies-key.json is excluded on the cabinet side already")
    s = re.sub(r"\S*[./]\S*", "PATH", s)   # paths and file names name the apps (.config/retroarch); commands must not
    check(not re.search(r"\b(rm|mv|cp|sudo|chmod|chown|tee|dd|mkdir|touch|retroarch|flycast)\b", s),
          "the remote script has no write verb, no sudo, no app launch")
    check(not re.search(r">\s*[^&\s]", s), "the remote script redirects nothing into a file")
    planted = re.sub(r"\S*[./]\S*", "PATH", s + "; rm -f x")
    check(re.search(r"\b(rm|mv|cp|sudo)\b", planted), "BROKEN: a planted rm in the remote script is caught")


def test_make_target_dry_by_default():
    r = subprocess.run(["make", "--no-print-directory", "-n", "backup-config"], cwd=ROOT, env=CLEAN, capture_output=True, text=True)
    line = [l for l in r.stdout.splitlines() if "backup_config.py" in l]
    check(r.returncode == 0 and line and "--apply" not in line[0], "make backup-config is a dry run: " + brief(r))
    r = subprocess.run(["make", "--no-print-directory", "-n", "backup-config", "APPLY=1"], cwd=ROOT, env=CLEAN,
                       capture_output=True, text=True)
    check(r.returncode == 0 and "backup_config.py --apply" in r.stdout, "APPLY=1 passes --apply")
    check("git commit" not in r.stdout and "git add" not in r.stdout, "the target never commits")


def export_excludes():
    r = subprocess.run(["make", "--no-print-directory", "-n", "edition-export", "OUT=/nonexistent/out"], cwd=ROOT, env=CLEAN,
                       capture_output=True, text=True)
    return re.findall(r"--exclude (\S+)", r.stdout)


def test_community_export_drops_personal_backup():
    if E is None:
        check(True, "community tree: no export tool here, nothing to export")
        return
    ex = export_excludes()
    check("personal/" in ex, "make edition-export passes --exclude personal/ (%s)" % ex)
    real = E.file_set(ROOT, ex)
    check(not [p for p, _l in real if p.startswith("personal/")], "the real export file set has nothing under personal/")
    rel = "personal/backup/%s/.config/retroarch/retroarch.cfg" % HOST
    listing = [("README.md", False), (rel, False), ("personal/backup/%s/ES-DE/settings/es_settings.xml" % HOST, False)]
    check([p for p, _l in E.file_set(ROOT, ex, listing)] == ["README.md"], "personal/backup/** is never in the export set")
    check([p for p, _l in E.file_set(ROOT, [x for x in ex if x != "personal/"], listing)] != ["README.md"],
          "BROKEN: without the personal/ exclude the backup would be exported (the check has teeth)")
    with tempfile.TemporaryDirectory() as d:
        repo, out = pathlib.Path(d) / "repo", pathlib.Path(d) / "out"
        body = 'video_driver = "gl"\n' + "# hand-tuned line\n" * 8
        write(repo, rel, body)
        write(out, "docs/copied.cfg", body)
        shas = E.left_out_shas(repo, listing[1:2], [])
        hits = E.scan_tree(out, [], private_shas=shas)
        check(("docs/copied.cfg", 0, "private:dropped-file") in hits,
              "BROKEN: a backup file copied outside personal/ is a dropped-file hit in the export scan")


def test_committed_backup_is_clean():
    base = ROOT / "personal" / "backup"
    if not base.exists():
        check(True, "no committed backup yet")
        return
    hits = B.scan_tree(base)
    check(hits == [], "the committed backup passes the final scan (%s)" % hits[:5])
    names = [p.name for p in base.rglob("*")]
    check("trophies-key.json" not in names and not any(".bak" in n or n.endswith("~") for n in names),
          "the committed backup has no key file and no backups")
    check(all(B.allowed(p.relative_to(d).as_posix()) for d in base.iterdir() if d.is_dir()
              for p in d.rglob("*") if p.is_file()), "every committed backup file is on the allowlist")
    public = B.public_shas(ROOT)
    dupes = [str(p.relative_to(base)) for p in base.rglob("*") if p.is_file() and p.stat().st_size >= B.SAME_MIN
             and hashlib.sha256(p.read_bytes()).hexdigest() in public]
    check(not dupes, "no committed backup file is a copy of a public repo file (export dropped-file hit): %s" % dupes[:5])


if __name__ == "__main__":
    main(globals())
