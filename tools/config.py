#!/usr/bin/env python3
"""coin-dojo configuration loader (ticket #118, C1 of #103). One loader for make, Ansible and scripts.

Terraform-style: variables are DECLARED in config/variables.d/<area>.yml (name, type, default, range, description,
consumers); a target overlay (config/targets/<target>.yml), a local file and the command line override them.

    precedence (low to high):  declared default  <  config/targets/<target>.yml  <  local  <  command line
    local = $HK_CONFIG, else config/local.yml, else personal/config/local.yml (EDITION=personal)
    command line = `make ... hk_x=1`, `--set hk_x=1`, or the env HK_SET="hk_x=1 hk_y=2"

    config.py check  [--limit L]                      validate; exit 1 on any problem (prints `config-check: OK ...`, or RESULT: FAIL config-check)
    config.py show   [--limit L] [name ...]           effective value and its source
    config.py get    [--limit L] [--soft] name        one raw value (for make / sh). --soft (scripts, #131): other variables' problems
                                                      (e.g. no cabinet host on a forge-only machine) do not stop it, an unset one prints nothing
    config.py guard-cmd [--limit L]                   the remote pgrep line of cabinet-guard (from hk_game_*)
    config.py render [--limit L] --out DIR            effective-L.json + inventory-L.yml in DIR (validates first)
    config.py docs   [--out FILE | --check]           docs/CONFIG.md from the declarations (deterministic)

--limit is the Ansible host: coin-dojo (the cabinet, target `cabinet`) or coin-dojo-vm (target `vm`). A variable with
`targets: [cabinet]` does not exist for the vm: the VM render never carries the cabinet's address.
Secrets never go in config: only WHERE they are (a Keychain item name). Needs PyYAML (`make config-venv`).
"""
import argparse
import difflib
import json
import os
import re
import shlex
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TYPES = ("str", "int", "bool", "float", "list", "enum", "path", "map")
LIMITS = {"coin-dojo": "cabinet", "coin-dojo-vm": "vm"}
TARGETS = ("cabinet", "vm")
DECL_KEYS = {"type", "default", "required", "allowed", "min", "max", "pattern", "items", "description",
             "consumers", "targets", "exports", "example", "fields", "aliases", "max_items", "max_distinct"}
REF = re.compile(r"\{\{\s*(hk_[a-z0-9_]+)\s*\}\}")
NAME = re.compile(r"hk_[a-z0-9_]+$")


class ConfigError(Exception):
    """Carries a list of problems (each one line)."""

    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = list(problems)


def need_yaml():
    try:
        import yaml  # noqa: F401
        return yaml
    except ImportError:
        sys.stderr.write("config: PyYAML is missing. Run `make config-venv` (creates ~/.venvs/coin-dojo), "
                         "then use that python (make does it for you).\n")
        raise SystemExit(2)


def load_yaml(path):
    yaml = need_yaml()
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(["%s: cannot read (%s)" % (rel(path), str(exc).splitlines()[0] if str(exc) else exc)])
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(["%s: the top level must be a mapping of name: value" % rel(path)])
    return data


def rel(path):
    try:
        r = os.path.relpath(path, ROOT)
    except ValueError:
        return path
    return path if r.startswith("..") else r


# --- declarations ---------------------------------------------------------------------------------------------


def load_declarations(root=ROOT):
    """{name: decl} from config/variables.d/*.yml. Raises ConfigError on a malformed or colliding declaration."""
    d = os.path.join(root, "config", "variables.d")
    decls, problems = {}, []
    files = sorted(f for f in os.listdir(d) if f.endswith(".yml")) if os.path.isdir(d) else []
    if not files:
        raise ConfigError(["%s: no variable files (config/variables.d/<area>.yml)" % rel(d)])
    for fn in files:
        path = os.path.join(d, fn)
        data = load_yaml(path)
        area = fn[:-4]
        extra = set(data) - {"area", "description", "variables"}
        if extra:
            problems.append("%s: unknown top-level key(s) %s (allowed: area, description, variables)" % (rel(path), sorted(extra)))
        if data.get("area") != area:
            problems.append("%s: `area:` must be %r (the file name)" % (rel(path), area))
        for name, decl in (data.get("variables") or {}).items():
            where = "%s: %s" % (rel(path), name)
            if name in decls:
                problems.append("%s: declared twice (also in %s)" % (where, decls[name]["_file"]))
                continue
            p = check_decl(name, decl, where)
            problems += p
            if not p:
                decl = dict(decl)
                decl["_file"] = rel(path)
                decl["_area"] = area
                decl.setdefault("targets", list(TARGETS))
                decls[name] = decl
    seen = {}
    for name, d in decls.items():               # #240: an old name a local file may still use (renamed variable)
        for old in d.get("aliases") or []:
            if old in decls:
                problems.append("%s: alias %s is itself a declared variable" % (d["_file"], old))
            elif old in seen:
                problems.append("%s: alias %s is already an alias of %s" % (d["_file"], old, seen[old]))
            seen[old] = name
    owner = {}
    for name, d in decls.items():
        ex = d.get("exports") or []
        for legacy in (ex if isinstance(ex, (list, dict)) else []):
            if legacy in owner:
                problems.append("%s: exports %s, which %s already exports" % (d["_file"], legacy, owner[legacy]))
            owner[legacy] = name
    if problems:
        raise ConfigError(problems)
    # a default must itself be valid (after interpolation, checked in resolve())
    return decls


def check_decl(name, decl, where):
    p = []
    if not NAME.match(str(name)):
        p.append("%s: name must match hk_[a-z0-9_]+" % where)
    if not isinstance(decl, dict):
        return p + ["%s: declaration must be a mapping" % where]
    for k in sorted(set(decl) - DECL_KEYS):
        hint = difflib.get_close_matches(k, DECL_KEYS, 1)
        p.append("%s: unknown declaration key %r%s" % (where, k, " (did you mean %r?)" % hint[0] if hint else ""))
    if decl.get("type") not in TYPES:
        p.append("%s: type must be one of %s" % (where, "/".join(TYPES)))
    if not str(decl.get("description", "")).strip():
        p.append("%s: description is required" % where)
    cons = decl.get("consumers")
    if not (isinstance(cons, list) and cons and all(isinstance(c, str) for c in cons)):
        p.append("%s: consumers must be a non-empty list of roles/scripts that read it" % where)
    if "default" not in decl and not decl.get("required"):
        p.append("%s: needs a default, or required: true" % where)
    if "default" in decl and decl.get("required"):
        p.append("%s: required: true and a default contradict each other" % where)
    if (decl.get("type") == "enum" or (decl.get("type") == "list" and decl.get("items") == "enum")) \
            and not (isinstance(decl.get("allowed"), list) and decl["allowed"]):
        p.append("%s: an enum (or a list of enum items) needs allowed: [...]" % where)
    t = decl.get("targets", list(TARGETS))
    if not (isinstance(t, list) and t and set(t) <= set(TARGETS)):
        p.append("%s: targets must be a non-empty subset of %s" % (where, list(TARGETS)))
    al = decl.get("aliases", [])
    if not (isinstance(al, list) and all(isinstance(a, str) and NAME.match(a) for a in al)):
        p.append("%s: aliases must be a list of old hk_ names" % where)
    ex = decl.get("exports", [])
    if not isinstance(ex, (list, dict)):
        p.append("%s: exports must be a list of Ansible var names or a {name: template} map" % where)
    return p


# --- value checks ---------------------------------------------------------------------------------------------


def _template_in(value):
    """The first '{{' / '{%' / '{#' found in a (nested) value, else None."""
    if isinstance(value, str):
        for mark in ("{{", "{%", "{#"):
            if mark in value:
                return mark
    elif isinstance(value, list):
        for v in value:
            m = _template_in(v)
            if m:
                return m
    elif isinstance(value, dict):
        for k, v in value.items():
            m = _template_in(k) or _template_in(v)
            if m:
                return m
    return None


def value_problem(decl, value):
    """Return a message when value does not fit decl, else None. (References are resolved before this.)"""
    tpl = _template_in(value)
    if tpl:
        # Values reach Ansible as extra vars, where a leftover {{ }} or {% %} would run (lookup('pipe', ...)):
        # config values are plain data; references between variables are resolved before this check (#148)
        return "contains a template (%s): config values are plain data, not Jinja" % tpl
    t = decl["type"]
    if t in ("str", "path"):
        if not isinstance(value, str):
            return "must be a string, got %s %r" % (type(value).__name__, value)
        if t == "path" and not value.strip():
            return "must be a non-empty path"
        if "pattern" in decl and not re.fullmatch(decl["pattern"], value):
            return "%r does not match the pattern %s" % (value, decl["pattern"])
    elif t == "enum":
        if value not in decl["allowed"]:
            near = difflib.get_close_matches(str(value), [str(a) for a in decl["allowed"]], 1)
            return "%r is not one of %s%s" % (value, decl["allowed"], " (did you mean %r?)" % near[0] if near else "")
    elif t == "bool":
        if not isinstance(value, bool):
            return "must be true or false, got %r" % (value,)
    elif t in ("int", "float"):
        ok = isinstance(value, int) and not isinstance(value, bool) if t == "int" else \
            isinstance(value, (int, float)) and not isinstance(value, bool)
        if not ok:
            return "must be %s, got %s %r" % ("a whole number" if t == "int" else "a number", type(value).__name__, value)
        if "min" in decl and value < decl["min"]:
            return "%s is below the minimum %s" % (value, decl["min"])
        if "max" in decl and value > decl["max"]:
            return "%s is above the maximum %s" % (value, decl["max"])
    elif t == "list":
        if not isinstance(value, list):
            return "must be a list, got %s %r" % (type(value).__name__, value)
        item = decl.get("items")
        for fld, cap in (decl.get("max_distinct") or {}).items():   # at most `cap` different values of an item's field
            seen = {v.get(fld) for v in value if isinstance(v, dict)}
            if len(seen) > cap:
                return "%d different %s values, at most %d allowed" % (len(seen), fld, cap)
        if decl.get("max_items") and len(value) > decl["max_items"]:
            return "%d items, at most %d allowed" % (len(value), decl["max_items"])
        if item:
            sub = {"type": item}
            if item == "map":
                sub["fields"] = decl.get("fields") or {}
            if item == "str" and "pattern" in decl:
                sub["pattern"] = decl["pattern"]                  # a list of strings: the pattern applies to each item
            if item == "enum":
                sub["allowed"] = decl.get("allowed") or []   # a list of enum items: allowed names the items
            for i, v in enumerate(value):
                m = value_problem(sub, v)
                if m:
                    return "item %d: %s" % (i, m)
    elif t == "map":
        if not isinstance(value, dict):
            return "must be a mapping, got %s %r" % (type(value).__name__, value)
        fields = decl.get("fields") or {}
        for k in sorted(set(value) - set(fields)) if fields else []:
            near = difflib.get_close_matches(k, list(fields), 1)
            return "unknown key %r%s (keys: %s)" % (k, " (did you mean %r?)" % near[0] if near else "", ", ".join(fields))
        for k, sub in fields.items():
            if sub.get("required") and k not in value:
                return "%s: required" % k
            if k in value:
                m = value_problem(sub, value[k])
                if m:
                    return "%s: %s" % (k, m)
    return None


# --- layers and resolution ------------------------------------------------------------------------------------


def local_path(env=None, root=ROOT, edition=None):
    env = os.environ if env is None else env
    if env.get("HK_CONFIG"):
        return env["HK_CONFIG"]
    p = os.path.join(root, "config", "local.yml")
    if os.path.isfile(p):
        return p
    if (edition or env.get("EDITION", "personal")) == "personal":
        p = os.path.join(root, "personal", "config", "local.yml")
        if os.path.isfile(p):
            return p
    return None


def parse_set(items):
    """['hk_a=1', 'hk_b=[x,y]'] -> {name: parsed} (YAML scalars, so 600000 is an int and true a bool)."""
    yaml = need_yaml()
    out, problems = {}, []
    for it in items:
        if "=" not in it:
            problems.append("--set %r: expected name=value" % it)
            continue
        k, v = it.split("=", 1)
        try:
            out[k.strip()] = yaml.safe_load(v) if v.strip() != "" else ""
        except yaml.YAMLError:
            out[k.strip()] = v
    if problems:
        raise ConfigError(problems)
    return out


def alias_of(decls):
    """{old name: declared name} from the declarations' `aliases:` (#240)."""
    return {old: name for name, d in decls.items() for old in d.get("aliases") or []}


def hint(name, decls):
    near = difflib.get_close_matches(name, list(decls), 1, 0.6)
    return " (did you mean %s?)" % near[0] if near else ""


def target_of(limit):
    if limit not in LIMITS:
        raise ConfigError(["--limit %r: LIMIT must be one of %s" % (limit, ", ".join(sorted(LIMITS)))])
    return LIMITS[limit]


class Config:
    """The merged, validated configuration of one target."""

    def __init__(self, limit="coin-dojo", root=ROOT, local=None, use_local=True, cli=None, env=None):
        self.root, self.limit, self.target = root, limit, target_of(limit)
        self.decls = load_declarations(root)
        self.problems = []
        self.layers = []   # (source label, {name: value}) in increasing priority
        tpath = os.path.join(root, "config", "targets", self.target + ".yml")
        if os.path.isfile(tpath):
            self.layers.append(("target:%s" % self.target, load_yaml(tpath), rel(tpath)))
        lp = local if local is not None else (local_path(env, root) if use_local else None)
        self.local_file = lp
        if lp:
            if not os.path.isfile(lp):
                raise ConfigError(["local config %s does not exist (HK_CONFIG)" % lp])
            self.layers.append(("local", load_yaml(lp), rel(lp)))
        if cli:
            self.layers.append(("cli", dict(cli), "command line"))
        self._resolve()

    def applies(self, name):
        return self.target in self.decls[name]["targets"]

    def _resolve(self):
        values, source = {}, {}
        for name, d in self.decls.items():
            if "default" in d:
                values[name], source[name] = d["default"], "default"
        aliases = alias_of(self.decls)
        for label, data, where in self.layers:
            # an old name (alias) first, so the new name wins when a file sets both
            for name, v in sorted(data.items(), key=lambda kv: kv[0] not in aliases):
                name = aliases.get(name, name)
                if name not in self.decls:
                    self.problems.append("%s: unknown variable %s%s" % (where, name, hint(name, self.decls)))
                    continue
                if not self.applies(name):
                    # A local file sets cabinet variables for the cabinet; for the vm they are dropped, not an error.
                    # On the command line it is a mistake worth saying.
                    if label == "cli":
                        self.problems.append("cli: %s is not used by the %s target (only: %s)" % (
                            name, self.target, ", ".join(self.decls[name]["targets"])))
                    continue
                if self.decls[name]["type"] == "map" and self.decls[name].get("fields") and isinstance(v, dict) \
                        and isinstance(values.get(name), dict):
                    v = {**values[name], **v}   # a map with declared fields: set one key, keep the others
                values[name], source[name] = v, "%s (%s)" % (label, where) if label != "cli" else "cli"
        # keep only variables that exist for this target
        names = [n for n in self.decls if self.applies(n)]
        self.values, self.source = {}, {}
        for name in names:
            d = self.decls[name]
            if name not in values:
                if d.get("required"):
                    self.problems.append("missing required %s: %s Set it in config/local.yml (copy config/local.example.yml), "
                                         "or pass %s=VALUE" % (name, d["description"].split(".")[0] + ".", name))
                continue
            self.source[name] = source[name]
        for name in names:
            if name in self.source:
                self.values[name] = self._interp(name, values, set())
        for name, v in sorted(self.values.items()):
            m = value_problem(self.decls[name], v)
            if m:
                self.problems.append("%s = %s: %s [from %s]" % (name, json.dumps(v), m, self.source[name]))

    def _interp(self, name, raw, stack):
        """Resolve {{ hk_other }} references inside string values (and list items), with cycle detection."""
        def sub(v):
            if isinstance(v, str):
                def rep(m):
                    ref = m.group(1)
                    if ref in stack or name == ref:
                        self.problems.append("%s: reference cycle through %s" % (name, ref))
                        return m.group(0)
                    if ref not in self.decls or ref not in raw or not self.applies(ref):
                        self.problems.append("%s: refers to %s, which is unknown or unset for the %s target" % (name, ref, self.target))
                        return m.group(0)
                    return str(self._interp(ref, raw, stack | {name}))
                return REF.sub(rep, v)
            if isinstance(v, list):
                return [sub(i) for i in v]
            return v
        out = sub(raw[name])
        if isinstance(out, str) and self.decls[name]["type"] == "path" and out.startswith("~/"):
            out = os.path.expanduser(out)
        return out

    def raise_for_problems(self):
        if self.problems:
            raise ConfigError(self.problems)

    # --- exports: legacy Ansible variable names -------------------------------------------------------------

    def exports(self):
        """{ansible var: value} for every `exports:` of a variable that is set for this target."""
        out = {}
        for name, v in self.values.items():
            ex = self.decls[name].get("exports") or []
            items = ex.items() if isinstance(ex, dict) else [(e, None) for e in ex]
            for legacy, tmpl in items:
                if tmpl is None:
                    out[legacy] = v
                else:
                    m = REF.sub(lambda mm: str(self.values.get(mm.group(1), mm.group(0))), str(tmpl))
                    out[legacy] = m
        return out

    def effective(self):
        eff = dict(self.exports())
        eff.update(self.values)
        return dict(sorted(eff.items()))

    # --- generated inventory ----------------------------------------------------------------------------------

    def inventory(self):
        """Ansible inventory (YAML text) with the connection vars of the cabinet / the VM, from config."""
        def q(v):
            return json.dumps(v)
        lines = ["# Generated by tools/config.py from the effective config. Do not edit; do not commit.", "all:", "  children:"]
        v = self.values
        if self.target == "cabinet":
            lines += ["    pios:", "      hosts:", "        coin-dojo:",
                      "          ansible_host: %s" % q(v["hk_cabinet_host"]),
                      "          ansible_user: %s" % q(v["hk_ssh_user"]),
                      "          ansible_port: %s" % q(v["hk_ssh_port"])]
            if v.get("hk_ssh_common_args"):
                lines.append("          ansible_ssh_common_args: %s" % q(v["hk_ssh_common_args"]))
        else:
            lines += ["    vm:", "      hosts:", "        coin-dojo-vm:",
                      "          ansible_host: %s" % q(v["hk_vm_host"]),
                      "          ansible_port: %s" % q(v["hk_vm_port"]),
                      "          ansible_user: %s" % q(v["hk_ssh_user"])]
            if v.get("hk_ssh_common_args"):
                lines.append("          ansible_ssh_common_args: %s" % q(v["hk_ssh_common_args"]))
            # vm-guard compares the VM with the cabinet's address: list the cabinet host too when it is configured.
            cab = self.cabinet_host_soft()
            if cab:
                lines += ["    pios:", "      hosts:", "        coin-dojo:", "          ansible_host: %s" % q(cab)]
        return "\n".join(lines) + "\n"

    def cabinet_host_soft(self):
        for _label, data, _w in reversed(self.layers):
            if isinstance(data.get("hk_cabinet_host"), str):
                return data["hk_cabinet_host"]
        return None


# --- docs -----------------------------------------------------------------------------------------------------


def md_cell(v):
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return "`%s`" % s.replace("|", "\\|") if s != "" else "(empty)"


def render_docs(root=ROOT):
    decls = load_declarations(root)
    areas = {}
    for name, d in decls.items():
        areas.setdefault(d["_area"], []).append(name)
    out = ["# Configuration reference", "",
           "<!-- Generated by `make config-docs` from config/variables.d/*.yml. Do not edit by hand. -->", "",
           "Every lever of the cabinet is a variable declared in `config/variables.d/<area>.yml` (name, type, default, range, "
           "description, consumers), like Terraform's `variables.tf`. The defaults are the neutral, community values; "
           "you override what you need and nothing else.", "",
           "Precedence, low to high: declared default, `config/targets/<target>.yml` (vm), your local file, the command line.", "",
           "- Your local file is `$HK_CONFIG`, else `config/local.yml` (git-ignored), else `personal/config/local.yml` "
           "(EDITION=personal). Start from `config/local.example.yml`.",
           "- Command line: `make deploy hk_ssh_user=me`, or `SET`-style via the env `HK_SET=\"hk_a=1 hk_b=2\"`.",
           "- `make config-check` validates everything (unknown names get a did-you-mean hint) and runs before every deploy; "
           "`make config` shows each effective value and where it came from.",
           "- Secrets never go in config; a variable only says where a secret lives (a Keychain item name).",
           "- A variable with targets `cabinet` does not exist for the VM, and the other way round.", ""]
    for area in sorted(areas):
        out += ["## %s" % area, ""]
        for name in sorted(areas[area]):
            d = decls[name]
            out += ["### `%s`" % name, "", d["description"].strip(), ""]
            out.append("- Type: `%s`%s" % (d["type"], " (items: %s)" % d["items"] if d.get("items") else ""))
            out.append("- Default: %s" % (md_cell(d["default"]) if "default" in d else "none, required"))
            if d.get("allowed"):
                out.append("- Allowed: %s" % ", ".join(md_cell(a) for a in d["allowed"]))
            if "min" in d or "max" in d:
                out.append("- Range: %s to %s" % (d.get("min", "-inf"), d.get("max", "+inf")))
            for k, sub in (d.get("fields") or {}).items():
                out.append("- Key `%s`: `%s`%s" % (k, sub["type"], (", " + str(sub["min"]) + " to " + str(sub["max"])) if "min" in sub else ""))
            if d.get("pattern"):
                out.append("- Pattern: `%s`" % d["pattern"])
            if d.get("aliases"):
                out.append("- Old name%s, still read: %s" % ("s" if len(d["aliases"]) > 1 else "",
                                                            ", ".join("`%s`" % a for a in d["aliases"])))
            out.append("- Targets: %s" % ", ".join(d["targets"]))
            out.append("- Read by: %s" % ", ".join("`%s`" % c for c in d["consumers"]))
            ex = d.get("exports")
            if ex:
                out.append("- Also sets the Ansible variable%s: %s" % ("s" if len(ex) > 1 else "", ", ".join("`%s`" % e for e in ex)))
            if d.get("example") is not None:
                out.append("- Example: %s" % md_cell(d["example"]))
            out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


# --- commands -------------------------------------------------------------------------------------------------


def cli_overrides(args, env):
    items = list(args.set or [])
    items += env.get("HK_SET", "").split()
    return parse_set(items)


def load(args, env):
    return Config(limit=args.limit, root=ROOT, cli=cli_overrides(args, env), env=env)


def report(problems, what="config-check"):
    for p in problems:
        sys.stderr.write("config: %s\n" % p)
    print("RESULT: FAIL %s (%d problem%s)" % (what, len(problems), "" if len(problems) == 1 else "s"))


def png_size(path):
    """(width, height) of a PNG from its header, or None when the file is not a PNG."""
    with open(path, "rb") as fh:
        head = fh.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def art_warnings(cfg):
    """WARN lines (never a failure) for hk_theme_art_dir: a missing folder, or a backdrop that is not a 1440x1080 PNG."""
    art = cfg.values.get("hk_theme_art_dir") or ""
    if not art:
        return []
    base = art if os.path.isabs(art) else os.path.join(cfg.root, art)
    back = os.path.join(base, "backdrops")
    if not os.path.isdir(back):
        return ["hk_theme_art_dir %s has no backdrops/ folder: the theme keeps its community backdrop" % art]
    out = []
    for name in sorted(os.listdir(back)):
        if name.lower().endswith(".png") and not name.startswith("eye-glow"):
            size = png_size(os.path.join(back, name))
            if size != (1440, 1080):
                out.append("hk_theme_art_dir: backdrops/%s is %s, want a 1440x1080 PNG" % (
                    name, "not a PNG" if size is None else "%dx%d" % size))
    return out


def cmd_check(args, env):
    try:
        cfg = load(args, env)
        cfg.raise_for_problems()
    except ConfigError as exc:
        report(exc.problems)
        return 1
    for w in art_warnings(cfg):
        sys.stderr.write("config: WARN %s\n" % w)
    print("config-check: OK (%s, %d variables, local: %s)" % (
        args.limit, len(cfg.values), rel(cfg.local_file) if cfg.local_file else "none"))
    return 0


def cmd_show(args, env):
    try:
        cfg = load(args, env)
        cfg.raise_for_problems()
    except ConfigError as exc:
        report(exc.problems, "config")
        return 1
    names = args.names or sorted(cfg.values)
    bad = [n for n in names if n not in cfg.decls]
    if bad:
        report(["unknown variable %s%s" % (n, hint(n, cfg.decls)) for n in bad], "config")
        return 1
    w = max([len(n) for n in names] + [4])
    print("# target %s (--limit %s), local file: %s" % (cfg.target, args.limit, rel(cfg.local_file) if cfg.local_file else "none"))
    for n in names:
        if n in cfg.values:
            print("%-*s = %s   [%s]" % (w, n, json.dumps(cfg.values[n], ensure_ascii=False), cfg.source[n]))
        else:
            print("%-*s = (not used by the %s target)" % (w, n, cfg.target))
    return 0


def cmd_get(args, env):
    soft = getattr(args, "soft", False)
    try:
        cfg = load(args, env)
        if not soft:
            cfg.raise_for_problems()
        else:   # only problems that name the asked variable count (a missing cabinet host must not stop a forge script)
            own = [p for p in cfg.problems if (args.names[:1] or [""])[0] in p and "missing required" not in p]
            if own:
                raise ConfigError(own)
    except ConfigError as exc:
        report(exc.problems, "config")
        return 1
    n = args.names[0] if args.names else ""
    if n not in cfg.decls:
        sys.stderr.write("config: unknown variable %s%s\n" % (n, hint(n, cfg.decls)))
        return 1
    if soft and n not in cfg.values:
        return 0
    if n not in cfg.values:
        sys.stderr.write("config: %s is not used by the %s target\n" % (n, cfg.target))
        return 1
    v = cfg.values[n]
    if isinstance(v, bool):
        v = "true" if v else "false"
    print(v if isinstance(v, str) else json.dumps(v))
    return 0


def cmd_guard(args, env):
    """The remote shell snippet of cabinet-guard: prints the pids of any running game, or the current-mode file when
    the session is in Fightcade mode (empty = none)."""
    try:
        cfg = load(args, env)
        cfg.raise_for_problems()
    except ConfigError as exc:
        report(exc.problems, "config")
        return 1
    esc = lambda s: re.sub(r'([\\"$`])', r"\\\1", s)   # noqa: E731
    parts = ["pgrep -x %s" % shlex.quote(p) for p in cfg.values["hk_game_procs"]]
    parts += ['pgrep -f "%s"' % esc(p) for p in cfg.values["hk_game_patterns"]]
    # #209: the session's Fightcade mode counts as a running game, only while the session in session.pid is alive and
    # is coin-dojo-session (a stale current-mode left by a killed session is no session). No quotes or $: the line
    # goes into single quotes in mk/cabinet.mk.
    run = "/run/user/%d/coin-dojo" % int(cfg.values["hk_cabinet_uid"])
    parts.append('grep -qsx fightcade {r}/current-mode && grep -qsxE "[0-9]+" {r}/session.pid && '
                 "xargs -I{{}} grep -qs coin-dojo-session /proc/{{}}/cmdline < {r}/session.pid && "
                 "echo session-in-fightcade-mode".format(r=run))
    print("; ".join(parts + ["true"]))
    return 0


def cmd_render(args, env):
    try:
        cfg = load(args, env)
        cfg.raise_for_problems()
    except ConfigError as exc:
        report(exc.problems, "config-render")
        return 1
    out = args.out
    os.makedirs(out, exist_ok=True)
    jp = os.path.join(out, "effective-%s.json" % args.limit)
    ip = os.path.join(out, "inventory-%s.yml" % args.limit)
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump(cfg.effective(), fh, indent=1, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    with open(ip, "w", encoding="utf-8") as fh:
        fh.write(cfg.inventory())
    print(jp)
    print(ip)
    return 0


def cmd_docs(args, env):
    try:
        text = render_docs(ROOT)
    except ConfigError as exc:
        report(exc.problems, "config-docs")
        return 1
    target = args.out or os.path.join(ROOT, "docs", "CONFIG.md")
    if args.check:
        try:
            cur = open(target, encoding="utf-8").read()
        except OSError:
            cur = None
        if cur != text:
            print("RESULT: FAIL config-docs: %s is out of date; run `make config-docs`" % rel(target))
            return 1
        print("RESULT: PASS config-docs: %s is up to date" % rel(target))
        return 0
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("config-docs: wrote %s" % rel(target))
    return 0


def main(argv=None, env=None):
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(prog="config.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("check", cmd_check), ("show", cmd_show), ("get", cmd_get), ("render", cmd_render), ("guard-cmd", cmd_guard), ("docs", cmd_docs)):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        if name != "docs":
            p.add_argument("--limit", default="coin-dojo")
            p.add_argument("--set", action="append", metavar="NAME=VALUE", help="command-line override (repeatable)")
        if name == "get":
            p.add_argument("--soft", action="store_true", help="tolerate problems of other variables; unset prints nothing")
        if name in ("show", "get"):
            p.add_argument("names", nargs="*")
        if name in ("render", "docs"):
            p.add_argument("--out")
        if name == "docs":
            p.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "render" and not args.out:
        ap.error("render needs --out DIR")
    try:
        return args.fn(args, env)
    except ConfigError as exc:
        report(exc.problems)
        return 1


if __name__ == "__main__":
    sys.exit(main())
