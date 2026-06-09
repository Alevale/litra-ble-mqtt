"""Configuration loading for the Litra MQTT bridge.

Two run contexts are supported, auto-detected:

* **Home Assistant add-on** - options come from ``/data/options.json`` (written
  by the Supervisor from the add-on's config schema), and MQTT broker details
  come from the Supervisor's MQTT *service* API, so the user never types broker
  credentials.

* **Standalone** (e.g. a systemd service) - everything comes from ``LITRA_*``
  environment variables. If no lights are configured explicitly, every Litra
  currently present on the host is exposed automatically.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from dataclasses import dataclass, field

from litra_ble.device import DEFAULT_MODEL, PROFILES, list_litras

log = logging.getLogger("litra-mqtt.config")

OPTIONS_PATH = "/data/options.json"
SUPERVISOR_MQTT_URL = "http://supervisor/services/mqtt"


@dataclass
class LightCfg:
    address: str | None
    name: str
    model: str = DEFAULT_MODEL

    def normalized(self) -> "LightCfg":
        model = self.model if self.model in PROFILES else DEFAULT_MODEL
        return LightCfg(address=self.address, name=self.name, model=model)


@dataclass
class Config:
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_tls: bool = False
    discovery_prefix: str = "homeassistant"
    lights: list[LightCfg] = field(default_factory=list)
    state_readback: bool = False
    presence_interval: float = 5.0
    pair_on_start: bool = True
    auto_discover: bool = True
    scan_seconds: float = 8.0
    log_level: str = "INFO"


def _supervisor_mqtt() -> dict | None:
    token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN")
    if not token:
        return None
    req = urllib.request.Request(
        SUPERVISOR_MQTT_URL, headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as e:  # broker may simply not be configured yet
        log.warning("could not fetch MQTT service from Supervisor: %s", e)
        return None
    return payload.get("data", payload)


def _from_addon(options: dict) -> Config:
    cfg = Config()
    cfg.discovery_prefix = options.get("discovery_prefix") or "homeassistant"
    cfg.state_readback = bool(options.get("state_readback", False))
    cfg.presence_interval = float(options.get("presence_interval", 5))
    cfg.scan_seconds = float(options.get("scan_seconds", 8))
    cfg.pair_on_start = bool(options.get("pair_on_start", True))
    cfg.auto_discover = bool(options.get("auto_discover", True))
    cfg.log_level = (options.get("log_level") or "info").upper()

    cfg.lights = []
    for light in options.get("lights", []):
        addr = (light.get("address") or "").strip()
        if not addr:
            # In add-on mode an address is required to target a specific light;
            # an empty one would silently grab "the first Litra" and collide
            # with any other empty entry. Skip it loudly instead.
            log.warning("ignoring a light with no 'address' set - fill in its "
                        "Bluetooth MAC in the add-on configuration")
            continue
        cfg.lights.append(LightCfg(
            address=addr,
            name=light.get("name") or f"Litra {addr}",
            model=light.get("model") or DEFAULT_MODEL,
        ).normalized())

    mqtt = _supervisor_mqtt()
    if mqtt:
        cfg.mqtt_host = mqtt.get("host", "core-mosquitto")
        cfg.mqtt_port = int(mqtt.get("port", 1883))
        cfg.mqtt_username = mqtt.get("username") or None
        cfg.mqtt_password = mqtt.get("password") or None
        cfg.mqtt_tls = bool(mqtt.get("ssl", False))
        log.info("MQTT from Supervisor: %s:%s (user=%s, tls=%s)",
                 cfg.mqtt_host, cfg.mqtt_port, cfg.mqtt_username, cfg.mqtt_tls)
    else:
        # Fall back to any explicit broker override in the options.
        cfg.mqtt_host = options.get("mqtt_host", "core-mosquitto")
        cfg.mqtt_port = int(options.get("mqtt_port", 1883))
        cfg.mqtt_username = options.get("mqtt_username") or None
        cfg.mqtt_password = options.get("mqtt_password") or None
    return cfg


def _autodiscover_lights() -> list[LightCfg]:
    lights = []
    for info in list_litras():
        addr = info["address"]
        pretty = ":".join(addr[i:i + 2] for i in range(0, len(addr), 2)).upper()
        lights.append(LightCfg(
            address=pretty or None,
            name=info["name"] or "Litra",
            model=info.get("model", DEFAULT_MODEL),  # from product id, not assumed
        ))
    return lights


def _from_env() -> Config:
    def env(name, default=None):
        v = os.environ.get(name)
        return v if v not in (None, "") else default

    cfg = Config(
        mqtt_host=env("LITRA_MQTT_HOST", "localhost"),
        mqtt_port=int(env("LITRA_MQTT_PORT", "1883")),
        mqtt_username=env("LITRA_MQTT_USERNAME"),
        mqtt_password=env("LITRA_MQTT_PASSWORD"),
        mqtt_tls=env("LITRA_MQTT_TLS", "0") == "1",
        discovery_prefix=env("LITRA_DISCOVERY_PREFIX", "homeassistant"),
        state_readback=env("LITRA_STATE_READBACK", "0") == "1",
        presence_interval=float(env("LITRA_PRESENCE_INTERVAL", "5")),
        pair_on_start=env("LITRA_PAIR_ON_START", "0") == "1",
        auto_discover=env("LITRA_AUTO_DISCOVER", "0") == "1",
        scan_seconds=float(env("LITRA_SCAN_SECONDS", "8")),
        log_level=env("LITRA_LOG_LEVEL", "INFO").upper(),
    )

    raw = env("LITRA_LIGHTS")
    if raw:
        cfg.lights = [
            LightCfg(address=l.get("address"), name=l.get("name") or "Litra",
                     model=l.get("model") or DEFAULT_MODEL).normalized()
            for l in json.loads(raw)
        ]
    elif env("LITRA_LIGHT_ADDRESS"):
        cfg.lights = [LightCfg(
            address=env("LITRA_LIGHT_ADDRESS"),
            name=env("LITRA_DEVICE_NAME", "Litra Beam LX"),
            model=env("LITRA_LIGHT_MODEL", DEFAULT_MODEL),
        ).normalized()]
    else:
        cfg.lights = _autodiscover_lights()
    return cfg


def load() -> Config:
    """Load configuration, auto-detecting add-on vs standalone."""
    if os.path.exists(OPTIONS_PATH):
        with open(OPTIONS_PATH) as f:
            options = json.load(f)
        log.info("loaded add-on options from %s", OPTIONS_PATH)
        cfg = _from_addon(options)
    else:
        cfg = _from_env()

    if not cfg.lights:
        log.warning("no lights configured and none auto-discovered")
    else:
        log.info("configured %d light(s): %s", len(cfg.lights),
                 ", ".join(f"{l.name}[{l.model}]" for l in cfg.lights))
    return cfg
