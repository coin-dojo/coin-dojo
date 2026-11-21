#!/bin/bash
# Image build step 3 (#244): run the cabinet's Ansible roles into the image (ansible/image.yml), so the image is what a
# deploy makes. Runs INSIDE the pinned Ansible container (ansible/Dockerfile, the image bin/ans builds), privileged,
# with the repo mounted at its own path and the work folder at /work:
#
#   provision.sh /work/<out.img> <repo root>
#
# Mounts root (/mnt/root), boot (/mnt/root/boot/firmware) and data (/mnt/root/data), binds /dev /proc /sys, renders the
# config (tools/config.py: the declared defaults, your local file, HK_SET; EDITION picks the local file as everywhere),
# runs the play over a chroot connection, then seals: the cabinet user owns its home and /data, no SSH host keys, no
# apt cache, no Ansible leftovers. No secret reaches the play: no become password (a chroot runs as root), no
# RetroAchievements token or key (bin/deploy is not involved).
# Env: EDITION (personal|community, default community), HK_SET, COIN_DOJO_VERSION (the theme's REV stamp),
#      HK_LICENCES_STRICT=1 exactly (a public release: licences.py strict, #254; 0 or empty = off).
set -euo pipefail
IMG=${1:?image}; REPO=${2:?repo root}
[ -f "$REPO/ansible/image.yml" ] || { echo "ERROR: $REPO/ansible/image.yml not found (is the repo mounted at its own path?)" >&2; exit 2; }
export EDITION=${EDITION:-community}
R=/mnt/root

# Partition offsets from the MBR (sectors): python, as this container has no sfdisk.
read -r P1S P1Z P2S P2Z P3S P3Z < <(python3 -I -c 'import struct, sys
d = open(sys.argv[1], "rb").read(512)
print(" ".join("%d %d" % struct.unpack_from("<II", d, 446 + 16 * i + 8) for i in range(3)))' "$IMG")
echo ">> parts: boot=$P1S/$P1Z root=$P2S/$P2Z data=$P3S/$P3Z"
[ "$P3Z" -gt 0 ] || { echo "ERROR: no data partition in $IMG (run prep-image.sh first)" >&2; exit 2; }

RL=; BL=; DL=
cleanup(){ set +e
  umount "$R/data" "$R/boot/firmware" "$R/dev/pts" "$R/dev" "$R/proc" "$R/sys" "$R" 2>/dev/null
  for L in $RL $BL $DL; do losetup -d "$L" 2>/dev/null; done
}
trap cleanup EXIT
RL=$(losetup --find --show --offset $((P2S*512)) --sizelimit $((P2Z*512)) "$IMG")
BL=$(losetup --find --show --offset $((P1S*512)) --sizelimit $((P1Z*512)) "$IMG")
DL=$(losetup --find --show --offset $((P3S*512)) --sizelimit $((P3Z*512)) "$IMG")
mkdir -p "$R"; mount "$RL" "$R"
mkdir -p "$R/boot/firmware" "$R/data"; mount "$BL" "$R/boot/firmware"; mount "$DL" "$R/data"
mount --bind /dev "$R/dev"; mount --bind /dev/pts "$R/dev/pts"; mount --bind /proc "$R/proc"; mount --bind /sys "$R/sys"
cp /etc/resolv.conf "$R/etc/resolv.conf"

# The config the play gets: what `make deploy` renders for the cabinet. hk_cabinet_host is required there but means
# nothing to an image (no SSH), so a placeholder fills it; your HK_SET still wins.
CFG=$(mktemp -d)
HK_SET="hk_cabinet_host=image.invalid ${HK_SET:-}" python3 "$REPO/tools/config.py" render --limit coin-dojo --out "$CFG" >/dev/null \
  || { echo "ERROR: the configuration is invalid (make config-check shows why)" >&2; exit 2; }

# The roles render hk_cabinet_uid into /run/user/<uid> (session, flycast, Fightcade, powerd, trophiesd): the image's
# user must have exactly that uid, or the cabinet would run in another user's runtime dir (#244).
CU=$(cat "$R/etc/coindojo/built-user")
WANT_UID=$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["hk_cabinet_uid"])' "$CFG/effective-coin-dojo.json")
HAVE_UID=$(chroot "$R" id -u "$CU" 2>/dev/null || echo none)
[ "$HAVE_UID" = "$WANT_UID" ] \
  || { echo "ERROR: image user $CU has uid $HAVE_UID but hk_cabinet_uid is $WANT_UID (chroot-setup.sh makes the user with HK_CABINET_UID)" >&2; exit 2; }
echo ">> image user $CU has uid $HAVE_UID = hk_cabinet_uid"

echo ">> ansible: image.yml into the image (EDITION=$EDITION)"
cd "$REPO/ansible"
ANSIBLE_LOCAL_TEMP=/tmp/.ansible-local ANSIBLE_FORCE_COLOR=0 ansible-playbook -i inventory/image.yml image.yml \
  -e "@$CFG/effective-coin-dojo.json" -e "coin_dojo_edition=$EDITION" -e "coin_dojo_version=${COIN_DOJO_VERSION:-unknown}"
rm -rf "$CFG"

# ---- licences (#254, #267): /usr/share/doc/coindojo/licences + LICENCES.md on boot, generated from
# skills/build-image/licences/components.json and the image's own dpkg database (never hand-copied). It refuses a
# binary no component accounts for (a core added without its licence row) only with HK_LICENCES_STRICT=1 exactly
# (the public release), which also refuses every open release blocker. Otherwise (=0, empty, unset) the step never
# fails the build: problems are WARN lines and offline personal builds keep working. licences.py reads the variable.
echo ">> licences: the licence folder and LICENCES.md (strict: ${HK_LICENCES_STRICT:-off})"
LIC="$REPO/skills/build-image/scripts/licences.py"
if [ "${HK_LICENCES_STRICT:-}" = 1 ]; then
  python3 -I "$LIC" fetch --repo "$REPO" --cache /work/cache/licences
  python3 -I "$LIC" build --repo "$REPO" --root "$R" --cache /work/cache/licences --version "${COIN_DOJO_VERSION:-unknown}"
else
  python3 -I "$LIC" fetch --repo "$REPO" --cache /work/cache/licences || echo "WARN: licences fetch failed (not strict: continuing)"
  python3 -I "$LIC" build --repo "$REPO" --root "$R" --cache /work/cache/licences --version "${COIN_DOJO_VERSION:-unknown}" \
    || echo "WARN: licences build failed (not strict: continuing)"
fi

# ---- seal (ownership is the play's last step: image.yml post_tasks) ----
echo ">> seal: no SSH host keys, apt cache or Ansible leftovers"
CU=$(cat "$R/etc/coindojo/built-user")
rm -f "$R"/etc/ssh/ssh_host_*
chroot "$R" apt-get clean
rm -rf "$R/root/.ansible" "$R/home/$CU/.ansible" "$R"/tmp/ansible* "$R"/tmp/hk-*
echo "PROVISION_DONE"
