import unittest
from unittest.mock import Mock, patch

from assistant_core import AssistantCore


class AssistantCorePhase2Tests(unittest.TestCase):
    def setUp(self):
        project_patcher = patch("assistant_core.project_actions.ProjectAssistant")
        personal_patcher = patch("assistant_core.personal_actions.PersonalAssistant")
        self.addCleanup(project_patcher.stop)
        self.addCleanup(personal_patcher.stop)
        self.mock_project_class = project_patcher.start()
        self.mock_personal_class = personal_patcher.start()
        self.mock_project_class.return_value = Mock()
        self.mock_personal_class.return_value = Mock()
        self.assistant = AssistantCore()

    def test_energy_intent_uses_energy_module_without_gpt(self):
        with patch(
            "assistant_core.energy_actions.calculate_consumption",
            return_value={
                "quantity": 12,
                "power_watts": 60,
                "duration_hours": 8,
                "total_power_watts": 720,
                "energy_kwh": 5.76,
            },
        ) as calculate_consumption, patch("assistant_core.safe_ask_gpt") as safe_ask_gpt:
            response = self.assistant.handle_intent(
                {
                    "intent": "energy_consumption",
                    "target": "",
                    "slots": {
                        "quantity": 12,
                        "power_watts": 60,
                        "duration_hours": 8,
                    },
                }
            )

        calculate_consumption.assert_called_once_with(
            quantity=12,
            power_watts=60,
            duration_hours=8,
        )
        safe_ask_gpt.assert_not_called()
        self.assertIn("5.76", response)
        self.assertIn("720", response)

    def test_iot_status_keeps_safe_response_when_api_is_unavailable(self):
        with patch("assistant_core.iot_actions.get_iot_status", return_value="API IoT non configuree."):
            response = self.assistant.handle_intent(
                {
                    "intent": "iot_status",
                    "target": "",
                    "slots": {},
                }
            )

        self.assertEqual("API IoT non configuree.", response)


if __name__ == "__main__":
    unittest.main()
