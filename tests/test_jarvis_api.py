"""Tests unitaires pour les routes API de l'agent Jarvis headless dans NOC Dashboard."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import main


class TestJarvisAPI(unittest.TestCase):
    def setUp(self) -> None:
        self.app = main.create_app()
        self.client = TestClient(self.app)

    def test_jarvis_status_endpoint(self) -> None:
        with self.client as client:
            response = client.get("/api/jarvis/status")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertIn("tools_count", data)
            self.assertIn("model", data)
            self.assertEqual(data["available"], True)
            self.assertGreaterEqual(data["tools_count"], 40)

    def test_jarvis_history_endpoint(self) -> None:
        with self.client as client:
            response = client.get("/api/jarvis/history")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertIn("messages", data)
            self.assertIsInstance(data["messages"], list)

    def test_jarvis_clear_history_endpoint(self) -> None:
        with self.client as client:
            response = client.delete("/api/jarvis/history/clear")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["ok"], True)

    def test_jarvis_query_validation(self) -> None:
        with self.client as client:
            # Rejet prompt vide
            resp_empty = client.post("/api/jarvis/query", json={"prompt": "   "})
            self.assertEqual(resp_empty.status_code, 422)

            # Execution d'un prompt mocké
            with patch.object(
                self.app.state.runtime.jarvis,
                "execute_prompt",
                return_value={
                    "status": "success",
                    "reply": "Test réponse Jarvis.",
                    "thought_chain": ["Réflexion test"],
                    "tools_used": [{"tool": "get_time_response"}],
                    "timestamp": "2026-10-14T18:00:00",
                    "session_id": "test_session",
                    "source": "web_text",
                },
            ):
                response = client.post("/api/jarvis/query", json={"prompt": "Quelle heure est-il ?"})
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["status"], "success")
                self.assertEqual(data["reply"], "Test réponse Jarvis.")
                self.assertEqual(len(data["thought_chain"]), 1)
                self.assertEqual(len(data["tools_used"]), 1)


if __name__ == "__main__":
    unittest.main()
