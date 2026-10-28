"""Phase 9: truthful replies and optional WhatsApp audio, without real delivery."""
import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from action_status import action_status_fields
from config import Settings
from whatsapp import WhatsAppController, wants_voice_reply


@pytest.mark.parametrize("status", ["error", "failed", "blocked", "degraded_fallback"])
def test_failed_actions_never_claim_success(status):
    result = action_status_fields({"status": status})
    assert result["ok"] is False
    assert result["status"] == status
    assert result["verification_status"] == "not_verified"


def test_pending_and_completed_keep_structured_fields():
    assert action_status_fields({"status": "confirmation_required", "pending_action_id": "a"}) == {
        "ok": True, "status": "pending_confirmation", "security_status": "pending_confirmation",
        "requires_confirmation": True, "pending_action_id": "a", "verification_status": "pending_confirmation",
    }
    assert action_status_fields({"status": "completed", "verification_status": "verified"})["verification_status"] == "verified"


def test_unavailable_semantic_fallback_and_local_failures_are_truthful():
    import main
    assert action_status_fields({"status": "degraded"})["ok"] is False
    assert main.instant_local_status("Impossible d'activer la vision.")["ok"] is False
    assert main.instant_local_status("Action refusée : service protégé.")["status"] == "blocked"


@pytest.mark.parametrize("message", [{"audio": {"path": "a.ogg"}}, {"source": "voice"},
    {"voice_reply_requested": True}, {"text": "Réponds à cette question en vocal"}])
def test_whatsapp_audio_is_opt_in(message):
    assert wants_voice_reply(message)
    assert not wants_voice_reply({"text": "statut"})


def test_worker_sends_both_text_and_generated_audio():
    async def run():
        received = []
        audio = base64.b64encode(b"ID3test-mp3").decode()
        runtime = SimpleNamespace(publish_module_update=AsyncMock(),
            collector=SimpleNamespace(add_event=lambda *args: None),
            synthesize_whatsapp_reply=AsyncMock(return_value={"voice_reply_b64": audio, "voice_mime_type": "audio/mpeg"}))
        settings = Settings(admin_phone_number="212770010264", whatsapp_webhook_token="test-token")
        controller = WhatsAppController(settings, runtime, AsyncMock())
        controller._transcribe = AsyncMock(return_value="statut")
        controller._execute = AsyncMock(return_value=("Tout est prêt", True))
        def capture(request):
            received.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        controller.client = httpx.AsyncClient(transport=httpx.MockTransport(capture))
        controller.conversation_worker = asyncio.create_task(controller._worker(controller.conversation_queue))
        try:
            await controller.accept({"id": "voice-1", "from": "212770010264", "audio": {"path": "a.ogg"}})
            await asyncio.wait_for(controller.conversation_queue.join(), 2)
            assert received[0]["reply"] == received[0]["text"] == "Tout est prêt"
            assert base64.b64decode(received[0]["voice_reply_b64"]) == b"ID3test-mp3"
            assert received[0]["voice_mime_type"] == "audio/mpeg"
            assert controller.last_command["voice_reply_sent"] is True
            assert "voice_reply_b64" not in controller.snapshot()["last_command"]
        finally:
            await controller.close()
    asyncio.run(run())


def test_tts_failure_preserves_text_and_does_not_repeat_action():
    async def run():
        runtime = SimpleNamespace(publish_module_update=AsyncMock(),
            collector=SimpleNamespace(add_event=lambda *args: None),
            synthesize_whatsapp_reply=AsyncMock(side_effect=RuntimeError("offline")))
        controller = WhatsAppController(Settings(admin_phone_number="212770010264", whatsapp_webhook_token="test-token"), runtime, AsyncMock())
        controller._execute = AsyncMock(return_value=("Réponse conservée", True))
        controller._send_reply = AsyncMock(return_value=True)
        controller.worker = asyncio.create_task(controller._worker())
        try:
            await controller.accept({"id": "text-1", "from": "212770010264", "text": "statut", "voice_reply": True})
            await asyncio.wait_for(controller.queue.join(), 2)
            controller._send_reply.assert_awaited_once_with("Réponse conservée", "212770010264")
            controller._execute.assert_awaited_once()
        finally:
            await controller.close()
    asyncio.run(run())


@pytest.mark.parametrize("status", ["success", "error", "failed", "blocked", "degraded_fallback", "pending_confirmation"])
def test_voice_endpoint_propagates_execution_metadata(monkeypatch, status):
    import time
    import main
    from fastapi.testclient import TestClient
    from proactive_companion import ProactiveCompanion
    app = main.create_app(Settings())
    runtime = SimpleNamespace(voice_session_until=time.monotonic() + 60,
        jarvis=SimpleNamespace(active_session_id="phase9-voice", memory=None),
        cyber=SimpleNamespace(is_banned=lambda peer: False, record_request_traffic=lambda path: None),
        pending_incomplete_text="", pending_incomplete_until=0)
    app.state.runtime = runtime
    monkeypatch.setattr(main, "is_self_echo_transcript", lambda *args: False)
    monkeypatch.setattr(main, "handle_instant_local_command", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "resolve_pending_voice_command", lambda *args: None)
    monkeypatch.setattr(main, "extract_voice_interact_command", lambda *args, **kwargs: (True, "Question précise", True))
    monkeypatch.setattr(main.autonomous_library, "execute_autonomous_task", lambda *args, **kwargs: {"handled": False})
    monkeypatch.setattr(main, "execute_voice_semantic_turn", lambda *args: {
        "status": status, "reply": "Résultat explicite", "pending_action_id": "test-action" if status == "pending_confirmation" else None})
    monkeypatch.setattr(main, "generate_cached_tts_chunks", AsyncMock(return_value=(None, [])))
    monkeypatch.setattr(ProactiveCompanion, "evaluate_proactive_event", lambda *args: None)
    monkeypatch.setattr(main.dynamic_attention_engine, "classify_utterance_attention", lambda *args, **kwargs: {})
    # No lifespan is entered: no service worker, camera, microphone or model starts.
    client = TestClient(app, raise_server_exceptions=True)
    try:
        response = client.post("/api/voice/interact", json={"text": "Moctar réponds à ma question"})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] == status
        assert payload["ok"] is (status in {"success", "pending_confirmation"})
        assert payload["requires_confirmation"] is (status == "pending_confirmation")
        assert "security_status" in payload and "verification_status" in payload
        assert payload["pending_action_id"] == ("test-action" if status == "pending_confirmation" else None)
    finally:
        client.close()


def test_whatsapp_tts_uses_remy_and_returns_bounded_mp3(monkeypatch):
    import main
    synthesize = AsyncMock(return_value=(b"ID3fake", False))
    monkeypatch.setattr(main, "generate_cached_tts", synthesize)
    result = asyncio.run(main.Runtime.synthesize_whatsapp_reply(SimpleNamespace(), "Bonjour"))
    synthesize.assert_awaited_once_with("Bonjour", "fr-FR-RemyMultilingualNeural")
    assert base64.b64decode(result["voice_reply_b64"]) == b"ID3fake"
    assert result["voice_mime_type"] == "audio/mpeg"
