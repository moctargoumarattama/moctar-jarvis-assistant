import unittest
from unittest.mock import patch

from actions import iot_actions


class IoTActionsTests(unittest.TestCase):
    def test_status_returns_clear_message_without_base_url(self):
        with patch.dict(iot_actions.config.IOT_SETTINGS, {"base_url": ""}, clear=False):
            response = iot_actions.get_iot_status()

        self.assertEqual("API IoT non configuree.", response)

    def test_temperature_returns_safe_message_when_http_call_fails(self):
        with patch.dict(iot_actions.config.IOT_SETTINGS, {"base_url": "http://iot.local"}, clear=False):
            with patch("actions.iot_actions._safe_get_json", return_value=(None, "timeout")):
                response = iot_actions.get_iot_temperature()

        self.assertIn("Temperature indisponible", response)
        self.assertIn("timeout", response)

    def test_relay_returns_clear_message_without_base_url(self):
        with patch.dict(iot_actions.config.IOT_SETTINGS, {"base_url": ""}, clear=False):
            response = iot_actions.set_relay_state(relay_id=1, enabled=True)

        self.assertEqual("API IoT non configuree.", response)


if __name__ == "__main__":
    unittest.main()
