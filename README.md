# Litra Beam LX → Home Assistant (over Bluetooth, via MQTT)

Control a **Logitech Litra Beam LX** from Home Assistant — including over
**Bluetooth**, which Logitech's own software and every existing open-source
Litra tool don't support. Exposes the front light (on/off, brightness, colour
temperature) **and** the 7-zone back RGB strip as native Home Assistant lights.

No cloud, no Logi software, no custom Home Assistant integration to maintain —
just a small MQTT bridge running on the machine the light is paired to.

```
┌─────────────┐   Bluetooth-HID    ┌──────────────────┐    MQTT     ┌────────────────┐
│ Litra Beam  │◀──(/dev/hidraw)───▶│  litra-mqtt      │◀──────────▶│ Home Assistant │
│ LX          │                    │  bridge (this)   │  discovery  │  + MQTT broker │
└─────────────┘                    └──────────────────┘             └────────────────┘
        the light + the bridge live on the same box;  HA can be anywhere on the LAN
```

## Why this exists

The Litra Beam LX speaks a Logitech vendor HID protocol. The excellent
[`litra`](https://github.com/timrogers/litra) and
[`litra-rs`](https://github.com/timrogers/litra-rs) projects reverse-engineered
that protocol, but only over **USB**. This project reuses the same protocol over
**Bluetooth Low Energy**: once the light is BLE-paired, Linux exposes it as a
`/dev/hidraw` device and the very same 20-byte reports work. See
[`docs/PROTOCOL.md`](docs/PROTOCOL.md) for the byte-level details and credits.

## Features

| Entity | Capabilities |
|---|---|
| **Front** light | on/off, brightness, colour temperature (2700–6500 K) |
| **Back RGB** light | on/off, brightness, RGB colour (all 7 zones, latched together) |

- Works over **Bluetooth** *or* USB (auto-detects `/dev/hidraw`).
- Home Assistant **MQTT discovery** — entities appear automatically.
- Availability tracking (HA shows the light as *unavailable* when it's asleep).
- Tiny: pure-Python, one dependency (`paho-mqtt`).
- Stand-alone **CLI** for scripting and testing.

## Requirements

- A Linux host with Bluetooth (or a USB cable) — this is where the bridge runs.
  It does **not** have to be the Home Assistant machine.
- Python 3.9+.
- An MQTT broker reachable by both the bridge and Home Assistant. If you run
  Home Assistant, the **Mosquitto broker add-on** is the easy choice.
- Home Assistant with the **MQTT integration** enabled.

## Setup

### 1. Pair the light over Bluetooth (one time)

Put the Litra in pairing mode (press the Bluetooth button on the back), then:

```bash
bluetoothctl --timeout 20 scan le        # note the "Litra Beam LX" address
bluetoothctl                              # then, inside the prompt:
  agent NoInputNoOutput
  default-agent
  pair  <ADDRESS>
  trust <ADDRESS>                         # so it auto-reconnects after reboot
  quit
```

`trust` is important — it lets BlueZ silently reconnect the light whenever it's
powered on and in range.

### 2. Install the bridge

```bash
git clone https://github.com/alexvales/litra-ble-mqtt
cd litra-ble-mqtt
sudo ./scripts/install.sh
```

This installs a udev rule (so the bridge doesn't need root), creates a Python
venv, drops a config file at `/etc/litra-mqtt.env`, and installs a systemd
service.

### 3. Configure and start

```bash
sudoedit /etc/litra-mqtt.env          # set LITRA_MQTT_HOST + credentials
sudo systemctl start litra-mqtt
journalctl -u litra-mqtt -f           # watch it connect
```

Within a few seconds Home Assistant shows a **Litra Beam LX** device with two
lights. Done — now use them in automations, dashboards, or voice commands.

## Command-line usage

After install you have a `litra` command (or run `python3 litra.py …` from the
repo):

```bash
litra on
litra brightness 60            # percent
litra temperature 4500         # Kelvin
litra status                   # read back current state
litra back-on
litra back-brightness 80
litra back-color 255 30 0      # warm orange across all zones
litra back-color 0 0 255 3     # just zone 3
```

## How the Home Assistant side works

The bridge publishes [MQTT discovery](docs/MQTT.md) messages, so you don't
configure anything by hand in Home Assistant. See **[docs/MQTT.md](docs/MQTT.md)**
for a full explanation of MQTT, what you need on your Home Assistant box, and how
discovery, state, and availability topics fit together.

## Project layout

```
litra_ble/device.py   HID protocol + device control (no dependencies)
litra_ble/cli.py      command-line interface
litra_ble/bridge.py   MQTT bridge with Home Assistant discovery
udev/                 udev rule for non-root hidraw access
systemd/              reference systemd unit
scripts/install.sh    one-shot installer
docs/                 MQTT guide + reverse-engineered protocol notes
```

## Credits

The Litra HID protocol was reverse-engineered by
[Tim Rogers](https://github.com/timrogers) and contributors in the
[`litra`](https://github.com/timrogers/litra) / [`litra-rs`](https://github.com/timrogers/litra-rs)
projects (MIT). This project re-applies that protocol over Bluetooth and wraps it
for Home Assistant. See [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

## License

MIT — see [LICENSE](LICENSE).
