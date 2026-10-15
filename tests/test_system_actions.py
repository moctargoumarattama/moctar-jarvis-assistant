"""Tests unitaires pour les actions système et la vision d'écran (system_actions.py)."""

from datetime import datetime
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import actions
from actions import system_actions


class SystemActionsTests(unittest.TestCase):
    def test_get_time_response(self):
        fixed_dt = datetime(2026, 10, 14, 15, 30)
        res = system_actions.get_time_response(now=fixed_dt)
        self.assertTrue(res["success"])
        self.assertEqual("get_time_response", res["action"])
        self.assertIn("15:30", res["message"])
        self.assertEqual("15:30", res["data"]["time"])

    def test_get_date_response(self):
        fixed_dt = datetime(2026, 10, 14, 15, 30)
        res = system_actions.get_date_response(now=fixed_dt)
        self.assertTrue(res["success"])
        self.assertEqual("get_date_response", res["action"])
        self.assertIn("2026", res["message"])

    def test_get_battery_response_success(self):
        mock_batt = Mock(percent=85, power_plugged=True)
        res = system_actions.get_battery_response(battery_provider=lambda: mock_batt)
        self.assertTrue(res["success"])
        self.assertIn("85", res["message"])
        self.assertEqual(85, res["data"]["percent"])

    def test_get_battery_response_none(self):
        res = system_actions.get_battery_response(battery_provider=lambda: None)
        self.assertFalse(res["success"])
        self.assertIn("ne peux pas lire", res["message"])

    def test_system_telemetry_does_not_invent_metrics_when_psutil_is_unavailable(self):
        for missing in ("all", "cpu_percent", "virtual_memory", "disk_usage"):
            with self.subTest(missing=missing):
                collectors = {
                    "cpu_percent": lambda **kwargs: 17.25,
                    "virtual_memory": lambda: SimpleNamespace(total=16 * 1024 ** 3, available=12 * 1024 ** 3, percent=25.0),
                    "disk_usage": lambda path: SimpleNamespace(total=500 * 1024 ** 3, free=125 * 1024 ** 3, percent=75.0),
                }
                if missing == "all":
                    collectors = {}
                else:
                    collectors.pop(missing)

                res = system_actions.get_system_telemetry(_psutil_module=SimpleNamespace(**collectors))

                self.assertFalse(res["success"])
                self.assertIsNone(res["data"])
                self.assertIn("psutil indisponible", res["error"])

    def test_system_telemetry_rejects_missing_measurements(self):
        for missing in ("cpu_percent", "virtual_memory", "disk_usage"):
            with self.subTest(missing=missing):
                collectors = SimpleNamespace(
                    cpu_percent=lambda **kwargs: 17.25,
                    virtual_memory=lambda: SimpleNamespace(total=16 * 1024 ** 3, available=12 * 1024 ** 3, percent=25.0),
                    disk_usage=lambda path: SimpleNamespace(total=500 * 1024 ** 3, free=125 * 1024 ** 3, percent=75.0),
                )
                setattr(collectors, missing, lambda *args, **kwargs: None)

                res = system_actions.get_system_telemetry(_psutil_module=collectors)

                self.assertFalse(res["success"])
                self.assertIsNone(res["data"])
                self.assertIn("indisponible", res["error"])

    def test_system_telemetry_preserves_the_collected_measurements(self):
        collectors = SimpleNamespace(
            cpu_percent=lambda **kwargs: 17.25,
            virtual_memory=lambda: SimpleNamespace(total=16 * 1024 ** 3, available=12 * 1024 ** 3, percent=25.0),
            disk_usage=lambda path: SimpleNamespace(total=500 * 1024 ** 3, free=125 * 1024 ** 3, percent=75.0),
            sensors_temperatures=lambda: {"coretemp": [SimpleNamespace(current=46.5)]},
        )

        res = system_actions.get_system_telemetry(_psutil_module=collectors)

        self.assertTrue(res["success"])
        self.assertIsNone(res["error"])
        self.assertEqual({
            "cpu_percent": 17.25,
            "temperature_c": 46.5,
            "memory": {"used_gb": 4.0, "total_gb": 16.0, "percent": 25.0},
            "disk": {"free_gb": 125.0, "total_gb": 500.0, "percent_used": 75.0},
        }, res["data"])
        self.assertIn("CPU 17.25%", res["message"])
        self.assertIn("RAM 4.0/16.0 Go (25.0%)", res["message"])
        self.assertIn("125.0/500.0 Go (25.0% libre)", res["message"])

    def test_capture_screenshot_success(self):
        mock_pyautogui = Mock()
        mock_img = Mock()
        mock_pyautogui.screenshot.return_value = mock_img

        res = system_actions.capture_screenshot(pyautogui_module=mock_pyautogui)
        self.assertTrue(res["success"])
        self.assertEqual("capture_screenshot", res["action"])
        mock_img.save.assert_called_once()
        self.assertIn("screenshot_", res["data"]["name"])

    def test_analyze_screen_with_vision_model(self):
        mock_pyautogui = Mock()
        mock_img = Mock()
        mock_pyautogui.screenshot.return_value = mock_img

        with patch("ai_brain.ask_ai", return_value="Je vois VS Code ouvert sur main_jarvis.py"):
            res = system_actions.analyze_screen(
                question="Que vois-tu ?",
                pyautogui_module=mock_pyautogui,
            )

        self.assertTrue(res["success"])
        self.assertEqual("analyze_screen", res["action"])
        self.assertIn("VS Code", res["message"])
        self.assertEqual("Je vois VS Code ouvert sur main_jarvis.py", res["data"]["analysis"])

    def test_analyze_screen_cloud_offline_preserves_capture(self):
        mock_pyautogui = Mock()
        mock_img = Mock()
        mock_pyautogui.screenshot.return_value = mock_img

        with patch("ai_brain.ask_ai", side_effect=RuntimeError("cloud unavailable")):
            res = system_actions.analyze_screen(
                question="Que vois-tu ?",
                pyautogui_module=mock_pyautogui,
            )

        self.assertTrue(res["success"])
        self.assertEqual("analyze_screen", res["action"])
        self.assertIn("Capture enregistrée", res["message"])
        self.assertIsNone(res["data"]["analysis"])

    def test_actions_dispatcher_has_analyze_screen(self):
        self.assertIn("analyze_screen", actions.TOOL_DISPATCHER)
        schema = actions.get_all_tools_schema()
        tool_names = [t["function"]["name"] for t in schema]
        self.assertIn("analyze_screen", tool_names)


if __name__ == "__main__":
    unittest.main()

