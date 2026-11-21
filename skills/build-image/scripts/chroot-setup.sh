#!/bin/bash
# Runs INSIDE the arm64 Pi OS chroot. Builds the BASE of the Coin Dojo arcade OS onto a
# 3-partition image (boot / root / data): packages, ES-DE, the user, boot-to-frontend kiosk,
# the cabinet's global controller profile, de-bloat, SSH, first boot. Everything a deploy
# manages (cores, ROM folders, swap, session, SETTINGS, theme, power, Fightcade, ...) comes
# next from the Ansible roles themselves (provision.sh runs ansible/image.yml, #244): this
# script keeps no list of its own.
# Data (ROMs/BIOS/saves/media) lives on /data (p3) so OS reflashes keep it.
#
# No secrets (#237): the image ships with no user password, no SSH key, no Wi-Fi and no SSH host keys.
# They come at first boot from /boot/firmware/coindojo.yml (coindojo-firstboot, written by build.sh from
# CONFIG= or the Keychain flow) or from Raspberry Pi Imager's userconf.txt. The one exception, set after this script
# by build.sh: a community image without a configured password gets the default login password 12345 (#265).
# Opt-in: COIN_DOJO_SSH_PASSWORD=yes allows password SSH (default: key-only).
# Optional offline cache at /tmp/cache (ES-DE_aarch64.AppImage, glsl-shaders-<GLSL_COMMIT>.tar.gz).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
# The image's user and hostname arrive from run.sh (hk_cabinet_user, hk_cabinet_hostname); the defaults are the declared ones.
USER_NAME=${HK_CABINET_USER:-coindojo}
HOME_DIR=/home/$USER_NAME
CACHE=/tmp/cache
LOG(){ echo ">> $*"; }

LOG "apt stack"
apt-get update -qq
apt-get install -y --no-install-recommends \
  retroarch libretro-core-info \
  cage seatd \
  libgl1-mesa-dri libgles2 libegl1 mesa-vulkan-drivers \
  unzip curl ca-certificates fonts-dejavu-core \
  alsa-utils pulseaudio pulseaudio-utils rtkit \
  cloud-guest-utils e2fsprogs python3 openssh-client sudo rsync polkitd >/dev/null
# Swap is NOT set up here: role cabinet_tweaks pins Pi OS's own zram (rpi-swap). rsync: role esde_theme syncs the
# theme with it; polkitd: the roles' NetworkManager/time zone rules (rules.d) need it.
LOG "stack installed"

# ---- user ----
# The cabinet user gets uid hk_cabinet_uid (HK_CABINET_UID), the number the roles render into /run/user/<uid>
# (session, flycast, Fightcade, powerd, trophiesd). Pi OS trixie ships its own `pi` at uid 1000: a cabinet flashed
# with Imager renames that first user, so the image removes the stock pi and gives its uid to the cabinet user (#244).
USER_UID=${HK_CABINET_UID:-1000}
case "$USER_UID" in ''|*[!0-9]*) echo "ERROR: HK_CABINET_UID='$USER_UID' is not a number" >&2; exit 2 ;; esac
if [ "$USER_NAME" != pi ] && id pi >/dev/null 2>&1; then
  LOG "remove the stock pi user (uid $(id -u pi))"
  userdel -r pi >/dev/null 2>&1 || ! id pi >/dev/null 2>&1 || userdel pi
  ! getent group pi >/dev/null 2>&1 || groupdel pi
  rm -f /etc/sudoers.d/010_pi-nopasswd   # Pi OS's passwordless sudo for pi: no pi, no rule
fi
if id "$USER_NAME" >/dev/null 2>&1; then
  [ "$(id -u "$USER_NAME")" = "$USER_UID" ] \
    || { echo "ERROR: $USER_NAME already exists with uid $(id -u "$USER_NAME"), hk_cabinet_uid is $USER_UID" >&2; exit 2; }
else
  OWNER=$(getent passwd "$USER_UID" | cut -d: -f1 || true)
  [ -z "$OWNER" ] || { echo "ERROR: uid $USER_UID (hk_cabinet_uid) is taken by $OWNER in the base image" >&2; exit 2; }
  LOG "create user $USER_NAME (uid $USER_UID)"
  getent group "$USER_NAME" >/dev/null 2>&1 || groupadd -g "$USER_UID" "$USER_NAME" 2>/dev/null || groupadd "$USER_NAME"
  useradd -m -s /bin/bash -u "$USER_UID" -g "$USER_NAME" \
    -G video,render,audio,input,tty,plugdev,netdev,dialout,sudo "$USER_NAME"
fi
for g in _seatd seatd seat; do getent group "$g" >/dev/null 2>&1 && usermod -aG "$g" "$USER_NAME" || true; done
systemctl enable seatd 2>/dev/null || true
# Locked here (#237); first boot sets the password (coindojo.yml / userconf.txt). A community image without one
# gets the default 12345 from build.sh afterwards (#265, coindojo-firstboot --default-login).
passwd -l "$USER_NAME" >/dev/null
mkdir -p /etc/coindojo; echo "$USER_NAME" > /etc/coindojo/built-user
systemctl disable userconfig.service 2>/dev/null || true
# ---- hardening: root's password is locked (#168, #240). The stock pi (sudo, passwordless sudoers rule) is removed by
# the user block above (#244), so the old "pi leaves sudo, pi locked" step is gone; verify.sh still checks both. ----
passwd -l root >/dev/null 2>&1 || true
# ---- end hardening ----
rm -f /etc/systemd/system/multi-user.target.wants/userconfig.service /boot/firmware/userconf.txt 2>/dev/null || true

# ---- ssh ----
LOG "ssh (key-only; the key arrives at first boot)"
mkdir -p "$HOME_DIR/.ssh"; chmod 700 "$HOME_DIR/.ssh"; rm -f "$HOME_DIR/.ssh/authorized_keys"
systemctl enable ssh
# Key-only by default (#41): sshd keeps the FIRST value it reads and the Include sits at the top of
# sshd_config, so this 00- drop-in wins over every later one. The password (set at first boot) stays for
# sudo and the local console only. Password SSH is an explicit, documented opt-in:
# COIN_DOJO_SSH_PASSWORD=yes (see SKILL.md "SSH access"); never the default.
mkdir -p /etc/ssh/sshd_config.d
rm -f /etc/ssh/sshd_config.d/99-arcade.conf   # legacy drop-in (PasswordAuthentication yes)
if [ "${COIN_DOJO_SSH_PASSWORD:-no}" = "yes" ]; then
  LOG "WARNING: COIN_DOJO_SSH_PASSWORD=yes -> password SSH login ENABLED (opt-in)"
  SSH_PW_AUTH=yes
else
  SSH_PW_AUTH=no
fi
cat > /etc/ssh/sshd_config.d/00-coin-dojo-keyonly.conf <<SSHD
# coin-dojo (#41): SSH is key-only. Managed by skills/build-image and the sshd_keyonly role.
PasswordAuthentication $SSH_PW_AUTH
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PermitRootLogin no
PubkeyAuthentication yes
SSHD
chmod 0644 /etc/ssh/sshd_config.d/00-coin-dojo-keyonly.conf
echo "${HK_CABINET_HOSTNAME:-coin-dojo}" > /etc/hostname
grep -q "${HK_CABINET_HOSTNAME:-coin-dojo}" /etc/hosts || echo "127.0.1.1 ${HK_CABINET_HOSTNAME:-coin-dojo}" >> /etc/hosts

# ---- data partition mount (/data = p3, by label) ----
LOG "/data fstab"
mkdir -p /data
grep -q "coin-dojo-data" /etc/fstab || \
  echo 'LABEL=coin-dojo-data  /data  ext4  defaults,noatime,nofail  0  2' >> /etc/fstab

# ---- ES-DE (cache or download) ----
LOG "ES-DE"
mkdir -p /opt/ES-DE
if [ -f "$CACHE/ES-DE_aarch64.AppImage" ]; then
  cp "$CACHE/ES-DE_aarch64.AppImage" /opt/ES-DE/
else
  curl -fL --retry 3 -o /opt/ES-DE/ES-DE_aarch64.AppImage https://packages.es-de.org/release/3.5.0/ES-DE_aarch64.AppImage
fi
chmod +x /opt/ES-DE/ES-DE_aarch64.AppImage
( cd /opt/ES-DE && ./ES-DE_aarch64.AppImage --appimage-extract >/dev/null )
printf '#!/bin/bash\nexec /opt/ES-DE/squashfs-root/AppRun "$@"\n' > /usr/local/bin/es-de
chmod +x /usr/local/bin/es-de

# ---- cores: NOT here (#244). Role `cores` installs exactly the ones launch.py runs (its CORE_BY_SYSTEM). ----
CORES=/usr/lib/aarch64-linux-gnu/libretro; mkdir -p "$CORES"

# ---- RetroArch config (BIOS/saves on /data) ----
LOG "retroarch.cfg"
RA="$HOME_DIR/.config/retroarch"
mkdir -p "$RA/autoconfig/udev" "$RA/shaders" "$RA/overlays"
cat > "$RA/retroarch.cfg" <<CFG
libretro_directory = "$CORES"
libretro_info_path = "/usr/share/libretro/info"
system_directory = "/data/bios"
savefile_directory = "/data/saves"
savestate_directory = "/data/states"
joypad_autoconfig_dir = "$RA/autoconfig"
input_overlay_directory = "$RA/overlays"
video_shader_dir = "$RA/shaders"
video_driver = "gl"
video_fullscreen = "true"
video_smooth = "false"
# Fit games to the cabinet's centered 4:3 bezel opening (x 240..1680 of 1920x1080).
# Verified on hardware in a CPS3 fighter: fills the bezel exactly, no overscan hacks needed.
video_force_aspect = "true"
video_aspect_ratio = "1.333300"
aspect_ratio_index = "22"
custom_viewport_width = "1440"
custom_viewport_height = "1080"
custom_viewport_x = "240"
custom_viewport_y = "0"
video_scale_integer = "false"
audio_driver = "pulse"
# Never advertise a hosted netplay session on the public lobby (#42 LOW-8); LAN/direct netplay still works.
netplay_public_announce = "false"
# RetroArch menu must also land inside the bezel. Ozone/XMB render across the full
# 1920 panel and overflow the bezel. RGUI with aspect_ratio_lock=0 (None) is the one
# mode that makes the quick menu INHERIT the game's custom viewport (240..1680) instead
# of computing its own rect (which mis-centers under cage's non-1:1 surface). Verified
# on hardware: menu fills the bezel, right border + help line visible. rgui_aspect_ratio
# 0 = 4:3. See esde/retroarch bezel notes.
menu_driver = "rgui"
rgui_aspect_ratio = "0"
rgui_aspect_ratio_lock = "0"
menu_rgui_full_width_layout = "true"
input_joypad_driver = "udev"
input_max_users = "4"
video_threaded = "true"
rewind_enable = "false"
video_shader_enable = "true"
video_shader = "$RA/shaders/shaders_glsl/crt/crt-geom.glslp"
CFG

# ---- light CRT shader (GLSL, for the GL driver) + per-core 5-star tuning (Pi5/2GB) ----
# See docs/reference/iconic-look-crt-bezel-shaders.md + emulator-modes-and-flags.md.
LOG "shaders + core options (2GB tuning)"
# libretro/glsl-shaders at the commit the retroarch122 role pins (retroarch122_shader_commit, #277): the shipped
# tree is known exactly (licence manifest: skills/build-image/licences/components.json, id glsl-shaders). The tarball
# is sha256-checked (codeload archive of that commit, read 2026-11-20); a mismatch ships no shaders, never other ones.
GLSL_COMMIT="a35069864b4bf30f8c8e740d57206543fb7e745e"
GLSL_SHA256="c09ae526ed76241be0264a8b526333b6448bd6f2d5f0b17b3f6e6b7f326d470a"
GLSL_URL="https://codeload.github.com/libretro/glsl-shaders/tar.gz/$GLSL_COMMIT"
GLSL_TGZ=/tmp/glsl-shaders.tar.gz
if [ -f "$CACHE/glsl-shaders-$GLSL_COMMIT.tar.gz" ]; then
  cp "$CACHE/glsl-shaders-$GLSL_COMMIT.tar.gz" "$GLSL_TGZ"
else
  curl -fL --retry 3 -o "$GLSL_TGZ" "$GLSL_URL" || LOG "  WARN glsl-shaders download failed (set shader on-device)"
fi
if [ -f "$GLSL_TGZ" ] && echo "$GLSL_SHA256  $GLSL_TGZ" | sha256sum -c --quiet -; then
  mkdir -p "$RA/shaders/shaders_glsl"
  tar -xzf "$GLSL_TGZ" -C "$RA/shaders/shaders_glsl" --strip-components=1
elif [ -f "$GLSL_TGZ" ]; then
  LOG "  WARN glsl-shaders tarball is not the pinned $GLSL_SHA256: not installed"
fi
rm -f "$GLSL_TGZ"
cat > "$RA/retroarch-core-options.cfg" <<'OPTS'
flycast_internal_resolution = "640x480"
flycast_threaded_rendering = "enabled"
flycast_cable_type = "VGA (RGB)"
flycast_region = "USA"
flycast_framerate = "normal"
pcsx_rearmed_neon_enhancement_enable = "disabled"
pcsx_rearmed_frameskip = "0"
ppsspp_internal_resolution = "480x272"
ppsspp_cpu_core = "JIT"
ppsspp_frameskip = "0"
mame2003-plus_skip_disclaimer = "enabled"
mame2003-plus_skip_warnings = "enabled"
fbneo-frameskip = "0"
fbneo-cpu-speed-adjust = "100"
OPTS

# ---- 2GB RAM headroom: zram swap comes from Pi OS's rpi-swap, pinned by role cabinet_tweaks (#52, #244) ----

# ---- GLOBAL controller profile (measured on the cabinet: "USB gamepad") ----
LOG "controller profile"
cat > "$RA/autoconfig/udev/USB gamepad.cfg" <<'PAD'
input_driver = "udev"
input_device = "USB gamepad"
input_vendor_id = "2079"
input_product_id = "58369"
input_up_btn = "h0up"
input_down_btn = "h0down"
input_left_btn = "h0left"
input_right_btn = "h0right"
input_up_axis = "-1"
input_down_axis = "+1"
input_left_axis = "-0"
input_right_axis = "+0"
# Physical panel wiring captured live from the cabinet stick (js0), verified in
# CPS3 six-button fighter: JAB=4 STRONG=0 FIERCE=5 SHORT=3 FORWARD=2 ROUNDHOUSE=1.
# Maps the 6-button fight panel to RetroPad Y/X/L (punches) + B/A/R (kicks).
input_y_btn = "4"
input_x_btn = "0"
input_l_btn = "5"
input_b_btn = "3"
input_a_btn = "2"
input_r_btn = "1"
input_select_btn = "8"
input_start_btn = "9"
input_enable_hotkey_btn = "8"
input_menu_toggle_btn = "9"
input_exit_emulator_btn = "5"
PAD

# ---- audio: HDMI -> monitor -> aux -> speakers (standard PulseAudio combine-sink) ----
# Pi 5 has no analog jack; sound rides HDMI. Merge BOTH HDMI sinks into "combined"
# so audio plays whichever port the monitor is on (measured: the panel takes audio over HDMI).
LOG "audio (HDMI combine-sink)"
mkdir -p /etc/pulse/default.pa.d
cat > /etc/pulse/default.pa.d/combine-sink.pa <<'PA'
load-module module-combine-sink sink_name=combined
set-default-sink combined
PA

# ---- ES-DE settings (ROMs/media on /data) ----
LOG "ES-DE settings"
ESDE_HOME="$HOME_DIR/ES-DE"
mkdir -p "$ESDE_HOME/settings" "$ESDE_HOME/custom_systems"
cat > "$ESDE_HOME/settings/es_settings.xml" <<ESS
<?xml version="1.0"?>
<string name="ROMDirectory" value="/data/roms" />
<string name="MediaDirectory" value="/data/media" />
<string name="StartupSystem" value="" />
<bool name="ScreensaverControls" value="true" />
<string name="ScreensaverType" value="video" />
<int name="ScreensaverTimer" value="90000" />
<string name="ThemeAspectRatio" value="4:3" />
ESS
cat > "$ESDE_HOME/settings/es_controllers.txt" <<'SDL'
0300cb231f08000001e4000010010000,USB gamepad,a:b3,b:b2,x:b0,y:b4,leftshoulder:b5,rightshoulder:b1,back:b8,start:b9,dpup:h0.1,dpdown:h0.4,dpleft:h0.8,dpright:h0.2,leftx:a0,lefty:a1,platform:Linux,
SDL
cat > "$ESDE_HOME/custom_systems/es_find_rules.xml" <<'FR'
<?xml version="1.0"?>
<ruleList>
  <emulator name="RETROARCH">
    <rule type="systempath"><entry>retroarch</entry></rule>
    <rule type="staticpath"><entry>/usr/bin/retroarch</entry></rule>
  </emulator>
  <core name="RETROARCH">
    <rule type="corepath"><entry>/usr/lib/aarch64-linux-gnu/libretro</entry></rule>
  </core>
</ruleList>
FR

# ---- kiosk: autologin tty1 -> cage -> ES-DE ----
LOG "kiosk autostart"
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf <<AL
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $USER_NAME --noclear %I \$TERM
AL
# The autologin drop-in is inert unless getty@tty1 is actually wired into
# getty.target; without this the cabinet boots to a blank console (no frontend).
systemctl enable getty@tty1.service 2>/dev/null \
  || ln -sf /usr/lib/systemd/system/getty@.service \
            /etc/systemd/system/getty.target.wants/getty@tty1.service
# The cabinet's panel map (#244: = dreamcast_sdl_gamecontrollerconfig, LK = accept, MK = back; flycast's pad
# mapping files are written for exactly this line, tests/test_image_play.py keeps the two equal).
SDLCFG='0300cb231f08000001e4000010010000,USB gamepad,a:b3,b:b2,x:b0,y:b4,leftshoulder:b5,rightshoulder:b1,back:b8,start:b9,dpup:h0.1,dpdown:h0.4,dpleft:h0.8,dpright:h0.2,leftx:a0,lefty:a1,platform:Linux,'
cat > "$HOME_DIR/.bash_profile" <<PROF
if [ "\$(tty)" = "/dev/tty1" ] && [ -z "\${WAYLAND_DISPLAY:-}" ]; then
  # No first-boot config yet (#237): show coindojo-firstboot's hint on screen before the frontend starts.
  if [ -f /run/coindojo/no-config ]; then clear; cat /run/coindojo/no-config /run/coindojo/default-login 2>/dev/null; sleep 15
  # The community default login is still set (#265): say so, and how to change it, until it is changed.
  elif [ -f /run/coindojo/default-login ]; then clear; cat /run/coindojo/default-login; sleep 5; fi
  export SDL_GAMECONTROLLERCONFIG="$SDLCFG"
  # Pi 5 Mesa v3d caps desktop GL at 3.1; ES-DE's AppImage requests a 3.3 core
  # context and fails with EGL_BAD_MATCH. Advertise 3.3 so its renderer starts.
  export MESA_GL_VERSION_OVERRIDE=3.3COMPAT
  export MESA_GLSL_VERSION_OVERRIDE=330
  pulseaudio --start >/dev/null 2>&1 || true   # HDMI combine-sink for speaker audio
  # The cabinet's physical bezel crops the 1920x1080 panel to a centered 4:3
  # (1440x1080 at x=240). cage can't letterbox, so ES-DE letterboxes ITSELF:
  # --fullscreen-padding renders the UI into a centered 1440x1080 box with black
  # bars (NOT scaled -> no stretch), landing exactly in the bezel opening. This
  # frames every theme and view, unlike ThemeAspectRatio which still spans full width.
  exec cage -d -- /usr/local/bin/es-de --resolution 1440 1080 --fullscreen-padding 1
fi
PROF

# ---- firstboot: expand data partition ----
LOG "firstboot service"
install -m 0755 /tmp/firstboot-expand.sh /usr/local/sbin/coin-dojo-firstboot.sh
cat > /etc/systemd/system/coin-dojo-firstboot.service <<'UNIT'
[Unit]
Description=Coin Dojo first-boot: expand data partition
ConditionPathExists=/var/lib/coin-dojo-firstboot-pending
DefaultDependencies=no
After=local-fs.target
Before=getty@tty1.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/coin-dojo-firstboot.sh
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
UNIT
systemctl enable coin-dojo-firstboot.service
touch /var/lib/coin-dojo-firstboot-pending

# ---- first-boot config: /boot/firmware/coindojo.yml (#237) ----
# Every boot, before getty/ssh/NetworkManager: apply coindojo.yml (or Imager's userconf.txt) once, scrub its
# secrets, make SSH host keys if missing; with no config at all, leave the on-screen hint for .bash_profile.
LOG "coindojo first-boot config"
install -m 0755 /tmp/coindojo-firstboot.py /usr/local/sbin/coindojo-firstboot
cat > /etc/systemd/system/coindojo-firstboot.service <<'UNIT'
[Unit]
Description=Coin Dojo first boot: apply /boot/firmware/coindojo.yml once, then scrub its secrets
DefaultDependencies=no
After=local-fs.target systemd-remount-fs.service coin-dojo-firstboot.service
Before=getty@tty1.service ssh.service NetworkManager.service multi-user.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/coindojo-firstboot --apply
StandardOutput=journal+console
[Install]
WantedBy=multi-user.target
UNIT
systemctl enable coindojo-firstboot.service
# passwd rewrites /etc/shadow: this path unit ends the default-login reminder right away (same signal as role
# share's coin-dojo-share-sync.path, #266: /etc/coindojo/default-password vs /etc/shadow). It NEVER grants sudo
# (#270): only a boot-partition password applied by coindojo-firstboot --apply does.
cat > /etc/systemd/system/coindojo-password.service <<'UNIT'
[Unit]
Description=Coin Dojo: end the default-login reminder once the password is changed (never sudo)
After=local-fs.target coindojo-firstboot.service
# #270 L6: every passwd/chpasswd rewrites /etc/shadow; a burst must never hit the start limit and stop the unit
StartLimitIntervalSec=0
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/coindojo-firstboot --password-changed
UNIT
cat > /etc/systemd/system/coindojo-password.path <<'UNIT'
[Unit]
Description=Coin Dojo: watch the cabinet user's password (/etc/shadow)
[Path]
PathChanged=/etc/shadow
Unit=coindojo-password.service
[Install]
WantedBy=multi-user.target
UNIT
systemctl enable coindojo-password.path
install -m 0644 /tmp/coindojo.example.yml /boot/firmware/coindojo.example.yml

# ---- WiFi: Ethernet-first, optional boot-partition wifi.conf for wireless ----
# Ethernet needs zero config. For WiFi, the user edits /boot/firmware/wifi.conf
# (the FAT boot partition, editable on any Mac/PC after flashing) with THEIR own
# SSID+password; an every-boot service imports it into NetworkManager and scrubs
# the plaintext. The image ships wifi.conf EMPTY -- no credentials are baked in.
LOG "wifi import service"
install -m 0755 /tmp/wifi-import.sh /usr/local/sbin/coin-dojo-wifi.sh
cat > /etc/systemd/system/coin-dojo-wifi.service <<'UNIT'
[Unit]
Description=Coin Dojo: import user WiFi from boot-partition wifi.conf
After=NetworkManager.service
Wants=NetworkManager.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/coin-dojo-wifi.sh
[Install]
WantedBy=multi-user.target
UNIT
systemctl enable coin-dojo-wifi.service
# Ship the EMPTY template on the FAT boot partition (no secrets).
cat > /boot/firmware/wifi.conf <<'TPL'
# Coin Dojo WiFi setup (OPTIONAL -- plug in Ethernet and you can skip this).
#
# To connect over WiFi instead:
#   1. Fill in your network name (SSID) and password (PSK) below.
#   2. Save this file and put the SD card back in the cabinet.
#   3. Power on. Your WiFi is imported on boot and these lines are cleared
#      (your password is moved into the OS, not left here in plain text).
#
# WPA/WPA2 personal networks only. For an open network, leave PSK blank.
SSID=
PSK=
TPL

# ---- boot: 1080p + strip stock p2 resize (data is last now) ----
LOG "cmdline/config (1080p, disable stock resize)"
CMD=/boot/firmware/cmdline.txt
if [ -f "$CMD" ]; then
  sed -i 's# init=/usr/lib/raspi-config/init_resize.sh##g; s# resize##g' "$CMD"
  # both HDMI ports @1080p (either port can carry the panel) + hide the console cursor
  grep -q "video=HDMI-A-1" "$CMD" || sed -i 's/$/ video=HDMI-A-1:1920x1080@60 video=HDMI-A-2:1920x1080@60 vt.global_cursor_default=0/' "$CMD"
  # WiFi regulatory domain: without a country set, the Pi's brcmfmac radio stays
  # rfkill soft-blocked (wlan0 "unavailable") and never associates. Setting the
  # regdom on the cmdline is what raspi-config do_wifi_country persists.
  grep -q "cfg80211.ieee80211_regdom" "$CMD" || sed -i 's/$/ cfg80211.ieee80211_regdom=US/' "$CMD"
fi
# Make sure NetworkManager starts with the radio on (don't ship a disabled-wifi state).
mkdir -p /var/lib/NetworkManager
cat > /var/lib/NetworkManager/NetworkManager.state <<'NMSTATE'
[main]
NetworkingEnabled=true
WirelessEnabled=true
WWANEnabled=true
NMSTATE
for u in rpi-resizerootfs resize2fs_once rpiwiggle; do systemctl disable "$u" 2>/dev/null || true; done
CFGTXT=/boot/firmware/config.txt
if [ -f "$CFGTXT" ] && ! grep -q "coin-dojo-1080p" "$CFGTXT"; then
  printf '\n# coin-dojo-1080p (RTD2662 panel; audio rides HDMI to the monitor)\nhdmi_force_hotplug=1\ndisable_fw_kms_setup=1\ndisable_overscan=1\n' >> "$CFGTXT"
fi

# ---- de-bloat ----
LOG "de-bloat"
systemctl mask apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
systemctl disable rpi-eeprom-update 2>/dev/null || true
apt-get -y purge unattended-upgrades >/dev/null 2>&1 || true
# cloud-init ships in Pi OS trixie Lite and adds ~1-2 min of datasource-search delay to a
# headless boot (the "cloud-init stage" screen). We provision the image ourselves, so kill it.
touch /etc/cloud/cloud-init.disabled 2>/dev/null || true
systemctl mask cloud-init.service cloud-init-local.service cloud-init-network.service \
  cloud-init-main.service cloud-config.service cloud-final.service cloud-init.target 2>/dev/null || true

# No SSH host keys in the image (every card would share them): coindojo-firstboot makes them at first boot.
rm -f /etc/ssh/ssh_host_*

chown -R "$USER_NAME:$USER_NAME" "$HOME_DIR"
LOG "BUILD_OK"
