#!/bin/bash
# Release step 1 (#287): re-check a built image's licence gate WITHOUT changing the image. Runs INSIDE the pinned
# Ansible container (the one provision.sh runs in: python3, losetup, mount), privileged, with the image's folder at
# /work READ-ONLY, the licence-text cache at /cache (read-write), the repo at its own path and an output folder at /out:
#
#   release-inspect.sh /work/<image.img> <repo root>
#
# The partitions are attached READ-ONLY (losetup -r). The root gets a tmpfs overlay, the boot partition is copied to
# tmpfs, so `licences.py build --strict` can write its bundle without touching the image. Then:
#   1. /out/GATE.tsv = the image's own record (licences.py build wrote it at provision);
#   2. licences.py fetch + build --strict over the overlay (same --version as the record) must exit 0;
#   3. licences.py release-check --root over the overlay must exit 0 (no tip link in the image);
#   4. the regenerated licence folder and LICENCES.md must equal the image's byte for byte (the bundle in the image is
#      exactly what strict produces from this repo now: same manifest, same build/*/out BUILDINFO, same licence texts).
# It copies out the image's licence folder (/out/licences) and the Raspberry Pi archive keyring (/out/keyring.pgp)
# for the sources step. Last line: INSPECT_OK, or INSPECT_FAIL <why> (exit 1).
set -uo pipefail
IMG=${1:?image}; REPO=${2:?repo root}
DOC=usr/share/doc/coindojo/licences
LIC="$REPO/skills/build-image/scripts/licences.py"
fail(){ echo "INSPECT_FAIL $*"; exit 1; }
[ -f "$IMG" ] || fail "no image at $IMG"
[ -f "$LIC" ] || fail "no licences.py at $LIC (is the repo mounted at its own path?)"
read -r P1S P1Z P2S P2Z P3S P3Z < <(python3 -I -c 'import struct, sys
d = open(sys.argv[1], "rb").read(512)
print(" ".join("%d %d" % struct.unpack_from("<II", d, 446 + 16 * i + 8) for i in range(3)))' "$IMG")
[ "${P3Z:-0}" -gt 0 ] || fail "$IMG has no data partition (not a built image)"

RL=; BL=; DL=; O=/ov
cleanup(){ set +e
  umount "$O/merged/data" "$O/merged" /mnt/ro-data /mnt/ro-boot /mnt/ro-root "$O" 2>/dev/null
  for L in $RL $BL $DL; do losetup -d "$L" 2>/dev/null; done
}
trap cleanup EXIT
RL=$(losetup -r --find --show --offset $((P2S*512)) --sizelimit $((P2Z*512)) "$IMG") || fail "losetup root"
BL=$(losetup -r --find --show --offset $((P1S*512)) --sizelimit $((P1Z*512)) "$IMG") || fail "losetup boot"
DL=$(losetup -r --find --show --offset $((P3S*512)) --sizelimit $((P3Z*512)) "$IMG") || fail "losetup data"
mkdir -p /mnt/ro-root /mnt/ro-boot /mnt/ro-data "$O"
mount -o ro "$RL" /mnt/ro-root && mount -o ro "$BL" /mnt/ro-boot && mount -o ro "$DL" /mnt/ro-data || fail "mount (read-only)"
mount -t tmpfs tmpfs "$O" && mkdir -p "$O/up" "$O/wk" "$O/merged" "$O/boot" || fail "tmpfs"
mount -t overlay overlay -o "lowerdir=/mnt/ro-root,upperdir=$O/up,workdir=$O/wk" "$O/merged" || fail "overlay"
mount --bind -o ro /mnt/ro-data "$O/merged/data" 2>/dev/null || mount --bind /mnt/ro-data "$O/merged/data" || fail "bind data"
cp -a /mnt/ro-boot/. "$O/boot/" || fail "copy boot"

[ -f "/mnt/ro-root/$DOC/GATE.tsv" ] || fail "the image has no $DOC/GATE.tsv: it was built before #287 recorded the licence gate; rebuild it with HK_LICENCES_STRICT=1"
mkdir -p /out && rm -rf /out/licences && cp -a "/mnt/ro-root/$DOC" /out/licences && cp "/mnt/ro-root/$DOC/GATE.tsv" /out/GATE.tsv \
  || fail "copy out the licence folder"
KR=/mnt/ro-root/usr/share/keyrings/raspberrypi-archive-keyring.pgp
[ ! -f "$KR" ] || cp "$KR" /out/keyring.pgp
VERSION=$(awk -F'\t' '$1=="version"{print $2}' /out/GATE.tsv)
echo ">> image record: $(grep -v '^#' /out/GATE.tsv | tr '\t\n' '= ')"

echo ">> licences.py fetch + build --strict over a tmpfs overlay (the image is read-only)"
python3 -I "$LIC" fetch --strict --repo "$REPO" --cache /cache || fail "licences.py fetch --strict"
python3 -I "$LIC" build --strict --repo "$REPO" --root "$O/merged" --boot "$O/boot" --cache /cache \
  --version "${VERSION:-unknown}" || fail "licences.py build --strict refuses this image"
python3 -I "$LIC" release-check --root "$O/merged" --boot "$O/boot" || fail "licences.py release-check: tip link in the image"
diff -r "/mnt/ro-root/$DOC" "$O/merged/$DOC" >/dev/null \
  || fail "the image's licence folder differs from what build --strict writes now (stale image or changed repo)"
cmp -s /mnt/ro-boot/LICENCES.md "$O/boot/LICENCES.md" || fail "the boot partition's LICENCES.md differs from what build --strict writes now"
echo "INSPECT_OK"
