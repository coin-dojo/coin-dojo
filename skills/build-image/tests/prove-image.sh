#!/bin/bash
# shellcheck disable=SC2016,SC2034,SC2012  # ok '...' checks are eval'd later (single quotes, vars used there)
# prove-image.sh - first boot inside a COPY of a built image, in a chroot (#237). Runs INSIDE a privileged arm64
# Linux container: make -C skills/build-image prove-image IMG=<built.img>   (the image itself is never touched)
# Mounts the copy's root + boot, chroots in and runs the image's own /usr/local/sbin/coindojo-firstboot --apply
# (its python3, usermod, chpasswd, ssh-keygen) against the coindojo.yml that the build put on boot. PASS =
# the config is applied and no secret is left on the boot partition. Also: openssl in the image hashes from
# stdin (the personal flow). Every check prints PASS/FAIL; the last line is RESULT.
set -uo pipefail
IMG=${1:?image}
SCR=${SCR:-/work/.prove-image}
export DEBIAN_FRONTEND=noninteractive
command -v sfdisk >/dev/null || { apt-get update -qq >/dev/null && apt-get install -y -qq fdisk >/dev/null; }
fails=0
ok(){ if eval "$1"; then echo "PASS: $2"; else echo "FAIL: $2"; fails=$((fails+1)); fi; }
mkdir -p "$SCR"; COPY="$SCR/copy.img"
cp --sparse=always "$IMG" "$COPY"
part(){ sfdisk -d "$COPY" | awk -F'[=,]' -v n="$1" -v f="$2" '/ : start=/{c++; if(c==n){s=$2;z=$4;gsub(/ /,"",s);gsub(/ /,"",z); if(f=="start")print s; else print z; exit}}'; }
RL=$(losetup --find --show --offset $(( $(part 2 start) * 512 )) --sizelimit $(( $(part 2 size) * 512 )) "$COPY")
BL=$(losetup --find --show --offset $(( $(part 1 start) * 512 )) --sizelimit $(( $(part 1 size) * 512 )) "$COPY")
R=/mnt/pi; mkdir -p $R; mount "$RL" $R; mount "$BL" $R/boot/firmware
for d in dev proc sys; do mount --bind /$d $R/$d; done
cleanup(){ for d in sys proc dev boot/firmware; do umount $R/$d 2>/dev/null; done; umount $R 2>/dev/null
  losetup -d "$RL" "$BL" 2>/dev/null; rm -rf "$SCR"; }
trap cleanup EXIT
B=$R/boot/firmware
BUILT=$(cat $R/etc/coindojo/built-user)
echo ">> image user: $BUILT; boot has: $(cd $B && ls coindojo* 2>/dev/null | tr '\n' ' ')"
ok '[ -f $B/coindojo.yml ]' "the build put coindojo.yml on the boot partition"
PSK=$(sed -n 's/^wifi_psk:[[:space:]]*"\{0,1\}\([^"]*\)"\{0,1\}.*/\1/p' $B/coindojo.yml)
HASH=$(sed -n "s/^password_hash:[[:space:]]*'\{0,1\}\([^']*\)'\{0,1\}.*/\1/p" $B/coindojo.yml)
NEWU=$(sed -n 's/^user:[[:space:]]*//p' $B/coindojo.yml | tr -d "\"'"); NEWU=${NEWU:-$BUILT}
ok '[ "$(grep "^$BUILT:" $R/etc/shadow | cut -d: -f2 | cut -c1)" = "!" ]' "before first boot: $BUILT is locked"
chroot $R /usr/local/sbin/coindojo-firstboot --apply > "$SCR/apply.log" 2>&1; rc=$?
sed 's/^/   | /' "$SCR/apply.log"
ok '[ $rc -eq 0 ]' "the image's coindojo-firstboot --apply ran in the chroot (rc $rc)"
# #244: user: may only name the image's own user (make image refuses any other), so nothing is renamed.
ok '[ "$NEWU" = "$BUILT" ] && grep -q "^$NEWU:" $R/etc/passwd && [ -d $R/home/$NEWU/.config/retroarch ]' "user: is the image's own user $BUILT (no rename)"
ok '[ "$(grep "^$NEWU:" $R/etc/shadow | cut -d: -f2)" = "$HASH" ]' "password hash applied"
ok '[ -s $R/home/$NEWU/.ssh/authorized_keys ]' "SSH key installed"
ok 'ls $R/etc/ssh/ssh_host_ed25519_key >/dev/null 2>&1' "SSH host keys made"
ok 'grep -q "^psk=" $R/etc/NetworkManager/system-connections/coindojo-wifi.nmconnection' "Wi-Fi keyfile written"
ok '[ ! -e $B/coindojo.yml ] && [ -f $B/coindojo.applied.yml ]' "coindojo.yml removed, applied copy left"
ok '! grep -rqsF -e "$PSK" -e "$HASH" $B' "no PSK or hash left on the boot partition"
ok 'grep -q -- "--autologin $NEWU " $R/etc/systemd/system/getty@tty1.service.d/autologin.conf' "tty1 autologin is $NEWU"
H=$(printf '%s' EXAMPLE-not-real | chroot $R openssl passwd -6 -stdin 2>/dev/null)
ok 'case "$H" in \$6\$*) true;; *) false;; esac' "openssl in the image hashes a password from stdin (personal flow)"
[ "$fails" -eq 0 ] && echo "RESULT: PASS" || { echo "RESULT: FAIL ($fails)"; exit 1; }
