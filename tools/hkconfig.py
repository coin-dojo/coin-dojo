#!/usr/bin/env python3
"""hkconfig: how a stdlib-only script reads the cabinet configuration (ticket #131, C8 of #103).

The ONE mechanism: ask the loader, `tools/config.py get --soft NAME`, run with the config venv's python (PyYAML) when
~/.venvs/coin-dojo exists, else the caller's python. So scripts that run under plain `python3 -I` need no PyYAML
themselves. Used by tools/{tickets,release,changelog}.py, build/fetch.py, vm/vm_guard.py; shell scripts call
`tools/hkcfg NAME` (the same command).

    get(name)                  -> the value as text, "" when unset; raises ConfigUnavailable when the loader fails
    first(env_name, name)      -> $env_name when set (tests and one-off overrides win), else get(name)
    require(value, what, var)  -> value, or SystemExit with a clear "set <var> in config/local.yml" message (never a
                                  silent fallback to somebody's host)
Environment: HK_CONFIG (local file, as everywhere), CONFIG_PYTHON (python with PyYAML).
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConfigUnavailable(Exception):
    """The loader could not answer (no PyYAML, a broken config file, an unknown variable)."""


def python():
    if os.environ.get("CONFIG_PYTHON"):
        return os.environ["CONFIG_PYTHON"]
    venv = os.path.expanduser("~/.venvs/coin-dojo/bin/python")
    return venv if os.path.exists(venv) else sys.executable


def get(name, limit="coin-dojo"):
    cmd = [python(), os.path.join(ROOT, "tools", "config.py"), "get", "--soft", "--limit", limit, name]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConfigUnavailable("cannot run the config loader (%s)" % exc)
    if r.returncode != 0:
        why = (r.stderr.strip().splitlines() or ["exit %d" % r.returncode])[-1]
        raise ConfigUnavailable("config %s: %s" % (name, why))
    return r.stdout.rstrip("\n")


def first(env_name, name, limit="coin-dojo"):
    v = os.environ.get(env_name, "").strip() if env_name else ""
    return v if v else get(name, limit)


def require(value, what, var):
    if value:
        return value
    sys.stderr.write("%s: not set. Set %s in config/local.yml (see config/local.example.yml, docs/CONFIG.md).\n" % (what, var))
    raise SystemExit(1)
