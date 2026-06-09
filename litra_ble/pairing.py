"""Pair Litra lights to the host's BlueZ by Bluetooth address.

Litra lights speak HID-over-GATT, which requires a *bonded* (encrypted) BLE
link before the kernel will expose the control hidraw node. Bonding a new device
needs a registered BlueZ pairing agent — without one, ``Pair()`` fails with
``AuthenticationFailed`` (the original symptom that blocked this whole project).

Rather than re-implement an ``org.bluez.Agent1`` over D-Bus, this drives the
``bluetoothctl`` CLI, which ships with BlueZ and handles the agent dance for us.
It works the same whether BlueZ runs on a plain host or is reached from inside a
Home Assistant add-on container via the host D-Bus socket.

The flow per address:
  * if already paired -> just (re)trust and connect,
  * otherwise -> register a NoInputNoOutput agent, scan, pair, trust, connect.

``trust`` is what lets BlueZ silently reconnect the light after a reboot or when
it wakes from sleep, so we only ever need to pair once.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time

log = logging.getLogger("litra-mqtt.pairing")

BCTL = "bluetoothctl"


def _oneshot(*args: str, timeout: float = 15) -> str:
    """Run `bluetoothctl <args>` and return combined output (best-effort)."""
    try:
        proc = subprocess.run(
            [BCTL, *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return (proc.stdout or "") + (proc.stderr or "")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.debug("bluetoothctl %s failed: %s", args, e)
        return ""


def available() -> bool:
    """True if bluetoothctl exists and an adapter is present."""
    out = _oneshot("list")
    return "Controller" in out


def is_paired(address: str) -> bool:
    return "Paired: yes" in _oneshot("info", address)


def is_connected(address: str) -> bool:
    return "Connected: yes" in _oneshot("info", address)


def power_on() -> None:
    _oneshot("power", "on")


class _Session:
    """A short-lived interactive bluetoothctl session.

    Commands are written to stdin with delays between them while a background
    thread drains stdout (so a full pipe buffer can never deadlock us).
    """

    def __init__(self):
        self.proc = subprocess.Popen(
            [BCTL], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        self.lines: list[str] = []
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.lines.append(line)

    def send(self, cmd: str, wait: float = 0.5) -> None:
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(cmd + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass
        time.sleep(wait)

    def close(self) -> str:
        self.send("quit", 0.2)
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self._reader.join(timeout=2)
        return "".join(self.lines)


def pair(address: str, scan_seconds: float = 8.0) -> bool:
    """Pair + trust + connect a single address. Returns True on success."""
    power_on()

    if is_paired(address):
        # Already bonded: make sure it's trusted (auto-reconnect) and connected.
        _oneshot("trust", address)
        _oneshot("connect", address)
        log.info("%s already paired; ensured trusted + connected", address)
        return True

    log.info("pairing %s (scanning up to %.0fs)...", address, scan_seconds)
    s = _Session()
    s.send("power on", 1.0)
    s.send("agent NoInputNoOutput", 0.5)
    s.send("default-agent", 0.5)
    s.send("scan le", scan_seconds)   # let advertisements come in
    s.send("scan off", 0.5)
    s.send(f"pair {address}", 6.0)
    s.send(f"trust {address}", 1.0)
    s.send(f"connect {address}", 4.0)
    out = s.close()

    ok = ("Pairing successful" in out) or is_paired(address)
    if ok:
        _oneshot("trust", address)
        log.info("paired %s", address)
    else:
        log.warning("failed to pair %s; is it in pairing mode and in range?",
                    address)
        log.debug("bluetoothctl transcript:\n%s", out)
    return ok


def ensure_paired(addresses: list[str], scan_seconds: float = 8.0) -> dict[str, bool]:
    """Ensure every address is paired+trusted+connected. Returns {addr: ok}."""
    if not available():
        log.warning("no Bluetooth adapter / bluetoothctl; skipping pairing")
        return {a: False for a in addresses}
    power_on()
    results: dict[str, bool] = {}
    for addr in addresses:
        try:
            results[addr] = pair(addr, scan_seconds=scan_seconds)
        except Exception as e:  # never let one light stop the others
            log.warning("error pairing %s: %s", addr, e)
            results[addr] = False
    return results
