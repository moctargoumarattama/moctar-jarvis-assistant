"""Tests pour l'Intelligence Poussée Nouvelle Génération de M.O.C.T.A.R :
1. Multimodal Vision de l'Écran (analyze_screen via Fast Voice Function Calling)
2. Apprentissage & Recherche de Faits Personnels (store_memory_fact, search_memory_facts)
3. Exécution Autonome en Arrière-Plan (BackgroundTaskManager, run_background_task)
4. Prosodie Vocale Émotionnelle & Dynamique (get_dynamic_tts_prosody)
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import actions
from background_task_manager import BackgroundTaskManager, run_background_task
import main
from voice_semantic_engine import VOICE_FAST_TOOLS, execute_voice_semantic_turn


class NextGenIntelligenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())

    def test_voice_fast_tools_contain_next_gen_capabilities(self) -> None:
        """Vérifie que analyze_screen, store_memory_fact, search_memory_facts et run_background_task sont disponibles en vocal direct."""
        tool_names = [t.get("function", {}).get("name") for t in VOICE_FAST_TOOLS]
        self.assertIn("analyze_screen", tool_names)
        self.assertIn("store_memory_fact", tool_names)
        self.assertIn("search_memory_facts", tool_names)
        self.assertIn("run_background_task", tool_names)

    def test_semantic_turn_with_analyze_screen(self) -> None:
        """Vérifie l'analyse d'écran par commande vocale ('Regarde mon écran')."""
        mock_brain = MagicMock()
        mock_brain.chat.side_effect = [
            {
                "content": "",
                "tool_calls": [{"name": "analyze_screen", "arguments": {"question": "Quel est le bug ?"}}],
                "finish_reason": "tool_calls",
            },
            {
                "content": "Je vois une erreur de syntaxe à la ligne 42 du fichier main.py.",
                "tool_calls": [],
                "finish_reason": "stop",
            },
        ]
        mock_runtime = MagicMock()
        mock_runtime.ai_brain = mock_brain
        mock_runtime.jarvis.memory.get_context_window.return_value = []

        with patch("voice_semantic_engine.execute_tool", return_value={"success": True, "message": "VS Code visible avec erreur SyntaxError."}):
            res = execute_voice_semantic_turn("Regarde mon écran, c'est quoi le bug ?", runtime=mock_runtime)

        self.assertEqual(res["status"], "success")
        self.assertTrue(res["semantic_tool_calling"])
        self.assertIn("ligne 42", res["reply"])
        self.assertTrue(res["reply"].endswith("Moctar."))

    def test_semantic_turn_with_store_and_search_memory_fact(self) -> None:
        """Vérifie la mémorisation et la recherche de faits personnels à la voix."""
        mock_brain = MagicMock()
        mock_brain.chat.side_effect = [
            {
                "content": "",
                "tool_calls": [{"name": "store_memory_fact", "arguments": {"category": "network", "key": "port_ssh", "fact": "Le port SSH est 2222"}}],
                "finish_reason": "tool_calls",
            },
            {
                "content": "C'est bien noté, j'ai mémorisé que ton port SSH est le 2222.",
                "tool_calls": [],
                "finish_reason": "stop",
            },
        ]
        mock_runtime = MagicMock()
        mock_runtime.ai_brain = mock_brain
        mock_runtime.jarvis.memory.get_context_window.return_value = []

        with patch("voice_semantic_engine.execute_tool", return_value={"success": True, "message": "Fait port_ssh enregistré."}):
            res = execute_voice_semantic_turn("Retiens que mon port SSH est le 2222", runtime=mock_runtime)

        self.assertEqual(res["status"], "success")
        self.assertIn("2222", res["reply"])
        self.assertTrue(res["reply"].endswith("Moctar."))

    def test_background_task_manager_execution(self) -> None:
        """Vérifie le lancement asynchrone non-bloquant d'une commande en tâche de fond."""
        mgr = BackgroundTaskManager.get_instance()
        res = mgr.start_task(command="echo 'Test OK'", description="Vérification unitaire")
        self.assertTrue(res["success"])
        self.assertIn(res["status"], ("running", "completed"))
        tid = res["task_id"]

        # Attendre la fin de la tâche (ultra rapide)
        time.sleep(0.4)
        task = mgr.get_task(tid)
        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "success")
        self.assertEqual(task["exit_code"], 0)
        self.assertIn("Test OK", task["output"])

    def test_dynamic_tts_prosody_modulation(self) -> None:
        """Vérifie que la prosodie s'adapte au moment de la journée et au contenu."""
        # Nuit tardive : ton doux et calme
        rate_night, pitch_night = main.get_dynamic_tts_prosody("bonsoir", hour=23)
        self.assertEqual(rate_night, "+6%")
        self.assertEqual(pitch_night, "-1Hz")

        # Matin : ton alerte et énergique
        rate_morning, pitch_morning = main.get_dynamic_tts_prosody("bonjour", hour=8)
        self.assertEqual(rate_morning, "+22%")
        self.assertEqual(pitch_morning, "+3Hz")

        # Alerte ou urgence
        rate_alert, _ = main.get_dynamic_tts_prosody("Alerte système critique", hour=14)
        self.assertEqual(rate_alert, "+22%")

    def test_background_task_fast_failure_detection(self) -> None:
        """Vérifie que les commandes échouant immédiatement (code != 0) sont détectées sans halluciner."""
        mgr = BackgroundTaskManager.get_instance()
        # Commande qui échoue immédiatement avec code 128
        res = mgr.start_task(command='python -c "import sys; sys.exit(128)"', description="Commit test")
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["exit_code"], 128)
        self.assertIn("Échec immédiat", res["message"])

    def test_whisper_video_hallucinations_purged(self) -> None:
        """Vérifie le rejet des hallucinations YouTube de Whisper sur le bruit du ventilateur/silence."""
        import voice_transcribe
        for hallu in (
            "Merci d'avoir regardé cette vidéo !",
            "merci d'avoir suivi cette vidéo",
            "Merci de votre attention.",
            "À bientôt pour une nouvelle vidéo !",
            "Bon visionnage à tous.",
        ):
            filtered = voice_transcribe.filter_whisper_hallucinations(hallu)
            self.assertEqual(filtered, "", f"L'hallucination '{hallu}' aurait dû être filtrée !")

    def test_spoken_telemetry_clean_and_friendly(self) -> None:
        """Vérifie que l'état matériel est formulé de manière vivante et que clean_tts_text purge les en-têtes bureaucratiques."""
        import autonomous_library
        res = autonomous_library.execute_autonomous_task("Quel est l'état du système ?")
        self.assertTrue(res["handled"])
        self.assertNotIn("[État Opérationnel]", res["reply"])
        self.assertNotIn("[Recommandations M.O.C.T.A.R]", res["reply"])
        self.assertIn("vert Moctar", res["reply"])

        # Vérification du nettoyage TTS
        bureaucratic_text = "[État Opérationnel]\nStatut matériel VERT. CPU 2 %.\n[Recommandations M.O.C.T.A.R]\n1. Conserver une marge."
        cleaned = main.clean_tts_text(bureaucratic_text)
        self.assertNotIn("État Opérationnel", cleaned)
        self.assertNotIn("Recommandations", cleaned)

    def test_network_status_companion_reply(self) -> None:
        """Vérifie que 'Quel est l'état du réseau ?' déclenche un contrôle de connectivité fluide."""
        import autonomous_library
        res = autonomous_library.execute_autonomous_task("Quel est l'état du réseau ?")
        self.assertTrue(res["handled"])
        self.assertEqual(res["task_category"], "network")
        self.assertIn("réseau", res["reply"].lower())
        self.assertNotIn("[État Opérationnel]", res["reply"])

    def test_no_duplicate_moctar_in_reply(self) -> None:
        """Vérifie que 'Moctar' n'est jamais répété deux fois si déjà présent dans la phrase."""
        from voice_semantic_engine import _format_reply_ending
        reply1 = _format_reply_ending("Oui Moctar, je m'en occupe.")
        self.assertEqual(reply1.count("Moctar"), 1)
        self.assertFalse(reply1.endswith("Moctar. Moctar."))

        reply2 = _format_reply_ending("Tout est prêt.")
        self.assertTrue(reply2.endswith("Moctar."))


if __name__ == "__main__":
    unittest.main()
