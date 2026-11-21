#!/usr/bin/env python3
"""A fake `nmcli` for tests and the test VM (no Wi-Fi hardware there). TEST ONLY: never installed on the cabinet.

Answers exactly the nmcli calls settings_core.NM makes, from a JSON state file:
  $HK_FAKE_NMCLI_STATE (default /tmp/coin-dojo-fake-nmcli.json; created with three sample networks if missing)
  {"device": "wlan0", "ip": "192.0.2.77/24", "active": null,
   "networks": [{"ssid": "...", "signal": 80, "security": "WPA2", "password": "..."}],
   "saved": {"<ssid>": {"uuid": "...", "password": "..."}}}
With $HK_FAKE_NMCLI_ARGV set, every argv is appended there as one JSON list per line, so a test can check that the
password reached nmcli as ONE element, unchanged (tests only; the VM does not set it).
Errors are worded like NetworkManager 1.52's. Exit 0 ok, 4 activation failed, 10 not found, 2 usage.
"""
import json
import os
import sys
import uuid as uuidlib

STATE = os.environ.get("HK_FAKE_NMCLI_STATE", "/tmp/coin-dojo-fake-nmcli.json")
SAMPLE = {
    "device": "wlan0", "ip": "192.0.2.77/24", "active": "Cabinet Test",
    "networks": [
        {"ssid": "Cabinet Test", "signal": 82, "security": "WPA2", "password": "coin-dojo"},
        {"ssid": "Dojo: 5GHz", "signal": 64, "security": "WPA2 WPA3", "password": "p@ss \"word\" $HOME 'x'"},
        {"ssid": "Arcade Guest", "signal": 41, "security": "", "password": ""},
        {"ssid": "Neighbour", "signal": 23, "security": "WPA2", "password": "secret-neighbour"},
    ],
    "saved": {"Cabinet Test": {"uuid": "8a3f6c1e-0000-4000-8000-000000000001", "password": "coin-dojo"}},
}


def esc(s):
    return s.replace("\\", "\\\\").replace(":", "\\:")


def load():
    if not os.path.exists(STATE):
        save(SAMPLE)
    with open(STATE, encoding="utf-8") as f:
        return json.load(f)


def save(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATE)


def fail(msg, rc):
    print(msg, file=sys.stderr)
    return rc


def main(argv):
    if os.environ.get("HK_FAKE_NMCLI_ARGV"):
        with open(os.environ["HK_FAKE_NMCLI_ARGV"], "a", encoding="utf-8") as f:
            f.write(json.dumps(["nmcli"] + argv) + "\n")
    st = load()
    a = list(argv)
    wait = None
    if a[:1] == ["--wait"]:
        wait, a = a[1], a[2:]
    fields = None
    terse = False
    if a[:1] in (["-t"], ["-g"]):
        terse = a[0] == "-t"
        if a[0] == "-g":
            fields = a[1]
            a = a[2:]
        elif a[1:2] == ["-f"]:
            fields = a[2]
            a = a[3:]
    dev = st["device"]
    if a[:2] == ["device", "status"]:
        state = "connected" if st["active"] else "disconnected"
        print("%s:wifi:%s:%s" % (dev, state, esc(st["active"] or "")))
        print("lo:loopback:connected (externally):lo")
        return 0
    if a[:2] == ["device", "show"] and fields == "IP4.ADDRESS":
        print(st["ip"] if st["active"] else "")
        return 0
    if a[:3] == ["device", "wifi", "list"]:
        for n in sorted(st["networks"], key=lambda n: -n["signal"]):
            print("%s:%s:%d:%s" % ("*" if n["ssid"] == st["active"] else " ", esc(n["ssid"]), n["signal"], esc(n["security"])))
        return 0
    if a[:2] == ["connection", "show"] and len(a) == 2:
        for ssid, c in st["saved"].items():
            print("%s:%s:802-11-wireless" % (esc(ssid), c["uuid"]))
        return 0
    if a[:3] == ["connection", "show", "uuid"] and fields == "802-11-wireless.ssid":
        for ssid, c in st["saved"].items():
            if c["uuid"] == a[3]:
                print(esc(ssid))
                return 0
        return fail("Error: %s - no such connection profile." % a[3], 10)
    if a[:3] == ["device", "wifi", "connect"] and len(a) >= 4:
        ssid, rest = a[3], a[4:]
        opts = dict(zip(rest[0::2], rest[1::2]))
        if len(rest) % 2 or set(opts) - {"password", "ifname"}:
            return fail("Error: invalid extra argument '%s'." % rest[-1], 2)
        net = next((n for n in st["networks"] if n["ssid"] == ssid), None)
        if net is None:
            return fail("Error: No network with SSID '%s' found." % ssid, 10)
        pw = opts.get("password")
        saved = st["saved"].get(ssid)
        if net["security"]:
            given = pw if pw is not None else (saved or {}).get("password")
            if given is None:
                return fail("Error: Connection activation failed: (7) Secrets were required, but not provided.", 4)
            if given != net["password"]:
                return fail("Error: Connection activation failed: (7) Secrets were required, but not provided.", 4)
        st["active"] = ssid
        st["saved"][ssid] = {"uuid": (saved or {}).get("uuid") or str(uuidlib.uuid4()), "password": pw or ""}
        save(st)
        print("Device '%s' successfully activated with '%s'." % (dev, st["saved"][ssid]["uuid"]))
        return 0
    if a[:3] == ["connection", "up", "uuid"]:
        for ssid, c in st["saved"].items():
            if c["uuid"] == a[3]:
                net = next((n for n in st["networks"] if n["ssid"] == ssid), None)
                if net is None:
                    return fail("Error: Connection activation failed: No suitable device found for this connection.", 4)
                if net["security"] and c["password"] != net["password"]:
                    return fail("Error: Connection activation failed: (7) Secrets were required, but not provided.", 4)
                st["active"] = ssid
                save(st)
                print("Connection successfully activated (D-Bus active path: /org/freedesktop/NetworkManager/ActiveConnection/7)")
                return 0
        return fail("Error: unknown connection '%s'." % a[3], 10)
    if a[:3] == ["connection", "delete", "uuid"]:
        for ssid, c in list(st["saved"].items()):
            if c["uuid"] == a[3]:
                del st["saved"][ssid]
                if st["active"] == ssid:
                    st["active"] = None
                save(st)
                print("Connection '%s' (%s) successfully deleted." % (ssid, c["uuid"]))
                return 0
        return fail("Error: unknown connection '%s'." % a[3], 10)
    _ = (wait, terse)
    return fail("fake nmcli: unsupported call: %s" % " ".join(argv), 2)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
