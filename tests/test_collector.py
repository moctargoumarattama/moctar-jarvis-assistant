"""Checks for failed probes, bounded data and subprocess cleanup."""
from __future__ import annotations

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

from collector import Collector, run_command
from config import SERVICES, Settings


def hardware_snapshot(cpu: float = 10.0) -> dict:
    disk = {"path": "/", "used": 10, "total": 100, "percent": 10.0,
            "available": True, "mounted": True, "error": None}
    return {
        "cpu": {"percent": cpu, "cores": [cpu, cpu]},
        "memory": {"used": 20, "total": 100, "percent": 20.0},
        "swap": {"used": 0, "total": 100, "percent": 0.0},
        "temperature_c": 40.0,
        "disks": {"root": disk, "backup": dict(disk, path="/mnt/backups")},
        "process_rss_bytes": 1024,
    }


class CollectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.collector = Collector(Settings(event_limit=20))

    async def asyncTearDown(self) -> None:
        await self.collector.close()

    def transport(self, handler) -> None:
        self.collector.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def test_failed_systemd_and_http_probes_do_not_abort_collection(self) -> None:
        self.collector.systemctl = "/usr/bin/systemctl"

        def offline(request):
            raise httpx.ConnectError("Connection refused", request=request)

        self.transport(offline)
        with patch("collector.run_command", AsyncMock(side_effect=OSError("systemctl absent"))), \
                patch.object(self.collector, "hardware_metrics", return_value=hardware_snapshot()):
            snapshot = await self.collector.collect()

        self.assertEqual(snapshot["global_status"], "incident")
        self.assertEqual(len(snapshot["services"]), 4)
        for service in snapshot["services"]:
            self.assertFalse(service["healthy"])
            self.assertEqual(service["systemd"], "unknown")
            self.assertIsNone(service["latency_ms"])
        self.assertFalse(snapshot["infrastructure"]["whatsapp"]["connected"])
        self.assertIsNone(snapshot["collector"]["error"])

    async def test_http_and_systemd_are_both_required_for_service_health(self) -> None:
        self.transport(lambda request: httpx.Response(200))
        with patch.object(self.collector, "systemd_status", AsyncMock(return_value=("inactive", "stopped"))):
            service = await self.collector.service_probe(SERVICES[0])
        self.assertEqual(service["http_status"], 200)
        self.assertFalse(service["healthy"])
        self.assertGreaterEqual(service["latency_ms"], 0)

    async def test_whatsapp_secret_and_boolean_connected_contract(self) -> None:
        requests = []

        def gateway(request):
            requests.append(request)
            return httpx.Response(200, json={"connected": "false", "phone": "+212600000000"})

        self.transport(gateway)
        with patch.object(self.collector, "systemd_status", AsyncMock(return_value=("active", None))):
            gateway_snapshot = await self.collector.whatsapp_probe()
        self.assertEqual(requests[0].headers["x-gateway-secret"], self.collector.settings.gateway_secret)
        self.assertFalse(gateway_snapshot["connected"])
        self.assertFalse(gateway_snapshot["healthy"])
        self.assertEqual(gateway_snapshot["status"], "DECONNECTE")

    async def test_gateway_rejects_oversized_or_invalid_json_without_crashing(self) -> None:
        for content in (b"{" + b"x" * 17000, b"not json", b"[]"):
            with self.subTest(size=len(content)):
                async with httpx.AsyncClient(transport=httpx.MockTransport(
                        lambda request: httpx.Response(200, content=content))) as client:
                    self.collector.client = client
                    result = await self.collector.http_probe("http://127.0.0.1:3001/", read_json=True)
                self.assertFalse(result["ok"])
                self.assertIsNone(result["data"])
                self.assertIsNotNone(result["error"])
        self.collector.client = None

    async def test_total_http_deadline_stops_a_slow_trickling_response(self) -> None:
        class SlowStream(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                yield b'{"connected":'
                while True:
                    await asyncio.sleep(0.005)
                    yield b" "

            async def aclose(self):
                self.closed = True

        stream = SlowStream()
        self.collector.settings = Settings(http_timeout=0.04)
        self.transport(lambda request: httpx.Response(200, stream=stream))
        result = await asyncio.wait_for(
            self.collector.http_probe("http://127.0.0.1:3001/", read_json=True), timeout=0.5)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["data"])
        self.assertTrue(stream.closed)

    async def test_cpu_priming_and_sampling_share_a_dedicated_thread(self) -> None:
        threads = []

        def prime():
            threads.append(threading.get_ident())

        def sample():
            threads.append(threading.get_ident())
            return hardware_snapshot()

        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"connected": True})))
        with patch.object(self.collector, "_prime_cpu", side_effect=prime), \
                patch.object(self.collector, "hardware_metrics", side_effect=sample), \
                patch.object(self.collector, "systemd_status", AsyncMock(return_value=("active", None))), \
                patch("collector.httpx.AsyncClient", return_value=client):
            await self.collector.start()
            await self.collector.collect()
            await self.collector.collect()
        self.assertEqual(len(threads), 3)
        self.assertEqual(len(set(threads)), 1)
        self.assertNotEqual(threads[0], threading.get_ident())

    async def test_hardware_failure_can_recover_next_cycle(self) -> None:
        self.transport(lambda request: httpx.Response(200, json={"connected": True}))
        with patch.object(self.collector, "systemd_status", AsyncMock(return_value=("active", None))), \
                patch.object(self.collector, "hardware_metrics", side_effect=[OSError("hardware unavailable"), hardware_snapshot()]), \
                patch.object(self.collector, "disk_metrics", return_value=hardware_snapshot()["disks"]["root"]):
            failed = await self.collector.collect()
            recovered = await self.collector.collect()
        self.assertEqual(failed["global_status"], "incident")
        self.assertIsNone(failed["hardware"]["cpu"]["percent"])
        self.assertIsNotNone(failed["collector"]["error"])
        self.assertEqual(recovered["global_status"], "operational")
        self.assertIsNone(recovered["collector"]["error"])

    async def test_unreadable_root_disk_is_an_incident_and_can_recover(self) -> None:
        failed_hardware = hardware_snapshot()
        failed_hardware["disks"]["root"] = {
            "path": "/", "used": None, "total": None, "percent": None,
            "available": False, "mounted": True, "error": "Disk unavailable",
        }
        self.transport(lambda request: httpx.Response(200, json={"connected": True}))
        with patch.object(self.collector, "systemd_status", AsyncMock(return_value=("active", None))), \
                patch.object(self.collector, "hardware_metrics", side_effect=[failed_hardware, hardware_snapshot()]):
            failed = await self.collector.collect()
            recovered = await self.collector.collect()
        self.assertEqual(failed["global_status"], "incident")
        self.assertEqual(recovered["global_status"], "operational")

    async def test_unchanged_states_do_not_flood_events_and_critical_cpu_recovers(self) -> None:
        self.transport(lambda request: httpx.Response(200, json={"connected": True}))
        with patch.object(self.collector, "systemd_status", AsyncMock(return_value=("active", None))), \
                patch.object(self.collector, "hardware_metrics", side_effect=[
                    hardware_snapshot(), hardware_snapshot(), hardware_snapshot(98), hardware_snapshot()]):
            first = await self.collector.collect()
            unchanged = await self.collector.collect()
            overloaded = await self.collector.collect()
            recovered = await self.collector.collect()
        self.assertEqual(len(first["events"]), len(unchanged["events"]))
        self.assertEqual(overloaded["global_status"], "incident")
        self.assertEqual(recovered["global_status"], "operational")
        self.assertEqual(len(recovered["events"]), len(first["events"]) + 2)
        self.assertEqual(recovered["events"][-1]["level"], "info")

    def test_unmounted_backup_does_not_report_root_filesystem_as_backup(self) -> None:
        with patch("collector.os.path.ismount", return_value=False), \
                patch("collector.psutil.disk_usage") as disk_usage:
            backup = self.collector.disk_metrics("/mnt/babamarket-backups", require_mount=True)
        disk_usage.assert_not_called()
        self.assertFalse(backup["available"])
        self.assertFalse(backup["mounted"])
        self.assertIsNone(backup["total"])
        self.assertEqual(backup["path"], "/mnt/babamarket-backups")

    def test_missing_temperature_sensor_is_supported(self) -> None:
        with patch("collector.psutil.sensors_temperatures", side_effect=AttributeError, create=True), \
                patch("collector.psutil.virtual_memory", return_value=SimpleNamespace(total=100, available=80, percent=20.0)), \
                patch("collector.psutil.swap_memory", return_value=SimpleNamespace(total=0, used=0, percent=0.0)), \
                patch("collector.psutil.cpu_percent", side_effect=[10.0, [10.0]]), \
                patch.object(self.collector, "disk_metrics", return_value=hardware_snapshot()["disks"]["root"]):
            hardware = self.collector.hardware_metrics()
        self.assertIsNone(hardware["temperature_c"])
        self.assertEqual(hardware["memory"]["used"], 20)

    def test_event_memory_is_bounded(self) -> None:
        collector = Collector(Settings(event_limit=3))
        for _ in range(7):
            collector.add_event("error", "x" * 1000)
        self.assertEqual(len(collector.events), 3)
        self.assertEqual([event["id"] for event in collector.events], [5, 6, 7])
        self.assertTrue(all(len(event["message"]) <= 500 for event in collector.events))


class CommandCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_kills_and_reaps_child_process(self) -> None:
        calls = 0

        async def communicate():
            nonlocal calls
            calls += 1
            if calls == 1:
                await asyncio.sleep(3600)
            return b"", b""

        process = Mock(returncode=None)
        process.communicate = AsyncMock(side_effect=communicate)
        with patch("collector.asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as launch:
            with self.assertRaises(asyncio.TimeoutError):
                await run_command(["systemctl", "is-active", "klasora.service"], timeout=0.01)
        process.kill.assert_called_once()
        self.assertEqual(process.communicate.await_count, 2)
        self.assertEqual(launch.call_args.args, ("systemctl", "is-active", "klasora.service"))
        self.assertNotIn("shell", launch.call_args.kwargs)

    async def test_task_cancellation_also_reaps_child_process(self) -> None:
        entered = asyncio.Event()
        calls = 0

        async def communicate():
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await asyncio.sleep(3600)
            return b"", b""

        process = Mock(returncode=None)
        process.communicate = AsyncMock(side_effect=communicate)
        with patch("collector.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            task = asyncio.create_task(run_command(["systemctl", "is-active", "klasora.service"], timeout=5))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        process.kill.assert_called_once()
        self.assertEqual(process.communicate.await_count, 2)


if __name__ == "__main__":
    unittest.main()
