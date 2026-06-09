# How the Home Assistant / MQTT side works

This is the conceptual guide for the bridge ↔ Home Assistant link. If you just
want it running, follow the README; this explains *why* it's built this way and
*what you need on your Home Assistant machine (e.g. a Raspberry Pi 4)*.

## Why MQTT (and not a custom HA integration)?

The Litra's Bluetooth radio is on the **bridge machine** (the box you paired the
light to). Home Assistant often runs on a **different** machine — a Raspberry Pi,
a NUC, a VM. A custom Home Assistant integration would run *inside* Home
Assistant, on the Pi, where there is **no radio link to the light** — so it
physically can't talk to it.

MQTT solves exactly this: it's a tiny publish/subscribe message bus. The bridge
(next to the light) and Home Assistant (anywhere on the LAN) both connect to a
shared **broker** and exchange small messages. Nothing needs to run inside Home
Assistant except the standard MQTT integration it already ships with.

MQTT discovery is also one of Home Assistant's most stable contracts — it rarely
changes between releases — so this bridge keeps working across HA upgrades
without maintenance, unlike a custom integration that breaks on API churn.

## What you need on the Home Assistant machine (the Pi 4)

Two things, both first-party and five-minute installs:

1. **An MQTT broker.** The official **Mosquitto broker add-on**:
   *Settings → Add-ons → Add-on Store → Mosquitto broker → Install → Start.*
   (If you don't run Home Assistant OS / Supervised, install `mosquitto` as a
   normal package instead — `sudo apt install mosquitto`.)

2. **The MQTT integration.** *Settings → Devices & Services → Add Integration →
   MQTT.* Point it at the broker (with the add-on, it auto-detects `core-mosquitto`).

Create a broker login (Mosquitto add-on: add a user under its configuration, or
a Home Assistant user it can authenticate against), and use those same
credentials in the bridge's `/etc/litra-mqtt.env`.

That's the entire Home Assistant-side requirement. You do **not** install this
project on the Pi — it runs on the machine with the light.

## The message flow

```
                         broker (Mosquitto on the Pi)
                                   ▲   ▲
        publishes discovery +      │   │   subscribes to discovery,
        state, subscribes to    ───┘   └───  publishes commands
        command topic                              │
            │                                       │
   ┌────────┴─────────┐                   ┌─────────┴────────┐
   │  litra-mqtt      │                   │  Home Assistant  │
   │  bridge (Litra)  │                   │  (MQTT integ.)   │
   └──────────────────┘                   └──────────────────┘
```

### 1. Discovery (bridge → HA, once at startup)

The bridge publishes a retained config message describing each light, e.g.:

```
topic:   homeassistant/light/litra_e1edf4/front/config
payload: {
  "name": "Front",
  "unique_id": "litra_e1edf4_front",
  "schema": "json",
  "command_topic": "litra/litra_e1edf4/front/set",
  "state_topic":   "litra/litra_e1edf4/front/state",
  "availability_topic": "litra/litra_e1edf4/availability",
  "brightness": true,
  "supported_color_modes": ["color_temp"],
  "min_mireds": 154, "max_mireds": 370,
  "device": { "identifiers": ["litra_e1edf4"], "name": "Litra Beam LX",
              "manufacturer": "Logitech", "model": "Litra Beam LX" }
}
```

Home Assistant sees this and **creates the entity automatically** — no YAML.
Because it's *retained*, HA picks it up even if it (re)starts later. A second
message does the same for the back RGB light.

### 2. Commands (HA → bridge)

When you toggle the light in HA, it publishes JSON to the `command_topic`:

```
topic:   litra/litra_e1edf4/front/set
payload: {"state":"ON","brightness":200,"color_temp":250}
```

The bridge receives it, converts the units (HA brightness 0–255 → device
lumens 30–400; mireds → Kelvin) and writes the corresponding HID report to the
light.

### 3. State (bridge → HA)

After acting, the bridge publishes the new state (retained) to `state_topic`, so
the HA UI reflects reality and survives restarts:

```
topic:   litra/litra_e1edf4/front/state
payload: {"state":"ON","brightness":200,"color_temp":250,"color_mode":"color_temp"}
```

By default the bridge is **optimistic** — it echoes back what it just set. Set
`LITRA_STATE_READBACK=1` to instead query the device for its true state on a
timer (this also catches changes made with the **physical buttons** on the
light), at the cost of a little extra Bluetooth traffic.

### 4. Availability (bridge → HA)

The bridge publishes `online` / `offline` (retained) to the `availability_topic`
as the light connects and disconnects, and registers an MQTT *last will* so the
broker marks it `offline` if the bridge itself dies. Home Assistant greys the
entity out when it's unavailable instead of pretending it's controllable.

## Using it once it's in Home Assistant

The two lights are normal HA light entities, so everything "just works":

- **Dashboards / the app** — tap to toggle, sliders for brightness and colour.
- **Automations** — e.g. turn the front light to 4500 K at 30 % every weekday at
  08:00; set the back strip red when the doorbell rings.
- **Voice** — with HA's Assist (or Google/Alexa via your existing HA exposure),
  "turn on the Litra front", "set the Litra back light to blue", etc. Expose the
  entities to your voice assistant the same way you expose any other HA light.

## Troubleshooting

- **Entities don't appear:** check `journalctl -u litra-mqtt -f` shows
  *"connected to MQTT broker"* and *"published discovery"*. Confirm
  `LITRA_DISCOVERY_PREFIX` matches HA's MQTT discovery prefix (default
  `homeassistant`). Use a tool like MQTT Explorer to confirm the messages land.
- **Entity shows unavailable:** the light is asleep or out of range, or the
  Bluetooth bond dropped. `litra status` on the bridge host confirms presence;
  `bluetoothctl info <ADDRESS>` shows the BLE connection.
- **Commands ignored:** verify the bridge user can write the hidraw node
  (`litra on` should work without sudo once the udev rule is installed).
