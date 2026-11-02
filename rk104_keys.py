"""Key-mapping actions for the RK104 (see PROTOCOL.md, commands 0x11 / 0x27).

An action is a small JSON-able dict; encode() turns it into the 4-byte
keymap entry. Keys without an action are sent as 00 00 00 00 = firmware
default (this is what the vendor app does for unmapped keys).
"""

_letters = {0x04 + i: chr(ord("A") + i) for i in range(26)}
_digits = {0x1E + i: str((i + 1) % 10) for i in range(10)}
HID = {**_letters, **_digits,
       0x28: "Enter", 0x29: "Esc", 0x2A: "Backspace", 0x2B: "Tab", 0x2C: "Space",
       0x2D: "-", 0x2E: "=", 0x2F: "[", 0x30: "]", 0x31: "\\", 0x32: "# (ISO)",
       0x33: ";", 0x34: "'", 0x35: "`", 0x36: ",", 0x37: ".", 0x38: "/",
       0x39: "Caps Lock",
       **{0x3A + i: "F%d" % (i + 1) for i in range(12)},
       0x46: "Print Screen", 0x47: "Scroll Lock", 0x48: "Pause", 0x49: "Insert",
       0x4A: "Home", 0x4B: "Page Up", 0x4C: "Delete", 0x4D: "End", 0x4E: "Page Down",
       0x4F: "Right", 0x50: "Left", 0x51: "Down", 0x52: "Up",
       0x53: "Num Lock", 0x54: "KP /", 0x55: "KP *", 0x56: "KP -", 0x57: "KP +",
       0x58: "KP Enter", **{0x59 + i: "KP %d" % ((i + 1) % 10) for i in range(10)},
       0x63: "KP .", 0x64: "\\ (ISO)", 0x65: "Menu",
       **{0x68 + i: "F%d" % (i + 13) for i in range(12)},
       0x7F: "Mute (kbd)", 0x80: "Vol Up (kbd)", 0x81: "Vol Down (kbd)",
       0xE0: "Left Ctrl", 0xE1: "Left Shift", 0xE2: "Left Alt", 0xE3: "Left Win",
       0xE4: "Right Ctrl", 0xE5: "Right Shift", 0xE6: "Right Alt", 0xE7: "Right Win"}

SHORT = {"Left Ctrl": "LCtl", "Left Shift": "LSft", "Left Alt": "LAlt", "Left Win": "LWin",
         "Right Ctrl": "RCtl", "Right Shift": "RSft", "Right Alt": "RAlt", "Right Win": "RWin",
         "Backspace": "Bksp", "Caps Lock": "Caps", "Print Screen": "PrtSc",
         "Scroll Lock": "ScrLk", "Page Up": "PgUp", "Page Down": "PgDn", "Delete": "Del",
         "Insert": "Ins", "Num Lock": "Num"}

MODS = [(0x01, "Ctrl"), (0x02, "Shift"), (0x04, "Alt"), (0x08, "Win")]

MOUSE = {  # name -> (b1, b2)
    "Left click": (0x01, 0x01), "Right click": (0x01, 0x02), "Middle click": (0x01, 0x04),
    "Back": (0x01, 0x08), "Forward": (0x01, 0x10),
    "Wheel up": (0x03, 0x01), "Wheel down": (0x03, 0xFF),
}

CONSUMER = {  # HID consumer page usages
    "Play/Pause": 0xCD, "Stop": 0xB7, "Previous": 0xB6, "Next": 0xB5,
    "Volume up": 0xE9, "Volume down": 0xEA, "Mute": 0xE2,
    "Calculator": 0x192, "Mail": 0x18A, "Browser home": 0x223, "My computer": 0x194,
    "Brightness up": 0x6F, "Brightness down": 0x70,
}


def encode(action):
    """Action dict -> 4-byte keymap entry."""
    t = action["type"]
    if t == "key":
        mod, code = action.get("mod", 0), action["code"]
        if 0xE0 <= code <= 0xE7:  # modifier key: goes into the modifier byte
            return bytes((0x02, mod | (1 << (code - 0xE0)), 0, 0))
        return bytes((0x02, mod, code, 0))
    if t == "mouse":
        b1, b2 = MOUSE[action["name"]]
        return bytes((0x01, b1, b2, 0))
    if t == "consumer":
        c = action["code"]
        return bytes((0x03, c & 0xFF, c >> 8, 0))
    if t == "disabled":  # key event with no usage; untested
        return bytes((0x02, 0, 0, 0))
    raise ValueError("unknown action %r" % (action,))


def describe(action, short=False):
    t = action["type"]
    if t == "key":
        name = HID.get(action["code"], "0x%02X" % action["code"])
        if short:
            name = SHORT.get(name, name)
        mods = [n for bit, n in MODS if action.get("mod", 0) & bit]
        if short:
            mods = [m[0] for m in mods]  # C S A W
        return "+".join(mods + [name])
    if t == "mouse":
        return action["name"] if short else "Mouse " + action["name"]
    if t == "consumer":
        n = next((k for k, v in CONSUMER.items() if v == action["code"]),
                 "0x%03X" % action["code"])
        return n
    if t == "disabled":
        return "✕" if short else "Disabled"
    return "?"
