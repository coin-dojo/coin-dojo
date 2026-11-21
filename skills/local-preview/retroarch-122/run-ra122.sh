#!/bin/bash
# Inside the container: cabinet RetroArch 1.22.2 in a headless wlroots session (cage), driven over UDP 55355.
# Env: ROM (file name under ROM_DIR), BOOT (s to let the game run after RetroArch answers, default 20),
#      READY_WAIT (s for the Wayland socket and for RetroArch's first answer, default 60),
#      SETTLE / HOLD (s between overlay steps, default 1.5 / 2). Paths (tests point them elsewhere):
#      OUT (/out), WORK (/tmp), RUN_DIR (/tmp/xdg), OVERRIDE (/override.cfg), RA_BIN (/ra/retroarch),
#      CORE (/core/genesis_plus_gx_libretro.so), ROM_DIR (/rom).
# Writes OUT/startup.txt ("alive <version>" or "<CODE>: reason"), OUT/*.png, OUT/diff.txt ("shown paused running"),
# OUT/cage.log, OUT/ra.log (separate: RetroArch truncates a shared stderr file), OUT/ra.exit (RetroArch exit code).
# Startup smoke (#175): exit 3 with a named error before any screenshot when the session cannot start:
#   CAGE_START_FAILED  cage exited or made no Wayland socket within READY_WAIT
#   RA_EXITED          RetroArch exited (139 = segfault); RA_NO_ANSWER  running but silent on UDP 55355
#   SHOT_FAILED        grim could not take a screenshot
# Liveness uses VERSION, never GET_STATUS: the cabinet's 1.22.2 build segfaults on GET_STATUS here (#175).
set -u
OUT=${OUT:-/out} WORK=${WORK:-/tmp} RUN_DIR=${RUN_DIR:-/tmp/xdg} OVERRIDE=${OVERRIDE:-/override.cfg}
RA_BIN=${RA_BIN:-/ra/retroarch} CORE=${CORE:-/core/genesis_plus_gx_libretro.so} ROM_DIR=${ROM_DIR:-/rom}
READY_WAIT=${READY_WAIT:-60} SETTLE=${SETTLE:-1.5} HOLD=${HOLD:-2}
export XDG_RUNTIME_DIR="$RUN_DIR" WLR_BACKENDS=headless WLR_RENDERER=pixman LIBGL_ALWAYS_SOFTWARE=1 WLR_LIBINPUT_NO_DEVICES=1
mkdir -p "$XDG_RUNTIME_DIR" "$OUT" "$WORK"; chmod 700 "$XDG_RUNTIME_DIR"
rm -f "$OUT/startup.txt" "$OUT/diff.txt" "$OUT/ra.exit" "$OUT/ra.pid" "$OUT"/[0-9]-*.png
# the override is copied with the overlay forced on, never edited in place
sed 's/^input_overlay_enable.*/input_overlay_enable = "true"/' "$OVERRIDE" > "$WORK/override.cfg"
cat > "$WORK/ra.cfg" <<CFG
video_driver = "gl"
video_context_driver = "wayland"
audio_driver = "null"
video_fullscreen = "true"
config_save_on_exit = "false"
menu_driver = "rgui"
network_cmd_enable = "true"
network_cmd_port = "55355"
libretro_directory = "$(dirname "$CORE")"
aspect_ratio_index = "22"
custom_viewport_width = "1440"
custom_viewport_height = "1080"
custom_viewport_x = "240"
custom_viewport_y = "0"
system_directory = "$WORK"
CFG
# cage's child: run RetroArch, keep its pid and exit code for the smoke check
cat > "$WORK/ra-child.sh" <<'CHILD'
#!/bin/bash
"$RA_BIN" -c "$WORK/ra.cfg" --appendconfig "$WORK/override.cfg" -L "$CORE" "$ROM_DIR/$ROM" >"$OUT/ra.log" 2>&1 &
echo $! > "$OUT/ra.pid"
wait $!
echo $? > "$OUT/ra.exit"
CHILD
chmod +x "$WORK/ra-child.sh"
export RA_BIN CORE ROM_DIR ROM OUT WORK
cage -- "$WORK/ra-child.sh" >"$OUT/cage.log" 2>&1 &
CPID=$!

stop() { kill -9 "$(cat "$OUT/ra.pid" 2>/dev/null)" "$CPID" 2>/dev/null; wait "$CPID" 2>/dev/null; }
fail() { echo "$1: $2" > "$OUT/startup.txt"; echo "ERROR: $1: $2" >&2; stop; exit 3; }
ra_exited() {
  local c; c=$(cat "$OUT/ra.exit" 2>/dev/null) || return 1
  [ "$c" = 139 ] && c="$c (segfault)"
  fail RA_EXITED "RetroArch exited with $c $1; see $OUT/ra.log"
}
ask() { echo -n "$1" | nc -u -w1 127.0.0.1 55355 2>/dev/null; }
cmd() { ask "$1" >/dev/null; }

# 1. cage must make a Wayland socket
deadline=$((SECONDS + READY_WAIT)); SOCK=
while [ -z "$SOCK" ]; do
  SOCK=$(find "$XDG_RUNTIME_DIR" -maxdepth 1 -name 'wayland-[0-9]' 2>/dev/null | sort | head -1)
  [ -n "$SOCK" ] && break
  if ! kill -0 "$CPID" 2>/dev/null; then
    # cage quits with its only client: a RetroArch crash takes the socket with it
    wait "$CPID"; rc=$?; ra_exited "and cage quit with it (cage exit $rc)"
    fail CAGE_START_FAILED "cage exited with $rc before making a Wayland socket; see $OUT/cage.log"
  fi
  [ "$SECONDS" -lt "$deadline" ] || fail CAGE_START_FAILED "no Wayland socket after ${READY_WAIT}s; see $OUT/cage.log"
  sleep 0.5
done
WAYLAND_DISPLAY=$(basename "$SOCK"); export WAYLAND_DISPLAY
OUTPUT=$(wlr-randr 2>>"$OUT/cage.log" | awk 'NR==1{print $1}')
wlr-randr --output "$OUTPUT" --custom-mode 1920x1080 >>"$OUT/cage.log" 2>&1 || echo "RUN: wlr-randr failed" >&2

# 2. RetroArch must answer on the command port
VER=
while [ -z "$VER" ]; do
  VER=$(ask VERSION | head -1)
  [ -n "$VER" ] && break
  ra_exited "during startup"
  [ "$SECONDS" -lt "$deadline" ] || fail RA_NO_ANSWER "RetroArch did not answer VERSION on UDP 55355 within ${READY_WAIT}s; see $OUT/ra.log"
  sleep 0.5
done
echo "alive $VER" > "$OUT/startup.txt"
echo "RUN: cage on $WAYLAND_DISPLAY, RetroArch $VER answers" >&2

sleep "${BOOT:-20}"
shot() { grim "$OUT/$1.png" 2>>"$OUT/cage.log" || { ra_exited "before screenshot $1"; fail SHOT_FAILED "grim could not take $1; see $OUT/cage.log"; }; }
shot 0-running
cmd OVERLAY_NEXT; cmd PAUSE_TOGGLE; sleep "$SETTLE"; shot 1-open-a; sleep "$HOLD"; shot 2-open-b
cmd PAUSE_TOGGLE; cmd OVERLAY_NEXT; sleep "$SETTLE"; shot 3-closed-a; sleep "$HOLD"; shot 4-closed-b
[ -n "$(ask VERSION)" ] || { ra_exited "during the overlay steps"; fail RA_NO_ANSWER "RetroArch stopped answering during the overlay steps"; }
stop
d() { compare -metric AE "$OUT/$1.png" "$OUT/$2.png" null: 2>&1 | cut -d' ' -f1; }
echo "$(d 0-running 1-open-a) $(d 1-open-a 2-open-b) $(d 3-closed-a 4-closed-b)" > "$OUT/diff.txt"
echo "RUN: done"
