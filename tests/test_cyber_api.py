"""HTTP traps, real peer identity, durable bans and live cyber publications."""
from __future__ import annotations

import asyncio
import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from config import Settings
from cyberdefense import TRAP_PATHS
from main import create_app
from test_api import local_snapshot


class CyberAPITests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.state_path = directory / "blocked_ips.json"
        self.settings = Settings(interval=60, voice_enabled=False,
                                 vps_token="private-cyber-vps-test-token",
                                 sentinel_geoip_enabled=False,
                                 sentinel_state_path=str(directory / "sentinel.json"),
                                 cyber_state_path=str(self.state_path))
        for method in ("start", "close"):
            self.stack.enter_context(patch("main.collector.Collector." + method, new=AsyncMock()))
        self.stack.enter_context(patch("main.WhatsAppController.refresh_voice_readiness", new=AsyncMock()))
        self.collect = self.stack.enter_context(patch("main.collector.Collector.collect", new=AsyncMock(
            side_effect=lambda: copy.deepcopy(local_snapshot()))))
        self.application = create_app(self.settings)
        self.client = self.stack.enter_context(TestClient(self.application, client=("127.0.0.1", 50000)))
        self.runtime = self.application.state.runtime

    async def request_as(self, peer, path, method="GET", headers=None, payload=None):
        """Supply the ASGI connection peer directly, never through an HTTP header."""
        body = json.dumps(payload).encode() if payload is not None else b""
        request_headers = {"host": "testserver", **(headers or {})}
        if payload is not None:
            request_headers.setdefault("content-type", "application/json")
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": method, "scheme": "http",
            "path": path, "raw_path": path.encode(), "root_path": "", "query_string": b"",
            "headers": [(name.lower().encode(), value.encode()) for name, value in request_headers.items()],
            "server": ("testserver", 80), "client": (peer, 50001) if peer is not None else None,
        }
        received = False
        status, response_headers, response_body = None, [], bytearray()

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Event().wait()

        async def send(message):
            nonlocal status, response_headers
            if message["type"] == "http.response.start":
                status, response_headers = message["status"], message["headers"]
            elif message["type"] == "http.response.body":
                response_body.extend(message.get("body", b""))

        await asyncio.wait_for(self.application(scope, receive, send), timeout=3)
        return httpx.Response(status, headers=response_headers, content=bytes(response_body))

    def peer_request(self, peer, path, method="GET", headers=None, payload=None):
        return self.client.portal.call(self.request_as, peer, path, method, headers, payload)

    def cyber(self):
        response = self.client.get("/api/cyber/banned-ips")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_every_trap_accepts_get_post_and_trailing_slash_without_reading_credentials(self):
        secret = "submitted-password-must-not-be-stored"
        for path in sorted(TRAP_PATHS):
            for method in ("GET", "POST"):
                for suffix in ("", "/"):
                    with self.subTest(path=path, method=method, suffix=suffix):
                        response = self.peer_request("8.8.8.8", path + suffix, method,
                                                     payload={"username": "root", "password": secret} if method == "POST" else None)
                        self.assertEqual(response.status_code, 403)
                        self.assertNotIn(secret, response.text)
        snapshot = self.cyber()
        self.assertEqual(snapshot["active_bans"], 1)
        self.assertEqual(snapshot["blocked_today"], 1)
        self.assertEqual(snapshot["banned_ips"][0]["hits"], 4 * len(TRAP_PATHS))
        self.assertEqual({attempt["path"] for attempt in snapshot["recent_attempts"]}, set(TRAP_PATHS))
        self.assertNotIn(secret, json.dumps(snapshot))
        self.assertNotIn(secret, self.state_path.read_text(encoding="utf-8"))

    def test_banned_peer_cannot_use_dashboard_api_but_can_still_hit_the_traps(self):
        self.assertEqual(self.peer_request("8.8.8.8", "/wp-login.php").status_code, 403)
        for path in ("/", "/api/health", "/api/overview", "/api/cyber/banned-ips", "/api/stream"):
            with self.subTest(path=path):
                response = self.peer_request("8.8.8.8", path)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(self.peer_request("8.8.8.8", "/.env").status_code, 403)
        self.assertEqual(self.cyber()["banned_ips"][0]["hits"], 2)
        self.assertEqual(self.peer_request("1.1.1.1", "/api/health").status_code, 200)

    def test_proxy_headers_cannot_protect_attacker_or_ban_an_innocent_peer(self):
        response = self.peer_request("8.8.8.8", "/.env", headers={
            "x-forwarded-for": "127.0.0.1", "forwarded": "for=187.77.171.225", "cf-connecting-ip": "1.1.1.1"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual([row["ip"] for row in self.cyber()["banned_ips"]], ["8.8.8.8"])
        self.assertEqual(self.peer_request("127.0.0.1", "/wp-admin", headers={"x-forwarded-for": "1.1.1.1"}).status_code, 404)
        self.assertFalse(self.runtime.cyber.is_banned("1.1.1.1"))
        self.assertEqual(self.peer_request("1.1.1.1", "/api/health", headers={"x-forwarded-for": "8.8.8.8"}).status_code, 200)

    def test_loopback_lan_tailscale_and_protected_vps_never_become_bans(self):
        for peer in ("127.0.0.1", "::1", "192.168.11.103", "10.1.2.3", "100.68.163.14",
                     "187.77.171.225", "::ffff:187.77.171.225"):
            with self.subTest(peer=peer):
                self.assertEqual(self.peer_request(peer, "/wp-admin.php").status_code, 404)
                self.assertEqual(self.peer_request(peer, "/api/health").status_code, 200)
        snapshot = self.cyber()
        self.assertEqual(snapshot["active_bans"], 0)
        self.assertEqual(snapshot["blocked_today"], 0)
        self.assertTrue(all(row["protected"] and not row["blocked"] for row in snapshot["recent_attempts"]))

    def test_unknown_or_missing_connection_peer_is_not_a_server_error_or_a_ban(self):
        for peer in ("testclient", None):
            with self.subTest(peer=peer):
                self.assertEqual(self.peer_request(peer, "/api/config").status_code, 404)
        self.assertEqual(self.cyber()["banned_ips"], [])
        self.assertFalse(self.state_path.exists())

    def test_ipv4_mapped_peer_uses_the_same_ban_as_ipv4(self):
        self.assertEqual(self.peer_request("::ffff:8.8.8.8", "/phpmyadmin").status_code, 403)
        self.assertEqual(self.peer_request("8.8.8.8", "/api/health").status_code, 403)
        self.assertEqual(self.peer_request("::ffff:8.8.8.8", "/api/health").status_code, 403)
        self.assertEqual([row["ip"] for row in self.cyber()["banned_ips"]], ["8.8.8.8"])

    def test_ban_survives_restart_and_unban_is_durable(self):
        self.peer_request("8.8.8.8", "/actuator/env")
        stored = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["banned_ips"][0]["ip"], "8.8.8.8")
        application = create_app(self.settings)
        client = self.stack.enter_context(TestClient(application, client=("127.0.0.1", 50002)))
        restarted = application.state.runtime
        self.assertTrue(restarted.cyber.is_banned("8.8.8.8"))
        removed = client.delete("/api/cyber/unban/8.8.8.8")
        self.assertEqual(removed.status_code, 200)
        self.assertTrue(removed.json()["removed"])
        self.assertTrue(removed.json()["persisted"])
        self.assertFalse(restarted.cyber.is_banned("8.8.8.8"))
        self.assertEqual(json.loads(self.state_path.read_text())["banned_ips"], [])
        again = create_app(self.settings)
        self.stack.enter_context(TestClient(again, client=("127.0.0.1", 50003)))
        self.assertFalse(again.state.runtime.cyber.is_banned("8.8.8.8"))

    def test_invalid_unban_addresses_are_rejected_without_mutation(self):
        self.peer_request("8.8.8.8", "/.env")
        for ip in ("not-an-ip", "999.1.1.1", "8.8.8.8:80", "fe80::1%25eth0"):
            with self.subTest(ip=ip):
                self.assertEqual(self.client.delete("/api/cyber/unban/" + ip).status_code, 422)
        self.assertTrue(self.runtime.cyber.is_banned("8.8.8.8"))
        self.assertFalse(self.client.delete("/api/cyber/unban/1.1.1.1").json()["removed"])

    def test_simulation_is_persisted_and_never_blocks_real_clients(self):
        response = self.client.post("/api/cyber/simulate", json={"path": "/wp-login.php"})
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result["simulated"])
        self.assertTrue(result["persisted"])
        self.assertTrue(result["ip"].startswith("198.51.100."))
        state = self.cyber()
        self.assertEqual(state["active_bans"], 0)
        self.assertEqual(state["simulated_bans"], 1)
        self.assertEqual(state["blocked_today"], 0)
        self.assertTrue(state["banned_ips"][0]["simulated"])
        self.assertFalse(self.runtime.cyber.is_banned(result["ip"]))
        self.assertEqual(self.peer_request(result["ip"], "/api/health").status_code, 200)
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        application = create_app(self.settings)
        client = self.stack.enter_context(TestClient(application, client=("127.0.0.1", 50004)))
        restored = client.get("/api/cyber/banned-ips").json()
        self.assertEqual(restored["simulated_bans"], 1)
        self.assertFalse(application.state.runtime.cyber.is_banned(result["ip"]))

    def test_simulation_body_must_be_small_valid_json_and_an_allowlisted_path(self):
        for payload in ({}, [], {"path": 42}, {"path": "/real-admin"},
                        {"path": "/.env", "ip": "8.8.8.8"}, {"path": "/.env/"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/api/cyber/simulate", json=payload).status_code, 422)
        self.assertEqual(self.client.post("/api/cyber/simulate", content="{}", headers={"Content-Type": "text/plain"}).status_code, 415)
        self.assertEqual(self.client.post("/api/cyber/simulate", content="{bad", headers={"Content-Type": "application/json"}).status_code, 400)
        self.assertEqual(self.client.post("/api/cyber/simulate", content=b" " * 1025, headers={"Content-Type": "application/json"}).status_code, 413)
        self.assertEqual(self.client.post("/api/cyber/simulate", content=iter([b" " * 600, b" " * 600]), headers={"Content-Type": "application/json"}).status_code, 413)
        self.assertEqual(self.cyber()["banned_ips"], [])
        self.assertFalse(self.state_path.exists())

    def test_cross_origin_mutations_have_no_effect(self):
        self.client.post("/api/cyber/simulate", json={"path": "/.env"})
        simulated = self.cyber()["banned_ips"][0]["ip"]
        before = self.state_path.read_bytes()
        revision = self.runtime.revision
        for headers in ({"Origin": "https://attacker.invalid"}, {"Sec-Fetch-Site": "cross-site"},
                        {"Sec-Fetch-Site": "same-site"}, {"Origin": "null"}):
            for method, path, payload in (("POST", "/api/cyber/simulate", {"path": "/.env"}),
                                          ("POST", "/api/cyber/audit", None),
                                          ("DELETE", "/api/cyber/unban/" + simulated, None)):
                with self.subTest(headers=headers, path=path):
                    response = self.client.request(method, path, json=payload, headers=headers)
                    self.assertEqual(response.status_code, 403)
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertEqual(self.runtime.revision, revision)

    def test_export_is_a_complete_json_attachment_without_machine_secrets(self):
        self.peer_request("8.8.8.8", "/.env")
        self.client.post("/api/cyber/simulate", json={"path": "/wp-admin"})
        response = self.client.get("/api/cyber/export")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertIn(".json", response.headers["content-disposition"])
        exported, current = response.json(), self.cyber()
        self.assertEqual(set(exported), set(current))
        self.assertEqual(exported["banned_ips"], current["banned_ips"])
        self.assertEqual(exported["recent_attempts"], current["recent_attempts"])
        self.assertEqual(len(exported["traffic"]), 60)
        self.assertNotIn(self.settings.vps_token, response.text)
        self.assertNotIn(self.settings.gateway_secret, response.text)

    def test_audit_uses_real_scans_and_module_updates_do_not_recollect(self):
        self.client.post("/api/cyber/simulate", json={"path": "/.env"})
        self.assertEqual(self.cyber()["audit"]["score"], 0)
        self.peer_request("8.8.8.8", "/.env")
        self.assertGreater(self.cyber()["audit"]["score"], 0)
        calls, collected, revision = self.collect.await_count, self.runtime.last_published, self.runtime.revision
        response = self.client.post("/api/cyber/audit")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertGreater(self.runtime.revision, revision)
        self.assertEqual(self.collect.await_count, calls)
        self.assertEqual(self.runtime.last_published, collected)

    def test_persistence_failure_is_visible_and_does_not_undo_in_memory_ban(self):
        with patch.object(self.runtime.cyber, "_write_state", side_effect=OSError("private-error-details")):
            self.assertEqual(self.peer_request("8.8.8.8", "/.env").status_code, 403)
        state = self.cyber()
        self.assertTrue(self.runtime.cyber.is_banned("8.8.8.8"))
        self.assertEqual(state["persistence"]["status"], "error")
        self.assertNotIn("private-error-details", json.dumps(state))
        self.assertFalse(self.state_path.exists())

    def test_sse_receives_cyber_mutation_without_running_hardware_probes(self):
        async def exercise():
            scope = {
                "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1", "method": "GET", "scheme": "http",
                "path": "/api/stream", "root_path": "", "query_string": b"",
                "headers": [(b"host", b"testserver")],
                "server": ("testserver", 80), "client": ("127.0.0.1", 50005),
            }
            first, disconnected = asyncio.Event(), asyncio.Event()
            frames = []
            received = False

            async def receive():
                nonlocal received
                if not received:
                    received = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                await disconnected.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                body = message.get("body", b"")
                if b"event: snapshot" in body:
                    frames.append(json.loads(next(line[6:] for line in body.splitlines() if line.startswith(b"data: "))))
                    first.set()
                    if len(frames) >= 2:
                        disconnected.set()

            task = asyncio.create_task(self.application(scope, receive, send))
            try:
                await asyncio.wait_for(first.wait(), 3)
                result = await self.request_as("127.0.0.1", "/api/cyber/simulate", "POST", payload={"path": "/.env"})
                self.assertEqual(result.status_code, 200)
                await asyncio.wait_for(task, 3)
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            return frames

        calls, collected = self.collect.await_count, self.runtime.last_published
        frames = self.client.portal.call(exercise)
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0]["cyber"]["simulated_bans"], 0)
        self.assertEqual(frames[1]["cyber"]["simulated_bans"], 1)
        self.assertEqual(self.collect.await_count, calls)
        self.assertEqual(self.runtime.last_published, collected)
        self.assertEqual(self.runtime.active_streams, 0)

    def test_open_sse_closes_and_releases_its_slot_when_its_peer_is_banned(self):
        async def exercise():
            scope = {
                "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1", "method": "GET", "scheme": "http",
                "path": "/api/stream", "root_path": "", "query_string": b"",
                "headers": [(b"host", b"testserver")],
                "server": ("testserver", 80), "client": ("8.8.8.8", 50006),
            }
            first, disconnected = asyncio.Event(), asyncio.Event()
            frames = []
            received = False

            async def receive():
                nonlocal received
                if not received:
                    received = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                await disconnected.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                body = message.get("body", b"")
                if b"event: snapshot" in body:
                    frames.append(body)
                    first.set()
                    # A broken guard would emit a second frame; then finish the
                    # response so the failure reports the leak rather than hangs.
                    if len(frames) > 1:
                        disconnected.set()

            task = asyncio.create_task(self.application(scope, receive, send))
            try:
                await asyncio.wait_for(first.wait(), 3)
                await asyncio.sleep(0.02)
                response = await self.request_as("8.8.8.8", "/.env")
                self.assertEqual(response.status_code, 403)
                await asyncio.wait_for(task, 3)
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            return frames

        calls, collected = self.collect.await_count, self.runtime.last_published
        frames = self.client.portal.call(exercise)
        self.assertEqual(len(frames), 1)
        self.assertTrue(self.runtime.cyber.is_banned("8.8.8.8"))
        self.assertEqual(self.runtime.active_streams, 0)
        self.assertEqual(self.collect.await_count, calls)
        self.assertEqual(self.runtime.last_published, collected)


if __name__ == "__main__":
    unittest.main()
