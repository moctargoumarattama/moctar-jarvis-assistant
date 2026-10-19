"""Tests unitaires pour l'interaction vocale native Web M.O.C.T.A.R :
- /api/voice/interact (cycle complet : transcription faster-whisper -> wake-word / session -> AgentCore -> edge-tts + cache audio_url)
- Anti-hallucination Whisper et rejet propre des fichiers audio silencieux
- /api/voice/tts, /api/voice/tts/cache/{filename} et /api/voice/transcribe
"""
from __future__ import annotations

import copy
import io
import json
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
            # 1. Question conversationnelle simple : "Yo Moctar que penses-tu de l'astronomie ?"
            self.app.state.runtime.voice_session_until = 0.0
            with patch("main.voice_transcribe.transcribe_audio", return_value="Yo Moctar que penses-tu de l'astronomie ?"):
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
                self.assertEqual(data["user_text"], "que penses-tu de l'astronomie")
                self.assertEqual(data["reply_text"], "Je m'appelle M.O.C.T.A.R, votre copilote vocal Moctar.")
                self.assertTrue(data["audio_url"].startswith("/api/voice/tts/cache/"))
                chat_mock.assert_called_once()
                self.assertEqual(chat_mock.call_args.args[0], "que penses-tu de l'astronomie")
                self.assertIn(main.VOICE_FAST_SYSTEM_PROMPT, chat_mock.call_args.kwargs["system_prompt"])
                self.assertIn("TÉLÉMÉTRIE EN TEMPS RÉEL", chat_mock.call_args.kwargs["system_prompt"])
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

            # 3. Requête conversationnelle longue (> 6 mots) sans mot-clé système : routage direct Llama/Gemini par défaut
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
                chat_mock.assert_called_once()
                self.assertIn("raconte-moi une découverte fascinante", chat_mock.call_args.args[0])
                self.assertIn(main.VOICE_FAST_SYSTEM_PROMPT, chat_mock.call_args.kwargs["system_prompt"])
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
        """Vérifie les commandes instantanées locales (0 ms, sans LLM) : identité, heure, date, température °C, CPU/RAM."""
        # 1. Identité
        for phrase in ("comment t'appelles-tu", "qui es-tu", "c'est quoi ton nom"):
            res_id = main.handle_instant_local_command(phrase)
            self.assertEqual(
                res_id,
                "Je suis M.O.C.T.A.R, votre système autonome de contrôle et d'intelligence opérationnelle, Moctar.",
            )

        # 2. Heure
        res_heure = main.handle_instant_local_command("quelle heure est-il")
        self.assertIsNotNone(res_heure)
        self.assertIn("Il est", res_heure)
        self.assertTrue(res_heure.endswith(", Moctar."))

        # 3. Date
        res_date = main.handle_instant_local_command("on est quel jour")
        self.assertIsNotNone(res_date)
        self.assertIn("Nous sommes le", res_date)
        self.assertTrue(res_date.endswith(", Moctar."))

        # 4. Température réelle en °C
        with patch("main.get_cpu_temperature_celsius", return_value=53.4):
            for phrase in ("la température actuelle", "température processeur", "le processeur chauffe ?"):
                res_temp = main.handle_instant_local_command(phrase)
                self.assertIsNotNone(res_temp)
                self.assertIn("Le processeur du serveur est à 53°C", res_temp)
                self.assertTrue(res_temp.endswith(", Moctar."))

        # 5. CPU / RAM (sans mention de température)
        res_sys = main.handle_instant_local_command("donne-moi l'état de la mémoire et du processeur")
        self.assertIsNotNone(res_sys)
        self.assertIn("Processeur", res_sys)
        self.assertIn("mémoire vive", res_sys)
        self.assertTrue(res_sys.endswith(", Moctar."))

        # Non local
        self.assertIsNone(main.handle_instant_local_command("qui a peint la Joconde"))

    def test_extract_voice_interact_command_clean_wake_and_after(self) -> None:
        """Vérifie l'extraction du texte après le mot-clé, le traitement de 'au Mokhtar.' en réveil pur et le filtrage des bruits < 4 chars."""
        # 1. 'au Mokhtar.' -> réveil pur (_WAKE_ONLY_SENTINEL)
        accepted, cmd, detected = main.extract_voice_interact_command("au Mokhtar.", session_active=False)
        self.assertTrue(accepted)
        self.assertTrue(detected)
        self.assertEqual(cmd, main._WAKE_ONLY_SENTINEL)

        # 2. 'salut Moctar' -> réveil pur
        accepted2, cmd2, detected2 = main.extract_voice_interact_command("salut Moctar", session_active=False)
        self.assertTrue(accepted2)
        self.assertTrue(detected2)
        self.assertEqual(cmd2, main._WAKE_ONLY_SENTINEL)

        # 3. Texte après le mot-clé -> priorité absolue
        accepted3, cmd3, detected3 = main.extract_voice_interact_command("Yo Moctar ouvre YouTube", session_active=False)
        self.assertTrue(accepted3)
        self.assertTrue(detected3)
        self.assertEqual(cmd3, "ouvre YouTube")

        # 4. Phrase résiduelle de moins de 4 caractères en session active -> ignorée
        for short_noise in ("au", "et", "ah", "ha"):
            acc, _, _ = main.extract_voice_interact_command(short_noise, session_active=True)
            self.assertFalse(acc, f"{short_noise} doit être ignoré")

        # 5. Paroles ambiantes sans mot-clé hors session -> strictement rejetées
        for ambient in (
            "Bon, on va dire que c'est bon.",
            "Non, non, non, mon frère.",
            "du système",
            "du pc",
            "oui",
            "non",
        ):
            acc, cmd, detected = main.extract_voice_interact_command(ambient, session_active=False)
            self.assertFalse(acc, f"{ambient} sans mot-clé doit être ignoré")
            self.assertFalse(detected, f"{ambient} ne doit pas détecter de mot-clé")
            self.assertEqual(cmd, "")

    def test_voice_interact_rejects_ambient_speech_without_wake_word(self) -> None:
        """Vérifie que les bruits de fond et paroles de la pièce sans mot-clé hors session sont rejetés sans LLM."""
        wav_bytes = encode_pcm16_wav(struct.pack("<hhhh", 2000, -2000, 2000, -2000))
        jarvis_mock = AsyncMock()
        self.app.state.runtime.jarvis = SimpleNamespace(execute_prompt_async=jarvis_mock)
        self.app.state.runtime.voice_session_until = 0.0

        for ambient_phrase in (
            "Bon, on va dire...",
            "Non, non, non, mon frère.",
            "du système",
            "du pc",
        ):
            with patch("main.voice_transcribe.transcribe_audio", return_value=ambient_phrase):
                resp = self.client.post(
                    "/api/voice/interact",
                    content=wav_bytes,
                    headers={"Content-Type": "audio/wav"},
                )
                self.assertEqual(resp.status_code, 200)
                body = resp.json()
                self.assertTrue(body["ok"])
                self.assertTrue(body["ignored"])
                self.assertEqual(body["reason"], "wake_word_missing")
                self.assertEqual(body["user_text"], ambient_phrase)
                self.assertEqual(body["reply_text"], "")
                jarvis_mock.assert_not_awaited()

    def test_voice_fast_llm_reply_groq_llama_call(self) -> None:
        """Vérifie l'appel direct à Groq Llama-3.3-70B avec télémétrie vivante injectée."""
        import ai_brain

        fake_resp = Mock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "choices": [{"message": {"content": "La vitesse de la lumière est d'environ 300 000 km/s, Moctar."}}],
        }

        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test_mock_key"}), patch(
            "requests.post", return_value=fake_resp
        ) as post_mock:
            reply = ai_brain.voice_fast_llm_reply("Quelle est la vitesse de la lumière ?")
            self.assertEqual(reply, "La vitesse de la lumière est d'environ 300 000 km/s, Moctar.")
            post_mock.assert_called_once()
            call_kwargs = post_mock.call_args.kwargs
            self.assertEqual(call_kwargs["headers"]["Authorization"], "Bearer gsk_test_mock_key")
            payload = call_kwargs["json"]
            self.assertEqual(payload["model"], "llama-3.3-70b-versatile")
            self.assertEqual(payload["temperature"], 0.3)
            self.assertEqual(payload["max_tokens"], 250)
            self.assertEqual(call_kwargs["timeout"], 8.0)
            system_msg = payload["messages"][0]["content"]
            self.assertIn("TÉLÉMÉTRIE EN TEMPS RÉEL DU SERVEUR DELL PRECISION M4800", system_msg)
            self.assertIn("Date et heure :", system_msg)
            self.assertIn("Charge CPU :", system_msg)

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


    def test_voice_interact_executes_autonomous_open_browser(self) -> None:
        """Vérifie que 'Yo Moctar ouvre YouTube' exécute immédiatement open_site sans LLM et renvoie action='open_browser' avec l'URL."""
        wav_bytes = encode_pcm16_wav(struct.pack("<hhhh", 2000, -2000, 2000, -2000))
        with patch("main.voice_transcribe.transcribe_audio", return_value="Yo Moctar ouvre YouTube"):
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
            self.assertEqual(data["action"], "open_browser")
            self.assertEqual(data["url"], "https://www.youtube.com")
            self.assertIn("YouTube", data["reply_text"])
            self.assertTrue(data["reply_text"].endswith("Moctar."))

    def test_voice_interact_executes_autonomous_add_todo_and_list(self) -> None:
        """Vérifie que les ordres d'ajout et consultation de tâches s'exécutent réellement dans SQLite/JSON."""
        wav_bytes = encode_pcm16_wav(struct.pack("<hhhh", 2000, -2000, 2000, -2000))
        with patch("main.voice_transcribe.transcribe_audio", return_value="Yo Moctar ajoute la tâche vérifier les panneaux solaires"):
            resp = self.client.post(
                "/api/voice/interact",
                content=wav_bytes,
                headers={"Content-Type": "audio/wav"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["ok"])
            self.assertIn("vérifier les panneaux solaires", data["reply_text"])
            self.assertTrue(data["reply_text"].endswith("Moctar."))

        with patch("main.voice_transcribe.transcribe_audio", return_value="Yo Moctar liste mes tâches"):
            resp_list = self.client.post(
                "/api/voice/interact",
                content=wav_bytes,
                headers={"Content-Type": "audio/wav"},
            )
            self.assertEqual(resp_list.status_code, 200)
            data_list = resp_list.json()
            self.assertTrue(data_list["ok"])
            self.assertIn("vérifier les panneaux solaires", data_list["reply_text"])

    def test_voice_interact_executes_autonomous_google_and_youtube_searches(self) -> None:
        """Vérifie que la recherche Google et YouTube renvoient l'action 'open_browser' avec l'URL de recherche correspondante."""
        wav_bytes = encode_pcm16_wav(struct.pack("<hhhh", 2000, -2000, 2000, -2000))
        # Google
        with patch("main.voice_transcribe.transcribe_audio", return_value="Yo Moctar cherche sur Google le prix de l'onduleur"):
            resp = self.client.post(
                "/api/voice/interact",
                content=wav_bytes,
                headers={"Content-Type": "audio/wav"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["action"], "open_browser")
            self.assertIn("google.com/search?q=", data["url"])
            self.assertIn("prix", data["url"])

        # YouTube Musique
        with patch("main.voice_transcribe.transcribe_audio", return_value="Yo Moctar mets du Coran sur YouTube"):
            resp_yt = self.client.post(
                "/api/voice/interact",
                content=wav_bytes,
                headers={"Content-Type": "audio/wav"},
            )
            self.assertEqual(resp_yt.status_code, 200)
            data_yt = resp_yt.json()
            self.assertEqual(data_yt["action"], "open_browser")
            self.assertIn("youtube.com/results?search_query=", data_yt["url"])
            self.assertIn("Coran", data_yt["url"])


    def test_weather_service_open_meteo_report_and_caching(self) -> None:
        """Vérifie le connecteur météo Open-Meteo, le géocodage et la mise en cache."""
        import weather_service

        # 1. Extraction de la ville
        self.assertEqual(weather_service.extract_city_from_query("quel temps fait-il à Paris ?"), "Paris")
        self.assertEqual(weather_service.extract_city_from_query("météo à Casablanca"), "Casablanca")
        self.assertEqual(weather_service.extract_city_from_query("donne-moi la météo"), "Niamey")

        # 2. Rapport météo
        fake_payload = {
            "current": {
                "temperature_2m": 31.4,
                "apparent_temperature": 33.0,
                "relative_humidity_2m": 38,
                "wind_speed_10m": 12.0,
                "weather_code": 0,
            },
            "daily": {
                "weather_code": [0, 1],
                "temperature_2m_max": [39.0, 38.0],
                "temperature_2m_min": [25.0, 24.0],
            },
        }
        mock_resp = Mock()
        mock_resp.read.return_value = json.dumps(fake_payload).encode("utf-8")
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            report = weather_service.get_weather_report("Niamey")
            self.assertTrue(report["success"])
            self.assertEqual(report["city"], "Niamey")
            self.assertEqual(report["country"], "Niger")
            self.assertIn("31°C", report["oral_text"])
            self.assertIn("ciel totalement dégagé", report["oral_text"])
            self.assertTrue(report["oral_text"].endswith("Moctar."))

            # Deuxième appel immédiat : vérification de la lecture en cache
            cached_report = weather_service.get_weather_report("Niamey")
            self.assertTrue(cached_report["success"])
            self.assertEqual(cached_report["oral_text"], report["oral_text"])

    def test_web_search_service_factual_retrieval(self) -> None:
        """Vérifie l'enrichissement par recherche web (DuckDuckGo et Wikipédia) et la détection."""
        import web_search_service

        # Détection de requêtes nécessitant le web
        self.assertTrue(web_search_service.is_live_web_query("cherche sur le web le vainqueur du match"))
        self.assertTrue(web_search_service.is_live_web_query("qui est Elon Musk"))
        self.assertTrue(web_search_service.is_live_web_query("qu'est-ce que l'énergie photovoltaïque"))
        self.assertFalse(web_search_service.is_live_web_query("bonjour"))

        # Extraction de résumé Wikipédia mocké
        wiki_data = {"extract": "Niamey est la capitale et la plus grande ville du Niger."}
        mock_resp = Mock()
        mock_resp.read.return_value = json.dumps(wiki_data).encode("utf-8")
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            summary = web_search_service.search_wikipedia_summary("qui est Niamey")
            self.assertEqual(summary, "Niamey est la capitale et la plus grande ville du Niger.")

    def test_voice_fast_llm_reply_multi_turn_history_injection(self) -> None:
        """Vérifie que l'historique multi-tours (3-4 messages précédents) est injecté dans le payload Groq."""
        import ai_brain

        fake_resp = Mock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "choices": [{"message": {"content": "Elle compte environ 1,5 million d'habitants, Moctar."}}],
        }

        prior_history = [
            {"role": "user", "content": "Quelle est la capitale du Niger ?"},
            {"role": "assistant", "content": "C'est Niamey, Moctar."},
        ]

        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test_key"}), patch(
            "requests.post", return_value=fake_resp
        ) as post_mock:
            reply = ai_brain.voice_fast_llm_reply(
                "Combien d'habitants y vivent ?",
                history=prior_history,
            )
            self.assertEqual(reply, "Elle compte environ 1,5 million d'habitants, Moctar.")
            post_mock.assert_called_once()
            payload = post_mock.call_args.kwargs["json"]
            msgs = payload["messages"]

            # System message en 0
            self.assertEqual(msgs[0]["role"], "system")
            # Tour 1 utilisateur
            self.assertEqual(msgs[1]["role"], "user")
            self.assertEqual(msgs[1]["content"], "Quelle est la capitale du Niger ?")
            # Tour 1 assistant
            self.assertEqual(msgs[2]["role"], "assistant")
            self.assertEqual(msgs[2]["content"], "C'est Niamey, Moctar.")
            # Tour 2 (question actuelle)
            self.assertEqual(msgs[3]["role"], "user")
            self.assertEqual(msgs[3]["content"], "Combien d'habitants y vivent ?")

    def test_voice_interact_executes_autonomous_weather_report(self) -> None:
        """Vérifie que la météo est exécutée en tâche autonome ultra-rapide sans passer par le LLM."""
        wav_bytes = encode_pcm16_wav(struct.pack("<hhhh", 2000, -2000, 2000, -2000))

        with patch("main.voice_transcribe.transcribe_audio", return_value="Yo Moctar quel temps fait-il à Niamey"), patch(
            "weather_service.get_weather_report",
            return_value={
                "success": True,
                "city": "Niamey",
                "country": "Niger",
                "temperature": 32.0,
                "oral_text": "À Niamey, le ciel est dégagé avec 32°C actuellement, Moctar.",
            },
        ):
            resp = self.client.post(
                "/api/voice/interact",
                content=wav_bytes,
                headers={"Content-Type": "audio/wav"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertFalse(data["ignored"])
            self.assertIn("32°C", data["reply_text"])
            self.assertTrue(data["reply_text"].endswith("Moctar."))
            self.assertIsNotNone(data["audio_url"])


if __name__ == "__main__":
    unittest.main()
