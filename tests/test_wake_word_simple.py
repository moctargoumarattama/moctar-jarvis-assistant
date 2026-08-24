import unittest

import wake_word_simple


class WakeWordSimpleTests(unittest.TestCase):
    def setUp(self):
        if hasattr(wake_word_simple, "reset_soft_wake_state"):
            wake_word_simple.reset_soft_wake_state()

    def test_normalize_wake_text_keeps_simple_words_only(self):
        normalized = wake_word_simple.normalize_wake_text("Hey,   Moctar !")

        self.assertEqual("hey moctar", normalized)

    def test_contains_wake_word_detects_yo(self):
        self.assertTrue(wake_word_simple.contains_wake_word("yo"))

    def test_contains_wake_word_detects_hey_moctar(self):
        self.assertTrue(wake_word_simple.contains_wake_word("hey moctar"))

    def test_contains_wake_word_detects_yo_moctar(self):
        self.assertTrue(wake_word_simple.contains_wake_word("yo moctar"))

    def test_contains_wake_word_rejects_moctar_alone(self):
        self.assertFalse(wake_word_simple.contains_wake_word("moctar"))

    def test_contains_wake_word_rejects_spelled_moctar(self):
        self.assertFalse(wake_word_simple.contains_wake_word("hey m.o.c.t.a.r"))

    def test_contains_wake_word_detects_inline_yo_command(self):
        self.assertTrue(wake_word_simple.contains_wake_word("yo mets la musique"))

    def test_contains_wake_word_detects_inline_you_alias(self):
        self.assertTrue(wake_word_simple.contains_wake_word("you mets la musique"))

    def test_contains_wake_word_detects_inline_active_command(self):
        self.assertTrue(wake_word_simple.contains_wake_word("yo active youtube"))

    def test_contains_wake_word_rejects_loose_yo_sentence(self):
        self.assertFalse(wake_word_simple.contains_wake_word("yo salut"))

    def test_detect_wake_phrase_prioritizes_longer_match(self):
        match, confidence = wake_word_simple.detect_wake_phrase("hey moctar ouvre github")

        self.assertEqual("hey moctar", match)
        self.assertGreaterEqual(confidence, wake_word_simple.config.SOFT_WAKE_MIN_CONFIDENCE)

    def test_parse_wake_activation_extracts_inline_command(self):
        activation = wake_word_simple.parse_wake_activation("yo mets la musique")

        self.assertIsNotNone(activation)
        self.assertEqual("yo", activation["wake_word"])
        self.assertEqual("mets la musique", activation["command"])

    def test_parse_wake_activation_extracts_inline_command_with_alias(self):
        activation = wake_word_simple.parse_wake_activation("you mets ninho")

        self.assertIsNotNone(activation)
        self.assertEqual("yo", activation["wake_word"])
        self.assertEqual("mets ninho", activation["command"])

    def test_parse_wake_activation_extracts_inline_active_command(self):
        activation = wake_word_simple.parse_wake_activation("yo active youtube")

        self.assertIsNotNone(activation)
        self.assertEqual("yo", activation["wake_word"])
        self.assertEqual("active youtube", activation["command"])

    def test_evaluate_transcript_for_activation_uses_pending_name_alias(self):
        first = wake_word_simple.evaluate_transcript_for_activation("Mokhtar", now_value=10.0)
        second = wake_word_simple.evaluate_transcript_for_activation("mets la musique", now_value=12.0)

        self.assertIsNone(first)
        self.assertIsNotNone(second)
        self.assertEqual("hey moctar", second["wake_word"])
        self.assertEqual("mets la musique", second["command"])

    def test_evaluate_transcript_for_activation_accepts_command_only_opening(self):
        activation = wake_word_simple.evaluate_transcript_for_activation("active YouTube", now_value=10.0)

        self.assertIsNotNone(activation)
        self.assertTrue(activation["command_only"])
        self.assertEqual("active youtube", activation["command"])

    def test_evaluate_transcript_for_activation_accepts_command_only_music(self):
        activation = wake_word_simple.evaluate_transcript_for_activation("mets la musique", now_value=10.0)

        self.assertIsNone(activation)

    def test_contains_wake_word_ignores_normal_sentence(self):
        self.assertFalse(wake_word_simple.contains_wake_word("bonjour comment vas tu"))


if __name__ == "__main__":
    unittest.main()
