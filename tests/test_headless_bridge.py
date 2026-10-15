"""Tests unitaires pour JarvisHeadlessBridge."""

import tempfile
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, Mock, patch

import actions
from agent_core import AgentResult
from headless_bridge import JarvisHeadlessBridge, select_tools_for_prompt


def telemetry_result(cpu=12.5, memory=45.0, disk=60.0):
    return {
        "success": True,
        "action": "get_system_telemetry",
        "message": "Mesures de test.",
        "data": {
            "cpu_percent": cpu,
            "temperature_c": None,
            "memory": {"used_gb": 7.2, "total_gb": 16.0, "percent": memory},
            "disk": {"free_gb": 40.0, "total_gb": 100.0, "percent_used": disk},
        },
        "error": None,
    }


def hybrid_health(*, online=True):
    return {
        "provider": "hybrid",
        "mode": "gemini" if online else "local",
        "active_provider": "gemini" if online else "local",
        "online": online,
        "ollama_online": False,
        "gemini_online": online,
        "gemini_configured": True,
        "all_providers_offline": not online,
        "model_offline": not online,
        "model": "gemini-3.5-flash-lite",
        "base_url": "https://generativelanguage.googleapis.com",
    }


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
        with patch("ai_brain.get_ai_status", return_value={"online": True, "model": "qwen2.5:1.5b", "base_url": "http://localhost:11434"}):
            status = self.bridge.get_status()

        self.assertIn("status", status)
        self.assertIn("model", status)
        self.assertIn("base_url", status)
        self.assertIn("ollama_online", status)
        self.assertIn("tools_count", status)
        self.assertIn("memory", status)
        self.assertGreater(status["tools_count"], 10)
        self.assertTrue(status["ollama_online"])

    def test_gemini_status_reports_ready_when_ollama_is_offline(self):
        for provider in ("gemini", "hybrid"):
            with self.subTest(provider=provider), patch.object(self.bridge.brain, "get_health_status", return_value={
                "provider": provider,
                "mode": "gemini",
                "online": True,
                "ollama_online": False,
                "model": "gemini-2.0-flash",
                "base_url": "https://generativelanguage.googleapis.com",
            }):
                status = self.bridge.get_status()

            self.assertEqual("ready", status["status"])
            self.assertEqual(provider, status["provider"])
            self.assertEqual("gemini", status["mode"])
            self.assertEqual("gemini-2.0-flash", status["model"])
            self.assertEqual("https://generativelanguage.googleapis.com", status["base_url"])
            self.assertFalse(status["ollama_online"])
            self.assertFalse(status["model_offline"])

    def test_hybrid_status_exposes_cloud_health_when_ollama_is_offline(self):
        with patch.object(self.bridge.brain, "get_health_status", return_value=hybrid_health()) as health:
            status = self.bridge.get_status()

        health.assert_called_once_with()
        self.assertEqual("ready", status["status"])
        self.assertEqual("hybrid", status["provider"])
        self.assertEqual("gemini", status["active_provider"])
        self.assertTrue(status["online"])
        self.assertTrue(status["gemini_online"])
        self.assertTrue(status["gemini_configured"])
        self.assertFalse(status["ollama_online"])
        self.assertFalse(status["all_providers_offline"])
        self.assertFalse(status["model_offline"])

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

    def test_agent_result_is_unwrapped_without_collecting_unrelated_metrics(self):
        collect = Mock()
        with patch.dict(actions.TOOL_DISPATCHER, {"get_system_telemetry": collect}), \
                patch.object(self.bridge.agent, "run", return_value=AgentResult("Il est 15:30.")):
            result = self.bridge.execute_prompt("Quelle heure est-il ?")
        self.assertEqual("Il est 15:30.", result["reply"])
        collect.assert_not_called()

    def test_offline_ai_reports_unavailability_instead_of_an_unrelated_greeting(self):
        with patch.object(self.bridge.agent, "_call_llm", side_effect=TimeoutError("offline")):
            result = self.bridge.execute_prompt("Explique pourquoi les étoiles brillent")
        self.assertEqual("error", result["status"])
        self.assertIn("moteur IA est indisponible", result["reply"])
        self.assertNotIn("Bonjour", result["reply"])

    def test_offline_greeting_still_works_locally(self):
        with patch.object(self.bridge.agent, "_call_llm", side_effect=TimeoutError("offline")):
            result = self.bridge.execute_prompt("bonjour")
        self.assertEqual("success", result["status"])
        self.assertIn("Bonjour", result["reply"])

    def test_general_prompt_exposes_five_tools_and_status_words_select_telemetry(self):
        self.assertEqual(5, len(select_tools_for_prompt("Bonjour !")))
        for prompt in ("État global", "statut", "système", "infrastructure", "cpu", "ram"):
            with self.subTest(prompt=prompt):
                self.assertIn("get_system_telemetry", select_tools_for_prompt(prompt))

    def test_status_metrics_are_in_first_system_message_and_collected_once(self):
        measured = telemetry_result()
        collect = Mock(return_value=measured)
        original_timeout = self.bridge.brain.timeout
        reply = "[État Opérationnel]\nCPU 12.5 %, RAM 45 %, Disque 60 %.\n[Recommandations M.O.C.T.A.R]\nStatut VERT : surveiller la charge."
        with patch.dict(actions.TOOL_DISPATCHER, {"get_system_telemetry": collect}), \
                patch.object(self.bridge.agent, "_call_llm", return_value={"content": reply}) as llm:
            result = self.bridge.execute_prompt("Quel est l'état global du système et de l'infrastructure ?")

        collect.assert_called_once_with()
        self.assertIn("CPU 12.5 %", result["reply"])
        self.assertIn("RAM 7.2/16 Go (45 %)", result["reply"])
        self.assertIn("Statut VERT : surveiller la charge.", result["reply"])
        system_content = llm.call_args.args[0][0]["content"]
        self.assertIn(json.dumps(measured, ensure_ascii=False), system_content)
        self.assertEqual([], llm.call_args.kwargs["tools"])
        self.assertFalse(llm.call_args.kwargs["think"])
        self.assertEqual(measured["data"], result["tools_used"][0]["data"])
        self.assertEqual(1, len(result["tools_used"]))
        self.assertEqual(original_timeout, self.bridge.brain.timeout)
        self.assertEqual([reply], result["thought_chain"])

    def test_gemini_telemetry_synthesis_does_not_report_offline(self):
        reply = (
            "[État Opérationnel]\nMesures disponibles.\n"
            "[Recommandations M.O.C.T.A.R]\nSurveiller la charge."
        )
        with patch.dict(actions.TOOL_DISPATCHER, {"get_system_telemetry": Mock(return_value=telemetry_result())}), \
                patch.object(self.bridge.agent, "_call_llm", return_value={"content": reply}), \
                patch.object(self.bridge.brain, "get_health_status", return_value=hybrid_health()) as health, \
                patch("autonomous_library.execute_autonomous_task") as autonomous:
            result = self.bridge.execute_prompt("État du système")

        self.assertEqual("success", result["status"])
        self.assertIn("CPU 12.5 %", result["reply"])
        self.assertIn("Surveiller la charge.", result["reply"])
        self.assertEqual("gemini", result["active_provider"])
        self.assertTrue(result["online"])
        self.assertFalse(result["model_offline"])
        self.assertIsNone(result["model_offline_details"])
        health.assert_called_once_with(probe=False)
        autonomous.assert_not_called()

    def test_deterministic_telemetry_formatting_preserves_cloud_health(self):
        with patch.dict(actions.TOOL_DISPATCHER, {"get_system_telemetry": Mock(return_value=telemetry_result())}), \
                patch.object(self.bridge.agent, "_call_llm", return_value={"content": "Mesures disponibles."}), \
                patch.object(self.bridge.brain, "get_health_status", return_value=hybrid_health()):
            result = self.bridge.execute_prompt("État du système")

        self.assertIn("CPU 12.5 %", result["reply"])
        self.assertFalse(result["model_offline"])
        self.assertFalse(result["all_providers_offline"])

    def test_both_providers_unavailable_marks_autonomous_fallback_offline(self):
        with patch.object(self.bridge.agent, "_call_llm", side_effect=TimeoutError("Tous les fournisseurs sont indisponibles")), \
                patch.object(self.bridge.brain, "get_health_status", return_value=hybrid_health(online=False)):
            result = self.bridge.execute_prompt("Explique pourquoi les étoiles brillent")

        self.assertTrue(result["model_offline"])
        self.assertTrue(result["all_providers_offline"])
        self.assertFalse(result["gemini_online"])
        self.assertIsNotNone(result["model_offline_details"])

    def test_autonomous_response_keeps_operational_cloud_online(self):
        with patch.object(self.bridge.agent, "_call_llm", side_effect=TimeoutError("Budget de synthèse dépassé")), \
                patch.object(self.bridge.brain, "get_health_status", return_value=hybrid_health()):
            result = self.bridge.execute_prompt("dimensionne solaire 10 kwh")

        self.assertEqual("success", result["status"])
        self.assertIn("Dimensionnement Solaire", result["reply"])
        self.assertTrue(result["gemini_online"])
        self.assertFalse(result["model_offline"])
        self.assertIsNone(result["model_offline_details"])

    def test_deterministic_success_does_not_claim_a_new_provider_failure(self):
        with patch.object(self.bridge.agent, "run", return_value=AgentResult("Il est 15:30.")), \
                patch.object(self.bridge.brain, "get_health_status", return_value=hybrid_health(online=False)):
            result = self.bridge.execute_prompt("Quelle heure est-il ?")

        self.assertEqual("success", result["status"])
        self.assertFalse(result["model_offline"])
        self.assertIsNone(result["model_offline_details"])

    def test_tool_error_does_not_mark_operational_cloud_offline(self):
        with patch.object(self.bridge.agent, "run", side_effect=RuntimeError("Outil indisponible")), \
                patch.object(self.bridge.brain, "get_health_status", return_value=hybrid_health()), \
                patch("autonomous_library.execute_autonomous_task", return_value={"handled": False}):
            result = self.bridge.execute_prompt("Analyse cette demande")

        self.assertEqual("error", result["status"])
        self.assertTrue(result["gemini_online"])
        self.assertFalse(result["model_offline"])
        self.assertIsNone(result["model_offline_details"])

    def test_ollama_timeout_returns_measured_report_and_prioritized_advice(self):
        measured = telemetry_result(cpu=96.0, memory=92.0, disk=91.0)
        with patch.dict(actions.TOOL_DISPATCHER, {"get_system_telemetry": Mock(return_value=measured)}), \
                patch.object(self.bridge.agent, "_call_llm", side_effect=TimeoutError("CPU occupé")):
            result = self.bridge.execute_prompt("Quel est le statut du système ?")

        self.assertEqual("success", result["status"])
        for expected in ("[État Opérationnel]", "ROUGE", "CPU 96 %", "RAM 7.2/16 Go (92 %)",
                         "Disque 40/100 Go libres (91 % utilisés)", "[Recommandations M.O.C.T.A.R]",
                         "processus gourmands", "après sauvegarde", "ne sont pas mesurés"):
            self.assertIn(expected, result["reply"])
        self.assertNotIn("Bonjour monsieur", result["reply"])

    def test_missing_telemetry_does_not_fabricate_zero_metrics(self):
        with patch.dict(actions.TOOL_DISPATCHER, {"get_system_telemetry": None}), \
                patch.object(self.bridge.agent, "_call_llm", side_effect=TimeoutError()):
            result = self.bridge.execute_prompt("État du système")
        self.assertIn("ORANGE", result["reply"])
        self.assertIn("Télémétrie indisponible", result["reply"])
        self.assertNotIn("CPU 0", result["reply"])
        self.assertFalse(result["tools_used"][0]["success"])

    def test_unstructured_filler_is_replaced_by_local_measured_report(self):
        with patch.dict(actions.TOOL_DISPATCHER, {"get_system_telemetry": Mock(return_value=telemetry_result())}), \
                patch.object(self.bridge.agent, "_call_llm", return_value={"content": "Le système va bien."}):
            result = self.bridge.execute_prompt("État du système")
        self.assertIn("CPU 12.5 %", result["reply"])
        self.assertIn("[Recommandations M.O.C.T.A.R]", result["reply"])

    def test_status_inference_shares_a_bounded_timeout_across_retries(self):
        original_timeout = self.bridge.brain.timeout
        observed_timeouts = []

        def promise(*args, **kwargs):
            observed_timeouts.append(self.bridge.brain.timeout)
            return {"content": "Je vais vérifier le système."}

        with patch.dict(actions.TOOL_DISPATCHER, {"get_system_telemetry": Mock(return_value=telemetry_result())}), \
                patch.object(self.bridge.agent, "_call_llm", side_effect=promise) as llm, \
                patch("headless_bridge.time.monotonic", side_effect=[10.0, 10.8, 11.1]):
            result = self.bridge.execute_prompt("État du système")
        self.assertEqual(1, llm.call_count)
        self.assertAlmostEqual(0.2, observed_timeouts[0])
        self.assertEqual(original_timeout, self.bridge.brain.timeout)
        self.assertIn("CPU 12.5 %", result["reply"])

    def test_model_cannot_replace_measured_operational_values(self):
        invented_reply = (
            "[État Opérationnel]\nCPU 1 %, RAM 1 %, Disque 1 %. Infrastructure vérifiée.\n"
            "[Recommandations M.O.C.T.A.R]\nSurveiller les journaux."
        )
        with patch.dict(actions.TOOL_DISPATCHER, {"get_system_telemetry": Mock(return_value=telemetry_result())}), \
                patch.object(self.bridge.agent, "_call_llm", return_value={"content": invented_reply}):
            result = self.bridge.execute_prompt("État global du système")
        self.assertIn("CPU 12.5 %", result["reply"])
        self.assertIn("RAM 7.2/16 Go (45 %)", result["reply"])
        self.assertIn("Disque 40/100 Go libres (60 % utilisés)", result["reply"])
        self.assertNotIn("Infrastructure vérifiée", result["reply"])
        self.assertNotIn("CPU 1 %", result["reply"])
        self.assertIn("Surveiller les journaux.", result["reply"])

    def test_mixed_status_and_git_request_preserves_project_tools(self):
        with patch.dict(actions.TOOL_DISPATCHER, {"get_system_telemetry": Mock(return_value=telemetry_result())}), \
                patch.object(self.bridge.agent, "run", return_value=AgentResult("Statut Git consulté.")) as run:
            self.bridge.execute_prompt("Vérifie le système et le statut git du projet.")
        self.assertIn("git_status", run.call_args.kwargs["tool_names"])
        self.assertNotIn("get_system_telemetry", run.call_args.kwargs["tool_names"])
        self.assertTrue(run.call_args.kwargs["context"]["system_telemetry"]["success"])

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

    def test_public_history_excludes_whatsapp_before_limiting_and_preserves_private_storage(self):
        public_session = "session_web_public"
        private_session = "whatsapp:1:212770010264"
        self.bridge.memory.add_message(public_session, "user", "Message web public")
        self.bridge.memory.add_message(private_session, "user", "Question WhatsApp privée")
        self.bridge.memory.add_message(private_session, "assistant", "Réponse confidentielle")
        history = self.bridge.get_history(limit=1)
        self.assertEqual(1, len(history))
        self.assertEqual(history[0]["content"], "Message web public")
        self.assertEqual(history[0]["session_id"], public_session)
        self.assertEqual([], self.bridge.get_history(limit=20, session_id=private_session))
        with self.bridge.memory._get_connection() as connection:
            count = connection.execute("SELECT COUNT(*) FROM conversation_history WHERE session_id = ?", (private_session,)).fetchone()[0]
        self.assertEqual(2, count)

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
