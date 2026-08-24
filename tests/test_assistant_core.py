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
        self.mock_personal_class.return_value.poll_due_reminders.return_value = []
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

    def test_date_intent_is_answered_locally(self):
        with patch("assistant_core.system_actions.get_date_response", return_value="Nous sommes le dimanche 23 aout 2026."):
            response = self.assistant.handle_intent(
                {
                    "intent": "date",
                    "target": "",
                    "slots": {},
                }
            )

        self.assertEqual("Nous sommes le dimanche 23 aout 2026.", response)

    def test_chat_fallback_is_local_and_does_not_require_gpt(self):
        response = self.assistant.handle_intent(
            {
                "intent": "chat_fallback",
                "target": "bonjour",
                "slots": {},
            }
        )
        self.assertIn("Bonjour", response)
        self.assertIn("pret", response)

    def test_music_without_title_waits_for_a_follow_up(self):
        with patch("assistant_core.music_actions.play_music", return_value="J'ouvre Ninho dans YouTube Music.") as play_music:
            response = self.assistant.handle_intent({"intent": "play_music", "target": "", "slots": {}})
            follow_up = self.assistant.handle_intent({"intent": "chat_fallback", "target": "Ninho", "slots": {}})

        self.assertIn("Quelle musique", response)
        play_music.assert_called_once_with("Ninho")
        self.assertIn("Ninho", follow_up)

    def test_music_is_paused_then_resumed_for_a_command(self):
        self.assistant.music_playing = True
        with patch("assistant_core.music_actions.control_music", return_value="Je mets la musique en pause.") as control:
            self.assertTrue(self.assistant.pause_music_for_command())
        with patch("assistant_core.music_actions.control_music", return_value="Je reprends la musique.") as control:
            self.assertTrue(self.assistant.resume_music_after_command())

        self.assertTrue(self.assistant.music_playing)

    def test_daily_brief_uses_local_personal_context(self):
        self.mock_personal_class.return_value.get_open_todos.return_value = [
            {"content": "appeler client"},
        ]
        self.mock_personal_class.return_value.get_upcoming_reminders.return_value = [
            {"content": "reunion", "due_at": "2026-08-14T11:00:00"},
        ]

        with patch("ai_brain.can_answer_general_questions", return_value=False):
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

        with patch("ai_brain.can_answer_general_questions", return_value=False):
            response = self.assistant.handle_intent(
                {
                    "intent": "next_action",
                    "target": "",
                    "slots": {},
                }
            )

        self.assertIn("envoyer rapport", response)

    def test_prioritize_tasks_uses_local_brain(self):
        self.mock_personal_class.return_value.get_open_todos.return_value = [
            {"content": "urgent finir devis"},
            {"content": "classer dossiers"},
        ]

        with patch("ai_brain.can_answer_general_questions", return_value=False):
            response = self.assistant.handle_intent(
                {
                    "intent": "prioritize_tasks",
                    "target": "",
                    "slots": {},
                }
            )

        self.assertIn("Priorisation intelligente", response)

    def test_focus_mode_returns_project_focus_plan(self):
        self.mock_personal_class.return_value.get_open_todos.return_value = [
            {"content": "noor_express corriger bug facture"},
            {"content": "acheter cable hdmi"},
        ]

        with patch("ai_brain.can_answer_general_questions", return_value=False):
            response = self.assistant.handle_intent(
                {
                    "intent": "focus_mode",
                    "target": "noor_express",
                    "slots": {},
                }
            )

        self.assertIn("Mode focus projet actif", response)
        self.assertIn("noor_express", response)

    def test_sensitive_intent_requires_confirmation_before_execution(self):
        with patch("assistant_core.system_actions.close_app", return_value="Application fermee.") as close_app:
            prompt = self.assistant.handle_intent({"intent": "close_app", "target": "edge", "slots": {}})
            self.assertIn("Confirme", prompt)
            close_app.assert_not_called()

            result = self.assistant.handle_intent({"intent": "confirm_yes", "target": "", "slots": {}})
            self.assertEqual("Application fermee.", result)
            close_app.assert_called_once_with("edge")
            self.assertEqual("close_app", self.assistant.session_memory.last_turn()["intent"])

    def test_background_scheduler_messages_are_announced_once_per_pending_task(self):
        task = {"id": 11, "platform": "whatsapp", "title": "Promo du lundi"}
        with patch(
            "assistant_core.sched_core.get_pending_tasks",
            side_effect=[[task], [task], [], [task]],
        ):
            first = self.assistant.poll_background_messages()
            second = self.assistant.poll_background_messages()
            third = self.assistant.poll_background_messages()
            fourth = self.assistant.poll_background_messages()

        self.assertEqual(1, len(first))
        self.assertIn("Promo du lundi", first[0])
        self.assertEqual([], second)
        self.assertEqual([], third)
        self.assertEqual(1, len(fourth))


if __name__ == "__main__":
    unittest.main()
