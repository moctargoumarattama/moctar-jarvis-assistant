"""Tests unitaires pour les actions système et la vision d'écran (system_actions.py)."""

from datetime import datetime
import os
from pathlib import Path
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

        with patch("ai_brain.ask_ollama", return_value="Je vois VS Code ouvert sur main_jarvis.py"):
            res = system_actions.analyze_screen(
                question="Que vois-tu ?",
                pyautogui_module=mock_pyautogui,
            )

        self.assertTrue(res["success"])
        self.assertEqual("analyze_screen", res["action"])
        self.assertIn("VS Code", res["message"])
        self.assertEqual("Je vois VS Code ouvert sur main_jarvis.py", res["data"]["analysis"])

    def test_analyze_screen_ollama_offline_fallback(self):
        mock_pyautogui = Mock()
        mock_img = Mock()
        mock_pyautogui.screenshot.return_value = mock_img

        with patch("ai_brain.ask_ollama", side_effect=RuntimeError("connection refused")):
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

