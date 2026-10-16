"""Tests unitaires pour les endpoints vocaux M.O.C.T.A.R (/api/voice/tts, /api/voice/transcribe)
et l'agent compagnon natif Windows HP (clients/hp_companion/moctar_hp_agent.py).
"""
from __future__ import annotations

import copy
import os
import struct
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

import main
from clients.hp_companion import moctar_hp_agent as hp_agent
from config import Settings
from main import create_app
from test_api import local_snapshot


class MoctarVoiceEndpointsTests(unittest.TestCase):
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

    def test_voice_tts_generates_mp3_and_caches_repeated_phrases(self) -> None:
        fake_mp3 = b"ID3\x04\x00\x00\x00\x00\x00\x1fMOCTAR-EDGE-TTS-MP3"
        synth_mock = AsyncMock(return_value=fake_mp3)
        with patch("main.synthesize_edge_tts", new=synth_mock):
            first = self.client.post(
                "/api/voice/tts",
                json={"text": "Bonjour **Moctar**, système opérationnel.", "voice": "fr-FR-HenriNeural"},
            )
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.headers["content-type"], "audio/mpeg")
            self.assertEqual(first.headers["x-tts-cache"], "MISS")
            self.assertEqual(first.headers["x-tts-voice"], "fr-FR-HenriNeural")
            self.assertEqual(first.content, fake_mp3)
            synth_mock.assert_awaited_once_with("Bonjour Moctar , système opérationnel.", "fr-FR-HenriNeural")

            # Deuxième appel identique -> servi depuis le cache local sans rappeler edge-tts
            second = self.client.post(
                "/api/voice/tts",
                json={"text": "Bonjour **Moctar**, système opérationnel.", "voice": "fr-FR-HenriNeural"},
            )
            self.assertEqual(second.status_code, 200)
            self.assertEqual(second.headers["x-tts-cache"], "HIT")
            self.assertEqual(second.content, fake_mp3)
            self.assertEqual(synth_mock.await_count, 1)

    def test_voice_tts_supports_remy_multilingual_and_rejects_empty_text(self) -> None:
        fake_mp3 = b"ID3-REMY-MP3"
        with patch("main.synthesize_edge_tts", new=AsyncMock(return_value=fake_mp3)) as synth_mock:
            resp = self.client.post(
                "/api/voice/tts",
                json={"text": "Analyse terminée.", "voice": "fr-FR-RemyMultilingualNeural"},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.headers["x-tts-voice"], "fr-FR-RemyMultilingualNeural")
            synth_mock.assert_awaited_once_with("Analyse terminée.", "fr-FR-RemyMultilingualNeural")

        empty = self.client.post("/api/voice/tts", json={"text": "   "})
        self.assertEqual(empty.status_code, 422)

    def test_voice_transcribe_accepts_direct_wav_and_webm_blobs(self) -> None:
        wav_bytes = hp_agent.encode_pcm16_wav(struct.pack("<hhhh", 0, 1200, -1200, 0))
        with patch("main.voice_transcribe.transcribe_audio", return_value="Yo Moctar ouvre YouTube") as whisper_mock:
            res_wav = self.client.post(
                "/api/voice/transcribe",
                content=wav_bytes,
                headers={"Content-Type": "audio/wav"},
            )
            self.assertEqual(res_wav.status_code, 200)
            payload = res_wav.json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["text"], "Yo Moctar ouvre YouTube")
            self.assertEqual(payload["engine"], "faster-whisper")
            self.assertEqual(whisper_mock.call_count, 1)

            res_webm = self.client.post(
                "/api/voice/transcribe",
                content=b"\x1a\x45\xdf\xa3fake-webm-opus-stream",
                headers={"Content-Type": "audio/webm"},
            )
            self.assertEqual(res_webm.status_code, 200)
            self.assertEqual(res_webm.json()["text"], "Yo Moctar ouvre YouTube")

    def test_voice_transcribe_accepts_multipart_form_upload(self) -> None:
        wav_bytes = hp_agent.encode_pcm16_wav(struct.pack("<hhhh", 500, -500, 500, -500))
        boundary = "----MoctarBoundary987654"
        multipart_body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="phrase.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
        ).encode("latin-1") + wav_bytes + f"\r\n--{boundary}--\r\n".encode("latin-1")

        with patch("main.voice_transcribe.transcribe_audio", return_value="ELMo quel est le statut") as whisper_mock:
            resp = self.client.post(
                "/api/voice/transcribe",
                content=multipart_body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["text"], "ELMo quel est le statut")
            whisper_mock.assert_called_once()

    def test_voice_transcribe_rejects_empty_or_unsupported_mime(self) -> None:
        self.assertEqual(
            self.client.post("/api/voice/transcribe", content=b"", headers={"Content-Type": "audio/wav"}).status_code,
            422,
        )
        self.assertEqual(
            self.client.post(
                "/api/voice/transcribe", content=b"hello", headers={"Content-Type": "text/plain"}
            ).status_code,
            415,
        )


class MoctarHPCompanionAgentTests(unittest.TestCase):
    def test_wake_word_extraction_supports_moctar_yo_moctar_and_elmo(self) -> None:
        self.assertEqual(hp_agent.extract_wake_command("Yo Moctar quel est le statut CPU"), (True, "quel est le statut CPU"))
        self.assertEqual(hp_agent.extract_wake_command("Moctar, ouvre GitHub"), (True, "ouvre GitHub"))
        self.assertEqual(hp_agent.extract_wake_command("ELMo vérifie la RAM"), (True, "vérifie la RAM"))
        self.assertEqual(hp_agent.extract_wake_command("Yo Moctar"), (True, ""))
        self.assertEqual(hp_agent.extract_wake_command("Bonjour tout le monde"), (False, ""))

    def test_rms_and_wav_encoding(self) -> None:
        silence = struct.pack("<hhhh", 0, 0, 0, 0)
        loud = struct.pack("<hhhh", 16384, -16384, 16384, -16384)
        self.assertAlmostEqual(hp_agent.compute_rms(silence), 0.0, places=5)
        self.assertAlmostEqual(hp_agent.compute_rms(loud), 0.5, places=2)
        wav_data = hp_agent.encode_pcm16_wav(loud)
        self.assertTrue(wav_data.startswith(b"RIFF"))

    def test_companion_agent_pipeline_transcribes_queries_and_plays_tts(self) -> None:
        session = MagicMock()
        played_Buffers: list[bytes] = []

        def fake_post(url, **kwargs):
            if url.endswith("/api/voice/transcribe"):
                return SimpleNamespace(
                    raise_for_status=lambda: None,
                    json=lambda: {"ok": True, "text": "Yo Moctar donne l'état du serveur"},
                )
            if url.endswith("/api/jarvis/query"):
                return SimpleNamespace(
                    raise_for_status=lambda: None,
                    json=lambda: {"status": "ok", "reply": "Tous les systèmes sont nominaux, Moctar."},
                )
            if url.endswith("/api/voice/tts"):
                return SimpleNamespace(
                    raise_for_status=lambda: None,
                    content=b"ID3-FAKE-MP3-AUDIO",
                )
            raise AssertionError(f"Unexpected URL: {url}")

        session.post.side_effect = fake_post
        agent = hp_agent.MoctarHPCompanionAgent(
            server_url="https://moctar.klasora.com",
            session=session,
            audio_player=lambda data: (played_Buffers.append(data) or True),
        )
        wav_bytes = hp_agent.encode_pcm16_wav(struct.pack("<hhhh", 4000, -4000, 4000, -4000))
        reply = agent.handle_phrase_audio(wav_bytes)
        self.assertEqual(reply, "Tous les systèmes sont nominaux, Moctar.")
        self.assertEqual(played_Buffers, [b"ID3-FAKE-MP3-AUDIO"])


if __name__ == "__main__":
    unittest.main()

