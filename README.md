# rk104

Linux control of the **RAGNOK RK104** keyboard: lighting effects, per-key RGB,
live/real-time colours and key remapping. Ragnok ships Windows-only software.
The protocol was reverse engineered from that software and is documented in
[PROTOCOL.md](PROTOCOL.md).

Supported: wired USB mode (`0c45:8006`). The 2.4G dongle (`05ac:024f`) uses a
different transport that is only partly documented.

## Setup

Allow your user to access the keyboard's hidraw device:

```sh
echo 'KERNEL=="hidraw*", ATTRS{idVendor}=="0c45", ATTRS{idProduct}=="8006", MODE="0660", TAG+="uaccess"' \
  | sudo tee /etc/udev/rules.d/70-rk104.rules
sudo udevadm control --reload && sudo udevadm trigger
```

Replug the keyboard afterwards. The GUI needs PySide6 (`pacman -S pyside6` or
`pip install PySide6`). The CLI uses only the Python standard library.

## GUI

```sh
python3 rk104_gui.py            # talk to the keyboard
python3 rk104_gui.py --dry-run  # print packets instead
```

- **Effects**: built-in modes with colour, rainbow, brightness, speed and direction.
- **Per-key**: paint keys, fill or select groups, live preview (not saved) or save to the keyboard.
  Schemes can be saved and loaded as JSON.
- **Key mapping**: remap keys on the base or Fn layer to another key (with
  modifiers), a mouse action or a media key. The keyboard cannot report its
  mapping back, so the GUI keeps its copy in `~/.config/rk104/keymap.json`.

## CLI

```sh
python3 rk104.py mode 1 --color ff0000            # static red
python3 rk104.py mode 11 --colorful 1 --speed 4   # built-in effect
python3 rk104.py perkey all=000000 esc=ff0000 w=00ff00 a=00ff00 s=00ff00 d=00ff00
python3 rk104.py direct all=0000ff                # real-time, not saved
python3 rk104.py keymap-reset                     # restore default key mapping
python3 rk104.py --dry-run mode 0                 # show packets only
```

Effect numbers: 0 off, 1 Static, 2 SingleOn, 3 SingleOff, 4 Glittering,
5 Falling, 6 Colourful, 7 Breath, 8 Spectrum, 9 Outward, 10 Scrolling,
11 Rolling, 12 Rotating, 13 Explode, 14 Launch, 15 Ripples, 16 Flowing,
17 Pulsating, 18 Tilt, 19 Shuttle, 20 LED Off.

## Files

- `rk104.py`: protocol library and CLI
- `rk104_keys.py`: HID key, mouse and media tables for remapping
- `rk104_gui.py`: PySide6 GUI
- `extracted/rk104_layout.xml`: key positions and LED/key indices, taken from the vendor software
- `PROTOCOL.md`: protocol notes

Not affiliated with Ragnok. Use at your own risk.
