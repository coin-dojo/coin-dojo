# Headless ES-DE 3.5.0 preview image (arm64, Debian 13 trixie like the cabinet).
# Only runtime deps: ES-DE itself comes from YOUR extracted AppImage (bind-mounted), nothing is bundled here.
FROM debian:trixie-20261104-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2
RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      xvfb xdotool imagemagick x11-utils procps ca-certificates \
      libgl1 libgl1-mesa-dri libglx-mesa0 libegl1 libgles2 libdrm2 libgbm1 \
      libx11-6 libxext6 libxcursor1 libxrandr2 libxi6 libxfixes3 libxkbcommon0 libxss1 \
      libasound2t64 libpulse0 fonts-dejavu-core \
      libharfbuzz0b libfribidi0 libcom-err2 libgpg-error0 \
 && rm -rf /var/lib/apt/lists/*
