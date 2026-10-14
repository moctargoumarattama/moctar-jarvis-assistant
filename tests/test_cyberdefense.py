"""Bannissements locaux, simulations sans victime et sauvegarde transactionnelle."""
import asyncio
import ipaddress
import json
import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import cyberdefense
from cyberdefense import CyberDefenseManager, MAX_ATTEMPTS, MAX_BANS, TRAP_PATHS


class CyberDefenseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state" / "blocked_ips.json"
        self.runtime = SimpleNamespace(collector=SimpleNamespace(add_event=Mock()), publish_module_update=AsyncMock())
        self.settings = SimpleNamespace(cyber_state_path=str(self.path), cyber_protected_ips=("9.9.9.9",))
        self.manager = CyberDefenseManager(self.settings, self.runtime)
        await self.manager.start()

    async def reload(self):
        manager = CyberDefenseManager(self.settings, self.runtime)
        await manager.start()
        return manager

    async def test_start_does_not_create_a_state_directory_without_incidents(self):
        self.assertFalse(self.path.exists())
        self.assertFalse(self.path.parent.exists())
        self.assertEqual(self.manager.snapshot()["persistence"]["status"], "missing")

    async def test_real_trap_bans_canonical_ip_and_restores_it_after_restart(self):
        result = await self.manager.trap("8.8.8.8", "/.env")
        self.assertTrue(result["blocked"])
        self.assertTrue(result["persisted"])
        self.assertTrue(self.manager.is_banned("8.8.8.8"))
        restored = await self.reload()
        row = restored.snapshot()["banned_ips"][0]
        self.assertEqual(row["ip"], "8.8.8.8")
        self.assertEqual(row["reason"], "Tentative /.env")
        self.assertEqual(restored.snapshot()["blocked_today"], 1)
        self.assertTrue(restored.is_banned("8.8.8.8"))
        self.assertEqual(self.runtime.publish_module_update.await_count, 1)
        if os.name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    async def test_ipv6_is_canonical_and_ipv4_mapped_addresses_share_one_ban(self):
        await self.manager.trap("2001:4860:4860:0000:0000:0000:0000:8888", "/api/config")
        self.assertTrue(self.manager.is_banned("2001:4860:4860::8888"))
        await self.manager.trap("::ffff:8.8.4.4", "/wp-admin")
        self.assertTrue(self.manager.is_banned("8.8.4.4"))
        self.assertTrue(self.manager.is_banned("::ffff:8.8.4.4"))
        self.assertEqual(self.manager.snapshot()["active_bans"], 2)

    async def test_protected_addresses_are_recorded_without_bans_or_real_scores(self):
        for value in ("127.0.0.1", "::1", "192.168.11.103", "10.0.0.1", "169.254.1.2", "100.68.163.14", "100.127.255.254", "187.77.171.225", "9.9.9.9", "::ffff:192.168.11.103", "224.0.0.1", "fe80::1", "0.0.0.0"):
            with self.subTest(ip=value):
                result = await self.manager.trap(value, "/.env")
                self.assertTrue(result["protected"])
                self.assertFalse(result["blocked"])
                self.assertFalse(self.manager.is_banned(value))
        view = self.manager.snapshot()
        self.assertEqual(view["active_bans"], 0)
        self.assertEqual(view["blocked_today"], 0)
        self.assertEqual(view["audit"]["score"], 0)

    async def test_simulation_allocates_documentation_ip_and_never_filters_clients(self):
        first = await self.manager.simulate("/.env")
        second = await self.manager.simulate("/wp-admin.php")
        self.assertNotEqual(first["ip"], second["ip"])
        self.assertIn(ipaddress.ip_address(first["ip"]), ipaddress.ip_network("198.51.100.0/24"))
        self.assertFalse(self.manager.is_banned(first["ip"]))
        self.assertFalse(self.manager.is_banned("187.77.171.225"))
        view = self.manager.snapshot()
        self.assertEqual(view["simulated_bans"], 2)
        self.assertEqual(view["active_bans"], 0)
        self.assertEqual(view["blocked_today"], 0)
        self.assertEqual(view["audit"]["score"], 0)
        self.assertGreater(view["audit"]["simulation"]["score"], 0)
        self.assertIn("Exercice", view["audit"]["simulation"]["summary"])
        self.assertEqual(view["traffic"][-1]["count"], 2)
        self.assertEqual(view["traffic"][-1]["simulated_count"], 2)
        self.assertEqual((await self.reload()).snapshot()["simulated_bans"], 2)

    async def test_only_known_paths_and_strict_ips_can_be_banned_or_simulated(self):
        for path in ("/", "/.env?download=1", "/evil", "//.env", "/.env\n"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                await self.manager.simulate(path)
        for value in ("8.8.8.8:80", " 8.8.8.8", "8.8.8.8\n", "fe80::1%eth0", "8.8.8.0/24", "008.008.008.008", "not-an-ip"):
            with self.subTest(ip=value):
                invalid = await self.manager.trap(value, "/.env")
                self.assertTrue(invalid["ignored"])
                self.assertTrue(invalid["protected"])
                self.assertFalse(invalid["blocked"])
            self.assertFalse(self.manager.is_banned(value))
        self.assertTrue((await self.manager.trap("testclient", "/.env"))["ignored"])
        self.assertTrue((await self.manager.trap("", "/.env"))["ignored"])
        self.assertFalse(self.path.exists())

    async def test_unban_is_idempotent_and_persisted_and_leaves_history(self):
        await self.manager.trap("8.8.8.8", "/phpmyadmin")
        response = await self.manager.unban("::ffff:8.8.8.8")
        self.assertTrue(response["removed"])
        self.assertFalse(self.manager.is_banned("8.8.8.8"))
        self.assertFalse((await self.manager.unban("8.8.8.8"))["removed"])
        restored = await self.reload()
        self.assertEqual(restored.snapshot()["active_bans"], 0)
        self.assertEqual(len(restored.snapshot()["recent_attempts"]), 1)
        self.assertEqual(restored.snapshot()["blocked_today"], 1)

    async def test_capacity_does_not_evict_an_existing_real_ban(self):
        with patch.object(cyberdefense, "MAX_BANS", 2):
            await self.manager.trap("8.8.8.8", "/.env")
            await self.manager.trap("8.8.4.4", "/wp-admin")
            blocked = await self.manager.trap("1.1.1.1", "/api/config")
            self.assertFalse(blocked["blocked"])
            self.assertTrue(blocked["capacity"])
            self.assertTrue(self.manager.is_banned("8.8.8.8"))
            self.assertTrue(self.manager.is_banned("8.8.4.4"))
            self.assertFalse((await self.manager.simulate("/.env"))["ok"])
            repeated = await self.manager.trap("8.8.8.8", "/.env")
            self.assertTrue(repeated["blocked"])
            self.assertEqual(self.manager.bans["8.8.8.8"]["hits"], 2)
            self.assertEqual(self.manager.snapshot()["blocked_today"], 2)

    async def test_attempts_and_traffic_remain_bounded(self):
        # Répétitions d'une IP : ne créent aucune entrée de ban supplémentaire.
        with patch.object(self.manager, "_persist_locked", new=AsyncMock(return_value=True)):
            for _ in range(MAX_ATTEMPTS + 30):
                await self.manager.trap("8.8.8.8", "/api/config")
        self.assertEqual(len(self.manager.attempts), MAX_ATTEMPTS)
        self.assertEqual(len(self.manager.snapshot()["traffic"]), 60)
        self.assertEqual(self.manager.snapshot()["active_bans"], 1)
        self.assertEqual(self.manager.snapshot()["blocked_today"], 1)

    async def test_corrupted_or_oversized_json_is_reported_without_destroying_it(self):
        self.path.parent.mkdir()
        for raw in (b"{broken", b"[]", b"x" * (cyberdefense.MAX_STATE_BYTES + 1)):
            with self.subTest(size=len(raw)):
                self.path.write_bytes(raw)
                restored = await self.reload()
                self.assertEqual(restored.snapshot()["persistence"]["status"], "error")
                self.assertEqual(restored.snapshot()["active_bans"], 0)
                self.assertEqual(self.path.read_bytes(), raw)

    async def test_non_regular_state_files_are_refused_without_reading_them(self):
        self.path.parent.mkdir()
        self.path.write_text("{}")
        metadata = SimpleNamespace(st_mode=stat.S_IFIFO | 0o600, st_size=0)
        with patch("cyberdefense.os.fstat", return_value=metadata):
            restored = await self.reload()
        self.assertEqual(restored.snapshot()["persistence"]["status"], "error")
        self.assertEqual(self.path.read_text(), "{}")

    async def test_state_parent_symlink_is_refused_when_available(self):
        real = Path(self.directory.name) / "real"
        real.mkdir()
        try:
            self.path.parent.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Création de liens symboliques non autorisée sur ce poste")
        restored = await self.reload()
        self.assertEqual(restored.snapshot()["persistence"]["status"], "error")
        result = await restored.trap("8.8.8.8", "/.env")
        self.assertFalse(result["persisted"])
        self.assertEqual(list(real.iterdir()), [])

    async def test_corrupt_rows_and_protected_bans_are_ignored_on_load(self):
        await self.manager.trap("8.8.8.8", "/.env")
        data = json.loads(self.path.read_text())
        valid = data["banned_ips"][0]
        data["banned_ips"] += [{**valid, "ip": "187.77.171.225"}, {**valid, "ip": "100.68.163.14"}, {**valid, "ip": "not-an-ip"}, {**valid, "path": "/evil"}, {**valid, "ip": "1.1.1.1", "timestamp": "yesterday"}]
        self.path.write_text(json.dumps(data))
        restored = await self.reload()
        self.assertEqual(restored.snapshot()["active_bans"], 1)
        self.assertTrue(restored.is_banned("8.8.8.8"))
        self.assertFalse(restored.is_banned("187.77.171.225"))

    async def test_persistence_error_does_not_claim_durability_and_keeps_old_file(self):
        await self.manager.trap("8.8.8.8", "/.env")
        old = self.path.read_bytes()
        with patch("cyberdefense.os.replace", side_effect=PermissionError("refused")):
            result = await self.manager.trap("1.1.1.1", "/wp-admin")
        self.assertTrue(result["blocked"])
        self.assertFalse(result["persisted"])
        self.assertEqual(result["cyber"]["persistence"]["status"], "error")
        self.assertIn("redémarrage", result["cyber"]["persistence"]["error"])
        self.assertEqual(self.path.read_bytes(), old)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        self.assertFalse((await self.reload()).is_banned("1.1.1.1"))

    async def test_concurrent_mutations_save_one_complete_consistent_state(self):
        ips = [f"8.8.8.{index}" for index in range(1, 21)]
        await asyncio.gather(*(self.manager.trap(ip, "/.env") for ip in ips))
        await asyncio.gather(*(self.manager.unban(ip) for ip in ips[::2]))
        restored = await self.reload()
        self.assertEqual(restored.snapshot()["active_bans"], 10)
        self.assertEqual(restored.snapshot()["blocked_today"], 20)
        for ip in ips:
            self.assertEqual(restored.is_banned(ip), ip not in ips[::2])

    async def test_cancelled_request_finishes_disk_write_before_next_mutation(self):
        writing = threading.Event()
        release = threading.Event()
        original = self.manager._write_state
        calls = 0

        def delayed(payload):
            nonlocal calls
            calls += 1
            if calls == 1:
                writing.set()
                if not release.wait(3):
                    raise RuntimeError("test thread timeout")
            original(payload)

        with patch.object(self.manager, "_write_state", side_effect=delayed):
            first = asyncio.create_task(self.manager.trap("8.8.8.8", "/.env"))
            while not writing.is_set():
                await asyncio.sleep(0)
            self.assertEqual(self.manager.snapshot()["persistence"]["status"], "saving")
            first.cancel()
            await asyncio.sleep(0)
            first.cancel()
            second = asyncio.create_task(self.manager.trap("1.1.1.1", "/wp-admin"))
            await asyncio.sleep(0.01)
            self.assertFalse(second.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await first
            await second
        restored = await self.reload()
        self.assertTrue(restored.is_banned("8.8.8.8"))
        self.assertTrue(restored.is_banned("1.1.1.1"))
        self.assertEqual(restored.snapshot()["blocked_today"], 2)

    async def test_audit_varies_by_real_patterns_and_is_not_an_accuracy_claim(self):
        await self.manager.simulate("/.env")
        self.assertEqual(self.manager.snapshot()["audit"]["rules"], [])
        await self.manager.trap("8.8.8.8", "/.env")
        secret = self.manager.snapshot()["audit"]
        self.assertEqual([row["id"] for row in secret["rules"]], ["secret-scan"])
        await self.manager.trap("1.1.1.1", "/wp-admin.php")
        both = (await self.manager.audit_refresh())["cyber"]["audit"]
        self.assertGreater(both["score"], secret["score"])
        self.assertIn("wordpress-scan", [row["id"] for row in both["rules"]])
        self.assertIn("n'est pas un taux de détection", both["summary"])
        self.assertTrue(any("SSH" in advice for advice in both["recommendations"]))

    async def test_old_attempts_do_not_affect_current_hour_score_and_day_rolls(self):
        await self.manager.trap("8.8.8.8", "/.env")
        self.manager.attempts[0]["timestamp"] = cyberdefense._timestamp(time.time() - 7200)
        self.manager._counter_day = "2000-01-01"
        view = self.manager.snapshot()
        self.assertEqual(view["audit"]["score"], 0)
        self.assertEqual(view["blocked_today"], 0)
        self.assertTrue(self.manager.is_banned("8.8.8.8"))

    async def test_snapshot_is_detached_from_mutable_manager_state(self):
        await self.manager.trap("8.8.8.8", "/.env")
        view = self.manager.snapshot()
        view["banned_ips"][0]["hits"] = 123
        view["recent_attempts"][0]["path"] = "/evil"
        view["persistence"]["status"] = "fake"
        self.assertEqual(self.manager.bans["8.8.8.8"]["hits"], 1)
        self.assertEqual(self.manager.attempts[0]["path"], "/.env")
        self.assertEqual(self.manager.snapshot()["persistence"]["status"], "ready")


if __name__ == "__main__":
    unittest.main()
