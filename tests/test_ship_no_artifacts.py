#!/usr/bin/env python3
"""Compliance gate (ticket #29): coin-dojo ships NO artifacts.

Founding principle (AGENTS.md): no Pi image, launcher binary, ROMs, artwork, BIOS or firmware in git.
Scans GIT-TRACKED files only (`git ls-files`), so untracked local files never fail the suite.
Pure stdlib; pytest-compatible, or run directly:  python3 tests/test_ship_no_artifacts.py

Rules (each failure names the path and the rule):
  rom-ext      ROM / disc / BIOS extension
  bios-name    known BIOS file name
  binary       tracked file > 5 MB, ELF/Mach-O magic, or an extension-less "flycast*" file (the binary)
  personal-art any image or glow file under a theme's _inc/art/backdrops or _inc/art/bootmenu that is not on the
               SHIPPED_ART list (path + sha256, #263: the credited night art, the original dojo.png, nothing else),
               or publisher/SFA2/box-art file names
               (skipped under personal/: the maintainer's private art zone, never exported to the community repo;
               every other rule still applies there)
  secret-file  es_settings.xml* (ScreenScraper password, incl. .bak copies), *.pem, id_ed25519*, id_rsa*,
               *.nmconnection (WiFi PSK), .env* (secrets file)
  scraper-cred any tracked text file with a non-empty Scraper(Username|Password)ScreenScraper value (#42)

The broken case: test_selftest_* plant forbidden files in a throwaway git repo and assert every rule
fires; a clean throwaway repo must pass.
"""
import fnmatch
import hashlib
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ROM_EXTS = {".zip", ".7z", ".chd", ".iso", ".bin", ".cue", ".gdi", ".cdi", ".img", ".nes", ".sfc",
            ".smc", ".gba", ".gb", ".gbc", ".z64", ".n64", ".md", ".pce"}
# ".md" is both Mega Drive ROM and Markdown. Docs are plain text: a tracked .md is only flagged when it
# is binary (NUL byte) or carries a Mega Drive header ("SEGA" at 0x100). Same-name allowlist is empty on purpose.
AMBIGUOUS_TEXT_EXTS = {".md"}
# Explicit allowlist of tracked paths that may carry a ROM_EXTS extension. Currently EMPTY: `git ls-files`
# on main shows only .md docs (handled by the text check above). Add entries here with a justification.
ALLOW_ROM_EXT = set()

BIOS_NAMES = ["dc_boot.bin", "dc_flash.bin", "naomi.zip", "awbios.zip", "scph*.bin", "neogeo.zip",
              "saturn_bios.bin", "bios.bin", "bios*.zip", "pgm.zip", "airlbios.zip", "hod2bios.zip",
              "stvbios.zip", "sega_101.bin", "mpr-*.bin", "gba_bios.bin", "syscard*.pce",
              "kick*.rom", "carthw.hex"]
MAX_BYTES = 5 * 1024 * 1024
MAGICS = {b"\x7fELF": "ELF executable", b"\xcf\xfa\xed\xfe": "Mach-O executable",
          b"\xfe\xed\xfa\xcf": "Mach-O executable", b"MZ": "Windows PE executable"}
BACKDROP_OK = {".gitignore", "readme.md"}     # .gitignore keeps your own art out
# The art that ships on purpose (#263, the maintainer 2026-11-19), exactly these files by path and sha256, credited in
# THIRD_PARTY.md and docs/LEGAL.md (removed on request): the night backdrop, its eye glow and the two boot-menu crops of
# it, plus the original dojo.png (gen_backdrop.py output: when the generator changes, update its sha256). Anything
# else in those folders fails, a listed file with other bytes fails.
_ART = "themes/coin-dojo/_inc/art/"
SHIPPED_ART = {
    _ART + "backdrops/default.png": "d978df53331eefc1bff493ad1425a4dadcbf4acbf49dbd31b1343f7ad79fee0c",
    _ART + "backdrops/dojo.png": "7d8fcf0ad733d9eb7cb5e2d5942e2b324675f10c59b4a3dba223452d0e3159e1",
    _ART + "backdrops/eye-glow.png": "47cab09a5e5e08f6d9674be690e3f409596b73f8eb17f92111e56b309d217b30",
    _ART + "backdrops/eye-glow.json": "8c75cabdf6e62ff2d1da6beba949424f5c3a7c334bfb38f60161ec57a89f17b8",
    _ART + "backdrops/eye-glow.xml": "daa10505db7c7a35b7a1319521c90969762d886dcd2487d9386008079b463266",
    _ART + "bootmenu/esde.png": "7d0a0646c0271021b6e1ac23599f8e45aa5cb97ac44480b6713a452ac121766c",
    _ART + "bootmenu/fightcade.png": "170cdfd3c24568387801e4978b66d2b006f600b378f485e02364f6ec80077097",
}
ART_WORDS = ["alpha2", "sfa2", "cap" + "com", "boxart"]     # split: the edition export renames the plain word (#190)
PERSONAL = "personal"   # the maintainer's private zone (personal/README.md): art OK, never exported
SECRET_GLOBS = ["es_settings.xml*", "*.pem", "id_ed25519*", "id_rsa*", "*.nmconnection", ".env*"]
# ES-DE stores ScreenScraper credentials as XML <string name=KEY value=VALUE />, KEY being
# Scraper{Username,Password}ScreenScraper. An empty value is fine (a template); any non-empty value is a
# real credential (#42).
SCRAPER_CRED = re.compile(rb'Scraper(?:Username|Password)ScreenScraper"?\s+value\s*=\s*"[^"]+"')


def tracked(root):
    out = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def _head(path, n=0x110):
    with open(path, "rb") as f:
        return f.read(n)


def scan(root, shipped=None):
    """Return a list of 'path: [rule] reason' failures for git-tracked files under root (shipped: the art allowlist,
    {path: sha256}; default SHIPPED_ART)."""
    shipped = SHIPPED_ART if shipped is None else shipped
    bad = []
    for rel in tracked(root):
        full = os.path.join(root, rel)
        if not os.path.isfile(full) or os.path.islink(full):
            continue
        low = rel.lower()
        base = os.path.basename(low)
        ext = os.path.splitext(base)[1]

        if ext in ROM_EXTS and rel not in ALLOW_ROM_EXT:
            head = _head(full)
            texty = ext in AMBIGUOUS_TEXT_EXTS and b"\0" not in head and head[0x100:0x104] != b"SEGA"
            if not texty:
                bad.append(f"{rel}: [rom-ext] ROM/disc/BIOS extension '{ext}' - ship no ROMs/BIOS")
        for pat in BIOS_NAMES:
            if fnmatch.fnmatch(base, pat):
                bad.append(f"{rel}: [bios-name] matches BIOS name '{pat}' - ship no BIOS")
                break

        size = os.path.getsize(full)
        if size > MAX_BYTES:
            bad.append(f"{rel}: [binary] {size} bytes > 5 MB - ship no images/binaries")
        head = _head(full, 4)
        for magic, what in MAGICS.items():
            if head.startswith(magic) and not (magic == b"MZ" and ext in (".md", ".txt", ".py", ".sh")):
                bad.append(f"{rel}: [binary] {what} magic - ship no launcher/emulator binaries")
                break
        if "flycast" in base and ext == "":
            bad.append(f"{rel}: [binary] extension-less 'flycast*' file looks like the emulator binary "
                       "- it is built, never shipped (scripts like flycast-run.sh are fine)")

        parts = low.split("/")
        art_ok = parts[0] == PERSONAL      # personal art is allowed here (private repo only); other rules still run
        if not art_ok and ("backdrops" in parts or "bootmenu" in parts) and "art" in parts and "_inc" in parts \
                and "themes" in parts and base not in BACKDROP_OK:
            want = shipped.get(rel)
            if want is None:
                bad.append(f"{rel}: [personal-art] not on the shipped-art list (path + sha256) - art you did not "
                           "make stays in personal/ or hk_theme_art_dir")
            else:
                with open(full, "rb") as f:
                    if hashlib.sha256(f.read()).hexdigest() != want:
                        bad.append(f"{rel}: [personal-art] bytes differ from the listed sha256 - only the credited "
                                   "art ships (update the list on purpose, with THIRD_PARTY.md)")
        for w in ([] if art_ok else ART_WORDS):
            if w in low:
                bad.append(f"{rel}: [personal-art] name contains '{w}' - publisher/SFA2/box art is not "
                           "ours to ship (community theme = original art only)")
                break

        for pat in SECRET_GLOBS:
            if fnmatch.fnmatch(base, pat):
                bad.append(f"{rel}: [secret-file] matches '{pat}' - secrets-shaped file (scraper "
                           "password / private key) must never be in git")
                break

        if size <= MAX_BYTES:
            with open(full, "rb") as f:
                if SCRAPER_CRED.search(f.read()):
                    bad.append(f"{rel}: [scraper-cred] non-empty ScreenScraper username/password value "
                               "- scraper credentials must never be in git")
    return bad


# ---- the real check -------------------------------------------------------------------------------
def test_shipped_art_list_matches_the_repo():
    """Every listed file is tracked with its sha256 (a stale list fails here, a stranger fails scan)."""
    track = set(tracked(ROOT))
    for rel, sha in SHIPPED_ART.items():
        assert rel in track, f"{rel} is on the shipped-art list but not tracked"
        with open(os.path.join(ROOT, rel), "rb") as f:
            assert hashlib.sha256(f.read()).hexdigest() == sha, f"{rel}: sha256 differs from the list"


def test_repo_ships_no_artifacts():
    bad = scan(ROOT)
    assert not bad, "ship-no-artifacts violations:\n  " + "\n  ".join(bad)


# ---- self-test: the deliberately broken repo -----------------------------------------------------
def _make_repo(files):
    d = tempfile.mkdtemp(prefix="shipnoart-")
    subprocess.run(["git", "init", "-q", d], check=True)
    for rel, data in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
    subprocess.run(["git", "-C", d, "add", "-A", "-f"], check=True)
    return d


BROKEN = {
    "roms/game.zip": b"PK",                                   # rom-ext
    "x/disc.chd": b"x",                                       # rom-ext
    "x/sonic.md": b"\0\0binary rom",                          # rom-ext (binary .md)
    "bios/dc_boot.bin": b"x",                                 # bios-name (+rom-ext)
    "bios/scph5501.bin": b"x",                                # bios-name
    "bios/neogeo.zip": b"x",                                  # bios-name
    "big/blob.dat": b"\0" * (5 * 1024 * 1024 + 1),            # binary: size
    "bin/tool": b"\x7fELF\x02\x01",                           # binary: ELF
    "bin/flycast": b"#!/bin/sh\n",                            # binary: name
    "dist/flycast-arm64": b"#!/bin/sh\n",                      # binary: versioned binary name
    "themes/t/_inc/art/backdrops/fighter.png": b"png",  # personal-art: backdrop not on the list
    "themes/t/_inc/art/bootmenu/esde.png": b"png2",     # personal-art: listed path, other bytes
    "themes/t/_inc/art/bootmenu/extra.png": b"png",     # personal-art: bootmenu folder, not on the list
    "art/SFA2-logo.png": b"png",                              # personal-art: sfa2
    "art/alpha2.jpg": b"jpg",                                 # personal-art: alpha2
    "art/Cap" "com.png": b"png",                              # personal-art: the publisher word
    "art/boxart.png": b"png",                                 # personal-art: boxart
    "cfg/es_settings.xml": b"<x/>",                           # secret-file
    "keys/a.pem": b"x",                                       # secret-file
    "keys/id_ed25519": b"x",                                  # secret-file
    "personal/roms/game.zip": b"PK",                          # rom-ext still fires inside personal/
    "personal/keys/a.pem": b"x",                              # secret-file still fires inside personal/
    "cfg/es_settings.xml.bak-collections": b"<x/>",           # secret-file: backup copy (#42)
    "ES-DE/_backups/1/es_settings.xml": b"<x/>",              # secret-file: hand-made backup dir copy
    "etc/NetworkManager/wifi.nmconnection": b"psk=x",         # secret-file: WiFi PSK
    "keys/id_rsa": b"x",                                      # secret-file
    "keys/id_rsa.pub": b"x",                                  # secret-file (glob is id_rsa*, like id_ed25519*)
    ".env": b"A=1\n",                                         # secret-file
    "deploy/.env.local": b"A=1\n",                            # secret-file
    # scraper-cred: key name split so this test file itself never matches the rule
    "cfg/settings-copy.xml": b'<string name="Scraper' + b'PasswordScreenScraper" value="x1" />\n',
    "notes/scraper.txt": b'Scraper' + b'UsernameScreenScraper" value="player_one"',
}
CLEAN = {
    "README.md": b"# hi\n",
    "docs/notes.md": b"plain markdown\n",
    "build/flycast/build.sh": b"#!/bin/sh\n",
    "build/flycast/README.md": b"how to build\n",
    "themes/t/_inc/art/backdrops/default.png": b"png",        # on FIXTURE_SHIPPED
    "themes/t/_inc/art/backdrops/README.md": b"drop yours here\n",
    "themes/t/_inc/art/backdrops/.gitignore": b"*\n!default.png\n",
    "ansible/roles/dreamcast/files/flycast-run.sh": b"#!/bin/sh\n",
    "ansible/roles/dreamcast/files/flycast-exit-combo.py": b"print()\n",
    "scripts/tool.py": b"print(1)\n",
    "personal/themes/t/_inc/art/backdrops/fighter.png": b"png",  # personal art: allowed only under personal/
    "personal/art/Cap" "com.png": b"png",
    # empty scraper values (a template) are fine; .envrc-like names outside the glob are not flagged
    "cfg/template.xml": b'<string name="Scraper' + b'PasswordScreenScraper" value="" />\n',
    "docs/env.md": b"set .env yourself\n",
}


FIXTURE_SHIPPED = {"themes/t/_inc/art/backdrops/default.png": hashlib.sha256(b"png").hexdigest(),
                   "themes/t/_inc/art/bootmenu/esde.png": hashlib.sha256(b"png").hexdigest()}


def test_selftest_broken_repo_fires_every_rule():
    out = "\n".join(scan(_make_repo(BROKEN), FIXTURE_SHIPPED))
    for rel in BROKEN:
        assert rel + ":" in out, f"rule did not fire for planted file {rel}\n{out}"
    for rule in ("rom-ext", "bios-name", "binary", "personal-art", "secret-file", "scraper-cred"):
        assert f"[{rule}]" in out, f"rule {rule} never fired"


def test_personal_art_allowed_only_under_personal():
    d = _make_repo({"personal/themes/t/_inc/art/backdrops/cps2.png": b"png",
                    "themes/t/_inc/art/backdrops/cps2.png": b"png"})
    out = scan(d, FIXTURE_SHIPPED)
    assert len(out) == 1 and out[0].startswith("themes/t/_inc/art/backdrops/cps2.png: [personal-art]"), out


def test_listed_art_passes_unlisted_or_changed_art_fails():
    ok = {"themes/t/_inc/art/backdrops/default.png": b"png", "themes/t/_inc/art/bootmenu/esde.png": b"png"}
    assert scan(_make_repo(ok), FIXTURE_SHIPPED) == []
    out = scan(_make_repo(dict(ok, **{"themes/t/_inc/art/backdrops/new.png": b"png"})), FIXTURE_SHIPPED)
    assert len(out) == 1 and "not on the shipped-art list" in out[0], out
    out = scan(_make_repo({"themes/t/_inc/art/backdrops/default.png": b"other"}), FIXTURE_SHIPPED)
    assert len(out) == 1 and "differ" in out[0], out
    out = scan(_make_repo({"themes/t/_inc/art/backdrops/default.png": b"png"}), {})   # an empty list lists nothing
    assert len(out) == 1 and "not on the shipped-art list" in out[0], out


def test_selftest_clean_repo_passes():
    assert scan(_make_repo(CLEAN), FIXTURE_SHIPPED) == []


def test_selftest_untracked_files_ignored():
    d = _make_repo(CLEAN)
    with open(os.path.join(d, "game.zip"), "wb") as f:  # untracked
        f.write(b"x")
    assert scan(d, FIXTURE_SHIPPED) == []


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}\n{e}")
    sys.exit(1 if fails else 0)
