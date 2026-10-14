"""Tests unitaires pour JarvisHeadlessBridge."""

import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock, Mock, patch

from headless_bridge import JarvisHeadlessBridge


class JarvisHeadlessBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_memory.db"
        self.bridge = JarvisHeadlessBridge(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_bridge_initialization(self):
        self.assertIsNotNone(self.bridge.brain)
        self.assertIsNotNone(self.bridge.memory)
        self.assertIsNotNone(self.bridge.agent)
        self.assertTrue(self.bridge.active_session_id.startswith("session_web_"))

    def test_get_status_returns_required_fields(self):
        with patch("ai_brain.get_ai_status", return_value={"online": True, "model": "qwen3:8b", "base_url": "http://localhost:11434"}):
            status = self.bridge.get_status()

        self.assertIn("status", status)
        self.assertIn("model", status)
        self.assertIn("base_url", status)
        self.assertIn("ollama_online", status)
        self.assertIn("tools_count", status)
        self.assertIn("memory", status)
        self.assertGreater(status["tools_count"], 10)

    def test_execute_prompt_empty(self):
        res = self.bridge.execute_prompt("")
        self.assertEqual("error", res["status"])
        self.assertEqual([], res["thought_chain"])
        self.assertEqual([], res["tools_used"])

    def test_execute_prompt_success(self):
        with patch.object(self.bridge.agent, "run", return_value="Il est 15:30."):
            res = self.bridge.execute_prompt("quelle heure est-il ?")

        self.assertEqual("success", res["status"])
        self.assertEqual("Il est 15:30.", res["reply"])
        self.assertIn("timestamp", res)
        self.assertIn("session_id", res)
        self.assertEqual("web_text", res["source"])

    def test_clear_and_get_history(self):
        self.bridge.memory.add_message("session_test", "user", "bonjour")
        self.bridge.memory.add_message("session_test", "assistant", "salut")

        history = self.bridge.get_history(limit=5, session_id="session_test")
        self.assertEqual(2, len(history))
        self.assertEqual("user", history[0]["role"])
        self.assertEqual("assistant", history[1]["role"])

        res = self.bridge.clear_history(session_id="session_test")
        self.assertEqual("success", res["status"])
        self.assertEqual([], self.bridge.get_history(limit=5, session_id="session_test"))

    def test_execute_prompt_captures_tool_calls(self):
        def fake_run(*args, **kwargs):
            import actions
            actions.execute_tool("get_time_response", {})
            return "Il est 16:00."

        with patch.object(self.bridge.agent, "run", side_effect=fake_run):
            res = self.bridge.execute_prompt("l'heure stp")

        self.assertEqual("success", res["status"])
        self.assertEqual("Il est 16:00.", res["reply"])
        self.assertEqual(1, len(res["tools_used"]))
        self.assertEqual("get_time_response", res["tools_used"][0]["tool"])


if __name__ == "__main__":
    unittest.main()
