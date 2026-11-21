#!/usr/bin/env python3
"""Check a screenshot (binary PPM, `grim -t ppm`) for the VM smoke test (#82). Stdlib only.

    shotcheck.py FILE --ui          PASS when the frame is not blank: >= 8 distinct colours (ES-DE drew UI)
    shotcheck.py FILE --red 0.30    PASS when >= 30% of pixels are NES red (the test ROM is running)

Prints one `RESULT: PASS|FAIL shot ...` line; exit 0 on PASS, 1 on FAIL, 2 on a bad file.
"""
import sys


def read_ppm(path):
    with open(path, "rb") as fh:
        data = fh.read()
    fields, pos = [], 0
    while len(fields) < 4:                       # magic, width, height, maxval (comments allowed)
        while pos < len(data) and data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            pos = data.index(b"\n", pos) + 1
            continue
        end = pos
        while end < len(data) and not data[end:end + 1].isspace():
            end += 1
        fields.append(data[pos:end])
        pos = end
    if fields[0] != b"P6" or int(fields[3]) != 255:
        raise ValueError("not a P6/255 PPM")
    w, h = int(fields[1]), int(fields[2])
    pix = data[pos + 1:pos + 1 + w * h * 3]
    if len(pix) != w * h * 3:
        raise ValueError("truncated PPM")
    return w, h, pix


def is_red(r, g, b):
    # NES $16 is a strong red in every core palette (~ (180..230, 20..60, 0..60)).
    return r >= 120 and g <= 90 and b <= 90 and r - max(g, b) >= 80


def main(argv):
    if len(argv) < 3 or argv[2] not in ("--ui", "--red"):
        print(__doc__, file=sys.stderr)
        return 2
    path, mode = argv[1], argv[2]
    try:
        w, h, pix = read_ppm(path)
    except (OSError, ValueError, IndexError) as exc:
        print("RESULT: FAIL shot %s: unreadable (%s)" % (path, exc))
        return 2
    step = 3 * 7                                   # every 7th pixel is plenty
    sample = [tuple(pix[i:i + 3]) for i in range(0, len(pix) - 2, step)]
    if mode == "--ui":
        n = len(set(sample))
        ok = n >= 8
        print("RESULT: %s shot %s %dx%d: %d distinct colours (need >= 8)" % ("PASS" if ok else "FAIL", path, w, h, n))
    else:
        want = float(argv[3]) if len(argv) > 3 else 0.30
        frac = sum(1 for p in sample if is_red(*p)) / float(len(sample) or 1)
        ok = frac >= want
        print("RESULT: %s shot %s %dx%d: %.0f%% red (need >= %.0f%%)" % ("PASS" if ok else "FAIL", path, w, h, frac * 100, want * 100))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
