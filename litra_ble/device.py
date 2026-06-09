"""Low-level control of a Logitech Litra Beam LX over its HID interface.

The Litra Beam LX, whether connected over USB or Bluetooth (BLE), is exposed by
the Linux kernel as a HID device (`/dev/hidraw*`). Control happens via 20-byte
HID output reports on a Logitech vendor collection (Usage Page 0xFF43, Report
ID 0x11), using a proprietary protocol reverse-engineered by the open-source
`litra` / `litra-rs` projects (https://github.com/timrogers/litra-rs).

Report layout (20 bytes total):

    byte 0 : 0x11            report id
    byte 1 : 0xff            "software" / direct addressing
    byte 2 : <feature index> 0x06 front light, 0x0a back power/brightness,
                             0x0c back colour zones
    byte 3 : <function>      e.g. 0x1c power, 0x4c brightness, 0x9c temperature
    byte 4+: <params>        zero-padded to 20 bytes

Brightness on the front light is a 2-byte big-endian *lumen* value (30..400).
Colour temperature is a 2-byte big-endian Kelvin value (2700..6500).

This module talks straight to /dev/hidraw and has no third-party dependencies.
"""
from __future__ import annotations

import glob
import os
import select
import time

VENDOR_ID = 0x046D
# Beam LX reports 0xC903 over USB and 0xB903 over Bluetooth-HID.
PRODUCT_IDS = (0xB903, 0xC903)

# HID++ feature indices used by the Beam LX.
FEAT_FRONT = 0x06        # front ("key") light: power / brightness / temperature
FEAT_BACK = 0x0A         # back strip: power / brightness
FEAT_BACK_COLOR = 0x0C   # back strip: per-zone colour + commit

# Front light ranges.
LUMEN_MIN, LUMEN_MAX = 30, 400
TEMP_MIN, TEMP_MAX = 2700, 6500

# The back strip is divided into this many addressable colour zones.
BACK_ZONES = 7

REPORT_LEN = 20


class LitraError(RuntimeError):
    pass


class LitraNotFound(LitraError):
    pass


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


class LitraBeamLX:
    """A single Litra Beam LX, addressed via its /dev/hidraw node.

    The hidraw path is resolved lazily on every operation, because over
    Bluetooth the node disappears when the light sleeps and reappears (possibly
    as a different number) when it wakes. Callers therefore never hold a stale
    file descriptor.
    """

    def __init__(self, path: str | None = None):
        self._fixed_path = path

    # -- device discovery -----------------------------------------------------

    @staticmethod
    def _uevent(hidraw_name: str) -> str:
        try:
            with open(f"/sys/class/hidraw/{hidraw_name}/device/uevent") as f:
                return f.read()
        except OSError:
            return ""

    @classmethod
    def find_hidraw(cls) -> str | None:
        """Return the /dev/hidraw* path for the first Litra found, or None."""
        for path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
            name = os.path.basename(path)
            uevent = cls._uevent(name).upper()
            if f"{VENDOR_ID:04X}" not in uevent:
                continue
            if any(f"{pid:04X}" in uevent for pid in PRODUCT_IDS):
                return f"/dev/{name}"
        return None

    def resolve_path(self) -> str:
        path = self._fixed_path or self.find_hidraw()
        if not path or not os.path.exists(path):
            raise LitraNotFound(
                "Litra HID device not found - is it powered on and "
                "Bluetooth-connected?"
            )
        return path

    @property
    def present(self) -> bool:
        try:
            self.resolve_path()
            return True
        except LitraNotFound:
            return False

    def unique_suffix(self) -> str:
        """A stable id derived from the device's Bluetooth/USB address.

        Used to build MQTT unique_ids so multiple lights don't collide.
        Falls back to 'beamlx' if the address can't be read.
        """
        path = self._fixed_path or self.find_hidraw()
        if path:
            uevent = self._uevent(os.path.basename(path))
            for line in uevent.splitlines():
                if line.startswith("HID_UNIQ="):
                    addr = line.split("=", 1)[1].strip()
                    if addr:
                        return addr.replace(":", "").lower()[-6:]
        return "beamlx"

    # -- raw I/O --------------------------------------------------------------

    @staticmethod
    def _report(*payload: int) -> bytes:
        data = bytes(payload)
        if len(data) > REPORT_LEN:
            raise ValueError("report longer than 20 bytes")
        return data + bytes(REPORT_LEN - len(data))

    def _write(self, *payload: int) -> None:
        path = self.resolve_path()
        report = self._report(*payload)
        try:
            fd = os.open(path, os.O_WRONLY)
        except PermissionError as e:
            raise LitraError(
                f"permission denied opening {path}; install the udev rule or "
                f"run as root ({e})"
            ) from e
        try:
            os.write(fd, report)
        except OSError as e:
            raise LitraError(f"write to {path} failed: {e}") from e
        finally:
            os.close(fd)

    def _query(self, *payload: int, timeout: float = 0.5) -> bytes | None:
        """Send a query report and return the device's response report.

        The device answers a request on feature/function (bytes 2 and 3) with
        an input report echoing the same feature index. We read until we see a
        0x11 report whose feature index matches, or until ``timeout`` elapses.
        Returns the 20-byte response, or None if the device didn't answer.
        Requires read access to the hidraw node.
        """
        path = self.resolve_path()
        want_feature = payload[2] if len(payload) > 2 else None
        fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        try:
            os.write(fd, self._report(*payload))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                r, _, _ = select.select([fd], [], [], deadline - time.monotonic())
                if not r:
                    break
                try:
                    resp = os.read(fd, 64)
                except BlockingIOError:
                    continue
                if len(resp) >= 4 and resp[0] == 0x11 and (
                    want_feature is None or resp[2] == want_feature
                ):
                    return bytes(resp[:REPORT_LEN].ljust(REPORT_LEN, b"\x00"))
            return None
        finally:
            os.close(fd)

    # -- front light ----------------------------------------------------------

    def front_power(self, on: bool) -> None:
        self._write(0x11, 0xFF, FEAT_FRONT, 0x1C, 0x01 if on else 0x00)

    def front_brightness_lumen(self, lumen: int) -> None:
        lm = _clamp(int(lumen), LUMEN_MIN, LUMEN_MAX)
        self._write(0x11, 0xFF, FEAT_FRONT, 0x4C, (lm >> 8) & 0xFF, lm & 0xFF)

    def front_brightness_pct(self, pct: float) -> None:
        pct = _clamp(pct, 0, 100)
        self.front_brightness_lumen(round(LUMEN_MIN + (LUMEN_MAX - LUMEN_MIN) * pct / 100))

    def front_temperature(self, kelvin: int) -> None:
        k = _clamp(int(kelvin), TEMP_MIN, TEMP_MAX)
        self._write(0x11, 0xFF, FEAT_FRONT, 0x9C, (k >> 8) & 0xFF, k & 0xFF)

    def get_front_power(self) -> bool | None:
        resp = self._query(0x11, 0xFF, FEAT_FRONT, 0x01)
        return None if resp is None else bool(resp[4])

    def get_front_brightness_lumen(self) -> int | None:
        resp = self._query(0x11, 0xFF, FEAT_FRONT, 0x31)
        return None if resp is None else (resp[4] << 8) | resp[5]

    def get_front_temperature(self) -> int | None:
        resp = self._query(0x11, 0xFF, FEAT_FRONT, 0x81)
        return None if resp is None else (resp[4] << 8) | resp[5]

    # -- back strip -----------------------------------------------------------

    def back_power(self, on: bool) -> None:
        self._write(0x11, 0xFF, FEAT_BACK, 0x4B, 0x01 if on else 0x00)

    def back_brightness_pct(self, pct: float) -> None:
        p = _clamp(round(pct), 1, 100)  # 0 is rejected by the firmware
        self._write(0x11, 0xFF, FEAT_BACK, 0x2B, 0x00, p)

    def _back_set_zone(self, zone: int, r: int, g: int, b: int) -> None:
        # A zero channel can hang the device, so every channel is floored at 1.
        r, g, b = max(1, r), max(1, g), max(1, b)
        self._write(0x11, 0xFF, FEAT_BACK_COLOR, 0x1B, zone, r, g, b,
                    0xFF, 0x00, 0x00, 0x00, 0xFF, 0x00, 0x00, 0x00,
                    0xFF, 0x00, 0x00, 0x00)

    def _back_commit(self) -> None:
        self._write(0x11, 0xFF, FEAT_BACK_COLOR, 0x7B, 0x00, 0x00, 0x01)

    def back_color(self, r: int, g: int, b: int, zones: list[int] | None = None) -> None:
        """Set colour on the given zones (default: all) then latch once.

        The device only applies queued zone colours when it receives the commit
        (function 0x7B). Writing every zone first and committing a *single* time
        makes all zones change together instead of sweeping left-to-right.
        """
        if zones is None:
            zones = list(range(1, BACK_ZONES + 1))
        for zone in zones:
            self._back_set_zone(zone, r, g, b)
        self._back_commit()

    def get_back_power(self) -> bool | None:
        resp = self._query(0x11, 0xFF, FEAT_BACK, 0x3B)
        return None if resp is None else bool(resp[4])

    def get_back_brightness_pct(self) -> int | None:
        resp = self._query(0x11, 0xFF, FEAT_BACK, 0x1B)
        return None if resp is None else ((resp[4] << 8) | resp[5])
