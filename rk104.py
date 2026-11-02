#!/usr/bin/env python3
"""RAGNOK RK104 (wired, 0c45:8006) lighting library + CLI. See PROTOCOL.md.

CLI examples:
  rk104.py --dry-run mode 1 --color ff0000 --brightness 6 --speed 3
  rk104.py mode 0                         # lights off
  rk104.py perkey all=000000 Esc=ff0000 W=00ff00 A=00ff00 S=00ff00 D=00ff00
  rk104.py direct all=0000ff              # real-time, not saved
"""
import argparse, fcntl, glob, os, re, sys, threading, time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

VID, PID, IFACE = 0x0C45, 0x8006, 2
DELAY = 0.035
HERE = os.path.dirname(os.path.abspath(__file__))
LAYOUT = os.path.join(HERE, "extracted", "rk104_layout.xml")

# Mode 1 confirmed static; 7/8/11 confirmed to behave as named.
MODES = ["Off", "Static", "SingleOn", "SingleOff", "Glittering", "Falling", "Colourful",
         "Breath", "Spectrum", "Outward", "Scrolling", "Rolling", "Rotating", "Explode",
         "Launch", "Ripples", "Flowing", "Pulsating", "Tilt", "Shuttle", "LED Off"]
MODE_CUSTOM = 0x80
MAX_BRIGHTNESS = 6
MAX_SPEED = 6


class DeviceError(Exception):
    pass


class DeviceNotFound(DeviceError):
    """No wired RK104 config interface present."""


class DevicePermission(DeviceError):
    """Interface present but not openable by this user."""

    def __init__(self, msg, path):
        super().__init__(msg)
        self.path = path


DONGLE = (0x05AC, 0x024F)


def present_ids():
    """Set of (vid, pid) for all hidraw devices currently present."""
    ids = set()
    for h in glob.glob("/sys/class/hidraw/hidraw*"):
        m = re.search(r"([0-9A-F]{4}):([0-9A-F]{4})\.",
                      os.path.basename(os.path.realpath(os.path.join(h, "device"))))
        if m:
            ids.add((int(m[1], 16), int(m[2], 16)))
    return ids


def _ioc(d, nr, size):
    return (d << 30) | (size << 16) | (ord("H") << 8) | nr


HIDIOCSFEATURE = _ioc(3, 0x06, 65)
HIDIOCGFEATURE = _ioc(3, 0x07, 65)


def find_hidraw():
    for h in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        dev = os.path.realpath(os.path.join(h, "device"))
        m = re.search(r"([0-9A-F]{4}):([0-9A-F]{4})\.", os.path.basename(dev))
        if not m or (int(m[1], 16), int(m[2], 16)) != (VID, PID):
            continue
        iface = os.path.basename(os.path.dirname(dev))  # e.g. 1-2:1.2
        if iface.endswith(".%d" % IFACE):
            return "/dev/" + os.path.basename(h)
    raise DeviceNotFound("RK104 (wired, 0c45:8006) not found")


@dataclass
class Key:
    name: str
    code: int
    key_index: int
    light_index: int
    x: int
    y: int
    w: int
    h: int


def load_layout(path=LAYOUT):
    """Return list of Key in the 800x300 coordinate space of assets/keyboard.png."""
    root = ET.parse(path).getroot()
    out = []
    for k in root.iter("key"):
        out.append(Key(k.get("name"), int(k.get("code"), 16), int(k.get("key_index")),
                       int(k.get("light_index")), int(k.get("rect_left")), int(k.get("rect_top")),
                       int(k.get("rect_width")), int(k.get("rect_height"))))
    return out


class RK104:
    """Thread-safe device handle. Every public method is one full transaction."""

    def __init__(self, dry_run=False):
        self.dry = dry_run
        self.fd = None
        self.lock = threading.Lock()

    # --- low level ---------------------------------------------------------
    def open(self):
        if self.dry or self.fd is not None:
            return
        path = find_hidraw()
        try:
            self.fd = os.open(path, os.O_RDWR)
        except PermissionError as e:
            raise DevicePermission("no permission for %s (install the udev rule)" % path, path) from e

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def _set(self, payload):
        assert len(payload) == 64
        if self.dry:
            print("SET", bytes(payload).hex(" "))
            return
        try:
            fcntl.ioctl(self.fd, HIDIOCSFEATURE, bytearray([0]) + bytearray(payload))
        except OSError as e:
            self.close()
            raise DeviceError("write failed: %s" % e) from e

    def _get(self):
        if self.dry:
            return bytes(64)
        buf = bytearray(65)
        try:
            fcntl.ioctl(self.fd, HIDIOCGFEATURE, buf, True)
        except OSError as e:
            self.close()
            raise DeviceError("read failed: %s" % e) from e
        return bytes(buf[1:])

    def _cmd(self, c, nblocks=0, arg=0, ack=True):
        p = bytearray(64)
        p[0], p[1], p[2], p[8] = 0x04, c, arg, nblocks
        self._set(p)
        if ack:
            time.sleep(DELAY)
            r = self._get()
            if not self.dry and r[3] != 1:
                print("warn: cmd %02x ack byte3=%02x" % (c, r[3]), file=sys.stderr)

    def _bulk(self, data):
        assert len(data) % 64 == 0
        for i in range(0, len(data), 64):
            self._set(data[i:i + 64])

    def _transaction(self, cmd, data):
        self._cmd(0x18)
        self._cmd(cmd, len(data) // 64)
        self._bulk(data)
        self._cmd(0x02)
        self._cmd(0xF0, ack=False)

    # --- high level --------------------------------------------------------
    def set_effect(self, mode, color=(255, 255, 255), colorful=False,
                   brightness=MAX_BRIGHTNESS, speed=3, direction=0):
        """Built-in effect (saved on keyboard). mode 0 = off."""
        p = bytearray(64)
        p[0] = mode
        if mode:
            p[1:4] = bytes(color)
            p[8], p[9], p[10], p[11] = int(colorful), brightness, speed, direction
        p[14:16] = b"\xaa\x55"
        with self.lock:
            self.open()
            self._transaction(0x13, p)

    def set_custom(self, colors, brightness=MAX_BRIGHTNESS):
        """Per-key colours, saved on keyboard. colors: {light_index: (r,g,b)}."""
        p = bytearray(64)
        p[0], p[9] = MODE_CUSTOM, brightness
        p[14:16] = b"\xaa\x55"
        buf = _rgb_table(colors, 576)
        buf[-2:] = b"\xaa\x55"
        with self.lock:
            self.open()
            self._transaction(0x13, p)
            self._transaction(0x23, buf)

    def set_keymap(self, layer, entries):
        """Write one key layer. layer 0 = base (cmd 0x11), 1 = Fn layer (0x27).

        entries: {key_index: 4-byte entry}. Keys not listed are sent as zeros,
        which the keyboard treats as its firmware default.
        """
        buf = bytearray(576)
        for ki, e in entries.items():
            if 0 < ki and ki * 4 + 4 <= 574:
                buf[ki * 4:ki * 4 + 4] = bytes(e)
        buf[-2:] = b"\xaa\x55"
        with self.lock:
            self.open()
            self._transaction(0x27 if layer else 0x11, buf)

    def reset_keymap(self):
        """Restore both layers to firmware defaults."""
        self.set_keymap(0, {})
        self.set_keymap(1, {})

    def set_direct(self, colors):
        """Real-time per-key colours, not saved. colors: {light_index: (r,g,b)}."""
        buf = _rgb_table(colors, 512)
        with self.lock:
            self.open()
            self._cmd(0x20, 8)
            self._bulk(buf)
            self._cmd(0x02)


def _rgb_table(colors, size):
    buf = bytearray(size)
    for li, (r, g, b) in colors.items():
        if 0 < li and li * 4 + 4 <= size - 2:
            buf[li * 4:li * 4 + 4] = bytes((li, r, g, b))
    return buf


def rgb(s):
    v = int(s.lstrip("#"), 16)
    return (v >> 16) & 255, (v >> 8) & 255, v & 255


def colour_map(specs):
    by_name = {}
    keys = load_layout()
    for k in keys:
        by_name.setdefault(k.name.lower(), k.light_index)
    out = {}
    for s in specs:
        name, col = s.rsplit("=", 1)
        c = rgb(col)
        if name.startswith("#"):  # raw light index, e.g. #89=ff0000
            out[int(name[1:])] = c
        elif name.lower() == "all":
            for k in keys:
                out[k.light_index] = c
        elif name.lower() in by_name:
            out[by_name[name.lower()]] = c
        else:
            sys.exit("unknown key %r; names: %s" % (name, ", ".join(sorted(by_name))))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    sp = ap.add_subparsers(dest="op", required=True)
    m = sp.add_parser("mode")
    m.add_argument("mode", type=int)
    m.add_argument("--color", default="ffffff")
    m.add_argument("--colorful", type=int, default=0)
    m.add_argument("--brightness", type=int, default=6)
    m.add_argument("--speed", type=int, default=3)
    m.add_argument("--direction", type=int, default=0)
    pk = sp.add_parser("perkey")
    pk.add_argument("--brightness", type=int, default=6)
    pk.add_argument("keys", nargs="+", help="name=RRGGBB or all=RRGGBB")
    dr = sp.add_parser("direct")
    dr.add_argument("keys", nargs="+")
    sp.add_parser("keymap-reset", help="restore default key mapping (both layers)")
    sp.add_parser("keymap-probe", help="map every key index not in the layout to a unique "
                                       "letter/digit, to discover hidden key positions")
    a = ap.parse_args()
    d = RK104(a.dry_run)
    try:
        if a.op == "mode":
            d.set_effect(a.mode, rgb(a.color), a.colorful, a.brightness, a.speed, a.direction)
        elif a.op == "perkey":
            d.set_custom(colour_map(a.keys), a.brightness)
        elif a.op == "direct":
            d.set_direct(colour_map(a.keys))
        elif a.op == "keymap-reset":
            d.reset_keymap()
        elif a.op == "keymap-probe":
            used = {k.key_index for k in load_layout()}
            free = [i for i in range(1, 143) if i not in used]
            # a..z, 1..0, then Shift+a.. (shows as A, B, ...)
            codes = [(0, 0x04 + i) for i in range(26)] + [(0, 0x1E + i) for i in range(10)]
            codes += [(0x02, 0x04 + i) for i in range(26)]
            codes = [c for c in codes if c[1] != 0x05]  # skip b/B: real B types that
            entries, table = {}, []
            for ki, (mod, code) in zip(free, codes):
                entries[ki] = bytes((0x02, mod, code, 0))
                ch = "abcdefghijklmnopqrstuvwxyz"[code - 4] if code < 0x1E else "1234567890"[code - 0x1E]
                table.append("%s=#%d" % (ch.upper() if mod else ch, ki))
            d.set_keymap(0, entries)
            print("Probe map written. Type with the unknown keys; each character means:")
            print("  " + "  ".join(table))
            print("Restore afterwards with: python3 rk104.py keymap-reset")
    except DeviceError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
