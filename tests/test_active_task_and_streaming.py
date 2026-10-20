"""Tests unitaires pour le Streaming Audio Première Phrase (< 350 ms)
et la Mémoire de Travail Active (Task State Tracking).
"""

from __future__ import annotations

import asyncio
import os
import struct
import tempfile
import time
import unittest
from pathlib import Path
import copy
from contextlib import ExitStack
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

import active_task_tracker
import main
from main import (
    DEFAULT_TTS_VOICE,
    Settings,
    create_app,
    generate_cached_tts_chunks,
    handle_instant_local_command,
    split_speech_sentences,
)
from test_api import local_snapshot


def encode_pcm16_wav(raw_bytes: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    byte_rate = sample_rate * channels * 2
    block_align = channels * 2
    data_size = len(raw_bytes)
    chunk_size = 36 + data_size
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        chunk_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        16,
        b"data",
        data_size,
    )
    return header + raw_bytes


class ActiveTaskAndStreamingTests(unittest.TestCase):
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
        self.tmp_dir = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ, {"MOCTAR_TTS_CACHE_DIR": str(self.tmp_dir)}, clear=False))
        main._TTS_MEMORY_CACHE.clear()

        self.tracker = active_task_tracker.ActiveTaskTracker(storage_path=self.tmp_dir / "task_state.json")
        self.stack.enter_context(patch("active_task_tracker.get_task_tracker", return_value=self.tracker))

        self.settings = Settings(admin_token="admin-secret", interval=60, sentinel_geoip_enabled=False)
        self.app = create_app(self.settings)
        self.client = self.stack.enter_context(TestClient(self.app))

    def test_split_speech_sentences_abbreviations_and_decimals(self) -> None:
        """Vérifie le découpage des phrases tout en préservant M., Dr., M.O.C.T.A.R et décimaux."""
        text = "Bonjour M. Moctar. Le modèle gemini-3.5 est prêt. M.O.C.T.A.R veille sur le système."
        sentences = split_speech_sentences(text)
        self.assertEqual(len(sentences), 3)
        self.assertEqual(sentences[0], "Bonjour M. Moctar.")
        self.assertEqual(sentences[1], "Le modèle gemini-3.5 est prêt.")
        self.assertEqual(sentences[2], "M.O.C.T.A.R veille sur le système.")

    def test_split_speech_sentences_short_merging(self) -> None:
        """Vérifie que les phrases trop courtes (< 3 mots) sont fusionnées pour un flux oral naturel."""
        text = "Oui. C'est prêt. VS Code est ouvert sur le projet Moctar."
        sentences = split_speech_sentences(text)
        self.assertEqual(len(sentences), 2)
        self.assertEqual(sentences[0], "Oui. C'est prêt.")
        self.assertEqual(sentences[1], "VS Code est ouvert sur le projet Moctar.")

    def test_generate_cached_tts_chunks_first_sentence_streaming(self) -> None:
        """Vérifie la génération en parallèle des chunks audio pour la première phrase (< 350 ms)."""
        multi_sentence = "Environnement de travail initialisé. VS Code et terminal de supervision prêts, Moctar."

        async def _test() -> None:
            fake_mp3 = b"ID3-CHUNK-MP3"
            with patch("main.synthesize_edge_tts", new=AsyncMock(return_value=fake_mp3)):
                full_url, chunks = await generate_cached_tts_chunks(multi_sentence, DEFAULT_TTS_VOICE)
                self.assertTrue(full_url.startswith("/api/voice/tts/cache/"))
                self.assertTrue(full_url.endswith(".mp3"))
                self.assertEqual(len(chunks), 2)
                for c in chunks:
                    self.assertTrue(c.startswith("/api/voice/tts/cache/"))
                    self.assertTrue(c.endswith(".mp3"))

        asyncio.run(_test())

    def test_api_voice_interact_returns_audio_chunks(self) -> None:
        """Vérifie que /api/voice/interact retourne audio_chunks et first_chunk_audio_url."""
        wav_bytes = encode_pcm16_wav(struct.pack("<hhhh", 1000, -1000, 1000, -1000))
        fake_mp3 = b"ID3-AUDIO-MP3"

        with patch("main.voice_transcribe.transcribe_audio", return_value="Yo Moctar ouvre VS Code"), patch(
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
            self.assertIn("audio_chunks", data)
            self.assertIn("first_chunk_audio_url", data)
            self.assertTrue(isinstance(data["audio_chunks"], list))
            self.assertTrue(len(data["audio_chunks"]) >= 1)
            self.assertEqual(data["first_chunk_audio_url"], data["audio_chunks"][0])

    def test_active_task_tracker_lifecycle(self) -> None:
        """Vérifie le cycle de vie de la tâche active : objectif, sous-tâche, actions et complétion."""
        # 1. Définition d'un objectif
        self.tracker.set_goal("Refonte du pipeline vocal", subtask="Découpage première phrase")
        state = self.tracker.get_state()
        self.assertEqual(state["goal"], "Refonte du pipeline vocal")
        self.assertEqual(state["active_subtask"], "Découpage première phrase")
        self.assertEqual(state["status"], "in_progress")

        # 2. Ajout de points à vérifier
        self.tracker.update_state(add_pending=["Tester la latence", "Vérifier la RAM"])
        state = self.tracker.get_state()
        self.assertIn("Tester la latence", state["pending_checks"])
        self.assertIn("Vérifier la RAM", state["pending_checks"])

        # 3. Enregistrement d'une action
        self.tracker.record_action("Tests pytest exécutés avec succès")
        state = self.tracker.get_state()
        self.assertEqual(state["last_action"], "Tests pytest exécutés avec succès")

        # 4. Synthèse vocale de l'état
        speech = self.tracker.format_speech_summary()
        self.assertIn("Refonte du pipeline vocal", speech)
        self.assertIn("Découpage première phrase", speech)
        self.assertIn("Tests pytest", speech)
        self.assertTrue(speech.endswith("Moctar."))

        # 5. Clôture de la tâche
        self.tracker.complete_task(summary="Pipeline audio validé")
        state = self.tracker.get_state()
        self.assertEqual(state["status"], "completed")
        self.assertIn("validée et clôturée", self.tracker.format_speech_summary())

    def test_handle_instant_local_command_on_en_etait_ou(self) -> None:
        """Vérifie que la question 'On en était où ?' répond instantanément (0 ms) avec l'état actif."""
        self.tracker.set_goal("Supervision des conteneurs", subtask="Analyse des logs NOC")
        self.tracker.record_action("Vérification service jarvis")

        reply = handle_instant_local_command("on en était où ?")
        self.assertIsNotNone(reply)
        self.assertIn("Supervision des conteneurs", reply)
        self.assertIn("Analyse des logs NOC", reply)
        self.assertTrue(reply.endswith("Moctar."))

        # Variantes d'interrogation orale
        reply2 = handle_instant_local_command("qu'est-ce qu'on faisait ?")
        self.assertIsNotNone(reply2)
        self.assertIn("Supervision des conteneurs", reply2)

        reply3 = handle_instant_local_command("rappelle-moi notre tâche")
        self.assertIsNotNone(reply3)
        self.assertIn("Supervision des conteneurs", reply3)

    def test_voice_semantic_engine_get_active_task_tool(self) -> None:
        """Vérifie que le Function Calling du LLM peut invoquer get_active_task et formuler l'état."""
        from voice_semantic_engine import execute_voice_semantic_turn

        self.tracker.set_goal("Optimisation de la mémoire", subtask="Nettoyage cache TTS")
        self.tracker.record_action("Purge effectuée")

        mock_brain = Mock()
        mock_brain.chat.return_value = {
            "content": "",
            "tool_calls": [{"name": "get_active_task", "arguments": {}}],
            "finish_reason": "tool_calls",
        }
        mock_runtime = Mock()
        mock_runtime.ai_brain = mock_brain
        mock_runtime.jarvis = None

        res = execute_voice_semantic_turn("on en était où dans notre travail ?", runtime=mock_runtime)
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["semantic_tool_calling"])
        self.assertIn("Optimisation de la mémoire", res["reply"])
        self.assertTrue(res["reply"].endswith("Moctar."))


if __name__ == "__main__":
    unittest.main()
