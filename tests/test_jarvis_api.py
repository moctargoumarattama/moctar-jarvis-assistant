"""Tests unitaires pour les routes API de l'agent Jarvis headless dans NOC Dashboard."""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import URLError

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

    def test_hybrid_status_routes_keep_cloud_online_with_ollama_offline(self) -> None:
        with patch("ai_brain.load_dotenv"), patch.dict(os.environ, {
            "AI_PROVIDER": "hybrid", "GEMINI_API_KEY": "test-gemini-key",
            "GEMINI_MODEL": "gemini-3.5-flash-lite",
        }), patch("ai_brain.genai") as genai, \
                patch("ai_brain.urlopen", side_effect=URLError("Ollama offline")):
            with self.client as client:
                for path in ("/api/jarvis/status", "/api/status", "/api/system/ai-status"):
                    with self.subTest(path=path):
                        response = client.get(path)
                        self.assertEqual(200, response.status_code)
                        data = response.json()
                        self.assertTrue(data["available"])
                        self.assertTrue(data["online"])
                        self.assertEqual("hybrid", data["provider"])
                        self.assertEqual("gemini", data["active_provider"])
                        self.assertEqual("gemini-3.5-flash-lite", data["model"])
                        self.assertTrue(data["gemini_online"])
                        self.assertFalse(data["ollama_online"])
                        self.assertFalse(data["model_offline"])
                        self.assertFalse(data["all_providers_offline"])
                health = client.get("/api/health")
                self.assertEqual(200, health.status_code)
                self.assertEqual("ok", health.json()["status"])
                self.assertTrue(health.json()["collector_ready"])
                self.assertTrue(health.json()["ai_online"])
                self.assertFalse(health.json()["ai"]["model_offline"])
            genai.Client.return_value.models.generate_content.assert_not_called()

    def test_status_probe_runs_outside_the_async_event_loop(self) -> None:
        import asyncio

        def status_probe():
            with self.assertRaises(RuntimeError):
                asyncio.get_running_loop()
            return {"status": "ready", "online": True, "model_offline": False}

        with self.client as client:
            with patch.object(self.app.state.runtime.jarvis, "get_status", side_effect=status_probe):
                self.assertTrue(client.get("/api/status").json()["online"])
                self.assertTrue(client.get("/api/health").json()["ai_online"])

    def test_status_probe_error_keeps_the_bridge_available(self) -> None:
        with self.client as client:
            with patch.object(self.app.state.runtime.jarvis, "get_status", side_effect=RuntimeError("probe failed")):
                response = client.get("/api/status")
                self.assertEqual(500, response.status_code)
                data = response.json()
                self.assertTrue(data["available"])
                self.assertNotIn("model_offline", data)
                self.assertNotIn("all_providers_offline", data)
                health = client.get("/api/health")
                self.assertEqual(200, health.status_code)
                self.assertTrue(health.json()["collector_ready"])

    def test_status_routes_report_an_absent_bridge(self) -> None:
        with self.client as client:
            self.app.state.runtime.jarvis = None
            for path in ("/api/jarvis/status", "/api/status", "/api/system/ai-status"):
                with self.subTest(path=path):
                    data = client.get(path).json()
                    self.assertFalse(data["available"])
                    self.assertFalse(data["online"])

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


    def test_open_site_smart_extraction_and_alias(self) -> None:
        import actions
        # 1. Target standard youtube
        res_yt = actions.execute_tool("open_site", {"target": "youtube"})
        self.assertTrue(res_yt["success"])
        self.assertEqual(res_yt["data"]["action"], "open_browser")
        self.assertEqual(res_yt["data"]["url"], "https://www.youtube.com")

        # 2. Argument alias 'url'
        res_url = actions.execute_tool("open_site", {"url": "https://babamarket.ma"})
        self.assertTrue(res_url["success"])
        self.assertEqual(res_url["data"]["url"], "https://babamarket.ma")

        # 3. Argument alias 'query' pour recherche
        res_query = actions.execute_tool("open_site", {"query": "python test"})
        self.assertTrue(res_query["success"])
        self.assertIn("google.com/search?q=python+test", res_query["data"]["url"])

        # 4. open_app ciblant un site -> délégation automatique transparente
        res_app_site = actions.execute_tool("open_app", {"target": "youtube"})
        self.assertTrue(res_app_site["success"])
        self.assertEqual(res_app_site["data"]["action"], "open_browser")
        self.assertEqual(res_app_site["data"]["url"], "https://www.youtube.com")

    def test_jarvis_query_fast_path_open_browser(self) -> None:
        with self.client as client:
            # Fast-path pour ouverture YouTube
            response = client.post(
                "/api/jarvis/query",
                json={"prompt": "ouvre youtube"},
            )
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["action"], "open_browser")
            self.assertEqual(data["url"], "https://www.youtube.com")
            self.assertIn("YouTube", data["reply"])
            self.assertIn("https://www.youtube.com", data["reply"])

            # Fast-path avec faute de frappe fréquente "ouve"
            response_typo = client.post(
                "/api/jarvis/query",
                json={"prompt": "ouve google"},
            )
            self.assertEqual(response_typo.status_code, 200)
            data_typo = response_typo.json()
            self.assertEqual(data_typo["status"], "success")
            self.assertEqual(data_typo["action"], "open_browser")
            self.assertEqual(data_typo["url"], "https://www.google.com")

    def test_wake_word_elements_in_html(self) -> None:
        index_path = Path("static/index.html")
        content = index_path.read_text(encoding="utf-8")
        self.assertIn("jarvis-wakeword-toggle", content)
        self.assertIn("jarvis-wakeword-badge", content)
        self.assertIn("Yo Moctar", content)
        self.assertIn("EN VEILLE ÉCOUTE", content)

    def test_model_offline_modal_and_typing_elements_in_html(self) -> None:
        index_path = Path("static/index.html")
        content = index_path.read_text(encoding="utf-8")
        self.assertIn("model-offline-modal", content)
        self.assertIn("Moteur IA Hors-Ligne", content)
        self.assertIn("MODE AUTONOME ACTIF", content)
        self.assertIn("model-offline-retry-btn", content)
        self.assertIn("model-offline-close-btn", content)
        self.assertIn("jarvis-typing-bubble", content)
        self.assertIn("showModelOfflinePopup", content)

    def test_autonomous_task_library_direct_execution(self) -> None:
        import autonomous_library
        # 1. Aide et catalogue complet
        res_help = autonomous_library.execute_autonomous_task("aide")
        self.assertTrue(res_help["handled"])
        self.assertIn("BIBLIOTHÈQUE DES 46 TÂCHES AUTONOMES", res_help["reply"])
        self.assertIn("Surveillance Matérielle", res_help["reply"])

        # 2. Salutation locale
        res_greet = autonomous_library.execute_autonomous_task("bonjour")
        self.assertTrue(res_greet["handled"])
        self.assertIn("Bonjour monsieur", res_greet["reply"])

        # 3. Calcul solaire autonome
        res_solar = autonomous_library.execute_autonomous_task("dimensionne solaire 8 kwh")
        self.assertTrue(res_solar["handled"])
        self.assertIn("Dimensionnement Solaire", res_solar["reply"])
        self.assertIn("Wc", res_solar["reply"])

        # 4. Heure et date
        res_time = autonomous_library.execute_autonomous_task("quelle heure est-il")
        self.assertTrue(res_time["handled"])
        self.assertIn("🕒", res_time["reply"])

        # 5. Volume
        res_vol = autonomous_library.execute_autonomous_task("coupe le son")
        self.assertTrue(res_vol["handled"])
        self.assertIn("0%", res_vol["reply"])

    def test_jarvis_query_model_offline_flag(self) -> None:
        with self.client as client:
            bridge = self.app.state.runtime.jarvis
            with patch.object(bridge.agent, "_call_llm", side_effect=TimeoutError("Both providers offline")), \
                    patch.object(bridge.brain, "get_health_status", return_value={
                        "online": False, "all_providers_offline": True, "model_offline": True,
                    }):
                response = client.post(
                    "/api/jarvis/query",
                    json={"prompt": "dimensionne solaire 10 kwh"},
                )
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["status"], "success")
                self.assertTrue(data.get("model_offline"))
                self.assertIn("Dimensionnement Solaire", data["reply"])

    def test_jarvis_query_uses_gemini_when_ollama_is_offline(self) -> None:
        expected_reply = "Un panneau solaire transforme la lumiere en electricite."
        sdk_response = SimpleNamespace(candidates=[SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(text=expected_reply, thought=False, function_call=None)]),
            finish_reason="STOP",
        )])
        with patch("ai_brain.load_dotenv"), patch.dict(os.environ, {
            "AI_PROVIDER": "hybrid", "GEMINI_API_KEY": "test-gemini-key",
            "GEMINI_MODEL": "gemini-test-model",
        }), patch("ai_brain.genai") as genai, \
                patch("ai_brain.urlopen", side_effect=URLError("Ollama offline")) as ollama:
            gemini = genai.Client.return_value.models.generate_content
            gemini.return_value = sdk_response
            with self.client as client:
                response = client.post("/api/jarvis/query", json={"prompt": "Explique simplement le principe photovoltaïque."})

        self.assertEqual(200, response.status_code)
        data = response.json()
        self.assertEqual("success", data["status"])
        self.assertEqual(expected_reply, data["reply"])
        self.assertFalse(data["model_offline"])
        self.assertFalse(data["all_providers_offline"])
        self.assertTrue(data["online"])
        self.assertTrue(data["gemini_online"])
        self.assertEqual("gemini", data["active_provider"])
        self.assertIsNone(data["model_offline_details"])
        self.assertNotIn("Ollama", data["reply"])
        self.assertGreaterEqual(ollama.call_count, 1)
        gemini.assert_called_once()
        self.assertEqual("gemini-test-model", gemini.call_args.kwargs["model"])

    def test_jarvis_query_uses_gemini_when_ollama_freezes_past_deadline(self) -> None:
        expected_reply = "Un onduleur convertit le courant continu en courant alternatif."
        sdk_response = SimpleNamespace(candidates=[SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(text=expected_reply, thought=False, function_call=None)]),
            finish_reason="STOP",
        )])
        release_local = threading.Event()

        def frozen_ollama(*args, **kwargs):
            release_local.wait(timeout=2)
            raise TimeoutError("Ollama frozen")

        with patch("ai_brain.load_dotenv"), patch.dict(os.environ, {
            "AI_PROVIDER": "hybrid", "GEMINI_API_KEY": "test-gemini-key",
        }), patch("ai_brain.genai") as genai, \
                patch("ai_brain.HYBRID_OLLAMA_TIMEOUT_SECONDS", 0.03), \
                patch("ai_brain.urlopen", side_effect=frozen_ollama):
            genai.Client.return_value.models.generate_content.return_value = sdk_response
            try:
                with self.client as client:
                    started = time.monotonic()
                    response = client.post("/api/jarvis/query", json={"prompt": "Explique le principe d'un onduleur."})
                    elapsed = time.monotonic() - started
            finally:
                release_local.set()

        self.assertLess(elapsed, 1.5)
        self.assertEqual(200, response.status_code)
        self.assertEqual(expected_reply, response.json()["reply"])
        self.assertTrue(response.json()["online"])
        self.assertFalse(response.json()["model_offline"])
        genai.Client.return_value.models.generate_content.assert_called_once()

    def test_telemetry_reply_and_health_stay_online_on_gemini_fallback(self) -> None:
        telemetry_result = {
            "success": True, "action": "get_system_telemetry", "message": "Mesures relevees.",
            "data": {"cpu_percent": 12.5, "temperature_c": None,
                     "memory": {"used_gb": 4.2, "total_gb": 16.0, "percent": 26.25},
                     "disk": {"free_gb": 52.0, "total_gb": 256.0, "percent_used": 79.7}},
            "error": None,
        }
        sdk_response = SimpleNamespace(candidates=[SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(text="Surveillance active.", thought=False, function_call=None)]),
            finish_reason="STOP",
        )])
        with patch("ai_brain.load_dotenv"), patch.dict(os.environ, {
            "AI_PROVIDER": "hybrid", "GEMINI_API_KEY": "test-gemini-key",
        }), patch("ai_brain.genai") as genai, \
                patch("ai_brain.urlopen", side_effect=URLError("Ollama offline")), \
                patch.dict("actions.TOOL_DISPATCHER", {"get_system_telemetry": Mock(return_value=telemetry_result)}):
            genai.Client.return_value.models.generate_content.return_value = sdk_response
            with self.client as client:
                response = client.post("/api/jarvis/query", json={"prompt": "Quel est l'etat du systeme et du CPU ?"})
                health = client.get("/api/health")
                status = client.get("/api/system/ai-status")

        self.assertEqual(200, response.status_code)
        data = response.json()
        self.assertEqual("success", data["status"])
        self.assertIn("CPU 12.5 %", data["reply"])
        self.assertEqual("get_system_telemetry", data["tools_used"][0]["tool"])
        self.assertFalse(data["model_offline"])
        self.assertTrue(data["online"])
        self.assertTrue(health.json()["ai_online"])
        self.assertFalse(health.json()["ai"]["all_providers_offline"])
        self.assertTrue(status.json()["online"])
        self.assertFalse(status.json()["model_offline"])

    def test_double_provider_failure_keeps_autonomous_solar_task(self) -> None:
        with patch("ai_brain.load_dotenv"), patch.dict(os.environ, {
            "AI_PROVIDER": "hybrid", "GEMINI_API_KEY": "test-gemini-key",
        }), patch("ai_brain.genai") as genai, \
                patch("ai_brain.urlopen", side_effect=URLError("Ollama offline")):
            gemini = genai.Client.return_value.models.generate_content
            gemini.side_effect = RuntimeError("Gemini unavailable")
            with self.client as client:
                response = client.post("/api/jarvis/query", json={"prompt": "dimensionne solaire 10 kwh"})
                status = client.get("/api/system/ai-status")
                health = client.get("/api/health")

        self.assertEqual(200, response.status_code)
        data = response.json()
        self.assertEqual("success", data["status"])
        self.assertTrue(data["model_offline"])
        self.assertTrue(data["all_providers_offline"])
        self.assertFalse(data["online"])
        self.assertEqual(200, status.status_code)
        self.assertTrue(status.json()["available"])
        self.assertFalse(status.json()["online"])
        self.assertTrue(status.json()["all_providers_offline"])
        self.assertTrue(status.json()["model_offline"])
        self.assertEqual(200, health.status_code)
        self.assertFalse(health.json()["ai_online"])
        self.assertTrue(data["autonomous_available"])
        self.assertIn("Dimensionnement Solaire", data["reply"])
        self.assertIn("Wc", data["reply"])
        gemini.assert_called_once()


if __name__ == "__main__":
    unittest.main()

