"""Control a Logitech Litra Beam LX over USB or Bluetooth HID, and bridge it
to Home Assistant via MQTT."""

from litra_ble.device import (
    LitraBeamLX,
    LitraError,
    LitraNotFound,
)

__version__ = "0.1.0"
__all__ = ["LitraBeamLX", "LitraError", "LitraNotFound", "__version__"]
