#!/bin/bash
# Structural verify of a built Coin Dojo image (3 partitions). Auto-detects offsets.
set -uo pipefail
IMG=${1:?image}
CU=${HK_CABINET_USER:-coindojo}   # the image's user (hk_cabinet_user, from run.sh)
command -v sfdisk >/dev/null || { apt-get update -qq >/dev/null && apt-get install -y -qq fdisk >/dev/null; }
part(){ sfdisk -d "$IMG" | awk -F'[=,]' -v n="$1" -v f="$2" '/ : start=/{c++; if(c==n){s=$2;z=$4;gsub(/ /,"",s);gsub(/ /,"",z); if(f=="start")print s; else print z; exit}}'; }
P1S=$(part 1 start); P1Z=$(part 1 size); P2S=$(part 2 start); P2Z=$(part 2 size); P3S=$(part 3 start); P3Z=$(part 3 size)
echo ">> partitions: boot=$P1S/$P1Z root=$P2S/$P2Z data=$P3S/$P3Z"
[ -n "$P3S" ] && echo "PASS: 3rd (data) partition exists" || echo "FAIL: no data partition"

# Unmount and detach on ANY exit (#237 round 2): a stopped verify must not leak loop devices.
RL=; BL=; DL=
cleanup(){ umount /mnt/root /mnt/boot /mnt/data 2>/dev/null; for L in $RL $BL $DL; do losetup -d "$L" 2>/dev/null; done; }
trap cleanup EXIT
RL=$(losetup --find --show --offset $((P2S*512)) --sizelimit $((P2Z*512)) "$IMG")
BL=$(losetup --find --show --offset $((P1S*512)) --sizelimit $((P1Z*512)) "$IMG")
DL=$(losetup --find --show --offset $((P3S*512)) --sizelimit $((P3Z*512)) "$IMG")
mkdir -p /mnt/root /mnt/boot /mnt/data
mount -o ro "$RL" /mnt/root; mount -o ro "$BL" /mnt/boot; mount -o ro "$DL" /mnt/data
R=/mnt/root; B=/mnt/boot
chk(){ [ -e "$R/$1" ] && echo "PASS: $2" || echo "FAIL: $2 ($1)"; }
grp(){ grep -q "$1" "$2" 2>/dev/null && echo "PASS: $3" || echo "FAIL: $3"; }

echo "--- OS stack ---"
chk usr/bin/retroarch "RetroArch"
echo "cores: $(find "$R/usr/lib/aarch64-linux-gnu/libretro" -name '*.so' 2>/dev/null | wc -l | tr -d ' ')"
chk opt/ES-DE/squashfs-root/AppRun "ES-DE launcher"
chk "home/$CU/.config/retroarch/autoconfig/udev/USB gamepad.cfg" "controller profile"
echo "--- data partition ($(blkid -o value -s LABEL "$DL" 2>/dev/null)) ---"
chk ../data/roms "data/roms (seeded)"
chk ../data/bios "data/bios"
grp "coin-dojo-data" "$R/etc/fstab" "/data in fstab"
echo "--- repoint to /data ---"
grp "/data/roms" "$R/home/$CU/ES-DE/settings/es_settings.xml" "ES-DE ROMDirectory=/data"
grp "/data/bios" "$R/home/$CU/.config/retroarch/retroarch.cfg" "RetroArch BIOS=/data"
echo "--- audio (HDMI combine-sink) ---"
grp "combine-sink" "$R/etc/pulse/default.pa.d/combine-sink.pa" "pulse combine-sink"
grp 'audio_driver = "pulse"' "$R/home/$CU/.config/retroarch/retroarch.cfg" "RetroArch audio=pulse"
echo "--- display ---"
grp "video=HDMI-A-2:1920x1080" /mnt/boot/cmdline.txt "both HDMI 1080p"
grp "vt.global_cursor_default=0" /mnt/boot/cmdline.txt "cursor hidden"
grp "cfg80211.ieee80211_regdom=US" /mnt/boot/cmdline.txt "wifi regdom set"
chk etc/systemd/system/coin-dojo-wifi.service "wifi import service"
chk usr/local/sbin/coin-dojo-wifi.sh "wifi import script"
[ -f /mnt/boot/wifi.conf ] && ! grep -qE '^(SSID|PSK)=.+' /mnt/boot/wifi.conf && echo "PASS: wifi.conf shipped empty (no baked creds)" || echo "FAIL: wifi.conf missing or has baked creds"
echo "--- firstboot + kiosk + ssh ---"
chk etc/systemd/system/coin-dojo-firstboot.service "firstboot service"
chk var/lib/coin-dojo-firstboot-pending "firstboot pending marker"
chk etc/systemd/system/getty@tty1.service.d/autologin.conf "tty1 autologin"
[ -L $R/etc/systemd/system/getty.target.wants/getty@tty1.service ] && echo "PASS: getty@tty1 enabled" || echo "FAIL: getty@tty1 enabled"
grp "MESA_GL_VERSION_OVERRIDE" "$R/home/$CU/.bash_profile" "ES-DE GL override"
[ -L $R/etc/systemd/system/multi-user.target.wants/ssh.service ] && echo "PASS: ssh enabled" || echo "FAIL: ssh enabled"
D=$R/etc/ssh/sshd_config.d
grp "^KbdInteractiveAuthentication no" "$D/00-coin-dojo-keyonly.conf" "sshd keyboard-interactive off"
if [ "${COIN_DOJO_SSH_PASSWORD:-no}" = "yes" ]; then
  echo "WARN: password SSH enabled (COIN_DOJO_SSH_PASSWORD=yes opt-in, not key-only)"
else
  grp "^PasswordAuthentication no" "$D/00-coin-dojo-keyonly.conf" "sshd key-only (PasswordAuthentication no)"
  # no OTHER drop-in may turn passwords back on (a name that sorts before 00- would win)
  if grep -lE '^[[:space:]]*(PasswordAuthentication|KbdInteractiveAuthentication)[[:space:]]+yes' "$D"/*.conf >/dev/null 2>&1; then
    echo "FAIL: a drop-in re-enables password SSH"
  else
    echo "PASS: no drop-in re-enables password SSH"
  fi
fi
# >>> hardening (#168): no pi in sudo; no ScreenScraper account baked into ES-DE's settings
echo "--- hardening ---"
if grep -qE '^sudo:[^:]*:[^:]*:(.*,)?pi(,|$)' "$R/etc/group" 2>/dev/null; then echo "FAIL: pi is in the sudo group"; else echo "PASS: pi not in the sudo group"; fi
[ ! -e "$R/etc/sudoers.d/010_pi-nopasswd" ] && echo "PASS: no passwordless sudo rule for pi (010_pi-nopasswd)" \
  || echo "FAIL: /etc/sudoers.d/010_pi-nopasswd still in the image"
if grep -qE 'name="Scraper(Username|Password)[A-Za-z]*" value="[^"]+"' "$R/home/$CU/ES-DE/settings/es_settings.xml" 2>/dev/null; then
  echo "FAIL: es_settings.xml carries a scraper user/password"
else
  echo "PASS: no scraper account in es_settings.xml"
fi
# <<< hardening
# >>> secrets (#237, #240): nothing secret in the image; first-boot config only on the boot partition
echo "--- no baked secrets ---"
# #299: the owner's password-SSH drop-in is written on the cabinet once the owner set their own password (SETTINGS >
# PASSWORD or a boot-partition password); an image never ships it, whatever the edition.
OWNSSH=$R/etc/ssh/sshd_config.d/10-coindojo-owner-ssh.conf
if [ -e "$OWNSSH" ] || [ -L "$OWNSSH" ]; then
  echo "FAIL: the image already has the owner's password-SSH drop-in (${OWNSSH#"$R"})"
else
  echo "PASS: no owner password-SSH drop-in in the image (it comes with the owner's own password)"
fi
# A usable password is a hash ($...) or an EMPTY field (user:: logs in with no password at all).
# #265: a community image whose boot config sets no password has ONE: the cabinet user's default login (12345, the
# hash build.sh recorded in /etc/coindojo/default-password); every other account stays locked. Personal, or a
# configured password: every account is locked and there is no default.
CU=${CU:-coindojo}; DMARK=$R/etc/coindojo/default-password; DEFAULT=
# "Does the boot config give a password?" is the build's own rule: the image's coindojo-firstboot --sets-password
# (its parser; an empty or empty-quoted value is no password), run in the image (VERIFY_FIRSTBOOT: the tests' copy).
fb_sets_password(){
  if [ -n "${VERIFY_FIRSTBOOT:-}" ]; then python3 "$VERIFY_FIRSTBOOT" --sets-password - < "$B/coindojo.yml"
  else chroot "$R" /usr/bin/python3 -I -B /usr/local/sbin/coindojo-firstboot --sets-password - < "$B/coindojo.yml"; fi
}
SETS=1
if [ -f "$B/coindojo.yml" ]; then
  fb_sets_password >/dev/null 2>&1; SETS=$?
  [ "$SETS" -le 1 ] || echo "FAIL: cannot tell whether the first-boot config sets a password (coindojo-firstboot --sets-password rc $SETS)"
fi
if [ "${EDITION:-community}" = community ] && [ "$SETS" = 1 ]; then DEFAULT=1; fi
if awk -F: -v u="${DEFAULT:+$CU}" '$1 != u && ($2 ~ /^\$/ || $2 == "") {f=1} END {exit !f}' "$R/etc/shadow" 2>/dev/null; then
  echo "FAIL: a usable or empty password in /etc/shadow ($(awk -F: -v u="${DEFAULT:+$CU}" '$1 != u && ($2 ~ /^\$/ || $2 == "") {printf "%s ", $1}' "$R/etc/shadow"))"
elif [ -n "$DEFAULT" ]; then echo "PASS: no password in /etc/shadow but $CU's default login (every other account locked)"
else echo "PASS: no password in /etc/shadow (every account locked until first boot)"; fi
# The settings role's two sudoers lines (ansible/roles/settings/defaults/main.yml; the tests pin them to it).
PWRULE="$CU ALL=(root) NOPASSWD: /usr/local/sbin/coin-dojo-set-password \"\""
HOSTRULE="$CU ALL=(root) NOPASSWD: /usr/local/sbin/coin-dojo-set-hostname ^[a-z0-9]([a-z0-9-]{0\\,61}[a-z0-9])?\$"
if [ -n "$DEFAULT" ]; then
  CUPW=$(awk -F: -v u="$CU" '$1 == u {print $2}' "$R/etc/shadow" 2>/dev/null)
  if [ -n "$CUPW" ] && [ "${CUPW#\$6\$}" != "$CUPW" ] && [ -f "$DMARK" ] && [ "$CUPW" = "$(cat "$DMARK")" ]; then
    echo "PASS: $CU has a password set: the community default login $CU / 12345 (console, and the network folder: roms and bios from the local subnets; admin off)"
  else
    echo "FAIL: $CU has no default password (locked, empty or not the recorded default; community image, no configured password)"
  fi
  # The default password opens the console and, while it is 12345, the network folder (roms, bios; the local subnets
  # only, #266). Never sudo (#270), never SSH: SSH must refuse passwords, whatever the opt-in says.
  if [ "${COIN_DOJO_SSH_PASSWORD:-no}" != yes ] && grep -q '^PasswordAuthentication no' "$R/etc/ssh/sshd_config.d/00-coin-dojo-keyonly.conf" 2>/dev/null \
     && ! grep -qsiE '^[[:space:]]*(PasswordAuthentication|KbdInteractiveAuthentication|ChallengeResponseAuthentication)[[:space:]]+yes' \
          "$R"/etc/ssh/sshd_config.d/*.conf; then
    echo "PASS: SSH refuses passwords (the default login works on the console and the network folder only)"
  else
    echo "FAIL: password SSH is possible on an image with the default password 12345"
  fi
  # #270: admin is off with the default: not in the sudo group, no rule for every command, the boot-partition grant pending.
  ALLRULE="^[[:space:]]*${CU}[[:space:]]+ALL[[:space:]]*=[[:space:]]*(\([^)]*\))?[[:space:]]*(NOPASSWD:[[:space:]]*)?ALL[[:space:]]*\$"
  if grep -qE "^sudo:[^:]*:[^:]*:(.*,)?$CU(,|\$)" "$R/etc/group" 2>/dev/null; then
    echo "FAIL: $CU is in the sudo group although the default password 12345 is active"
  elif [ -e "$R/etc/sudoers.d/coindojo-admin" ] || grep -qsE "$ALLRULE" "$R/etc/sudoers" "$R"/etc/sudoers.d/*; then
    echo "FAIL: a sudoers rule gives $CU every command although the default password 12345 is active"
  elif [ "$(cat "$R/etc/coindojo/sudo-pending" 2>/dev/null)" != "$CU" ]; then
    echo "FAIL: no admin grant pending for $CU (/etc/coindojo/sudo-pending)"
  else
    echo "PASS: $CU has no sudo while the default password is active (admin needs a boot-partition password)"
  fi
  # #271: SETTINGS > PASSWORD needs its ONE root helper through sudo, also without admin (the allowlist is below).
  if grep -qxF "$PWRULE" "$R/etc/sudoers.d/coin-dojo-settings" 2>/dev/null; then
    echo "PASS: SETTINGS > PASSWORD may run its one helper (no arguments) without admin"
  else
    echo "FAIL: no exact sudoers rule for the SETTINGS password helper in /etc/sudoers.d/coin-dojo-settings ($PWRULE)"
  fi
  [ -L "$R/etc/systemd/system/multi-user.target.wants/coindojo-password.path" ] \
    && echo "PASS: coindojo-password.path enabled (ends the reminder after passwd; never sudo)" \
    || echo "FAIL: coindojo-password.path enabled"
else
  # A password of the user's own (or the personal edition): sudo from the first boot, nothing pending.
  if grep -qE "^sudo:[^:]*:[^:]*:(.*,)?$CU(,|\$)" "$R/etc/group" 2>/dev/null && [ ! -e "$R/etc/coindojo/sudo-pending" ]; then
    echo "PASS: $CU is in the sudo group (its own password, no default)"
  else
    echo "FAIL: $CU is not in the sudo group, or an admin grant is pending, without a default password"
  fi
  [ ! -e "$DMARK" ] && echo "PASS: no default login (personal edition or a configured password)" \
    || echo "FAIL: a default-password mark in an image that must have none (${DMARK#"$R"})"
fi
# #270 M3: sudoers is an ALLOWLIST. Every line sudo reads (continuations joined, blanks and comments out; `#include`
# and `#<uid>` are not comments) in /etc/sudoers and /etc/sudoers.d/* must be stock Pi OS or one of the rules our
# roles write: power (poweroff), settings (hostname helper, password helper with NO arguments), cabinet_tweaks
# (no credential cache), and, only once admin is on (no default password), coindojo-admin. Aliases, groups, uids,
# wildcards, other users, Defaults that change the environment: all FAIL. Only /etc/sudoers may include, and only
# /etc/sudoers.d. Stock lines: debian's sudo + raspberrypi-sys-mods' 010_* files (whitespace collapsed).
sudo_lines(){
  awk '{ c = $0; sub(/^[[:space:]]+/, "", c)
         # a comment ends at its newline, even with a trailing backslash: drop it BEFORE the continuation test,
         # or it would swallow the next line (a hidden rule, #270 re-review N1)
         if (buf == "" && c ~ /^#/ && c !~ /^#include/ && c !~ /^#[0-9]/) next
         if (sub(/\\$/, "")) { buf = buf $0 " "; next }
         l = buf $0; buf = ""
         gsub(/[[:space:]]+/, " ", l); sub(/^ /, "", l); sub(/ $/, "", l)
         if (l == "") next
         if (l ~ /^#/ && l !~ /^#include/ && l !~ /^#[0-9]/) next
         print l }
       END { if (buf != "") print buf }' "$1"
}
SUDO_STOCK='Defaults env_reset
Defaults mail_badpass
Defaults secure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Defaults use_pty
Defaults env_keep += "NO_AT_BRIDGE"
Defaults env_keep += "ftp_proxy http_proxy https_proxy no_proxy"
Defaults env_keep += "DPKG_DEB_THREADS_MAX"
Defaults env_keep += "http_proxy HTTP_PROXY"
Defaults env_keep += "https_proxy HTTPS_PROXY"
Defaults env_keep += "ftp_proxy FTP_PROXY"
Defaults env_keep += "RSYNC_PROXY"
Defaults env_keep += "no_proxy NO_PROXY"
Defaults timestamp_type=global
Defaults !tty_tickets
root ALL=(ALL:ALL) ALL
%sudo ALL=(ALL:ALL) ALL'
SUDO_OURS="$CU ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff
$HOSTRULE
$PWRULE
Defaults:$CU timestamp_timeout=0"
[ -z "$DEFAULT" ] && SUDO_OURS="$SUDO_OURS
$CU ALL=(ALL:ALL) ALL"
sbad=; sinc=
for f in "$R/etc/sudoers" "$R"/etc/sudoers.d/*; do
  [ -e "$f" ] || [ -L "$f" ] || continue
  if [ ! -f "$f" ]; then sbad="$sbad ${f#"$R"}: not a regular file;"; continue; fi
  while IFS= read -r l; do
    case "$l" in
      "@includedir /etc/sudoers.d"|"#includedir /etc/sudoers.d")
        [ "$f" = "$R/etc/sudoers" ] || sinc="$sinc ${f#"$R"}: $l;" ;;
      @include*|"#include"*) sinc="$sinc ${f#"$R"}: $l;" ;;
      *) printf '%s\n%s\n' "$SUDO_STOCK" "$SUDO_OURS" | grep -qxF -- "$l" || sbad="$sbad ${f#"$R"}: $l;" ;;
    esac
  done < <(sudo_lines "$f")
done
[ -z "$sinc" ] && echo "PASS: sudoers includes nothing but /etc/sudoers.d" || echo "FAIL: sudoers includes more than /etc/sudoers.d:$sinc"
[ -z "$sbad" ] && echo "PASS: every sudoers line is stock Pi OS or one of our rules (allowlist)" \
  || echo "FAIL: a sudoers line outside the allowlist:$sbad"
# The two helpers sudo runs as root: root:root regular files nobody else may write (a writable helper = root).
for h in coin-dojo-set-password coin-dojo-set-hostname; do
  hp="$R/usr/local/sbin/$h"
  if [ ! -L "$hp" ] && [ -n "$(find "$hp" -maxdepth 0 -type f -user "${VERIFY_ROOT_UID:-0}" -group "${VERIFY_ROOT_GID:-0}" \
       ! -perm -020 ! -perm -002 2>/dev/null)" ]; then
    echo "PASS: /usr/local/sbin/$h is root:root, not group/other-writable"
  else
    echo "FAIL: /usr/local/sbin/$h missing, a link, not root:root, or group/other-writable (sudo runs it as root)"
  fi
done
# No Wi-Fi secret anywhere but the boot config: NetworkManager profiles, wpa_supplicant, Imager/legacy files on boot.
if grep -lsE '^[[:space:]]*psk=' "$R"/etc/NetworkManager/system-connections/* "$R"/etc/wpa_supplicant/*.conf 2>/dev/null | grep -q .; then
  echo "FAIL: a Wi-Fi PSK is baked into the root filesystem"; else echo "PASS: no Wi-Fi PSK in the root filesystem"; fi
for f in wpa_supplicant.conf userconf.txt userconf; do
  [ -e "$B/$f" ] && echo "FAIL: $f on the boot partition (credentials from outside the build)"
done
[ -e "$B/userconf.txt" ] || [ -e "$B/wpa_supplicant.conf" ] || [ -e "$B/userconf" ] || echo "PASS: no userconf.txt or wpa_supplicant.conf on boot"
K=$(find "$R/home" "$R/root" -name authorized_keys -size +0 2>/dev/null | head -1)
[ -z "$K" ] && echo "PASS: no SSH key baked in (it arrives at first boot)" || echo "FAIL: SSH key baked in (${K#"$R"})"
ls "$R"/etc/ssh/ssh_host_*key >/dev/null 2>&1 && echo "FAIL: SSH host keys baked in (every card would share them)" \
  || echo "PASS: no SSH host keys (made at first boot)"
if grep -rlsF -e "PRIVATE KEY-----" "$R/home" "$R/root" "$R/etc" "$B" 2>/dev/null | head -1 | grep -q .; then
  echo "FAIL: private key material in the image"; else echo "PASS: no private key material"; fi
grp "^PermitRootLogin no" "$R/etc/ssh/sshd_config.d/00-coin-dojo-keyonly.conf" "sshd PermitRootLogin no"
if grep -qE '^pi:\$' "$R/etc/shadow" 2>/dev/null; then echo "FAIL: pi has a usable password"; else echo "PASS: pi has no usable password"; fi
# The plaintext build password and the boot config's secret values must not sit anywhere in the root filesystem.
PATS=$(mktemp); chmod 600 "$PATS"
[ -z "${COINDOJO_PASSWORD:-}" ] || printf '%s\n' "$COINDOJO_PASSWORD" >> "$PATS"
[ -f "$B/coindojo.yml" ] && sed -n -e 's/^password_hash:[[:space:]]*//p' -e 's/^wifi_psk:[[:space:]]*//p' "$B/coindojo.yml" \
  | sed -e "s/^[\"']//" -e "s/[\"'][[:space:]]*\$//" | grep -v '^$' >> "$PATS"
if [ -s "$PATS" ]; then
  if grep -rqsF -f "$PATS" "$R" 2>/dev/null; then echo "FAIL: a first-boot secret sits in the root filesystem in plain text"
  else echo "PASS: no first-boot secret in the root filesystem"; fi
fi
rm -f "$PATS"
echo "--- first-boot config ---"
# #270: admin comes only from a password on the boot partition, so only root may write it: its fstab line must not
# hand the FAT files to a user (uid=/gid= other than 0) or let group/others write (umask/fmask/dmask without 022).
BOOTOPTS=$(awk '$1 !~ /^#/ && $2 == "/boot/firmware" {print $4; exit}' "$R/etc/fstab" 2>/dev/null)
if [ -z "$BOOTOPTS" ]; then
  echo "FAIL: /boot/firmware is not in /etc/fstab (the boot partition must be mounted root-only)"
else
  bad=; IFS=, read -r -a OPTS <<< "$BOOTOPTS"
  for o in "${OPTS[@]}"; do
    case "$o" in
      uid=0|gid=0) ;;
      uid=*|gid=*) bad="$bad $o" ;;
      umask=*|fmask=*|dmask=*) v=${o#*=}; case "$v" in *[!0-7]*|'') bad="$bad $o" ;; *) [ $(( 8#$v & 8#022 )) -eq $(( 8#022 )) ] || bad="$bad $o" ;; esac ;;
    esac
  done
  [ -z "$bad" ] && echo "PASS: /boot/firmware is mounted root-only ($BOOTOPTS)" \
    || echo "FAIL: /boot/firmware mount options let a user write it:$bad"
fi
chk usr/local/sbin/coindojo-firstboot "coindojo-firstboot script"
[ -L "$R/etc/systemd/system/multi-user.target.wants/coindojo-firstboot.service" ] && echo "PASS: coindojo-firstboot enabled" || echo "FAIL: coindojo-firstboot enabled"
[ -f "$B/coindojo.example.yml" ] && ! grep -qE '^(password_hash|wifi_psk|ssh_pubkey):[[:space:]]*[^[:space:]#]' "$B/coindojo.example.yml" \
  && echo "PASS: coindojo.example.yml on boot, empty" || echo "FAIL: coindojo.example.yml missing or filled in"
if [ -f "$B/coindojo.yml" ]; then
  echo "PASS: coindojo.yml on boot (applied and scrubbed at first boot)"
  grep -qE '^(password_hash|wifi_psk):[[:space:]]*[^[:space:]#]' "$B/coindojo.yml" \
    && echo "INFO: CONFIG secrets present on boot (expected for a personal image: never share it)"
else
  echo "PASS: no coindojo.yml (the cabinet shows the no-config hint)"
fi
# <<< secrets
# >>> stack (#244): what the cabinet's roles put in (ansible/image.yml). The boot-check list until #232 has its own.
echo "--- cabinet stack (roles) ---"
DA=${VERIFY_DATA:-/mnt/data}; H=home/$CU   # VERIFY_DATA: the tests' fixture
# a path as the image sees it -> where it is mounted here (/data is the data partition)
img(){ case "$1" in /data|/data/*) echo "$DA${1#/data}" ;; *) echo "$R$1" ;; esac; }
has(){ [ -e "$(img "$1")" ] && echo "PASS: $2" || echo "FAIL: $2 ($1)"; }
en(){ [ -L "$R/etc/systemd/system/$1.wants/$2" ] && echo "PASS: $2 enabled" || echo "FAIL: $2 enabled ($1)"; }
# The launcher's cores, binaries and ROM folders: launch.py's own table (run.sh wrote it with launcher_table.py).
EXP=${EXPECT_PATHS:-$(dirname "$IMG")/.expect-paths}
if [ -s "$EXP" ]; then
  miss=0; n=0
  while IFS= read -r p; do n=$((n+1)); [ -e "$(img "$p")" ] || { echo "FAIL: launcher needs $p"; miss=1; }; done < "$EXP"
  [ "$miss" = 0 ] && echo "PASS: every core, emulator and ROM folder launch.py names is in the image ($n paths)"
else
  echo "FAIL: no launcher table ($EXP): run.sh writes it from launch.py"
fi
has /usr/local/bin/coin-dojo-session "session loop (boot menu hub, #209)"
grp "coin-dojo-session" "$R/$H/.bash_profile" "tty1 starts the session loop"
has /data/coin-dojo/settings/bootmenu_app.py "boot menu app"
has /data/coin-dojo/settings/settings_app.py "SETTINGS app"
ESYS="$R/$H/ES-DE/custom_systems/es_systems.xml"
grp "<name>settings</name>" "$ESYS" "SETTINGS on the carousel"
grp "<name>quit</name>" "$ESYS" "QUIT TO MAIN MENU on the carousel"
for l in settings/Settings.hks quit/Quit.hks; do
  t=$(readlink "$DA/coin-dojo/nested_out/roms_nested/$l" 2>/dev/null)
  [ -n "$t" ] && [ -e "$(img "$t")" ] && echo "PASS: carousel entry $l -> $t" || echo "FAIL: carousel entry $l (dead or missing link)"
done
has "/$H/ES-DE/scripts/game-select/50-coin-dojo-settings.sh" "SETTINGS/QUIT open on one press (game-select hook)"
THEME=$(sed -n 's/.*name="Theme" value="\([^"]*\)".*/\1/p' "$R/$H/ES-DE/settings/es_settings.xml" 2>/dev/null)
[ -n "$THEME" ] && [ -f "$R/$H/ES-DE/themes/$THEME/theme.xml" ] && echo "PASS: ES-DE theme $THEME selected and installed" \
  || echo "FAIL: ES-DE theme not selected or not installed (Theme='$THEME')"
has "/$H/ES-DE/scripts/launch.py" "launch.py (the launcher)"
en multi-user.target coin-dojo-powerd.service
has /etc/systemd/user/coin-dojo-stickmouse.service "stickmouse user unit (#211)"
has /data/launcher/stickmouse.py "stickmouse"
has /etc/udev/rules.d/61-coin-dojo-stickmouse.rules "uinput rule (stickmouse)"
has /etc/modules-load.d/coin-dojo-stickmouse.conf "uinput loads at boot"
grp '^kernel=kernel8.img' "$B/config.txt" "4 KB-page kernel (cabinet_tweaks)"
grp '^Mechanism=zram' "$R/etc/rpi/swap.conf.d/50-coin-dojo.conf" "zram swap pinned (rpi-swap)"
[ ! -e "$R/etc/default/zramswap" ] && echo "PASS: one zram mechanism (no zram-tools config)" || echo "FAIL: zram-tools config next to rpi-swap"
grp '^vm.swappiness' "$R/etc/sysctl.d/99-zz-coin-dojo.conf" "vm.swappiness at boot (session)"
grp 'wifi.powersave = 2' "$R/etc/NetworkManager/conf.d/90-coin-dojo-wifi.conf" "Wi-Fi power save off"
has /etc/polkit-1/rules.d/51-coin-dojo-net.rules "polkit: reconnect Wi-Fi without sudo"
has /etc/polkit-1/rules.d/50-coin-dojo-settings.rules "polkit: SETTINGS Wi-Fi + time zone at the seat"
for s in coin-dojo-power coin-dojo-settings coin-dojo-sudo-timeout; do has "/etc/sudoers.d/$s" "sudoers.d/$s"; done
has /etc/systemd/journald.conf.d/50-coin-dojo.conf "persistent journal (journald)"
if [ "${IMAGE_FIGHTCADE:-false}" = true ]; then
  has /usr/local/bin/coin-dojo-fightcade "Fightcade mode launcher"
  grp '^:hk-wine:M::MZ::' "$R/etc/binfmt.d/coin-dojo-wine.conf" "#228 binfmt rule (Windows .exe -> wine wrapper)"
  has /data/fightcade/opt/box64/box64 "Box64"
  if [ "${IMAGE_FIGHTCADE_INSTALL:-deploy}" = first-launch ]; then
    # #267: a shareable image holds NONE of Wine, Electron, Fightcade (or a prefix built from Wine); the cabinet
    # downloads them on the first FIGHTCADE pick. Names: their pinned files (from the image's manifest) and what only
    # their trees contain.
    FCM="$R/usr/local/etc/coin-dojo-fightcade-fetch.json"
    has /usr/local/lib/coin-dojo-fightcade/hk-fightcade-fetch "#267 first-launch download engine"
    has /usr/local/lib/coin-dojo-fightcade/hk_fc_firstrun.py "#267 first-launch screen"
    has /usr/local/etc/coin-dojo-fightcade-fetch.json "#267 first-launch manifest"
    grp '^HK_FC_INSTALL=first-launch' "$R/usr/local/etc/coin-dojo-fightcade.conf" "#267 launcher in first-launch mode"
    bad=$(grep -o '"url": "[^"]*"' "$FCM" 2>/dev/null | grep -v '"url": "https://' | head -2)
    [ -z "$bad" ] && grep -q '"url": "https://' "$FCM" 2>/dev/null && echo "PASS: #267 every download url is https" \
      || echo "FAIL: #267 a download url that is not https (or none): $bad"
    pinned=()
    while IFS= read -r f; do
      [ -n "$f" ] && pinned+=(-o -name "$f" -o -name "$f.part")
    done < <(sed -n 's/.*"file": "\([^"/]*\)".*/\1/p' "$FCM" 2>/dev/null)
    tp=$(find "$R" "$DA" -xdev \( -name fcadefbneo.exe -o -name fc2-electron -o -name ntdll.dll -o -name ntdll.so \
         -o -name chrome_100_percent.pak -o -name 'Fightcade-linux-*.tar.gz' "${pinned[@]}" \) -print 2>/dev/null | head -5)
    [ -z "$tp" ] && echo "PASS: #267 no Fightcade, Wine or Electron file in the image" \
      || echo "FAIL: #267 third-party Fightcade files in a first-launch image: $tp"
    for d in opt/wine opt/electron opt/Fightcade prefix/.hk-stamp; do
      [ ! -e "$DA/fightcade/$d" ] || echo "FAIL: #267 /data/fightcade/$d is in a first-launch image"
    done
  else
    has /data/fightcade/prefix/.hk-stamp "base Wine prefix built"
  fi
else
  [ ! -e "$R/usr/local/bin/coin-dojo-fightcade" ] && echo "PASS: no Fightcade mode (hk_image_fightcade off: the boot menu shows ARCADE only)" \
    || echo "FAIL: Fightcade launcher present although hk_image_fightcade is off"
fi
CUID=$(awk -F: -v u="$CU" '$1 == u {print $3}' "$R/etc/passwd")
# The uid the roles rendered (/run/user/<uid>) must be the user's real one (#244: Pi OS's pi held 1000).
WUID=${HK_CABINET_UID:-1000}
[ "$CUID" = "$WUID" ] && echo "PASS: $CU has uid $CUID = hk_cabinet_uid" || echo "FAIL: $CU has uid '${CUID:-none}', hk_cabinet_uid is $WUID"
RUN=$(sed -n "s/^HK_RUN_DIR=['\"]\{0,1\}\([^'\"]*\).*/\1/p" "$R/usr/local/etc/coin-dojo-session.conf" 2>/dev/null)
case "$RUN" in "/run/user/$CUID/"*) echo "PASS: session HK_RUN_DIR $RUN is $CU's runtime dir" ;;
  *) echo "FAIL: session HK_RUN_DIR '$RUN' is not under /run/user/${CUID:-?}/" ;; esac
# Every /run/user/<n> any role wrote (units, launchers, session, flycast, Fightcade, powerd, trophiesd) is that uid.
other=$(grep -rhosE '/run/user/[0-9]+' "$R/usr/local" "$R/etc/systemd" "$R/etc/sudoers.d" "$R/$H" "$DA/launcher" \
        "$DA/coin-dojo" "$DA/emulators/flycast" 2>/dev/null | sort -u | grep -vx "/run/user/${CUID:-x}" | tr '\n' ' ')
[ -z "$other" ] && echo "PASS: every /run/user path the roles wrote is $CU's" || echo "FAIL: another uid's runtime dir in the image: $other"
bad=$(find "$R/$H" "$DA" -xdev -path "$DA/lost+found" -prune -o ! -uid "${CUID:-x}" -print 2>/dev/null \
      | grep -v "/coin-dojo/settings/settings.hks$" | head -3)
[ -z "$bad" ] && echo "PASS: $CU owns its home and /data" || echo "FAIL: not $CU's: $bad"
# <<< stack
# >>> licences (#254, #267): what licences.py wrote at provision (the generator already refused unlisted binaries)
echo "--- licences ---"
LD=$R/usr/share/doc/coindojo/licences
# A licence problem fails verify only for a public release (HK_LICENCES_STRICT=1 exactly); otherwise it is a WARN, so
# an offline personal build still passes.
lbad(){ if [ "${HK_LICENCES_STRICT:-}" = 1 ]; then echo "FAIL: $*"; else echo "WARN: $*"; fi; }
grep -qF "not a lawyer" "$LD/LICENCES.md" 2>/dev/null && echo "PASS: licence summary (usr/share/doc/coindojo/licences/LICENCES.md)" \
  || lbad "no licence summary in usr/share/doc/coindojo/licences"
[ -s "$B/LICENCES.md" ] && cmp -s "$B/LICENCES.md" "$LD/LICENCES.md" && echo "PASS: LICENCES.md on the boot partition" \
  || lbad "LICENCES.md missing from the boot partition (or not the generated text)"
if [ -s "$LD/components.tsv" ]; then
  n=0; miss=
  while IFS=$'\t' read -r id _v _spdx _o _src _ref _nc files _sums; do
    case "$id" in ''|'#'*) continue ;; esac
    n=$((n+1)); [ "$files" = "per-file headers" ] && continue
    [ "$files" = missing ] && { miss="$miss $id/(all)"; continue; }
    for f in $(printf '%s' "$files" | tr ',' ' '); do [ -s "$LD/$id/$f" ] || miss="$miss $id/$f"; done
  done < "$LD/components.tsv"
  [ -z "$miss" ] && echo "PASS: every component's licence text is in the image ($n components)" \
    || lbad "licence texts missing:$miss"
  u=$(awk -F'\t' '!/^#/ && $6 == "unpinned" {printf "%s ", $1}' "$LD/components.tsv")
  [ -z "$u" ] || echo "WARN: no exact source ref (release blocker, licences.py --strict): $u"
else
  lbad "no components.tsv in usr/share/doc/coindojo/licences"
fi
if [ -s "$LD/debian-packages.tsv" ]; then
  np=$(grep -vc '^#' "$LD/debian-packages.tsv")
  nu=$(awk -F'\t' '!/^#/ && $7 !~ /^https:\/\/(snapshot\.debian\.org|archive\.raspberrypi\.com)\// {c++} END {print c+0}' "$LD/debian-packages.tsv")
  [ "$np" -gt 0 ] && [ "$nu" = 0 ] && echo "PASS: debian-packages.tsv lists $np packages, each with its source link" \
    || lbad "debian-packages.tsv: $np packages, $nu without a source link"
  nc=$(awk -F'\t' '!/^#/ && $8 != "yes" && $8 !~ /^none upstream/ {c++} END {print c+0}' "$LD/debian-packages.tsv")
  [ "$nc" = 0 ] || echo "WARN: $nc packages without /usr/share/doc/<pkg>/copyright (release blocker)"
else
  lbad "no debian-packages.tsv in usr/share/doc/coindojo/licences"
fi
# <<< licences
# >>> share (#266): the network folder (role share, hk_share_enable). On: smbd enabled, exactly roms + bios, guest
# off, SMB3 minimum, bound to the LAN interfaces, no NetBIOS. Off: smbd not enabled.
echo "--- network folder ---"
SMB=$R/etc/samba/smb.conf; W=$R/etc/systemd/system/multi-user.target.wants
val(){ sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//Ip" "$SMB" 2>/dev/null | tr '[:upper:]' '[:lower:]' | sed 's/[[:space:]]*$//'; }
if [ -L "$W/smbd.service" ]; then
  echo "PASS: smbd enabled (network folder on)"
  secs=$(sed -n 's/^[[:space:]]*\[\([^]]*\)\].*/\1/p' "$SMB" 2>/dev/null | tr '[:upper:]' '[:lower:]' | sort | tr '\n' ' ')
  [ "$secs" = "bios global roms " ] && echo "PASS: network folder shares roms and bios only" \
    || echo "FAIL: smb.conf sections '$secs' (want global, roms, bios only)"
  if grep -iqE '^[[:space:]]*(guest ok|public|guest only)[[:space:]]*=[[:space:]]*(yes|true|1|on)' "$SMB" 2>/dev/null \
     || [ "$(val 'map to guest')" != never ]; then echo "FAIL: network folder allows guests"
  else echo "PASS: network folder guest off"; fi
  case "$(val 'server min protocol')" in smb3|smb3_00|smb3_02|smb3_11) echo "PASS: network folder min protocol SMB3" ;;
    *) echo "FAIL: network folder min protocol '$(val 'server min protocol')' (want SMB3)" ;; esac
  [ "$(val 'bind interfaces only')" = yes ] && [ -n "$(val 'interfaces')" ] && echo "PASS: network folder bound to $(val 'interfaces')" \
    || echo "FAIL: network folder not bound to the LAN interfaces"
  [ "$(val 'smb ports')" = 445 ] && [ "$(readlink "$R/etc/systemd/system/nmbd.service")" = /dev/null ] \
    && echo "PASS: no NetBIOS (port 445 only, nmbd masked)" || echo "FAIL: NetBIOS on (smb ports '$(val 'smb ports')', nmbd not masked)"
  # who may connect: loopback in smb.conf, the live subnets from the include (written at every smbd start)
  allow=$(val 'hosts allow' | tr ' ' '\n' | grep -vxE '127\.0\.0\.1|::1' | tr '\n' ' ')
  if [ -z "$allow" ] && [ "$(val 'include')" = /run/coin-dojo-share/hosts.conf ] \
     && [ -f "$R/etc/systemd/system/smbd.service.d/50-coin-dojo-hosts.conf" ]; then
    echo "PASS: network folder allows only the live local subnets (loopback + include, smbd writes them)"
  else echo "FAIL: network folder hosts allow '$(val 'hosts allow')' / include '$(val 'include')' (want loopback + the live-subnet include)"; fi
  bv=$(sed -n '/^[[:space:]]*\[bios\]/,/^[[:space:]]*\[/p' "$SMB" 2>/dev/null | sed -n 's/^[[:space:]]*veto files[[:space:]]*=[[:space:]]*//p')
  case "$bv" in */mame/*) echo "PASS: bios share blocks MAME's scripts and settings (mame/)" ;;
    *) echo "WARN: bios share does not veto mame/ (hk_share_bios_veto off)" ;; esac
  rv=$(sed -n '/^[[:space:]]*\[roms\]/,/^[[:space:]]*\[/p' "$SMB" 2>/dev/null | sed -n 's/^[[:space:]]*veto files[[:space:]]*=[[:space:]]*//p')
  case "$rv" in */\*.cmd/*) echo "PASS: roms share blocks MAME command files (*.cmd)" ;;
    *) echo "WARN: roms share does not veto *.cmd (hk_share_roms_veto off)" ;; esac
  grep -q -- '--hosts-only /run/coin-dojo-share/hosts.conf' "$R/etc/systemd/system/smbd.service.d/50-coin-dojo-hosts.conf" 2>/dev/null \
    && echo "PASS: smbd refuses to start on a bad hosts file (fail closed)" || echo "FAIL: smbd starts without checking its hosts file"
  for u in coin-dojo-share-sync.service coin-dojo-share-sync.path; do
    [ -L "$W/$u" ] && echo "PASS: $u enabled" || echo "FAIL: $u enabled"; done
else
  echo "PASS: no network folder (smbd not enabled: hk_share_enable off)"
fi
# <<< share
echo "--- rootfs usage ---"; df -h /mnt/root | tail -1

cleanup; RL=; BL=; DL=
echo VERIFY_DONE
