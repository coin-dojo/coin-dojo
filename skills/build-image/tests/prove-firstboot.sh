#!/bin/bash
# shellcheck disable=SC2016,SC2034,SC2012  # ok '...' checks are eval'd later (single quotes, vars used there)
# prove-firstboot.sh - the first-boot unit under real systemd with real account tools (#237).
#   make -C skills/build-image prove-firstboot          (host: needs docker; ~1-2 min, mostly apt)
# Starts the pinned debian:12 with systemd as PID 1 (privileged), installs coindojo-firstboot and the unit text
# taken from chroot-setup.sh, makes the image user (locked, like the build), then runs the unit through systemctl:
#   1. a full coindojo.yml   -> user renamed, hash in /etc/shadow, key, hostname, timezone, Wi-Fi keyfile,
#                                SSH host keys made, coindojo.yml gone, no secret left on /boot or in the journal
#   2. the next boot           -> nothing changes
#   3. a malformed file        -> unit fails, nothing applied, secrets blanked, error file + on-screen hint
#   4. no config, never set    -> /run/coindojo/no-config (the hint), boot goes on
#   8. user: naming another user -> refused, nothing applied (#244: the roles baked the image user in)
#   9. the user renamed under the image (Imager firstrun.sh) -> loud error, nothing applied
#  10. #270: sudo off with the default 12345; a runtime passwd keeps it off; a boot-partition password turns it on
#  11. #270 re-review: ~/.ssh -> /etc, authorized_keys(.tmp) -> /etc/shadow: nothing outside the home changes
# Every check prints PASS/FAIL; the last line is RESULT.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); SKILL=$(cd "$HERE/.." && pwd)
if [ "${1:-}" != --inner ]; then
  # shellcheck source=../scripts/lib-host.sh
  . "$SKILL/scripts/lib-host.sh"
  hk_check_docker
  NAME=coindojo-prove-$$
  docker run -d --name "$NAME" --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
    -v "$SKILL":/skill:ro --tmpfs /run --tmpfs /run/lock "$HK_DEBIAN_IMAGE" bash -c \
    'apt-get update -qq >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq systemd python3 passwd openssh-server tzdata sudo openssl >/dev/null 2>&1; touch /ready; exec /lib/systemd/systemd' >/dev/null \
    || hk_die "could not start the systemd container"
  trap 'docker rm -f "$NAME" >/dev/null 2>&1' EXIT
  st=
  for i in $(seq 1 150); do   # up to ~5 min: apt in the container, then systemd boots
    if docker exec "$NAME" test -e /ready 2>/dev/null; then
      st=$(docker exec "$NAME" systemctl is-system-running 2>/dev/null || true)   # degraded exits 1: read, don't test
      case "$st" in running|degraded) break ;; esac
    fi
    [ $((i % 15)) -eq 0 ] && echo ">> waiting for the systemd container (${i}x2 s, state '${st:-apt}')"
    sleep 2
  done
  case "$st" in running|degraded) ;; *) hk_die "systemd in the container did not come up (state '${st:-none}')" ;; esac
  docker exec "$NAME" bash /skill/tests/prove-firstboot.sh --inner
  exit $?
fi

# ---------------- inside the container (systemd is PID 1) ----------------
fails=0
ok(){ if eval "$1"; then echo "PASS: $2"; else echo "FAIL: $2"; fails=$((fails+1)); fi; }
B=/boot/firmware
PSK=EXAMPLE-wkey-0237x   # test secrets avoid pass/psk/secret: the allow-list must catch them
HASH=$(python3 -W ignore -c 'import crypt; print(crypt.crypt("EXAMPLE-not-real", crypt.mksalt(crypt.METHOD_SHA512)))')
KEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEXAMPLEfakeKEYfakeKEYfakeKEYfakeKEY you@laptop"
echo ">> systemd: $(systemctl is-system-running 2>&1), $(systemctl --version | head -1)"

# the image as chroot-setup.sh leaves it: locked user (a neutral name), built-user, autologin drop-in, script, unit, no host keys
useradd -m -s /bin/bash arcade; passwd -l arcade >/dev/null
mkdir -p /etc/coindojo "$B" /etc/systemd/system/getty@tty1.service.d /home/arcade/.config/retroarch
echo arcade > /etc/coindojo/built-user
printf '[Service]\nExecStart=\nExecStart=-/sbin/agetty --autologin arcade --noclear %%I $TERM\n' > /etc/systemd/system/getty@tty1.service.d/autologin.conf
echo 'joypad_autoconfig_dir = "/home/arcade/.config/retroarch/autoconfig"' > /home/arcade/.config/retroarch/retroarch.cfg
echo "console=tty1 root=PARTUUID=x cfg80211.ieee80211_regdom=US" > $B/cmdline.txt
install -m 0755 /skill/scripts/coindojo-firstboot.py /usr/local/sbin/coindojo-firstboot
sed -n "/^cat > \/etc\/systemd\/system\/coindojo-firstboot.service <<'UNIT'$/,/^UNIT$/p" /skill/scripts/chroot-setup.sh \
  | sed '1d;$d' > /etc/systemd/system/coindojo-firstboot.service
ok '[ -s /etc/systemd/system/coindojo-firstboot.service ]' "unit text extracted from chroot-setup.sh"
rm -f /etc/ssh/ssh_host_*
systemctl daemon-reload; systemctl enable coindojo-firstboot.service >/dev/null 2>&1
ok 'systemctl is-enabled coindojo-firstboot.service >/dev/null' "unit enables (WantedBy=multi-user.target)"

echo "--- 1. full coindojo.yml ---"
cat > $B/coindojo.yml <<YML
user: dojo
password_hash: '$HASH'
ssh_pubkey: "$KEY"
hostname: coindojo
timezone: Europe/Berlin
wifi_ssid: "Home Net"
wifi_psk: $PSK
wifi_country: DE
YML
# A power cut after `usermod -l` on an earlier boot (#237 round 2): the login is renamed, home/group/autologin
# are not. This boot must finish the rename with the real tools.
mkdir -p /var/lib/coindojo; echo "arcade dojo" > /var/lib/coindojo/rename; usermod -l dojo arcade
ok 'getent passwd dojo | grep -q ":/home/arcade:" && getent group arcade >/dev/null' "power cut staged: login renamed, home and group still old"
systemctl start coindojo-firstboot.service; rc=$?
ok '[ $rc -eq 0 ]' "unit ran (systemctl start rc $rc)"
ok 'getent passwd dojo | grep -q ":/home/dojo:" && ! getent passwd arcade >/dev/null' "user arcade renamed to dojo, home /home/dojo"
ok '[ "$(getent shadow dojo | cut -d: -f2)" = "$HASH" ]' "the hash is dojo's password in /etc/shadow"
ok 'getent group dojo >/dev/null && ! getent group arcade >/dev/null' "group renamed"
ok '[ ! -e /var/lib/coindojo/rename ]' "the resumed rename finished (state file gone)"
ok '[ "$(cat /home/dojo/.ssh/authorized_keys)" = "$KEY" ] && [ "$(stat -c %U:%a /home/dojo/.ssh/authorized_keys)" = dojo:600 ]' "SSH key installed, dojo:600"
ok 'grep -q -- "--autologin dojo " /etc/systemd/system/getty@tty1.service.d/autologin.conf' "tty1 autologin follows the rename"
ok '[ "$(cat /etc/hostname)" = coindojo ] && [ "$(hostname)" = coindojo ]' "hostname set (file and live)"
ok '[ "$(readlink /etc/localtime)" = /usr/share/zoneinfo/Europe/Berlin ]' "timezone Europe/Berlin"
NM=/etc/NetworkManager/system-connections/coindojo-wifi.nmconnection
ok '[ "$(stat -c %a $NM)" = 600 ] && grep -q "^psk=$PSK$" $NM' "Wi-Fi keyfile 0600 with the PSK (the only place it lives)"
ok 'grep -q "regdom=DE" $B/cmdline.txt' "Wi-Fi country DE on the kernel command line"
ok 'ls /etc/ssh/ssh_host_ed25519_key >/dev/null 2>&1' "SSH host keys made at first boot"
ok '[ ! -e $B/coindojo.yml ] && [ -f $B/coindojo.applied.yml ]' "coindojo.yml removed, coindojo.applied.yml left"
ok '! grep -rqsF -e "$PSK" -e "$HASH" $B' "no PSK or hash left on /boot/firmware (secrets scrubbed)"
ok '! journalctl -u coindojo-firstboot --no-pager 2>/dev/null | grep -qF -e "$PSK" -e "$HASH"' "no secret in the journal"
ok '[ ! -e /run/coindojo/no-config ]' "no hint after a config"
journalctl -u coindojo-firstboot --no-pager -o cat 2>/dev/null | sed 's/^/   | /' | tail -10

echo "--- 2. next boot ---"
S1=$(md5sum /etc/shadow /etc/passwd | md5sum)
systemctl restart coindojo-firstboot.service
ok '[ "$(md5sum /etc/shadow /etc/passwd | md5sum)" = "$S1" ] && [ ! -e /run/coindojo/no-config ]' "a second run changes nothing, no hint"

echo "--- 3. malformed coindojo.yml (broken case) ---"
printf '\xef\xbb\xbfhostname: other\nwifi_ssid: n\nwifi_psk: %s\nPassword_hash: x%s\npassword: hunter2EXAMPLE\ncolour: red\n' "$PSK" "$PSK" > $B/coindojo.yml
systemctl restart coindojo-firstboot.service; rc=$?
ok '[ $rc -ne 0 ] && systemctl is-failed coindojo-firstboot.service >/dev/null' "BROKEN: the unit fails on a malformed file"
ok '[ "$(cat /etc/hostname)" = coindojo ]' "nothing applied (hostname unchanged)"
ok '! grep -rqsF -e "$PSK" -e hunter2EXAMPLE $B /run/coindojo && grep -q "^hostname: .other" $B/coindojo.yml' "BOM, Password_hash:, plain password: every secret gone from boot, the rest kept to fix"
ok 'grep -q "line 4: keys are lower case" $B/coindojo.error.txt && grep -q "line 4" /run/coindojo/no-config' "error file and on-screen hint say why (line 4: keys are lower case)"

echo "--- 4. no config, never configured ---"
rm -f $B/coindojo.yml $B/coindojo.error.txt /var/lib/coindojo/applied /run/coindojo/no-config
systemctl reset-failed coindojo-firstboot.service; systemctl restart coindojo-firstboot.service; rc=$?
ok '[ $rc -eq 0 ] && grep -q "coindojo.example.yml" /run/coindojo/no-config' "no config -> unit succeeds and leaves the on-screen hint"

echo "--- 5. a second config after the rename (round 3) ---"
printf 'hostname: second\nssh_pubkey: "%s"\n' "${KEY% *} second@box" > $B/coindojo.yml
systemctl restart coindojo-firstboot.service; rc=$?
ok '[ $rc -eq 0 ] && [ "$(cat /etc/hostname)" = second ] && grep -q "second@box" /home/dojo/.ssh/authorized_keys' "a second coindojo.yml without user: applies to the renamed user dojo"
echo "--- 6. a lost newline glues a secret onto another key (round 3, broken case) ---"
printf 'hostname: dojo wifi_psk: hunter2XYZQ\ntimezone: Europe/Berlin hunter2XYZQ\n' > $B/coindojo.yml
systemctl restart coindojo-firstboot.service; rc=$?
ok '[ $rc -ne 0 ]' "BROKEN: the glued file is refused"
ok '! grep -rqsF hunter2XYZQ $B /run/coindojo && ! journalctl -u coindojo-firstboot --no-pager 2>/dev/null | grep -qF hunter2XYZQ' "the secret is in no file on boot, not in /run, not in the journal"
systemctl reset-failed coindojo-firstboot.service
echo "--- 7. a glued secret that used to VALIDATE (round 4, success path before the fix) ---"
printf 'wifi_ssid: home wifi_psk: Zq7mK9vR2x\n' > $B/coindojo.yml
systemctl restart coindojo-firstboot.service; rc=$?
ok '[ $rc -ne 0 ]' "BROKEN: wifi_ssid with a glued wifi_psk is refused"
ok '! grep -rqsF Zq7mK9vR2x $B /run/coindojo /etc/NetworkManager && ! journalctl -u coindojo-firstboot --no-pager 2>/dev/null | grep -qF Zq7mK9vR2x' "the secret is in no file (boot, /run, NetworkManager) and not in the journal"
systemctl reset-failed coindojo-firstboot.service
echo "--- 8. user: naming someone else (#244 fix round, broken case) ---"
printf 'user: other\nhostname: third\n' > $B/coindojo.yml
systemctl restart coindojo-firstboot.service; rc=$?
ok '[ $rc -ne 0 ] && ! getent passwd other >/dev/null && [ "$(cat /etc/hostname)" = second ]' "BROKEN: user: other is refused, nothing applied (no rename, hostname unchanged)"
ok 'grep -q "is not this image.s user (dojo)" $B/coindojo.error.txt && grep -q "hk_cabinet_user" /run/coindojo/no-config' "error file and on-screen hint say why"
systemctl reset-failed coindojo-firstboot.service
echo "--- 9. Imager's firstrun.sh renamed the cabinet user (round 3, broken case) ---"
rm -f $B/coindojo.yml $B/coindojo.error.txt
usermod -l bob dojo && groupmod -n bob dojo && usermod -d /home/bob -m bob   # what firstrun.sh does to uid 1000
printf 'hostname: fourth\n' > $B/coindojo.yml
systemctl restart coindojo-firstboot.service; rc=$?
ok '[ $rc -ne 0 ] && [ "$(cat /etc/hostname)" = second ]' "BROKEN: the user renamed under the image -> unit fails, nothing applied"
ok 'grep -q "THE CABINET USER IS GONE" /run/coindojo/no-config && grep -q "the account is now bob" $B/coindojo.error.txt' "loud on-screen error and coindojo.error.txt name what happened"
systemctl reset-failed coindojo-firstboot.service

echo "--- 10. #270: admin (sudo) is off with the default 12345; a runtime passwd never turns it on; a boot-partition password does ---"
NEWPW=EXAMPLE-new-270x; BOOTPW=EXAMPLE-boot-270y
useradd -m -s /bin/bash -G sudo nduser
echo nduser > /etc/coindojo/built-user; echo nduser > /etc/coindojo/user   # step 9 renamed the old user away
coindojo-firstboot --default-login --user nduser --edition community; rc=$?
ok '[ $rc -eq 0 ] && ! id -nG nduser | grep -qw sudo && [ "$(cat /etc/coindojo/sudo-pending)" = nduser ]' "community default login: nduser out of the sudo group, grant pending"
for u in coindojo-password.service coindojo-password.path; do
  sed -n "/^cat > \/etc\/systemd\/system\/$u <<'UNIT'$/,/^UNIT$/p" /skill/scripts/chroot-setup.sh | sed '1d;$d' > /etc/systemd/system/$u
done
systemctl daemon-reload; systemctl enable --now coindojo-password.path >/dev/null 2>&1
ok 'systemctl is-active -q coindojo-password.path' "coindojo-password.path (unit text from chroot-setup.sh) is active"
ok '! printf "12345\n" | su nduser -c "sudo -S -k true" 2>/dev/null' "BROKEN: sudo with the default password 12345 is refused"
# The attack: code running as nduser knows 12345 and runs the real passwd on a pty (script gives it one).
S0=$(getent shadow nduser | cut -d: -f2)
# passwd flushes typed-ahead input at each prompt (TCSAFLUSH): answer each prompt after a pause.
{ sleep 2; echo 12345; sleep 1; echo "$NEWPW"; sleep 1; echo "$NEWPW"; sleep 2; } \
  | su nduser -c "script -qec passwd /dev/null" > /tmp/passwd.out 2>&1
ok '[ "$(getent shadow nduser | cut -d: -f2)" != "$S0" ]' "nduser changed its own password with passwd (on a pty)"
for i in $(seq 1 20); do [ ! -e /etc/coindojo/default-password ] && break; sleep 0.5; done
ok '[ ! -e /etc/coindojo/default-password ]' "the path unit ran: the 12345 reminder ended (${i}x0.5 s)"
ok '[ ! -e /etc/sudoers.d/coindojo-admin ] && [ -e /etc/coindojo/sudo-pending ]' "BROKEN: a runtime passwd writes no sudoers file, admin stays off"
ok '! printf "%s\n" "$NEWPW" | su nduser -c "sudo -S -k true" 2>/dev/null' "BROKEN: sudo with the new runtime password is refused"
ok '[ ! -e /etc/ssh/sshd_config.d/10-coindojo-owner-ssh.conf ]' "BROKEN: a runtime passwd writes no password-SSH drop-in (#299)"
# #299: the image's key-only drop-in (chroot-setup.sh; the container's stock sshd would allow passwords for everyone).
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitEmptyPasswords no\nPermitRootLogin no\nPubkeyAuthentication yes\n' \
  > /etc/ssh/sshd_config.d/00-coin-dojo-keyonly.conf
# The way in: the SD card in a computer, password_hash in coindojo.yml on the boot partition, boot.
H=$(printf '%s' "$BOOTPW" | openssl passwd -6 -stdin)
printf "password_hash: '%s'\n" "$H" > $B/coindojo.yml
systemctl restart coindojo-firstboot.service; rc=$?
ok '[ $rc -eq 0 ] && [ -e /etc/sudoers.d/coindojo-admin ] && [ ! -e /etc/coindojo/sudo-pending ] && [ ! -e $B/coindojo.yml ]' "coindojo.yml password_hash at boot -> sudoers.d/coindojo-admin, applied and removed"
ok '[ "$(stat -c %a /etc/sudoers.d/coindojo-admin)" = 440 ] && ! ls /etc/sudoers.d | grep -q tmp' "the rule is 0440, no temp file left"
ok 'printf "%s\n" "$BOOTPW" | su nduser -c "sudo -S -k true" 2>/dev/null' "sudo with the boot-partition password works"
ok '! printf "12345\n" | su nduser -c "sudo -S -k true" 2>/dev/null' "sudo with 12345 is still refused"
ok 'journalctl -u coindojo-firstboot.service --no-pager | grep -q "admin (sudo) on for nduser"' "the journal says admin is on"
# #299: the same boot turned password SSH on for nduser only, checked by the real sshd (Match blocks applied).
# Captured first: under pipefail, grep -q closing the pipe early would fail the check (SIGPIPE on sshd)
SSHT() { local o; o=$(/usr/sbin/sshd -T -C "user=$1,host=x,addr=127.0.0.1" 2>&1); printf '%s\n' "$o"; }
ok '[ -e /etc/ssh/sshd_config.d/10-coindojo-owner-ssh.conf ] && /usr/sbin/sshd -t' "password_hash at boot -> the owner drop-in, sshd -t passes"
ok 'SSHT nduser > /tmp/ssht.n; grep -qx "passwordauthentication yes" /tmp/ssht.n' "real sshd: password SSH on for nduser"
ok 'SSHT root > /tmp/ssht.r; grep -qx "passwordauthentication no" /tmp/ssht.r && grep -qx "permitrootlogin no" /tmp/ssht.r' "real sshd: root keeps no password SSH, no root login"
ok 'SSHT someoneelse > /tmp/ssht.o; grep -qx "passwordauthentication no" /tmp/ssht.o && grep -qx "pubkeyauthentication yes" /tmp/ssht.n' "real sshd: other users stay key-only, keys still on"
ok '! journalctl --no-pager | grep -q -e "$NEWPW" -e "$BOOTPW"' "no password is in the journal"

echo "--- 11. #270 re-review: root never writes the user's home through the user's symlinks (broken cases) ---"
ETC0=$(stat -c '%a %U' /etc); SH0=$(stat -c '%a %U %G' /etc/shadow); SHM=$(md5sum < /etc/shadow)
same(){ [ "$(stat -c '%a %U' /etc)" = "$ETC0" ] && [ "$(stat -c '%a %U %G' /etc/shadow)" = "$SH0" ] && [ "$(md5sum < /etc/shadow)" = "$SHM" ] && [ ! -e /etc/authorized_keys ]; }
H=~nduser
rm -rf "$H/.ssh"; su nduser -c "ln -s /etc $H/.ssh"
printf 'ssh_pubkey: "%s"\n' "$KEY" > $B/coindojo.yml; systemctl restart coindojo-firstboot.service; rc=$?
ok 'same && [ $rc -ne 0 ] && grep -q "not a plain folder" $B/coindojo.error.txt' "BROKEN: ~/.ssh -> /etc: /etc and /etc/shadow untouched, the key refused with a reason"
systemctl reset-failed coindojo-firstboot.service
rm -f "$H/.ssh"; su nduser -c "mkdir -m 700 $H/.ssh && ln -s /etc/shadow $H/.ssh/authorized_keys"
printf 'ssh_pubkey: "%s"\n' "$KEY" > $B/coindojo.yml; systemctl restart coindojo-firstboot.service; rc=$?
ok 'same && [ $rc -ne 0 ] && [ -L $H/.ssh/authorized_keys ] && [ -z "$(find $H/.ssh -type f)" ] && ! grep -rqs "^root:" "$H"' "BROKEN: authorized_keys -> /etc/shadow: shadow untouched, not copied into the home"
systemctl reset-failed coindojo-firstboot.service
su nduser -c "rm $H/.ssh/authorized_keys && ln -s /etc/shadow $H/.ssh/authorized_keys.tmp"
printf 'ssh_pubkey: "%s"\n' "$KEY" > $B/coindojo.yml; systemctl restart coindojo-firstboot.service; rc=$?
ok 'same && [ $rc -eq 0 ] && [ ! -L $H/.ssh/authorized_keys ] && grep -qF "$KEY" $H/.ssh/authorized_keys && [ ! -e $H/.ssh/authorized_keys.tmp ]' "BROKEN: a planted authorized_keys.tmp -> /etc/shadow: shadow untouched, the key in a real file"
ok '[ "$(stat -c "%U %a" $H/.ssh/authorized_keys)" = "nduser 600" ] && [ "$(stat -c "%U %a" $H/.ssh)" = "nduser 700" ]' "written as nduser: authorized_keys nduser 600, ~/.ssh nduser 700"

[ "$fails" -eq 0 ] && echo "RESULT: PASS" || { echo "RESULT: FAIL ($fails)"; exit 1; }
