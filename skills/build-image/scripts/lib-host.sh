#!/usr/bin/env bash
# Host-side preflight for run.sh (#237): works on Linux x86_64/arm64 and macOS (Colima or Docker Desktop).
# Sourced by run.sh and by tests/test_portability.py (which puts a stub `docker` first on PATH).
# Every check fails fast with one clear ERROR line before any heavy step. No Mac-only tools here.

# Pinned images (multi-arch index digests, checked 2026-11-19 against registry-1.docker.io).
HK_DEBIAN_IMAGE=${HK_DEBIAN_IMAGE:-debian:12@sha256:268fb24c39bca92d326dd83ea3abebe4a3f7751c804ffef23bb6d857dd343ff8}
HK_BINFMT_IMAGE=${HK_BINFMT_IMAGE:-tonistiigi/binfmt:qemu-v10.0.4@sha256:8f58e6214f4cc9dc83ce8f5acad1ece508eb6b20e696a8c1e9f274481982c541}

hk_die(){ echo "ERROR: $*" >&2; exit 1; }

# docker installed and its daemon answering.
hk_check_docker(){
  command -v docker >/dev/null 2>&1 \
    || hk_die "docker not found. Install Docker: Linux = Docker Engine (docs.docker.com/engine/install), macOS = Colima (brew install colima docker; colima start) or Docker Desktop"
  docker info >/dev/null 2>&1 \
    || hk_die "docker is installed but its daemon does not answer. Start it: Linux = sudo systemctl start docker (and add yourself to the docker group), macOS = colima start, or open Docker Desktop"
}

# The daemon's CPU (on a Mac that is the Linux VM's, not the Mac's). Prints arm64 or amd64.
hk_daemon_arch(){
  local a; a=$(docker info --format '{{.Architecture}}' 2>/dev/null) || a=
  case "$a" in
    aarch64|arm64) echo arm64 ;;
    x86_64|amd64) echo amd64 ;;
    *) hk_die "unknown docker host architecture '${a:-?}' (need aarch64 or x86_64)" ;;
  esac
}

# Can the daemon run an arm64 container? Native on arm64; on x86_64 only through qemu-user binfmt.
hk_can_run_arm64(){
  [ "$(docker run --rm --platform linux/arm64 "$HK_DEBIAN_IMAGE" uname -m 2>/dev/null)" = aarch64 ]
}

# arm64 is required for the Pi OS chroot. On x86_64, register qemu-aarch64 through the pinned binfmt
# image (HK_BINFMT=auto, the default) or refuse (HK_BINFMT=no).
hk_ensure_arm64(){
  local arch; arch=$(hk_daemon_arch)
  if hk_can_run_arm64; then
    [ "$arch" = arm64 ] && echo ">> docker host is arm64: the Pi OS chroot runs natively" \
                        || echo ">> docker host is x86_64: arm64 runs through qemu-user binfmt (already registered; slower)"
    return 0
  fi
  [ "$arch" = arm64 ] && hk_die "the arm64 docker host cannot run an arm64 container (docker run --platform linux/arm64 failed); check 'docker run --rm $HK_DEBIAN_IMAGE uname -m'"
  if [ "${HK_BINFMT:-auto}" != auto ]; then
    hk_die "docker host is x86_64 and no qemu-aarch64 binfmt handler is registered (HK_BINFMT=${HK_BINFMT}). Register it once per host boot with: docker run --privileged --rm $HK_BINFMT_IMAGE --install arm64"
  fi
  echo ">> docker host is x86_64: registering the qemu-aarch64 binfmt handler (pinned $HK_BINFMT_IMAGE)."
  echo ">>   This changes the host kernel's binfmt_misc table until reboot; undo: docker run --privileged --rm $HK_BINFMT_IMAGE --uninstall qemu-aarch64"
  docker run --privileged --rm "$HK_BINFMT_IMAGE" --install arm64 >/dev/null \
    || hk_die "registering qemu-aarch64 binfmt failed (needs a privileged container)"
  hk_can_run_arm64 \
    || hk_die "qemu-aarch64 binfmt registered but an arm64 container still does not start"
  echo ">> arm64 emulation works (expect a build several times slower than on an arm64 host)"
}

# The daemon must see WORK (Colima shares only \$HOME by default; Docker Desktop its file-sharing list).
hk_check_mount(){
  local work=$1 probe=".hk-mount-probe.$$"
  : > "$work/$probe" || hk_die "cannot write to $work"
  if ! docker run --rm -v "$work":/work "$HK_DEBIAN_IMAGE" test -f "/work/$probe" >/dev/null 2>&1; then
    rm -f "$work/$probe"
    hk_die "docker cannot see $work. Put IMAGE_OUT in a folder the docker VM shares (Colima: under \$HOME; Docker Desktop: Settings > Resources > File sharing)"
  fi
  rm -f "$work/$probe"
}

# Free space in WORK, in MiB (POSIX df, same on Linux and macOS).
hk_free_mib(){ df -Pk "$1" | awk 'NR==2{print int($4/1024)}'; }

hk_check_space(){
  local work=$1 need_mib=$2 free
  free=$(hk_free_mib "$work")
  [ "${free:-0}" -ge "$need_mib" ] \
    || hk_die "not enough free space in $work: ${free:-?} MiB free, the build needs about $need_mib MiB"
}

# sha256 of a file: sha256sum (Linux) or shasum -a 256 (macOS).
hk_sha256(){
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  else hk_die "need sha256sum or shasum"; fi
}
