import os
import unittest
from unittest.mock import patch

import ai_brain


class AiBrainConfigTests(unittest.TestCase):
    def test_api_key_is_loaded_from_environment(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "  test-key-value  "}):
            self.assertEqual(ai_brain.get_openai_api_key(), "test-key-value")

    def test_missing_api_key_raises_clear_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                ai_brain.get_openai_api_key()

    def test_safe_ask_gpt_returns_fallback_on_failure(self):
        with patch("ai_brain.ask_gpt", side_effect=RuntimeError("boom")):
            answer = ai_brain.safe_ask_gpt("bonjour", fallback="fallback local")

        self.assertEqual("fallback local", answer)


class LocalBrainTests(unittest.TestCase):
    def setUp(self):
        self.brain = ai_brain.LocalBrain()

    def test_generates_daily_brief_from_local_context(self):
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
        response = self.brain.suggest_next_action(
            todos=[{"content": "preparer devis"}],
            reminders=[{"content": "envoyer rapport", "due_at": "2026-08-14T08:00:00"}],
            history=[],
        )

        self.assertIn("envoyer rapport", response)


if __name__ == "__main__":
    unittest.main()
