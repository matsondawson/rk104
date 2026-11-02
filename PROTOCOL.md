# RAGNOK RK104 USB protocol (reverse engineered)

Source: `RAGNOK RK104 Driver-1.0.0.0.exe` (Inno Setup 6.1, LZMA solid). The main
app is a 32-bit MFC/"MUI" program that talks HID through `hid.dll`, which it loads
at runtime. The addresses below refer to that EXE (image base 0x400000) after
undoing Inno's E8/E9 call-address transform.

Status: from static analysis. 2026-11-02: tested on the wired keyboard
(hidraw interface 2) with `rk104.py`. Setting all LEDs to red worked, which
confirms the transport and the start/commit sequence. Other commands are not yet
tested individually.

## Devices

From the embedded `config.xml`:

| mode        | VID:PID   | HID interface Windows uses | product         |
|-------------|-----------|----------------------------|-----------------|
| USB (wired) | 0C45:8006 | MI_00 path match           | "HFD RK104"     |
| 2.4G dongle | 05AC:024F | MI_03                      | "RK104 2.4G"    |

On Linux, the wired keyboard shows up as 3 hidraw nodes. The config interface
is **interface 2** (`1-x:1.2`). Its report descriptor:

```
06 13 FF   Usage Page 0xFF13 (vendor)
09 01 A1 01 15 00 26 FF 00 75 08 95 40
09 02 81 02   Input   64 bytes
09 03 91 02   Output  64 bytes
0A 00 FF B1 02 Feature 64 bytes   (no report ID)
```

## Transport: wired, feature reports

- Each transfer is `HidD_SetFeature(65 bytes)`: report ID `0x00` followed by a
  64-byte payload. On Linux use `HIDIOCSFEATURE(65)` with `buf[0]=0`.
- Commands that expect an ack: after SetFeature, sleep `cmd_delaytime` (35 ms,
  from the device XML), then `HidD_GetFeature(65)`. Success when
  `resp[1+3] == 0x01`, i.e. payload byte 3 is 1. A variant at 0x44efb0 waits
  5 ms and retries while payload byte 3 is `0xFF` (busy).
- Bulk data longer than 64 bytes goes as `len/64` back-to-back 64-byte
  SetFeature reports with no ack. Each failing report is retried up to 10
  times.

### Command header (64-byte payload)

```
byte 0 : 0x04
byte 1 : command
byte 2 : sub-arg (usually 0)
byte 8 : number of 64-byte data blocks that follow
rest   : 0
```

### Typical write transaction

```
04 18                     start        (ack)
04 <cmd> .. [8]=N         announce     (ack)
<N x 64-byte data blocks> data         (no ack)
04 02                     commit/save  (ack)
04 F0                     refresh/apply (sometimes ack, sometimes not)
```

## Commands

### 0x13: set lighting effect (1 block)
`04 18`, then `04 13 00 00 00 00 00 00 01`, then this block, then `04 02`, then `04 F0`.

| off   | meaning |
|-------|---------|
| 0     | mode. 0 = off, 1..18 = built-in effect, 0x80 = custom per-key (see 0x23) |
| 1,2,3 | R, G, B (COLORREF bytes, low to high) |
| 8     | colorful / rainbow flag (0 = use RGB, 1 = multicolour) |
| 9     | brightness 0..6 |
| 10    | speed 0..6 |
| 11    | direction |
| 14,15 | `AA 55` trailer |

When the mode is 0, only bytes 0 and 14..15 are set. Byte 9 carries brightness
for mode 0x80 too.

Mode values. Mode 1 = static colour is **confirmed** on hardware
(`mode 1 --color ff0000` turned the whole board solid red). The rest follow the
generic effect list in the language file (string IDs 521..540), assuming
value = position + 1. Only mode 1 is verified:
1 Static, 2 SingleOn, 3 SingleOff, 4 Glittering, 5 Falling, 6 Colourful,
7 Breath, 8 Spectrum, 9 Outward, 10 Scrolling, 11 Rolling (the default,
`default_mode=11`), 12 Rotating, 13 Explode, 14 Launch, 15 Ripples,
16 Flowing, 17 Pulsating, 18 Tilt, 19 Shuttle, 20 LED Off.

The app also has a themed name list (IDs 500..517: "Stellar Epoch", "Photon
Strike", ...). It is probably UI naming only. Its first entry does not match
mode 1 = static.

The block layout matches the DB table `t_light_data(mode, brightness, speed,
direction, colorful, colorindex, color_value, ...)`. Builder: 0x42b040.

### 0x23: custom per-key colours (stored on the keyboard)
Builder: 0x44b910. First send the 0x13 transaction with mode `0x80` and a
brightness (0x44b800). Then:
`04 18`, then `04 23 .. [8]=9`, then 9 blocks (576 bytes), then `04 02`, then `04 F0`.

Data = 144 entries of 4 bytes: `[light_index, R, G, B]` at offset
`light_index*4`. The last 2 bytes of the 576 (offset 0x23E) are `AA 55`.
Mono variant: `[8]=3`, 192 bytes, one byte per light_index (`FF` = on),
`AA 55` at 0xBE.

`light_index` comes from `extracted/rk104_layout.xml` (1..123; Esc=1).

Split-board keys (tested on hardware 2026-11-02): the two B keys share
light_index 78 and key_index 78, and the two Space keys share 94. They light
together and the keyboard reports them as the same key, so they cannot be set
separately. Indices not listed in the layout XML (14-18, 35, 36, 53, 54, 71,
72, 89, 90, 107-111, 114, 124-142) have no LED. A keymap probe that sent a
unique key from each of them produced nothing from either B or either Space.

### 0x20: real-time / direct LED stream (not saved)
Builder: 0x427fe0 (also 0x432990). This is what the app uses for music mode and
real-time effects. No `04 18`. Uses the 5 ms busy-retry ack variant.
`04 20 .. [8]=8`, then 8 blocks (512 bytes) of `[light_index, R, G, B]`, then `04 02`.
The app pre-scales RGB by brightness (`c*b>>8`). Mono: `[8]=3`, 192 bytes.

### 0x11 / 0x27: key remap (base layer / Fn layer)
Builder: 0x4185e0. `04 18`, then `04 11|27 .. [8]=9`, then 9 blocks, then `04 02`, then `04 F0`.

576 bytes = 144 x 4-byte entries at `key_index*4` (key_index from the layout
XML). `AA 55` at offset 0x23E.

An all-zero entry means "firmware default". The vendor database only stores
remapped keys, and "Restore defaults" (0x414690) deletes every row and then
uploads a map that is all zeros. A remap therefore only needs to fill in the
keys it changes. The app writes both layers: base first, then Fn
(0x434840). Entry formats:

| bytes             | action |
|-------------------|--------|
| `02 mod code 00`  | keyboard key. `mod` = HID modifier bits (01 LCtrl, 02 LShift, 04 LAlt, 08 LGUI, ...), `code` = HID usage. For a modifier key itself (E0..E7): `02 code' 00 00` |
| `01 01 mask 00`   | mouse button: mask 01 L, 02 R, 04 M, 08 Back, 10 Fwd |
| `01 03 01/FF 00`  | mouse wheel up/down |
| `03 lo hi 00`     | consumer usage, e.g. CD play/pause, B7 stop, B6 prev, B5 next, E9 vol+, EA vol-, E2 mute |
| `06 idx mode cnt` | run macro #idx (mode = play type, cnt = repeat count) |
| `05 02 v 00`      | special/device function v (app types 8..11) |
| `05 03 00 00`     | (app type 1, probably "disable"/restore) |
| `07 a b c`        | app type 0xD, 3 raw params |

Shortcut presets the app emits: Win+D `02 08 07`, Win+E `02 08 08`,
Win+L `02 08 0F`, Ctrl+W `02 01 1A`, Alt+Tab `02 04 2B`, Ctrl+C/V/X
`02 01 06/19/1B`.

### 0x15: macro upload
Builder: 0x42d630. `04 19` (ack). Then `04 15 .. [8]=N` (ack), sleep 30 ms,
send N blocks, sleep 30 ms, then `04 02`.

Buffer layout:
- Header: 100 entries x 4 bytes at 0x000..0x18F, one per macro:
  `[off_lo, off_hi, nevents_lo, nevents_hi]`. `FF FF FF FF` = empty.
- Events start at 0x190. Each event is 4 bytes `[b0 b1 b2 b3]`:
  - key down `00 00 hid B0`; key up `00 00 hid 30`
  - mouse down `00 00 mask 90`; mouse up `00 00 mask 10` (mask 1 = L, 2 = R, 4 = M)
  - delay `lo hi 00 50` (milliseconds, minimum 10). One follows every action.
- The next macro starts 8 bytes after the previous one ends.
- N = ceil-ish(len/64) (+1 if len is an exact multiple of 64). Last 2 bytes
  of the `N*64` buffer are `AA 55`.

### 0x17: device settings (1 block)
Builder: 0x414230. `04 18`, then `04 17 01 .. [8]=1`, then this block, then `04 02`.

| off   | meaning |
|-------|---------|
| 0..1  | `00 01` |
| 2,3,4 | three checkbox flags (UI controls 0x678/0x67c/0x680, not identified) |
| 5     | fn_switch |
| 6     | sleep_time |
| 8     | key_respondtime |
| 62,63 | `AA 55` |

### Other commands seen

| cmd       | notes |
|-----------|-------|
| 0x28      | `[8]=1`. Block contains `5A` and the local date/time: clock sync (0x423b48) |
| 0x72      | used with a 4 KB buffer (0x4230f2): LED animation/frames upload |
| 0xF5      | `04 F5 .. [8]=9|3`, then the device returns 9 or 3 blocks via GetFeature: read back per-key data (0x42ae60) |
| 0x02      | commit |
| 0x18      | start |
| 0x19      | start (used before macros) |
| 0xF0      | apply/refresh |

## Transport: 2.4G dongle (05AC:024F MI_03), output reports
Function 0x44f4e0. Writes a 33-byte (`0x21`) output report with WriteFile,
then reads back until response bytes 1..3 echo the request (20 tries, 10 ms
apart). A checksum is placed at offset 0x20: the byte-sum of the first n
bytes. Payloads carry the same data with a different header, e.g.
`05 10 00 mode ...` for light mode, `05 10 .. 80` for custom, `05 01 ..` for
settings, and `AA 55` trailers again. Not decoded further.
