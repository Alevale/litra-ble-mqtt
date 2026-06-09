"""Low-level control of Logitech Litra lights over their HID interface.

A Litra light, whether connected over USB or Bluetooth (BLE), is exposed by the
Linux kernel as a HID device (`/dev/hidraw*`). Control happens via 20-byte HID
output reports on a Logitech vendor collection (Usage Page 0xFF43, Report ID
0x11), using a proprietary protocol reverse-engineered by the open-source
`litra` / `litra-rs` projects (https://github.com/timrogers/litra-rs).

Report layout (20 bytes total):

    byte 0 : 0x11            report id
    byte 1 : 0xff            direct addressing
    byte 2 : <feature index> model-dependent (see PROFILES)
    byte 3 : <function>      e.g. 0x1c power, 0x4c brightness, 0x9c temperature
    byte 4+: <params>        zero-padded to 20 bytes

Front brightness is a 2-byte big-endian *lumen* value (30..400); colour
temperature is a 2-byte big-endian Kelvin value (2700..6500).

Multiple lights are supported: a :class:`LitraLight` is addressed by its
Bluetooth/USB address (the kernel's ``HID_UNIQ``), so several lights on one host
don't get confused. Pass ``address=None`` to grab the first Litra found.

This module talks straight to /dev/hidraw and has no third-party dependencies.
"""
from __future__ import annotations

import glob
import os
import select
import time

VENDOR_ID = 0x046D
# Known Litra product ids (USB and, where applicable, BLE).
PRODUCT_IDS = (0xB903, 0xC903, 0xC900, 0xC901)

# Per-model protocol profile. The big difference between models is the front
# light's feature index (0x06 on the Beam LX, 0x04 on the older Beam/Glow) and
# whether a back RGB strip exists.
PROFILES = {
    "beam_lx": {"front": 0x06, "has_back": True},
    "beam": {"front": 0x04, "has_back": False},
    "glow": {"front": 0x04, "has_back": False},
}
DEFAULT_MODEL = "beam_lx"

# Back strip feature indices (Beam LX only).
FEAT_BACK = 0x0A         # back power / brightness
FEAT_BACK_COLOR = 0x0C   # back per-zone colour + commit
BACK_ZONES = 7

# Front light ranges.
LUMEN_MIN, LUMEN_MAX = 30, 400
TEMP_MIN, TEMP_MAX = 2700, 6500

REPORT_LEN = 20


class LitraError(RuntimeError):
    pass


class LitraNotFound(LitraError):
    pass


class LitraUnsupported(LitraError):
    """Raised when an operation isn't supported by this light's model."""


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _norm_addr(addr: str | None) -> str | None:
    return addr.replace(":", "").lower() if addr else None


def _read_uevent(hidraw_name: str) -> str:
    try:
        with open(f"/sys/class/hidraw/{hidraw_name}/device/uevent") as f:
            return f.read()
    except OSError:
        return ""


def _uevent_field(uevent: str, key: str) -> str | None:
    for line in uevent.splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


def list_litras() -> list[dict]:
    """Return info dicts for every Litra HID node currently present.

    Each dict has: ``path`` (/dev/hidrawN), ``address`` (lowercased, no colons),
    ``product`` (int) and ``name``.
    """
    found = []
    for syspath in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        name = os.path.basename(syspath)
        uevent = _read_uevent(name)
        hid_id = (_uevent_field(uevent, "HID_ID") or "").upper()
        if f"{VENDOR_ID:08X}" not in hid_id and f"{VENDOR_ID:04X}" not in hid_id:
            continue
        if not any(f"{pid:04X}" in hid_id for pid in PRODUCT_IDS):
            continue
        found.append({
            "path": f"/dev/{name}",
            "address": _norm_addr(_uevent_field(uevent, "HID_UNIQ")) or "",
            "product": next((pid for pid in PRODUCT_IDS
                             if f"{pid:04X}" in hid_id), 0),
            "name": _uevent_field(uevent, "HID_NAME") or "Litra",
        })
    return found


class LitraLight:
    """A single Litra light, addressed by its hidraw node.

    The hidraw path is resolved lazily on every operation, because over
    Bluetooth the node disappears when the light sleeps and reappears (possibly
    as a different number) when it wakes. Callers never hold a stale fd.
    """

    def __init__(self, address: str | None = None, model: str = DEFAULT_MODEL,
                 name: str | None = None):
        if model not in PROFILES:
            raise LitraUnsupported(f"unknown model {model!r}; known: {list(PROFILES)}")
        self.address = address
        self.model = model
        self.name = name or (f"Litra {address}" if address else "Litra")
        self._profile = PROFILES[model]

    # -- discovery ------------------------------------------------------------

    def find_hidraw(self) -> str | None:
        want = _norm_addr(self.address)
        for info in list_litras():
            if want is None or info["address"] == want:
                return info["path"]
        return None

    def resolve_path(self) -> str:
        path = self.find_hidraw()
        if not path or not os.path.exists(path):
            who = self.address or "any Litra"
            raise LitraNotFound(
                f"{who} not found - is it powered on and connected?"
            )
        return path

    @property
    def present(self) -> bool:
        return self.find_hidraw() is not None

    @property
    def has_back(self) -> bool:
        return bool(self._profile["has_back"])

    def unique_suffix(self) -> str:
        """Stable id for MQTT unique_ids, derived from the address."""
        addr = _norm_addr(self.address)
        if addr:
            return addr[-6:]
        path = self.find_hidraw()
        if path:
            addr = _norm_addr(_uevent_field(_read_uevent(os.path.basename(path)),
                                            "HID_UNIQ"))
            if addr:
                return addr[-6:]
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
        try:
            fd = os.open(path, os.O_WRONLY)
        except PermissionError as e:
            raise LitraError(
                f"permission denied opening {path}; install the udev rule or "
                f"run as root ({e})"
            ) from e
        try:
            os.write(fd, self._report(*payload))
        except OSError as e:
            raise LitraError(f"write to {path} failed: {e}") from e
        finally:
            os.close(fd)

    def _query(self, *payload: int, timeout: float = 0.5) -> bytes | None:
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

    @property
    def _front(self) -> int:
        return self._profile["front"]

    def front_power(self, on: bool) -> None:
        self._write(0x11, 0xFF, self._front, 0x1C, 0x01 if on else 0x00)

    def front_brightness_lumen(self, lumen: int) -> None:
        lm = _clamp(int(lumen), LUMEN_MIN, LUMEN_MAX)
        self._write(0x11, 0xFF, self._front, 0x4C, (lm >> 8) & 0xFF, lm & 0xFF)

    def front_brightness_pct(self, pct: float) -> None:
        pct = _clamp(pct, 0, 100)
        self.front_brightness_lumen(round(LUMEN_MIN + (LUMEN_MAX - LUMEN_MIN) * pct / 100))

    def front_temperature(self, kelvin: int) -> None:
        k = _clamp(int(kelvin), TEMP_MIN, TEMP_MAX)
        self._write(0x11, 0xFF, self._front, 0x9C, (k >> 8) & 0xFF, k & 0xFF)

    def get_front_power(self) -> bool | None:
        resp = self._query(0x11, 0xFF, self._front, 0x01)
        return None if resp is None else bool(resp[4])

    def get_front_brightness_lumen(self) -> int | None:
        resp = self._query(0x11, 0xFF, self._front, 0x31)
        return None if resp is None else (resp[4] << 8) | resp[5]

    def get_front_temperature(self) -> int | None:
        resp = self._query(0x11, 0xFF, self._front, 0x81)
        return None if resp is None else (resp[4] << 8) | resp[5]

    # -- back strip (Beam LX only) -------------------------------------------

    def _require_back(self) -> None:
        if not self.has_back:
            raise LitraUnsupported(f"{self.model} has no back RGB strip")

    def back_power(self, on: bool) -> None:
        self._require_back()
        self._write(0x11, 0xFF, FEAT_BACK, 0x4B, 0x01 if on else 0x00)

    def back_brightness_pct(self, pct: float) -> None:
        self._require_back()
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

        The device only applies queued zone colours on commit. Writing every
        zone first and committing a *single* time makes all zones change
        together instead of sweeping left-to-right.
        """
        self._require_back()
        if zones is None:
            zones = list(range(1, BACK_ZONES + 1))
        for zone in zones:
            self._back_set_zone(zone, r, g, b)
        self._back_commit()

    def get_back_power(self) -> bool | None:
        self._require_back()
        resp = self._query(0x11, 0xFF, FEAT_BACK, 0x3B)
        return None if resp is None else bool(resp[4])

    def get_back_brightness_pct(self) -> int | None:
        self._require_back()
        resp = self._query(0x11, 0xFF, FEAT_BACK, 0x1B)
        return None if resp is None else ((resp[4] << 8) | resp[5])


# Backwards-compatible alias (single-device callers / the CLI).
class LitraBeamLX(LitraLight):
    def __init__(self, address: str | None = None, name: str | None = None):
        super().__init__(address=address, model="beam_lx", name=name)
