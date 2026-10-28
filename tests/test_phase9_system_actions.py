"""Phase 9: native app launches and read-only Linux diagnostics, all subprocesses mocked."""

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import actions
from actions import system_actions
from assistant_plugins import build_default_registry
from intent_engine import detect_intent


class NativeApplicationLaunchTests(unittest.TestCase):
    def test_rejects_shell_operators_without_launching_anything(self):
        for suffix in ("; touch /tmp/a", " && whoami", " || whoami", " | cat", " `whoami`",
                       " $(whoami)", " & whoami", " > /tmp/a", "\nwhoami"):
            with self.subTest(suffix=suffix), \
                    patch.dict(system_actions.config.APP_COMMANDS, {"notepad": "gedit" + suffix}), \
                    patch.object(system_actions.subprocess, "Popen") as launch:
                result = system_actions.open_app("notepad")
                self.assertFalse(result["success"])
                self.assertIn("Métacaractère shell interdit", result["error"])
                launch.assert_not_called()

    def test_windows_start_alias_and_quoted_executable_are_parsed_as_argv(self):
        with patch.object(system_actions.os, "name", "nt"):
            self.assertEqual(["calc"], system_actions._app_command_args("start calc"))
            self.assertEqual(["code"], system_actions._app_command_args("start code"))
            self.assertEqual(
                [r"C:\Program Files\App\app.exe", "--flag", "hello world"],
                system_actions._app_command_args(r'start "" "C:\Program Files\App\app.exe" --flag "hello world"'),
            )

    def test_linux_alias_keeps_display_environment_and_uses_direct_argv(self):
        with patch.object(system_actions.os, "name", "posix"), \
                patch.object(system_actions.os, "getuid", return_value=1000, create=True), \
                patch.dict(system_actions.config.APP_COMMANDS, {"notepad": 'gedit "file with spaces.txt"'}), \
                patch.object(system_actions.os.path, "exists", return_value=False), \
                patch.object(system_actions.os.path, "isfile", return_value=False), \
                patch.object(system_actions.shutil, "which", return_value="/usr/bin/gedit"), \
                patch.object(system_actions.subprocess, "Popen") as launch:
            result = system_actions.open_app(app="bloc-notes")
        self.assertTrue(result["success"])
        self.assertEqual(["/usr/bin/gedit", "file with spaces.txt"], launch.call_args.args[0])
        self.assertFalse(launch.call_args.kwargs["shell"])
        self.assertIn("DISPLAY", launch.call_args.kwargs["env"])
        self.assertTrue(launch.call_args.kwargs["env"]["DBUS_SESSION_BUS_ADDRESS"].startswith("unix:"))

    def test_linux_root_forwards_gui_context_to_moctar_without_shell(self):
        def executable(name, **kwargs):
            return "/usr/bin/" + name

        with patch.object(system_actions.os, "name", "posix"), \
                patch.object(system_actions.os, "getuid", return_value=0, create=True), \
                patch.dict(system_actions.config.APP_COMMANDS, {"notepad": "gedit"}), \
                patch.object(system_actions.os.path, "exists", return_value=True), \
                patch.object(system_actions.os.path, "isfile", return_value=False), \
                patch.object(system_actions.shutil, "which", side_effect=executable), \
                patch.object(system_actions.subprocess, "Popen") as launch:
            result = system_actions.open_app("notepad")
        self.assertTrue(result["success"])
        argv = launch.call_args.args[0]
        self.assertEqual(["/usr/bin/sudo", "-n", "-u", "moctar", "env"], argv[:5])
        self.assertEqual("/usr/bin/gedit", argv[-1])
        self.assertTrue(any(arg.startswith("DISPLAY=") for arg in argv))
        self.assertTrue(any(arg.startswith("XAUTHORITY=") for arg in argv))
        self.assertFalse(launch.call_args.kwargs["shell"])

    def test_windows_code_cmd_wrapper_is_replaced_by_native_code_executable(self):
        with patch.object(system_actions.os, "name", "nt"), \
                patch.dict(system_actions.config.APP_COMMANDS, {"vscode": "start code"}), \
                patch.object(system_actions.os.path, "isfile", side_effect=lambda path: path.endswith("Code.exe")), \
                patch.object(system_actions.shutil, "which", return_value="C:/VSCode/bin/code.cmd"), \
                patch.object(system_actions.subprocess, "Popen") as launch:
            result = system_actions.open_app("visual studio code")
        self.assertTrue(result["success"])
        self.assertTrue(launch.call_args.args[0][0].endswith("Code.exe"))
        self.assertFalse(launch.call_args.kwargs["shell"])

    def test_missing_executable_returns_failure_without_popen(self):
        with patch.object(system_actions.os, "name", "posix"), \
                patch.dict(system_actions.config.APP_COMMANDS, {"notepad": "missing-editor"}), \
                patch.object(system_actions.os.path, "isfile", return_value=False), \
                patch.object(system_actions.shutil, "which", return_value=None), \
                patch.object(system_actions.subprocess, "Popen") as launch:
            result = system_actions.open_app("notepad")
        self.assertFalse(result["success"])
        self.assertIn("introuvable", result["error"])
        launch.assert_not_called()

    def test_list_configuration_keeps_arguments_with_spaces_intact(self):
        with patch.object(system_actions.os, "name", "posix"), \
                patch.object(system_actions.os, "getuid", return_value=1000, create=True), \
                patch.dict(system_actions.config.APP_COMMANDS, {"notepad": ["gedit", "hello world.txt"]}), \
                patch.object(system_actions.os.path, "isfile", return_value=False), \
                patch.object(system_actions.shutil, "which", return_value="/usr/bin/gedit"), \
                patch.object(system_actions.subprocess, "Popen") as launch:
            result = system_actions.open_app("notepad")
        self.assertTrue(result["success"])
        self.assertEqual(["/usr/bin/gedit", "hello world.txt"], launch.call_args.args[0])


class LinuxServiceDiagnosticTests(unittest.TestCase):
    def test_rejects_invalid_unit_names_and_options_before_subprocess(self):
        for unit in ("", "--help", "-u", "jarvis; reboot", "jarvis.service|cat", "../jarvis.service", None):
            with self.subTest(unit=unit), patch.object(system_actions.subprocess, "run") as run:
                response = system_actions.diagnose_linux_service(unit)
                self.assertIn("refusé", response)
                run.assert_not_called()

    def test_non_linux_returns_explanation_without_subprocess(self):
        with patch.object(system_actions.sys, "platform", "win32"), \
                patch.object(system_actions.subprocess, "run") as run:
            response = system_actions.diagnose_linux_service()
        self.assertIn("Linux Ubuntu", response)
        run.assert_not_called()

    def test_reads_exact_unit_and_reports_failures_from_real_returned_logs(self):
        with patch.object(system_actions.sys, "platform", "linux"), \
                patch.object(system_actions.subprocess, "run", side_effect=[
                    SimpleNamespace(returncode=3, stdout="failed\n", stderr=""),
                    SimpleNamespace(returncode=0, stdout="ready\nERROR database offline\nTraceback boom\nException\nFailed\nexit-code", stderr=""),
                ]) as run:
            response = system_actions.diagnose_linux_service("jarvis@dell.service")
        self.assertIn("État réel : failed", response)
        self.assertIn("ERROR database offline", response)
        self.assertIn("Traceback boom", response)
        self.assertNotIn("\nready\n", response)
        self.assertEqual(["systemctl", "is-active", "jarvis@dell.service"], run.call_args_list[0].args[0])
        self.assertEqual(["journalctl", "-u", "jarvis@dell.service", "-n", "25", "--no-pager"], run.call_args_list[1].args[0])
        for call in run.call_args_list:
            self.assertFalse(call.kwargs["shell"])
            self.assertEqual(4, call.kwargs["timeout"])

    def test_empty_journal_never_claims_service_is_healthy(self):
        with patch.object(system_actions.sys, "platform", "linux"), \
                patch.object(system_actions.subprocess, "run", side_effect=[
                    SimpleNamespace(returncode=0, stdout="active\n", stderr=""),
                    SimpleNamespace(returncode=0, stdout="", stderr=""),
                ]):
            response = system_actions.diagnose_linux_service()
        self.assertIn("État réel : active", response)
        self.assertIn("cause de panne non déterminée", response)

    def test_timeouts_missing_tools_and_denied_logs_are_explicit(self):
        failures = [FileNotFoundError("systemctl absent"), subprocess.TimeoutExpired("systemctl", 4)]
        for failure in failures:
            with self.subTest(failure=failure), patch.object(system_actions.sys, "platform", "linux"), \
                    patch.object(system_actions.subprocess, "run", side_effect=[
                        failure, SimpleNamespace(returncode=1, stdout="", stderr="Permission denied"),
                    ]):
                response = system_actions.diagnose_linux_service()
            self.assertIn("Permission denied", response)
            self.assertIn("Journal inaccessible", response)
            self.assertNotIn("Aucun des marqueurs", response)

    def test_diagnostic_is_exposed_in_dispatcher_schema_and_system_registry(self):
        self.assertIs(system_actions.diagnose_linux_service, actions.TOOL_DISPATCHER["diagnose_linux_service"])
        schema = next(tool for tool in actions.get_all_tools_schema() if tool["function"]["name"] == "diagnose_linux_service")
        self.assertEqual("jarvis.service", schema["function"]["parameters"]["properties"]["service_name"]["default"])
        # A Mock allows the registry's unrelated desktop handlers to remain untouched.
        route = build_default_registry(Mock()).get("diagnose_linux_service")
        self.assertEqual("system", route.domain)
        self.assertTrue(route.ai_enabled)
        with patch.object(system_actions, "diagnose_linux_service", return_value="checked") as diagnostic:
            self.assertEqual("checked", route.handler({"slots": {"service_name": "nginx.service"}}))
        diagnostic.assert_called_once_with(service_name="nginx.service")


class ServiceDiagnosticIntentTests(unittest.TestCase):
    def test_recognizes_all_three_phrasings_and_preserves_jarvis_unit(self):
        examples = {
            "Jarvis diagnostique le service jarvis.service": "jarvis.service",
            "pourquoi le service jarvis plante": "jarvis",
            "vérifie les logs de klasora-whatsapp.service": "klasora-whatsapp.service",
            "diagnostique le service": "jarvis.service",
            "vérifie les logs de jarvis.service stp": "jarvis.service",
            "diagnostique le service jarvis@dell.service": "jarvis@dell.service",
            "Pourquoi le service MyDaemon.service plante ?": "MyDaemon.service",
        }
        for phrase, service in examples.items():
            with self.subTest(phrase=phrase):
                intent = detect_intent(phrase)
                self.assertEqual("diagnose_linux_service", intent["intent"])
                self.assertEqual(service, intent["target"])
                self.assertEqual(service, intent["slots"]["service_name"])

    def test_general_explanatory_questions_remain_chat(self):
        self.assertEqual("chat_fallback", detect_intent("pourquoi le ciel est bleu")["intent"])


if __name__ == "__main__":
    unittest.main()
