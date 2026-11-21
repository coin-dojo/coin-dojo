#!/usr/bin/env python3
"""Generate a fully-transparent Xcursor theme 'blank' so cage/wlroots and SDL
draw no visible pointer on the kiosk (the cabinet has no mouse at all).
Writes the theme under ~/.icons/blank/. wlroots + SDL(Wayland) honour
XCURSOR_THEME / XCURSOR_SIZE from the environment."""
import os, struct

HOME = os.path.expanduser('~')
CURS = os.path.join(HOME, '.icons', 'blank', 'cursors')
os.makedirs(CURS, exist_ok=True)

def xcursor_blank(path):
    # One 1x1 fully-transparent ARGB image, as a minimal Xcursor file.
    w = h = 1
    xhot = yhot = 0
    delay = 0
    nominal = 24                      # nominal size this image serves
    pixels = struct.pack('<I', 0x00000000)  # transparent BGRA/ARGB = 0
    # Build chunk explicitly to the documented layout:
    chunk = struct.pack('<IIII', 36, 0xfffd0002, nominal, 1)   # size, type, subtype(size), version
    chunk += struct.pack('<IIIII', w, h, xhot, yhot, delay)    # width,height,xhot,yhot,delay
    chunk += pixels
    # file header
    magic = b'Xcur'
    header_size = 16
    version = 0x00010000
    ntoc = 1
    fhdr = magic + struct.pack('<III', header_size, version, ntoc)
    toc_size = 12
    body_offset = len(fhdr) + toc_size
    toc = struct.pack('<III', 0xfffd0002, nominal, body_offset)
    with open(path, 'wb') as f:
        f.write(fhdr + toc + chunk)

main_cursor = os.path.join(CURS, 'left_ptr')
xcursor_blank(main_cursor)

# symlink the common cursor names wlroots / SDL / apps may request
for name in ('default', 'pointer', 'arrow', 'hand1', 'hand2', 'xterm',
             'text', 'top_left_arrow', 'left_ptr_watch', 'watch', 'wait',
             'crosshair', 'fleur', 'grab', 'grabbing'):
    link = os.path.join(CURS, name)
    try:
        if os.path.lexists(link):
            os.remove(link)
        os.symlink('left_ptr', link)
    except OSError:
        pass

with open(os.path.join(HOME, '.icons', 'blank', 'index.theme'), 'w') as f:
    f.write('[Icon Theme]\nName=blank\nComment=Transparent cursor for kiosk\n')

print('wrote', main_cursor, os.path.getsize(main_cursor), 'bytes')
print('cursors:', sorted(os.listdir(CURS)))
