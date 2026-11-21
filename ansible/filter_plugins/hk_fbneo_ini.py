"""hk_fbneo_ini: set keys in an FBNeo ini (fcadefbneo.ini) text, for the fightcade role (#208).

    content: "{{ default_ini_text | hk_fbneo_ini(lookup('template', 'fcadefbneo-coin-dojo.ini.j2')) }}"

FBNeo's ini is `key value` lines (CRLF in Fightcade's copy) with `//` comments; keys may carry an index
(`szAppRomPaths[6]`). The overrides text has the same format. Every override key replaces the value of its line in
place (first match; the line ending is kept); a key the default does not have is appended at the end. Comment and
blank lines of the overrides are ignored. A value is copied byte for byte (Windows paths keep their backslashes).
Raises ValueError on an override line that is not `key value`, so a broken template fails the play.
Pure stdlib, so the tests import it without Ansible.
"""
import re

_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*(?:\[[0-9]+\])?)(?:[ \t]+(.*))?$")


def parse_overrides(text):
    """'key value' lines -> [(key, value)] in order; '//' comments and blank lines skipped."""
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        m = _KEY.match(line)
        if not m:
            raise ValueError("hk_fbneo_ini: override line %d is not 'key value': %r" % (n, raw))
        out.append((m.group(1), (m.group(2) or "").strip()))
    return out


def hk_fbneo_ini(default_text, overrides_text):
    overrides = parse_overrides(overrides_text)
    eol = "\r\n" if "\r\n" in default_text else "\n"
    lines = default_text.splitlines(True)
    done = set()
    for i, line in enumerate(lines):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        key = body.split(None, 1)[0] if body.strip() else ""
        for k, v in overrides:
            if k == key and k not in done:
                lines[i] = ("%s %s" % (k, v)).rstrip() + ending
                done.add(k)
                break
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += eol
    for k, v in overrides:
        if k not in done:
            lines.append(("%s %s" % (k, v)).rstrip() + eol)
            done.add(k)
    return "".join(lines)


class FilterModule(object):
    def filters(self):
        return {"hk_fbneo_ini": hk_fbneo_ini}
