"""Public monitoring, remote input validation and safe restart behavior."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import time
import unittest
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from starlette.requests import Request

import collector
from config import Settings
from main import create_app


LEGACY_VIEW_TOKEN = "obsolete-view-secret"
VPS_TOKEN = "test-vps-secret"
ADMIN_TOKEN = "test-admin-secret"


def local_snapshot() -> dict:
    return {
        "timestamp": collector.utc_now(), "hostname": "test-host", "platform": "Linux",
        "uptime_seconds": 100, "global_status": "operational", "hardware": {},
        "services": [], "infrastructure": {}, "vps": [], "events": [],
        "actions": {"restart_enabled": False},
        "collector": {"last_success": collector.utc_now(), "error": None},
    }


def remote_report(hostname: str = "vps-one") -> dict:
    return {
        "hostname": hostname, "cpu_percent": 20.0,
        "memory": {"used": 25, "total": 100, "percent": 25.0},
        "disk": {"used": 10, "total": 100, "percent": 10.0},
        "services": [{"name": "nginx", "status": "active"}],
    }


class APITests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("main.collector.Collector.start", new=AsyncMock()))
        self.stack.enter_context(patch("main.collector.Collector.close", new=AsyncMock()))
        self.stack.enter_context(patch("main.WhatsAppController.refresh_voice_readiness", new=AsyncMock()))
        self.stack.enter_context(patch("main.collector.Collector.collect", new=AsyncMock(
            side_effect=lambda: copy.deepcopy(local_snapshot()))))
        self.settings = Settings(vps_token=VPS_TOKEN,
                                 admin_token=ADMIN_TOKEN, interval=60)
        self.client, self.runtime = self.open_client(self.settings)

    def open_client(self, settings: Settings):
        application = create_app(settings)
        client = self.stack.enter_context(TestClient(application))
        return client, application.state.runtime

    def report(self, payload: dict | None = None):
        return self.client.post("/api/vps/report", json=payload or remote_report(),
                                headers={"x-vps-token": VPS_TOKEN})

    def enable_restart_host(self) -> AsyncMock:
        self.runtime.collector.systemctl = "/usr/bin/systemctl"
        self.stack.enter_context(patch("main.shutil.which", return_value="/usr/bin/sudo"))
        command = AsyncMock(return_value=(0, "", ""))
        self.stack.enter_context(patch("main.collector.run_command", new=command))
        return command

    def test_snapshot_is_public_and_preserves_security_headers(self) -> None:
        for headers in ({}, {"Authorization": "Bearer invalid"},
                        {"Authorization": "Bearer " + LEGACY_VIEW_TOKEN}):
            with self.subTest(headers=headers):
                response = self.client.get("/api/snapshot", headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["hostname"], "test-host")
                self.assertNotIn("www-authenticate", response.headers)
                self.assertNotIn("set-cookie", response.headers)
        snapshot = self.client.get("/api/snapshot")
        self.assertEqual(snapshot.status_code, 200)
        self.assertEqual(snapshot.headers["x-content-type-options"], "nosniff")
        self.assertEqual(snapshot.headers["x-frame-options"], "DENY")
        self.assertEqual(snapshot.headers["cache-control"], "no-store")

    def test_legacy_environment_tokens_and_session_cookies_do_not_block_monitoring(self) -> None:
        with patch.dict(os.environ, {"NOC_VIEW_TOKEN": LEGACY_VIEW_TOKEN,
                                     "NOC_COOKIE_SECURE": "true"}, clear=True):
            settings = Settings.from_env()
        client, _ = self.open_client(settings)
        for cookie in ("1.expired-signature", "forged-session", "x" * 101):
            with self.subTest(cookie=cookie):
                client.cookies.set("noc_session", cookie)
                response = client.get("/api/snapshot")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["hostname"], "test-host")

    def test_obsolete_login_and_logout_routes_are_removed(self) -> None:
        for path in ("/api/login", "/api/logout"):
            with self.subTest(path=path):
                response = self.client.post(path, json={"token": LEGACY_VIEW_TOKEN})
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("set-cookie", response.headers)

    def test_cross_origin_mutations_are_rejected(self) -> None:
        command = self.enable_restart_host()
        response = self.client.post("/api/service/klasora/restart", headers={
            "x-admin-token": ADMIN_TOKEN, "Origin": "https://attacker.invalid"})
        self.assertEqual(response.status_code, 403)
        command.assert_not_awaited()

    def test_malformed_origin_is_rejected_without_server_error(self) -> None:
        command = self.enable_restart_host()
        response = self.client.post("/api/service/klasora/restart", headers={
            "x-admin-token": ADMIN_TOKEN, "Origin": "http://["})
        self.assertEqual(response.status_code, 403)
        command.assert_not_awaited()

    def test_report_requires_its_own_token_and_disabled_reporting_is_closed(self) -> None:
        for headers in ({}, {"x-vps-token": LEGACY_VIEW_TOKEN}, {"x-vps-token": ADMIN_TOKEN}):
            with self.subTest(headers=headers):
                self.assertEqual(self.client.post("/api/vps/report", json=remote_report(), headers=headers).status_code, 401)
        self.assertEqual(self.runtime.vps, {})
        client, runtime = self.open_client(replace(self.settings, vps_token=""))
        response = client.post("/api/vps/report", json=remote_report(), headers={"x-vps-token": VPS_TOKEN})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(runtime.vps, {})

    def test_valid_vps_report_is_published_and_becomes_stale(self) -> None:
        self.assertEqual(self.report().status_code, 200)
        self.client.portal.call(self.runtime.publish, local_snapshot())
        published = self.client.get("/api/snapshot").json()
        self.assertEqual(published["vps"][0]["hostname"], "vps-one")
        self.assertTrue(published["vps"][0]["healthy"])
        self.assertFalse(published["vps"][0]["stale"])
        self.runtime.vps["vps-one"]["seen"] = time.monotonic() - self.settings.vps_stale_seconds - 1
        self.client.portal.call(self.runtime.publish, local_snapshot())
        published = self.client.get("/api/snapshot").json()
        self.assertTrue(published["vps"][0]["stale"])
        self.assertFalse(published["vps"][0]["healthy"])
        self.assertEqual(published["global_status"], "incident")
        self.assertEqual(self.report().status_code, 200)
        self.assertTrue(self.runtime.vps_snapshot()[0]["healthy"])

    def test_vps_incident_metrics_and_stopped_services_affect_global_health(self) -> None:
        for change in ("cpu", "memory", "disk", "service"):
            payload = remote_report()
            if change == "cpu":
                payload["cpu_percent"] = 98
            elif change in {"memory", "disk"}:
                payload[change].update(used=95, percent=95)
            else:
                payload["services"][0]["status"] = "inactive"
            with self.subTest(change=change):
                self.assertEqual(self.report(payload).status_code, 200)
                self.client.portal.call(self.runtime.publish, local_snapshot())
                self.assertFalse(self.runtime.latest["vps"][0]["healthy"])
                self.assertEqual(self.runtime.latest["global_status"], "incident")

    def test_hostnames_services_and_unexpected_fields_are_validated(self) -> None:
        invalid = []
        for hostname in ("<img src=x onerror=alert(1)>", "../server", "", "x" * 129):
            invalid.append({**remote_report(), "hostname": hostname})
        duplicate = remote_report()
        duplicate["services"] *= 2
        invalid.append(duplicate)
        unknown_service = remote_report()
        unknown_service["services"][0]["status"] = "<script>alert(1)</script>"
        invalid.append(unknown_service)
        invalid.append({**remote_report(), "unexpected": "field"})
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.report(payload).status_code, 422)
        self.assertEqual(self.runtime.vps, {})

    def test_vps_numeric_ranges_and_consistency_are_enforced(self) -> None:
        invalid = []
        for cpu in (-1, 101, "not-a-number", True):
            invalid.append({**remote_report(), "cpu_percent": cpu})
        for metric in ("memory", "disk"):
            for values in (
                {"used": -1}, {"total": 0}, {"used": 101}, {"percent": 101},
                {"percent": 60}, {"used": True}, {"total": 2**61},
            ):
                payload = remote_report()
                payload[metric].update(values)
                invalid.append(payload)
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.report(payload).status_code, 422)
        self.assertEqual(self.runtime.vps, {})

    def test_nonfinite_json_numbers_are_rejected(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            for field in ("cpu_percent", "memory", "disk"):
                payload = remote_report()
                if field == "cpu_percent":
                    payload[field] = value
                else:
                    payload[field]["percent"] = value
                with self.subTest(field=field, value=value):
                    response = self.client.post("/api/vps/report", content=json.dumps(payload),
                        headers={"x-vps-token": VPS_TOKEN, "Content-Type": "application/json"})
                    self.assertEqual(response.status_code, 422)
                    self.assertNotIn("NaN", response.text)
        self.assertEqual(self.runtime.vps, {})

    def test_body_limit_applies_to_length_header_and_streamed_chunks(self) -> None:
        headers = {"x-vps-token": VPS_TOKEN, "Content-Type": "application/json"}
        oversized = b" " * 32769 + json.dumps(remote_report()).encode()
        self.assertEqual(self.client.post("/api/vps/report", content=oversized, headers=headers).status_code, 413)
        # An iterable has no declared Content-Length: the streaming limit must still apply.
        chunks = iter([b" " * 16000, b" " * 16000, b" " * 1000])
        self.assertEqual(self.client.post("/api/vps/report", content=chunks, headers=headers).status_code, 413)
        self.assertEqual(self.runtime.vps, {})

    def test_report_json_content_type_and_syntax_are_enforced(self) -> None:
        response = self.client.post("/api/vps/report", content="{}", headers={"x-vps-token": VPS_TOKEN})
        self.assertEqual(response.status_code, 415)
        response = self.client.post("/api/vps/report", content="{bad json", headers={
            "x-vps-token": VPS_TOKEN, "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 400)

    def test_vps_count_is_bounded_and_expired_slots_are_reused(self) -> None:
        client, runtime = self.open_client(replace(self.settings, max_vps=1))
        headers = {"x-vps-token": VPS_TOKEN}
        self.assertEqual(client.post("/api/vps/report", json=remote_report("first"), headers=headers).status_code, 200)
        self.assertEqual(client.post("/api/vps/report", json=remote_report("second"), headers=headers).status_code, 429)
        self.assertEqual(client.post("/api/vps/report", json=remote_report("first"), headers=headers).status_code, 200)
        runtime.vps_snapshot()
        runtime.vps["first"]["seen"] = time.monotonic() - self.settings.vps_retention_seconds - 1
        self.assertEqual(client.post("/api/vps/report", json=remote_report("second"), headers=headers).status_code, 200)
        self.assertEqual(set(runtime.vps), {"second"})
        self.assertNotIn("vps:first", runtime.collector._states)

    def test_restart_is_direct_but_requires_an_allowlisted_service(self) -> None:
        command = self.enable_restart_host()
        for name in ("cloudflared", "klasora-whatsapp", "ssh", "klasora;id"):
            with self.subTest(name=name):
                response = self.client.post(f"/api/service/{name}/restart", headers={"x-admin-token": ADMIN_TOKEN})
                self.assertEqual(response.status_code, 404)
        command.assert_not_awaited()
        response = self.client.post("/api/service/klasora/restart")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(command.await_count, 1)

    def test_restart_uses_fixed_arguments_and_repeated_requests_are_limited(self) -> None:
        command = self.enable_restart_host()
        headers = {}
        response = self.client.post("/api/service/klasora/restart", headers=headers)
        self.assertEqual(response.status_code, 200)
        command.assert_awaited_once_with([
            "/usr/bin/sudo", "-n", "/usr/bin/systemctl", "--no-block", "restart", "klasora.service"], 5)
        self.assertEqual(self.client.post("/api/service/klasora/restart", headers=headers).status_code, 429)
        self.assertEqual(command.await_count, 1)

    def test_disabled_restart_remains_closed_and_admin_token_is_unnecessary(self) -> None:
        command = self.enable_restart_host()
        client, runtime = self.open_client(replace(self.settings, enable_restart=False, admin_token=""))
        runtime.collector.systemctl = "/usr/bin/systemctl"
        self.assertEqual(client.post("/api/service/klasora/restart").status_code, 503)
        command.assert_not_awaited()
        client, runtime = self.open_client(replace(self.settings, admin_token=""))
        runtime.collector.systemctl = "/usr/bin/systemctl"
        self.assertEqual(client.post("/api/service/klasora/restart").status_code, 200)
        self.assertEqual(command.await_count, 1)

    def test_browser_metadata_and_origin_prevent_foreign_page_actions(self) -> None:
        command = self.enable_restart_host()
        for headers in (
            {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
            {"Origin": "https://testserver"}, {"Origin": "http://testserver:8080"},
            {"Origin": "null"}, {"Origin": "http://user@testserver"},
            {"Origin": "http://testserver/path"}, {"Origin": "http://testserver?query=1"},
            {"Sec-Fetch-Site": "same-origin", "Origin": "http://elsewhere"},
            {"Sec-Fetch-Site": "cross-site", "Origin": "http://testserver"},
        ):
            with self.subTest(headers=headers):
                self.assertEqual(self.client.post("/api/service/klasora/restart", headers=headers).status_code, 403)
        command.assert_not_awaited()
        self.assertEqual(self.client.post("/api/service/klasora/restart", headers={
            "Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"}).status_code, 200)

    def test_restart_failure_and_timeout_are_reported_without_leaking_stderr(self) -> None:
        command = self.enable_restart_host()
        headers = {"x-admin-token": ADMIN_TOKEN}
        command.return_value = (1, "", "sensitive command error")
        response = self.client.post("/api/service/merca-market/restart", headers=headers)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("sensitive command error", response.text)
        command.side_effect = asyncio.TimeoutError
        response = self.client.post("/api/service/klasora/restart", headers=headers)
        self.assertEqual(response.status_code, 504)
        self.assertEqual(self.runtime.collector.events[-1]["level"], "error")

    def test_sse_connection_count_limit_rejects_excess_viewers(self) -> None:
        self.runtime.active_streams = self.settings.max_streams
        response = self.client.get("/api/stream")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.runtime.active_streams, self.settings.max_streams)

    def test_disconnect_before_sse_generator_starts_releases_connection_slot(self) -> None:
        async def exercise():
            scope = {
                "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1", "method": "GET", "scheme": "http",
                "path": "/api/stream", "root_path": "", "query_string": b"",
                "headers": [(b"host", b"testserver")],
                "server": ("testserver", 80), "client": ("127.0.0.1", 1234),
            }

            async def receive():
                return {"type": "http.disconnect"}

            async def send(message):
                if message["type"] == "http.response.start":
                    await asyncio.sleep(5)

            route = next(route for route in self.client.app.routes if route.path == "/api/stream")
            response = await route.endpoint(Request(scope, receive))
            await asyncio.wait_for(response(scope, receive, send), timeout=1)

        self.client.portal.call(exercise)
        self.assertEqual(self.runtime.active_streams, 0)

    def test_failed_serialization_preserves_last_valid_snapshot(self) -> None:
        previous = self.runtime.latest
        previous_serialized = self.runtime.serialized
        previous_revision = self.runtime.revision
        invalid = local_snapshot()
        invalid["hardware"] = {"cpu": {"percent": float("nan")}}
        with self.assertRaises(ValueError):
            self.client.portal.call(self.runtime.publish, invalid)
        self.assertIs(self.runtime.latest, previous)
        self.assertEqual(self.runtime.serialized, previous_serialized)
        self.assertEqual(self.runtime.revision, previous_revision)

    def test_sse_without_auth_streams_new_snapshots_and_ignores_legacy_credentials(self) -> None:
        async def exercise(extra_headers):
            scope = {
                "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1", "method": "GET", "scheme": "http",
                "path": "/api/stream", "root_path": "", "query_string": b"",
                "headers": [(b"host", b"testserver"), *extra_headers],
                "server": ("testserver", 80), "client": ("127.0.0.1", 1234),
            }
            first_frame = asyncio.Event()
            disconnect = asyncio.Event()
            frames = []
            statuses = []
            body_received = False

            async def receive():
                nonlocal body_received
                if not body_received:
                    body_received = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                await disconnect.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                if message["type"] == "http.response.start":
                    statuses.append(message["status"])
                body = message.get("body", b"")
                if b"event: snapshot" in body:
                    frames.append(body)
                    first_frame.set()
                    if len(frames) > 1:
                        disconnect.set()

            task = asyncio.create_task(self.client.app(scope, receive, send))
            try:
                await asyncio.wait_for(first_frame.wait(), timeout=1)
                await asyncio.sleep(0.02)
                updated = local_snapshot()
                updated["hostname"] = "updated-host"
                await self.runtime.publish(updated)
                await asyncio.wait_for(task, timeout=1)
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            return frames, statuses

        for headers in ([], [(b"cookie", b"noc_session=1.expired-signature"),
                             (b"authorization", b"Bearer obsolete-view-secret")]):
            with self.subTest(legacy_credentials=bool(headers)):
                frames, statuses = self.client.portal.call(exercise, headers)
                self.assertEqual(statuses, [200])
                self.assertEqual(len(frames), 2)
                data = next(line[6:] for line in frames[1].splitlines() if line.startswith(b"data: "))
                self.assertEqual(json.loads(data)["hostname"], "updated-host")
                self.assertEqual(self.runtime.active_streams, 0)

    def test_health_rejects_a_stale_snapshot_or_stopped_collection_task(self) -> None:
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.runtime.last_published = time.monotonic() - max(10, 3 * self.settings.interval) - 1
        stale = self.client.get("/api/health")
        self.assertEqual(stale.status_code, 503)
        self.assertFalse(stale.json()["collector_ready"])
        self.runtime.last_published = time.monotonic()

        async def stop_collection():
            self.runtime.collect_task.cancel()
            try:
                await self.runtime.collect_task
            except asyncio.CancelledError:
                pass

        self.client.portal.call(stop_collection)
        self.assertIsNone(self.runtime.latest["collector"]["error"])
        stopped = self.client.get("/api/health")
        self.assertEqual(stopped.status_code, 503)
        self.assertFalse(stopped.json()["collector_ready"])


if __name__ == "__main__":
    unittest.main()
