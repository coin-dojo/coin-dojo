#!/bin/sh
# harness-only ES-DE <command> target: runs the real dispatcher in dry-run mode and logs it
COIN_DOJO_ROMS=/preview/roms /usr/bin/python3 /launch.py --dry-run "$@" >> /preview/out/launch.log 2>&1
