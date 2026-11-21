#!/usr/bin/env python3
"""Tests for the emulator/shader levers (ticket #126, C3 of #103).

Run: python3 ansible/tests/test_emulator_config.py   (stdlib; the config-check cases use ~/.venvs/coin-dojo, else this python
when it has PyYAML as on Forgejo CI, else a counted SKIP; #200: never a crash)

The levers hk_crt_enabled, hk_crt_preset, hk_crt_off_cores (config/variables.d/shaders.yml) and hk_flycast_* (emulators.yml)
are read by the roles retroarch122, dreamcast and launcher through pass-through defaults. "No hidden knob": above the
`# --- internal` marker of each role's defaults every value is `"{{ hk_... }}"` of a declared variable. Broken cases (must
FAIL): a literal lever planted in those defaults, a reference to an undeclared hk_ name, a hk_crt_off_cores value that is no
known core, a hardcoded uid in flycast-run.sh.
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
from hktest import check, main, skip  # noqa: E402

ROLES = ("retroarch122", "dreamcast", "launcher")
MARK = "# --- internal"
PASS = re.compile(r'^"\{\{ (hk_[a-z0-9_]+)(?: \| [a-z_()\x27|]+)? \}\}"\s*(#.*)?$')


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def declared():
    names = set()
    d = os.path.join(ROOT, "config", "variables.d")
    for f in os.listdir(d):
        if f.endswith(".yml"):
            names |= set(re.findall(r"(?m)^  (hk_[a-z0-9_]+):\s*$", read("config", "variables.d", f)))
    return names


def lever_problems(defaults, names):
    """Problems with one role's defaults text: a marker, every key above it a declared pass-through."""
    p = []
    if MARK not in defaults:
        return ["no '%s' marker: levers and internal values are not separated" % MARK]
    head = defaults.split(MARK, 1)[0]
    for m in re.finditer(r"(?m)^([A-Za-z0-9_]+):[ \t]*(.*)$", head):
        key, val = m.group(1), m.group(2).strip()
        pm = PASS.match(val)
        if not pm:
            p.append("%s is a literal above the internal marker (declare it as a hk_ variable): %s" % (key, val[:50]))
        elif pm.group(1) not in names:
            p.append("%s reads %s, which no config/variables.d file declares" % (key, pm.group(1)))
    for ref in re.findall(r"\{\{[^}]*?\b(hk_[a-z0-9_]+)\b", defaults):
        if ref not in names and ref != "hk_repo_root":
            p.append("references undeclared %s" % ref)
    return p


def test_roles_have_no_hidden_knob():
    names = declared()
    for role in ROLES:
        d = read("ansible", "roles", role, "defaults", "main.yml")
        p = lever_problems(d, names)
        check(not p, "%s defaults: every lever is a declared hk_ pass-through %s" % (role, p or ""))
        check(len(re.findall(r"(?m)^[a-z0-9_]+:", d.split(MARK, 1)[0])) >= 1, "%s defaults: the lever block is not empty" % role)


def test_broken_planted_literal_fails():
    names = declared()
    for role in ROLES:
        d = read("ansible", "roles", role, "defaults", "main.yml")
        planted = d.replace(MARK, "%s_secret_knob: 42\n\n%s" % (role, MARK), 1)
        check(lever_problems(planted, names) != [], "BROKEN: a planted literal lever in %s defaults is flagged" % role)
        typo = d.replace("{{ hk_", "{{ hk_zz_", 1)
        check(lever_problems(typo, names) != [], "BROKEN: a pass-through of an undeclared name in %s defaults is flagged" % role)
        check(lever_problems(d.replace(MARK, "# nothing", 1), names) != [], "BROKEN: %s defaults without the internal marker are flagged" % role)


def test_defaults_are_todays_live_values():
    s = read("config", "variables.d", "shaders.yml")
    check(re.search(r"(?ms)^  hk_crt_enabled:.*?^    default: true\s*$", s) is not None, "hk_crt_enabled defaults to true (CRT on, #102)")
    check(re.search(r"(?ms)^  hk_crt_preset:.*?^    default: crt/crt-pi\.glslp\s*$", s) is not None, "hk_crt_preset defaults to crt/crt-pi.glslp")
    check(re.search(r"(?ms)^  hk_crt_off_cores:.*?^    default: \[Mupen64Plus-Next, PCSX-ReARMed\]\s*$", s) is not None,
          "hk_crt_off_cores defaults to [Mupen64Plus-Next, PCSX-ReARMed]")
    e = read("config", "variables.d", "emulators.yml")
    check(re.search(r"(?ms)^  hk_upscale:.*?^    default: 2\s*$", e) is not None, "hk_upscale defaults to 2 (#170: the live 2x)")
    for name, val in (("hk_flycast_resolution", "0"), ("hk_flycast_vsync", "true"), ("hk_flycast_show_fps", "false")):
        check(re.search(r"(?ms)^  %s:.*?^    default: %s\s*$" % (name, val), e) is not None, "%s defaults to %s (the live emu.cfg; 0 = 960 via hk_upscale)" % (name, val))
    check("hk_emulators_root" not in declared(), "hk_emulators_root is not a lever (#164: fixed image layout)")


def emulator_path_problems(launch, smoke, ra_defaults, dc_defaults, launcher_defaults):
    """#164: every reader of the fixed /data/emulators layout names the same install paths."""
    def val(rx, text):
        m = re.search(rx, text, re.M)
        return m.group(1).strip('"') if m else None
    ra_dir, core_dir = val(r"^retroarch122_dir:\s*(\S+)", ra_defaults), val(r"^retroarch122_core_dir:\s*(\S+)", ra_defaults)
    dc_dir = val(r"^dreamcast_dir:\s*(\S+)", dc_defaults)
    ra_bin, fly = val(r'^RETROARCH = "([^"]+)"', launch), val(r'^FLYCAST = "([^"]+)"', launch)
    n64 = val(r'^\s*"n64": "([^"]+)"', launch)
    smoke_ra = val(r"^RA=\$\{RA:-([^}]+)\}", smoke)
    p = []
    roots = {"/".join(x.split("/")[:3]) for x in (ra_dir, core_dir, dc_dir, ra_bin, fly, n64, smoke_ra) if x}
    if roots != {"/data/emulators"}:
        p.append("more than one emulators root: %s" % sorted(roots))
    if ra_bin != "%s/retroarch" % ra_dir:
        p.append("launch.py RETROARCH %s is not retroarch122_dir/retroarch (%s)" % (ra_bin, ra_dir))
    if smoke_ra != ra_bin:
        p.append("vm/guest/smoke.sh RA %s != launch.py RETROARCH %s" % (smoke_ra, ra_bin))
    if not (fly or "").startswith("%s/" % dc_dir):
        p.append("launch.py FLYCAST %s is not under dreamcast_dir %s" % (fly, dc_dir))
    if not (n64 or "").startswith("%s/" % core_dir):
        p.append("launch.py n64 core %s is not under retroarch122_core_dir %s" % (n64, core_dir))
    for path in re.findall(r"^\s+- (/data/emulators/\S+)", launcher_defaults, re.M):
        if path not in (ra_bin, n64):
            p.append("launcher default %s is not a path launch.py uses" % path)
    return p


def test_emulator_paths_agree():
    args = (read("skills", "collections", "launch.py"), read("vm", "guest", "smoke.sh"),
            read("ansible", "roles", "retroarch122", "defaults", "main.yml"), read("ansible", "roles", "dreamcast", "defaults", "main.yml"),
            read("ansible", "roles", "launcher", "defaults", "main.yml"))
    check(emulator_path_problems(*args) == [], "#164: role defaults, launch.py and smoke.sh name the same emulator paths %s" % emulator_path_problems(*args))
    broken = (args[0].replace('RETROARCH = "/data/emulators/', 'RETROARCH = "/opt/emulators/'),) + args[1:]
    check(any("more than one emulators root" in x for x in emulator_path_problems(*broken)), "BROKEN case: a planted second root is flagged")
    broken = args[:2] + (args[2].replace("retroarch122_dir: /data/emulators/retroarch-1.22", "retroarch122_dir: /data/emulators/retroarch-1.23"),) + args[3:]
    check(any("RETROARCH" in x for x in emulator_path_problems(*broken)), "BROKEN case: a role dir that drifts from launch.py is flagged")


def test_flycast_run_takes_uid_from_config():
    t = read("ansible", "roles", "dreamcast", "templates", "flycast-run.sh.j2")
    check("/run/user/{{ dreamcast_uid }}" in t and not re.search(r"/run/user/\d", t), "flycast-run.sh: the runtime dir uses the configured uid")
    check("/data/emulators" not in t and t.count("{{ dreamcast_dir }}") == 2, "flycast-run.sh: the install dir is dreamcast_dir, not a literal")
    check(not re.search(r"/run/user/\d", t.replace("{{ dreamcast_uid }}", "4242").replace("/run/user/4242", "/run/user/{{ dreamcast_uid }}")),
          "flycast-run.sh: no uid literal left")
    d = read("ansible", "roles", "dreamcast", "defaults", "main.yml")
    check('dreamcast_uid: "{{ hk_cabinet_uid }}"' in d, "dreamcast_uid is the hk_cabinet_uid pass-through")
    bad = t.replace("{{ dreamcast_uid }}", "4242")
    check(re.search(r"/run/user/\d", bad) is not None, "BROKEN: a hardcoded uid is detected by the rule")
    check(not os.path.exists(os.path.join(ROOT, "ansible", "roles", "dreamcast", "files", "flycast-run.sh")),
          "no second, literal flycast-run.sh is left under files/")


VENV_PY = os.path.expanduser("~/.venvs/coin-dojo/bin/python")
NO_PYYAML = "PyYAML venv missing (make config-venv) and this python has no PyYAML: the config-check cases did not run"


def config_python(venv=VENV_PY, fallback=sys.executable):
    """The python that runs tools/config.py: the make config-venv venv, else `fallback` when it imports yaml (Forgejo
    CI puts the pinned PyYAML on PYTHONPATH), else None. #200: test_upscale_lever called the venv unguarded and
    crashed with FileNotFoundError on the runner, which has no venv."""
    if venv and os.path.exists(venv):
        return venv
    try:
        r = subprocess.run([fallback, "-c", "import yaml"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return fallback if r.returncode == 0 else None


def config_check(extra, python=None):
    venv = python or config_python()
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as fh:
        fh.write("hk_cabinet_host: arcade.local\n" + extra)
    try:
        env = {k: v for k, v in os.environ.items() if not k.startswith("HK_")}
        r = subprocess.run([venv, os.path.join(ROOT, "tools", "config.py"), "check"], capture_output=True, text=True,
                           env=dict(env, HK_CONFIG=fh.name))
        return r.returncode, r.stdout + r.stderr
    finally:
        os.unlink(fh.name)


def test_config_python():
    missing = os.path.join(tempfile.gettempdir(), "no-such-venv-200", "bin", "python")
    check(config_python(missing, sys.executable) in (sys.executable, None), "no venv: this python or None, never the venv")
    check(config_python(missing, missing) is None, "BROKEN: no venv and no python with PyYAML = None (SKIP, not a crash)")
    check(config_python(sys.executable, missing) == sys.executable, "an existing venv python is used as is")


def test_config_check_cases():
    if not config_python():
        skip(NO_PYYAML)
        return
    rc, out = config_check("")
    check(rc == 0, "config-check passes with the defaults (%s)" % out.strip()[:80])
    rc, out = config_check("hk_crt_off_cores: [Mupen64Plus-Next, Nestopia]\nhk_crt_enabled: false\nhk_flycast_resolution: 1440\n")
    check(rc == 0, "config-check passes with known cores and other lever values (%s)" % out.strip()[:80])
    rc, out = config_check("hk_crt_off_cores: []\n")
    check(rc == 0, "an empty hk_crt_off_cores (scanlines on every core) is valid")
    rc, out = config_check("hk_crt_off_cores: [Mupen64Plus-Next, Doom]\n")
    check(rc != 0 and "hk_crt_off_cores" in out and "Doom" in out, "BROKEN: a hk_crt_off_cores value outside the known cores FAILs (%s)" % out.strip()[:100])
    rc, out = config_check("hk_crt_off_cores: [Mupen64Plus-Nxt]\n")
    check(rc != 0 and "did you mean 'Mupen64Plus-Next'" in out, "BROKEN: a mistyped core name FAILs with a hint")
    rc, out = config_check("hk_crt_off_cores: Mupen64Plus-Next\n")
    check(rc != 0, "BROKEN: hk_crt_off_cores as a bare string FAILs")
    rc, out = config_check("hk_crt_preset: crt/crt-royale.glslp\n")
    check(rc != 0 and "hk_crt_preset" in out, "BROKEN: a preset the role does not pin FAILs")
    rc, out = config_check("hk_crt_enabled: sometimes\n")
    check(rc != 0, "BROKEN: hk_crt_enabled must be true or false")
    rc, out = config_check("hk_flycast_resolution: 99999\n")
    check(rc != 0 and "maximum" in out, "BROKEN: a flycast resolution above the maximum FAILs")


def upscale_problems(ct_defaults, dc_defaults, max_n):
    """#170: every hk_upscale-derived size is 4:3 and at most 2x. -> problems (empty = ok)."""
    out = []
    m = re.search(r"mupen64plus-43screensize, value: \"\{\{ (\d+) \* cabinet_upscale \| int \}\}x\{\{ (\d+) \* cabinet_upscale", ct_defaults)
    if not m:
        out.append("N64 screen size is not derived from cabinet_upscale")
    else:
        w, h = int(m.group(1)), int(m.group(2))
        if (w, h) != (320, 240) or w * 3 != h * 4:
            out.append("N64 base size %dx%d is not the native 4:3 320x240" % (w, h))
    if "else 480 * dreamcast_upscale | int" not in dc_defaults:
        out.append("flycast height is not 480 x dreamcast_upscale")
    if "'enabled' if cabinet_upscale | int >= 2 else 'disabled'" not in ct_defaults:
        out.append("PSX enhancement (a fixed 2x) is not tied to hk_upscale >= 2")
    if max_n != 2:
        out.append("hk_upscale max is %s, not 2 (the 2 GB Pi 5 ceiling)" % max_n)
    return out


def test_upscale_lever():
    ct = read("ansible", "roles", "cabinet_tweaks", "defaults", "main.yml")
    dc = read("ansible", "roles", "dreamcast", "defaults", "main.yml")
    e = read("config", "variables.d", "emulators.yml")
    mx = re.search(r"(?ms)^  hk_upscale:.*?^    max: (\d+)\s*$", e)
    bad = upscale_problems(ct, dc, int(mx.group(1)) if mx else None)
    check(not bad, "#170 hk_upscale: N64 320x240 x n, flycast 480 x n, PSX at n >= 2, max 2 (%s)" % "; ".join(bad))
    wide = ct.replace("{{ 320 * cabinet_upscale", "{{ 427 * cabinet_upscale")
    check(upscale_problems(wide, dc, 2), "BROKEN: a 16:9 N64 base size (427x240) is flagged")
    check(upscale_problems(ct, dc, 3), "BROKEN: a max of 3 is flagged")
    check("cabinet_core_options: \"{{ cabinet_upscale_options + hk_core_options }}\"" in ct,
          "hk_core_options come after the derived ones (a same-key line there wins)")
    if not config_python():
        skip("hk_upscale config-check cases: " + NO_PYYAML)
        return
    for val in ("3", "0"):
        rc, out = config_check("hk_upscale: %s\n" % val)
        check(rc != 0 and "hk_upscale" in out, "BROKEN: hk_upscale %s FAILs config-check" % val)
    rc, out = config_check("hk_upscale: 1\n")
    check(rc == 0, "hk_upscale 1 (native) passes (%s)" % out.strip()[-120:])


def test_known_cores_match_the_cabinet():
    s = read("config", "variables.d", "shaders.yml")
    allowed = re.search(r"(?ms)^  hk_crt_off_cores:.*?^    allowed: \[(.*?)\]\s*$", s).group(1).split(", ")
    for core in ("Mupen64Plus-Next", "PCSX-ReARMed", "Snes9x", "FinalBurn Neo", "Genesis Plus GX", "Beetle PCE", "Beetle Saturn", "Nestopia", "Stella", "Flycast"):
        check(core in allowed, "known core %s (a folder under ~/.config/retroarch/config on the cabinet)" % core)


if __name__ == "__main__":
    main(globals())
