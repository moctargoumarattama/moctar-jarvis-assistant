"""Persistance bornée et reprise prudente des derniers rapports VPS."""
from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from config import Settings
from main import VPSReport
from vps_registry import VPSRegistry


def report(hostname="vps-one"):
    return {"hostname": hostname, "cpu_percent": 23.5,
            "memory": {"used": 30, "total": 100, "percent": 30.0},
            "disk": {"used": 40, "total": 100, "percent": 40.0}, "services": []}


class VPSRegistryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state" / "vps.json"
        self.settings = Settings(vps_token="private-test-token", vps_state_path=str(self.path))
        self.registry = VPSRegistry(self.settings, VPSReport)

    def entry(self, hostname="vps-one", age=0):
        return {"report": report(hostname), "last_seen": (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(),
                "seen": time.monotonic() - age, "stale": False, "restored": False}

    async def test_saved_report_survives_restart_with_real_age_and_unconfirmed_presence(self):
        entry = self.entry(age=120)
        await self.registry.persist({"vps-one": entry})
        self.assertNotIn(self.settings.vps_token, self.path.read_text())
        self.assertFalse(list(self.path.parent.glob("*.tmp")))
        restored = await VPSRegistry(self.settings, VPSReport).load()
        self.assertEqual(restored["vps-one"]["report"], entry["report"])
        self.assertTrue(restored["vps-one"]["restored"])
        self.assertTrue(restored["vps-one"]["stale"])
        self.assertAlmostEqual(time.monotonic() - restored["vps-one"]["seen"], 120, delta=3)

    async def test_invalid_expired_and_future_reports_are_not_restored(self):
        valid = self.entry("valid")
        invalid = self.entry("invalid")
        invalid["report"]["cpu_percent"] = 200
        rows = [valid, invalid, self.entry("expired", self.settings.vps_retention_seconds + 10),
                self.entry("future", -120), {"report": report("bad-time"), "last_seen": "not-a-date"},
                {"report": report("naive-time"), "last_seen": "2026-10-14T10:00:00"}]
        self.path.parent.mkdir()
        self.path.write_text(json.dumps({"version": 1, "servers": rows}))
        self.assertEqual(set(await self.registry.load()), {"valid"})

    async def test_corrupt_oversized_or_nonregular_state_does_not_prevent_startup(self):
        self.path.parent.mkdir()
        for content in ("{broken", "[]", '{"version":2,"servers":[]}', " " * (self.registry.max_bytes + 1)):
            with self.subTest(size=len(content)):
                self.path.write_text(content)
                self.assertEqual(await self.registry.load(), {})
        with patch.object(Path, "is_symlink", return_value=True):
            self.assertEqual(await self.registry.load(), {})

    async def test_capacity_is_enforced_when_loading_and_saving(self):
        registry = VPSRegistry(replace(self.settings, max_vps=1), VPSReport)
        await registry.persist({"first": self.entry("first"), "second": self.entry("second")})
        self.assertEqual(set(await registry.load()), {"first"})
        self.assertEqual(len(json.loads(self.path.read_text())["servers"]), 1)

    async def test_failed_atomic_replace_preserves_previous_report_and_removes_temporary_file(self):
        await self.registry.persist({"first": self.entry("first")})
        with patch("vps_registry.os.replace", side_effect=OSError("disk unavailable")):
            await self.registry.persist({"second": self.entry("second")})
        self.assertEqual(set(await self.registry.load()), {"first"})
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    async def test_cancelled_request_keeps_write_lock_until_its_thread_finishes(self):
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()
        original_write = self.registry._write

        def slow_write(payload):
            if payload["servers"][0]["report"]["hostname"] == "first":
                loop.call_soon_threadsafe(started.set)
                release.wait(3)
            original_write(payload)

        with patch.object(self.registry, "_write", side_effect=slow_write):
            first = asyncio.create_task(self.registry.persist({"first": self.entry("first")}))
            await asyncio.wait_for(started.wait(), 3)
            first.cancel()
            await asyncio.sleep(0)
            first.cancel()
            second = asyncio.create_task(self.registry.persist({"second": self.entry("second")}))
            await asyncio.sleep(0)
            self.assertFalse(second.done())
            release.set()
            results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertEqual(set(await self.registry.load()), {"second"})

    async def test_persistence_can_be_disabled_and_disabled_reporting_does_not_load_old_hosts(self):
        await self.registry.persist({"first": self.entry("first")})
        disabled = VPSRegistry(replace(self.settings, vps_token=""), VPSReport)
        self.assertEqual(await disabled.load(), {})
        disabled = VPSRegistry(replace(self.settings, vps_state_path=""), VPSReport)
        await disabled.persist({"second": self.entry("second")})
        self.assertEqual(await disabled.load(), {})
        self.assertEqual(set(await self.registry.load()), {"first"})

    async def test_delete_failure_preserves_memory_and_success_survives_reload(self):
        servers = {"first": self.entry("first"), "second": self.entry("second")}
        await self.registry.persist(servers)
        with patch("vps_registry.os.replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                await self.registry.remove(servers, "first")
        self.assertEqual(set(servers), {"first", "second"})
        self.assertEqual(set(await self.registry.load()), {"first", "second"})
        self.assertTrue(await self.registry.remove(servers, "first"))
        self.assertEqual(set(servers), {"second"})
        self.assertEqual(set(await self.registry.load()), {"second"})
        self.assertFalse(await self.registry.remove(servers, "missing"))

    async def test_delete_and_concurrent_reports_are_serialized_without_losing_updates(self):
        servers = {"first": self.entry("first"), "second": self.entry("second")}
        await self.registry.persist(servers)
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()
        original_write = self.registry._write

        def slow_write(payload):
            if len(payload["servers"]) == 1:
                loop.call_soon_threadsafe(started.set)
                release.wait(3)
            return original_write(payload)

        with patch.object(self.registry, "_write", side_effect=slow_write):
            removal = asyncio.create_task(self.registry.remove(servers, "first"))
            await asyncio.wait_for(started.wait(), 3)
            # A fresh report for the removed node must be applied after deletion,
            # along with updates from unrelated hosts, rather than silently lost.
            refresh = asyncio.create_task(self.registry.upsert(servers, "first", self.entry("first")))
            other = asyncio.create_task(self.registry.upsert(servers, "third", self.entry("third")))
            await asyncio.sleep(0)
            self.assertFalse(refresh.done())
            self.assertFalse(other.done())
            release.set()
            self.assertEqual([True, True, True], await asyncio.gather(removal, refresh, other))
        self.assertEqual(set(servers), {"first", "second", "third"})
        self.assertEqual(set(await self.registry.load()), set(servers))

    async def test_cancelled_delete_keeps_memory_in_sync_with_completed_disk_write(self):
        servers = {"first": self.entry("first"), "second": self.entry("second")}
        await self.registry.persist(servers)
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()
        original_write = self.registry._write

        def slow_write(payload):
            loop.call_soon_threadsafe(started.set)
            release.wait(3)
            return original_write(payload)

        with patch.object(self.registry, "_write", side_effect=slow_write):
            removal = asyncio.create_task(self.registry.remove(servers, "first"))
            await asyncio.wait_for(started.wait(), 3)
            removal.cancel()
            await asyncio.sleep(0)
            self.assertIn("first", servers)
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await removal
        self.assertEqual(set(servers), {"second"})
        self.assertEqual(set(await self.registry.load()), {"second"})
