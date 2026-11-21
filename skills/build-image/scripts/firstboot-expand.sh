#!/bin/bash
# Runs ONCE on first boot on the cabinet: grow the data partition (p3) to fill
# the whole SD card, then grow its ext4. Idempotent + self-disabling. Works on any card size: the data
# partition is last, so it takes whatever the card has beyond root (16, 32, 64 GB ...; #237).
# Test overrides (the loop-device proof, tests/prove-grow.sh): HK_FIRSTBOOT_MARK, HK_DISK, HK_DATA_MNT, HK_DATA_OWNER.
set -e
MARK=${HK_FIRSTBOOT_MARK:-/var/lib/coin-dojo-firstboot-pending}
[ -e "$MARK" ] || exit 0

if [ -n "${HK_DISK:-}" ]; then
  DISK=$HK_DISK
else
  ROOT_SRC=$(findmnt -no SOURCE /)                 # e.g. /dev/mmcblk0p2
  DISK=/dev/$(lsblk -no pkname "$ROOT_SRC")        # e.g. /dev/mmcblk0
fi
case "$DISK" in *[0-9]) PSEP=p;; *) PSEP=;; esac
P3="${DISK}${PSEP}3"
DATA=${HK_DATA_MNT:-/data}

echo "firstboot: growing $P3 to fill $DISK"
growpart "$DISK" 3 || true
e2fsck -f -p "$P3" || true
resize2fs "$P3" || true

mkdir -p "$DATA"
mountpoint -q "$DATA" || mount "$P3" "$DATA"
mkdir -p "$DATA/roms" "$DATA/bios" "$DATA/saves" "$DATA/states" "$DATA/media"
# #296: exactly the launcher's systems (launcher_table.py --format systems; themes/tests/test_rom_system_art.py checks it)
for s in arcade atari2600 cps1 cps2 cps3 dreamcast fbneo genesis n64 naomi neogeo nes pcengine psx saturn segacd snes; do
  mkdir -p "$DATA/roms/$s"
done
# #298: Fightcade's own ROM versions (searched before the arcade folders); not a launcher system, so not in the loop
mkdir -p "$DATA/roms/fightcade"
chown -R "${HK_DATA_OWNER:-coindojo:coindojo}" "$DATA"

rm -f "$MARK"
systemctl disable coin-dojo-firstboot.service 2>/dev/null || true
echo "firstboot: done"
