#!/usr/bin/env python3
"""The image's starter carousel (#244): the tool systems SETTINGS and QUIT TO MAIN MENU, from nested.py itself.

    starter.py --tools DIR --nested-root DIR --settings-command CMD --quit-command CMD

A new image has no ROMs, picks or dat, so the nested main screen cannot be built yet. This prints the files the full
build would write for its tool systems, as JSON {"es_systems": ..., "gamelists": {slug: xml}, "links": {slug: name}},
with ONE difference: no <loadExclusive/>, so ES-DE also shows every bundled system a user drops ROMs into. The first
`make deploy-esde` replaces it with the nested main screen (which adds <loadExclusive/> again).
"""
import argparse
import json
import sys


def starter(nested, nested_root, settings_command, quit_command):
    xml = nested.systems_xml({}, nested_root, "unused", settings_command=settings_command, quit_command=quit_command)
    if "<loadExclusive/>\n" not in xml:
        raise ValueError("nested.systems_xml no longer writes <loadExclusive/> as expected")
    return {
        "es_systems": xml.replace("<loadExclusive/>\n", "", 1),
        "gamelists": {nested.SETTINGS_SLUG: nested.settings_gamelist_xml({}),
                      nested.QUIT_SLUG: nested.quit_gamelist_xml({})},
        "links": {nested.SETTINGS_SLUG: nested.SETTINGS_LINK, nested.QUIT_SLUG: nested.QUIT_LINK},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tools", required=True, help="the folder holding nested.py (skills/collections)")
    ap.add_argument("--nested-root", required=True)
    ap.add_argument("--settings-command", required=True)
    ap.add_argument("--quit-command", required=True)
    a = ap.parse_args(argv)
    sys.path.insert(0, a.tools)
    import nested  # noqa: E402  (the one source of the tool-system blocks and their gamelists)
    print(json.dumps(starter(nested, a.nested_root, a.settings_command, a.quit_command), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
