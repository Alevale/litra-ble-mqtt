"""Command-line control of the Litra Beam LX.

    litra on | off | toggle | blink
    litra brightness <0-100>            # front, percent
    litra lumen <30-400>                # front, raw lumens
    litra temperature <2700-6500>       # front, Kelvin
    litra status                        # read back current state
    litra back-on | back-off
    litra back-brightness <1-100>
    litra back-color <R> <G> <B> [zone] # 0-255 each; zone 1-7, default all
    litra raw <hexbytes>                # e.g. 11ff061c01

Requires write access to /dev/hidraw* (install the udev rule, or use sudo).
"""
from __future__ import annotations

import sys
import time

from litra_ble.device import LitraBeamLX, LitraError


def _need(args, n, usage):
    if len(args) < n:
        sys.exit(f"usage: litra {usage}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.exit(__doc__)

    cmd, rest = argv[0], argv[1:]
    dev = LitraBeamLX()

    try:
        if cmd == "on":
            dev.front_power(True)
        elif cmd == "off":
            dev.front_power(False)
        elif cmd == "toggle":
            state = dev.get_front_power()
            dev.front_power(not state if state is not None else True)
        elif cmd == "blink":
            for _ in range(3):
                dev.front_power(True); time.sleep(0.5)
                dev.front_power(False); time.sleep(0.5)
            dev.front_power(True)
        elif cmd == "brightness":
            _need(rest, 1, "brightness <0-100>")
            dev.front_brightness_pct(float(rest[0]))
        elif cmd == "lumen":
            _need(rest, 1, "lumen <30-400>")
            dev.front_brightness_lumen(int(rest[0]))
        elif cmd == "temperature":
            _need(rest, 1, "temperature <2700-6500>")
            dev.front_temperature(int(rest[0]))
        elif cmd == "back-on":
            dev.back_power(True)
        elif cmd == "back-off":
            dev.back_power(False)
        elif cmd == "back-brightness":
            _need(rest, 1, "back-brightness <1-100>")
            dev.back_brightness_pct(float(rest[0]))
        elif cmd == "back-color":
            _need(rest, 3, "back-color <R> <G> <B> [zone]")
            r, g, b = (int(x) for x in rest[:3])
            zones = [int(rest[3])] if len(rest) > 3 else None
            dev.back_color(r, g, b, zones)
        elif cmd == "raw":
            _need(rest, 1, "raw <hexbytes>")
            dev._write(*bytes.fromhex(rest[0]))
        elif cmd == "status":
            return _status(dev)
        else:
            sys.exit(f"unknown command: {cmd}\n{__doc__}")
    except LitraError as e:
        sys.exit(f"error: {e}")

    print(f"ok: {cmd}")
    return 0


def _status(dev: LitraBeamLX) -> int:
    if not dev.present:
        print("device: offline")
        return 1
    print(f"device: online ({dev.resolve_path()})")

    def fmt(value, suffix=""):
        return "?" if value is None else f"{value}{suffix}"

    print(f"  front power:       {fmt(dev.get_front_power())}")
    print(f"  front brightness:  {fmt(dev.get_front_brightness_lumen(), ' lm')}")
    print(f"  front temperature: {fmt(dev.get_front_temperature(), ' K')}")
    print(f"  back power:        {fmt(dev.get_back_power())}")
    print(f"  back brightness:   {fmt(dev.get_back_brightness_pct(), ' %')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
