#!/usr/bin/env python3
"""Resident 'Shut down?' overlay for the coin-dojo cabinet (#66).

A tiny native Wayland client: Python stdlib (the wire protocol over the cage socket, fds via
SCM_RIGHTS, a memfd shm pool) plus ctypes into libcairo + libfreetype, which are already on the
cabinet. No build step, no new packages, no browser.

Why: the old path cold-started cog (WPE WebKit) on every press, ~1 s before anything showed. This
overlay is PRIMED: powerd connects it at start, learns the output size, and pre-renders the prompt
frame into shared memory. A press then costs one compositor round trip: create the toplevel, ack
its configure, attach the ready buffer. cage 0.3.1 has no layer-shell, so the overlay is a normal
fullscreen xdg_toplevel that is created on show and destroyed on hide (cage then refocuses ES-DE).

API (all methods are safe to call from any thread; none raises):
    ov = Overlay(); ov.prime()            -> bool: connected and the prompt frame is ready
    ov.show_prompt(seconds)               -> bool: prompt mapped (countdown ticks every second)
    ov.show_shutdown(wait=0.3)            -> bool: the 'safe to switch off' frame was presented
    ov.hide()
"""
import ctypes
import ctypes.util
import mmap
import os
import socket
import struct
import threading
import time

FONT_DIR = os.environ.get('POWERD_FONT_DIR', '/data/launcher/fonts')
FONTS = {'px': 'PressStart2P-Regular.ttf', 'disp': 'ArchivoBlack-Regular.ttf', 'ui': 'Rajdhani-SemiBold.ttf'}
TITLE = 'coin-dojo-power'
SHM_ARGB8888 = 0
CONFIGURE_TIMEOUT = 0.5      # s to wait for cage's configure when mapping
PRESS_LOCK_WAIT = 0.05       # s a press waits for the overlay lock (the primer may hold it while connecting)

# ---- text of the two frames (tests read these) ------------------------------------------------
PROMPT_TITLE = 'SHUT DOWN?'
PROMPT_SUB = ('PRESS', 'POWER', 'AGAIN TO CONFIRM')
PROMPT_HINT = 'OR WAIT - THE CABINET STAYS ON'
OFF_TITLE = 'SHUTTING DOWN'
OFF_LINES = ('SAFE TO SWITCH OFF THE CABINET', 'WHEN THE SCREEN SAYS NO SIGNAL')

# ---- palette (shutdown.html / theme tokens) ---------------------------------------------------
INDIGO = (0x0A, 0x10, 0x38)
CYAN = (0x36, 0xD6, 0xE7)
AMBER = (0xF5, 0xA6, 0x23)
INK = (0xEA, 0xF0, 0xFF)
MUTED = (0x9F, 0xB0, 0xD8)
RED = (0xE2, 0x3B, 0x3B)


def _rgb(c, a=1.0):
    return (c[0] / 255.0, c[1] / 255.0, c[2] / 255.0, a)


# ================================================================================================
# Wayland wire protocol (only what we need)
# ================================================================================================
def _pad(n):
    return (n + 3) & ~3


def enc_uint(v):
    return struct.pack('=I', v & 0xFFFFFFFF)


def enc_int(v):
    return struct.pack('=i', v)


def enc_str(s):
    b = s.encode('utf-8') + b'\0'
    return struct.pack('=I', len(b)) + b + b'\0' * (_pad(len(b)) - len(b))


def message(obj, opcode, payload=b''):
    size = 8 + len(payload)
    return struct.pack('=II', obj, (size << 16) | opcode) + payload


def split_messages(buf):
    """bytes -> ([(obj, opcode, payload)], rest). Incomplete trailing bytes are returned as rest."""
    out, i = [], 0
    while len(buf) - i >= 8:
        obj, so = struct.unpack_from('=II', buf, i)
        size, op = so >> 16, so & 0xFFFF
        if size < 8:
            raise ValueError('bad wayland message size %d' % size)
        if len(buf) - i < size:
            break
        out.append((obj, op, bytes(buf[i + 8:i + size])))
        i += size
    return out, bytes(buf[i:])


def dec_str(p, off=0):
    n = struct.unpack_from('=I', p, off)[0]
    s = p[off + 4:off + 4 + n - 1].decode('utf-8', 'replace') if n else ''
    return s, off + 4 + _pad(n)


class WaylandConn:
    """One client connection. Request helpers send immediately; a reader thread dispatches events."""

    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(2.0)
        self.sock.connect(path)
        self.sock.settimeout(None)
        self.lock = threading.Lock()
        self.next_id = 2                         # 1 is wl_display
        self.handlers = {}                       # obj id -> fn(opcode, payload)
        self.alive = True
        self.error = None
        self.handlers[1] = self._on_display
        self.reader = threading.Thread(target=self._read_loop, name='wl-reader', daemon=True)

    def start(self):
        self.reader.start()

    def new_id(self, handler=None):
        with self.lock:
            i = self.next_id
            self.next_id += 1
        if handler:
            self.handlers[i] = handler
        return i

    def send(self, data, fds=()):
        with self.lock:
            if not self.alive:
                raise OSError('wayland connection is closed')
            if fds:
                self.sock.sendmsg([data], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack('=%di' % len(fds), *fds))])
            else:
                self.sock.sendall(data)

    def sync(self, timeout=1.0):
        """wl_display.sync round trip. True when the compositor answered in time."""
        ev = threading.Event()
        cb = self.new_id(lambda op, p: ev.set())
        self.send(message(1, 0, enc_uint(cb)))
        return ev.wait(timeout) and self.alive

    def _on_display(self, op, p):
        if op == 0:                              # error(object, code, message)
            obj, code = struct.unpack_from('=II', p, 0)
            msg, _ = dec_str(p, 8)
            self.error = 'wl_display.error obj=%d code=%d %s' % (obj, code, msg)
            self.close()
        elif op == 1:                            # delete_id
            self.handlers.pop(struct.unpack_from('=I', p, 0)[0], None)

    def _read_loop(self):
        buf = b''
        try:
            while self.alive:
                data = self.sock.recv(65536)
                if not data:
                    break
                msgs, buf = split_messages(buf + data)
                for obj, op, payload in msgs:
                    h = self.handlers.get(obj)
                    if h:
                        try:
                            h(op, payload)
                        except Exception as e:  # a handler bug must not kill the connection silently
                            self.error = 'handler obj=%d op=%d: %r' % (obj, op, e)
        except OSError:
            pass
        self.close()

    def close(self):
        self.alive = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ================================================================================================
# Drawing: ctypes into cairo (+ FreeType for the theme fonts; cairo's toy font if they are missing)
# ================================================================================================
class _TextExtents(ctypes.Structure):
    _fields_ = [(n, ctypes.c_double) for n in ('x_bearing', 'y_bearing', 'width', 'height', 'x_advance', 'y_advance')]


def _lib(name, soname):
    path = ctypes.util.find_library(name) or soname
    return ctypes.CDLL(path)


class Painter:
    """Renders the two frames into an ARGB32 (premultiplied = wl_shm ARGB8888) buffer."""

    def __init__(self, font_dir=FONT_DIR):
        c = self.c = _lib('cairo', 'libcairo.so.2')
        vp, d, i = ctypes.c_void_p, ctypes.c_double, ctypes.c_int
        sig = {
            'cairo_image_surface_create_for_data': (vp, [vp, i, i, i, i]),
            'cairo_create': (vp, [vp]), 'cairo_destroy': (None, [vp]),
            'cairo_surface_destroy': (None, [vp]), 'cairo_surface_flush': (None, [vp]),
            'cairo_set_operator': (None, [vp, i]), 'cairo_paint': (None, [vp]),
            'cairo_set_source_rgba': (None, [vp, d, d, d, d]), 'cairo_set_source': (None, [vp, vp]),
            'cairo_rectangle': (None, [vp, d, d, d, d]), 'cairo_fill': (None, [vp]),
            'cairo_fill_preserve': (None, [vp]), 'cairo_stroke': (None, [vp]),
            'cairo_set_line_width': (None, [vp, d]), 'cairo_new_sub_path': (None, [vp]),
            'cairo_close_path': (None, [vp]), 'cairo_arc': (None, [vp, d, d, d, d, d]),
            'cairo_move_to': (None, [vp, d, d]), 'cairo_show_text': (None, [vp, ctypes.c_char_p]),
            'cairo_set_font_size': (None, [vp, d]), 'cairo_set_font_face': (None, [vp, vp]),
            'cairo_select_font_face': (None, [vp, ctypes.c_char_p, i, i]),
            'cairo_text_extents': (None, [vp, ctypes.c_char_p, ctypes.POINTER(_TextExtents)]),
            'cairo_pattern_create_linear': (vp, [d, d, d, d]),
            'cairo_pattern_create_radial': (vp, [d, d, d, d, d, d]),
            'cairo_pattern_add_color_stop_rgba': (None, [vp, d, d, d, d, d]),
            'cairo_pattern_destroy': (None, [vp]),
        }
        for name, (res, args) in sig.items():
            fn = getattr(c, name)
            fn.restype, fn.argtypes = res, args
        self.faces = {}
        self._ft_lib = None
        try:
            ft = self.ft = _lib('freetype', 'libfreetype.so.6')
            ft.FT_Init_FreeType.argtypes = [ctypes.POINTER(vp)]
            ft.FT_New_Face.argtypes = [vp, ctypes.c_char_p, ctypes.c_long, ctypes.POINTER(vp)]
            c.cairo_ft_font_face_create_for_ft_face.restype = vp
            c.cairo_ft_font_face_create_for_ft_face.argtypes = [vp, i]
            lib = vp()
            if ft.FT_Init_FreeType(ctypes.byref(lib)) == 0:
                self._ft_lib = lib
                for key, fname in FONTS.items():
                    path = os.path.join(font_dir, fname)
                    face = vp()
                    if os.path.isfile(path) and ft.FT_New_Face(lib, path.encode(), 0, ctypes.byref(face)) == 0:
                        self.faces[key] = c.cairo_ft_font_face_create_for_ft_face(face, 0)
        except (OSError, AttributeError):
            pass                                  # no FreeType: toy fonts below

    # -- small helpers --------------------------------------------------------------------------
    def _font(self, cr, key, size):
        f = self.faces.get(key)
        if f:
            self.c.cairo_set_font_face(cr, f)
        else:
            self.c.cairo_select_font_face(cr, b'monospace' if key == 'px' else b'sans-serif', 0, 1)
        self.c.cairo_set_font_size(cr, size)

    def _width(self, cr, text):
        ext = _TextExtents()
        self.c.cairo_text_extents(cr, text.encode(), ctypes.byref(ext))
        return ext.x_advance

    def _text(self, cr, text, cx, y, color, key, size):
        self._font(cr, key, size)
        w = self._width(cr, text)
        self.c.cairo_set_source_rgba(cr, *color)
        self.c.cairo_move_to(cr, cx - w / 2.0, y)
        self.c.cairo_show_text(cr, text.encode())
        return w

    def _rrect(self, cr, x, y, w, h, r):
        c, pi = self.c, 3.141592653589793
        c.cairo_new_sub_path(cr)
        c.cairo_arc(cr, x + w - r, y + r, r, -pi / 2, 0)
        c.cairo_arc(cr, x + w - r, y + h - r, r, 0, pi / 2)
        c.cairo_arc(cr, x + r, y + h - r, r, pi / 2, pi)
        c.cairo_arc(cr, x + r, y + r, r, pi, 3 * pi / 2)
        c.cairo_close_path(cr)

    def _hgrad(self, x0, x1, a, b):
        p = self.c.cairo_pattern_create_linear(x0, 0, x1, 0)
        self.c.cairo_pattern_add_color_stop_rgba(p, 0, *_rgb(a))
        self.c.cairo_pattern_add_color_stop_rgba(p, 1, *_rgb(b))
        return p

    # -- frames ---------------------------------------------------------------------------------
    def render(self, addr, w, h, stride, kind, left=0, total=8):
        """kind 'prompt' (countdown `left` of `total`) or 'off'. Draws into the buffer at addr."""
        c = self.c
        surf = c.cairo_image_surface_create_for_data(addr, 0, w, h, stride)   # CAIRO_FORMAT_ARGB32
        cr = c.cairo_create(surf)
        try:
            c.cairo_set_operator(cr, 1)                                        # SOURCE: replace, incl. alpha
            bg = c.cairo_pattern_create_radial(w / 2, h * .45, 0, w / 2, h * .45, max(w, h) * .6)
            c.cairo_pattern_add_color_stop_rgba(bg, 0, *_rgb(INDIGO, .82 if kind == 'prompt' else .96))
            c.cairo_pattern_add_color_stop_rgba(bg, 1, 4 / 255., 5 / 255., 16 / 255., .93 if kind == 'prompt' else .99)
            c.cairo_set_source(cr, bg)
            c.cairo_paint(cr)
            c.cairo_pattern_destroy(bg)
            c.cairo_set_operator(cr, 2)                                        # OVER for the card

            s = min(w / 1440.0, h / 1080.0) * 1.5                              # card scale; ES-DE is 4:3 centred
            cw, ch = 640 * s, 300 * s
            x, y = (w - cw) / 2, (h - ch) / 2
            self._rrect(cr, x, y, cw, ch, 14 * s)
            card = c.cairo_pattern_create_linear(x, y, x + cw * .6, y + ch)
            c.cairo_pattern_add_color_stop_rgba(card, 0, 0x10 / 255., 0x1a / 255., 0x4a / 255., 1)
            c.cairo_pattern_add_color_stop_rgba(card, .6, *_rgb(INDIGO))
            c.cairo_pattern_add_color_stop_rgba(card, 1, 0x1a / 255., 0x0a / 255., 0x16 / 255., 1)
            c.cairo_set_source(cr, card)
            c.cairo_fill_preserve(cr)
            c.cairo_pattern_destroy(card)
            c.cairo_set_source_rgba(cr, *_rgb(CYAN))
            c.cairo_set_line_width(cr, 3 * s)
            c.cairo_stroke(cr)
            edge = self._hgrad(x, x + cw, AMBER, RED)
            c.cairo_set_source(cr, edge)
            c.cairo_rectangle(cr, x + 10 * s, y + 1.5 * s, cw - 20 * s, 5 * s)
            c.cairo_fill(cr)
            c.cairo_pattern_destroy(edge)

            mid = w / 2.0
            if kind == 'prompt':
                self._text(cr, PROMPT_TITLE, mid, y + 92 * s, _rgb(INK), 'disp', 52 * s)
                # "PRESS [POWER] AGAIN TO CONFIRM" with an amber pill around POWER
                self._font(cr, 'px', 12 * s)
                a, b, z = PROMPT_SUB
                gap = 10 * s
                wa, wb, wz = self._width(cr, a), self._width(cr, b), self._width(cr, z)
                pw = wb + 24 * s
                total_w = wa + gap + pw + gap + wz
                sx, sy = mid - total_w / 2, y + 150 * s
                c.cairo_set_source_rgba(cr, *_rgb(CYAN))
                c.cairo_move_to(cr, sx, sy)
                c.cairo_show_text(cr, a.encode())
                self._rrect(cr, sx + wa + gap, sy - 20 * s, pw, 28 * s, 7 * s)
                c.cairo_set_source_rgba(cr, *_rgb(AMBER))
                c.cairo_fill(cr)
                c.cairo_set_source_rgba(cr, 7 / 255., 16 / 255., 31 / 255., 1)
                c.cairo_move_to(cr, sx + wa + gap + 12 * s, sy)
                c.cairo_show_text(cr, b.encode())
                c.cairo_set_source_rgba(cr, *_rgb(CYAN))
                c.cairo_move_to(cr, sx + wa + gap + pw + gap, sy)
                c.cairo_show_text(cr, z.encode())

                self._text(cr, 'CANCELS IN %d' % max(0, left), mid, y + 200 * s, _rgb(AMBER), 'px', 16 * s)
                bx, by, bw, bh = x + 48 * s, y + 218 * s, cw - 96 * s, 12 * s
                self._rrect(cr, bx, by, bw, bh, 6 * s)
                c.cairo_set_source_rgba(cr, 7 / 255., 12 / 255., 34 / 255., 1)
                c.cairo_fill(cr)
                frac = max(0.0, min(1.0, float(left) / float(total or 1)))
                if frac > 0:
                    fill = self._hgrad(bx, bx + bw, AMBER, RED)
                    c.cairo_set_source(cr, fill)
                    self._rrect(cr, bx, by, max(bh, bw * frac), bh, 6 * s)
                    c.cairo_fill(cr)
                    c.cairo_pattern_destroy(fill)
                self._text(cr, PROMPT_HINT, mid, y + 268 * s, _rgb(MUTED), 'px', 9 * s)
            else:
                self._text(cr, OFF_TITLE, mid, y + 100 * s, _rgb(INK), 'disp', 46 * s)
                self._text(cr, OFF_LINES[0], mid, y + 175 * s, _rgb(CYAN), 'px', 13 * s)
                self._text(cr, OFF_LINES[1], mid, y + 215 * s, _rgb(AMBER), 'px', 13 * s)
                self._text(cr, 'PLEASE WAIT...', mid, y + 268 * s, _rgb(MUTED), 'px', 9 * s)
            c.cairo_surface_flush(surf)
        finally:
            c.cairo_destroy(cr)
            c.cairo_surface_destroy(surf)


# ================================================================================================
# The overlay
# ================================================================================================
class Overlay:
    def __init__(self, display=None, runtime_dir=None, painter_factory=Painter, log=None):
        self.display = display or os.environ.get('WAYLAND_DISPLAY', 'wayland-0')
        self.runtime_dir = runtime_dir or os.environ.get('XDG_RUNTIME_DIR', '/run/user/%d' % os.getuid())
        self.painter_factory = painter_factory
        self.painter = None
        self.log = log or (lambda msg: None)
        self.lock = threading.RLock()
        self.conn = None
        self._last_err = None
        self._reset()

    def _reset(self):
        self.globals = {}
        self.compositor = self.shm = self.wm_base = self.output = None
        self.out_w = self.out_h = 0
        self.size = (0, 0)
        self.mm = None
        self.pool = None
        self.buffers = []          # [(id, addr)] two buffers in one pool
        self.busy = {}
        self.front = 0
        self.fresh = False         # buffers[front] holds the full-countdown prompt
        self.surface = self.xsurf = self.toplevel = None
        self.cfg_size = (0, 0)
        self.cfg_event = threading.Event()
        self.cfg_serial = None
        self.ticker = None
        self.shown = None          # None | 'prompt' | 'off'
        self.gen = 0

    @property
    def path(self):
        return self.display if self.display.startswith('/') else os.path.join(self.runtime_dir, self.display)

    def ready(self):
        c = self.conn
        return bool(c and c.alive and self.buffers)

    # -- connect + prepare ----------------------------------------------------------------------
    def prime(self):
        """Connect, bind globals, pre-render the prompt. Idempotent; False when cage is not there."""
        with self.lock:
            if self.ready():
                return True
            self._drop()
            if not os.path.exists(self.path):
                return False
            try:
                t0 = time.monotonic()
                if self.painter is None:
                    self.painter = self.painter_factory()
                conn = self.conn = WaylandConn(self.path)
                reg = conn.new_id(self._on_registry)
                conn.start()
                conn.send(message(1, 1, enc_uint(reg)))                 # wl_display.get_registry
                if not conn.sync():
                    raise OSError('no registry reply')
                need = ('wl_compositor', 'wl_shm', 'xdg_wm_base')
                if any(n not in self.globals for n in need):
                    raise OSError('compositor lacks %s' % [n for n in need if n not in self.globals])
                self.compositor = self._bind(reg, 'wl_compositor', 4)
                self.shm = self._bind(reg, 'wl_shm', 1)
                self.wm_base = self._bind(reg, 'xdg_wm_base', 1, self._on_wm_base)
                if 'wl_output' in self.globals:
                    self.output = self._bind(reg, 'wl_output', 2, self._on_output)
                if not conn.sync():
                    raise OSError('no reply after bind')
                w, h = self.out_w or 1920, self.out_h or 1080
                self._alloc(w, h)
                self._paint(self.front, 'prompt', 8, 8)
                self.fresh = True
                self._last_err = None
                self.log('overlay primed %dx%d in %.0f ms' % (w, h, (time.monotonic() - t0) * 1000))
                return True
            except Exception as e:
                err = 'overlay prime failed: %r' % (e,)
                if err != self._last_err:                              # once per distinct error, not every 2 s
                    self.log(err)
                    self._last_err = err
                self._drop()
                return False

    def _bind(self, reg, iface, version, handler=None):
        name, ver = self.globals[iface]
        oid = self.conn.new_id(handler)
        self.conn.send(message(reg, 0, enc_uint(name) + enc_str(iface) + enc_uint(min(ver, version)) + enc_uint(oid)))
        return oid

    def _on_registry(self, op, p):
        if op == 0:
            name = struct.unpack_from('=I', p, 0)[0]
            iface, off = dec_str(p, 4)
            ver = struct.unpack_from('=I', p, off)[0]
            if iface == 'wl_output' and iface in self.globals:
                return                                                 # first output wins
            self.globals[iface] = (name, ver)

    def _on_wm_base(self, op, p):
        if op == 0:                                                    # ping -> pong or cage kills us
            self.conn.send(message(self.wm_base, 3, p[:4]))

    def _on_output(self, op, p):
        if op == 1:                                                    # mode(flags, w, h, refresh)
            flags, w, h, _r = struct.unpack_from('=Iiii', p, 0)
            if flags & 1:                                              # current
                self.out_w, self.out_h = w, h

    def _alloc(self, w, h):
        stride = w * 4
        one = stride * h
        fd = os.memfd_create('coin-dojo-overlay', os.MFD_CLOEXEC)
        try:
            os.ftruncate(fd, one * 2)
            self.mm = mmap.mmap(fd, one * 2)
            self.pool = self.conn.new_id()
            self.conn.send(message(self.shm, 0, enc_uint(self.pool) + enc_int(one * 2)), fds=[fd])
        finally:
            os.close(fd)
        base = ctypes.addressof(ctypes.c_char.from_buffer(self.mm))
        self.buffers = []
        for k in range(2):
            bid = self.conn.new_id(self._buffer_handler(k))
            self.conn.send(message(self.pool, 0, enc_uint(bid) + enc_int(k * one) + enc_int(w) + enc_int(h)
                                   + enc_int(stride) + enc_uint(SHM_ARGB8888)))
            self.buffers.append((bid, base + k * one))
            self.busy[k] = False
        self.size = (w, h)

    def _buffer_handler(self, k):
        def h(op, p):
            if op == 0:                                                # release
                self.busy[k] = False
        return h

    def _paint(self, k, kind, left=0, total=8):
        w, h = self.size
        self.painter.render(self.buffers[k][1], w, h, w * 4, kind, left, total)

    # -- map / unmap ----------------------------------------------------------------------------
    def _on_xsurf(self, op, p):
        if op == 0:                                                    # configure(serial)
            self.cfg_serial = struct.unpack_from('=I', p, 0)[0]
            self.cfg_event.set()

    def _on_toplevel(self, op, p):
        if op == 0:                                                    # configure(w, h, states)
            self.cfg_size = struct.unpack_from('=ii', p, 0)

    def _map(self):
        """Create the toplevel and wait for its first configure. Caller holds the lock."""
        c = self.conn
        self.cfg_event.clear()
        self.cfg_serial = None
        self.surface = c.new_id()
        c.send(message(self.compositor, 0, enc_uint(self.surface)))
        self.xsurf = c.new_id(self._on_xsurf)
        c.send(message(self.wm_base, 2, enc_uint(self.xsurf) + enc_uint(self.surface)))
        self.toplevel = c.new_id(self._on_toplevel)
        c.send(message(self.xsurf, 1, enc_uint(self.toplevel)))
        c.send(message(self.toplevel, 2, enc_str(TITLE)))              # set_title
        c.send(message(self.toplevel, 3, enc_str(TITLE)))              # set_app_id
        c.send(message(self.surface, 6))                               # initial commit, no buffer
        if not self.cfg_event.wait(CONFIGURE_TIMEOUT) or self.cfg_serial is None:
            raise OSError('no configure from the compositor')
        c.send(message(self.xsurf, 4, enc_uint(self.cfg_serial)))      # ack_configure
        w, h = self.cfg_size
        if w > 0 and h > 0 and (w, h) != self.size:                   # cage gave another size
            self._realloc(w, h)

    def _realloc(self, w, h):
        for bid, _a in self.buffers:
            self.conn.send(message(bid, 0))
        self.conn.send(message(self.pool, 1))
        self.buffers = []
        self._alloc(w, h)

    def _present(self, k, wait=0.0):
        """Attach buffer k, commit; with wait>0 block until the frame callback (presented) or wait s."""
        c = self.conn
        done = threading.Event()
        bid = self.buffers[k][0]
        w, h = self.size
        c.send(message(self.surface, 1, enc_uint(bid) + enc_int(0) + enc_int(0)))   # attach
        c.send(message(self.surface, 2, enc_int(0) + enc_int(0) + enc_int(w) + enc_int(h)))
        cb = c.new_id(lambda op, p: done.set())
        c.send(message(self.surface, 3, enc_uint(cb)))                 # frame callback
        c.send(message(self.surface, 6))                               # commit
        self.busy[k] = True
        self.front = k
        return done.wait(wait) if wait > 0 else True

    def _unmap(self):
        c = self.conn
        for oid, op in ((self.toplevel, 0), (self.xsurf, 0), (self.surface, 0)):
            if oid:
                c.send(message(oid, op))
        self.surface = self.xsurf = self.toplevel = None

    def _free_buffer(self):
        for k in (1 - self.front, self.front):
            if not self.busy.get(k):
                return k
        return None

    # -- public ---------------------------------------------------------------------------------
    def _try_lock(self, wait=PRESS_LOCK_WAIT):
        return self.lock.acquire(timeout=wait)

    def show_prompt(self, seconds=8):
        """Never connects here: a press must not wait on a slow/half-dead compositor. powerd's primer
        thread (re)primes in the background; not ready (or busy priming) -> False at once, powerd
        uses the fallback."""
        if not self._try_lock():
            return False
        try:
            try:
                if not self.ready():
                    return False
                self.gen += 1
                if not self.surface:
                    self._map()
                if not self.fresh:                                     # stale frame: paint a new one (~ms)
                    k = self._free_buffer()
                    self.front = self.front if k is None else k
                    self._paint(self.front, 'prompt', seconds, seconds)
                self._present(self.front)
                self.fresh = False
                self.shown = 'prompt'
                gen = self.gen
                self.ticker = threading.Thread(target=self._tick, args=(gen, seconds), daemon=True)
                self.ticker.start()
                return True
            except Exception as e:
                self.log('overlay show failed: %r' % (e,))
                self._drop()
                return False
        finally:
            self.lock.release()

    def _tick(self, gen, seconds):
        start = time.monotonic()
        for left in range(seconds - 1, -1, -1):
            time.sleep(max(0.0, start + (seconds - left) - time.monotonic()))
            with self.lock:
                if gen != self.gen or self.shown != 'prompt' or not self.ready():
                    return
                k = self._free_buffer()
                if k is None:
                    continue
                try:
                    self._paint(k, 'prompt', left, seconds)
                    self._present(k)
                except Exception as e:
                    self.log('overlay tick failed: %r' % (e,))
                    return

    def show_shutdown(self, wait=0.3):
        """Show the 'safe to switch off' frame; block at most ~`wait` s for it to reach the screen."""
        if not self._try_lock():
            return False
        try:
            try:
                if not self.ready():
                    return False
                self.gen += 1                                          # stops the countdown ticker
                if not self.surface:
                    self._map()
                k = self._free_buffer()
                if k is None:
                    k = 1 - self.front
                self._paint(k, 'off')
                self.shown = 'off'
                self.fresh = False
                return self._present(k, wait=wait)
            except Exception as e:
                self.log('overlay shutdown frame failed: %r' % (e,))
                return False
        finally:
            self.lock.release()

    def hide(self):
        if not self._try_lock(1.0):
            return
        try:
            self.gen += 1
            self.shown = None
            try:
                if self.ready() and self.surface:
                    self._unmap()
                    # re-render the prompt's first frame now, so the next press is instant again
                    k = self._free_buffer()
                    if k is not None:
                        self._paint(k, 'prompt', 8, 8)
                        self.front = k
                        self.fresh = True
            except Exception as e:
                self.log('overlay hide failed: %r' % (e,))
                self._drop()
        finally:
            self.lock.release()

    def _drop(self):
        if self.conn:
            self.conn.close()
        self.conn = None
        self._reset()


if __name__ == '__main__':
    # Manual check under a compositor: python3 overlay.py  (prompt 3 s, shutdown frame 2 s, hide)
    import sys
    ov = Overlay(log=lambda m: sys.stderr.write(m + '\n'))
    t = time.monotonic()
    ok = ov.prime()
    print('prime', ok, '%.1f ms' % ((time.monotonic() - t) * 1000))
    t = time.monotonic()
    print('show', ov.show_prompt(8), '%.1f ms' % ((time.monotonic() - t) * 1000))
    time.sleep(3)
    t = time.monotonic()
    print('off', ov.show_shutdown(), '%.1f ms' % ((time.monotonic() - t) * 1000))
    time.sleep(2)
    ov.hide()
    sys.exit(0 if ok else 1)
