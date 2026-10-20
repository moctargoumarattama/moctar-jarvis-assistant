"""Tests pour la Métamorphose Complice de M.O.C.T.A.R :
1. Interruption Immédiate (Barge-In Vocal Full-Duplex)
2. Personnalité Complice, Surnoms & Salutations Vivantes
3. Écoute Native Système Arrière-Plan (Dell M4800 OS Daemon)
4. Mémoire Épisodique & Continuité WhatsApp <-> PC
5. Proactivité Bienveillante (Prendre des nouvelles)
"""

from __future__ import annotations

import copy
import io
import os
import struct
import tempfile
import time
import unittest
import wave
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import assistant_memory
import dynamic_attention_engine
import main
import native_voice_daemon
import proactive_companion
import stt_phonetic_repair
from config import Settings
from main import create_app
from test_api import local_snapshot


def encode_pcm16_wav(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


class CompanionAndBargeInTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ("start", "close"):
            self.stack.enter_context(patch("main.collector.Collector." + name, new=AsyncMock()))
        self.stack.enter_context(
            patch(
                "main.collector.Collector.collect",
                new=AsyncMock(side_effect=lambda: copy.deepcopy(local_snapshot())),
            )
        )
        self.stack.enter_context(patch("main.WhatsAppController.refresh_voice_readiness", new=AsyncMock()))
        self.temp_dir = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ, {"MOCTAR_TTS_CACHE_DIR": str(self.temp_dir)}, clear=False))
        main._TTS_MEMORY_CACHE.clear()
        self.settings = Settings(admin_token="admin-secret", interval=60, sentinel_geoip_enabled=False)
        self.app = create_app(self.settings)
        self.client = self.stack.enter_context(TestClient(self.app))

    # =========================================================================
    # MODULE 1 : INTERRUPTIONS & BARGE-IN
    # =========================================================================

    def test_stt_repair_and_attention_for_interruption_keywords(self) -> None:
        """Vérifie la détection et la réparation des formules d'interruption vocale."""
        for phrase in ("tais-toi", "arrête de parler", "chut", "silence", "ferme-la"):
            repaired, _ = stt_phonetic_repair.repair_stt_transcript(phrase)
            self.assertIn("tais-toi", repaired.lower())

        # Détection attentionnelle de l'interruption
        att = dynamic_attention_engine.classify_utterance_attention("tais-toi", is_session_active=True)
        self.assertEqual(att["intent"], "interruption")
        self.assertTrue(att["is_directed"])
        self.assertFalse(att["should_disengage"])

    def test_instant_interruption_commands(self) -> None:
        """Vérifie la réponse instantanée (0 ms) aux ordres d'interruption."""
        res_silence = main.handle_instant_local_command("tais-toi")
        self.assertEqual(res_silence, "Pardon, je t'écoute Moctar.")

        res_stop = main.handle_instant_local_command("stop")
        self.assertEqual(res_stop, "Pardon, je t'écoute Moctar.")

        res_wait = main.handle_instant_local_command("attends voir")
        self.assertEqual(res_wait, "Je vous attends Moctar, prenez votre temps.")

    def test_api_barge_in_header_preserves_session_active(self) -> None:
        """Vérifie que l'interruption barge-in coupe et maintient la session active."""
        wav_bytes = encode_pcm16_wav(struct.pack("<hhhh", 1000, -1000, 1000, -1000))
        fake_mp3 = b"ID3-AUDIO-MP3"

        with patch("main.voice_transcribe.transcribe_audio", return_value="tais-toi"), patch(
            "main.synthesize_edge_tts", new=AsyncMock(return_value=fake_mp3)
        ):
            resp = self.client.post(
                "/api/voice/interact",
                content=wav_bytes,
                headers={"Content-Type": "audio/wav", "X-Voice-Barge-In": "true"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["ok"])
            self.assertFalse(data["ignored"])
            self.assertEqual(data["reply_text"], "Pardon, je t'écoute Moctar.")
            self.assertTrue(data["session_active"])

    # =========================================================================
    # MODULE 2 : PERSONNALITÉ COMPLICE & SALUTATIONS VIVANTES
    # =========================================================================

    def test_familiar_nicknames_and_presence_check(self) -> None:
        """Vérifie l'écoute sur surnoms familiers ('Moc', 'Mokti', 'Tu es là ?')."""
        self.assertTrue(main.is_direct_voice_command("Moc"))
        self.assertTrue(main.is_direct_voice_command("Tu es là ?"))
        self.assertTrue(main.is_direct_voice_command("t'es là"))

        res_presence = main.handle_instant_local_command("tu es là ?")
        self.assertIn("Oui Moctar, je suis là", res_presence)

    def test_time_based_greetings(self) -> None:
        """Vérifie la personnalisation des salutations selon l'heure du jour."""
        # 1. Matin
        with patch("main.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 8
            mock_dt.now.return_value.strftime = lambda fmt: "08:00"
            res_morning = main.handle_instant_local_command("bonjour")
            self.assertIn("Bonjour Moctar", res_morning)
            self.assertIn("Prêt pour cette journée", res_morning)

        # 2. Après-midi
        with patch("main.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 15
            mock_dt.now.return_value.strftime = lambda fmt: "15:00"
            res_afternoon = main.handle_instant_local_command("bonjour")
            self.assertIn("Oui Moctar, je suis là", res_afternoon)
            self.assertIn("On avance sur quoi", res_afternoon)

        # 3. Nuit tardive (> 23h)
        with patch("main.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 23
            mock_dt.now.return_value.strftime = lambda fmt: "23:45"
            res_night = main.handle_instant_local_command("bonjour")
            self.assertIn("Encore au travail Moctar", res_night)
            self.assertIn("Je reste avec toi", res_night)

    # =========================================================================
    # MODULE 3 : ÉCOUTE NATIVE SYSTÈME OS DAEMON
    # =========================================================================

    def test_native_voice_daemon_lifecycle_and_trigger_endpoint(self) -> None:
        """Vérifie le déclenchement du démon natif via /api/voice/trigger."""
        daemon = native_voice_daemon.NativeVoiceDaemon.get_instance()
        self.assertIsNotNone(daemon)

        resp = self.client.post("/api/voice/trigger")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["triggered"])

    # =========================================================================
    # MODULE 4 : MÉMOIRE ÉPISODIQUE & CONTINUITÉ WHATSAPP <-> PC
    # =========================================================================

    def test_episodic_memory_save_and_greeting_continuity(self) -> None:
        """Vérifie la mémorisation d'épisodes et la génération d'accueil contextuel."""
        db_path = self.temp_dir / "test_episodic_memory.db"
        mem = assistant_memory.AssistantMemory(db_path=db_path)

        # 1. Sauvegarde d'un épisode de la veille
        ok = mem.save_daily_episode(
            topic="streaming audio et barge-in",
            summary="Implémentation du lecteur de chunks audio et de l'interruption",
            source="desktop",
        )
        self.assertTrue(ok)

        latest = mem.get_latest_daily_episode()
        self.assertIsNotNone(latest)
        self.assertEqual(latest["topic"], "streaming audio et barge-in")

        # 2. Accueil matinal avec référence au travail précédent
        greet = mem.build_episodic_greeting(hour=9)
        self.assertIsNotNone(greet)
        self.assertIn("streaming audio", greet)
        self.assertIn("Moctar", greet)

        # 3. Accueil avec continuité WhatsApp récente
        mem.save_daily_episode(
            topic="déploiement du VPS",
            summary="Redémarrage du service et vérification des logs",
            source="whatsapp",
        )
        greet_wa = mem.build_episodic_greeting(hour=14)
        self.assertIsNotNone(greet_wa)
        self.assertIn("Sur WhatsApp tout à l'heure", greet_wa)
        self.assertIn("déploiement du VPS", greet_wa)

    # =========================================================================
    # MODULE 5 : PROACTIVITÉ BIENVEILLANTE (PRENDRE DES NOUVELLES)
    # =========================================================================

    def test_proactive_companion_infrastructure_alert_and_fatigue(self) -> None:
        """Vérifie l'évaluation proactive pour alertes VPS et fatigue de travail."""
        comp = proactive_companion.ProactiveCompanion(cooldown_seconds=0.0)

        fake_runtime = type("FakeRuntime", (), {
            "vps": [{"name": "VPS-PROD", "status": "down", "cpu": 99.0}],
            "cyber": None,
        })()

        # Alerte infra
        event = comp.evaluate_proactive_event(fake_runtime)
        self.assertIsNotNone(event)
        self.assertEqual(event["category"], "infra_alert")
        self.assertIn("VPS-PROD", event["message"])
        self.assertIn("Moctar", event["message"])

    def test_api_voice_proactive_endpoint(self) -> None:
        """Vérifie l'endpoint /api/voice/proactive pour le dashboard et l'audio."""
        fake_mp3 = b"ID3-PROACTIVE-MP3"
        with patch("main.synthesize_edge_tts", new=AsyncMock(return_value=fake_mp3)):
            resp = self.client.get("/api/voice/proactive")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["ok"])


if __name__ == "__main__":
    unittest.main()

