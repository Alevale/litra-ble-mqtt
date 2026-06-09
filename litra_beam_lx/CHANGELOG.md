# Changelog

## 0.3.2

- Wait for the kernel hidraw node to appear after bonding (it can lag a few
  seconds behind connect) instead of giving up immediately.
- When a light bonds but no HID device shows up, log diagnostics (bluetoothctl
  info + hidraw/sys state) to pinpoint the cause.

## 0.3.1

- More reliable pairing: keep the BLE scan running *through* the pair attempt
  (turning it off first could drop the device and fail the bond), retry a
  couple of times, and longer waits — BLE bonding over a rotating random
  address is flaky.
- Default scan time raised 8s → 20s (configurable up to 60s).
- On a failed pairing, the relevant `bluetoothctl` output is now logged so the
  cause (e.g. AuthenticationFailed, out of range) is visible.

## 0.3.0

- **Auto-discovery**: with `auto_discover` on (default), the add-on scans for
  Litra lights in pairing mode, bonds them, and exposes every connected Litra
  with its model detected automatically — no need to find or type MAC addresses.
- The `lights` list is now optional (use it only to pin names/models or to
  control exactly which lights are exposed).

## 0.2.0

- Initial add-on release.
- Multi-light support: configure any number of Litra lights by Bluetooth address.
- Litra Beam LX front light (brightness + colour temperature) and 7-zone back
  RGB strip as separate Home Assistant light entities.
- Older models (`beam`, `glow`) supported as front-light-only.
- Automatic pairing/trusting of configured lights on startup.
- MQTT broker details obtained from the Supervisor (no manual credentials).
- Per-light availability with bridge-wide last-will for crash detection.
