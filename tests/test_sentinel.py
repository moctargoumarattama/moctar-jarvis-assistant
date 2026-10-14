"""Réception chiffrée, GeoIP non bloquante et publication indépendante des sondes."""
import asyncio
import copy
import json
import tempfile
import time
import unittest
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi.testclient import TestClient

import collector
from whatsapp import parse_intent
from config import Settings
from main import create_app
from sentinel import SentinelManager, local_day
from sentinel_crypto import encrypt


TOKEN = "sentinel-test-secret"


def packet(number=1, kind="incident"):
    now = time.time()
    result = {"kind": kind, "event_id": f"incident-{number:08d}", "sent_at": now,
              "agent": "vps-production", "day": local_day(now), "blocked_today": number}
    if kind == "incident":
        result.update(source_ip="8.8.8.8", username="root", password="test-password",
                      blocked=True, new_block=True, capture="credentials",
                      timestamp=datetime.fromtimestamp(now, timezone.utc).isoformat())
    return result


def snapshot():
    return {"timestamp": collector.utc_now(), "hostname": "dell", "platform": "Linux",
            "uptime_seconds": 1, "global_status": "operational", "hardware": {},
            "services": [], "infrastructure": {}, "vps": [], "events": [],
            "actions": {"restart_enabled": False},
            "collector": {"last_success": collector.utc_now(), "error": None}}


class SentinelAPITests(unittest.TestCase):
    def setUp(self):
        # Les tests Sentinel ne lancent pas d'import natif Whisper à chaque API.
        readiness = patch("main.WhatsAppController.refresh_voice_readiness", new=AsyncMock())
        readiness.start()
        self.addCleanup(readiness.stop)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.settings = Settings(vps_token=TOKEN, admin_phone_number="212770010264",
                                 whatsapp_webhook_token="webhook-secret", interval=60,
                                 sentinel_geoip_enabled=False,
                                 sentinel_state_path=str(Path(directory) / "counter.json"))
        for method in ("start", "close"):
            self.stack.enter_context(patch(f"main.collector.Collector.{method}", new=AsyncMock()))
        self.collect = self.stack.enter_context(patch("main.collector.Collector.collect", new=AsyncMock(side_effect=lambda: copy.deepcopy(snapshot()))))
        self.application = create_app(self.settings)
        self.client = self.stack.enter_context(TestClient(self.application))
        self.runtime = self.application.state.runtime

    def post(self, body, token=TOKEN):
        return self.client.post("/api/sentinel/incident", json=body,
                                headers={"X-VPS-Token": token})

    def test_incident_requires_token_and_authenticated_encryption(self):
        valid = encrypt(packet(), TOKEN)
        self.assertEqual(self.post(valid, "wrong").status_code, 401)
        self.assertEqual(self.post(packet()).status_code, 422)
        self.assertEqual(self.post(encrypt(packet(), "wrong")).status_code, 422)
        self.assertEqual(len(self.runtime.sentinel.incidents), 0)

    def test_incident_publishes_immediately_without_recollecting(self):
        calls = self.collect.await_count
        last_collected = self.runtime.last_published
        revision = self.runtime.revision
        self.assertEqual(self.post(encrypt(packet(), TOKEN)).status_code, 200)
        data = self.client.get("/api/snapshot").json()
        self.assertEqual(data["sentinel"]["blocked_today"], 1)
        self.assertEqual(data["sentinel"]["incidents"][0]["username"], "root")
        self.assertEqual(data["sentinel"]["agent_status"], "active")
        self.assertGreater(self.runtime.revision, revision)
        self.assertEqual(self.collect.await_count, calls)
        self.assertEqual(self.runtime.last_published, last_collected)

    def test_replayed_reports_are_idempotent_and_daily_count_is_monotonic(self):
        report = encrypt(packet(5), TOKEN)
        self.assertEqual(self.post(report).status_code, 200)
        repeated = self.post(report)
        self.assertTrue(repeated.json()["duplicate"])
        self.post(encrypt(packet(2), TOKEN))
        self.assertEqual(self.runtime.sentinel.snapshot()["blocked_today"], 5)
        self.assertEqual(len(self.runtime.sentinel.incidents), 2)
        counter = json.loads(Path(self.settings.sentinel_state_path).read_text())
        self.assertEqual(counter["agents"]["vps-production"], 5)

    def test_ring_buffer_is_fifty_and_credentials_are_not_in_general_logs(self):
        for number in range(1, 54):
            self.assertEqual(self.post(encrypt(packet(number), TOKEN)).status_code, 200)
        radar = self.runtime.sentinel.snapshot()
        self.assertEqual(len(radar["incidents"]), 50)
        self.assertEqual(radar["blocked_today"], 53)
        self.assertNotIn("test-password", json.dumps(list(self.runtime.collector.events)))

    def test_expired_invalid_ips_and_private_bans_are_rejected(self):
        for change in ({"sent_at": time.time() - 1000}, {"source_ip": "not-an-ip"},
                       {"source_ip": "192.168.11.103"}, {"day": "1900-01-01"},
                       {"password": "control\ncharacters"}, {"extra": "forbidden"}):
            with self.subTest(change=change):
                self.assertEqual(self.post(encrypt({**packet(), **change}, TOKEN)).status_code, 422)

    def test_heartbeat_maintains_status_and_detects_disconnect(self):
        self.assertEqual(self.post(encrypt(packet(3, "heartbeat"), TOKEN)).status_code, 200)
        self.assertEqual(len(self.runtime.sentinel.incidents), 0)
        self.runtime.sentinel.agents["vps-production"]["seen"] = time.monotonic() - 100
        self.assertEqual(self.runtime.sentinel.snapshot()["agent_status"], "disconnected")

    def test_voice_webhook_checks_secret_and_exact_admin_before_enqueue(self):
        body = {"event": "messages.upsert", "sessionId": "1", "data": {
            "from": "212770010264@s.whatsapp.net", "fromMe": False,
            "id": "whatsapp-1", "messageType": "conversation", "text": "statut"}}
        accepted = self.stack.enter_context(patch.object(self.runtime.whatsapp, "accept", new=AsyncMock(return_value={"accepted": True})))
        self.assertEqual(self.client.post("/api/whatsapp/webhook", json=body).status_code, 403)
        accepted.assert_not_awaited()
        headers = {"X-Gateway-Secret": "webhook-secret"}
        self.assertEqual(self.client.post("/api/whatsapp/webhook", json=None, headers=headers).status_code, 415)
        self.assertEqual(self.client.post("/api/whatsapp/webhook", json=[], headers=headers).status_code, 422)
        self.assertEqual(self.client.post("/api/whatsapp/webhook", json=body, headers=headers).status_code, 200)
        accepted.side_effect = PermissionError()
        self.assertEqual(self.client.post("/api/whatsapp/webhook", json=body, headers=headers).status_code, 403)

    def test_command_queue_overload_does_not_block_existing_sse(self):
        accepted = self.stack.enter_context(patch.object(self.runtime.whatsapp, "accept", new=AsyncMock(side_effect=asyncio.QueueFull)))
        response = self.client.post("/api/whatsapp/webhook", json={}, headers={"X-Gateway-Secret": "webhook-secret"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "5")
        self.assertEqual(self.client.get("/api/snapshot").status_code, 200)


class GeoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = SimpleNamespace(collector=SimpleNamespace(add_event=Mock()), publish_module_update=AsyncMock())
        self.manager = SentinelManager(Settings(vps_token=TOKEN), self.runtime)
        self.addAsyncCleanup(self.manager.close)

    async def test_country_cached_and_requests_only_use_validated_ip(self):
        requests = []
        def response(request):
            requests.append(str(request.url))
            return httpx.Response(200, json={"success": True, "country_code": "MA", "country": "Maroc"})
        self.manager.client = httpx.AsyncClient(transport=httpx.MockTransport(response))
        self.assertEqual(await self.manager.lookup_country("8.8.8.8"), ("MA", "Maroc"))
        self.assertEqual(await self.manager.lookup_country("8.8.8.8"), ("MA", "Maroc"))
        self.assertEqual(len(requests), 1)
        self.assertTrue(requests[0].startswith("https://ipwho.is/8.8.8.8"))

    async def test_geo_outage_falls_back_without_failing_incident(self):
        self.manager.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
        self.assertEqual(await self.manager.lookup_country("8.8.8.8"), ("", "Indéterminé"))
        self.assertEqual(await self.manager.lookup_country("192.168.11.103"), ("", "Réseau privé"))

    async def test_geo_queue_overload_keeps_incidents_and_bounded_queue(self):
        for number in range(1, 67):
            await self.manager.accept(encrypt(packet(number), TOKEN))
        self.assertEqual(len(self.manager.incidents), 50)
        self.assertEqual(self.manager.geo_queue.qsize(), 64)
        self.assertEqual(self.manager.queue_dropped, 2)
        self.assertEqual(len(self.manager.geo_cache), 0)


class VoiceRegressionTests(unittest.TestCase):
    def test_french_asr_status_homophone_stays_read_only(self):
        self.assertEqual(parse_intent("Statue des services."), ("status", None))
        self.assertEqual(parse_intent("Ne redémarre pas klasora."), ("unknown", None))
        self.assertEqual(parse_intent("statue redémarre klasora"), ("unknown", None))
