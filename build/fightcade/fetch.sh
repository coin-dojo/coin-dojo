#!/bin/bash
# shellcheck disable=SC2034  # the pins are read by name
# Fetch the sha256-pinned third-party downloads the Fightcade mode needs (#207, part A of #204):
# Wine 10.0 (amd64 wow64), Electron 9.1.0 (linux-arm64) and the Fightcade linux tarball. Nothing is
# unpacked here: a download is only moved into place after its sha256 matched (a mismatch deletes it).
# Usage: fetch.sh [--out DIR] [--help]
# Exit codes: 0 ok, 1 download failed or sha256/size mismatch, 2 bad usage or bad pin, 3 curl or python3 missing.
set -euo pipefail

# ---- pins: build/fightcade/pins.json is the ONE source (#267) -----------------
# The fightcade role (deploy and image) and the cabinet's first-launch download read the same file. Change a
# version there only. Every url names its version (Fightcade: .../Fightcade-linux-v<version>.tar.gz), so a mismatch
# means a damaged or changed file; a new Fightcade release is a re-pin (README.md "Re-pinning Fightcade").
# ----------------------------------------------------------------------------

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib.sh
source "$HERE/../lib.sh"
OUT="$HERE/out"
PINS="$HERE/pins.json"

usage() {
  cat <<USAGE
Usage: $(basename "$0") [--out DIR]

Downloads the Wine, Electron and Fightcade files pinned in $PINS into DIR (default: $OUT,
gitignored), each sha256- and size-checked before it is kept (Electron also against its SHASUMS256.txt),
and writes VERSION.txt (the Fightcade version) and SHA256SUMS. Already-correct files are kept.
Env FETCH_URL_PREFIX: tests only, replaces the URL directory part with a local file:// directory.
Exit codes: 0 ok, 1 download failed / sha256 or size mismatch, 2 bad usage or bad pin, 3 curl or python3 missing.
USAGE
}

build_parse_args "$@"
command -v curl >/dev/null 2>&1 || build_fail 3 "curl not found"
command -v python3 >/dev/null 2>&1 || build_fail 3 "python3 not found (it reads $PINS)"
# pins.json -> WINE_URL WINE_FILE WINE_SHA256 WINE_SIZE, ELECTRON_* (+ ELECTRON_SHASUMS_URL), FIGHTCADE_* (+ _VERSION).
# A missing key or a url that is not https is a bad pin (exit 2).
PIN_VARS="$(python3 -I - "$PINS" <<'PY'
import json, shlex, sys
try:
    p = json.load(open(sys.argv[1], encoding="utf-8"))
    out = []
    for name in ("wine", "electron", "fightcade"):
        for k in ("url", "file", "sha256", "size"):
            v = p[name][k]
            if k == "url" and not str(v).startswith("https://"):
                raise ValueError("%s.url is not https" % name)
            out.append("%s_%s=%s" % (name.upper(), k.upper(), shlex.quote(str(v))))
    out.append("ELECTRON_SHASUMS_URL=%s" % shlex.quote(p["electron"]["shasums_url"]))
    out.append("FIGHTCADE_VERSION=%s" % shlex.quote(p["fightcade"]["version"]))
except (OSError, ValueError, KeyError, TypeError) as e:
    sys.exit("bad pins file %s: %s" % (sys.argv[1], e))
print("\n".join(out))
PY
)" || build_fail 2 "cannot read the pins in $PINS"
eval "$PIN_VARS"
build_require_re WINE_SHA256 '^[0-9a-f]{64}$' "a sha256"
build_require_re ELECTRON_SHA256 '^[0-9a-f]{64}$' "a sha256"
build_require_re FIGHTCADE_SHA256 '^[0-9a-f]{64}$' "a sha256"
build_require_re WINE_SIZE '^[0-9]+$' "a size in bytes"
build_require_re ELECTRON_SIZE '^[0-9]+$' "a size in bytes"
build_require_re FIGHTCADE_SIZE '^[0-9]+$' "a size in bytes"
# shellcheck disable=SC2119  # no argument: the --under-home check is not wanted here
build_prepare_out

TMP=""
trap '[ -z "$TMP" ] || rm -rf "$TMP"' EXIT
TMP="$(mktemp -d "$OUT/.fetch.XXXXXX")"

# url_for URL: the real URL, or PREFIX/<basename> for the tests.
url_for() { if [ -n "${FETCH_URL_PREFIX:-}" ]; then echo "$FETCH_URL_PREFIX/$(basename "$1")"; else echo "$1"; fi; }

# fetch_pinned URL FILE SHA256 SIZE [RE-PIN HINT]: download to TMP, compare, move into OUT only on a match. The size
# is pinned too (the cabinet's first-launch download shows it and stops there), so a wrong size pin fails here.
fetch_pinned() {
  local url="$1" file="$2" want="$3" size="$4" hint="${5:-}" got
  if [ -f "$OUT/$file" ] && [ "$(build_sha256_of "$OUT/$file")" = "$want" ]; then
    echo "ok (kept): $file"
    return 0
  fi
  echo "fetching $file"
  curl -fL --retry 3 --connect-timeout 20 -sS -o "$TMP/$file" "$(url_for "$url")" \
    || build_fail 1 "download failed: $url"
  got="$(build_sha256_of "$TMP/$file")"
  if [ "$got" != "$want" ]; then
    rm -f "$TMP/$file"
    build_fail 1 "sha256 mismatch for $file: got $got, pinned $want${hint:+ ($hint)}"
  fi
  got="$(wc -c < "$TMP/$file" | tr -d ' ')"
  if [ "$got" != "$size" ]; then
    rm -f "$TMP/$file"
    build_fail 1 "size of $file is $got bytes, pins.json says $size: fix the size pin"
  fi
  mv -f "$TMP/$file" "$OUT/$file"
  echo "ok: $file"
}

fetch_pinned "$WINE_URL" "$WINE_FILE" "$WINE_SHA256" "$WINE_SIZE"

# Electron: the pin AND the release's own SHASUMS256.txt must agree with the download. The check runs
# whenever the zip is (re)downloaded; a kept, pin-matching zip needs no network.
check_electron_shasums() {
  local upstream
  curl -fL --retry 3 --connect-timeout 20 -sS -o "$TMP/SHASUMS256.txt" "$(url_for "$ELECTRON_SHASUMS_URL")" \
    || build_fail 1 "download failed: $ELECTRON_SHASUMS_URL"
  upstream="$(awk -v f="*$ELECTRON_FILE" '$2 == f {print $1}' "$TMP/SHASUMS256.txt")"
  [ -n "$upstream" ] || build_fail 1 "$ELECTRON_FILE not listed in Electron's SHASUMS256.txt"
  [ "$upstream" = "$ELECTRON_SHA256" ] \
    || build_fail 1 "pinned Electron sha256 differs from SHASUMS256.txt: pinned $ELECTRON_SHA256, upstream $upstream"
}
if [ -f "$OUT/$ELECTRON_FILE" ] && [ "$(build_sha256_of "$OUT/$ELECTRON_FILE")" = "$ELECTRON_SHA256" ]; then
  echo "ok (kept): $ELECTRON_FILE"
else
  check_electron_shasums
  fetch_pinned "$ELECTRON_URL" "$ELECTRON_FILE" "$ELECTRON_SHA256" "$ELECTRON_SIZE"
fi

fetch_pinned "$FIGHTCADE_URL" "$FIGHTCADE_FILE" "$FIGHTCADE_SHA256" "$FIGHTCADE_SIZE" \
  "the pinned versioned Fightcade file changed or arrived damaged; fetch again, and re-pin only after testing (build/fightcade/README.md)"

# VERSION.txt: read the one file out of the (already verified) tarball, never unpack it.
ver="$(tar -xzOf "$OUT/$FIGHTCADE_FILE" Fightcade/VERSION.txt 2>/dev/null | tr -d '[:space:]' || true)"
[ "$ver" = "$FIGHTCADE_VERSION" ] || build_fail 1 "Fightcade VERSION.txt says '$ver', pinned $FIGHTCADE_VERSION"
printf '%s\n' "$ver" > "$OUT/VERSION.txt"
{
  printf '%s  %s\n' "$WINE_SHA256" "$WINE_FILE"
  printf '%s  %s\n' "$ELECTRON_SHA256" "$ELECTRON_FILE"
  printf '%s  %s\n' "$FIGHTCADE_SHA256" "$FIGHTCADE_FILE"
} > "$OUT/SHA256SUMS"
echo "done: $OUT"
cat "$OUT/SHA256SUMS"
