# rk104

Linux control of the **RAGNOK RK104** keyboard: lighting effects, per-key RGB,
live/real-time colours and key remapping. Ragnok ships Windows-only software.
The protocol was reverse engineered from that software and is documented in
[PROTOCOL.md](PROTOCOL.md).

Supported: wired USB mode (`0c45:8006`). The 2.4G dongle (`05ac:024f`) uses a
different transport that is only partly documented.

## Setup

### 1. USB permissions

The tools talk to the keyboard through `/dev/hidraw*`. On most distros that
device is readable only by root, so add a udev rule that gives your user
access. Do not run the GUI as root.

**systemd distros** (Arch/CachyOS/Manjaro, Fedora, Ubuntu/Debian/Mint/Pop!_OS,
openSUSE, ...): grant access to whoever is logged in at the seat:

```sh
echo 'KERNEL=="hidraw*", ATTRS{idVendor}=="0c45", ATTRS{idProduct}=="8006", MODE="0660", TAG+="uaccess"' \
  | sudo tee /etc/udev/rules.d/70-rk104.rules
sudo udevadm control --reload && sudo udevadm trigger
```

The file name must sort before `73-seat-late.rules` for `uaccess` to apply,
which is why it starts with `70-`.

**Without systemd-logind** (Void, Alpine, Gentoo/OpenRC, Artix, Devuan, or SSH
sessions): `uaccess` does nothing there, so use a group instead:

```sh
sudo groupadd -f rk104
sudo usermod -aG rk104 "$USER"
echo 'KERNEL=="hidraw*", ATTRS{idVendor}=="0c45", ATTRS{idProduct}=="8006", MODE="0660", GROUP="rk104"' \
  | sudo tee /etc/udev/rules.d/70-rk104.rules
sudo udevadm control --reload && sudo udevadm trigger   # Alpine/mdev: replug or reboot
```

Log out and back in so the new group takes effect. (On Alpine with mdev
rather than eudev, add an equivalent mdev rule instead.)

**NixOS**, in `configuration.nix`:

```nix
services.udev.extraRules = ''
  KERNEL=="hidraw*", ATTRS{idVendor}=="0c45", ATTRS{idProduct}=="8006", MODE="0660", TAG+="uaccess"
'';
```

Then run `sudo nixos-rebuild switch`.

**Check that it worked:** unplug and replug the keyboard, then run:

```sh
python3 rk104.py direct all=00ff00   # whole board should turn green
```

If it says `no permission for /dev/hidrawN`, compare against
`ls -l /dev/hidraw*` and `getfacl /dev/hidrawN`. Your user should appear in
the ACL (uaccess) or the group (group method).

### 2. Python

The CLI uses only the Python 3 standard library. The GUI also needs PySide6:

| distro | command |
|---|---|
| Arch / CachyOS / Manjaro | `sudo pacman -S pyside6` |
| Fedora | `sudo dnf install python3-pyside6` |
| Debian 13+ / Ubuntu 24.10+ | `sudo apt install python3-pyside6.qtwidgets` |
| anything else | `python3 -m venv .venv && .venv/bin/pip install PySide6`, then run with `.venv/bin/python` |

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
