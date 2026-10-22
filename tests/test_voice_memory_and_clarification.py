"""Tests unitaires pour la correction de la double mémoire et la demande de clarification/répétition.

Vérifie :
1. L'idempotence et l'élimination stricte des doublons dans AssistantMemory (anti-doublon < 15s).
2. La déduplication propre de la fenêtre de contexte glissante (get_context_window).
3. La commande locale de répétition ("Répète ce que tu viens de dire", "Tu as dit quoi ?").
4. La détection et le traitement des phrases inintelligibles / non-sens ("Sur l'autre toi, j'ai chiqué pour toi", "du mois").
5. Le fait que M.O.C.T.A.R demande poliment de répéter sans jamais halluciner d'outils au hasard.
"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from assistant_memory import AssistantMemory
import dynamic_attention_engine
import main
import voice_semantic_engine


class TestVoiceMemoryAndClarification(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_memory.db"
        self.memory = AssistantMemory(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Double Mémoire & Idempotence
    # -------------------------------------------------------------------------
    def test_double_memory_prevention_in_session(self):
        """Vérifie que l'enregistrement consécutif identique n'engendre aucun doublon."""
        sess = "session_voice_test"
        # Simulation d'un double appel user puis assistant (comme l'ancien bug main.py + voice_semantic_engine.py)
        self.memory.add_message(sess, "user", "Mets la musique.")
        self.memory.add_message(sess, "user", "Mets la musique.")
        self.memory.add_message(sess, "assistant", "Musique lancée, Moctar.")
        self.memory.add_message(sess, "assistant", "Musique lancée, Moctar.")

        window = self.memory.get_context_window(sess, max_turns=12)
        self.assertEqual(2, len(window))
        self.assertEqual("user", window[0]["role"])
        self.assertEqual("Mets la musique.", window[0]["content"])
        self.assertEqual("assistant", window[1]["role"])
        self.assertEqual("Musique lancée, Moctar.", window[1]["content"])

    def test_consecutive_duplicates_collapsed_in_context_window(self):
        """Vérifie que même si des doublons historiques existaient dans la base, la fenêtre de contexte les élimine."""
        sess = "session_legacy_duplicates"
        # Insertion directe simulant d'anciens doublons
        with self.memory._get_connection() as conn:
            conn.execute(
                "INSERT INTO conversation_history (session_id, timestamp, role, content) VALUES (?, ?, ?, ?)",
                (sess, "2026-10-20T10:00:00", "user", "Ancienne commande"),
            )
            conn.execute(
                "INSERT INTO conversation_history (session_id, timestamp, role, content) VALUES (?, ?, ?, ?)",
                (sess, "2026-10-20T10:00:01", "user", "Ancienne commande"),
            )
            conn.execute(
                "INSERT INTO conversation_history (session_id, timestamp, role, content) VALUES (?, ?, ?, ?)",
                (sess, "2026-10-20T10:00:02", "assistant", "Ancienne réponse, Moctar."),
            )
            conn.execute(
                "INSERT INTO conversation_history (session_id, timestamp, role, content) VALUES (?, ?, ?, ?)",
                (sess, "2026-10-20T10:00:03", "assistant", "Ancienne réponse, Moctar."),
            )
            conn.commit()

        window = self.memory.get_context_window(sess, max_turns=12)
        self.assertEqual(2, len(window))
        self.assertEqual("Ancienne commande", window[0]["content"])
        self.assertEqual("Ancienne réponse, Moctar.", window[1]["content"])

    # -------------------------------------------------------------------------
    # 2. Répétition à la Demande ("Répète ce que tu viens de dire")
    # -------------------------------------------------------------------------
    def test_handle_instant_local_command_repeat_phrase(self):
        """Vérifie que Moctar peut demander de répéter la dernière phrase prononcée."""
        sess = "session_repeat"
        self.memory.add_message(sess, "user", "Résultat du match ?")
        self.memory.add_message(sess, "assistant", "Le Real Madrid mène 2 à 1 contre le FC Barcelone, Moctar.")

        for phrase in (
            "répète",
            "repete",
            "répète ce que tu viens de dire",
            "tu as dit quoi ?",
            "qu'est-ce que tu as dit ?",
            "peux-tu répéter ?",
            "j'ai pas entendu",
        ):
            reply = main.handle_instant_local_command(phrase, memory=self.memory)
            self.assertIsNotNone(reply, f"Échec pour '{phrase}'")
            self.assertIn("Le Real Madrid mène 2 à 1 contre le FC Barcelone, Moctar.", reply)
            self.assertTrue(reply.startswith("Je disais :"))

    def test_handle_instant_local_command_repeat_empty(self):
        """Vérifie le retour élégant si rien n'a encore été dit."""
        empty_mem = AssistantMemory(db_path=Path(self.temp_dir.name) / "empty.db")
        reply = main.handle_instant_local_command("répète", memory=empty_mem)
        self.assertIsNotNone(reply)
        self.assertIn("Je n'avais encore rien dit de particulier", reply)
        self.assertTrue(reply.endswith("Moctar."))

    # -------------------------------------------------------------------------
    # 3. Détection du Non-sens et Demande Polie de Répétition
    # -------------------------------------------------------------------------
    def test_nonsensical_detection(self):
        """Vérifie que les artefacts et phrases sans queue ni tête sont interceptés."""
        test_cases = [
            "Sur l'autre toi, j'ai chiqué pour toi.",
            "Sur l'autre toi",
            "J'ai chiqué pour toi",
            "Je sais qui pour toi.",
            "Du mois,",
            "du mois",
            "Merci d'avoir regardé cette vidéo !",
            "...",
            "Sous-titres réalisés par la communauté",
        ]
        for phrase in test_cases:
            self.assertTrue(
                dynamic_attention_engine.is_incomprehensible_or_nonsensical(phrase),
                f"La phrase '{phrase}' aurait dû être classée inintelligible.",
            )

        # Les requêtes légitimes doivent rester valides
        valid_cases = [
            "Mets de la musique",
            "Ouvre Chrome",
            "Quel est le résultat de la Ligue 1 ?",
            "Donne-moi l'état de la mémoire et du processeur",
            "Bonjour Moctar",
            "On va passer à ceci",
        ]
        for phrase in valid_cases:
            self.assertFalse(
                dynamic_attention_engine.is_incomprehensible_or_nonsensical(phrase),
                f"Faux positif sur commande valide : '{phrase}'",
            )

    def test_execute_voice_semantic_turn_asks_repeat_on_gibberish(self):
        """Vérifie que execute_voice_semantic_turn retourne immédiatement une demande de clarification sans outils."""
        res = voice_semantic_engine.execute_voice_semantic_turn("Sur l'autre toi, j'ai chiqué pour toi.")
        self.assertEqual("success", res.get("status"))
        self.assertEqual("Pardon Moctar, je n'ai pas bien compris votre phrase. Pouvez-vous répéter ?", res.get("reply"))
        self.assertEqual([], res.get("tools_used"))
        self.assertIsNone(res.get("action"))
        self.assertTrue(res.get("ask_repeat"))


if __name__ == "__main__":
    unittest.main()
