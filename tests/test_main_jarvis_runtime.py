import threading
import unittest
from unittest.mock import Mock, patch

import main_jarvis
import wake_word_simple


class MainJarvisRuntimeTests(unittest.TestCase):
    def tearDown(self):
        wake_word_simple.reset_soft_wake_state()

    def test_format_assistant_message_keeps_clean_text(self):
        self.assertEqual("Bonjour", main_jarvis.format_assistant_message("Bonjour"))
        self.assertEqual(
            "Je n'ai pas de reponse pour le moment.",
            main_jarvis.format_assistant_message(""),
        )

    def test_setup_runtime_keeps_ui_click_and_soft_wake_startup(self):
        fake_app = Mock()
        fake_signal = Mock()
        manual_signal = Mock()
        reset_signal = Mock()
        fake_ui = Mock()
        fake_ui.listen_requested = fake_signal
        fake_ui.manual_command_requested = manual_signal
        fake_ui.reset_requested = reset_signal

        with patch("main_jarvis.run_ui", return_value=(fake_app, fake_ui)), patch(
            "main_jarvis.pyttsx3.init",
            return_value=Mock(),
        ), patch("main_jarvis.AssistantCore", return_value=Mock()), patch(
            "main_jarvis.start_soft_wake_listener",
        ) as start_soft_wake_listener:
            _app, _ui, _engine, _assistant, state = main_jarvis.setup_runtime()

        fake_signal.connect.assert_called_once()
        manual_signal.connect.assert_called_once()
        reset_signal.connect.assert_called_once()
        start_soft_wake_listener.assert_called_once()
        self.assertFalse(state["should_listen"])

    def test_manual_command_uses_the_normal_command_pipeline_without_microphone(self):
        fake_app = Mock()
        fake_ui = Mock()
        assistant = Mock()
        assistant.pause_music_for_command.return_value = False
        state = {
            "should_listen": False,
            "runtime_phase": main_jarvis.RUNTIME_IDLE,
        }

        with patch("main_jarvis.consume_voice_activation") as consume_activation, patch(
            "main_jarvis.start_command_job",
            return_value=True,
        ) as start_command_job, patch("main_jarvis._set_listening_popup"):
            submitted = main_jarvis.submit_manual_command(
                "  verifie mes taches de demain  ",
                fake_app,
                fake_ui,
                assistant,
                state,
            )

        self.assertTrue(submitted)
        consume_activation.assert_called_once()
        start_command_job.assert_called_once_with(
            "verifie mes taches de demain",
            assistant,
            False,
            state,
        )
        self.assertEqual("manual", state["activation_source"])

    def test_manual_command_is_rejected_while_processing(self):
        fake_ui = Mock()
        state = {
            "should_listen": False,
            "runtime_phase": main_jarvis.RUNTIME_PROCESSING,
            "command_job": {"done": False},
        }

        with patch("main_jarvis.start_command_job") as start_command_job:
            submitted = main_jarvis.submit_manual_command(
                "ouvre chrome",
                Mock(),
                fake_ui,
                Mock(),
                state,
            )

        self.assertFalse(submitted)
        start_command_job.assert_not_called()
        self.assertIn("Reinitialiser", fake_ui.update_text.call_args.args[0])

    def test_reset_clears_transient_runtime_and_discards_active_jobs(self):
        command_job = {"music_was_paused": True, "cancelled": False}
        listen_job = {"cancelled": False}
        state = {
            "should_listen": True,
            "runtime_phase": main_jarvis.RUNTIME_PROCESSING,
            "command_job": command_job,
            "listen_job": listen_job,
            "feedback": "ancien message",
            "last_command_signature": "ouvre chrome",
            "last_command_at": 10.0,
        }
        assistant = Mock()
        assistant.music_paused_for_command = True

        with patch("main_jarvis.consume_voice_activation") as consume_activation, patch(
            "main_jarvis._set_listening_popup"
        ):
            reset = main_jarvis.reset_runtime(Mock(), Mock(), assistant, state)

        self.assertTrue(reset)
        self.assertTrue(command_job["cancelled"])
        self.assertTrue(listen_job["cancelled"])
        self.assertNotIn("command_job", state)
        self.assertNotIn("listen_job", state)
        self.assertNotIn("last_command_signature", state)
        self.assertEqual(main_jarvis.RUNTIME_IDLE, state["runtime_phase"])
        assistant.reset_session.assert_called_once()
        assistant.resume_music_after_command.assert_called_once()
        consume_activation.assert_called_once()

    def test_reset_prevents_a_late_command_result_from_replacing_current_state(self):
        entered = threading.Event()
        release = threading.Event()
        assistant = Mock()
        assistant.music_paused_for_command = False
        state = {
            "should_listen": False,
            "runtime_phase": main_jarvis.RUNTIME_IDLE,
        }

        def delayed_command(_command, _assistant):
            entered.set()
            release.wait(timeout=2)
            return "ancienne reponse"

        with patch("main_jarvis.handle_command", side_effect=delayed_command), patch(
            "main_jarvis.consume_voice_activation"
        ), patch("main_jarvis._set_listening_popup"):
            self.assertTrue(
                main_jarvis.start_command_job("question lente", assistant, False, state)
            )
            old_job = state["command_job"]
            self.assertTrue(entered.wait(timeout=1))
            main_jarvis.reset_runtime(Mock(), Mock(), assistant, state)
            release.set()
            old_job["thread"].join(timeout=2)

        self.assertNotIn("command_job", state)
        self.assertTrue(old_job["cancelled"])
        self.assertTrue(old_job["done"])
        self.assertEqual("ancienne reponse", old_job["result"])
        self.assertGreaterEqual(assistant.reset_session.call_count, 2)

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

    def test_close_app_is_safe_to_activate_because_execution_requires_confirmation(self):
        self.assertTrue(main_jarvis.is_safe_command_only_activation("ferme chrome"))

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

    def test_ui_activation_invalidates_pending_soft_wake_duplicate(self):
        wake_word_simple.reset_soft_wake_state()
        self.assertTrue(
            wake_word_simple.publish_soft_wake_transcript(
                "hey moctar ouvre chrome",
                now_value=10.0,
            )
        )
        fake_app = Mock()
        fake_signal = Mock()
        fake_ui = Mock()
        fake_ui.listen_requested = fake_signal

        with patch("main_jarvis.run_ui", return_value=(fake_app, fake_ui)), patch(
            "main_jarvis.pyttsx3.init",
            return_value=Mock(),
        ), patch("main_jarvis.AssistantCore", return_value=Mock()), patch(
            "main_jarvis.start_soft_wake_listener",
        ):
            _app, _ui, _engine, _assistant, state = main_jarvis.setup_runtime()

        activate_from_ui = fake_signal.connect.call_args.args[0]
        activate_from_ui()

        self.assertEqual(main_jarvis.RUNTIME_LISTENING, state["runtime_phase"])
        self.assertTrue(state["should_listen"])
        self.assertIsNone(wake_word_simple.consume_voice_activation())

    def test_same_command_is_debounced_across_activation_sources(self):
        state = {}

        self.assertFalse(main_jarvis._is_duplicate_command("Ouvre Chrome", state, now_value=10.0))
        self.assertTrue(main_jarvis._is_duplicate_command("ouvre chrome", state, now_value=12.0))
        self.assertFalse(main_jarvis._is_duplicate_command("ouvre github", state, now_value=12.1))

    def test_soft_wake_command_during_tts_is_ignored(self):
        wake_word_simple.reset_soft_wake_state()
        state = {"runtime_phase": main_jarvis.RUNTIME_PROCESSING}
        publish_results = []

        def speak_attempt(_engine, _text):
            self.assertEqual(main_jarvis.RUNTIME_SPEAKING, state["runtime_phase"])
            publish_results.append(
                wake_word_simple.publish_soft_wake_transcript(
                    "ouvre chrome",
                    now_value=10.0,
                )
            )

        with patch("main_jarvis.t2s.text2speech", side_effect=speak_attempt):
            main_jarvis.speak(Mock(), Mock(), Mock(), "Test", state=state)

        self.assertEqual([False], publish_results)
        self.assertEqual(main_jarvis.RUNTIME_IDLE, state["runtime_phase"])
        self.assertFalse(wake_word_simple.should_activate_by_voice())


    def test_listen_job_watchdog_timeout_aborts_and_releases_mic(self):
        alive_thread = Mock()
        alive_thread.is_alive.return_value = True
        job = {
            "result": None,
            "done": False,
            "cancelled": False,
            "started_at": 10.0,
            "thread": alive_thread,
        }
        state = {"listen_job": job}

        with patch("time.monotonic", return_value=10.0 + main_jarvis.LISTEN_SAFETY_TIMEOUT + 1.0), \
             patch("main_jarvis.release_command_microphone") as release_mic:
            finished = main_jarvis.get_finished_listen_job(state)

        self.assertIsNotNone(finished)
        self.assertTrue(finished.get("timeout"))
        self.assertTrue(job["cancelled"])
        self.assertIsNone(state["listen_job"])
        release_mic.assert_called_once_with(resume_listener=False)

    def test_setup_runtime_connects_listen_cancelled(self):
        fake_app = Mock()
        fake_ui = Mock()
        fake_signal = Mock()
        cancel_signal = Mock()
        fake_ui.listen_requested = fake_signal
        fake_ui.manual_command_requested = Mock()
        fake_ui.reset_requested = Mock()
        fake_ui.listen_cancelled = cancel_signal

        with patch("main_jarvis.run_ui", return_value=(fake_app, fake_ui)), \
             patch("main_jarvis.pyttsx3.init", return_value=Mock()), \
             patch("main_jarvis.AssistantCore", return_value=Mock()), \
             patch("main_jarvis.start_soft_wake_listener"):
            _app, _ui, _engine, _assistant, state = main_jarvis.setup_runtime()

        cancel_signal.connect.assert_called_once()
        cancel_handler = cancel_signal.connect.call_args.args[0]

        # Simulate user cancelling while a listen job is active
        mock_job = {"cancelled": False}
        state["listen_job"] = mock_job
        state["should_listen"] = True
        state["runtime_phase"] = main_jarvis.RUNTIME_LISTENING

        with patch("main_jarvis.release_command_microphone") as release_mic, \
             patch("main_jarvis._set_listening_popup") as set_popup:
            cancel_handler()

        self.assertTrue(mock_job["cancelled"])
        self.assertIsNone(state["listen_job"])
        self.assertFalse(state["should_listen"])
        self.assertEqual(main_jarvis.RUNTIME_IDLE, state["runtime_phase"])
        release_mic.assert_called_once_with(resume_listener=False)
        set_popup.assert_called_once_with(fake_ui, False)
        fake_ui.set_mode.assert_called_with("idle")


if __name__ == "__main__":
    unittest.main()
