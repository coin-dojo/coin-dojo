#!/usr/bin/env python3
"""Per-game Dreamcast stick mode (skills/controls/flycast/dcpad.py, dc_games.tsv, SDL_USB gamepad_<ID>.cfg).

Facts the files rely on (flycast v2.7 unless noted):
  * the panel reports only ABS_X/ABS_Y (cabinet /sys/class/input/event1/device/capabilities/abs = 3, range 0..255),
    so SDL's hat binds never fire and the stick reaches flycast as axes 0/1;
  * an axis direction maps to ONE DC key (mapping.cpp set_axis L395-403); a d-pad key on an axis is pressed at
    |value| >= 16384 (gamepad_device.cpp L476-494), so the stick gives 8-way d-pad;
  * per-game file name = "SDL_<name>_<game ID>.cfg", tried before "SDL_<name>.cfg" (gamepad_device.cpp L587-650);
    game ID = IP.BIN product number, trailing blanks trimmed (emulator.cpp L851-858);
  * IP.BIN peripheral bits 21/22 = analog stick required (mc.pp.se/dc/ip0000.bin.html).
Run: python3 skills/controls/tests/test_dc_stick.py"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FLY = os.path.abspath(os.path.join(HERE, "..", "flycast"))
sys.path.insert(0, FLY)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "lib"))
from hktest import main  # noqa: E402  (shared runner: one PASS per test, counted RESULT line, #137)
import dcpad  # noqa: E402
import test_flycast_mapping as fm  # noqa: E402

TSV = os.path.join(FLY, "dc_games.tsv")
BASE = os.path.join(FLY, "SDL_USB gamepad.cfg")
DEFAULTS = os.path.join(HERE, "..", "..", "..", "ansible", "roles", "dreamcast", "defaults", "main.yml")
STICK = {(0, False), (0, True), (1, False), (1, True)}
DPAD = {(0, False): "btn_dpad1_left", (0, True): "btn_dpad1_right", (1, False): "btn_dpad1_up",
        (1, True): "btn_dpad1_down"}


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _ip(pid=b"T38706M   ", periph=b"01BBA10 ", magic=b"SEGA SEGAKATANA "):
    s = bytearray(2048)
    s[0:16] = magic
    s[0x38:0x40] = periph
    s[0x40:0x4A] = pid
    s[0x80:0x87] = b"IKARUGA"
    return bytes(s)


def per_game_problems(text):
    """A per-game file must be the base mapping with only the 4 stick axis directions moved onto the d-pad."""
    p = []
    bini, bdig, bana = fm.load_mapping(_read(BASE))
    ini, dig, ana = fm.load_mapping(text)
    if ini.get("emulator") != bini.get("emulator"):
        p.append("[emulator] differs from the base")
    if "combo" in ini:
        p.append("[combo] present")
    if dig != bdig:
        p.append("digital binds differ from the base (LP/MP swap, menu, start must stay)")
    for k in set(ana) | set(bana):
        want = DPAD[k] if k in STICK else bana.get(k)
        if ana.get(k) != want:
            p.append("analog %s: %s, want %s" % (k, ana.get(k), want))
    return p


ROWS = dcpad.read_table(TSV)


# --- tests ------------------------------------------------------------------------------------------------------
def test_ipbin_parse_and_flags():
    ip = dcpad.parse_ip(_ip())
    assert ip == {"id": "T38706M", "peripherals": "01BBA10", "title": "IKARUGA"}, ip
    assert not dcpad.needs_analog("01BBA10") and "start_a_b_dpad" in dcpad.flags("01BBA10")
    assert dcpad.needs_analog("063BA10") and {"analog_h", "analog_v"} <= dcpad.flags("063BA10")   # Psyvariar 2
    assert dcpad.parse_ip(_ip(pid=b"T1214N\0xyz"))["id"] == "T1214N"      # flycast cuts at the first NUL too


def test_broken_ipbin_refused():
    for bad in (_ip(magic=b"SEGA SEGASATURN "), _ip(periph=b"01BBZ10 ")):
        try:
            dcpad.parse_ip(bad)
        except ValueError:
            continue
        raise AssertionError("BROKEN IP.BIN accepted")


def test_filename_matches_flycast():
    # make_mapping_filename(instance=false, DC, perGame=true): api "SDL" + "_" + name + "_" + gameId + ".cfg"
    assert dcpad.mapping_filename("T38706M") == "SDL_USB gamepad_T38706M.cfg"
    for bad in ("../x", "T 1", "", "T38706M/..", "a" * 11):
        try:
            dcpad.mapping_filename(bad)
        except ValueError:
            continue
        raise AssertionError("BROKEN: unsafe id accepted %r" % bad)


def test_table_rule_and_ikaruga():
    by = {r["id"]: r for r in ROWS}
    assert by["T38706M"]["rom"] == "Ikaruga" and by["T38706M"]["stick"] == "dpad"
    for r in ROWS:
        assert (r["stick"] == "dpad") == (not dcpad.needs_analog(r["peripherals"])), r


def test_committed_files_are_rendered_and_complete():
    base = _read(BASE)
    want = {dcpad.mapping_filename(r["id"]) for r in ROWS if r["stick"] == "dpad"}
    have = {f for f in os.listdir(FLY) if re.fullmatch(r"SDL_USB gamepad_T[0-9A-Z-]+\.cfg", f)}
    assert have == want, (sorted(have - want), sorted(want - have))
    for r in ROWS:
        if r["stick"] != "dpad":
            continue
        text = _read(os.path.join(FLY, dcpad.mapping_filename(r["id"])))
        assert text == dcpad.render(base, r["id"]), "stale %s: re-run dcpad.py render" % r["id"]
        assert per_game_problems(text) == [], (r["id"], per_game_problems(text))


def test_role_lists_every_mapping_file():
    listed = re.findall(r"^  - (SDL_USB gamepad[^ #]*\.cfg)", _read(DEFAULTS), re.M)
    files = sorted(f for f in os.listdir(FLY) if f.startswith("SDL_USB gamepad") and f.endswith(".cfg"))
    assert sorted(listed) == files, (listed, files)


# broken cases
def test_broken_dpad_for_analog_game_refused():
    import tempfile
    for row in ("T9907M\tPsyvariar 2\t063BA10\tdpad\tx\n", "T1\tx\t01BBA10\tdpad\n", "T1\tx\t01BBA10\tboth\tx\n",
                "../T1\tx\t01BBA10\tdpad\tx\n", "T1\ta\t01BBA10\tdpad\tx\nT1\tb\t01BBA10\tdpad\tx\n"):
        with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False) as f:
            f.write(row)
        try:
            dcpad.read_table(f.name)
        except ValueError:
            continue
        finally:
            os.unlink(f.name)
        raise AssertionError("BROKEN table row accepted: %r" % row)


def test_broken_per_game_files_fail():
    good = dcpad.render(_read(BASE), "T38706M")
    back = good.replace("bind0 = 0-:btn_dpad1_left", "bind0 = 0-:btn_analog_left")
    assert per_game_problems(back) != [], "BROKEN: stick left back on analog accepted"
    unswapped = good.replace("bind0 = 0:btn_y", "bind0 = 0:btn_x").replace("bind4 = 4:btn_x", "bind4 = 4:btn_y")
    assert per_game_problems(unswapped) != [], "BROKEN: LP/MP swap lost accepted"
    nomenu = good.replace("bind6 = 8:btn_menu", "bind6 = 8:btn_start")
    assert per_game_problems(nomenu) != [], "BROKEN: menu lost accepted"
    assert per_game_problems(good + "\n[combo]\nbind0 = 8,9:btn_escape:0\n") != []


def test_broken_base_without_stick_axes_refused():
    base = _read(BASE).replace("bind3 = 1+:btn_analog_down\n", "")
    for b in (base, _read(BASE).replace("0-:btn_analog_left", "0-:axis2_left")):
        try:
            dcpad.render(b, "T38706M")
        except ValueError:
            continue
        raise AssertionError("BROKEN base rendered")


if __name__ == "__main__":
    main(globals(), per_test=True)
