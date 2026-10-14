import unittest
from unittest.mock import Mock, patch

from agent_core import AgentResult
from assistant_core import AssistantCore


class AssistantAgentIntegrationTests(unittest.TestCase):
    def setUp(self):
        project_patcher = patch("assistant_core.project_actions.ProjectAssistant")
        personal_patcher = patch("assistant_core.personal_actions.PersonalAssistant")
        self.addCleanup(project_patcher.stop)
        self.addCleanup(personal_patcher.stop)
        project_patcher.start().return_value = Mock()
        personal = personal_patcher.start().return_value
        personal.get_open_todos.return_value = []
        personal.get_upcoming_reminders.return_value = []
        personal.poll_due_reminders.return_value = []
        self.assistant = AssistantCore()

    def test_simple_historical_intent_bypasses_agent(self):
        with patch.object(self.assistant.agent, "run") as agent_run:
            with patch("assistant_core.system_actions.get_time_response", return_value="Il est 08:00."):
                response = self.assistant.handle_intent(
                    {
                        "intent": "time",
                        "target": "",
                        "raw": "quelle heure est-il",
                        "slots": {},
                    }
                )

        self.assertEqual("Il est 08:00.", response)
        agent_run.assert_not_called()

    def test_general_question_is_routed_to_agent(self):
        with patch("assistant_core.ai_brain.can_answer_general_questions", return_value=True):
            with patch.object(
                self.assistant.agent,
                "run",
                return_value=AgentResult("La diffusion de Rayleigh explique la couleur bleue."),
            ) as agent_run:
                response = self.assistant.handle_intent(
                    {
                        "intent": "chat_fallback",
                        "target": "pourquoi le ciel est bleu",
                        "raw": "Pourquoi le ciel est bleu ?",
                        "slots": {},
                    }
                )

        self.assertIn("Rayleigh", response)
        agent_run.assert_called_once()
        self.assertEqual((), agent_run.call_args.kwargs["tool_names"])

    def test_iot_temperature_is_routed_to_agent_when_raw_prompt_exists(self):
        with patch("assistant_core.ai_brain.can_answer_general_questions", return_value=True):
            with patch.object(
                self.assistant.agent,
                "run",
                return_value=AgentResult("La temperature IoT est de 24 degres."),
            ) as agent_run:
                response = self.assistant.handle_intent(
                    {
                        "intent": "iot_temperature",
                        "target": "",
                        "raw": "Quelle est la temperature de mon systeme IoT ?",
                        "slots": {},
                    }
                )

        self.assertIn("24", response)
        agent_run.assert_called_once()

    def test_compound_project_request_uses_agent_while_simple_request_keeps_intent(self):
        with patch("assistant_core.ai_brain.can_answer_general_questions", return_value=True):
            with patch.object(
                self.assistant.agent,
                "run",
                return_value=AgentResult("Le projet est ouvert et Git contient deux modifications."),
            ) as agent_run:
                response = self.assistant.handle_intent(
                    {
                        "intent": "open_project",
                        "target": "noor_express",
                        "raw": "Ouvre le projet Noor Express puis dis-moi si Git est propre.",
                        "slots": {},
                    }
                )

        self.assertIn("deux modifications", response)
        agent_run.assert_called_once()
        self.assertIn("git_status", agent_run.call_args.kwargs["tool_names"])
        self.assertIn("open_folder", agent_run.call_args.kwargs["tool_names"])


if __name__ == "__main__":
    unittest.main()
