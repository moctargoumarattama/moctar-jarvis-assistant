"""
Tests pour la reconnaissance vocale de Moctar, l'isolation/suppression de l'audio joué par le PC
et le branchement direct des initiatives proactives dans la boucle vocale.
"""

import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

import voice_transcribe
import proactive_companion
import native_voice_daemon
import main
from main import create_app


class TestPcAudioIsolationAndHallucinationFiltering(unittest.TestCase):
    def test_filter_youtube_and_video_subtitles(self):
        """Vérifie que les hallucinations de Whisper issues de vidéos YouTube/PC sont ignorées."""
        test_cases = [
            "Merci d'avoir regardé cette vidéo !",
            "merci d avoir regarde cette video",
            "Abonnez-vous à la chaîne et likez !",
            "Sous-titres réalisés par la communauté",
            "N'hésitez pas à vous abonner pour d'autres vidéos",
            "[Musique]",
            "(musique)",
            "[Applaudissements]",
        ]
        for phrase in test_cases:
            filtered = voice_transcribe.filter_whisper_hallucinations(phrase)
            self.assertEqual(
                filtered,
                "",
                f"La phrase '{phrase}' aurait dû être filtrée comme bruit/audio PC mais a donné '{filtered}'",
            )

    def test_genuine_user_commands_are_preserved(self):
        """Vérifie que les véritables ordres vocaux de Moctar ne sont pas filtrés."""
        genuine = [
            "Quelle est la météo à Casablanca ?",
            "Relance le service Docker",
            "Vérifie la RAM et l'état des disques",
            "Mets la musique en pause",
            "Passe sur la vue globe 3D",
        ]
        for phrase in genuine:
            filtered = voice_transcribe.filter_whisper_hallucinations(phrase)
            self.assertEqual(filtered, phrase)

    def test_linux_aec_and_playback_detection_helpers(self):
        """Vérifie que les helpers de détection d'audio PC et d'annulation d'écho existent et s'exécutent."""
        self.assertTrue(callable(native_voice_daemon.is_pc_audio_playing))
        self.assertTrue(callable(native_voice_daemon.setup_linux_acoustic_echo_cancellation))
        # Sur un environnement non-linux ou sans pactl, doit retourner False sans planter
        self.assertIsInstance(native_voice_daemon.is_pc_audio_playing(), bool)


class TestProactiveInitiativesInVoiceLoop(unittest.TestCase):
    def setUp(self):
        self.companion = proactive_companion.ProactiveCompanion.get_instance()
        # Reset l'état du compagnon
        self.companion._pending_suggestion = None
        self.companion._last_trigger_time = 0.0
        self.companion._dnd_until = 0.0

    def test_register_and_pop_custom_initiative(self):
        """Vérifie l'enregistrement et la consommation d'une initiative pour la boucle vocale."""
        self.companion.register_initiative(
            "Moctar, la synchronisation du code est terminée avec succès.",
            category="git_sync",
            priority="normal",
        )
        event = self.companion.evaluate_proactive_event(None)
        self.assertIsNotNone(event)
        self.assertEqual(event.get("category"), "git_sync")
        self.assertIn("synchronisation", event.get("message", ""))

        # Une fois consommée, elle ne doit pas se répéter
        second_eval = self.companion.evaluate_proactive_event(None)
        self.assertIsNone(second_eval)

    def test_system_resource_initiative(self):
        """Vérifie la génération d'une initiative proactive en cas de saturation de la RAM."""
        fake_mem = MagicMock()
        fake_mem.percent = 92.5
        with patch("psutil.virtual_memory", return_value=fake_mem):
            res_event = self.companion.check_system_resources()
            self.assertIsNotNone(res_event)
            self.assertIn("mémoire vive", res_event.get("message", ""))
            self.assertIn("92%", res_event.get("message", ""))


class TestVoiceInteractPcAudioGatingAndChainedInitiatives(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.patch_col_start = patch("main.collector.Collector.start", new=AsyncMock())
        self.patch_col_close = patch("main.collector.Collector.close", new=AsyncMock())
        self.patch_col_start.start()
        self.patch_col_close.start()

        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()

        self.companion = proactive_companion.ProactiveCompanion.get_instance()
        self.companion._pending_suggestion = None
        self.companion._last_trigger_time = 0.0

    def tearDown(self):
        try:
            self.client_context.__exit__(None, None, None)
        finally:
            self.patch_col_start.stop()
            self.patch_col_close.stop()

    @patch("main.transcribe_direct_audio", new_callable=AsyncMock, return_value="Merci pour cette vidéo.")
    @patch("native_voice_daemon.is_pc_audio_playing", return_value=True)
    def test_pc_audio_bleed_ignored_when_pc_playing(self, mock_pc, mock_transcribe):
        """Quand le PC diffuse du son et que l'audio capturé n'a pas de mot-clé, il est ignoré."""
        fake_wav = b"RIFF____WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        resp = self.client.post(
            "/api/voice/interact",
            content=fake_wav,
            headers={"Content-Type": "audio/wav", "X-Voice-Session-Active": "false"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("ignored"))

    @patch("main.transcribe_direct_audio", new_callable=AsyncMock, return_value="Yo Moctar quelle heure est-il ?")
    @patch("native_voice_daemon.is_pc_audio_playing", return_value=True)
    def test_wake_word_breaks_through_when_pc_playing(self, mock_pc, mock_transcribe):
        """La voix de Moctar adressée avec mot-clé perce même pendant la lecture audio du PC."""
        fake_wav = b"RIFF____WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        resp = self.client.post(
            "/api/voice/interact",
            content=fake_wav,
            headers={"Content-Type": "audio/wav", "X-Voice-Session-Active": "false"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data.get("ignored"))
        self.assertTrue(data.get("wake_word_detected"))

    @patch("main.transcribe_direct_audio", new_callable=AsyncMock, return_value="Yo Moctar relance le service.")
    @patch("native_voice_daemon.is_pc_audio_playing", return_value=False)
    def test_proactive_initiative_chained_to_interaction_response(self, mock_pc, mock_transcribe):
        """Vérifie qu'une initiative en attente est naturellement rattachée à la fin de la réponse vocale."""
        # Enregistrer une initiative urgente
        self.companion.register_initiative(
            "J'ai vu une alerte sur le VPS 2, tu veux que je regarde ?",
            category="infra_alert",
            priority="high",
        )
        fake_wav = b"RIFF____WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        resp = self.client.post(
            "/api/voice/interact",
            content=fake_wav,
            headers={"Content-Type": "audio/wav", "X-Voice-Session-Active": "false"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data.get("ignored"))
        reply = data.get("reply_text", "")
        self.assertIn("Au fait", reply)
        self.assertIn("VPS 2", reply)
        # La session collaborative doit être maintenue active pour la réponse de Moctar
        self.assertTrue(data.get("session_active"))


if __name__ == "__main__":
    unittest.main()
