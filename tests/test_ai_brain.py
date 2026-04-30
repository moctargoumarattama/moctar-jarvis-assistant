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


if __name__ == "__main__":
    unittest.main()
