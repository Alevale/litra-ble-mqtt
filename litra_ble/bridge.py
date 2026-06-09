"""MQTT <-> Litra bridge with Home Assistant discovery, multi-light capable.

Runs on the machine the lights are paired to (the one with the Bluetooth radio
- which may be a Home Assistant add-on container with host Bluetooth access).
For each configured light it publishes Home Assistant light entities via MQTT
discovery and turns incoming MQTT commands into HID writes:

  * "<name> Front" - dimmable white light with adjustable colour temperature
  * "<name> Back"  - RGB light (the 7-zone strip), on Beam LX models only

State is optimistic by default (the bridge echoes what it was told). Enable
read-back to poll the device for its true state (also catches the physical
buttons) at the cost of extra Bluetooth traffic.

See litra_ble/config.py for configuration (add-on options or LITRA_* env vars).
"""
from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time

import paho.mqtt.client as mqtt

from litra_ble import pairing
from litra_ble.config import Config, LightCfg, load as load_config
from litra_ble.device import (
    LUMEN_MAX,
    LUMEN_MIN,
    TEMP_MAX,
    TEMP_MIN,
    LitraError,
    LitraLight,
    list_litras,
)

log = logging.getLogger("litra-mqtt")

HA_BRIGHTNESS_MAX = 255
MIRED_MIN = round(1_000_000 / TEMP_MAX)  # coolest (6500K -> ~154)
MIRED_MAX = round(1_000_000 / TEMP_MIN)  # warmest (2700K -> ~370)


def _mireds_to_kelvin(mireds: int) -> int:
    return max(TEMP_MIN, min(TEMP_MAX, round(1_000_000 / mireds)))


def _kelvin_to_mireds(kelvin: int) -> int:
    return max(MIRED_MIN, min(MIRED_MAX, round(1_000_000 / kelvin)))


def _lumen_to_ha(lumen: int) -> int:
    frac = (lumen - LUMEN_MIN) / (LUMEN_MAX - LUMEN_MIN)
    return max(1, min(HA_BRIGHTNESS_MAX, round(frac * HA_BRIGHTNESS_MAX)))


def _ha_to_pct(brightness: int) -> float:
    return max(0.0, min(100.0, brightness / HA_BRIGHTNESS_MAX * 100))


def _make_client(client_id: str) -> mqtt.Client:
    """Construct a paho Client that works on both v1.x and v2.x."""
    try:  # paho-mqtt >= 2.0
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)  # type: ignore[attr-defined]
    except AttributeError:  # paho-mqtt 1.x
        return mqtt.Client(client_id=client_id)


class LightBridge:
    """MQTT discovery, command handling and state for a single light."""

    def __init__(self, cfg: LightCfg, discovery_prefix: str, bridge_status: str):
        self.dev = LitraLight(address=cfg.address, model=cfg.model, name=cfg.name)
        self.discovery_prefix = discovery_prefix
        self.bridge_status = bridge_status
        self.node_id = f"litra_{self.dev.unique_suffix()}"
        base = f"litra/{self.node_id}"
        self.t_avail = f"{base}/availability"
        self.front = {"set": f"{base}/front/set", "state": f"{base}/front/state"}
        self.back = {"set": f"{base}/back/set", "state": f"{base}/back/state"}

        self.front_state = {"state": "OFF", "brightness": 255,
                            "color_temp": MIRED_MIN, "color_mode": "color_temp"}
        self.back_state = {"state": "OFF", "brightness": 255,
                           "color": {"r": 255, "g": 255, "b": 255},
                           "color_mode": "rgb"}
        self._last_present: bool | None = None

    # -- topics this light owns ----------------------------------------------

    @property
    def command_topics(self) -> list[str]:
        topics = [self.front["set"]]
        if self.dev.has_back:
            topics.append(self.back["set"])
        return topics

    def owns(self, topic: str) -> bool:
        return topic in (self.front["set"], self.back["set"])

    # -- discovery -----------------------------------------------------------

    def _device_block(self) -> dict:
        return {
            "identifiers": [self.node_id],
            "name": self.dev.name,
            "manufacturer": "Logitech",
            "model": "Litra Beam LX" if self.dev.has_back else "Litra",
        }

    def _availability(self) -> dict:
        # The entity is available only when BOTH the bridge process is up
        # (LWT-backed) and this specific light is connected.
        return {
            "availability": [
                {"topic": self.bridge_status},
                {"topic": self.t_avail},
            ],
            "availability_mode": "all",
        }

    def publish_discovery(self, client: mqtt.Client) -> None:
        front_cfg = {
            "name": "Front",
            "unique_id": f"{self.node_id}_front",
            "schema": "json",
            "command_topic": self.front["set"],
            "state_topic": self.front["state"],
            "brightness": True,
            "supported_color_modes": ["color_temp"],
            "min_mireds": MIRED_MIN,
            "max_mireds": MIRED_MAX,
            "device": self._device_block(),
            **self._availability(),
        }
        client.publish(
            f"{self.discovery_prefix}/light/{self.node_id}/front/config",
            json.dumps(front_cfg), retain=True,
        )
        if self.dev.has_back:
            back_cfg = {
                "name": "Back RGB",
                "unique_id": f"{self.node_id}_back",
                "schema": "json",
                "command_topic": self.back["set"],
                "state_topic": self.back["state"],
                "brightness": True,
                "supported_color_modes": ["rgb"],
                "device": self._device_block(),
                **self._availability(),
            }
            client.publish(
                f"{self.discovery_prefix}/light/{self.node_id}/back/config",
                json.dumps(back_cfg), retain=True,
            )
        log.info("published discovery for %s (%s)", self.dev.name,
                 "front+back" if self.dev.has_back else "front")

    # -- command handling ----------------------------------------------------

    def handle(self, topic: str, payload: dict, client: mqtt.Client) -> None:
        if topic == self.front["set"]:
            self._handle_front(payload)
        elif topic == self.back["set"] and self.dev.has_back:
            self._handle_back(payload)
        else:
            return
        self.publish_state(client)

    def _handle_front(self, payload: dict) -> None:
        if payload.get("state") == "OFF":
            self.dev.front_power(False)
            self.front_state["state"] = "OFF"
            return
        self.dev.front_power(True)
        self.front_state["state"] = "ON"
        if "color_temp" in payload:
            mireds = int(payload["color_temp"])
            self.dev.front_temperature(_mireds_to_kelvin(mireds))
            self.front_state["color_temp"] = mireds
        if "brightness" in payload:
            b = int(payload["brightness"])
            self.dev.front_brightness_pct(_ha_to_pct(b))
            self.front_state["brightness"] = b

    def _handle_back(self, payload: dict) -> None:
        if payload.get("state") == "OFF":
            self.dev.back_power(False)
            self.back_state["state"] = "OFF"
            return
        self.dev.back_power(True)
        self.back_state["state"] = "ON"
        if "color" in payload:
            c = payload["color"]
            r, g, b = int(c.get("r", 255)), int(c.get("g", 255)), int(c.get("b", 255))
            self.dev.back_color(r, g, b)
            self.back_state["color"] = {"r": r, "g": g, "b": b}
        if "brightness" in payload:
            br = int(payload["brightness"])
            self.dev.back_brightness_pct(_ha_to_pct(br))
            self.back_state["brightness"] = br

    # -- state / availability -------------------------------------------------

    def publish_state(self, client: mqtt.Client) -> None:
        client.publish(self.front["state"], json.dumps(self.front_state), retain=True)
        if self.dev.has_back:
            client.publish(self.back["state"], json.dumps(self.back_state), retain=True)

    def publish_availability(self, client: mqtt.Client, force: bool = False) -> bool:
        present = self.dev.present
        if force or present != self._last_present:
            self._last_present = present
            client.publish(self.t_avail, "online" if present else "offline",
                           retain=True)
            log.info("%s availability: %s", self.dev.name,
                     "online" if present else "offline")
        return present

    def refresh_from_device(self, client: mqtt.Client) -> None:
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
            if self.dev.has_back:
                bp = self.dev.get_back_power()
                if bp is not None:
                    self.back_state["state"] = "ON" if bp else "OFF"
        except LitraError as e:
            log.debug("%s readback skipped: %s", self.dev.name, e)
            return
        self.publish_state(client)


class Bridge:
    """Owns the MQTT connection and dispatches to per-light bridges."""

    BRIDGE_STATUS = "litra/bridge/status"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        # Populated in run() after discovery/pairing (auto-discovery may add
        # lights that weren't in the static config).
        self.lights: list[LightBridge] = []
        self.client = _make_client("litra-mqtt")
        # LWT: if the bridge dies, every light's entity goes unavailable
        # (each entity requires this topic == "online", see _availability()).
        self.client.will_set(self.BRIDGE_STATUS, "offline", retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        if cfg.mqtt_username:
            self.client.username_pw_set(cfg.mqtt_username, cfg.mqtt_password)
        if cfg.mqtt_tls:
            self.client.tls_set()
        self._stop = threading.Event()

    def _on_connect(self, client, userdata, flags, rc, *args):
        if rc != 0:
            log.error("MQTT connect failed (rc=%s)", rc)
            return
        log.info("connected to MQTT broker")
        client.publish(self.BRIDGE_STATUS, "online", retain=True)
        subs = []
        for lb in self.lights:
            lb.publish_discovery(client)
            subs.extend((t, 0) for t in lb.command_topics)
            lb.publish_availability(client, force=True)
            lb.publish_state(client)
        if subs:
            client.subscribe(subs)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            log.warning("ignoring non-JSON message on %s", msg.topic)
            return
        for lb in self.lights:
            if lb.owns(msg.topic):
                try:
                    lb.handle(msg.topic, payload, client)
                except LitraError as e:
                    log.warning("%s command failed: %s", lb.dev.name, e)
                    lb.publish_availability(client, force=True)
                except Exception as e:  # malformed payload etc. - never die
                    log.warning("%s: bad command %r: %s", lb.dev.name, payload, e)
                return

    def _presence_loop(self):
        while not self._stop.wait(self.cfg.presence_interval):
            for lb in self.lights:
                lb.publish_availability(self.client)
                if self.cfg.state_readback:
                    lb.refresh_from_device(self.client)

    @staticmethod
    def _norm(addr: str | None) -> str:
        return addr.replace(":", "").lower() if addr else ""

    @staticmethod
    def _colonize(norm: str) -> str:
        return ":".join(norm[i:i + 2] for i in range(0, len(norm), 2)).upper()

    def _resolve_light_cfgs(self) -> list[LightCfg]:
        """Determine which lights to bridge: configured + auto-discovered.

        Auto-discovery scans BLE for advertising "Litra" devices, pairs anything
        new, then enumerates every bonded Litra (model detected from its product
        id). Explicit configured lights keep their name/model and are always
        included (even if currently absent, so they show up as unavailable).
        """
        configured = {self._norm(lc.address): lc
                      for lc in self.cfg.lights if lc.address}

        discovered: dict[str, tuple[str, str]] = {}
        if self.cfg.auto_discover:
            log.info("scanning for Litra lights...")
            for addr, name in pairing.scan_for_litras(self.cfg.scan_seconds):
                discovered[self._norm(addr)] = (addr, name)
            if discovered:
                log.info("discovered: %s",
                         ", ".join(n for _, n in discovered.values()))

        to_pair = [lc.address for lc in configured.values()]
        to_pair += [addr for na, (addr, _) in discovered.items()
                    if na not in configured]
        if (self.cfg.pair_on_start or self.cfg.auto_discover) and to_pair:
            log.info("pairing %d light(s)...", len(to_pair))
            pairing.ensure_paired(to_pair, scan_seconds=self.cfg.scan_seconds)
            # The kernel hidraw node can lag a few seconds behind the bond/
            # connect, so wait for it to show up before giving up.
            for _ in range(10):
                if list_litras():
                    break
                time.sleep(1.5)

        result: list[LightCfg] = []
        seen: set[str] = set()
        for info in list_litras():            # every bonded Litra now visible
            na = info["address"]
            if not na:
                continue
            seen.add(na)
            if na in configured:
                result.append(configured[na])
            else:
                name = discovered.get(na, (None, info["name"]))[1] or info["name"]
                result.append(LightCfg(
                    address=self._colonize(na),
                    name=f"{name} ({na[-4:].upper()})",  # disambiguate same-model
                    model=info["model"],
                ))
        # configured lights not currently present -> still expose (unavailable)
        for na, lc in configured.items():
            if na not in seen:
                result.append(lc)

        if not seen and to_pair:
            # Bonded something but no HID node appeared - show why.
            log.warning("paired but no HID device showed up. Diagnostics:\n%s",
                        pairing.diagnostics(to_pair))
        return result

    def run(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: self._stop.set())

        self.lights = [
            LightBridge(lc, self.cfg.discovery_prefix, self.BRIDGE_STATUS)
            for lc in self._resolve_light_cfgs()
        ]
        if not self.lights:
            log.error("no lights found. Put a Litra in pairing mode and restart, "
                      "or set addresses in the configuration.")
            return
        log.info("bridging %d light(s): %s", len(self.lights),
                 ", ".join(lb.dev.name for lb in self.lights))

        # Retry the initial connect: on a host/HAOS reboot the broker may not be
        # accepting connections yet. paho only auto-reconnects AFTER the first
        # successful connect, so we loop here until that first connect lands.
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        log.info("connecting to MQTT %s:%s", self.cfg.mqtt_host, self.cfg.mqtt_port)
        while not self._stop.is_set():
            try:
                self.client.connect(self.cfg.mqtt_host, self.cfg.mqtt_port, keepalive=30)
                break
            except (OSError, ConnectionError) as e:
                log.warning("broker not reachable (%s); retrying in 5s", e)
                if self._stop.wait(5):
                    return

        watcher = threading.Thread(target=self._presence_loop, daemon=True)
        watcher.start()

        self.client.loop_start()
        self._stop.wait()
        log.info("shutting down")
        self.client.publish(self.BRIDGE_STATUS, "offline", retain=True)
        for lb in self.lights:
            self.client.publish(lb.t_avail, "offline", retain=True)
        time.sleep(0.2)
        self.client.loop_stop()
        self.client.disconnect()


def main() -> int:
    cfg = load_config()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not cfg.lights and not cfg.auto_discover:
        log.error("no lights configured and auto-discovery is off; "
                  "set options.lights or enable auto_discover")
        return 1
    Bridge(cfg).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
