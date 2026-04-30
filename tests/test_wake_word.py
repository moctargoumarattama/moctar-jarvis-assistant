import unittest
from unittest.mock import Mock, patch

import wake_word


class WakeWordServiceTests(unittest.TestCase):
    def test_start_disables_service_cleanly_without_access_key(self):
        with patch("wake_word.get_picovoice_access_key", return_value=""):
            service = wake_word.WakeWordService()
            message = service.start()

        self.assertFalse(service.enabled)
        self.assertIn("desactive", message.lower())
        self.assertFalse(service.consume_detection())

    def test_start_disables_service_when_backend_is_unavailable(self):
        with patch("wake_word.get_picovoice_access_key", return_value="test-key"):
            service = wake_word.WakeWordService()
            with patch.object(service, "_start_backend", side_effect=RuntimeError("backend missing")):
                message = service.start()

        self.assertFalse(service.enabled)
        self.assertIn("indisponible", message.lower())


if __name__ == "__main__":
    unittest.main()
