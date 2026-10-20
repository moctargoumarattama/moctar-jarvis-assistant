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
            self.assertIn("Le point est fait", data["reply_text"])
            self.assertFalse(data["session_active"])

    def test_api_voice_interact_disengage_voila_tout_ca_aujourdhui(self) -> None:
        """Vérifie que 'Voilà, tout ça aujourd'hui.' clôture la session proprement sans confusion LLM."""
        wav_bytes = encode_pcm16_wav(struct.pack("<hhhh", 1000, -1000, 1000, -1000))
        fake_mp3 = b"ID3-DISENGAGE-MP3"

        with patch("main.voice_transcribe.transcribe_audio", return_value="Voilà, tout ça aujourd'hui."), patch(
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
            self.assertIn("Le point est fait", data["reply_text"])
            self.assertFalse(data["session_active"])

    def test_multi_turn_and_two_step_function_calling_synthesis(self) -> None:
        """Vérifie que l'historique conversationnel est préservé et que le LLM synthétise le résultat d'un outil en langage naturel."""
        from unittest.mock import MagicMock
        from voice_semantic_engine import execute_voice_semantic_turn

        mock_brain = MagicMock()
        # Tour 1: Appel de fonction get_weather
        tool_call_res = {
            "content": "",
            "tool_calls": [{"name": "get_weather", "arguments": {"city": "Niamey"}}],
            "finish_reason": "tool_calls",
        }
        # Tour 2: Synthèse vocale de l'outil
        synthesis_res = {
            "content": "À Niamey, il fait actuellement 32 degrés avec un ciel complètement dégagé.",
            "tool_calls": [],
            "finish_reason": "stop",
        }
        mock_brain.chat.side_effect = [tool_call_res, synthesis_res]

        mock_runtime = MagicMock()
        mock_runtime.ai_brain = mock_brain
        mock_runtime.jarvis.memory.get_context_window.return_value = [
            {"role": "user", "content": "Bonjour Moctar"},
            {"role": "assistant", "content": "Bonjour Moctar, je vous écoute."},
        ]

        with patch("voice_semantic_engine.execute_tool", return_value={"success": True, "data": {"city": "Niamey", "temp": 32, "condition": "ciel dégagé"}, "message": "32°C à Niamey"}):
            res = execute_voice_semantic_turn("Un météo ?", runtime=mock_runtime)

        self.assertEqual(res["status"], "success")
        self.assertTrue(res["semantic_tool_calling"])
        self.assertIn("32 degrés", res["reply"])
        self.assertTrue(res["reply"].endswith("Moctar."))
        self.assertEqual(mock_brain.chat.call_count, 2)
        first_call_msgs = mock_brain.chat.call_args_list[0].args[0]
        self.assertEqual(len(first_call_msgs), 3)
        self.assertEqual(first_call_msgs[0]["content"], "Bonjour Moctar")
        self.assertEqual(first_call_msgs[2]["content"], "Un météo ?")

    def test_api_voice_interact_stt_repair_ou_vscod(self) -> None:
        """Vérifie que la distorsion phonétique 'ou VSCOD' est réparée en amont et ouvre VS Code immédiatement."""
        wav_bytes = encode_pcm16_wav(struct.pack("<hhhh", 1000, -1000, 1000, -1000))
        fake_mp3 = b"ID3-VSCODE-MP3"

        mock_brain = Mock()
        mock_brain.chat.return_value = {
            "content": "",
            "tool_calls": [{"name": "open_app", "arguments": {"target": "vscode"}}],
            "finish_reason": "tool_calls",
        }
        self.app.state.runtime.ai_brain = mock_brain

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

    def test_dynamic_stt_acoustic_biasing_prompt(self) -> None:
        """Vérifie que build_dynamic_stt_prompt amorce Whisper avec le glossaire actif et le contexte du Dell M4800."""
        import voice_transcribe

        # 1. Prompt de base
        prompt_default = voice_transcribe.build_dynamic_stt_prompt()
        self.assertIn("Moctar", prompt_default)
        self.assertIn("M.O.C.T.A.R", prompt_default)
        self.assertIn("Dell Precision M4800", prompt_default)
        self.assertIn("VS Code", prompt_default)
        self.assertIn("mode bureau", prompt_default)
        self.assertLessEqual(len(prompt_default), 850)

        # 2. Biasing avec fenêtre active VS Code
        fake_vscode = {"available": True, "app_name": "vscode", "window_title": "voice_transcribe.py - Jarvis"}
        prompt_vscode = voice_transcribe.build_dynamic_stt_prompt(active_window=fake_vscode)
        self.assertIn("Éditeur VS Code", prompt_vscode)
        self.assertIn("débogage", prompt_vscode)

        # 3. Biasing avec fenêtre active Terminal
        fake_term = {"available": True, "app_name": "terminal", "window_title": "moctar@Precision-M4800:~"}
        prompt_term = voice_transcribe.build_dynamic_stt_prompt(active_window=fake_term)
        self.assertIn("Terminal bash", prompt_term)
        self.assertIn("systemctl", prompt_term)

    def test_vad_semantic_incomplete_utterance_detection(self) -> None:
        """Vérifie la détection chirurgicale des énoncés inachevés (connecteurs terminaux, ellipses)."""
        # A) Phrases inachevées (doivent être détectées)
        incomplete_examples = [
            "ouvre VS Code et",
            "attends voir parce que",
            "je voudrais que...",
            "mets la musique de",
            "vérifie les",
            "si on regarde dans",
            "peux-tu",
            "relance le",
        ]
        for phrase in incomplete_examples:
            self.assertTrue(
                dynamic_attention_engine.is_incomplete_utterance(phrase),
                f"Échec détection inachevée pour '{phrase}'",
            )

        # B) Phrases achevées (ne doivent PAS être détectées comme inachevées)
        complete_examples = [
            "ouvre VS Code",
            "donne la météo à Niamey",
            "ferme ça",
            "mets-le à droite",
            "quel temps fait-il",
            "qui es-tu",
        ]
        for phrase in complete_examples:
            self.assertFalse(
                dynamic_attention_engine.is_incomplete_utterance(phrase),
                f"Faux positif inachevé pour '{phrase}'",
            )

    def test_classify_utterance_attention_with_incomplete_speech(self) -> None:
        """Vérifie le routage attentionnel prédictif pour les phrases coupées en cours de route."""
        # En session active avec fin inachevée -> maintien silencieux
        att_session = dynamic_attention_engine.classify_utterance_attention("ouvre VS Code et", is_session_active=True)
        self.assertTrue(att_session["is_incomplete"])
        self.assertTrue(att_session["is_thinking_out_loud"])
        self.assertFalse(att_session["is_directed"])
        self.assertEqual(att_session["reason"], "incomplete_utterance")

        # Hors session avec mot-clé mais phrase inachevée -> ouverture de session silencieuse
        att_wake = dynamic_attention_engine.classify_utterance_attention("Yo Moctar, je voudrais...", is_session_active=False)
        self.assertTrue(att_wake["is_incomplete"])
        self.assertTrue(att_wake["is_thinking_out_loud"])
        self.assertEqual(att_wake["reason"], "incomplete_utterance")

        # Hors session parole d'ambiance avec connecteur -> rejet ambient sans activation
        att_ambient = dynamic_attention_engine.classify_utterance_attention("et puis voilà", is_session_active=False)
        self.assertFalse(att_ambient["is_thinking_out_loud"])
        self.assertEqual(att_ambient["intent"], "ambient")

    def test_api_voice_interact_incomplete_utterance_silently_preserves_session(self) -> None:
        """Vérifie que /api/voice/interact ne coupe pas Moctar sur une phrase inachevée et prolonge l'écoute."""
        wav_bytes = encode_pcm16_wav(struct.pack("<hhhh", 1000, -1000, 1000, -1000))

        with patch("main.voice_transcribe.transcribe_audio", return_value="ouvre VS Code et"):
            resp = self.client.post(
                "/api/voice/interact",
                content=wav_bytes,
                headers={"Content-Type": "audio/wav", "X-Voice-Session-Active": "true"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["ok"])
            self.assertTrue(data["ignored"])
            self.assertEqual(data["reason"], "incomplete_utterance")
            self.assertEqual(data["user_text"], "ouvre VS Code et")
            self.assertEqual(data["reply_text"], "")
            self.assertIsNone(data["audio_url"])
            self.assertTrue(data["session_active"])

    def test_proactive_engineer_personality_prompt(self) -> None:
        """Vérifie que la personnalité proactive d'ingénieur de M.O.C.T.A.R est bien ancrée."""
        from voice_semantic_engine import VOICE_SEMANTIC_SYSTEM_PROMPT

        self.assertIn("copilote d'ingénierie d'élite", VOICE_SEMANTIC_SYSTEM_PROMPT)
        self.assertIn("ChatGPT Voice / Claude", VOICE_SEMANTIC_SYSTEM_PROMPT)
        self.assertIn("étape logique suivante", VOICE_SEMANTIC_SYSTEM_PROMPT)
        self.assertIn("bon sens d'ingénieur", VOICE_SEMANTIC_SYSTEM_PROMPT)
        self.assertTrue(VOICE_SEMANTIC_SYSTEM_PROMPT.strip().endswith("'Moctar'."))

    def test_stt_repair_whisper_logs_distortions(self) -> None:
        """Vérifie la réparation des distorsions réelles capturées dans les logs de Moctar."""
        # 1. 'J'ai qui, toi ?' / 'Rikitoa.' -> 'tu es qui toi'
        t1, _ = stt_phonetic_repair.repair_stt_transcript("J'ai qui, toi ?")
        self.assertIn("tu es qui toi", t1.lower())

        t2, _ = stt_phonetic_repair.repair_stt_transcript("Rikitoa.")
        self.assertIn("tu es qui toi", t2.lower())

        # 2. 'Qu'en ton job ?' -> 'c'est quoi ton job'
        t3, _ = stt_phonetic_repair.repair_stt_transcript("Qu'en ton job ?")
        self.assertIn("c'est quoi ton job", t3.lower())

        # 3. 'Active le mode bureau.' -> 'active le mode bureau'
        t4, _ = stt_phonetic_repair.repair_stt_transcript("Active le mode bureau.")
        self.assertEqual(t4, "active le mode bureau.")

        # 4. 'Et on va passer à ceci.' -> 'on va passer à ceci'
        t5, _ = stt_phonetic_repair.repair_stt_transcript("Et on va passer à ceci.")
        self.assertIn("on va passer à ceci", t5.lower())

    def test_is_direct_voice_command_identity_and_job(self) -> None:
        """Vérifie que les questions d'identité et de rôle sont reconnues comme des ordres opérationnels directs."""
        for phrase in ("C'est quoi ton job ?", "Tu es qui toi ?", "Quel est ton job ?", "Tu sers à quoi ?", "On va passer à ceci"):
            self.assertTrue(main.is_direct_voice_command(phrase), f"Échec is_direct_voice_command pour '{phrase}'")

    def test_handle_instant_local_command_job_and_transition(self) -> None:
        """Vérifie les réponses instantanées (0 ms) pour le rôle et la transition collaborative."""
        # Job / Rôle
        res_job = main.handle_instant_local_command("c'est quoi ton job ?")
        self.assertIsNotNone(res_job)
        self.assertIn("copilote d'ingénierie", res_job)
        self.assertIn("Dell Precision M4800", res_job)
        self.assertTrue(res_job.endswith("Moctar."))

        # Tu es qui toi
        res_who = main.handle_instant_local_command("tu es qui toi ?")
        self.assertIsNotNone(res_who)
        self.assertIn("copilote d'ingénierie", res_who)

        # Transition
        res_trans = main.handle_instant_local_command("on va passer à ceci")
        self.assertIsNotNone(res_trans)
        self.assertIn("Je vous écoute Moctar", res_trans)

    def test_dynamic_attention_omni_attention_for_direct_queries(self) -> None:
        """Vérifie que les questions directes à la 2e personne sont traitées en directed speech même hors session."""
        for q in ("C'est quoi ton job ?", "Tu es qui toi ?", "On va passer à ceci"):
            att = dynamic_attention_engine.classify_utterance_attention(q, is_session_active=False)
            self.assertTrue(att["is_directed"], f"Échec directed pour '{q}'")
            self.assertEqual(att["intent"], "directed_speech")

    def test_continuous_session_active_default_45_seconds(self) -> None:
        """Vérifie que la session d'écoute reste active 45s via followupMs et enchaîne les répliques sans wake-word."""
        index_html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn("followupMs: 45000", index_html)

        wav_bytes = encode_pcm16_wav(struct.pack("<hhhh", 1000, -1000, 1000, -1000))
        fake_mp3 = b"ID3-AUDIO-MP3"

        with patch.object(main, "VOICE_SESSION_DURATION_SECONDS", 45.0):
            # Tour 1 : activation mode bureau
            with patch("main.voice_transcribe.transcribe_audio", return_value="Active le mode bureau."), patch(
                "main.synthesize_edge_tts", new=AsyncMock(return_value=fake_mp3)
            ):
                resp1 = self.client.post(
                    "/api/voice/interact",
                    content=wav_bytes,
                    headers={"Content-Type": "audio/wav"},
                )
                self.assertEqual(resp1.status_code, 200)
                data1 = resp1.json()
                self.assertTrue(data1["ok"])
                self.assertFalse(data1["ignored"])
                self.assertTrue(data1["session_active"])

            # Tour 2 : enchaînement immédiat sans mot de réveil ("C'est quoi ton job ?")
            with patch("main.voice_transcribe.transcribe_audio", return_value="C'est quoi ton job ?"), patch(
                "main.synthesize_edge_tts", new=AsyncMock(return_value=fake_mp3)
            ):
                resp2 = self.client.post(
                    "/api/voice/interact",
                    content=wav_bytes,
                    headers={"Content-Type": "audio/wav", "X-Voice-Session-Active": "true"},
                )
                self.assertEqual(resp2.status_code, 200)
                data2 = resp2.json()
                self.assertTrue(data2["ok"])
                self.assertFalse(data2["ignored"])
                self.assertIn("copilote d'ingénierie", data2["reply_text"])
                self.assertTrue(data2["session_active"])


if __name__ == "__main__":
    unittest.main()

