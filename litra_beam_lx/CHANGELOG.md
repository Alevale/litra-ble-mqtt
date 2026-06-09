# Changelog

## 0.2.0

- Initial add-on release.
- Multi-light support: configure any number of Litra lights by Bluetooth address.
- Litra Beam LX front light (brightness + colour temperature) and 7-zone back
  RGB strip as separate Home Assistant light entities.
- Older models (`beam`, `glow`) supported as front-light-only.
- Automatic pairing/trusting of configured lights on startup.
- MQTT broker details obtained from the Supervisor (no manual credentials).
- Per-light availability with bridge-wide last-will for crash detection.
