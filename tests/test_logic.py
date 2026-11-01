"""Unit tests for the logic this fork changed. No Bluetooth or MQTT needed."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from litra_ble import device, pairing  # noqa: E402


class ValidAddressTest(unittest.TestCase):
    def test_accepts_mac_in_either_case(self):
        self.assertTrue(pairing.valid_address("AA:BB:CC:DD:EE:FF"))
        self.assertTrue(pairing.valid_address("aa:bb:cc:dd:ee:ff"))

    def test_rejects_anything_that_could_reach_bluetoothctl_stdin(self):
        for bad in ("", None, "AA:BB:CC:DD:EE", "AA:BB:CC:DD:EE:FF\nremove all",
                    "AA:BB:CC:DD:EE:FF ", "pair AA:BB:CC:DD:EE:FF"):
            self.assertFalse(pairing.valid_address(bad), repr(bad))

    def test_pair_refuses_invalid_address_without_calling_bluetoothctl(self):
        with mock.patch.object(pairing, "_oneshot") as oneshot, \
                mock.patch.object(pairing, "_Session") as session:
            self.assertFalse(pairing.pair("not-a-mac\nremove all"))
            oneshot.assert_not_called()
            session.assert_not_called()


class ModelFromNameTest(unittest.TestCase):
    def test_known_models(self):
        self.assertEqual(device._model_from_name("Litra Beam LX"), "beam_lx")
        self.assertEqual(device._model_from_name("Litra Beam"), "beam")
        self.assertEqual(device._model_from_name("Litra Glow"), "glow")

    def test_unknown_names_are_skipped_not_guessed(self):
        self.assertIsNone(device._model_from_name("Litra Something"))
        self.assertIsNone(device._model_from_name("Keyboard"))
        self.assertIsNone(device._model_from_name(""))


class TemperatureTest(unittest.TestCase):
    def sent_kelvin(self, kelvin):
        light = device.LitraLight(model="beam")
        with mock.patch.object(light, "_write") as write:
            light.front_temperature(kelvin)
        payload = write.call_args.args
        self.assertEqual(payload[:4], (0x11, 0xFF, 0x04, 0x9C))
        return (payload[4] << 8) | payload[5]

    def test_rounds_to_100_k(self):
        # Values Home Assistant produces after its mired round-trip.
        self.assertEqual(self.sent_kelvin(2702), 2700)
        self.assertEqual(self.sent_kelvin(6494), 6500)
        self.assertEqual(self.sent_kelvin(4049), 4000)

    def test_clamps_to_range(self):
        self.assertEqual(self.sent_kelvin(1000), 2700)
        self.assertEqual(self.sent_kelvin(9000), 6500)


if __name__ == "__main__":
    unittest.main()
