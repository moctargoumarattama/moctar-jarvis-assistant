"""Les deux routes distantes utilisent le jeton chargé depuis l'environnement."""
import copy
import os
import tempfile
import time
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from config import Settings
from main import create_app
from sentinel import local_day
from sentinel_crypto import encrypt


TOKEN = "remote-environment-test-secret"


class RemoteEnvironmentTests(unittest.TestCase):
    def open_client(self, token):
        stack = ExitStack()
        self.addCleanup(stack.close)
        directory = stack.enter_context(tempfile.TemporaryDirectory())
        with patch.dict(os.environ, {
            "NOC_VPS_TOKEN": token, "NOC_SENTINEL_GEOIP": "false",
            "NOC_SENTINEL_STATE_PATH": str(Path(directory) / "counter.json"),
            "NOC_VOICE_ENABLED": "false",
        }, clear=True):
            settings = Settings.from_env()
        for method in ("start", "close"):
            stack.enter_context(patch(f"main.collector.Collector.{method}", new=AsyncMock()))
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(), "hostname": "dell",
            "platform": "Linux", "uptime_seconds": 1, "global_status": "operational",
            "hardware": {}, "services": [], "infrastructure": {}, "vps": [], "events": [],
            "actions": {"restart_enabled": False}, "collector": {},
        }
        stack.enter_context(patch("main.collector.Collector.collect", new=AsyncMock(
            side_effect=lambda: copy.deepcopy(snapshot))))
        stack.enter_context(patch("main.WhatsAppController.refresh_voice_readiness", new=AsyncMock()))
        application = create_app(settings)
        return stack.enter_context(TestClient(application)), application.state.runtime

    def payloads(self):
        now = time.time()
        vps = {"hostname": "external-vps", "cpu_percent": 2,
               "memory": {"used": 10, "total": 100, "percent": 10},
               "disk": {"used": 20, "total": 100, "percent": 20}, "services": []}
        incident = {"kind": "incident", "event_id": "environment-incident-0001",
                    "sent_at": now, "agent": "external-vps", "day": local_day(now),
                    "blocked_today": 1, "source_ip": "8.8.8.8", "username": "root",
                    "password": "attempt", "blocked": True, "new_block": True,
                    "capture": "credentials", "timestamp": datetime.fromtimestamp(now, timezone.utc).isoformat()}
        return {"/api/vps/report": vps, "/api/sentinel/incident": encrypt(incident, TOKEN)}

    def test_environment_token_accepts_both_json_reports_and_publishes_them(self):
        client, runtime = self.open_client(TOKEN)
        for path, payload in self.payloads().items():
            with self.subTest(path=path):
                response = client.post(path, json=payload, headers={"x-vps-token": TOKEN})
                self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("external-vps", runtime.vps)
        snapshot = client.get("/api/overview").json()
        self.assertEqual(snapshot["sentinel"]["blocked_today"], 1)
        self.assertEqual(snapshot["sentinel"]["agent_status"], "active")
        self.assertNotIn(TOKEN, str(snapshot))

    def test_empty_missing_and_wrong_token_do_not_store_remote_reports(self):
        for token, expected in ((TOKEN, 401), ("", 503)):
            client, runtime = self.open_client(token)
            for path, payload in self.payloads().items():
                for headers in ({}, {"x-vps-token": "wrong"}):
                    with self.subTest(token_configured=bool(token), path=path, headers=headers):
                        self.assertEqual(client.post(path, json=payload, headers=headers).status_code, expected)
            self.assertEqual(runtime.vps, {})
            self.assertEqual(len(runtime.sentinel.incidents), 0)


if __name__ == "__main__":
    unittest.main()
