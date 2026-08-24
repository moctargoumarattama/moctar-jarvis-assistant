import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import ai_brain


class AiBrainConfigTests(unittest.TestCase):
    def test_ollama_settings_are_loaded_from_environment(self):
        with patch.dict(
            os.environ,
            {"OLLAMA_MODEL": "qwen3:14b", "OLLAMA_BASE_URL": "http://localhost:11434"},
            clear=True,
        ):
            model, base_url = ai_brain._ollama_settings()

        self.assertEqual("qwen3:14b", model)
        self.assertEqual("http://localhost:11434", base_url)

    def test_get_ai_status_reports_ollama_when_tags_are_available(self):
        response = Mock()
        response.read.return_value = b'{"models":[{"name":"qwen3:8b"}]}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)

        with patch("ai_brain.urlopen", return_value=response):
            status = ai_brain.get_ai_status()

        self.assertEqual("ollama", status["mode"])
        self.assertEqual("qwen3:8b", status["model"])
        self.assertIn("Ollama", status["label"])

    def test_ask_ollama_posts_to_chat_endpoint(self):
        response = Mock()
        response.read.return_value = b'{"message":{"content":"bonjour"}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)

        with patch.dict(
            os.environ,
            {"OLLAMA_MODEL": "qwen3:8b", "OLLAMA_BASE_URL": "http://localhost:11434"},
            clear=True,
        ):
            with patch("ai_brain.urlopen", return_value=response) as open_mock:
                answer = ai_brain.ask_ollama("salut")

        self.assertEqual("bonjour", answer)
        open_mock.assert_called_once()
        request = open_mock.call_args.args[0]
        self.assertEqual("http://localhost:11434/api/chat", request.full_url)
        self.assertEqual("POST", request.method)
        self.assertEqual("application/json", request.headers["Content-type"])

    def test_safe_ask_ollama_returns_fallback_on_failure(self):
        with patch("ai_brain.ask_ollama", side_effect=RuntimeError("boom")):
            answer = ai_brain.safe_ask_ollama("bonjour", fallback="fallback local")

        self.assertEqual("fallback local", answer)

    def test_ask_ollama_includes_images_when_provided(self):
        response = Mock()
        response.read.return_value = b'{"message":{"content":"analyse"}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as handle:
            handle.write(b"fake-image")
            image_path = Path(handle.name)

        try:
            with patch("ai_brain.urlopen", return_value=response) as open_mock:
                ai_brain.ask_ollama("decris", images=[str(image_path)])
        finally:
            image_path.unlink(missing_ok=True)

        request = open_mock.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertIn("images", payload["messages"][1])
        self.assertEqual(1, len(payload["messages"][1]["images"]))

    def test_local_brain_chat_uses_ollama_path(self):
        with patch("ai_brain.can_answer_general_questions", return_value=True):
            with patch("ai_brain.safe_ask_ollama", return_value="message ameliore") as ask_mock:
                answer = ai_brain.LocalBrain().chat("Rends ce message plus pro", context="scheduler")

        self.assertEqual("message ameliore", answer)
        ask_mock.assert_called_once()
        self.assertIn("Contexte additionnel: scheduler", ask_mock.call_args.args[0])

class LocalBrainTests(unittest.TestCase):
    def setUp(self):
        self.brain = ai_brain.LocalBrain()

    def test_generates_daily_brief_from_local_context(self):
        with patch("ai_brain.can_answer_general_questions", return_value=False):
            response = self.brain.generate_daily_brief(
                todos=[
                    {"content": "appeler client"},
                    {"content": "verifier les panneaux"},
                ],
                reminders=[
                    {"content": "reunion chantier", "due_at": "2026-08-14T09:30:00"},
                ],
                history=[
                    {"intent": "open_project", "target": "audit energetique"},
                    {"intent": "open_project", "target": "audit energetique"},
                ],
                preferences={"favorite_music": "afrobeat"},
            )

        self.assertIn("Brief local", response)
        self.assertIn("reunion chantier", response)
        self.assertIn("appeler client", response)
        self.assertIn("afrobeat", response)

    def test_suggests_next_action_from_first_reminder(self):
        with patch("ai_brain.can_answer_general_questions", return_value=False):
            response = self.brain.suggest_next_action(
                todos=[{"content": "preparer devis"}],
                reminders=[{"content": "envoyer rapport", "due_at": "2026-08-14T08:00:00"}],
                history=[],
            )

        self.assertIn("envoyer rapport", response)

    def test_prioritization_pushes_urgent_task_first(self):
        ranked = self.brain.prioritize_tasks(
            todos=[
                {"content": "verifier archive"},
                {"content": "urgent appeler client"},
            ]
        )

        self.assertEqual("urgent appeler client", ranked[0]["content"])

    def test_project_focus_mode_uses_active_project(self):
        with patch("ai_brain.can_answer_general_questions", return_value=False):
            response = self.brain.project_focus_mode(
                todos=[
                    {"content": "noor_express corriger endpoint"},
                    {"content": "faire menage boite mail"},
                ],
                insights={"active_project": "noor_express"},
                history=[],
            )

        self.assertIn("noor_express", response)
        self.assertIn("corriger endpoint", response)

    def test_build_auto_routine_evening_label(self):
        with patch("ai_brain.can_answer_general_questions", return_value=False):
            response = self.brain.build_auto_routine(
                todos=[{"content": "urgent finir rapport"}],
                reminders=[],
                now=ai_brain.datetime(2026, 8, 14, 20, 0, 0),
                mode="auto",
            )

        self.assertIn("Routine soir auto", response)

    def test_answers_greeting_without_external_ai(self):
        response = self.brain.answer("bonjour")

        self.assertIn("Bonjour", response)
        self.assertIn("pret", response)

    def test_answers_simple_math_locally(self):
        response = self.brain.answer("combien font 0 x 0")

        self.assertEqual("0", response)

    def test_open_fact_question_prefers_ollama_with_short_prompt(self):
        with patch("ai_brain.can_answer_general_questions", return_value=True):
            with patch("ai_brain.safe_ask_ollama", return_value="Niamey est la capitale du Niger.") as ask_mock:
                response = self.brain.answer("ou est la capitale du niger")

        self.assertEqual("Niamey est la capitale du Niger.", response)
        self.assertIn("une ou deux phrases maximum", ask_mock.call_args.kwargs["system_prompt"])

    def test_activity_request_uses_ollama_with_personal_context(self):
        with patch("ai_brain.can_answer_general_questions", return_value=True):
            with patch(
                "ai_brain.safe_ask_ollama",
                return_value="Demain, commence par appeler les clients puis prepare l'offre.",
            ) as ask_mock:
                response = self.brain.answer(
                    "donne moi un plan pour demain pour baba market",
                    todos=[{"content": "appeler trois clients chauds"}],
                    reminders=[{"content": "envoyer offre", "due_at": "2026-08-24T09:00:00"}],
                    history=[{"intent": "open_project", "target": "baba_market", "response": "Projet ouvert."}],
                    insights={"active_project": "baba_market"},
                )

        self.assertIn("Demain", response)
        self.assertIn("Projet actif: baba_market", ask_mock.call_args.args[0])
        self.assertIn("Todos ouverts:", ask_mock.call_args.args[0])
        self.assertIn("activites quotidiennes", ask_mock.call_args.kwargs["system_prompt"])

    def test_enhance_social_post_uses_local_fallback_when_ai_is_disabled(self):
        with patch("ai_brain.can_answer_general_questions", return_value=False):
            result = self.brain.enhance_social_post(
                "",
                platform="facebook",
                title="Promotion Baba Market",
                media_paths=["C:/demo/photo.jpg", "C:/demo/video.mp4"],
            )

        self.assertIn("Promotion Baba Market", result)
        self.assertIn("commentaire", result.lower())


if __name__ == "__main__":
    unittest.main()
