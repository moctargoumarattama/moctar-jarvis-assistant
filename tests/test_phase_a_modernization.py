"""Tests unitaires pour la Phase A : Modernisation Futuriste, Contrôle Média 0ms, Mode Bureau & Correctif Groq."""

import os
import unittest
from unittest.mock import MagicMock, patch

import actions.media_actions as media_actions
import actions.workspace_layout as workspace_layout
import ai_brain
import autonomous_library
import config
import main
import weather_service


class TestPhaseAModernization(unittest.TestCase):
    """Validation rigoureuse des 4 chantiers de la Phase A."""

    # -------------------------------------------------------------------------
    # 1. Correctif Groq (404), Auto-découverte & Identité Vocale Futuriste
    # -------------------------------------------------------------------------
    def test_groq_models_priority_order(self):
        """Vérifie la chaîne de priorité des modèles Groq de production."""
        expected = [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "llama-3.1-70b-versatile",
            "llama3-70b-8192",
        ]
        self.assertEqual(ai_brain.GROQ_MODELS_PRIORITY, expected)
        self.assertEqual(ai_brain.DEFAULT_PREFERRED_GROQ_MODELS[:4], expected)

    def test_groq_auto_discovery_on_refresh(self):
        """Vérifie que l'auto-découverte interroge l'API Groq et ordonne les modèles approuvés."""
        fake_api_response = {
            "data": [
                {"id": "whisper-large-v3"},
                {"id": "llama-guard-3-8b"},
                {"id": "llama-3.3-70b-versatile"},
                {"id": "llama-3.1-8b-instant"},
                {"id": "qwen-2.5-32b"},
            ]
        }

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = str(fake_api_response).replace("'", '"').encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            discovered = ai_brain.get_available_groq_chat_models("fake_key_123", force_refresh=True)
            self.assertIn("llama-3.3-70b-versatile", discovered)
            self.assertIn("llama-3.1-8b-instant", discovered)
            self.assertIn("qwen-2.5-32b", discovered)
            self.assertNotIn("whisper-large-v3", discovered)
            self.assertNotIn("llama-guard-3-8b", discovered)

    def test_voice_prompt_and_moctar_ending(self):
        """Vérifie que le prompt système vocal est calibré et que la réponse se termine par 'Moctar.'"""
        self.assertIn("cybernétique", ai_brain.MOCTAR_SYSTEM_PROMPT)
        self.assertIn("NOC", ai_brain.MOCTAR_SYSTEM_PROMPT)
        self.assertTrue(ai_brain.MOCTAR_SYSTEM_PROMPT.strip().endswith("Moctar.'") or ai_brain.MOCTAR_SYSTEM_PROMPT.strip().endswith("Moctar.\""))

        self.assertEqual(ai_brain._ensure_moctar_ending("Tout est sous contrôle"), "Tout est sous contrôle, Moctar.")
        self.assertEqual(ai_brain._ensure_moctar_ending("Oui Moctar"), "Oui Moctar.")
        self.assertEqual(ai_brain._ensure_moctar_ending("Affirmatif Moctar."), "Affirmatif Moctar.")

    def test_groq_404_fallback_to_gemini(self):
        """Vérifie que lors d'un échec Groq (404), le cerveau bascule proprement sur Gemini."""
        mock_brain = MagicMock()
        mock_brain.chat.return_value = {"content": "Systèmes opérationnels, analyse achevée"}

        mock_runtime = MagicMock()
        mock_runtime.ai_brain = mock_brain
        mock_runtime.settings.groq_api_key = "invalid_key_trigger_fail"

        # Simuler un échec 404 HTTP de Groq
        mock_post = MagicMock()
        mock_post.return_value.status_code = 404
        mock_post.return_value.text = '{"error": {"message": "model_not_found", "code": 404}}'

        with patch("requests.post", mock_post):
            with patch.dict(os.environ, {"GROQ_API_KEY": "fake_key"}, clear=False):
                reply = ai_brain.voice_fast_llm_reply(
                    prompt="État général ?",
                    runtime=mock_runtime,
                )
                self.assertTrue(reply.endswith("Moctar."))
                self.assertIn("Systèmes opérationnels", reply)

    # -------------------------------------------------------------------------
    # 2. Contrôle Média Universel en 0 ms
    # -------------------------------------------------------------------------
    def test_media_actions_playback_commands(self):
        """Valide l'exécution et les réponses orales des actions de lecture."""
        with patch("subprocess.run") as mock_sub:
            mock_sub.return_value.returncode = 0

            res_pause = media_actions.control_playback("pause")
            self.assertTrue(res_pause["success"])
            self.assertEqual(res_pause["message"], "Lecture mise en pause, Moctar.")

            res_play = media_actions.control_playback("play")
            self.assertTrue(res_play["success"])
            self.assertEqual(res_play["message"], "Reprise de la lecture, Moctar.")

            res_next = media_actions.control_playback("next")
            self.assertTrue(res_next["success"])
            self.assertEqual(res_next["message"], "Piste suivante activée, Moctar.")

            res_prev = media_actions.control_playback("previous")
            self.assertTrue(res_prev["success"])
            self.assertEqual(res_prev["message"], "Retour à la piste précédente, Moctar.")

    def test_media_actions_volume_adjustment(self):
        """Valide l'ajustement du volume système (up, down, mute, set)."""
        with patch("subprocess.run") as mock_sub:
            mock_sub.return_value.returncode = 0

            res_up = media_actions.adjust_system_volume("up")
            self.assertTrue(res_up["success"])
            self.assertEqual(res_up["message"], "Niveau audio ajusté, Moctar.")

            res_down = media_actions.adjust_system_volume("down")
            self.assertTrue(res_down["success"])
            self.assertEqual(res_down["message"], "Niveau audio ajusté, Moctar.")

            res_mute = media_actions.adjust_system_volume("mute")
            self.assertTrue(res_mute["success"])
            self.assertEqual(res_mute["message"], "Niveau audio ajusté, Moctar.")

            res_set = media_actions.adjust_system_volume("set", percent=40)
            self.assertTrue(res_set["success"])
            self.assertEqual(res_set["message"], "Niveau audio ajusté, Moctar.")

    def test_media_actions_launch_playlist(self):
        """Valide le lancement de la playlist Spotify / Music."""
        with patch("subprocess.Popen"):
            res = media_actions.launch_playlist()
            self.assertTrue(res["success"])
            self.assertEqual(res["message"], "Initialisation du flux audio. Votre playlist est lancée, Moctar.")

    def test_autonomous_library_media_deterministic_routing(self):
        """Valide les déclencheurs vocaux 0 ms dans autonomous_library."""
        with patch("subprocess.run") as mock_sub:
            mock_sub.return_value.returncode = 0

            # Pause
            for phrase in ["Mets pause", "Pause", "Mets en pause", "Stop la musique"]:
                res = autonomous_library.execute_autonomous_task(phrase)
                self.assertTrue(res["handled"])
                self.assertEqual(res["task_category"], "media")
                self.assertEqual(res["reply"], "Lecture mise en pause, Moctar.")

            # Play
            for phrase in ["Reprends la lecture", "Remets la musique", "Lecture"]:
                res = autonomous_library.execute_autonomous_task(phrase)
                self.assertTrue(res["handled"])
                self.assertEqual(res["task_category"], "media")
                self.assertEqual(res["reply"], "Reprise de la lecture, Moctar.")

            # Next
            for phrase in ["Chanson suivante", "Piste suivante", "Musique suivante"]:
                res = autonomous_library.execute_autonomous_task(phrase)
                self.assertTrue(res["handled"])
                self.assertEqual(res["task_category"], "media")
                self.assertEqual(res["reply"], "Piste suivante activée, Moctar.")

            # Previous
            for phrase in ["Chanson précédente", "Piste précédente"]:
                res = autonomous_library.execute_autonomous_task(phrase)
                self.assertTrue(res["handled"])
                self.assertEqual(res["task_category"], "media")
                self.assertEqual(res["reply"], "Retour à la piste précédente, Moctar.")

            # Playlist
            for phrase in ["Mets de la musique", "Lance ma playlist", "Lance Spotify"]:
                res = autonomous_library.execute_autonomous_task(phrase)
                self.assertTrue(res["handled"])
                self.assertEqual(res["task_category"], "media")
                self.assertEqual(res["reply"], "Initialisation du flux audio. Votre playlist est lancée, Moctar.")

            # Volume
            for phrase in ["Monte le son", "Baisse le son", "Coupe le son", "Mets le volume à 35%", "Diminue le volume, diminue le volume.", "Augmente le volume"]:
                res = autonomous_library.execute_autonomous_task(phrase)
                self.assertTrue(res["handled"])
                self.assertEqual(res["task_category"], "system_tools")
                self.assertEqual(res["reply"], "Niveau audio ajusté, Moctar.")

    def test_voice_direct_commands_media_and_volume(self):
        """Vérifie que les ordres directs multimédias et volume ne sont jamais ignorés hors session."""
        phrases = [
            "Mets la musique.",
            "Diminue le volume, diminue le volume.",
            "Pause.",
            "Mets YouTube en pause",
            "Bien, on pause.",
            "Augmente le son",
            "Coupe le son",
            "Piste suivante",
            "Lance Spotify",
        ]
        for phrase in phrases:
            self.assertTrue(
                main.is_direct_voice_command(phrase),
                f"La phrase '{phrase}' devrait être acceptée en commande directe sans mot-clé.",
            )

    def test_media_actions_youtube_targeting(self):
        """Vérifie que le contrôle média cible spécifiquement les fenêtres YouTube via la touche 'k'."""
        with patch("shutil.which", return_value="/usr/bin/xdotool"):
            with patch("subprocess.run") as mock_sub:
                # Simuler la détection d'une fenêtre YouTube
                def fake_run(cmd, *args, **kwargs):
                    mock_res = MagicMock()
                    mock_res.returncode = 0
                    if "search" in cmd:
                        mock_res.stdout = "1234567\n"
                    else:
                        mock_res.stdout = ""
                    return mock_res

                mock_sub.side_effect = fake_run

                res = media_actions.control_playback("pause")
                self.assertTrue(res["success"])
                self.assertEqual(res["message"], "Lecture mise en pause, Moctar.")

                # Vérifier qu'une commande xdotool key --window 1234567 k a été envoyée
                calls = [call_args[0][0] for call_args in mock_sub.call_args_list]
                yt_key_calls = [c for c in calls if "key" in c and "1234567" in c and "k" in c]
                self.assertTrue(len(yt_key_calls) > 0, "La touche 'k' doit être envoyée à la fenêtre YouTube détectée.")

    # -------------------------------------------------------------------------
    # 3. Positionnement Géométrique des Fenêtres — Mode Bureau
    # -------------------------------------------------------------------------
    def test_calculate_workspace_layout_1920x1080(self):
        """Vérifie le découpage géométrique exact 2/3 - 1/3 sur résolution 1920x1080."""
        layout = workspace_layout.calculate_workspace_layout(1920, 1080)
        # Top-Left (Media / Spotify) : W/3 = 640, 2H/3 = 720, (0, 0)
        self.assertEqual(layout["top_left"], (0, 0, 640, 720))
        # Top-Right (VS Code / Terminal) : 2W/3 = 1280, 2H/3 = 720, (W/3=640, 0)
        self.assertEqual(layout["top_right"], (640, 0, 1280, 720))
        # Bottom (Dashboard Chrome) : W = 1920, H/3 = 360, (0, 2H/3=720)
        self.assertEqual(layout["bottom"], (0, 720, 1920, 360))

    def test_screen_resolution_resilience(self):
        """Vérifie que la détection d'écran retourne un tuple (W, H) valide sans jamais lever d'exception."""
        w, h = workspace_layout.get_screen_resolution()
        self.assertIsInstance(w, int)
        self.assertIsInstance(h, int)
        self.assertGreaterEqual(w, 640)
        self.assertGreaterEqual(h, 480)

    def test_autonomous_library_mode_bureau_routing(self):
        """Vérifie l'activation déterministe du mode bureau géométrique."""
        for phrase in ["Mode bureau", "Agencement bureau", "Mode travail", "Disposition travail"]:
            res = autonomous_library.execute_autonomous_task(phrase)
            self.assertTrue(res["handled"])
            self.assertEqual(res["task_category"], "workspace_layout")
            self.assertEqual(res["reply"], "Agencement géométrique du poste de travail appliqué, Moctar.")

    # -------------------------------------------------------------------------
    # 4. Résolution de Collision Climat vs Tâches
    # -------------------------------------------------------------------------
    def test_weather_vs_todos_collision_resolution(self):
        """Vérifie qu'une requête climatique avec lieu n'active jamais la liste des tâches (todos)."""
        complex_queries = [
            "Le programme du Maroc, il fait quel degré ?",
            "Le programme du Maroc, quelle température ?",
            "Le programme en France, il fait chaud ou froid ?",
            "C'est quoi le programme au Maroc, il fait combien de degrés ?",
            "Dis-moi le programme de météo au Maroc",
        ]

        for q in complex_queries:
            res = autonomous_library.execute_autonomous_task(q)
            self.assertTrue(res["handled"], f"Échec de traitement pour: {q}")
            self.assertEqual(
                res["task_category"],
                "weather",
                f"Collision détectée : '{q}' routé vers '{res.get('task_category')}' au lieu de 'weather'",
            )
            self.assertNotIn("Vos tâches en cours", res["reply"])

    def test_pure_todo_query_routes_to_productivity(self):
        """Vérifie que les vraies requêtes de to-dos / programme du jour restent intactes."""
        todo_queries = [
            "Quel est mon programme du jour ?",
            "Liste mes tâches",
            "Liste todo",
            "Qu'est-ce que j'ai à faire ?",
        ]

        for q in todo_queries:
            res = autonomous_library.execute_autonomous_task(q)
            self.assertTrue(res["handled"])
            self.assertEqual(res["task_category"], "productivity")
            self.assertIn("tâches en cours", res["reply"])


if __name__ == "__main__":
    unittest.main()
