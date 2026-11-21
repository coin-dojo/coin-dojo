#!/usr/bin/env bash
# Host-side orchestrator: build the arcade OS image end-to-end in privileged Docker containers.
# prep -> build (base OS) -> provision (the cabinet's Ansible roles, ansible/image.yml, #244) -> verify, then
# <out.img>.sha256. Runs on Linux x86_64/arm64 and macOS (Colima or Docker
# Desktop); on an x86_64 docker host the arm64 chroot runs through qemu-user binfmt (lib-host.sh, #237).
#
# Usage:  run.sh <base-pi-os-lite.img> <output.img> [ROOT_GIB=7] [DATA_MIB=512]
# Env:    MIN_CARD_GB=16   refuse to build an image that would not fit this card size (imgsize.py)
#         HK_BINFMT=auto   x86_64 only: register qemu-aarch64 through the pinned binfmt image; no = refuse
#         CONFIG=path      a coindojo.yml (coindojo.example.yml) checked now and put on the boot partition
#         EDITION=community|personal   which local config and overlays the roles see (default community)
#         HK_SET="hk_x=v ..."          config overrides, as for make deploy (e.g. hk_image_fightcade=true)
#         HK_IMAGE_FIGHTCADE, HK_FIGHTCADE_INSTALL, HK_ROM_ROOT   skip the config lookup of these (like HK_CABINET_USER)
#         HK_LICENCES_STRICT=1   a public release: the licence bundle refuses every open release blocker (#254);
#                                exactly 1 (0 or empty = off: licence problems are WARN lines, offline builds work)
# Inputs the roles copy from your own build folder (never in git): build/retroarch, build/n64, build/flycast,
#          build/stella (make -C build all); with hk_image_fightcade also build/box64 and build/fightcade
#          With HK_LICENCES_STRICT=1 also the eight pinned cores, build/<core> (make -C build cores, #274); without it
#          a core that is not built comes from the libretro buildbot (WARN, personal use only).
#          (not build/fightcade with hk_fightcade_install: first-launch, #267: the image then holds no Fightcade binaries).
# Secrets: none in the root filesystem (#237). The first-boot config on the boot partition is CONFIG=, else
#          (the Keychain flow) one generated from COINDOJO_PASSWORD (alias CABINET_PW, else the macOS Keychain
#          item named by hk_become_keychain) and COINDOJO_PUBKEY (alias COIN_DOJO_PUBKEY, else
#          ~/.ssh/id_ed25519.pub in the Keychain flow), else none: the cabinet shows how to add one.
#          EDITION=community with no password there: the default login <user> / 12345 (#265; console and network folder, no sudo: #270).
# SSH is key-only by default; COIN_DOJO_SSH_PASSWORD=yes opts in to password SSH (not recommended; refused with the
#          community default password).
# Optional offline cache: put ES-DE_aarch64.AppImage + cores/*.so in <workdir>/cache
#          (workdir = the folder holding <output.img>) to skip downloads. The pinned licence texts are kept in
#          <workdir>/cache/licences (sha256-named, re-checked on every build).
#
# SHIP NO ARTIFACTS: needs a base Pi OS Lite image you supply (or make base-image); bundles nothing.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib-host.sh
. "$HERE/lib-host.sh"
BASE=${1:?base image path}; OUT=${2:?output image path}
ROOT_GIB=${3:-7}; DATA_MIB=${4:-512}; MIN_CARD_GB=${MIN_CARD_GB:-16}
T0=$SECONDS
step(){ echo "== $* (t+$((SECONDS - T0))s) =="; }
REPO=$(cd "$HERE/../../.." && pwd)
HKCFG="$REPO/tools/hkcfg"
# make exports EDITION (mk/flags.mk); a direct run builds the community edition (nothing from personal/).
export EDITION=${EDITION:-community} HK_SET=${HK_SET:-}
case "$EDITION" in
  personal) echo "== EDITION: PERSONAL. This image holds your personal art and lists: never share or publish it ==" ;;
  community) echo "== EDITION: community (nothing from personal/) ==" ;;
  *) hk_die "EDITION='$EDITION': use community or personal" ;;
esac

# Fightcade mode in the image (#244, hk_image_fightcade): its install (~1.3 GB) goes to the data partition, so that
# partition starts at 3072 MiB at least (it still grows to fill the card at first boot).
FIGHTCADE=${HK_IMAGE_FIGHTCADE:-$("$HKCFG" hk_image_fightcade 2>/dev/null || true)}
case "$FIGHTCADE" in true|True) FIGHTCADE=true ;; *) FIGHTCADE=false ;; esac
# #267 hk_fightcade_install: first-launch = the image holds none of Wine/Electron/Fightcade (the cabinet downloads them
# the first time FIGHTCADE is picked): no build/fightcade needed and no data partition bump (it grows at first boot).
FC_INSTALL=${HK_FIGHTCADE_INSTALL:-$("$HKCFG" hk_fightcade_install 2>/dev/null || true)}
case "$FC_INSTALL" in first-launch) ;; *) FC_INSTALL=deploy ;; esac
# A community image is one people share: it must never bundle Fightcade's binaries (proprietary; Coordinator 2026-11-19).
# #294: so a community image with Fightcade mode (the default since #294) always uses first-launch.
if [ "$FIGHTCADE" = true ] && [ "$FC_INSTALL" = deploy ] && [ "$EDITION" = community ]; then
  FC_INSTALL=first-launch
  echo ">> EDITION=community: Fightcade mode installs on the first FIGHTCADE pick (first-launch); nothing of it is bundled"
fi
[ "$FIGHTCADE" = false ] || echo ">> Fightcade mode in the image, install: $FC_INSTALL"
# #301: the roles in the container read the config (provision.sh renders it with HK_SET): hand them run.sh's decisions,
# or a community build switched to first-launch above still runs the fightcade role in deploy mode.
HK_SET="${HK_SET:+$HK_SET }hk_image_fightcade=$FIGHTCADE hk_fightcade_install=$FC_INSTALL"
if [ "$FIGHTCADE" = true ] && [ "$FC_INSTALL" = deploy ] && [ "$DATA_MIB" -lt 3072 ]; then
  echo ">> hk_image_fightcade: data partition $DATA_MIB -> 3072 MiB (Fightcade mode + its Wine prefix need ~2.1 GB there)"; DATA_MIB=3072
fi

# ---- fast-fail: inputs, size, docker, arm64, mounts, space (nothing heavy yet) ----
[ -f "$BASE" ] || hk_die "base image not found: $BASE (download Raspberry Pi OS Lite arm64, or make base-image)"
command -v python3 >/dev/null 2>&1 || hk_die "python3 not found (needed for the size check and the config loader)"
mkdir -p "$(dirname "$OUT")"
WORK=$(cd "$(dirname "$OUT")" && pwd); OUT_B=$(basename "$OUT")
BASE_DIR=$(cd "$(dirname "$BASE")" && pwd); BASE_B=$(basename "$BASE")
[ "$BASE_DIR/$BASE_B" != "$WORK/$OUT_B" ] || hk_die "output must not be the base image itself"
if [ -n "${CONFIG:-}" ]; then
  [ -f "$CONFIG" ] || hk_die "CONFIG file not found: $CONFIG"
  python3 "$HERE/coindojo-firstboot.py" --check "$CONFIG" || hk_die "CONFIG=$CONFIG is malformed (see coindojo.example.yml)"
fi
python3 "$HERE/imgsize.py" "$BASE" "$ROOT_GIB" "$DATA_MIB" "$MIN_CARD_GB"
IMG_MIB=$(( 1024 + ROOT_GIB * 1024 + DATA_MIB ))
hk_check_docker
hk_ensure_arm64
hk_check_mount "$WORK"
[ "$BASE_DIR" = "$WORK" ] || hk_check_mount "$BASE_DIR"
hk_check_mount "$REPO"
hk_check_space "$WORK" $(( IMG_MIB + 512 ))
# The roles copy these from your build folder (sha256-pinned or self-checked there); missing = stop now, not mid-build.
NEED="retroarch/out/retroarch n64/out/mupen64plus_next_libretro.so flycast/out/flycast stella/out/stella_libretro.so"
[ "$FIGHTCADE" = false ] || NEED="$NEED box64/out/box64"
# the Fightcade file name comes from the one pin source (build/fightcade/pins.json)
FC_FILE=$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["fightcade"]["file"])' "$REPO/build/fightcade/pins.json") \
  || hk_die "cannot read build/fightcade/pins.json"
[ "$FIGHTCADE" = false ] || [ "$FC_INSTALL" = first-launch ] || NEED="$NEED fightcade/out/$FC_FILE"
# A release (HK_LICENCES_STRICT=1) installs only libretro cores built from their pinned commits (#274): every core the
# licence manifest lists as built must be there now. Otherwise the cores role downloads a core that is not built from
# the buildbot, with a WARN line (fine for personal use, not for a release).
if [ "${HK_LICENCES_STRICT:-}" = 1 ]; then
  PINNED=$(python3 -I -c 'import json, os, sys
for c in json.load(open(sys.argv[1]))["components"]:
    if c.get("core") and c.get("origin") == "built":
        print("%s/out/%s_libretro.so" % (os.path.basename(c["build"]), c["core"]))' \
    "$REPO/skills/build-image/licences/components.json") || hk_die "cannot read skills/build-image/licences/components.json"
  NEED="$NEED $PINNED"
fi
# IMAGE_BUILD_DIR: only the tests point this check at a fake tree (the roles always read <repo>/build).
for f in $NEED; do
  [ -f "${IMAGE_BUILD_DIR:-$REPO/build}/$f" ] || hk_die "build/$f is missing: build it first (make -C build ${f%%/*}; the emulators: make -C build all)"
done

# ---- names from the config (tools/hkcfg -> tools/config.py get): cabinet user, hostname, Keychain item ----
export HK_CABINET_USER HK_CABINET_UID HK_CABINET_HOSTNAME
[ -n "${HK_CABINET_USER:-}" ] || HK_CABINET_USER=$("$HKCFG" hk_cabinet_user) \
  || hk_die "cannot read hk_cabinet_user from the config: run make config-venv (PyYAML), or export HK_CABINET_USER and HK_CABINET_HOSTNAME"
# #244: the image's user is fixed now (the roles bake its name in): a CONFIG= naming another user: stops here.
[ -z "${CONFIG:-}" ] || python3 "$HERE/coindojo-firstboot.py" --check "$CONFIG" --user "$HK_CABINET_USER" >/dev/null \
  || hk_die "CONFIG=$CONFIG names another user: than hk_cabinet_user=$HK_CABINET_USER (leave user: empty, or set hk_cabinet_user)"
# The uid the roles render into /run/user/<uid>: chroot-setup.sh creates the user with it, provision.sh checks it (#244).
[ -n "${HK_CABINET_UID:-}" ] || HK_CABINET_UID=$("$HKCFG" hk_cabinet_uid) \
  || hk_die "cannot read hk_cabinet_uid from the config: run make config-venv (PyYAML), or export HK_CABINET_UID"
[ -n "${HK_CABINET_HOSTNAME:-}" ] || HK_CABINET_HOSTNAME=$("$HKCFG" hk_cabinet_hostname) \
  || hk_die "cannot read hk_cabinet_hostname from the config: run make config-venv (PyYAML), or export HK_CABINET_HOSTNAME"
# First-boot secrets (personal flow only when no CONFIG). Neutral names first, the old ones as aliases.
export COINDOJO_PASSWORD COINDOJO_PUBKEY
COINDOJO_PASSWORD=${COINDOJO_PASSWORD:-${CABINET_PW:-}}
COINDOJO_PUBKEY=${COINDOJO_PUBKEY:-${COIN_DOJO_PUBKEY:-}}
if [ -n "${CONFIG:-}" ]; then
  COINDOJO_PASSWORD=; COINDOJO_PUBKEY=
else
  KC=
  [ -n "$COINDOJO_PASSWORD" ] || ! command -v security >/dev/null 2>&1 || KC=$("$HKCFG" hk_become_keychain 2>/dev/null || true)
  if [ -n "$KC" ]; then
    if COINDOJO_PASSWORD=$(security find-generic-password -s "$KC" -w 2>/dev/null); then
      echo ">> personal flow: password from the Keychain item named by hk_become_keychain"
      [ -n "$COINDOJO_PUBKEY" ] || COINDOJO_PUBKEY=$(cat "$HOME/.ssh/id_ed25519.pub" 2>/dev/null || true)
    else
      COINDOJO_PASSWORD=; echo ">> WARN: Keychain item $KC (hk_become_keychain) not found; building without a first-boot password"
    fi
  fi
  [ -n "$COINDOJO_PASSWORD$COINDOJO_PUBKEY" ] \
    || echo ">> no CONFIG=: the image has no first-boot config; put coindojo.yml on its boot partition after flashing"
fi
export COIN_DOJO_SSH_PASSWORD=${COIN_DOJO_SSH_PASSWORD:-no}
# #265: a community image without a password of yours gets the default login password 12345 (build.sh). That
# password must never meet password SSH: stop now, before anything heavy, instead of at the end of the chroot.
if [ "$EDITION" = community ] && [ "$COIN_DOJO_SSH_PASSWORD" = yes ] && [ -z "$COINDOJO_PASSWORD" ] \
   && ! { [ -n "${CONFIG:-}" ] && python3 "$HERE/coindojo-firstboot.py" --sets-password "$CONFIG"; }; then
  hk_die "COIN_DOJO_SSH_PASSWORD=yes with the community default password 12345 would let anyone on your network log in: keep SSH key-only, or give a password (CONFIG= with password_hash, or COINDOJO_PASSWORD)"
fi

# stage the scripts beside the image so the container (which mounts WORK=/work) sees them
STAGED="prep-image.sh build.sh chroot-setup.sh provision.sh firstboot-expand.sh wifi-import.sh verify.sh coindojo-firstboot.py"
for f in $STAGED; do cp "$HERE/$f" "$WORK/"; done
cp "$HERE/../coindojo.example.yml" "$WORK/"
cleanup(){ for f in $STAGED coindojo.example.yml .coindojo.yml ".verify.$OUT_B.log" .expect-paths; do rm -f "$WORK/$f"; done; }
trap cleanup EXIT
[ -z "${CONFIG:-}" ] || ( umask 077; cp "$CONFIG" "$WORK/.coindojo.yml" )
# What verify.sh must find for the launcher (#244): launch.py's own cores, binaries and ROM folders, never a list here.
ROM_ROOT=${HK_ROM_ROOT:-$("$HKCFG" hk_rom_root)} || hk_die "cannot read hk_rom_root from the config"
python3 -I "$REPO/ansible/roles/cores/files/launcher_table.py" --format paths --rom-root "${ROM_ROOT:-/data/roms}" > "$WORK/.expect-paths" \
  || hk_die "skills/collections/launch.py has broken tables (launcher_table.py says why)"
# The Ansible control container (ansible/Dockerfile, pinned); bin/ans builds it on first use.
"$BASH" "$REPO/ansible/bin/ans" true </dev/null || hk_die "the pinned Ansible container could not be built (ansible/bin/ans)"
ANS_IMAGE=$("$BASH" "$REPO/ansible/bin/ans" --image)
COIN_DOJO_VERSION=$(git -C "$REPO" describe --tags --always --dirty=+dirty 2>/dev/null || echo unknown)
export COIN_DOJO_VERSION IMAGE_FIGHTCADE=$FIGHTCADE IMAGE_FIGHTCADE_INSTALL=$FC_INSTALL

# prep and verify run on the host's own arch; only the build (the Pi OS chroot) needs arm64.
dr(){ docker run --rm --privileged -v "$WORK":/work -v "$BASE_DIR":/base:ro "$@"; }
# A personal image gets first-boot secrets on its boot partition: create it owner-only BEFORE anything writes
# into it, so a failed build or verify never leaves a world-readable image holding a Wi-Fi password. prep's cp
# writes into the existing file and keeps its mode.
SECRET_IMG=; [ -z "${CONFIG:-}${COINDOJO_PASSWORD:-}${COINDOJO_PUBKEY:-}" ] || SECRET_IMG=1
if [ -n "$SECRET_IMG" ]; then rm -f "$WORK/$OUT_B" "$WORK/$OUT_B.sha256"; ( umask 077; : > "$WORK/$OUT_B" ); fi
step "prep (repartition -> boot/root/data)"
dr "$HK_DEBIAN_IMAGE" bash /work/prep-image.sh "/base/$BASE_B" "/work/$OUT_B" "$ROOT_GIB" "$DATA_MIB"
step "build (chroot install, arm64)"
dr --platform linux/arm64 -e EDITION -e COINDOJO_PASSWORD -e COINDOJO_PUBKEY -e COIN_DOJO_SSH_PASSWORD -e HK_CABINET_USER -e HK_CABINET_UID -e HK_CABINET_HOSTNAME \
  "$HK_DEBIAN_IMAGE" bash /work/build.sh "/work/$OUT_B"
step "provision (the cabinet's Ansible roles into the image, chroot)"
# The repo at its own path (the roles, launch.py, build/*/out); no SSH key, no Keychain, no become password.
docker run --rm --privileged -v "$WORK":/work -v "$REPO":"$REPO" -e EDITION -e HK_SET -e COIN_DOJO_VERSION -e HK_LICENCES_STRICT \
  "$ANS_IMAGE" bash /work/provision.sh "/work/$OUT_B" "$REPO"
step "verify"
VLOG="$WORK/.verify.$OUT_B.log"
dr -e EDITION -e COINDOJO_PASSWORD -e COIN_DOJO_SSH_PASSWORD -e HK_CABINET_USER -e HK_CABINET_UID -e HK_CABINET_HOSTNAME -e IMAGE_FIGHTCADE -e IMAGE_FIGHTCADE_INSTALL -e HK_LICENCES_STRICT "$HK_DEBIAN_IMAGE" \
  bash /work/verify.sh "/work/$OUT_B" | tee "$VLOG"
# The image file belongs to you, not root (Linux docker writes as root; a no-op on macOS shares).
dr "$HK_DEBIAN_IMAGE" chown "$(id -u):$(id -g)" "/work/$OUT_B" 2>/dev/null || true
if grep -q '^FAIL' "$VLOG" || ! grep -q '^VERIFY_DONE' "$VLOG"; then
  hk_die "verify failed (FAIL lines above): do not flash $OUT"
fi
SUM=$(hk_sha256 "$WORK/$OUT_B")
printf '%s  %s\n' "$SUM" "$OUT_B" > "$WORK/$OUT_B.sha256"
step "done"
echo "DONE -> $OUT (EDITION=$EDITION, $(( $(wc -c < "$WORK/$OUT_B") / 1048576 )) MiB, sha256 $SUM in $OUT_B.sha256, $((SECONDS - T0))s)"
# A personal image (CONFIG= or the personal flow) carries its first-boot secrets on the boot partition until the
# first boot: Wi-Fi password, password hash. Owner-only files and a loud warning (#237 round 2).
if [ -n "$SECRET_IMG" ]; then
  chmod 600 "$WORK/$OUT_B" "$WORK/$OUT_B.sha256"
  echo "WARNING: this image contains your first-boot secrets (Wi-Fi password, password hash) until its first boot:"
  echo "WARNING: never share it or upload it. It is mode 600; flash it, boot it once, then delete it."
fi
