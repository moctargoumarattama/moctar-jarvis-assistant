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
        self.mock_personal_class.return_value.get_open_todos.return_value = []
        self.mock_personal_class.return_value.get_upcoming_reminders.return_value = []
        self.assistant = AssistantCore()

    def test_energy_intent_uses_energy_module(self):
        with patch(
            "assistant_core.energy_actions.calculate_consumption",
            return_value={
                "quantity": 12,
                "power_watts": 60,
                "duration_hours": 8,
                "total_power_watts": 720,
                "energy_kwh": 5.76,
            },
        ) as calculate_consumption:
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

    def test_chat_fallback_is_local_and_does_not_require_gpt(self):
        response = self.assistant.handle_intent(
            {
                "intent": "chat_fallback",
                "target": "bonjour",
                "slots": {},
            }
        )
        self.assertIn("Mode local actif", response)

    def test_daily_brief_uses_local_personal_context(self):
        self.mock_personal_class.return_value.get_open_todos.return_value = [
            {"content": "appeler client"},
        ]
        self.mock_personal_class.return_value.get_upcoming_reminders.return_value = [
            {"content": "reunion", "due_at": "2026-08-14T11:00:00"},
        ]

        response = self.assistant.handle_intent(
            {
                "intent": "daily_brief",
                "target": "",
                "slots": {},
            }
        )

        self.assertIn("Brief local", response)
        self.assertIn("appeler client", response)
        self.assertIn("reunion", response)

    def test_next_action_prefers_upcoming_reminder(self):
        self.mock_personal_class.return_value.get_upcoming_reminders.return_value = [
            {"content": "envoyer rapport", "due_at": "2026-08-14T08:30:00"},
        ]

        response = self.assistant.handle_intent(
            {
                "intent": "next_action",
                "target": "",
                "slots": {},
            }
        )

        self.assertIn("envoyer rapport", response)

    def test_sensitive_intent_requires_confirmation_before_execution(self):
        with patch("assistant_core.system_actions.close_app", return_value="Application fermee.") as close_app:
            prompt = self.assistant.handle_intent({"intent": "close_app", "target": "edge", "slots": {}})
            self.assertIn("Confirme", prompt)
            close_app.assert_not_called()

            result = self.assistant.handle_intent({"intent": "confirm_yes", "target": "", "slots": {}})
            self.assertEqual("Application fermee.", result)
            close_app.assert_called_once_with("edge")
            self.assertEqual("close_app", self.assistant.session_memory.last_turn()["intent"])


if __name__ == "__main__":
    unittest.main()
