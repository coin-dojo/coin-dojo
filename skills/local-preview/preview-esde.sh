#!/usr/bin/env bash
# Render ES-DE with a theme + sample collections in an arm64 container and save screenshots,
# so theme / collection / settings changes are verified BEFORE they touch the cabinet.
#
# SHIP NO ARTIFACTS: ES-DE is YOUR extracted AppImage (cached on this Mac, never in git); the
# fixture ROMs are empty placeholder files; no art, BIOS or real ROMs are involved.
#
# Usage:  skills/local-preview/preview-esde.sh <theme-dir> <out-dir> [--view system|gamelist|both]
#             [--keys "Return Down Down"] [--fixture DIR] [--appimage FILE] [--keep]
# Writes: <out-dir>/system.png, gamelist.png (per --view), es_log.txt, es-stdout.log
# Exit:   0 = rendered with the theme and no theme errors; 1 = theme fell back / has errors;
#         2 = bad usage or missing prereq; 3 = ES-DE crashed / timed out.
# Cache:  $COIN_DOJO_PREVIEW_CACHE (default $HK_CACHE/preview, ~/.cache/coin-dojo/preview): AppImage, extracted tree,
#         scratch runs. Must live under $HOME (Colima only shares $HOME with containers).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ESDE="$HERE/esde"
CACHE="${COIN_DOJO_PREVIEW_CACHE:-${HK_CACHE:-$HOME/.cache/coin-dojo}/preview}"
FIXTURE="$HERE/fixtures/esde-preview"
APPIMAGE="${ESDE_APPIMAGE:-$CACHE/es-de/ES-DE_aarch64.AppImage}"
VIEW=both KEYS=Return KEEP=0 TIMEOUT=240

die() { echo "ERROR: $*" >&2; exit 2; }
usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

[ $# -ge 2 ] || usage
THEME_DIR="$1"; OUT_DIR="$2"; shift 2
while [ $# -gt 0 ]; do
  case "$1" in
    --view) VIEW="${2:-}"; shift 2 ;;
    --keys) KEYS="${2:-}"; shift 2 ;;
    --fixture) FIXTURE="${2:-}"; shift 2 ;;
    --appimage) APPIMAGE="${2:-}"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    *) usage ;;
  esac
done
case "$VIEW" in system|gamelist|both) ;; *) die "--view must be system, gamelist or both" ;; esac
[ -f "$THEME_DIR/theme.xml" ] || die "not an ES-DE theme (no theme.xml): $THEME_DIR"
[ -d "$FIXTURE/gamelists" ] && [ -d "$FIXTURE/collections" ] || die "bad fixture dir: $FIXTURE"
command -v docker >/dev/null && docker info >/dev/null 2>&1 || die "docker/Colima not running (colima start)"
command -v python3 >/dev/null || die "python3 missing"
[ -f "$APPIMAGE" ] || die "ES-DE AppImage not found: $APPIMAGE
  Supply your own (same 3.5.0 aarch64 build the cabinet runs), e.g.:
  mkdir -p $CACHE/es-de && scp coindojo@<cabinet>:/opt/ES-DE/ES-DE_aarch64.AppImage $CACHE/es-de/"
case "$(cd "$THEME_DIR" && pwd)" in "$HOME"/*) ;; *) die "theme dir must be under \$HOME (Colima shares only \$HOME): $THEME_DIR" ;; esac
THEME_DIR="$(cd "$THEME_DIR" && pwd)"
THEME_NAME="$(basename "$THEME_DIR")"
mkdir -p "$OUT_DIR"; OUT_DIR="$(cd "$OUT_DIR" && pwd)"
rm -f "$OUT_DIR"/system.png "$OUT_DIR"/gamelist.png "$OUT_DIR"/es_log.txt

# 1. image (cached by Dockerfile hash)
TAG="coin-dojo-esde-preview:$(shasum -a 256 "$ESDE/Dockerfile" | cut -c1-8)"
if ! docker image inspect "$TAG" >/dev/null 2>&1; then
  echo ">> building $TAG (one-time, ~1 min)"
  docker build -q --platform linux/arm64 -t "$TAG" "$ESDE" >/dev/null 2>&1
fi

# 2. extract the AppImage once per AppImage (needs no FUSE: --appimage-extract inside the container)
SQ="$CACHE/es-de/squashfs-root"
STAMP="$CACHE/es-de/.extracted-from"
SUM="$(shasum -a 256 "$APPIMAGE" | cut -d' ' -f1)"
if [ ! -x "$SQ/AppRun" ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$SUM" ]; then
  echo ">> extracting $(basename "$APPIMAGE") (one-time)"
  rm -rf "$SQ"
  AIDIR="$(cd "$(dirname "$APPIMAGE")" && pwd)"
  docker run --rm --platform linux/arm64 -v "$AIDIR":/in:ro -v "$CACHE/es-de":/w -w /w "$TAG" \
    bash -c "cp /in/$(basename "$APPIMAGE") /tmp/es.AppImage && /tmp/es.AppImage --appimage-extract >/dev/null 2>&1 ; [ -x /w/squashfs-root/AppRun ]" \
    || die "AppImage extraction failed"
  echo "$SUM" > "$STAMP"
fi

# 3. throwaway home + fake ROM tree
RUN="$(mktemp -d "$CACHE/run-XXXXXX")"
NAME="coin-dojo-esde-$$"
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; [ "$KEEP" = 1 ] && echo "kept: $RUN" || rm -rf "$RUN"; }
trap cleanup EXIT
mkdir -p "$RUN/out"
python3 -I "$ESDE/make_home.py" "$FIXTURE" /preview/theme "$RUN" --theme-name "$THEME_NAME"

# 4. render (host-side watchdog: the container is killed after $TIMEOUT s)
echo ">> rendering theme '$THEME_NAME' (view=$VIEW, software GL, ~30 s)"
docker run -d --name "$NAME" --platform linux/arm64 -e VIEW="$VIEW" -e KEYS="$KEYS" \
  -v "$SQ":/es:ro -v "$RUN/home":/preview/home -v "$RUN/roms":/preview/roms:ro \
  -v "$RUN/media":/preview/media:ro -v "$THEME_DIR":/preview/theme:ro -v "$RUN/out":/preview/out \
  -v "$ESDE/run-es.sh":/run-es.sh:ro "$TAG" /run-es.sh >/dev/null
RC=""; for _ in $(seq 1 "$TIMEOUT"); do
  [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" = false ] && { RC="$(docker inspect -f '{{.State.ExitCode}}' "$NAME")"; break; }
  sleep 1
done
docker logs "$NAME" 2>&1 | grep '^RUN:' || true
[ -n "$RC" ] || { echo "ERROR: container timed out after ${TIMEOUT}s" >&2; exit 3; }
cp "$RUN"/out/*.png "$RUN"/out/es_log.txt "$RUN"/out/es-stdout.log "$OUT_DIR"/ 2>/dev/null || true
[ "$RC" = 0 ] || { echo "ERROR: render failed (exit $RC); see $OUT_DIR/es-stdout.log" >&2; exit 3; }

# 5. fail loudly if ES-DE fell back to its built-in theme or the theme logged errors
[ -f "$OUT_DIR/es_log.txt" ] || { echo "ERROR: no es_log.txt" >&2; exit 3; }
python3 -I "$ESDE/check_log.py" "$OUT_DIR/es_log.txt" "$THEME_NAME" || exit 1
for f in system gamelist; do
  case "$VIEW:$f" in system:gamelist|gamelist:system) continue ;; esac
  [ "$(wc -c < "$OUT_DIR/$f.png" 2>/dev/null || echo 0)" -gt 20000 ] || { echo "ERROR: $f.png missing or blank" >&2; exit 3; }
done
echo "OK: screenshots in $OUT_DIR"
