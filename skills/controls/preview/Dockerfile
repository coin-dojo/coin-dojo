# RetroArch overlay check (#57): Debian 13 arm64 like the cabinet, RetroArch 1.20 from Debian; the core comes from the cabinet (bind-mounted).
# Nothing is bundled: the ROM and the overlays are bind-mounted at run time.
FROM debian:trixie-20261104-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2
RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      retroarch xvfb xdotool imagemagick x11-utils procps \
      libgl1-mesa-dri libglx-mesa0 \
 && rm -rf /var/lib/apt/lists/*
