"""Tests pour l'Expérience Vocale Dynamique Avancée de M.O.C.T.A.R :
1. Couche de Réparation Phonétique & Correction d'Erreurs STT (< 0.1 ms)
2. Conscience Contextuelle Permanente (Dell M4800 Live Desktop & Résolution des pronoms 'ça', 'le')
3. Conversation Continue Fluide (Dynamic Turn-Taking & Thinking Out Loud vs Directed Speech)
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
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

import desktop_window_manager
import dynamic_attention_engine
import main
import stt_phonetic_repair
from config import Settings
from main import create_app
from test_api import local_snapshot


def encode_pcm16_wav(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """Construit un conteneur WAV mono 16-bit PCM valide en mémoire."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


class DynamicVoiceExperienceTests(unittest.TestCase):
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

    def test_stt_phonetic_repair_submillisecond_accuracy(self) -> None:
        """Vérifie la réparation phonétique chirurgicale et instantanée (< 1 ms)."""
        test_cases = [
            ("ou VSCOD", "ouvre VS Code"),
            ("ouvre visuel studio", "ouvre Visual Studio Code"),
            ("donne la rame", "donne la RAM"),
            ("charge le ces pu", "charge le CPU"),
            ("relance docker com pose", "relance docker compose"),
            ("fais un git poule", "fais un git pull"),
            ("coupe la zik", "coupe la musique"),
            ("mets toi en plein ecran", "mets toi en plein écran"),
        ]

        for raw, expected in test_cases:
            t0 = time.perf_counter()
            repaired, repairs = stt_phonetic_repair.repair_stt_transcript(raw)
            duration_ms = (time.perf_counter() - t0) * 1000.0

            self.assertLess(duration_ms, 1.0, f"Réparation trop lente ({duration_ms:.3f}ms) pour '{raw}'")
            self.assertEqual(repaired.lower(), expected.lower())
            self.assertGreater(len(repairs), 0)

    def test_desktop_window_manager_and_pronoun_resolution(self) -> None:
        """Vérifie la détection de fenêtre et la résolution contextuelle de pronoms ('ferme ça', 'mets-le à droite')."""
        fake_window = {
            "available": True,
            "app_name": "vscode",
            "window_title": "Jarvis - Visual Studio Code",
            "process_name": "code",
            "pid": 4242,
        }

        # 1. Résolution de 'ferme ça'
        res_close = desktop_window_manager.resolve_pronoun_reference("ferme ça", fake_window)
        self.assertIsNotNone(res_close)
        self.assertEqual(res_close["action"], "close_active_window")
        self.assertEqual(res_close["target"], "vscode")

        # 2. Résolution de 'mets-le à droite'
        res_right = desktop_window_manager.resolve_pronoun_reference("mets-le à droite", fake_window)
        self.assertIsNotNone(res_right)
        self.assertEqual(res_right["action"], "snap_active_window")
        self.assertEqual(res_right["position"], "right")

        # 3. Résolution de 'plein écran'
        res_max = desktop_window_manager.resolve_pronoun_reference("plein écran", fake_window)
        self.assertIsNotNone(res_max)
        self.assertEqual(res_max["action"], "snap_active_window")
        self.assertEqual(res_max["position"], "maximize")

    def test_dynamic_attention_engine_classification(self) -> None:
        """Vérifie la distinction entre réflexion à voix haute (thinking out loud) et ordre explicite (directed speech)."""
        # A) Réflexion à voix haute en session active
        thinking_phrases = [
            "attends voir...",
            "voyons pourquoi ça plante",
            "voyons pourquoi ça bugue",
            "hum attends une seconde",
            "si je fais un git commit...",
            "c'est bizarre ce truc",
        ]
        for phrase in thinking_phrases:
            att = dynamic_attention_engine.classify_utterance_attention(phrase, is_session_active=True)
            self.assertTrue(att["is_thinking_out_loud"], f"Échec thinking out loud pour '{phrase}'")
            self.assertFalse(att["is_directed"])
            self.assertFalse(att["should_disengage"])

        # B) Désengagement explicite
        disengage_phrases = [
            "merci moctar",
            "c'est bon",
            "laisse tomber",
            "bonne nuit",
        ]
        for phrase in disengage_phrases:
            att = dynamic_attention_engine.classify_utterance_attention(phrase, is_session_active=True)
            self.assertTrue(att["should_disengage"], f"Échec disengage pour '{phrase}'")

        # C) Ordres explicites adressés au copilote
        directed_phrases = [
            "ouvre VS Code",
            "donne l'heure",
            "ferme ça",
            "mets-le à droite",
            "quelle est la charge CPU",
        ]
        for phrase in directed_phrases:
            att = dynamic_attention_engine.classify_utterance_attention(phrase, is_session_active=True)
            self.assertTrue(att["is_directed"], f"Échec directed speech pour '{phrase}'")
            self.assertFalse(att["is_thinking_out_loud"])

    def test_api_voice_interact_thinking_out_loud_preserves_session_silently(self) -> None:
        """Vérifie que Moctar réfléchissant à voix haute n'est pas coupé et que la session d'écoute reste active."""
        wav_bytes = encode_pcm16_wav(struct.pack("<hhhh", 1000, -1000, 1000, -1000))
        fake_mp3 = b"ID3-SILENT-MP3"

        with patch("main.voice_transcribe.transcribe_audio", return_value="attends voir pourquoi ça plante"), patch(
            "main.synthesize_edge_tts", new=AsyncMock(return_value=fake_mp3)
        ):
            resp = self.client.post(
                "/api/voice/interact",
                content=wav_bytes,
                headers={"Content-Type": "audio/wav", "X-Voice-Session-Active": "true"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["ok"])
            self.assertTrue(data["ignored"])
            self.assertEqual(data["reason"], "thinking_out_loud")
            self.assertTrue(data["session_active"])
            self.assertEqual(data["reply_text"], "")

    def test_api_voice_interact_disengage_releases_session(self) -> None:
        """Vérifie qu'une formule de politesse ou clôture libère la session avec courtoisie."""
        wav_bytes = encode_pcm16_wav(struct.pack("<hhhh", 1000, -1000, 1000, -1000))
        fake_mp3 = b"ID3-DISENGAGE-MP3"

        with patch("main.voice_transcribe.transcribe_audio", return_value="merci moctar"), patch(
            "main.synthesize_edge_tts", new=AsyncMock(return_value=fake_mp3)
        ):
            resp = self.client.post(
                "/api/voice/interact",
                content=wav_bytes,
                headers={"Content-Type": "audio/wav", "X-Voice-Session-Active": "true"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["ok"])
            self.assertFalse(data["ignored"])
            self.assertEqual(data["reply_text"], "À votre service Moctar, je reste en veille.")
            self.assertFalse(data["session_active"])

    def test_api_voice_interact_stt_repair_ou_vscod(self) -> None:
        """Vérifie que la distorsion phonétique 'ou VSCOD' est réparée en amont et ouvre VS Code immédiatement."""
        wav_bytes = encode_pcm16_wav(struct.pack("<hhhh", 1000, -1000, 1000, -1000))
        fake_mp3 = b"ID3-VSCODE-MP3"

        with patch("main.voice_transcribe.transcribe_audio", return_value="ou VSCOD"), patch(
            "main.synthesize_edge_tts", new=AsyncMock(return_value=fake_mp3)
        ):
            resp = self.client.post(
                "/api/voice/interact",
                content=wav_bytes,
                headers={"Content-Type": "audio/wav"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["ok"])
            self.assertFalse(data["ignored"])
            self.assertEqual(data["user_text"], "ouvre VS Code")
            self.assertIn("VS Code", data["reply_text"])
            self.assertTrue(data["reply_text"].endswith("Moctar."))
            self.assertEqual(data["action"], "open_app")
            self.assertEqual(data["url"], "vscode://file/opt/jarvis")


if __name__ == "__main__":
    unittest.main()
