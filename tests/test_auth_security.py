import unittest

import auth_security


class AuthSecurityTests(unittest.TestCase):
    def test_password_hash_is_not_plaintext_and_verifies(self):
        password = "super-secret-pass"

        stored = auth_security.hash_password(password)

        self.assertTrue(auth_security.is_password_hash(stored))
        self.assertNotIn(password, stored)
        self.assertTrue(auth_security.verify_password(password, stored))
        self.assertFalse(auth_security.verify_password("wrong-pass", stored))

    def test_hash_uses_unique_salt_each_time(self):
        password = "same-password"

        first = auth_security.hash_password(password)
        second = auth_security.hash_password(password)

        self.assertNotEqual(first, second)
        self.assertTrue(auth_security.verify_password(password, first))
        self.assertTrue(auth_security.verify_password(password, second))

    def test_plaintext_passwords_are_supported_only_for_legacy_login(self):
        self.assertTrue(auth_security.verify_password("legacy-pass", "legacy-pass"))
        self.assertFalse(auth_security.is_password_hash("legacy-pass"))


if __name__ == "__main__":
    unittest.main()
