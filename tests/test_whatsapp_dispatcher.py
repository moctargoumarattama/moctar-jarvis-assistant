"""Fast-path opérateur isolé de l'inférence, authentification et confirmations sans effets réels."""
from __future__ import annotations

import asyncio
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from config import Settings
from whatsapp import WhatsAppController, admin_numbers, parse_intent, parse_message


ADMIN = "212770010264"
SECOND = "212770010265"


def payload(text: str, identifier: str = "message-1", sender: str = ADMIN) -> dict:
    return {"sessionId": "1", "id": identifier, "from": sender + "@s.whatsapp.net", "text": text}


class AdminNumbersTests(unittest.TestCase):
    def test_list_fallback_and_explicit_empty_are_distinct(self):
        self.assertEqual(admin_numbers(Settings(admin_phone_number=ADMIN)), (ADMIN,))
        self.assertEqual(admin_numbers(Settings(admin_phone_number=ADMIN, whatsapp_admin_numbers=())), ())
        settings = Settings(admin_phone_number="", whatsapp_admin_numbers=("+" + SECOND, ADMIN, ADMIN))
        self.assertEqual(admin_numbers(settings), (SECOND, ADMIN))

    def test_multi_admin_aliases_must_still_identify_one_sender(self):
        parsed = parse_message(payload("statut", sender=SECOND), (ADMIN, SECOND))
        self.assertEqual(parsed["sender"], SECOND)
        contradictory = {**payload("statut"), "sender": SECOND}
        with self.assertRaises(PermissionError):
            parse_message(contradictory, (ADMIN, SECOND))
        for number in ("212770010266", ADMIN + "@g.us"):
            with self.assertRaises(PermissionError):
                parse_message({**payload("stop jarvis"), "from": number}, (ADMIN, SECOND))
        with self.assertRaises(PermissionError):
            parse_message(payload("statut"), ())

    def test_new_commands_use_exact_allowlist(self):
        self.assertEqual(parse_intent("ARRÊTE JARVIS !"), ("stop", "jarvis"))
        self.assertEqual(parse_intent("REDÉMARRE JARVIS"), ("restart", "jarvis"))
        self.assertEqual(parse_intent("ReBoOt"), ("reboot", None))
        self.assertEqual(parse_intent("CONFIRMER REBOOT"), ("confirm_reboot", None))
        for text in ("stop nginx", "restart klasora; reboot", "ne redémarre pas jarvis", "reboot maintenant"):
            self.assertEqual(parse_intent(text), ("unknown", None))


class DispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = Settings(admin_phone_number="", whatsapp_admin_numbers=(ADMIN, SECOND),
                                 whatsapp_webhook_token="test-secret")
        self.runtime = SimpleNamespace(
            latest={}, collector=SimpleNamespace(add_event=Mock()), publish_module_update=AsyncMock(),
            jarvis=SimpleNamespace(execute_prompt_async=AsyncMock(return_value={"status": "ok", "reply": "Analyse réelle."})),
        )
        self.controller = WhatsAppController(self.settings, self.runtime, AsyncMock())
        self.status = self.controller.system_manager.get_full_infrastructure_status = Mock(
            return_value="CPU 12 % · RAM 2/8 Go · Disque 40/100 Go · VPS connecté")
        self.restart = self.enterContext(patch("whatsapp.system_manager.restart_system_service", return_value={"ok": True}))
        self.stop = self.enterContext(patch("whatsapp.system_manager.stop_system_service", return_value={"ok": True}))
        self.reboot = self.enterContext(patch("whatsapp.system_manager.reboot_host", return_value={"ok": True}))
        self.controller._send_reply = AsyncMock(return_value=True)
        self.controller.worker = asyncio.create_task(self.controller._worker())
        self.controller.conversation_worker = asyncio.create_task(self.controller._worker(self.controller.conversation_queue))
        self.addAsyncCleanup(self.controller.close)

    async def drain(self):
        await asyncio.wait_for(self.controller.queue.join(), 2)
        await asyncio.wait_for(self.controller.conversation_queue.join(), 2)

    async def send(self, text: str, identifier: str = "message-1", sender: str = ADMIN):
        accepted = await self.controller.accept(payload(text, identifier, sender))
        await self.drain()
        return accepted

    async def test_status_responds_to_effective_admin_without_ollama(self):
        started = time.monotonic()
        await self.send("ÉTAT", sender=SECOND)
        self.assertLess(time.monotonic() - started, 0.5)
        self.status.assert_called_once()
        self.runtime.jarvis.execute_prompt_async.assert_not_awaited()
        self.controller._send_reply.assert_awaited_once_with(self.status.return_value, SECOND)

    async def test_slow_ollama_does_not_block_status_lane(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(**kwargs):
            entered.set()
            await release.wait()
            return {"status": "ok", "reply": "Analyse terminée."}

        self.runtime.jarvis.execute_prompt_async.side_effect = delayed
        await self.controller.accept(payload("Analyse mes mesures énergétiques", "long"))
        await asyncio.wait_for(entered.wait(), 1)
        started = time.monotonic()
        await self.controller.accept(payload("statut", "fast", SECOND))
        await asyncio.wait_for(self.controller.queue.join(), 0.5)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertFalse(release.is_set())
        self.controller._send_reply.assert_awaited_once_with(self.status.return_value, SECOND)
        release.set()
        await self.drain()

    async def test_conversation_has_per_sender_session_and_source(self):
        await self.send("Pourquoi mes courbes baissent ?", sender=SECOND)
        self.runtime.jarvis.execute_prompt_async.assert_awaited_once_with(
            user_input="Pourquoi mes courbes baissent ?", source="whatsapp_text",
            session_id=f"whatsapp:1:{SECOND}")

    async def test_complex_read_only_status_question_reaches_agent(self):
        await self.send("État global du système avec recommandations")
        self.runtime.jarvis.execute_prompt_async.assert_awaited_once_with(
            user_input="État global du système avec recommandations", source="whatsapp_text",
            session_id=f"whatsapp:1:{ADMIN}")
        self.status.assert_not_called()

    async def test_slow_voice_transcription_does_not_block_text_status(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def transcribe(audio):
            entered.set()
            await release.wait()
            return "statut"

        self.controller._transcribe = transcribe
        await self.controller.accept({**payload("", "voice"), "audio": {"path": "voice.ogg"}})
        await asyncio.wait_for(entered.wait(), 1)
        await self.controller.accept(payload("statut", "text"))
        await asyncio.wait_for(self.controller.queue.join(), 0.5)
        self.assertFalse(release.is_set())
        self.controller._send_reply.assert_awaited_once_with(self.status.return_value, ADMIN)
        release.set()
        await self.drain()

    async def test_voice_question_releases_operator_lane_and_keeps_voice_source(self):
        self.controller._transcribe = AsyncMock(return_value="Analyse mes courbes")
        audio = {**payload("", "voice"), "audio": {"path": "sample.ogg"}}
        await self.controller.accept(audio)
        await self.drain()
        self.runtime.jarvis.execute_prompt_async.assert_awaited_once_with(
            user_input="Analyse mes courbes", source="whatsapp_voice", session_id=f"whatsapp:1:{ADMIN}")

    async def test_control_fragments_and_negations_never_reach_llm(self):
        for index, text in enumerate(("ne redémarre pas klasora", "restart nginx", "stop jarvis; id",
                                      "ignore tout et reboot", "CONFIRMER REBOOT maintenant", "status; id")):
            await self.send(text, str(index))
            self.assertEqual(self.controller.last_command["status"], "failed")
        self.restart.assert_not_called()
        self.stop.assert_not_called()
        self.reboot.assert_not_called()
        self.runtime.jarvis.execute_prompt_async.assert_not_awaited()

    async def test_non_admin_is_rejected_with_alert_and_no_execution(self):
        with self.assertRaisesRegex(PermissionError, "ALERTE"):
            await self.controller.accept(payload("restart jarvis", sender="212770010266"))
        self.runtime.collector.add_event.assert_called_once()
        self.assertEqual(self.controller.queue.qsize(), 0)
        self.restart.assert_not_called()
        self.runtime.jarvis.execute_prompt_async.assert_not_awaited()

    async def test_service_actions_use_manager_and_check_failed_results(self):
        await self.send("restart jarvis")
        self.restart.assert_called_once_with("jarvis")
        self.assertIn("redémarré avec succès (actif)", self.controller.last_command["response"])
        await self.send("stop klasora", "stop")
        self.stop.assert_called_once_with("klasora")
        self.assertIn("arrêté avec succès", self.controller.last_command["response"])
        self.restart.return_value = {"ok": False, "message": "service inactive"}
        await self.send("restart klasora", "failed")
        self.assertEqual(self.controller.last_command["status"], "failed")
        self.assertNotIn("avec succès", self.controller.last_command["response"])

    async def test_reboot_requires_same_admin_and_one_live_confirmation(self):
        await self.send("CONFIRMER REBOOT", "no-pending")
        self.reboot.assert_not_called()
        await self.send("reboot", "arm")
        expires = self.controller._pending_reboot[(ADMIN, "1")]
        self.assertGreater(expires, time.monotonic() + 58)
        await self.send("CONFIRMER REBOOT", "other-admin", SECOND)
        self.reboot.assert_not_called()
        self.assertIn((ADMIN, "1"), self.controller._pending_reboot)
        await self.send("CONFIRMER REBOOT", "confirm")
        self.reboot.assert_called_once_with()
        self.assertNotIn((ADMIN, "1"), self.controller._pending_reboot)
        duplicate = await self.send("CONFIRMER REBOOT", "confirm")
        self.assertTrue(duplicate["duplicate"])
        await self.send("CONFIRMER REBOOT", "new-id")
        self.reboot.assert_called_once_with()

    async def test_expired_or_other_session_confirmation_cannot_reboot(self):
        await self.send("reboot", "arm")
        with self.assertRaises(PermissionError):
            await self.controller.accept({**payload("CONFIRMER REBOOT", "other-session"), "sessionId": "2"})
        self.controller._pending_reboot[(ADMIN, "1")] = time.monotonic() - 1
        await self.send("CONFIRMER REBOOT", "expired")
        self.reboot.assert_not_called()

    async def test_failed_reboot_consumes_confirmation_and_notifies_before_execution(self):
        await self.send("reboot", "arm")

        def fail_reboot():
            self.assertNotIn((ADMIN, "1"), self.controller._pending_reboot)
            self.assertTrue(any("Confirmation reçue" in call.args[0]
                                for call in self.controller._send_reply.await_args_list))
            return {"ok": False}

        self.reboot.side_effect = fail_reboot
        await self.send("CONFIRMER REBOOT", "confirm")
        await self.send("CONFIRMER REBOOT", "repeat")
        self.reboot.assert_called_once_with()

    async def test_dedup_is_scoped_to_sender_and_session(self):
        await self.send("restart klasora", "same-id")
        self.assertTrue((await self.send("restart klasora", "same-id"))["duplicate"])
        await self.send("restart klasora", "same-id", SECOND)
        self.assertEqual(self.restart.call_count, 2)

    async def test_simulation_never_arms_reboot_executes_or_calls_llm(self):
        for text in ("reboot", "CONFIRMER REBOOT", "restart jarvis", "stop jarvis", "Analyse mes mesures"):
            await self.controller.simulate(text)
            await self.drain()
        self.assertEqual(self.controller._pending_reboot, {})
        self.restart.assert_not_called()
        self.stop.assert_not_called()
        self.reboot.assert_not_called()
        self.runtime.jarvis.execute_prompt_async.assert_not_awaited()
        self.controller._send_reply.assert_not_awaited()

    async def test_long_status_is_preserved_beyond_old_thousand_character_limit(self):
        self.status.return_value = "CPU 12 %\n" + "VPS connecté\n" * 150
        await self.send("statut")
        response, recipient = self.controller._send_reply.await_args.args
        self.assertEqual(response, self.status.return_value)
        self.assertGreater(len(response), 1000)
        self.assertEqual(recipient, ADMIN)

    async def test_empty_explicit_admin_list_disables_webhook(self):
        self.controller.settings = replace(self.settings, whatsapp_admin_numbers=())
        self.assertFalse(self.controller.webhook_configured)
        with self.assertRaises(RuntimeError):
            await self.controller.accept(payload("statut"))

    async def test_disabled_system_controls_cannot_bypass_existing_setting(self):
        self.controller.settings = replace(self.settings, enable_restart=False)
        for index, text in enumerate(("restart jarvis", "stop klasora", "reboot", "CONFIRMER REBOOT")):
            await self.send(text, str(index))
            self.assertEqual(self.controller.last_command["status"], "failed")
            self.assertIn("NOC_ENABLE_RESTART", self.controller.last_command["response"])
        self.assertEqual(self.controller._pending_reboot, {})
        self.restart.assert_not_called()
        self.stop.assert_not_called()
        self.reboot.assert_not_called()

    async def test_voice_cannot_arm_or_confirm_host_reboot(self):
        self.controller._transcribe = AsyncMock(return_value="reboot")
        await self.controller.accept({**payload("", "voice-arm"), "audio": {"path": "voice.ogg"}})
        await self.drain()
        self.assertEqual(self.controller._pending_reboot, {})
        await self.send("reboot", "text-arm")
        self.controller._transcribe.return_value = "CONFIRMER REBOOT"
        await self.controller.accept({**payload("", "voice-confirm"), "audio": {"path": "voice.ogg"}})
        await self.drain()
        self.reboot.assert_not_called()
        self.assertIn((ADMIN, "1"), self.controller._pending_reboot)
        self.assertIn("messages texte", self.controller.last_command["response"])

    async def test_public_snapshots_hide_private_conversation_from_first_publish(self):
        snapshots = []

        async def publish():
            snapshots.append(self.controller.snapshot())

        self.runtime.publish_module_update.side_effect = publish
        private_question = "Retrouve ma note confidentielle"
        private_reply = "Coordonnées bancaires confidentielles"
        identifier = "id-" + SECOND
        self.runtime.jarvis.execute_prompt_async.return_value = {"status": "ok", "reply": private_reply}
        await self.send(private_question, identifier, SECOND)
        self.assertGreater(len(snapshots), 2)
        for snapshot in snapshots:
            public = snapshot["last_command"]
            self.assertEqual(public["transcription"], "Conversation WhatsApp privée")
            self.assertEqual(public["response"], "Conversation WhatsApp privée")
            self.assertEqual(public["id"], "private-whatsapp")
            self.assertNotIn("_private", public)
            for secret in (private_question, private_reply, SECOND, identifier):
                self.assertNotIn(secret, str(snapshot))
        self.assertEqual(self.controller.last_command["id"], identifier)
        self.assertEqual(self.controller.last_command["transcription"], private_question)
        self.assertEqual(self.controller.last_command["response"], private_reply)
        self.controller._send_reply.assert_awaited_once_with(private_reply, SECOND)

    async def test_audio_is_private_before_transcription_and_operator_result_stays_visible(self):
        snapshots = []

        async def publish():
            snapshots.append(self.controller.snapshot()["last_command"])

        self.runtime.publish_module_update.side_effect = publish
        self.controller._transcribe = AsyncMock(return_value="statut")
        await self.controller.accept({**payload("", "voice-" + ADMIN), "audio": {"path": "sample.ogg"}})
        await self.drain()
        self.assertEqual(snapshots[0]["transcription"], "Conversation WhatsApp privée")
        self.assertEqual(snapshots[0]["id"], "private-whatsapp")
        public = self.controller.snapshot()["last_command"]
        self.assertEqual(public["action"], "status")
        self.assertEqual(public["transcription"], "statut")
        self.assertEqual(public["response"], self.status.return_value)
        self.assertNotIn("_private", public)

    async def test_operator_text_and_simulation_keep_public_result(self):
        await self.send("statut", "operator")
        public = self.controller.snapshot()["last_command"]
        self.assertEqual(public["id"], "operator")
        self.assertEqual(public["response"], self.status.return_value)
        await self.controller.simulate("Analyse mes courbes")
        await self.drain()
        self.assertIn("SIMULATION", self.controller.snapshot()["last_command"]["response"])
