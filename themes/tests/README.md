# themes/tests

## Run

    python3 themes/tests/test_theme_xml.py     # no deps (stdlib only)
    pytest themes/tests/                        # if pytest is installed

Well-formedness guard for the ES-DE theme packs under `themes/`.

**Why:** ES-DE does not report a broken theme. If any theme XML fails to parse it
silently falls back to its built-in default theme — the cabinet looks like it "lost
the theme" with no error. This test turns that into a red build.

Checks:
1. Every `*.xml` under `themes/` is well-formed (parses with ElementTree).
2. Every literal asset path (`<path>` / `<staticImage>` / `<defaultImage>` /
   `<fontPath>` that is not a `${variable}`) points to a file that exists.
3. A built-in positive control proves the parser rejects malformed XML, using the
   exact failure shape that caused the fallback (a `<text>` block whose close tag
   was swallowed, leaving mismatched tags).

## The deliberately-broken case (must FAIL)

Prove the guard works — do one of these and the suite MUST go red:

1. **Break the XML** — delete a closing tag in `coin-dojo/theme.xml`
   (e.g. remove a `</view>`). `test_theme_xml_is_wellformed` goes red. Restore to
   go green.
2. **Reference a missing asset** — add `<path>./_inc/art/does-not-exist.png</path>`
   to any element → `test_literal_asset_paths_exist` flags it.

Any of these turning the suite red is the intended behaviour: the guard is doing its job.
