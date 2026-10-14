"""Readiness réelle hors processus, livraison audio et simulations sans effets."""
from __future__ import annotations

import asyncio
import copy
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

from config import Settings
from main import create_app
from test_api import local_snapshot
from whatsapp import WhatsAppController, parse_intent, parse_message

ADMIN = "212770010264"


class VoiceReadinessTests(unittest.IsolatedAsyncioTestCase):
    def controller(self, **kwargs):
        runtime = SimpleNamespace(publish_module_update=AsyncMock())
        return WhatsAppController(Settings(whisper_python=sys.executable, **kwargs), runtime, AsyncMock())

    async def test_ready_does_not_depend_on_whatsapp_token(self):
        controller = self.controller()
        result = {"status": "ready", "model": "tiny", "model_path": "/models/whisper", "reason": "Prêt"}
        with patch("whatsapp.shutil.which", return_value="ffmpeg"), \
             patch("whatsapp.run_process", new=AsyncMock(return_value=(0, json.dumps(result).encode()))) as runner:
            state = await controller.refresh_voice_readiness()
        self.assertTrue(state["enabled"])
        self.assertFalse(state["webhook_configured"])
        self.assertEqual(state["status"], "ready")
        self.assertIn("--check", runner.call_args.args[0])
        self.assertNotIn("--audio", runner.call_args.args[0])
        controller.busy = True
        self.assertEqual(controller.snapshot()["status"], "active")

    async def test_missing_model_explains_download_without_claiming_ready(self):
        controller = self.controller()
        result = {"status": "missing_model", "model": "base", "model_path": None,
                  "reason": "Modèle absent : lancez install-voice.sh", "download_available": True}
        with patch("whatsapp.shutil.which", return_value="ffmpeg"), \
             patch("whatsapp.run_process", new=AsyncMock(return_value=(0, json.dumps(result).encode()))):
            state = await controller.refresh_voice_readiness()
        self.assertFalse(state["enabled"])
        self.assertEqual(state["status"], "missing_model")
        self.assertTrue(state["download_available"])
        self.assertIn("install-voice.sh", state["reason"])

    async def test_falls_back_to_main_venv_when_configured_python_lacks_dependency(self):
        with tempfile.NamedTemporaryFile() as executable:
            controller = self.controller()
            controller.settings = replace(controller.settings, whisper_python=executable.name)
            missing = {"status": "disabled", "reason": "faster-whisper absent"}
            ready = {"status": "ready", "model_path": "/models/whisper", "reason": "Prêt"}
            with patch("whatsapp.shutil.which", return_value="ffmpeg"), \
                 patch("whatsapp.run_process", new=AsyncMock(side_effect=[
                     (0, json.dumps(missing).encode()), (0, json.dumps(ready).encode())])) as runner:
                state = await controller.refresh_voice_readiness()
            self.assertTrue(state["enabled"])
            self.assertEqual(controller._voice_python, sys.executable)
            self.assertEqual(runner.await_count, 2)

    async def test_explicit_disable_or_missing_ffmpeg_never_imports_whisper(self):
        for disabled in (True, False):
            controller = self.controller(voice_enabled=not disabled)
            with patch("whatsapp.shutil.which", return_value=None), \
                 patch("whatsapp.run_process", new=AsyncMock()) as runner:
                state = await controller.refresh_voice_readiness()
            self.assertFalse(state["enabled"])
            self.assertEqual(state["status"], "disabled")
            runner.assert_not_awaited()

    async def test_failed_checker_returns_clear_state_and_no_native_error(self):
        controller = self.controller()
        with patch("whatsapp.shutil.which", return_value="ffmpeg"), \
             patch("whatsapp.run_process", new=AsyncMock(side_effect=OSError("private-native-error"))):
            state = await controller.refresh_voice_readiness()
        self.assertFalse(state["enabled"])
        self.assertNotIn("private-native-error", json.dumps(state))

    async def test_local_audio_formats_keep_the_same_directory_restrictions(self):
        controller = self.controller()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media = root / "media"
            media.mkdir()
            controller.settings = replace(controller.settings, whatsapp_media_dir=str(media))
            for suffix in (".ogg", ".opus", ".mp3", ".wav"):
                source = media / ("note" + suffix)
                source.write_bytes(b"audio")
                controller._copy_local_audio(str(source), root / "copy.audio")
                self.assertEqual((root / "copy.audio").read_bytes(), b"audio")
                with self.assertRaises(ValueError):
                    controller._copy_local_audio(str(root / source.name), root / "copy.audio")

    async def test_remote_engine_initializes_even_without_whatsapp_secret(self):
        controller = self.controller(voice_remote_url="http://100.68.163.14:9000/transcribe", voice_remote_token="test-remote")
        try:
            with patch("whatsapp.shutil.which", return_value="ffmpeg"), \
                 patch.object(controller, "_readiness_loop", new=AsyncMock()):
                await controller.start()
                state = await controller.refresh_voice_readiness()
            self.assertTrue(state["enabled"])
            self.assertEqual(state["model"], "remote")
            self.assertIsNone(controller.client)
            self.assertIsNotNone(controller.remote_client)
        finally:
            await controller.close()


class VoiceAPITests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ("start", "close"):
            self.stack.enter_context(patch("main.collector.Collector." + name, new=AsyncMock()))
        self.stack.enter_context(patch("main.collector.Collector.collect", new=AsyncMock(side_effect=lambda: copy.deepcopy(local_snapshot()))))
        self.stack.enter_context(patch("main.WhatsAppController.refresh_voice_readiness", new=AsyncMock()))
        self.settings = Settings(admin_token="admin-secret", whatsapp_webhook_token="webhook-secret", interval=60,
                                 whatsapp_max_audio_bytes=32, sentinel_geoip_enabled=False)
        self.app = create_app(self.settings)
        self.client = self.stack.enter_context(TestClient(self.app))
        self.runtime = self.app.state.runtime
        self.controller = self.runtime.whatsapp
        self.controller._voice.update(status="ready", model="tiny", model_path="/models/whisper", reason="Prêt")
        self.client.portal.call(self.runtime.publish_module_update)
        self.restart = self.controller.restart = AsyncMock()
        self.backup = self.controller._backup = AsyncMock()
        self.reply = self.controller._send_reply = AsyncMock(return_value=True)

    def drain(self):
        async def join():
            await asyncio.wait_for(self.controller.queue.join(), 2)
        self.client.portal.call(join)

    def audio_headers(self, **changes):
        return {"X-Gateway-Secret": "webhook-secret", "X-WhatsApp-From": ADMIN + "@s.whatsapp.net",
                "X-Message-Id": "audio-test", "X-Session-Id": "1", "Content-Type": "audio/ogg", **changes}

    def test_overview_and_status_expose_same_voice_contract(self):
        snapshot = self.client.get("/api/snapshot").json()["voice"]
        self.assertEqual(snapshot, self.client.get("/api/overview").json()["voice"])
        self.assertEqual(snapshot, self.client.get("/api/voice/status").json())
        self.assertTrue(snapshot["enabled"])
        self.assertEqual(snapshot["status"], "ready")
        self.assertEqual(snapshot["model"], "tiny")
        self.assertNotIn("secret", json.dumps(snapshot))
        self.assertIn("/events", {route.path for route in self.app.routes})

    def test_simulate_restart_updates_sse_state_without_restarting_or_replying(self):
        revision, collected = self.runtime.revision, self.runtime.last_published
        result = self.client.post("/api/voice/simulate", json={"text": "restart babamarket"}, headers={"X-Admin-Token": "admin-secret"})
        self.assertEqual(result.status_code, 202)
        self.assertTrue(result.json()["simulated"])
        self.drain()
        state = self.client.get("/api/overview").json()["voice"]
        self.assertEqual(state["last_command"]["action"], "restart")
        self.assertEqual(state["last_command"]["service"], "babamarket")
        self.assertEqual(state["last_command"]["source"], "simulation")
        self.assertTrue(state["last_command"]["simulated"])
        self.assertIn("SIMULATION", state["last_command"]["response"])
        self.assertGreater(self.runtime.revision, revision)
        self.assertEqual(collected, self.runtime.last_published)
        self.restart.assert_not_awaited()
        self.backup.assert_not_awaited()
        self.reply.assert_not_awaited()

    def test_simulation_is_direct_but_rejects_cross_origin_or_invalid_payload(self):
        headers = {"Origin": "https://wrong.invalid"}
        self.assertEqual(self.client.post("/api/voice/simulate", json={"text": "statut"}, headers=headers).status_code, 403)
        for body in ({"text": ""}, {"text": 12}, {"text": "statut", "execute": True}, []):
            self.assertEqual(self.client.post("/api/voice/simulate", json=body).status_code, 422)
        self.assertIsNone(self.controller.last_command)
        self.assertEqual(self.client.post("/api/voice/simulate", json={"text": "statut"}).status_code, 202)

    def test_voice_webhook_alias_accepts_original_gateway_json(self):
        body = {"event": "messages.upsert", "sessionId": "1", "data": {
            "from": ADMIN + "@s.whatsapp.net", "fromMe": False, "id": "text-test",
            "messageType": "conversation", "text": "statut"}}
        self.assertEqual(self.client.post("/api/voice/webhook", json=body, headers={"X-Gateway-Secret": "webhook-secret"}).status_code, 200)
        self.drain()
        self.assertEqual(self.controller.last_command["action"], "status")

    def test_raw_audio_is_queued_then_private_upload_is_removed(self):
        paths = []
        async def transcribe(audio):
            path = Path(audio["upload_path"])
            paths.append(path)
            self.assertEqual(path.read_bytes(), b"OggSfake-audio")
            return "statut"
        self.controller._transcribe = transcribe
        result = self.client.post("/api/voice/process", content=b"OggSfake-audio", headers=self.audio_headers())
        self.assertEqual(result.status_code, 202)
        self.drain()
        self.assertEqual(self.controller.last_command["source"], "voice")
        self.assertEqual(self.controller.last_command["status"], "completed")
        self.assertEqual(len(paths), 1)
        self.assertFalse(paths[0].exists())
        self.assertEqual(self.controller.uploading, 0)

    def test_sender_message_contract_accepts_header_id_and_rejects_conflicts(self):
        headers = {"X-Gateway-Secret": "webhook-secret", "X-Message-Id": "gateway-text-1"}
        body = {"sender": ADMIN, "message": "statut"}
        response = self.client.post("/api/whatsapp/webhook", json=body, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["accepted"])
        self.assertNotIn("reply", response.json())
        self.drain()
        self.assertEqual(self.controller.last_command["action"], "status")
        self.assertEqual(self.controller.last_command["id"], "gateway-text-1")
        repeated = self.client.post("/api/whatsapp/webhook", json=body, headers=headers)
        self.assertTrue(repeated.json()["duplicate"])
        self.assertEqual(self.client.post("/api/whatsapp/webhook", json={**body, "id": "different"}, headers=headers).status_code, 422)
        self.assertEqual(self.client.post("/api/whatsapp/webhook", json=body, headers={"X-Gateway-Secret": "webhook-secret"}).status_code, 422)

    def test_raw_audio_accepts_x_sender_and_validates_both_identity_headers(self):
        self.controller._transcribe = AsyncMock(return_value="statut")
        headers = self.audio_headers()
        headers.pop("X-WhatsApp-From")
        headers["X-Sender"] = ADMIN + ":38@s.whatsapp.net"
        response = self.client.post("/api/voice/process", content=b"OggSaudio", headers=headers)
        self.assertEqual(response.status_code, 202)
        self.drain()
        headers["X-WhatsApp-From"] = "212770010265@s.whatsapp.net"
        self.assertEqual(self.client.post("/api/voice/process", content=b"OggSaudio", headers=headers).status_code, 403)

    def test_audio_authentication_precedes_file_creation(self):
        with patch("main.tempfile.mkstemp") as create:
            for headers, expected in ((self.audio_headers(**{"X-Gateway-Secret": "wrong"}), 403),
                                      (self.audio_headers(**{"X-WhatsApp-From": "212770010265@s.whatsapp.net"}), 403),
                                      (self.audio_headers(**{"X-Session-Id": "2"}), 403),
                                      (self.audio_headers(**{"X-Message-Id": "bad/id"}), 422)):
                self.assertEqual(self.client.post("/api/voice/process", content=b"audio", headers=headers).status_code, expected)
            create.assert_not_called()

    def test_failed_transcription_cleans_upload_and_preserves_module_readiness(self):
        paths = []
        async def transcribe(audio):
            paths.append(Path(audio["upload_path"]))
            raise ValueError("Audio sans parole")
        self.controller._transcribe = transcribe
        self.assertEqual(self.client.post("/api/voice/process", content=b"OggSfake", headers=self.audio_headers()).status_code, 202)
        self.drain()
        self.assertEqual(self.controller.last_command["status"], "failed")
        self.assertTrue(self.controller.snapshot()["enabled"])
        self.assertFalse(paths[0].exists())

    def test_duplicate_upload_is_not_transcribed_twice_or_left_on_disk(self):
        self.controller._transcribe = AsyncMock(return_value="statut")
        with tempfile.TemporaryDirectory() as temporary:
            original = tempfile.mkstemp
            def create_upload(**kwargs):
                kwargs["dir"] = temporary
                return original(**kwargs)
            with patch("main.tempfile.mkstemp", side_effect=create_upload):
                first = self.client.post("/api/voice/process", content=b"OggSfake", headers=self.audio_headers())
                self.drain()
                duplicate = self.client.post("/api/voice/process", content=b"OggSfake", headers=self.audio_headers())
            self.assertEqual(first.status_code, 202)
            self.assertTrue(duplicate.json()["duplicate"])
            self.controller._transcribe.assert_awaited_once()
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_simulation_works_before_whatsapp_is_configured(self):
        app = create_app(replace(self.settings, whatsapp_webhook_token=""))
        client = self.stack.enter_context(TestClient(app))
        result = client.post("/api/voice/simulate", json={"text": "restart babamarket"}, headers={"X-Admin-Token": "admin-secret"})
        self.assertEqual(result.status_code, 202)
        async def join():
            await asyncio.wait_for(app.state.runtime.whatsapp.queue.join(), 2)
        client.portal.call(join)
        voice = client.get("/api/overview").json()["voice"]
        self.assertFalse(voice["webhook_configured"])
        self.assertTrue(voice["last_command"]["simulated"])

    def test_audio_limits_empty_files_and_wrong_mime_are_rejected(self):
        self.assertEqual(self.client.post("/api/voice/process", content=b"x" * 33, headers=self.audio_headers()).status_code, 413)
        self.assertIn(self.client.post("/api/voice/process", content=b"", headers=self.audio_headers()).status_code, (413, 422))
        self.assertEqual(self.client.post("/api/voice/process", content=b"data", headers=self.audio_headers(**{"Content-Type": "text/html"})).status_code, 415)
        self.assertEqual(self.client.post("/api/voice/process", content=b"data", headers=self.audio_headers(**{"Content-Encoding": "gzip"})).status_code, 415)
        self.assertIsNone(self.controller.last_command)

    def test_mpeg_wav_and_opus_are_supported_without_relaxing_sender_filter(self):
        for mime in ("audio/mpeg", "audio/wav", "audio/opus"):
            message = parse_message({"from": ADMIN, "id": "a", "audio": {"path": "a", "mimetype": mime}}, ADMIN)
            self.assertEqual(message["source"], "voice")
        self.assertEqual(parse_intent("restart babamarket"), ("restart", "babamarket"))
        self.assertEqual(parse_intent("do not restart babamarket"), ("unknown", None))


if __name__ == "__main__":
    unittest.main()
