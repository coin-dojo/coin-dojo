# Ideas backlog — apps for the arcade cabinet

Ideas for apps and capabilities to add to the arcade image. **These are placeholders /
structure — the maintainer will add his own specific ideas.** Each becomes a Forgejo issue in
`<forge repo>` when it's ready to be worked, and ideally grows a deterministic skill under
`skills/` when implemented.

Legend: 🎮 front-end · 📺 media · 🌐 kiosk/web · 🔧 ops · 🏗️ pipeline

## Starter epics / ideas

- 🏗️ **Automated image build pipeline (edit → verify → write)** — script the
  image workflow end to end: copy pristine → chroot edits → verify gates →
  produce a named, dated custom image. Make the verify gates a test. *(foundational — do this
  early so every later app rides the same pipeline.)*

- 🔧 **Maintenance / SSH ops skill** — a deterministic skill for day-to-day ops over SSH:
  health check, update packages, read logs, reboot safely, back up the running card. Builds
  on SSH already being enabled in the custom image.

- 🎮 **RetroArch / EmulationStation front-end options** — evaluate adding a retro front-end
  alongside the stock SF2 experience (RetroArch, EmulationStation, or Batocera-style launcher).
  Decide: layer onto the vendor image, or a separate boot option.

- 📺 **Kodi / media player** — turn the cabinet into a media player when not gaming (Kodi or
  similar), with a way to switch between arcade and media modes.

- 📺 **Moonlight / Steam streaming client** — stream games from a desktop/GPU host to the
  cabinet (Moonlight for NVIDIA GameStream / Sunshine, or Steam Link).

- 🌐 **DNS-filtered ad-free browser kiosk** — a locked-down kiosk browser (e.g. Chromium
  kiosk) that uses your network's DNS filter for DNS, for a clean info/dashboard
  screen on the cabinet.

- 🔧 **Monitoring integration** — surface the cabinet in your own status dashboard, so its
  status shows next to the rest of your home network.

## Follow-ups (2026-11-14)

- 🎮 **Re-enable the in-game controls overlay** (SELECT+HP) after the maintainer's play test of RetroArch 1.22.2 (#57).
- 🎮 **Dreamcast flycast mapping file** so the card matches the real mapping. Needs the maintainer's OK first.
- 🎮 **N64 per-game cards** (in progress, Input).
- 📺 **SERIES #55** (in progress, Emulation).
- 🏗️ **RetroArch 1.22 Wayland preview container** (`skills/local-preview/retroarch-122/`, cage + grim; one image since #113; status: `skills/local-preview/SKILL.md`).

## How to turn an idea into work

1. Open an issue on the repo,
   with the "done when" and whether it edits the image or runs on the live cabinet.
2. If it edits the image, it probably extends `skills/build-image` or gets its own
   skill. Add tests for any script.
3. Keep it deterministic and keep secrets out of git.
