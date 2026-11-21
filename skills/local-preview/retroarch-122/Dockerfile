# RetroArch 1.22 overlay check (#57): the cabinet's 1.22.2 build is Wayland/GLES only (no X11), so this runs it in a
# headless wlroots session (cage) and screenshots with grim. Debian's retroarch package is only here for its libraries.
FROM debian:trixie-20261104-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2
RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      retroarch cage grim wlr-randr netcat-openbsd imagemagick procps \
      libgl1-mesa-dri libegl-mesa0 libgles2 libegl1 \
 && rm -rf /var/lib/apt/lists/*
