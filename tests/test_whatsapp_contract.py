"""Contrats gateway PN/appareils/LID, sans exécuter de commande ni envoyer de message."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from config import Settings
from whatsapp import WhatsAppController, normalize_phone, parse_intent, parse_message


ADMIN = "212770010264"
OTHER = "212770010265"


class WhatsAppContractTests(unittest.TestCase):
    def test_sender_message_message_id_aliases(self) -> None:
        for sender in ("+212 770-010-264", ADMIN + "@s.whatsapp.net", ADMIN + ":17@s.whatsapp.net"):
            with self.subTest(sender=sender):
                result = parse_message({"sender": sender, "message": "statut", "messageId": "3EB0ADMIN"}, ADMIN)
                self.assertEqual(result["id"], "3EB0ADMIN")
                self.assertEqual(result["text"], "statut")
                self.assertEqual(result["source"], "text")

    def test_sender_message_alias_without_stable_id_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Identifiant"):
            parse_message({"sender": ADMIN, "message": "statut"}, ADMIN)

    def test_baileys_object_message_and_key_id_preserved(self) -> None:
        result = parse_message({"event": "messages.upsert", "data": {
            "key": {"id": "3EB0DEVICE", "remoteJid": ADMIN + ":2@s.whatsapp.net"},
            "message": {"extendedTextMessage": {"text": "état"}}}}, ADMIN)
        self.assertEqual(result["id"], "3EB0DEVICE")
        self.assertEqual(result["text"], "état")

    def test_device_jid_requires_numeric_device_and_phone_domains(self) -> None:
        for domain in ("c.us", "s.whatsapp.net"):
            self.assertEqual(normalize_phone(ADMIN + ":0@" + domain), ADMIN)
            self.assertEqual(normalize_phone(ADMIN + ":234@" + domain), ADMIN)
        for value in (ADMIN + ":device@s.whatsapp.net", ADMIN + ":@s.whatsapp.net",
                      ADMIN + ":1@g.us", ADMIN + ":1@lid", ADMIN + ":1",
                      ADMIN + ":1:2@s.whatsapp.net"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_phone(value)

    def test_matching_sender_fields_accept_different_phone_formats(self) -> None:
        result = parse_message({"from": "+212 770 010 264", "sender": ADMIN + ":4@c.us",
                                "key": {"remoteJid": ADMIN + "@s.whatsapp.net"},
                                "id": "matching-fields", "message": "statut"}, ADMIN)
        self.assertEqual(result["text"], "statut")

    def test_conflicting_explicit_sender_fields_rejected(self) -> None:
        for fields in (
            {"from": ADMIN, "sender": OTHER},
            {"from": OTHER, "sender": ADMIN},
            {"from": ADMIN, "key": {"remoteJid": OTHER + "@s.whatsapp.net"}},
            {"sender": ADMIN, "key": {"remoteJid": OTHER + ":9@s.whatsapp.net"}},
            {"from": ADMIN, "sender": ADMIN + "@g.us"},
            {"from": None, "sender": ADMIN},
        ):
            with self.subTest(fields=fields), self.assertRaises(PermissionError):
                parse_message({**fields, "id": "conflicting-fields", "message": "statut"}, ADMIN)

    def test_lid_digits_alone_never_identify_phone(self) -> None:
        for lid in (ADMIN + "@lid", "999999999999999@lid", ADMIN + ":4@lid"):
            with self.subTest(lid=lid), self.assertRaises(PermissionError):
                parse_message({"from": lid, "id": "unassociated-lid", "text": "statut"}, ADMIN)
        with self.assertRaises(PermissionError):
            parse_message({"from": ADMIN + "@lid", "sender": ADMIN,
                           "id": "alias-is-not-association", "text": "statut"}, ADMIN)

    def test_lid_associated_with_verified_admin_pn_accepted(self) -> None:
        for association in (
            {"senderPn": ADMIN + "@s.whatsapp.net"},
            {"senderPn": "+212770010264"},
            {"key": {"remoteJidAlt": ADMIN + ":4@s.whatsapp.net"}},
            {"senderPn": ADMIN, "key": {"remoteJidAlt": ADMIN + "@c.us"}},
        ):
            with self.subTest(association=association):
                result = parse_message({"from": "999999999999999@lid", "id": "associated-lid",
                                        "text": "statut", **association}, ADMIN)
                self.assertEqual(result["id"], "associated-lid")

    def test_baileys_lid_remote_jid_with_admin_alt_pn(self) -> None:
        result = parse_message({"key": {"id": "baileys-lid", "remoteJid": "987654321234567@lid",
                                         "remoteJidAlt": ADMIN + "@s.whatsapp.net"},
                                "message": {"conversation": "statut"}}, ADMIN)
        self.assertEqual(result["text"], "statut")

    def test_lid_associated_nonadmin_invalid_or_conflicting_pn_rejected(self) -> None:
        for association in (
            {"senderPn": OTHER + "@s.whatsapp.net"},
            {"senderPn": ADMIN + "@lid"},
            {"senderPn": ADMIN + "@g.us"},
            {"senderPn": None},
            {"senderPn": ADMIN, "key": {"remoteJidAlt": OTHER + "@s.whatsapp.net"}},
            {"senderPn": ADMIN, "sender": OTHER},
        ):
            with self.subTest(association=association), self.assertRaises(PermissionError):
                parse_message({"from": ADMIN + "@lid", "id": "invalid-associated-lid",
                               "text": "statut", **association}, ADMIN)

    def test_group_cannot_be_resolved_using_associated_admin_number(self) -> None:
        with self.assertRaises(PermissionError):
            parse_message({"from": ADMIN + "@g.us", "senderPn": ADMIN,
                           "id": "group-not-person", "text": "statut"}, ADMIN)

    def test_nonadmin_sender_alias_rejected(self) -> None:
        with self.assertRaises(PermissionError):
            parse_message({"sender": OTHER + ":2@s.whatsapp.net", "messageId": "wrong-admin",
                           "message": "statut"}, ADMIN)

    def test_outgoing_alias_and_baileys_messages_stay_ignored(self) -> None:
        self.assertIsNone(parse_message({"sender": ADMIN, "message": "statut", "fromMe": True}, ADMIN))
        self.assertIsNone(parse_message({"key": {"remoteJid": ADMIN + "@lid", "fromMe": True}}, ADMIN))
        with self.assertRaises(ValueError):
            parse_message({"sender": ADMIN, "messageId": "outgoing-format", "message": "statut", "fromMe": "false"}, ADMIN)

    def test_gateway_status_help_aliases_remain_exact_read_only(self) -> None:
        self.assertEqual(parse_intent("STATUS !"), ("status", None))
        self.assertEqual(parse_intent("aide"), ("help", None))
        self.assertEqual(parse_intent("help."), ("help", None))
        for text in ("help restart klasora", "status; restart klasora", "aide redémarre klasora"):
            self.assertEqual(parse_intent(text), ("unknown", None))


class WhatsAppReadOnlyAliasTests(unittest.IsolatedAsyncioTestCase):
    async def test_help_and_status_do_not_restart_or_backup(self) -> None:
        restart = AsyncMock()
        runtime = SimpleNamespace(latest={"services": [], "hardware": {}, "infrastructure": {}})
        controller = WhatsAppController(Settings(), runtime, restart)
        controller._backup = AsyncMock()
        for text in ("status", "aide", "help"):
            action, service = parse_intent(text)
            response, success = await controller._execute(action, service)
            self.assertTrue(success)
            self.assertTrue(response)
        restart.assert_not_awaited()
        controller._backup.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
