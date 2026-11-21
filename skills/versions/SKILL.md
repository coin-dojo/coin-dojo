# versions: change a pinned version (#278)

**When we patch, use `make bump`.** Every third-party thing the cabinet, the image or CI uses is pinned (a tag, a
commit, a sha256 or an image digest) in its own source file: `build/*/build.sh`, `build/fightcade/pins.json` (#267;
`fetch.sh` and the role read it), role defaults, Dockerfiles, `ansible/requirements.*`. A
component built from source is also named in the licence manifest `skills/build-image/licences/components.json`
(its `ref` and the commit in its raw licence URLs, #254): one more place of the same pin, bumped with it
(`licences.py check` also refuses a raw licence URL read at another commit or tag than the `ref`). The
generated `versions.lock` at the repo root lists them all: component, version, commit, sha256, source, where it is
pinned. `tools/versions.py` knows where each pin sits; `make bump` changes every place at once, regenerates the lock
and writes the CHANGELOG line, so nothing is left half-bumped.

Never edit `versions.lock` by hand, and never change a pin in one file only.

## Steps

1. See the component and every place it is pinned:

   ```
   make versions-show
   ```

   PASS: the component is listed with `file:line` per field (version, commit, sha256, source). Tagged sha256 fields
   (`sha256:x86_64`, `sha256:archive`, ...) need one value each in step 3.

2. Get the new values from upstream: the release tag, its full 40-hex commit (`git ls-remote <repo> refs/tags/<tag>`),
   and the sha256 of every pinned file (from the release's published checksum, or `shasum -a 256 <file>` of the
   download). Never reuse an old sha256 for a new version.

3. Bump (dry runs do not exist here: it edits the files, review with `git diff` before committing):

   ```
   make bump C=<component> V=<version>                         # version only (pip / collection / tag-only images)
   make bump C=<component> V=<version> COMMIT=<40 hex>         # built from source at a commit
   make bump C=<component> V=<version> SHA=<64 hex>            # a download or image digest
   make bump C=<component> V=<version> SHA=x86_64=<hex>,aarch64=<hex>   # several pinned files
   make bump C=raspios-lite-arm64 V=<folder date> SHA=<hex> URL=<the .img.xz URL>   # Pi OS: always with URL=
   ```

   Pi OS's release folder date (`raspios_lite_arm64-<date>/`) and its file's date (`<date>-raspios-...img.xz`) can
   differ, so that bump refuses without `URL=` and checks that the URL's folder date is `V`. `URL=` also works for
   any component with a source URL. Every edit is made and checked in memory first: a refused bump leaves every
   file byte-identical.

   PASS: `RESULT: PASS versions bump ...`, the files it changed are named, and `CHANGELOG line added: - <component>
   <old> → <new>`. FAIL: `RESULT: FAIL versions: ...` names what is missing (COMMIT=, SHA=, a tag) and nothing is
   written. A hash-list pin (`pyyaml-config`, every wheel hash) is edited by hand, then `make versions`.
   `versions: TODO by hand: ...` names what a bump cannot change itself: Fightcade's download `size` in pins.json
   (bump does not download), and after a source pin the licence texts at the new ref (`licences.py`) and the
   manifest's human `version` text.

4. Check:

   ```
   git diff
   make test-file F=tests/test_versions.py
   python3 tools/changelog.py versions
   ```

   PASS: the diff touches only the pin sources, `versions.lock` and the CHANGELOG `### Versions` line; both
   commands end with `RESULT: PASS`. Then rebuild and test what the pin feeds (the component's own README or
   SKILL: e.g. `make -C build retroarch` and the role's test) before committing.

## A pin changed by hand (or a new pin source)

- Changed a pin source directly: `make versions`, then add the CHANGELOG line it needs
  (`python3 tools/changelog.py versions` prints the missing line).
- A libretro core built from a pinned commit (`build/<core>/build.sh` with `CORE=` and `CORE_COMMIT=`, #274) needs
  no entry: `core_components()` finds it by glob, with its manifest entry (`"build": "build/<core>"`) as the second
  place; `make versions` and its `- <core> <commit> (new)` line are all it takes.
- Added a new pinned thing: add a `C(...)` entry to `COMPONENTS` in `tools/versions.py` (one regex per field, one
  capture group each), `make versions`, and a `### Versions` line `- <component> <version> (new)`.
  `tests/test_versions.py` fails while any sha256, digest, 40-hex commit or `==` pin in a tracked file is not
  listed there (own files go in `OWN`, data that only looks like a pin in `NOT_PINS`, each with its reason).
- What the scan cannot see: it reads code and config only (scripts, YAML, JSON, Dockerfiles, Makefiles,
  requirements; not `docs/`, `notes/` or any `.md`, SKILL.md included), and only hashes, digests, 40-hex commits
  and `==` pins. A **tag-only** pin (`debian:trixie`, `node:20.18.1-bookworm`, a versioned download URL without a
  sha256 such as ES-DE's in chroot-setup.sh) or a version written in prose is invisible to it: add those
  components by hand.

## What enforces it

- `tests/test_versions.py` (runs with every code change): the committed lock equals a fresh `make versions`; every
  pin is listed; all places of one pin agree; `make bump` leaves sources, lock and CHANGELOG consistent.
- `tools/changelog.py versions`: each component whose version, commit or sha256 differs from the lock at
  `git merge-base HEAD origin/main` has a bullet under `[Unreleased]` → `### Versions` with its name and new version
  (`old → new`). When that merge base is HEAD itself (on main, or a branch without commits) it skips with a SKIP
  line; a change already named in a released section's `### Versions` is never asked again.
