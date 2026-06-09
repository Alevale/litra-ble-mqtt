"""MQTT <-> Litra Beam LX bridge with Home Assistant discovery.

Runs on the machine the Litra is paired to (the one with the Bluetooth radio).
It publishes two Home Assistant light entities via MQTT discovery and turns
incoming MQTT commands into HID writes:

  * "<name> Front"  - dimmable white light with adjustable colour temperature
  * "<name> Back"   - RGB light (the 7-zone strip, driven as one colour)

Home Assistant only needs an MQTT broker (e.g. the Mosquitto add-on) and the
MQTT integration; the entities appear automatically. The light's Bluetooth
connection itself is owned by the host's BlueZ, not by this bridge.

State handling is optimistic by default: the bridge echoes back whatever it was
last told to do. Set LITRA_STATE_READBACK=1 to instead query the device for its
real state on a timer (useful to track the physical buttons), at the cost of
extra BLE traffic.

Configuration (environment variables):

  LITRA_MQTT_HOST           broker host (default: localhost)
  LITRA_MQTT_PORT           broker port (default: 1883)
  LITRA_MQTT_USERNAME       broker username (optional)
  LITRA_MQTT_PASSWORD       broker password (optional)
  LITRA_DISCOVERY_PREFIX    HA discovery prefix (default: homeassistant)
  LITRA_NODE_ID             unique id for this light (default: derived from MAC)
  LITRA_DEVICE_NAME         friendly device name (default: "Litra Beam LX")
  LITRA_PRESENCE_INTERVAL   seconds between availability checks (default: 5)
  LITRA_STATE_READBACK      "1" to poll real device state (default: 0)
"""
from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time

import paho.mqtt.client as mqtt

from litra_ble.device import (
    LUMEN_MAX,
    LUMEN_MIN,
    TEMP_MAX,
    TEMP_MIN,
    LitraBeamLX,
    LitraError,
)

log = logging.getLogger("litra-mqtt")

# Home Assistant brightness is 0..255; colour temperature is in mireds.
HA_BRIGHTNESS_MAX = 255
MIRED_MIN = round(1_000_000 / TEMP_MAX)  # coolest (6500K -> ~153)
MIRED_MAX = round(1_000_000 / TEMP_MIN)  # warmest (2700K -> ~370)


def _env(name, default=None):
    val = os.environ.get(name)
    return val if val not in (None, "") else default


def _mireds_to_kelvin(mireds: int) -> int:
    return max(TEMP_MIN, min(TEMP_MAX, round(1_000_000 / mireds)))


def _kelvin_to_mireds(kelvin: int) -> int:
    return max(MIRED_MIN, min(MIRED_MAX, round(1_000_000 / kelvin)))


def _lumen_to_ha(lumen: int) -> int:
    frac = (lumen - LUMEN_MIN) / (LUMEN_MAX - LUMEN_MIN)
    return max(1, min(HA_BRIGHTNESS_MAX, round(frac * HA_BRIGHTNESS_MAX)))


def _ha_to_pct(brightness: int) -> float:
    return max(0.0, min(100.0, brightness / HA_BRIGHTNESS_MAX * 100))


def _make_client() -> mqtt.Client:
    """Construct a paho Client that works on both v1.x and v2.x."""
    try:  # paho-mqtt >= 2.0
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)  # type: ignore[attr-defined]
    except AttributeError:  # paho-mqtt 1.x
        return mqtt.Client()


class Bridge:
    def __init__(self):
        self.dev = LitraBeamLX()
        self.discovery_prefix = _env("LITRA_DISCOVERY_PREFIX", "homeassistant")
        self.node_id = _env("LITRA_NODE_ID") or f"litra_{self.dev.unique_suffix()}"
        self.device_name = _env("LITRA_DEVICE_NAME", "Litra Beam LX")
        self.presence_interval = float(_env("LITRA_PRESENCE_INTERVAL", "5"))
        self.readback = _env("LITRA_STATE_READBACK", "0") == "1"

        base = f"litra/{self.node_id}"
        self.t_avail = f"{base}/availability"
        self.front = {
            "set": f"{base}/front/set",
            "state": f"{base}/front/state",
        }
        self.back = {
            "set": f"{base}/back/set",
            "state": f"{base}/back/state",
        }

        # Optimistic in-memory state, echoed to HA.
        self.front_state = {"state": "OFF", "brightness": 255, "color_temp": MIRED_MIN}
        self.back_state = {
            "state": "OFF", "brightness": 255, "color": {"r": 255, "g": 255, "b": 255}
        }

        self.client = _make_client()
        self.client.will_set(self.t_avail, "offline", retain=True)
        user = _env("LITRA_MQTT_USERNAME")
        if user:
            self.client.username_pw_set(user, _env("LITRA_MQTT_PASSWORD"))
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        self._stop = threading.Event()
        self._last_present: bool | None = None

    # -- HA discovery ---------------------------------------------------------

    def _device_block(self) -> dict:
        return {
            "identifiers": [self.node_id],
            "name": self.device_name,
            "manufacturer": "Logitech",
            "model": "Litra Beam LX",
        }

    def _publish_discovery(self) -> None:
        front_cfg = {
            "name": "Front",
            "unique_id": f"{self.node_id}_front",
            "schema": "json",
            "command_topic": self.front["set"],
            "state_topic": self.front["state"],
            "availability_topic": self.t_avail,
            "brightness": True,
            "supported_color_modes": ["color_temp"],
            "min_mireds": MIRED_MIN,
            "max_mireds": MIRED_MAX,
            "device": self._device_block(),
        }
        back_cfg = {
            "name": "Back RGB",
            "unique_id": f"{self.node_id}_back",
            "schema": "json",
            "command_topic": self.back["set"],
            "state_topic": self.back["state"],
            "availability_topic": self.t_avail,
            "brightness": True,
            "supported_color_modes": ["rgb"],
            "device": self._device_block(),
        }
        for obj, cfg in (("front", front_cfg), ("back", back_cfg)):
            topic = f"{self.discovery_prefix}/light/{self.node_id}/{obj}/config"
            self.client.publish(topic, json.dumps(cfg), retain=True)
        log.info("published discovery for %s (front + back)", self.node_id)

    # -- MQTT callbacks -------------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc, *args):
        if rc != 0:
            log.error("MQTT connect failed (rc=%s)", rc)
            return
        log.info("connected to MQTT broker")
        self._publish_discovery()
        client.subscribe([(self.front["set"], 0), (self.back["set"], 0)])
        self._publish_availability(force=True)
        self._publish_state()

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            log.warning("ignoring non-JSON message on %s", msg.topic)
            return
        try:
            if msg.topic == self.front["set"]:
                self._handle_front(payload)
            elif msg.topic == self.back["set"]:
                self._handle_back(payload)
        except LitraError as e:
            log.warning("command failed: %s", e)
            self._publish_availability(force=True)

    # -- command handlers -----------------------------------------------------

    def _handle_front(self, payload: dict) -> None:
        if payload.get("state") == "OFF":
            self.dev.front_power(False)
            self.front_state["state"] = "OFF"
            self._publish_state()
            return

        self.dev.front_power(True)
        self.front_state["state"] = "ON"
        if "color_temp" in payload:
            mireds = int(payload["color_temp"])
            self.dev.front_temperature(_mireds_to_kelvin(mireds))
            self.front_state["color_temp"] = mireds
        if "brightness" in payload:
            brightness = int(payload["brightness"])
            self.dev.front_brightness_pct(_ha_to_pct(brightness))
            self.front_state["brightness"] = brightness
        self._publish_state()

    def _handle_back(self, payload: dict) -> None:
        if payload.get("state") == "OFF":
            self.dev.back_power(False)
            self.back_state["state"] = "OFF"
            self._publish_state()
            return

        self.dev.back_power(True)
        self.back_state["state"] = "ON"
        if "color" in payload:
            c = payload["color"]
            r, g, b = int(c.get("r", 255)), int(c.get("g", 255)), int(c.get("b", 255))
            self.dev.back_color(r, g, b)
            self.back_state["color"] = {"r": r, "g": g, "b": b}
        if "brightness" in payload:
            brightness = int(payload["brightness"])
            self.dev.back_brightness_pct(_ha_to_pct(brightness))
            self.back_state["brightness"] = brightness
        self._publish_state()

    # -- state / availability publishing --------------------------------------

    def _publish_state(self) -> None:
        self.front_state["color_mode"] = "color_temp"
        self.back_state["color_mode"] = "rgb"
        self.client.publish(self.front["state"], json.dumps(self.front_state), retain=True)
        self.client.publish(self.back["state"], json.dumps(self.back_state), retain=True)

    def _publish_availability(self, force: bool = False) -> None:
        present = self.dev.present
        if force or present != self._last_present:
            self._last_present = present
            self.client.publish(
                self.t_avail, "online" if present else "offline", retain=True
            )
            log.info("availability: %s", "online" if present else "offline")

    def _refresh_from_device(self) -> None:
        """Optional: pull real state from the device (LITRA_STATE_READBACK=1)."""
        if not self.dev.present:
            return
        try:
            fp = self.dev.get_front_power()
            if fp is not None:
                self.front_state["state"] = "ON" if fp else "OFF"
            fl = self.dev.get_front_brightness_lumen()
            if fl:
                self.front_state["brightness"] = _lumen_to_ha(fl)
            ft = self.dev.get_front_temperature()
            if ft:
                self.front_state["color_temp"] = _kelvin_to_mireds(ft)
            bp = self.dev.get_back_power()
            if bp is not None:
                self.back_state["state"] = "ON" if bp else "OFF"
        except LitraError as e:
            log.debug("readback skipped: %s", e)
            return
        self._publish_state()

    # -- main loop ------------------------------------------------------------

    def _presence_loop(self) -> None:
        while not self._stop.wait(self.presence_interval):
            self._publish_availability()
            if self.readback:
                self._refresh_from_device()

    def run(self) -> None:
        host = _env("LITRA_MQTT_HOST", "localhost")
        port = int(_env("LITRA_MQTT_PORT", "1883"))
        log.info("connecting to MQTT %s:%s as node %s", host, port, self.node_id)
        self.client.connect(host, port, keepalive=30)

        watcher = threading.Thread(target=self._presence_loop, daemon=True)
        watcher.start()

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: self._stop.set())

        self.client.loop_start()
        self._stop.wait()
        log.info("shutting down")
        self.client.publish(self.t_avail, "offline", retain=True)
        time.sleep(0.2)
        self.client.loop_stop()
        self.client.disconnect()


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, _env("LITRA_LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    Bridge().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
