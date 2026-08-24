import unittest
from unittest.mock import Mock, patch

import main_jarvis


class MainJarvisRuntimeTests(unittest.TestCase):
    def test_format_assistant_message_keeps_clean_text(self):
        self.assertEqual("Bonjour", main_jarvis.format_assistant_message("Bonjour"))
        self.assertEqual(
            "Je n'ai pas de reponse pour le moment.",
            main_jarvis.format_assistant_message(""),
        )

    def test_setup_runtime_keeps_ui_click_and_soft_wake_startup(self):
        fake_app = Mock()
        fake_signal = Mock()
        fake_ui = Mock()
        fake_ui.listen_requested = fake_signal

        with patch("main_jarvis.run_ui", return_value=(fake_app, fake_ui)), patch(
            "main_jarvis.pyttsx3.init",
            return_value=Mock(),
        ), patch("main_jarvis.AssistantCore", return_value=Mock()), patch(
            "main_jarvis.start_soft_wake_listener",
        ) as start_soft_wake_listener:
            _app, _ui, _engine, _assistant, state = main_jarvis.setup_runtime()

        fake_signal.connect.assert_called_once()
        start_soft_wake_listener.assert_called_once()
        self.assertFalse(state["should_listen"])

    def test_resolve_next_command_uses_inline_voice_command_without_microphone(self):
        fake_app = Mock()
        fake_ui = Mock()
        state = {
            "should_listen": False,
            "activate_listen": Mock(),
        }

        with patch(
            "main_jarvis.consume_voice_activation",
            return_value={"wake_word": "yo", "command": "mets ninho", "confidence": 0.9},
        ), patch("main_jarvis.listen_for_command") as listen_for_command:
            command = main_jarvis.resolve_next_command(fake_app, fake_ui, state)

        state["activate_listen"].assert_called_once_with("soft_wake")
        listen_for_command.assert_not_called()
        self.assertEqual("mets ninho", command)
        self.assertFalse(state["should_listen"])

    def test_resolve_next_command_accepts_safe_command_only_voice_payload(self):
        fake_app = Mock()
        fake_ui = Mock()
        state = {
            "should_listen": False,
            "activate_listen": Mock(),
        }

        with patch(
            "main_jarvis.consume_voice_activation",
            return_value={
                "wake_word": "implicit command",
                "command": "active youtube",
                "confidence": 0.88,
                "command_only": True,
            },
        ), patch("main_jarvis.listen_for_command") as listen_for_command:
            command = main_jarvis.resolve_next_command(fake_app, fake_ui, state)

        state["activate_listen"].assert_called_once_with("soft_wake")
        listen_for_command.assert_not_called()
        self.assertEqual("active youtube", command)

    def test_resolve_next_command_rejects_ambiguous_command_only_payload(self):
        fake_app = Mock()
        fake_ui = Mock()
        state = {
            "should_listen": False,
            "activate_listen": Mock(),
        }

        with patch(
            "main_jarvis.consume_voice_activation",
            return_value={
                "wake_word": "implicit command",
                "command": "ferme youtube",
                "confidence": 0.88,
                "command_only": True,
            },
        ), patch("main_jarvis.listen_for_command") as listen_for_command:
            command = main_jarvis.resolve_next_command(fake_app, fake_ui, state)

        state["activate_listen"].assert_called_once_with("soft_wake")
        listen_for_command.assert_not_called()
        self.assertEqual("", command)
        self.assertFalse(state["should_listen"])

    def test_button_listening_wins_over_pending_wake_payload(self):
        fake_app = Mock()
        fake_ui = Mock()
        state = {
            "should_listen": True,
            "activate_listen": Mock(),
        }

        with patch(
            "main_jarvis.consume_voice_activation",
            return_value={"command": "mets damso"},
        ), patch("main_jarvis.listen_for_command", return_value="ouvre github") as listen:
            command = main_jarvis.resolve_next_command(fake_app, fake_ui, state)

        listen.assert_called_once_with(fake_app, fake_ui)
        self.assertEqual("ouvre github", command)
        self.assertFalse(state["should_listen"])


if __name__ == "__main__":
    unittest.main()
