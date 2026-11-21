#!/bin/bash
# Inside the container: start RetroArch with the per-game override, press the hotkey twice, take screenshots.
# Screen and viewport copy the cabinet (1920x1080 panel, 4:3 game at x 240). The cabinet uses START+HP (pad button 5, Start = hotkey enabler) for both hotkeys; here both sit on the "o" key (xdotool sends keys only).
set -u
export DISPLAY=:99
Xvfb :99 -screen 0 1920x1080x24 >/dev/null 2>&1 &
sleep 2
cat > /tmp/ra.cfg <<CFG
video_driver = "gl"
input_driver = "x"
audio_driver = "null"
video_fullscreen = "true"
config_save_on_exit = "false"
menu_driver = "rgui"
input_enable_hotkey = "nul"
input_overlay_next = "o"
input_pause_toggle = "o"
libretro_directory = "/core"
aspect_ratio_index = "22"
custom_viewport_width = "1440"
custom_viewport_height = "1080"
custom_viewport_x = "240"
custom_viewport_y = "0"
system_directory = "/tmp"
CFG
retroarch -c /tmp/ra.cfg --appendconfig "/override.cfg" -L /core/genesis_plus_gx_libretro.so "/rom/$ROM" >/out/ra.log 2>&1 &
sleep "${BOOT:-20}"
press() { W=$(xdotool search --class retroarch | head -1); xdotool windowfocus "$W" 2>/dev/null; xdotool keydown o; sleep 0.15; xdotool keyup o; }
shot() { import -window root "/out/$1.png"; }
shot 0-running
press; sleep 1.5; shot 1-open-a; sleep 2; shot 2-open-b
press; sleep 1.5; shot 3-closed-a; sleep 2; shot 4-closed-b
pkill -9 retroarch
d() { compare -metric AE "/out/$1.png" "/out/$2.png" null: 2>&1 | cut -d' ' -f1; }
echo "$(d 0-running 1-open-a) $(d 1-open-a 2-open-b) $(d 3-closed-a 4-closed-b)" > /out/diff.txt
echo "RUN: done"
