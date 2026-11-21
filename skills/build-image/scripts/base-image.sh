#!/usr/bin/env bash
# base-image.sh - fetch the official Raspberry Pi OS Lite arm64 image, sha256-pinned, and decompress it (#237).
#   base-image.sh [DEST_DIR]        (make base-image [BASE_DIR=...]; default ~/.cache/coin-dojo/base-image)
# Prints the path to use as BASE_IMG. Nothing is redistributed: this downloads from downloads.raspberrypi.com.
# The pin (URL + sha256 of the .img.xz, from the official .sha256 file) is below. A newer release: set BOTH
# BASE_URL and BASE_SHA256 (the release's .img.xz and its published sha256). A sha mismatch deletes the download.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib-host.sh
. "$HERE/lib-host.sh"

# Pin: Raspberry Pi OS Lite (trixie) arm64, 2026-10-06; sha256 from its published .img.xz.sha256 (checked 2026-11-19).
PIN_URL=https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-10-06/2026-10-06-raspios-trixie-arm64-lite.img.xz
PIN_SHA256=483db18a48da399b5b7022ffae9a07bc6d99daab30e593aedac09b69ff422843

if [ -n "${BASE_URL:-}${BASE_SHA256:-}" ]; then
  [ -n "${BASE_URL:-}" ] && [ -n "${BASE_SHA256:-}" ] || hk_die "set both BASE_URL and BASE_SHA256 (a URL without its sha256 is not accepted)"
  URL=$BASE_URL; SHA=$BASE_SHA256
else
  URL=$PIN_URL; SHA=$PIN_SHA256
fi
case "$SHA" in *[!0-9a-f]*|"") hk_die "BASE_SHA256 must be 64 lowercase hex digits" ;; esac
[ ${#SHA} -eq 64 ] || hk_die "BASE_SHA256 must be 64 lowercase hex digits"
case "$URL" in *.img.xz) ;; *) hk_die "the URL must point at a .img.xz: $URL" ;; esac

DEST=${1:-${BASE_DIR:-$HOME/.cache/coin-dojo/base-image}}
mkdir -p "$DEST"
XZ="$DEST/$(basename "$URL")"; IMG="${XZ%.xz}"

if [ -f "$XZ" ] && [ "$(hk_sha256 "$XZ")" = "$SHA" ]; then
  echo ">> $XZ already downloaded (sha256 ok)"
else
  command -v curl >/dev/null 2>&1 || hk_die "curl not found"
  echo ">> downloading $URL"
  rm -f "$XZ.part"
  curl -fL --retry 3 --progress-bar -o "$XZ.part" "$URL" || { rm -f "$XZ.part"; hk_die "download failed: $URL"; }
  GOT=$(hk_sha256 "$XZ.part")
  if [ "$GOT" != "$SHA" ]; then
    rm -f "$XZ.part"
    hk_die "sha256 mismatch for $(basename "$URL"): got $GOT, pinned $SHA (download deleted)"
  fi
  mv "$XZ.part" "$XZ"
  echo ">> sha256 ok ($SHA)"
fi

if [ ! -f "$IMG" ]; then
  echo ">> decompressing -> $IMG"
  if command -v xz >/dev/null 2>&1; then xz -dc "$XZ" > "$IMG.part"
  else python3 -c 'import lzma,shutil,sys; shutil.copyfileobj(lzma.open(sys.argv[1]), open(sys.argv[2], "wb"), 1 << 20)' "$XZ" "$IMG.part"; fi \
    || { rm -f "$IMG.part"; hk_die "decompressing $XZ failed"; }
  mv "$IMG.part" "$IMG"
fi
echo "BASE_IMG=$IMG"
