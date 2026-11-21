# Skills

A **skill** is a deterministic workflow for one task on the arcade cabinet: numbered steps, exact commands and
explicit **verify gates**, so a local LLM or a human can run it without guessing. How to write one:
`skills/AGENTS.md`.

## Skills

| Skill | Does |
|---|---|
| `badges` | Make a carousel badge for a collection; theme art generators. |
| `build-image` | Build a flashable OS image on the Mac in an arm64 container. |
| `collections` | Build the ES-DE custom collections (MUST PLAY, genres, decades), nested menus, metadata and `launch.py`. |
| `controls` | Controls helper (#57): card per game in the ES-DE info panel (live); in-game overlay on SELECT+HP (built, OFF). |
| `local-preview` | Run the cabinet's menu and ES-DE on a Mac (Colima + VNC) to test here first. |
| `settings` | The SETTINGS carousel entry: Wi-Fi and cabinet name with the stick and buttons. |
| `versions` | Change a pinned version: `make bump` updates every pin source, `versions.lock` and the CHANGELOG line. |

Each skill's workflow is `skills/<name>/SKILL.md`. New ideas: `BACKLOG.md`.
