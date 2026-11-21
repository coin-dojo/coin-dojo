#!/bin/bash
# Repartition a Raspberry Pi OS base image into the Coin Dojo 3-partition layout:
#   p1 boot (FAT, unchanged)  p2 root (ext4, grown)  p3 data (ext4, /data)
# The data partition is a small placeholder here; firstboot-expand grows it to
# fill the whole SD card on first boot. The MBR disk-id is preserved so the
# existing root=PARTUUID= reference keeps working.
#
# Usage (inside an arm64 privileged container):
#   prep-image.sh <base.img> <out.img> [ROOT_GIB=7] [DATA_MIB=512]
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
BASE=${1:?base image}; OUT=${2:?output image}
ROOT_GIB=${3:-7}; DATA_MIB=${4:-512}

command -v sfdisk >/dev/null || { apt-get update -qq >/dev/null && apt-get install -y -qq fdisk e2fsprogs >/dev/null; }

# Detach on ANY exit (#237 round 2): a failed prep must not leak loop devices.
RL=; DL=
cleanup(){ for L in $RL $DL; do losetup -d "$L" 2>/dev/null; done; }
trap cleanup EXIT

echo ">> copying base -> out"
cp --reflink=auto "$BASE" "$OUT"

# read existing layout
DISKID=$(sfdisk --disk-id "$OUT")
read -r P1_START P1_SIZE < <(sfdisk -d "$OUT" | awk -F'[=,]' '/ : start=/{c++; if(c==1){s=$2;z=$4;gsub(/ /,"",s);gsub(/ /,"",z);print s,z;exit}}')
P2_START=$(sfdisk -d "$OUT" | awk -F'[=,]' '/ : start=/{c++; if(c==2){s=$2;gsub(/ /,"",s);print s;exit}}')
echo ">> disk-id=$DISKID  p1=$P1_START/$P1_SIZE  p2_start=$P2_START"

# compute new geometry (sectors)
ROOT_SIZE=$(( ROOT_GIB * 2097152 ))              # GiB -> 512B sectors
ROOT_END=$(( P2_START + ROOT_SIZE - 1 ))
DATA_START=$(( ((ROOT_END + 1 + 2047) / 2048) * 2048 ))   # align up to 1MiB
DATA_SIZE=$(( DATA_MIB * 2048 ))
DATA_END=$(( DATA_START + DATA_SIZE - 1 ))
TOTAL=$(( DATA_END + 1 ))
echo ">> new: root=$P2_START+$ROOT_SIZE  data=$DATA_START+$DATA_SIZE  total_sectors=$TOTAL"

echo ">> growing image file"
truncate -s $(( TOTAL * 512 )) "$OUT"

echo ">> writing 3-partition table (disk-id preserved)"
sfdisk "$OUT" >/dev/null <<SFD
label: dos
label-id: $DISKID
unit: sectors
start=$P1_START, size=$P1_SIZE, type=c
start=$P2_START, size=$ROOT_SIZE, type=83
start=$DATA_START, size=$DATA_SIZE, type=83
SFD

echo ">> resize rootfs (p2) to fill its partition"
RL=$(losetup --find --show --offset $((P2_START*512)) --sizelimit $((ROOT_SIZE*512)) "$OUT")
e2fsck -f -p "$RL" || true
resize2fs "$RL"
losetup -d "$RL"; RL=

echo ">> mkfs ext4 on data (p3), label=coin-dojo-data"
DL=$(losetup --find --show --offset $((DATA_START*512)) --sizelimit $((DATA_SIZE*512)) "$OUT")
mkfs.ext4 -q -L coin-dojo-data "$DL"
losetup -d "$DL"; DL=

echo "PREP_OK diskid=$DISKID data_partuuid=${DISKID#0x}-03"
