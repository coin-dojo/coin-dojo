#!/bin/bash
# prove-grow.sh - first-boot data growth on several card sizes (#237). Runs INSIDE a privileged Linux container:
#   make -C skills/build-image prove-grow IMG=<built.img> [SIZES="16000000000 64000000000"]
# For each size: copy IMG (never touched), extend the copy to a sparse "card" of that many bytes, attach it as a
# loop device and run the real firstboot-expand.sh against it (HK_DISK etc.). PASS = p3 ends within 1 MiB of the
# card's end and its ext4 fills p3. Broken control (BROKEN=1): growpart stubbed to do nothing -> must FAIL.
set -uo pipefail
IMG=${1:?image}; shift
SIZES=${*:-16000000000 64000000000}
HERE=$(cd "$(dirname "$0")" && pwd)
EXPAND=${EXPAND:-$HERE/../scripts/firstboot-expand.sh}
SCR=${SCR:-/work/.prove-grow.$$}
export DEBIAN_FRONTEND=noninteractive
command -v growpart >/dev/null && command -v sfdisk >/dev/null && command -v resize2fs >/dev/null \
  || { apt-get update -qq >/dev/null && apt-get install -y -qq cloud-guest-utils fdisk e2fsprogs >/dev/null; }
mkdir -p "$SCR" /mnt/pg
fails=0
# Partition device nodes do not appear in a container's /dev: make them from sysfs.
nodes(){ local L=${1#/dev/} p; for p in /sys/block/"$L"/"$L"p*; do [ -e "$p/dev" ] || continue
  rm -f "/dev/$(basename "$p")"; mknod "/dev/$(basename "$p")" b "$(cut -d: -f1 "$p/dev")" "$(cut -d: -f2 "$p/dev")"; done; }
# A shim in front of growpart: the real one, then fresh nodes (udev does this on the Pi; a container has no udev).
# BROKEN=1 swaps in a growpart that does nothing: the check below must then FAIL.
REAL_GROWPART=$(command -v growpart); mkdir -p "$SCR/stub"
# shellcheck disable=SC2016   # the $ expressions belong to the generated shim, not to this shell
if [ "${BROKEN:-0}" = 1 ]; then printf '#!/bin/sh\necho NOCHANGE\nexit 1\n' > "$SCR/stub/growpart"; echo ">> BROKEN control: growpart does nothing"
else printf '#!/bin/bash\n%s "$@"; rc=$?\nL=${1#/dev/}; for p in /sys/block/$L/${L}p*; do rm -f /dev/${p##*/}; mknod /dev/${p##*/} b $(cut -d: -f1 $p/dev) $(cut -d: -f2 $p/dev); done\nexit $rc\n' "$REAL_GROWPART" > "$SCR/stub/growpart"; fi
chmod +x "$SCR/stub/growpart"; export PATH="$SCR/stub:$PATH"
for SIZE in $SIZES; do
  CARD="$SCR/card-$SIZE.img"
  cp --sparse=always "$IMG" "$CARD"
  [ "$(stat -c %s "$CARD")" -le "$SIZE" ] || { echo "FAIL: image is bigger than a $SIZE-byte card"; fails=$((fails+1)); rm -f "$CARD"; continue; }
  truncate -s "$SIZE" "$CARD"
  L=$(losetup -fP --show "$CARD"); nodes "$L"
  touch "$SCR/mark"
  HK_FIRSTBOOT_MARK="$SCR/mark" HK_DISK="$L" HK_DATA_MNT=/mnt/pg HK_DATA_OWNER=0:0 \
    bash "$EXPAND" > "$SCR/expand.log" 2>&1 || true
  # systemctl is absent in the container; the script's `|| true` covers it.
  umount /mnt/pg 2>/dev/null; partx -u "$L" 2>/dev/null; nodes "$L"
  read -r P3S P3Z < <(sfdisk -d "$L" | awk -F'[=,]' '/ : start=/{c++; if(c==3){s=$2;z=$4;gsub(/ /,"",s);gsub(/ /,"",z);print s,z}}')
  END=$(( (P3S + P3Z) * 512 )); GAP=$(( SIZE - END ))
  FSB=$(dumpe2fs -h "${L}p3" 2>/dev/null | awk -F: '/^Block count/{c=$2}/^Block size/{b=$2}END{printf "%.0f\n", c*b}')
  case "$FSB$P3S$P3Z" in ''|*[!0-9]*) echo "FAIL: could not read p3/ext4 sizes (fs=$FSB p3=$P3S/$P3Z)"; fails=$((fails+1)); losetup -d "$L"; rm -f "$CARD"; continue ;; esac
  PB=$(( P3Z * 512 ))
  echo ">> card $SIZE B: p3 = $((PB/1048576)) MiB, ends $((GAP/1024)) KiB before the card end; ext4 = $((FSB/1048576)) MiB"
  if [ "$GAP" -lt 1048576 ] && [ "$GAP" -ge 0 ] && [ $(( PB - FSB )) -lt 4194304 ]; then
    echo "PASS: data grows to fill a $((SIZE/1000000000)) GB card ($((PB/1073741824)) GiB data)"
  else
    echo "FAIL: data did not fill the $((SIZE/1000000000)) GB card"; sed 's/^/   | /' "$SCR/expand.log"; fails=$((fails+1))
  fi
  [ -e "$SCR/mark" ] && { echo "FAIL: first-boot marker not removed"; fails=$((fails+1)); }
  losetup -d "$L"; rm -f "$CARD"
done
rm -rf "$SCR"
[ "$fails" -eq 0 ] && echo "RESULT: PASS" || { echo "RESULT: FAIL ($fails)"; exit 1; }
