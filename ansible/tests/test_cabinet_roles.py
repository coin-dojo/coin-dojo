#!/usr/bin/env python3
"""Checks for the Debian-cabinet roles `dreamcast` (#22) and `cabinet_tweaks` (#24, zram #52, remaps #56).

Pure stdlib (no ansible/PyYAML). pytest-compatible; or run directly:

    python3 ansible/tests/test_cabinet_roles.py

Each rule is a function over file TEXT so the same rule can be run against a deliberately
broken copy (test_broken_*): those MUST be flagged, proving the lint can fail.
"""
import os
import re
import sys

ANSIBLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests", "lib"))
from hktest import check as _check, main, skip  # noqa: E402  (shared helper: counted SKIPs, one RESULT line, #153)


def _read(*parts):
    with open(os.path.join(ANSIBLE, *parts), encoding="utf-8") as fh:
        return fh.read()


# ---- rules over text: each returns a list of problems (empty = ok) ----------------------------
def rule_kernel(tasks, defaults):
    p = []
    if not re.search(r"line:\s*kernel=kernel8\.img", tasks):
        p.append("kernel=kernel8.img line missing")
    if not re.search(r"insertafter:\s*'\^\\\[all\\\]'", tasks):
        p.append("kernel line not placed under [all]")
    if "backup: true" not in tasks:
        p.append("config.txt not backed up before change")
    if not re.search(r"(?m)^  hk_kernel_4k:\n    type: bool\n    default: true\s*$", defaults):
        p.append("hk_kernel_4k lever missing or not default true")
    if not re.search(r'(?m)^cabinet_kernel_4k: "\{\{ hk_kernel_4k \}\}"\s*$', defaults):
        p.append("cabinet_kernel_4k is not a pass-through to hk_kernel_4k")
    if "when: cabinet_kernel_4k | bool" not in tasks:
        p.append("kernel task not guarded by cabinet_kernel_4k")
    if re.search(r"ansible\.builtin\.(reboot|command:\s*\S*reboot)", tasks):
        p.append("automatic reboot forbidden")
    if "Revert" not in tasks:
        p.append("no revert comment")
    return p


def rule_handlers(handlers):
    p = []
    if "reboot required" not in handlers.lower():
        p.append("no 'reboot required' message")
    if "cabinet_reboot_required" not in handlers:
        p.append("reboot flag fact not set")
    if re.search(r"ansible\.builtin\.reboot", handlers):
        p.append("handler reboots automatically")
    return p


def rule_retroarch(defaults):
    p = []
    for k, v in (("input_enable_hotkey_btn", "9"), ("input_exit_emulator_btn", "8"),
                 ("netplay_public_announce", "false"),   # #42: never announce to the public lobby
                 ("input_menu_toggle_btn", "0"), ("fps_show", "false"),
                 ("input_overlay_next_btn", "5"), ("input_pause_toggle_btn", "5")):
        if not re.search(r'key: %s, value: "%s"' % (k, v), defaults):
            p.append("retroarch.cfg %s=%s missing" % (k, v))
    for key in ("pcsx_rearmed_neon_enhancement_enable", "pcsx_rearmed_neon_enhancement_no_main",
                "mupen64plus-43screensize", "reicast_internal_resolution"):
        if key not in defaults:
            p.append("core option %s missing" % key)
    return p


BEZEL_X = (240 / 1920.0, 1680 / 1920.0)   # the 4:3 window of the 1920 panel the bezel leaves visible (#174)


def bezel_problems(cfg):
    """#174: RetroArch's menu and popups stay inside the bezel. cfg = {key: value} of hk_retroarch_cfg."""
    out = []
    if cfg.get("rgui_aspect_ratio_lock") != "0":
        out.append("rgui_aspect_ratio_lock must be 0 (follows the 4:3 viewport; 1 = Fit shifts the menu under the bezel)")
    anchor = cfg.get("cheevos_appearance_anchor")
    if anchor not in ("1", "4"):                                   # top-center / bottom-center
        pad = float(cfg.get("cheevos_appearance_padding_h") or 0)
        if cfg.get("cheevos_appearance_padding_auto") != "false" or pad < BEZEL_X[0]:
            out.append("achievement popups anchored %r land outside the bezel (use 1/4 or a padding_h >= %.3f)"
                       % (anchor, BEZEL_X[0]))
    try:
        x = float(cfg.get("video_message_pos_x", "0.05"))
    except ValueError:
        x = -1
    if not BEZEL_X[0] + 0.01 <= x <= BEZEL_X[1] - 0.25:
        out.append("video_message_pos_x %r is not inside the bezel window" % cfg.get("video_message_pos_x"))
    if cfg.get("menu_show_load_content_animation") != "false":
        out.append("the full-width load-content band is on")
    for k in ("notification_show_autoconfig", "notification_show_autoconfig_fails", "notification_show_remap_load",
              "notification_show_config_override_load"):
        if cfg.get(k) != "false":
            out.append("%s is on: a toast at the fixed bottom-left spot, under the bezel (#174, #186)" % k)
    return out


def retroarch_cfg_defaults():
    t = _read("..", "config", "variables.d", "launch.yml")
    blk = t.split("  hk_retroarch_cfg:", 1)[1].split("\n  hk_", 1)[0]
    return dict(re.findall(r'\{ key: (\w+), value: "([^"]*)" \}', blk))


def test_bezel_keys():
    cfg = retroarch_cfg_defaults()
    bad = bezel_problems(cfg)
    _check(not bad, "#174 retroarch.cfg keeps the menu + popups inside the bezel (%s)" % "; ".join(bad))
    for k, v, why in (("cheevos_appearance_anchor", "0", "top-left popups"), ("video_message_pos_x", "0.050000", "OSD at x=96"),
                      ("rgui_aspect_ratio_lock", "1", "RGUI Fit"), ("menu_show_load_content_animation", "true", "the band"),
                      ("notification_show_remap_load", "true", "the remap toast (#186)")):
        _check(bezel_problems(dict(cfg, **{k: v})), "BROKEN CASE: %s (%s=%s) is flagged" % (why, k, v))
    pad = dict(cfg, cheevos_appearance_anchor="0", cheevos_appearance_padding_auto="false", cheevos_appearance_padding_h="0.140000")
    _check(not bezel_problems(pad), "top-left popups with a manual padding past the bezel pass")


def trophies_carousel_problems(tasks, defaults, decl):
    """#181: the TROPHIES carousel entry only with hk_trophies_carousel (default off); the LP overlay needs none."""
    out = []
    if "if esde_trophies_entry.stat.exists and esde_collections_trophies_carousel | bool else []" not in tasks:
        out.append("--trophies-entry is not gated by esde_collections_trophies_carousel")
    if 'esde_collections_trophies_carousel: "{{ hk_trophies_carousel | default(false) }}"' not in defaults:
        out.append("esde_collections_trophies_carousel does not pass hk_trophies_carousel through")
    if not re.search(r"(?ms)^  hk_trophies_carousel:\n    type: bool\n    default: false$", decl):
        out.append("hk_trophies_carousel must be a bool, default false")
    return out


def test_trophies_carousel_off_by_default():
    t = _read("roles", "esde_collections", "tasks", "main.yml")
    d = _read("roles", "esde_collections", "defaults", "main.yml")
    decl = _read("..", "config", "variables.d", "achievements.yml")
    bad = trophies_carousel_problems(t, d, decl)
    _check(not bad, "#181 TROPHIES carousel entry off unless hk_trophies_carousel (%s)" % "; ".join(bad))
    _check(trophies_carousel_problems(t.replace(" and esde_collections_trophies_carousel | bool", ""), d, decl),
           "BROKEN CASE: the old gate (entry file only) is flagged")
    _check(trophies_carousel_problems(t, d, decl.replace("type: bool\n    default: false\n    description: >-\n      Also show TROPHIES",
                                                          "type: bool\n    default: true\n    description: >-\n      Also show TROPHIES")),
           "BROKEN CASE: hk_trophies_carousel defaulting to true is flagged")


def benchmark_problems(defaults, base):
    """#185: benchmark mode's keys put the FPS counter top right inside the bezel; switching it off writes back a normal
    value for every key it changed (from cabinet_benchmark_off or hk_retroarch_cfg). base = hk_retroarch_cfg {key: v}."""
    def block(name):
        m = re.search(r"(?m)^%s:[^\n]*\n((?:  - \{ key: [^\n]*\n)+)" % name, defaults)
        return dict(re.findall(r'\{ key: (\w+), value: "([^"]*)" \}', m.group(1))) if m else {}
    on, off = block("cabinet_benchmark_on"), block("cabinet_benchmark_off")
    out = []
    if on.get("fps_show") != "true" or on.get("menu_enable_widgets") != "false":
        out.append("on: fps_show true needs the widgets off (with widgets RetroArch draws it under the right bezel)")
    try:
        x, y = float(on.get("video_message_pos_x", "-1")), float(on.get("video_message_pos_y", "-1"))
    except ValueError:
        x = y = -1
    if not (0.5 <= x <= BEZEL_X[1] - 0.11 and y >= 0.9):
        out.append("on: the counter must sit top right inside the bezel (x %s, y %s)" % (x, y))
    missing = sorted(k for k in on if k not in off and k not in base)
    if missing:
        out.append("off does not restore %s" % missing)
    if "combine(cabinet_benchmark_on | items2dict if cabinet_benchmark | bool else {})" not in defaults:
        out.append("the on keys are not merged by key only when benchmark mode is on")
    return out


def test_benchmark_mode_keys():
    d = _read("roles", "cabinet_tweaks", "defaults", "main.yml")
    base = retroarch_cfg_defaults() if "retroarch_cfg_defaults" in globals() else {}
    bad = benchmark_problems(d, base)
    _check(not bad, "#185 benchmark mode: FPS top right inside the bezel, off restores every key (%s)" % "; ".join(bad))
    _check(benchmark_problems(d.replace('{ key: video_message_pos_x, value: "0.750000" }',
                                        '{ key: video_message_pos_x, value: "0.950000" }'), base),
           "BROKEN CASE: a counter at x 0.95 (under the right bezel) is flagged")
    _check(benchmark_problems(d.replace('  - { key: video_msg_bgcolor_opacity, value: "1.000000" }\n', ''), base),
           "BROKEN CASE: an on-key that off never restores is flagged")



def dreamcast_gui_problems(defaults, template):
    """#187: flycast's launcher exports the env vars our patched build reads (GUI inside the 4:3 area, FPS top right),
    and benchmark mode (hk_benchmark_mode) turns flycast's counter on as well as hk_flycast_show_fps."""
    out = []
    m = re.search(r"(?m)^dreamcast_gui_env:\n((?:  \w+: [^\n]*\n)+)", defaults)
    env = dict(re.findall(r'  (\w+): "([^"]*)"', m.group(1))) if m else {}
    for var in ("FLYCAST_GUI_43", "FLYCAST_FPS_TOPRIGHT"):
        if env.get(var) != "1":
            out.append("dreamcast_gui_env must set %s to \"1\"" % var)
    if "export {{ k }}={{ v | quote }}" not in template or "dreamcast_gui_env | dictsort" not in template:
        out.append("flycast-run.sh does not export dreamcast_gui_env")
    elif template.index("dreamcast_gui_env") > template.index("{{ dreamcast_dir }}/flycast "):
        out.append("flycast-run.sh exports the env after flycast starts")
    if 'dreamcast_benchmark: "{{ hk_benchmark_mode }}"' not in defaults:
        out.append("dreamcast_benchmark is not a pass-through of hk_benchmark_mode")
    if "key: rend.ShowFPS, value: \"{{ 'yes' if (dreamcast_show_fps | bool or dreamcast_benchmark | bool)" not in defaults:
        out.append("rend.ShowFPS does not follow benchmark mode")
    return out


def test_dreamcast_gui_inside_bezel():
    d = _read("roles", "dreamcast", "defaults", "main.yml")
    t = _read("roles", "dreamcast", "templates", "flycast-run.sh.j2")
    bad = dreamcast_gui_problems(d, t)
    _check(not bad, "#187 flycast GUI + FPS inside the 4:3 bezel, benchmark mode drives rend.ShowFPS (%s)" % "; ".join(bad))
    _check(dreamcast_gui_problems(d.replace('  FLYCAST_GUI_43: "1"\n', '  FLYCAST_GUI_43: "0"\n'), t),
           "BROKEN CASE: FLYCAST_GUI_43 off is flagged")
    _check(dreamcast_gui_problems(d, t.replace("export {{ k }}={{ v | quote }}", "# no export")),
           "BROKEN CASE: a launcher without the exports is flagged")
    _check(dreamcast_gui_problems(d.replace("dreamcast_show_fps | bool or dreamcast_benchmark | bool",
                                            "dreamcast_show_fps | bool"), t),
           "BROKEN CASE: rend.ShowFPS ignoring benchmark mode is flagged")


def rule_es_settings(tasks, defaults, files):
    p = []
    for name in ("CollectionSystemsCustom", "CollectionSystemsAuto", "StartupSystem",
                 "CollectionCustomGrouping", "MenuColorScheme"):
        if not re.search(r"(?m)^\s+%s:" % name, defaults):
            p.append("es key %s missing" % name)
    # Attract mode: manual (ScreensaverControls) + auto only after 1 h idle (the maintainer 2026-11-14).
    if not re.search(r"hk_attract_timer_ms:\n\s+type: int\n\s+default: 600000\n", defaults):
        p.append("ScreensaverTimer must be 600000 (10 min)")
    if not re.search(r"hk_attract_controls:\n\s+type: bool\n\s+default: true\n", defaults):
        p.append("ScreensaverControls must be true (manual attract mode)")
    # No favorites (the maintainer 2026-11-14) and LAST PLAYED on (#61).
    for key in ("FavoritesAddButton", "FavoritesStar", "FavoritesFirst"):
        if not re.search(r"(?m)^\s+%s: false\s*$" % key, defaults):
            p.append("%s must be false (favorites off)" % key)
    if not re.search(r"(?m)^\s+CollectionSystemsAuto: recent\b", defaults):
        p.append("CollectionSystemsAuto must be recent (LAST PLAYED)")
    # es_settings.xml holds a ScreenScraper password: never templated/copied wholesale.
    if re.search(r"(template|copy):\s*\n(?:\s+\S.*\n)*?\s+dest:.*es_settings", tasks):
        p.append("es_settings.xml templated/copied (password!)")
    if any("es_settings" in f for f in files):
        p.append("es_settings file shipped in role files")
    if "lineinfile" not in tasks or "no_log: true" not in tasks:
        p.append("es_settings edit must be lineinfile + no_log")
    p += rule_es_settings_tasks(tasks)
    return p


# Task keywords that are not the module name (the first other key of a task is its module).
TASK_KEYS = {"name", "become", "become_user", "when", "loop", "loop_control", "register", "no_log", "notify",
             "changed_when", "failed_when", "check_mode", "diff", "tags", "vars", "environment", "args",
             "delegate_to", "run_once", "ignore_errors", "until", "retries", "delay", "listen"}
READERS = ("fetch", "slurp", "synchronize", "command", "shell", "copy", "template", "script", "raw")


def task_blocks(tasks):
    """Split a tasks/main.yml text into its top-level task blocks."""
    return [b for b in re.split(r"(?m)^(?=- )", tasks) if b.startswith("- ")]


def task_module(block):
    for m in re.finditer(r"(?m)^(?:- |  )([A-Za-z_][\w.]*):", block):
        key = m.group(1)
        if key not in TASK_KEYS:
            return key.split(".")[-1]
    return ""


def rule_es_settings_tasks(tasks):
    """#42 (Security): a task that touches es_settings.xml (ScreenScraper password) may only stat it or edit it
    with lineinfile (no_log, mode 0600, no backup copy); no module may read/copy it anywhere."""
    p = []
    for b in task_blocks(tasks):
        mod = task_module(b)
        if "cabinet_es_settings }}" in b:
            if mod not in ("stat", "lineinfile"):
                p.append("es_settings touched by %s (only stat/lineinfile)" % mod)
            if mod == "lineinfile":
                if not re.search(r"(?m)^  no_log: true\b", b):
                    p.append("es_settings lineinfile without no_log")
                if not re.search(r'(?m)^    mode: "0600"', b):
                    p.append("es_settings lineinfile without mode 0600")
        if re.search(r"(?m)^    backup: (true|yes)\b", b) and "es_settings" in b:
            p.append("es_settings backup copy (the password would be copied)")
        if mod in READERS and "es_settings" in b:
            p.append("es_settings read/copied by %s" % mod)
    return p


def rule_hardening(tasks, defaults):
    """the maintainer 2026-11-14: pi out of sudo (user module, exact groups minus sudo), apt keeps no cache,
    unattended upgrades stay off."""
    p = []
    if not re.search(r"(?m)^cabinet_nosudo_users:.*\bpi\b", defaults):
        p.append("pi not in cabinet_nosudo_users")
    m = re.search(r"- name: Take these users out of the sudo group.*?(?=\n- name:|\Z)", tasks, re.S)
    blk = m.group(0) if m else ""
    if "ansible.builtin.user:" not in blk:
        p.append("sudo removal is not an ansible.builtin.user task")
    if not re.search(r"(?m)^    append: false\s*$", blk):
        p.append("user groups not exact (append must be false)")
    if "reject('equalto', 'sudo')" not in blk:
        p.append("sudo not removed from the groups")
    if not re.search(r'APT::Keep-Downloaded-Packages "false";', tasks):
        p.append("apt cache not off (APT::Keep-Downloaded-Packages)")
    if "50-coin-dojo-nocache" not in defaults:
        p.append("apt nocache file is not 50-coin-dojo-nocache")
    if re.search(r"unattended-upgrades|APT::Periodic::Unattended-Upgrade \"1\"", tasks + defaults):
        p.append("unattended upgrades must stay off")
    return p


def rule_sudo_timeout(tasks, defaults):
    """#93: sudo keeps no cached credential for the cabinet user (Pi OS timestamp_type=global gave every coindojo
    process ~15 min of root after a BECOME=1 deploy). One visudo-validated 0440 drop-in, plus an apply-time proof."""
    p = []
    m = re.search(r"(?m)^cabinet_sudo_timeout_file:\s*(\S+)", defaults)
    path = m.group(1) if m else ""
    name = path.rsplit("/", 1)[-1]
    if not path.startswith("/etc/sudoers.d/"):
        p.append("sudo timeout file is not under /etc/sudoers.d/")
    if "." in name or name.endswith("~"):
        p.append("sudo timeout file name has a dot or ends in ~ (sudo skips it)")
    if name and not name > "010_global-tty":
        p.append("sudo timeout file sorts before Pi OS's 010_* files")
    m = re.search(r"- name: Sudo caches no credentials.*?(?=\n- name:|\Z)", tasks, re.S)
    blk = m.group(0) if m else ""
    if not blk:
        p.append("no sudo timeout task")
    if not re.search(r"(?m)^      Defaults:\{\{ cabinet_user \}\} timestamp_timeout=0\s*$", blk):
        p.append("drop-in does not set Defaults:<cabinet user> timestamp_timeout=0")
    if not re.search(r"(?m)^    validate: \S*visudo -cf %s\s*$", blk):
        p.append("sudo timeout drop-in not validated by visudo")
    if not re.search(r'(?m)^    mode: "0440"\s*$', blk):
        p.append("sudo timeout drop-in not mode 0440")
    m = re.search(r"- name: Check that sudo without a password is refused.*?(?=\n- name:|\Z)", tasks, re.S)
    chk = m.group(0) if m else ""
    if "sudo -n true" not in chk or "failed_when: cabinet_sudo_cached.rc == 0" not in chk:
        p.append("no apply-time proof that `sudo -n true` is refused")
    if re.search(r"(?m)^  become: true", chk):
        p.append("the proof runs with become (it must run as the cabinet user)")
    return p


def rule_dreamcast(tasks, defaults):
    p = []
    for pkg in ("libzip5", "libminiupnpc18"):
        if pkg not in defaults:
            p.append("apt package %s missing" % pkg)
    for bios in ("dc_boot.bin", "dc_flash.bin", "naomi.zip", "naomi2.zip", "awbios.zip"):
        if bios not in defaults:
            p.append("BIOS link %s missing" % bios)
    if "state: link" not in tasks:
        p.append("BIOS must be symlinks")
    if not re.search(r'key: pvr\.rend, value: "0"', defaults):
        p.append("pvr.rend must be 0 (GLES)")
    if "dreamcast_flycast_binary_src" not in tasks:
        p.append("flycast binary not copied from a configurable local path")
    if '<emulator name=\\"FLYCAST\\">' not in tasks:
        p.append("FLYCAST emulator rule missing")
    if "Flycast (Standalone)" not in defaults:
        p.append("alternativeEmulator label missing")
    # #23: panel-sized window, else SDL/cage stretches the 4:3 frame to 16:9
    for k, v in (("width", "1920"), ("height", "1080"), ("maximized", "yes")):
        if not re.search(r'section: window, key: %s, value: "%s"' % (k, v), defaults):
            p.append("window.%s must be %s (4:3 pillarbox, #23)" % (k, v))
    return p


def rule_no_artifacts(files):
    bad = [f for f in files if f.lower().endswith((".bin", ".zip", ".chd", ".iso", ".rom", ".img"))
           or os.path.basename(f) == "flycast"]
    return ["artifact in role files: %s" % f for f in bad]


# ES-DE 3.5 es_systems.xml (linuxarm) labels for the pinned systems; "Mesen" is first = the broken default.
ESDE_LABELS = {
    "nes": ["Mesen", "Mesen (Standalone)", "MesenCE", "Nestopia UE", "Nestopia UE (Standalone)", "FCEUmm", "QuickNES"],
    "psx": ["Beetle PSX", "Beetle PSX HW", "PCSX ReARMed", "SwanStation", "DuckStation (Standalone)"],
    "cps1": ["MAME - Current", "MAME 2003-Plus", "FinalBurn Neo", "FB Alpha 2012"],
    "cps2": ["MAME - Current", "MAME 2003-Plus", "FinalBurn Neo", "FB Alpha 2012"],
    "cps3": ["MAME - Current", "MAME 2003-Plus", "FinalBurn Neo", "FB Alpha 2012"],
}
# Cores installed on the cabinet (/usr/lib/aarch64-linux-gnu/libretro), measured 2026-11-14.
CABINET_CORES = {"nestopia_libretro.so", "fbneo_libretro.so", "pcsx_rearmed_libretro.so"}
REQUIRED_PINS = ("nes", "cps1", "cps2", "cps3", "psx")


def parse_alt_emulators(defaults):
    """{system: (label, core)} from the hk_alt_emulators default in config/variables.d/launch.yml (#125)."""
    m = re.search(r"^  hk_alt_emulators:\n(?:.*\n)*?    default:\n((?:      .*\n)+)", defaults, re.M)
    out = {}
    for line in (m.group(1) if m else "").splitlines():
        x = re.match(r'\s+(\w+):\s*\{\s*label:\s*"([^"]+)",\s*core:\s*(\S+)\s*\}', line)
        if x:
            out[x.group(1)] = (x.group(2), x.group(3))
    return out


def rule_alt_emulators(defaults, labels=ESDE_LABELS, cores=CABINET_CORES):
    p = []
    pins = parse_alt_emulators(defaults)
    for sysname in REQUIRED_PINS:
        if sysname not in pins:
            p.append("%s not pinned" % sysname)
    for sysname, (label, core) in pins.items():
        if label not in labels.get(sysname, []):
            p.append("%s: label %r is not an ES-DE label" % (sysname, label))
        if core not in cores:
            p.append("%s: core %s not installed on the cabinet" % (sysname, core))
    if pins.get("nes", ("",))[0] == "Mesen":
        p.append("nes pinned to Mesen (core not installed)")
    return p


def _files(role):
    base = os.path.join(ANSIBLE, "roles", role, "files")
    return sorted(os.listdir(base)) if os.path.isdir(base) else []


CONFIG_D = os.path.join(ANSIBLE, "..", "config", "variables.d")


def _config_text(*areas):
    out = []
    for a in areas:
        with open(os.path.join(CONFIG_D, a + ".yml"), encoding="utf-8") as fh:
            out.append(fh.read())
    return "\n".join(out)


def _role(role):
    """(tasks, defaults). For cabinet_tweaks the 'defaults' text also holds the declared levers (#125):
    config/variables.d/{display,attract,esde,launch}.yml, so the rules read the live values where they are declared."""
    d = lambda *a: _read("roles", role, *a)
    defaults = d("defaults", "main.yml")
    if role == "cabinet_tweaks":
        defaults += "\n" + _config_text("display", "attract", "esde", "launch")
    return d("tasks", "main.yml"), defaults


# ---- the tests --------------------------------------------------------------------------------
def test_layout_and_wiring():
    for role, extra in (("dreamcast", ["templates/flycast-run.sh.j2", "files/flycast-exit-combo.py"]),
                        ("cabinet_tweaks", ["files/gen_cursor.py", "handlers/main.yml"])):
        for rel in ["tasks/main.yml", "defaults/main.yml"] + extra:
            _check(os.path.isfile(os.path.join(ANSIBLE, "roles", role, rel)), "%s/%s exists" % (role, rel))
    site = _read("site.yml")
    for role in ("dreamcast", "cabinet_tweaks"):
        _check(re.search(r"role:\s*%s\b" % role, site) is not None, "site.yml wires %s" % role)
    _check("hosts: pios" in site, "site.yml targets the Debian cabinet group (pios)")
    _check("pios:" in _read("inventory", "hosts.yml"), "inventory defines group pios")
    inv = _read("inventory", "hosts.yml")
    # #118: the address and user come from the config (tools/config.py renders the host), never from the file.
    _check("ansible_user" not in inv and "ansible_host" not in inv, "pios host has no address/user in the file (config renders them)")


# Role defaults that are still literal levers owned by other tickets (swap packages: the VM overlay is still in
# inventory/vm.yml; remaps, per-game core options and overrides -> controls; hardening). Each moves to config/variables.d with its ticket and leaves this list.
PENDING_LEVERS = {"cabinet_swap_packages",
                  "cabinet_remaps", "cabinet_core_option_files", "cabinet_game_overrides", "cabinet_nosudo_users"}


def rule_no_hidden_knob(defaults, pending=PENDING_LEVERS):
    """#125: every default of the role is `{{ hk_* }}` (a pass-through to a declared lever), derived from one
    (references only hk_*/cabinet_*), or marked `# internal` on its key line. A planted literal is flagged."""
    p = []
    for m in re.finditer(r"(?m)^([a-z][a-z0-9_]*):(.*)$", defaults):
        name, rest = m.group(1), m.group(2)
        if name in pending or "# internal" in rest:
            continue
        if "{{ hk_" in rest or "{{ cabinet_" in rest:
            continue
        p.append("%s: a literal default that is not a lever (declare hk_* in config/variables.d, or mark # internal)" % name)
    return p


def test_no_hidden_knob():
    _, d = _role("cabinet_tweaks")
    role_only = _read("roles", "cabinet_tweaks", "defaults", "main.yml")
    _check(rule_no_hidden_knob(role_only) == [], "cabinet_tweaks: every default is {{ hk_* }} or # internal %s" % rule_no_hidden_knob(role_only))
    planted = role_only + "\ncabinet_attract_idle: 600000\n"
    _check(any("cabinet_attract_idle" in x for x in rule_no_hidden_knob(planted)), "BROKEN control: a planted literal default is flagged")
    _check(any("cabinet_kernel_4k" in x for x in rule_no_hidden_knob(role_only.replace('"{{ hk_kernel_4k }}"', "true"))),
           "BROKEN control: the kernel switch turned back into a literal is flagged")
    _check(any("cabinet_alt_emulators" in x for x in rule_no_hidden_knob(role_only.replace('"{{ hk_alt_emulators }}"', "{}"))),
           "BROKEN control: alt emulators turned back into a literal is flagged")
    levers = _config_text("display", "attract", "esde", "launch")
    for name in ("hk_kernel_4k", "hk_attract_timer_ms", "hk_attract_controls", "hk_attract_type", "hk_esde_settings",
                 "hk_alt_emulators", "hk_core_options", "hk_retroarch_cfg"):
        _check(re.search(r"(?m)^  %s:" % name, levers) is not None and name in role_only, "%s is declared and read by the role" % name)


def test_kernel_rules():
    t, d = _role("cabinet_tweaks")
    _check(rule_kernel(t, d) == [], "kernel tweak: guarded, idempotent line, backup, no auto reboot %s" % rule_kernel(t, d))
    h = _read("roles", "cabinet_tweaks", "handlers", "main.yml")
    _check(rule_handlers(h) == [], "reboot handler flags + prints, never reboots %s" % rule_handlers(h))


def test_retroarch_and_es_rules():
    t, d = _role("cabinet_tweaks")
    _check(rule_retroarch(d) == [], "retroarch cfg + core options %s" % rule_retroarch(d))
    r = rule_es_settings(t, d, _files("cabinet_tweaks"))
    _check(r == [], "es_settings: in place, only the 4 keys, no_log %s" % r)
    _check("XCURSOR_THEME=blank" in t and "MESA_GLSL_VERSION_OVERRIDE" in t,
           "hidden cursor exported after the MESA_GLSL line")


def rule_exit_hold(run):
    """#88: the maintainer taps Select+Start (RetroArch exits on a tap); a 0.6 s hold left him stuck in flycast."""
    m = re.search(r'flycast-exit-combo\.py "\$FPID" ([0-9.]+)', run)
    if not m:
        return ["exit watcher hold argument missing"]
    return [] if float(m.group(1)) <= 0.2 else ["exit hold %s s too long (must be a tap, <= 0.2 s)" % m.group(1)]


def test_dreamcast_rules():
    t, d = _role("dreamcast")
    _check(rule_dreamcast(t, d) == [], "dreamcast role content %s" % rule_dreamcast(t, d))
    run = _read("roles", "dreamcast", "templates", "flycast-run.sh.j2")
    _check("SDL_VIDEODRIVER=wayland" in run and "flycast-exit-combo.py" in run,
           "launcher sets wayland + starts exit watcher")
    combo = _read("roles", "dreamcast", "files", "flycast-exit-combo.py")
    _check("SELECT, START = 8, 9" in combo, "exit combo is Select=8 + Start=9")
    _check(rule_exit_hold(run) == [], "exit combo fires on a tap (hold <= 0.2 s, #88) %s" % rule_exit_hold(run))


def test_ship_no_artifacts():
    for role in ("dreamcast", "cabinet_tweaks"):
        r = rule_no_artifacts(_files(role))
        _check(r == [], "%s ships no binary/BIOS %s" % (role, r))


def test_es_xml_regexes_idempotent():
    """The two XML edits (replace module regexps) add once and never duplicate."""
    t, _ = _role("dreamcast")
    rules_rx = re.search(r"regexp: '(\\A\(\?!\[\\s\\S\]\*<emulator[^']*)'", t)
    gl_rx = re.search(r"regexp: '(\\A\(<\\\?xml[^']*)'", t)
    _check(bool(rules_rx and gl_rx), "both ES-DE regexps found in the role")
    if not (rules_rx and gl_rx):
        return
    rep1 = '\\g<1>  <emulator name="FLYCAST">\n  </emulator>\n\\g<2>'
    a = '<ruleList>\n  <emulator name="RETROARCH">\n  </emulator>\n</ruleList>\n'
    b = re.sub(rules_rx.group(1), rep1, a)
    _check(b.count('name="FLYCAST"') == 1 and 'name="RETROARCH"' in b, "find_rules: adds FLYCAST, keeps RETROARCH")
    _check(re.sub(rules_rx.group(1), rep1, b) == b, "find_rules: second run is a no-op")
    rep2 = "\\g<1>\n<alternativeEmulator>\n\t<label>X</label>\n</alternativeEmulator>\\g<2>"
    g = '<?xml version="1.0"?>\n<gameList>\n</gameList>\n'
    g2 = re.sub(gl_rx.group(1), rep2, g)
    _check(g2.count("<alternativeEmulator>") == 1 and g2.index("<alternativeEmulator>") < g2.index("<gameList>"),
           "gamelist: alternativeEmulator added before <gameList>")
    _check(re.sub(gl_rx.group(1), rep2, g2) == g2, "gamelist: second run is a no-op")


def _alt_regexes(tasks):
    ins = re.search(r"regexp: '(\\A\(<\\\?xml[^']*)'", tasks)
    rep = re.search(r"regexp: '(\(\\A<\\\?xml[^']*)'", tasks)
    return ins.group(1) if ins else None, rep.group(1) if rep else None


def _apply_pin(text, label, ins_rx, rep_rx):
    """Mimic the role: create if missing, insert if absent, replace a wrong label (Ansible replace = re.sub)."""
    if text is None:
        return '<?xml version="1.0"?>\n<alternativeEmulator>\n\t<label>%s</label>\n</alternativeEmulator>\n<gameList>\n</gameList>\n' % label
    text = re.sub(ins_rx, "\\g<1>\n<alternativeEmulator>\n\t<label>%s</label>\n</alternativeEmulator>\\g<2>" % label, text)
    rx = rep_rx.replace("{{ item.value.label | regex_escape }}", re.escape(label))
    return re.sub(rx, "\\g<1>%s\\g<2>" % label, text)


def test_alt_emulators_vars():
    t, d = _role("cabinet_tweaks")
    pins = parse_alt_emulators(d)
    _check(pins.get("nes", ("",))[0] == "Nestopia UE", "vars pin nes to Nestopia UE %s" % (pins.get("nes"),))
    _check(rule_alt_emulators(d) == [], "every pinned label exists in ES-DE 3.5 and its core is installed %s" % rule_alt_emulators(d))
    _check("ansible.builtin.assert" in t and "cabinet_alt_core_stat" in t, "task asserts the cores exist (fails loudly)")
    _check("force: false" in t, "missing gamelist is created with force: false (never overwrites)")


def test_alt_emulators_regexes():
    t, _ = _role("cabinet_tweaks")
    ins, rep = _alt_regexes(t)
    _check(bool(ins and rep), "insert + replace regexps found in the role")
    if not (ins and rep):
        return
    want = '<?xml version="1.0"?>\n<alternativeEmulator>\n\t<label>Nestopia UE</label>\n</alternativeEmulator>\n'
    games = '<gameList>\n\t<game><path>./a.nes</path><name>A &amp; B</name></game>\n</gameList>\n'
    created = _apply_pin(None, "Nestopia UE", ins, rep)
    _check(created == want + "<gameList>\n</gameList>\n", "missing gamelist created in the exact form")
    bare = '<?xml version="1.0"?>\n' + games
    once = _apply_pin(bare, "Nestopia UE", ins, rep)
    _check(once == want + games, "inserted once after the declaration, <gameList> untouched")
    _check(_apply_pin(once, "Nestopia UE", ins, rep) == once, "second run is a no-op")
    wrong = want.replace("Nestopia UE", "Mesen") + games
    fixed = _apply_pin(wrong, "Nestopia UE", ins, rep)
    _check(fixed == want + games and fixed.count("<alternativeEmulator>") == 1, "wrong label (Mesen) replaced, not duplicated")
    correct = want + games
    _check(_apply_pin(correct, "Nestopia UE", ins, rep) == correct, "correct existing label stays byte-identical")
    odd = '<?xml version="1.0" encoding="UTF-8"?>\r\n<alternativeEmulator>\r\n\t<label>FinalBurn Neo</label>\r\n</alternativeEmulator>\r\n' + games
    _check(_apply_pin(odd, "FinalBurn Neo", ins, rep) == odd, "existing correct block byte-identical (CRLF, encoding attr)")
    _check("alternativeEmulator" in _read("..", "skills", "collections", "collgen.py") and
           "alternativeEmulator" in _read("..", "skills", "collections", "tests", "test_names.py"),
           "collgen.py names keeps <alternativeEmulator> (covered by skills/collections/tests/test_names.py)")


# ---- deliberately-broken controls: the rules MUST flag these ----------------------------------
def test_broken_kernel_no_backup_no_guard():
    t, d = _role("cabinet_tweaks")
    bad_t = t.replace("backup: true", "").replace("when: cabinet_kernel_4k | bool", "")
    _check(len(rule_kernel(bad_t, d)) >= 2, "BROKEN control: kernel rule flags missing backup/guard")
    bad_t2 = t + "\n- ansible.builtin.reboot:\n"
    _check("automatic reboot forbidden" in rule_kernel(bad_t2, d), "BROKEN control: auto reboot flagged")


def test_broken_es_settings_templated():
    t, d = _role("cabinet_tweaks")
    bad = t + "\n- ansible.builtin.template:\n    src: es_settings.xml.j2\n    dest: /home/x/ES-DE/settings/es_settings.xml\n"
    _check("es_settings.xml templated/copied (password!)" in rule_es_settings(bad, d, []),
           "BROKEN control: templating es_settings.xml is flagged")
    _check(rule_no_artifacts(["dc_boot.bin", "flycast"]) != [], "BROKEN control: BIOS/binary in files flagged")
    bad_d = d.replace("CollectionCustomGrouping", "Nope")
    _check(rule_es_settings(t, bad_d, []) != [], "BROKEN control: missing ES key flagged")
    bad_ss = d.replace("default: 600000", "default: 90000")
    _check("ScreensaverTimer must be 600000 (10 min)" in rule_es_settings(t, bad_ss, []),
           "BROKEN control: 90 s attract-mode timer flagged")
    bad_fav = d.replace("FavoritesAddButton: false", "FavoritesAddButton: true")
    _check("FavoritesAddButton must be false (favorites off)" in rule_es_settings(t, bad_fav, []),
           "BROKEN control: favorites toggle left on is flagged")


def _es_task(tasks, name):
    """(start, end) of the cabinet_tweaks task called NAME."""
    i = tasks.index("- name: " + name)
    j = tasks.find("\n- ", i + 1)
    return i, (len(tasks) if j < 0 else j + 1)


def test_broken_es_settings_42():
    """#42 (Security): backup, fetch/slurp/shell readers, a dropped no_log or mode must all be flagged."""
    t, d = _role("cabinet_tweaks")
    _check(rule_es_settings_tasks(t) == [], "es_settings tasks: stat/lineinfile only, no_log + 0600 %s"
           % rule_es_settings_tasks(t))
    i, j = _es_task(t, "Set the ES-DE collection/startup keys")
    blk = t[i:j]
    backup = t[:i] + blk.replace('    mode: "0600"', '    mode: "0600"\n    backup: true', 1) + t[j:]
    _check("es_settings backup copy (the password would be copied)" in rule_es_settings_tasks(backup),
           "BROKEN control: backup: true on the es_settings lineinfile is flagged")
    nolog = t[:i] + blk.replace("  no_log: true", "  no_log: false", 1) + t[j:]
    _check("es_settings lineinfile without no_log" in rule_es_settings_tasks(nolog),
           "BROKEN control: dropped no_log is flagged")
    nomode = t[:i] + blk.replace('    mode: "0600"', '    mode: "0644"', 1) + t[j:]
    _check("es_settings lineinfile without mode 0600" in rule_es_settings_tasks(nomode),
           "BROKEN control: es_settings mode 0644 is flagged")
    for mod, arg in (("fetch", "src: \"{{ cabinet_es_settings }}\"\n    dest: /tmp/x"),
                     ("slurp", "src: \"{{ cabinet_es_settings }}\""),
                     ("shell", "cmd: cat {{ cabinet_es_settings }}"),
                     ("synchronize", "src: \"{{ cabinet_es_settings }}\"\n    dest: /tmp/x")):
        bad = t + "\n- name: planted\n  ansible.builtin.%s:\n    %s\n" % (mod, arg)
        r = rule_es_settings_tasks(bad)
        _check("es_settings touched by %s (only stat/lineinfile)" % mod in r and "es_settings read/copied by %s" % mod in r,
               "BROKEN control: %s of es_settings.xml is flagged" % mod)
    lit = t + "\n- name: planted literal\n  ansible.builtin.slurp:\n    src: /home/cab/ES-DE/settings/es_settings.xml\n"
    _check("es_settings read/copied by slurp" in rule_es_settings_tasks(lit),
           "BROKEN control: slurp of the literal es_settings.xml path is flagged")
    no_np = d.replace('key: netplay_public_announce, value: "false"', 'key: netplay_public_announce, value: "true"')
    _check("retroarch.cfg netplay_public_announce=false missing" in rule_retroarch(no_np),
           "BROKEN control: netplay_public_announce true is flagged")


def test_broken_dreamcast_window_640():
    t, d = _role("dreamcast")
    bad = d.replace('key: width, value: "1920"', 'key: width, value: "640"')
    _check(any("window.width" in x for x in rule_dreamcast(t, bad)),
           "BROKEN control: 640-wide flycast window (stretched 16:9) is flagged")


def test_broken_exit_hold_06():
    run = _read("roles", "dreamcast", "templates", "flycast-run.sh.j2").replace('"$FPID" 0.1', '"$FPID" 0.6')
    _check(rule_exit_hold(run) != [], "BROKEN control: 0.6 s exit hold (the maintainer stuck, #88) is flagged")


def test_broken_hotkey_select():
    _, d = _role("cabinet_tweaks")
    old = d.replace('input_enable_hotkey_btn, value: "9"', 'input_enable_hotkey_btn, value: "8"')
    _check(any("hotkey_btn=9" in x for x in rule_retroarch(old)),
           "BROKEN control: hotkey back on Select (8) is flagged")


def test_broken_alt_emulators():
    _, d = _role("cabinet_tweaks")
    mesen = d.replace('label: "Nestopia UE"', 'label: "Mesen"')
    _check(any("Mesen" in x for x in rule_alt_emulators(mesen)), "BROKEN control: nes pinned to Mesen is flagged")
    no_nes = re.sub(r"^\s+nes:.*\n", "", d, flags=re.M)
    _check("nes not pinned" in rule_alt_emulators(no_nes), "BROKEN control: missing nes entry is flagged")
    typo = d.replace('"PCSX ReARMed"', '"PCSX Rearmed"')
    _check(any("psx" in x and "not an ES-DE label" in x for x in rule_alt_emulators(typo)), "BROKEN control: unknown ES-DE label flagged")
    nocore = d.replace("fbneo_libretro.so", "fbneo_missing.so")
    _check(any("not installed" in x for x in rule_alt_emulators(nocore)), "BROKEN control: core not installed flagged")


# ---- remaps (#56, #57): semantic check, identity lines may be omitted (RetroArch drops them) ---------
REMAPS_DIR = os.path.join(ANSIBLE, "..", "skills", "controls", "remaps")
# RetroPad ids: a remap line btn_<name> = "<id>" with id == RETROPAD[name] is the identity (no-op).
RETROPAD = {"b": 0, "y": 1, "select": 2, "start": 3, "up": 4, "down": 5, "left": 6, "right": 7,
            "a": 8, "x": 9, "l": 10, "r": 11, "l2": 12, "r2": 13, "l3": 14, "r3": 15}
# The maintainer's Genesis / Sega CD panel (#56): LP MP HP = X Y Z, LK MK HK = A B C; player 2 = player 1.
GENESIS = {"y": 10, "x": 9, "l": 11, "b": 1, "a": 0, "r": 8}
EXPECTED_REMAPS = {
    ("Genesis Plus GX", "Genesis Plus GX.rmp"): {1: GENESIS, 2: GENESIS},
    ("PCSX-ReARMed", "PCSX-ReARMed.rmp"): {p: {"l": 11, "r": 13} for p in (1, 2)},
    ("FinalBurn Neo", "neogeo.rmp"): {p: {"y": 0, "x": 8, "l": 1, "b": 9, "a": 12} for p in (1, 2)},
}


def effective_map(text, player):
    """{button: target id} for one player: identity, overlaid with the file's btn lines."""
    out = dict(RETROPAD)
    for m in re.finditer(r'(?m)^input_player%d_btn_(\w+)\s*=\s*"(-?\d+)"' % player, text):
        out[m.group(1)] = int(m.group(2))
    return out


def rule_remap_semantics(text, want):
    """want = {player: {button: id}}; every other button must stay identity (or be absent)."""
    p = []
    for player, buttons in want.items():
        got = effective_map(text, player)
        exp = dict(RETROPAD, **buttons)
        for b in sorted(exp):
            if got.get(b) != exp[b]:
                p.append("player%d %s = %s, want %s" % (player, b, got.get(b), exp[b]))
    return p


def _yaml_list_block(defaults, name):
    """The '- {...}' lines of one top-level list in defaults (stops at the first line that isn't an item)."""
    m = re.search(r"^%s:\n((?:  - .*\n)+)" % re.escape(name), defaults, re.M)
    return m.group(1) if m else ""


def parse_cabinet_remaps(defaults):
    return re.findall(r'\{\s*core:\s*"([^"]+)",\s*file:\s*"([^"]+)"\s*\}', _yaml_list_block(defaults, "cabinet_remaps"))


def parse_cabinet_core_options(defaults):
    return re.findall(r'\{\s*core:\s*"([^"]+)",\s*file:\s*"([^"]+)"\s*\}', _yaml_list_block(defaults, "cabinet_core_option_files"))


def rule_core_options_wired(defaults, tasks, repo_files):
    """Per-game core options (#64): skills/controls/core-options/<core>/*.opt listed == shipped, copied to config/<core>/."""
    p = []
    listed = parse_cabinet_core_options(defaults)
    for cf in repo_files:
        if cf not in listed:
            p.append("%s/%s is in skills/controls/core-options but not in cabinet_core_option_files" % cf)
    for cf in listed:
        if cf not in repo_files:
            p.append("cabinet_core_option_files lists %s/%s but the file is missing" % cf)
    if 'loop: "{{ cabinet_core_option_files }}"' not in tasks or "/config/{{ item.core }}/{{ item.file }}" not in tasks:
        p.append("core options not copied to config/<core>/<file>")
    return p


def _repo_core_options():
    d0 = os.path.join(os.path.dirname(REMAPS_DIR), "core-options")
    out = []
    for core in sorted(os.listdir(d0)) if os.path.isdir(d0) else []:
        d = os.path.join(d0, core)
        if os.path.isdir(d):
            out += [(core, f) for f in sorted(os.listdir(d)) if f.endswith(".opt")]
    return out


# Written by the retroarch122 role instead (#122: two roles on one file flipped it every run).
OWNED_BY_RETROARCH122 = {("Mupen64Plus-Next", "Mupen64Plus-Next.rmp")}


def rule_remaps_wired(defaults, repo_files):
    """repo_files = [(core, file)] found in skills/controls/remaps. Listed == shipped, both ways."""
    p = []
    listed = parse_cabinet_remaps(defaults)
    for cf in repo_files:
        if cf in OWNED_BY_RETROARCH122:
            continue
        if cf not in listed:
            p.append("%s/%s is in skills/controls/remaps but not in cabinet_remaps" % cf)
    for cf in listed:
        if cf not in repo_files:
            p.append("cabinet_remaps lists %s/%s but the file is missing" % cf)
    for cf in EXPECTED_REMAPS:
        if cf not in listed:
            p.append("%s/%s not deployed" % cf)
    if "skills/controls/remaps" not in defaults:
        p.append("cabinet_remaps_src must point at skills/controls/remaps (single source)")
    return p


def rule_remap_tasks(tasks, defaults):
    p = []
    if not re.search(r'key: remap_save_on_exit, value: "false"', defaults):
        p.append("remap_save_on_exit must be false (RetroArch rewrites the remaps on exit)")
    if not re.search(r'src: "\{\{ cabinet_remaps_src \}\}/', tasks) or 'loop: "{{ cabinet_remaps }}"' not in tasks:
        p.append("remaps not copied from cabinet_remaps_src")
    guard = tasks.find("pgrep -f retroarch")
    first_ra = min([i for i in (tasks.find("cabinet_retroarch_dir"),) if i >= 0] or [len(tasks)])
    if guard < 0 or "cabinet_retroarch_running.rc == 1" not in tasks:
        p.append("no 'RetroArch not running' guard")
    elif guard > first_ra:
        p.append("RetroArch guard comes after a RetroArch file task")
    return p


def _repo_remaps():
    out = []
    for core in sorted(os.listdir(REMAPS_DIR)):
        d = os.path.join(REMAPS_DIR, core)
        if os.path.isdir(d):
            out += [(core, f) for f in sorted(os.listdir(d)) if f.endswith(".rmp")]
    return out


def _remap_text(core, f):
    with open(os.path.join(REMAPS_DIR, core, f), encoding="utf-8") as fh:
        return fh.read()


# What RetroArch 1.20 wrote back on exit (observed 2026-11-14): reformatted, identity x=9 dropped, extras.
GENESIS_RA_REWRITE = """input_libretro_device_p1 = "1"
input_player1_analog_dpad_mode = "0"
input_player1_btn_a = "0"
input_player1_btn_b = "1"
input_player1_btn_l = "11"
input_player1_btn_r = "8"
input_player1_btn_y = "10"
input_player2_btn_a = "0"
input_player2_btn_b = "1"
input_player2_btn_l = "11"
input_player2_btn_r = "8"
input_player2_btn_y = "10"
input_remap_port_p1 = "0"
input_turbo_enable = "true"
"""


def test_remaps_wired_and_semantic():
    t, d = _role("cabinet_tweaks")
    repo = _repo_remaps()
    _check(rule_remaps_wired(d, repo) == [], "every skills/controls/remaps file is deployed, from that single source %s" % rule_remaps_wired(d, repo))
    co = _repo_core_options()
    _check(co and rule_core_options_wired(d, t, co) == [], "every per-game core option is deployed to config/<core>/ %s" % rule_core_options_wired(d, t, co))
    for (core, f), want in sorted(EXPECTED_REMAPS.items()):
        if (core, f) in repo:
            r = rule_remap_semantics(_remap_text(core, f), want)
            _check(r == [], "%s/%s maps the panel as decided %s" % (core, f, r))
    g = _remap_text("Genesis Plus GX", "Genesis Plus GX.rmp")
    for pl in (1, 2):
        for b, v in sorted(GENESIS.items()):
            _check(re.search(r'(?m)^input_player%d_btn_%s = "%d"$' % (pl, b, v), g) is not None,
                   "Genesis line input_player%d_btn_%s = \"%d\"" % (pl, b, v))
    _check(rule_remap_semantics(GENESIS_RA_REWRITE, EXPECTED_REMAPS[("Genesis Plus GX", "Genesis Plus GX.rmp")]) == [],
           "RetroArch's exit rewrite (identity x=9 dropped) still counts as the same remap")
    _check(rule_remap_tasks(t, d) == [], "remaps: copied whole, remap_save_on_exit false, RetroArch-running guard first %s" % rule_remap_tasks(t, d))


def test_broken_remaps():
    t, d = _role("cabinet_tweaks")
    want = EXPECTED_REMAPS[("Genesis Plus GX", "Genesis Plus GX.rmp")]
    g = _remap_text("Genesis Plus GX", "Genesis Plus GX.rmp")
    swapped = g.replace('input_player1_btn_y = "10"', 'input_player1_btn_y = "TMP"') \
               .replace('input_player1_btn_l = "11"', 'input_player1_btn_l = "10"').replace('"TMP"', '"11"')
    _check(rule_remap_semantics(swapped, want) != [], "BROKEN control: swapped LP/HP pair (X<->Z) is flagged")
    p2 = g.replace('input_player2_btn_b = "1"', 'input_player2_btn_b = "0"')
    _check(any(x.startswith("player2") for x in rule_remap_semantics(p2, want)), "BROKEN control: player 2 drifted from player 1 is flagged")
    _check(rule_remap_semantics('input_player1_btn_y = "10"\n', want) != [], "BROKEN control: half-written remap (lines lost) is flagged")
    repo = _repo_remaps()
    missing = [cf for cf in repo if cf[0] != "Genesis Plus GX"]
    _check(any("missing" in x for x in rule_remaps_wired(d, missing)), "BROKEN control: listed remap file missing from the repo is flagged")
    unlisted = d.replace('{ core: "PCSX-ReARMed", file: "PCSX-ReARMed.rmp" }', "")
    _check(any("not in cabinet_remaps" in x for x in rule_remaps_wired(unlisted, repo)), "BROKEN control: remap in the skill but not deployed is flagged")
    co = _repo_core_options()
    no_opt = d.replace('{ core: "Mupen64Plus-Next", file: "Killer Instinct Gold.opt" }', "")
    _check(any("not in cabinet_core_option_files" in x for x in rule_core_options_wired(no_opt, t, co)), "BROKEN control: KI Gold .opt not deployed is flagged")
    to_remaps = t.replace("/config/{{ item.core }}/{{ item.file }}", "/config/remaps/{{ item.core }}/{{ item.file }}x")
    _check(any("not copied" in x for x in rule_core_options_wired(d, to_remaps, co)), "BROKEN control: .opt copied into remaps/ is flagged")
    on = d.replace('key: remap_save_on_exit, value: "false"', 'key: remap_save_on_exit, value: "true"')
    _check(any("remap_save_on_exit" in x for x in rule_remap_tasks(t, on)), "BROKEN control: remap_save_on_exit true is flagged")
    noguard = t.replace("pgrep -f retroarch", "true")
    _check(any("guard" in x for x in rule_remap_tasks(noguard, d)), "BROKEN control: no RetroArch-running guard is flagged")


# ---- zram swap (#52): OS default ensured --------------------------------------------------------
def rule_zram(tasks, defaults):
    p = []
    for pkg in ("rpi-swap", "systemd-zram-generator"):
        if not re.search(r"(?m)^cabinet_swap_packages:.*\b%s\b" % re.escape(pkg), defaults):
            p.append("package %s not ensured" % pkg)
    if "state: latest" in tasks:
        p.append("packages must be state: present (no upgrade)")
    # #129: the mechanism is the lever hk_swap.mechanism (config/variables.d/storage.yml); its default is what is pinned.
    if not re.search(r'(?m)^cabinet_swap_mechanism: "\{\{ hk_swap\.mechanism \}\}"\s*$', defaults):
        p.append("cabinet_swap_mechanism is not a pass-through to hk_swap.mechanism")
    m = re.search(r"hk_swap:\n(?:.*\n)*?    default: \{mechanism: (\S+?),", defaults)
    if not m or m.group(1) not in ("zram", "zram+file"):
        p.append("swap mechanism must be zram or zram+file, got %s" % (m.group(1) if m else None))
    if "Mechanism={{ cabinet_swap_mechanism }}" not in tasks or "dest: \"{{ cabinet_swap_dropin }}\"" not in tasks:
        p.append("rpi-swap drop-in not written")
    if "'/dev/zram' in cabinet_proc_swaps.stdout" not in tasks:
        p.append("no assert that zram swap is active")
    if re.search(r"ansible\.builtin\.(reboot|systemd)\b", tasks):
        p.append("zram must not reboot or restart units")
    return p


def _storage_cfg():
    return "\n" + _read("..", "config", "variables.d", "storage.yml")


def test_zram_rules():
    t, d = _role("cabinet_tweaks")
    d += _storage_cfg()
    _check(rule_zram(t, d) == [], "zram: packages present, drop-in pins the mechanism (zram, RAM only), assert active, no reboot %s" % rule_zram(t, d))


def test_hardening():
    t, d = _role("cabinet_tweaks")
    r = rule_hardening(t, d)
    _check(r == [], "pi out of sudo, apt no cache, no unattended upgrades %s" % r)


def test_broken_hardening():
    t, d = _role("cabinet_tweaks")
    _check("user groups not exact (append must be false)" in rule_hardening(t.replace("    append: false\n", "    append: true\n"), d),
           "BROKEN control: append true (sudo never removed) is flagged")
    _check("sudo not removed from the groups" in rule_hardening(t.replace("reject('equalto', 'sudo') | ", ""), d),
           "BROKEN control: groups that keep sudo are flagged")
    _check("pi not in cabinet_nosudo_users" in rule_hardening(t, d.replace("cabinet_nosudo_users: [pi]", "cabinet_nosudo_users: []")),
           "BROKEN control: pi dropped from the list is flagged")
    _check(any("apt cache" in x for x in rule_hardening(t.replace('Keep-Downloaded-Packages "false"', 'Keep-Downloaded-Packages "true"'), d)),
           "BROKEN control: apt cache kept is flagged")
    planted = t + "\n- name: planted\n  ansible.builtin.apt:\n    name: unattended-upgrades\n"
    _check("unattended upgrades must stay off" in rule_hardening(planted, d), "BROKEN control: unattended-upgrades install is flagged")


def test_sudo_timeout():
    t, d = _role("cabinet_tweaks")
    r = rule_sudo_timeout(t, d)
    _check(r == [], "#93: sudo caches no credential for the cabinet user, visudo-validated, proven on apply %s" % r)


def test_sudo_timeout_line_passes_visudo():
    import shutil
    import subprocess
    import tempfile
    visudo = shutil.which("visudo") or next((x for x in ("/usr/sbin/visudo",) if os.path.exists(x)), None)
    if not visudo:
        skip("no visudo on this host: the rendered sudo timeout line is not syntax-checked here")
        return
    t, _ = _role("cabinet_tweaks")
    m = re.search(r"(?m)^      (Defaults:\{\{ cabinet_user \}\} timestamp_timeout=0)\s*$", t)
    line = m.group(1).replace("{{ cabinet_user }}", "coindojo") if m else ""
    def ok(text):
        with tempfile.NamedTemporaryFile("w", suffix="-sudoers", delete=False) as fh:
            fh.write(text + "\n")
        try:
            return subprocess.run([visudo, "-cf", fh.name], capture_output=True).returncode == 0
        finally:
            os.unlink(fh.name)
    _check(bool(line) and ok(line), "the rendered drop-in passes visudo -c: %r" % line)
    _check(not ok("Defaults:coindojo timestamp_timeout=zero"), "BROKEN control: visudo rejects a bad timeout value")


def test_broken_sudo_timeout():
    t, d = _role("cabinet_tweaks")
    _check("drop-in does not set Defaults:<cabinet user> timestamp_timeout=0" in rule_sudo_timeout(
        t.replace("timestamp_timeout=0", "timestamp_timeout=15"), d), "BROKEN control: a 15 min timeout is flagged")
    _check("sudo timeout drop-in not validated by visudo" in rule_sudo_timeout(
        t.replace("    validate: /usr/sbin/visudo -cf %s\n", ""), d), "BROKEN control: no visudo validate is flagged")
    _check(any("dot" in x for x in rule_sudo_timeout(
        t, d.replace("/etc/sudoers.d/coin-dojo-sudo-timeout", "/etc/sudoers.d/coin-dojo-sudo.conf"))),
        "BROKEN control: a file name with a dot (sudo skips it) is flagged")
    _check("no apply-time proof that `sudo -n true` is refused" in rule_sudo_timeout(
        t.replace("failed_when: cabinet_sudo_cached.rc == 0", "failed_when: false"), d),
        "BROKEN control: a proof that never fails is flagged")


def test_broken_zram():
    t, d = _role("cabinet_tweaks")
    d += _storage_cfg()
    no_pkg = re.sub(r"(?m)^cabinet_swap_packages:.*$", "cabinet_swap_packages: [rpi-swap]", d)
    _check("package systemd-zram-generator not ensured" in rule_zram(t, no_pkg), "BROKEN control: zram generator package not ensured is flagged")
    off = d.replace("default: {mechanism: zram,", "default: {mechanism: swapfile,")
    _check(any("mechanism" in x for x in rule_zram(t, off)), "BROKEN control: swapfile-only (zram off) is flagged")
    lit = d.replace('cabinet_swap_mechanism: "{{ hk_swap.mechanism }}"', "cabinet_swap_mechanism: zram")
    _check(any("pass-through" in x for x in rule_zram(t, lit)), "BROKEN control: a literal swap mechanism (not the lever) is flagged")
    no_assert = t.replace("'/dev/zram' in cabinet_proc_swaps.stdout", "true")
    _check("no assert that zram swap is active" in rule_zram(no_assert, d), "BROKEN control: zram not asserted active is flagged")
    _check(rule_zram("", "") != [], "BROKEN control: role without zram at all is flagged")


if __name__ == "__main__":
    main(globals())
