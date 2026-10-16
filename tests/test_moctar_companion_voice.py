"""Tests unitaires pour l'interaction vocale native Web M.O.C.T.A.R :
- /api/voice/interact (cycle complet : transcription faster-whisper -> wake-word / session -> AgentCore -> edge-tts + cache audio_url)
- Anti-hallucination Whisper et rejet propre des fichiers audio silencieux
- /api/voice/tts, /api/voice/tts/cache/{filename} et /api/voice/transcribe
"""
from __future__ import annotations

import copy
import io
import os
import struct
import tempfile
import unittest
import wave
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

import main
import voice_transcribe
from config import Settings
from main import create_app
from test_api import local_snapshot
from test_voice_transcribe import local_model


def encode_pcm16_wav(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """Construit un conteneur WAV mono 16-bit PCM valide en mémoire."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


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
        wav_bytes = encode_pcm16_wav(struct.pack("<hhhh", 0, 1200, -1200, 0))
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
        wav_bytes = encode_pcm16_wav(struct.pack("<hhhh", 500, -500, 500, -500))
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

    def test_silent_audio_upload_and_whisper_hallucinations_are_ignored(self) -> None:
        """Simule l'envoi d'un fichier audio silencieux et d'hallucinations Whisper ('Au revoir YouTube', etc.)."""
        silent_wav = encode_pcm16_wav(struct.pack("<" + "h" * 1600, *([0] * 1600)))
        silent_path = self.temp_dir / "silence.wav"
        silent_path.write_bytes(silent_wav)

        model_dir = local_model(self.temp_dir / "models" / "whisper")
        engine = Mock()
        factory = Mock(return_value=engine)

        with patch.object(voice_transcribe, "APP_DIR", self.temp_dir), patch.object(
            voice_transcribe, "_dependency", return_value=(factory, False)
        ):
            # 1. Un WAV PCM silencieux est ignoré immédiatement sans déclencher Whisper
            self.assertEqual(voice_transcribe.transcribe_audio(silent_path), "")
            factory.assert_not_called()

            # 2. Une hallucination Whisper sur un flux bruité est filtrée avec no_speech_threshold=0.6
            loud_wav = encode_pcm16_wav(struct.pack("<hhhh", 0, 12000, -12000, 0))
            loud_path = self.temp_dir / "noisy.wav"
            loud_path.write_bytes(loud_wav)
            for hallucination in (
                "Au revoir YouTube",
                "Sous-titres réalisés par la communauté d'Amara.org",
                "Merci d'avoir regardé cette vidéo !",
                "Abonnez-vous à la chaîne",
            ):
                engine.transcribe.return_value = (iter([SimpleNamespace(text=hallucination, no_speech_prob=0.1)]), None)
                self.assertEqual(voice_transcribe.transcribe_audio(loud_path), "")
                kwargs = engine.transcribe.call_args.kwargs
                self.assertFalse(kwargs["condition_on_previous_text"])
                self.assertEqual(kwargs["no_speech_threshold"], 0.6)

            # 3. Envoi du fichier silencieux sur /api/voice/interact -> ignoré sans appeler AgentCore ni TTS
            jarvis_mock = AsyncMock()
            self.app.state.runtime.jarvis = SimpleNamespace(execute_prompt_async=jarvis_mock)
            resp = self.client.post(
                "/api/voice/interact",
                content=silent_wav,
                headers={"Content-Type": "audio/wav"},
            )
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(body["ok"])
            self.assertTrue(body["ignored"])
            self.assertEqual(body["user_text"], "")
            self.assertEqual(body["reply_text"], "")
            self.assertIsNone(body["audio_url"])
            jarvis_mock.assert_not_awaited()

    def test_voice_interact_full_cycle_from_audio_to_agent_and_cached_tts(self) -> None:
        """Valide le cycle complet de /api/voice/interact : audio -> Whisper -> AgentCore -> edge-tts -> audio_url."""
        wav_bytes = encode_pcm16_wav(struct.pack("<hhhh", 2000, -2000, 2000, -2000))
        fake_mp3 = b"ID3\x04\x00\x00\x00\x00\x00\x1fMOCTAR-INTERACT-TTS-MP3"
        jarvis_mock = AsyncMock(
            return_value={
                "status": "success",
                "reply": "Tous les services du Dell Precision sont actifs, Moctar.",
                "tools_used": ["get_system_metrics"],
                "thought_chain": ["Vérification des services"],
            }
        )
        self.app.state.runtime.jarvis = SimpleNamespace(execute_prompt_async=jarvis_mock)

        with patch(
            "main.voice_transcribe.transcribe_audio",
            return_value="Yo Moctar donne-moi l'état des services",
        ) as whisper_mock, patch(
            "main.synthesize_edge_tts",
            new=AsyncMock(return_value=fake_mp3),
        ) as tts_mock:
            resp = self.client.post(
                "/api/voice/interact",
                content=wav_bytes,
                headers={"Content-Type": "audio/wav"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["ok"])
            self.assertFalse(data["ignored"])
            self.assertEqual(data["user_text"], "donne-moi l'état des services")
            self.assertEqual(data["reply_text"], "Tous les services du Dell Precision sont actifs, Moctar.")
            self.assertTrue(data["audio_url"].startswith("/api/voice/tts/cache/"))
            self.assertTrue(data["audio_url"].endswith(".mp3"))
            self.assertFalse(data["session_active"])
            whisper_mock.assert_called_once()
            jarvis_mock.assert_awaited_once_with(
                user_input="donne-moi l'état des services",
                source="web_voice",
            )
            tts_mock.assert_awaited_once_with(
                "Tous les services du Dell Precision sont actifs, Moctar.",
                "fr-FR-HenriNeural",
            )

            # Le dashboard peut télécharger directement l'audio généré via data["audio_url"]
            audio_resp = self.client.get(data["audio_url"])
            self.assertEqual(audio_resp.status_code, 200)
            self.assertEqual(audio_resp.headers["content-type"], "audio/mpeg")
            self.assertEqual(audio_resp.content, fake_mp3)

    def test_phonetic_wake_words_anywhere_and_wake_only_butler_greeting(self) -> None:
        """Valide la tolérance phonétique totale (Moktar, Motard, Mouctar, etc.), la réponse majordome immédiate sur prénom seul et la gestion de session."""
        wav_bytes = encode_pcm16_wav(struct.pack("<hhhh", 2500, -2500, 2500, -2500))
        fake_mp3 = b"ID3-BUTLER-MP3"
        jarvis_mock = AsyncMock(
            return_value={
                "status": "success",
                "reply": "La charge CPU est à 12 pourcent, Moctar.",
                "tools_used": [],
                "thought_chain": [],
            }
        )
        self.app.state.runtime.jarvis = SimpleNamespace(execute_prompt_async=jarvis_mock)

        with patch(
            "main.synthesize_edge_tts",
            new=AsyncMock(return_value=fake_mp3),
        ):
            # 1. Appel avec uniquement le prénom ("Yo Moctar" / "Motard") -> réponse majordome immédiate sans solliciter le LLM
            self.app.state.runtime.voice_session_until = 0.0
            with patch("main.voice_transcribe.transcribe_audio", return_value="Yo Motard"):
                wake_resp = self.client.post(
                    "/api/voice/interact",
                    content=wav_bytes,
                    headers={"Content-Type": "audio/wav"},
                )
                self.assertEqual(wake_resp.status_code, 200)
                wake_data = wake_resp.json()
                self.assertTrue(wake_data["ok"])
                self.assertFalse(wake_data["ignored"])
                self.assertEqual(wake_data["reply_text"], "À vos ordres Moctar, je vous écoute.")
                self.assertFalse(wake_data["session_active"])
                self.assertTrue(wake_data["audio_url"].startswith("/api/voice/tts/cache/"))
                jarvis_mock.assert_not_awaited()

            # 2. Avec le header de session active (fourni par le dashboard ou PTT), toute phrase sans mot-clé est traitée directement
            with patch("main.voice_transcribe.transcribe_audio", return_value="vérifie les disques durs"):
                followup_resp = self.client.post(
                    "/api/voice/interact",
                    content=wav_bytes,
                    headers={"Content-Type": "audio/wav", "X-Voice-Session-Active": "true"},
                )
                self.assertEqual(followup_resp.status_code, 200)
                followup_data = followup_resp.json()
                self.assertFalse(followup_data["ignored"])
                self.assertEqual(followup_data["user_text"], "vérifie les disques durs")
                self.assertEqual(followup_data["reply_text"], "La charge CPU est à 12 pourcent, Moctar.")
                jarvis_mock.assert_awaited_once_with(user_input="vérifie les disques durs", source="web_voice")

            # 3. Variantes phonétiques au milieu ou à la fin de la phrase hors session active
            for variant_phrase, expected_cmd in (
                ("Quel est le statut réseau Mokhtar ?", "Quel est le statut réseau"),
                ("Peux-tu vérifier les services Mouctar", "Peux-tu vérifier les services"),
                ("Allô Moctar ouvre YouTube", "ouvre YouTube"),
                ("et vous m'occupe tard donne le statut", "donne le statut"),
            ):
                self.app.state.runtime.voice_session_until = 0.0
                accepted, cmd, detected = main.extract_voice_interact_command(variant_phrase, session_active=False)
                self.assertTrue(accepted, variant_phrase)
                self.assertTrue(detected, variant_phrase)
                self.assertEqual(cmd, expected_cmd, variant_phrase)

            # 4. Approximations de dictionnaire du log live ("Mokta", "m'occupe tard", "m'occaque") sans ordre -> réponse majordome
            for live_whisper_log in (
                "tout le monde. Mokta. Mokta. Mokta.",
                "You, m'occaque. You.",
                "et vous m'occupe tard.",
            ):
                self.app.state.runtime.voice_session_until = 0.0
                accepted, cmd, detected = main.extract_voice_interact_command(live_whisper_log, session_active=False)
                self.assertTrue(accepted, live_whisper_log)
                self.assertTrue(detected, live_whisper_log)
                self.assertEqual(cmd, main._WAKE_ONLY_SENTINEL, live_whisper_log)

    def test_voice_interact_conversational_fast_path(self) -> None:
        """Vérifie le routage rapide (< 1s) direct vers ai_brain.chat pour les salutations et questions simples sans action système."""
        wav_bytes = encode_pcm16_wav(struct.pack("<hhhh", 2500, -2500, 2500, -2500))
        fake_mp3 = b"ID3-FASTPATH-MP3"
        jarvis_mock = AsyncMock()
        self.app.state.runtime.jarvis = SimpleNamespace(execute_prompt_async=jarvis_mock)
        chat_mock = Mock(return_value={"content": "Je m'appelle M.O.C.T.A.R, votre copilote vocal.", "finish_reason": "stop"})
        self.app.state.runtime.ai_brain = SimpleNamespace(chat=chat_mock)

        with patch("main.synthesize_edge_tts", new=AsyncMock(return_value=fake_mp3)):
            # 1. Question conversationnelle simple : "Yo Moctar comment tu t'appelles ?"
            self.app.state.runtime.voice_session_until = 0.0
            with patch("main.voice_transcribe.transcribe_audio", return_value="Yo Moctar comment tu t'appelles ?"):
                resp = self.client.post(
                    "/api/voice/interact",
                    content=wav_bytes,
                    headers={"Content-Type": "audio/wav"},
                )
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertTrue(data["ok"])
                self.assertFalse(data["ignored"])
                self.assertTrue(data["fast_path"])
                self.assertEqual(data["user_text"], "comment tu t'appelles")
                self.assertEqual(data["reply_text"], "Je m'appelle M.O.C.T.A.R, votre copilote vocal Moctar.")
                self.assertTrue(data["audio_url"].startswith("/api/voice/tts/cache/"))
                chat_mock.assert_called_once_with(
                    "comment tu t'appelles",
                    system_prompt=main.VOICE_FAST_SYSTEM_PROMPT,
                )
                jarvis_mock.assert_not_awaited()

            # 2. Requête système complexe : ne passe PAS par le fast-path, appelle execute_prompt_async
            chat_mock.reset_mock()
            jarvis_mock.reset_mock()
            jarvis_mock.return_value = {
                "status": "success",
                "reply": "Service docker relancé avec succès.",
                "tools_used": ["docker_restart"],
                "thought_chain": ["Relance du service docker"],
            }
            with patch("main.voice_transcribe.transcribe_audio", return_value="Yo Moctar relance docker"):
                resp2 = self.client.post(
                    "/api/voice/interact",
                    content=wav_bytes,
                    headers={"Content-Type": "audio/wav"},
                )
                self.assertEqual(resp2.status_code, 200)
                data2 = resp2.json()
                self.assertTrue(data2["ok"])
                self.assertFalse(data2["ignored"])
                self.assertFalse(data2["fast_path"])
                self.assertEqual(data2["reply_text"], "Service docker relancé avec succès.")
                chat_mock.assert_not_called()
                jarvis_mock.assert_awaited_once_with(
                    user_input="relance docker",
                    source="web_voice",
                )

            # 3. Requête conversationnelle longue (> 6 mots) sans mot-clé système : routage direct Gemini par défaut (routeur inversé)
            chat_mock.reset_mock()
            jarvis_mock.reset_mock()
            chat_mock.return_value = {
                "content": "La première exoplanète confirmée autour d'une étoile similaire au Soleil a été découverte en 1995, Moctar.",
                "finish_reason": "stop",
            }
            with patch(
                "main.voice_transcribe.transcribe_audio",
                return_value="Yo Moctar raconte-moi une découverte fascinante sur l'astrophysique et les trous noirs",
            ):
                resp3 = self.client.post(
                    "/api/voice/interact",
                    content=wav_bytes,
                    headers={"Content-Type": "audio/wav"},
                )
                self.assertEqual(resp3.status_code, 200)
                data3 = resp3.json()
                self.assertTrue(data3["ok"])
                self.assertFalse(data3["ignored"])
                self.assertTrue(data3["fast_path"])
                self.assertEqual(
                    data3["reply_text"],
                    "La première exoplanète confirmée autour d'une étoile similaire au Soleil a été découverte en 1995, Moctar.",
                )
                chat_mock.assert_called_once_with(
                    "raconte-moi une découverte fascinante sur l'astrophysique et les trous noirs",
                    system_prompt=main.VOICE_FAST_SYSTEM_PROMPT,
                )
                jarvis_mock.assert_not_awaited()

    def test_transcribe_audio_removes_hallucinatory_prompt(self) -> None:
        """Vérifie que transcribe_audio ne passe aucun initial_prompt hallucinatoire à Whisper avec décodage greedy rapide."""
        local_model(self.temp_dir / "models" / "whisper")
        loud_wav = encode_pcm16_wav(struct.pack("<hhhh", 0, 10000, -10000, 0))
        loud_path = self.temp_dir / "speech.wav"
        loud_path.write_bytes(loud_wav)
        engine = Mock()
        engine.transcribe.return_value = (iter([SimpleNamespace(text="Moctar à l'écoute", no_speech_prob=0.05)]), None)
        factory = Mock(return_value=engine)

        with patch.object(voice_transcribe, "APP_DIR", self.temp_dir), patch.object(
            voice_transcribe, "_dependency", return_value=(factory, False)
        ):
            text = voice_transcribe.transcribe_audio(loud_path)
            self.assertEqual(text, "Moctar à l'écoute")
            kwargs = engine.transcribe.call_args.kwargs
            self.assertEqual(kwargs["language"], "fr")
            self.assertEqual(kwargs["beam_size"], 1)
            self.assertEqual(kwargs["best_of"], 1)
            self.assertEqual(kwargs["temperature"], 0.0)
            self.assertIsNone(kwargs.get("initial_prompt"))

    def test_instant_local_commands_without_llm(self) -> None:
        """Vérifie les commandes instantanées locales (0 ms, sans LLM) : heure, date, statut système."""
        # Heure
        res_heure = main.handle_instant_local_command("quelle heure est-il")
        self.assertIsNotNone(res_heure)
        self.assertIn("Il est", res_heure)
        self.assertTrue(res_heure.endswith(", Moctar."))

        # Date
        res_date = main.handle_instant_local_command("on est quel jour")
        self.assertIsNotNone(res_date)
        self.assertIn("Nous sommes le", res_date)
        self.assertTrue(res_date.endswith(", Moctar."))

        # CPU / RAM
        res_sys = main.handle_instant_local_command("donne-moi l'état de la mémoire et du processeur")
        self.assertIsNotNone(res_sys)
        self.assertIn("Processeur", res_sys)
        self.assertIn("mémoire vive", res_sys)
        self.assertTrue(res_sys.endswith(", Moctar."))

        # Non local
        self.assertIsNone(main.handle_instant_local_command("qui a peint la Joconde"))

    def test_whisper_defaults_to_base_model(self) -> None:
        """Vérifie que le modèle par défaut est 'base' dans config et voice_transcribe."""
        self.assertEqual(self.settings.whisper_model, "base")
        self.assertEqual(voice_transcribe.MODEL_NAMES[0], "base")

    def test_gemini_503_fallback_courtesy_spoken_reply(self) -> None:
        """Vérifie qu'en cas d'erreur 503 UNAVAILABLE, AIBrain bascule et renvoie la réponse de courtoisie vocale immédiate."""
        import ai_brain

        brain = ai_brain.AIBrain()
        error_503 = Exception("503 UNAVAILABLE: The model is overloaded. Please try again later.")
        mock_client = Mock()
        mock_client.models.generate_content.side_effect = error_503
        brain.gemini_client = mock_client

        with patch("time.sleep", return_value=None):
            result = brain.chat("Bonjour")

        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(
            result["content"],
            "Je subis une micro-coupure de liaison avec le serveur neural, mais je reste en ligne Moctar.",
        )
        self.assertTrue(result.get("fallback_courtesy"))

    def test_transcribe_audio_groq_whisper_and_fallback_to_local(self) -> None:
        """Vérifie la transcription via l'API Groq Whisper Large-v3 et le repli local si la clé est absente ou en échec."""
        loud_wav = encode_pcm16_wav(struct.pack("<hhhh", 0, 10000, -10000, 0))
        loud_path = self.temp_dir / "groq_test.wav"
        loud_path.write_bytes(loud_wav)

        # 1. Groq API réussie avec GROQ_API_KEY
        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test_secret_key"}), patch(
            "voice_transcribe.transcribe_groq_api", return_value="Yo Moctar ouvre YouTube"
        ) as groq_mock, patch("voice_transcribe._dependency") as dep_mock:
            result = voice_transcribe.transcribe_audio(loud_path)
            self.assertEqual(result, "Yo Moctar ouvre YouTube")
            groq_mock.assert_called_once_with(loud_path, api_key=None)
            dep_mock.assert_not_called()

        # 2. Rejet des hallucinations répétitives (ex: 'c'est une vidéo' répété 4+ fois)
        repetitive_hallucination = "c'est une vidéo, c'est une vidéo, c'est une vidéo, c'est une vidéo"
        self.assertTrue(voice_transcribe.has_repetitive_hallucination(repetitive_hallucination))
        self.assertEqual(voice_transcribe.filter_whisper_hallucinations(repetitive_hallucination), "")

        # 3. Repli automatique sur faster-whisper local si Groq échoue ou si GROQ_API_KEY est absente
        local_model(self.temp_dir / "models" / "whisper")
        engine = Mock()
        engine.transcribe.return_value = (iter([SimpleNamespace(text="Moctar en local", no_speech_prob=0.05)]), None)
        factory = Mock(return_value=engine)

        with patch.dict(os.environ, {"GROQ_API_KEY": ""}, clear=False), patch(
            "voice_transcribe.transcribe_groq_api", return_value=None
        ), patch.object(voice_transcribe, "APP_DIR", self.temp_dir), patch.object(
            voice_transcribe, "_dependency", return_value=(factory, False)
        ):
            fallback_text = voice_transcribe.transcribe_audio(loud_path)
            self.assertEqual(fallback_text, "Moctar en local")
            self.assertEqual(engine.transcribe.call_count, 1)
            kwargs = engine.transcribe.call_args.kwargs
            self.assertEqual(kwargs["compression_ratio_threshold"], 2.2)

    def test_transcribe_groq_api_direct_http_call(self) -> None:
        """Vérifie l'appel HTTP vers l'API Groq Whisper Large-v3."""
        fake_wav = self.temp_dir / "test_api.wav"
        fake_wav.write_bytes(b"RIFF44WAVEfmt 16000data0000")
        fake_response = Mock()
        fake_response.status_code = 200
        fake_response.json.return_value = {"text": "Bonjour Moctar"}

        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_12345"}), patch(
            "requests.post", return_value=fake_response
        ) as post_mock:
            text = voice_transcribe.transcribe_groq_api(fake_wav)
            self.assertEqual(text, "Bonjour Moctar")
            post_mock.assert_called_once()
            call_kwargs = post_mock.call_args.kwargs
            self.assertEqual(call_kwargs["headers"]["Authorization"], "Bearer gsk_12345")
            self.assertEqual(call_kwargs["data"]["model"], "whisper-large-v3")
            self.assertEqual(call_kwargs["data"]["language"], "fr")
            self.assertEqual(call_kwargs["data"]["temperature"], "0.0")
            self.assertEqual(call_kwargs["data"]["prompt"], "")

    def test_filter_whisper_hallucinations_rejects_hallucinatory_patterns(self) -> None:
        """Vérifie le rejet systématique des hallucinations (oui, is that you, assistant vocal, anglais)."""
        self.assertEqual(voice_transcribe.filter_whisper_hallucinations("Oui."), "")
        self.assertEqual(voice_transcribe.filter_whisper_hallucinations("oui"), "")
        self.assertEqual(voice_transcribe.filter_whisper_hallucinations("Is that you?"), "")
        self.assertEqual(voice_transcribe.filter_whisper_hallucinations("assistant vocal"), "")
        self.assertEqual(voice_transcribe.filter_whisper_hallucinations("Thank you."), "")
        self.assertEqual(voice_transcribe.filter_whisper_hallucinations("au revoir"), "")
        self.assertEqual(voice_transcribe.filter_whisper_hallucinations("Hello world how are you"), "")
        self.assertEqual(
            voice_transcribe.filter_whisper_hallucinations("Bonjour Moctar quelle heure est-il"),
            "Bonjour Moctar quelle heure est-il",
        )


if __name__ == "__main__":
    unittest.main()
