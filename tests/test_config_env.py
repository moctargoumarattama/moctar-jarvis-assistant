"""Formats de numéros administrateur et secret VPS lus depuis l'environnement."""
import os
import unittest
from unittest.mock import patch

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

    def test_vps_token_is_environment_value_or_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Settings.from_env().vps_token, '')
        with patch.dict(os.environ, {'NOC_VPS_TOKEN':'private-test-vps'}, clear=True):
            self.assertEqual(Settings.from_env().vps_token, 'private-test-vps')


if __name__ == '__main__':
    unittest.main()
