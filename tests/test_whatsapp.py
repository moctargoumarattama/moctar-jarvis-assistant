"""Autorisation, parsing exact, ressources bornées et commandes sans effets réels."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx

from config import Settings
from whatsapp import (WhatsAppController, normalize_phone, parse_intent,
                      parse_message, run_process, validate_media_url)


ADMIN = "212770010264"


def message(text: str = "statut", identifier: str = "test-1", sender: str = ADMIN) -> dict:
    return {"id": identifier, "from": sender + "@s.whatsapp.net", "text": text}


def runtime() -> SimpleNamespace:
    return SimpleNamespace(
        latest={"timestamp": "2026-10-13T00:00:00Z", "services": [
            {"name": "klasora", "label": "Klasora", "port": 5005, "healthy": True}],
            "hardware": {"cpu": {"percent": 12.5}, "memory": {"used": 2**30, "total": 8 * 2**30}},
            "infrastructure": {"whatsapp": {"healthy": True}}},
        collector=SimpleNamespace(add_event=Mock()), publish_module_update=AsyncMock(),
    )


class MessageTests(unittest.TestCase):
    def test_international_phone_and_personal_jids(self) -> None:
        self.assertEqual(normalize_phone("+212 770-010-264"), ADMIN)
        self.assertEqual(normalize_phone(ADMIN + "@c.us"), ADMIN)
        for invalid in (ADMIN + "@g.us", ADMIN + "@lid", "212;systemctl", "1234", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize_phone(invalid)

    def test_other_phone_and_groups_forbidden(self) -> None:
        for sender in ("212770010265", ADMIN + "@g.us", None):
            payload = message()
            payload["from"] = sender
            with self.subTest(sender=sender), self.assertRaises(PermissionError):
                parse_message(payload, ADMIN)

    def test_exact_gateway_audio_contract(self) -> None:
        payload = {"event": "messages.upsert", "sessionId": "1", "data": {
            "from": ADMIN + "@s.whatsapp.net", "fromMe": False, "id": "3EB0ABC",
            "messageType": "audioMessage", "mimetype": "audio/ogg; codecs=opus",
            "audioUrl": "http://127.0.0.1:3001/media/download/audio_temp.ogg",
            "base64": None, "timestamp": 123}}
        parsed = parse_message(payload, "+" + ADMIN)
        self.assertEqual(parsed["source"], "voice")
        self.assertEqual(parsed["audio"]["url"], payload["data"]["audioUrl"])

    def test_gateway_text_and_baileys_text(self) -> None:
        for data in (
            {"id": "a", "from": ADMIN + "@c.us", "body": "état", "fromMe": False},
            {"key": {"id": "a", "remoteJid": ADMIN + "@s.whatsapp.net"},
             "message": {"conversation": "état"}},
        ):
            self.assertEqual(parse_message({"event": "message", "data": data}, ADMIN)["text"], "état")

    def test_outgoing_messages_and_other_events_are_ignored(self) -> None:
        payload = message()
        payload["fromMe"] = True
        self.assertIsNone(parse_message(payload, ADMIN))
        self.assertIsNone(parse_message({"event": "session.status"}, ADMIN))
        payload["fromMe"] = "false"
        with self.assertRaises(ValueError):
            parse_message(payload, ADMIN)

    def test_identifier_and_text_size_required(self) -> None:
        for payload in (
            {"from": ADMIN, "text": "statut"},
            message("x" * 1001), message(identifier="x" * 129),
            message(identifier="id\nsecondline"), message("")
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_message(payload, ADMIN)

    def test_ssrf_redirect_targets_and_traversals_rejected(self) -> None:
        for url in (
            "http://169.254.169.254/media/a.ogg", "https://127.0.0.1:3001/media/a.ogg",
            "http://127.0.0.1:5050/media/a.ogg", "http://localhost:3001/media/a.ogg",
            "http://127.0.0.1:3001/admin", "http://127.0.0.1:3001/media/%2e%2e/admin",
            "http://user:pass@127.0.0.1:3001/media/a.ogg",
            "http://127.0.0.1:3001/media/%5csecret.ogg",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_media_url(url)
        self.assertEqual(validate_media_url("http://127.0.0.1:3001/uploads/a.ogg?key=opaque"),
                         "http://127.0.0.1:3001/uploads/a.ogg?key=opaque")

    def test_phrase_grammar_rejects_negations_and_shell_fragments(self) -> None:
        self.assertEqual(parse_intent("ÉTAT."), ("status", None))
        self.assertEqual(parse_intent("Redémarre BabaMarket !"), ("restart", "babamarket"))
        self.assertEqual(parse_intent("sauvegarde babamarket"), ("backup", "babamarket"))
        for text in ("ne redémarre pas klasora", "redémarre nginx", "redémarre klasora; id",
                     "redémarre klasora et babamarket", "sauvegarde klasora", "redémarre whatsapp-gateway"):
            with self.subTest(text=text):
                self.assertEqual(parse_intent(text), ("unknown", None))


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.settings = Settings(admin_phone_number=ADMIN, whatsapp_webhook_token="test-secret")
        self.runtime = runtime()
        self.restart = AsyncMock(return_value={"ok": True})
        self.controller = WhatsAppController(self.settings, self.runtime, self.restart)
        self.addAsyncCleanup(self.controller.close)

    async def test_queue_and_replay_are_bounded(self) -> None:
        first = await self.controller.accept(message())
        duplicate = await self.controller.accept(message())
        self.assertTrue(first["accepted"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(self.controller.queue.qsize(), 1)
        for index in range(2, 5):
            await self.controller.accept(message(identifier=str(index)))
        with self.assertRaises(asyncio.QueueFull):
            await self.controller.accept(message(identifier="retryable"))
        self.assertNotIn("retryable", self.controller._seen)
        self.restart.assert_not_awaited()

    async def test_unauthorized_sender_cannot_enqueue(self) -> None:
        with self.assertRaises(PermissionError):
            await self.controller.accept(message(sender="212770010265"))
        self.assertEqual(self.controller.queue.qsize(), 0)
        self.assertEqual(len(self.controller._seen), 0)

    async def test_other_session_cannot_enqueue(self) -> None:
        payload = message()
        payload["sessionId"] = "2"
        with self.assertRaises(PermissionError):
            await self.controller.accept(payload)

    async def test_status_uses_existing_snapshot_without_new_probe(self) -> None:
        response, success = await self.controller._execute("status", None)
        self.assertTrue(success)
        self.assertIn("Klasora (5005)", response)
        self.assertIn("WhatsApp (3001)", response)
        self.assertIn("12.5 %", response)
        self.assertIn("1.0/8.0 Gio", response)
        self.restart.assert_not_awaited()

    async def test_worker_restarts_only_allowlisted_service_and_publishes(self) -> None:
        self.controller._send_reply = AsyncMock(return_value=True)
        worker = asyncio.create_task(self.controller._worker())
        self.controller.worker = worker
        await self.controller.accept(message("redémarre klasora"))
        await asyncio.wait_for(self.controller.queue.join(), 2)
        self.restart.assert_awaited_once_with("klasora")
        state = self.controller.snapshot()["last_command"]
        self.assertEqual(state["action"], "restart")
        self.assertEqual(state["service"], "klasora")
        self.assertEqual(state["status"], "completed")
        self.assertTrue(state["reply_sent"])
        self.assertGreaterEqual(self.runtime.publish_module_update.await_count, 3)
        self.assertNotIn(ADMIN, json.dumps(self.controller.snapshot()))

    async def test_worker_recovers_after_failed_restart(self) -> None:
        self.restart.side_effect = RuntimeError("secret must not leak")
        self.controller._send_reply = AsyncMock(return_value=True)
        self.controller.worker = asyncio.create_task(self.controller._worker())
        await self.controller.accept(message("redémarre klasora"))
        await asyncio.wait_for(self.controller.queue.join(), 2)
        self.assertEqual(self.controller.last_command["status"], "failed")
        self.assertNotIn("secret", self.controller.last_command["response"])
        await self.controller.accept(message("statut", identifier="next"))
        await asyncio.wait_for(self.controller.queue.join(), 2)
        self.assertEqual(self.controller.last_command["status"], "completed")

    async def test_unknown_and_unconfigured_backup_never_execute(self) -> None:
        self.controller.settings = replace(self.settings, backup_script="")
        with patch("whatsapp.run_process", new=AsyncMock()) as process:
            _, success = await self.controller._execute("unknown", None)
            self.assertFalse(success)
            response, success = await self.controller._execute("backup", "babamarket")
            self.assertFalse(success)
            self.assertIn("NOC_BACKUP_SCRIPT", response)
            process.assert_not_awaited()
            self.restart.assert_not_awaited()

    async def test_backup_sudo_only_for_exact_installed_path(self) -> None:
        attributes = SimpleNamespace(st_uid=0, st_mode=0o100755)
        for configured, expected in (
            ("/usr/local/bin/backup-babamarket.sh", ["/usr/bin/sudo", "-n", "/usr/local/bin/backup-babamarket.sh"]),
            ("/opt/noc-dashboard/custom-backup.sh", ["/opt/noc-dashboard/custom-backup.sh"]),
        ):
            script = MagicMock()
            script.is_absolute.return_value = script.is_file.return_value = True
            script.is_symlink.return_value = False
            script.resolve.return_value = script
            script.__str__.return_value = configured
            script.stat.return_value = attributes
            parent = Mock()
            parent.stat.return_value = SimpleNamespace(st_uid=0, st_mode=0o40755)
            script.parents = [parent]
            self.controller.settings = replace(self.settings, backup_script=configured)
            with patch("whatsapp.Path", return_value=script), patch("whatsapp.os.name", "posix"), \
                 patch("whatsapp.os.access", return_value=True), patch("whatsapp.shutil.which", return_value="/usr/bin/sudo"), \
                 patch("whatsapp.run_process", new=AsyncMock(return_value=(0, b""))) as process:
                response, success = await self.controller._backup()
                self.assertTrue(success)
                self.assertIn("terminée", response)
                process.assert_awaited_once_with(expected, self.settings.backup_timeout)

    async def test_backup_missing_or_mutable_never_runs(self) -> None:
        self.controller.settings = replace(self.settings, backup_script="/usr/local/bin/backup-babamarket.sh")
        with patch("whatsapp.run_process", new=AsyncMock()) as process:
            response, success = await self.controller._backup()
            self.assertFalse(success)
            self.assertIn("absent", response)
            process.assert_not_awaited()
        script = MagicMock()
        script.is_absolute.return_value = script.is_file.return_value = True
        script.is_symlink.return_value = False
        script.resolve.return_value = script
        script.stat.return_value = SimpleNamespace(st_uid=0, st_mode=0o100777)
        with patch("whatsapp.Path", return_value=script), patch("whatsapp.os.name", "posix"), \
             patch("whatsapp.run_process", new=AsyncMock()) as process:
            _, success = await self.controller._backup()
            self.assertFalse(success)
            process.assert_not_awaited()

    async def test_shutdown_cancels_inflight_voice_and_releases_queue(self) -> None:
        entered = asyncio.Event()
        async def transcribe(audio: dict) -> str:
            entered.set()
            await asyncio.sleep(60)
            return "statut"
        self.controller._transcribe = transcribe
        self.controller.worker = asyncio.create_task(self.controller._worker())
        payload = message()
        payload["audio"] = {"path": "voice.ogg"}
        await self.controller.accept(payload)
        await asyncio.wait_for(entered.wait(), 1)
        await self.controller.close()
        await asyncio.wait_for(self.controller.queue.join(), 1)
        self.assertFalse(self.controller.busy)

    async def test_reply_uses_exact_gateway_contract_and_never_reads_body(self) -> None:
        received = {}
        def handler(request: httpx.Request) -> httpx.Response:
            received.update(json.loads(request.content))
            self.assertEqual(request.headers["x-gateway-secret"], self.settings.gateway_secret)
            return httpx.Response(200, json={"ok": True})
        self.controller.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.assertTrue(await self.controller._send_reply("✅ état opérationnel"))
        self.assertEqual(received, {"to": ADMIN,
                                    "text": "✅ état opérationnel"})

    async def test_media_paths_and_size_are_confined(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media = root / "media"
            media.mkdir()
            source, destination = media / "voice.ogg", root / "copy.ogg"
            source.write_bytes(b"OggSexample")
            self.controller.settings = replace(self.settings, whatsapp_media_dir=str(media), whatsapp_max_audio_bytes=12)
            self.controller._copy_local_audio(str(source), destination)
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            with self.assertRaises(ValueError):
                self.controller._copy_local_audio(str(root / "outside.ogg"), destination)
            with self.assertRaises(ValueError):
                self.controller._copy_local_audio(str(media / ".." / "outside.ogg"), destination)
            source.write_bytes(b"x" * 13)
            with self.assertRaises(ValueError):
                self.controller._copy_local_audio(str(source), destination)

    async def test_download_byte_cap(self) -> None:
        self.controller.settings = replace(self.settings, whatsapp_max_audio_bytes=10)
        self.controller.client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=httpx.ByteStream(b"x" * 11))), follow_redirects=False)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                await self.controller._download_audio("http://127.0.0.1:3001/media/test.ogg", Path(temporary) / "a.ogg")

    async def test_compressed_audio_rejected_before_decompression(self) -> None:
        self.controller.client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-encoding": "gzip"},
                                          stream=httpx.ByteStream(b"untrusted-compressed-data"))))
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "Compression"):
                await self.controller._download_audio("http://127.0.0.1:3001/media/test.ogg", Path(temporary) / "a.ogg")

    async def test_media_redirect_rejected(self) -> None:
        self.controller.client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(302, headers={"location": "http://169.254.169.254/secret"})), follow_redirects=False)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                await self.controller._download_audio("http://127.0.0.1:3001/media/test.ogg", Path(temporary) / "a.ogg")

    async def test_voice_temporary_files_are_removed_after_transcription_failure(self) -> None:
        self.controller._voice.update(status="ready", model_path="missing-whisper-model")
        paths = []
        def copy(source: str, destination: Path) -> None:
            paths.append(destination.parent)
            destination.write_bytes(b"ogg")
        async def process(arguments: list[str], timeout: float, capture: bool = False):
            Path(arguments[-1]).write_bytes(b"RIFFwav")
            return 0, b""
        with patch("whatsapp.shutil.which", return_value="ffmpeg"), \
             patch.object(self.controller, "_copy_local_audio", side_effect=copy), \
             patch("whatsapp.run_process", side_effect=process):
            with self.assertRaises(ValueError):
                await self.controller._transcribe({"path": "voice.ogg"})
        self.assertEqual(len(paths), 1)
        self.assertFalse(paths[0].exists())

    async def test_subprocess_output_is_bounded_and_timeout_reaped(self) -> None:
        with self.assertRaises(ValueError):
            await run_process([sys.executable, "-c", "print('x' * 9000)"], 5, capture=True)
        with self.assertRaises(TimeoutError):
            await run_process([sys.executable, "-c", "import time; time.sleep(5)"], 0.05)


if __name__ == "__main__":
    unittest.main()
