"""Tests unitaires pour les routes API de l'agent Jarvis headless dans NOC Dashboard."""
from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

import main
from config import Settings
from headless_bridge import JarvisHeadlessBridge


class TestJarvisAPI(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp_dir = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.db_path = self.temp_dir / "jarvis-test-memory.db"
        self.stack.enter_context(patch(
            "main.JarvisHeadlessBridge",
            side_effect=lambda **kwargs: JarvisHeadlessBridge(db_path=self.db_path, **kwargs),
        ))
        self.stack.enter_context(patch("main.collector.Collector.start", new=AsyncMock()))
        self.stack.enter_context(patch("main.collector.Collector.close", new=AsyncMock()))
        self.stack.enter_context(patch(
            "main.WhatsAppController.refresh_voice_readiness", new=AsyncMock(),
        ))
        self.stack.enter_context(patch(
            "main.collector.Collector.collect", new=AsyncMock(side_effect=self.local_snapshot),
        ))
        self.stack.enter_context(patch(
            "ai_brain.get_ai_status", return_value={"online": True},
        ))
        self.app = main.create_app(Settings(
            interval=60,
            voice_enabled=False,
            sentinel_geoip_enabled=False,
            sentinel_state_path=str(self.temp_dir / "sentinel.json"),
            cyber_state_path=str(self.temp_dir / "cyber.json"),
        ))
        self.client = TestClient(self.app)

    @staticmethod
    def local_snapshot() -> dict:
        return {
            "timestamp": main.collector.utc_now(),
            "hostname": "test-host",
            "platform": "test",
            "uptime_seconds": 100,
            "global_status": "operational",
            "hardware": {},
            "services": [],
            "infrastructure": {},
            "vps": [],
            "events": [],
            "actions": {"restart_enabled": False},
            "collector": {"last_success": main.collector.utc_now(), "error": None},
        }

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
        telemetry_result = {
            "success": True,
            "action": "get_system_telemetry",
            "message": "CPU 12.5%, RAM 4.2/16 Go, Disque 52 Go libre.",
            "data": {
                "cpu_percent": 12.5,
                "temperature_c": None,
                "memory": {"used_gb": 4.2, "total_gb": 16.0, "percent": 26.25},
                "disk": {"free_gb": 52.0, "total_gb": 256.0, "percent_used": 79.7},
            },
            "error": None,
        }
        telemetry = Mock(return_value=telemetry_result)
        expected_reply = (
            "[État Opérationnel]\n"
            "CPU 12.5%, RAM 4.2/16 Go (26.25%), Disque 52 Go libres.\n"
            "[Recommandations M.O.C.T.A.R]\n"
            "VERT : maintenir la surveillance des ressources."
        )

        def infer(messages, tools=None, think=None):
            # Le premier appel IA reçoit déjà les observations réelles, sans
            # dépendre du function-calling du modèle ni d'un second prélèvement.
            telemetry.assert_called_once_with()
            self.assertEqual("system", messages[0]["role"])
            system = messages[0]["content"]
            self.assertIn(json.dumps(telemetry_result, ensure_ascii=False), system)
            self.assertIn("collected_at", system)
            tool_names = {tool["function"]["name"] for tool in tools or []}
            self.assertNotIn("get_system_telemetry", tool_names)
            return {"content": expected_reply, "tool_calls": [], "finish_reason": "stop"}

        with self.client as client:
            bridge = self.app.state.runtime.jarvis
            self.assertEqual(self.db_path, bridge.db_path)
            with patch.dict("actions.TOOL_DISPATCHER", {"get_system_telemetry": telemetry}), \
                    patch.object(bridge.agent, "_call_llm", side_effect=infer) as llm:
                response = client.post(
                    "/api/jarvis/query",
                    json={"prompt": "Quel est l'état global du système et de l'infrastructure ?"},
                )
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["status"], "success")
                self.assertIn("CPU 12.5 %", data["reply"])
                self.assertIn("RAM 4.2/16 Go (26.25 %)", data["reply"])
                self.assertIn("Disque 52/256 Go libres (79.7 % utilisés)", data["reply"])
                self.assertIn("VERT : maintenir la surveillance des ressources.", data["reply"])
                self.assertIn("[État Opérationnel]", data["reply"])
                self.assertIn("[Recommandations M.O.C.T.A.R]", data["reply"])
                self.assertEqual(1, len(data["tools_used"]))
                self.assertEqual("get_system_telemetry", data["tools_used"][0]["tool"])
                self.assertEqual(telemetry_result["data"], data["tools_used"][0]["data"])
                llm.assert_called_once()


if __name__ == "__main__":
    unittest.main()
