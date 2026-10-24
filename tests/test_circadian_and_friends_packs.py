"""Tests unitaires pour le Pack Bienveillance & Rythme Circadien et le Pack Démo Devant Amis & Accueil Invités."""

from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import main
from assistant_memory import AssistantMemory
from proactive_companion import ProactiveCompanion


class TestCircadianAndFriendsPacks(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_memory.db"
        self.memory = AssistantMemory(db_path=self.db_path)
        self.companion = ProactiveCompanion()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    # =========================================================================
    # 1. GESTION DES AMIS ET INVITÉS (AssistantMemory)
    # =========================================================================

    def test_record_and_retrieve_known_friends(self) -> None:
        """Vérifie la mémorisation et la comptabilisation des visites des amis."""
        # Premier enregistrement
        friend_1 = self.memory.record_friend("Karim", relation="ami", notes="Ami ingénieur")
        self.assertEqual(friend_1["name"], "Karim")
        self.assertEqual(friend_1["visit_count"], 1)

        # Deuxième passage de Karim
        friend_1_again = self.memory.record_friend("karim")
        self.assertEqual(friend_1_again["visit_count"], 2)

        # Nouvel ami
        self.memory.record_friend("Youssef", relation="collègue")

        # Récupération individuelle
        k = self.memory.get_known_friend("karim")
        self.assertIsNotNone(k)
        self.assertEqual(k["name"], "Karim")
        self.assertEqual(k["visit_count"], 2)

        # Liste complète
        all_friends = self.memory.get_all_known_friends()
        self.assertEqual(len(all_friends), 2)
        names = [f["name"] for f in all_friends]
        self.assertIn("Karim", names)
        self.assertIn("Youssef", names)

    # =========================================================================
    # 2. RYTHME CIRCADIEN ET SALUTATIONS ÉPISODIQUES
    # =========================================================================

    def test_episodic_greeting_circadian_late_night(self) -> None:
        """Vérifie que la salutation épisodique prend en compte la fatigue nocturne."""
        self.memory.save_daily_episode(
            topic="l'optimisation du HUD 3D",
            summary="Travail sur Three.js et réactivité vocale",
            source="desktop",
        )
        # Nuit (23h)
        greeting_night = self.memory.build_episodic_greeting(hour=23)
        self.assertIsNotNone(greeting_night)
        self.assertIn("Encore debout Moctar", greeting_night)
        self.assertIn("l'optimisation du HUD 3D", greeting_night)
        self.assertIn("Pense à souffler un peu", greeting_night)

        # Matin (8h)
        greeting_morning = self.memory.build_episodic_greeting(hour=8)
        self.assertIsNotNone(greeting_morning)
        self.assertIn("On avançait sur", greeting_morning)

    # =========================================================================
    # 3. PROACTIVE COMPANION : BIENVEILLANCE CIRCADIENNE
    # =========================================================================

    def test_proactive_companion_circadian_wellness_trigger(self) -> None:
        """Vérifie le déclenchement de la bienveillance nocturne après une longue session."""
        self.companion._work_session_start = time.monotonic() - 3600.0  # 1h active
        self.companion._last_trigger_time = 0.0

        with patch("proactive_companion.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 23
            mock_dt.now.return_value.strftime = lambda fmt: "20261024"
            event = self.companion.evaluate_proactive_event()
            self.assertIsNotNone(event)
            self.assertEqual(event.get("category"), "circadian_wellness")
            self.assertIn("Moctar, il se fait tard dans la nuit", event.get("message", ""))
            self.assertIn("préserver ton sommeil", event.get("message", ""))

            # Dédoublonnage journalier : ne spamme pas la même nuit
            event_again = self.companion.evaluate_proactive_event()
            self.assertIsNone(event_again)

    # =========================================================================
    # 4. COMMANDES INSTANTANÉES LOCALES (PACK DÉMO & BIENVEILLANCE)
    # =========================================================================

    def test_instant_friend_intro_and_recognition(self) -> None:
        """Vérifie l'accueil personnalisé à la première rencontre et la reconnaissance au retour."""
        # 1. Première présentation
        res_first = main.handle_instant_local_command("Je te présente Karim", memory=self.memory)
        self.assertIsNotNone(res_first)
        self.assertIn("Enchanté Karim", res_first)
        self.assertIn("Ravi de faire ta connaissance", res_first)

        # 2. Deuxième passage : reconnaissance chaleureuse
        res_second = main.handle_instant_local_command("Dis bonjour à Karim", memory=self.memory)
        self.assertIsNotNone(res_second)
        self.assertIn("Ravi de te revoir Karim", res_second)
        self.assertIn("Toujours un plaisir de t'accueillir", res_second)

        # 3. Demande de la liste des amis
        res_list = main.handle_instant_local_command("Qui sont mes amis ?", memory=self.memory)
        self.assertIsNotNone(res_list)
        self.assertIn("Karim", res_list)

    def test_instant_circadian_and_caring_replies(self) -> None:
        """Vérifie les réponses bienveillantes pour le sommeil, les pauses et les encouragements."""
        # Bonne nuit / sommeil
        res_nuit = main.handle_instant_local_command("Bonne nuit")
        self.assertIsNotNone(res_nuit)
        self.assertIn("Bonne nuit Moctar", res_nuit)
        self.assertIn("Repose-toi bien", res_nuit)

        # Pause café
        res_pause = main.handle_instant_local_command("Je fais une pause")
        self.assertIsNotNone(res_pause)
        self.assertIn("Très bonne idée Moctar", res_pause)
        self.assertIn("souffle un peu", res_pause)

        # Comment ça va / Humeur
        res_forme = main.handle_instant_local_command("Comment tu vas ?")
        self.assertIsNotNone(res_forme)
        self.assertIn("Tout tourne comme une horloge Moctar", res_forme)

        # Remerciements fraternels
        res_merci = main.handle_instant_local_command("Merci mon ami")
        self.assertIsNotNone(res_merci)
        self.assertIn("Avec grand plaisir Moctar", res_merci)
        self.assertIn("sacrée équipe", res_merci)

    # =========================================================================
    # 5. DÉTECTION DIRECTE SANS MOT DE RÉVEIL (is_direct_voice_command)
    # =========================================================================

    def test_direct_voice_detection_for_new_companion_phrases(self) -> None:
        """Vérifie que les nouvelles phrases relationnelles sont reconnues directement."""
        self.assertTrue(main.is_direct_voice_command("Je te présente Moussa"))
        self.assertTrue(main.is_direct_voice_command("Dis bonjour à Karim"))
        self.assertTrue(main.is_direct_voice_command("Qui sont mes amis ?"))
        self.assertTrue(main.is_direct_voice_command("Bonne nuit"))
        self.assertTrue(main.is_direct_voice_command("Je vais dormir"))
        self.assertTrue(main.is_direct_voice_command("Pause café"))
        self.assertTrue(main.is_direct_voice_command("Comment tu vas"))
        self.assertTrue(main.is_direct_voice_command("Merci mon ami"))
        self.assertTrue(main.is_direct_voice_command("Merci frérot"))


if __name__ == "__main__":
    unittest.main()
