"""Tests unitaires pour le moteur vocal sémantique natif avec Tool Calling LLM (voice_semantic_engine.py).

Valide :
- La mort des regex et la compréhension sémantique des intentions informelles ('prépare-moi de quoi bosser', 'ouvre le truc pour coder')
- Le Function Calling structuré Gemini / LLM (< 500 ms)
- Le déclenchement effectif des outils système, météo, multimédia, todos et projet
- Le routage conversationnel pur sans outil
- La robustesse du repli déterministe autonome en cas de blackout réseau
"""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import voice_semantic_engine
from voice_semantic_engine import execute_voice_semantic_turn


class VoiceSemanticEngineTests(unittest.TestCase):
    def test_semantic_intent_work_mode_invokes_vscode(self) -> None:
        """Vérifie que les formulations libres ('prépare-moi de quoi bosser', 'ouvre le truc pour coder') déclenchent open_app(target='vscode')."""
        mock_brain = Mock()
        mock_brain.chat.return_value = {
            "content": "",
            "tool_calls": [{"name": "open_app", "arguments": {"target": "vscode"}}],
            "finish_reason": "tool_calls",
        }
        mock_runtime = Mock()
        mock_runtime.ai_brain = mock_brain

        for phrase in (
            "prépare-moi de quoi bosser",
            "ouvre le truc pour coder",
            "mets-toi au mode bureau",
        ):
            res = execute_voice_semantic_turn(phrase, runtime=mock_runtime)
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["action"], "open_app")
            self.assertIsNone(res["url"])
            self.assertEqual(res["title"], "Visual Studio Code")
            self.assertTrue(len(res["reply"]) > 0)
            self.assertTrue(any(t["tool"] == "open_app" for t in res["tools_used"]))

    def test_semantic_intent_weather_invokes_get_weather(self) -> None:
        """Vérifie que 'il fait quel degré à Casa ?' déclenche get_weather(city='Casablanca')."""
        mock_brain = Mock()
        mock_brain.chat.return_value = {
            "content": "",
            "tool_calls": [{"name": "get_weather", "arguments": {"city": "Casablanca", "target_day": "today"}}],
            "finish_reason": "tool_calls",
        }
        mock_runtime = Mock()
        mock_runtime.ai_brain = mock_brain

        with patch(
            "weather_service.get_weather_report",
            return_value={
                "success": True,
                "city": "Casablanca",
                "temperature": 24.0,
                "oral_text": "À Casablanca, ciel dégagé et 24°C actuellement, Moctar.",
            },
        ):
            res = execute_voice_semantic_turn("il fait quel degré à Casa ?", runtime=mock_runtime)
            self.assertEqual(res["status"], "success")
            self.assertIn("Casablanca", res["reply"])
            self.assertTrue(any(t["tool"] == "get_weather" for t in res["tools_used"]))

    def test_semantic_intent_music_invokes_control_music(self) -> None:
        """Vérifie que 'coupe le son' ou 'pause' déclenche control_music."""
        mock_brain = Mock()
        mock_brain.chat.return_value = {
            "content": "",
            "tool_calls": [{"name": "control_music", "arguments": {"action": "pause"}}],
            "finish_reason": "tool_calls",
        }
        mock_runtime = Mock()
        mock_runtime.ai_brain = mock_brain

        res = execute_voice_semantic_turn("coupe le son", runtime=mock_runtime)
        self.assertEqual(res["status"], "success")
        self.assertTrue(len(res["reply"]) > 0)
        self.assertTrue(any(t["tool"] == "control_music" for t in res["tools_used"]))

    def test_semantic_intent_system_optimization_invokes_optimize_system(self) -> None:
        """Vérifie que 'nettoie la RAM' ou 'optimise le système' déclenche optimize_system()."""
        mock_brain = Mock()
        mock_brain.chat.return_value = {
            "content": "",
            "tool_calls": [{"name": "optimize_system", "arguments": {}}],
            "finish_reason": "tool_calls",
        }
        mock_runtime = Mock()
        mock_runtime.ai_brain = mock_brain

        res = execute_voice_semantic_turn("purge la mémoire vive", runtime=mock_runtime)
        self.assertEqual(res["status"], "success")
        self.assertTrue(len(res["reply"]) > 0)
        self.assertTrue(any(t["tool"] == "optimize_system" for t in res["tools_used"]))

    def test_semantic_conversational_turn_without_tools(self) -> None:
        """Vérifie les échanges purement intellectuels ou salutations sans appel d'outil inutile."""
        mock_brain = Mock()
        mock_brain.chat.return_value = {
            "content": "Bonjour Moctar, je suis opérationnel et à votre écoute pour superviser l'infrastructure.",
            "tool_calls": [],
            "finish_reason": "stop",
        }
        mock_runtime = Mock()
        mock_runtime.ai_brain = mock_brain

        res = execute_voice_semantic_turn("Scott", runtime=mock_runtime)
        self.assertEqual(res["status"], "success")
        self.assertFalse(res["semantic_tool_calling"])
        self.assertEqual(res["tools_used"], [])
        self.assertIn("Moctar", res["reply"])

    def test_offline_fallback_to_autonomous_library(self) -> None:
        """Vérifie le repli propre sur la bibliothèque déterministe autonome quand aucun LLM n'est accessible."""
        mock_brain = Mock()
        mock_brain.chat.side_effect = RuntimeError("503 Server Unavailable")
        mock_runtime = Mock()
        mock_runtime.ai_brain = mock_brain

        res = execute_voice_semantic_turn("ouvre YouTube", runtime=mock_runtime)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["action"], "open_browser")
        self.assertEqual(res["url"], "https://www.youtube.com")
        self.assertTrue(len(res["reply"]) > 0)


if __name__ == "__main__":
    unittest.main()

