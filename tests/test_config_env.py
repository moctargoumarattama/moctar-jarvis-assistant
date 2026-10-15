"""Formats de numéros administrateur et secret VPS lus depuis l'environnement."""
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from config import Settings
from whatsapp import normalize_phone, parse_message


class EnvironmentConfigTests(unittest.TestCase):
    def test_admin_number_is_digits_for_every_configured_phone_format(self):
        for phone in ('212770010264', '+212770010264', '+212 770-010-264',
                      ' +212 (770) 010 264 ', '+212.770.010.264'):
            with self.subTest(phone=phone), patch.dict(os.environ, {'ADMIN_PHONE_NUMBER': phone}, clear=True):
                settings = Settings.from_env()
                self.assertEqual(settings.admin_phone_number, '212770010264')
                self.assertEqual(normalize_phone(settings.admin_phone_number), '212770010264')
                message = parse_message({'id':'phone-test','sender':'212770010264@s.whatsapp.net','message':'statut'}, settings.admin_phone_number)
                self.assertEqual(message['text'], 'statut')

    def test_admin_default_and_explicit_empty_are_distinct(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Settings.from_env().admin_phone_number, '212770010264')
        with patch.dict(os.environ, {'ADMIN_PHONE_NUMBER':''}, clear=True):
            self.assertEqual(Settings.from_env().admin_phone_number, '')

    def test_whatsapp_admin_phone_is_supported_as_single_admin(self):
        with patch.dict(os.environ, {'WHATSAPP_ADMIN_PHONE': '+212 770-010-265'}, clear=True):
            settings = Settings.from_env()
            self.assertEqual(settings.admin_phone_number, '212770010265')
            self.assertEqual(settings.whatsapp_admin_numbers, ('212770010265',))

    def test_vps_token_is_environment_value_or_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Settings.from_env().vps_token, '')
        with patch.dict(os.environ, {'NOC_VPS_TOKEN':'private-test-vps'}, clear=True):
            self.assertEqual(Settings.from_env().vps_token, 'private-test-vps')

    def test_multiple_whatsapp_admins_are_normalized_and_deduplicated(self):
        with patch.dict(os.environ, {
            'WHATSAPP_ADMIN_NUMBERS': '+212 600-000-001,+227.900.000.00,212600000001',
            'ADMIN_PHONE_NUMBER': '212600000002',
        }, clear=True):
            self.assertEqual(Settings.from_env().whatsapp_admin_numbers, ('212600000001', '22790000000'))

    def test_explicit_empty_admin_list_disables_legacy_fallback(self):
        with patch.dict(os.environ, {'WHATSAPP_ADMIN_NUMBERS': '', 'ADMIN_PHONE_NUMBER': '212600000001'}, clear=True):
            self.assertEqual(Settings.from_env().whatsapp_admin_numbers, ())
        with patch.dict(os.environ, {'ADMIN_PHONE_NUMBER': '+212600000001'}, clear=True):
            self.assertEqual(Settings.from_env().whatsapp_admin_numbers, ('212600000001',))

    def test_invalid_admin_list_is_rejected_without_exposing_contents(self):
        for value in ('212600000001,,22790000000', '123', '212600000001@g.us', '212600000001;reboot'):
            with self.subTest(value=value), patch.dict(os.environ, {'WHATSAPP_ADMIN_NUMBERS': value}, clear=True):
                with self.assertRaises(ValueError) as error:
                    Settings.from_env()
                self.assertNotIn(value, str(error.exception))

    def test_vps_state_uses_systemd_state_directory_or_explicit_override(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"STATE_DIRECTORY": directory}, clear=True):
                self.assertEqual(Settings.from_env().vps_state_path, str(Path(directory) / "vps-registry.json"))
            custom = str(Path(directory) / "custom.json")
            with patch.dict(os.environ, {"STATE_DIRECTORY": directory, "NOC_VPS_STATE_PATH": custom}, clear=True):
                self.assertEqual(Settings.from_env().vps_state_path, custom)
            with patch.dict(os.environ, {"NOC_VPS_STATE_PATH": ""}, clear=True):
                self.assertEqual(Settings.from_env().vps_state_path, "")


if __name__ == '__main__':
    unittest.main()
