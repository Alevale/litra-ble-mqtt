# Litra Beam LX Bridge

Control one or more Logitech **Litra** lights from Home Assistant over
**Bluetooth** — including the **Litra Beam LX** front light *and* its 7-zone back
RGB strip. Each light shows up as a normal Home Assistant device with light
entities you can use in dashboards, automations, and voice assistants.

This add-on runs on the Home Assistant host's Bluetooth radio, so the host needs
to be within Bluetooth range of the lights.

## Requirements

- The **Mosquitto broker** add-on (or another MQTT broker) installed and started.
  The MQTT integration must be configured (it usually auto-discovers Mosquitto).
  This add-on gets the broker credentials from the Supervisor automatically — you
  do **not** type them anywhere.
- Bluetooth available on the host. The Pi's built-in Bluetooth works; for several
  lights or long range, a dedicated USB Bluetooth dongle is more reliable.
- **Protection mode OFF** for this add-on (Info tab → toggle off). It needs raw
  HID device access, which protected add-ons aren't allowed.

## Setup

### 1. Find each light's Bluetooth address

Put the light into pairing mode (hold the Bluetooth button on the back until it
blinks). Then, from a machine with Bluetooth, scan:

```bash
bluetoothctl --timeout 20 scan le | grep -i litra
# [NEW] Device FE:FA:09:E1:ED:F4 Litra Beam LX
```

The `FE:FA:09:E1:ED:F4` part is the address you'll put in the config. Repeat for
each light (each has its own address).

> Tip: keep the light in pairing mode the first time you start the add-on, so it
> can complete the initial bond. After that it reconnects on its own.

### 2. Configure the add-on

Example for **two** Beam LX lights:

```yaml
lights:
  - address: "FE:FA:09:E1:ED:F4"
    name: "Office Litra"
    model: "beam_lx"
  - address: "C1:AB:00:11:22:33"
    name: "Desk Litra"
    model: "beam_lx"
pair_on_start: true
state_readback: false
scan_seconds: 8
presence_interval: 5
log_level: info
```

Option reference:

| Option | Meaning |
|---|---|
| `lights[].address` | The light's Bluetooth MAC address (required). |
| `lights[].name` | Friendly name shown in Home Assistant. |
| `lights[].model` | `beam_lx` (front + back RGB), `beam` or `glow` (front only). |
| `pair_on_start` | Pair/trust each address on startup. Leave `true`. |
| `state_readback` | Poll the device for real state (also catches the physical buttons) at the cost of extra Bluetooth traffic. |
| `scan_seconds` | How long to scan when pairing a not-yet-bonded light. |
| `presence_interval` | Seconds between availability checks. |
| `log_level` | `debug`, `info`, `warning`, `error`. |

### 3. Start it

Start the add-on and watch the log. You're looking for:

```
pairing 2 configured light(s)...
connected to MQTT broker
published discovery for Office Litra (front+back)
published discovery for Desk Litra (front+back)
```

Each light then appears in **Settings → Devices & Services → MQTT** as a device
with **Front** and **Back RGB** light entities.

## Using the lights

The entities are standard Home Assistant lights:

- **Front** — on/off, brightness, colour temperature (2700–6500 K).
- **Back RGB** — on/off, brightness, RGB colour (all 7 zones, changed together).

Use them in automations ("front light to 4500 K at 30% on weekday mornings"),
dashboards, or voice ("turn on the Office Litra").

## Troubleshooting

- **A light won't pair:** make sure it's in pairing mode (blinking) and in range
  the first time you start the add-on. Increase `scan_seconds`. Check the log
  with `log_level: debug` for the `bluetoothctl` transcript.
- **Entity shows "unavailable":** the light is asleep/out of range, or the bond
  dropped. It returns automatically when the light reconnects.
- **No entities at all:** confirm the Mosquitto add-on is running and the MQTT
  integration is set up; the log should say *connected to MQTT broker*.
- **Bluetooth conflicts:** if you also use Home Assistant's Bluetooth integration
  on the same adapter and see instability, add a dedicated USB Bluetooth dongle
  for the lights.

## How it works

The Litra speaks a Logitech vendor HID protocol over Bluetooth (HID-over-GATT).
Once bonded, the kernel exposes each light as a `/dev/hidraw` device; the add-on
writes 20-byte control reports to it and publishes/subscribes MQTT topics that
Home Assistant understands via MQTT discovery. Protocol credit:
[timrogers/litra](https://github.com/timrogers/litra) (USB); this project
re-applies it over Bluetooth.
