"""Standalone agent configuration, Linux metrics and authenticated delivery."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from main import VPSReport
import vps_agent


TOKEN = "private-agent-test-token"
ENV = {"NOC_REPORT_URL": "https://noc.example/api/vps/report", "NOC_VPS_TOKEN": TOKEN}


def report() -> dict:
    return {
        "hostname": "vps-test", "cpu_percent": 20.0,
        "memory": {"used": 25, "total": 100, "percent": 25.0},
        "disk": {"used": 10, "total": 100, "percent": 10.0},
        "services": [{"name": "nginx", "status": "active"}],
    }


class AgentTestCase(unittest.TestCase):
    def setUp(self):
        vps_agent.STOP.clear()
        self.addCleanup(vps_agent.STOP.clear)


class ConfigurationTests(AgentTestCase):
    def settings(self, **values):
        with patch.dict(os.environ, {**ENV, **values}, clear=True):
            return vps_agent.Settings.from_environment()

    def test_url_and_token_are_required_without_exposing_values(self):
        for missing in ("NOC_REPORT_URL", "NOC_VPS_TOKEN"):
            with self.subTest(missing=missing), self.assertRaises(vps_agent.ConfigurationError) as error:
                self.settings(**{missing: ""})
            self.assertNotIn(TOKEN, str(error.exception))

    def test_https_url_is_preserved_and_trailing_slash_is_removed(self):
        settings = self.settings(NOC_REPORT_URL="https://noc.example:5050/api/vps/report/")
        self.assertEqual(settings.report_url, "https://noc.example:5050/api/vps/report")
        self.assertEqual(settings.token, TOKEN)

    def test_http_loopback_and_tailscale_addresses_need_no_override(self):
        for host in ("localhost", "127.0.0.1", "[::1]", "100.64.0.0", "100.68.163.14", "100.127.255.255"):
            with self.subTest(host=host):
                settings = self.settings(NOC_REPORT_URL=f"http://{host}:5050/api/vps/report")
                self.assertEqual(settings.report_url, f"http://{host}:5050/api/vps/report")

    def test_other_http_targets_require_explicit_override(self):
        for host in ("100.63.255.255", "100.128.0.0", "192.168.11.20", "8.8.8.8", "noc.example"):
            with self.subTest(host=host):
                url = f"http://{host}:5050/api/vps/report"
                with self.assertRaises(vps_agent.ConfigurationError):
                    self.settings(NOC_REPORT_URL=url)
                self.assertEqual(self.settings(NOC_REPORT_URL=url, NOC_ALLOW_INSECURE_HTTP="true").report_url, url)

    def test_invalid_urls_are_rejected_even_with_http_override(self):
        for url in (
            "ftp://noc.example/api/vps/report", "https:///api/vps/report",
            "https://user:password@noc.example/api/vps/report",
            "https://noc.example/api/sentinel/incident", "https://noc.example/api/vps/report?token=secret",
            "https://noc.example/api/vps/report#fragment", "https://noc.example:0/api/vps/report",
            "https://noc.example:65536/api/vps/report", "https://[bad/api/vps/report",
        ):
            with self.subTest(url=url), self.assertRaises(vps_agent.ConfigurationError):
                self.settings(NOC_REPORT_URL=url, NOC_ALLOW_INSECURE_HTTP="true")

    def test_invalid_tokens_are_rejected_without_echoing_them(self):
        for token in ("secret with-space", "secret\ninside", "non-ascii-é", "x" * 4097):
            with self.subTest(length=len(token)), self.assertRaises(vps_agent.ConfigurationError) as error:
                self.settings(NOC_VPS_TOKEN=token)
            self.assertNotIn(token, str(error.exception))

    def test_systemd_names_are_deduplicated_and_bounded(self):
        settings = self.settings(NOC_VPS_SERVICES="nginx, nginx, worker@1.service")
        self.assertEqual(settings.services, ("nginx", "worker@1.service"))
        for names in ("nginx;id", "-nginx", "x" * 129, ",".join(f"svc{n}" for n in range(17))):
            with self.subTest(names=names), self.assertRaises(vps_agent.ConfigurationError):
                self.settings(NOC_VPS_SERVICES=names)


class LinuxMetricTests(AgentTestCase):
    def test_cpu_delta_excludes_guest_ticks_and_stays_in_range(self):
        sampler = vps_agent.CpuSampler()
        ticks = ["cpu 100 0 100 800 0 0 0 0 50 0\n", "cpu 160 0 140 900 0 0 0 0 80 0\n"]
        with patch("vps_agent.Path.read_text", side_effect=ticks):
            self.assertEqual(sampler.sample(), 0.0)
            self.assertEqual(sampler.sample(), 50.0)

    def test_empty_or_incomplete_cpu_statistics_raise_controlled_os_error(self):
        for text in ("", "cpu\n", "unexpected 1 2 3 4\n"):
            with self.subTest(text=text), patch("vps_agent.Path.read_text", return_value=text):
                with self.assertRaises(OSError):
                    vps_agent.CpuSampler().sample()

    def test_older_kernel_memory_fallback_uses_byte_units_and_consistent_percent(self):
        meminfo = "MemTotal: 1000 kB\nMemFree: 200 kB\nBuffers: 50 kB\nCached: 100 kB\nSReclaimable: 50 kB\nShmem: 20 kB\n"
        with patch("vps_agent.Path.read_text", return_value=meminfo):
            memory = vps_agent.memory_metrics()
        self.assertEqual(memory, {"used": 620 * 1024, "total": 1000 * 1024, "percent": 62.0})

    def test_collect_report_matches_actual_backend_schema_from_linux_proc(self):
        cpu = vps_agent.CpuSampler()
        cpu_ticks = iter(["cpu 100 0 100 800 0 0 0 0\n", "cpu 160 0 140 900 0 0 0 0\n"])

        def proc_file(path, **kwargs):
            if path.as_posix() == "/proc/stat":
                return next(cpu_ticks)
            self.assertEqual(path.as_posix(), "/proc/meminfo")
            return "MemTotal: 1000 kB\nMemAvailable: 400 kB\n"

        settings = vps_agent.Settings(ENV["NOC_REPORT_URL"], TOKEN, ("nginx", "worker@1.service"))
        with patch("vps_agent.Path.read_text", autospec=True, side_effect=proc_file), \
                patch("vps_agent.shutil.disk_usage", return_value=SimpleNamespace(used=50, total=100)), \
                patch("vps_agent.socket.gethostname", return_value="vps-test"), \
                patch("vps_agent.subprocess.run", return_value=SimpleNamespace(stdout="active\n")):
            cpu.sample()
            payload = vps_agent.collect_report(settings, cpu)
        validated = VPSReport.model_validate(payload)
        self.assertEqual(validated.cpu_percent, 50.0)
        self.assertEqual(validated.memory.used, 600 * 1024)
        self.assertEqual(validated.disk.percent, 50.0)
        self.assertEqual([service.name for service in validated.services], ["nginx", "worker@1.service"])
        self.assertNotIn(TOKEN, json.dumps(payload))

    def test_failed_systemd_probe_stays_unknown_and_has_no_shell(self):
        for error in (OSError("systemctl missing"), subprocess.TimeoutExpired("systemctl", 1)):
            with self.subTest(error=type(error).__name__), \
                    patch("vps_agent.subprocess.run", side_effect=error) as execute:
                self.assertEqual(vps_agent.service_status("nginx.service"), {"name": "nginx.service", "status": "unknown"})
            self.assertEqual(execute.call_args.args[0], ["systemctl", "is-active", "nginx.service"])
            self.assertEqual(execute.call_args.kwargs["timeout"], 1.0)
            self.assertNotIn("shell", execute.call_args.kwargs)


class DeliveryTests(AgentTestCase):
    def settings(self):
        return vps_agent.Settings(ENV["NOC_REPORT_URL"], TOKEN, ())

    def success(self):
        response = MagicMock(status=200)
        response.__enter__.return_value = response
        return response

    def test_post_contains_plain_json_and_auth_header_with_bounded_timeout(self):
        opener = Mock()
        opener.open.return_value = self.success()
        with patch("vps_agent.urllib.request.build_opener", return_value=opener) as build:
            self.assertTrue(vps_agent.send_report(self.settings(), report()))
        request = opener.open.call_args.args[0]
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(headers["x-vps-token"], TOKEN)
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(json.loads(request.data), report())
        self.assertNotIn("ciphertext", json.loads(request.data))
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 3.0})
        proxy, redirect = build.call_args.args
        self.assertEqual(proxy.proxies, {})
        self.assertIsInstance(redirect, vps_agent.NoRedirect)

    def test_unauthorized_or_invalid_report_is_not_retried_and_http_error_is_closed(self):
        for code in (401, 403, 422):
            stream = io.BytesIO(b"private server error")
            error = urllib.error.HTTPError(self.settings().report_url, code, "refused", {}, stream)
            opener = Mock(open=Mock(side_effect=error))
            output = io.StringIO()
            with self.subTest(code=code), patch("vps_agent.urllib.request.build_opener", return_value=opener), \
                    patch.object(vps_agent.STOP, "wait") as wait, contextlib.redirect_stderr(output):
                self.assertFalse(vps_agent.send_report(self.settings(), report()))
            self.assertEqual(opener.open.call_count, 1)
            wait.assert_not_called()
            self.assertTrue(stream.closed)
            self.assertNotIn(TOKEN, output.getvalue())

    def test_server_failure_or_rate_limit_gets_one_retry(self):
        for code in (429, 500):
            error = urllib.error.HTTPError(self.settings().report_url, code, "refused", {}, io.BytesIO())
            opener = Mock(open=Mock(side_effect=[error, self.success()]))
            with self.subTest(code=code), patch("vps_agent.urllib.request.build_opener", return_value=opener), \
                    patch.object(vps_agent.STOP, "wait", return_value=False) as wait, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertTrue(vps_agent.send_report(self.settings(), report()))
            self.assertEqual(opener.open.call_count, 2)
            wait.assert_called_once_with(1.0)

    def test_network_failure_and_timeout_are_bounded_and_redact_secrets(self):
        opener = Mock(open=Mock(side_effect=[urllib.error.URLError(TOKEN), TimeoutError(TOKEN)]))
        output = io.StringIO()
        with patch("vps_agent.urllib.request.build_opener", return_value=opener), \
                patch.object(vps_agent.STOP, "wait", return_value=False), contextlib.redirect_stderr(output):
            self.assertFalse(vps_agent.send_report(self.settings(), report()))
        self.assertEqual(opener.open.call_count, 2)
        self.assertNotIn(TOKEN, output.getvalue())

    def test_stop_prevents_post_or_interrupts_retry(self):
        opener = Mock(open=Mock(side_effect=TimeoutError))
        with patch("vps_agent.urllib.request.build_opener", return_value=opener):
            vps_agent.STOP.set()
            self.assertFalse(vps_agent.send_report(self.settings(), report()))
            opener.open.assert_not_called()
            vps_agent.STOP.clear()
            with patch.object(vps_agent.STOP, "wait", return_value=True), contextlib.redirect_stderr(io.StringIO()):
                self.assertFalse(vps_agent.send_report(self.settings(), report()))
            self.assertEqual(opener.open.call_count, 1)


class LocalHTTPTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        self.requests = []
        self.redirect = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                owner.requests.append((self.path, dict(self.headers), body))
                if owner.redirect:
                    self.send_response(307)
                    self.send_header("Location", "/redirect-target")
                elif self.headers.get("x-vps-token") != TOKEN:
                    self.send_response(401)
                else:
                    try:
                        VPSReport.model_validate(json.loads(body))
                        self.send_response(200)
                    except ValueError:
                        self.send_response(422)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/vps/report"

    def test_real_stdlib_http_request_is_authenticated_and_validated_by_backend_schema(self):
        self.assertTrue(vps_agent.send_report(vps_agent.Settings(self.url, TOKEN, ()), report()))
        self.assertEqual(len(self.requests), 1)
        path, headers, body = self.requests[0]
        self.assertEqual(path, "/api/vps/report")
        self.assertEqual(headers["X-Vps-Token"], TOKEN)
        self.assertEqual(json.loads(body), report())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(vps_agent.send_report(vps_agent.Settings(self.url, "wrong", ()), report()))
        self.assertEqual(len(self.requests), 2)

    def test_redirect_target_never_receives_the_token(self):
        self.redirect = True
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(vps_agent.send_report(vps_agent.Settings(self.url, TOKEN, ()), report()))
        self.assertEqual([request[0] for request in self.requests], ["/api/vps/report"])


class MainTests(AgentTestCase):
    def test_once_exit_codes_and_signal_handlers_are_correct(self):
        settings = vps_agent.Settings(ENV["NOC_REPORT_URL"], TOKEN, ())
        for accepted in (True, False):
            with self.subTest(accepted=accepted), patch("vps_agent.Settings.from_environment", return_value=settings), \
                    patch("vps_agent.sys.platform", "linux"), patch("vps_agent.signal.signal") as register, \
                    patch("vps_agent.CpuSampler") as sampler, patch("vps_agent.collect_report", return_value=report()), \
                    patch("vps_agent.send_report", return_value=accepted) as send, \
                    patch.object(vps_agent.STOP, "wait", return_value=False):
                self.assertEqual(vps_agent.main(["--once"]), 0 if accepted else 1)
            send.assert_called_once_with(settings, report())
            sampler.return_value.sample.assert_called_once()
            self.assertEqual(register.call_count, 2)
            register.call_args_list[0].args[1](None, None)
            self.assertTrue(vps_agent.STOP.is_set())
            vps_agent.STOP.clear()

    def test_configuration_or_non_linux_failure_exits_two_before_collection(self):
        for linux in (True, False):
            environment = {} if linux else ENV
            output = io.StringIO()
            with self.subTest(linux=linux), patch.dict(os.environ, environment, clear=True), \
                    patch("vps_agent.sys.platform", "linux" if linux else "win32"), \
                    patch("vps_agent.CpuSampler") as sample, contextlib.redirect_stderr(output):
                self.assertEqual(vps_agent.main(["--once"]), 2)
            sample.assert_not_called()
            self.assertNotIn(TOKEN, output.getvalue())

    def test_failed_cpu_or_report_collection_exits_one_without_posting(self):
        for prime_failed in (True, False):
            with self.subTest(prime_failed=prime_failed), patch.dict(os.environ, ENV, clear=True), \
                    patch("vps_agent.sys.platform", "linux"), patch("vps_agent.signal.signal"), \
                    patch("vps_agent.CpuSampler") as sampler, \
                    patch("vps_agent.collect_report", side_effect=OSError(TOKEN)), \
                    patch("vps_agent.send_report") as send, patch.object(vps_agent.STOP, "wait", return_value=False), \
                    contextlib.redirect_stderr(io.StringIO()) as output:
                if prime_failed:
                    sampler.return_value.sample.side_effect = OSError(TOKEN)
                self.assertEqual(vps_agent.main(["--once"]), 1)
            send.assert_not_called()
            self.assertNotIn(TOKEN, output.getvalue())


if __name__ == "__main__":
    unittest.main()
