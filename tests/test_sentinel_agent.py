"""Sentinelle : simulation complète, aucune modification du pare-feu de la machine."""

import asyncio
import base64
import ipaddress
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sentinel_crypto
import vps_sentinel as agent


class CryptoTests(unittest.TestCase):
    def test_authenticated_round_trip_and_unique_nonce(self):
        payload = {"kind": "incident", "username": "administrateur", "password": "mot-de-passe-essai"}
        first = sentinel_crypto.encrypt(payload, "secret-essai")
        second = sentinel_crypto.encrypt(payload, "secret-essai")
        self.assertNotEqual(first["nonce"], second["nonce"])
        self.assertEqual(sentinel_crypto.decrypt(first, "secret-essai"), payload)
        self.assertNotIn("mot-de-passe-essai", json.dumps(first))

    def test_wrong_key_and_tampering_are_rejected(self):
        envelope = sentinel_crypto.encrypt({"kind": "heartbeat"}, "secret-essai")
        with self.assertRaisesRegex(ValueError, "^Enveloppe sentinelle invalide$"):
            sentinel_crypto.decrypt(envelope, "mauvaise-cle")
        raw = bytearray(base64.b64decode(envelope["ciphertext"]))
        raw[-1] ^= 1
        envelope["ciphertext"] = base64.b64encode(raw).decode("ascii")
        with self.assertRaises(ValueError):
            sentinel_crypto.decrypt(envelope, "secret-essai")

    def test_envelope_limits_types_and_strict_base64(self):
        with self.assertRaises(ValueError):
            sentinel_crypto.encrypt({"value": "x" * 8192}, "secret-essai")
        envelope = sentinel_crypto.encrypt({}, "secret-essai")
        for changes in ({"version": True}, {"nonce": "!" * 16}, {"ciphertext": "a" * 12000}, {"extra": "forbidden"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                sentinel_crypto.decrypt({**envelope, **changes}, "secret-essai")


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = {"NOC_VPS_TOKEN": "secret-essai", "SENTINEL_STATE_FILE": str(Path(self.directory.name) / "counters.json")}

    def test_http_restricted_to_tailscale_or_loopback(self):
        for url in ("http://100.68.163.14:5050/api/sentinel/incident", "http://127.0.0.1:5050/api/sentinel/incident", "https://example.com/api/sentinel/incident"):
            with self.subTest(url=url), patch.dict(os.environ, {**self.environment, "SENTINEL_REPORT_URL": url}, clear=True):
                self.assertEqual(agent.Settings.from_env().url, url)
        for url in ("http://8.8.8.8/api/sentinel/incident", "https://user:password@example.com/api/sentinel/incident", "https://example.com/api/sentinel/incident?token=secret"):
            with self.subTest(url=url), patch.dict(os.environ, {**self.environment, "SENTINEL_REPORT_URL": url}, clear=True), self.assertRaises(ValueError):
                agent.Settings.from_env()

    def test_management_protection_is_additive(self):
        with patch.dict(os.environ, {**self.environment, "SENTINEL_PROTECTED_CIDRS": "8.8.8.8/32"}, clear=True):
            settings = agent.Settings.from_env()
        for source in ("127.0.0.1", "::1", "100.68.163.14", "192.168.11.8", "187.77.171.225", "8.8.8.8", "::ffff:100.68.163.14"):
            self.assertEqual(agent.eligible_ip(source, settings.protected)[1], "protected")
        for source in ("10.0.0.1", "169.254.1.1", "224.0.0.1", "0.0.0.0", "8.8.8.8;touch /tmp/test"):
            self.assertIsNotNone(agent.eligible_ip(source, settings.protected)[1])

    def test_invalid_token_cidr_and_client_limit_fail(self):
        for changes in ({"NOC_VPS_TOKEN": ""}, {"SENTINEL_PROTECTED_CIDRS": "invalid"}, {"SENTINEL_MAX_CLIENTS": "17"}, {"SENTINEL_PORT": "65536"}):
            with self.subTest(changes=changes), patch.dict(os.environ, {**self.environment, **changes}, clear=True), self.assertRaises(ValueError):
                agent.Settings.from_env()


class CounterTests(unittest.TestCase):
    def test_persistence_daily_reset_and_cache_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "counters.json"
            day = ["2026-10-13"]
            counters = agent.Counters(path, day_provider=lambda: day[0])
            counters.increment()
            for index in range(300):
                counters.remember(str(ipaddress.ip_address(0x08080000 + index)), True)
            counters.save()
            self.assertEqual(len(counters.cache), 256)
            restored = agent.Counters(path, day_provider=lambda: day[0])
            self.assertEqual(restored.blocked_today, 1)
            self.assertLessEqual(path.stat().st_size, agent.MAX_STATE_BYTES)
            day[0] = "2026-10-14"
            restored.refresh_day()
            self.assertEqual(restored.blocked_today, 0)
            self.assertEqual(json.loads(path.read_text())["day"], "2026-10-14")


class FakeWriter:
    def __init__(self, source="8.8.8.8"):
        self.source, self.output, self.closed = source, bytearray(), False

    def write(self, data):
        self.output.extend(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass

    def get_extra_info(self, key):
        return (self.source, 43210) if key == "peername" else None


class ProtocolAndFirewallTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = agent.Settings(token="secret-essai", state_file=Path(self.directory.name) / "counters.json", firewall="iptables")
        self.counters = agent.Counters(self.settings.state_file, day_provider=lambda: "2026-10-13")

    async def asyncTearDown(self):
        self.directory.cleanup()

    def reader(self, data):
        reader = asyncio.StreamReader(limit=agent.MAX_LINE_BYTES)
        reader.feed_data(data)
        reader.feed_eof()
        return reader

    async def test_telnet_credentials_are_printable_and_bounded(self):
        writer = FakeWriter()
        result = await agent.credentials(self.reader(b"\xff\xfb\x01root\r\n" + b"p" * 100 + b"\x00\r\n"), writer)
        self.assertEqual(result, ("root", "p" * 64, "credentials"))
        self.assertEqual(writer.output, b"login: Password: ")

    async def test_ssh_banner_does_not_claim_ssh_password_capture(self):
        result = await agent.credentials(self.reader(b"SSH-2.0-OpenSSH_9.0\r\n"), FakeWriter())
        self.assertEqual(result, ("", "", "ssh_negotiation"))

    async def test_oversized_and_missing_password_are_incomplete_attempts(self):
        self.assertEqual((await agent.credentials(self.reader(b"x" * 300 + b"\n"), FakeWriter()))[2], "negotiation_incomplete")
        self.assertEqual(await agent.credentials(self.reader(b"root\n"), FakeWriter()), ("root", "", "negotiation_incomplete"))

    async def test_protected_peer_never_executes_firewall(self):
        firewall = agent.Firewall(self.settings, self.counters)
        with patch.object(agent, "run_firewall", new_callable=AsyncMock) as execute:
            self.assertEqual(await firewall.block("100.68.163.14"), (False, False, "protected"))
            execute.assert_not_awaited()

    async def test_ban_exact_command_count_and_deduplication(self):
        firewall = agent.Firewall(self.settings, self.counters)
        with patch.object(agent.shutil, "which", return_value="/usr/sbin/iptables"), patch.object(agent, "run_firewall", new_callable=AsyncMock, return_value=(0, b"")) as execute:
            self.assertEqual(await firewall.block("8.8.8.8"), (True, True, None))
            self.assertEqual(await firewall.block("8.8.8.8"), (True, False, None))
            execute.assert_awaited_once_with(["/usr/sbin/iptables", "-w", "2", "-I", "INPUT", "1", "-s", "8.8.8.8", "-j", "DROP"])
            self.assertEqual(self.counters.blocked_today, 1)

    async def test_failed_firewall_and_inactive_ufw_do_not_increment(self):
        firewall = agent.Firewall(self.settings, self.counters)
        with patch.object(firewall, "command", return_value=["/test/firewall"]), patch.object(agent, "run_firewall", new_callable=AsyncMock, return_value=(1, b"")):
            self.assertEqual(await firewall.block("8.8.8.8"), (False, False, "firewall_refused"))
        settings = agent.Settings(token="secret-essai", firewall="ufw")
        firewall = agent.Firewall(settings, self.counters)
        with patch.object(agent.Path, "read_text", return_value="ENABLED=no\n"), patch.object(agent.shutil, "which", return_value="/usr/sbin/ufw"), patch.object(agent, "run_firewall", new_callable=AsyncMock) as execute:
            self.assertEqual(await firewall.block("8.8.4.4"), (False, False, "firewall_unavailable"))
            execute.assert_not_awaited()
        self.assertEqual(self.counters.blocked_today, 0)

    async def test_incident_contract_and_queue_are_bounded(self):
        sentinel = agent.Sentinel(self.settings, self.counters)
        self.addCleanup(sentinel.reporter_pool.shutdown, False)
        writer = FakeWriter()
        with patch.object(sentinel.firewall, "block", new_callable=AsyncMock, return_value=(True, True, None)) as block:
            await sentinel.handle(self.reader(b"root\npassword-essai\n"), writer, "8.8.8.8")
            block.assert_awaited_once_with("8.8.8.8")
        report = sentinel.queue.get_nowait()
        self.assertEqual((report["kind"], report["username"], report["password"], report["blocked"]), ("incident", "root", "password-essai", True))
        self.assertTrue(writer.closed)
        for index in range(100):
            sentinel.enqueue({"index": index})
        self.assertEqual(sentinel.queue.qsize(), 64)
        self.assertEqual(sentinel.queue.get_nowait()["index"], 36)

    async def test_capacity_rejects_connection_without_spawning_task(self):
        sentinel = agent.Sentinel(self.settings, self.counters)
        self.addCleanup(sentinel.reporter_pool.shutdown, False)
        sentinel.clients = set(range(16))
        writer = FakeWriter()
        sentinel.connected(self.reader(b"root\n"), writer)
        self.assertTrue(writer.closed)
        self.assertEqual(len(sentinel.clients), 16)
        self.assertEqual(sentinel.queue.get_nowait()["ban_error"], "capacity")


if __name__ == "__main__":
    unittest.main()
