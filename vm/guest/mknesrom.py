#!/usr/bin/env python3
"""Write a tiny test NES ROM (#82): our own 6502 code, MIT like the rest of the repo, no game.

It is generated at run time and never committed (ship-no-artifacts: no ROM files in git). The
program sets the backdrop colour to NES palette $16 (red), turns the background on and loops, so
a running core fills the screen red. smoke.sh checks that colour in the screenshot: red there
proves the core really ran the content, not just that RetroArch opened.

    python3 -I mknesrom.py OUT.nes        # writes 24,592 bytes (iNES, mapper 0, 16 KB PRG, 8 KB CHR)
"""
import sys

ORG = 0xC000
# (address, bytes) of the program; offsets checked by tests/test_vm.py.
CODE = bytes([
    0x78,                    # C000 SEI
    0xD8,                    # C001 CLD
    0xA2, 0x40,              # C002 LDX #$40
    0x8E, 0x17, 0x40,        # C004 STX $4017   (APU frame IRQ off)
    0xA2, 0xFF,              # C007 LDX #$FF
    0x9A,                    # C009 TXS
    0xE8,                    # C00A INX         (X = 0)
    0x8E, 0x00, 0x20,        # C00B STX $2000   (NMI off)
    0x8E, 0x01, 0x20,        # C00E STX $2001   (rendering off)
    0x2C, 0x02, 0x20,        # C011 BIT $2002   (wait vblank 1)
    0x10, 0xFB,              # C014 BPL $C011
    0x2C, 0x02, 0x20,        # C016 BIT $2002   (wait vblank 2: PPU warmed up)
    0x10, 0xFB,              # C019 BPL $C016
    0xA9, 0x3F,              # C01B LDA #$3F
    0x8D, 0x06, 0x20,        # C01D STA $2006
    0xA9, 0x00,              # C020 LDA #$00
    0x8D, 0x06, 0x20,        # C022 STA $2006   (PPU address $3F00 = backdrop colour)
    0xA9, 0x16,              # C025 LDA #$16    (red)
    0x8D, 0x07, 0x20,        # C027 STA $2007
    0xA9, 0x00,              # C02A LDA #$00
    0x8D, 0x06, 0x20,        # C02C STA $2006
    0x8D, 0x06, 0x20,        # C02F STA $2006
    0x8D, 0x05, 0x20,        # C032 STA $2005
    0x8D, 0x05, 0x20,        # C035 STA $2005   (scroll 0,0)
    0xA9, 0x0A,              # C038 LDA #$0A    (background on, incl. the left 8 px)
    0x8D, 0x01, 0x20,        # C03A STA $2001
    0x4C, 0x3D, 0xC0,        # C03D JMP $C03D   (loop forever)
    0x40,                    # C040 RTI         (NMI / IRQ)
])
NMI = IRQ = 0xC040
RESET = 0xC000


def build():
    prg = bytearray(16 * 1024)
    prg[0:len(CODE)] = CODE
    for off, addr in ((0x3FFA, NMI), (0x3FFC, RESET), (0x3FFE, IRQ)):
        prg[off] = addr & 0xFF
        prg[off + 1] = addr >> 8
    header = b"NES\x1a" + bytes([1, 1, 0, 0]) + bytes(8)   # 1x16 KB PRG, 1x8 KB CHR, mapper 0
    chr_rom = bytes(8 * 1024)                              # tile 0 blank: the backdrop shows
    return header + bytes(prg) + chr_rom


def main(argv):
    if len(argv) != 2:
        print("usage: mknesrom.py OUT.nes", file=sys.stderr)
        return 2
    with open(argv[1], "wb") as fh:
        fh.write(build())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
