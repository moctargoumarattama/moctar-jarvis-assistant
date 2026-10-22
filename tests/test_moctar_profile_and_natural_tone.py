"""
Tests unitaires pour le profil utilisateur autonome de Moctar, le ton naturel (température 0.6,
absence de suffixe forcé 'Moctar.') et le pipelining de synthèse vocale (First-Sentence Streaming).
"""

import os
import unittest
import tempfile
import asyncio
from unittest.mock import patch

import assistant_memory
import voice_semantic_engine


class TestMoctarProfileAutonomousLearning(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_moctar_memory.db")
        self.mem = assistant_memory.AssistantMemory(db_path=self.db_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_default_profile_structure(self):
        profile = self.mem.get_moctar_profile()
        self.assertEqual(profile.get("name"), "Moctar")
        self.assertIn("Precision M4800", profile.get("hardware", ""))
        self.assertIn("Ubuntu", profile.get("hardware", ""))
        self.assertIn("Ligue 1", profile.get("favorite_competitions", []))
        self.assertIsInstance(profile.get("recent_topics"), list)
        self.assertEqual(profile.get("total_turns"), 0)

    def test_update_moctar_profile(self):
        updated = self.mem.update_moctar_profile({
            "preferred_editor": "Neovim",
            "known_preferences": {"theme": "sombre", "concise": True},
        })
        self.assertEqual(updated.get("preferred_editor"), "Neovim")
        self.assertEqual(updated.get("known_preferences", {}).get("theme"), "sombre")

        # Vérifier la persistance dans la base de données
        reloaded = self.mem.get_moctar_profile()
        self.assertEqual(reloaded.get("preferred_editor"), "Neovim")

    def test_record_conversation_turn_extracts_topics_and_increments(self):
        # Tour 1: Moctar parle de météo
        self.mem.record_conversation_turn_for_profile(
            "Quelle est la météo aujourd'hui à Casablanca ?",
            "Il fait 22 degrés à Casablanca sous un ciel dégagé."
        )
        p1 = self.mem.get_moctar_profile()
        self.assertEqual(p1.get("total_turns"), 1)
        topics1 = p1.get("recent_topics", [])
        self.assertIn("Météo", topics1)

        # Tour 2: Moctar mentionne du code Python et VS Code
        self.mem.record_conversation_turn_for_profile(
            "Analyse mon script Python dans VS Code s'il te plaît.",
            "Votre script Python est propre et bien documenté."
        )
        p2 = self.mem.get_moctar_profile()
        self.assertEqual(p2.get("total_turns"), 2)
        topics2 = p2.get("recent_topics", [])
        self.assertIn("Développement & VS Code", topics2)

    def test_moctar_profile_prompt_snippet(self):
        self.mem.update_moctar_profile({
            "recent_topics": ["Docker", "Météo"],
            "total_turns": 5
        })
        snippet = self.mem.get_moctar_profile_prompt_snippet()
        self.assertIn("Utilisateur : Moctar", snippet)
        self.assertIn("Precision M4800", snippet)
        self.assertIn("Docker, Météo", snippet)
        self.assertIn("5 tours de conversation", snippet)


class TestNaturalToneAndTemperature(unittest.TestCase):
    def test_temperature_is_natural_point_six(self):
        self.assertEqual(voice_semantic_engine.VOICE_FAST_TEMPERATURE, 0.6)
        self.assertEqual(voice_semantic_engine.VOICE_SEMANTIC_TEMPERATURE, 0.6)

    def test_system_prompt_does_not_force_mechanic_suffix(self):
        # Vérifie que le prompt système n'oblige pas à terminer mécaniquement par 'Moctar.'
        self.assertNotIn("Chaque réponse doit obligatoirement se terminer par 'Moctar.'", voice_semantic_engine.VOICE_SEMANTIC_SYSTEM_PROMPT)
        self.assertIn("naturel", voice_semantic_engine.VOICE_SEMANTIC_SYSTEM_PROMPT.lower())

    def test_format_reply_ending_does_not_force_moctar_suffix(self):
        # Les réponses naturelles et directes doivent être conservées telles quelles
        reply1 = "Le processeur est à 12%, tout tourne normalement."
        formatted1 = voice_semantic_engine._format_reply_ending(reply1)
        self.assertFalse(formatted1.endswith("Moctar."))
        self.assertEqual(formatted1, "Le processeur est à 12%, tout tourne normalement.")

        # Les questions de clarification doivent être préservées avec leur point d'interrogation
        reply2 = "Voulez-vous que je coupe le service ou que je le redémarre ?"
        formatted2 = voice_semantic_engine._format_reply_ending(reply2)
        self.assertTrue(formatted2.endswith("?"))
        self.assertEqual(formatted2, reply2)


class TestFirstSentenceStreamingTTS(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_tts_chunk_generation(self):
        import main

        mock_tts_counter = 0

        async def fake_tts_with_key(sentence, voice, rate=None, pitch=None):
            nonlocal mock_tts_counter
            mock_tts_counter += 1
            await asyncio.sleep(0.01)
            return f"/fake/path_{mock_tts_counter}.mp3", b"data", f"key_{mock_tts_counter}"

        long_text = "Le système est totalement opérationnel. Tous les capteurs de télémétrie sont au vert. Bonne journée !"

        with patch.object(main, "generate_cached_tts_with_key", side_effect=fake_tts_with_key):
            full_audio, chunks = await main.generate_cached_tts_chunks(long_text)

            # Vérifier qu'on a bien découpé en chunks de phrases
            self.assertIsNotNone(full_audio)
            self.assertIsInstance(chunks, list)
            self.assertGreaterEqual(len(chunks), 2)
            # Le premier chunk correspond à la première phrase
            self.assertTrue(chunks[0].startswith("/api/voice/tts/cache/key_"))


if __name__ == "__main__":
    unittest.main()
