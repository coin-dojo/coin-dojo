"""hk_settings_state: the SETTINGS app's state file, parsed strictly (#197). The one parser for the roles that read it
(cabinet_tweaks, retroarch122, dreamcast; "the cabinet wins", #189/#171).

    - ansible.builtin.slurp: {src: "{{ x_settings_state }}"}
      register: x_settings_state_file          # read as the cabinet user, no_log, failed_when: false
    - ansible.builtin.set_fact:
        x_settings_app: "{{ x_settings_state_file | hk_settings_state }}"

Input: a slurp result. Output: {key: bool} with only the known keys whose value is a real JSON boolean, plus (#285) {"attract_minutes": 5|10|30|60, "attract_type": video|slideshow|dim|black} and (#206)
{"timezone": "<Area/City>"} when the file holds a string of a zone name's shape (no '.', so no '..' or path; at most 64
characters). Everything else means "not set" for that key, so the hk_* default applies: no file / unreadable (a
symlink to a file the cabinet user cannot read), not base64, over 64 KiB, not UTF-8, not JSON, not a JSON object, a duplicated key anywhere, a value
that is a string ("true"), a number (1) or null.

Same as the app (skills/settings/settings_core.py _read_json + isinstance(v, bool)) for every file, except a duplicated
key: the app's json.load takes the last value, this refuses the whole file (Security, #197: one reading, no guessing).
Pure stdlib, so the tests import it without Ansible.
"""
import base64
import binascii
import json
import re

KEYS = ("benchmark_mode", "cheevos_enable", "cheevos_hardcore", "cheevos_login", "attract_enabled")
ATTRACT_MINUTES = (5, 10, 30, 60)             # = skills/settings attract_apply.MINUTES (#285)
ATTRACT_TYPES = ("video", "slideshow", "dim", "black")   # = attract_apply.TYPES
TZ_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+\-]*(/[A-Za-z0-9][A-Za-z0-9_+\-]*){0,2}")     # = skills/settings settings_core.TZ_RE
MAX_BYTES = 64 * 1024        # the app writes a few lines; anything bigger is not its file


class _Duplicate(ValueError):
    pass


def _no_duplicates(pairs):
    seen = set()
    for k, _v in pairs:
        if k in seen:
            raise _Duplicate(k)
        seen.add(k)
    return dict(pairs)


def parse_state_bytes(raw):
    """bytes -> {key: bool}; {} for anything that is not a JSON object without duplicate keys."""
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        return {}
    try:
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except ValueError:                     # UnicodeDecodeError, JSONDecodeError and _Duplicate are all ValueErrors
        return {}
    except RecursionError:
        return {}
    if not isinstance(doc, dict):
        return {}
    out = {k: doc[k] for k in KEYS if isinstance(doc.get(k), bool)}
    tz = doc.get("timezone")
    if isinstance(tz, str) and len(tz) <= 64 and TZ_RE.fullmatch(tz):
        out["timezone"] = tz
    m = doc.get("attract_minutes")                  # (#285) an int from the menu's list, never a bool or a string
    if isinstance(m, int) and not isinstance(m, bool) and m in ATTRACT_MINUTES:
        out["attract_minutes"] = m
    t = doc.get("attract_type")
    if isinstance(t, str) and t in ATTRACT_TYPES:
        out["attract_type"] = t
    return out


def hk_settings_state(result):
    """A slurp result (registered with failed_when: false) -> {key: bool}; {} when the read failed."""
    if not isinstance(result, dict) or result.get("failed") or result.get("encoding", "base64") != "base64":
        return {}
    content = result.get("content")
    if not isinstance(content, str) or len(content) > MAX_BYTES * 4 // 3 + 8:
        return {}
    try:
        raw = base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError):
        return {}
    return parse_state_bytes(raw)


class FilterModule:
    def filters(self):
        return {"hk_settings_state": hk_settings_state}
