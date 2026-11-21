#!/usr/bin/env python3
"""imgsize.py - will the built image fit the smallest supported SD card? (#237)

    imgsize.py BASE_IMG ROOT_GIB DATA_MIB [MIN_CARD_GB=16]

Reads the base image's MBR (stdlib only, runs on Linux and macOS before any docker step) and computes the
output size with the same geometry as prep-image.sh: boot partition kept, root grown to ROOT_GIB from its
current start, data partition of DATA_MIB aligned up to 1 MiB after it.

A card sold as N GB holds N * 10^9 bytes on paper and a little less in practice, so the budget is
N * 10^9 * CARD_FACTOR (0.93: the smallest "16 GB" cards hold about 14.9e9 bytes).

Exit 0 = fits (prints the numbers), 1 = too big for MIN_CARD_GB, 2 = not a usable base image.
"""
import struct
import sys

CARD_FACTOR = 0.93
SECTOR = 512


def die(code, msg):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(code)


def mbr_partitions(path):
    with open(path, "rb") as f:
        head = f.read(512)
    if head[:6] == b"\xfd7zXZ\x00":
        die(2, "%s is still xz-compressed: decompress it (xz -dk) or use make base-image" % path)
    if len(head) < 512 or head[510:512] != b"\x55\xaa":
        die(2, "%s has no MBR partition table: is it a Raspberry Pi OS .img?" % path)
    parts = []
    for i in range(4):
        e = head[446 + 16 * i: 446 + 16 * (i + 1)]
        ptype = e[4]
        start, size = struct.unpack("<II", e[8:16])
        if ptype and size:
            parts.append((start, size))
    if len(parts) < 2:
        die(2, "%s has %d partition(s), a Pi OS image has boot + root" % (path, len(parts)))
    return parts


def total_bytes(p2_start, root_gib, data_mib):
    root_size = root_gib * 2097152
    root_end = p2_start + root_size - 1
    data_start = ((root_end + 1 + 2047) // 2048) * 2048
    data_end = data_start + data_mib * 2048 - 1
    return (data_end + 1) * SECTOR


def budget_bytes(card_gb):
    return int(card_gb * 10**9 * CARD_FACTOR)


def main(argv):
    if len(argv) not in (4, 5):
        die(2, "usage: imgsize.py BASE_IMG ROOT_GIB DATA_MIB [MIN_CARD_GB]")
    base = argv[1]
    try:
        root_gib, data_mib = int(argv[2]), int(argv[3])
        card_gb = int(argv[4]) if len(argv) == 5 else 16
    except ValueError:
        die(2, "ROOT_GIB, DATA_MIB and MIN_CARD_GB must be whole numbers")
    if root_gib < 4 or data_mib < 64 or card_gb < 4:
        die(2, "ROOT_GIB must be >= 4 (Pi OS + the arcade stack), DATA_MIB >= 64, MIN_CARD_GB >= 4")
    try:
        parts = mbr_partitions(base)
    except OSError as e:
        die(2, "cannot read %s: %s" % (base, e.strerror))
    total = total_bytes(parts[1][0], root_gib, data_mib)
    budget = budget_bytes(card_gb)
    mib = total // 1048576
    print("image: %d MiB (%.2f GiB) = boot + root %d GiB + data %d MiB; smallest card %d GB holds >= %d MiB"
          % (mib, total / 2**30, root_gib, data_mib, card_gb, budget // 1048576))
    if total > budget:
        die(1, "the image (%d MiB) would not fit a %d GB card (budget %d MiB): lower ROOT_GIB/DATA_MIB or raise MIN_CARD_GB"
            % (mib, card_gb, budget // 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
