# Changelog

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
