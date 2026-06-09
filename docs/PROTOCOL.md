# Litra Beam LX HID protocol notes

The control protocol used here was reverse-engineered by
[Tim Rogers](https://github.com/timrogers) and contributors in
[`litra`](https://github.com/timrogers/litra) and
[`litra-rs`](https://github.com/timrogers/litra-rs) (MIT licensed). Those
projects target **USB**. This file records what's needed to drive the same
device over **Bluetooth-HID**, and the Beam-LX-specific differences.

## Transport

Once the Beam LX is BLE-paired, BlueZ's HID-over-GATT (HoGP) support hands it to
the kernel, which exposes it as a standard HID device at `/dev/hidraw*`. The
device presents a Logitech vendor HID collection:

- **Usage Page** `0xFF43`
- **Report ID** `0x11`, 19-byte payload (20 bytes including the report id)

The same vendor report works identically over USB and BLE. Only the USB/BLE
product id differs:

| Transport | Bus | VID:PID |
|---|---|---|
| USB | 0003 | `046D:C903` |
| Bluetooth LE | 0005 | `046D:B903` |

## Report layout (20 bytes)

```
byte 0   0x11        report id
byte 1   0xff        direct addressing
byte 2   feature     0x06 front, 0x0a back power/brightness, 0x0c back colour
byte 3   function    e.g. 0x1c power, 0x4c brightness, 0x9c temperature
byte 4+  parameters  zero-padded to 20 bytes
```

> **Key Beam LX difference:** the front light uses feature index **`0x06`**.
> The older Litra Glow / Litra Beam use `0x04`. Sending `0x04` to a Beam LX
> silently does nothing — this is the single most common reason "it doesn't work".

## Front light (feature `0x06`)

| Action | Bytes | Notes |
|---|---|---|
| Power on | `11 ff 06 1c 01` | |
| Power off | `11 ff 06 1c 00` | |
| Brightness | `11 ff 06 4c <hi> <lo>` | big-endian **lumens**, 30–400 |
| Temperature | `11 ff 06 9c <hi> <lo>` | big-endian **Kelvin**, 2700–6500 |
| Query power | `11 ff 06 01` | response byte[4]: 1=on |
| Query brightness | `11 ff 06 31` | response bytes[4:6] = lumens |
| Query temperature | `11 ff 06 81` | response bytes[4:6] = Kelvin |

Example: brightness 100 lm → `11 ff 06 4c 00 64`; 4000 K → `11 ff 06 9c 0f a0`.

## Back RGB strip (Beam LX only)

The Beam LX has a 7-zone RGB strip on the back, addressed via two features.

### Power / brightness (feature `0x0a`)

| Action | Bytes | Notes |
|---|---|---|
| Back on | `11 ff 0a 4b 01` | |
| Back off | `11 ff 0a 4b 00` | |
| Back brightness | `11 ff 0a 2b 00 <pct>` | 1–100 (0 is rejected) |
| Query back power | `11 ff 0a 3b` | response byte[4]: 1=on |
| Query back brightness | `11 ff 0a 1b` | response bytes[4:6] = percent |

### Colour zones (feature `0x0c`)

Set a zone's colour:

```
11 ff 0c 1b <zone> <R> <G> <B> ff 00 00 00 ff 00 00 00 ff 00 00 00
```

- `zone` is 1–7.
- Each of R, G, B must be **≥ 1** — a zero channel can hang the device.

Then **commit** to latch the queued colours:

```
11 ff 0c 7b 00 00 01
```

**Avoiding the left-to-right "wipe":** the device only applies queued zone
colours on commit. To change all zones at once, write all seven zone reports
*first*, then send a **single** commit. Committing after each zone produces a
visible sweep because each commit latches one zone over BLE before the next
report arrives. (`device.py::back_color` does the batch-then-commit.)

## State read-back

Query commands are sent like any other report; the device replies with an input
report (report id `0x11`) echoing the same feature index in byte 2. Open the
hidraw node `O_RDWR`, write the query, then read until a matching `0x11` report
arrives (see `device.py::_query`). This requires read access to the hidraw node.
