"""Commandes opérateur : autorisations et sous-processus toujours simulés."""
from __future__ import annotations

import subprocess
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from config import SERVICES, Settings, parse_whatsapp_admin_numbers
import system_manager
from whatsapp import admin_numbers, parse_message


def completed(code: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, stdout, stderr)


class SystemCommandAuthorizationTests(unittest.TestCase):
    def test_admin_list_normalizes_multiple_numbers_and_deduplicates(self):
        numbers = parse_whatsapp_admin_numbers("+212 770-010-264, +33 (6) 12.34.56.78,212770010264")
        self.assertEqual(numbers, ("212770010264", "33612345678"))
        self.assertEqual(admin_numbers(Settings(whatsapp_admin_numbers=numbers)), numbers)
        for number in numbers:
            parsed = parse_message({"id": "test", "from": number + "@s.whatsapp.net", "text": "restart jarvis"}, numbers)
            self.assertEqual(parsed["sender"], number)

    def test_empty_list_denies_every_sender_instead_of_falling_back_to_legacy_admin(self):
        numbers = parse_whatsapp_admin_numbers("  ")
        self.assertEqual(admin_numbers(Settings(whatsapp_admin_numbers=numbers)), ())
        with self.assertRaises(PermissionError):
            parse_message({"id": "test", "from": "212770010264", "text": "reboot"}, numbers)

    def test_invalid_or_oversized_admin_lists_are_rejected(self):
        for raw in ("123", "212770010264,", "*,212770010264", "212770010264@evil.test", "1" * 16,
                    ",".join(str(212770010000 + index) for index in range(33)), None):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_whatsapp_admin_numbers(raw)

    def test_unknown_sender_groups_and_conflicting_sender_aliases_are_denied(self):
        numbers = ("212770010264", "33612345678")
        for extra in ({"from": "212770010265"}, {"from": "212770010264@g.us"},
                      {"from": "212770010264", "sender": "33612345678"}):
            with self.subTest(extra=extra), self.assertRaises(PermissionError):
                parse_message({"id": "test", "text": "stop jarvis", **extra}, numbers)


class SystemServiceCommandTests(unittest.TestCase):
    @patch("system_manager.subprocess.run")
    def test_only_exact_allowlisted_units_can_reach_subprocess(self, run):
        invalid = ["nginx", "ollama", "JARVIS", "jarvis.service", " jarvis", "--all", "jarvis\n", "jarvis;reboot", "$(reboot)", None, 7]
        for name in invalid:
            for operation in (system_manager.restart_system_service, system_manager.stop_system_service):
                with self.subTest(name=name, operation=operation.__name__):
                    result = operation(name)
                    self.assertFalse(result["ok"])
                    self.assertIn("non autorisé", result["message"])
        run.assert_not_called()

    @patch("system_manager.subprocess.run")
    def test_each_authorized_restart_is_checked_for_active_state(self, run):
        self.assertEqual(set(system_manager.SERVICES_ALLOWLIST), {"merca-market", "klasora", "babamarket", "moc-mal", "jarvis"})
        for name in system_manager.SERVICES_ALLOWLIST:
            run.reset_mock()
            run.side_effect = [completed(), completed(0, "active\n")]
            result = system_manager.restart_system_service(name)
            self.assertTrue(result["ok"])
            self.assertTrue(result["success"])
            self.assertEqual(result["state"], "active")
            self.assertEqual(result["service"], name)
            self.assertEqual(run.call_args_list[0].args[0], ["sudo", "-n", "/usr/bin/systemctl", "restart", name + ".service"])
            self.assertEqual(run.call_args_list[1].args[0], ["/usr/bin/systemctl", "is-active", name + ".service"])
            for call in run.call_args_list:
                self.assertIs(call.kwargs["shell"], False)
                self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)
                self.assertLessEqual(call.kwargs["timeout"], 15)

    @patch("system_manager.subprocess.run")
    def test_restart_does_not_claim_success_for_inactive_or_inconsistent_results(self, run):
        for code, state in ((3, "inactive"), (3, "failed"), (0, "activating"), (1, "active"), (0, "garbage")):
            with self.subTest(code=code, state=state):
                run.side_effect = [completed(), completed(code, state)]
                result = system_manager.restart_system_service("klasora")
                self.assertFalse(result["success"])

    @patch("system_manager.subprocess.run")
    def test_stop_requires_inactive_state_not_a_running_or_failed_service(self, run):
        run.side_effect = [completed(), completed(3, "inactive\n")]
        result = system_manager.stop_system_service("babamarket")
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "inactive")
        self.assertEqual(run.call_args_list[0].args[0], ["sudo", "-n", "/usr/bin/systemctl", "stop", "babamarket.service"])
        for state, code in (("active", 0), ("failed", 3), ("deactivating", 0), ("inactive", 4)):
            with self.subTest(state=state, code=code):
                run.side_effect = [completed(), completed(code, state)]
                self.assertFalse(system_manager.stop_system_service("jarvis")["ok"])

    @patch("system_manager.subprocess.run")
    def test_failure_timeout_and_missing_systemctl_are_controlled(self, run):
        run.return_value = completed(1, stderr="secret sudo error")
        result = system_manager.restart_system_service("moc-mal")
        self.assertFalse(result["ok"])
        self.assertNotIn("secret", result["message"])
        self.assertEqual(run.call_count, 1)
        for error in (subprocess.TimeoutExpired("systemctl", 15), FileNotFoundError("sudo")):
            for operation in (system_manager.restart_system_service, system_manager.stop_system_service):
                with self.subTest(error=type(error), operation=operation.__name__):
                    run.side_effect = error
                    self.assertFalse(operation("jarvis")["ok"])
        run.side_effect = [completed(), subprocess.TimeoutExpired("is-active", 5)]
        self.assertFalse(system_manager.restart_system_service("jarvis")["ok"])

    @patch("system_manager.subprocess.run", return_value=completed())
    def test_reboot_uses_only_fixed_noninteractive_command(self, run):
        result = system_manager.reboot_host()
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "reboot_requested")
        self.assertEqual(run.call_args.args[0], ["sudo", "-n", "/sbin/reboot"])
        self.assertIs(run.call_args.kwargs["shell"], False)
        self.assertIn("acceptée", result["message"])

    @patch("system_manager.subprocess.run")
    def test_reboot_error_does_not_claim_success(self, run):
        run.return_value = completed(1)
        self.assertFalse(system_manager.reboot_host()["ok"])
        for error in (subprocess.TimeoutExpired("reboot", 15), OSError()):
            run.side_effect = error
            self.assertFalse(system_manager.reboot_host()["ok"])


class InfrastructureStatusTests(unittest.TestCase):
    @staticmethod
    def runtime(age: int = 0):
        stamp = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
        snapshot = {
            "timestamp": stamp, "hostname": "dell-server", "collector": {"last_success": stamp, "error": None},
            "hardware": {
                "cpu": {"percent": 17.5}, "memory": {"used": 4 * 2 ** 30, "total": 16 * 2 ** 30, "percent": 25.0},
                "disks": {"root": {"used": 50 * 2 ** 30, "total": 100 * 2 ** 30, "percent": 50.0}}, "temperature_c": 48.2,
            },
            "services": [{"name": service.name, "systemd": "active", "http_status": 200} for service in SERVICES], "vps": [],
        }
        return SimpleNamespace(latest=snapshot, settings=Settings(), vps_snapshot=Mock(return_value=[]),
                               collector=SimpleNamespace(collect=Mock(side_effect=AssertionError("No live collection"))))

    @patch("system_manager.subprocess.run", side_effect=AssertionError("No status subprocess"))
    def test_status_is_instant_cached_real_metrics_and_four_local_http_probes(self, run):
        runtime = self.runtime()
        started = time.perf_counter()
        text = system_manager.SystemManager(runtime).get_full_infrastructure_status()
        self.assertLess(time.perf_counter() - started, 0.5)
        for expected in ("CPU : 17.5 %", "RAM : 4.0/16.0 Go (25.0 %)", "Disque système : 50.0/100.0 Go (50.0 %)",
                         "Température : 48.2 °C", "dell-server", "Dernière collecte", "âge 0 s"):
            self.assertIn(expected, text)
        for service in SERVICES:
            self.assertIn(f"{service.label} : systemd active ; HTTP 200 (127.0.0.1:{service.port})", text)
        runtime.collector.collect.assert_not_called()
        run.assert_not_called()

    def test_standalone_realtime_metrics_never_unavailable(self):
        text = system_manager.get_full_infrastructure_status()
        self.assertNotIn("CPU : indisponible", text)
        self.assertNotIn("RAM : indisponible", text)
        self.assertNotIn("Disque système : indisponible", text)
        self.assertIn("CPU : ", text)
        self.assertIn("RAM : ", text)
        self.assertIn("Disque système : ", text)
        self.assertIn("lecture directe temps réel", text)
        self.assertIn("M.O.C.T.A.R — État de l'infrastructure", text)
        for service in SERVICES:
            self.assertIn(service.label, text)

    def test_stale_hardware_is_explicit_even_after_snapshot_republish(self):
        runtime = self.runtime(age=300)
        runtime.latest["timestamp"] = datetime.now(timezone.utc).isoformat()
        text = system_manager.get_full_infrastructure_status(runtime)
        self.assertIn("mesures anciennes", text)
        self.assertIn("âge 300 s", text)
        self.assertIn("CPU : 17.5 %", text)

    def test_registry_reports_distinguish_current_stale_and_restored_vps(self):
        runtime = self.runtime()
        stamp = datetime.now(timezone.utc).isoformat()
        shared = {"last_seen": stamp, "cpu_percent": 12, "memory": {"used": 2 ** 30, "total": 2 * 2 ** 30, "percent": 50},
                  "disk": {"used": 10 * 2 ** 30, "total": 20 * 2 ** 30, "percent": 50}, "healthy": True}
        runtime.vps_snapshot.return_value = [
            {**shared, "hostname": "vps-connected", "stale": False},
            {**shared, "hostname": "vps-expired", "stale": True},
            {**shared, "hostname": "vps-restored", "stale": True, "restored": True},
        ]
        text = system_manager.get_full_infrastructure_status(runtime)
        self.assertIn("vps-connected : connecté.", text)
        self.assertIn("vps-expired : rapport périmé", text)
        self.assertIn("vps-restored : en attente de reconnexion (rapport restauré)", text)
        self.assertIn("CPU 12.0 % ; RAM 1.0/2.0 Go (50.0 %) ; Disque 10.0/20.0 Go (50.0 %)", text)
        runtime.vps_snapshot.assert_called_once_with()

    def test_failed_registry_lookup_keeps_cached_reports_and_warns(self):
        runtime = self.runtime()
        runtime.vps_snapshot.side_effect = RuntimeError("Unavailable")
        runtime.latest["vps"] = [{"hostname": "cached-vps", "restored": True}]
        text = system_manager.get_full_infrastructure_status(runtime)
        self.assertIn("registre indisponible", text)
        self.assertIn("cached-vps : en attente", text)

    def test_many_vps_fit_one_whatsapp_message_with_explicit_omission(self):
        runtime = self.runtime()
        runtime.vps_snapshot.return_value = [
            {"hostname": "vps-" + str(index) + "x" * 120, "last_seen": datetime.now(timezone.utc).isoformat(),
             "restored": True} for index in range(100)
        ]
        text = system_manager.get_full_infrastructure_status(runtime)
        self.assertLessEqual(len(text), 4096)
        self.assertIn("autres VPS dans le tableau de bord", text)
        self.assertIn("Merca-Market", text)
        self.assertIn("Moc-Mal", text)
        self.assertIn("CPU : 17.5 %", text)


if __name__ == "__main__":
    unittest.main()
