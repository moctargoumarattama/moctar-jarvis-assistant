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

    def test_get_system_telemetry_action(self) -> None:
        import actions
        from actions.system_actions import get_system_telemetry
        from agent_core import AgentCore

        # 1. Vérification dans TOOL_DISPATCHER
        self.assertIn("get_system_telemetry", actions.TOOL_DISPATCHER)

        # 2. Exécution directe
        result = get_system_telemetry()
        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "get_system_telemetry")
        self.assertIn("data", result)
        self.assertIn("cpu_percent", result["data"])
        self.assertIn("memory", result["data"])
        self.assertIn("disk", result["data"])
        self.assertIn("temperature_c", result["data"])

        # 3. Exécution via execute_tool
        exec_result = actions.execute_tool("get_system_telemetry", {})
        self.assertTrue(exec_result["success"])
        self.assertEqual(exec_result["action"], "get_system_telemetry")

        # 4. Présence dans la liste des outils AgentCore
        with self.client as client:
            tool_names = [t.get("function", {}).get("name") for t in self.app.state.runtime.jarvis.agent.tools]
            self.assertIn("get_system_telemetry", tool_names)

    def test_select_tools_for_telemetry_prompt(self) -> None:
        from headless_bridge import select_tools_for_prompt

        test_queries = [
            "Quel est l'état du système et de l'infrastructure ?",
            "Vérifie la charge cpu et la ram",
            "Combien d'espace disque reste-t-il ?",
            "Ressource matérielle serveur",
            "Analyse le matériel hôte",
        ]

        for query in test_queries:
            with self.subTest(query=query):
                selected = select_tools_for_prompt(query)
                self.assertIn(
                    "get_system_telemetry",
                    selected,
                    f"'get_system_telemetry' doit être sélectionné pour le prompt '{query}'",
                )

    def test_jarvis_query_system_telemetry(self) -> None:
        with self.client as client:
            with patch.object(
                self.app.state.runtime.jarvis,
                "execute_prompt",
                return_value={
                    "status": "success",
                    "reply": "Télémétrie : CPU 12%, RAM 4.2/16 Go, Disque 52 Go libre.",
                    "thought_chain": ["Appel de get_system_telemetry"],
                    "tools_used": [{"tool": "get_system_telemetry", "result": {"cpu_percent": 12.0}}],
                    "timestamp": "2026-10-14T18:05:00",
                    "session_id": "test_telemetry_session",
                    "source": "web_text",
                },
            ):
                response = client.post(
                    "/api/jarvis/query",
                    json={"prompt": "Vérifie les ressources CPU et l'espace disque du système."},
                )
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["status"], "success")
                self.assertIn("get_system_telemetry", [t["tool"] for t in data["tools_used"]])


if __name__ == "__main__":
    unittest.main()
